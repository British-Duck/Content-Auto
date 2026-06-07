"""
Stage 4.5 — Output-QA (post-render, vision).

Runs AFTER render. Confirms the FINISHED video matches its plan — the things the
editorial critic structurally could not see: footage vs gradient, footage matching
the claim, text legibility, audio present, technicals. It is a flag-and-stop gate:
it never edits and never triggers a re-render. On FAIL the caller stops the publish
and surfaces blocking_issues to a human.

Inputs:
  spec          the (researched) spec JSON
  probe_report  the Quality Gate's technical report (ffprobe-derived checks)
  frames_dir    output/<id>_frames/ — one extracted frame per segment (screen_NN.jpg)

The frames are passed to a vision-capable Gemini model as images, in segment order.

Returns the QA verdict JSON:
  decision        PASS | FAIL
  blocking_issues each hard-check failure, naming the segment index
  warnings        soft-check concerns
  per_segment     [{index, footage_ok, matches_claim, text_legible, note}]
  summary         2-3 sentences
"""
import json
import logging
import time
from pathlib import Path
from typing import Optional

import config
import gemini
from cost_tracker import get_tracker

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (config.PROMPTS_DIR / "output_qa.md").read_text(encoding="utf-8")


def qa_output(spec: dict, probe_report: dict, frames_dir: Optional[Path]) -> Optional[dict]:
    """Judge the rendered video against its plan from the per-segment frames + probe.
    Returns the verdict dict, or None if QA could not run (no frames / call failure)."""
    log.info("Output-QA: judging the rendered video against its plan...")

    if not frames_dir:
        log.warning("Output-QA: no frames directory — cannot judge the render")
        return None
    frames_dir = Path(frames_dir)

    # One frame per segment, in order. Missing frames are reported, not silently skipped.
    n = len(spec.get("screens", []))
    frames: list[tuple[int, Path]] = []
    missing: list[int] = []
    for i in range(n):
        f = frames_dir / f"screen_{i:02d}.jpg"
        if f.exists():
            frames.append((i, f))
        else:
            missing.append(i)
    if missing:
        log.warning("Output-QA: missing extracted frame(s) for segment(s) %s", missing)
    if not frames:
        log.warning("Output-QA: no extracted frames found in %s — cannot judge the render", frames_dir)
        return None

    indices = [i for i, _ in frames]
    context = {
        "spec": spec,
        "ffprobe_report": probe_report,
        "frame_segment_indices": indices,
        "frames_missing_for_segments": missing,
    }
    prompt = (
        f"{SYSTEM_PROMPT}\n\n---\n\n"
        f"The {len(frames)} images that follow are one extracted frame per segment, "
        f"in segment order. Their segment indices are: {indices}. Match each image to "
        f"its segment by this order.\n\n"
        f"SPEC + TECHNICAL REPORT:\n{json.dumps(context, indent=2, ensure_ascii=False)}"
    )

    time.sleep(config.GEMINI_RPM_SLEEP)
    log.info("Calling Gemini (Output-QA, %d frame(s))...", len(frames))
    get_tracker().record_text_call(
        "output_qa",
        prompt_words=len(prompt.split()),
        response_words=300,
        thinking_tokens=config.THINKING_BUDGET_REVIEWER,
    )
    try:
        raw = gemini.generate_vision(
            prompt, [f for _, f in frames],
            temperature=0.2,
            thinking_budget=config.THINKING_BUDGET_REVIEWER,
            model=config.OUTPUT_QA_MODEL,
        )
    except Exception as e:
        log.error("Output-QA call failed: %s", e)
        return None

    try:
        result = gemini.parse_json(raw)
    except (json.JSONDecodeError, ValueError) as e:
        log.error("Output-QA JSON parse failed: %s", e)
        log.debug("Raw: %s", raw[:2000])
        return None

    decision = result.get("decision", "UNKNOWN")
    log.info("Output-QA verdict: %s", decision)
    for issue in result.get("blocking_issues") or []:
        log.warning("  [BLOCKING] %s", issue)
    for warn in result.get("warnings") or []:
        log.info("  warning: %s", warn)
    if result.get("summary"):
        log.info("  summary: %s", result["summary"])

    return result


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)-5s %(message)s",
                        datefmt="%H:%M:%S")
    if len(sys.argv) < 3:
        print("Usage: python output_qa.py <spec.json> <frames_dir> [probe.json]")
        sys.exit(1)
    spec = json.loads(open(sys.argv[1], encoding="utf-8").read())
    probe = json.loads(open(sys.argv[3], encoding="utf-8").read()) if len(sys.argv) > 3 else {}
    result = qa_output(spec, probe, Path(sys.argv[2]))
    if result:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result and result.get("decision") == "PASS" else 1)
