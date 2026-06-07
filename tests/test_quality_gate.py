"""
Tests for Step 6a — frame-extraction timestamps account for crossfade compression.

The rendered timeline is shorter than the sum of segment durations because each
seam crossfades with the previous segment by CROSSFADE_DURATION. The per-segment
frame the Quality Gate extracts (and hands to Output-QA) must sit at the segment's
true midpoint in that compressed timeline, not at the naive cumulative offset.

Run: pytest tests/test_quality_gate.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quality_gate import _frame_timestamps  # noqa: E402


def _screens(durations):
    return [{"duration": d} for d in durations]


def test_no_crossfade_is_plain_midpoint():
    ts = _frame_timestamps(_screens([3.0, 3.0, 3.0]), xfade=0.0)
    assert ts == [1.5, 4.5, 7.5]


def test_crossfade_shifts_each_segment_earlier_by_i_times_xfade():
    # FTX-like: 7 segments, 0.3s seams. Segment i drifts earlier by i*0.3 vs naive.
    durs = [2.8, 3.2, 2.5, 4.0, 2.0, 3.5, 4.5]
    xf = 0.3
    ts = _frame_timestamps(_screens(durs), xfade=xf)
    cumulative = 0.0
    for i, d in enumerate(durs):
        naive = cumulative + d / 2
        assert abs(ts[i] - (naive - i * xf)) < 1e-9
        cumulative += d
    # The last frame is pulled (n-1)*xf = 1.8s earlier than the naive sample.
    assert abs((sum(durs[:-1]) + durs[-1] / 2) - ts[-1] - 1.8) < 1e-9


def test_clamps_within_actual_duration():
    # A frame that would fall past the real end is clamped just inside it.
    ts = _frame_timestamps(_screens([2.0, 2.0, 2.0]), xfade=0.0, actual=5.0)
    assert all(0.0 <= t <= 5.0 - 0.05 + 1e-9 for t in ts)
    assert ts[-1] == 5.0 - 0.05  # naive 5.0 clamped to actual-0.05
