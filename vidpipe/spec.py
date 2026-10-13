import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)


@dataclass
class Clip:
    url: str
    start: float
    end: Optional[float] = None  # None = ambient (play for screen duration from start)

    @property
    def ambient(self) -> bool:
        return self.end is None

    @property
    def duration(self) -> Optional[float]:
        if self.end is None:
            return None
        return self.end - self.start


@dataclass
class Screen:
    text: str
    duration: float
    text_size: int = 64
    panel: bool = False
    subtitle: Optional[str] = None
    clip: Optional[Clip] = None
    clip_description: Optional[str] = None  # used by researcher agent to find footage
    still: Optional[str] = None  # Ken Burns fallback: path to a representative still image


@dataclass
class HeroClip:
    """A single source carrying a whole single_clip video: one continuous window
    plus the key-moment timestamp the payoff lands on. Resolved by the researcher
    and written onto the spec before render."""
    url: str
    window_start: float
    window_end: float
    key_moment_ts: Optional[float] = None
    subject_x: float = 0.5            # 0..1 horizontal focus for the vertical crop
    source_duration: Optional[float] = None

    @property
    def window_length(self) -> float:
        return max(0.0, self.window_end - self.window_start)


@dataclass
class VideoSpec:
    id: str
    label: str
    scheme: str
    screens: List[Screen]
    voice: Optional[str] = None
    voice_script: Optional[str] = None
    layout: Optional[str] = None  # "B" | "C"; None = use config.LAYOUT_STYLE
    format: str = "montage"       # "montage" (default) | "single_clip"
    hero_clip: Optional[HeroClip] = None  # set only for a resolved single_clip spec


def load_spec(path: Path) -> VideoSpec:
    log.info("Loading spec: %s", path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    for key in ("id", "label", "scheme", "screens"):
        if key not in data:
            raise ValueError(f"Spec missing required field: {key}")

    if not data["screens"]:
        raise ValueError("Spec must have at least one screen")

    screens: List[Screen] = []
    for i, s in enumerate(data["screens"]):
        if "text" not in s:
            raise ValueError(f"Screen {i} missing 'text'")
        if "duration" not in s:
            raise ValueError(f"Screen {i} missing 'duration'")

        clip = None
        if "clip" in s and s["clip"]:
            c = s["clip"]
            if "url" not in c:
                log.warning("Screen %d clip has no url — skipping clip", i)
            elif "start" not in c:
                log.warning("Screen %d clip missing start — skipping clip", i)
            else:
                raw_end = c.get("end")
                end = float(raw_end) if raw_end is not None else None
                clip = Clip(url=c["url"], start=float(c["start"]), end=end)
                if clip.end is not None and clip.duration <= 0:
                    log.warning("Screen %d clip has non-positive duration — skipping", i)
                    clip = None

        still = s.get("still")
        if still and not Path(still).exists():
            log.warning("Screen %d still image not found (%s) — ignoring", i, still)
            still = None

        screens.append(Screen(
            text=s["text"],
            duration=float(s["duration"]),
            text_size=s.get("text_size", 64),
            panel=s.get("panel", False),
            subtitle=s.get("subtitle"),
            clip=clip,
            clip_description=s.get("clip_description"),
            still=still,
        ))

    # Single-clip event format (additive — montage specs omit these and are
    # unaffected). A single_clip spec only takes the dedicated render path once the
    # researcher has resolved a hero_clip window; otherwise it renders as montage.
    fmt = data.get("format") or "montage"
    hero_clip = None
    hc = data.get("hero_clip")
    if fmt == "single_clip" and isinstance(hc, dict) and hc.get("url"):
        try:
            hero_clip = HeroClip(
                url=hc["url"],
                window_start=float(hc["window_start"]),
                window_end=float(hc["window_end"]),
                key_moment_ts=(float(hc["key_moment_ts"])
                               if hc.get("key_moment_ts") is not None else None),
                subject_x=float(hc.get("subject_x", 0.5)),
                source_duration=(float(hc["source_duration"])
                                 if hc.get("source_duration") is not None else None),
            )
            if hero_clip.window_length <= 0:
                log.warning("hero_clip window non-positive — ignoring, will render as montage")
                hero_clip = None
        except (KeyError, TypeError, ValueError) as e:
            log.warning("hero_clip malformed (%s) — ignoring, will render as montage", e)
            hero_clip = None

    spec = VideoSpec(
        id=data["id"],
        label=data["label"],
        scheme=data["scheme"],
        screens=screens,
        voice=data.get("voice"),
        voice_script=data.get("voice_script"),
        layout=data.get("layout"),
        format=fmt,
        hero_clip=hero_clip,
    )

    total_dur = sum(sc.duration for sc in spec.screens)
    clip_count = sum(1 for sc in spec.screens if sc.clip)
    log.info("Loaded: %s — %d screens, %.1fs total, %d clips",
             spec.id, len(spec.screens), total_dur, clip_count)
    return spec
