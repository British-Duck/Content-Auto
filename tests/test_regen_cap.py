"""
Tests for Phase A, Step 3 — the bounded Stage 1.5 regeneration loop.

The CD can't run live (missing-prompt issue), so the CD and reviewer are stubbed
to return a spec that keeps failing the (unchanged) factual check, letting us count
regenerations without a live model. Tests run the loop directly AND through the real
orchestrate.run() lifecycle so the record + abort-before-render behaviour is exercised.

Run: pytest tests/test_regen_cap.py -v
"""
import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import config  # noqa: E402


def _v2_spec() -> dict:
    """A v2-contract-valid spec (passes the Step 1 gate cleanly, ~30s)."""
    clip = "some real event 2020"
    return {
        "storyboard": "angle, shape, per-segment reasons, three-channel plan",
        "id": "regen-cap-test", "label": "REGEN CAP TEST",
        "structure": "straight report", "scheme": "dark_red",
        "voice_script": "narration that adds something the text does not show",
        "screens": [
            {"text": "The lead line", "reason": "hook", "duration": 5.0, "transition": "cut",
             "text_size": 88, "panel": True, "subtitle": None, "clip_mode": None,
             "clip_description": clip, "clip_moment": "the key moment", "audio_mode": "vo"},
            {"text": "Second beat", "reason": "develop", "duration": 5.0, "transition": "crossfade",
             "text_size": 84, "panel": False, "subtitle": None, "clip_mode": None,
             "clip_description": None, "clip_moment": None, "audio_mode": "vo"},
            {"text": "Third beat", "reason": "develop", "duration": 5.0, "transition": "cut",
             "text_size": 84, "panel": True, "subtitle": None, "clip_mode": None,
             "clip_description": clip, "clip_moment": "another moment", "audio_mode": "vo"},
            {"text": "The turn", "reason": "turn", "duration": 5.0, "transition": "zoom",
             "text_size": 84, "panel": False, "subtitle": None, "clip_mode": None,
             "clip_description": None, "clip_moment": None, "audio_mode": "vo"},
            {"text": "The proof", "reason": "proof", "duration": 5.0, "transition": "cut",
             "text_size": 84, "panel": True, "subtitle": None, "clip_mode": None,
             "clip_description": clip, "clip_moment": "proof moment", "audio_mode": "vo"},
            {"text": "So what now?", "reason": "kick", "duration": 5.0, "transition": "crossfade",
             "text_size": 84, "panel": False, "subtitle": None, "clip_mode": None,
             "clip_description": None, "clip_moment": None, "audio_mode": "vo"},
        ],
    }


_FAILING_REVIEW = {
    "decision": "REVISE", "overall_score": 5.0,
    "scores": {"factual_accuracy": {"score": 4, "reason": "a number looks wrong"}},
    "fixes": ["check the number"], "summary": "factual issue",
}
_PASSING_REVIEW = {
    "decision": "PUBLISH", "overall_score": 9.0,
    "scores": {"factual_accuracy": {"score": 9, "reason": "checks out"}},
    "fixes": [], "summary": "good",
}


# ── Unit: the loop itself caps regenerations ─────────────────────────────────

def test_loop_caps_at_two_regenerations(monkeypatch):
    import orchestrate
    fake_cr = types.ModuleType("content_reviewer")
    fake_cr.review_spec = lambda spec: dict(_FAILING_REVIEW)
    monkeypatch.setitem(sys.modules, "content_reviewer", fake_cr)

    revise_calls = {"n": 0}
    fake_cd = types.ModuleType("creative_director")

    def fake_revise(brief, spec, review):
        revise_calls["n"] += 1
        return dict(spec)
    fake_cd.revise_spec = fake_revise
    monkeypatch.setitem(sys.modules, "creative_director", fake_cd)

    best, review, attempts, regens, blocking = orchestrate._spec_review_loop(
        {"id": "x", "screens": []}, "a brief")

    assert regens == 2                  # capped
    assert revise_calls["n"] == 2       # no third regeneration
    assert len(attempts) == 3           # 3 reviews (initial + 2 regen)
    assert blocking is not None
    assert blocking["factual_accuracy"] == 4


def test_loop_stops_early_when_spec_passes(monkeypatch):
    import orchestrate
    state = {"n": 0}

    def review(spec):
        state["n"] += 1
        return dict(_FAILING_REVIEW) if state["n"] == 1 else dict(_PASSING_REVIEW)
    fake_cr = types.ModuleType("content_reviewer")
    fake_cr.review_spec = review
    monkeypatch.setitem(sys.modules, "content_reviewer", fake_cr)

    revise_calls = {"n": 0}
    fake_cd = types.ModuleType("creative_director")

    def fake_revise(brief, spec, review):
        revise_calls["n"] += 1
        return dict(spec)
    fake_cd.revise_spec = fake_revise
    monkeypatch.setitem(sys.modules, "creative_director", fake_cd)

    *_, regens, blocking = orchestrate._spec_review_loop({"id": "x", "screens": []}, "b")
    assert regens == 1            # passed on the second try
    assert blocking is None
    assert revise_calls["n"] == 1


# ── Integration: run() flags after the cap and does not render ───────────────

def _wire_brief_run(monkeypatch, review_fn, tmp_path):
    """Stub the CD (generate + revise), reviewer, research, render, quality and
    cleanup so the real run() drives a brief through Stage 1.5 offline. Redirects
    SPECS_DIR/OUTPUT_DIR to a temp dir. Returns (cd_calls, render_calls)."""
    import orchestrate, researcher, quality_gate

    monkeypatch.setattr(config, "SPECS_DIR", tmp_path)
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)

    cd_calls = {"generate": 0, "revise": 0}
    fake_cd = types.ModuleType("creative_director")

    def fake_generate(brief, force=False, dry_run=False):
        cd_calls["generate"] += 1
        return json.loads(json.dumps(_v2_spec()))

    def fake_revise(brief, spec, review):
        cd_calls["revise"] += 1
        return json.loads(json.dumps(_v2_spec()))
    fake_cd.generate_spec = fake_generate
    fake_cd.revise_spec = fake_revise
    monkeypatch.setitem(sys.modules, "creative_director", fake_cd)

    fake_cr = types.ModuleType("content_reviewer")
    fake_cr.review_spec = review_fn
    monkeypatch.setitem(sys.modules, "content_reviewer", fake_cr)

    monkeypatch.setattr(researcher, "research_spec", lambda spec, selective=True: spec)

    render_calls = []
    fake_cli = types.ModuleType("vidpipe.cli")

    def fake_render(args):
        render_calls.append(args)
        Path(args[args.index("-o") + 1]).write_bytes(b"fake")
        return 0
    fake_cli.main = fake_render
    monkeypatch.setitem(sys.modules, "vidpipe.cli", fake_cli)

    monkeypatch.setattr(quality_gate, "check_video",
                        lambda v, s, preview=False: {"passed": True,
                                                     "checks": {"r": {"passed": True, "detail": "ok"}},
                                                     "frames_dir": None})
    monkeypatch.setattr(orchestrate, "_cleanup_intermediates", lambda spec_id="": None)
    return cd_calls, render_calls


def test_run_flags_after_regen_cap_and_does_not_render(monkeypatch, tmp_path):
    import orchestrate
    monkeypatch.setattr(config, "COST_CEILING_USD", 100.0)  # ensure regen cap fires, not ceiling
    cd_calls, render_calls = _wire_brief_run(monkeypatch, lambda spec: dict(_FAILING_REVIEW), tmp_path)

    summary = orchestrate.run("A persistently failing brief, dark_red")

    data = json.loads(Path(summary["run_record"]).read_text(encoding="utf-8"))
    assert data["regenerations"] == 2
    assert data["final_status"] == "flagged_regen_cap"
    assert any("regen" in f.lower() or "factual" in f.lower() for f in data["flags_for_human"])
    assert render_calls == []                  # failing draft NOT rendered
    assert summary["output"] is None
    assert cd_calls["generate"] == 1 and cd_calls["revise"] == 2   # 3 CD calls total


def test_run_proceeds_when_spec_passes_after_one_regen(monkeypatch, tmp_path):
    import orchestrate
    monkeypatch.setattr(config, "COST_CEILING_USD", 100.0)

    state = {"n": 0}

    def review(spec):
        state["n"] += 1
        # fail once (triggers a regen), then pass for the loop + Stage 5.
        return dict(_FAILING_REVIEW) if state["n"] == 1 else dict(_PASSING_REVIEW)

    cd_calls, render_calls = _wire_brief_run(monkeypatch, review, tmp_path)

    summary = orchestrate.run("A brief that passes after one fix, dark_red")

    data = json.loads(Path(summary["run_record"]).read_text(encoding="utf-8"))
    assert data["regenerations"] == 1
    assert data["final_status"] == "publish"
    assert cd_calls["revise"] == 1
    assert len(render_calls) == 1              # proceeded to render
