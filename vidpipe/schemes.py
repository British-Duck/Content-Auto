from dataclasses import dataclass
from typing import Dict, Tuple

Color = Tuple[int, int, int]
RGBA = Tuple[int, int, int, int]


@dataclass(frozen=True)
class ColorScheme:
    name: str
    bg_top: Color
    bg_bottom: Color
    accent: Color
    text: Color
    muted: Color
    gold: Color
    red: Color
    panel: RGBA
    bar: Color


SCHEMES: Dict[str, ColorScheme] = {
    "dark_gold": ColorScheme(
        name="dark_gold",
        bg_top=(30, 25, 10),
        bg_bottom=(10, 8, 3),
        accent=(218, 165, 32),
        text=(255, 255, 255),
        muted=(160, 160, 160),
        gold=(218, 165, 32),
        red=(220, 50, 50),
        panel=(0, 0, 0, 140),
        bar=(218, 165, 32),
    ),
    "dark_red": ColorScheme(
        name="dark_red",
        bg_top=(40, 10, 10),
        bg_bottom=(15, 5, 5),
        accent=(200, 40, 40),
        text=(255, 255, 255),
        muted=(160, 160, 160),
        gold=(218, 165, 32),
        red=(220, 50, 50),
        panel=(0, 0, 0, 140),
        bar=(200, 40, 40),
    ),
    "dark_blue": ColorScheme(
        name="dark_blue",
        bg_top=(10, 15, 40),
        bg_bottom=(3, 5, 15),
        accent=(40, 100, 220),
        text=(255, 255, 255),
        muted=(160, 160, 160),
        gold=(218, 165, 32),
        red=(220, 50, 50),
        panel=(0, 0, 0, 140),
        bar=(40, 100, 220),
    ),
    "dark_green": ColorScheme(
        name="dark_green",
        bg_top=(10, 35, 15),
        bg_bottom=(3, 12, 5),
        accent=(40, 180, 70),
        text=(255, 255, 255),
        muted=(160, 160, 160),
        gold=(218, 165, 32),
        red=(220, 50, 50),
        panel=(0, 0, 0, 140),
        bar=(40, 180, 70),
    ),
    "dark_purple": ColorScheme(
        name="dark_purple",
        bg_top=(30, 10, 40),
        bg_bottom=(12, 3, 15),
        accent=(150, 50, 200),
        text=(255, 255, 255),
        muted=(160, 160, 160),
        gold=(218, 165, 32),
        red=(220, 50, 50),
        panel=(0, 0, 0, 140),
        bar=(150, 50, 200),
    ),
}


def get_scheme(name: str) -> ColorScheme:
    if name not in SCHEMES:
        raise ValueError(f"Unknown scheme: {name}. Available: {list(SCHEMES.keys())}")
    return SCHEMES[name]
