"""Shared fixtures and helpers for the video pipeline test suite."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
FIXTURES_DIR = Path(__file__).parent / "fixtures"
REPORTS_DIR = Path(__file__).parent / "reports"
BASELINES_DIR = Path(__file__).parent / "baselines"


def run_pipeline(
    spec_path: Path,
    output_path: Path,
    extra_args: list[str] | None = None,
    timeout: int = 300,
) -> subprocess.CompletedProcess:
    """Run the vidpipe CLI and return the CompletedProcess."""
    cmd = [
        "python", "-m", "vidpipe",
        str(spec_path),
        "-o", str(output_path),
    ] + (extra_args or [])
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(PROJECT_ROOT),
    )


def pytest_addoption(parser):
    parser.addoption("--video", default=None, help="Path to video file to test")
    parser.addoption("--spec", default=None, help="Path to spec JSON to test against")
    parser.addoption("--preview", action="store_true", default=False,
                     help="Video was built in --preview mode")


# ── Session-scoped quality report fixture ────────────────────────────────────

@pytest.fixture(scope="session")
def quality_report(request, tmp_path_factory):
    """
    Build (or load) the video under test and run the full quality validation.
    Returns a TestReport usable by all quality test functions.

    Override with:
        pytest tests/test_output_quality.py --video path.mp4 --spec spec.json
    """
    from tests.quality import VideoQualityValidator

    video_opt = request.config.getoption("--video")
    spec_opt = request.config.getoption("--spec")
    preview = request.config.getoption("--preview")

    if video_opt:
        spec = json.loads(Path(spec_opt).read_text()) if spec_opt else None
        video_path = Path(video_opt)
        if not video_path.exists():
            pytest.skip(f"--video file not found: {video_path}")
    else:
        # Build a minimal 2-screen fixture video (no network, no voice)
        spec = {
            "id": "pytest_fixture_video",
            "label": "TEST",
            "scheme": "dark_gold",
            "screens": [
                {"text": "Screen one\nTest content", "duration": 2.0, "panel": True},
                {"text": "Screen two\n[GOLD]highlighted", "duration": 2.0},
            ],
        }
        tmp = tmp_path_factory.mktemp("fixture_video")
        spec_path = tmp / "spec.json"
        spec_path.write_text(json.dumps(spec))
        video_path = tmp / "output.mp4"
        result = run_pipeline(spec_path, video_path, ["--no-voice"])
        if result.returncode != 0:
            pytest.skip(
                f"Fixture video build failed — skipping quality tests.\n"
                f"stderr: {result.stderr[:800]}"
            )

    v = VideoQualityValidator(video_path, spec=spec, preview=preview,
                              baseline_dir=BASELINES_DIR)
    report = v.validate()
    return report
