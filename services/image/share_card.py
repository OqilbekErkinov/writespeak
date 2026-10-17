"""The shareable result card (PNG, 1080x1080) in the Bandzilla brand (Brand
Guidelines v1.0, p. 50): the logo, the task, a big Band number in Unbounded,
the Gotcha count, the mascot in the mood that fits the result, a line on
how it was scored and the bot's name in the same place every time.

Pillow draws it directly - a single fixed layout doesn't need the HTML/CSS
pipeline. Fonts are the brand's variable TTFs in fonts/ (SIL OFL), the logo
and mascots are in assets/. Manrope has no ʻ/ʼ glyphs: Uzbek text here uses
‘ and ’ instead.
"""
from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_HERE = Path(__file__).parent
_FONTS = _HERE / "fonts"
_ASSETS = _HERE / "assets"

_SIZE = 1080
_PAD = 84
_RULE_Y = _SIZE - 132  # the footer's rule
_PET_H = 430  # the mascot's height, bottom-right, standing on the rule
_TEXT_W = 560  # the text column left of the mascot
INK, LIME, WHITE = (6, 32, 24), (91, 242, 166), (255, 255, 255)

_TEXT = {
    "uz": {
        "found": "{n} ta xato topildi",
        "clean": "Xato topilmadi",
        "estimate": "IELTS’ning 4 mezoni bo‘yicha baholandi",
        "footer": "Siz ham sinab ko‘ring: @{bot}",
    },
    "ru": {
        "found": "Найдено ошибок: {n}",
        "clean": "Ошибок не найдено",
        "estimate": "Оценено по 4 критериям IELTS",
        "footer": "Попробуйте и вы: @{bot}",
    },
}


@lru_cache(maxsize=16)
def _font(name: str, size: int, weight: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(_FONTS / f"{name}.ttf"), size)
    font.set_variation_by_axes([weight])
    return font


@lru_cache(maxsize=16)
def _asset(name: str) -> Image.Image:
    return Image.open(_ASSETS / f"{name}.png").convert("RGBA")


def _scales(img: Image.Image) -> None:
    """The Ink "skin" pattern (p. 45), low contrast under everything."""
    draw = ImageDraw.Draw(img)
    r = 60
    for row, y in enumerate(range(0, _SIZE + r, r)):
        for x in range(-2 * r + (row % 2) * r, _SIZE + r, 2 * r):
            draw.ellipse((x - r + 2, y - r + 2, x + r - 2, y + r - 2), fill=(11, 42, 33), outline=(18, 58, 45), width=4)


def _lines(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and draw.textlength(trial, font=font) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + [line] if line else lines


def _text(draw: ImageDraw.ImageDraw, xy, text: str, font, fill, spacing: float = 0) -> None:
    """Draws `text` with its cap-top at `xy`, letter-spaced if asked."""
    x, y = xy
    top = font.getbbox("H")[1]
    if not spacing:
        draw.text((x, y - top), text, font=font, fill=fill)
        return
    for ch in text:
        draw.text((x, y - top), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + spacing


def build_share_card(
    *, student_name: str, band: float, task_label: str, bot_username: str, mistakes: int | None = None,
    lang: str = "uz",
) -> bytes:
    """`mistakes`: how many the report found (None = no Gotcha line);
    `student_name` is not printed - the card shows the result, not the person."""
    words = _TEXT.get(lang, _TEXT["uz"])
    record = band >= 8
    if record:  # a record: the Lime card and the Wow mascot (p. 50)
        bg, fg, accent, soft, rule = LIME, INK, INK, (6, 32, 24), (6, 32, 24, 70)
        logo, mascot = "logo-on-lime", "mascot-wow_cape"
    else:
        bg, fg, accent, soft, rule = INK, WHITE, LIME, (190, 214, 204), (255, 255, 255, 50)
        logo, mascot = "logo-on-dark", "mascot-cool_cape" if band >= 7 else "mascot-chomp"

    img = Image.new("RGBA", (_SIZE, _SIZE), bg)
    if not record:
        _scales(img)

    pet = _asset(mascot)
    pet = pet.resize((round(pet.width * _PET_H / pet.height), _PET_H), Image.LANCZOS)
    img.alpha_composite(pet, (_SIZE - 56 - pet.width, _RULE_Y - 12 - pet.height))
    mark = _asset(logo)
    mark = mark.resize((360, round(mark.height * 360 / mark.width)), Image.LANCZOS)
    img.alpha_composite(mark, (_PAD, _PAD))
    draw = ImageDraw.Draw(img)

    y = _PAD + mark.height + 86
    _text(draw, (_PAD, y), task_label.upper().replace(" ", " · ", 1), _font("Manrope", 34, 800), accent, spacing=4)
    y += 64
    number = f"{band:.1f}"
    big = _font("Unbounded", 290, 800)
    _text(draw, (_PAD - 10, y), number, big, fg)
    box = big.getbbox(number)
    y += box[3] - big.getbbox("H")[1] + 52

    if mistakes is not None:
        _text(draw, (_PAD, y), "Gotcha!", _font("Caveat", 84, 700), accent)
        y += 78
        found = words["found"].format(n=mistakes) if mistakes else words["clean"]
        for line in _lines(draw, found, _font("Unbounded", 36, 700), _TEXT_W):
            _text(draw, (_PAD, y), line, _font("Unbounded", 36, 700), fg)
            y += 50
        y += 14
    for line in _lines(draw, words["estimate"], _font("Manrope", 26, 600), _TEXT_W):
        _text(draw, (_PAD, y), line, _font("Manrope", 26, 600), soft)
        y += 36

    # the footer: a rule and the bot's name, always in the same place
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).line((_PAD, _RULE_Y, _SIZE - _PAD, _RULE_Y), fill=rule, width=3)
    img.alpha_composite(overlay)
    draw = ImageDraw.Draw(img)
    _text(draw, (_PAD, _SIZE - 96), words["footer"].format(bot=bot_username), _font("Manrope", 32, 800), fg)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()
