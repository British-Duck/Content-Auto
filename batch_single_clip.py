"""
One-off batch driver for the single-clip event format (format: "single_clip").

Three single-continuous-moment event stories — a race, a record feat, a filmed
disaster — exactly the shape single_clip is for. Each is a real event with very
findable continuous footage. The CD chooses the format; this batch confirms the
single_clip path end-to-end and leaves the videos in one folder to watch.

Run:  python batch_single_clip.py
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

import orchestrate  # noqa: E402  (imported after the dir redirect)

BRIEFS = [
    # dark_gold — a race (one continuous take: gun, run, finish)
    "On 16 August 2009 at the Berlin World Championships, Usain Bolt ran the 100 "
    "metres in 9.58 seconds, a world record that still stands. He beat the field by "
    "a clear margin and was visibly easing off before the line. The whole thing is one "
    "continuous filmed moment: the gun, the run, the finish. Use dark_gold.",

    # dark_blue — a record feat, filmed live and continuous
    "On 14 October 2012 the skydiver Felix Baumgartner jumped from a balloon at the "
    "edge of space, about 39 kilometres up, and became the first person to break the "
    "sound barrier in freefall, reaching roughly 1,358 km/h. Millions watched the live "
    "stream as he stepped off the capsule and fell. Use dark_blue.",

    # dark_red — a filmed disaster that unfolds in one continuous shot
    "On 6 May 1937 the German airship Hindenburg caught fire and was destroyed in about "
    "34 seconds as it tried to dock in New Jersey, killing 36 people. A radio reporter "
    "watching it burn cried out 'oh, the humanity'. The disaster was captured on film. "
    "Use dark_red.",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("batch-sc")

results = []
t_start = time.time()
for i, brief in enumerate(BRIEFS, 1):
    log.info("=" * 70)
    log.info("########## SINGLE-CLIP VIDEO %d/%d ##########", i, len(BRIEFS))
    log.info("brief: %s", brief[:90])
    log.info("=" * 70)
    t0 = time.time()
    entry = {"n": i, "brief": brief[:70]}
    try:
        summary = orchestrate.run(brief, force_regen=True)
        spec_path = None
        out = summary.get("output")
        fmt = None
        if out:
            sp = Path(out).with_suffix(".json")
            sp = BATCH / (Path(out).stem + ".json")
            if sp.exists():
                try:
                    fmt = json.loads(sp.read_text(encoding="utf-8")).get("format")
                except Exception:
                    pass
        entry.update({
            "output": out,
            "format": fmt,
            "success": summary.get("success"),
            "spec_review": (summary.get("spec_review") or {}).get("final_decision"),
            "output_qa": (summary.get("output_qa") or {}).get("decision"),
            "stages": summary.get("stages"),
            "run_record": summary.get("run_record"),
            "cost": (summary.get("cost") or {}),
            "elapsed_s": round(time.time() - t0, 1),
        })
    except Exception as e:
        log.exception("video %d crashed", i)
        entry["error"] = str(e)
    results.append(entry)
    (BATCH / "BATCH_SUMMARY.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

log.info("=" * 70)
log.info("BATCH DONE in %.0fs", time.time() - t_start)
for r in results:
    out = r.get("output") or r.get("error") or "(no output)"
    log.info("  %d. %-9s fmt=%s qa=%s success=%s  %s",
             r["n"], "OK" if r.get("output") else "FLAGGED",
             r.get("format"), r.get("output_qa"), r.get("success"),
             Path(out).name if r.get("output") else out)
