"""
Tests for Phase B, Step 5 — the split reviewers wired into orchestrate.run().

Offline-faithful: the real run() lifecycle is driven with the editorial critic,
Output-QA, research and render stubbed, so the run record is produced exactly as a
live run would produce it. The model's *judgement* (whether a draft is a REVISE, or a
render a FAIL) is demonstrated separately with real Gemini calls; here we prove the
WIRING — record blocks populated, FAIL → non-publish + blocking_issues + no re-render.

Run: pytest tests/test_step5_reviewers.py -v
"""
import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

import config  # noqa: E402


def _v2_spec() -> dict:
    clip = "boeing 737 max footage"
    return {
        "storyboard": "angle, shape, per-segment reasons, three-channel plan",
        "id": "step5-itest", "label": "STEP FIVE TEST",
        "structure": "evidence-stack", "scheme": "dark_red",
        "voice_script": "narration that adds something the text does not show",
        "screens": [
            {"text": "The lead line", "reason": "hook", "duration": 5.0, "transition": "cut",
             "text_size": 88, "panel": True, "subtitle": None, "clip_mode": None,
             "clip_description": clip, "clip_moment": "grounded jets", "audio_mode": "vo"},
            {"text": "Second beat", "reason": "develop", "duration": 5.0, "transition": "crossfade",
             "text_size": 84, "panel": False, "subtitle": None, "clip_mode": None,
             "clip_description": None, "clip_moment": None, "audio_mode": "vo"},
            {"text": "The proof", "reason": "proof", "duration": 5.0, "transition": "cut",
             "text_size": 84, "panel": True, "subtitle": None, "clip_mode": None,
             "clip_description": clip, "clip_moment": "the hearing", "audio_mode": "vo"},
            {"text": "So what now?", "reason": "kick", "duration": 5.0, "transition": "crossfade",
             "text_size": 84, "panel": False, "subtitle": None, "clip_mode": None,
             "clip_description": None, "clip_moment": None, "audio_mode": "vo"},
        ],
    }


def _wire(monkeypatch, tmp_path, *, qa_result, frames_dir="frames", review_fn=None,
          generate_fn=None):
    """Stub research/render/quality + the two reviewers so run() executes offline.
    Returns render_calls (a list appended to once per render call)."""
    import orchestrate, researcher, quality_gate

    monkeypatch.setattr(config, "SPECS_DIR", tmp_path)
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(config, "COST_CEILING_USD", 100.0)

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
                                                     "frames_dir": frames_dir})

    fake_qa = types.ModuleType("output_qa")
    fake_qa.qa_output = lambda spec, probe, frames: dict(qa_result) if qa_result else None
    monkeypatch.setitem(sys.modules, "output_qa", fake_qa)

    if review_fn is not None:
        fake_ec = types.ModuleType("editorial_critic")
        fake_ec.review_spec = review_fn
        monkeypatch.setitem(sys.modules, "editorial_critic", fake_ec)

    if generate_fn is not None:
        fake_cd = types.ModuleType("creative_director")
        fake_cd.generate_spec = generate_fn
        fake_cd.revise_spec = lambda brief, spec, review: json.loads(json.dumps(_v2_spec()))
        monkeypatch.setitem(sys.modules, "creative_director", fake_cd)

    monkeypatch.setattr(orchestrate, "_cleanup_intermediates", lambda spec_id="": None)
    return render_calls


_QA_FAIL = {
    "decision": "FAIL",
    "blocking_issues": [
        "Segment 2: Missing footage. Rendered a gradient instead of the specified "
        "'boeing 737 max footage'.",
    ],
    "warnings": [], "per_segment": [], "summary": "gradient on an evidence beat",
}
_QA_PASS = {
    "decision": "PASS", "blocking_issues": [], "warnings": [],
    "per_segment": [], "summary": "render matches the plan",
}


# ── Output-QA FAIL: non-publish, blocking_issues surfaced, NOT re-rendered ────

def test_output_qa_fail_flags_and_does_not_republish(tmp_path, monkeypatch):
    import orchestrate
    spec_path = tmp_path / "step5-itest.json"
    spec_path.write_text(json.dumps(_v2_spec()), encoding="utf-8")

    render_calls = _wire(monkeypatch, tmp_path, qa_result=_QA_FAIL)

    summary = orchestrate.run(str(spec_path))

    assert summary["success"] is False                 # non-publish (drives exit code 1)
    data = json.loads(Path(summary["run_record"]).read_text(encoding="utf-8"))
    assert data["stages"]["output_qa"]["status"] == "FAIL"
    assert data["stages"]["output_qa"]["detail"]["blocking_issues"]
    assert data["final_status"] == "needs_human_review"
    # The segment-named blocking issue reached flags_for_human.
    assert any("Segment 2" in f and "gradient" in f.lower() for f in data["flags_for_human"])
    assert len(render_calls) == 1                      # flag-and-stop — never re-rendered


def test_output_qa_pass_publishes(tmp_path, monkeypatch):
    import orchestrate
    spec_path = tmp_path / "step5-itest.json"
    spec_path.write_text(json.dumps(_v2_spec()), encoding="utf-8")

    render_calls = _wire(monkeypatch, tmp_path, qa_result=_QA_PASS)

    summary = orchestrate.run(str(spec_path))

    assert summary["success"] is True
    data = json.loads(Path(summary["run_record"]).read_text(encoding="utf-8"))
    assert data["stages"]["output_qa"]["status"] == "PASS"
    assert data["final_status"] == "publish"
    assert len(render_calls) == 1


# ── Both run-record reviewer blocks populated on a full brief run ─────────────

def test_both_reviewer_blocks_populated_on_brief_run(tmp_path, monkeypatch):
    import orchestrate

    state = {"n": 0}

    def review(spec):
        state["n"] += 1
        # REVISE once (triggers a regeneration), then PUBLISH.
        if state["n"] == 1:
            return {"decision": "REVISE", "biggest_weakness": "weak hook",
                    "scores": {"hook": {"score": 4, "reason": "generic"}},
                    "fixes": ["lead with the number"],
                    "facts_to_check": ["verify the 346 figure"]}
        return {"decision": "PUBLISH", "biggest_weakness": "minor",
                "scores": {"hook": {"score": 9, "reason": "strong"}},
                "fixes": [], "facts_to_check": []}

    def generate(brief, force=False, dry_run=False):
        return json.loads(json.dumps(_v2_spec()))

    render_calls = _wire(monkeypatch, tmp_path, qa_result=_QA_PASS,
                         review_fn=review, generate_fn=generate)

    summary = orchestrate.run("A brief that revises once then passes, dark_red")

    data = json.loads(Path(summary["run_record"]).read_text(encoding="utf-8"))
    # editorial_critic block populated with the final verdict + regen count
    ec = data["stages"]["editorial_critic"]
    assert ec["status"] == "PUBLISH"
    assert ec["detail"]["regenerations"] == 1
    assert data["regenerations"] == 1
    # facts_to_check surfaced (logged for a human, never blocking)
    assert any("346" in f for f in ec["detail"]["facts_to_check"])
    assert any("non-blocking" in f.lower() for f in data["flags_for_human"])
    # output_qa block populated
    assert data["stages"]["output_qa"]["status"] == "PASS"
    assert data["final_status"] == "publish"
    assert len(render_calls) == 1
