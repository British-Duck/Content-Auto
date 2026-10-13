"""
Tests for the editorial critic's deterministic COMPUTED METRICS feed.

The pipeline measures script-fit and spine numbers; the model only judges them.
Offline-faithful: the LLM call is captured, never made.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import editorial_critic  # noqa: E402


def _screen(text="A beat", clip_desc=None, dur=4.0):
    return {"text": text, "reason": "because", "duration": dur, "transition": "cut",
            "text_size": 84, "panel": bool(clip_desc), "subtitle": None,
            "clip_mode": None, "clip_description": clip_desc, "clip_moment": None,
            "audio_mode": "vo"}


def _spec(screens, voice_script="one two three four five six"):
    return {"storyboard": "plan", "id": "metrics-test", "label": "METRICS TEST",
            "structure": "straight report", "scheme": "dark_gold",
            "voice_script": voice_script, "screens": screens}


def test_metrics_runtime_and_read_estimate():
    spec = _spec([_screen(dur=10.0), _screen(dur=5.0)],
                 voice_script=" ".join(["word"] * 33))
    m = editorial_critic.compute_metrics(spec)
    assert m["runtime_s"] == 15.0
    assert m["voice_script_words"] == 33
    assert m["estimated_read_s"] == pytest.approx(10.0)      # 33 / 3.3
    assert m["read_overrun_pct"] == pytest.approx(-33.3)     # comfortably under


def test_metrics_flags_overrun():
    # The Bolt failure shape: ~34s of script over a 24.5s runtime.
    spec = _spec([_screen(dur=12.0), _screen(dur=12.5)],
                 voice_script=" ".join(["word"] * 112))
    m = editorial_critic.compute_metrics(spec)
    assert m["estimated_read_s"] > m["runtime_s"]
    assert m["read_overrun_pct"] > 15.0


def test_metrics_detects_near_duplicate_spine_descriptions():
    # Case/punctuation drift silently breaks the exact-match spine grouping.
    spec = _spec([_screen(clip_desc="Usain Bolt 9.58 Berlin"),
                  _screen(clip_desc="usain bolt 958 berlin"),
                  _screen(clip_desc="crowd reaction shot")])
    m = editorial_critic.compute_metrics(spec)
    assert len(m["near_duplicate_clip_descriptions"]) == 1
    assert sorted(m["near_duplicate_clip_descriptions"][0]) == [
        "Usain Bolt 9.58 Berlin", "usain bolt 958 berlin"]
    assert m["n_gradient_screens"] == 0


def test_metrics_counts_spine_and_gradients():
    spec = _spec([_screen(clip_desc="hero query"), _screen(),
                  _screen(clip_desc="hero query"), _screen(),
                  _screen(clip_desc="hero query")])
    m = editorial_critic.compute_metrics(spec)
    assert m["clip_description_counts"] == {"hero query": 3}
    assert m["n_gradient_screens"] == 2
    assert m["near_duplicate_clip_descriptions"] == []


def test_review_spec_injects_metrics_into_prompt(monkeypatch):
    captured = {}

    def fake_generate(prompt, **kw):
        captured["prompt"] = prompt
        return json.dumps({"decision": "PUBLISH", "biggest_weakness": "none",
                           "scores": {}, "fixes": [], "facts_to_check": []})

    monkeypatch.setattr(editorial_critic.llm, "generate", fake_generate)
    monkeypatch.setattr(editorial_critic.time, "sleep", lambda s: None)

    spec = _spec([_screen(dur=10.0), _screen(dur=10.0), _screen(dur=10.0)],
                 voice_script=" ".join(["word"] * 99))
    result = editorial_critic.review_spec(spec)

    assert result["decision"] == "PUBLISH"
    assert "COMPUTED METRICS" in captured["prompt"]
    assert '"runtime_s": 30.0' in captured["prompt"]
    assert '"voice_script_words": 99' in captured["prompt"]
    # The metrics block must precede the spec so the model reads it as context.
    assert captured["prompt"].index("COMPUTED METRICS") < captured["prompt"].index("VIDEO SPEC:")
