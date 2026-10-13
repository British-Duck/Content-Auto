"""
Acceptance + regression tests for the Phase A, Step 1 spec validation gate.

Run as pytest (`pytest tests/test_spec_validator.py`) or standalone for a
human-readable acceptance report (`python tests/test_spec_validator.py`).
"""
import copy
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from spec_validator import validate_spec, SpecValidationError  # noqa: E402


def valid_spec() -> dict:
    """A fully contract-compliant v2 spec (8 screens, ~36s, one isolated gradient)."""
    return {
        "storyboard": "Angle: the record that should not exist. Shape: reveal across 8 "
                      "beats, the margin withheld until the end. Channels: footage carries "
                      "the proof, voice adds the context the text omits.",
        "id": "usain-bolt-untouchable-record",
        "label": "THE UNTOUCHABLE RECORD",
        "structure": "reveal: withhold the winning margin until the final beat",
        "scheme": "dark_gold",
        "voice_script": "Fifteen years on, the fastest man alive is still a ghost no "
                        "sprinter has caught, and the numbers say they may never.",
        "screens": [
            {"text": "Nobody has come close", "reason": "hook: a record frozen for 15 years",
             "duration": 4.0, "transition": "cut", "text_size": 90, "panel": True,
             "subtitle": None, "clip_mode": None,
             "clip_description": "Usain Bolt 9.58 world record Berlin 2009",
             "clip_moment": "Bolt crossing the line clear of the field", "audio_mode": "vo"},
            {"text": "9.58 seconds", "reason": "the number, stated plainly as the spine",
             "duration": 5.0, "transition": "crossfade", "text_size": 96, "panel": True,
             "subtitle": "100m world record", "clip_mode": None,
             "clip_description": "Usain Bolt 9.58 world record Berlin 2009",
             "clip_moment": "the clock reading 9.58", "audio_mode": "vo"},
            {"text": "He was decelerating", "reason": "the turn: the surprising detail",
             "duration": 4.0, "transition": "zoom", "text_size": 88, "panel": True,
             "subtitle": None, "clip_mode": None,
             "clip_description": "Usain Bolt 9.58 world record Berlin 2009",
             "clip_moment": "Bolt easing up in the final 10 metres", "audio_mode": "vo"},
            {"text": "And still won by a street", "reason": "stacks proof onto the turn",
             "duration": 5.0, "transition": "cut", "text_size": 84, "panel": True,
             "subtitle": None, "clip_mode": None,
             "clip_description": "Usain Bolt 9.58 world record Berlin 2009",
             "clip_moment": "the gap to second place at the finish", "audio_mode": "vo"},
            {"text": "Two metres ahead", "reason": "data beat: the margin, held as a card",
             "duration": 4.0, "transition": "crossfade", "text_size": 92, "panel": False,
             "subtitle": "the winning margin", "clip_mode": None,
             "clip_description": None, "clip_moment": None, "audio_mode": "vo"},
            {"text": "The crowd knew instantly", "reason": "native audio carries the roar",
             "duration": 5.0, "transition": "zoom", "text_size": 86, "panel": True,
             "subtitle": None, "clip_mode": "ambient",
             "clip_description": "Usain Bolt 9.58 world record Berlin 2009",
             "clip_moment": "the stadium reaction as he finishes", "audio_mode": "native"},
            {"text": "No one since has touched it", "reason": "kick toward the open question",
             "duration": 4.0, "transition": "cut", "text_size": 82, "panel": True,
             "subtitle": None, "clip_mode": None,
             "clip_description": "100m world record progression",
             "clip_moment": "the record line untouched on a progression chart", "audio_mode": "vo"},
            {"text": "So who ever will?", "reason": "kick: provoke, do not summarise",
             "duration": 5.0, "transition": "crossfade", "text_size": 94, "panel": True,
             "subtitle": None, "clip_mode": None,
             "clip_description": "Usain Bolt 9.58 world record Berlin 2009",
             "clip_moment": "Bolt's signature pose", "audio_mode": "vo"},
        ],
    }


def malformed_spec() -> dict:
    """The acceptance scenario: bad scheme + duration 9.0 + two consecutive
    gradients + an empty-string subtitle, plus a non-cut first transition and a
    panel mismatch to exercise the repair paths."""
    s = valid_spec()
    s["scheme"] = "dark_orange"            # HARD: bad enum
    s["screens"][0]["transition"] = "zoom"  # REPAIR: first segment -> cut
    s["screens"][1]["duration"] = 9.0       # REPAIR: clamp -> 6.0
    s["screens"][2]["subtitle"] = ""        # REPAIR: "" -> null
    s["screens"][3]["panel"] = False        # REPAIR: has clip -> panel True
    # HARD: make screens 4 and 5 both gradient (consecutive)
    s["screens"][5]["clip_description"] = None
    s["screens"][5]["clip_mode"] = None
    return s


# ── pytest ───────────────────────────────────────────────────────────────────

def test_valid_spec_passes_untouched():
    spec = valid_spec()
    original = copy.deepcopy(spec)
    result, repairs = validate_spec(spec)
    assert repairs == []
    assert result == original  # no mutation on a clean spec


def test_malformed_spec_is_rejected_with_hard_violations():
    spec = malformed_spec()
    with pytest.raises(SpecValidationError) as ei:
        validate_spec(spec)
    violations = ei.value.violations
    assert any("scheme" in v for v in violations)
    assert any("two consecutive gradient" in v for v in violations)


def test_repairs_applied_even_when_rejected():
    # Repairs mutate the spec in place, so they are observable after a hard reject.
    spec = malformed_spec()
    with pytest.raises(SpecValidationError):
        validate_spec(spec)
    assert spec["screens"][0]["transition"] == "cut"   # first forced to cut
    assert spec["screens"][1]["duration"] == 6.0        # clamped from 9.0
    assert spec["screens"][2]["subtitle"] is None       # "" -> null
    assert spec["screens"][3]["panel"] is True          # matched to clip_description


def test_out_of_range_values_are_clamped_not_rejected():
    spec = valid_spec()
    spec["screens"][0]["duration"] = 0.2     # below min
    spec["screens"][1]["text_size"] = 140    # above max
    result, repairs = validate_spec(spec)    # must NOT raise
    assert result["screens"][0]["duration"] == 1.0
    assert result["screens"][1]["text_size"] == 100
    assert len(repairs) == 2


# ── standalone acceptance report ─────────────────────────────────────────────

def _report():
    logging.basicConfig(level=logging.INFO, format="    %(levelname)-7s %(message)s")

    print("\n" + "=" * 72)
    print("  STEP 1 ACCEPTANCE — Spec Validation Gate")
    print("=" * 72)

    print("\n[1] VALID SPEC — expect: passes, untouched, 0 repairs\n")
    spec = valid_spec()
    original = copy.deepcopy(spec)
    result, repairs = validate_spec(spec)
    untouched = result == original
    print(f"\n    ->repairs={len(repairs)}  untouched={untouched}  "
          f"VERDICT: {'PASS' if (not repairs and untouched) else 'FAIL'}")

    print("\n" + "-" * 72)
    print("\n[2] MALFORMED SPEC — bad scheme, duration 9.0, consecutive gradients,")
    print("    empty subtitle (+ non-cut first transition, panel mismatch)")
    print("    expect: HARD-REJECT, repairs applied + logged, never proceeds\n")
    bad = malformed_spec()
    try:
        validate_spec(bad)
        print("\n    ->VERDICT: FAIL (should have raised)")
    except SpecValidationError as e:
        print(f"\n    ->REJECTED with {len(e.violations)} hard violation(s):")
        for v in e.violations:
            print(f"        [X] {v}")
        print("    ->repairs applied in place despite reject:")
        print(f"        screens[0].transition = {bad['screens'][0]['transition']!r} (was 'zoom')")
        print(f"        screens[1].duration   = {bad['screens'][1]['duration']!r} (was 9.0)")
        print(f"        screens[2].subtitle   = {bad['screens'][2]['subtitle']!r} (was '')")
        print(f"        screens[3].panel      = {bad['screens'][3]['panel']!r} (was False)")
        hard_ok = any("scheme" in v for v in e.violations) and \
            any("two consecutive gradient" in v for v in e.violations)
        repairs_ok = (bad["screens"][0]["transition"] == "cut" and
                      bad["screens"][1]["duration"] == 6.0 and
                      bad["screens"][2]["subtitle"] is None and
                      bad["screens"][3]["panel"] is True)
        print(f"\n    ->VERDICT: {'PASS' if (hard_ok and repairs_ok) else 'FAIL'}")

    print("\n" + "=" * 72 + "\n")


if __name__ == "__main__":
    _report()
