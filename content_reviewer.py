"""
Stage 5 — Content Reviewer.

Sends the finished spec to Gemini for quality scoring across six dimensions.
Returns a structured verdict: PUBLISH / REVISE / REJECT.
"""
import json
import logging
import time
from typing import Optional

import config
import gemini
import llm
from cost_tracker import get_tracker

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (config.PROMPTS_DIR / "content_reviewer.txt").read_text(encoding="utf-8")


def review_spec(spec: dict) -> Optional[dict]:
    log.info("Reviewing spec…")

    prompt = f"{SYSTEM_PROMPT}\n\n---\n\nVIDEO SPEC:\n{json.dumps(spec, indent=2)}"

    time.sleep(config.GEMINI_RPM_SLEEP)
    log.info("Calling Gemini (review)…")
    get_tracker().record_text_call(
        "content_reviewer",
        prompt_words=len(prompt.split()),
        response_words=300,
        thinking_tokens=config.THINKING_BUDGET_REVIEWER,
    )
    try:
        raw = llm.generate(prompt, temperature=0.3,
                           thinking_budget=config.THINKING_BUDGET_REVIEWER,
                           model=config.REVIEWER_MODEL)
    except Exception as e:
        log.error("Review call failed: %s", e)
        return None

    try:
        result = gemini.parse_json(raw)
    except (json.JSONDecodeError, ValueError) as e:
        log.error("Review JSON parse failed: %s", e)
        log.debug("Raw: %s", raw[:2000])
        return None

    decision = result.get("decision", "UNKNOWN")
    score = result.get("overall_score", 0)
    log.info("Verdict: %s (%.1f/10)", decision, score)

    for dim, info in result.get("scores", {}).items():
        log.info("  %s: %d — %s", dim, info.get("score", 0), info.get("reason", ""))

    if result.get("fixes"):
        log.info("Suggested fixes:")
        for fix in result["fixes"]:
            log.info("  → %s", fix)

    return result


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S")
    if len(sys.argv) < 2:
        print("Usage: python content_reviewer.py <spec.json>")
        sys.exit(1)
    spec = json.loads(open(sys.argv[1], encoding="utf-8").read())
    result = review_spec(spec)
    if result:
        print(json.dumps(result, indent=2))
    sys.exit(0 if result else 1)
