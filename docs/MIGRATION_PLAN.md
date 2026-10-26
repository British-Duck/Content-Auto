# News Dispatch v2 — System Model & Migration Plan

The single migration reference: what the system is, which steps are done, and what
remains. A phased, Claude-Code-driven migration — each step is one scoped session with a
clear acceptance check. Do not move to the next step until the current one passes.

---

## Part 1 — The system at its most basic level

### What it is
A translation machine: it converts editorial intent into produced media. A human decides
what is true and what the angle is (the brief); the machine turns that into a watchable
video. One artifact carries everything between stages: the spec — the contract and the
single source of truth.

### The three layers
1. **TRUTH** — the brief. What's real, what's the angle. Human-owned; the brief owns
   truth because there is no live grounding.
2. **COMPOSITION** — the Creative Director → the spec. Angle, segments, rhythm, the three
   channels (text / voice / footage), timing. Where quality is decided; cheapest to upgrade.
3. **EXECUTION** — research + render. Turns the spec into footage, voice, pixels.
   Mechanical. *Validation showed this layer is the binding constraint on quality: the
   writing is good, the footage is where it falls apart.*

### The contract, gates, spine, loop
- **CONTRACT gate**: schema validation right after the CD; rejects/repairs, logs.
- **EDITORIAL gate** (pre-render critic): is the plan good? Hook, arc, rhythm,
  script-fit. REVISE → back to the CD, bounded.
- **OUTPUT gate** (post-render QA, vision): did execution match the plan? Real footage vs
  gradient, footage matches the claim, legibility. Flag-and-stop.
- **OBSERVABILITY spine**: run record + cost meter; every fallback logged loudly.
- **CONTROL loop**: bounded regeneration, two passes then flag-and-stop.

Principle: review each failure at the layer where it is visible.

### The master clock (deepest structural choice)
Today the visual timeline is master and the voice is forced to fit it (the truncation
failure mode, now caught pre-render by the script-fit check). Target is voice-as-master:
the narration's measured length defines the video; segments are timed to the read. Makes
truncation impossible by construction and enables sync. That is Step 12.

### Principles this migration serves
- Slop is the centroid; pull off it with brief, exemplars, constraints.
- Spend where quality is decided (CD); save where it's executed (researcher).
- Measure before you optimise.
- Composition over segmentation; the relationships between beats are the craft.
- Register fixed, structure flexes.
- Format follows function; every segment has a reason.
- Facts are the human's job; the brief owns truth.
- Review the plan pre-render, the product post-render.
- Make silent failures loud.
- Flag-and-stop over auto-fix.

### Build principle
Ship a thin slice and look at real output before building the measurement and throughput
layers. Order: rails → new prompts → real videos you actually watch → then eval, brief
contract, music, throughput. Don't build the factory before validating the product.

---

## Part 2 — Where we are (2026-07-21)

### PHASE A — Safety rails ✅ complete
- **Step 1 — Spec schema + validation gate.** v2 spec contract (`structure`, per-segment
  `reason`/`transition`/`audio_mode`, `voice` removed) enforced by `spec_validator.py`
  immediately after the CD: hard-rejects out-of-contract specs, auto-repairs the safe
  ones, logs every violation.
- **Step 2 — Structured run record + cost meter.** `run_record.py` writes one JSON per
  run (verdicts, fallback reasons, per-stage cost from real token usage); a per-run cost
  ceiling aborts and flags when crossed.
- **Step 3 — Bounded regeneration.** The Stage 1.5 loop is capped at two regenerations;
  after the cap the spec is flagged for a human, never looped or shipped.

### PHASE B — The thin slice ✅ complete
- **Step 4 — Creative Director v2 live.** Stage 1 runs on `prompts/creative_director_v2.md`
  with a single fixed narrator; the renderer tolerates the additive fields (inert until
  Phase E).
- **Step 5 — Reviewer split.** Pre-render `editorial_critic.py` (loop acts on REVISE;
  facts demoted to `facts_to_check`, never a machine reject) + post-render `output_qa.py`
  (vision, judges extracted frames against the plan, FAIL = non-publish exit code).
- **First validation batch (5 videos, watched).** Composition good across the board;
  execution the weak point — gradient fallbacks, clips not tracking the voice. Root
  cause: low footage hit-rate compounded by many-short-clip composition, where every
  short clip is another hit-rate gamble.
- **Step 6a — Output-QA made honest.** Frame-extraction drift fixed (crossfade
  compression accounted for), plus single_clip-aware verdicts, so the gate's verdicts are
  trustworthy.
- **Step 6b — Hero-clip composition.** CD prompt now defaults to one hero clip as the
  visual spine (supporting clips only where they add context; gradients only deliberate,
  never a silent fallback); researcher gained a footage fallback ladder (alt queries →
  Ken Burns still → gradient, audited per beat) and the `single_clip` format for
  single-continuous-moment stories. Trust fixes followed: format-aware duration ceiling,
  single_clip truncation guard, shared validated clip cache.

**Open before Phase C:** the 6b montage-set validation (`eval/6b_validation.md`) was
cut short by an API spend limit; re-run `scripts/batch_phase_b.py` and re-watch, plus
re-score the single-clip set under the now-honest QA. Keep re-running and watching until
quality clears ~4/5.

---

## Part 3 — The plan from here

### PHASE C — Make it measurable

**Step 7 — Eval harness + human-anchored judge.** A runner over a frozen brief set
(~15–20 briefs): generate specs at fixed temperature, N runs each; score with the
editorial-critic rubric as judge; compare against (a) human ratings of a calibration
subset and (b) the frozen v1 baseline. Output a readable comparison.
*Acceptance: running v1 vs v2 produces a comparison you can act on.*

**Step 8 — Regression guard.** Editing the CD prompt auto-reruns the eval and reports
pass or regress vs the frozen baseline.
*Acceptance: a CD prompt edit surfaces a clear pass-or-regress result.*

### PHASE D — Inputs & coherence

**Step 9 — Brief contract + draft mode.** Define the brief contract (core verifiable
fact with numbers, angle hint, scheme, what's surprising) and reject thin briefs with
what's missing. Add a draft path: brief → CD → critic, no research/render.
*Acceptance: a thin brief is rejected with specifics; draft mode returns a critiqued
spec for pennies.*

**Step 10 — Footage hit-rate (the deeper root) + music polish.** Better/parallel clip
queries, a preflight footage check on the lead clip (a no-footage story fails fast), and
an explicit fallback strategy — a hero-clip miss is flagged loudly, never silently
gradiented. Note: the hero-clip model only reduces the hit-rate burden; this step
addresses the root the validation proved is the real bottleneck.
*Acceptance: a no-footage lead fails fast instead of rendering gradient.*

### PHASE E — Composition depth & throughput

**Step 11 — Wire transitions + audio_mode into the renderer.** Make `transition`
(cut/crossfade/zoom) and `audio_mode` (vo/native) actually drive the render, and play a
hero clip continuously across its spine segments rather than restarting it.
*Acceptance: changing a segment's transition or audio_mode visibly changes the output.*

**Step 12 — Voice-as-master timing (the master-clock fix).** Measure the TTS duration;
segment durations become relative weights/minimums; the renderer lays segments against
the measured voice and times cuts to the read; add sync intent. Absorbs the old render
safety net — truncation becomes impossible by construction.

**Step 13 — Batch runner + review surface.** Queue runner (briefs → specs → review →
render) + one review surface showing passed and flagged videos with frames and reasons.
*Prerequisite: move run-record and cost-meter off module globals to per-run context
before anything runs in parallel.*

### Removed / changed
- The standalone "render safety net" step is gone: Step 5's script-fit check catches the
  common truncation case and Step 12 dissolves it by construction. Revisit only if a
  residual minor cut appears in practice.

### Later (deliberate adds)
- Gemini Google Search grounding on the reviewer for real fact-checking.
- Online performance signal (retention/saves) fed back as exemplar labels.
- ElevenLabs voice swap at monetisation.

### Exemplar debt (parallel track)
Co-author 3–4 News Dispatch specs in your voice — one reveal, one evidence-stack, one
straight report — and inject into CD v2's empty EXEMPLARS slot. Do this now that real
outputs exist to anchor on.
