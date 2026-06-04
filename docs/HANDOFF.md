# HANDOFF — Content Automation pipeline

Last updated: 2026-05-31. Read this first, then `docs/pipeline_map.txt` for the full
system map and `docs/test_results_may_2026.txt` for the original 5-topic robustness test.

The system turns a plain-English brief into a postable vertical short video through 5 stages:
Creative Director → Researcher → Render → Quality Gate → Content Reviewer. See the pipeline map.

---

## Where things stand (the short version)

The pipeline works end-to-end and is **close to ready for real media accounts**. Recent work
focused on (a) visual quality of the render and (b) upgrading the creative-director "brain".
The latest open thread is **text layout/presentation** — see "In progress" below.

---

## Per-stage model selection (NEW — this is how you change the "brain")

Models are now chosen per stage in `config.py`, routed by `llm.py` (prefix `gemini-*` → Gemini,
`claude-*` → Claude via `claude.py`). The **researcher must stay Gemini** (native video analysis).

```
CREATIVE_MODEL = "gemini-2.5-pro"      # creative director (currently Pro)
REVIEWER_MODEL = "gemini-2.5-flash"    # content reviewer
GEMINI_MODEL   = "gemini-2.5-flash"    # researcher + default
```

**To run the planned Anthropic setup** (Sonnet writes, Opus reviews): set
`CREATIVE_MODEL = "claude-sonnet-4-6"`, `REVIEWER_MODEL = "claude-opus-4-8"`, add
`ANTHROPIC_API_KEY` to `.env`, and `pip install anthropic`. `claude.py` is built and dormant
(prompt caching + adaptive thinking already wired); if the key/SDK is missing, `llm.py` falls
back to Gemini so nothing breaks. Rough cost: Sonnet CD ≈ +$0.05/video; Opus reviewer ≈
+$0.07–0.10/video (Opus's $75/1M output rate dominates, not call count).

---

## Pro brain re-test result (2026-05-31)

Re-ran the 5 robustness briefs from a **fresh sheet** (cleared briefs/timestamps/clip_knowledge —
backed up to `cache/_pretest_backup_20260531_005532/`, restore by moving back). Creative
director on `gemini-2.5-pro`, reviewer still Flash. Outputs in `output/pro/`, scores in
`logs/pro_retest_results.json`.

| Topic | Flash (1st test) | Pro (fresh sheet) |
|---|---|---|
| Bolt | REVISE 7.4 | PUBLISH 9.12 |
| Boeing | PUBLISH 9.6 | PUBLISH 10.0 |
| Shaq | PUBLISH 8.8 | PUBLISH 9.33 |
| GameStop | REVISE 5.9 | PUBLISH 9.55 |
| Tiger | REJECT 7.6 | PUBLISH 9.0 |
| **Avg** | **7.86** | **9.40** |

All 5 cleared PUBLISH. **Read with caveats:** (1) the reviewer scores the spec JSON, not the
video, so this measures script/structure, not what you watch; (2) it bundles Pro + new prompt +
corrected briefs + fresh clips, so it's not a clean brain-only isolation; (3) absolute scores
look inflated (Boeing 10.0 straight) — trust the consistent *direction*, not the digits;
(4) reported $ under-counts Pro (cost tracker still bills Flash rates).

---

## Render improvements landed this session (all in `vidpipe/`)

- **Crop:** `renderer.py` — landscape clips now fit fully in-frame with a blurred/darkened
  background fill (no more zoomed center-crop). Confirmed good on real footage.
- **Text overlay:** `text.py` — removed the dev "1/6" counter pill; subtitle size made
  proportional (`text_size*0.42`) instead of fixed 26px.
- **Audio:** `voiceover.py` — explicit `-map 0:v:0/1:a:0` + `loudnorm` for reliable, normalized voiceover.
- **Cost dry-run:** `orchestrate.py` — `_est_thinking_tokens()` stops `--dry-run` crashing on
  uncapped (None) thinking budgets.

## Text layout / presentation — SELECTABLE (B or C)

`text.py` + `renderer.py` + `config.LAYOUT_STYLE` (+ optional per-spec `"layout"` field).
Set the style in `config.py` (`LAYOUT_STYLE = "B"` default) or override per video by adding a
top-level `"layout": "C"` to a spec JSON. `renderer.render_screen` resolves `spec.layout or
config.LAYOUT_STYLE`.

- **Option B** — every screen: headline **top**, subtitle **bottom**. Clip screens get soft
  top/bottom gradient **scrims** (`_draw_scrims`); the centre stays clear for footage.
- **Option C** — **clip** screens: headline **centred over footage** with a soft full-frame
  **dim** (`_draw_full_dim`) — the "title card over video" look. **Stat/story/monologue (gradient)**
  screens: top/bottom.
- Placement is driven by explicit `centered` / `scrim` / `dim` flags `renderer.py` passes to
  `render_overlay` (no longer derived from `panel`).
- **Gradient fallback**: if a clip fails or none was found, the overlay re-renders without
  scrim/dim but keeps the layout's placement, so it reads as a clean gradient.

Verified: both B and C overlays render correctly over synthetic footage (scrim keeps the middle
clear; dim makes centred text legible). ⚠️ **Not yet eyeballed over real YouTube footage** — the
verification renders kept hitting transient `yt-dlp` throttling (clips fell back to gradient). Do
one clean `--skip-research` re-render of a `specs/pro/*.json` once downloads recover and check
`output/<id>_frames/`. Open question for the user: which of B/C should be the default, or should
the creative director pick per video (would need a CD prompt/`_fixup` change to emit `layout`).

---

## Known issues / next priorities

1. **Per-screen audio mode** (designed, NOT built) — highest-value next feature. All clip audio is
   stripped; news/testimony clips (Boeing CEO, GameStop anchors) lose meaning that screen text
   can't replace. Design (audio_mode: null | "clip_audio" | "ducked") is in
   `docs/test_results_may_2026.txt` and `docs/pipeline_map.txt`. Touches spec.py, renderer.py,
   assembler.py, voiceover.py, creative_director.txt.
2. **Factual gate blind to recent events** — the content reviewer uses its training cutoff, so it
   *false-rejected* the true (post-cutoff) Starliner story. Treat REJECT as advisory on current
   events; factual accuracy of briefs is the user's responsibility.
3. **Source footage quality** — space/news topics pull YouTube clips with burned-in chyrons
   (ABC/news) and channel watermarks. Pipeline can't strip these; mitigate via better
   clip_descriptions, not code.
4. **yt-dlp transient failures** — rapid repeated re-renders can get throttled; downloads recover
   on their own. Not a code bug.

---

## Common commands

```
python orchestrate.py "Brief text. Use dark_red, scandal angle."   # new brief
python orchestrate.py specs/<id>.json --skip-research              # re-render existing spec
python orchestrate.py "Brief..." --dry-run                        # cost preview (no API calls)
python orchestrate.py "Brief..." --force-regen                    # ignore brief cache
python run_pro_retest.py                                          # the 5-brief Pro A/B (fresh sheet)
```

Old Flash baseline videos: `output/*.mp4`. New Pro videos: `output/pro/*.mp4`. Kept separate on purpose.
