"""
Tests for Phase A, Step 2 — run record + cost meter + ceiling + loud fallbacks.

Offline-faithful: the live CD/Gemini path can't run today (missing-prompt issue),
so the cost hooks are exercised with mocked Gemini usage and the orchestrator is
driven with stubbed research/render — through the REAL run() lifecycle, so the
record is produced exactly as a live run would produce it.

Run: pytest tests/test_run_record.py -v
"""
import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402


# ── Cost meter: pricing comes from the config table, attributed per stage ─────

def test_meter_prices_from_config_table():
    from run_record import CostMeter
    m = CostMeter()
    with m.stage("research"):
        m.record_gemini_usage(
            {"prompt_token_count": 2000, "candidates_token_count": 100, "thoughts_token_count": 0},
            "gemini-2.5-flash")
    expected = round((2000 * 0.15 + 100 * 0.60) / 1_000_000, 6)
    assert round(m.total_cost(), 6) == expected
    assert m.cost_by_stage()["research"] == m.total_cost()


def test_meter_unknown_model_falls_back_to_default_price():
    from run_record import CostMeter
    m = CostMeter()
    m.record_gemini_usage(
        {"prompt_token_count": 1_000_000, "candidates_token_count": 0, "thoughts_token_count": 0},
        "some-future-model")
    # 1M input tokens at the default input price == that price exactly.
    assert round(m.total_cost(), 6) == round(config.GEMINI_PRICING_DEFAULT["input"], 6)


# ── Acceptance #4: CD-stage cost hook with a mocked Gemini usage response ─────

def test_cd_cost_hook_with_mocked_gemini_usage():
    import gemini
    from run_record import reset_cost_meter

    meter = reset_cost_meter()

    class FakeUsage:
        prompt_token_count = 1000
        candidates_token_count = 500
        thoughts_token_count = 200

    class FakeResponse:
        usage_metadata = FakeUsage()
        text = "{}"

    # This is exactly what gemini.generate() calls after a real API response,
    # under the stage context orchestrate sets around the Creative Director.
    with meter.stage("creative_director"):
        gemini._emit_usage(FakeResponse(), "gemini-2.5-pro")

    expected = round((1000 * 1.25 + 500 * 10.0 + 200 * 10.0) / 1_000_000, 6)
    by_stage = meter.cost_by_stage()
    assert "creative_director" in by_stage
    assert by_stage["creative_director"] == expected
    assert meter.total_cost() == expected


# ── Record shape: forward-compatible, captures the CD's reasoning ────────────

def test_record_is_forward_compatible():
    from run_record import RunRecord, CostMeter
    rec = RunRecord(brief="A brief about something", input_arg="A brief about something",
                    meter=CostMeter())
    d = rec.data
    assert d["stages"]["editorial_critic"] is None     # Step 5
    assert d["stages"]["output_qa"] is None             # Step 5
    assert d["regenerations"] is None                   # Step 3
    for k in ("creative_director", "validation", "research", "render"):
        assert set(d["stages"][k]) == {"status", "cost", "detail"}
    assert set(d["prompt_versions"]) == {"creative_director", "editorial_critic", "output_qa"}
    assert d["cost_ceiling"] == config.COST_CEILING_USD


def test_bind_spec_captures_storyboard_and_structure():
    from run_record import RunRecord, CostMeter
    rec = RunRecord(brief="b", input_arg="b", meter=CostMeter())
    rec.bind_spec({"id": "foo-bar", "structure": "reveal", "storyboard": "SB text", "screens": []})
    assert rec.data["spec_id"] == "foo-bar"
    assert rec.data["structure"] == "reveal"
    assert rec.data["storyboard"] == "SB text"
    assert rec.data["run_id"].startswith("foo-bar_")


# ── Acceptance #2: a real no-footage case records a loud fallback ────────────

def test_researcher_records_gradient_fallback_with_screen_index(monkeypatch):
    import researcher
    from run_record import RunRecord, CostMeter, set_active_record

    rec = RunRecord(brief="b", input_arg="b", meter=CostMeter())
    set_active_record(rec)
    try:
        # Force "no usable footage": the YouTube search returns nothing.
        monkeypatch.setattr(researcher, "_search_youtube", lambda q, n: [])
        spec = {"id": "grad", "screens": [
            {"text": "x", "duration": 4.0, "clip_description": "nonexistent footage query"},
        ]}
        researcher.research_spec(spec, selective=True)
    finally:
        set_active_record(None)

    assert spec["screens"][0]["clip"] is None
    assert any(f["screen_index"] == 0 for f in rec.data["fallback_reasons"])


# ── Integration helpers ──────────────────────────────────────────────────────

def _provided_spec():
    return {
        "id": "step2-itest", "label": "STEP TWO TEST", "scheme": "dark_red",
        "voice_script": "narration", "screens": [
            {"text": "Lead", "duration": 5.0, "clip_description": "some query", "panel": True},
            {"text": "Beat", "duration": 5.0, "clip_description": None, "panel": False},
            {"text": "Proof", "duration": 5.0, "clip_description": "another query", "panel": True},
            {"text": "Kick", "duration": 5.0, "clip_description": None, "panel": False},
        ],
    }


def _wire_stubs(monkeypatch, render_calls, tmp_path):
    """Stub research (emits real research cost + a fallback), render, quality, reviewer,
    and the cache cleanup — so the REAL run() lifecycle writes a real record offline.
    Redirect SPECS_DIR/OUTPUT_DIR to a temp dir so the working tree is never touched."""
    import orchestrate, researcher, quality_gate
    from run_record import get_cost_meter, get_active_record

    monkeypatch.setattr(config, "SPECS_DIR", tmp_path)
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)

    def fake_research(spec, selective=True):
        get_cost_meter().record_gemini_usage(
            {"prompt_token_count": 2000, "candidates_token_count": 100, "thoughts_token_count": 0},
            "gemini-2.5-flash")
        get_active_record().add_fallback(2, "stub: no usable footage for 'another query'")
        spec["screens"][0]["clip"] = {"url": "http://x", "start": 0.0, "end": 3.0}
        return spec
    monkeypatch.setattr(researcher, "research_spec", fake_research)

    def fake_render(args):
        render_calls.append(args)
        out = Path(args[args.index("-o") + 1])
        out.write_bytes(b"fake")
        return 0
    fake_cli = types.ModuleType("vidpipe.cli")
    fake_cli.main = fake_render
    monkeypatch.setitem(sys.modules, "vidpipe.cli", fake_cli)

    monkeypatch.setattr(quality_gate, "check_video",
                        lambda v, s, preview=False: {"passed": True,
                                                     "checks": {"resolution": {"passed": True, "detail": "ok"}},
                                                     "frames_dir": None})

    # Post-render Output-QA (Step 5) — PASS so a provided-spec run reaches "publish".
    fake_qa = types.ModuleType("output_qa")
    fake_qa.qa_output = lambda spec, probe, frames: {
        "decision": "PASS", "blocking_issues": [], "warnings": [],
        "per_segment": [], "summary": "ok",
    }
    monkeypatch.setitem(sys.modules, "output_qa", fake_qa)

    monkeypatch.setattr(orchestrate, "_cleanup_intermediates", lambda spec_id="": None)


# ── Acceptance #1: provided-spec run produces a complete record ──────────────

def test_provided_spec_run_writes_complete_record(tmp_path, monkeypatch):
    import orchestrate
    spec_path = tmp_path / "step2-itest.json"
    spec_path.write_text(json.dumps(_provided_spec()), encoding="utf-8")

    render_calls = []
    _wire_stubs(monkeypatch, render_calls, tmp_path)

    summary = orchestrate.run(str(spec_path))

    rec_path = Path(summary["run_record"])
    assert rec_path.exists()
    data = json.loads(rec_path.read_text(encoding="utf-8"))

    assert data["stages"]["research"]["status"] == "OK"
    assert data["stages"]["research"]["cost"] > 0          # research cost reported
    assert data["stages"]["render"]["status"] == "OK"      # render populated
    assert data["total_cost"] > 0                          # total_cost set
    assert data["stages"]["editorial_critic"] is None      # still null (Step 5)
    assert any(f["screen_index"] == 2 for f in data["fallback_reasons"])
    assert data["final_status"] == "publish"
    assert len(render_calls) == 1


# ── Acceptance #3: a very low ceiling aborts before render, still writes ──────

def test_low_ceiling_aborts_before_render_and_writes_record(tmp_path, monkeypatch):
    import orchestrate
    monkeypatch.setattr(config, "COST_CEILING_USD", 0.0001)  # below the stub research cost

    spec_path = tmp_path / "step2-itest.json"
    spec_path.write_text(json.dumps(_provided_spec()), encoding="utf-8")

    render_calls = []
    _wire_stubs(monkeypatch, render_calls, tmp_path)

    summary = orchestrate.run(str(spec_path))

    data = json.loads(Path(summary["run_record"]).read_text(encoding="utf-8"))
    assert data["cost_exceeded"] is True
    assert data["final_status"] == "aborted_cost_ceiling"
    assert any("ceiling" in f.lower() for f in data["flags_for_human"])
    assert render_calls == []          # render never ran
    assert summary["output"] is None
