"""
Stage 1 — Creative Director.

Two-call Gemini chain (generate → self-critique) that turns a text brief
into a validated video spec JSON. Caches specs by brief hash to avoid
regenerating identical briefs.
"""
import hashlib
import json
import logging
import time
from typing import Optional

import config
import gemini
import llm
from cost_tracker import get_tracker

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (config.PROMPTS_DIR / "creative_director_v2.md").read_text(encoding="utf-8")

REVISION_PROMPT = """\
You are the creative director. The EDITORIAL CRITIC asked for a REVISE on this
short-form video spec. Revise it to address EVERY point below, then return an
improved spec.

ORIGINAL BRIEF:
{brief}

EDITORIAL VERDICT: {decision}

BIGGEST WEAKNESS (fix this above all):
{biggest_weakness}

LOW-SCORING DIMENSIONS (raise these):
{weak_dims}

REQUIRED FIXES (address each one):
{fixes}

RULES:
- Keep the same JSON structure and the same "id".
- Fix the specific problems above; do NOT regress what already scores well.
- Do not introduce factual errors or claims the brief does not support.
- Keep scheme = "{scheme}" (do not change it).
- No empty strings (use null for subtitle / clip_description).
- Return ONLY the corrected spec JSON. No markdown, no commentary.

CURRENT SPEC:
{spec_json}"""

VALID_SCHEMES = {"dark_gold", "dark_red", "dark_blue", "dark_green", "dark_purple"}


def _validate(data: dict) -> list[str]:
    errors = []
    screens = data.get("screens")
    if not isinstance(screens, list):
        return ["missing or invalid 'screens' array"]

    n = len(screens)
    if n < 4 or n > 12:
        errors.append(f"screen count {n} outside 4–12")

    if data.get("scheme") not in VALID_SCHEMES:
        errors.append(f"invalid scheme: {data.get('scheme')}")
    for key in ("id", "label", "voice_script"):
        if not data.get(key):
            errors.append(f"missing: {key}")

    durations = []
    for i, s in enumerate(screens):
        d = s.get("duration", 0)
        durations.append(d)
        if not 0.5 <= d <= 8.0:
            errors.append(f"screen {i}: duration {d} outside 0.5–8.0")

    if durations:
        total = sum(durations)
        if not 20 <= total <= 90:
            errors.append(f"total {total:.1f}s outside 20–90s")

    return errors


_FORBIDDEN_DESC_WORDS = frozenset({
    "slow", "motion", "slow-motion", "slowmotion", "slo-mo",
    "replay", "closeup", "close-up", "angle", "scene", "highlights",
})


def _simplify_clip_description(desc: str) -> str:
    """Strip production-speak words that can't appear in a real YouTube title."""
    words = desc.split()
    cleaned = [w for w in words if w.lower().rstrip(".,") not in _FORBIDDEN_DESC_WORDS]
    if len(cleaned) >= 3:
        result = " ".join(cleaned).strip()
        if result != desc:
            log.info("Simplified clip_description: %r → %r", desc, result)
        return result
    return desc


def _normalize_clip_descriptions(screens: list) -> None:
    """
    If multiple screens reference the same source video (same 3-word prefix),
    normalise them all to the shortest query in the group.
    """
    from collections import defaultdict
    groups = defaultdict(list)
    for i, s in enumerate(screens):
        desc = s.get("clip_description")
        if not desc:
            continue
        prefix = " ".join(desc.lower().split()[:3])
        groups[prefix].append((i, s, desc))

    for prefix, items in groups.items():
        if len(items) <= 1:
            continue
        descs = [d for _, _, d in items]
        if len(set(descs)) == 1:
            continue  # already identical
        shortest = min(descs, key=lambda x: len(x.split()))
        for i, s, old_desc in items:
            if old_desc != shortest:
                log.info("Normalised clip_description screen %d: %r → %r", i + 1, old_desc, shortest)
                s["clip_description"] = shortest


def _fixup(spec: dict) -> dict:
    if spec.get("scheme") not in VALID_SCHEMES:
        spec["scheme"] = "dark_gold"
    # v2: the CD no longer emits a `voice` field; the narrator is fixed in config.
    screens = spec.get("screens", [])
    for s in screens:
        desc = s.get("clip_description")
        if desc == "":
            desc = None
        if desc:
            desc = _simplify_clip_description(desc)
        s["clip_description"] = desc
        s["panel"] = bool(desc)
        if s.get("subtitle") == "":
            s["subtitle"] = None
        if s.get("clip_mode") not in (None, "ambient"):
            s["clip_mode"] = None
        if s.get("clip_moment") == "":
            s["clip_moment"] = None
    _normalize_clip_descriptions(screens)
    return spec


def _scheme_from_brief(brief: str) -> Optional[str]:
    """Extract an explicitly requested scheme from the brief (e.g. 'Use dark_purple').

    Returns the scheme only if exactly one valid scheme token appears in the brief,
    so an ambiguous brief mentioning several never forces the wrong one.
    """
    low = brief.lower()
    found = [s for s in VALID_SCHEMES if s in low]
    return found[0] if len(found) == 1 else None


def _force_scheme(spec: dict, brief: str) -> None:
    """If the brief names a scheme, make the spec honour it (overriding the LLM)."""
    forced = _scheme_from_brief(brief)
    if forced and spec.get("scheme") != forced:
        log.info("Honouring brief scheme: %s → %s", spec.get("scheme"), forced)
        spec["scheme"] = forced


def _brief_hash(brief: str) -> str:
    return hashlib.sha256(brief.strip().lower().encode()).hexdigest()[:16]


def generate_spec(brief: str, dry_run: bool = False, force: bool = False) -> Optional[dict]:
    log.info("Stage 1: Creative Director")
    log.info("Brief: %s", brief[:120])

    if not force:
        cache_file = config.BRIEF_CACHE_DIR / f"{_brief_hash(brief)}.json"
        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                # Re-emit storyboard from sidecar file if present
                sb_path = config.SPECS_DIR / f"{cached.get('id', '_')}_storyboard.md"
                if sb_path.exists():
                    storyboard = sb_path.read_text(encoding="utf-8")
                    log.info("\n── STORYBOARD (cached) ─────────────────────────────────\n%s\n────────────────────────────────────────────────────────", storyboard)
                log.info("Brief cache hit → reusing spec '%s'", cached.get("id", "?"))
                get_tracker().record_cache(True, "brief")
                _force_scheme(cached, brief)
                return cached
            except (json.JSONDecodeError, KeyError):
                pass
        get_tracker().record_cache(False, "brief")

    # Call 1: Generate
    prompt_text = f"{SYSTEM_PROMPT}\n\n---\n\nBRIEF:\n{brief}"
    log.info("Calling Gemini (generate)…")
    get_tracker().record_text_call(
        "creative_director_generate",
        prompt_words=len(prompt_text.split()),
        response_words=400,
        thinking_tokens=config.THINKING_BUDGET_CREATIVE,
    )
    try:
        raw = llm.generate(prompt_text, thinking_budget=config.THINKING_BUDGET_CREATIVE,
                           model=config.CREATIVE_MODEL)
    except Exception as e:
        log.error("Generate failed: %s", e)
        return None

    try:
        draft = gemini.parse_json(raw)
    except (json.JSONDecodeError, ValueError) as e:
        log.error("JSON parse failed: %s", e)
        log.debug("Raw: %s", raw[:2000])
        return None

    draft_errors = _validate(draft)
    if draft_errors:
        log.info("Draft has %d issue(s) (fixup + Step-1 gate handle these): %s",
                 len(draft_errors), draft_errors)
    else:
        log.info("Draft passed first-call validation")

    # v2: single-pass generation. The old v1 self-critique pass is retired — it
    # encoded the v1 schema (a `voice` field, old enums) and would corrupt a v2 spec.
    # Mechanical repairs are now the Step-1 validator's job; editorial review is the
    # Step-5 critic's. The v2 system prompt carries its own FINAL CHECK.
    spec = _fixup(draft)
    _force_scheme(spec, brief)

    final_errors = _validate(spec)
    if final_errors:
        log.warning("Final spec has %d issue(s): %s", len(final_errors), final_errors)

    # v2: KEEP storyboard on the spec (do NOT pop). The Step-1 gate requires it and
    # the run record captures it. Still mirror it to a sidecar .md for easy reading.
    storyboard = spec.get("storyboard", "") or ""

    if not dry_run:
        # Brief cache stores the full spec, storyboard included.
        brief_cache = config.BRIEF_CACHE_DIR / f"{_brief_hash(brief)}.json"
        brief_cache.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")

        out = config.SPECS_DIR / f"{spec['id']}.json"
        out.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info("Spec → %s", out)

        if storyboard:
            sb_path = config.SPECS_DIR / f"{spec['id']}_storyboard.md"
            sb_path.write_text(storyboard, encoding="utf-8")
            log.info("Storyboard → %s", sb_path)

    if storyboard:
        log.info("\n── STORYBOARD ──────────────────────────────────────────\n%s\n────────────────────────────────────────────────────────", storyboard)

    total = sum(s["duration"] for s in spec["screens"])
    clips = sum(1 for s in spec["screens"] if s.get("clip_description"))
    ambient = sum(1 for s in spec["screens"] if s.get("clip_mode") == "ambient")
    log.info("%s — %d screens, %.1fs, scheme=%s, %d clips (%d ambient)",
             spec["id"], len(spec["screens"]), total, spec["scheme"], clips, ambient)
    return spec


def revise_spec(brief: str, spec: dict, review: dict) -> Optional[dict]:
    """Regenerate a spec using the editorial critic's feedback.

    Used by the spec-stage review loop: when the critic returns REVISE, its
    biggest_weakness + low-scoring dimensions + fixes are fed back to the creative
    director so it can try again with that extra information. Returns the revised
    spec (validated, scheme-honoured), or None on failure so the caller can fall
    back to the best spec so far.
    """
    scores = review.get("scores", {}) or {}
    weak = [
        f"- {dim} ({d.get('score')}/10): {d.get('reason', '')}"
        for dim, d in scores.items()
        if isinstance(d, dict) and isinstance(d.get("score"), (int, float)) and d["score"] < 8
    ]
    fixes = review.get("fixes") or []
    prompt = REVISION_PROMPT.format(
        brief=brief,
        decision=review.get("decision", "REVISE"),
        biggest_weakness=review.get("biggest_weakness", "") or "(see fixes)",
        weak_dims="\n".join(weak) or "(none flagged individually — see biggest weakness)",
        fixes="\n".join(f"- {f}" for f in fixes) or "(see biggest weakness)",
        scheme=spec.get("scheme"),
        spec_json=json.dumps(spec, indent=2, ensure_ascii=False),
    )

    time.sleep(config.GEMINI_RPM_SLEEP)
    log.info("Creative Director: revising spec from reviewer feedback…")
    get_tracker().record_text_call(
        "creative_director_revise",
        prompt_words=len(prompt.split()),
        response_words=400,
        thinking_tokens=config.THINKING_BUDGET_CREATIVE,
    )
    try:
        raw = llm.generate(prompt, thinking_budget=config.THINKING_BUDGET_CREATIVE,
                           model=config.CREATIVE_MODEL)
        revised = gemini.parse_json(raw)
    except Exception as e:
        log.error("Revision call failed: %s", e)
        return None

    revised = _fixup(revised)        # v2: keep storyboard on the revised spec too
    _force_scheme(revised, brief)

    errs = _validate(revised)
    if errs:
        log.warning("Revised spec still has %d issue(s): %s", len(errs), errs)

    # Persist the revised spec + brief cache so the improved version is reused.
    try:
        brief_cache = config.BRIEF_CACHE_DIR / f"{_brief_hash(brief)}.json"
        brief_cache.write_text(json.dumps(revised, indent=2, ensure_ascii=False), encoding="utf-8")
        out = config.SPECS_DIR / f"{revised['id']}.json"
        out.write_text(json.dumps(revised, indent=2, ensure_ascii=False), encoding="utf-8")
    except (KeyError, OSError) as e:
        log.warning("Could not persist revised spec: %s", e)

    return revised


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S")
    if len(sys.argv) < 2:
        print("Usage: python creative_director.py \"<brief>\"")
        sys.exit(1)
    result = generate_spec(" ".join(sys.argv[1:]))
    if result:
        print(json.dumps(result, indent=2))
    sys.exit(0 if result else 1)
