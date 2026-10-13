"""
Tests for Step 6b — hero-clip spine detection + the missing-spine loud flag.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import orchestrate  # noqa: E402


class _FakeRecord:
    def __init__(self):
        self.flags = []

    def add_flag(self, msg):
        self.flags.append(msg)


def _spec(screens):
    return {"id": "t", "screens": screens}


def test_hero_is_the_most_shared_clip_description():
    spec = _spec([
        {"clip_description": "apollo 11 landing broadcast"},
        {"clip_description": "apollo 11 landing broadcast"},
        {"clip_description": "apollo 11 landing broadcast"},
        {"clip_description": "mission control room 1969"},
        {"clip_description": None},
    ])
    desc, indices = orchestrate._hero_clip_description(spec)
    assert desc == "apollo 11 landing broadcast"
    assert indices == [0, 1, 2]


def test_missing_spine_flagged_when_hero_finds_no_footage():
    spec = _spec([
        {"clip_description": "hero source", "clip": None},
        {"clip_description": "hero source", "clip": None},
        {"clip_description": "hero source", "clip": None},
        {"clip_description": None},
    ])
    rec = _FakeRecord()
    orchestrate._flag_missing_spine(spec, rec)
    assert len(rec.flags) == 1
    assert "MISSING SPINE" in rec.flags[0]
    assert "hero source" in rec.flags[0]


def test_no_flag_when_hero_partially_resolves():
    spec = _spec([
        {"clip_description": "hero source", "clip": {"url": "http://x", "start": 0, "end": 3}},
        {"clip_description": "hero source", "clip": None},
        {"clip_description": "hero source", "clip": None},
    ])
    rec = _FakeRecord()
    orchestrate._flag_missing_spine(spec, rec)
    assert rec.flags == []  # spine exists (>=1 segment resolved)


def test_single_clip_is_not_a_spine():
    # A lone clip_description that fails is an ordinary fallback, not a missing spine.
    spec = _spec([
        {"clip_description": "one off clip", "clip": None},
        {"clip_description": None},
    ])
    rec = _FakeRecord()
    orchestrate._flag_missing_spine(spec, rec)
    assert rec.flags == []
