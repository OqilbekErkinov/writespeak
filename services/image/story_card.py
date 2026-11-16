"""The result card (the owner, 2026-11-16): after every check the student gets a picture of their result to post
on Instagram (a post, or a story) or Telegram - 1080x1350 (4:5; the first 1080x1920 looked too tall in a
chat), the overall band and the four criteria, a kind word that fits the result, and a different design each
time (ten layouts in the Bandzilla brand, each with its own mascot mood; since 2026-11-16 in the colours of our
Instagram carousels too - yellow, pink, mint, white stickers with an ink border).

Never harsh: no "mistakes", no "low" - the words only encourage (the owner: "qattiq so'zlarni ishlatmagin,
muloyim so'zlar bo'lsin"). The student's name is not printed - the card shows the result, not the person.

Fonts: the brand's variable TTFs in fonts/ (Unbounded, Manrope,
Caveat; SIL OFL). Manrope has no ʻ/ʼ glyphs, so Uzbek text uses ‘ and ’. No emoji - none of the fonts has them.

    png, design = build_story_card(module="writing", task="task2", band=6.5,
                                   criteria={"TA": 7, "CC": 6, "LR": 6.5, "GRA": 6}, lang="uz",
                                   bot_username="bandzilla_bot", avoid="lime_pop", seed=123)
"""
from __future__ import annotations

import io
import math
import random
from datetime import date
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

_HERE = Path(__file__).parent
_FONTS = _HERE / "fonts"
_ASSETS = _HERE / "assets"

W, H = 1080, 1350  # 4:5 - an Instagram post, and it sits well in a story too (the owner, 2026-11-16)
PAD = 88

INK = (6, 32, 24)
INK_2 = (11, 42, 33)
LIME = (91, 242, 166)
SKIN = (43, 203, 123)
MOSS = (10, 124, 74)
PAPER = (243, 251, 247)
WHITE = (255, 255, 255)
MINT = (198, 244, 221)
SUN = (255, 222, 89)

DESIGNS = ("ink_scales", "lime_pop", "paper_ring", "split", "certificate", "bento",
           "yellow_big", "pink_gotcha", "goal_arrow", "sticker_list")

# --- words -----------------------------------------------------------------------------------------------------

_WORDS = {
    "uz": {
        "title": "Mening IELTS natijam",
        "tag": "NATIJAM", "criteria": "4 mezon", "now": "Hozir", "next": "keyingi maqsad",
        "peak": "Cho‘qqidaman!",
        "overall": "Umumiy ball",
        "best": "Eng kuchli tomonim",
        "even": "Barcha mezonlar bir tekis",
        "footer": "IELTS Writing va Speaking’ni tekshiring",
        "scored": "IELTS’ning 4 mezoni bo‘yicha",
        "follow": "Instagram va Telegram: @{handle}",
        "cheers": {
            "top": ["Ajoyib natija!", "Bu — yuksak daraja!", "Mukammallikka juda yaqin!", "Zo‘r ish, tabriklaymiz!"],
            "high": ["Juda yaxshi natija!", "Maqsad sari ishonchli qadam", "Zo‘r! Yuqori ball juda yaqin",
                     "Ajoyib o‘sish!"],
            "mid": ["Yaxshi natija!", "Har bir mashq — oldinga qadam", "Barqaror o‘sish, davom etamiz!",
                    "To‘g‘ri yo‘ldaman!"],
            "start": ["Yaxshi boshlanish!", "Har bir urinish — yangi tajriba", "Qadam-baqadam oldinga!",
                      "O‘sish shu yerdan boshlanadi"],
            "first": ["Birinchi qadam qo‘yildi!", "Har bir mashq meni kuchliroq qiladi", "Boshlanishi bor — davomi bor!",
                      "Bugun — eng yaxshi boshlanish"],
        },
    },
    "ru": {
        "title": "Мой результат IELTS",
        "tag": "РЕЗУЛЬТАТ", "criteria": "4 критерия", "now": "Сейчас", "next": "следующая цель",
        "peak": "На вершине!",
        "overall": "Общий балл",
        "best": "Моя сильная сторона",
        "even": "Все критерии ровно",
        "footer": "Проверьте свой IELTS Writing и Speaking",
        "scored": "По 4 критериям IELTS",
        "follow": "Instagram и Telegram: @{handle}",
        "cheers": {
            "top": ["Отличный результат!", "Это высокий уровень!", "Почти идеально!", "Блестящая работа!"],
            "high": ["Очень хороший результат!", "Уверенный шаг к цели", "Высокий балл совсем близко",
                     "Отличный рост!"],
            "mid": ["Хороший результат!", "Каждая практика — шаг вперёд", "Стабильный рост, продолжаю!",
                    "Я на верном пути!"],
            "start": ["Хорошее начало!", "Каждая попытка — новый опыт", "Шаг за шагом вперёд!",
                      "Рост начинается здесь"],
            "first": ["Первый шаг сделан!", "Каждая практика делает сильнее", "Начало положено!",
                      "Сегодня — лучший старт"],
        },
    },
    "en": {
        "title": "My IELTS result",
        "tag": "MY RESULT", "criteria": "4 criteria", "now": "Now", "next": "next goal", "peak": "At the top!",
        "overall": "Overall band",
        "best": "My strongest skill",
        "even": "All criteria even",
        "footer": "Check your IELTS Writing & Speaking",
        "scored": "Scored on the 4 IELTS criteria",
        "follow": "Instagram & Telegram: @{handle}",
        "cheers": {
            "top": ["Outstanding result!", "This is a high level!", "So close to perfect!", "Brilliant work!"],
            "high": ["A really good result!", "A confident step to my goal", "A top band is close!",
                     "Great progress!"],
            "mid": ["A good result!", "Every practice is a step forward", "Steady growth, keep going!",
                    "I'm on the right track!"],
            "start": ["A good start!", "Every try is new experience", "Step by step, forward!",
                      "Growth starts here"],
            "first": ["The first step is taken!", "Every practice makes me stronger", "A start is made!",
                      "Today is the best start"],
        },
    },
}

_TASKS = {"task1": "Writing · Task 1", "task2": "Writing · Task 2", "part1": "Speaking · Part 1",
          "part2": "Speaking · Part 2", "part3": "Speaking · Part 3", "full": "Speaking · Full test"}

# (key in criteria_scores, short label, full label)
_WRITING = [("TA", "TA", "Task Achievement"), ("CC", "CC", "Coherence & Cohesion"),
            ("LR", "LR", "Lexical Resource"), ("GRA", "GRA", "Grammar")]
_SPEAKING = [("FC", "FC", "Fluency & Coherence"), ("LR", "LR", "Lexical Resource"),
             ("GRA", "GRA", "Grammar"), ("Pronunciation", "P", "Pronunciation")]

# the mascot's mood follows the result (all of them happy - the card never frowns)
_MASCOTS = {
    "top": ["mascot-wow_cape", "mascot-love_cape", "mascot-cool_cape", "mascot-happy_cape"],
    "high": ["mascot-cool", "mascot-cool_cape", "mascot-wink_cape", "mascot-love"],
    "mid": ["mascot-happy", "mascot-wink", "mascot-chomp", "mascot-happy_cape"],
    "start": ["mascot-happy", "mascot-wink", "mascot-love"],
    "first": ["mascot-happy", "mascot-wink", "mascot-full"],
}


def tier(band: float) -> str:
    if band >= 8:
        return "top"
    if band >= 7:
        return "high"
    if band >= 6:
        return "mid"
    if band >= 5:
        return "start"
    return "first"


def _criteria(module: str, task: str, scores: dict) -> list[tuple[str, str, float]]:
    """[(short, full, band)] in the IELTS order - the ones the check has."""
    rows = []
    for key, short, full in (_WRITING if module == "writing" else _SPEAKING):
        value = scores.get(key)
        if value is None and key == "Pronunciation":
            value = scores.get("P")
        if value is None:
            continue
        if key == "TA" and task == "task2":
            short, full = "TR", "Task Response"
        rows.append((short, full, float(value)))
    return rows


def _band(v: float) -> str:
    return f"{v:.1f}"


# --- drawing helpers ------------------------------------------------------------------------------------------------


@lru_cache(maxsize=64)
def _font(name: str, size: int, weight: int = 700) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(_FONTS / f"{name}.ttf"), size)
    try:
        font.set_variation_by_axes([weight])
    except (OSError, AttributeError):  # a static font
        pass
    return font


@lru_cache(maxsize=32)
def _asset(name: str) -> Image.Image:
    return Image.open(_ASSETS / f"{name}.png").convert("RGBA")


def _sized(name: str, height: int) -> Image.Image:
    im = _asset(name)
    return im.resize((round(im.width * height / im.height), height), Image.LANCZOS)


def _tinted(name: str, color) -> Image.Image:
    """An asset's shape in one flat colour (a zigzag in pink)."""
    im = _asset(name)
    flat = Image.new("RGBA", im.size, (*color, 255))
    flat.putalpha(im.getchannel("A"))
    return flat


def _sized_w(name: str, width: int) -> Image.Image:
    im = _asset(name)
    return im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)


def _cap_top(font) -> int:
    return font.getbbox("H")[1]


def _text(d: ImageDraw.ImageDraw, x: float, y: float, s: str, font, fill, anchor: str = "l",
          spacing: float = 0) -> float:
    """`s` with its cap-top at y; anchor l / m (centred on x) / r. Returns its width."""
    w = d.textlength(s, font=font) + spacing * max(len(s) - 1, 0)
    if anchor == "m":
        x -= w / 2
    elif anchor == "r":
        x -= w
    top = _cap_top(font)
    if not spacing:
        d.text((x, y - top), s, font=font, fill=fill)
    else:
        for ch in s:
            d.text((x, y - top), ch, font=font, fill=fill)
            x += d.textlength(ch, font=font) + spacing
    return w


def _fit(d: ImageDraw.ImageDraw, s: str, name: str, size: int, weight: int, width: int, min_size: int = 20):
    """The largest font (down to min_size) that keeps `s` within `width` on one line."""
    while size > min_size and d.textlength(s, font=_font(name, size, weight)) > width:
        size -= 2
    return _font(name, size, weight)


def _wrap(d: ImageDraw.ImageDraw, s: str, font, width: int) -> list[str]:
    lines, line = [], ""
    for word in s.split():
        trial = f"{line} {word}".strip()
        if line and d.textlength(trial, font=font) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + [line] if line else lines


def _layer() -> Image.Image:
    return Image.new("RGBA", (W, H), (0, 0, 0, 0))


def _rrect(img: Image.Image, box, r: int, fill, outline=None, width: int = 0) -> None:
    layer = _layer()
    ImageDraw.Draw(layer).rounded_rectangle(box, r, fill=fill, outline=outline, width=width)
    img.alpha_composite(layer)


def _shadow(img: Image.Image, box, r: int, blur: int = 30, alpha: int = 90, offset: int = 18) -> None:
    layer = _layer()
    x0, y0, x1, y1 = box
    ImageDraw.Draw(layer).rounded_rectangle((x0, y0 + offset, x1, y1 + offset), r, fill=(0, 0, 0, alpha))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))


def _glow(img: Image.Image, xy, radius: int, color, alpha: int) -> None:
    layer = _layer()
    x, y = xy
    ImageDraw.Draw(layer).ellipse((x - radius, y - radius, x + radius, y + radius), fill=(*color, alpha))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(radius // 2)))


def _scales(img: Image.Image, fill, outline, r: int = 64, top: int = 0, bottom: int = H) -> None:
    d = ImageDraw.Draw(img)
    for row, y in enumerate(range(top, bottom + r, r)):
        for x in range(-2 * r + (row % 2) * r, W + r, 2 * r):
            d.ellipse((x - r + 3, y - r + 3, x + r - 3, y + r - 3), fill=fill, outline=outline, width=4)


def _dots(img: Image.Image, color, step: int = 44, r: int = 3) -> None:
    d = ImageDraw.Draw(img)
    for y in range(step // 2, H, step):
        for x in range(step // 2, W, step):
            d.ellipse((x - r, y - r, x + r, y + r), fill=color)


def _gradient(top, bottom) -> Image.Image:
    grad = Image.new("RGBA", (1, H))
    for y in range(H):
        t = y / (H - 1)
        grad.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,))
    return grad.resize((W, H))


def _paste(img: Image.Image, im: Image.Image, x: int, y: int, rotate: float = 0) -> None:
    if rotate:
        im = im.rotate(rotate, resample=Image.BICUBIC, expand=True)
    img.alpha_composite(im, (round(x), round(y)))


def _pill(img: Image.Image, cx: float, y: int, s: str, font, fg, bg, pad_x: int = 34, pad_y: int = 18,
          anchor: str = "m") -> tuple[int, int, int, int]:
    d = ImageDraw.Draw(img)
    tw = d.textlength(s, font=font)
    cap = font.getbbox("H")[3] - _cap_top(font)
    w, h = tw + 2 * pad_x, cap + 2 * pad_y
    x0 = cx - w / 2 if anchor == "m" else cx
    box = (round(x0), y, round(x0 + w), round(y + h))
    _rrect(img, box, round(h / 2), bg)
    _text(ImageDraw.Draw(img), x0 + pad_x, y + pad_y, s, font, fg)
    return box


def _bar(img: Image.Image, x: int, y: int, w: int, h: int, value: float, track, fill) -> None:
    _rrect(img, (x, y, x + w, y + h), h // 2, track)
    filled = max(h, round(w * max(0.0, min(value, 9.0)) / 9.0))
    _rrect(img, (x, y, x + filled, y + h), h // 2, fill)


def _ring(img: Image.Image, cx: int, cy: int, r: int, width: int, value: float, track, fill) -> None:
    layer = _layer()
    d = ImageDraw.Draw(layer)
    box = (cx - r, cy - r, cx + r, cy + r)
    d.arc(box, 0, 360, fill=track, width=width)
    end = -90 + 360 * max(0.0, min(value, 9.0)) / 9.0
    d.arc(box, -90, end, fill=fill, width=width)
    # round caps
    for ang in (-90, end):
        a = math.radians(ang)
        px, py = cx + (r - width / 2) * math.cos(a), cy + (r - width / 2) * math.sin(a)
        d.ellipse((px - width / 2, py - width / 2, px + width / 2, py + width / 2), fill=fill)
    img.alpha_composite(layer)


FOOTER_Y = 1130  # the footer block (call to action, the bot, our Instagram/Telegram) runs to ~FOOTER_Y + 170


def _footer(img: Image.Image, words: dict, bot: str, fg, sub, accent_bg, accent_fg, y: int = FOOTER_Y) -> None:
    """The call to action, the same place on every design: a line, the bot's name in a pill, and our Instagram
    and Telegram channel under it (the owner, 2026-11-16)."""
    d = ImageDraw.Draw(img)
    _text(d, W / 2, y, words["footer"], _fit(d, words["footer"], "Manrope", 30, 600, W - 2 * PAD), sub, anchor="m")
    _pill(img, W / 2, y + 48, f"@{bot}", _font("Unbounded", 34, 700), accent_fg, accent_bg, pad_x=40, pad_y=21)
    d = ImageDraw.Draw(img)
    _text(d, W / 2, y + 146, words["follow"], _fit(d, words["follow"], "Manrope", 28, 700, W - 2 * PAD), fg,
          anchor="m")


def _header(img: Image.Image, logo: str, words: dict, day: str, fg, y: int = 64) -> None:
    mark = _sized_w(logo, 300)
    _paste(img, mark, PAD, y)
    d = ImageDraw.Draw(img)
    _text(d, W - PAD, y + 18, day, _font("Manrope", 28, 700), fg, anchor="r")


# --- the designs -------------------------------------------------------------------------------------------------
# Each draws the whole 1080x1350 card from `c` (a dict: band, rows, cheer, best, label, words, day, bot, mascot).


def _d_ink_scales(c: dict) -> Image.Image:
    img = Image.new("RGBA", (W, H), INK)
    _scales(img, INK_2, (18, 58, 45))
    _glow(img, (W - 140, 430), 300, SKIN, 60)
    w = c["words"]
    _header(img, "logo-on-dark", w, c["day"], (170, 205, 190))
    d = ImageDraw.Draw(img)
    _pill(img, PAD, 150, c["label"].upper(), _font("Manrope", 28, 800), INK, LIME, anchor="l")
    _text(d, PAD, 236, c["cheer"], _fit(d, c["cheer"], "Caveat", 80, 700, W - 2 * PAD), LIME)
    _text(d, PAD, 340, w["overall"].upper(), _font("Manrope", 28, 800), (170, 205, 190), spacing=4)
    big = _fit(d, _band(c["band"]), "Unbounded", 250, 800, W - 2 * PAD - 320, min_size=180)  # clear of the mascot
    _text(d, PAD - 10, 386, _band(c["band"]), big, WHITE)
    pet = _sized(c["mascot"], 380)
    _paste(img, pet, W - pet.width - 50, 300)
    y = 712
    for short, full, v in c["rows"]:
        d = ImageDraw.Draw(img)
        _text(d, PAD, y, full, _font("Manrope", 30, 700), WHITE)
        _text(d, W - PAD, y - 4, _band(v), _font("Unbounded", 38, 700), LIME, anchor="r")
        _bar(img, PAD, y + 46, W - 2 * PAD, 14, v, (30, 70, 55), LIME)
        y += 92
    _footer(img, w, c["bot"], WHITE, (170, 205, 190), LIME, INK)
    return img


def _d_lime_pop(c: dict) -> Image.Image:
    img = Image.new("RGBA", (W, H), LIME)
    zz = _sized_w("zigzag-ink", W)
    zz = zz.resize((W, 56))
    _paste(img, zz.rotate(180), 0, 0)
    _paste(img, zz, 0, H - zz.height)
    w = c["words"]
    _header(img, "logo-on-lime", w, c["day"], INK, y=92)
    d = ImageDraw.Draw(img)
    _text(d, W / 2, 196, c["label"].upper(), _font("Manrope", 30, 800), INK, anchor="m", spacing=5)
    cx, cy, r = W // 2 - 70, 450, 200
    _shadow(img, (cx - r, cy - r, cx + r, cy + r), r, blur=26, alpha=70, offset=14)
    disc = _layer()
    ImageDraw.Draw(disc).ellipse((cx - r, cy - r, cx + r, cy + r), fill=INK)
    img.alpha_composite(disc)
    d = ImageDraw.Draw(img)
    _text(d, cx, cy - 118, w["overall"].upper(), _font("Manrope", 24, 800), LIME, anchor="m", spacing=3)
    _text(d, cx, cy - 72, _band(c["band"]), _font("Unbounded", 150, 800), WHITE, anchor="m")
    pet = _sized(c["mascot"], 270)
    _paste(img, pet, cx + r - 30, cy - 20, rotate=-8)
    d = ImageDraw.Draw(img)
    _text(d, W / 2, 722, c["cheer"], _fit(d, c["cheer"], "Unbounded", 44, 700, W - 2 * PAD), INK, anchor="m")
    tw, th, gap = (W - 2 * PAD - 24) // 2, 118, 18
    for i, (short, full, v) in enumerate(c["rows"]):
        x = PAD + (i % 2) * (tw + gap + 6)
        y = 790 + (i // 2) * (th + gap)
        _rrect(img, (x, y, x + tw, y + th), 30, INK)
        d = ImageDraw.Draw(img)
        _text(d, x + 30, y + 24, full, _fit(d, full, "Manrope", 26, 700, tw - 60), (170, 230, 200))
        _text(d, x + 30, y + 62, _band(v), _font("Unbounded", 46, 800), LIME)
    _footer(img, w, c["bot"], INK, (6, 32, 24), INK, LIME, y=1096)
    return img


def _d_paper_ring(c: dict) -> Image.Image:
    img = Image.new("RGBA", (W, H), PAPER)
    _dots(img, (214, 234, 224))
    w = c["words"]
    _header(img, "logo-on-light", w, c["day"], (88, 115, 103))
    d = ImageDraw.Draw(img)
    _text(d, W / 2, 158, w["title"].upper(), _font("Manrope", 28, 800), MOSS, anchor="m", spacing=5)
    _text(d, W / 2, 204, c["label"], _font("Unbounded", 40, 700), INK, anchor="m")
    cx, cy, r = W // 2, 478, 210
    _ring(img, cx, cy, r, 34, c["band"], (214, 236, 225), SKIN)
    d = ImageDraw.Draw(img)
    _text(d, cx, cy - 112, w["overall"], _font("Manrope", 28, 700), (88, 115, 103), anchor="m")
    _text(d, cx, cy - 66, _band(c["band"]), _font("Unbounded", 136, 800), INK, anchor="m")
    _text(d, cx, cy + 90, "/ 9.0", _font("Manrope", 30, 700), (120, 150, 136), anchor="m")
    pet = _sized(c["mascot"], 210)
    _paste(img, pet, cx + r - 50, cy + r - 205)
    d = ImageDraw.Draw(img)
    _text(d, W / 2, 742, c["cheer"], _fit(d, c["cheer"], "Caveat", 76, 700, W - 2 * PAD), MOSS, anchor="m")
    half = (W - 2 * PAD) // 2
    for i, (short, full, v) in enumerate(c["rows"]):
        x = PAD + (i % 2) * (half + 10)
        yy = 846 + (i // 2) * 108
        _rrect(img, (x, yy, x + half - 10, yy + 88), 44, WHITE, outline=(205, 228, 216), width=3)
        d = ImageDraw.Draw(img)
        _text(d, x + 34, yy + 30, short, _font("Unbounded", 30, 700), MOSS)
        _text(d, x + half - 44, yy + 26, _band(v), _font("Unbounded", 36, 800), INK, anchor="r")
    _footer(img, w, c["bot"], INK, (88, 115, 103), INK, LIME)
    return img


def _d_split(c: dict) -> Image.Image:
    img = Image.new("RGBA", (W, H), INK)
    top = _layer()
    ImageDraw.Draw(top).polygon([(0, 0), (W, 0), (W, 600), (0, 740)], fill=PINK)  # pink since 2026-11-16
    img.alpha_composite(top)
    zz = _tinted("zigzag-lime", PINK).resize((W, 56))
    w = c["words"]
    _header(img, "logo-on-lime", w, c["day"], INK)
    d = ImageDraw.Draw(img)
    _text(d, PAD, 160, w["title"], _font("Manrope", 32, 800), INK)
    _text(d, PAD, 212, c["label"], _font("Unbounded", 48, 700), INK)
    _text(d, PAD - 12, 300, _band(c["band"]),
          _fit(d, _band(c["band"]), "Unbounded", 270, 800, W - 2 * PAD - 340, min_size=190), INK)
    pet = _sized(c["mascot"], 340)
    _paste(img, pet, W - pet.width - 50, 296)
    d = ImageDraw.Draw(img)
    _text(d, PAD, 760, c["cheer"], _fit(d, c["cheer"], "Caveat", 84, 700, W - 2 * PAD), YELLOW)
    y = 856
    for short, full, v in c["rows"]:
        d = ImageDraw.Draw(img)
        _text(d, PAD, y, short, _font("Unbounded", 32, 800), PINK)
        _text(d, PAD + 120, y + 4, full, _font("Manrope", 30, 600), (190, 225, 210))
        _text(d, W - PAD, y - 2, _band(v), _font("Unbounded", 36, 800), WHITE, anchor="r")
        y += 50
    _paste(img, zz, 0, H - zz.height)
    _footer(img, w, c["bot"], WHITE, (170, 205, 190), PINK, INK, y=1104)
    return img


def _d_certificate(c: dict) -> Image.Image:
    img = Image.new("RGBA", (W, H), INK)
    _scales(img, INK_2, (18, 58, 45), r=52)
    w = c["words"]
    card = (64, 70, W - 64, 1060)
    _shadow(img, card, 44, blur=30, alpha=140, offset=20)
    _rrect(img, card, 44, PAPER)
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = card[0] + 24, card[1] + 24, card[2] - 24, card[3] - 24
    for x in range(x0 + 30, x1 - 30, 28):
        d.line((x, y0, x + 14, y0), fill=(160, 205, 182), width=3)
        d.line((x, y1, x + 14, y1), fill=(160, 205, 182), width=3)
    for y in range(y0 + 30, y1 - 30, 28):
        d.line((x0, y, x0, y + 14), fill=(160, 205, 182), width=3)
        d.line((x1, y, x1, y + 14), fill=(160, 205, 182), width=3)
    mark = _sized_w("logo-on-light", 270)
    _paste(img, mark, W / 2 - mark.width / 2, 126)
    d = ImageDraw.Draw(img)
    _text(d, W / 2, 240, w["title"].upper(), _font("Manrope", 28, 800), MOSS, anchor="m", spacing=6)
    _text(d, W / 2, 290, c["label"], _font("Unbounded", 42, 700), INK, anchor="m")
    burst = _sized_w("burst", 340)
    by = 360
    _paste(img, burst, W / 2 - burst.width / 2, by)
    d = ImageDraw.Draw(img)
    _text(d, W / 2, by + burst.height / 2 - 58, _band(c["band"]), _font("Unbounded", 110, 800), INK, anchor="m")
    _text(d, W / 2, 680, c["cheer"], _fit(d, c["cheer"], "Caveat", 72, 700, W - 2 * PAD - 80), MOSS, anchor="m")
    y = 790
    for short, full, v in c["rows"]:
        d = ImageDraw.Draw(img)
        _text(d, 150, y, full, _font("Manrope", 30, 700), INK)
        _text(d, W - 150, y - 2, _band(v), _font("Unbounded", 34, 800), MOSS, anchor="r")
        d.line((150, y + 48, W - 150, y + 48), fill=(214, 234, 224), width=2)
        y += 60
    pet = _sized(c["mascot"], 200)
    _paste(img, pet, W - 64 - pet.width + 40, 6, rotate=8)  # on the card's top corner, its head inside the picture
    _footer(img, w, c["bot"], WHITE, (170, 205, 190), LIME, INK, y=1120)
    return img


def _d_bento(c: dict) -> Image.Image:
    img = Image.new("RGBA", (W, H), INK)
    w = c["words"]
    _header(img, "logo-on-dark", w, c["day"], (170, 205, 190))
    g = 22
    L, R = PAD - 30, W - PAD + 30
    mid = (L + R) // 2
    _rrect(img, (L, 142, mid + 120, 520), 44, YELLOW)  # the carousels' colours since 2026-11-16
    _rrect(img, (mid + 120 + g, 142, R, 520), 44, PINK)
    d = ImageDraw.Draw(img)
    _text(d, L + 46, 186, c["label"].upper(), _fit(d, c["label"].upper(), "Manrope", 26, 800, mid + 40 - L - 46), INK,
          spacing=3)
    _text(d, L + 38, 262, _band(c["band"]), _font("Unbounded", 180, 800), INK)
    _text(d, L + 46, 466, w["overall"], _font("Manrope", 28, 700), (20, 70, 50))
    pet = _sized(c["mascot"], 280)
    cw = R - (mid + 120 + g)
    _paste(img, pet, mid + 120 + g + (cw - pet.width) / 2, 520 - pet.height - 20)
    _rrect(img, (L, 520 + g, R, 690), 44, (20, 58, 45))
    d = ImageDraw.Draw(img)
    _text(d, W / 2, 592, c["cheer"], _fit(d, c["cheer"], "Caveat", 76, 700, R - L - 80), YELLOW, anchor="m")
    tw = (R - L - g) // 2
    colors = [LIME, (255, 227, 233), MINT, (255, 241, 196)]
    for i, (short, full, v) in enumerate(c["rows"]):
        x = L + (i % 2) * (tw + g)
        y = 712 + (i // 2) * (160 + g)
        _rrect(img, (x, y, x + tw, y + 160), 36, colors[i])
        d = ImageDraw.Draw(img)
        _text(d, x + 36, y + 30, full, _fit(d, full, "Manrope", 26, 700, tw - 72), (60, 95, 80))
        _text(d, x + 36, y + 78, _band(v), _font("Unbounded", 52, 800), INK)
        _bar(img, x + 190, y + 100, tw - 226, 12, v, (255, 255, 255, 150), INK)
    _footer(img, w, c["bot"], WHITE, (170, 205, 190), LIME, INK, y=1124)
    return img


# --- the carousel look (the owner, 2026-11-16: "like our Instagram carousels - not everything green") ----------
# The brand's social posts: yellow, pink, mint and lime grounds, black Unbounded headlines, white "sticker" cards
# with a thick ink border and a hard offset shadow, a small ink tag top-left, Caveat notes in red, a huge faded
# word behind, the mascot big at the side.

YELLOW = (251, 220, 101)
PINK = (251, 120, 145)
RED_NOTE = (214, 40, 78)


def _sticker(img: Image.Image, box, fill=WHITE, radius: int = 26, border: int = 5, shadow: int = 12,
             rotate: float = 0, draw=None) -> None:
    """A white card with an ink border and a hard ink shadow down-right (the carousels' cards), optionally
    tilted; `draw(d, w, h)` writes its content in the card's own coordinates before it is tilted."""
    x0, y0, x1, y1 = (round(v) for v in box)
    w, h = x1 - x0, y1 - y0
    card = Image.new("RGBA", (w + shadow + 2 * border, h + shadow + 2 * border), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    cd.rounded_rectangle((shadow, shadow, w + shadow, h + shadow), radius, fill=INK)
    cd.rounded_rectangle((0, 0, w, h), radius, fill=fill, outline=INK, width=border)
    if draw is not None:
        draw(ImageDraw.Draw(card), w, h)
    if rotate:
        card = card.rotate(rotate, resample=Image.BICUBIC, expand=True)
    img.alpha_composite(card, (x0 - (card.width - (w + shadow + 2 * border)) // 2,
                               y0 - (card.height - (h + shadow + 2 * border)) // 2))


def _tag(img: Image.Image, x: int, y: int, text: str, bg=INK, fg=LIME) -> None:
    """The carousels' top-left tag: an ink pill with the symbol and a word in Lime."""
    font = _font("Manrope", 26, 800)
    d = ImageDraw.Draw(img)
    tw = d.textlength(text, font=font) + 3 * (len(text) - 1)
    sym = _sized("symbol-on-dark", 40)
    w, h = 22 + sym.width + 12 + tw + 26, 58
    _rrect(img, (x, y, x + w, y + h), h // 2, bg)
    _paste(img, sym, x + 18, y + 9)
    _text(ImageDraw.Draw(img), x + 22 + sym.width + 12, y + 19, text, font, fg, spacing=3)


def _corner(img: Image.Image, day: str, fg=INK) -> None:
    _text(ImageDraw.Draw(img), W - PAD, 82, day, _font("Unbounded", 26, 700), fg, anchor="r")


def _outlined(d: ImageDraw.ImageDraw, x: float, y: float, s: str, font, fill, stroke, width: int,
              anchor: str = "l") -> None:
    """White letters with an ink outline (the carousels' "Topa olasizmi?")."""
    w = d.textlength(s, font=font)
    if anchor == "m":
        x -= w / 2
    d.text((x, y - _cap_top(font)), s, font=font, fill=fill, stroke_width=width, stroke_fill=stroke)


def _rows_in_card(rows, label: str, value_color=INK, name_size: int = 30):
    """The four criteria inside a sticker: an ink label pill, then name ... band per row."""
    def draw(d, w, h):
        lab = _font("Manrope", 22, 800)
        lw = d.textlength(label, font=lab) + 2 * (len(label) - 1)
        d.rounded_rectangle((30, 28, 30 + lw + 32, 70), 10, fill=INK)
        _text(d, 46, 39, label, lab, LIME, spacing=2)
        y = 104
        step = (h - 130) / max(len(rows), 1)
        for i, (short, full, v) in enumerate(rows):
            _text(d, 34, y, full, _fit(d, full, "Manrope", name_size, 700, w - 190), INK)
            _text(d, w - 34, y - 4, _band(v), _font("Unbounded", 36, 800), value_color, anchor="r")
            if i < len(rows) - 1:
                d.line((34, y + step - 22, w - 34, y + step - 22), fill=(222, 230, 226), width=2)
            y += step
    return draw


def _d_yellow_big(c: dict) -> Image.Image:
    img = Image.new("RGBA", (W, H), YELLOW)
    w = c["words"]
    _tag(img, PAD, 64, w["tag"])
    _corner(img, c["day"])
    d = ImageDraw.Draw(img)
    _text(d, PAD, 172, c["label"], _font("Unbounded", 40, 700), INK)
    _text(d, PAD - 8, 236, _band(c["band"]),
          _fit(d, _band(c["band"]), "Unbounded", 270, 800, W - 2 * PAD - 300, min_size=200), INK)
    _text(d, PAD, 470, c["cheer"], _fit(d, c["cheer"], "Caveat", 72, 700, W - 2 * PAD - 340), RED_NOTE)
    pet = _sized(c["mascot"], 420)
    _paste(img, pet, W - pet.width - 40, 250)
    _sticker(img, (PAD, 580, W - PAD - 10, 1040), rotate=-1.2,
             draw=_rows_in_card(c["rows"], w["criteria"].upper()))
    _footer(img, w, c["bot"], INK, (60, 50, 20), INK, YELLOW)
    return img


def _d_pink_gotcha(c: dict) -> Image.Image:
    img = Image.new("RGBA", (W, H), PINK)
    d = ImageDraw.Draw(img)
    ghost = ("WRITING" if c["module"] == "writing" else "SPEAKING")
    _text(d, -40, 470, ghost, _font("Unbounded", 230, 800), (236, 100, 128))
    w = c["words"]
    _tag(img, PAD, 64, w["tag"])
    _corner(img, c["day"])
    d = ImageDraw.Draw(img)
    _text(d, PAD, 172, w["title"], _fit(d, w["title"], "Unbounded", 52, 800, W - 2 * PAD), INK)
    _text(d, PAD, 240, c["label"], _font("Manrope", 34, 700), INK)
    _outlined(d, PAD - 6, 312, _band(c["band"]), _font("Unbounded", 230, 800), WHITE, INK, 7)
    pet = _sized(c["mascot"], 330)
    _paste(img, pet, W - pet.width - 50, 240, rotate=4)
    _text(d, PAD, 560, c["cheer"], _fit(d, c["cheer"], "Caveat", 70, 700, W - 2 * PAD), (120, 10, 40))

    def grid(dd, cw, ch):
        half = (cw - 40) // 2
        for i, (short, full, v) in enumerate(c["rows"]):
            x = 26 + (i % 2) * (half + 12)
            y = 26 + (i // 2) * ((ch - 52) // 2)
            dd.rounded_rectangle((x, y, x + half - 12, y + (ch - 52) // 2 - 14), 18, fill=(255, 236, 241))
            _text(dd, x + 24, y + 26, full, _fit(dd, full, "Manrope", 24, 700, half - 60), (120, 10, 40))
            _text(dd, x + 24, y + 70, _band(v), _font("Unbounded", 50, 800), INK)
    _sticker(img, (PAD, 660, W - PAD - 10, 1040), rotate=1.2, draw=grid)
    _footer(img, w, c["bot"], INK, (90, 20, 40), INK, PINK)
    return img


def _d_goal_arrow(c: dict) -> Image.Image:
    img = Image.new("RGBA", (W, H), MINT)
    w = c["words"]
    _tag(img, PAD, 64, w["tag"])
    _corner(img, c["day"])
    d = ImageDraw.Draw(img)
    _text(d, PAD, 172, c["label"], _font("Unbounded", 40, 700), INK)
    band = c["band"]
    top = band >= 9
    goal = 9.0 if top else min(9.0, band + 0.5)
    _text(d, PAD, 250, w["now"], _font("Manrope", 30, 800), MOSS)
    big = _font("Unbounded", 160, 800)
    _text(d, PAD - 6, 296, _band(band), big, INK)
    if top:
        _text(d, PAD + 10, 520, w["peak"], _fit(d, w["peak"], "Caveat", 80, 700, 600), RED_NOTE)
    else:
        # a red arrow from the right of today's band, curving down to the next goal (the carousels' "6.0 -> 7.0")
        x0 = PAD + d.textlength(_band(band), font=big) + 18
        p0, p1, p2 = (x0, 360), (x0 + 150, 340), (x0 + 130, 470)
        pts = [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
                (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]) for t in [i / 24 for i in range(25)]]
        layer = _layer()
        ld = ImageDraw.Draw(layer)
        ld.line(pts, fill=RED_NOTE, width=10, joint="curve")
        ex, ey = p2
        ld.polygon([(ex - 22, ey - 6), (ex + 20, ey - 10), (ex - 2, ey + 30)], fill=RED_NOTE)
        img.alpha_composite(layer)
        d = ImageDraw.Draw(img)
        _text(d, x0 + 40, 500, _band(goal), big, MOSS)
        _text(d, PAD, 560, w["next"], _font("Caveat", 64, 700), RED_NOTE)
    pet = _sized(c["mascot"], 300)
    _paste(img, pet, W - pet.width - 40, 170)
    _sticker(img, (PAD, 690, W - PAD - 10, 1060), rotate=-1,
             draw=_rows_in_card(c["rows"], w["criteria"].upper(), value_color=MOSS, name_size=28))
    _footer(img, w, c["bot"], INK, (40, 80, 60), INK, LIME, y=1140)
    return img


def _d_sticker_list(c: dict) -> Image.Image:
    img = Image.new("RGBA", (W, H), (9, 98, 60))
    _scales(img, (12, 108, 67), (16, 120, 75), r=56)
    w = c["words"]
    _tag(img, PAD, 64, w["tag"], bg=LIME, fg=INK)
    _corner(img, c["day"], fg=(200, 240, 220))
    d = ImageDraw.Draw(img)
    _text(d, PAD, 172, c["label"], _font("Unbounded", 40, 700), WHITE)
    _text(d, PAD - 8, 236, _band(c["band"]),
          _fit(d, _band(c["band"]), "Unbounded", 230, 800, W - 2 * PAD - 320, min_size=180), WHITE)
    _text(d, PAD, 452, c["cheer"], _fit(d, c["cheer"], "Caveat", 70, 700, W - 2 * PAD - 240), SUN)
    pet = _sized(c["mascot"], 340)
    _paste(img, pet, W - pet.width - 50, 180)
    y = 556
    for i, (short, full, v) in enumerate(c["rows"]):
        def row(dd, cw, ch, short=short, full=full, v=v):
            _text(dd, 30, 36, short, _font("Unbounded", 36, 800), MOSS)
            _text(dd, 150, 40, full, _fit(dd, full, "Manrope", 28, 700, cw - 330), INK)
            _text(dd, cw - 30, 32, _band(v), _font("Unbounded", 40, 800), INK, anchor="r")
        _sticker(img, (PAD, y, W - PAD - 10, y + 108), radius=22, shadow=9, rotate=(-0.8 if i % 2 else 0.8),
                 fill=(WHITE if i % 2 else (255, 247, 214)), draw=row)
        y += 132
    _footer(img, w, c["bot"], WHITE, (200, 240, 220), YELLOW, INK, y=1124)
    return img


_DRAW = {"ink_scales": _d_ink_scales, "lime_pop": _d_lime_pop, "paper_ring": _d_paper_ring,
         "split": _d_split, "certificate": _d_certificate, "bento": _d_bento, "yellow_big": _d_yellow_big,
         "pink_gotcha": _d_pink_gotcha, "goal_arrow": _d_goal_arrow, "sticker_list": _d_sticker_list}


def build_story_card(*, module: str, task: str, band: float, criteria: dict, lang: str = "uz",
                     bot_username: str = "bandzilla_bot", social: str = "bandzilla_uz", design: str | None = None,
                     avoid: str | None = None, seed: int | None = None, day: date | None = None) -> tuple[bytes, str]:
    """The card as PNG bytes, and the design used. `design`: a fixed one (tests, previews); else a random one,
    never `avoid` (the student's previous card) so the next one always looks different. `seed`: repeatable."""
    rnd = random.Random(seed)
    words = dict(_WORDS.get(lang, _WORDS["uz"]))
    words["follow"] = words["follow"].format(handle=social)  # our Instagram and Telegram channel
    level = tier(band)
    rows = _criteria(module, task, criteria or {})
    if design not in _DRAW:
        choices = [d for d in DESIGNS if d != avoid] or list(DESIGNS)
        design = rnd.choice(choices)
    best = ""
    if rows:
        top_value = max(v for *_, v in rows)
        tops = [full for _, full, v in rows if v == top_value]
        best = f"{words['best']}: {tops[0]} {_band(top_value)}" if len(tops) < len(rows) else \
            f"{words['even']}: {_band(top_value)}"
    c = {
        "band": float(band), "rows": rows, "words": words, "bot": bot_username, "module": module,
        "cheer": rnd.choice(words["cheers"][level]),
        "best": best,
        "label": _TASKS.get(task, "IELTS"),
        "day": (day or date.today()).strftime("%d.%m.%Y"),
        "mascot": rnd.choice(_MASCOTS[level]),
    }
    img = _DRAW[design](c)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue(), design
