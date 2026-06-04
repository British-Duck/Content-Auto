"""
Clip knowledge base — stores and retrieves descriptions of analysed video segments.

Every clip the researcher finds via Gemini is saved here with a human-readable
description of what it shows. Before running Gemini video analysis for a new
screen, the researcher checks this base first.

Matching is semantic, not keyword-based: Gemini reads the stored descriptions
and reasons whether any clip specifically matches the new clip_moment — even
across completely different topics. A swimming finish clip might correctly
match a "athlete's hand reaching a goal" beat in a story about something else.

Structure:
  cache/clip_knowledge/[video_id].json  — one file per YouTube video
  cache/clip_knowledge/thumbs/          — one frame per known segment

Non-breaking: all functions return None/[] on any error so the researcher
always falls back to normal YouTube search + Gemini video analysis.
"""
import json
import logging
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path
from typing import Optional

import config
import gemini
from cost_tracker import get_tracker

log = logging.getLogger(__name__)

KB_DIR = config.ROOT / "cache" / "clip_knowledge"
THUMB_DIR = KB_DIR / "thumbs"
KB_DIR.mkdir(parents=True, exist_ok=True)
THUMB_DIR.mkdir(parents=True, exist_ok=True)

# Maximum candidates sent to Gemini for matching — keeps the prompt lean
_MATCH_CANDIDATE_LIMIT = 40

_MATCH_PROMPT = """\
I need a video clip that shows specifically: {moment}

Below are clips from my library. A match means the clip shows the EXACT
action or scene I need — at the same level of specificity. Do not match
on topic alone. These are NOT matches:
  "athlete running" for "athlete collapsing at finish line"
  "crowd cheering" for "crowd in shocked silence"
  "swimming race" for "swimmer's hand touching the wall"

Candidates:
{candidates}

Return JSON only:
{{"match": true|false, "clip_id": "<video_id>@<start>-<end> or null", "reason": "<one sentence>"}}

If no candidate is a specific match, return match: false and clip_id: null."""


@dataclass
class ClipSegment:
    video_id: str
    url: str
    title: str
    start: float
    end: float
    description: str        # what Gemini observed in the clip (its "reason")
    found_for_moment: str   # the clip_moment it was originally discovered for
    verified: bool = False
    added: str = field(default_factory=lambda: str(date.today()))

    @property
    def clip_id(self) -> str:
        return f"{self.video_id}@{self.start}-{self.end}"

    @property
    def duration(self) -> float:
        return self.end - self.start

    def as_candidate_line(self, index: int) -> str:
        verified = " [VERIFIED]" if self.verified else ""
        return (
            f"{index}. [{self.clip_id}]{verified}\n"
            f"   Shows: \"{self.description}\"\n"
            f"   Originally found for: \"{self.found_for_moment}\""
        )


# ── Storage ───────────────────────────────────────────────────────────────────

def _kb_path(video_id: str) -> Path:
    return KB_DIR / f"{video_id}.json"


def _load_segments(video_id: str) -> tuple[dict, list[ClipSegment]]:
    """Return (raw_meta, segments) for a video. Raw meta holds url/title."""
    path = _kb_path(video_id)
    if not path.exists():
        return {}, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        segments = [ClipSegment(**s) for s in data.get("segments", [])]
        return data, segments
    except Exception as e:
        log.warning("KB: failed to load %s — %s", path.name, e)
        return {}, []


def _write_segments(video_id: str, url: str, title: str,
                    segments: list[ClipSegment]) -> None:
    """Atomic write — never leaves a partial/corrupt file."""
    path = _kb_path(video_id)
    data = {
        "video_id": video_id,
        "url": url,
        "title": title,
        "segments": [asdict(s) for s in segments],
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ── Public write API ──────────────────────────────────────────────────────────

def save_clip(video_id: str, url: str, title: str,
              start: float, end: float,
              description: str, found_for_moment: str) -> ClipSegment:
    """
    Persist a newly discovered clip segment to the knowledge base.
    If a segment with the same timestamps already exists, returns it unchanged.
    """
    meta, segments = _load_segments(video_id)

    for seg in segments:
        if abs(seg.start - start) < 0.5 and abs(seg.end - end) < 0.5:
            log.debug("KB: segment already recorded %s", seg.clip_id)
            return seg

    segment = ClipSegment(
        video_id=video_id,
        url=url,
        title=title,
        start=round(start, 2),
        end=round(end, 2),
        description=description,
        found_for_moment=found_for_moment,
    )
    segments.append(segment)
    _write_segments(video_id, url, title, segments)
    log.info("KB: saved %s — \"%s\"", segment.clip_id, description[:70])
    return segment


def extract_thumbnail(video_path: Path, start: float, end: float,
                      video_id: str) -> Optional[Path]:
    """
    Save a single frame from the midpoint of the clip for human verification.
    Returns the thumbnail path, or None if extraction fails.
    """
    safe_id = f"{video_id}_{start:.1f}_{end:.1f}".replace(".", "_")
    out = THUMB_DIR / f"{safe_id}.jpg"
    if out.exists():
        return out
    mid = (start + end) / 2
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-ss", str(mid), "-i", str(video_path),
        "-frames:v", "1", "-q:v", "3", "-vf", "scale=320:-2",
        str(out),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if r.returncode == 0 and out.exists():
            log.debug("KB: thumbnail saved %s", out.name)
            return out
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


# ── Public search API ─────────────────────────────────────────────────────────

def _stop_words() -> frozenset:
    return frozenset({
        "the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "or",
        "is", "was", "with", "from", "as", "by", "shows", "showing", "clip",
        "footage", "video", "moment", "scene", "shot", "frame",
    })


def _keywords(text: str) -> set[str]:
    stops = _stop_words()
    return {
        w.lower().strip(".,;:'\"")
        for w in text.split()
        if len(w) > 2 and w.lower().strip(".,;:'\"") not in stops
    }


def _load_all_segments() -> list[ClipSegment]:
    segments = []
    for kb_file in KB_DIR.glob("*.json"):
        try:
            data = json.loads(kb_file.read_text(encoding="utf-8"))
            for s in data.get("segments", []):
                segments.append(ClipSegment(**s))
        except Exception:
            pass
    return segments


def _pre_filter(clip_moment: str, all_segs: list[ClipSegment],
                limit: int) -> list[ClipSegment]:
    """
    Fast keyword pre-filter before the Gemini semantic match call.
    Verified clips are always included in the candidate set.
    Unverified clips are ranked by word overlap with clip_moment.
    """
    moment_kw = _keywords(clip_moment)
    if not moment_kw:
        return all_segs[:limit]

    verified = [s for s in all_segs if s.verified]
    unverified = [s for s in all_segs if not s.verified]

    scored = []
    for seg in unverified:
        seg_kw = _keywords(seg.description) | _keywords(seg.found_for_moment)
        overlap = len(moment_kw & seg_kw)
        scored.append((overlap, seg))
    scored.sort(key=lambda x: x[0], reverse=True)

    # Verified clips always appear; top-overlap unverified fill the rest
    candidates = verified + [s for _, s in scored]
    return candidates[:limit]


def match_from_knowledge(clip_moment: str) -> Optional[ClipSegment]:
    """
    Check whether any known clip specifically matches this clip_moment.

    1. Load all known segments.
    2. Pre-filter by keyword overlap to a manageable candidate list.
    3. Send candidates + clip_moment to Gemini as a cheap text call.
    4. Return the matching ClipSegment or None.

    Returns None silently on any error — the caller falls back to normal flow.
    """
    all_segs = _load_all_segments()
    if not all_segs:
        return None

    candidates = _pre_filter(clip_moment, all_segs, _MATCH_CANDIDATE_LIMIT)
    if not candidates:
        return None

    candidate_block = "\n".join(
        seg.as_candidate_line(i + 1) for i, seg in enumerate(candidates)
    )
    prompt = _MATCH_PROMPT.format(moment=clip_moment, candidates=candidate_block)

    get_tracker().record_text_call(
        "kb_match",
        prompt_words=len(prompt.split()),
        response_words=20,
        thinking_tokens=0,
    )

    try:
        raw = gemini.generate(prompt, temperature=0.1, thinking_budget=0)
        result = gemini.parse_json(raw)
    except Exception as e:
        log.warning("KB: match call failed — %s", e)
        return None

    if not result.get("match"):
        log.debug("KB: no match found for moment: %s", clip_moment[:60])
        return None

    clip_id = result.get("clip_id", "")
    reason = result.get("reason", "")

    for seg in candidates:
        if seg.clip_id == clip_id:
            log.info("KB: matched %s — %s", clip_id, reason)
            return seg

    log.warning("KB: Gemini returned clip_id %r not found in candidates", clip_id)
    return None


# ── Verification helper (for manage_clips.py / future UI) ────────────────────

def mark_verified(video_id: str, start: float, end: float,
                  verified: bool = True) -> bool:
    """Mark a segment as human-verified. Returns True if found and updated."""
    meta, segments = _load_segments(video_id)
    for seg in segments:
        if abs(seg.start - start) < 0.5 and abs(seg.end - end) < 0.5:
            seg.verified = verified
            _write_segments(video_id, seg.url, seg.title, segments)
            log.info("KB: %s %s", "verified" if verified else "unverified", seg.clip_id)
            return True
    return False


def list_unverified() -> list[ClipSegment]:
    return [s for s in _load_all_segments() if not s.verified]
