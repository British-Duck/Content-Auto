"""
Tests for the single-clip event format (format: "single_clip").

Offline-faithful: no ffmpeg, no network. They exercise the contract gate, the spec
model, the pure render-math helpers, and the researcher's resolve/fallback control
flow (Gemini + yt-dlp mocked). The montage path is asserted untouched.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import spec_validator  # noqa: E402
import researcher  # noqa: E402
from vidpipe import single_clip  # noqa: E402
from vidpipe.spec import load_spec  # noqa: E402


# ── A minimal, contract-valid v2 spec builder ────────────────────────────────

def _screen(text="A beat", clip_desc=None, clip_moment=None, dur=4.0):
    return {
        "text": text, "reason": "because", "duration": dur, "transition": "cut",
        "text_size": 84, "panel": bool(clip_desc), "subtitle": None,
        "clip_mode": None, "clip_description": clip_desc, "clip_moment": clip_moment,
        "audio_mode": "vo",
    }


def _base_spec(**over):
    # Alternating clip / gradient so the contract's "no two consecutive gradients"
    # rule passes; callers can override `screens`.
    spec = {
        "storyboard": "the plan", "id": "test-story", "label": "TEST STORY",
        "structure": "straight report", "scheme": "dark_gold",
        "voice_script": "narration goes here.",
        "screens": [_screen(clip_desc="a clip moment"), _screen(),
                    _screen(clip_desc="b clip moment"), _screen(),
                    _screen(clip_desc="c clip moment")],
    }
    spec.update(over)
    # keep transitions valid: first must be cut
    spec["screens"][0]["transition"] = "cut"
    return spec


# ── Validator ────────────────────────────────────────────────────────────────

def test_absent_format_left_untouched_as_montage():
    spec, repairs = spec_validator.validate_spec(_base_spec())
    # A clean montage spec never mentions format and must not be mutated or repaired;
    # the rest of the pipeline treats absent == montage.
    assert "format" not in spec
    assert not any("format" in r for r in repairs)
    assert spec.get("format", "montage") == "montage"


def test_single_clip_accepted_with_top_level_source():
    hero = "Usain Bolt 9.58 world record Berlin 2009"
    spec = _base_spec(format="single_clip", clip_description=hero,
                      key_moment="he crosses the line")
    out, _ = spec_validator.validate_spec(spec)
    assert out["format"] == "single_clip"
    assert out["clip_description"] == hero


def test_single_clip_without_source_rejected():
    spec = _base_spec(format="single_clip")  # no top-level clip_description
    with pytest.raises(spec_validator.SpecValidationError) as e:
        spec_validator.validate_spec(spec)
    assert any("clip_description" in v for v in e.value.violations)


def test_bad_format_value_rejected():
    spec = _base_spec(format="slideshow")
    with pytest.raises(spec_validator.SpecValidationError) as e:
        spec_validator.validate_spec(spec)
    assert any("format" in v for v in e.value.violations)


def test_single_clip_allows_long_held_beats():
    # 7-13s beats are the single_clip pacing; they must NOT be clamped to 6s
    # (the clamp dragged the first Bolt draft to 24.5s, under the 30s floor).
    screens = [_screen(clip_desc="full race upload", dur=d) for d in (9.0, 13.0, 12.5)]
    screens.append(_screen(clip_desc="full race upload", dur=6.5))
    spec = _base_spec(format="single_clip", clip_description="full race upload",
                      key_moment="the finish", screens=screens)
    out, repairs = spec_validator.validate_spec(spec)
    assert [s["duration"] for s in out["screens"]] == [9.0, 13.0, 12.5, 6.5]
    assert not any("duration" in r for r in repairs)


def test_single_clip_still_clamps_past_its_own_ceiling():
    screens = [_screen(clip_desc="full race upload", dur=d) for d in (20.0, 8.0, 8.0, 8.0)]
    spec = _base_spec(format="single_clip", clip_description="full race upload",
                      key_moment="the finish", screens=screens)
    out, repairs = spec_validator.validate_spec(spec)
    assert out["screens"][0]["duration"] == spec_validator.SINGLE_CLIP_DURATION_MAX
    assert any("duration" in r for r in repairs)


def test_montage_duration_ceiling_unchanged():
    spec = _base_spec()
    spec["screens"][0]["duration"] = 9.0
    out, repairs = spec_validator.validate_spec(spec)
    assert out["screens"][0]["duration"] == spec_validator.DURATION_MAX
    assert any("duration" in r for r in repairs)


# ── Spec model (load_spec) ───────────────────────────────────────────────────

def _write(tmp_path, spec):
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(spec), encoding="utf-8")
    return p


def test_load_spec_parses_hero_clip(tmp_path):
    spec = _base_spec(
        format="single_clip",
        clip_description="some race full broadcast",
        hero_clip={"url": "https://youtu.be/abc12345678", "window_start": 10.0,
                   "window_end": 50.0, "key_moment_ts": 32.0, "subject_x": 0.7,
                   "source_duration": 120.0},
    )
    vs = load_spec(_write(tmp_path, spec))
    assert vs.format == "single_clip"
    assert vs.hero_clip is not None
    assert vs.hero_clip.window_length == pytest.approx(40.0)
    assert vs.hero_clip.subject_x == 0.7


def test_load_spec_montage_has_no_hero_clip(tmp_path):
    vs = load_spec(_write(tmp_path, _base_spec()))
    assert vs.format == "montage"
    assert vs.hero_clip is None


def test_load_spec_malformed_hero_clip_falls_back(tmp_path):
    spec = _base_spec(format="single_clip", clip_description="x y z",
                      hero_clip={"url": "https://youtu.be/abc12345678",
                                 "window_start": 50.0, "window_end": 50.0})  # zero-length
    vs = load_spec(_write(tmp_path, spec))
    assert vs.hero_clip is None  # ignored → will render as montage


# ── Render math (pure) ───────────────────────────────────────────────────────

def test_beat_timeline_is_cumulative():
    screens = [{"duration": 2.0}, {"duration": 3.0}, {"duration": 1.5}]
    assert single_clip.beat_timeline(screens) == [(0.0, 2.0), (2.0, 5.0), (5.0, 6.5)]


def test_wide_source_uses_blurred_fit_not_crop():
    # 16:9 into 9:16 discards ~68% of width — must NOT centre-crop the subject out.
    chain = single_clip.vertical_chain(1920, 1080, 1080, 1920, subject_x=0.5)
    assert "boxblur" in chain          # blurred-background fit
    assert "force_original_aspect_ratio=decrease" in chain


def test_portrait_source_uses_focus_crop():
    # A 4:5 source can fill the frame with a focus-aware crop (little width lost).
    chain = single_clip.vertical_chain(1080, 1350, 1080, 1920, subject_x=0.8)
    assert "boxblur" not in chain
    assert "crop=1080:1920" in chain
    assert "0.800" in chain            # subject_x anchored, not naive centre


def test_aligned_start_lands_key_at_payoff_when_lead_up_exists():
    # window 0-50, key at 30s, 35s runtime, 60s source, key_fraction 0.6
    # desired start = 30 - 0.6*35 = 9.0 (slack available) → key lands at 60% in.
    start = single_clip.aligned_start(0.0, 50.0, 30.0, 35.0, 60.0, 0.6)
    assert start == pytest.approx(9.0)
    assert (30.0 - start) / 35.0 == pytest.approx(0.6)


def test_aligned_start_clamps_when_lead_up_too_short():
    # Bolt-like: window 0-35 (no slack), key at 11.8 — can't slide, plays from start.
    start = single_clip.aligned_start(0.0, 35.0, 11.8, 35.0, 45.9, 0.6)
    assert start == 0.0


def test_aligned_start_no_key_uses_window_start():
    assert single_clip.aligned_start(5.0, 60.0, None, 35.0, 90.0, 0.6) == 5.0


def test_fit_start_slides_back_when_source_short():
    # Window said start at 10s but the fetched file is only 30s: a 25s runtime
    # overruns (10+25 > 30) and must slide back to 5.0 so the full runtime fits.
    assert single_clip.fit_start_to_source(10.0, 25.0, 30.0) == pytest.approx(5.0)


def test_fit_start_refuses_uncoverable_source():
    # Source shorter than the runtime itself: no start can work, must be None
    # (the renderer then FAILS loudly instead of shipping a cut-off video).
    assert single_clip.fit_start_to_source(0.0, 31.5, 25.1) is None


def test_fit_start_noop_when_source_covers():
    assert single_clip.fit_start_to_source(9.0, 35.0, 90.0) == pytest.approx(9.0)


def test_fit_start_unknown_duration_passes_through():
    assert single_clip.fit_start_to_source(9.0, 35.0, 0.0) == pytest.approx(9.0)


def test_render_refuses_source_shorter_than_runtime(monkeypatch, tmp_path):
    # Full control-flow check: a fetched hero source shorter than the runtime must
    # abort the render (return False) BEFORE any ffmpeg/overlay work happens.
    spec = _base_spec(
        format="single_clip", clip_description="full disaster footage",
        hero_clip={"url": "https://youtu.be/abc12345678", "window_start": 3.0,
                   "window_end": 43.0, "key_moment_ts": 34.0, "subject_x": 0.5,
                   "source_duration": 327.0},
        screens=[_screen(clip_desc="full disaster footage", dur=8.0) for _ in range(4)],
    )
    vs = load_spec(_write(tmp_path, spec))
    monkeypatch.setattr(single_clip, "ensure_clip", lambda url: tmp_path / "fake.mp4")
    monkeypatch.setattr(single_clip, "_probe_dims", lambda p: (1280, 720, 25.1))

    def _no_ffmpeg(*a, **k):
        raise AssertionError("ffmpeg must not run for an uncoverable source")
    monkeypatch.setattr(single_clip, "_run_ffmpeg", _no_ffmpeg)

    ok = single_clip.render_single_clip(vs, tmp_path, tmp_path / "out.mp4")
    assert ok is False


def test_overlay_chain_one_window_per_beat():
    tl = single_clip.beat_timeline([{"duration": 2.0}, {"duration": 2.0}, {"duration": 2.0}])
    chain = single_clip.overlay_chain(tl)
    assert chain.count("overlay=0:0:enable=") == 3
    assert chain.endswith("[vout]")
    assert "gte(t,4.000)" in chain     # last beat runs to the end


# ── Output-QA format awareness ───────────────────────────────────────────────

def _run_qa(monkeypatch, tmp_path, spec):
    """Drive output_qa.qa_output offline, capturing the prompt it builds."""
    import output_qa

    frames = tmp_path / "frames"
    frames.mkdir(exist_ok=True)
    for i in range(len(spec["screens"])):
        (frames / f"screen_{i:02d}.jpg").write_bytes(b"fake-jpeg")

    captured = {}

    def fake_vision(prompt, images, **kw):
        captured["prompt"] = prompt
        return json.dumps({"decision": "PASS", "blocking_issues": [], "warnings": [],
                           "per_segment": [], "summary": "ok"})

    monkeypatch.setattr(output_qa.gemini, "generate_vision", fake_vision)
    monkeypatch.setattr(output_qa.time, "sleep", lambda s: None)
    result = output_qa.qa_output(spec, {"passed": True}, frames)
    return result, captured["prompt"]


def test_output_qa_prompt_flags_single_clip_mode(monkeypatch, tmp_path):
    spec = _base_spec(format="single_clip", clip_description="full race upload",
                      key_moment="the finish",
                      screens=[_screen(clip_desc="full race upload") for _ in range(4)])
    result, prompt = _run_qa(monkeypatch, tmp_path, spec)
    assert result["decision"] == "PASS"
    assert "FORMAT: single_clip" in prompt
    assert "NOT render instructions" in prompt
    # The system prompt's replacement checks must be present for the model to use.
    assert "CONTINUOUS FOOTAGE" in prompt


def test_output_qa_prompt_flags_montage_mode(monkeypatch, tmp_path):
    result, prompt = _run_qa(monkeypatch, tmp_path, _base_spec())
    assert result["decision"] == "PASS"
    assert "FORMAT: montage" in prompt
    assert "FORMAT: single_clip" not in prompt


# ── Researcher resolve / fallback control flow ───────────────────────────────

class _FakeRecord:
    def __init__(self):
        self.flags = []
        self.tiers = []

    def add_flag(self, msg):
        self.flags.append(msg)

    def add_footage_tier(self, idx, tier, **d):
        self.tiers.append((idx, tier))


def test_single_clip_research_success(monkeypatch):
    from run_record import set_active_record
    rec = _FakeRecord()
    set_active_record(rec)
    try:
        hero = {"url": "https://youtu.be/abc12345678", "window_start": 8.0,
                "window_end": 48.0, "key_moment_ts": 30.0, "subject_x": 0.5,
                "source_duration": 90.0}
        monkeypatch.setattr(researcher, "_research_single_clip", lambda spec: hero)
        spec = _base_spec(format="single_clip", clip_description="full race upload",
                          screens=[_screen(clip_desc="full race upload") for _ in range(5)])
        out = researcher.research_spec(spec, selective=True)
        assert out["format"] == "single_clip"
        assert out["hero_clip"] == hero
        assert any(t == "single_clip_window" for _, t in rec.tiers)
    finally:
        set_active_record(None)


def test_single_clip_research_falls_back_to_montage(monkeypatch):
    from run_record import set_active_record
    rec = _FakeRecord()
    set_active_record(rec)
    try:
        monkeypatch.setattr(researcher, "_research_single_clip", lambda spec: None)
        # screens carry NO clip_description so the montage fallback is a no-op
        # (keeps the test offline — no search/download).
        spec = _base_spec(format="single_clip", clip_description="missing footage event",
                          screens=[_screen(), _screen(text="second")])
        out = researcher.research_spec(spec, selective=True)
        assert out["format"] == "montage"          # demoted
        assert out["hero_clip"] is None
        assert any("fell back to montage" in f for f in rec.flags)
    finally:
        set_active_record(None)


def _wire_single_clip_internals(monkeypatch, candidates, finder):
    monkeypatch.setattr(researcher, "_search_youtube", lambda q, n: candidates)
    monkeypatch.setattr(researcher, "_rank_candidates", lambda c, q: c)  # preserve order
    monkeypatch.setattr(researcher, "_download", lambda url, vid: Path("fake_" + vid))
    monkeypatch.setattr(researcher, "_probe_duration", lambda p: 120.0)
    monkeypatch.setattr(researcher, "_find_window", finder)
    monkeypatch.setattr(researcher.time, "sleep", lambda s: None)


def _win(overlay):
    return {"window_start": 1.0, "window_end": 41.0, "key_moment_ts": 21.0,
            "subject_x": 0.5, "heavy_overlay": overlay, "confidence": "high", "reason": ""}


def test_single_clip_prefers_clean_over_overlaid(monkeypatch):
    cands = [{"id": "over", "url": "u_over", "title": "t", "duration": 120},
             {"id": "clean", "url": "u_clean", "title": "t", "duration": 120}]
    _wire_single_clip_internals(monkeypatch, cands,
                                lambda vid, *a, **k: _win("over" in str(vid)))
    hero = researcher._research_single_clip({"clip_description": "x", "screens": [{"duration": 35.0}]})
    assert hero["url"] == "u_clean"   # the overlaid candidate was skipped for a cleaner one


def test_single_clip_falls_back_to_overlaid_when_no_clean(monkeypatch):
    cands = [{"id": "over", "url": "u_over", "title": "t", "duration": 120}]
    _wire_single_clip_internals(monkeypatch, cands, lambda vid, *a, **k: _win(True))
    hero = researcher._research_single_clip({"clip_description": "x", "screens": [{"duration": 35.0}]})
    assert hero["url"] == "u_over"    # overlaid is still one continuous take → beats montage


def test_hero_source_query_prefers_top_level():
    spec = _base_spec(format="single_clip", clip_description="top level query",
                      screens=[_screen(clip_desc="spine query") for _ in range(3)])
    assert researcher._hero_source_query(spec) == "top level query"


def test_hero_source_query_falls_back_to_spine():
    spec = _base_spec(screens=[_screen(clip_desc="spine query"),
                               _screen(clip_desc="spine query"),
                               _screen(clip_desc="other")])
    assert researcher._hero_source_query(spec) == "spine query"
