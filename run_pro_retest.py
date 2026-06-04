"""One-off: re-run the 5 robustness briefs on the upgraded creative-director
brain (config.CREATIVE_MODEL) from a FRESH SHEET, and capture the content
reviewer's scores for a Flash-vs-new comparison.

- force_regen=True + cleared caches (see the shell step that backs up
  cache/briefs, cache/timestamps, cache/clip_knowledge) => no first-test data reused.
- Outputs are redirected to output/pro/ and specs/pro/ so the original Flash
  videos in output/ are left untouched (old vs new kept clearly separate).
- Sequential, never parallel (the shared caches aren't lock-safe).
"""
import json
import logging
import time
from pathlib import Path

import config

# Redirect this run's artifacts so old (Flash) and new (Pro) never mix.
config.OUTPUT_DIR = config.ROOT / "output" / "pro"
config.SPECS_DIR = config.ROOT / "specs" / "pro"
config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
config.SPECS_DIR.mkdir(parents=True, exist_ok=True)

import orchestrate  # noqa: E402  (imported after the path overrides above)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pro_retest")

# Exact briefs the user supplied (the original 5, with the two corrections baked in).
BRIEFS = [
    ("bolt",
     "Usain Bolt's 9.58 100m world record has stood since 2009. Nobody has come "
     "within a tenth of a second. It might be the most unbreakable record in sport. "
     "Use dark_gold, achievement angle."),
    ("boeing737max",
     "The Boeing 737 MAX was grounded worldwide after two crashes killed 346 people. "
     "The MCAS system pushed the planes into nosedives the pilots couldn't override. "
     "FAA testimony revealed Boeing knew about the problems. Use dark_red, scandal angle."),
    ("shaq",
     "Shaquille O'Neal was one of the most dominant players in NBA history - but he "
     "was a career 52% free throw shooter. Teams literally invented the 'Hack-a-Shaq' "
     "strategy, fouling him on purpose to send him to the line. His greatest weakness "
     "was a wide open shot. Use dark_purple, sports angle."),
    ("gamestop",
     "In January 2021, Reddit traders on WallStreetBets took on Wall Street hedge funds. "
     "GameStop stock rocketed from around $17 to over $400 as retail investors squeezed "
     "the short sellers. Then Robinhood halted buying. Use dark_red, controversy angle."),
    ("tiger",
     "In 2019, Tiger Woods won the Masters - his first major in 11 years. He'd been "
     "through four back surgeries and many had written him off completely. When he "
     "embraced his son behind the 18th green, it mirrored his own father from decades "
     "earlier. Use dark_gold, comeback angle."),
]

# Flash content-reviewer verdicts from the 2026-05-30 robustness test (the baseline).
FLASH_BASELINE = {
    "bolt": ("REVISE", 7.4), "boeing737max": ("PUBLISH", 9.6), "shaq": ("PUBLISH", 8.8),
    "gamestop": ("REVISE", 5.9), "tiger": ("REJECT", 7.6),
}

OUT = config.ROOT / "logs" / "pro_retest_results.json"


def main():
    log.info("Creative model = %s | Reviewer model = %s",
             config.CREATIVE_MODEL, config.REVIEWER_MODEL)
    results = {}
    for key, brief in BRIEFS:
        log.info("\n%s\n# PRO REGEN: %s\n%s", "#" * 70, key, "#" * 70)
        t0 = time.time()
        try:
            summary = orchestrate.run(brief, force_regen=True)
        except Exception as e:  # keep going so one failure doesn't lose the rest
            log.error("%s failed: %s", key, e)
            results[key] = {"error": str(e)}
            OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")
            continue

        review = summary.get("review") or {}
        results[key] = {
            "flash_baseline": FLASH_BASELINE.get(key),
            "new_decision": review.get("decision"),
            "new_overall": review.get("overall_score"),
            "new_scores": {d: i.get("score") for d, i in (review.get("scores") or {}).items()},
            "quality_gate": summary.get("stages", {}).get("quality_gate"),
            "output": summary.get("output"),
            "elapsed_s": round(time.time() - t0, 1),
            "cost": summary.get("cost"),
        }
        log.info("RESULT %s: %s %s (was %s)", key, results[key]["new_decision"],
                 results[key]["new_overall"], FLASH_BASELINE.get(key))
        OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")  # save after each

    log.info("\nFINAL RESULTS:\n%s", json.dumps(results, indent=2))
    log.info("Saved -> %s", OUT)


if __name__ == "__main__":
    main()
