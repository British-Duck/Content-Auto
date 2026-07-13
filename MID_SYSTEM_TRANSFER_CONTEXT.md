# MID-SYSTEM TRANSFER CONTEXT

Handoff for continuing the **News Dispatch v2 migration** in a fresh chat.
Written 2026-06-06. Read this first, then `CLAUDE.md`, then `docs/MIGRATION_PLAN.md`.

---

## 1. What this project is

A short-form vertical video pipeline: **brief → spec → footage → render → QA**, being
migrated from v1 to a "News Dispatch v2" design. Entry point `orchestrate.py::run()`.
Five stages: Creative Director → Researcher → Render (`vidpipe/`) → Quality Gate →
Content Reviewer, plus a **Stage 1.5** spec-review/regeneration loop between CD and research.

Full architecture + key files + v2 spec schema are in **`CLAUDE.md`** (kept current).
The phased plan being executed is **`docs/MIGRATION_PLAN.md`** (Phases A–E).

---

## 2. Git / repo state

- **Remote:** `origin` → https://github.com/British-Duck/Content-Auto.git
- **Branch for all migration work:** `migration/news-dispatch-v2`
- **Commits:**
  - `d77f9b3` Initial commit (their README)
  - `3ee2856` Import baseline (pre-migration snapshot) — **pushed to `origin/main`**
  - `703d14d` **Phase A: safety rails (Steps 1–3)** — committed **locally on the branch, NOT pushed**
- **`main`** is pushed and = baseline. The **migration branch is local only** (not pushed yet).
- **`.env`** holds `GEMINI_API_KEY` only (no `ANTHROPIC_API_KEY`). `.env` is gitignored.
- **`.gitignore`** excludes `.env`, `cache/`, `output/`, `.clip_cache/`, `.trim_cache/`,
  `.claude/`, `logs/*.json`. (Generated specs under `specs/` ARE tracked, by repo convention.)

### Uncommitted right now (Step 4 — built, verified, awaiting your go to commit)
Modified: `creative_director.py`, `config.py`, `vidpipe/cli.py`, `orchestrate.py`
Untracked sample artifacts from the live run (left out of code changes; your call whether to commit):
`specs/usain-bolt-958-record.json`, `..._storyboard.md`, `..._timeline.txt`

---

## 3. Migration progress

### ✅ Phase A — safety rails (committed: 703d14d)
- **Step 1 — Spec validation gate** (`spec_validator.py`): enforces the v2 contract right
  after the CD, before research/render. HARD-REJECT (raise) on bad enum / missing-required /
  screen count ∉ 4–12 / empty-where-null / two consecutive gradients. AUTO-REPAIR (log) for
  duration & text_size clamp, `panel` to match `clip_description`, empty-string subtitle/
  clip_description/clip_moment → null, first transition → cut, stray `voice` stripped. Wired
  in `orchestrate.run()` for CD-generated specs only (a provided `.json` is taken as final).
- **Step 2 — Run record + cost meter** (`run_record.py`): one forward-compatible record per
  video → `logs/<run_id>.json`, written on completion AND abort (via `try/finally` in `run()`).
  `CostMeter` captures real Gemini token usage (emitted by `gemini.py::_emit_usage` and the
  researcher's direct video call), attributes per stage, prices via `config.GEMINI_PRICING`.
  Per-run **cost ceiling** `config.COST_CEILING_USD` (0.75) aborts after a paid stage. Loud
  fallbacks: researcher records every gradient/no-footage event with the screen index. The
  `editorial_critic`/`output_qa` blocks and `regenerations` are **present-but-null** (Steps 5/3).
- **Step 3 — Bounded Stage 1.5 loop** (`orchestrate.py`): cap = 2 regenerations
  (`config.SPEC_REVIEW_MAX_RETRIES = 2`), 3 CD calls total. Trigger UNCHANGED (factual only).
  After the cap a still-failing spec is flagged for a human and **NOT rendered**
  (`final_status="flagged_regen_cap"`); composes with the cost ceiling via the shared abort path.
- **Tests:** `tests/test_spec_validator.py`, `test_run_record.py`, `test_regen_cap.py` —
  **16 passing**, offline-faithful (CD/Gemini mocked, real `run()` lifecycle exercised).

### ✅ Step 4 — Creative Director v2 live (uncommitted; verified end-to-end)
- `creative_director.py` now loads **`prompts/creative_director_v2.md`**; **single-pass**
  generation (the v1 self-critique call is retired — it encoded the old schema and would
  corrupt a v2 spec); **keeps `storyboard`** on the spec (no longer popped); all `voice`
  handling removed; screen-count check 5→4.
- **Fixed narrator** `config.NARRATOR_VOICE = en-GB-RyanNeural`; `vidpipe/cli.py` reads it
  instead of `spec.voice`.
- `orchestrate.py`: None-safe `quality_ok`/`review_ok` (a failed stage leaves `None`),
  dry-run CD estimate 2→1.
- **Renderer tolerates the new additive fields** (`structure`, `reason`, `transition`,
  `audio_mode`, `clip_moment`) — `vidpipe/spec.py::load_spec` reads only the keys it needs and
  ignores the rest. `transition`/`audio_mode` are **inert** until Phase E.
- **First live run succeeded:** brief → v2 spec (gate passed, storyboard intact) → research
  (3/3 clips) → render → quality gate **7/7 PASS**. Output:
  `output/usain-bolt-958-record.mp4` (~15s). Live CD cost ≈ **$0.032** (Pro), total ≈ $0.057.

### ⏭ NEXT — Phase B, Step 5: split the reviewer (NOT started)
From `docs/MIGRATION_PLAN.md`:
- Wire **`prompts/editorial_critic.md`** into the pre-render stage; **broaden the Stage 1.5
  loop** to act on an editorial `REVISE` (not only `factual_accuracy < 8`), within the 2-pass
  cap from Step 3. Populate the `editorial_critic` record block.
- Wire **`prompts/output_qa.md`** into the Quality Gate: feed it the spec + ffprobe report +
  the per-segment extracted frames (`output/<id>_frames/`, already produced by `quality_gate.py`);
  a FAIL sets the non-publish exit code and surfaces `blocking_issues`. Populate the `output_qa` block.
- **Remove the old factual hard-reject.** Then make 5–10 real videos and watch them.

---

## 4. Known issues / things to watch

1. **`prompts/content_reviewer.txt` and `creative_director.txt` are GONE** (only the 3 v2
   `.md` prompts remain). `content_reviewer.py` still loads `content_reviewer.txt` at import,
   so **Stage 1.5 and Stage 5 currently fail gracefully** (caught + logged), which is why a
   live run ends `final_status="needs_human_review"` even though the video rendered and the
   quality gate passed. **Step 5 resolves this** by replacing those stages with the v2 reviewers.
   Do NOT "fix" content_reviewer.py in isolation — replace it as part of Step 5.
2. **Short-video / VO-truncation (content quality, not a wiring bug):** the live run's CD made
   a **16.7s** spec (under the v2 30–50s target). The validator **warns but doesn't block**
   (runtime is warn-only by design). The `voice_script` was ~95 words (~30s of speech), so
   ffmpeg's `-shortest` **truncates the narration to ~15s** — audible cut-off. Watch for this;
   it's the kind of thing the Step-5 editorial critic should catch, or a small CD-prompt nudge.
3. **`CREATIVE_MODEL = gemini-2.5-pro`** (best available — no `ANTHROPIC_API_KEY`, so Claude
   models would fall back to Gemini via `llm.py`). CD temperature = `GEMINI_TEMPERATURE = 1.0`.
4. A stale first-run record `logs/usain-bolt-958-record_20260606_141922.json` exists with
   `final_status=in_progress` (from the pre-fix crash). Gitignored. The clean record is the
   `_142322.json` one.

---

## 5. v2 spec schema (target — see `prompts/creative_director_v2.md`)

Top-level: `storyboard` (non-empty), `id` (kebab-case), `label` (UPPER, 2–4 words),
`structure` (one line), `scheme` (`dark_{red,gold,blue,green,purple}`), `voice_script`,
`screens[]` (4–12, typ. 5–9). **`voice` is removed** (narrator fixed in config).
Per screen: `text`, `reason`, `duration` (1.0–6.0), `transition` (`cut|crossfade|zoom`,
first=cut), `text_size` (76–100), `panel` (true iff `clip_description` set), `subtitle|null`,
`clip_mode` (`null|ambient`), `clip_description|null`, `clip_moment|null`,
`audio_mode` (`vo|native`). Cross-field: no two consecutive gradients; total runtime 30–50s
(warn only). The Step-1 `spec_validator.py` is the authority on this contract.

---

## 6. How to run / verify

```bash
# Tests (offline, fast):
python -m pytest tests/test_spec_validator.py tests/test_run_record.py tests/test_regen_cap.py -q   # 16 passing

# Live run from a brief (real Gemini + yt-dlp + ffmpeg):
python orchestrate.py "<brief text>. Use dark_gold, <angle> angle."
python orchestrate.py specs/<id>.json --skip-research   # re-render an existing spec
python orchestrate.py "<brief>" --dry-run               # cost preview, no API calls
python orchestrate.py "<brief>" --force-regen           # ignore brief cache
```
- Run records land in `logs/<run_id>.json`; rendered videos in `output/<id>.mp4`; per-segment
  frames in `output/<id>_frames/`. Specs + storyboards + timelines in `specs/`.
- **Environment:** Windows, PowerShell. Use the PowerShell tool (not bash) for PS syntax.
  Console is cp1252 — avoid `→`/`✗`/`✓` in code that prints/logs (use ASCII); em/en-dashes are OK.
- Integration tests redirect `SPECS_DIR`/`OUTPUT_DIR` to a temp dir so they don't pollute the tree.

---

## 7. Working principles & boundaries (carry these forward)

- One scoped migration step at a time; don't proceed until its acceptance check passes.
- Thin slice first: rails → new prompts → real output you watch → then eval/throughput.
- **Flag-and-stop over auto-fix.** Make silent failures loud (always log why anything falls back).
- New spec fields are additive; the renderer must tolerate unknown fields.
- Show the diff + acceptance-check result for each step before continuing.
- Work on `migration/news-dispatch-v2`; no destructive commands.
- The user reviews each step and says when to commit/proceed. Commits end with
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## 8. Immediate open decisions for the user

1. **Commit Step 4?** (4 code files: `creative_director.py`, `config.py`, `vidpipe/cli.py`,
   `orchestrate.py`.) And include the sample `specs/usain-bolt-958-record.*` or leave untracked?
2. **Push `migration/news-dispatch-v2` to origin?** (Currently local only.)
3. Watch `output/usain-bolt-958-record.mp4` and decide whether the short-length / VO-truncation
   needs a CD-prompt nudge now or is left for the Step-5 editorial critic to catch.
4. Then: **proceed to Step 5** (split the reviewer).
