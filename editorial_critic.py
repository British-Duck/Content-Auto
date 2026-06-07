"""
Stage 1.5 — Editorial Critic (pre-render).

Judges the EDITORIAL PLAN of a spec — the writing and structure — BEFORE the
expensive research/render stages. Cheap insurance: catching a weak hook here costs
one regeneration; catching it post-render wastes the whole render.

Returns the critic's verdict JSON:
  decision         PUBLISH | REVISE | REJECT
  biggest_weakness one sentence
  scores           {hook, arc, rhythm, text_craft, voice, reasons} → {score, reason}
  fixes            actionable fixes (feed back into one regeneration)
  facts_to_check   claims a HUMAN should verify — NEVER a reject (see prompt)

Facts are a human's job: this stage never rejects on factual grounds.
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

SYSTEM_PROMPT = (config.PROMPTS_DIR / "editorial_critic.md").read_text(encoding="utf-8")


def review_spec(spec: dict) -> Optional[dict]:
    """Run the editorial critic over a spec. Returns its verdict dict, or None on
    a call/parse failure (the caller then proceeds with the best spec so far)."""
    log.info("Editorial critic: reviewing spec plan...")

    prompt = f"{SYSTEM_PROMPT}\n\n---\n\nVIDEO SPEC:\n{json.dumps(spec, indent=2, ensure_ascii=False)}"

    time.sleep(config.GEMINI_RPM_SLEEP)
    log.info("Calling Gemini (editorial critic)...")
    get_tracker().record_text_call(
        "editorial_critic",
        prompt_words=len(prompt.split()),
        response_words=300,
        thinking_tokens=config.THINKING_BUDGET_REVIEWER,
    )
    try:
        raw = llm.generate(prompt, temperature=0.3,
                           thinking_budget=config.THINKING_BUDGET_REVIEWER,
                           model=config.EDITORIAL_CRITIC_MODEL)
    except Exception as e:
        log.error("Editorial critic call failed: %s", e)
        return None

    try:
        result = gemini.parse_json(raw)
    except (json.JSONDecodeError, ValueError) as e:
        log.error("Editorial critic JSON parse failed: %s", e)
        log.debug("Raw: %s", raw[:2000])
        return None

    decision = result.get("decision", "UNKNOWN")
    log.info("Editorial verdict: %s", decision)
    log.info("  biggest weakness: %s", result.get("biggest_weakness", ""))
    for dim, info in (result.get("scores") or {}).items():
        if isinstance(info, dict):
            log.info("  %s: %s — %s", dim, info.get("score"), info.get("reason", ""))
    for fix in result.get("fixes") or []:
        log.info("  fix: %s", fix)
    for fact in result.get("facts_to_check") or []:
        # Loud, but NEVER a reject - facts are the human's job.
        log.info("  [fact to verify - human] %s", fact)

    return result


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)-5s %(message)s",
                        datefmt="%H:%M:%S")
    if len(sys.argv) < 2:
        print("Usage: python editorial_critic.py <spec.json>")
        sys.exit(1)
    spec = json.loads(open(sys.argv[1], encoding="utf-8").read())
    result = review_spec(spec)
    if result:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result else 1)
