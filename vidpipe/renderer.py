import logging
import subprocess
from pathlib import Path

from PIL import Image

import config
from .clips import ensure_clip
from .schemes import ColorScheme, get_scheme
from .spec import Screen, VideoSpec
from .text import create_gradient, render_overlay

log = logging.getLogger(__name__)

# Shared encoding flags used by every segment — ensures consistent concat/xfade
_ENCODE_FLAGS = [
    "-c:v", "libx264",
    "-pix_fmt", "yuv420p",
    "-an",  # no audio in segments
]

# Background fill: blur radius and how far the backdrop is darkened/desaturated
# so the in-focus foreground clip stands out against it.
_BG_BLUR = 40
_BG_BRIGHTNESS = -0.25
_BG_SATURATION = 0.65
# Foreground (the actual clip, shown in full) gets a gentle correction only.
_FG_BRIGHTNESS = -0.04
_FG_SATURATION = 0.90


def _run_ffmpeg(args: list, desc: str = "ffmpeg") -> bool:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning"] + args
    log.debug("cmd: %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        log.error("ffmpeg not found — install it and add to PATH")
        return False
    except subprocess.TimeoutExpired:
        log.error("%s timed out", desc)
        return False
    if result.returncode != 0:
        log.error("%s failed (exit %d):\n%s", desc, result.returncode,
                  result.stderr.strip())
        return False
    return True


def render_screen(
    screen: Screen,
    screen_index: int,
    spec: VideoSpec,
    output_path: Path,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    crf: int = 18,
    preset: str = "medium",
) -> bool:
    scheme = get_scheme(spec.scheme)

    # Layout style: spec override, else global default.
    #   B — every screen top/bottom (clip screens get scrims).
    #   C — clip screens centred over footage (dim); gradient screens top/bottom.
    layout = (getattr(spec, "layout", None) or config.LAYOUT_STYLE).upper()
    centered = layout == "C" and screen.panel

    overlay_img = render_overlay(
        screen_text=screen.text,
        screen_index=screen_index,
        total_screens=len(spec.screens),
        label=spec.label,
        scheme=scheme,
        text_size=screen.text_size,
        subtitle=screen.subtitle,
        centered=centered,
        scrim=screen.panel and not centered,   # top/bottom text over footage
        dim=screen.panel and centered,          # centred text over footage
        width=width,
        height=height,
    )

    overlay_path = output_path.with_suffix(".overlay.png")
    overlay_img.save(str(overlay_path), "PNG")

    success = False

    if screen.clip:
        success = _render_clip_screen(
            screen, overlay_path, output_path,
            width, height, fps, crf, preset,
        )
        if not success:
            log.warning("Clip failed for screen %d — falling back to gradient",
                        screen_index + 1)
    elif screen.still:
        # Footage fallback ladder (tier 3): a relevant still with a slow Ken Burns
        # push, instead of a blank gradient.
        success = _render_still_screen(
            screen, overlay_path, output_path,
            width, height, fps, crf, preset,
        )
        if not success:
            log.warning("Still failed for screen %d — falling back to gradient",
                        screen_index + 1)

    if not success:
        # Gradient fallback — the clip failed to render or none was found. Re-render
        # the overlay without the footage-legibility treatment (no scrim/dim), keeping
        # the layout's placement so the screen reads as a clean gradient.
        if screen.panel:
            overlay_img = render_overlay(
                screen_text=screen.text,
                screen_index=screen_index,
                total_screens=len(spec.screens),
                label=spec.label,
                scheme=scheme,
                text_size=screen.text_size,
                subtitle=screen.subtitle,
                centered=centered,
                scrim=False,
                dim=False,
                width=width,
                height=height,
            )
        success = _render_gradient_screen(
            screen, overlay_img, scheme, output_path,
            width, height, fps, crf, preset,
        )

    for suffix in (".overlay.png", ".composite.png"):
        p = output_path.with_suffix(suffix)
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass

    return success


def _render_clip_screen(
    screen: Screen,
    overlay_path: Path,
    output_path: Path,
    width: int, height: int, fps: int, crf: int, preset: str,
) -> bool:
    clip_path = ensure_clip(screen.clip.url)
    if clip_path is None:
        return False

    # Blurred-background fill instead of a center crop. The clip is scaled to
    # fit ENTIRELY inside the 1080x1920 frame (full horizontal content visible),
    # centered vertically. A blurred, darkened copy of the same clip fills the
    # leftover vertical space so there are no black bars. This is the standard
    # TikTok/Reels treatment and avoids the "zoomed in" look that a center crop
    # produced on 16:9 landscape sources (only ~31% of width was visible).
    filter_complex = (
        f"[0:v]setpts=PTS-STARTPTS,split=2[bg][fg];"
        # Background: cover the whole frame, then blur + darken.
        f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},boxblur={_BG_BLUR}:1,"
        f"eq=brightness={_BG_BRIGHTNESS}:saturation={_BG_SATURATION}[bgb];"
        # Foreground: fit fully inside the frame (nothing cropped), gentle correction.
        f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"eq=brightness={_FG_BRIGHTNESS}:saturation={_FG_SATURATION}[fgs];"
        # Center the foreground over the blurred background, then text overlay.
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[base];"
        f"[base][1:v]overlay=0:0[out]"
    )

    args = [
        "-ss", str(screen.clip.start),
        "-t", str(screen.duration),
        "-i", str(clip_path),
        "-i", str(overlay_path),
        "-filter_complex", filter_complex,
        "-map", "[out]",
    ] + _ENCODE_FLAGS + [
        "-preset", preset, "-crf", str(crf),
        "-r", str(fps), "-g", str(fps),
        str(output_path),
    ]

    mode = "ambient" if screen.clip.ambient else f"{screen.clip.start:.1f}–{screen.clip.end:.1f}s"
    return _run_ffmpeg(args, f"clip @{mode}")


def _render_still_screen(
    screen: Screen,
    overlay_path: Path,
    output_path: Path,
    width: int, height: int, fps: int, crf: int, preset: str,
) -> bool:
    """Footage fallback ladder tier 3: render a representative STILL with a slow
    Ken Burns push (zoompan) and the same blurred-background fill used for clips,
    so a footage-poor beat reads as composed footage rather than a blank gradient."""
    still_path = Path(screen.still)
    if not still_path.exists():
        return False

    frames = max(2, int(round(screen.duration * fps)))
    zoom_max = 1.15
    inc = (zoom_max - 1.0) / frames

    # Single still frame (input 0) is composited onto its own blurred fill, then
    # zoompan expands that one frame into `frames` frames with a gentle zoom-in.
    # The text overlay (input 1, looped) is applied AFTER the zoom so it stays fixed.
    filter_complex = (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},boxblur={_BG_BLUR}:1,"
        f"eq=brightness={_BG_BRIGHTNESS}:saturation={_BG_SATURATION}[bgb];"
        f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"eq=brightness={_FG_BRIGHTNESS}:saturation={_FG_SATURATION}[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[fill];"
        f"[fill]zoompan=z='min(zoom+{inc:.6f},{zoom_max})':d={frames}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps}[kb];"
        f"[kb][1:v]overlay=0:0[out]"
    )

    args = [
        "-i", str(still_path),
        "-framerate", str(fps), "-loop", "1", "-i", str(overlay_path),
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-t", str(screen.duration),
    ] + _ENCODE_FLAGS + [
        "-preset", preset, "-crf", str(crf),
        "-r", str(fps), "-g", str(fps),
        str(output_path),
    ]
    return _run_ffmpeg(args, "ken burns still")


def _render_gradient_screen(
    screen: Screen,
    overlay_img: Image.Image,
    scheme: ColorScheme,
    output_path: Path,
    width: int, height: int, fps: int, crf: int, preset: str,
) -> bool:
    gradient = create_gradient(width, height, scheme.bg_top, scheme.bg_bottom)
    composite = Image.alpha_composite(gradient, overlay_img).convert("RGB")

    comp_path = output_path.with_suffix(".composite.png")
    composite.save(str(comp_path), "PNG")

    args = [
        "-loop", "1",
        "-framerate", str(fps),
        "-i", str(comp_path),
        "-t", str(screen.duration),
    ] + _ENCODE_FLAGS + [
        "-preset", preset, "-crf", str(crf),
        "-r", str(fps), "-g", str(fps),
        str(output_path),
    ]

    return _run_ffmpeg(args, "gradient screen")
