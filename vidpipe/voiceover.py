import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Optional

import config

log = logging.getLogger(__name__)

_MUSIC_EXTS = (".mp3", ".m4a", ".wav", ".ogg", ".flac", ".opus")


def find_music(scheme: str) -> Optional[Path]:
    """Resolve a music-bed file for a scheme: MUSIC_DIR/<mood>.<ext>, falling
    back to MUSIC_DIR/<default>.<ext>. Returns None (→ voiceover-only) if music
    is disabled or no file is present, so a missing library never breaks a run.
    """
    if not getattr(config, "MUSIC_ENABLED", False):
        return None
    mdir = config.MUSIC_DIR
    if not mdir.exists():
        return None
    stems = []
    mood = config.MUSIC_BY_SCHEME.get(scheme)
    if mood:
        stems.append(mood)
    stems.append(config.MUSIC_DEFAULT)
    for stem in stems:
        for ext in _MUSIC_EXTS:
            p = mdir / f"{stem}{ext}"
            if p.exists():
                return p
    return None


def _probe_duration(path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(out.stdout.strip())
    except (ValueError, subprocess.SubprocessError):
        return 0.0


async def _generate_tts(text: str, voice: str, output: Path) -> bool:
    try:
        import edge_tts
    except ImportError:
        log.error("edge-tts not installed — pip install edge-tts")
        return False

    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(output))
    return True


def generate_voiceover(text: str, voice: str, output: Path) -> bool:
    log.info("Generating voiceover (voice=%s) ...", voice)
    try:
        ok = asyncio.run(_generate_tts(text, voice, output))
        if ok and output.exists() and output.stat().st_size > 0:
            log.info("Voiceover: %s (%.0f KB)", output.name,
                     output.stat().st_size / 1024)
            return True
        log.error("Voiceover file empty or missing")
        return False
    except Exception as e:
        log.error("Voiceover failed: %s", e)
        return False


def mix_voiceover(video: Path, audio: Path, output: Path,
                  music: Optional[Path] = None) -> bool:
    """Mux the voiceover into the (silent) video. If `music` is given, mix a
    ducked music bed underneath the voiceover; otherwise do a voiceover-only mux.
    """
    if music is not None and Path(music).exists():
        return _mix_voice_and_music(video, audio, Path(music), output)
    return _mix_voice_only(video, audio, output)


def _mix_voice_only(video: Path, audio: Path, output: Path) -> bool:
    log.info("Mixing voiceover into video ...")
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-i", str(video),
        "-i", str(audio),
        # Explicit stream mapping: video from input 0, audio from input 1.
        # Without -map, ffmpeg auto-selects and can silently drop audio on
        # some players/containers when the video has no prior audio track.
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        # Normalize audio to a consistent loudness level so voiceover is
        # always clearly audible regardless of TTS output level.
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-shortest",
        "-movflags", "+faststart",
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("Audio mix failed:\n%s", result.stderr.strip())
        return False
    log.info("Final: %s (%.1f MB)", output.name, output.stat().st_size / 1e6)
    return True


def _mix_voice_and_music(video: Path, audio: Path, music: Path, output: Path) -> bool:
    """Mix voiceover over a music bed that ducks under the voice (sidechain
    compression). Music is looped to cover the whole video and faded in/out.
    Falls back to voiceover-only if the ducked mix fails.
    """
    log.info("Mixing voiceover + ducked music bed (%s) ...", music.name)
    dur = _probe_duration(video)
    fade = config.MUSIC_FADE
    fade_out_start = max(0.0, dur - fade)
    gain = config.MUSIC_BED_GAIN_DB
    ratio = config.MUSIC_DUCK_RATIO

    # [1]=voiceover, [2]=looped music. Duck the music by the voice, then mix
    # voice on top, trim to the video length, and normalise to -14 LUFS.
    # Split the voiceover: one copy is mixed in, the other is the sidechain key
    # that ducks the music (a labelled link can only be consumed once).
    filter_complex = (
        f"[1:a]aresample=48000,asplit=2[vo][vokey];"
        f"[2:a]aresample=48000,volume={gain}dB,"
        f"afade=t=in:d={fade:.2f},afade=t=out:st={fade_out_start:.2f}:d={fade:.2f}[mus];"
        f"[mus][vokey]sidechaincompress=threshold=0.03:ratio={ratio}:attack=5:release=250[musd];"
        f"[vo][musd]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
        f"atrim=0:{dur:.3f},loudnorm=I=-14:TP=-1.5:LRA=11[aout]"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-i", str(video),
        "-i", str(audio),
        "-stream_loop", "-1", "-i", str(music),
        "-filter_complex", filter_complex,
        "-map", "0:v:0",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-shortest",
        "-movflags", "+faststart",
        str(output),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("Music mix failed (falling back to voiceover-only):\n%s",
                  result.stderr.strip())
        return _mix_voice_only(video, audio, output)
    log.info("Final (with music): %s (%.1f MB)", output.name, output.stat().st_size / 1e6)
    return True
