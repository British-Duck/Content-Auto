# Content-Auto

[![tests](https://github.com/British-Duck/Content-Auto/actions/workflows/tests.yml/badge.svg)](https://github.com/British-Duck/Content-Auto/actions/workflows/tests.yml)

An automated pipeline that turns a one-line editorial brief into a finished 1080×1920
short-form news video: script, sourced footage, render and narration. It includes
machine review before and after rendering, and a hard per-run cost ceiling.

> **Status:** research prototype, under active development. The pipeline runs end to
> end. It is partway through a planned migration to its v2 architecture
> (see [Status and roadmap](#status-and-roadmap)).

## Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Requirements](#requirements)
4. [Installation](#installation)
5. [Usage](#usage)
6. [Configuration](#configuration)
7. [Outputs](#outputs)
8. [Testing](#testing)
9. [Repository layout](#repository-layout)
10. [Design principles](#design-principles)
11. [Status and roadmap](#status-and-roadmap)
12. [Limitations](#limitations)
13. [Documentation](#documentation)
14. [License](#license)

## Overview

Given a brief such as:

```
"Usain Bolt's 9.58 world record. Use dark_blue, untouchable-record angle."
```

the pipeline:

1. plans a 30–50 second video as a structured JSON **spec**: storyboard, per-screen
   text beats, durations, footage descriptions and a voice script;
2. reviews that plan with an **editorial critic** before any expensive work is done;
3. searches for, timestamps and trims real **footage** for each beat;
4. **renders** the video with ffmpeg: text overlays, a colour scheme, TTS narration and
   a music bed ducked under the voice;
5. checks the result twice: once with **technical checks** (ffprobe) and once with a
   **vision review** of extracted frames against the plan.

Anything that falls below the bar is flagged for a human and the run exits with a
non-publish status. The pipeline never silently ships or auto-repairs a defective video.

Most of the engineering effort is in the systems around the model calls, not the calls
themselves. The pipeline has six stages and uses more than one model, so the work goes
into keeping it **observable** (one structured run record per video), **bounded**
(capped revision loops and a cost ceiling that aborts the run) and **honest** (every
fallback is logged with its reason).

## Architecture

```
brief
  │
  ▼
[1]   Creative Director ── brief → spec JSON                 creative_director.py
  │
  ▼
[gate] Spec contract ──── hard-reject / auto-repair / log    spec_validator.py
  │
  ▼
[1.5] Editorial Critic ── PUBLISH / REVISE / REJECT          editorial_critic.py
  │      └─ REVISE → back to [1], at most 2 regenerations
  ▼
[2]   Researcher ──────── footage search + timestamps        researcher.py
  │
  ▼
[3]   Render ──────────── spec → MP4 (ffmpeg)                vidpipe/
  │
  ▼
[4]   Quality Gate ────── ffprobe checks + frame extraction  quality_gate.py
  │
  ▼
[5]   Output-QA ───────── vision review → PASS / FAIL        output_qa.py
```

| Stage | Module | Responsibility |
|---|---|---|
| 1. Creative Director | `creative_director.py` | Generates the v2 spec from the brief in a single LLM pass, then applies deterministic fix-ups and the requested colour scheme. Specs are cached by brief hash. |
| Spec contract | `spec_validator.py` | Enforces the spec schema before any paid stage. It rejects specs that break the contract, auto-repairs the safe cases and logs every violation. |
| 1.5. Editorial Critic | `editorial_critic.py` | Reviews the plan before rendering, using deterministic metrics (script fit, footage-spine counts, near-duplicate clips) as well as the model's judgement. A `REVISE` sends fixes back to stage 1, up to two regenerations. A `REJECT`, or a spec still marked `REVISE` after the cap, is flagged for a human and not rendered. Factual claims go to a `facts_to_check` list for a human and never cause a rejection. |
| 2. Researcher | `researcher.py`, `clip_knowledge.py` | Finds footage for each beat in this order: yt-dlp search, then timestamps from video metadata (free), then Gemini video analysis only when needed. If footage can't be found, it falls back through alternate queries, then a Ken Burns still, then a gradient, and records which tier each beat ended up on. A clip knowledge base built across runs is checked before any new download. |
| 3. Render | `vidpipe/` | ffmpeg and Pillow renderer with two paths. **Montage** gives each screen its own segment, joined with crossfades. **Single-clip** uses one continuous hero window with a focus-aware vertical crop, and the text beats are overlaid as a timeline. Narration comes from edge-tts, with a sidechain-ducked music bed underneath. |
| 4. Quality Gate | `quality_gate.py` | Checks resolution, duration, codec and black frames with ffprobe, and extracts one frame per segment. |
| 5. Output-QA | `output_qa.py` | Vision review of the extracted frames against the plan. It checks for real footage versus gradient fallbacks, whether the footage matches the on-screen claim, and whether the text is legible. The review depends on the format: single-clip videos are judged on continuity rather than per-segment matches. A `FAIL` reports `blocking_issues` and sets a non-zero exit code. This stage never re-renders. |

**Cross-cutting components**

- `run_record.py` writes one JSON record per run: stage verdicts, the footage fallback
  ladder, and per-stage cost computed from real token usage. If cumulative spend goes
  over the configured ceiling, the run aborts.
- `llm.py` routes each stage to its configured model. `gemini-*` models go to
  `gemini.py` and `claude-*` models go to `claude.py`. If a provider is misconfigured,
  it falls back to Gemini and logs why.
- `cost_tracker.py` estimates cost before a run (`--dry-run`) and meters it during one.

## Requirements

- Python 3.11 or later (developed on 3.13)
- [ffmpeg](https://ffmpeg.org/) and ffprobe on `PATH`
- A Google Gemini API key

Python dependencies (`requirements.txt`):

| Package | Purpose |
|---|---|
| `google-genai` | Gemini client: generation, review and native video analysis |
| `yt-dlp` | YouTube search, metadata and downloads |
| `edge-tts` | Narration |
| `Pillow`, `numpy` | Text overlay and gradient rendering |
| `python-dotenv` | Loads `.env` |
| `anthropic` | *Optional.* Only needed if a stage is switched to a `claude-*` model |

## Installation

```bash
git clone <repository-url>
cd <repository>
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then set GEMINI_API_KEY
```

For development and tests, run `pip install -r requirements-dev.txt` instead.

Music is optional. To use it, put royalty-free instrumental tracks in `music/` (see
[`music/README.md`](music/README.md)). Without music, the video has voiceover only.

## Usage

```bash
# Brief → finished video
python orchestrate.py "Brief text. Use dark_red, scandal angle."

# Estimate cost stage by stage without making any API calls
python orchestrate.py "Brief text..." --dry-run

# Fast half-resolution render for iteration
python orchestrate.py "Brief text..." --preview

# Ignore the brief cache and regenerate the spec
python orchestrate.py "Brief text..." --force-regen

# Re-render an existing spec without searching for footage again
python orchestrate.py specs/<id>.json --skip-research
```

| Flag | Effect |
|---|---|
| `--dry-run` | Prints the projected cost of each stage and exits |
| `--preview` | Renders at 540×960 / 24 fps |
| `--force-regen` | Bypasses the cached spec for this brief |
| `--skip-research` | Uses the clips already in the spec (or gradients) |
| `--no-voice` | Renders without narration |
| `-v`, `--verbose` | Debug logging |

**Briefs.** A brief is free text. It can name a colour scheme (`dark_gold`, `dark_red`,
`dark_blue`, `dark_green`, `dark_purple`) and an editorial angle. If the brief names a
scheme, the pipeline always uses it.

**Exit codes.** The exit code is zero only if both the technical Quality Gate and
Output-QA pass. A spec that the editorial critic rejected is flagged and not rendered.

## Configuration

All settings are in [`config.py`](config.py). The main ones:

| Setting | Default | Notes |
|---|---|---|
| `CREATIVE_MODEL` | `gemini-3-flash-preview` | Stage 1 |
| `EDITORIAL_CRITIC_MODEL` | `gemini-3-flash-preview` | Stage 1.5 |
| `OUTPUT_QA_MODEL` | `gemini-3-flash-preview` | Stage 5; must support vision |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Researcher and default; must stay Gemini (native video analysis) |
| `COST_CEILING_USD` | `0.75` | Per-run hard ceiling; the run aborts if spend goes over it |
| `SPEC_REVIEW_MAX_RETRIES` | `2` | Cap on critic-driven regenerations |
| `THINKING_BUDGET_*` | varies | Per-stage thinking-token caps; the largest cost lever |
| `NARRATOR_VOICE` | `en-GB-RyanNeural` | Single fixed narrator |
| `MUSIC_ENABLED` | `True` | Music bed on or off; see `MUSIC_*` for levels and ducking |

Each stage's model can be changed with one line in `config.py`. To try a Claude model
on a judgement stage, set it to a `claude-*` model ID, add `ANTHROPIC_API_KEY` to `.env`
and install `anthropic`. The researcher has to stay on Gemini. For cost-related
settings and the cache layout, see [`docs/COST_TUNING.md`](docs/COST_TUNING.md).

## Outputs

A completed run produces the following. All of these are generated and git-ignored.

| Path | Contents |
|---|---|
| `output/<id>.mp4` | The rendered video |
| `output/<id>_frames/` | One extracted frame per segment (used by Output-QA) |
| `specs/<id>.json` | The final spec, with the resolved clips |
| `specs/<id>_storyboard.md`, `_timeline.txt` | Human-readable plan and timeline |
| `logs/` | The run record: verdicts, fallbacks, per-stage cost |
| `cache/`, `.clip_cache/` | Brief, search, metadata, timestamp and clip caches |

## Testing

```bash
pip install -r requirements-dev.txt
python -m pytest
```

A plain `pytest` runs 69 tests **offline**, with no API key, network access or ffmpeg
I/O. The core of it is the 66-test gate that CI runs on every push (Python 3.11 and
3.13):

```
tests/test_spec_validator.py    tests/test_run_record.py
tests/test_regen_cap.py         tests/test_step5_reviewers.py
tests/test_fallback_ladder.py   tests/test_hero_spine.py
tests/test_single_clip.py       tests/test_editorial_metrics.py
```

plus three frame-timestamp tests in `tests/test_quality_gate.py`.

These tests are *offline-faithful*. They run the real orchestrator lifecycle with only
the network, model and ffmpeg boundaries stubbed. That means they exercise the actual
control flow: the spec-contract gate, the bounded revision loop, the fallback ladder,
the run-record and cost-meter wiring, and the single-clip render maths.

Three markers are excluded by default (see `pytest.ini`) and are opt-in:

| Marker | Tests | Run with |
|---|---|---|
| `slow` | Build real videos with ffmpeg | `pytest -m slow` |
| `network` | Download real clips | `pytest -m network` |
| `legacy` | The v1 render suites `test_stress.py` and `test_output_quality.py` | `pytest -m legacy` |

The `legacy` suites compare renders against visual baselines in `tests/baselines/` that
were captured before the renderer changed, so their SSIM and keyframe checks are
expected to fail and they are not part of the gate. `tests/run_tests.py` is a
standalone validator that can be pointed at any rendered MP4.

## Repository layout

```
orchestrate.py           Entry point: wires stages, cost ceiling and run record
config.py                All tuneables: models, prices, render constants, thresholds
creative_director.py     Stage 1: brief → spec
spec_validator.py        Spec contract gate
editorial_critic.py      Stage 1.5: pre-render plan review
researcher.py            Stage 2: footage search, timestamps, fallback ladder
clip_knowledge.py        Cross-run clip knowledge base
vidpipe/                 Stage 3: renderer (montage and single-clip paths)
quality_gate.py          Stage 4: ffprobe checks and frame extraction
output_qa.py             Stage 5: post-render vision QA
run_record.py            Per-run JSON record and cost meter
cost_tracker.py          Cost estimation (dry run) and tracking
llm.py, gemini.py, claude.py   Provider router and clients
prompts/                 Versioned stage system prompts
tests/                   pytest suite, fixtures and the standalone validator
scripts/                 Batch drivers used for validation runs
eval/                    Validation notes and baseline scores
docs/                    Architecture, roadmap and operational notes
music/                   Optional music beds (not distributed)
```

## Design principles

- **Flag and stop rather than auto-fix.** Reviewers never edit output or trigger
  re-renders. A failed gate stops publication and names the problem. The one bounded
  exception is the pre-render revision loop, which is text-only, cheap and capped.
- **Review the plan before spending, and the product after.** A weak hook caught in the
  spec costs one text call. Caught after rendering, it wastes the whole research and
  render spend. Output-QA covers what spec review can't see, such as whether the
  footage on screen matches the claim.
- **Facts are a human responsibility.** A model rejecting a spec for "factual accuracy"
  is grading its own knowledge. Factual claims are extracted to a checklist instead.
- **Make silent failures loud.** Every fallback is logged with its reason. If a story's
  central footage could not be found, the run flags it rather than quietly filling the
  gap with a gradient.
- **Additive schema migration.** The renderer reads only the spec fields it knows about.
  That lets the spec contract change ahead of the render layer without breaking it.
- **Spend where quality is decided.** Models and thinking budgets are set per stage.
  Every call's real token usage is costed and attributed to its stage.

## Status and roadmap

Development follows a phased migration plan
([`docs/MIGRATION_PLAN.md`](docs/MIGRATION_PLAN.md)):

| Phase | Scope | Status |
|---|---|---|
| A | Safety rails: spec contract, run record, cost ceiling, bounded revision loop | Complete |
| B | Thin slice: v2 Creative Director prompt, hero-clip spine, single-clip format, split reviewers, footage fallback ladder | Complete; montage validation set still to be re-run |
| C | Measurement: eval harness with a human-anchored judge | Next |
| D | Inputs and coherence: brief contract, footage hit-rate | Planned |
| E | Composition depth and throughput: render wiring for `transition` / `audio_mode`, batch runner | Planned |

Quality priorities beyond the migration are set out in
[`docs/QUALITY_ROADMAP.md`](docs/QUALITY_ROADMAP.md).

## Limitations

- **Footage licensing.** The researcher sources clips from YouTube. Rendered videos may
  contain third-party copyrighted material and are not cleared for publication. The
  project is a technical prototype, not a publishing tool.
- **Factual accuracy is not verified automatically.** Claims are surfaced for human
  review, not checked.
- **Model-dependent quality.** Output quality and cost depend on preview-tier models
  whose behaviour and pricing may change.
- **No formal evaluation yet.** Quality has so far been judged by watching output
  against written criteria ([`eval/6b_validation.md`](eval/6b_validation.md)). A
  systematic eval harness is the next phase.

## Documentation

| Document | Purpose |
|---|---|
| [`docs/HANDOFF.md`](docs/HANDOFF.md) | Orientation for picking up the project |
| [`docs/MIGRATION_PLAN.md`](docs/MIGRATION_PLAN.md) | System model and phased roadmap |
| [`docs/QUALITY_ROADMAP.md`](docs/QUALITY_ROADMAP.md) | Definition of target quality, evidence and improvement plan |
| [`docs/COST_TUNING.md`](docs/COST_TUNING.md) | Cost levers, caches and rate limits |
| [`CLAUDE.md`](CLAUDE.md) | Working guide for agent-assisted development sessions |

## License

Released under the MIT License. See [LICENSE](LICENSE).

© Jack Street
