"""The story card (the owner, 2026-11-16): after every check the student gets a picture of their result to post
as an Instagram / Telegram story - 1080x1920, the overall band and the four criteria, a kind word that fits the
result, and a different design each time (seven layouts in the Bandzilla brand, each with its own mascot mood).

Never harsh: no "mistakes", no "low" - the words only encourage (the owner: "qattiq so'zlarni ishlatmagin,
muloyim so'zlar bo'lsin"). The student's name is not printed - the card shows the result, not the person.

Everything important stays inside the story safe zone (SAFE_TOP..SAFE_BOTTOM): Instagram and Telegram draw
their own bars over the top and bottom ~200 px. Fonts: the brand's variable TTFs in fonts/ (Unbounded, Manrope,
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

W, H = 1080, 1920
SAFE_TOP, SAFE_BOTTOM = 210, 1700
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

DESIGNS = ("ink_scales", "lime_pop", "paper_ring", "night_glow", "split", "certificate", "bento")

# --- words -----------------------------------------------------------------------------------------------------

_WORDS = {
    "uz": {
        "title": "Mening IELTS natijam",
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


FOOTER_Y = 1490


def _footer(img: Image.Image, words: dict, bot: str, fg, sub, accent_bg, accent_fg, y: int = FOOTER_Y) -> None:
    """The call to action, the same place on every design: a line, the bot's name in a pill, and our Instagram
    and Telegram channel under it (the owner, 2026-11-16) - all above the stories' reply bar."""
    d = ImageDraw.Draw(img)
    _text(d, W / 2, y, words["footer"], _fit(d, words["footer"], "Manrope", 34, 600, W - 2 * PAD), sub, anchor="m")
    _pill(img, W / 2, y + 58, f"@{bot}", _font("Unbounded", 38, 700), accent_fg, accent_bg, pad_x=44, pad_y=24)
    d = ImageDraw.Draw(img)
    _text(d, W / 2, y + 170, words["follow"], _fit(d, words["follow"], "Manrope", 30, 700, W - 2 * PAD), fg,
          anchor="m")


def _header(img: Image.Image, logo: str, words: dict, day: str, fg, y: int = SAFE_TOP) -> None:
    mark = _sized_w(logo, 330)
    _paste(img, mark, PAD, y)
    d = ImageDraw.Draw(img)
    _text(d, W - PAD, y + 20, day, _font("Manrope", 30, 700), fg, anchor="r")


# --- the designs -------------------------------------------------------------------------------------------------
# Each draws the whole card from `c` (a dict: band, rows, cheer, best, label, words, day, bot, mascot) on a fresh
# 1080x1920 image.


def _d_ink_scales(c: dict) -> Image.Image:
    img = Image.new("RGBA", (W, H), INK)
    _scales(img, INK_2, (18, 58, 45))
    _glow(img, (W - 140, 640), 360, SKIN, 60)
    w = c["words"]
    _header(img, "logo-on-dark", w, c["day"], (170, 205, 190))
    d = ImageDraw.Draw(img)
    _pill(img, PAD, 350, c["label"].upper(), _font("Manrope", 30, 800), INK, LIME, anchor="l")
    _text(d, PAD, 470, c["cheer"], _fit(d, c["cheer"], "Caveat", 92, 700, W - 2 * PAD), LIME)
    _text(d, PAD, 610, w["overall"].upper(), _font("Manrope", 30, 800), (170, 205, 190), spacing=4)
    big = _fit(d, _band(c["band"]), "Unbounded", 300, 800, W - 2 * PAD - 330, min_size=200)  # clear of the mascot
    _text(d, PAD - 12, 664, _band(c["band"]), big, WHITE)
    pet = _sized(c["mascot"], 470)
    _paste(img, pet, W - pet.width - 40, 520)
    y = 1010
    for short, full, v in c["rows"]:
        d = ImageDraw.Draw(img)
        _text(d, PAD, y, full, _font("Manrope", 34, 700), WHITE)
        _text(d, W - PAD, y - 4, _band(v), _font("Unbounded", 44, 700), LIME, anchor="r")
        _bar(img, PAD, y + 54, W - 2 * PAD, 16, v, (30, 70, 55), LIME)
        y += 104
    _footer(img, w, c["bot"], WHITE, (170, 205, 190), LIME, INK)
    return img


def _d_lime_pop(c: dict) -> Image.Image:
    img = Image.new("RGBA", (W, H), LIME)
    zz = _asset("zigzag-ink")
    for y0 in (0, H - zz.height):
        _paste(img, zz if y0 else zz.rotate(180), 0, y0)
    w = c["words"]
    _header(img, "logo-on-lime", w, c["day"], INK)
    d = ImageDraw.Draw(img)
    _text(d, W / 2, 360, c["label"].upper(), _font("Manrope", 34, 800), INK, anchor="m", spacing=5)
    # the band in a big ink disc, the mascot leaning on it
    cx, cy, r = W // 2 - 70, 720, 250
    _shadow(img, (cx - r, cy - r, cx + r, cy + r), r, blur=28, alpha=70, offset=16)
    disc = _layer()
    ImageDraw.Draw(disc).ellipse((cx - r, cy - r, cx + r, cy + r), fill=INK)
    img.alpha_composite(disc)
    d = ImageDraw.Draw(img)
    _text(d, cx, cy - 150, w["overall"].upper(), _font("Manrope", 26, 800), LIME, anchor="m", spacing=3)
    big = _font("Unbounded", 190, 800)
    _text(d, cx, cy - 92, _band(c["band"]), big, WHITE, anchor="m")
    pet = _sized(c["mascot"], 330)
    _paste(img, pet, cx + r - 40, cy - 40, rotate=-8)
    d = ImageDraw.Draw(img)
    _text(d, W / 2, 1050, c["cheer"], _fit(d, c["cheer"], "Unbounded", 50, 700, W - 2 * PAD), INK, anchor="m")
    # 2x2 tiles
    tw, th, gap = (W - 2 * PAD - 28) // 2, 150, 22
    for i, (short, full, v) in enumerate(c["rows"]):
        x = PAD + (i % 2) * (tw + gap)
        y = 1120 + (i // 2) * (th + gap)
        _rrect(img, (x, y, x + tw, y + th), 36, INK)
        d = ImageDraw.Draw(img)
        _text(d, x + 34, y + 32, full, _fit(d, full, "Manrope", 28, 700, tw - 68), (170, 230, 200))
        _text(d, x + 34, y + 74, _band(v), _font("Unbounded", 54, 800), LIME)
    _footer(img, w, c["bot"], INK, (6, 32, 24), INK, LIME)
    return img


def _d_paper_ring(c: dict) -> Image.Image:
    img = Image.new("RGBA", (W, H), PAPER)
    _dots(img, (214, 234, 224))
    w = c["words"]
    _header(img, "logo-on-light", w, c["day"], (88, 115, 103))
    d = ImageDraw.Draw(img)
    _text(d, W / 2, 360, w["title"].upper(), _font("Manrope", 32, 800), MOSS, anchor="m", spacing=5)
    _text(d, W / 2, 420, c["label"], _font("Unbounded", 44, 700), INK, anchor="m")
    cx, cy, r = W // 2, 790, 270
    _ring(img, cx, cy, r, 40, c["band"], (214, 236, 225), SKIN)
    d = ImageDraw.Draw(img)
    _text(d, cx, cy - 140, w["overall"], _font("Manrope", 30, 700), (88, 115, 103), anchor="m")
    _text(d, cx, cy - 86, _band(c["band"]), _font("Unbounded", 170, 800), INK, anchor="m")
    _text(d, cx, cy + 110, "/ 9.0", _font("Manrope", 34, 700), (120, 150, 136), anchor="m")
    pet = _sized(c["mascot"], 250)
    _paste(img, pet, cx + r - 70, cy + r - 250)
    d = ImageDraw.Draw(img)
    _text(d, W / 2, 1110, c["cheer"], _fit(d, c["cheer"], "Caveat", 86, 700, W - 2 * PAD), MOSS, anchor="m")
    y = 1220
    for i, (short, full, v) in enumerate(c["rows"]):
        x = PAD + (i % 2) * ((W - 2 * PAD) // 2 + 10)
        yy = y + (i // 2) * 120
        _rrect(img, (x, yy, x + (W - 2 * PAD) // 2 - 10, yy + 100), 50, WHITE, outline=(205, 228, 216), width=3)
        d = ImageDraw.Draw(img)
        _text(d, x + 36, yy + 34, short, _font("Unbounded", 34, 700), MOSS)
        _text(d, x + (W - 2 * PAD) // 2 - 46, yy + 30, _band(v), _font("Unbounded", 40, 800), INK, anchor="r")
    _footer(img, w, c["bot"], INK, (88, 115, 103), INK, LIME)
    return img


def _d_night_glow(c: dict) -> Image.Image:
    img = _gradient((4, 22, 17), (10, 92, 58))
    _glow(img, (160, 520), 300, LIME, 70)
    _glow(img, (W - 120, 1220), 340, SKIN, 80)
    w = c["words"]
    _header(img, "logo-on-dark", w, c["day"], (170, 215, 195))
    d = ImageDraw.Draw(img)
    _text(d, W / 2, 380, c["label"].upper(), _font("Manrope", 32, 800), LIME, anchor="m", spacing=6)
    # a glass panel with the band
    box = (PAD, 450, W - PAD, 900)
    _rrect(img, box, 56, (255, 255, 255, 26), outline=(255, 255, 255, 60), width=2)
    pet = _sized(c["mascot"], 250)
    _paste(img, pet, W - PAD - pet.width + 40, 900 - pet.height + 70, rotate=-6)
    d = ImageDraw.Draw(img)
    _text(d, W / 2, 500, w["overall"].upper(), _font("Manrope", 28, 800), (190, 230, 212), anchor="m", spacing=4)
    _text(d, W / 2, 560, _band(c["band"]), _font("Unbounded", 250, 800), WHITE, anchor="m")
    _text(d, W / 2, 1000, c["cheer"], _fit(d, c["cheer"], "Unbounded", 50, 700, W - 2 * PAD), WHITE, anchor="m")
    if c["best"]:
        _text(d, W / 2, 1076, c["best"], _fit(d, c["best"], "Manrope", 32, 600, W - 2 * PAD), (190, 230, 212),
              anchor="m")
    tw, gap = (W - 2 * PAD - 3 * 20) // 4, 20
    for i, (short, full, v) in enumerate(c["rows"]):
        x = PAD + i * (tw + gap)
        _rrect(img, (x, 1180, x + tw, 1420), 34, (255, 255, 255, 30), outline=(255, 255, 255, 70), width=2)
        d = ImageDraw.Draw(img)
        _text(d, x + tw / 2, 1224, short, _font("Unbounded", 30, 700), LIME, anchor="m")
        _text(d, x + tw / 2, 1290, _band(v), _font("Unbounded", 52, 800), WHITE, anchor="m")
        _bar(img, x + 28, 1378, tw - 56, 10, v, (255, 255, 255, 50), LIME)
    _footer(img, w, c["bot"], WHITE, (190, 230, 212), LIME, INK)
    return img


def _d_split(c: dict) -> Image.Image:
    img = Image.new("RGBA", (W, H), INK)
    top = _layer()
    ImageDraw.Draw(top).polygon([(0, 0), (W, 0), (W, 900), (0, 1080)], fill=LIME)
    img.alpha_composite(top)
    zz = _sized_w("zigzag-lime", W)
    w = c["words"]
    _header(img, "logo-on-lime", w, c["day"], INK)
    d = ImageDraw.Draw(img)
    _text(d, PAD, 380, w["title"], _font("Manrope", 36, 800), INK)
    _text(d, PAD, 440, c["label"], _font("Unbounded", 54, 700), INK)
    _text(d, PAD - 14, 540, _band(c["band"]),
          _fit(d, _band(c["band"]), "Unbounded", 330, 800, W - 2 * PAD - 360, min_size=220), INK)
    pet = _sized(c["mascot"], 420)
    _paste(img, pet, W - pet.width - 50, 560)
    d = ImageDraw.Draw(img)
    _text(d, PAD, 1096, c["cheer"], _fit(d, c["cheer"], "Caveat", 92, 700, W - 2 * PAD), LIME)
    y = 1204
    for short, full, v in c["rows"]:
        d = ImageDraw.Draw(img)
        _text(d, PAD, y, short, _font("Unbounded", 36, 800), LIME)
        _text(d, PAD + 130, y + 4, full, _font("Manrope", 32, 600), (190, 225, 210))
        _text(d, W - PAD, y - 2, _band(v), _font("Unbounded", 40, 800), WHITE, anchor="r")
        y += 58
    _paste(img, zz, 0, H - zz.height)
    _footer(img, w, c["bot"], WHITE, (170, 205, 190), LIME, INK)
    return img


def _d_certificate(c: dict) -> Image.Image:
    img = Image.new("RGBA", (W, H), INK)
    _scales(img, INK_2, (18, 58, 45), r=52)
    w = c["words"]
    card = (64, 250, W - 64, 1430)
    _shadow(img, card, 48, blur=34, alpha=140, offset=24)
    _rrect(img, card, 48, PAPER)
    # a dashed inner frame
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = card[0] + 26, card[1] + 26, card[2] - 26, card[3] - 26
    for x in range(x0 + 30, x1 - 30, 28):
        d.line((x, y0, x + 14, y0), fill=(160, 205, 182), width=3)
        d.line((x, y1, x + 14, y1), fill=(160, 205, 182), width=3)
    for y in range(y0 + 30, y1 - 30, 28):
        d.line((x0, y, x0, y + 14), fill=(160, 205, 182), width=3)
        d.line((x1, y, x1, y + 14), fill=(160, 205, 182), width=3)
    mark = _sized_w("logo-on-light", 300)
    _paste(img, mark, W / 2 - mark.width / 2, 330)
    d = ImageDraw.Draw(img)
    _text(d, W / 2, 470, w["title"].upper(), _font("Manrope", 30, 800), MOSS, anchor="m", spacing=6)
    _text(d, W / 2, 526, c["label"], _font("Unbounded", 46, 700), INK, anchor="m")
    burst = _sized_w("burst", 400)
    bx, by = W / 2 - burst.width / 2, 590
    _paste(img, burst, bx, by)
    d = ImageDraw.Draw(img)
    _text(d, W / 2, by + burst.height / 2 - 74, _band(c["band"]), _font("Unbounded", 136, 800), INK, anchor="m")
    _text(d, W / 2, 1020, c["cheer"], _fit(d, c["cheer"], "Caveat", 78, 700, W - 2 * PAD - 80), MOSS, anchor="m")
    y = 1140
    for short, full, v in c["rows"]:
        d = ImageDraw.Draw(img)
        _text(d, 150, y, full, _font("Manrope", 32, 700), INK)
        _text(d, W - 150, y - 2, _band(v), _font("Unbounded", 36, 800), MOSS, anchor="r")
        d.line((150, y + 50, W - 150, y + 50), fill=(214, 234, 224), width=2)
        y += 64
    pet = _sized(c["mascot"], 230)
    _paste(img, pet, W - 64 - pet.width + 40, 250 - 120, rotate=8)
    _footer(img, w, c["bot"], WHITE, (170, 205, 190), LIME, INK)
    return img


def _d_bento(c: dict) -> Image.Image:
    img = Image.new("RGBA", (W, H), INK)
    w = c["words"]
    _header(img, "logo-on-dark", w, c["day"], (170, 205, 190))
    g = 24
    L, R = PAD - 30, W - PAD + 30
    mid = (L + R) // 2
    # the big band tile (lime) and the mascot tile (moss)
    _rrect(img, (L, 330, mid + 120, 820), 48, LIME)
    _rrect(img, (mid + 120 + g, 330, R, 820), 48, MOSS)
    d = ImageDraw.Draw(img)
    _text(d, L + 50, 380, c["label"].upper(), _fit(d, c["label"].upper(), "Manrope", 28, 800, mid + 40 - L - 50), INK,
          spacing=3)
    _text(d, L + 40, 470, _band(c["band"]), _font("Unbounded", 220, 800), INK)
    _text(d, L + 50, 742, w["overall"], _font("Manrope", 30, 700), (20, 70, 50))
    pet = _sized(c["mascot"], 330)
    cw = R - (mid + 120 + g)
    _paste(img, pet, mid + 120 + g + (cw - pet.width) / 2, 820 - pet.height - 20)
    # the cheer tile
    _rrect(img, (L, 820 + g, R, 1030), 48, (20, 58, 45))
    d = ImageDraw.Draw(img)
    _text(d, W / 2, 900, c["cheer"], _fit(d, c["cheer"], "Caveat", 86, 700, R - L - 80), LIME, anchor="m")
    # four criteria tiles
    tw = (R - L - g) // 2
    colors = [(255, 255, 255), MINT, MINT, (255, 255, 255)]
    for i, (short, full, v) in enumerate(c["rows"]):
        x = L + (i % 2) * (tw + g)
        y = 1050 + (i // 2) * (170 + g)
        _rrect(img, (x, y, x + tw, y + 170), 40, colors[i])
        d = ImageDraw.Draw(img)
        _text(d, x + 40, y + 32, full, _fit(d, full, "Manrope", 28, 700, tw - 80), (60, 95, 80))
        _text(d, x + 40, y + 84, _band(v), _font("Unbounded", 56, 800), INK)
        _bar(img, x + 200, y + 106, tw - 240, 12, v, (210, 228, 219), SKIN)
    _footer(img, w, c["bot"], WHITE, (170, 205, 190), LIME, INK)
    return img


_DRAW = {"ink_scales": _d_ink_scales, "lime_pop": _d_lime_pop, "paper_ring": _d_paper_ring,
         "night_glow": _d_night_glow, "split": _d_split, "certificate": _d_certificate, "bento": _d_bento}


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
        "band": float(band), "rows": rows, "words": words, "bot": bot_username,
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
