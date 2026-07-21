# Content-Auto

A pipeline that turns a one-line editorial brief into a finished vertical short
video — script, sourced footage, render, narration — with two layers of machine
review and a hard cost ceiling, for roughly $0.10–0.20 of API spend per video.

## Overview

Given a brief like *"Usain Bolt's 9.58 world record. Use dark_blue, untouchable-record
angle."*, the pipeline plans a 30–50 second video as a JSON spec (storyboard, per-screen
text beats, voice script), finds and trims real YouTube footage for each beat, and renders
1080×1920 output with ffmpeg: text overlays, a colour scheme, TTS narration, and a music
bed ducked under the voice. It then checks its own work twice — an editorial critic judges
the *plan* before the expensive stages, and a vision model judges the finished *frames*
after render. Anything below the bar is flagged for a human and not published.

The interesting problems here are less "call an LLM" than systems engineering: keeping a
six-stage, multi-model pipeline observable (a structured run record per video), bounded
(capped revision loops, a per-run cost ceiling that aborts), and honest (every fallback is
logged with its reason; failures stop the pipeline rather than being papered over).

## Pipeline

| Stage | Module | What it does |
|---|---|---|
| 1. Creative Director | `creative_director.py` | Brief → v2 spec JSON (Gemini 2.5 Pro). Specs cached by brief hash. |
| 1 gate. Spec validation | `spec_validator.py` | Enforces the spec contract before any paid stage: hard-rejects out-of-contract specs, auto-repairs the safe ones, logs every violation. |
| 1.5. Editorial Critic | `editorial_critic.py` | Reviews the plan pre-render: PUBLISH / REVISE / REJECT. A REVISE feeds fixes back to the Creative Director, capped at two regenerations; after the cap the spec is flagged for a human, never looped or shipped. Factual claims are routed to a `facts_to_check` list for human verification — never grounds for machine rejection. |
| 2. Researcher | `researcher.py` | Finds footage per beat: yt-dlp search → free metadata timestamps → Gemini native video analysis only when needed. A fallback ladder (clip → alternate queries → Ken Burns still → gradient) records which tier each beat landed on. A cross-run clip knowledge base matches previously analysed footage semantically before any new download. |
| 3. Render | `vidpipe/` | Spec → MP4 via ffmpeg + PIL. Two paths: **montage** (one segment per screen, crossfaded) and **single_clip** (one continuous hero window with a focus-aware vertical crop and text beats overlaid as a timeline). edge-tts narration, sidechain-ducked music. |
| 4. Quality Gate | `quality_gate.py` | ffprobe technicals (resolution, duration, codec, black-frame detection) + one extracted frame per segment. |
| 5. Output-QA | `output_qa.py` | Vision review of the extracted frames against the plan: footage vs gradient, footage matching the claim, text legibility. A FAIL sets a non-publish exit code and surfaces `blocking_issues` — it never re-renders or auto-fixes. |

Cross-cutting: `run_record.py` writes one JSON record per run (verdicts, fallback reasons,
per-stage cost from real token usage) and aborts the run if cumulative cost crosses the
ceiling in `config.py`. `llm.py` routes each stage to its configured model (`gemini-*` or
`claude-*`, with graceful fallback), so the "brain" of any stage is a one-line config change.

## Tech stack

Python 3.11+ (developed on 3.13), ffmpeg/ffprobe, and:

- **google-genai** — Gemini 2.5 Pro (creative/review stages) and Flash (research); the
  researcher's native video analysis is the reason the pipeline is Gemini-first
- **yt-dlp** — YouTube search, metadata, and downloads
- **edge-tts** — narration (fixed narrator voice, `en-GB-RyanNeural`)
- **Pillow / numpy** — text overlay and gradient rendering
- **anthropic** (optional) — dormant Claude client behind the provider router
- **pytest** — test suite

## Getting started

Prerequisites: Python 3.11+, ffmpeg and ffprobe on PATH, a Gemini API key.

```
pip install -r requirements.txt
cp .env.example .env          # then set GEMINI_API_KEY
```

## Usage

```
python orchestrate.py "Brief text. Use dark_red, scandal angle."   # brief → video
python orchestrate.py specs/<id>.json --skip-research              # re-render a spec
python orchestrate.py "Brief..." --dry-run                         # cost preview, no API calls
python orchestrate.py "Brief..." --preview                         # fast half-res render
```

A dry run projects the stage-by-stage cost before anything is spent:

```
STAGE BREAKDOWN
  Creative Director:  1 text call(s)
  Researcher:         typical 2-6 video calls
  Editorial Critic:   1 text call (pre-render; +1 per regeneration)
  Render:             local only (ffmpeg)
  Quality Gate:       local only (ffprobe)
  Output-QA:          1 vision call (post-render, frames)

ESTIMATED COST
  Total:              $0.0247 - $0.0390
```

A completed run leaves the video in `output/`, a human-readable timeline in `specs/`, and
the full run record (verdicts, fallbacks, per-stage cost) in `logs/`. The exit code is
non-zero unless both the technical gate and Output-QA passed.

## Testing

```
python -m pytest -m "not slow and not network"   # offline suite, no API key needed
```

The suite (83 tests) is written to be *offline-faithful*: the real orchestrator lifecycle
runs with network, Gemini, and ffmpeg I/O stubbed, so the tests exercise genuine control
flow — the spec-contract gate, the bounded revision loop, the fallback ladder, run-record
and cost-meter wiring, and the single-clip render math — rather than mocks of it. Tests
marked `slow` build real videos and `network` downloads real clips; both are opt-in.
`tests/run_tests.py` is a standalone post-build validator (ffprobe checks, SSIM
regression against stored baselines) that can be pointed at any rendered MP4.

## Project structure

```
orchestrate.py         entry point — wires the stages, cost ceiling, run record
config.py              every tuneable: per-stage models, price table, render constants
creative_director.py   Stage 1 — brief → spec JSON
spec_validator.py      Stage 1 gate — spec contract (hard-reject / auto-repair)
editorial_critic.py    Stage 1.5 — pre-render plan review
researcher.py          Stage 2 — footage search, timestamps, fallback ladder
clip_knowledge.py      cross-run clip library with semantic matching
vidpipe/               Stage 3 — renderer (montage + single-clip paths)
quality_gate.py        Stage 4 — ffprobe technicals + frame extraction
output_qa.py           Stage 5 — post-render vision QA
run_record.py          per-run JSON record + real-token cost meter
llm.py / gemini.py / claude.py   provider router + clients
prompts/               versioned stage system prompts
tests/                 pytest suite + standalone quality validator
scripts/               one-off batch drivers (historical, kept runnable)
docs/                  handoff notes + system model / migration plan
```

## Design notes

- **Flag-and-stop over auto-fix.** Reviewers never edit or trigger re-renders. A failed
  gate stops the publish and names the problem; silent self-repair hides defects and
  compounds cost. The one bounded exception is the pre-render revision loop, which is
  cheap (text-only) and capped at two regenerations.
- **Review the plan before spending, the product after.** The editorial critic runs on
  the spec JSON — catching a weak hook there costs one text call; catching it post-render
  wastes the whole research + render spend. Output-QA then checks the things no spec
  review can see: whether the footage actually matches the claim on screen.
- **Facts are the human's job.** An LLM critic rejecting on "factual accuracy" is a
  model grading its own knowledge. Factual claims are extracted to a checklist for a
  human instead; they never block the pipeline.
- **Spend where quality is decided.** Model choice is per-stage: Pro where judgement
  matters (director, critics), Flash with capped thinking budgets where it's mechanical
  (timestamp extraction). Every call's real token usage is priced and attributed to its
  stage, and the run aborts at a configured cost ceiling.
- **Make silent failures loud.** Footage research degrades through an explicit ladder,
  and the tier each beat landed on is recorded. A story spine that found no footage is a
  flagged event, not a quietly gradient-filled video.
- **Additive schema migration.** New spec fields are inert until wired: the renderer
  reads only known keys, so the spec contract can move ahead of the render layer without
  breaking it.
- **Two render paths rather than one clever one.** Single-continuous-moment stories
  (a race, a crash) get a dedicated single-clip path instead of forcing the montage
  renderer to fake continuity — the montage path stays untouched.

## Status & roadmap

The pipeline runs end to end and is mid-migration to its v2 architecture
(`docs/MIGRATION_PLAN.md`): safety rails and the split reviewer stages are done; current
work is footage-execution quality (hero-clip spine, single-clip format) and an eval
harness with a human-anchored judge, followed by wiring the spec's `transition` and
`audio_mode` fields into the renderer and a batch runner.

## License & author

MIT — see [LICENSE](LICENSE). Written by Jack Street.
