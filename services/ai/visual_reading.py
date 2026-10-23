"""A Task 1 visual (chart, graph, table, diagram, map, process) turned into a
checked data sheet before any marking (Task 1 prompt A1-A4, 2026-10-22).
Every later call uses it: the scoring runs, the feedback, the missed-errors
and verification passes, the model answer and its checks.

  1. Two independent reads, in parallel, with different wording: the first
     on the picture as sent, the second on a copy prepared for reading
     (turned upright, plain borders trimmed, contrast raised when the photo
     is flat). When the first read says the picture is sideways or the
     visual fills less than half of it (a phone photo of a book page), a
     third read on the visual alone replaces the second.
  2. The reads are compared figure by figure: one that differs by more than
     its reading precision, or that only one read saw, is UNCERTAIN.
  3. Code checks: every pie adds up to 100% (±1), table totals add up, no
     two legend entries share a colour, every category has a value at every
     time point. A failed check makes its figures uncertain.
  4. The key features a Band 7-8 report has to mention are computed in code
     from the verified figures (services/ai/visual_features.py).
Uncertain figures are never used to mark the student wrong.

Until 2026-10-19 each call read the picture on its own and they disagreed
(report #62: a correct "7%" "corrected" to 8%); from 2026-10-19 to 10-22
one read made the sheet, with nothing to check it against.
"""
from __future__ import annotations

import asyncio
import base64
import difflib
import io
import logging
import re
from collections import Counter

import openai
from PIL import Image, ImageChops, ImageOps, ImageStat

from bot.config import settings
from services.ai import usage
from services.ai.openai_client import is_image_error, parse_flex
from services.ai.schemas import (
    KeyFeatures,
    SheetBlock,
    SheetValue,
    VisualBlock,
    VisualExtraction,
    VisualSheet,
)
from services.ai.visual_features import key_features
from services.storage.file_storage import BadImage, prepare_vision_image

logger = logging.getLogger(__name__)

READ_MAX_TOKENS = 16000
SMALL_VISUAL = 0.45  # the visual fills less of the picture than this: read it again, cropped

_RULES = """\
- One block per visual: a table and two pie charts are 3 blocks; a chart split into panels (Law / \
Business) is one block per panel; a chart with two axes in different units (% on the left, people on \
the right) is one block per axis, each with its own units; a pie with an inner and an outer ring is \
one block per ring. Two or more pictures of the same kind of thing compared side by side (two tunnels, \
two houses) are ONE table block: series = each feature (with its unit, e.g. "Cost (US$ million)"), \
point = each item. visual_type: line, bar, pie, table, map, process, other.
- overall: for every block, its main point in one line - a chart's main trend or contrast, a map's \
overall direction of development, a diagram's main difference.
- series and point are names of things in the data, never notes about where a label is drawn.
- legend: every legend entry with its colour or pattern exactly as drawn - tell shades apart ("dark \
blue" vs "light blue").
- data: every value, one per series and point, in a fixed order. series = the legend item it belongs \
to (without a legend: the bar's, slice's or row's label); point = where it sits on the other \
dimension - the year, the x-axis category, the panel or the table column ('' when there is only one). \
A grouped bar chart of activities by gender: series Boys / Girls, point = each activity.
  - printed=true, precision=0: the figure is written on the visual (a data label, a table cell, a \
number on the diagram). Copy it exactly.
  - printed=false: read off an axis or scale. Find the nearest gridlines; value = your best reading, \
precision = how far off a careful reader could be in the same units (usually half the gap between \
gridlines or less: 1, 2 or 5). On 3D bars read the flat top face against the back gridlines; on a \
line, the centre of each point.
  - value null and text = the words, for a table cell that is words; value null when a figure can't \
be read (and say so in `unreadable`). Never guess a precise number.
  - value is in the visual's units: 27 for 27%, 40 for $40,000 on a scale in thousands of dollars.
- Maps: map_features = every feature (building, road, area), its location and its state on each map \
(present / absent / changed, with a short detail such as "moved to the north", "extended", "replaced \
by a car park").
- Processes: process = every step in order, the logical stages that group them (e.g. energy \
generation, extraction, storage, collection), linear or cyclical, natural or man-made.
- unreadable: anything blurred, cropped, hidden or too small to read. readability: clear; \
partly_unclear when some values can't be read; unreadable when the image holds no usable visual (only \
question text, an unrelated picture, far too blurred).
- chart_box: [left, top, right, bottom] of the visual(s) as fractions of the image; rotation: degrees \
clockwise to turn the image upright (0 if it already is).
Never invent anything the visual doesn't show. The question text is context only: if it states \
figures, they must agree with what you read."""

READ_A = f"""You read the visual of an IELTS Academic Writing Task 1 question - a chart, graph, \
table, diagram, map or process - and describe it as DATA. Everything that follows (the scores, the \
feedback on a student's report, the model answer) takes its figures from your description, so be \
careful and honest.

{_RULES}"""

READ_B = f"""You are an IELTS examiner's assistant checking a Task 1 visual figure by figure. Ignore \
any earlier reading: read the picture again from scratch, series by series and point by point, and \
write down only what you can actually see. Read each figure on its own - its data label if one is \
printed, otherwise its position against the scale, gridline by gridline.

{_RULES}"""

# Added after the sheet wherever a student's report is judged (scores, feedback,
# missed-errors pass) - the same rules everywhere, so the same figure is never
# right in one part of the report and wrong in another.
JUDGE_RULES = """HOW TO JUDGE THE STUDENT'S FIGURES - by the DATA SHEET and the DATA CHECK (when \
given, software already compared every figure of the report with the sheet: a figure it found is \
correct), never by a new reading of the image:
- a printed figure: the student's figure must be that figure (rounding with "about", "nearly", "just \
over" is fine);
- a figure read off the scale (±N): ANY figure within ±N is correct ("around 40%" for 41 ±2) - never \
"correct" a reasonable reading into another reading;
- an UNCERTAIN figure, or one the sheet can't read: never count the student's figure as a mistake;
- a year between two labelled years on a line graph's axis is read from the chart, not invented;
- rankings, equal values, trends and the overview: a mistake only when the verified figures \
contradict them (e.g. "boys lead" where the sheet shows them equal).
A mistake about the data has criterion TR, and its `correct` gives the sheet's figure (e.g. "about 27%")."""

# Added after the sheet where the model answer is written and checked.
ANSWER_RULES = """Use ONLY the verified figures on this DATA SHEET: a printed figure as printed; one \
read off the scale with "about" or "around" - never more precise than the sheet; leave out every \
UNCERTAIN figure and what the sheet can't read; never rank figures the sheet shows as equal."""


class ChartImageRejected(Exception):
    """OpenAI couldn't open the chart picture - every other call would fail the
    same way, so the student is asked for the picture again."""


def _image_parts(images: list[tuple[bytes, str]]) -> list[dict]:
    return [
        {"type": "image_url",
         "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}", "detail": "high"}}
        for data, mime in images
    ]


# --- Preparing a picture for the second read ----------------------------------------------------


def prepare_for_reading(data: bytes, box: list[float] | None = None, rotation: int = 0) -> tuple[bytes, str]:
    """A copy of the picture prepared for reading: turned upright (camera
    EXIF, then `rotation` degrees clockwise), cropped to `box` (fractions,
    with a margin) or else trimmed of plain borders, contrast raised when the
    photo is flat (tones kept, so legend colours stay apart), a small result
    enlarged. Falls back to the picture as it was."""
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
        if rotation in (90, 180, 270):
            image = image.rotate(-rotation, expand=True)
        w, h = image.size
        if box and len(box) == 4 and 0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1:
            m = 0.03
            image = image.crop((int(max(0, box[0] - m) * w), int(max(0, box[1] - m) * h),
                                int(min(1, box[2] + m) * w), int(min(1, box[3] + m) * h)))
        else:
            background = Image.new("RGB", image.size, image.getpixel((0, 0)))
            bbox = ImageChops.difference(image, background).convert("L").point(lambda p: 255 if p > 24 else 0).getbbox()
            if bbox and (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) > 0.3 * w * h:
                pad = 12
                image = image.crop((max(0, bbox[0] - pad), max(0, bbox[1] - pad),
                                    min(w, bbox[2] + pad), min(h, bbox[3] + pad)))
        if ImageStat.Stat(image.convert("L")).stddev[0] < 45:
            image = ImageOps.autocontrast(image, cutoff=1, preserve_tone=True)
        if max(image.size) < 1200:
            scale = 1600 / max(image.size)
            image = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        image.save(buf, format="PNG", optimize=True)
        content, mime, _ = prepare_vision_image(buf.getvalue())
        return content, mime
    except (BadImage, OSError, ValueError) as e:
        logger.warning("Preparing the chart for its second read failed (%r) - the picture as sent", e)
        return data, "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"


# --- The reads --------------------------------------------------------------------------------------


async def _read(system: str, question: str, images: list[tuple[bytes, str]], label: str) -> VisualExtraction | None:
    """One read. None when it gave nothing usable; raises ChartImageRejected
    when OpenAI can't open the picture."""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": [
            {"type": "text", "text": f"QUESTION:\n{question}\n\nDescribe the visual in the attached image(s) as data."},
            *_image_parts(images),
        ]},
    ]
    try:
        response = await parse_flex(  # the Flex tier, the default as a fallback (openai_client)
            model=settings.openai_model_vision,
            messages=messages,
            response_format=VisualExtraction,
            max_completion_tokens=READ_MAX_TOKENS,
            # Sent as extra_body: openai 1.57 has no reasoning_effort argument yet.
            extra_body={"reasoning_effort": settings.openai_chart_reasoning_effort},
        )
    except openai.LengthFinishReasonError as e:
        if getattr(e, "completion", None) is not None:
            usage.record("chart", e.completion)
        logger.warning("Chart %s ran past %d output tokens", label, READ_MAX_TOKENS)
        return None
    except Exception as e:
        if is_image_error(e):
            raise ChartImageRejected(str(e)) from e
        logger.warning("Chart %s failed: %r", label, e)
        return None
    usage.record("chart", response)
    message = response.choices[0].message
    reading = message.parsed
    if reading is None:
        logger.warning("Chart %s gave nothing usable (refusal: %r)", label, getattr(message, "refusal", None))
        return None
    if reading.readability != "unreadable" and not any(
            b.data or b.map_features or (b.process and b.process.steps) for b in reading.visuals):
        logger.warning("Chart %s listed no data", label)
        return None
    return reading


def _box_area(box: list[float]) -> float:
    if len(box) != 4 or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
        return 1.0
    return (box[2] - box[0]) * (box[3] - box[1])


async def read_visual(question: str, images: list[tuple[bytes, str]]) -> VisualSheet | None:
    """The checked data sheet of the Task 1 visual in `images`. None when no
    read worked for some other reason: the check then goes on with each call
    reading the picture itself. Raises ChartImageRejected when OpenAI can't
    open the picture."""
    prepared = [prepare_for_reading(data) for data, _ in images]
    results = await asyncio.gather(
        _read(READ_A, question, images, "read 1"), _read(READ_B, question, prepared, "read 2"),
        return_exceptions=True)
    rejected = [r for r in results if isinstance(r, ChartImageRejected)]
    a, b = (None if isinstance(r, BaseException) else r for r in results)
    for r in results:
        if isinstance(r, BaseException) and not isinstance(r, ChartImageRejected):
            logger.warning("A chart read failed: %r", r)
    if a is None and b is None:
        if len(rejected) == 2:
            raise rejected[0]
        return None
    if a is not None and a.readability != "unreadable" and len(images) == 1 and (
            a.rotation in (90, 180, 270) or _box_area(a.chart_box) < SMALL_VISUAL):
        # A sideways picture, or a small visual in a big photo: read the visual alone.
        cropped = prepare_for_reading(images[0][0], box=a.chart_box, rotation=a.rotation)
        try:
            c = await _read(READ_B, question, [cropped], "read 3 (cropped)")
        except ChartImageRejected:
            c = None
        if c is not None:
            b = c
    sheet = combine(a, b)
    counts = Counter(v.status for blk in sheet.blocks for v in blk.values)
    logger.info("Chart read: %s, %d reads, %s, %s%s; checks: %s", ", ".join(blk.visual_type for blk in sheet.blocks),
                sheet.reads, dict(counts), sheet.readability,
                f" - {sheet.readability_note}" if sheet.readability_note else "", "; ".join(sheet.checks) or "none")
    return sheet


# --- Combining the two reads ---------------------------------------------------------------------------


def _norm(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", label.lower())


def _match_labels(left: list[str], right: list[str]) -> dict[str, str]:
    """left label -> right label: the same words, else the most similar (0.75+)."""
    out, free = {}, list(dict.fromkeys(right))
    for x in dict.fromkeys(left):
        hit = next((y for y in free if _norm(y) == _norm(x)), None)
        if hit is None:
            scored = [(difflib.SequenceMatcher(None, _norm(x), _norm(y)).ratio(), y) for y in free]
            best = max(scored, default=(0, None))
            hit = best[1] if best[0] >= 0.75 else None
        if hit is not None:
            out[x] = hit
            free.remove(hit)
    return out


def _pair_blocks(a: list[VisualBlock], b: list[VisualBlock]) -> list[tuple[VisualBlock, VisualBlock | None]]:
    if len(a) == len(b):
        return list(zip(a, b))
    pairs, free = [], list(b)
    for x in a:
        same = [y for y in free if y.visual_type == x.visual_type]
        best = max(same, key=lambda y: difflib.SequenceMatcher(None, _norm(x.title), _norm(y.title)).ratio(),
                   default=None)
        if best is not None:
            free.remove(best)
        pairs.append((x, best))
    return pairs


def _single(block: VisualBlock, status: str, note: str = "") -> SheetBlock:
    return SheetBlock(
        visual_type=block.visual_type, title=block.title, units=block.units, time_points=block.time_points,
        legend=block.legend, map_features=block.map_features, overall=block.overall, process=block.process,
        values=[SheetValue(series=v.series, point=v.point, value=v.value, text=v.text, printed=v.printed,
                           precision=0.0 if v.printed else max(v.precision, 0.0), status=status, note=note)
                for v in block.data],
    )


def _transposed(block: VisualBlock) -> list:
    return [v.model_copy(update={"series": v.point, "point": v.series}) for v in block.data]


def _merge_values(x: VisualBlock, y: VisualBlock) -> list[SheetValue]:
    # The second read may have put the legend on the other dimension (years as
    # series instead of points - Volume 1 pp.24, 36): its figures are taken
    # the way round that matches more of the first read's.
    def keyed(data):
        series = _match_labels([v.series for v in x.data], [v.series for v in data])
        points = _match_labels([v.point for v in x.data], [v.point for v in data])
        other = {(v.series, v.point): v for v in data}
        hits = sum((series.get(v.series), points.get(v.point)) in other for v in x.data)
        return hits, series, points, other, data

    hits, series, points, other, y_data = max(keyed(y.data), keyed(_transposed(y)), key=lambda k: k[0])
    used, out = set(), []
    for v in x.data:
        w = other.get((series.get(v.series), points.get(v.point)))
        prec = 0.0 if v.printed else max(v.precision, 0.0)
        if w is None:
            out.append(SheetValue(series=v.series, point=v.point, value=v.value, text=v.text, printed=v.printed,
                                  precision=prec, status="uncertain", note="seen by one read only"))
            continue
        used.add((w.series, w.point))
        if v.value is not None and w.value is not None:
            tol = max(prec, 0.0 if w.printed else w.precision, 0.5)
            if abs(v.value - w.value) <= tol:
                printed = v.printed or w.printed
                value = v.value if v.printed or not w.printed else w.value
                if not printed:
                    value = round(v.value + w.value) / 2  # the mean, to the nearest 0.5
                out.append(SheetValue(series=v.series, point=v.point, value=value, text="", printed=printed,
                                      precision=0.0 if printed else max(prec, w.precision, abs(v.value - w.value) / 2),
                                      status="verified"))
            else:
                # Kept between the two reads, wide enough to cover both: never
                # used to mark the student, only to rank what clearly differs.
                spread = abs(v.value - w.value) / 2 + max(prec, 0.0 if w.printed else w.precision)
                out.append(SheetValue(series=v.series, point=v.point, value=(v.value + w.value) / 2, text="",
                                      printed=False, precision=spread, status="uncertain",
                                      note=f"the reads disagree: {v.value:g} vs {w.value:g}"))
        elif v.value is None and w.value is None and v.text and _norm(v.text) == _norm(w.text):
            out.append(SheetValue(series=v.series, point=v.point, value=None, text=v.text, printed=True,
                                  precision=0.0, status="verified"))
        else:
            out.append(SheetValue(series=v.series, point=v.point, value=v.value, text=v.text, printed=v.printed,
                                  precision=prec, status="uncertain",
                                  note=f"the reads disagree: {v.value if v.value is not None else v.text!r} vs "
                                       f"{w.value if w.value is not None else w.text!r}"))
    for w in y_data:
        if (w.series, w.point) not in used:
            out.append(SheetValue(series=w.series, point=w.point, value=w.value, text=w.text, printed=w.printed,
                                  precision=0.0 if w.printed else max(w.precision, 0.0), status="uncertain",
                                  note="seen by one read only"))
    return out


def _merge_map(x: VisualBlock, y: VisualBlock, unreadable: list[str]):
    names = _match_labels([f.name for f in x.map_features], [f.name for f in y.map_features])
    other = {f.name: f for f in y.map_features}
    kept = []
    for f in x.map_features:
        g = other.get(names.get(f.name, ""))
        if g is None:
            unreadable.append(f"Map: {f.name} - seen by one read only")
            continue
        if [s.status for s in f.states] != [s.status for s in g.states]:
            unreadable.append(f"Map: {f.name} - the reads disagree on what changed")
            continue
        kept.append(f)
    return kept


def combine(a: VisualExtraction | None, b: VisualExtraction | None) -> VisualSheet:
    """The sheet from two reads (either may be None: then the other alone,
    as read)."""
    unreadable: list[str] = []
    checks: list[str] = []
    if a is None or b is None:
        one = a or b
        blocks = [_single(x, "verified") for x in one.visuals]
        readability, note, reads = one.readability, one.readability_note, 1
        unreadable += one.unreadable
        checks.append("only one read worked: its figures are used as read")
    else:
        if a.readability == "unreadable" and b.readability == "unreadable":
            readability = "unreadable"
        elif a.readability == "clear" and b.readability == "clear":
            readability = "clear"
        else:
            readability = "partly_unclear"
        note = a.readability_note or b.readability_note
        unreadable += list(dict.fromkeys(a.unreadable + b.unreadable))
        reads = 2
        blocks = []
        for x, y in _pair_blocks(a.visuals, b.visuals):
            if y is None:
                blocks.append(_single(x, "uncertain", "seen by one read only"))
                continue
            block = _single(x, "verified")
            block.values = _merge_values(x, y)
            if x.visual_type == "map":
                block.map_features = _merge_map(x, y, unreadable)
            if x.process and y.process and abs(len(x.process.steps) - len(y.process.steps)) > 1:
                checks.append(f"the reads found {len(x.process.steps)} and {len(y.process.steps)} steps")
            blocks.append(block)
    for block in blocks:
        _check(block, checks)
    if any(v.status == "uncertain" for blk in blocks for v in blk.values) and readability == "clear":
        readability = "partly_unclear"
        note = note or "some figures could not be confirmed"
    sheet = VisualSheet(blocks=blocks, readability=readability, readability_note=note, unreadable=unreadable,
                        checks=checks, features=KeyFeatures(expected_overview=[], key_comparisons=[],
                                                            suggested_grouping=""), reads=reads)
    if readability != "unreadable":
        sheet.features = key_features(sheet)
    return sheet


def _uncertain(values: list[SheetValue], note: str) -> None:
    for v in values:
        if v.status == "verified":
            v.status, v.note = "uncertain", note


def _check(block: SheetBlock, checks: list[str]) -> None:
    """The code checks; a failed one makes its figures uncertain."""
    name = block.title or block.visual_type
    numeric = [v for v in block.values if v.value is not None]
    if block.visual_type == "pie":
        by_point: dict[str, list[SheetValue]] = {}
        for v in numeric:
            by_point.setdefault(v.point, []).append(v)
        for point, vals in by_point.items():
            total = sum(v.value for v in vals)
            tol = 1 + sum(v.precision for v in vals)
            label = f"{name}{f' ({point})' if point else ''}"
            if "%" in block.units or 80 <= total <= 120:
                if abs(total - 100) > tol:
                    _uncertain(vals, f"the slices add up to {total:g}%, not 100%")
                    checks.append(f"{label}: the slices add up to {total:g}%, not 100% - not used to mark mistakes")
                else:
                    checks.append(f"{label}: the slices add up to {total:g}% - OK")
    # Totals in a table (or any block with a "Total" row or column).
    total_re = re.compile(r"\b(total|all|overall|sum)\b", re.IGNORECASE)
    for dim, other in (("series", "point"), ("point", "series")):
        totals = {getattr(v, other): v for v in numeric if total_re.search(getattr(v, dim))}
        for key, tv in totals.items():
            parts = [v for v in numeric if getattr(v, other) == key and not total_re.search(getattr(v, dim))]
            if len(parts) < 2:
                continue
            s = sum(v.value for v in parts)
            tol = 0.5 * len(parts) + sum(v.precision for v in parts) + tv.precision + 0.01 * abs(tv.value)
            if abs(s - tv.value) > tol:
                _uncertain(parts + [tv], f"its parts add up to {s:g}, not the total {tv.value:g}")
                checks.append(f"{name}: the parts of {key or 'the total'} add up to {s:g}, not {tv.value:g}")
    # Every legend colour stands for one category.
    colours: dict[str, list[str]] = {}
    for entry in block.legend:
        colours.setdefault(_norm(entry.colour), []).append(entry.label)
    for colour, labels in colours.items():
        if colour and len(labels) > 1:
            same = set(_norm(x) for x in labels)
            _uncertain([v for v in block.values if _norm(v.series) in same],
                       "its legend colour is shared with another category")
            checks.append(f"{name}: {', '.join(labels)} share one legend colour - their figures are not used to mark mistakes")
    # Every category at every time point.
    series = list(dict.fromkeys(v.series for v in numeric))
    points = list(dict.fromkeys(v.point for v in numeric))
    if len(series) >= 2 and len(points) >= 2:
        have = {(v.series, v.point) for v in numeric}
        missing = [f"{s} ({p})" for s in series for p in points if (s, p) not in have]
        if missing:
            checks.append(f"{name}: no figure for {', '.join(missing[:6])}")


# --- The sheet as text -------------------------------------------------------------------------------


def _fig(v: SheetValue, units: str) -> str:
    if v.value is None:
        return v.text or "can't be read"
    n = f"{v.value:g}" + ("%" if "%" in units else "")
    if v.printed:
        return f"{n} (printed)"
    return f"about {n} (±{v.precision:g})"


def sheet_text(sheet: VisualSheet | None) -> str:
    """The data sheet as the text every later call gets ("" without one)."""
    if sheet is None or sheet.readability == "unreadable":
        return ""
    lines = ["DATA SHEET - the visual, read twice and checked by software. Every figure in the scores, the "
             "feedback and the model answer comes from it."]
    if sheet.reads == 1:
        lines[0] = lines[0].replace("read twice and checked", "read once and checked")
    uncertain = []
    for i, blk in enumerate(sheet.blocks, 1):
        head = f"Visual {i}: {blk.visual_type}"
        extras = [x for x in (blk.title, f"units: {blk.units}" if blk.units else "",
                              f"time points: {', '.join(blk.time_points)}" if blk.time_points else "") if x]
        lines.append(head + (f" ({'; '.join(extras)})" if extras else ""))
        rows: dict[str, list[str]] = {}
        for v in blk.values:
            if v.status == "uncertain":
                uncertain.append(f"{v.series}{f' ({v.point})' if v.point else ''}: {_fig(v, blk.units)} - {v.note}")
                continue
            rows.setdefault(v.series, []).append(f"{v.point + ' ' if v.point else ''}{_fig(v, blk.units)}")
        lines += [f"- {s}: " + "; ".join(figs) for s, figs in rows.items()]
        for f in blk.map_features:
            states = "; ".join(f"{s.time} {s.status}" + (f" ({s.detail})" if s.detail else "") for s in f.states)
            lines.append(f"- {f.name} [{f.location}]: {states}")
        if blk.process:
            lines += [f"- step {k}: {step}" for k, step in enumerate(blk.process.steps, 1)]
            lines.append(f"- {blk.process.kind}, {blk.process.natural_or_manmade}; stages: "
                         + "; ".join(f"{s.name} (steps {', '.join(map(str, s.steps))})" for s in blk.process.stages))
    if uncertain:
        lines.append("UNCERTAIN - never mark the student wrong on these, never use them in the model answer:")
        lines += [f"- {x}" for x in uncertain]
    if sheet.unreadable:
        lines.append("Can't be read: " + "; ".join(sheet.unreadable))
    if sheet.checks:
        lines.append("Software checks: " + "; ".join(sheet.checks))
    f = sheet.features
    if f.expected_overview or f.key_comparisons:
        lines.append("KEY FEATURES (computed by software from the verified figures) - what a Band 7-8 report covers:")
        if f.expected_overview:
            lines.append("Expected overview:")
            lines += [f"- {x}" for x in f.expected_overview]
        if f.key_comparisons:
            lines.append("Key comparisons:")
            lines += [f"- {x}" for x in f.key_comparisons]
        if f.suggested_grouping:
            lines.append(f"A logical grouping: {f.suggested_grouping} (another grouping that works is fine).")
    return "\n".join(lines)
