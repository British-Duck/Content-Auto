"""
Shared configuration for the orchestrated content pipeline.
All tuneable constants live here — no magic numbers scattered across modules.
"""
from pathlib import Path

# ── Directories ──────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent
SPECS_DIR = ROOT / "specs"
OUTPUT_DIR = ROOT / "output"
CACHE_DIR = ROOT / ".clip_cache"
TIMESTAMP_CACHE_DIR = ROOT / "cache" / "timestamps"
LOGS_DIR = ROOT / "logs"
PROMPTS_DIR = ROOT / "prompts"

SEARCH_CACHE_DIR = ROOT / "cache" / "searches"
METADATA_CACHE_DIR = ROOT / "cache" / "metadata"
BRIEF_CACHE_DIR = ROOT / "cache" / "briefs"

for _d in (SPECS_DIR, OUTPUT_DIR, CACHE_DIR, TIMESTAMP_CACHE_DIR, LOGS_DIR,
           SEARCH_CACHE_DIR, METADATA_CACHE_DIR, BRIEF_CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ── Gemini ────────────────────────────────────────────────────────────────────

GEMINI_MODEL = "gemini-2.5-flash"
# Per-stage model selection. Each may be a Gemini ("gemini-*") or Claude
# ("claude-*") id; llm.py routes to the right provider. The researcher always
# uses GEMINI_MODEL (video analysis is Gemini-only).
#   To upgrade the creative-director "brain": set CREATIVE_MODEL.
#   To run the Anthropic plan (Sonnet writes, Opus reviews), set:
#     CREATIVE_MODEL = "claude-sonnet-4-6"
#     REVIEWER_MODEL = "claude-opus-4-8"
#   and add ANTHROPIC_API_KEY to .env. Falls back to GEMINI_MODEL if absent.
CREATIVE_MODEL = "gemini-2.5-pro"      # creative director (storyboard + spec)
REVIEWER_MODEL = "gemini-2.5-pro"      # content reviewer (quality scoring)
GEMINI_TEMPERATURE = 1.0   # creative director benefits from higher temp
GEMINI_RPM_SLEEP = 2.5     # seconds to sleep between API calls (free tier)

# ── Cost: model → price table (USD per 1M tokens, pay-as-you-go) ──────────────
# The ONE place prices live. Update here when Google changes pricing — never
# hardcode a rate inline. Verify against https://ai.google.dev/pricing.
# `thinking` is the price applied to thought tokens (usage.thoughts_token_count).
GEMINI_PRICING = {
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60, "thinking": 3.50},
    "gemini-2.5-pro":   {"input": 1.25, "output": 10.00, "thinking": 10.00},
}
# Fallback when a model id is not in the table (logged once when used).
GEMINI_PRICING_DEFAULT = {"input": 0.15, "output": 0.60, "thinking": 3.50}

# Per-run cost ceiling (USD, same unit as the price table). After each PAID stage
# the run aborts if cumulative Gemini cost crosses this. Set with comfortable
# headroom over the per-video target (~$0.10–0.20 with the Pro brain).
COST_CEILING_USD = 0.75

# ── Pipeline defaults ─────────────────────────────────────────────────────────

PROD_WIDTH = 1080
PROD_HEIGHT = 1920
PROD_FPS = 30
PROD_CRF = 18
PROD_PRESET = "medium"

PREVIEW_WIDTH = 540
PREVIEW_HEIGHT = 960
PREVIEW_FPS = 24
PREVIEW_CRF = 28
PREVIEW_PRESET = "ultrafast"

CROSSFADE_DURATION = 0.3   # seconds; 0 = hard cuts

# ── Music bed ─────────────────────────────────────────────────────────────────
# Drop royalty-free instrumental tracks into MUSIC_DIR named by MOOD (e.g.
# tense.mp3). The scheme → mood map picks the bed; if no matching file (or
# default.*) exists, the render falls back to voiceover-only — never an error.
# See music/README.md for where to get free tracks and how to name them.
MUSIC_DIR = ROOT / "music"
MUSIC_ENABLED = True
MUSIC_BED_GAIN_DB = -9      # baseline music level (sits under the voiceover)
MUSIC_DUCK_RATIO = 8        # sidechain compression ratio while the VO speaks
MUSIC_FADE = 1.0            # seconds fade in / out
MUSIC_BY_SCHEME = {
    "dark_red":    "tense",      # scandal / fraud
    "dark_gold":   "news",       # urgent / breaking
    "dark_blue":   "epic",       # survival / triumph
    "dark_green":  "uplifting",
    "dark_purple": "dramatic",
}
MUSIC_DEFAULT = "default"   # fallback filename stem when the mood file is absent

# Text layout style (a spec may override per-video via a top-level "layout" field):
#   "B" — every screen: headline top, subtitle bottom (clip screens get scrims).
#   "C" — clip screens: headline CENTRED over footage (soft full-frame dim);
#         stat/story/monologue (gradient) screens: top/bottom.
LAYOUT_STYLE = "B"

# ── Voice options ─────────────────────────────────────────────────────────────

VOICES = {
    "authoritative": "en-GB-RyanNeural",
    "news":          "en-US-GuyNeural",
    "energetic":     "en-US-ChristopherNeural",
    "calm":          "en-GB-SoniaNeural",
}
DEFAULT_VOICE = VOICES["authoritative"]

# ── Researcher ────────────────────────────────────────────────────────────────

RESEARCHER_MAX_SEARCH_RESULTS = 5
RESEARCHER_MIN_CLIP_DURATION = 1.5   # seconds — reject clips shorter than this
RESEARCHER_MAX_CLIP_DURATION = 8.0   # seconds — pad/trim if Gemini returns wider range
RESEARCHER_CONFIDENCE_THRESHOLD = "medium"  # reject "low" and "none"
RESEARCHER_GEMINI_TIMEOUT = 120      # seconds — kill generate_content if it hangs
RESEARCHER_UPLOAD_MAX_MB = 20        # recompress if trimmed file exceeds this
RESEARCHER_TRIM_DIR = ROOT / ".trim_cache"
RESEARCHER_TRIM_DIR.mkdir(parents=True, exist_ok=True)

# ── Spec review loop (Stage 1.5) ──────────────────────────────────────────────
# After the creative director writes a spec, the content reviewer scores it. If
# it flags a FACTUAL ERROR (factual_accuracy below the threshold), the reviewer's
# feedback is passed back to the creative director to fix — at most this many
# times. Non-factual issues (weak hook, pacing) are logged but NOT retried.
SPEC_REVIEW_MAX_RETRIES = 2          # hard cap: at most 2 regenerations (3 CD calls total).
                                     # After the cap, a still-failing spec is flagged for a
                                     # human and NOT rendered (never looped, never shipped).
SPEC_REVIEW_FACTUAL_THRESHOLD = 8    # retry only if factual_accuracy < this

# ── Quality gate ──────────────────────────────────────────────────────────────

QUALITY_DURATION_TOLERANCE = 0.5   # seconds
QUALITY_SSIM_FLOOR = 0.85

# ── Thinking budgets (0 = disabled, saves thinking-token cost) ───────────────

# Thinking budgets: None = uncapped (model decides), 0 = disabled.
# Uncapped gives best quality. Set to integers to cap and save money.
# See cost_tuning_guide.txt for per-stage tuning.
THINKING_BUDGET_CREATIVE = None    # uncapped — creative quality is priority
THINKING_BUDGET_CRITIQUE = None    # uncapped — needs to catch real issues
THINKING_BUDGET_TIMESTAMPS = 1024  # capped — visual matching, light reasoning enough
THINKING_BUDGET_REVIEWER = None    # uncapped — must be genuinely critical

# ── Researcher optimization ──────────────────────────────────────────────────

RESEARCHER_MAX_DOWNLOAD_CANDIDATES = 3  # download top-ranked candidates
