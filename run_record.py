"""
Phase A, Step 2 — structured run record + real-usage cost meter.

One RunRecord per video, created at the start of a run and written to
logs/<run_id>.json at the end AND on abort. It is the object every future stage
writes into, so it is built forward-compatible: the editorial_critic / output_qa
stage blocks and `regenerations` are present-but-null until Steps 3 and 5.

The CostMeter captures real token usage emitted by gemini.py (and the
researcher's direct video calls), attributes each call to the active pipeline
stage, prices it via the config model→price table, and aggregates per stage and
as total_cost. edge-tts / ffmpeg / yt-dlp are free — cost is Gemini only.

Two module globals mirror cost_tracker's pattern so the deep stages can reach the
current run without threading objects through every signature:
  get_cost_meter()  — gemini.py / researcher emit usage here.
  get_active_record() — the researcher records gradient fallbacks here.
Both no-op safely when no run is active (e.g. a stage module run standalone).
"""
import hashlib
import json
import logging
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

import config

log = logging.getLogger(__name__)

_warned_models: set = set()


# ── Pricing ──────────────────────────────────────────────────────────────────

def price_for(model: str) -> dict:
    table = getattr(config, "GEMINI_PRICING", {})
    if model in table:
        return table[model]
    if model not in _warned_models:
        log.warning("No price entry for model %r — using GEMINI_PRICING_DEFAULT", model)
        _warned_models.add(model)
    return getattr(config, "GEMINI_PRICING_DEFAULT", {"input": 0.0, "output": 0.0, "thinking": 0.0})


def _usage_field(usage, key: str) -> int:
    """Read a token count from an SDK usage object OR a plain dict (tests)."""
    if usage is None:
        return 0
    if isinstance(usage, dict):
        return int(usage.get(key) or 0)
    return int(getattr(usage, key, 0) or 0)


# ── Cost meter ───────────────────────────────────────────────────────────────

class CostMeter:
    def __init__(self):
        self.entries: list[dict] = []
        self._stack: list[str] = []

    @contextmanager
    def stage(self, name: str):
        """Attribute every Gemini call made inside this block to `name`."""
        self._stack.append(name)
        try:
            yield
        finally:
            self._stack.pop()

    def record_gemini_usage(self, usage, model: str) -> float:
        inp = _usage_field(usage, "prompt_token_count")
        out = _usage_field(usage, "candidates_token_count")
        think = _usage_field(usage, "thoughts_token_count")
        p = price_for(model)
        cost = (inp * p["input"] + out * p["output"] + think * p["thinking"]) / 1_000_000
        stage = self._stack[-1] if self._stack else "unattributed"
        self.entries.append({
            "stage": stage, "model": model,
            "input_tokens": inp, "output_tokens": out, "thinking_tokens": think,
            "cost_usd": round(cost, 6),
        })
        log.info("Gemini usage [%s/%s]: in=%d out=%d think=%d -> $%.5f",
                 stage, model, inp, out, think, cost)
        return cost

    def cost_by_stage(self) -> dict:
        out: dict = {}
        for e in self.entries:
            out[e["stage"]] = round(out.get(e["stage"], 0.0) + e["cost_usd"], 6)
        return out

    def total_cost(self) -> float:
        return round(sum(e["cost_usd"] for e in self.entries), 6)


# ── Run record ───────────────────────────────────────────────────────────────

def _stage_block() -> dict:
    return {"status": None, "cost": 0.0, "detail": {}}


def _prompt_version(filename: str) -> Optional[str]:
    p = config.PROMPTS_DIR / filename
    if not p.exists():
        return None
    h = hashlib.sha256(p.read_bytes()).hexdigest()[:8]
    return f"{filename}@{h}"


def _slug(brief: Optional[str], input_arg: str) -> str:
    if brief:
        words = [w for w in "".join(c if c.isalnum() else " " for c in brief).split()][:4]
        return "-".join(words).lower() or "run"
    stem = Path(input_arg).stem
    return stem or "run"


class RunRecord:
    """The structured, forward-compatible per-video record. Mutated by thin hooks
    in each stage; written to logs/<run_id>.json on completion or abort."""

    def __init__(self, *, brief: Optional[str], input_arg: str, meter: CostMeter,
                 model: str = None, temperature: float = None, cost_ceiling: float = None):
        self.meter = meter
        ts = datetime.now()
        self._ts = ts
        self.path: Optional[Path] = None
        self.data = {
            "run_id": f"{_slug(brief, input_arg)}_{ts:%Y%m%d_%H%M%S}",
            "timestamp": ts.isoformat(timespec="seconds"),
            "brief": brief,
            "spec_id": None,
            "prompt_versions": {
                "creative_director": _prompt_version("creative_director_v2.md"),
                "editorial_critic": _prompt_version("editorial_critic.md"),
                "output_qa": _prompt_version("output_qa.md"),
            },
            "model": model if model is not None else getattr(config, "CREATIVE_MODEL", None),
            "temperature": temperature if temperature is not None else getattr(config, "GEMINI_TEMPERATURE", None),
            "storyboard": None,
            "structure": None,
            "stages": {
                "creative_director": _stage_block(),
                "validation": _stage_block(),
                "research": _stage_block(),
                "render": _stage_block(),
                "editorial_critic": None,   # populated in Step 5
                "output_qa": None,          # populated in Step 5
            },
            "regenerations": None,          # populated in Step 3
            "total_cost": 0.0,
            "cost_ceiling": cost_ceiling if cost_ceiling is not None else getattr(config, "COST_CEILING_USD", None),
            "cost_exceeded": False,
            "fallback_reasons": [],
            "flags_for_human": [],
            "final_status": "in_progress",
        }

    # — capture the CD's reasoning so it can never be silently dropped —
    def bind_spec(self, spec: dict):
        spec_id = spec.get("id")
        self.data["spec_id"] = spec_id
        if spec_id:
            self.data["run_id"] = f"{spec_id}_{self._ts:%Y%m%d_%H%M%S}"
        self.data["structure"] = spec.get("structure")
        sb = spec.get("storyboard")
        if not sb and spec_id:
            # CD pops storyboard to a sidecar; recover it so the record keeps it.
            sidecar = config.SPECS_DIR / f"{spec_id}_storyboard.md"
            if sidecar.exists():
                sb = sidecar.read_text(encoding="utf-8")
        self.data["storyboard"] = sb

    def stage(self, name: str, status: str, **detail):
        block = self.data["stages"].get(name)
        if block is None:
            self.data["stages"][name] = block = _stage_block()
        block["status"] = status
        if detail:
            block["detail"].update(detail)

    def add_fallback(self, screen_index: int, reason: str):
        entry = {"screen_index": screen_index, "reason": reason}
        self.data["fallback_reasons"].append(entry)
        log.warning("[run-record] FALLBACK screen %s: %s", screen_index, reason)

    def add_flag(self, message: str):
        self.data["flags_for_human"].append(message)
        log.warning("[run-record] FLAG FOR HUMAN: %s", message)

    def set_final_status(self, status: str):
        self.data["final_status"] = status

    # — cost roll-up from the meter —
    def refresh_costs(self):
        by_stage = self.meter.cost_by_stage()
        for name, block in self.data["stages"].items():
            if block is not None:
                block["cost"] = by_stage.get(name, 0.0)
        self.data["total_cost"] = self.meter.total_cost()

    def enforce_ceiling(self) -> bool:
        """Refresh costs and return True if the per-run ceiling has been crossed.
        On crossing: set cost_exceeded and flag for a human (caller aborts)."""
        self.refresh_costs()
        ceiling = self.data["cost_ceiling"]
        if ceiling is not None and self.data["total_cost"] > ceiling:
            self.data["cost_exceeded"] = True
            self.add_flag(
                f"cost ceiling exceeded: ${self.data['total_cost']:.4f} > ${ceiling:.4f} — run aborted"
            )
            return True
        return False

    def write(self) -> Path:
        self.refresh_costs()
        self.path = config.LOGS_DIR / f"{self.data['run_id']}.json"
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info("Run record -> %s (total_cost $%.4f, final_status=%s)",
                 self.path.name, self.data["total_cost"], self.data["final_status"])
        return self.path


# ── Module globals (mirror cost_tracker) ─────────────────────────────────────

_meter: Optional[CostMeter] = None
_active_record: Optional[RunRecord] = None


def get_cost_meter() -> CostMeter:
    global _meter
    if _meter is None:
        _meter = CostMeter()
    return _meter


def reset_cost_meter() -> CostMeter:
    global _meter
    _meter = CostMeter()
    return _meter


def get_active_record() -> Optional[RunRecord]:
    return _active_record


def set_active_record(record: Optional[RunRecord]):
    global _active_record
    _active_record = record
