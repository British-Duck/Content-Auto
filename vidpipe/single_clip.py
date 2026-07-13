"""
Single-clip event renderer.

For a spec with format == "single_clip", the whole video rides ONE source: a
single continuous window of the single best source video, played for the full
runtime, with the text beats overlaid as a timeline of timed events. This is the
opposite of the montage path (vidpipe/renderer.py + assembler.py), which renders
one segment per screen and concatenates them — and which is left completely
untouched.

Why a separate path: single-continuous-moment stories (a race, a crash, a filmed
dramatic moment) have one real piece of footage with a natural arc (lead-up → key
moment → aftermath). Cutting them into per-screen segments forces the researcher to
find a fresh clip for every beat; the misses become blank gradients and repeated
footage. Playing the one window straight through removes those gaps by construction.

Visual treatment:
  • A focus-aware VERTICAL CROP fills the 9:16 frame, anchored horizontally on the
    subject (hero_clip.subject_x). When the source is so wide that a vertical crop
    would discard more than SINGLE_CLIP_CROP_MAX_DISCARD of the width (and risk
    cutting the subject out), we fall back to the blurred-background FIT — the whole
    frame shown in a band over a blurred copy of itself, so the subject is never
    lost. Neither is a naive centre-crop that chops the subject.
  • The text beats are burned in at their cumulative timestamps via timed overlays,
    so they track the on-screen action.

Internal cuts/zooms within the same source are allowed by design but not yet wired
(the footage carries its own motion); we never cut to a different clip, never insert
a gradient, and never repeat footage to fill.
"""
import logging
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

import config
from .clips import ensure_clip
from .schemes import get_scheme
from .spec import VideoSpec
from .text import render_overlay

log = logging.getLogger(__name__)

# Mirror the montage clip treatment so a single_clip video matches the house look.
_BG_BLUR = 40
_BG_BRIGHTNESS = -0.25
_BG_SATURATION = 0.65
_FG_BRIGHTNESS = -0.04
_FG_SATURATION = 0.90


def beat_timeline(screens) -> List[Tuple[float, float]]:
    """Cumulative [start, end) for each text beat, in a continuous (no-crossfade)
    timeline. Pure function — the renderer and the tests both use it."""
    out: List[Tuple[float, float]] = []
    t = 0.0
    for s in screens:
        d = float(s.get("duration", 0) if isinstance(s, dict) else s.duration)
        out.append((round(t, 3), round(t + d, 3)))
        t += d
    return out


def aligned_start(window_start: float, window_end: float, key_moment_ts: Optional[float],
                  runtime: float, source_dur: float, key_fraction: float) -> float:
    """The start time of the runtime-long sub-window to play.

    Slides the window so the source's key moment lands at `key_fraction` of the
    runtime (so the payoff footage sits under the payoff beats), bounded so it
    stays inside the relevant window [window_start, window_end] and the source. A
    short-lead source (key moment near the window start) simply can't slide and
    plays from window_start. Pure function — unit-tested without ffmpeg."""
    lo = max(0.0, window_start)
    # Upper bound: keep start+runtime inside the relevant window and the source.
    hi = window_end - runtime
    if source_dur:
        hi = min(hi, source_dur - runtime)
    hi = max(lo, hi)  # if the window is exactly runtime-long there is no slack
    if key_moment_ts is None:
        return min(max(lo, lo), hi)
    desired = key_moment_ts - key_fraction * runtime
    return min(max(desired, lo), hi)


def fit_start_to_source(start: float, runtime: float, src_dur: float) -> Optional[float]:
    """Final guard against the fetched source being shorter than expected (a stale
    or truncated cache file, or a different variant than the researcher probed).

    Returns the start time slid back so start+runtime fits inside src_dur, or None
    when the source cannot cover the runtime at all — in which case the render must
    FAIL loudly rather than let ffmpeg run out of frames and ship a cut-off video
    (the Hindenburg bug: 25.1s rendered of a 31.5s plan). Pure function."""
    if not src_dur or src_dur <= 0:
        return start  # unknown duration: nothing to clamp against
    if src_dur + 0.05 < runtime:
        return None
    return min(start, max(0.0, src_dur - runtime))


def _aligned_start(hero, runtime: float, src_dur: float) -> float:
    window_end = hero.window_end if hero.window_end > hero.window_start else \
        (src_dur or hero.window_start + runtime)
    start = aligned_start(hero.window_start, window_end, hero.key_moment_ts,
                          runtime, src_dur, config.SINGLE_CLIP_KEY_FRACTION)
    log.info("single_clip: play window %.1f-%.1fs (key @%s lands ~%.0f%% in)",
             start, start + runtime, hero.key_moment_ts,
             (((hero.key_moment_ts - start) / runtime * 100)
              if hero.key_moment_ts is not None and runtime else 0))
    return start


def _probe_dims(path: Path) -> Optional[Tuple[int, int, float]]:
    """(width, height, duration_seconds) of a video, or None on failure."""
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json",
           "-show_streams", "-show_format", str(path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    import json
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    vs = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not vs:
        return None
    w, h = int(vs.get("width", 0)), int(vs.get("height", 0))
    dur = float(data.get("format", {}).get("duration", 0) or 0)
    if w <= 0 or h <= 0:
        return None
    return w, h, dur


def vertical_chain(src_w: int, src_h: int, W: int, H: int, subject_x: float) -> str:
    """Filter chain from [0:v] to [base]: fit the source into the vertical frame.

    Decides between a focus-aware FILL crop and the blurred-background FIT based on
    how much width a crop would discard (see SINGLE_CLIP_CROP_MAX_DISCARD). Pure
    string-building — unit-tested without ffmpeg."""
    subject_x = min(1.0, max(0.0, subject_x))
    src_ar = src_w / src_h
    target_ar = W / H

    # A wider-than-target source means a vertical crop throws away width.
    if src_ar > target_ar:
        discard_w = 1.0 - (target_ar / src_ar)
    else:
        discard_w = 0.0  # source is narrow/portrait — width is kept, height is cropped

    if discard_w <= config.SINGLE_CLIP_CROP_MAX_DISCARD:
        # Focus-aware fill crop: cover the frame, then crop anchored on the subject.
        return (
            f"[0:v]setpts=PTS-STARTPTS,"
            f"scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H}:x='(iw-{W})*{subject_x:.3f}':y='(ih-{H})/2',"
            f"eq=brightness={_FG_BRIGHTNESS}:saturation={_FG_SATURATION},"
            f"setsar=1[base]"
        )

    # Too wide to crop without losing the subject — blurred-background fit so the
    # whole frame stays visible (identical treatment to a montage clip screen).
    return (
        f"[0:v]setpts=PTS-STARTPTS,split=2[bg][fg];"
        f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{H},boxblur={_BG_BLUR}:1,"
        f"eq=brightness={_BG_BRIGHTNESS}:saturation={_BG_SATURATION}[bgb];"
        f"[fg]scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"eq=brightness={_FG_BRIGHTNESS}:saturation={_FG_SATURATION}[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[base]"
    )


def overlay_chain(timeline: List[Tuple[float, float]]) -> str:
    """Chain that burns each beat's overlay PNG (inputs [1:v]..[N:v]) onto [base]
    during its time window, producing [vout]. Beat i uses input i+1."""
    if not timeline:
        return "[base]copy[vout]"
    parts = []
    src = "[base]"
    n = len(timeline)
    for i, (ts, te) in enumerate(timeline):
        inp = f"[{i + 1}:v]"
        dst = "[vout]" if i == n - 1 else f"[ov{i}]"
        # Inclusive lower bound, exclusive upper bound so adjacent beats don't both
        # show on the seam frame; the last beat runs to the end of the clip.
        last = i == n - 1
        cond = (f"gte(t,{ts:.3f})" if last
                else f"between(t,{ts:.3f},{te:.3f})*lt(t,{te:.3f})")
        parts.append(f"{src}{inp}overlay=0:0:enable='{cond}'{dst}")
        src = dst
    return ";".join(parts)


def _run_ffmpeg(args: list, desc: str) -> bool:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning"] + args
    log.debug("cmd: %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except FileNotFoundError:
        log.error("ffmpeg not found — install it and add to PATH")
        return False
    except subprocess.TimeoutExpired:
        log.error("%s timed out", desc)
        return False
    if result.returncode != 0:
        log.error("%s failed (exit %d):\n%s", desc, result.returncode, result.stderr.strip())
        return False
    return True


def render_single_clip(
    spec: VideoSpec,
    work: Path,
    output: Path,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    crf: int = 18,
    preset: str = "medium",
) -> bool:
    """Render the whole video on the one hero-clip window. Produces a SILENT video
    at `output` (the caller mixes the voiceover, exactly as for the montage path).
    Returns False if the source can't be fetched/probed so the caller can react."""
    hero = spec.hero_clip
    if hero is None:
        log.error("render_single_clip called without a resolved hero_clip")
        return False

    runtime = sum(s.duration for s in spec.screens)
    if runtime <= 0:
        log.error("single_clip runtime is zero")
        return False

    source = ensure_clip(hero.url)
    if source is None:
        log.error("single_clip: could not fetch hero source %s", hero.url)
        return False

    dims = _probe_dims(source)
    if dims is None:
        log.error("single_clip: could not probe hero source %s", source.name)
        return False
    src_w, src_h, src_dur = dims

    # The researcher recorded the duration of the file it analysed; a material
    # mismatch means we are looking at a DIFFERENT file (stale/truncated cache) —
    # say so loudly, because the window/key timestamps were chosen on the other one.
    if hero.source_duration and src_dur and abs(src_dur - hero.source_duration) > 2.0:
        log.warning(
            "single_clip: fetched source %s is %.1fs but research recorded %.1fs — "
            "cached file is stale or truncated (delete %s to force a clean re-download); "
            "window/key timestamps may not line up",
            source.name, src_dur, hero.source_duration, source,
        )

    start = _aligned_start(hero, runtime, src_dur)
    fitted = fit_start_to_source(start, runtime, src_dur)
    if fitted is None:
        log.error(
            "single_clip: source %s is only %.1fs — cannot cover the %.1fs runtime; "
            "refusing to render a truncated video",
            source.name, src_dur, runtime,
        )
        return False
    if fitted < start - 0.01:
        log.warning("single_clip: sliding start %.1fs -> %.1fs so the full runtime "
                    "fits inside the %.1fs source", start, fitted, src_dur)
    start = fitted

    # Build the per-beat overlay PNGs.
    scheme = get_scheme(spec.scheme)
    overlay_paths: List[Path] = []
    for i, screen in enumerate(spec.screens):
        img = render_overlay(
            screen_text=screen.text,
            screen_index=i,
            total_screens=len(spec.screens),
            label=spec.label,
            scheme=scheme,
            text_size=screen.text_size,
            subtitle=screen.subtitle,
            centered=False,          # News Dispatch layout B: top/bottom over footage
            scrim=True,              # text always sits over footage here
            dim=False,
            width=width,
            height=height,
        )
        p = work / f"sc_overlay_{i:03d}.png"
        img.save(str(p), "PNG")
        overlay_paths.append(p)

    timeline = beat_timeline([{"duration": s.duration} for s in spec.screens])

    filter_complex = vertical_chain(src_w, src_h, width, height, hero.subject_x) + ";" \
        + overlay_chain(timeline)

    # Input 0 = the source window (seek + bounded duration). Inputs 1..N = looped
    # overlay stills consumed by the timed overlay chain.
    args = ["-ss", f"{start:.3f}", "-t", f"{runtime:.3f}", "-i", str(source)]
    for p in overlay_paths:
        args += ["-loop", "1", "-framerate", str(fps), "-i", str(p)]
    args += [
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-t", f"{runtime:.3f}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an",
        "-preset", preset, "-crf", str(crf),
        "-r", str(fps), "-g", str(fps),
        "-movflags", "+faststart",
        str(output),
    ]
    log.info("single_clip: %s window %.1f–%.1fs (%.1fs), %d beats, subject_x=%.2f",
             source.name, start, start + runtime, runtime, len(spec.screens), hero.subject_x)
    ok = _run_ffmpeg(args, "single_clip render")

    for p in overlay_paths:
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    return ok
