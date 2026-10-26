"""
Tests for the footage fallback ladder (researcher tiers + still sourcing + tier log).

Heavy I/O (search, download, ffmpeg, Gemini) is mocked; we test the LADDER control
flow: tier 1 clip -> tier 2 alt-query clip -> tier 3 Ken Burns still -> tier 4 forced
gradient, plus the per-beat tier recording and still sourcing precedence.

Run: pytest tests/test_fallback_ladder.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import researcher  # noqa: E402


# ── Alternate-query generation ───────────────────────────────────────────────

def test_heuristic_alts_broadens_and_uses_moment():
    alts = researcher._heuristic_alts("Sam Bankman-Fried congressional testimony",
                                      "SBF speaking at the microphone")
    assert "Sam Bankman-Fried congressional testimony" not in alts  # never the original
    assert any(len(a.split()) < 4 for a in alts)                    # a broadened form
    assert all(a.strip() for a in alts)


def test_alternate_queries_falls_back_to_heuristic_on_llm_failure(monkeypatch):
    monkeypatch.setattr(researcher.gemini, "generate",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no api")))
    alts = researcher._alternate_queries("Volkswagen diesel emissions scandal", None,
                                         "context", n=3)
    assert alts                       # heuristics kicked in
    assert "Volkswagen diesel emissions scandal" not in alts


# ── Still sourcing precedence ────────────────────────────────────────────────

def test_make_still_prefers_downloaded_frame(monkeypatch, tmp_path):
    vid = tmp_path / "cand.mp4"
    vid.write_bytes(b"x")
    monkeypatch.setattr(researcher, "_extract_still", lambda v, at, out: True)
    monkeypatch.setattr(config, "STILLS_DIR", tmp_path)
    cand_acc = [{"video_id": "abc", "url": "u", "title": "t",
                 "path": str(vid), "moment_start": 12.0}]
    result = researcher._make_still({"clip_moment": "x"}, 0, cand_acc)
    assert result is not None
    _, detail = result
    assert detail["source"] == "candidate_frame"
    assert detail["at_seconds"] == 12.0


def test_make_still_falls_back_to_thumbnail(monkeypatch, tmp_path):
    monkeypatch.setattr(researcher, "_extract_still", lambda v, at, out: False)
    monkeypatch.setattr(researcher, "_fetch_thumbnail", lambda vid, out: True)
    monkeypatch.setattr(config, "STILLS_DIR", tmp_path)
    cand_acc = [{"video_id": "abc", "url": "u", "title": "t", "path": None, "moment_start": None}]
    result = researcher._make_still({}, 0, cand_acc)
    assert result is not None and result[1]["source"] == "thumbnail"


def test_make_still_none_when_no_candidates(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "STILLS_DIR", tmp_path)
    assert researcher._make_still({}, 0, []) is None


# ── The ladder, end to end (mocked resolution) ───────────────────────────────

def test_research_spec_ladder_tiers(monkeypatch):
    from run_record import RunRecord, CostMeter, set_active_record

    def fake_resolve(query, screen, used_ranges, cand_acc):
        cand_acc.append({"video_id": "v_" + query, "url": "u", "title": "t",
                         "path": None, "moment_start": None})
        if query in ("q1", "q2_alt1"):
            return {"url": "https://y/watch?v=VID12345678", "start": 1.0, "end": 3.0}
        return None

    monkeypatch.setattr(researcher, "_resolve_query", fake_resolve)
    monkeypatch.setattr(researcher, "_alternate_queries",
                        lambda desc, moment, context, n=None: [desc + "_alt1", desc + "_alt2"])
    monkeypatch.setattr(researcher, "_make_still",
                        lambda screen, si, cand_acc: None
                        if screen.get("clip_description") == "q4"
                        else ("/tmp/still.jpg", {"source": "candidate_frame", "video_id": "vX"}))

    spec = {"id": "ladder", "screens": [
        {"text": "A", "duration": 4.0, "clip_description": "q1"},   # tier 1 clip
        {"text": "B", "duration": 4.0, "clip_description": "q2"},   # tier 2 alt-query
        {"text": "C", "duration": 4.0, "clip_description": "q3"},   # tier 3 still
        {"text": "D", "duration": 4.0, "clip_description": "q4"},   # tier 4 forced gradient
        {"text": "E", "duration": 4.0, "clip_description": None},   # intentional gradient
    ]}

    rec = RunRecord(brief="b", input_arg="b", meter=CostMeter())
    set_active_record(rec)
    try:
        out = researcher.research_spec(spec, selective=True)
    finally:
        set_active_record(None)

    s = out["screens"]
    assert s[0].get("clip") and not s[0].get("still")
    assert s[1].get("clip")                       # resolved via an alternate query
    assert s[2].get("still") and not s[2].get("clip")
    assert not s[3].get("clip") and not s[3].get("still")
    assert not s[4].get("clip")

    tiers = {e["screen_index"]: e["tier"] for e in rec.data["footage_ladder"]}
    assert tiers[0] == "clip"
    assert tiers[1] == "alt_query_clip"
    assert tiers[2] == "kenburns_still"
    assert tiers[3] == "gradient_forced"
    assert tiers[4] == "intentional_gradient"
    # alt-query attempts are recorded; the loop stops at the first alt that hits.
    b1 = next(e for e in rec.data["footage_ladder"] if e["screen_index"] == 1)
    assert b1["alt_queries_tried"] == ["q2_alt1"]
    # a beat that exhausts all alternates records all of them
    b3 = next(e for e in rec.data["footage_ladder"] if e["screen_index"] == 3)
    assert b3["alt_queries_tried"] == ["q4_alt1", "q4_alt2"]


def test_add_footage_tier_appends_to_record():
    from run_record import RunRecord, CostMeter
    rec = RunRecord(brief="b", input_arg="b", meter=CostMeter())
    rec.add_footage_tier(2, "kenburns_still", source="thumbnail", video_id="abc")
    assert rec.data["footage_ladder"] == [
        {"screen_index": 2, "tier": "kenburns_still", "source": "thumbnail", "video_id": "abc"}
    ]
