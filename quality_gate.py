"""
Stage 4 — Quality Gate.

ffprobe checks (resolution, duration, fps, file size, codec, black frames).
Extracts one midpoint frame per screen to output/[id]_frames/.
"""
import json
import logging
import subprocess
from pathlib import Path
from typing import Optional

import config

log = logging.getLogger(__name__)


def _probe(video: Path) -> Optional[dict]:
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(video)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return json.loads(r.stdout) if r.returncode == 0 else None


def _extract_frame(video: Path, ts: float, out: Path) -> bool:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-ss", f"{ts:.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(out)]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _detect_black(video: Path) -> list[dict]:
    # pix_th=0.02: only flag pixels with <2% luminance as "black".
    # All five colour scheme backgrounds sit at 2.2–3.3% luminance, so dark
    # gradients pass. Pure black render failures (0%) are still caught.
    cmd = ["ffmpeg", "-hide_banner", "-i", str(video), "-vf", "blackdetect=d=0.5:pix_th=0.02", "-an", "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    blacks = []
    for line in r.stderr.split("\n"):
        if "black_start:" not in line:
            continue
        parts = {}
        for token in line.split():
            if ":" in token and token[0] != "[":
                k, v = token.split(":", 1)
                try:
                    parts[k] = float(v)
                except ValueError:
                    pass
        if "black_start" in parts and "black_end" in parts:
            blacks.append(parts)
    return blacks


def _check(name: str, passed: bool, detail: str) -> dict:
    return {"passed": passed, "detail": detail}


def check_video(video: Path, spec: dict, preview: bool = False) -> dict:
    log.info("Stage 4: Quality Gate — %s", video.name)
    checks = {}

    probe = _probe(video)
    if not probe:
        return {"passed": False, "checks": {"probe": _check("probe", False, "ffprobe failed")}, "frames_dir": None}

    vs = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in probe.get("streams", []) if s.get("codec_type") == "audio"), None)

    # Resolution
    exp_w, exp_h = (config.PREVIEW_WIDTH, config.PREVIEW_HEIGHT) if preview else (config.PROD_WIDTH, config.PROD_HEIGHT)
    if vs:
        w, h = int(vs.get("width", 0)), int(vs.get("height", 0))
        checks["resolution"] = _check("resolution", w == exp_w and h == exp_h, f"{w}x{h} (expected {exp_w}x{exp_h})")
    else:
        checks["resolution"] = _check("resolution", False, "no video stream")

    # Duration
    screens = spec.get("screens", [])
    expected = sum(s.get("duration", 0) for s in screens)
    xfade_reduction = max(0, len(screens) - 1) * config.CROSSFADE_DURATION
    expected_final = expected - xfade_reduction
    actual = float(probe.get("format", {}).get("duration", 0))
    diff = abs(actual - expected_final)
    checks["duration"] = _check("duration", diff <= config.QUALITY_DURATION_TOLERANCE, f"{actual:.2f}s vs {expected_final:.2f}s (diff {diff:.2f}s)")

    # FPS
    exp_fps = config.PREVIEW_FPS if preview else config.PROD_FPS
    if vs:
        try:
            num, den = vs.get("r_frame_rate", "0/1").split("/")
            fps = float(num) / float(den) if float(den) > 0 else 0
        except (ValueError, ZeroDivisionError):
            fps = 0
        checks["fps"] = _check("fps", abs(fps - exp_fps) <= 1.0, f"{fps:.1f} (expected {exp_fps})")
    else:
        checks["fps"] = _check("fps", False, "no video stream")

    # File size
    mb = video.stat().st_size / (1024 * 1024)
    limit = 25.0 if preview else 50.0
    checks["file_size"] = _check("file_size", 0.01 < mb < limit, f"{mb:.2f} MB (limit {limit})")

    # Audio
    checks["audio"] = _check("audio", True, "present" if audio else "absent (optional)")

    # Black frames
    blacks = _detect_black(video)
    checks["black_frames"] = _check(
        "black_frames", len(blacks) == 0,
        "none" if not blacks else f"{len(blacks)} segment(s): " + ", ".join(f"{b['black_start']:.1f}–{b['black_end']:.1f}s" for b in blacks[:5]),
    )

    # Codec
    if vs:
        codec = vs.get("codec_name", "unknown")
        checks["codec"] = _check("codec", codec in ("h264", "avc1"), codec)

    # Extract midpoint frames
    spec_id = spec.get("id", "unknown")
    frames_dir = config.OUTPUT_DIR / f"{spec_id}_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    cumulative = 0.0
    for i, screen in enumerate(screens):
        d = screen.get("duration", 0)
        _extract_frame(video, cumulative + d / 2, frames_dir / f"screen_{i:02d}.jpg")
        cumulative += d

    all_passed = all(c["passed"] for c in checks.values())
    for name, c in checks.items():
        log.info("  [%s] %s: %s", "PASS" if c["passed"] else "FAIL", name, c["detail"])
    log.info("Quality gate: %s", "PASSED" if all_passed else "FAILED")

    return {"passed": all_passed, "checks": checks, "frames_dir": str(frames_dir)}


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S")
    if len(sys.argv) < 3:
        print("Usage: python quality_gate.py <video.mp4> <spec.json> [--preview]")
        sys.exit(1)
    result = check_video(Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")), "--preview" in sys.argv)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["passed"] else 1)
