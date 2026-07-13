"""
Focused re-run for the single-take CD refinement: regenerate ONLY Baumgartner and
Hindenburg (Bolt already PASSes and is kept). Same briefs as batch_single_clip.py,
into the same folder so all three sit together to re-score.

Run:  python batch_single_clip_refine.py
"""
import json
import logging
import time
from pathlib import Path

import config

BATCH = config.ROOT / "output" / "single-clip-batch-2026-06-08"
BATCH.mkdir(parents=True, exist_ok=True)
config.OUTPUT_DIR = BATCH
config.SPECS_DIR = BATCH

import orchestrate  # noqa: E402

BRIEFS = [
    "On 14 October 2012 the skydiver Felix Baumgartner jumped from a balloon at the "
    "edge of space, about 39 kilometres up, and became the first person to break the "
    "sound barrier in freefall, reaching roughly 1,358 km/h. Millions watched the live "
    "stream as he stepped off the capsule and fell. Use dark_blue.",

    "On 6 May 1937 the German airship Hindenburg caught fire and was destroyed in about "
    "34 seconds as it tried to dock in New Jersey, killing 36 people. A radio reporter "
    "watching it burn cried out 'oh, the humanity'. The disaster was captured on film. "
    "Use dark_red.",
]

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("batch-sc-refine")

results = []
t_start = time.time()
for i, brief in enumerate(BRIEFS, 1):
    log.info("=" * 70)
    log.info("########## REFINE VIDEO %d/%d ##########", i, len(BRIEFS))
    log.info("brief: %s", brief[:90])
    log.info("=" * 70)
    t0 = time.time()
    entry = {"n": i, "brief": brief[:70]}
    try:
        summary = orchestrate.run(brief, force_regen=True)
        out = summary.get("output")
        fmt = None
        if out:
            sp = BATCH / (Path(out).stem + ".json")
            if sp.exists():
                try:
                    fmt = json.loads(sp.read_text(encoding="utf-8")).get("format")
                except Exception:
                    pass
        entry.update({
            "output": out, "format": fmt,
            "success": summary.get("success"),
            "spec_review": (summary.get("spec_review") or {}).get("final_decision"),
            "output_qa": (summary.get("output_qa") or {}).get("decision"),
            "run_record": summary.get("run_record"),
            "elapsed_s": round(time.time() - t0, 1),
        })
    except Exception as e:
        log.exception("video %d crashed", i)
        entry["error"] = str(e)
    results.append(entry)
    (BATCH / "REFINE_SUMMARY.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

log.info("=" * 70)
log.info("REFINE BATCH DONE in %.0fs", time.time() - t_start)
for r in results:
    out = r.get("output") or r.get("error") or "(no output)"
    log.info("  %d. %-9s fmt=%s qa=%s success=%s  %s",
             r["n"], "OK" if r.get("output") else "FLAGGED",
             r.get("format"), r.get("output_qa"), r.get("success"),
             Path(out).name if r.get("output") else out)
