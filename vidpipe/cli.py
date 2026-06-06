import argparse
import logging
import shutil
import subprocess
import time
from pathlib import Path

import config

from .assembler import concat_segments
from .renderer import render_screen
from .schemes import get_scheme
from .spec import load_spec
from .voiceover import generate_voiceover, mix_voiceover, find_music

log = logging.getLogger("vidpipe")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )


def _check_ffmpeg() -> bool:
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
        return r.returncode == 0
    except FileNotFoundError:
        return False


def main(argv: list = None) -> int:
    p = argparse.ArgumentParser(prog="vidpipe",
                                description="Short-form video pipeline")
    p.add_argument("spec", type=Path, help="JSON spec file")
    p.add_argument("-o", "--output", type=Path, help="Output MP4 path")
    p.add_argument("--preview", action="store_true",
                   help="Half-res fast preview")
    p.add_argument("--no-voice", action="store_true",
                   help="Skip voiceover")
    p.add_argument("--no-transitions", action="store_true",
                   help="Hard cuts between screens (faster, no crossfade)")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Debug logging")
    args = p.parse_args(argv)

    _setup_logging(args.verbose)

    if not _check_ffmpeg():
        log.error("ffmpeg not found — install it and add to PATH")
        return 1

    try:
        spec = load_spec(args.spec)
    except (FileNotFoundError, ValueError) as e:
        log.error("Spec load failed: %s", e)
        return 1

    try:
        get_scheme(spec.scheme)
    except ValueError as e:
        log.error(str(e))
        return 1

    output = args.output or Path(f"{spec.id}.mp4")

    if args.preview:
        w, h, crf, preset, fps = 540, 960, 28, "ultrafast", 24
        log.info("Preview mode: %dx%d", w, h)
    else:
        w, h, crf, preset, fps = 1080, 1920, 18, "medium", 30

    t0 = time.time()
    work = Path(f".vidpipe_{spec.id}")
    work.mkdir(exist_ok=True)

    segments = []
    for i, screen in enumerate(spec.screens):
        seg = work / f"seg_{i:03d}.mp4"
        log.info("Screen %d/%d ...", i + 1, len(spec.screens))
        if not render_screen(screen, i, spec, seg, w, h, fps, crf, preset):
            log.error("Screen %d failed", i + 1)
            return 1
        segments.append(seg)

    concat_out = work / "concat.mp4"
    screen_durations = [s.duration for s in spec.screens]
    crossfade = 0.0 if args.no_transitions or args.preview else 0.3
    if not concat_segments(
        segments, concat_out,
        screen_durations=screen_durations,
        crf=crf, preset=preset, fps=fps,
        crossfade=crossfade,
    ):
        log.error("Concat failed")
        return 1

    # v2: the narrator is fixed in config (spec no longer carries a voice).
    narrator = getattr(config, "NARRATOR_VOICE", None) or spec.voice
    if not args.no_voice and narrator and spec.voice_script:
        voice_file = work / "voiceover.mp3"
        music_file = find_music(spec.scheme)
        if music_file:
            log.info("Music bed: %s (scheme=%s)", music_file.name, spec.scheme)
        else:
            log.info("No music bed found for scheme=%s — voiceover only "
                     "(drop a track in %s to enable)", spec.scheme, config.MUSIC_DIR)
        if generate_voiceover(spec.voice_script, narrator, voice_file):
            if not mix_voiceover(concat_out, voice_file, output, music=music_file):
                log.warning("Voice mix failed — using video only")
                shutil.copy2(concat_out, output)
        else:
            log.warning("Voice generation failed — using video only")
            shutil.copy2(concat_out, output)
    else:
        shutil.copy2(concat_out, output)

    for f in work.iterdir():
        try:
            f.unlink()
        except OSError:
            pass
    try:
        work.rmdir()
    except OSError:
        log.debug("Could not remove work dir: %s", work)

    log.info("Done: %s (%.1fs)", output, time.time() - t0)
    return 0
