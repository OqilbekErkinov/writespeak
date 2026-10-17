"""The student's statistics card (PNG, 1080x1350) in the Bandzilla brand:
how many checks, the average and best band, the bands over time (Writing in
Lime, Speaking in Spike Yellow), each criterion's average over the latest
works, the weakest criterion, and the mascot in a mood that fits.

Drawn with Pillow like the share card (services/image/share_card.py), from
the works db.crud.get_works_since returns. Manrope has no ʻ/ʼ glyphs: Uzbek
text here uses ‘.
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw

from services.image.share_card import _asset, _font, _lines, _text

W, H = 1080, 1350
PAD = 64
INK, LIME, WHITE, YELLOW, MINT = (6, 32, 24), (91, 242, 166), (255, 255, 255), (255, 224, 102), (201, 247, 221)
PANEL, TRACK, SOFT, GRID = (11, 42, 33), (24, 64, 50), (176, 204, 192), (34, 74, 60)
_TASHKENT = timedelta(hours=5)
RECENT = 5  # works per skill behind the criterion averages

_TEXT = {
    "uz": {
        "title": "STATISTIKA", "checks": "tekshiruv", "avg": "o‘rtacha Band", "best": "eng yaxshi",
        "chart": "Ball dinamikasi", "crit": "Mezonlar · so‘nggi {n} ta ish", "none": "hali yo‘q",
        "weak": "Eng zaif joy", "strong": "Eng kuchli joy",
    },
    "ru": {
        "title": "СТАТИСТИКА", "checks": "проверок", "avg": "средний Band", "best": "лучший",
        "chart": "Динамика баллов", "crit": "Критерии · последние {n} работ", "none": "пока нет",
        "weak": "Слабое место", "strong": "Сильная сторона",
    },
    "en": {
        "title": "STATISTICS", "checks": "checks", "avg": "average Band", "best": "best",
        "chart": "Band trend", "crit": "Criteria · last {n} works", "none": "none yet",
        "weak": "Weakest area", "strong": "Strongest area",
    },
}
W_CRITERIA = (("TA", "Task"), ("CC", "Coherence"), ("LR", "Lexical"), ("GRA", "Grammar"))
S_CRITERIA = (("FC", "Fluency"), ("LR", "Lexical"), ("GRA", "Grammar"), ("Pronunciation", "Pronunc."))
CRITERION_NAMES = {
    ("w", "TA"): "Task Response", ("w", "CC"): "Coherence & Cohesion", ("w", "LR"): "Lexical Resource (Writing)",
    ("w", "GRA"): "Grammar (Writing)", ("s", "FC"): "Fluency & Coherence", ("s", "LR"): "Lexical Resource (Speaking)",
    ("s", "GRA"): "Grammar (Speaking)", ("s", "Pronunciation"): "Pronunciation",
}


def _scales(img: Image.Image) -> None:
    draw = ImageDraw.Draw(img)
    r = 60
    for row, y in enumerate(range(0, H + r, r)):
        for x in range(-2 * r + (row % 2) * r, W + r, 2 * r):
            draw.ellipse((x - r + 2, y - r + 2, x + r - 2, y + r - 2), fill=(10, 39, 30), outline=(16, 52, 41), width=4)


def criterion_averages(works: list[dict], kind: str) -> dict[str, float]:
    """Each criterion's average over the latest RECENT works of one skill."""
    latest = [w for w in works if w["kind"] == kind][:RECENT]
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for work in latest:
        for key, value in (work["criteria"] or {}).items():
            if value is not None:
                sums[key] = sums.get(key, 0.0) + value
                counts[key] = counts.get(key, 0) + 1
    return {k: sums[k] / counts[k] for k in sums}


def weakest(works: list[dict]) -> tuple[str, float] | None:
    """(name, average) of the lowest criterion across both skills."""
    found = [(avg, CRITERION_NAMES[(kind, key)])
             for kind in ("w", "s") for key, avg in criterion_averages(works, kind).items()
             if (kind, key) in CRITERION_NAMES]
    if not found:
        return None
    avg, name = min(found)
    return name, avg


def build_stats_card(*, student_name: str, works: list[dict], bot_username: str, lang: str = "uz") -> bytes:
    """`works`: db.crud.get_works_since rows (newest first), at least one."""
    words = _TEXT.get(lang, _TEXT["uz"])
    img = Image.new("RGBA", (W, H), INK)
    _scales(img)
    draw = ImageDraw.Draw(img)

    bands = [w["band"] for w in works]
    best, average = max(bands), sum(bands) / len(bands)
    writing = [w for w in works if w["kind"] == "w"]
    speaking = [w for w in works if w["kind"] == "s"]

    # header
    logo = _asset("logo-on-dark")
    logo = logo.resize((300, round(logo.height * 300 / logo.width)), Image.LANCZOS)
    img.alpha_composite(logo, (PAD, PAD))
    title_font = _font("Manrope", 26, 800)
    tw = sum(draw.textlength(c, font=title_font) + 4 for c in words["title"])
    _text(draw, (W - PAD - tw, PAD + 18), words["title"], title_font, LIME, spacing=4)
    name_font = _font("Unbounded", 50, 700)
    name = student_name
    while draw.textlength(name, font=name_font) > W - 2 * PAD and len(name) > 4:
        name = name[:-2]
    if name != student_name:
        name = name.rstrip() + "…"
    _text(draw, (PAD, PAD + 112), name, name_font, WHITE)

    # three tiles
    y = PAD + 200
    tile_w = (W - 2 * PAD - 2 * 20) // 3
    tiles = (
        (str(len(works)), f"{words['checks']} · W {len(writing)} · S {len(speaking)}"),
        (f"{average:.1f}", words["avg"]),
        (f"{best:.1f}", words["best"]),
    )
    for i, (value, label) in enumerate(tiles):
        x = PAD + i * (tile_w + 20)
        draw.rounded_rectangle((x, y, x + tile_w, y + 170), radius=26, fill=PANEL, outline=TRACK, width=2)
        _text(draw, (x + 28, y + 30), value, _font("Unbounded", 64, 800), LIME if i else WHITE)
        _text(draw, (x + 28, y + 122), label, _font("Manrope", 23, 700), SOFT)

    # bands over time
    y += 200
    draw.rounded_rectangle((PAD, y, W - PAD, y + 330), radius=28, fill=PANEL, outline=TRACK, width=2)
    _text(draw, (PAD + 32, y + 30), words["chart"], _font("Unbounded", 30, 700), WHITE)
    legend_font = _font("Manrope", 24, 800)
    lx = W - PAD - 32
    for label, colour in (("Speaking", YELLOW), ("Writing", LIME)):
        lx -= draw.textlength(label, font=legend_font)
        _text(draw, (lx, y + 34), label, legend_font, colour)
        lx -= 30
        draw.ellipse((lx, y + 38, lx + 18, y + 56), fill=colour)
        lx -= 26
    left, right, top, bottom = PAD + 84, W - PAD - 40, y + 92, y + 296
    axis_font = _font("Manrope", 22, 700)
    for band in range(4, 10):
        gy = bottom - (band - 4) / 5 * (bottom - top)
        draw.line((left, gy, right, gy), fill=GRID, width=2)
        _text(draw, (PAD + 36, gy - 9), str(band), axis_font, SOFT)
    chrono = list(reversed(works))[-14:]  # oldest first, the latest 14
    n = len(chrono)

    def point(i: int, band: float) -> tuple[float, float]:
        x = left + (i + 0.5) * (right - left) / n
        return x, bottom - (min(max(band, 4), 9) - 4) / 5 * (bottom - top)

    for kind, colour in (("w", LIME), ("s", YELLOW)):
        pts = [point(i, w["band"]) for i, w in enumerate(chrono) if w["kind"] == kind]
        if len(pts) > 1:
            draw.line(pts, fill=colour, width=6, joint="curve")
        for px, py in pts:
            draw.ellipse((px - 11, py - 11, px + 11, py + 11), fill=colour, outline=INK, width=4)

    # criteria
    y += 352
    draw.rounded_rectangle((PAD, y, W - PAD, y + 300), radius=28, fill=PANEL, outline=TRACK, width=2)
    _text(draw, (PAD + 32, y + 30), words["crit"].format(n=RECENT), _font("Unbounded", 26, 700), WHITE)
    col_w = (W - 2 * PAD - 64 - 40) // 2
    for c, (kind, criteria, colour, title) in enumerate((("w", W_CRITERIA, LIME, "Writing"),
                                                          ("s", S_CRITERIA, YELLOW, "Speaking"))):
        cx = PAD + 32 + c * (col_w + 40)
        _text(draw, (cx, y + 84), title, _font("Manrope", 24, 800), colour)
        averages = criterion_averages(works, kind)
        if not averages:
            _text(draw, (cx, y + 128), words["none"], _font("Manrope", 24, 600), SOFT)
            continue
        for r, (key, label) in enumerate(criteria):
            ry = y + 128 + r * 42
            value = averages.get(key)
            _text(draw, (cx, ry), label, _font("Manrope", 22, 700), SOFT)
            bx, bw = cx + 150, col_w - 150 - 60
            draw.rounded_rectangle((bx, ry + 2, bx + bw, ry + 20), radius=9, fill=TRACK)
            if value is not None:
                draw.rounded_rectangle((bx, ry + 2, bx + max(18, bw * value / 9), ry + 20), radius=9, fill=colour)
                _text(draw, (bx + bw + 14, ry - 1), f"{value:.1f}", _font("Unbounded", 22, 700), WHITE)

    # the weakest criterion, and the mascot
    y += 322
    weak = weakest(works)
    box_right = W - PAD - 220
    if weak is not None:
        name, avg = weak
        weak_font = _font("Unbounded", 28, 700)
        lines = _lines(draw, f"{name} · {avg:.1f}", weak_font, box_right - PAD - 64)[:2]
        draw.rounded_rectangle((PAD, y, box_right, y + 76 + 38 * len(lines)), radius=28, fill=MINT)
        _text(draw, (PAD + 32, y + 26), words["weak"], _font("Manrope", 24, 800), (10, 124, 74))
        for i, line in enumerate(lines):
            _text(draw, (PAD + 32, y + 64 + i * 38), line, weak_font, INK)
    pet = _asset("mascot-cool_cape" if best >= 7 else "mascot-happy")
    pet = pet.resize((round(pet.width * 220 / pet.height), 220), Image.LANCZOS)
    img.alpha_composite(pet, (W - PAD - pet.width + 10, H - 40 - pet.height))
    draw = ImageDraw.Draw(img)

    today = (datetime.now(timezone.utc) + _TASHKENT).strftime("%d.%m.%Y")
    _text(draw, (PAD, H - 52), f"@{bot_username} · {today}", _font("Manrope", 22, 700), SOFT)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()
