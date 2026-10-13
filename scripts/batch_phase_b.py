"""
One-off batch driver — generate the Phase B "watch" set (5 real videos) into a
single clearly-labelled, self-contained folder under output/.

Redirects OUTPUT_DIR + SPECS_DIR to the batch folder BEFORE importing the
orchestrator, so each video's spec / storyboard / timeline / mp4 / frames all land
together. Run records still go to logs/ (standard). Writes BATCH_SUMMARY.json.

Run:  python scripts/batch_phase_b.py
"""
import json
import logging
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root

import config

BATCH = config.ROOT / "output" / "phase-b-batch-2026-06-08-ladder"
BATCH.mkdir(parents=True, exist_ok=True)

# Co-locate all per-video artifacts in the batch folder.
config.OUTPUT_DIR = BATCH
config.SPECS_DIR = BATCH

import orchestrate  # noqa: E402  (imported after the dir redirect)

# Five briefs: one per colour scheme, varied structural bends, facts in the brief.
BRIEFS = [
    # dark_blue — reveal
    "In April 2019 the Event Horizon Telescope released the first ever image of a "
    "black hole, at the centre of galaxy M87, 55 million light-years away. The black "
    "hole is 6.5 billion times the mass of the Sun. The image was assembled from data "
    "recorded by eight radio telescopes across the globe — about 5 petabytes, too much "
    "to send over the internet, so the hard drives were flown by plane. "
    "Use dark_blue, reveal angle.",

    # dark_red — scandal
    "In 2015 Volkswagen admitted that 11 million of its diesel cars worldwide were "
    "fitted with software that detected emissions tests and cheated them, emitting up "
    "to 40 times the legal limit of nitrogen oxides on the road. The scandal cost the "
    "company more than 30 billion dollars in fines and settlements and its CEO resigned. "
    "Use dark_red, scandal angle.",

    # dark_green — collapse
    "In November 2022 the crypto exchange FTX collapsed within days after a leaked "
    "balance sheet triggered a bank run. Founder Sam Bankman-Fried had quietly used "
    "about 8 billion dollars of customer deposits to prop up his trading firm Alameda. "
    "The company filed for bankruptcy and he was later convicted of fraud. "
    "Use dark_green, collapse angle.",

    # dark_purple — mystery
    "In 1977 a radio telescope in Ohio detected a strong narrowband signal coming from "
    "the direction of the constellation Sagittarius, lasting 72 seconds. The astronomer "
    "circled the reading on the printout and wrote 'Wow!' beside it. The signal was never "
    "detected again and its source has never been explained. "
    "Use dark_purple, mystery angle.",

    # dark_gold — achievement
    "On 20 July 1969 Apollo 11 landed on the Moon with less than 30 seconds of fuel "
    "remaining, after Neil Armstrong took manual control to steer past a field of "
    "boulders. An estimated 650 million people watched the landing live on television. "
    "Use dark_gold, achievement angle.",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("batch")

results = []
t_start = time.time()
for i, brief in enumerate(BRIEFS, 1):
    log.info("=" * 70)
    log.info("########## VIDEO %d/%d ##########", i, len(BRIEFS))
    log.info("brief: %s", brief[:90])
    log.info("=" * 70)
    t0 = time.time()
    entry = {"n": i, "brief": brief[:70]}
    try:
        # force_regen: same briefs as the first batch — bypass the cached old specs
        # so the new hero-clip CD prompt actually regenerates each one.
        summary = orchestrate.run(brief, force_regen=True)
        entry.update({
            "output": summary.get("output"),
            "success": summary.get("success"),
            "spec_review": summary.get("spec_review"),
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
    ed = ((r.get("spec_review") or {}).get("final_decision"))
    log.info("  %d. %-9s ed=%s qa=%s success=%s  %s",
             r["n"], "OK" if r.get("output") else "FLAGGED",
             ed, r.get("output_qa"), r.get("success"), Path(out).name if r.get("output") else out)
