# Content Pipeline — Migration Plan (v1 → News Dispatch v2)

A phased, Claude-Code-driven migration. Each step is one scoped session with a clear
acceptance check. Do not move to the next step until the current one passes its check.

## Build principle
Ship a thin slice and look at real output before building the measurement and
throughput layers. Order: rails → new prompts → 5–10 real videos you actually watch →
then eval, brief contract, music, throughput. Don't build the factory before validating
the product.

## Principles this migration serves
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

---

## PHASE A — Safety rails (do first; cheap; prevents silent disaster)

### Step 1 — Spec schema + validation gate
Task for Claude Code: Define the v2 spec schema (existing fields plus `structure`,
and per-segment `reason`, `transition` ∈ {cut, crossfade, zoom}, `audio_mode` ∈
{vo, native}; `voice` removed). Add a validator that runs immediately after the
Creative Director and BEFORE research/render: it rejects or auto-repairs specs that
violate the contract (bad scheme/enum, duration out of 1.0–6.0, segment count out of
4–12, empty-string where null is required, malformed JSON) and logs every violation.
Files: a new schema/validator module; wire it into `orchestrate.py` after Stage 1.
Acceptance: a deliberately malformed spec is caught and never reaches render; the
violation appears in the log.

### Step 2 — Structured run record + cost meter
Task: Create one run-record object per video capturing brief, CD prompt version, model,
temperature, editorial-critic verdict, output-QA verdict, every fallback reason
(gradient fallbacks, missing footage), and per-stage cost (CD / research / QA). Write
it to `logs/` as JSON. Add a per-run cost ceiling that aborts and flags if exceeded.
Files: a run-record module; hooks in each stage; `orchestrate.py`.
Acceptance: a normal run produces a complete record; a forced gradient fallback shows
up explicitly as a logged reason; total cost is reported.

### Step 3 — Bound the regeneration loop
Task: Cap the Stage 1.5 loop at two regenerations. After the cap, stop and flag for
human review rather than looping or shipping the failing draft. (This step also sets up
Step 5's loop-broadening.)
Files: `orchestrate.py` loop logic.
Acceptance: a spec that keeps failing is flagged after two passes, not looped.

---

## PHASE B — Swap in the new prompts (the thin slice)

### Step 4 — Drop in Creative Director v2
Task: Point Stage 1 at `creative_director_v2.md`. Remove the `voice` field from the CD
output handling and set a single fixed narrator in config. Confirm the renderer ignores
the new additive fields (`structure`, `reason`, `transition`, `audio_mode`) so nothing
breaks (they stay inert until Phase E).
Files: CD stage config; renderer (verify unknown-field tolerance).
Acceptance: a run produces a schema-valid v2 spec with the new fields and renders end
to end exactly as before.

### Step 5 — Split the reviewer
Task: Replace the single content reviewer. Wire `editorial_critic.md` into the
pre-render stage and broaden the Stage 1.5 loop so it acts on an editorial REVISE
(not only `factual_accuracy < 8`), within the two-pass cap from Step 3. Wire
`output_qa.md` into the Quality Gate: feed it the spec, the ffprobe report, and the
per-segment extracted frames; make a FAIL set the non-publish exit code and surface
`blocking_issues` to the human. Remove the old factual hard-reject.
Files: reviewer stages; `orchestrate.py`; Quality Gate frame-passing.
Acceptance: a weak-hook draft triggers an editorial revise pre-render; a render with a
gradient on an evidence beat triggers an output-QA FAIL with the segment named.

### → Make 5–10 real videos here and watch them before continuing.

---

## PHASE C — Make it measurable (after you've seen output)

### Step 6 — Eval harness + human-anchored judge
Task: Build a runner over a frozen brief set (~15–20 briefs). For each: generate specs
at fixed temperature, N runs each; score with the editorial-critic rubric as judge;
compare against (a) your own human ratings of a calibration subset and (b) the frozen v1
baseline. Output a readable comparison.
Files: new `eval/` module; frozen brief set; your ratings file.
Acceptance: running v1 vs v2 produces a comparison you can act on.

### Step 7 — Regression guard
Task: Run the eval automatically on any CD prompt change and report pass/regress vs the
frozen baseline.
Acceptance: editing the CD prompt surfaces a clear pass-or-regress result.

---

## PHASE D — Inputs & coherence

### Step 8 — Brief contract + draft mode
Task: Define the brief contract (core verifiable fact with numbers, angle hint, scheme,
what's surprising) and reject thin briefs with what's missing. Add a draft/preview path:
brief → CD → editorial critic, no research/render.
Acceptance: a thin brief is rejected with specifics; draft mode returns a critiqued spec
for pennies.

### Step 9 — Music bed + footage preflight
Task: Config-driven music (scheme → mood from one library), auto-duck under VO, drop or
lower under `audio_mode: native` segments. Add a preflight footage check on the lead
clip so a no-footage story fails fast.
Acceptance: music is present and ducked correctly; a no-footage lead fails fast instead
of rendering gradient.

---

## PHASE E — Composition depth & throughput (later)

### Step 10 — Wire transitions + audio_mode into the renderer
Task: Make `transition` (cut/crossfade/zoom) and `audio_mode` (vo/native) actually drive
the render. This is where the composition design becomes real on screen.
Acceptance: changing a segment's transition or audio_mode visibly changes the output.

### Step 11 — Batch runner + review surface
Task: A queue runner (briefs → specs → review → render) and a single review surface
showing passed videos and flagged ones with frames and blocking reasons.
Acceptance: a batch of briefs runs unattended; flagged videos are reviewable in one place.

---

## Later (deliberate adds)
- Gemini Google Search grounding on the reviewer for real fact-checking.
- Online performance signal (retention/saves) fed back as exemplar labels.
- ElevenLabs voice swap at monetisation.

## Exemplar debt (parallel track)
Co-author 3–4 News Dispatch specs in your voice — one reveal, one evidence-stack, one
straight report — and inject into CD v2's exemplar slot. Do this once you've seen a few
real outputs, so the exemplars reflect what actually works.
