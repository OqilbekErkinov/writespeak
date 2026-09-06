"""Generates a shareable band-score card (PNG) for social media, using
Pillow directly rather than WeasyPrint - a single fixed layout doesn't need
a full HTML/CSS pipeline. Square (1080x1080) to fit Instagram feed/story
crops without letterboxing.

Font paths point at the Noto Sans files the Dockerfile already installs
(fonts-noto) for the PDF report/matplotlib chart - confirmed present via
`fc-list` on the deployed image on 2026-08-29.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_FONT_DIR = Path("/usr/share/fonts/truetype/noto")
_FONT_BOLD = _FONT_DIR / "NotoSans-Bold.ttf"
_FONT_REGULAR = _FONT_DIR / "NotoSans-Regular.ttf"

_SIZE = 1080
_BG = (16, 20, 38)
_ACCENT = (124, 156, 255)
_TEXT = (238, 240, 250)
_MUTED = (146, 154, 186)


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def _draw_centered(draw: ImageDraw.ImageDraw, y: int, text: str, font: ImageFont.FreeTypeFont, fill) -> int:
    """Draws `text` horizontally centered at `y`; returns the y just below it."""
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    draw.text(((_SIZE - width) / 2, y - bbox[1]), text, font=font, fill=fill)
    return y + height


def build_share_card(*, student_name: str, band: float, task_label: str, bot_username: str) -> bytes:
    img = Image.new("RGB", (_SIZE, _SIZE), _BG)
    draw = ImageDraw.Draw(img)

    y = 90
    y = _draw_centered(draw, y, "WriteSpeak", _font(_FONT_BOLD, 46), _ACCENT)
    y += 70
    y = _draw_centered(draw, y, task_label.upper(), _font(_FONT_REGULAR, 34), _MUTED)
    y += 60
    y = _draw_centered(draw, y, "BAND", _font(_FONT_REGULAR, 32), _MUTED)
    y += 20
    y = _draw_centered(draw, y, f"{band:.1f}", _font(_FONT_BOLD, 260), _TEXT)
    y += 60
    _draw_centered(draw, y, student_name, _font(_FONT_BOLD, 44), _TEXT)

    footer_text = f"Siz ham sinab ko'ring: @{bot_username}"
    footer_font = _font(_FONT_REGULAR, 30)
    footer_bbox = draw.textbbox((0, 0), footer_text, font=footer_font)
    _draw_centered(draw, _SIZE - 90 - footer_bbox[3], footer_text, footer_font, _MUTED)

    draw.rectangle([(0, _SIZE - 16), (_SIZE, _SIZE)], fill=_ACCENT)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
