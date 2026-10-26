"""
Stress tests — run the pipeline against edge-case specs and validate output.

Legacy v1 suite: compares renders against tests/baselines/, which predate the
current renderer, so it is excluded from the default run (see pytest.ini).
All tests are marked @pytest.mark.slow.
Tests that download clips are also marked @pytest.mark.network.

Run all:         pytest tests/test_stress.py -m legacy
Run no-network:  pytest tests/test_stress.py -m "legacy and not network"
"""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

import pytest

from tests.conftest import FIXTURES_DIR, PROJECT_ROOT, run_pipeline
from tests.quality import VideoQualityValidator

pytestmark = pytest.mark.legacy

BASELINES = Path(__file__).parent / "baselines"


def _validate(video_path: Path, spec: dict, preview: bool = False) -> None:
    """Run full quality validation and raise AssertionError on any failure."""
    v = VideoQualityValidator(video_path, spec=spec, preview=preview,
                              baseline_dir=BASELINES)
    report = v.validate()
    report.print_summary()
    failed = report.failed_checks
    if failed:
        msgs = [f"  [{c.name}] {c.detail}" + (f"\n    Fix: {c.fix}" if c.fix else "")
                for c in failed]
        raise AssertionError(f"{len(failed)} quality check(s) failed:\n" + "\n".join(msgs))


def _graceful(result: subprocess.CompletedProcess, label: str) -> None:
    """Assert no Python traceback — failure must be clean, not a crash."""
    assert "Traceback (most recent call last)" not in result.stderr, (
        f"{label}: unhandled Python exception\n{result.stderr[:1000]}"
    )


# ── 1. Single screen ─────────────────────────────────────────────────────────

@pytest.mark.slow
def test_single_screen(tmp_path):
    spec = json.loads((FIXTURES_DIR / "stress_1screen.json").read_text())
    out = tmp_path / "stress_1screen.mp4"
    result = run_pipeline(FIXTURES_DIR / "stress_1screen.json", out, ["--no-voice"])
    assert result.returncode == 0, f"Pipeline failed:\n{result.stderr}"
    _validate(out, spec)


# ── 2. Ten+ screens ──────────────────────────────────────────────────────────

@pytest.mark.slow
def test_ten_screens(tmp_path):
    spec = json.loads((FIXTURES_DIR / "stress_10screens.json").read_text())
    out = tmp_path / "stress_10screens.mp4"
    result = run_pipeline(FIXTURES_DIR / "stress_10screens.json", out, ["--no-voice"])
    assert result.returncode == 0, f"Pipeline failed:\n{result.stderr}"
    _validate(out, spec)


# ── 3. Very long text (heavy wrapping) ───────────────────────────────────────

@pytest.mark.slow
def test_long_text(tmp_path):
    spec = json.loads((FIXTURES_DIR / "stress_long_text.json").read_text())
    out = tmp_path / "stress_long_text.mp4"
    result = run_pipeline(FIXTURES_DIR / "stress_long_text.json", out, ["--no-voice"])
    assert result.returncode == 0, f"Pipeline failed:\n{result.stderr}"
    _validate(out, spec)


# ── 4. Non-existent clip URL (graceful fallback) ──────────────────────────────

@pytest.mark.slow
@pytest.mark.network
def test_bad_clip_url(tmp_path):
    spec = json.loads((FIXTURES_DIR / "stress_bad_clip.json").read_text())
    out = tmp_path / "stress_bad_clip.mp4"
    result = run_pipeline(FIXTURES_DIR / "stress_bad_clip.json", out, ["--no-voice"],
                          timeout=120)
    _graceful(result, "bad_clip_url")
    # Pipeline should fall back to gradient — output should still be valid
    if result.returncode == 0 and out.exists():
        _validate(out, spec)


# ── 5. Sub-second duration ───────────────────────────────────────────────────

@pytest.mark.slow
def test_short_duration(tmp_path):
    spec = json.loads((FIXTURES_DIR / "stress_short_duration.json").read_text())
    out = tmp_path / "stress_short_duration.mp4"
    result = run_pipeline(FIXTURES_DIR / "stress_short_duration.json", out, ["--no-voice"])
    _graceful(result, "short_duration")
    if result.returncode == 0 and out.exists():
        _validate(out, spec)


# ── 6. Long duration (15s screen) ────────────────────────────────────────────

@pytest.mark.slow
def test_long_duration(tmp_path):
    spec = json.loads((FIXTURES_DIR / "stress_long_duration.json").read_text())
    out = tmp_path / "stress_long_duration.mp4"
    result = run_pipeline(FIXTURES_DIR / "stress_long_duration.json", out, ["--no-voice"])
    assert result.returncode == 0, f"Pipeline failed:\n{result.stderr}"
    _validate(out, spec)


# ── 7. All screens with clips ─────────────────────────────────────────────────

@pytest.mark.slow
@pytest.mark.network
def test_all_clips(tmp_path):
    spec = json.loads((FIXTURES_DIR / "stress_all_clips.json").read_text())
    out = tmp_path / "stress_all_clips.mp4"
    result = run_pipeline(FIXTURES_DIR / "stress_all_clips.json", out, ["--no-voice"],
                          timeout=600)
    _graceful(result, "all_clips")
    if result.returncode == 0 and out.exists():
        _validate(out, spec)


# ── 8. No screens with clips (all gradients) ─────────────────────────────────

@pytest.mark.slow
def test_no_clips(tmp_path):
    spec = json.loads((FIXTURES_DIR / "stress_no_clips.json").read_text())
    out = tmp_path / "stress_no_clips.mp4"
    result = run_pipeline(FIXTURES_DIR / "stress_no_clips.json", out, ["--no-voice"])
    assert result.returncode == 0, f"Pipeline failed:\n{result.stderr}"
    _validate(out, spec)


# ── 9. Alternating clips and gradients ───────────────────────────────────────

@pytest.mark.slow
@pytest.mark.network
def test_alternating_clips(tmp_path):
    spec = json.loads((FIXTURES_DIR / "stress_alternating.json").read_text())
    out = tmp_path / "stress_alternating.mp4"
    result = run_pipeline(FIXTURES_DIR / "stress_alternating.json", out, ["--no-voice"],
                          timeout=600)
    _graceful(result, "alternating_clips")
    if result.returncode == 0 and out.exists():
        _validate(out, spec)


# ── 10. Empty spec (0 screens) — must fail cleanly ───────────────────────────

@pytest.mark.slow
def test_empty_spec(tmp_path):
    out = tmp_path / "stress_empty.mp4"
    result = run_pipeline(FIXTURES_DIR / "stress_empty.json", out, ["--no-voice"])
    _graceful(result, "empty_spec")
    assert result.returncode != 0, (
        "Pipeline should reject a spec with 0 screens but exited 0"
    )
    assert not out.exists() or out.stat().st_size == 0, (
        "Pipeline produced output for a 0-screen spec"
    )


# ── 11. Zero duration screen — must fail cleanly ─────────────────────────────

@pytest.mark.slow
def test_zero_duration(tmp_path):
    out = tmp_path / "stress_zero_duration.mp4"
    result = run_pipeline(FIXTURES_DIR / "stress_zero_duration.json", out, ["--no-voice"])
    _graceful(result, "zero_duration")
    # Accept either: clean failure OR valid output (pipeline may coerce to min duration)
    if result.returncode != 0:
        return  # graceful failure — OK
    if out.exists() and out.stat().st_size > 0:
        spec = json.loads((FIXTURES_DIR / "stress_zero_duration.json").read_text())
        _validate(out, spec)


# ── 12. Negative duration — must fail cleanly ────────────────────────────────

@pytest.mark.slow
def test_negative_duration(tmp_path):
    out = tmp_path / "stress_negative_duration.mp4"
    result = run_pipeline(FIXTURES_DIR / "stress_negative_duration.json", out, ["--no-voice"])
    _graceful(result, "negative_duration")
    assert result.returncode != 0 or not out.exists() or out.stat().st_size == 0, (
        "Pipeline produced output for a negative-duration spec without error"
    )


# ── 13. Unicode / emoji text ─────────────────────────────────────────────────

@pytest.mark.slow
def test_unicode_text(tmp_path):
    spec = json.loads((FIXTURES_DIR / "stress_unicode.json").read_text())
    out = tmp_path / "stress_unicode.mp4"
    result = run_pipeline(FIXTURES_DIR / "stress_unicode.json", out, ["--no-voice"])
    _graceful(result, "unicode_text")
    if result.returncode == 0 and out.exists():
        _validate(out, spec)


# ── 14. Corrupt cached clip — graceful fallback ───────────────────────────────

@pytest.mark.slow
def test_corrupt_clip(tmp_path):
    """Pre-populate clip cache with a corrupt file; pipeline must not crash."""
    clip_cache = tmp_path / ".clip_cache"
    clip_cache.mkdir()
    # Use a known YouTube ID so the cache lookup hits this file
    corrupt_id = "corruptXXXXX"
    (clip_cache / f"{corrupt_id}.mp4").write_bytes(b"NOT A VALID VIDEO FILE\x00\xff\x00")

    spec = {
        "id": "stress_corrupt_clip",
        "label": "CORRUPT",
        "scheme": "dark_gold",
        "screens": [{
            "text": "Corrupt clip test\nshould fallback",
            "duration": 2.0,
            "clip": {
                "url": f"https://www.youtube.com/watch?v={corrupt_id}",
                "start": 0,
                "end": 2,
            },
        }],
    }
    spec_path = tmp_path / "corrupt.json"
    spec_path.write_text(json.dumps(spec))
    out = tmp_path / "output.mp4"

    result = subprocess.run(
        ["python", "-m", "vidpipe", str(spec_path), "-o", str(out), "--no-voice"],
        capture_output=True, text=True, timeout=120,
        cwd=str(tmp_path),  # working dir has the pre-populated .clip_cache
    )
    _graceful(result, "corrupt_clip")
    # If it succeeded, output must be a valid video (fallback to gradient)
    if result.returncode == 0 and out.exists():
        _validate(out, spec)


# ── 15. Concurrent builds (race condition check) ──────────────────────────────

@pytest.mark.slow
def test_concurrent_builds(tmp_path):
    """Two pipeline runs with different IDs must complete without corrupting each other."""
    specs = [
        {
            "id": "concurrent_a",
            "label": "A",
            "scheme": "dark_gold",
            "screens": [{"text": "Concurrent A", "duration": 2.0}],
        },
        {
            "id": "concurrent_b",
            "label": "B",
            "scheme": "dark_blue",
            "screens": [{"text": "Concurrent B", "duration": 2.0}],
        },
    ]
    results: list[subprocess.CompletedProcess | None] = [None, None]

    def run(idx: int):
        spec_path = tmp_path / f"spec_{idx}.json"
        spec_path.write_text(json.dumps(specs[idx]))
        out = tmp_path / f"output_{idx}.mp4"
        results[idx] = run_pipeline(spec_path, out, ["--no-voice"])

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=180)

    for i, result in enumerate(results):
        assert result is not None, f"Build {i} thread timed out"
        _graceful(result, f"concurrent_{i}")
        assert result.returncode == 0, (
            f"Concurrent build {i} failed:\n{result.stderr[:500]}"
        )
        out = tmp_path / f"output_{i}.mp4"
        assert out.exists() and out.stat().st_size > 0, f"Build {i} produced no output"
        _validate(out, specs[i])


# ── 16. Preview mode ─────────────────────────────────────────────────────────

@pytest.mark.slow
def test_preview_mode(tmp_path):
    spec = {
        "id": "stress_preview",
        "label": "PREVIEW",
        "scheme": "dark_green",
        "screens": [
            {"text": "Preview screen 1", "duration": 2.0},
            {"text": "Preview screen 2", "duration": 2.0},
        ],
    }
    spec_path = tmp_path / "preview.json"
    spec_path.write_text(json.dumps(spec))
    out = tmp_path / "preview.mp4"
    result = run_pipeline(spec_path, out, ["--no-voice", "--preview"])
    assert result.returncode == 0, f"Pipeline failed:\n{result.stderr}"
    _validate(out, spec, preview=True)
