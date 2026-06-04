"""Anthropic Claude client — a drop-in alternative to gemini.generate() for the
creative-director and content-reviewer stages. Reached only via llm.py when a
stage's configured model is a "claude-*" id; the researcher stays on Gemini
because it does native video analysis Claude can't do.

Dormant by default: nothing here runs unless config.CREATIVE_MODEL /
REVIEWER_MODEL is set to a claude-* id AND ANTHROPIC_API_KEY is present.
"""
import logging
import os

from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

_client = None

# The creative director (generate) and reviewer both build their prompt as
# "<big static system prompt>\n\n---\n\n<per-request payload>". Splitting on this
# marker lets us cache the large unchanging prefix with Anthropic prompt caching
# (~10% cost on re-reads) and only pay full price on the volatile tail.
_PREFIX_SEP = "\n\n---\n\n"
_JSON_NUDGE = "\n\nRespond with ONLY the JSON object — no prose, no markdown code fences."


def _get_client():
    global _client
    if _client is None:
        import anthropic  # lazy import so the pipeline runs without the SDK installed
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY not set — add it to .env to use Claude models")
        _client = anthropic.Anthropic()
    return _client


def generate(contents: str, *, json_mode: bool = True,
             thinking_budget=None, model: str = None) -> str:
    """Mirror of gemini.generate(). temperature is intentionally ignored —
    Opus 4.x rejects sampling params; adaptive thinking replaces budget_tokens."""
    client = _get_client()

    head, sep, tail = contents.partition(_PREFIX_SEP)
    if sep:
        content_blocks = [
            {"type": "text", "text": head, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": sep + tail + (_JSON_NUDGE if json_mode else "")},
        ]
    else:
        content_blocks = [{"type": "text", "text": contents + (_JSON_NUDGE if json_mode else "")}]

    msg = client.messages.create(
        model=model or "claude-sonnet-4-6",
        max_tokens=16000,
        thinking={"type": "adaptive"},  # 4.x: adaptive replaces budget_tokens
        messages=[{"role": "user", "content": content_blocks}],
    )

    text = "".join(b.text for b in msg.content if b.type == "text")
    u = getattr(msg, "usage", None)
    if u is not None:
        log.info("Claude %s: in=%s cache_read=%s out=%s", model,
                 getattr(u, "input_tokens", "?"),
                 getattr(u, "cache_read_input_tokens", 0),
                 getattr(u, "output_tokens", "?"))
    return text
