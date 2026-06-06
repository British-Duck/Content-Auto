"""Shared Gemini API client and utilities."""
import json
import os
import logging
from pathlib import Path

from dotenv import load_dotenv

import config

load_dotenv()

log = logging.getLogger(__name__)

_client = None


def get_client():
    global _client
    if _client is None:
        from google import genai
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set — check .env file")
        _client = genai.Client(api_key=api_key)
    return _client


def generate(contents: str, *, temperature: float = config.GEMINI_TEMPERATURE,
             json_mode: bool = True, thinking_budget: int = None,
             model: str = None) -> str:
    from google.genai import types
    gen_config = types.GenerateContentConfig(
        temperature=temperature,
        response_mime_type="application/json" if json_mode else "text/plain",
    )
    if thinking_budget is not None:
        gen_config.thinking_config = types.ThinkingConfig(thinking_budget=thinking_budget)
    used_model = model or config.GEMINI_MODEL
    response = get_client().models.generate_content(
        model=used_model,
        contents=contents,
        config=gen_config,
    )
    _emit_usage(response, used_model)
    return response.text


def generate_vision(prompt: str, image_paths: list, *, temperature: float = 0.2,
                    json_mode: bool = True, thinking_budget: int = None,
                    model: str = None) -> str:
    """Multimodal generate: a text prompt plus one or more local images.

    Used by Output-QA to judge the rendered video from its per-segment frames.
    Images are sent inline as JPEG bytes, in the order given. Usage is emitted to
    the run cost meter exactly like generate(), so wrap the call in the caller's
    meter.stage(...) to attribute the cost."""
    from google.genai import types

    parts = [prompt]
    for p in image_paths:
        data = Path(p).read_bytes()
        parts.append(types.Part.from_bytes(data=data, mime_type="image/jpeg"))

    gen_config = types.GenerateContentConfig(
        temperature=temperature,
        response_mime_type="application/json" if json_mode else "text/plain",
    )
    if thinking_budget is not None:
        gen_config.thinking_config = types.ThinkingConfig(thinking_budget=thinking_budget)
    used_model = model or config.GEMINI_MODEL
    response = get_client().models.generate_content(
        model=used_model,
        contents=parts,
        config=gen_config,
    )
    _emit_usage(response, used_model)
    return response.text


def _emit_usage(response, model: str) -> None:
    """Forward real token usage to the run cost meter, attributed to the active
    stage. Best-effort: never let cost accounting break a generation."""
    try:
        from run_record import get_cost_meter
        get_cost_meter().record_gemini_usage(getattr(response, "usage_metadata", None), model)
    except Exception as e:
        log.debug("usage capture skipped: %s", e)


def parse_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        first_nl = text.index("\n")
        last_fence = text.rfind("```")
        text = text[first_nl + 1:last_fence].strip()
    return json.loads(text)
