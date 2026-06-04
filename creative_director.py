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

SYSTEM_PROMPT = (config.PROMPTS_DIR / "creative_director.txt").read_text(encoding="utf-8")

CRITIQUE_PROMPT = """\
You are a creative quality reviewer for short-form viral video specs.

Review the spec below. Make it MORE entertaining, captivating, and viral.

CRITICAL — CHECK IN THIS ORDER:

1. STORYBOARD: Does the storyboard faithfully describe what the screens show?
   Does the spec encode the storyboard accurately? Fix any drift.

2. clip_description AUDIT — most common failure, check carefully:

   a) FORBIDDEN WORDS: Does any clip_description contain these production-speak words?
        slow motion / slow-motion / slo-mo / replay / closeup / close-up
        controversy (appended as a descriptor) / scene / angle / highlights
      If yes → strip them. Rewrite as just "[Event name] [year if helpful]".

   b) SAME-SOURCE RULE: Find all clip_descriptions that reference the same event
      (they will share the first 3 words). They MUST be word-for-word identical.
      Take the shortest version and apply it to ALL screens from that event.
      Example — these three must become identical:
        "Enhanced Games 50m freestyle race"                     ← keep this
        "Enhanced Games 50m freestyle finish controversy"       ← WRONG — fix it
        "Enhanced Games 50m freestyle race full"                ← WRONG — fix it
      Fix: all three become "Enhanced Games 50m freestyle race"

   c) clip_moment quality: For every evidence screen or key story beat, is
      clip_moment set and specific? "swimmer's hand touching the wall" is good.
      "race footage" is not. If clip_moment is missing on a key beat, add it.

   d) REALITY CHECK: Paste each clip_description into YouTube search mentally.
      Would the first result be the actual event footage? If not → null.

3. clip_mode: Is "ambient" used correctly?
   Only valid when the source event is ≤ 60s AND continuous play is stronger.
   If the source is a long broadcast, use null (timestamped) instead.

4. CONSECUTIVE GRADIENTS: Never two gradient screens in a row. Fix any found.

5. CLIP SPREAD: Clips should be distributed across the video, not front-loaded.

6. OTHER:
   - Weak hook (screen 1 should stop the scroll)
   - Voice script that reads screen text (should ADD new information)
   - Factual errors
   - panel: true when clip_description is set, false otherwise
   - No empty strings (use null for subtitle and clip_description)
   - scheme: dark_gold | dark_red | dark_blue | dark_green | dark_purple
   - voice: en-GB-RyanNeural | en-US-GuyNeural | en-US-ChristopherNeural | en-GB-SoniaNeural

Return ONLY the corrected JSON (including the storyboard field).

SPEC:
{spec_json}"""

REVISION_PROMPT = """\
You are the creative director. This short-form video spec FAILED content review.
Revise it to address EVERY point below, then return an improved spec.

ORIGINAL BRIEF:
{brief}

REVIEWER VERDICT: {decision} ({score}/10)

LOW-SCORING DIMENSIONS (raise these):
{weak_dims}

REQUIRED FIXES (address each one):
{fixes}

REVIEWER SUMMARY:
{summary}

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
VALID_VOICES = {
    "en-GB-RyanNeural", "en-US-GuyNeural",
    "en-US-ChristopherNeural", "en-GB-SoniaNeural",
}


def _validate(data: dict) -> list[str]:
    errors = []
    screens = data.get("screens")
    if not isinstance(screens, list):
        return ["missing or invalid 'screens' array"]

    n = len(screens)
    if n < 5 or n > 12:
        errors.append(f"screen count {n} outside 5–12")

    if data.get("scheme") not in VALID_SCHEMES:
        errors.append(f"invalid scheme: {data.get('scheme')}")
    if data.get("voice") not in VALID_VOICES:
        errors.append(f"invalid voice: {data.get('voice')}")
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
    if spec.get("voice") not in VALID_VOICES:
        spec["voice"] = "en-GB-RyanNeural"
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
        log.info("Draft has %d issue(s) → critique pass", len(draft_errors))
    else:
        log.info("Draft passed first-call validation")

    # Call 2: Self-critique
    time.sleep(config.GEMINI_RPM_SLEEP)
    critique_text = CRITIQUE_PROMPT.format(spec_json=json.dumps(draft, indent=2))
    log.info("Calling Gemini (critique)…")
    get_tracker().record_text_call(
        "creative_director_critique",
        prompt_words=len(critique_text.split()),
        response_words=400,
        thinking_tokens=config.THINKING_BUDGET_CRITIQUE,
    )
    spec = draft
    try:
        raw2 = llm.generate(critique_text, thinking_budget=config.THINKING_BUDGET_CRITIQUE,
                            model=config.CREATIVE_MODEL)
        spec = gemini.parse_json(raw2)
    except Exception:
        log.warning("Critique failed — using draft")

    spec = _fixup(spec)
    _force_scheme(spec, brief)

    final_errors = _validate(spec)
    if final_errors:
        log.warning("Final spec has %d issue(s): %s", len(final_errors), final_errors)

    # Extract storyboard before saving — it's saved separately, not passed downstream
    storyboard = spec.pop("storyboard", "") or ""

    if not dry_run:
        # Brief cache stores full spec (without storyboard already popped)
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
    """Regenerate a spec using the content reviewer's feedback.

    Used by the spec-stage review loop: when the reviewer returns REVISE/REJECT,
    its low-scoring dimensions + fixes + summary are fed back to the creative
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
        score=review.get("overall_score", 0),
        weak_dims="\n".join(weak) or "(none flagged individually — see summary)",
        fixes="\n".join(f"- {f}" for f in fixes) or "(see summary)",
        summary=review.get("summary", ""),
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

    revised.pop("storyboard", None)  # not carried downstream
    revised = _fixup(revised)
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
