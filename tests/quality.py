"""
VideoQualityValidator — programmatic quality validation using ffprobe + ffmpeg.

Usage (standalone):
    python tests/run_tests.py output.mp4 spec.json

Usage (import):
    v = VideoQualityValidator(video_path, spec=spec_dict, preview=False)
    report = v.validate()
    report.print_summary()
"""
from __future__ import annotations

import json
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

_NULL = "NUL" if sys.platform == "win32" else "/dev/null"


# ── Result types ─────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str
    fix: Optional[str] = None


@dataclass
class QualityReport:
    video_path: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]

    def to_dict(self) -> dict:
        return {
            "video": self.video_path,
            "passed": self.passed,
            "total": len(self.checks),
            "failures": len(self.failed_checks),
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail, "fix": c.fix}
                for c in self.checks
            ],
        }

    def print_summary(self):
        status = "PASS" if self.passed else "FAIL"
        bar = "=" * 62
        print(f"\n{bar}")
        print(f"  Video Quality Report  [{status}]")
        print(f"  File: {self.video_path}")
        print(bar)
        for c in self.checks:
            mark = " OK " if c.passed else "FAIL"
            print(f"  [{mark}] {c.name}: {c.detail}")
            if not c.passed and c.fix:
                print(f"         Fix: {c.fix}")
        print(bar)
        passed_n = len(self.checks) - len(self.failed_checks)
        print(f"  {passed_n}/{len(self.checks)} checks passed\n")


# Keep alias for backwards compat
TestReport = QualityReport


# ── MP4 atom parser ───────────────────────────────────────────────────────────

def _find_mp4_atoms(path: Path) -> dict[bytes, int]:
    """Return first byte offset of each top-level MP4 atom type."""
    atoms: dict[bytes, int] = {}
    try:
        with open(path, "rb") as f:
            offset = 0
            for _ in range(256):  # cap iterations
                header = f.read(8)
                if len(header) < 8:
                    break
                size = struct.unpack(">I", header[:4])[0]
                box_type = header[4:8]
                if box_type not in atoms:
                    atoms[box_type] = offset
                if size == 0:
                    break
                if size == 1:
                    ext = f.read(8)
                    if len(ext) < 8:
                        break
                    size = struct.unpack(">Q", ext)[0]
                    offset += size
                else:
                    offset += size
                f.seek(offset)
    except OSError:
        pass
    return atoms


# ── SSIM (numpy only) ─────────────────────────────────────────────────────────

def _compute_ssim(img1: Image.Image, img2: Image.Image) -> float:
    """Simplified SSIM between two images (grayscale, 256x256 thumbnail)."""
    a = np.array(img1.convert("L").resize((256, 256)), dtype=np.float64)
    b = np.array(img2.convert("L").resize((256, 256)), dtype=np.float64)
    C1, C2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mu_a, mu_b = a.mean(), b.mean()
    var_a, var_b = a.var(), b.var()
    cov = ((a - mu_a) * (b - mu_b)).mean()
    return float(
        ((2 * mu_a * mu_b + C1) * (2 * cov + C2))
        / ((mu_a**2 + mu_b**2 + C1) * (var_a + var_b + C2))
    )


def _mean_brightness(img: Image.Image) -> float:
    return float(np.array(img.convert("L"), dtype=np.float64).mean())


# ── Validator ─────────────────────────────────────────────────────────────────

class VideoQualityValidator:
    """
    Validates a rendered video against the spec it was built from.

    spec can be a VideoSpec dataclass, a parsed dict, or None (skips spec-dependent checks).
    preview=True selects 540x960 / 24fps thresholds.
    baseline_dir stores/loads SSIM reference frames.
    """

    PROD_W, PROD_H = 1080, 1920
    PREV_W, PREV_H = 540, 960
    PROD_FPS = 30
    PREV_FPS = 24
    DURATION_TOL = 0.5      # seconds
    MIN_FILE_BYTES = 10_000
    MIN_BPS = 20_000         # 20 Kbps — floor to catch broken encodes; gradient videos legitimately compress to <100 Kbps
    MAX_BPS = 25_000_000     # 25 Mbps
    SSIM_FLOOR = 0.85

    def __init__(
        self,
        video_path: Path | str,
        spec=None,
        preview: bool = False,
        baseline_dir: Path | str | None = None,
        crossfade: float = 0.0,
        voice_enabled: Optional[bool] = None,
    ):
        self.video_path = Path(video_path)
        self.spec = spec
        self.preview = preview
        self.baseline_dir = Path(baseline_dir) if baseline_dir else Path(__file__).parent / "baselines"
        self.crossfade = crossfade            # seconds of crossfade per transition
        self.voice_enabled = voice_enabled    # None = infer from spec
        self._probe: Optional[dict] = None

    # ── Public ────────────────────────────────────────────────────────────────

    def validate(self) -> QualityReport:
        report = QualityReport(str(self.video_path))

        if not self.video_path.exists():
            report.checks.append(CheckResult(
                "file_exists", False, "output file not found",
                "Run: python -m vidpipe <spec.json> -o <output.mp4>",
            ))
            return report

        try:
            self._ensure_probe()
        except Exception as exc:
            report.checks.append(CheckResult(
                "ffprobe", False, f"ffprobe failed: {exc}",
                "Install ffprobe (bundled with ffmpeg)",
            ))
            return report

        for check in (
            self._check_file_size,
            self._check_resolution,
            self._check_framerate,
            self._check_duration,
            self._check_video_codec,
            self._check_pixel_format,
            self._check_bitrate,
            self._check_faststart,
            self._check_audio_stream,
        ):
            report.checks.append(check())

        if self.spec is not None:
            for check in (
                self._check_keyframe_timestamps,
                self._check_black_frames,
                self._check_frame_distinctiveness,
                self._check_ssim_regression,
            ):
                report.checks.append(check())

        return report

    # ── ffprobe ───────────────────────────────────────────────────────────────

    def _ensure_probe(self):
        if self._probe is None:
            cmd = [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_format", "-show_streams",
                str(self.video_path),
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip())
            self._probe = json.loads(r.stdout)

    def _video_stream(self) -> Optional[dict]:
        for s in self._probe.get("streams", []):
            if s.get("codec_type") == "video":
                return s
        return None

    def _audio_stream(self) -> Optional[dict]:
        for s in self._probe.get("streams", []):
            if s.get("codec_type") == "audio":
                return s
        return None

    def _probe_duration(self) -> Optional[float]:
        try:
            return float(self._probe["format"]["duration"])
        except (KeyError, TypeError, ValueError):
            pass
        vs = self._video_stream()
        if vs:
            try:
                return float(vs["duration"])
            except (KeyError, TypeError, ValueError):
                pass
        return None

    def _probe_bitrate(self) -> Optional[int]:
        try:
            br = self._probe["format"].get("bit_rate")
            if br and br != "N/A":
                return int(br)
        except (KeyError, TypeError, ValueError):
            pass
        # Estimate from file size and duration
        dur = self._probe_duration()
        if dur and dur > 0:
            return int((self.video_path.stat().st_size * 8) / dur)
        return None

    # ── Structural checks ─────────────────────────────────────────────────────

    def _check_file_size(self) -> CheckResult:
        size = self.video_path.stat().st_size
        if size == 0:
            return CheckResult("file_size", False, "file is 0 bytes",
                               "Pipeline produced empty output — check for ffmpeg errors")
        if size < self.MIN_FILE_BYTES:
            return CheckResult("file_size", False,
                               f"suspiciously small: {size:,} bytes",
                               "Encoding likely failed silently; inspect stderr logs")
        return CheckResult("file_size", True, f"{size / 1_048_576:.2f} MB ({size:,} bytes)")

    def _check_resolution(self) -> CheckResult:
        vs = self._video_stream()
        if not vs:
            return CheckResult("resolution", False, "no video stream found")
        w, h = vs.get("width"), vs.get("height")
        ew, eh = (self.PREV_W, self.PREV_H) if self.preview else (self.PROD_W, self.PROD_H)
        if w == ew and h == eh:
            return CheckResult("resolution", True, f"{w}x{h}")
        return CheckResult(
            "resolution", False, f"got {w}x{h}, expected {ew}x{eh}",
            f"Re-encode: ffmpeg -i input.mp4 -vf scale={ew}:{eh} -c:a copy output.mp4",
        )

    def _check_framerate(self) -> CheckResult:
        vs = self._video_stream()
        if not vs:
            return CheckResult("framerate", False, "no video stream found")
        raw = vs.get("r_frame_rate", "0/1")
        try:
            num, den = map(int, raw.split("/"))
            fps = round(num / den, 3) if den else 0
        except (ValueError, ZeroDivisionError):
            fps = 0
        expected = self.PREV_FPS if self.preview else self.PROD_FPS
        if abs(fps - expected) < 0.5:
            return CheckResult("framerate", True, f"{fps} fps")
        return CheckResult(
            "framerate", False, f"got {fps} fps, expected {expected}",
            f"Re-encode with -r {expected}",
        )

    def _check_duration(self) -> CheckResult:
        dur = self._probe_duration()
        if dur is None:
            return CheckResult("duration", False, "could not read duration")
        if self.spec is None:
            return CheckResult("duration", True, f"{dur:.3f}s (no spec for comparison)")
        screens = self._get_screens()
        raw_expected = sum(
            s.get("duration", 0) if isinstance(s, dict) else s.duration
            for s in screens
        )
        # Crossfade shortens the video by crossfade_duration per transition
        n_transitions = max(0, len(screens) - 1)
        expected = raw_expected - self.crossfade * n_transitions
        diff = abs(dur - expected)
        note = f" (includes {n_transitions}×{self.crossfade:.2f}s crossfades)" if self.crossfade else ""
        if diff <= self.DURATION_TOL:
            return CheckResult("duration", True,
                               f"{dur:.3f}s (expected {expected:.3f}s{note}, delta {diff:.3f}s)")
        return CheckResult(
            "duration", False,
            f"{dur:.3f}s vs expected {expected:.3f}s{note} — delta {diff:.3f}s exceeds {self.DURATION_TOL}s tolerance",
            "Check for dropped segments in assembler; inspect .vidpipe_*/concat.txt",
        )

    def _check_video_codec(self) -> CheckResult:
        vs = self._video_stream()
        if not vs:
            return CheckResult("video_codec", False, "no video stream found")
        codec = vs.get("codec_name", "unknown")
        if codec == "h264":
            return CheckResult("video_codec", True, "h264")
        return CheckResult(
            "video_codec", False, f"got '{codec}', expected h264",
            "Re-encode: ffmpeg -i input.mp4 -c:v libx264 -c:a copy output.mp4",
        )

    def _check_pixel_format(self) -> CheckResult:
        vs = self._video_stream()
        if not vs:
            return CheckResult("pixel_format", False, "no video stream found")
        pix = vs.get("pix_fmt", "unknown")
        if pix == "yuv420p":
            return CheckResult("pixel_format", True, "yuv420p")
        return CheckResult(
            "pixel_format", False, f"got '{pix}', expected yuv420p",
            "Re-encode: ffmpeg -i input.mp4 -pix_fmt yuv420p -c:a copy output.mp4",
        )

    def _check_bitrate(self) -> CheckResult:
        bps = self._probe_bitrate()
        if bps is None:
            return CheckResult("bitrate", False, "could not read bitrate")
        mbps = bps / 1_000_000
        min_kbps = self.MIN_BPS / 1000
        max_mbps = self.MAX_BPS / 1_000_000
        kbps = bps / 1000
        if bps < self.MIN_BPS:
            return CheckResult(
                "bitrate", False,
                f"{kbps:.1f} Kbps — below {min_kbps:.0f} Kbps minimum",
                "Lower the -crf value (e.g. crf=18) to increase quality and bitrate",
            )
        if bps > self.MAX_BPS:
            return CheckResult(
                "bitrate", False,
                f"{mbps:.2f} Mbps — above {max_mbps:.0f} Mbps maximum",
                "Raise the -crf value to reduce file size",
            )
        return CheckResult("bitrate", True, f"{mbps:.2f} Mbps" if mbps >= 1 else f"{kbps:.1f} Kbps")

    def _check_faststart(self) -> CheckResult:
        atoms = _find_mp4_atoms(self.video_path)
        moov = atoms.get(b"moov")
        mdat = atoms.get(b"mdat")
        if moov is None:
            return CheckResult("faststart", False, "moov atom not found — file may be corrupt")
        if mdat is not None and mdat < moov:
            return CheckResult(
                "faststart", False,
                f"mdat at byte {mdat:,} precedes moov at byte {moov:,} — not web-optimized",
                "Re-mux: ffmpeg -i input.mp4 -c copy -movflags +faststart output.mp4",
            )
        return CheckResult("faststart", True, f"moov at byte {moov:,} (before mdat)")

    def _check_audio_stream(self) -> CheckResult:
        has_voice = self._spec_has_voice()
        audio = self._audio_stream()

        if not has_voice:
            label = "no audio stream (voice not requested)"
            return CheckResult("audio", True, label if audio is None else "audio stream present (voice not requested — harmless)")

        if audio is None:
            return CheckResult(
                "audio", False,
                "voice was requested in spec but no audio stream found",
                "Re-run without --no-voice, or check edge-tts is installed",
            )

        issues = []
        codec = audio.get("codec_name", "unknown")
        sample_rate = int(audio.get("sample_rate", 0))

        if codec != "aac":
            issues.append(f"codec='{codec}' (expected aac)")
        if sample_rate not in (44100, 48000):
            issues.append(f"sample_rate={sample_rate} Hz (expected 44100 or 48000)")

        # A/V duration sync
        video_dur = self._probe_duration()
        audio_dur_str = audio.get("duration")
        if video_dur and audio_dur_str:
            drift = abs(video_dur - float(audio_dur_str))
            if drift > 1.0:
                issues.append(
                    f"A/V duration drift {drift:.3f}s "
                    f"(video={video_dur:.3f}s, audio={float(audio_dur_str):.3f}s)"
                )

        # Volume: non-silent
        vol = self._measure_audio_volume()
        if vol is not None and vol < -70:
            issues.append(f"audio appears silent (mean_volume={vol:.1f} dBFS)")

        if issues:
            return CheckResult(
                "audio", False, "; ".join(issues),
                "Re-mix: ffmpeg -i video.mp4 -c:v copy -c:a aac -ar 44100 output.mp4",
            )

        detail = f"aac, {sample_rate} Hz"
        if video_dur and audio_dur_str:
            detail += f", A/V drift {abs(video_dur - float(audio_dur_str)):.3f}s"
        return CheckResult("audio", True, detail)

    def _measure_audio_volume(self) -> Optional[float]:
        try:
            r = subprocess.run(
                ["ffmpeg", "-i", str(self.video_path), "-vn", "-af", "volumedetect", "-f", "null", _NULL],
                capture_output=True, text=True, timeout=30,
            )
            for line in r.stderr.splitlines():
                if "mean_volume" in line:
                    return float(line.split("mean_volume:")[1].strip().split()[0])
        except Exception:
            pass
        return None

    # ── Frame-level checks ────────────────────────────────────────────────────

    def _check_keyframe_timestamps(self) -> CheckResult:
        screens = self._get_screens()
        if not screens:
            return CheckResult("keyframe_timestamps", True, "no screens in spec")

        # Expected keyframe at the start of each screen, adjusted for crossfade
        # With crossfade c, screen i starts at: sum(d[0..i-1]) - c*i in the output video
        expected: list[float] = [0.0]
        t = 0.0
        for i, s in enumerate(screens[:-1]):
            d = s.get("duration", 0) if isinstance(s, dict) else s.duration
            t += d
            adjusted = max(0.0, round(t - self.crossfade * (i + 1), 4))
            expected.append(adjusted)

        try:
            keyframes = self._get_keyframe_timestamps()
        except Exception as exc:
            return CheckResult("keyframe_timestamps", False, f"ffprobe error: {exc}")

        if not keyframes:
            return CheckResult("keyframe_timestamps", False, "no I-frames found",
                               "Re-encode with explicit -force_key_frames at transitions")

        tol = 0.15  # 150 ms
        missing = [
            f"t={e:.3f}s (nearest={min(keyframes, key=lambda k: abs(k - e)):.3f}s)"
            for e in expected
            if min((abs(k - e) for k in keyframes), default=999) > tol
        ]

        if missing:
            return CheckResult(
                "keyframe_timestamps", False,
                f"keyframes absent near: {', '.join(missing)}",
                "Add -force_key_frames 'expr:gte(t,{transitions})' to encoding",
            )
        return CheckResult(
            "keyframe_timestamps", True,
            f"{len(keyframes)} I-frames; all {len(expected)} transition points covered",
        )

    def _get_keyframe_timestamps(self) -> list[float]:
        r = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-select_streams", "v:0",
                "-show_frames",
                "-show_entries", "frame=pict_type,pts_time",
                "-of", "json",
                str(self.video_path),
            ],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip())
        data = json.loads(r.stdout)
        return [
            float(f["pts_time"])
            for f in data.get("frames", [])
            if f.get("pict_type") == "I" and "pts_time" in f
        ]

    def _check_black_frames(self) -> CheckResult:
        screens = self._get_screens()
        if not screens:
            return CheckResult("black_frames", True, "no screens in spec")

        midpoints = self._screen_midpoints(screens)
        black: list[str] = []

        with tempfile.TemporaryDirectory() as tmp:
            for i, t in enumerate(midpoints):
                p = Path(tmp) / f"f{i:03d}.png"
                if not self._extract_frame(t, p):
                    continue
                try:
                    brightness = _mean_brightness(Image.open(p))
                    if brightness < 5.0:
                        black.append(f"screen {i} at t={t:.2f}s (brightness={brightness:.1f}/255)")
                except Exception:
                    pass

        if black:
            return CheckResult(
                "black_frames", False,
                f"black frames detected: {', '.join(black)}",
                "Check clip loading; inspect segment files in .vidpipe_*/",
            )
        return CheckResult("black_frames", True,
                           f"all {len(midpoints)} sampled frames are non-black")

    def _check_frame_distinctiveness(self) -> CheckResult:
        screens = self._get_screens()
        if len(screens) < 2:
            return CheckResult("frame_distinctiveness", True, "only 1 screen — nothing to compare")

        midpoints = self._screen_midpoints(screens)
        identical: list[str] = []

        with tempfile.TemporaryDirectory() as tmp:
            imgs: list[Optional[Image.Image]] = []
            for i, t in enumerate(midpoints):
                p = Path(tmp) / f"f{i:03d}.png"
                if self._extract_frame(t, p):
                    try:
                        imgs.append(Image.open(p).copy())
                        continue
                    except Exception:
                        pass
                imgs.append(None)

            for i in range(len(imgs) - 1):
                if imgs[i] is None or imgs[i + 1] is None:
                    continue
                score = _compute_ssim(imgs[i], imgs[i + 1])
                if score > 0.99:
                    identical.append(f"screens {i}&{i + 1} (SSIM={score:.4f})")

        if identical:
            return CheckResult(
                "frame_distinctiveness", False,
                f"adjacent screens appear identical: {', '.join(identical)}",
                "Ensure each segment has distinct rendered content",
            )
        return CheckResult("frame_distinctiveness", True,
                           f"all {len(screens)} screens are visually distinct")

    def _check_ssim_regression(self) -> CheckResult:
        spec_id = self._get_spec_id()
        baseline_dir = self.baseline_dir / spec_id
        screens = self._get_screens()
        midpoints = self._screen_midpoints(screens)

        with tempfile.TemporaryDirectory() as tmp:
            current: list[Optional[Image.Image]] = []
            for i, t in enumerate(midpoints):
                p = Path(tmp) / f"f{i:03d}.png"
                if self._extract_frame(t, p):
                    try:
                        current.append(Image.open(p).copy())
                        continue
                    except Exception:
                        pass
                current.append(None)

            if not baseline_dir.exists():
                baseline_dir.mkdir(parents=True, exist_ok=True)
                saved = sum(
                    1 for i, img in enumerate(current)
                    if img is not None
                    and not img.save(baseline_dir / f"screen_{i:03d}.png")  # save returns None
                )
                return CheckResult(
                    "ssim_regression", True,
                    f"first run — {saved} baseline frames saved to {baseline_dir}",
                )

            regressions: list[str] = []
            for i, img in enumerate(current):
                bp = baseline_dir / f"screen_{i:03d}.png"
                if img is None or not bp.exists():
                    continue
                try:
                    score = _compute_ssim(img, Image.open(bp))
                    if score < self.SSIM_FLOOR:
                        regressions.append(f"screen {i} SSIM={score:.3f}")
                except Exception:
                    pass

            if regressions:
                return CheckResult(
                    "ssim_regression", False,
                    f"visual regressions detected: {', '.join(regressions)} (threshold={self.SSIM_FLOOR})",
                    f"Delete {baseline_dir} to reset baselines if the change is intentional",
                )
            return CheckResult(
                "ssim_regression", True,
                f"all {len(current)} frames above SSIM={self.SSIM_FLOOR} vs baselines",
            )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_screens(self) -> list:
        if self.spec is None:
            return []
        if isinstance(self.spec, dict):
            return self.spec.get("screens", [])
        return list(getattr(self.spec, "screens", []))

    def _get_spec_id(self) -> str:
        if self.spec is None:
            return "unknown"
        if isinstance(self.spec, dict):
            return self.spec.get("id", "unknown")
        return str(getattr(self.spec, "id", "unknown"))

    def _spec_has_voice(self) -> bool:
        if self.voice_enabled is not None:
            return self.voice_enabled
        if self.spec is None:
            return False
        if isinstance(self.spec, dict):
            return bool(self.spec.get("voice") or self.spec.get("voice_script"))
        return bool(
            getattr(self.spec, "voice", None) or getattr(self.spec, "voice_script", None)
        )

    def _screen_midpoints(self, screens: list) -> list[float]:
        midpoints, t = [], 0.0
        for s in screens:
            d = s.get("duration", 0) if isinstance(s, dict) else s.duration
            midpoints.append(t + d / 2)
            t += d
        return midpoints

    def _extract_frame(self, timestamp: float, output: Path) -> bool:
        try:
            r = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-ss", str(timestamp),
                    "-i", str(self.video_path),
                    "-frames:v", "1",
                    "-q:v", "2",
                    str(output),
                ],
                capture_output=True, timeout=30,
            )
            return r.returncode == 0 and output.exists()
        except Exception:
            return False
