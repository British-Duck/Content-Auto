import hashlib
import logging
import re
import subprocess
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

CACHE_DIR = Path(".clip_cache")


def _video_id(url: str) -> str:
    m = re.search(r"(?:v=|/v/|youtu\.be/)([a-zA-Z0-9_-]{11})", url)
    if m:
        return m.group(1)
    return hashlib.md5(url.encode()).hexdigest()[:12]


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
            return None
    except FileNotFoundError:
        log.error("yt-dlp not found — install with: pip install yt-dlp")
        return None
    except subprocess.TimeoutExpired:
        log.error("yt-dlp timed out for %s", url)
        return None

    for ext in (".mp4", ".webm", ".mkv"):
        cached = CACHE_DIR / f"{vid}{ext}"
        if cached.exists():
            log.info("Downloaded: %s (%.1f MB)", cached.name, cached.stat().st_size / 1e6)
            return cached

    log.error("Download finished but file not found for %s", vid)
    return None
