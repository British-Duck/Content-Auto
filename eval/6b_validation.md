# Step 6b Validation — Watch & Score Sheet

Premise of 6b: *execution* (footage) is the binding constraint, and the hero-clip
spine + Ken Burns stills + single_clip format should lift quality from the first
batch's **~2/5** toward **4/5**. Only the eye confirms it, which is what the **Eyeball** column records.

- **QA** = Output-QA vision verdict (PASS = execution matched the plan; FAIL = surfaced blocking_issues). A PASS is *coherence, not quality* — it does not mean 4/5.
- **Eyeball (1–5)** = human score. 5 = ship, 4 = target bar, 2 = first-batch level. Not yet recorded for this run.
- **Baseline** = prior score from `eval/baseline_scores.json` (montage only; per-video scores were not recorded, so the aggregate ~2/5 applies).
- **REGRESSION** flag = a montage video that looks worse than its baseline.

Runs: regenerated **2026-06-12** on current 6b code (source post-dated the 06-08/09 outputs).

---

## Single-clip set (`output/single-clip-batch-2026-06-08/`)

3 single-take event briefs (race / record feat / filmed disaster). single_clip is
the format being validated here; watch for format-routing (did the CD pick
single_clip?), hero-clip framing/crop, key-moment landing, and VO/footage sync.

| # | Brief | Format chosen | Runtime (planned→rendered) | QA | Eyeball (1–5) | Notes |
|---|-------|---------------|----------------------------|----|---------------|-------|
| 1 | Usain Bolt 9.58 (race) | single_clip ✓ | 35.5 → 35.5s ✓ | FAIL\* |  | QA FAIL is a false positive (see below). Real watch-item: QA says opening beats (blocks/gun) show race already underway — possible hero-window start-alignment issue. mp4: `usain-bolt-958-world-record.mp4` |
| 2 | Felix Baumgartner (record feat) | single_clip ✓ | 38.0 → 38.0s ✓ | FAIL\* |  | QA FAIL is a false positive (per-segment footage-vs-claim, N/A to single_clip). mp4: `felix-baumgartner-stratos-jump.mp4` |
| 3 | Hindenburg (filmed disaster) | single_clip ✓ | 31.5 → **25.1s** ✗ | FAIL |  | **REAL bug**: rendered 6.4s short (hero window 3–43s but source handed to renderer ran out) → VO truncated. Plus QA false-positive on top. mp4: `hindenburg-disaster-humanity.mp4` |

\* **single_clip QA FAILs are not trustworthy.** Output-QA judges each segment's frame
against that segment's `clip_description`/`clip_moment` — the *montage* contract. single_clip
has no per-segment footage (it plays one continuous window with text slid over it), so the
per-segment "footage doesn't match the claim" blocking issues are false by construction. The
QA gate needs a single_clip-aware mode before its verdict on these means anything (the 6b
analogue of the 6a frame-drift honesty fix). **Format routing improved: all 3 now correctly
chose single_clip** (Hindenburg/Baumgartner previously mis-routed to montage on some regens).

## Montage set (`output/phase-b-batch-2026-06-08-ladder/`)

5 scatter-footage briefs — the ones that scored ~2/5. These test whether the
researcher rewrite (hero-spine + alt-query ladder + Ken Burns stills) lifts the
weak path. **Regression check: flag any that look worse than baseline.**

**⚠ BLOCKED — current-code re-run did not complete.** The Gemini project hit its
**monthly spending cap** (`429 RESOURCE_EXHAUSTED`) at 01:18 on 2026-06-12, during montage
video 1's research. Single-clip batch had already finished; montage videos 2–5 never ran.
**Re-run pending: lift the cap at https://ai.studio/spend, then re-run `scripts/batch_phase_b.py`.**

Interim fallback if you want to eyeball something now: the **06-08 montage outputs** in this
folder are complete but on *slightly older code* (pre the 06-08/09 `researcher.py` alt-query
ladder + `config.py` changes), so they under-represent the current researcher. Their run-time
verdicts were: FTX PASS, Apollo PASS, black-hole/VW/Wow FAIL; all 5 were ≥34s with 0–2 gradients.

| # | Brief | Scheme | Runtime | Gradients | QA | Eyeball (1–5) | Baseline | Regression? | Notes |
|---|-------|--------|---------|-----------|----|---------------|----------|-------------|-------|
| 1 | Black hole M87 (reveal) | dark_blue | BLOCKED | — | — |  | _(2 agg)_ |  | re-run after cap lifted |
| 2 | VW dieselgate (scandal) | dark_red | BLOCKED | — | — |  | _(2 agg)_ |  | re-run after cap lifted |
| 3 | FTX collapse (collapse) | dark_green | BLOCKED | — | — |  | _(2 agg)_ |  | re-run after cap lifted |
| 4 | Wow! signal (mystery) | dark_purple | BLOCKED | — | — |  | _(2 agg)_ |  | re-run after cap lifted |
| 5 | Apollo 11 fuel (achievement) | dark_gold | BLOCKED | — | — |  | _(2 agg)_ |  | re-run after cap lifted |

**Stale-code 06-08 montage stats (interim reference only — NOT the current-code validation):**

| Brief | Scheme | Runtime | Clips | Gradients | QA (06-08) |
|-------|--------|---------|-------|-----------|------------|
| Black hole M87 | dark_blue | 34.0s | 5 | 1 | FAIL |
| VW dieselgate | dark_red | 38.0s | 6 | 1 | FAIL |
| FTX collapse | dark_green | 34.5s | 5 | 2 | PASS |
| Wow! signal | dark_purple | 34.5s | 6 | 1 | FAIL |
| Apollo 11 fuel | dark_gold | 38.5s | 7 | 0 | PASS |

Structural read (even on stale code): all 5 now ≥34s (vs the first batch's 3/5 *under* 30s)
and gradients are down to 0–2/video — consistent with the hero-spine prompt working at the
structural level. Whether that translates to ~4/5 is your eyeball call, and needs the
current-code re-run to be a fair test.

---

## Loud flags surfaced by the run

1. **🛑 BLOCKER — Gemini monthly spend cap exhausted** (`429 RESOURCE_EXHAUSTED`, 2026-06-12 01:18).
   Montage re-run aborted at video 1. Lift at https://ai.studio/spend before continuing.

2. **⚠ Output-QA is not single_clip-aware (gate dishonest for single_clip).** All 3 single_clip
   videos FAILed with per-segment "footage doesn't match the claim" issues — but single_clip has
   no per-segment footage; it plays one continuous window with text slid over it. The per-screen
   `clip_description`/`clip_moment` are montage-contract fields the renderer *ignores*. QA verdicts
   on single_clip are false-by-construction until QA gets a single_clip mode. (6b analogue of the
   6a frame-drift honesty fix.) **Recommend folding a fix into Stage 2 or a 6c before trusting any
   single_clip QA.**

3. **🐞 Real single_clip truncation — Hindenburg rendered 25.1s vs 31.5s planned (−6.4s).** The hero
   source handed to the renderer ran out before the runtime (hero window 3–43s; `source_duration`
   recorded as 327s but the actual fetched/cached clip was shorter), so video AND VO were cut. This
   is exactly the Stage-3 safety-net case, now empirically confirmed. Bolt (35.5s) and Baumgartner
   (38s) rendered to length — the bug bites only when the resolved/downloaded source is shorter than
   `aligned_start + runtime`, which the renderer doesn't currently detect or correct.

4. **⚠ Per-screen 1–6s duration clamp fights the single_clip format.** The CD wrote single_clip beats
   of 7–13s (long, continuous-take pacing); `spec_validator` clamps each to ≤6s, which on the first
   Bolt draft pulled runtime to 24.5s (under the 30s floor). The editorial loop later revised runtimes
   back above 30, but the clamp is structurally wrong for single_clip, where long beats are the point.
   Consider a format-aware duration ceiling.

5. **✅ Format routing now consistent.** All 3 single-take briefs chose single_clip (earlier runs
   mis-routed Hindenburg/Baumgartner to montage). The CD's single-take rule is holding on current code.

6. **✅ Editorial script-fit working.** Bolt's first draft (112-word VO over 24.5s) was caught as a
   REVISE ("~34s of script over 24.5s runtime — critical failure") and revised up. Step 5's check holds.

## Bottom line for Stage 1
- single_clip path: format routing + script-fit good; **two real defects** (QA not single_clip-aware;
  source-shorter-than-runtime truncation) and one clamp-vs-format mismatch. None are 4/5 blockers on
  their own, but #2 means we currently *cannot trust the QA gate's read* on single_clip quality.
- montage path: **current-code validation incomplete (spend cap)**; stale-code structural signal is
  positive (length + gradient discipline up) but the eyeball + fair re-run are pending.
- Eyeball scores (your column) + the montage re-run are the two things needed to close Stage 1.
