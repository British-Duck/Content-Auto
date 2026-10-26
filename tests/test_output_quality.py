"""
Output quality tests — legacy v1 suite, validated against tests/baselines/, which
predate the current renderer. Excluded from the default run (see pytest.ini), so
every invocation needs -m legacy.

Default (no args): builds a 2-screen fixture video and validates it.
    pytest tests/test_output_quality.py -m legacy
Against a real build:
    pytest tests/test_output_quality.py -m legacy --video output.mp4 --spec spec.json
Against a preview build:
    pytest tests/test_output_quality.py -m legacy --video preview.mp4 --spec spec.json --preview
"""
from __future__ import annotations

import pytest

from tests.quality import CheckResult, QualityReport

pytestmark = pytest.mark.legacy


def _require(report: QualityReport, name: str) -> CheckResult:
    """Retrieve a named check from the report, failing the test if absent."""
    for c in report.checks:
        if c.name == name:
            return c
    pytest.fail(f"Check '{name}' not found in report — validator may have aborted early")


def _assert_check(check: CheckResult) -> None:
    if not check.passed:
        msg = f"[{check.name}] {check.detail}"
        if check.fix:
            msg += f"\n  Fix: {check.fix}"
        pytest.fail(msg)


# ── Structural tests (always run) ─────────────────────────────────────────────

def test_file_size(quality_report):
    _assert_check(_require(quality_report, "file_size"))


def test_resolution(quality_report):
    _assert_check(_require(quality_report, "resolution"))


def test_framerate(quality_report):
    _assert_check(_require(quality_report, "framerate"))


def test_duration(quality_report):
    _assert_check(_require(quality_report, "duration"))


def test_video_codec(quality_report):
    _assert_check(_require(quality_report, "video_codec"))


def test_pixel_format(quality_report):
    _assert_check(_require(quality_report, "pixel_format"))


def test_bitrate(quality_report):
    _assert_check(_require(quality_report, "bitrate"))


def test_faststart(quality_report):
    _assert_check(_require(quality_report, "faststart"))


def test_audio(quality_report):
    _assert_check(_require(quality_report, "audio"))


# ── Frame-level tests (require spec) ─────────────────────────────────────────

def test_keyframe_timestamps(quality_report):
    check = _require(quality_report, "keyframe_timestamps")
    if "no screens" in check.detail:
        pytest.skip("No screens in spec")
    _assert_check(check)


def test_black_frames(quality_report):
    check = _require(quality_report, "black_frames")
    if "no screens" in check.detail:
        pytest.skip("No screens in spec")
    _assert_check(check)


def test_frame_distinctiveness(quality_report):
    check = _require(quality_report, "frame_distinctiveness")
    if "only 1 screen" in check.detail:
        pytest.skip("Only 1 screen — nothing to compare")
    _assert_check(check)


def test_ssim_regression(quality_report):
    check = _require(quality_report, "ssim_regression")
    if "first run" in check.detail:
        pytest.skip(f"Baselines just created: {check.detail}")
    _assert_check(check)


# ── Summary printed for convenience ──────────────────────────────────────────

@pytest.fixture(autouse=True, scope="session")
def print_report_summary(quality_report):
    yield
    quality_report.print_summary()
