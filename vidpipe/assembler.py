"""
Video segment concatenation with crossfade transitions and forced keyframes.

Always re-encodes (no stream-copy) to guarantee:
  - I-frames at every screen transition boundary (clean seeking)
  - Consistent encoding parameters across all segments
  - Optional xfade crossfade blends between screens
"""
import logging
import subprocess
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)

CROSSFADE_DEFAULT = 0.3  # seconds


def concat_segments(
    segments: List[Path],
    output: Path,
    screen_durations: List[float],
    crf: int = 18,
    preset: str = "medium",
    fps: int = 30,
    crossfade: float = CROSSFADE_DEFAULT,
) -> bool:
    """
    Concatenate rendered segment files into a single video.

    segments        : list of .mp4 segment paths in order
    screen_durations: duration (seconds) of each segment — must be same length as segments
    crossfade       : blend duration between screens (0 = hard cut)
    """
    if not segments:
        log.error("No segments to concatenate")
        return False

    if len(segments) != len(screen_durations):
        log.error("Segment count %d != duration count %d", len(segments), len(screen_durations))
        return False

    if len(segments) == 1:
        return _encode_single(segments[0], output, crf, preset, fps)

    # Skip crossfade if any segment is too short to blend cleanly
    min_dur = min(screen_durations)
    if crossfade > 0 and min_dur <= crossfade + 0.05:
        log.info(
            "Segment too short for crossfade (min=%.2fs, crossfade=%.2fs) — using hard cuts",
            min_dur, crossfade,
        )
        crossfade = 0.0

    if crossfade > 0:
        return _concat_xfade(segments, screen_durations, output, crf, preset, fps, crossfade)
    else:
        return _concat_keyframes(segments, screen_durations, output, crf, preset, fps)


# ── Single-segment passthrough ─────────────────────────────────────────────

def _encode_single(seg: Path, output: Path, crf: int, preset: str, fps: int) -> bool:
    log.info("Single segment — encoding to output ...")
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-i", str(seg),
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-r", str(fps), "-g", str(fps),
        "-movflags", "+faststart",
        str(output),
    ]
    return _run(cmd, "single-segment encode")


# ── Hard-cut concat with forced keyframes ─────────────────────────────────

def _concat_keyframes(
    segments: List[Path],
    durations: List[float],
    output: Path,
    crf: int,
    preset: str,
    fps: int,
) -> bool:
    list_path = output.with_suffix(".concat.txt")
    _write_concat_list(list_path, segments)
    log.info("Concatenating %d segments (hard cut, forced keyframes) ...", len(segments))

    key_str = _keyframe_str(durations, crossfade=0.0)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-f", "concat", "-safe", "0",
        "-i", str(list_path),
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-r", str(fps), "-g", str(fps),
        "-force_key_frames", key_str,
        "-movflags", "+faststart",
        str(output),
    ]
    ok = _run(cmd, "concat+keyframes")
    _rm(list_path)
    if ok:
        log.info("Concat done: %s (%.1f MB)", output.name, output.stat().st_size / 1e6)
    return ok


# ── Crossfade concat with xfade filter ────────────────────────────────────

def _concat_xfade(
    segments: List[Path],
    durations: List[float],
    output: Path,
    crf: int,
    preset: str,
    fps: int,
    crossfade: float,
) -> bool:
    log.info(
        "Concatenating %d segments (%.2fs crossfade) ...", len(segments), crossfade
    )

    inputs: List[str] = []
    for seg in segments:
        inputs += ["-i", str(seg)]

    filter_complex = _xfade_filter(durations, crossfade)
    key_str = _keyframe_str(durations, crossfade)

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
    ] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-r", str(fps), "-g", str(fps),
        "-force_key_frames", key_str,
        "-movflags", "+faststart",
        str(output),
    ]

    ok = _run(cmd, "xfade concat")
    if ok:
        log.info("Concat done: %s (%.1f MB)", output.name, output.stat().st_size / 1e6)
    return ok


def _xfade_filter(durations: List[float], crossfade: float) -> str:
    """
    Build a chained xfade filter_complex string for N segments.

    xfade offset for transition i = sum(durations[0..i]) - crossfade*(i+1)
    This is the timestamp (in [0:v] timeline) where that transition starts.
    """
    parts = []
    cumulative = 0.0

    for i in range(len(durations) - 1):
        src1 = "[0:v]" if i == 0 else f"[xf{i}]"
        src2 = f"[{i + 1}:v]"
        dst = "[vout]" if i == len(durations) - 2 else f"[xf{i + 1}]"

        offset = max(0.01, round(cumulative + durations[i] - crossfade * (i + 1), 3))
        parts.append(
            f"{src1}{src2}xfade=transition=fade:duration={crossfade:.3f}:offset={offset:.3f}{dst}"
        )
        cumulative += durations[i]

    return ";".join(parts)


def _keyframe_str(durations: List[float], crossfade: float) -> str:
    """
    Compute forced-keyframe timestamps accounting for crossfade shortening.
    Keyframe at t=0 + start of each subsequent screen in the final video.
    """
    times = [0.0]
    cumulative = 0.0
    for i, dur in enumerate(durations[:-1]):
        cumulative += dur
        # In the output, the (i+1)th screen's transition begins at:
        # cumulative - crossfade*(i+1)
        kf = max(0.0, round(cumulative - crossfade * (i + 1), 3))
        times.append(kf)
    return ",".join(f"{t:.3f}" for t in times)


# ── Helpers ───────────────────────────────────────────────────────────────

def _write_concat_list(path: Path, segments: List[Path]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for seg in segments:
            safe = str(seg.resolve()).replace("\\", "/")
            f.write(f"file '{safe}'\n")


def _run(cmd: List[str], desc: str) -> bool:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        log.error("ffmpeg not found")
        return False
    except subprocess.TimeoutExpired:
        log.error("%s timed out", desc)
        return False
    if result.returncode != 0:
        log.error("%s failed:\n%s", desc, result.stderr.strip())
        return False
    return True


def _rm(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
