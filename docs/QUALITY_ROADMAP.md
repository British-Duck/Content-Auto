# Quality Roadmap — from "works" to A-class

Written 2026-07-22. The migration plan (`MIGRATION_PLAN.md`) says *what gets built in
which order*; this document says *what quality is, where today's output loses it, and
which levers buy it back* — with the evidence and the literature behind each call.

---

## 1. What this project is supposed to do

Turn a one-line human brief into a **publishable** 30–50s vertical news-dispatch video —
script, real sourced footage, narration, music, text design — at ~$0.05–0.20 of API
spend per video, with machine gates honest enough that a PASS is trustworthy and a human
only ever has to (a) supply the truth in the brief and (b) watch what passed. The bar
for "publishable" is a **watched quality of 4/5**: a video you would post on a real
channel without embarrassment, that a stranger scrolling past would finish.

The product being optimised is **retention behaviour**, not aesthetic self-judgment: a
short is A-class when the first seconds stop the scroll, the middle never gives a reason
to leave, and the ending makes finishing feel like a payoff. Everything below serves
that.

## 2. What an A-class short looks like (the checklist)

Derived from the failure evidence in §3 and the literature in §6. Each item is
checkable — by the critic pre-render, by Output-QA post-render, or by a human eyeball.

**The first 3 seconds (the gate everything else lives behind)**
- [ ] Hook lands **on real footage** within ~1.5s — never a gradient, never a title card.
- [ ] The hook opens a **specific information gap** — two facts that shouldn't coexist, a
      number that doesn't make sense — and does *not* resolve it in beat 1.
- [ ] The register is high-arousal (awe / alarm / indignation), never flat or somber.
- [ ] Segment 1 works **as a still** — it is the thumbnail and the cover frame.

**The middle (retention mechanics)**
- [ ] A visual change every **2–4 seconds** — a new moment of the source, a new shot, or
      a text change. No static stretch longer than ~4s.
- [ ] Footage tracks the narration beat-for-beat; no frame repeated across segments; no
      branded junk (logos, end-cards, product shots, title cards) on screen — ever.
- [ ] Text and voice are in counterpoint (text states the fact, voice carries the
      tension), never reading each other aloud.
- [ ] Pacing accelerates into the payoff; the longest beat is the one that earns it.
- [ ] Gradients appear only as deliberate suspense/data beats — at most 1–2, never
      opening, never adjacent.

**The ending (share mechanics)**
- [ ] Total runtime 30–40s — inside the completion-rate sweet spot, over the 30s floor.
- [ ] Ends on an open question or a loopable beat (the KICK), not a summary. A finished
      video that invites a rewatch or a comment is the share trigger.

**Production floor (table stakes)**
- [ ] Music bed present and ducked under the VO; narration normalised; no dead air.
- [ ] Every segment has a stated `reason`; the spec, the render, and the QA verdict agree.

## 3. Where today's output loses quality (the evidence)

From run records in `logs/`, `eval/6b_validation.md`, and the batch summaries, ranked by
frequency:

1. **Clip-moment imprecision** — the dominant defect. Real footage is found for ~85% of
   beats (the fallback-ladder work did its job), but the render repeats the **same frame
   across multiple segments** (VW: segments 2/4/6 identical to 0; black-hole: 4 segments
   on one still) or lands on the **wrong moment** of the right source ("foot leaves the
   platform" spec'd, "still standing on the ledge" rendered). The visual narrative dies
   even though the footage exists.
2. **Branded/junk frames** — GoPro product shots and logo end-cards, generic title
   cards, satellite stock instead of the specified subject. Burned-in chyrons can't be
   stripped, but junk *moments* are selectable-away.
3. **Voice overrun** — narration written long for the runtime; the critic's script-fit
   check catches most, but it remains the most common REVISE reason.
4. **QA fragility** — Output-QA has returned "no response" and silently not scored a
   completed video; a gate that can shrug is not a gate.
5. **No recorded human scores** — the whole programme is steering on an aggregate
   memory of "~2/5". Until eyeball scores are written down per video, every improvement
   claim is anecdote.

## 4. Improvement categories

Effort legend: **P** prompt-only · **C** config/asset-only · **K** new code.
Impact is the estimated contribution toward the 2→4 eyeball gap, judged against which
evidenced defects the category removes; treat as priors to be updated by the eval
harness, not measurements.

### A. Composition & script intelligence (P) — impact: high, cost: ~zero

What the spec *plans* determines the ceiling of everything downstream.
- **Hook & retention science in the CD prompt.** The current prompt has one paragraph
  on hooks and no retention model at all — no pattern-interrupt cadence, no cover-frame
  rule, no loop-ending mechanics. Encode §2's first-3-seconds and middle checklists as
  hard craft rules. *(This session.)*
- **Exemplars.** The EXEMPLARS slot has been empty since v2 shipped, while 5
  high-scoring specs sit unused in `specs/pro/`. Few-shot exemplars are the
  best-evidenced cheap lever in LLM generation (Brown et al. 2020); 3 curated,
  hook-upgraded exemplars anchor the register far harder than rules can. *(This session.)*
- **Critic as the same standard.** The editorial critic must score what the CD is told
  to write — hook mechanics and retention cadence become scored dimensions with
  deterministic computed metrics, so the writer/judge loop shares one definition of
  good. *(This session.)*

### B. Footage precision & source quality (K) — impact: highest, the binding constraint

The evidence says composition is already decent and execution is where 2/5 lives.
- **Clip-moment diversity.** Beats sharing a source (the hero-spine pattern the prompt
  itself mandates) must resolve to distinct, chronologically advancing windows; today
  they can collapse onto one frame. This single fix addresses the #1 defect in every
  failed montage QA. *(This session.)*
- **Junk-moment avoidance.** The researcher's video-analysis prompt gets explicit
  negative selection (no title cards, logos, end-cards, product shots), and candidate
  ranking penalises compilation/product uploads. *(This session.)*
- **Later:** parallel query fan-out with reranking; preflight hero-footage check that
  fails a no-footage story before spend (MIGRATION_PLAN Step 10); source-quality
  scoring in `clip_knowledge` so good sources compound across runs.

### C. Production value (C now, K later) — impact: medium-high, the polish gap

- **Music beds — now.** The entire music system (scheme→mood selection, sidechain
  ducking, loudness normalisation) is built and enabled but `music/` contains zero
  audio files, so every render ships voice-only. Dropping in 6 tracks activates it.
  Audio arousal is a documented sharing driver (§6). *(Sourcing list this session;
  files are a 5-minute human action.)*
- **Kinetic captions — next.** Text is currently one static PNG per segment. Word-level
  timed captions are the strongest single production-value norm on short-form platforms
  (most viewing starts muted; captions are the mute-proof channel). Needs renderer work
  (K, significant) — the highest-priority *new* feature after this session's fixes.
- **Native clip audio + per-segment transitions.** `audio_mode` and `transition` are
  validated spec fields the renderer ignores. Testimony/commentary payoffs (a CEO
  apologising, an anchor shouting) lose their meaning without native sound. This is
  MIGRATION_PLAN Step 11, unchanged.
- **Voice.** edge-tts is the floor; an ElevenLabs swap behind the existing
  `generate_voiceover` seam is the documented monetisation-time upgrade.

### D. Judgment & measurement (K) — impact: compounding, the flywheel

- **QA hardening — now.** No-response → retry once → explicit `FAILED_TO_SCORE`
  surfaced as non-publish. A gate must never shrug. *(This session.)*
- **Human scores — now and always.** `eval/6b_validation.md` has an Eyeball column that
  has never been filled. Every watched video gets a 1–5 written down; the human-anchored
  eval (MIGRATION_PLAN Step 7) turns those into the judge-calibration set. Preference
  learning starts with recorded preferences (Stiennon et al. 2020).
- **Later:** multi-frame temporal QA (motion, sync — today's QA sees one still per
  segment); retention-proxy scoring (predicted drop-off curve per spec) once the eval
  harness exists.

### E. Model & inference strategy (C now, K next) — impact: quality per dollar

- **Generation switch — now.** The pipeline still runs `gemini-2.5-pro` ($1.25/$10 per
  M tokens) for CD/critic/QA. Gemini 3 Flash ($0.50/$3) is a generation newer and ~3×
  cheaper — on the current $1.87 balance that is ~25–35 validation runs instead of ~9.
  Researcher stays on 2.5-flash until 3-flash video analysis is verified. Upgrade path
  when budget allows: 3.1 Pro ($2/$12) on the CD only, or the dormant Claude route
  (`llm.py` already routes `claude-*` ids) for a Sonnet-writes/Opus-reviews split.
- **Best-of-N spec sampling — next.** The single highest-leverage inference-time
  technique not yet used: generate N=3–4 specs at temperature 1.0, have the critic
  score all, keep the best. Verifier-guided reranking reliably beats single-sample
  generation (Cobbe et al. 2021; Zheng et al. 2023 for LLM-as-judge validity), and the
  critic already produces comparable scores — the selection primitive exists. At 3-Flash
  prices, N=4 costs roughly what one 2.5-Pro spec cost. Build after the eval harness
  can measure the lift.

## 5. Sequenced roadmap

| Order | Work | Category | Effort | Expected effect |
|---|---|---|---|---|
| 1 | Hook/retention prompt + exemplars + critic rubric | A | P | Spec ceiling up; hooks stop being the weakest beat |
| 2 | Clip-moment diversity + junk-moment avoidance | B | K (small) | Kills the #1 and #2 evidenced defects |
| 3 | QA no-response hardening | D | K (small) | Gate becomes trustworthy |
| 4 | Gemini 3 Flash switch | E | C | ~3× more validation runs per dollar |
| 5 | Music beds (drop files in) | C | C | Production floor rises on every video |
| 6 | Re-render + **record eyeball scores** | D | human | First real quality measurement |
| 7 | Eval harness (Step 7) + regression guard | D | K | Iteration compounds instead of drifting |
| 8 | Kinetic captions | C | K (large) | Mute-proof retention channel |
| 9 | Best-of-N spec sampling | E | K (medium) | Systematic spec-quality lift, measured by 7 |
| 10 | Native audio + per-segment transitions (Step 11) | C | K (large) | Payoff beats gain their sound |

Steps 1–5 are this session. The honest expectation: 1–5 together should move watched
quality from ~2/5 into the 3–3.5 band (they remove the defects that appear in every
failed QA verdict); the 4/5 bar most likely needs 8–10 on top, *measured* by 6–7 rather
than assumed.

## 6. Literature grounding

**Why hooks and arousal (the virality side):**
- Berger & Milkman (2012), *What Makes Online Content Viral?*, J. Marketing Research —
  sharing is driven by high-arousal emotion (awe, anger, anxiety); low-arousal affect
  (sadness) suppresses it. The dispatch register should aim awe/alarm/indignation.
- Tellis, MacInnis, Tirunillai & Zhang (2019), *What Drives Virality of Advertising
  Videos?*, J. Marketing — emotional, story-driven video outperforms informational; drama
  (character + plot) drives sharing; prominent branding early hurts it. Maps to: story
  arc over fact-list, and keep segment 1 free of anything that smells like an ad.
- Loewenstein (1994), *The Psychology of Curiosity*, Psych. Bulletin — the
  information-gap theory: curiosity is aroused by a specific, salient gap between what's
  known and knowable. This is the mechanism a hook exploits; a vague tease opens no gap.
- Lang (2000), the Limited Capacity Model (LC4MP), and Lang et al. (2000) on edits —
  cuts and structural changes trigger orienting responses that re-allocate attention;
  moderate pacing raises arousal and memory, excessive pacing overloads. Basis for the
  2–4s pattern-interrupt cadence rather than either static shots or strobe-cutting.
- Industry retention data (platform analytics, 2025–26): ~63% of highest-CTR short-form
  videos hook inside 3s; ~65% of viewers who survive 3s reach 10s; 16–30s videos hold
  ~84% completion vs ~68% at 31–60s. Directional, not peer-reviewed — but consistent
  with the academic attention literature, and it is the platform's own selection
  pressure. Basis for the 1.5s hook rule and the 30–40s runtime target.

**Why exemplars, judges, and sampling (the AI side):**
- Brown et al. (2020), *Language Models are Few-Shot Learners* — in-context exemplars
  steer style and structure more reliably than instructions alone. The empty EXEMPLARS
  slot is leaving the best-evidenced prompt lever unused.
- Cobbe et al. (2021), *Training Verifiers to Solve Math Word Problems* — generate-many,
  verifier-selects beats single-shot generation; the pattern transfers to any domain
  with a competent judge. Basis for best-of-N spec sampling with the critic as judge.
- Zheng et al. (2023), *Judging LLM-as-a-Judge (MT-Bench)* — strong-model judges agree
  with human preference at rates comparable to human-human agreement, with known biases
  (verbosity, position). Justifies the critic-as-selector, and warns: calibrate it
  against recorded human scores (hence the eyeball column).
- Madaan et al. (2023), *Self-Refine* — iterative critique-and-revise improves outputs
  without retraining; this is the existing Stage 1.5 loop, and argues for keeping the
  critic's feedback specific and actionable rather than a score alone.
- Stiennon et al. (2020), *Learning to Summarize from Human Feedback* — model quality
  ultimately tracks the quality of recorded human preferences. The pipeline's scarcest
  data asset is Jack's written 1–5 scores; every watched-but-unscored video is training
  signal thrown away.

## 7. Non-goals (so the roadmap stays honest)

- No auto-fix loops post-render: flag-and-stop stays. Quality comes from better
  generation and selection, not from a renderer that papers over bad plans.
- No fact-checking by machine: the brief owns truth; `facts_to_check` goes to a human.
- No engagement-bait dark patterns (fake countdowns, misleading hooks): the hook must be
  the story's own tension — a misleading gap trades one view for the channel's trust,
  and the literature (Tellis 2019) says drama works without deception.
