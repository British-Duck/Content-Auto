"""Provider router. Dispatches a generate() call to Gemini or Claude based on
the model id prefix, so each pipeline stage can pick its own model via config
(CREATIVE_MODEL, EDITORIAL_CRITIC_MODEL) with no code changes.

  "gemini-*"  -> gemini.generate   (also the researcher's video analysis)
  "claude-*"  -> claude.generate   (creative director / reviewer, optional)

If a claude-* model is selected but the SDK or ANTHROPIC_API_KEY is missing, the
call falls back to GEMINI_MODEL so a half-configured switch never breaks a run.
"""
import logging

import config
import gemini

log = logging.getLogger(__name__)


def generate(contents: str, *, temperature: float = config.GEMINI_TEMPERATURE,
             json_mode: bool = True, thinking_budget=None, model: str = None) -> str:
    model = model or config.GEMINI_MODEL

    if model.startswith("claude"):
        try:
            import claude
            return claude.generate(contents, json_mode=json_mode,
                                   thinking_budget=thinking_budget, model=model)
        except Exception as e:
            log.warning("Claude model '%s' unavailable — falling back to %s (%s)",
                        model, config.GEMINI_MODEL, e)
            return gemini.generate(contents, temperature=temperature, json_mode=json_mode,
                                   thinking_budget=thinking_budget, model=config.GEMINI_MODEL)

    return gemini.generate(contents, temperature=temperature, json_mode=json_mode,
                           thinking_budget=thinking_budget, model=model)
