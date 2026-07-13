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
import re
import time
from collections import Counter
from typing import Optional

import config
import gemini
import llm
from cost_tracker import get_tracker

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (config.PROMPTS_DIR / "editorial_critic.md").read_text(encoding="utf-8")

# Matches the reading-speed estimate stated in the critic prompt (~3-4 words/sec).
WORDS_PER_SECOND = 3.3


def compute_metrics(spec: dict) -> dict:
    """Deterministic numbers for the critic's script-fit and spine checks.

    LLMs are unreliable at arithmetic, and script-fit is the loop's most
    load-bearing trigger — so the pipeline measures, the model judges. Also
    surfaces near-duplicate clip_descriptions (differing only in case or
    punctuation), which silently break the hero-clip spine: the researcher
    groups spine segments by EXACT string match. Pure function."""
    screens = [s for s in (spec.get("screens") or []) if isinstance(s, dict)]
    durations = [s["duration"] for s in screens
                 if isinstance(s.get("duration"), (int, float))]
    runtime = round(sum(durations), 1)
    words = len((spec.get("voice_script") or "").split())
    est_read = round(words / WORDS_PER_SECOND, 1)

    counts = Counter(s["clip_description"] for s in screens
                     if isinstance(s.get("clip_description"), str))
    norm_groups: dict[str, list] = {}
    for desc in counts:
        norm = re.sub(r"[^a-z0-9 ]", "", desc.lower()).strip()
        norm_groups.setdefault(norm, []).append(desc)
    near_dupes = [sorted(group) for group in norm_groups.values() if len(group) > 1]

    return {
        "runtime_s": runtime,
        "voice_script_words": words,
        "estimated_read_s": est_read,
        "read_overrun_pct": (round((est_read - runtime) / runtime * 100.0, 1)
                             if runtime else None),
        "n_screens": len(screens),
        "n_gradient_screens": sum(1 for s in screens if not s.get("clip_description")),
        "clip_description_counts": dict(counts),
        "near_duplicate_clip_descriptions": near_dupes,
    }


def review_spec(spec: dict) -> Optional[dict]:
    """Run the editorial critic over a spec. Returns its verdict dict, or None on
    a call/parse failure (the caller then proceeds with the best spec so far)."""
    log.info("Editorial critic: reviewing spec plan...")

    metrics = compute_metrics(spec)
    log.info("  measured: runtime %.1fs, script %d words (~%.1fs read)",
             metrics["runtime_s"], metrics["voice_script_words"], metrics["estimated_read_s"])
    prompt = (
        f"{SYSTEM_PROMPT}\n\n---\n\n"
        f"COMPUTED METRICS (measured by the pipeline — trust these numbers, do NOT "
        f"recompute them):\n{json.dumps(metrics, indent=2, ensure_ascii=False)}\n\n"
        f"VIDEO SPEC:\n{json.dumps(spec, indent=2, ensure_ascii=False)}"
    )

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
