"""
Stage 2 — Researcher.

Searches YouTube via yt-dlp, attempts metadata-first timestamp extraction
(chapters/descriptions — free), falls back to Gemini video analysis only
when metadata is insufficient. Caches aggressively at every layer.

Cost optimizations:
  - Search results cached by query hash
  - Video metadata cached by video ID
  - Candidates ranked by title relevance; only top N downloaded
  - Chapters/description timestamps tried before any Gemini call
  - Thinking tokens disabled on Gemini video calls (thinking_budget=0)
  - Timestamp results cached by video+description hash
  - Selective re-research: screens with existing clips are skipped
"""
import hashlib
import json
import logging
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Optional

import config
import gemini
from cost_tracker import get_tracker

log = logging.getLogger(__name__)


def _record_fallback(screen_index: int, reason: str) -> None:
    """Record a gradient / no-footage fallback on the active run record, with the
    screen index and the reason. No-op when no run record is active (e.g. the
    researcher run standalone). Makes the silent failure loud in the record."""
    try:
        from run_record import get_active_record
        rec = get_active_record()
        if rec is not None:
            rec.add_fallback(screen_index, reason)
    except Exception as e:
        log.debug("fallback record skipped: %s", e)


def _record_tier(screen_index: int, tier: str, **detail) -> None:
    """Record which rung of the footage fallback ladder a beat landed on. No-op
    when no run record is active."""
    try:
        from run_record import get_active_record
        rec = get_active_record()
        if rec is not None:
            rec.add_footage_tier(screen_index, tier, **detail)
    except Exception as e:
        log.debug("tier record skipped: %s", e)


TIMESTAMP_PROMPT = """\
Find the segment in this video that best matches: {description}
{exclusion_note}
Prefer segments showing clear, unobstructed primary action (athletes competing, \
key moments, real events). Avoid: TV score overlays filling the frame, animated \
graphics or watermarked replays, commentary or interview shots, crowd-only shots.

Return JSON only — timestamps are ABSOLUTE SECONDS from the start of the video \
(e.g. start=4.5 means 4.5 seconds in, NOT a fraction or percentage):
{{"start": <float seconds>, "end": <float seconds>, "confidence": "high"|"medium"|"low", "reason": "<one sentence>"}}

Segment must be {min_dur:.1f}–{max_dur:.1f}s long. If nothing matches, confidence="low"."""

BATCH_TIMESTAMP_PROMPT = """\
Find segments in this video matching each description below.

Return JSON only — an array of objects, one per description, in order:
[{{"index": 0, "start": <float>, "end": <float>, "confidence": "high"|"medium"|"low", "reason": "<one sentence>"}}, ...]

Each segment must be {min_dur:.1f}–{max_dur:.1f}s. If nothing matches a description, confidence="low".

Descriptions:
{descriptions}"""

TRIM_PROFILES = [
    {"label": "480p/60s", "duration": 60, "scale": 480, "crf": 28},
    {"label": "360p/30s", "duration": 30, "scale": 360, "crf": 32},
]

# Single-clip window finding uploads a LONGER stretch (the event can run tens of
# seconds) so Gemini can judge a continuous arc, at a lower resolution to stay under
# the upload size limit.
SINGLE_CLIP_PROFILE = {
    "label": "single-clip-window",
    "duration": config.SINGLE_CLIP_PROBE_SECONDS,
    "scale": config.SINGLE_CLIP_PROBE_SCALE,
    "crf": 32,
}

SINGLE_CLIP_WINDOW_PROMPT = """\
You are choosing the single best CONTINUOUS window of this ONE video to carry an \
entire {target:.0f}-second vertical news clip. The whole story will play on this one \
stretch of footage — no cutting to other videos.

STORY / WHAT THE FOOTAGE SHOWS: {description}
THE KEY MOMENT (the payoff the story builds to): {key_moment}

Find the strongest continuous stretch with a natural arc — lead-up, the key moment, \
then a brief aftermath — that stays ON THIS STORY the whole time (no studio desk, no \
unrelated b-roll, no adverts in the middle). It should be about {target:.0f} seconds \
long and MUST be at least {min_len:.0f} seconds of uninterrupted relevant footage.

PREFER CLEAN FOOTAGE: a clear shot of the real action, not footage buried under \
burned-in graphics — a telemetry/stats dashboard, a broadcast scoreboard filling the \
frame, large watermarks or animated overlays that cover the subject. If this video's \
relevant stretch is dominated by such overlays AND a cleaner source likely exists, set \
confidence="low" so a cleaner candidate is tried instead. A small logo or a clean lower-\
third is fine.

Return JSON only — timestamps are ABSOLUTE SECONDS from the start of the video:
{{"window_start": <float>, "window_end": <float>, "key_moment_ts": <float within the window>, \
"subject_x": <float 0..1, horizontal position of the main subject/action: 0=left edge, \
0.5=centre, 1=right edge>, "heavy_overlay": <true|false, true if burned-in graphics cover \
much of the frame>, "confidence": "high"|"medium"|"low", "reason": "<one sentence>"}}

If there is NO continuous on-story stretch at least {min_len:.0f} seconds long, set \
confidence="low" (the story will be told a different way)."""

STOP_WORDS = frozenset({
    "the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "or",
    "is", "was", "are", "were", "be", "been", "being", "with", "from",
    "by", "about", "into", "through", "during", "before", "after",
    "this", "that", "these", "those", "it", "its", "not", "no",
})


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _query_hash(query: str) -> str:
    return hashlib.sha256(query.strip().lower().encode()).hexdigest()[:16]


# ── Search with caching ──────────────────────────────────────────────────────

def _search_youtube(query: str, max_results: int) -> list[dict]:
    cache_file = config.SEARCH_CACHE_DIR / f"{_query_hash(query)}.json"
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            log.info("Search cache hit: '%s' (%d results)", query[:60], len(cached))
            get_tracker().record_cache(True, "search")
            return cached
        except (json.JSONDecodeError, KeyError):
            pass

    get_tracker().record_cache(False, "search")
    log.info("yt-dlp search: %s", query)
    cmd = ["yt-dlp", f"ytsearch{max_results}:{query}", "--dump-json", "--flat-playlist",
           "--no-download", "--no-warnings"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        log.error("yt-dlp not found")
        return []
    except subprocess.TimeoutExpired:
        log.error("yt-dlp search timed out")
        return []

    if result.returncode != 0:
        log.warning("yt-dlp search failed: %s", result.stderr.strip()[:200])
        return []

    results = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            results.append({
                "url": entry.get("url") or entry.get("webpage_url") or
                       f"https://www.youtube.com/watch?v={entry.get('id', '')}",
                "id": entry.get("id", ""),
                "title": entry.get("title", ""),
                "duration": entry.get("duration", 0),
            })
        except json.JSONDecodeError:
            continue

    log.info("Found %d candidates", len(results))
    cache_file.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


# ── Candidate ranking ────────────────────────────────────────────────────────

def _significant_words(text: str) -> set[str]:
    return {w for w in text.lower().split() if w not in STOP_WORDS and len(w) > 1}


def _rank_candidates(candidates: list[dict], description: str) -> list[dict]:
    query_words = _significant_words(description)
    if not query_words:
        return candidates

    scored = []
    for cand in candidates:
        title_words = _significant_words(cand.get("title", ""))
        overlap = len(query_words & title_words)
        dur = cand.get("duration") or 0
        dur_bonus = 1.0 if 30 <= dur <= 600 else 0.5
        scored.append((overlap * dur_bonus, cand))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored]


# ── Video metadata extraction ────────────────────────────────────────────────

def _get_video_metadata(url: str, video_id: str) -> Optional[dict]:
    cache_file = config.METADATA_CACHE_DIR / f"{video_id}.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            pass

    log.info("Fetching metadata for %s", video_id)
    cmd = ["yt-dlp", "--dump-json", "--no-download", "--no-warnings", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
        meta = {
            "id": data.get("id", ""),
            "title": data.get("title", ""),
            "description": data.get("description", ""),
            "duration": data.get("duration", 0),
            "chapters": data.get("chapters") or [],
        }
        cache_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return meta
    except json.JSONDecodeError:
        return None


def _find_timestamps_from_metadata(metadata: dict, description: str,
                                   min_dur: float, max_dur: float) -> Optional[dict]:
    query_words = _significant_words(description)
    if not query_words:
        return None

    chapters = metadata.get("chapters") or []
    if chapters:
        for ch in chapters:
            title = ch.get("title", "")
            ch_words = _significant_words(title)
            overlap = len(query_words & ch_words)
            if overlap >= 2 or (overlap >= 1 and len(query_words) <= 2):
                start = float(ch.get("start_time", 0))
                end = float(ch.get("end_time", start + min_dur))
                if end - start < min_dur:
                    end = start + min_dur
                if end - start > max_dur:
                    end = start + max_dur
                log.info("Metadata hit (chapter): '%s' → %.1f–%.1fs", title, start, end)
                return {
                    "start": round(start, 2), "end": round(end, 2),
                    "confidence": "medium",
                    "reason": f"Matched chapter: {title}",
                    "source": "metadata",
                }

    desc_text = metadata.get("description", "")
    if desc_text:
        ts_pattern = r'(?:(\d+):)?(\d+):(\d{2})\s*[-–—:]\s*(.+)'
        for match in re.finditer(ts_pattern, desc_text):
            hours = int(match.group(1) or 0)
            minutes = int(match.group(2))
            seconds = int(match.group(3))
            label = match.group(4).strip()

            label_words = _significant_words(label)
            overlap = len(query_words & label_words)
            if overlap >= 2 or (overlap >= 1 and len(query_words) <= 2):
                start = hours * 3600 + minutes * 60 + seconds
                end = start + max(min_dur, min(max_dur, 5.0))
                log.info("Metadata hit (description): '%s' → %.1f–%.1fs",
                         match.group(0).strip()[:60], start, end)
                return {
                    "start": round(start, 2), "end": round(end, 2),
                    "confidence": "medium",
                    "reason": f"Matched description timestamp: {label[:60]}",
                    "source": "metadata",
                }

    return None


# ── Download ──────────────────────────────────────────────────────────────────

def _download(url: str, video_id: str) -> Optional[Path]:
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for ext in (".mp4", ".webm", ".mkv"):
        cached = config.CACHE_DIR / f"{video_id}{ext}"
        if cached.exists() and cached.stat().st_size > 0:
            log.info("Cache hit: %s", cached.name)
            get_tracker().record_download(url, cached=True)
            return cached

    log.info("Downloading %s", video_id)
    get_tracker().record_download(url, cached=False)
    template = str(config.CACHE_DIR / f"{video_id}.%(ext)s")
    cmd = [
        "yt-dlp", "-f",
        "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best",
        "--merge-output-format", "mp4", "-o", template,
        "--no-playlist", "--no-overwrites", url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.error("Download failed: %s", e)
        return None

    if result.returncode != 0:
        log.warning("Download failed: %s", result.stderr.strip()[:200])
        return None

    for ext in (".mp4", ".webm", ".mkv"):
        cached = config.CACHE_DIR / f"{video_id}{ext}"
        if cached.exists():
            log.info("Downloaded: %s (%.1f MB)", cached.name, cached.stat().st_size / 1e6)
            return cached
    return None


# ── Video trimming for Gemini upload ──────────────────────────────────────────

def _trim_for_upload(video_path: Path, profile: dict) -> Optional[Path]:
    h = _url_hash(str(video_path))
    out = config.RESEARCHER_TRIM_DIR / f"{h}_{profile['scale']}p_{profile['duration']}s.mp4"
    if out.exists() and out.stat().st_size > 0:
        log.debug("Trim cache hit: %s", out.name)
        return _ensure_size(out, profile)

    log.info("Trimming %s → %s", video_path.name, profile["label"])
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-i", str(video_path),
        "-t", str(profile["duration"]),
        "-vf", f"scale={profile['scale']}:-2",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-crf", str(profile["crf"]),
        "-an",
        str(out),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.error("Trim failed: %s", e)
        return None

    if r.returncode != 0:
        log.error("Trim failed: %s", r.stderr.strip()[:200])
        return None

    mb = out.stat().st_size / (1024 * 1024)
    log.info("Trimmed: %s (%.1f MB)", out.name, mb)
    return _ensure_size(out, profile)


def _ensure_size(path: Path, profile: dict) -> Optional[Path]:
    mb = path.stat().st_size / (1024 * 1024)
    if mb <= config.RESEARCHER_UPLOAD_MAX_MB:
        return path

    log.info("%.1f MB exceeds %d MB limit — recompressing", mb, config.RESEARCHER_UPLOAD_MAX_MB)
    recomp = path.with_stem(path.stem + "_small")
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-i", str(path),
        "-vf", f"scale={min(profile['scale'], 360)}:-2",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-crf", "36",
        "-an",
        str(recomp),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return path
    if r.returncode != 0:
        return path

    new_mb = recomp.stat().st_size / (1024 * 1024)
    log.info("Recompressed: %.1f MB → %.1f MB", mb, new_mb)
    return recomp


# ── Gemini video analysis with thinking disabled ──────────────────────────────

def _upload_and_analyse(client, video_path: Path, prompt: str, timeout: int) -> Optional[dict]:
    uploaded = None
    try:
        uploaded = client.files.upload(file=video_path)
        while uploaded.state.name == "PROCESSING":
            time.sleep(2)
            uploaded = client.files.get(name=uploaded.name)
        if uploaded.state.name == "FAILED":
            log.error("Upload processing failed: %s", video_path.name)
            return None
    except Exception as e:
        log.error("Upload error: %s", e)
        return None

    try:
        from google.genai import types

        def _call():
            gen_config = types.GenerateContentConfig(
                temperature=0.2,
                response_mime_type="application/json",
            )
            if config.THINKING_BUDGET_TIMESTAMPS is not None:
                gen_config.thinking_config = types.ThinkingConfig(
                    thinking_budget=config.THINKING_BUDGET_TIMESTAMPS,
                )
            return client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=[uploaded, prompt],
                config=gen_config,
            )

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_call)
            response = future.result(timeout=timeout)

        gemini._emit_usage(response, config.GEMINI_MODEL)  # researcher's direct video call
        return gemini.parse_json(response.text)

    except FuturesTimeout:
        log.warning("generate_content timed out after %ds for %s", timeout, video_path.name)
        return None
    except Exception as e:
        log.error("Analysis failed: %s", e)
        return None
    finally:
        if uploaded:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass


def _fix_fractional_timestamps(result: dict, video_duration: float) -> dict:
    """Detect and correct Gemini returning 0–1 normalized fractions instead of seconds.

    A narrow fractional range (e.g. 0.57–0.59) means Gemini found something specific.
    A wide range (e.g. 0.55–1.00) means it couldn't pinpoint the moment — downgrade
    those to low confidence so they get rejected rather than producing a random clip.
    """
    start = result.get("start", 0)
    end = result.get("end", 0)
    if isinstance(start, (int, float)) and isinstance(end, (int, float)):
        if 0 <= start < 2.0 and 0 <= end < 2.0 and (end - start) < 0.5:
            original_range = end - start
            corrected_start = round(start * video_duration, 2)
            corrected_end = round(end * video_duration, 2)
            log.warning(
                "Gemini returned fractional timestamps (%.3f–%.3f) — "
                "converting to seconds using %ds duration: %.1f–%.1fs",
                start, end, video_duration, corrected_start, corrected_end,
            )
            result = {**result, "start": corrected_start, "end": corrected_end}
            if original_range > 0.25:
                log.warning(
                    "Wide fractional range (%.2f) — Gemini could not pinpoint moment, "
                    "downgrading confidence to low",
                    original_range,
                )
                result = {**result, "confidence": "low"}
    return result


def _get_timestamps(video_path: Path, description: str, screen_duration: float,
                    exclude_ranges: list = None) -> Optional[dict]:
    exclude_ranges = exclude_ranges or []
    # Keep no-exclusion cache key identical to the original format so existing
    # cached results are still hit. Only change the key when exclusions are active.
    if exclude_ranges:
        exclusion_str = json.dumps(sorted(exclude_ranges))
        cache_key = _url_hash(f"{video_path.name}:{description}:{exclusion_str}")
    else:
        cache_key = _url_hash(f"{video_path.name}:{description}")
    cache_file = config.TIMESTAMP_CACHE_DIR / f"{cache_key}.json"
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            get_tracker().record_cache(True, "timestamps")
            return cached
        except (json.JSONDecodeError, KeyError):
            pass

    get_tracker().record_cache(False, "timestamps")

    if exclude_ranges:
        # Enforce forward chronology: new segment must start after the latest used endpoint
        max_used_end = max(e for _, e in exclude_ranges)
        ranges_str = ", ".join(f"{s:.1f}–{e:.1f}s" for s, e in exclude_ranges)
        exclusion_note = (
            f"IMPORTANT: The following segments are already in use for earlier screens — "
            f"find a DIFFERENT moment that (1) does NOT overlap with {ranges_str}, "
            f"AND (2) starts AFTER {max_used_end:.1f}s to preserve narrative chronology. "
            f"If no suitable segment exists after {max_used_end:.1f}s, return confidence=\"low\".\n"
        )
    else:
        exclusion_note = ""

    prompt = TIMESTAMP_PROMPT.format(
        description=description,
        exclusion_note=exclusion_note,
        min_dur=config.RESEARCHER_MIN_CLIP_DURATION,
        max_dur=min(config.RESEARCHER_MAX_CLIP_DURATION, screen_duration + 1.0),
    )

    client = gemini.get_client()

    for attempt, profile in enumerate(TRIM_PROFILES):
        trimmed = _trim_for_upload(video_path, profile)
        if trimmed is None:
            continue

        mb = trimmed.stat().st_size / (1024 * 1024)
        log.info("Attempt %d: uploading %s (%.1f MB, %s)", attempt + 1, trimmed.name, mb,
                 profile["label"])

        get_tracker().record_video_call(
            stage="researcher",
            video_seconds=profile["duration"],
            prompt_words=len(prompt.split()),
        )

        result = _upload_and_analyse(client, trimmed, prompt, config.RESEARCHER_GEMINI_TIMEOUT)
        if result is not None:
            result = _fix_fractional_timestamps(result, profile["duration"])
            cache_file.write_text(json.dumps(result, indent=2), encoding="utf-8")
            return result

        if attempt < len(TRIM_PROFILES) - 1:
            log.info("Retrying with smaller profile…")
            time.sleep(config.GEMINI_RPM_SLEEP)

    log.warning("All attempts failed for %s — falling back to gradient", video_path.name)
    return None


# ── Gradient fallback diagnostics ────────────────────────────────────────────

def _log_gradient(screen_num: int, diag: dict) -> None:
    """Emit a loud, structured log block explaining exactly why a screen got gradient."""
    tried = diag.get("candidates_tried", [])

    if diag["candidates_found"] == 0:
        overall = "no YouTube results returned for query"
    elif not tried:
        overall = "no candidates could be downloaded"
    else:
        skip_reasons = [c["skip_reason"] for c in tried if c.get("skip_reason")]
        overall = " | ".join(skip_reasons) if skip_reasons else "all candidates exhausted without match"

    log.warning(
        "\n[researcher] Screen %d → GRADIENT. Reason: %s\n"
        "  clip_description: %r\n"
        "  search query:     %r\n"
        "  candidates found: %d\n"
        "  candidates tried: %d",
        screen_num, overall,
        diag["clip_description"],
        diag["search_query"],
        diag["candidates_found"],
        len(tried),
    )
    for c in tried:
        gemini_str = (
            f"yes — {c['gemini_result']}" if c["gemini_attempted"]
            else "no"
        )
        log.warning(
            "    ├ [%s] %s\n"
            "      metadata: %s\n"
            "      gemini:   %s%s",
            c["video_id"],
            c.get("title", "?")[:70],
            c["metadata_check"],
            gemini_str,
            f"\n      skipped:  {c['skip_reason']}" if c.get("skip_reason") else "",
        )


# ── Footage fallback ladder: alt-queries, query resolution, Ken Burns stills ──

def _heuristic_alts(desc: str, moment: Optional[str]) -> list[str]:
    """Deterministic alternate queries when the LLM generator is unavailable:
    broaden the description, and derive one from the clip_moment."""
    words = desc.split()
    alts = []
    if len(words) >= 3:
        alts.append(" ".join(words[:-1]))          # drop trailing qualifier
    if len(words) >= 4:
        alts.append(" ".join(words[:2]))            # broadest: lead two words
    if moment:
        mw = [w for w in moment.split() if w.lower() not in STOP_WORDS][:4]
        if len(mw) >= 2:
            alts.append(" ".join(mw))
    out, seen = [], {desc.lower()}
    for a in alts:
        a = a.strip()
        if a and a.lower() not in seen:
            out.append(a)
            seen.add(a.lower())
    return out


def _alternate_queries(desc: str, moment: Optional[str], context: str,
                       n: int = None) -> list[str]:
    """Generate up to n ALTERNATE YouTube search queries for a beat whose first
    query missed. One cheap flash call (thinking disabled), with a deterministic
    heuristic fallback. Each alternate is a real, searchable phrasing."""
    if n is None:
        n = config.RESEARCHER_ALT_QUERIES
    if n <= 0:
        return []
    prompt = (
        f"Generate {n} ALTERNATE YouTube search queries to find REAL footage for a "
        f"news-video beat. The first query failed to return usable footage. Give "
        f"different phrasings likely to match a real upload, varying by: event name + "
        f"year, key person + event, broader event name, organisation + event. "
        f"3-6 words each, no punctuation, no quotes, real searchable events only.\n\n"
        f"FAILED QUERY: {desc}\n"
        f"WHAT TO SHOW: {moment or '(unspecified)'}\n"
        f"STORY CONTEXT: {context[:200]}\n\n"
        f'Return JSON only: {{"queries": ["...", "..."]}}'
    )
    alts = []
    try:
        get_tracker().record_text_call("researcher_alt_queries",
                                       prompt_words=len(prompt.split()),
                                       response_words=30, thinking_tokens=0)
        raw = gemini.generate(prompt, temperature=0.5, thinking_budget=0,
                              model=config.GEMINI_MODEL)
        data = gemini.parse_json(raw)
        cand = data.get("queries") if isinstance(data, dict) else data
        if isinstance(cand, list):
            alts = [q.strip() for q in cand if isinstance(q, str) and q.strip()]
    except Exception as e:
        log.info("  alt-query generation failed (%s) — using heuristic alternates", e)

    out, seen = [], {desc.lower()}
    for a in alts:
        if a.lower() not in seen:
            out.append(a)
            seen.add(a.lower())
    if not out:
        out = _heuristic_alts(desc, moment)
    return out[:n]


def _probe_duration(path: Path) -> Optional[float]:
    cmd = ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
           "-of", "csv=p=0", str(path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return float(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        return None


def _extract_still(video_path: Path, at_seconds: float, out_path: Path) -> bool:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
           "-ss", f"{max(0.0, at_seconds):.3f}", "-i", str(video_path),
           "-frames:v", "1", "-q:v", "2", str(out_path)]
    try:
        ok = subprocess.run(cmd, capture_output=True, text=True, timeout=30).returncode == 0
        return ok and out_path.exists() and out_path.stat().st_size > 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _fetch_thumbnail(video_id: str, out_path: Path) -> bool:
    """Download a YouTube still thumbnail (no new dependency — static i.ytimg URL)."""
    import urllib.request
    for quality in ("maxresdefault", "hqdefault", "mqdefault"):
        url = f"https://i.ytimg.com/vi/{video_id}/{quality}.jpg"
        try:
            urllib.request.urlretrieve(url, str(out_path))
            if out_path.exists() and out_path.stat().st_size > 2000:
                return True
        except Exception:
            continue
    return False


def _resolve_query(query: str, screen: dict, used_ranges: dict,
                   cand_acc: list) -> Optional[dict]:
    """Search YouTube for `query` and try to extract a usable clip (metadata
    shortcut, else download + Gemini analysis). Returns a clip dict
    {url,start,end} or None. Every top candidate is appended to `cand_acc`
    (with its downloaded path + any Gemini moment hint) so a later Ken Burns
    still can be sourced without re-downloading."""
    candidates = _search_youtube(query, config.RESEARCHER_MAX_SEARCH_RESULTS)
    if not candidates:
        log.info("  query %r: no YouTube results", query)
        return None

    ranked = _rank_candidates(candidates, query)
    top = ranked[:config.RESEARCHER_MAX_DOWNLOAD_CANDIDATES]
    moment_desc = screen.get("clip_moment") or query
    min_dur = config.RESEARCHER_MIN_CLIP_DURATION
    max_dur = min(config.RESEARCHER_MAX_CLIP_DURATION, screen["duration"] + 1.0)

    for cand in top:
        rec = {"video_id": cand["id"], "url": cand["url"],
               "title": cand.get("title", ""), "path": None, "moment_start": None}
        cand_acc.append(rec)

        # Metadata shortcut (free).
        meta = _get_video_metadata(cand["url"], cand["id"])
        if meta:
            ts = _find_timestamps_from_metadata(meta, query, min_dur, max_dur)
            if ts and ts.get("confidence") != "low":
                start, end = float(ts["start"]), float(ts["end"])
                used_ranges.setdefault(cand["id"], []).append((round(start, 2), round(end, 2)))
                rec["moment_start"] = round(start, 2)
                log.info("  query %r: metadata clip %s @%.1f-%.1fs", query, cand["id"], start, end)
                try:
                    import clip_knowledge as kb
                    kb.save_clip(video_id=cand["id"], url=cand["url"], title=cand.get("title", ""),
                                 start=round(start, 2), end=round(end, 2),
                                 description=ts.get("reason", ""), found_for_moment=moment_desc)
                except Exception:
                    pass
                return {"url": cand["url"], "start": round(start, 2), "end": round(end, 2)}

        # Gemini analysis (paid) — download then watch.
        vid = _download(cand["url"], cand["id"])
        if vid is None:
            continue
        rec["path"] = str(vid)
        time.sleep(config.GEMINI_RPM_SLEEP)
        exclusions = used_ranges.get(cand["id"], [])
        ts = _get_timestamps(vid, moment_desc, screen["duration"], exclude_ranges=exclusions)
        if ts is None:
            continue
        raw_start = ts.get("start")
        if isinstance(raw_start, (int, float)) and raw_start >= 0:
            rec["moment_start"] = float(raw_start)   # still hint even if rejected as a clip
        if ts.get("confidence") == "low":
            continue
        start, end = float(ts["start"]), float(ts["end"])
        if end - start < min_dur:
            continue
        if end - start > config.RESEARCHER_MAX_CLIP_DURATION:
            end = start + config.RESEARCHER_MAX_CLIP_DURATION
        used_ranges.setdefault(cand["id"], []).append((round(start, 2), round(end, 2)))
        log.info("  query %r: clip %s @%.1f-%.1fs (%s)", query, cand["id"], start, end,
                 ts.get("confidence"))
        try:
            import clip_knowledge as kb
            kb.save_clip(video_id=cand["id"], url=cand["url"], title=cand.get("title", ""),
                         start=round(start, 2), end=round(end, 2),
                         description=ts.get("reason", ""), found_for_moment=moment_desc)
            kb.extract_thumbnail(vid, round(start, 2), round(end, 2), cand["id"])
        except Exception:
            pass
        return {"url": cand["url"], "start": round(start, 2), "end": round(end, 2)}

    return None


def _make_still(screen: dict, si: int, cand_acc: list) -> Optional[tuple]:
    """Source a representative still for a footage-poor beat: prefer a frame from
    the best candidate already downloaded (at the Gemini moment hint, else a frame
    ~25% in), else the top candidate's YouTube thumbnail. Returns (still_path,
    detail) or None when there is no candidate to draw from at all."""
    config.STILLS_DIR.mkdir(parents=True, exist_ok=True)

    # 1) A frame from a downloaded candidate (best relevance/quality).
    for rec in cand_acc:
        p = rec.get("path")
        if not p or not Path(p).exists():
            continue
        path = Path(p)
        at = rec.get("moment_start")
        if not isinstance(at, (int, float)) or at <= 0:
            dur = _probe_duration(path) or 0.0
            at = min(8.0, dur * 0.25) if dur else 3.0
        out = config.STILLS_DIR / f"{_url_hash(rec['video_id'] + ':' + str(si))}.jpg"
        if _extract_still(path, at, out):
            return str(out), {"source": "candidate_frame", "video_id": rec["video_id"],
                              "at_seconds": round(at, 1)}

    # 2) A YouTube thumbnail from the top candidate (cheap, no download needed).
    for rec in cand_acc:
        out = config.STILLS_DIR / f"thumb_{_url_hash(rec['video_id'] + ':' + str(si))}.jpg"
        if _fetch_thumbnail(rec["video_id"], out):
            return str(out), {"source": "thumbnail", "video_id": rec["video_id"]}

    return None


# ── Single-clip window finding ────────────────────────────────────────────────

def _hero_source_query(spec: dict) -> Optional[str]:
    """The one source query for a single_clip spec: the top-level clip_description
    the CD named, else the clip_description shared by the most screens (the spine)."""
    top = spec.get("clip_description")
    if isinstance(top, str) and top.strip():
        return top.strip()
    from collections import Counter
    descs = [s.get("clip_description") for s in spec.get("screens", [])
             if s.get("clip_description")]
    if not descs:
        return None
    return Counter(descs).most_common(1)[0][0]


def _find_window(video_path: Path, description: str, key_moment: str,
                 target_len: float, min_len: float, source_dur: float) -> Optional[dict]:
    """Ask Gemini for the best continuous window of ONE source video. Returns the
    parsed window dict (validated) or None when no usable continuous stretch exists."""
    trimmed = _trim_for_upload(video_path, SINGLE_CLIP_PROFILE)
    if trimmed is None:
        return None

    prompt = SINGLE_CLIP_WINDOW_PROMPT.format(
        target=target_len, min_len=min_len,
        description=description, key_moment=key_moment or "(unspecified)",
    )
    mb = trimmed.stat().st_size / (1024 * 1024)
    log.info("single_clip: analysing %s (%.1f MB) for a %.0fs window",
             trimmed.name, mb, target_len)
    get_tracker().record_video_call(
        stage="researcher", video_seconds=SINGLE_CLIP_PROFILE["duration"],
        prompt_words=len(prompt.split()),
    )
    client = gemini.get_client()
    result = _upload_and_analyse(client, trimmed, prompt, config.RESEARCHER_GEMINI_TIMEOUT)
    if not isinstance(result, dict):
        return None
    if result.get("confidence") == "low":
        log.info("single_clip: low-confidence window for %r — rejecting", description)
        return None

    try:
        start = float(result["window_start"])
        end = float(result["window_end"])
    except (KeyError, TypeError, ValueError):
        return None
    if start < 0 or end <= start:
        return None
    # The window must be at least min_len of continuous footage and sit inside the
    # part of the source we actually have.
    if (end - start) < min_len:
        log.info("single_clip: window only %.1fs (< %.0fs floor) — rejecting", end - start, min_len)
        return None
    cap = source_dur if source_dur else end
    if start + min_len > cap:
        log.info("single_clip: window starts too late to fit in a %.1fs source — rejecting", cap)
        return None

    km = result.get("key_moment_ts")
    try:
        km = float(km) if km is not None else None
    except (TypeError, ValueError):
        km = None
    try:
        sx = float(result.get("subject_x", 0.5))
    except (TypeError, ValueError):
        sx = 0.5
    sx = min(1.0, max(0.0, sx))
    return {"window_start": round(start, 2), "window_end": round(end, 2),
            "key_moment_ts": (round(km, 2) if km is not None else None),
            "subject_x": round(sx, 3), "heavy_overlay": bool(result.get("heavy_overlay")),
            "confidence": result.get("confidence"), "reason": result.get("reason", "")}


def _research_single_clip(spec: dict) -> Optional[dict]:
    """Resolve the ONE source + continuous window a single_clip spec rides on.

    Searches for the hero source, downloads the top candidates, and asks Gemini for
    the best continuous window of the FIRST source that has a long-enough on-story
    stretch. Returns a hero_clip dict {url, window_start, window_end, key_moment_ts,
    subject_x, source_duration} on success, or None if no source has a long enough
    continuous relevant window (the caller then falls back to montage)."""
    query = _hero_source_query(spec)
    if not query:
        log.warning("single_clip: spec names no hero source query — cannot resolve")
        return None

    runtime = sum(s.get("duration", 0) for s in spec.get("screens", []))
    # Ask for a little MORE than the runtime so the renderer has slack to slide the
    # window and land the key moment under the payoff beats; require at least the
    # runtime of continuous footage so it can always be filled.
    target_len = min(max(runtime + 8.0, config.SINGLE_CLIP_TARGET_MIN),
                     config.SINGLE_CLIP_TARGET_MAX)
    min_len = max(runtime, config.SINGLE_CLIP_TARGET_MIN)
    key_moment = spec.get("key_moment") or next(
        (s.get("clip_moment") for s in spec.get("screens", []) if s.get("clip_moment")), "")

    candidates = _search_youtube(query, config.RESEARCHER_MAX_SEARCH_RESULTS)
    if not candidates:
        log.warning("single_clip: no YouTube results for hero query %r", query)
        return None
    ranked = _rank_candidates(candidates, query)

    def _hero(cand, win, source_dur):
        return {
            "url": cand["url"],
            "window_start": win["window_start"], "window_end": win["window_end"],
            "key_moment_ts": win["key_moment_ts"], "subject_x": win["subject_x"],
            "source_duration": round(source_dur, 2) if source_dur else None,
        }

    overlaid_fallback = None  # an acceptable window buried under graphics — used only
                              # if no CLEANER source turns up (still beats montage).
    for cand in ranked[:config.SINGLE_CLIP_MAX_CANDIDATES]:
        vid = _download(cand["url"], cand["id"])
        if vid is None:
            continue
        source_dur = _probe_duration(vid) or 0.0
        if source_dur and source_dur < min_len:
            log.info("single_clip: source %s only %.0fs (< %.0fs floor) — skipping",
                     cand["id"], source_dur, min_len)
            continue
        time.sleep(config.GEMINI_RPM_SLEEP)
        win = _find_window(vid, query, key_moment, target_len, min_len, source_dur)
        if win is None:
            continue
        if win.get("heavy_overlay"):
            # Prefer a cleaner alternative — remember this and keep looking.
            log.info("single_clip: %s window is under heavy burned-in graphics — "
                     "holding as fallback, trying a cleaner source", cand["id"])
            if overlaid_fallback is None:
                overlaid_fallback = _hero(cand, win, source_dur)
            continue
        log.info("single_clip: HERO %s window %.1f-%.1fs (key @%.1fs, subject_x=%.2f) — %s",
                 cand["id"], win["window_start"], win["window_end"],
                 win["key_moment_ts"] or -1, win["subject_x"], win.get("reason", ""))
        return _hero(cand, win, source_dur)

    if overlaid_fallback is not None:
        log.warning("single_clip: no clean source found for %r — using the overlaid "
                    "window (still one continuous take, beats montage)", query)
        return overlaid_fallback
    log.warning("single_clip: no candidate had a continuous %.0fs+ window for %r", min_len, query)
    return None


# ── Main research function ────────────────────────────────────────────────────

def research_spec(spec: dict, selective: bool = True) -> dict:
    log.info("Stage 2: Researcher — %s", spec.get("id", "?"))

    # Single-clip event format: resolve the ONE continuous window the whole video
    # rides on. On success we skip per-screen footage research entirely (the render
    # ignores per-screen clips). On failure we fall back to montage — the spec is a
    # valid montage spec (its spine screens carry the hero query), and the failure
    # is flagged loudly rather than faking a window.
    if spec.get("format") == "single_clip":
        if selective and isinstance(spec.get("hero_clip"), dict) and spec["hero_clip"].get("url"):
            log.info("single_clip: hero_clip already resolved — skipping research")
            return spec
        hero = _research_single_clip(spec)
        if hero is not None:
            spec["hero_clip"] = hero
            _record_tier(-1, "single_clip_window", url=hero["url"],
                         window=f"{hero['window_start']:.1f}-{hero['window_end']:.1f}s")
            return spec
        # No usable continuous window — demote to montage and flag for a human.
        spec["format"] = "montage"
        spec["hero_clip"] = None
        try:
            from run_record import get_active_record
            rec = get_active_record()
            if rec is not None:
                rec.add_flag(
                    f"single_clip: no continuous {config.SINGLE_CLIP_TARGET_MIN:.0f}s+ window "
                    f"found for {_hero_source_query(spec)!r} — fell back to montage"
                )
        except Exception as e:
            log.debug("single_clip fallback flag skipped: %s", e)
        log.warning("single_clip: falling back to montage research for %s", spec.get("id", "?"))

    screens = spec.get("screens", [])
    clip_screens = []

    for i, s in enumerate(screens):
        if s.get("clip_description"):
            if selective and s.get("clip") and isinstance(s["clip"], dict) and s["clip"].get("url"):
                log.info("Screen %d: already has clip → skipping", i + 1)
                continue
            clip_screens.append((i, s))

    if not clip_screens:
        log.info("No screens need footage")
        return spec

    log.info("%d screen(s) need footage", len(clip_screens))

    # Track claimed timestamp ranges per video so each screen gets a unique moment
    used_ranges: dict[str, list] = {}

    for idx, (si, screen) in enumerate(clip_screens):
        desc = screen["clip_description"]
        ambient = screen.get("clip_mode") == "ambient"
        moment_desc = screen.get("clip_moment") or desc
        log.info("Screen %d: '%s'%s", si + 1, desc, " [ambient]" if ambient else "")

        # Ambient mode: just find the best video URL — no timestamp analysis needed.
        if ambient:
            candidates = _search_youtube(desc, config.RESEARCHER_MAX_SEARCH_RESULTS)
            if candidates:
                best = _rank_candidates(candidates, desc)[0]
                screen["clip"] = {"url": best["url"], "start": 0.0, "end": None}
                screen["panel"] = True
                _record_tier(si, "clip", query=desc, video_id=best["id"], mode="ambient")
                log.info("Screen %d: ambient clip → %s ('%s')", si + 1, best["id"], best.get("title", "?")[:60])
            else:
                _record_fallback(si, f"ambient clip search returned no YouTube results for {desc!r}")
                screen["clip"] = None
                _record_tier(si, "gradient_forced", query=desc, reason="ambient: no results")
            continue

        # ── Knowledge base check (tier-1 shortcut, before any search/Gemini) ──
        if screen.get("clip_moment"):
            import clip_knowledge as kb
            kb_match = kb.match_from_knowledge(moment_desc)
            if kb_match:
                screen["clip"] = {"url": kb_match.url, "start": kb_match.start, "end": kb_match.end}
                screen["panel"] = True
                used_ranges.setdefault(kb_match.video_id, []).append((kb_match.start, kb_match.end))
                _record_tier(si, "clip", query=desc, source="knowledge_base", video_id=kb_match.video_id)
                log.info("Screen %d: knowledge base hit → %s (%.1f–%.1fs)",
                         si + 1, kb_match.clip_id, kb_match.start, kb_match.end)
                continue

        # ── Footage fallback ladder ──────────────────────────────────────────
        cand_acc: list = []   # every candidate seen (with downloaded path) — still source

        # Tier 1: the specified query.
        clip = _resolve_query(desc, screen, used_ranges, cand_acc)
        tier = "clip"
        queries_tried = [desc]

        # Tier 2: alternate query phrasings (cheap hit-rate boost).
        if clip is None and config.RESEARCHER_ALT_QUERIES > 0:
            context = f"{spec.get('label', '')}: {screen.get('text', '')}. {moment_desc}"
            alts = _alternate_queries(desc, screen.get("clip_moment"), context)
            log.info("Screen %d: first query missed — trying %d alternate(s): %s",
                     si + 1, len(alts), alts)
            for alt in alts:
                queries_tried.append(alt)
                clip = _resolve_query(alt, screen, used_ranges, cand_acc)
                if clip is not None:
                    tier = "alt_query_clip"
                    log.info("Screen %d: alt-query HIT with %r", si + 1, alt)
                    break

        if clip is not None:
            screen["clip"] = clip
            screen["still"] = None
            screen["panel"] = True
            _record_tier(si, tier, query=queries_tried[-1], alt_queries_tried=queries_tried[1:],
                         video_id=clip.get("url", "").split("v=")[-1][:11])
            continue

        # Tier 3: Ken Burns still — NEVER a blank gradient for a footage-wanted beat.
        still = _make_still(screen, si, cand_acc)
        if still:
            screen["still"] = still[0]
            screen["clip"] = None
            screen["panel"] = True
            _record_tier(si, "kenburns_still", query=desc, alt_queries_tried=queries_tried[1:], **still[1])
            _record_fallback(si, f"no usable VIDEO for {desc!r} after {len(queries_tried)} "
                                 f"query phrasing(s) — Ken Burns still ({still[1].get('source')})")
            log.warning("[researcher] Screen %d -> KEN BURNS STILL (%s) after %d query phrasing(s)",
                        si + 1, still[1].get("source"), len(queries_tried))
            continue

        # Tier 4: forced gradient ONLY when no still source exists at all (rare).
        screen["clip"] = None
        screen["still"] = None
        _record_tier(si, "gradient_forced", query=desc, alt_queries_tried=queries_tried[1:])
        _record_fallback(si, f"NO footage source for {desc!r} after {len(queries_tried)} "
                             f"query phrasing(s) — forced gradient (no candidate to still from)")
        log.warning("[researcher] Screen %d -> FORCED GRADIENT (no still source) after %d query phrasing(s)",
                    si + 1, len(queries_tried))

    # Record the CD's deliberate gradients (no clip_description) as their own tier.
    for i, s in enumerate(screens):
        if not s.get("clip_description") and not s.get("clip") and not s.get("still"):
            _record_tier(i, "intentional_gradient", reason="CD marked no footage (suspense/data beat)")

    clips = sum(1 for s in screens if s.get("clip"))
    stills = sum(1 for s in screens if s.get("still"))
    log.info("Research done: %d clip(s), %d Ken Burns still(s) over %d footage beat(s)",
             clips, stills, len(clip_screens))
    return spec


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)-5s %(message)s",
                        datefmt="%H:%M:%S")
    if len(sys.argv) < 2:
        print("Usage: python researcher.py <spec.json>")
        sys.exit(1)
    path = Path(sys.argv[1])
    data = json.loads(path.read_text(encoding="utf-8"))
    result = research_spec(data)
    out = path.with_stem(path.stem + "_researched")
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"→ {out}")
