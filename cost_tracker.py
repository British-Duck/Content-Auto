"""
Cost tracker — estimates and logs API spend per pipeline run.
Tracks Gemini calls, video uploads, downloads, and cache hits/misses.
Writes summary to logs/cost_estimate.json after each run.
"""
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import config

log = logging.getLogger(__name__)

# Gemini 2.5 Flash pricing (USD per 1M tokens, pay-as-you-go)
INPUT_PRICE = 0.15
OUTPUT_PRICE = 0.60
THINKING_PRICE = 3.50
# Video: billed as input tokens (~263 tokens per second at 1 fps)
TOKENS_PER_VIDEO_SECOND = 263
TOKENS_PER_WORD = 1.3


@dataclass
class CallRecord:
    stage: str
    call_type: str
    est_input_tokens: int = 0
    est_output_tokens: int = 0
    est_thinking_tokens: int = 0
    video_seconds: float = 0.0
    cache_hit: bool = False
    timestamp: float = field(default_factory=time.time)


class CostTracker:
    def __init__(self):
        self.calls: list[CallRecord] = []
        self.downloads: list[dict] = []
        self.cache_hits = 0
        self.cache_misses = 0
        self._start = time.time()

    def record_text_call(self, stage: str, prompt_words: int, response_words: int = 200,
                         thinking_tokens: int = 0, cache_hit: bool = False):
        self.calls.append(CallRecord(
            stage=stage,
            call_type="text",
            est_input_tokens=int(prompt_words * TOKENS_PER_WORD),
            est_output_tokens=int(response_words * TOKENS_PER_WORD),
            est_thinking_tokens=thinking_tokens or 0,
            cache_hit=cache_hit,
        ))

    def record_video_call(self, stage: str, video_seconds: float, prompt_words: int,
                          response_words: int = 50, thinking_tokens: int = 0,
                          cache_hit: bool = False):
        video_tokens = int(video_seconds * TOKENS_PER_VIDEO_SECOND)
        self.calls.append(CallRecord(
            stage=stage,
            call_type="video",
            est_input_tokens=int(prompt_words * TOKENS_PER_WORD) + video_tokens,
            est_output_tokens=int(response_words * TOKENS_PER_WORD),
            est_thinking_tokens=thinking_tokens or 0,
            video_seconds=video_seconds,
            cache_hit=cache_hit,
        ))

    def record_download(self, url: str, cached: bool):
        self.downloads.append({"url": url[:80], "cached": cached})
        if cached:
            self.cache_hits += 1
        else:
            self.cache_misses += 1

    def record_cache(self, hit: bool, label: str = ""):
        if hit:
            self.cache_hits += 1
        else:
            self.cache_misses += 1

    def estimate_cost(self) -> float:
        total = 0.0
        for c in self.calls:
            if c.cache_hit:
                continue
            total += c.est_input_tokens * INPUT_PRICE / 1_000_000
            total += c.est_output_tokens * OUTPUT_PRICE / 1_000_000
            total += c.est_thinking_tokens * THINKING_PRICE / 1_000_000
        return total

    def summary(self) -> dict:
        text_calls = [c for c in self.calls if c.call_type == "text" and not c.cache_hit]
        video_calls = [c for c in self.calls if c.call_type == "video" and not c.cache_hit]
        cached_calls = [c for c in self.calls if c.cache_hit]

        total_input = sum(c.est_input_tokens for c in self.calls if not c.cache_hit)
        total_output = sum(c.est_output_tokens for c in self.calls if not c.cache_hit)
        total_thinking = sum(c.est_thinking_tokens for c in self.calls if not c.cache_hit)
        total_video_sec = sum(c.video_seconds for c in self.calls if not c.cache_hit)

        return {
            "gemini_text_calls": len(text_calls),
            "gemini_video_calls": len(video_calls),
            "gemini_cached_calls": len(cached_calls),
            "est_input_tokens": total_input,
            "est_output_tokens": total_output,
            "est_thinking_tokens": total_thinking,
            "total_video_seconds_uploaded": round(total_video_sec, 1),
            "videos_downloaded": len(self.downloads),
            "videos_from_cache": sum(1 for d in self.downloads if d["cached"]),
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "est_cost_usd": round(self.estimate_cost(), 6),
            "elapsed_seconds": round(time.time() - self._start, 1),
            "calls_detail": [
                {
                    "stage": c.stage,
                    "type": c.call_type,
                    "input_tokens": c.est_input_tokens,
                    "output_tokens": c.est_output_tokens,
                    "thinking_tokens": c.est_thinking_tokens,
                    "video_sec": c.video_seconds,
                    "cached": c.cache_hit,
                }
                for c in self.calls
            ],
        }

    def save(self, path: Optional[Path] = None):
        if path is None:
            path = config.LOGS_DIR / "cost_estimate.json"
        data = self.summary()
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        log.info("Cost estimate: $%.4f (%d text + %d video calls, %d cache hits)",
                 data["est_cost_usd"],
                 data["gemini_text_calls"],
                 data["gemini_video_calls"],
                 data["cache_hits"])

    def print_summary(self):
        s = self.summary()
        print()
        print("=" * 50)
        print("COST ESTIMATE")
        print("=" * 50)
        print(f"  Gemini text calls:   {s['gemini_text_calls']}")
        print(f"  Gemini video calls:  {s['gemini_video_calls']}")
        print(f"  Cached (skipped):    {s['gemini_cached_calls']}")
        print(f"  Est. input tokens:   {s['est_input_tokens']:,}")
        print(f"  Est. output tokens:  {s['est_output_tokens']:,}")
        print(f"  Est. thinking tokens:{s['est_thinking_tokens']:,}")
        print(f"  Video uploaded:      {s['total_video_seconds_uploaded']:.0f}s")
        print(f"  Downloads:           {s['videos_downloaded']} ({s['videos_from_cache']} cached)")
        print(f"  Cache hits/misses:   {s['cache_hits']}/{s['cache_misses']}")
        print(f"  Estimated cost:      ${s['est_cost_usd']:.4f}")
        print("=" * 50)


_tracker: Optional[CostTracker] = None


def get_tracker() -> CostTracker:
    global _tracker
    if _tracker is None:
        _tracker = CostTracker()
    return _tracker


def reset_tracker() -> CostTracker:
    global _tracker
    _tracker = CostTracker()
    return _tracker
