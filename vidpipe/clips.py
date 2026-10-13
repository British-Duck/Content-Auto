import hashlib
import logging
import re
import subprocess
from pathlib import Path
from typing import Optional

import config

log = logging.getLogger(__name__)

# Shared with researcher._download (same directory, same YouTube-ID key), so a
# video the researcher analysed is a cache HIT here and the renderer works on
# the exact file that was probed. Absolute via config so the hit does not
# depend on the current working directory.
CACHE_DIR = Path(config.CACHE_DIR)


def _video_id(url: str) -> str:
    m = re.search(r"(?:v=|/v/|youtu\.be/|/shorts/)([a-zA-Z0-9_-]{11})", url)
    if m:
        return m.group(1)
    return hashlib.md5(url.encode()).hexdigest()[:12]


def _probe_duration(path: Path) -> Optional[float]:
    """Container-level duration via ffprobe, or None when the file is unreadable."""
    cmd = ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
           "-of", "csv=p=0", str(path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return float(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        return None


def ensure_clip(url: str) -> Optional[Path]:
    CACHE_DIR.mkdir(exist_ok=True)
    vid = _video_id(url)

    for ext in (".mp4", ".webm", ".mkv"):
        cached = CACHE_DIR / f"{vid}{ext}"
        if cached.exists() and cached.stat().st_size > 0:
            log.info("Clip cache hit: %s", cached.name)
            return cached

    log.info("Downloading clip %s ...", vid)
    template = str(CACHE_DIR / f"{vid}.%(ext)s")

    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
                      "best[height<=1080][ext=mp4]/best",
                "--merge-output-format", "mp4",
                "-o", template,
                "--no-playlist",
                "--no-overwrites",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            log.error("yt-dlp failed:\n%s", result.stderr.strip())
            _drop_partial(vid)
            return None
    except FileNotFoundError:
        log.error("yt-dlp not found — install with: pip install yt-dlp")
        return None
    except subprocess.TimeoutExpired:
        # A killed download/merge can leave a nonzero but TRUNCATED file that
        # would be accepted as a cache hit on every later run — the silent
        # source-shorter-than-expected failure. Never leave it behind.
        log.error("yt-dlp timed out for %s — removing any partial file", url)
        _drop_partial(vid)
        return None

    for ext in (".mp4", ".webm", ".mkv"):
        cached = CACHE_DIR / f"{vid}{ext}"
        if cached.exists():
            if _probe_duration(cached) is None:
                log.error("Downloaded file %s is unreadable (truncated merge?) — "
                          "deleting so it cannot poison the cache", cached.name)
                _drop_partial(vid)
                return None
            log.info("Downloaded: %s (%.1f MB)", cached.name, cached.stat().st_size / 1e6)
            return cached

    log.error("Download finished but file not found for %s", vid)
    return None


def _drop_partial(vid: str) -> None:
    for ext in (".mp4", ".webm", ".mkv", ".part"):
        p = CACHE_DIR / f"{vid}{ext}"
        try:
            if p.exists():
                p.unlink()
        except OSError as e:
            log.warning("Could not remove partial %s: %s", p.name, e)
