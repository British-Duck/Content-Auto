"""
Phase A, Step 1 — the News Dispatch v2 spec validation gate.

Runs immediately after the Creative Director and before any expensive stage
(Stage 1.5 review, research, render). Enforces the v2 spec contract. Two outcomes:

  HARD-REJECT  -> raise SpecValidationError; the spec must NOT proceed.
  AUTO-REPAIR  -> fix in place, record the repair, and continue.

Every violation — rejected or repaired — is logged with the field name and the
offending value, so silent failures become loud. Repairs are applied to the spec
in place, so they are observable even when a hard reject follows them.

This module is standalone: it imports nothing from the pipeline and has no side
effects beyond logging and mutating the spec dict it is given.
"""
import logging
import re

log = logging.getLogger(__name__)

# ── Contract constants ───────────────────────────────────────────────────────
SCHEMES = {"dark_red", "dark_gold", "dark_blue", "dark_green", "dark_purple"}
TRANSITIONS = {"cut", "crossfade", "zoom"}
AUDIO_MODES = {"vo", "native"}
CLIP_MODES = {None, "ambient"}

DURATION_MIN, DURATION_MAX = 1.0, 6.0
TEXT_SIZE_MIN, TEXT_SIZE_MAX = 76, 100
SCREEN_MIN, SCREEN_MAX = 4, 12
RUNTIME_MIN, RUNTIME_MAX = 30.0, 50.0
LABEL_MIN_WORDS, LABEL_MAX_WORDS = 2, 4

_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SpecValidationError(Exception):
    """Raised when a spec has one or more HARD-REJECT violations."""

    def __init__(self, violations):
        self.violations = list(violations)
        super().__init__(
            f"{len(self.violations)} hard violation(s): " + "; ".join(self.violations)
        )


def _is_number(x) -> bool:
    # bool is a subclass of int — exclude it so True/False never count as numbers.
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def validate_spec(spec: dict):
    """Validate and auto-repair a v2 spec.

    Returns (spec, repairs) where `repairs` is the list of applied auto-repairs.
    Raises SpecValidationError if any HARD violation remains (after repairs —
    repairs never mask a hard reject). Mutates `spec` in place.
    """
    hard: list[str] = []
    repairs: list[str] = []

    def reject(field, value, msg):
        v = f"{field}={value!r} - {msg}"
        log.error("Spec REJECT — %s", v)
        hard.append(v)

    def repair(field, old, new, msg):
        v = f"{field}: {msg} ({old!r} -> {new!r})"
        log.warning("Spec auto-repair — %s", v)
        repairs.append(v)

    if not isinstance(spec, dict):
        raise SpecValidationError([f"spec is not a JSON object (got {type(spec).__name__})"])

    # ── Top-level ────────────────────────────────────────────────────────────
    for field in ("storyboard", "structure", "voice_script"):
        val = spec.get(field)
        if not (isinstance(val, str) and val.strip()):
            reject(field, val, "must be a non-empty string")

    sid = spec.get("id")
    if not (isinstance(sid, str) and _ID_RE.match(sid)):
        reject("id", sid, "must be kebab-case (lowercase a-z/0-9, hyphen-separated)")

    label = spec.get("label")
    if not (isinstance(label, str) and label.strip()):
        reject("label", label, "must be a non-empty string")
    else:
        n_words = len(label.split())
        if not (LABEL_MIN_WORDS <= n_words <= LABEL_MAX_WORDS):
            reject("label", label, f"must be {LABEL_MIN_WORDS}-{LABEL_MAX_WORDS} words")
        if label != label.upper():
            reject("label", label, "must be upper-case")

    if spec.get("scheme") not in SCHEMES:
        reject("scheme", spec.get("scheme"), f"must be one of {sorted(SCHEMES)}")

    # voice must NOT be present (narrator is fixed in config). Remove + log.
    if "voice" in spec:
        old = spec.pop("voice")
        repair("voice", old, None, "forbidden in v2 (narrator fixed in config) — field removed")

    # ── Screens ──────────────────────────────────────────────────────────────
    screens = spec.get("screens")
    if not isinstance(screens, list):
        reject("screens", screens, "must be an array")
        screens = []
    elif not (SCREEN_MIN <= len(screens) <= SCREEN_MAX):
        reject("screens.length", len(screens), f"count must be {SCREEN_MIN}-{SCREEN_MAX}")

    is_gradient: list = []  # per-screen: True if clip_description is null (None = not a dict)

    for i, s in enumerate(screens):
        if not isinstance(s, dict):
            reject(f"screens[{i}]", s, "must be an object")
            is_gradient.append(None)
            continue

        # Required non-empty strings
        for field in ("text", "reason"):
            val = s.get(field)
            if not (isinstance(val, str) and val.strip()):
                reject(f"screens[{i}].{field}", val, "must be a non-empty string")

        # Empty-string -> null repair on the nullable string fields
        for field in ("subtitle", "clip_description", "clip_moment"):
            if s.get(field) == "":
                s[field] = None
                repair(f"screens[{i}].{field}", "", None, "empty string set to null")

        # duration — clamp to range (repair); non-numeric is a hard reject
        dur = s.get("duration")
        if not _is_number(dur):
            reject(f"screens[{i}].duration", dur, f"must be a number in {DURATION_MIN}-{DURATION_MAX}")
        elif dur < DURATION_MIN or dur > DURATION_MAX:
            new = float(min(max(dur, DURATION_MIN), DURATION_MAX))
            s["duration"] = new
            repair(f"screens[{i}].duration", dur, new, f"clamped to {DURATION_MIN}-{DURATION_MAX}")

        # text_size — clamp to range + coerce int (repair); non-numeric is hard
        ts = s.get("text_size")
        if not _is_number(ts):
            reject(f"screens[{i}].text_size", ts, f"must be an int in {TEXT_SIZE_MIN}-{TEXT_SIZE_MAX}")
        else:
            clamped = int(round(min(max(ts, TEXT_SIZE_MIN), TEXT_SIZE_MAX)))
            if clamped != ts:
                s["text_size"] = clamped
                repair(f"screens[{i}].text_size", ts, clamped, f"clamped to {TEXT_SIZE_MIN}-{TEXT_SIZE_MAX}")

        # transition — first segment forced to "cut" (repair); others must be enum
        tr = s.get("transition")
        if i == 0:
            if tr != "cut":
                s["transition"] = "cut"
                repair(f"screens[{i}].transition", tr, "cut", "first segment forced to cut")
        elif tr not in TRANSITIONS:
            reject(f"screens[{i}].transition", tr, f"must be one of {sorted(TRANSITIONS)}")

        # audio_mode — required enum
        if s.get("audio_mode") not in AUDIO_MODES:
            reject(f"screens[{i}].audio_mode", s.get("audio_mode"), f"must be one of {sorted(AUDIO_MODES)}")

        # clip_mode — null or "ambient"
        if s.get("clip_mode") not in CLIP_MODES:
            reject(f"screens[{i}].clip_mode", s.get("clip_mode"), "must be null or 'ambient'")

        # Type checks on the nullable strings (after empty-string repair)
        sub = s.get("subtitle")
        if sub is not None and not isinstance(sub, str):
            reject(f"screens[{i}].subtitle", sub, "must be a string or null")

        cd = s.get("clip_description")
        cd_is_str = isinstance(cd, str)
        if cd is not None and not cd_is_str:
            reject(f"screens[{i}].clip_description", cd, "must be a string or null")

        cmt = s.get("clip_moment")
        if cmt is not None and not isinstance(cmt, str):
            reject(f"screens[{i}].clip_moment", cmt, "must be a string or null")

        # panel — must be true iff clip_description is a non-null string (repair)
        expected_panel = bool(cd_is_str)
        if s.get("panel") is not expected_panel:
            repair(f"screens[{i}].panel", s.get("panel"), expected_panel,
                   "set to match clip_description presence")
            s["panel"] = expected_panel

        is_gradient.append(cd is None)

    # ── Cross-field ──────────────────────────────────────────────────────────
    # No two consecutive gradient screens.
    for i in range(1, len(is_gradient)):
        if is_gradient[i] and is_gradient[i - 1]:
            reject(f"screens[{i - 1}]+screens[{i}]", "gradient,gradient",
                   "two consecutive gradient screens (both clip_description null)")

    # Total runtime — warn only, never a hard fail.
    durations = [s.get("duration") for s in screens
                 if isinstance(s, dict) and _is_number(s.get("duration"))]
    total = sum(durations)
    if durations and not (RUNTIME_MIN <= total <= RUNTIME_MAX):
        log.warning("Spec runtime %.1fs outside target %.0f-%.0fs (warning only, not a reject)",
                    total, RUNTIME_MIN, RUNTIME_MAX)

    if hard:
        log.error("Spec validation FAILED: %d hard violation(s), %d repair(s) applied",
                  len(hard), len(repairs))
        raise SpecValidationError(hard)

    log.info("Spec validation passed%s",
             f" with {len(repairs)} auto-repair(s)" if repairs else " (no repairs needed)")
    return spec, repairs
