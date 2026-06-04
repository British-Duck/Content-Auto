import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .schemes import ColorScheme

log = logging.getLogger(__name__)

RGBA = Tuple[int, int, int, int]


@dataclass
class StyledLine:
    text: str
    color: RGBA
    font_size: int


def _find_font(bold: bool = True) -> Optional[str]:
    if bold:
        paths = [
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/ARIALBD.TTF",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
    else:
        paths = [
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/ARIAL.TTF",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
    for p in paths:
        if os.path.exists(p):
            return p
    return None


_font_cache: Dict[Tuple[int, bool], ImageFont.FreeTypeFont] = {}


def get_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    key = (size, bold)
    if key not in _font_cache:
        path = _find_font(bold)
        try:
            if path:
                _font_cache[key] = ImageFont.truetype(path, size)
            else:
                _font_cache[key] = ImageFont.truetype("arial", size)
        except (OSError, IOError):
            log.warning("No font found (size=%d), using default", size)
            try:
                _font_cache[key] = ImageFont.load_default(size=size)
            except TypeError:
                _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


def parse_text_lines(text: str, scheme: ColorScheme, base_size: int) -> List[StyledLine]:
    tag_map = {
        "[GOLD]": ((*scheme.gold, 255), 1.0),
        "[RED]": ((*scheme.red, 255), 1.0),
        "[GREY]": ((*scheme.muted, 255), 1.0),
        "[BIG]": ((*scheme.text, 255), 1.35),
        "[SMALL]": ((*scheme.text, 255), 0.7),
    }

    result: List[StyledLine] = []
    for line in text.split("\n"):
        color: RGBA = (*scheme.text, 255)
        size_mult = 1.0
        content = line

        for tag, (c, mult) in tag_map.items():
            if line.startswith(tag):
                color = c
                size_mult = mult
                content = line[len(tag):]
                break

        result.append(StyledLine(
            text=content,
            color=color,
            font_size=max(12, int(base_size * size_mult)),
        ))
    return result


def create_gradient(
    width: int, height: int,
    top: Tuple[int, int, int],
    bottom: Tuple[int, int, int],
) -> Image.Image:
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    for c in range(3):
        col = np.linspace(top[c], bottom[c], height, dtype=np.uint8)
        arr[:, :, c] = col[:, np.newaxis]
    return Image.fromarray(arr, "RGB").convert("RGBA")


def _draw_scrims(img: Image.Image) -> None:
    """Darken the top and bottom edges with a soft alpha gradient so headline and
    subtitle stay legible over any footage, while the centre of the frame — where
    the action is — stays untouched. Replaces the old flat panel boxes."""
    width, height = img.size
    band = int(height * 0.42)
    top = np.zeros((band, width, 4), dtype=np.uint8)
    top[:, :, 3] = np.linspace(185, 0, band, dtype=np.uint8)[:, None]
    img.alpha_composite(Image.fromarray(top, "RGBA"), (0, 0))
    bot = np.zeros((band, width, 4), dtype=np.uint8)
    bot[:, :, 3] = np.linspace(0, 205, band, dtype=np.uint8)[:, None]
    img.alpha_composite(Image.fromarray(bot, "RGBA"), (0, height - band))


def _draw_full_dim(img: Image.Image, alpha: int = 95) -> None:
    """Evenly darken the whole frame so a centred headline reads over footage
    (the 'title card over video' treatment used by Option C clip screens)."""
    width, height = img.size
    layer = np.zeros((height, width, 4), dtype=np.uint8)
    layer[:, :, 3] = alpha
    img.alpha_composite(Image.fromarray(layer, "RGBA"), (0, 0))


def _wrap_line(text: str, font: ImageFont.FreeTypeFont, max_w: int, draw: ImageDraw.Draw) -> List[str]:
    words = text.split()
    if not words:
        return [""]
    lines = []
    cur = words[0]
    for word in words[1:]:
        test = cur + " " + word
        if draw.textlength(test, font=font) <= max_w:
            cur = test
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    return lines


def render_overlay(
    screen_text: str,
    screen_index: int,
    total_screens: int,
    label: str,
    scheme: ColorScheme,
    text_size: int,
    subtitle: Optional[str],
    *,
    centered: bool = False,
    scrim: bool = False,
    dim: bool = False,
    width: int = 1080,
    height: int = 1920,
) -> Image.Image:
    """Render the text/UI overlay. Placement and legibility are decided by the
    caller (renderer.py) from the layout style:
      centered  — headline centred vs anchored top / subtitle bottom
      scrim     — soft top/bottom darkening (top/bottom text over footage)
      dim       — soft full-frame darkening (centred text over footage)"""
    scale = width / 1080.0
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    # Legibility treatment for text sitting over live footage.
    if scrim:
        _draw_scrims(img)
    elif dim:
        _draw_full_dim(img)

    draw = ImageDraw.Draw(img)

    margin = int(60 * scale)
    max_text_w = width - margin * 2

    # ---- Accent bar (left edge) ----
    bar_w = max(4, int(6 * scale))
    draw.rectangle([0, 0, bar_w, height], fill=(*scheme.accent, 220))

    # ---- Label pill (top-left) ----
    pill_fs = max(14, int(22 * scale))
    pill_font = get_font(pill_fs)
    px, py = int(30 * scale), int(55 * scale)
    pad_x, pad_y = int(16 * scale), int(8 * scale)
    lw = int(draw.textlength(label, font=pill_font))
    pw = lw + pad_x * 2
    ph = pill_fs + pad_y * 2
    draw.rounded_rectangle(
        [px, py, px + pw, py + ph],
        radius=int(6 * scale),
        fill=(*scheme.accent, 200),
    )
    draw.text((px + pad_x, py + pad_y), label, font=pill_font,
              fill=(255, 255, 255, 255))

    # Counter pill removed — screen numbers are a production annotation,
    # not content. The progress bar at the bottom handles viewer orientation.

    # ---- Progress bar (bottom) ----
    prog_h = max(3, int(4 * scale))
    prog_y = height - int(30 * scale)
    progress = (screen_index + 1) / total_screens
    draw.rectangle([0, prog_y, width, prog_y + prog_h],
                   fill=(255, 255, 255, 40))
    draw.rectangle([0, prog_y, int(width * progress), prog_y + prog_h],
                   fill=(*scheme.bar, 220))

    # ---- Parse, wrap, and measure text ----
    raw_lines = parse_text_lines(screen_text, scheme, max(12, int(text_size * scale)))
    spacing = int(14 * scale)

    metrics: List[Tuple[StyledLine, ImageFont.FreeTypeFont, int, int]] = []
    total_h = 0
    max_w = 0
    for sl in raw_lines:
        font = get_font(sl.font_size)
        if not sl.text.strip():
            h = int(sl.font_size * 0.4)
            metrics.append((sl, font, 0, h))
            total_h += h + spacing
            continue

        wrapped = _wrap_line(sl.text, font, max_text_w, draw)
        for wtext in wrapped:
            asc, desc = font.getmetrics()
            h = asc + desc
            w = int(draw.textlength(wtext, font=font))
            metrics.append((StyledLine(wtext, sl.color, sl.font_size), font, w, h))
            total_h += h + spacing
            max_w = max(max_w, w)

    if metrics:
        total_h -= spacing

    # ---- Vertical placement ----
    if centered:
        text_top = (height - total_h) // 2
    else:
        text_top = int(height * 0.12)

    # ---- Draw headline lines ----
    y = text_top
    shadow = max(2, int(3 * scale))
    for sl, font, w, h in metrics:
        if sl.text.strip():
            x = (width - w) // 2
            draw.text((x + shadow, y + shadow), sl.text, font=font,
                      fill=(0, 0, 0, 180))
            draw.text((x, y), sl.text, font=font, fill=sl.color)
        y += h + spacing

    # ---- Subtitle ----
    # Proportional to text_size so it reads as a companion line, not an
    # annotation. On clip screens it anchors to the lower third (clear of the
    # progress bar); on gradient screens it sits just under the centred headline.
    if subtitle:
        sfs = max(20, int(text_size * 0.42 * scale))
        sfont = get_font(sfs, bold=False)
        sub_lines = _wrap_line(subtitle, sfont, max_text_w, draw)
        sasc, sdesc = sfont.getmetrics()
        line_h = sasc + sdesc + int(4 * scale)

        if centered:
            sy = y + int(12 * scale)
        else:
            sub_block_h = line_h * len(sub_lines)
            sy = height - int(height * 0.12) - sub_block_h

        for sline in sub_lines:
            sw = int(draw.textlength(sline, font=sfont))
            sx = (width - sw) // 2
            draw.text((sx + shadow, sy + shadow), sline, font=sfont, fill=(0, 0, 0, 160))
            draw.text((sx, sy), sline, font=sfont, fill=(*scheme.text, 235))
            sy += line_h

    return img
