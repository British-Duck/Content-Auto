# HANDOFF — Content Automation pipeline

Last updated: 2026-07-21. Read this first; then `README.md` for the project overview and
`docs/MIGRATION_PLAN.md` for the system model and roadmap. `CLAUDE.md` at the repo root
is the working guide for agent-driven sessions.

The system turns a plain-English brief into a postable 1080×1920 short video through
five stages plus two gates:

```
brief → 1. Creative Director  (creative_director.py, prompts/creative_director_v2.md)
      → gate: spec contract   (spec_validator.py — hard-reject / auto-repair / log)
      → 1.5 Editorial Critic  (editorial_critic.py — pre-render plan review, PUBLISH/REVISE/REJECT;
                               REVISE loops back to the CD, capped at 2 regenerations)
      → 2. Researcher         (researcher.py — yt-dlp search, timestamps, fallback ladder,
                               single_clip hero-window resolution; must stay Gemini)
      → 3. Render             (vidpipe/ — montage and single_clip paths, edge-tts narration,
                               ducked music bed)
      → 4. Quality Gate       (quality_gate.py — ffprobe technicals + per-segment frames)
      → 5. Output-QA          (output_qa.py — vision review of frames vs plan; FAIL = non-publish
                               exit code, flag-and-stop, never re-renders)
```

Cross-cutting: `run_record.py` (one JSON record per run: verdicts, fallback ladder,
per-stage cost from real token usage, cost-ceiling abort) and `cost_tracker.py`.

## Per-stage model selection

Models are chosen per stage in `config.py` and routed by `llm.py` (prefix `gemini-*` →
`gemini.py`, `claude-*` → `claude.py`). The researcher must stay Gemini — native video
analysis.

```
CREATIVE_MODEL         = "gemini-3-flash-preview"  # creative director
EDITORIAL_CRITIC_MODEL = "gemini-3-flash-preview"  # Stage 1.5 pre-render critic
OUTPUT_QA_MODEL        = "gemini-3-flash-preview"  # post-render vision QA
GEMINI_MODEL           = "gemini-2.5-flash"        # researcher + default
```

To try Claude on a stage: set its model to a `claude-*` id, add `ANTHROPIC_API_KEY` to
`.env`, `pip install anthropic`. `claude.py` is built and dormant (prompt caching +
adaptive thinking wired); if the key/SDK is missing, `llm.py` falls back to Gemini and
logs why, so a half-configured switch never breaks a run.

## Where things stand

Phases A and B of the v2 migration are complete: safety rails (spec contract, run
record + cost ceiling, bounded revision loop), the v2 Creative Director prompt with
hero-clip composition and the `single_clip` format, the split reviewer (editorial critic
pre-render, vision Output-QA post-render), and the researcher's footage fallback ladder.
See `docs/MIGRATION_PLAN.md` Part 2 for the step-by-step record and Part 3 for what's
next (eval harness, brief contract, footage hit-rate, renderer wiring of
`transition`/`audio_mode`).

Immediate open items:
1. Re-run the Phase B watch set (`scripts/batch_phase_b.py`) and score it against
   `eval/6b_validation.md`.
2. Fill the CD prompt's empty EXEMPLARS slot with 3–4 hand-authored specs.
3. Phase C Step 7: the eval harness.

## Known issues

- **Source footage quality** — news/space topics pull YouTube clips with burned-in
  chyrons and watermarks. The pipeline can't strip these; mitigate via better
  `clip_description`s, not code.
- **yt-dlp transient throttling** — rapid repeated re-renders can get throttled;
  downloads recover on their own. Not a code bug.
- **Recent events** — reviewers' knowledge has a training cutoff; factual accuracy of
  briefs is the human's job by design (claims flow to `facts_to_check`, never a machine
  reject).

## Common commands

```
python orchestrate.py "Brief text. Use dark_red, scandal angle."   # new brief
python orchestrate.py specs/<id>.json --skip-research              # re-render existing spec
python orchestrate.py "Brief..." --dry-run                        # cost preview (no API calls)
python orchestrate.py "Brief..." --force-regen                    # ignore brief cache
python -m pytest -m "not slow and not network"                    # offline suite, no API key
```

One-off batch drivers (historical, kept runnable) live in `scripts/`.
