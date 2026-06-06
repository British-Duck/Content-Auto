# CLAUDE.md

Short-form vertical video pipeline (brief → spec → footage → render → QA).
Currently mid-migration from v1 to **News Dispatch v2**. Read `docs/MIGRATION_PLAN.md`
before doing any work — execute it **one scoped step at a time**, never ahead.

## Architecture (current v1 wiring)

Entry point: `orchestrate.py` → `run()`. Five stages:

1. **Creative Director** (`creative_director.py`) — brief → spec JSON. Two Gemini calls
   (generate → self-critique), then `_fixup` + `_force_scheme`. Caches specs by brief
   hash in `cache/briefs/`.
2. **Researcher** (`researcher.py`) — fills `clip` for each screen with a YouTube clip.
   yt-dlp search → metadata-first timestamps → Gemini video analysis fallback.
   **Must stay Gemini** (native video analysis). Heavily cached.
3. **Render** (`vidpipe/`, entry `vidpipe/cli.py`) — spec → MP4 via ffmpeg.
   `spec.py` loads the spec; `renderer.py`/`text.py` draw; `assembler.py` stitches;
   `voiceover.py` adds VO; `clips.py`/`schemes.py` support.
4. **Quality Gate** (`quality_gate.py`) — ffprobe technicals + extracts one midpoint
   frame per screen to `output/<id>_frames/`.
5. **Output-QA** (`output_qa.py`) — post-render vision QA. Feeds the spec + the ffprobe
   report + the per-segment frames to a vision-capable Gemini call and returns PASS/FAIL.
   A FAIL surfaces `blocking_issues` and sets the non-publish exit code; flag-and-stop,
   never re-renders. (Replaced the old `content_reviewer.py` in Step 5.)

**Stage 1.5** (`orchestrate._spec_review_loop`) sits between CD and Researcher: the
**Editorial Critic** (`editorial_critic.py`, prompt `editorial_critic.md`) judges the
spec's plan pre-render and, **on decision == REVISE**, passes its fixes back to the CD for
at most `SPEC_REVIEW_MAX_RETRIES` (2) regenerations. A REJECT (or a still-REVISE spec after
the cap) is flagged for a human and NOT rendered. Facts are never a reject — they flow to
`facts_to_check` (logged for a human). (Step 5 broadened the trigger from the old
factual_accuracy hard-reject, which was removed.)

### Key files
- `config.py` — all tuneables: per-stage models, dirs, render constants, thinking
  budgets, music, `LAYOUT_STYLE`. Models routed by `llm.py` (`gemini-*`→`gemini.py`,
  `claude-*`→`claude.py`, dormant; falls back to Gemini if key/SDK absent).
- `cost_tracker.py` — per-run cost meter. `gemini.py` — Gemini client + `parse_json`.
- Prompts in `prompts/`. **Stage prompts are loaded at import time** — a missing prompt
  file crashes the stage module on import (see "Current state").

## v2 spec schema (target — see `prompts/creative_director_v2.md`)

Existing fields **plus** these additive ones. The renderer must tolerate unknown fields
(`vidpipe/spec.py` reads only known keys, so new fields are inert until Phase E wires them).

Top-level: `storyboard`, `id`, `label`, `structure` (one-line shape), `scheme`
(`dark_{gold,red,blue,green,purple}`), `voice_script`, `screens[]`.
Per-screen: `text`, `reason` (why this segment+format), `duration` (1.0–6.0s),
`transition` (`cut|crossfade|zoom`), `text_size` (76–100), `panel`, `subtitle|null`,
`clip_mode` (`null|ambient`), `clip_description|null`, `clip_moment|null`,
`audio_mode` (`vo|native`).
**`voice` is removed** — a single fixed narrator lives in config.
Constraints: 4–12 screens (typ. 5–9), total runtime 30–50s.

New prompts staged in `prompts/`: `creative_director_v2.md` (Stage 1),
`editorial_critic.md` (pre-render reviewer), `output_qa.md` (post-render QA).

## Working principles (follow these)

- **One scoped migration step at a time.** Don't move on until its acceptance check passes.
- **Thin slice first:** rails → new prompts → real output you watch → then eval/throughput.
  Don't build the factory before validating the product.
- **Flag-and-stop over auto-fix.** Surface problems; never silently paper over them.
- **Make silent failures loud** — always log *why* anything falls back.
- **New spec fields are additive;** the renderer must tolerate unknown fields.
- Show the diff and the acceptance-check result for each step before continuing.
- Work on the migration branch; no destructive commands.

## Run commands

```
python orchestrate.py "Brief text. Use dark_red, scandal angle."   # new brief
python orchestrate.py specs/<id>.json --skip-research              # re-render a spec
python orchestrate.py "Brief..." --dry-run                        # cost preview, no API
python orchestrate.py "Brief..." --force-regen                    # ignore brief cache
```

`.env` holds `GEMINI_API_KEY` (no `ANTHROPIC_API_KEY` yet → Claude models fall back to
Gemini). Further detail: `PROJECT_HANDOFF.md`/`docs/HANDOFF.md`, `docs/pipeline_map.txt`.

## Current state (2026-06-06)

**Phase B is complete (Steps 4 + 5).** The pipeline runs end to end. Stage 1 uses
`creative_director_v2.md`; Stage 1.5 is the Editorial Critic (`editorial_critic.py`,
trigger = REVISE, 2-pass cap, facts → `facts_to_check`); the post-render stage is
Output-QA (`output_qa.py`, vision, FAIL → non-publish + `blocking_issues`, never
re-renders). The old `content_reviewer.py` and its removed `.txt` prompt are gone.
The run record's `editorial_critic` / `output_qa` blocks are populated and Output-QA
cost is attributed to an `output_qa` stage.

Offline-faithful tests: `tests/test_spec_validator.py`, `test_run_record.py`,
`test_regen_cap.py`, `test_step5_reviewers.py` — 20 passing.

**Next: Phase C, Step 6** (eval harness + human-anchored judge) — but first the plan
calls for making 5–10 real videos and watching them. Known watch-item: the v2 CD can
still write a `voice_script` that overruns a short runtime (the Bolt sample: 80 words /
~24s over a 16.7s spec); the Editorial Critic's script-fit check now flags this as a
REVISE, but a CD-prompt nudge toward longer specs may also help.
