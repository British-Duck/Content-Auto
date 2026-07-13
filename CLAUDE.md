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

## Current state (2026-07-14)

**Phase B complete (Steps 4 + 5 + 6a/6b) plus trust fixes.** The pipeline runs end
to end. Stage 1 uses `creative_director_v2.md` (hero-clip spine rules + `single_clip`
format, Rule 2.5); Stage 1.5 is the Editorial Critic (trigger = REVISE, 2-pass cap,
facts → `facts_to_check`, fed deterministic COMPUTED METRICS — script-fit numbers,
spine counts, near-duplicate clip_descriptions — via `editorial_critic.compute_metrics`);
the researcher has a footage fallback ladder (alt queries → Ken Burns still → gradient,
audited per beat in the run record's `footage_ladder`) and resolves `single_clip`
hero windows; the post-render stage is Output-QA (`output_qa.py`, vision, format-aware:
single_clip verdicts judge continuity/on-story, not per-segment clip matches).

Recent trust fixes (2026-07-14): format-aware duration ceiling (single_clip beats to
15s); single_clip truncation guard (`fit_start_to_source`, refuses uncoverable
sources); `vidpipe/clips.py` shares the researcher's cache dir + YouTube-ID key and
deletes/validates partial downloads.

Offline-faithful tests (the migration gate — legacy `test_stress.py` /
`test_output_quality.py` render suites have pre-existing failures, not the gate):
`test_spec_validator.py`, `test_run_record.py`, `test_regen_cap.py`,
`test_step5_reviewers.py`, `test_fallback_ladder.py`, `test_hero_spine.py`,
`test_single_clip.py`, `test_editorial_metrics.py` — 66 passing.

**Next:** (1) lift the Gemini spend cap (https://ai.studio/spend) and re-run
`batch_phase_b.py` — the montage-set validation from `eval/6b_validation.md` is still
open; re-watch the single-clip set with the now-honest QA. (2) Fill the CD prompt's
empty EXEMPLARS slot (3–4 hand-authored specs). (3) Phase C Step 7: eval harness.
