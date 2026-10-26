# scripts/

Batch drivers used for validation runs. Each one calls `orchestrate.run()` on a fixed
set of briefs, so running it **makes paid API calls**. They are run from the repo root:

```bash
python scripts/batch_phase_b.py
```

| Script | Purpose |
|---|---|
| `batch_phase_b.py` | Renders the 5-video Phase B montage watch set into one folder under `output/` and writes `BATCH_SUMMARY.json`. Scored against `eval/6b_validation.md`. |
| `batch_single_clip.py` | Renders 3 single-continuous-moment briefs (a race, a record jump, a filmed disaster) to validate the `single_clip` format end to end. |
| `batch_single_clip_refine.py` | Re-runs 2 of the single-clip briefs into the same folder after a Creative Director prompt refinement. |
| `run_pro_retest.py` | Historical: re-ran 5 robustness briefs when the Creative Director moved to a Pro model, to compare reviewer scores against the earlier Flash run. Written for the v1 content reviewer. |
| `_demo_blackhole.py` | Historical: one brief rendered to demonstrate the Ken Burns still tier of the footage fallback ladder. |
