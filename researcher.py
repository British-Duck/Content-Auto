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


# ── Main research function ────────────────────────────────────────────────────

def research_spec(spec: dict, selective: bool = True) -> dict:
    log.info("Stage 2: Researcher — %s", spec.get("id", "?"))
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
        log.info("Screen %d: '%s'%s", si + 1, desc, " [ambient]" if ambient else "")

        # Ambient mode: just find the best video URL — no timestamp analysis needed.
        if ambient:
            candidates = _search_youtube(desc, config.RESEARCHER_MAX_SEARCH_RESULTS)
            if not candidates:
                log.warning("\n[researcher] Screen %d → GRADIENT. Reason: ambient — no YouTube results\n  clip_description: %r", si + 1, desc)
                screen["clip"] = None
            else:
                best = _rank_candidates(candidates, desc)[0]
                screen["clip"] = {"url": best["url"], "start": 0.0, "end": None}
                screen["panel"] = True
                log.info("Screen %d: ambient clip → %s ('%s')", si + 1, best["id"], best.get("title", "?")[:60])
            continue

        # ── Knowledge base check (before any YouTube search or Gemini call) ──
        moment_desc = screen.get("clip_moment") or desc
        if screen.get("clip_moment"):
            import clip_knowledge as kb
            kb_match = kb.match_from_knowledge(moment_desc)
            if kb_match:
                screen["clip"] = {
                    "url": kb_match.url,
                    "start": kb_match.start,
                    "end": kb_match.end,
                }
                screen["panel"] = True
                used_ranges.setdefault(kb_match.video_id, []).append(
                    (kb_match.start, kb_match.end)
                )
                log.info(
                    "Screen %d: knowledge base hit → %s (%.1f–%.1fs)%s",
                    si + 1, kb_match.clip_id, kb_match.start, kb_match.end,
                    " [verified]" if kb_match.verified else "",
                )
                continue

        diag = {
            "clip_description": desc,
            "search_query": desc,
            "candidates_found": 0,
            "candidates_tried": [],
        }

        candidates = _search_youtube(desc, config.RESEARCHER_MAX_SEARCH_RESULTS)
        diag["candidates_found"] = len(candidates)

        if not candidates:
            _log_gradient(si + 1, diag)
            screen["clip"] = None
            continue

        ranked = _rank_candidates(candidates, desc)
        top_candidates = ranked[:config.RESEARCHER_MAX_DOWNLOAD_CANDIDATES]
        log.info("Ranked %d candidates, trying top %d", len(ranked), len(top_candidates))

        found = False
        for cand in top_candidates:
            cand_diag = {
                "video_id": cand["id"],
                "title": cand.get("title", "?"),
                "url": cand["url"],
                "metadata_check": "not attempted",
                "gemini_attempted": False,
                "gemini_result": None,
                "skip_reason": None,
            }

            # ── Step 1: metadata shortcut (free) ──────────────────────────────
            # Metadata is a shortcut, NEVER a gate. If it finds nothing,
            # Gemini analysis MUST still run on this candidate.
            meta = _get_video_metadata(cand["url"], cand["id"])
            if meta:
                ts_from_meta = _find_timestamps_from_metadata(
                    meta, desc,
                    config.RESEARCHER_MIN_CLIP_DURATION,
                    min(config.RESEARCHER_MAX_CLIP_DURATION, screen["duration"] + 1.0),
                )
                if ts_from_meta and ts_from_meta.get("confidence") != "low":
                    start, end = float(ts_from_meta["start"]), float(ts_from_meta["end"])
                    screen["clip"] = {"url": cand["url"], "start": round(start, 2),
                                      "end": round(end, 2)}
                    screen["panel"] = True
                    found = True
                    used_ranges.setdefault(cand["id"], []).append((round(start, 2), round(end, 2)))
                    cand_diag["metadata_check"] = (
                        f"HIT ({ts_from_meta['confidence']}) → "
                        f"{start:.1f}–{end:.1f}s — {ts_from_meta.get('reason', '')}"
                    )
                    log.info("Screen %d: clip from metadata (%.1f–%.1fs)", si + 1, start, end)
                    diag["candidates_tried"].append(cand_diag)
                    # Save to knowledge base
                    try:
                        import clip_knowledge as kb
                        kb.save_clip(
                            video_id=cand["id"], url=cand["url"],
                            title=cand.get("title", ""),
                            start=round(start, 2), end=round(end, 2),
                            description=ts_from_meta.get("reason", ""),
                            found_for_moment=moment_desc,
                        )
                    except Exception:
                        pass
                    break
                else:
                    chapters_n = len(meta.get("chapters") or [])
                    has_desc = bool(meta.get("description", "").strip())
                    cand_diag["metadata_check"] = (
                        f"no match — {chapters_n} chapters, "
                        f"description {'present' if has_desc else 'absent'}"
                    )
                    log.debug(
                        "Screen %d [%s]: metadata no match (%d chapters, desc=%s)"
                        " → escalating to Gemini",
                        si + 1, cand["id"], chapters_n, has_desc,
                    )
            else:
                cand_diag["metadata_check"] = "fetch failed"
                log.debug(
                    "Screen %d [%s]: metadata fetch failed → escalating to Gemini",
                    si + 1, cand["id"],
                )

            # ── Step 2: Gemini video analysis (paid) ──────────────────────────
            # Runs unconditionally when metadata finds nothing.
            # Upload the video and actually watch it — this is the accurate path.
            vid = _download(cand["url"], cand["id"])
            if vid is None:
                cand_diag["gemini_attempted"] = False
                cand_diag["skip_reason"] = "download failed"
                diag["candidates_tried"].append(cand_diag)
                continue

            time.sleep(config.GEMINI_RPM_SLEEP)
            cand_diag["gemini_attempted"] = True
            exclusions = used_ranges.get(cand["id"], [])
            if exclusions:
                log.info("Screen %d [%s]: passing %d exclusion range(s) to Gemini",
                         si + 1, cand["id"], len(exclusions))
            # clip_moment (if set) is a precise visual description for Gemini.
            # clip_description is the search query — too generic for timestamp analysis.
            moment_desc = screen.get("clip_moment") or desc
            if screen.get("clip_moment"):
                log.info("Screen %d: using clip_moment for analysis: '%s'",
                         si + 1, screen["clip_moment"])
            ts = _get_timestamps(vid, moment_desc, screen["duration"], exclude_ranges=exclusions)

            if ts is None:
                cand_diag["gemini_result"] = "None — timeout or API error"
                cand_diag["skip_reason"] = "Gemini returned None (timeout or error)"
                diag["candidates_tried"].append(cand_diag)
                continue

            confidence = ts.get("confidence", "unknown")
            reason_text = ts.get("reason", "")
            start_raw = ts.get("start", -1)
            end_raw = ts.get("end", -1)
            cand_diag["gemini_result"] = (
                f"confidence={confidence} start={start_raw} end={end_raw} — {reason_text}"
            )

            if confidence == "low":
                cand_diag["skip_reason"] = f"Gemini: low confidence — {reason_text}"
                diag["candidates_tried"].append(cand_diag)
                continue

            start, end = float(start_raw), float(end_raw)
            dur = end - start
            if dur < config.RESEARCHER_MIN_CLIP_DURATION:
                cand_diag["skip_reason"] = (
                    f"clip too short ({dur:.1f}s < {config.RESEARCHER_MIN_CLIP_DURATION}s min)"
                )
                diag["candidates_tried"].append(cand_diag)
                continue
            if dur > config.RESEARCHER_MAX_CLIP_DURATION:
                end = start + config.RESEARCHER_MAX_CLIP_DURATION

            screen["clip"] = {"url": cand["url"], "start": round(start, 2),
                              "end": round(end, 2)}
            screen["panel"] = True
            found = True
            used_ranges.setdefault(cand["id"], []).append((round(start, 2), round(end, 2)))
            log.info("Screen %d: clip set (%.1f–%.1fs, %s)", si + 1, start, end, confidence)
            diag["candidates_tried"].append(cand_diag)
            # Save to knowledge base and extract thumbnail for verification
            try:
                import clip_knowledge as kb
                seg = kb.save_clip(
                    video_id=cand["id"], url=cand["url"],
                    title=cand.get("title", ""),
                    start=round(start, 2), end=round(end, 2),
                    description=reason_text,
                    found_for_moment=moment_desc,
                )
                kb.extract_thumbnail(vid, round(start, 2), round(end, 2), cand["id"])
            except Exception:
                pass
            break

        if not found:
            _log_gradient(si + 1, diag)
            screen["clip"] = None

    clips = sum(1 for s in screens if s.get("clip"))
    log.info("Research done: %d/%d clips found", clips, len(clip_screens))
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
