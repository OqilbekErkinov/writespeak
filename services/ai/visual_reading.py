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
import copy
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
    FocusedRead,
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
# A table's every cell is written out, with its reasoning: one read of a
# 3x4 table (v1-p080) ran past 16000 tokens on 2026-10-27. A question that
# names a table gets more room from the start; any other read that runs out
# is tried once more with it.
READ_MAX_TOKENS_TABLE = 32000
SMALL_VISUAL = 0.45  # the visual fills less of the picture than this: read it again, cropped

_RULES = """\
- One block per visual: a table and two pie charts are 3 blocks; a chart split into panels (Law / \
Business) is one block per panel; a chart with two axes in different units (% on the left, people on \
the right) is one block per axis, each with its own units; a pie with an inner and an outer ring is \
one block per ring. Two or more pictures of the same kind of thing compared side by side (two tunnels, \
two houses) are ONE table block: series = each feature (with its unit, e.g. "Cost (US$ million)"), \
point = each item. visual_type: line, bar, pie, table, map, process, other.
- axis_labels: the tick labels on each axis exactly as printed, in order (years, categories, values); \
time_points: the time labels exactly as printed - never add or shift a year the axis doesn't print \
(an axis of 1984, 1988, 1992 ... is not 1987, 1990, 1993).
- overall: for every block, its main point in one line - a chart's main trend or contrast, a map's \
overall direction of development, a diagram's main difference.
- series and point are names of things in the data, never notes about where a label is drawn.
- legend: every legend entry with its colour or pattern exactly as drawn - tell shades apart ("dark \
blue" vs "light blue").
- data: every value, one per series and point, in a fixed order. series = the legend item it belongs \
to (without a legend: the bar's, slice's or row's label); point = where it sits on the other \
dimension - the year, the x-axis category, the panel or the table column ('' when there is only one). \
A grouped bar chart of activities by gender: series Boys / Girls, point = each activity.
- Grouped bars: in each group the bars stand in the legend's order. A missing bar or an empty cell is 0 \
(or "no data") - write it as 0, and never shift the next bar into the empty slot. bars_drawn: for every \
group, how many bars are actually drawn.
- Pies: match each slice to its legend entry by its pattern or colour AND by the percentage printed by \
it. When two legend patterns look alike (two greys, two textures), say so in `unreadable`.
- Line graphs: tell the lines apart by their legend style at two points - where each line starts and \
where it ends - before reading any value; read each point against the two gridlines around it (how far \
between them it is).
  - printed=true, precision=0: the figure is written on the visual (a data label, a table cell, a \
number on the diagram). Copy it exactly.
  - printed=false: read off an axis or scale. Find the nearest gridlines; value = your best reading, \
precision = how far off a careful reader could be in the same units - about a quarter of the gap \
between gridlines for a bar (a bar ending on or halfway to a gridline is read closely), half the gap for \
a point on a line, never more than half. On 3D bars read the flat top face against the back gridlines; \
on a line, the centre of each point.
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
- a year printed on the axis, or between the first and last labelled years, is never a TR mistake;
- rankings, equal values, trends and the overview: a mistake only when the verified figures \
contradict them (e.g. "boys lead" where the sheet shows them equal); a trend ("fell", "stayed \
stable") is wrong only when the change between its two figures is larger than both their ±ranges \
together and goes the other way;
- a claim about the writer's own grouping of the steps or categories ("in the final stage" of the \
three stages the writer defined) is never a data error - a different valid grouping is never a \
mistake; only a wrong step order or a missing step counts;
- a paraphrase of a chart label ("age at death" for "life expectancy", "proximity to home" for "close \
to parental home") is good writing, never a mistake.
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


def prepare_for_reading(data: bytes, box: list[float] | None = None, rotation: int = 0,
                        zoom: bool = False) -> tuple[bytes, str]:
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
        target = 2400 if zoom else 1600  # zoom: one figure read again, as large as the vision model takes
        if max(image.size) < (target if zoom else 1200):
            scale = target / max(image.size)
            image = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        image.save(buf, format="PNG", optimize=True)
        content, mime, _ = prepare_vision_image(buf.getvalue())
        return content, mime
    except (BadImage, OSError, ValueError) as e:
        logger.warning("Preparing the chart for its second read failed (%r) - the picture as sent", e)
        return data, "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"


# --- The reads --------------------------------------------------------------------------------------


async def _read(system: str, question: str, images: list[tuple[bytes, str]], label: str,
                limit: int | None = None) -> VisualExtraction | None:
    """One read. None when it gave nothing usable; raises ChartImageRejected
    when OpenAI can't open the picture."""
    if limit is None:
        limit = READ_MAX_TOKENS_TABLE if re.search(r"\btables?\b", question, re.IGNORECASE) else READ_MAX_TOKENS
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
            max_completion_tokens=limit,
            # Sent as extra_body: openai 1.57 has no reasoning_effort argument yet.
            extra_body={"reasoning_effort": settings.openai_chart_reasoning_effort},
        )
    except openai.LengthFinishReasonError as e:
        if getattr(e, "completion", None) is not None:
            usage.record("chart", e.completion)
        logger.warning("Chart %s ran past %d output tokens", label, limit)
        if limit < READ_MAX_TOKENS_TABLE:
            return await _read(system, question, images, f"{label} (more room)", READ_MAX_TOKENS_TABLE)
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


async def first_read(question: str, images: list[tuple[bytes, str]]) -> VisualExtraction | None:
    """Read 1 alone - enough to recognise a chart that has a checked key
    (services/ai/chart_keys.py). Raises ChartImageRejected."""
    return await _read(READ_A, question, images, "read 1")


async def read_visual(question: str, images: list[tuple[bytes, str]],
                      first: VisualExtraction | None = None) -> VisualSheet | None:
    """The checked data sheet of the Task 1 visual in `images`. None when no
    read worked for some other reason: the check then goes on with each call
    reading the picture itself. Raises ChartImageRejected when OpenAI can't
    open the picture. `first`: read 1, already made - only read 2 runs."""
    prepared = [prepare_for_reading(data) for data, _ in images]
    if first is None:
        results = await asyncio.gather(
            _read(READ_A, question, images, "read 1"), _read(READ_B, question, prepared, "read 2"),
            return_exceptions=True)
    else:
        results = [first, *await asyncio.gather(_read(READ_B, question, prepared, "read 2"), return_exceptions=True)]
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


# --- One figure read again, before a student's figure is called wrong ---------------------------------

FOCUS_MAX = 4  # focused re-reads per report
FOCUS = """You check ONE figure in a student's IELTS Task 1 report against the visual - nothing else. \
First decide which figure on the visual the student means, from their sentence: its series (legend item, \
or the bar's, slice's or row's label) and its point (year, x-axis category, column), as the visual labels \
them - a sentence may name another category only to compare ("the same as the figure for comedy"). \
found=false when you can't tell which figure is meant. Then read only that figure, as carefully as you can:
- printed on the visual (a data label, a table cell): copy it exactly;
- a bar: find its group, then its place in the legend's order; a bar that is not drawn is 0 \
(missing=true) - never take the next bar's height;
- a point on a line: tell the line by its legend style at two points (where it starts and where it ends), \
then find the two gridlines around the point and how far between them it is;
- a pie slice: match it by its pattern or colour AND by the percentage printed by it; when two legend \
patterns look alike, found=false."""


async def read_one(question: str, images: list[tuple[bytes, str]], sentence: str, figure: str) -> FocusedRead | None:
    """The figure a student's sentence gives, read once more on its own, on
    an enlarged copy - the two first reads share their model and their
    rules, so they can share a mistake (#53: both read every 1980 point
    about 20 too low). The model tells which figure is meant: keyword
    matching can't ("the same as the figure for comedy - and rising
    marginally to 16% in 2000" is another genre's figure)."""
    what = f"«{figure}» in the student's sentence: \"{sentence}\""
    messages = [
        {"role": "system", "content": FOCUS},
        {"role": "user", "content": [
            {"type": "text", "text": f"QUESTION:\n{question}\n\nThe figure to check: {what}"},
            *_image_parts([prepare_for_reading(d, zoom=True) for d, _ in images]),
        ]},
    ]
    try:
        response = await parse_flex(model=settings.openai_model_vision, messages=messages,
                                    response_format=FocusedRead, max_completion_tokens=8000,
                                    extra_body={"reasoning_effort": settings.openai_chart_reasoning_effort})
    except Exception as e:  # the figure then stays unconfirmed: never a mistake
        logger.warning("Focused re-read of %s failed: %r", what, e)
        return None
    usage.record("chart_check", response)
    return response.choices[0].message.parsed


async def check_report_figures(answer: str, visual: dict, question: str,
                               images: list[tuple[bytes, str]] | None) -> dict:
    """The sheet (as data) after every figure of the report that it doesn't
    show has been read once more on its own (the academic lead, 2026-10-27):
    a figure is wrong only when the sheet and the focused re-read agree with
    each other and both differ from the student by more than the reading
    precision; otherwise the cell becomes UNCERTAIN and the student's figure
    stands. A figure that can't be tied to one cell is set aside
    ("accepted_figures"); one that two readings contradict is listed in
    "confirmed_wrong"; one the sheet, the re-read and the student agree on is
    correct ("agreed_figures"). Years and ratio words are not re-read."""
    from services.ai.visual_check import figure_agrees, figures, find_cell, is_difference, match_figure

    visual = copy.deepcopy(visual)
    accepted, confirmed, agreed, reads = [], [], [], 0
    for fig in figures(answer):
        # a change or a gap ("rose by 2 points") is no figure a reading shows: it stays
        # "not found" for the judge to work out from the sheet, never re-read as a level
        if fig.get("ratio") or fig["year"] or is_difference(fig) or match_figure(fig, visual, question) != "miss":
            continue
        if reads >= FOCUS_MAX or not images:
            accepted.append(fig["text"])
            continue
        reads += 1
        got = await read_one(question, images, fig["sentence"], fig["text"])
        value = None if got is None or not got.found else (0.0 if got.missing else got.value)
        cell = find_cell(visual, got.series, got.point) if got is not None and got.found else None
        if visual.get("chart_key") and cell is not None and cell.get("value") is not None:
            # a checked answer key is the truth: the re-read only tells which figure is meant
            right = figure_agrees(fig, cell["value"], cell.get("precision") or 0.0)
            (agreed if right else confirmed).append(fig["text"])
            continue
        # A cell the two reads disagreed on (its value and ± cover both) may be
        # settled by a third reading; one a code check made uncertain (look-alike
        # legend patterns, a missing bar, slices off 100%) never is.
        structural = cell is not None and cell.get("status") == "uncertain" \
            and not str(cell.get("note", "")).startswith("the reads disagree")
        sheet_agrees = value is not None and cell is not None and cell.get("value") is not None \
            and abs(value - cell["value"]) <= max(cell.get("precision") or 0.0, 0.5)
        if value is None or cell is None or cell.get("value") is None or structural:
            accepted.append(fig["text"])  # nothing for the sheet to second: the student's figure stands
        elif sheet_agrees and figure_agrees(fig, cell["value"], cell.get("precision") or 0.0):
            # the sheet, the re-read and the student all agree: the matcher just
            # couldn't tie the figure to its cell (#93: "4,300 mg among 12- 19-
            # year-olds") - a correct figure, and the cell stays as sure as it was
            agreed.append(fig["text"])
        elif sheet_agrees and not figure_agrees(fig, value, cell.get("precision") or 0.0):
            cell["status"], cell["note"] = "verified", f"confirmed by a focused re-read ({value:g})"
            confirmed.append(fig["text"])
        else:  # the re-read disagrees with the sheet
            cell["status"], cell["note"] = "uncertain", f"a focused re-read gave {value:g}"
            accepted.append(fig["text"])
            if visual.get("readability") == "clear":
                visual["readability"] = "partly_unclear"
        logger.info("Focused re-read of %s: %s %s = %s (sheet %s)", fig["text"], got.series if got else "?",
                    got.point if got else "?", value, cell.get("value") if cell else None)
    visual["accepted_figures"] = accepted
    visual["confirmed_wrong"] = confirmed
    visual["agreed_figures"] = agreed
    return visual


async def judge_ready(answer: str, visual: dict, question: str,
                      images: list[tuple[bytes, str]] | None) -> tuple[dict, str, str, bool]:
    """Everything a report is judged by, as the bot and the scripts build it:
    (the sheet as data after the focused re-reads, the sheet's text - for
    the model answer - the sheet with the report's DATA CHECK - for judging -
    and whether some of the report's figures can't be checked, which the
    report then tells the student)."""
    from services.ai.visual_check import data_check, unchecked_figures

    visual = await check_report_figures(answer, visual, question, images)
    sheet = sheet_text(VisualSheet.model_validate(visual))
    judged = f"{sheet}\n\n{data_check(answer, visual, question)}".strip()
    return visual, sheet, judged, bool(unchecked_figures(answer, visual, question))


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


def _pair_blocks(a: list[VisualBlock], b: list[VisualBlock]) -> list[tuple[VisualBlock, VisualBlock | None, bool]]:
    """(first read's block, the second read's to compare it with, pooled).
    Blocks counted the same way pair in order. Counted differently - one read
    split a two-axis chart into two blocks, the other kept one (report #40:
    every figure ended "seen by one read only") - each block of the first
    read is compared with ALL the second read's figures pooled."""
    if len(a) == len(b):
        return [(x, y, False) for x, y in zip(a, b)]
    if not b:
        return [(x, None, False) for x in a]
    pool = b[0].model_copy(update={"data": [v for y in b for v in y.data],
                                   "map_features": [f for y in b for f in y.map_features]})
    return [(x, pool, True) for x in a]


def _single(block: VisualBlock, status: str, note: str = "") -> SheetBlock:
    return SheetBlock(
        visual_type=block.visual_type, title=block.title, units=block.units, time_points=block.time_points,
        axis_labels=block.axis_labels, legend=block.legend, map_features=block.map_features, overall=block.overall,
        process=block.process, bars_drawn=block.bars_drawn,
        values=[SheetValue(series=v.series, point=v.point, value=v.value, text=v.text, printed=v.printed,
                           precision=0.0 if v.printed else max(v.precision, 0.0), status=status, note=note)
                for v in block.data],
    )


def _transposed(block: VisualBlock) -> list:
    return [v.model_copy(update={"series": v.point, "point": v.series}) for v in block.data]


def _by_values(x: VisualBlock, data: list, series: dict, points: dict) -> None:
    """Series the names didn't pair ("Unemployment rate" / "Jobless %") paired
    by their figures: most shared points agreeing within precision."""
    left_x = [s for s in dict.fromkeys(v.series for v in x.data) if s not in series]
    left_y = [s for s in dict.fromkeys(v.series for v in data) if s not in series.values()]
    if not left_x or not left_y:
        return
    xs = {(v.series, v.point): v for v in x.data if v.value is not None}
    ys = {(v.series, v.point): v for v in data if v.value is not None}

    def agreement(sx: str, sy: str) -> float:
        shared = [(a, ys[(sy, points[a.point])]) for (s, _), a in xs.items()
                  if s == sx and points.get(a.point) is not None and (sy, points[a.point]) in ys]
        if len(shared) < 2:
            return 0.0
        ok = sum(abs(a.value - b.value) <= max(a.precision, b.precision, 0.5, 0.05 * abs(a.value)) for a, b in shared)
        return ok / len(shared)

    for sx in left_x:
        scored = [(agreement(sx, sy), sy) for sy in left_y]
        best = max(scored, default=(0.0, None))
        if best[0] >= 0.6:
            series[sx] = best[1]
            left_y.remove(best[1])


def _axis_gap(block: VisualBlock) -> float | None:
    """The step between the value axis's printed ticks (0, 20, 40 ... -> 20)."""
    nums = []
    for label in block.axis_labels:
        m = re.fullmatch(r"\s*[$£€]?\s*(-?\d{1,3}(?:,\d{3})+|-?\d+(?:\.\d+)?)\s*%?\s*[kKmM]?\s*", label)
        if m:
            nums.append(float(m.group(1).replace(",", "")))
    if len(nums) < 3 or any(1800 <= n <= 2100 and n == int(n) for n in nums):  # years are not a value scale
        return None
    steps = sorted({round(b - a, 6) for a, b in zip(nums, nums[1:]) if b > a})
    return steps[0] if steps else None


def _capped(block: VisualBlock) -> VisualBlock:
    """Scale readings no less precise than a quarter of the gridline step for
    bars - #48's bars on a 0-20-40 axis came back ±10, so 20 and 30
    "agreed". A point on a line is half a step (the academic lead,
    2026-10-27: #53's lines on a 50-unit grid were read 20 low by both reads,
    and "240" for a point at 230 is a fair reading)."""
    gap = _axis_gap(block)
    if not gap:
        return block
    if block.visual_type == "line":
        return block.model_copy(update={"data": [v if v.printed else v.model_copy(update={"precision": gap / 2})
                                                 for v in block.data]})
    limit = gap / 4
    return block.model_copy(update={"data": [v.model_copy(update={"precision": min(v.precision, limit)})
                                             if not v.printed and v.precision > limit else v for v in block.data]})


def _merge_values(x: VisualBlock, y: VisualBlock, pooled: bool = False) -> list[SheetValue]:
    # The second read may have put the legend on the other dimension (years as
    # series instead of points - Volume 1 pp.24, 36): its figures are taken
    # the way round that matches more of the first read's.
    def keyed(data):
        series = _match_labels([v.series for v in x.data], [v.series for v in data])
        points = _match_labels([v.point for v in x.data], [v.point for v in data])
        _by_values(x, data, series, points)
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
            # a printed figure against a scale reading: the reading's precision;
            # two scale readings: the stricter of the two
            tol = max(w.precision if v.printed else prec if w.printed else min(prec, w.precision), 0.5)
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
    for w in [] if pooled else y_data:  # a pool's other figures belong to other blocks
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
        blocks = [_single(_capped(x), "verified") for x in one.visuals]
        readability, note, reads = one.readability, one.readability_note, 1
        unreadable += one.unreadable
        checks.append("only one read worked: its figures are used as read")
    else:
        a = a.model_copy(update={"visuals": [_capped(x) for x in a.visuals]})
        b = b.model_copy(update={"visuals": [_capped(x) for x in b.visuals]})
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
        for x, y, pooled in _pair_blocks(a.visuals, b.visuals):
            if y is None:
                blocks.append(_single(x, "uncertain", "seen by one read only"))
                continue
            block = _single(x, "verified")
            block.values = _merge_values(x, y, pooled)
            if not block.axis_labels and y.axis_labels and not pooled:
                block.axis_labels = list(y.axis_labels)
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


_TEXTURES = {"dotted", "dots", "stippled", "speckled", "chequered", "checkered", "checked", "hatched", "hatching",
             "crosshatched", "striped", "stripes", "brick", "grid", "dashed", "wavy", "patterned", "textured",
             "diagonal", "lined", "mesh"}
_COLOURS = {"grey", "gray", "black", "white", "blue", "red", "green", "yellow", "orange", "purple", "brown", "pink"}


def _alike(a: str, b: str) -> bool:
    """Two legend patterns a reader can mix up: the same colour, both
    textured ("chequered grey" / "dotted grey"). Shades of a plain colour
    ("dark grey" / "light grey") are told apart by the reads and by order."""
    wa, wb = set(re.findall(r"[a-z]+", a.lower())), set(re.findall(r"[a-z]+", b.lower()))
    if _norm(a) == _norm(b) or not (wa & wb & _COLOURS):
        return False
    return bool(wa & _TEXTURES) and bool(wb & _TEXTURES)


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
    # Grouped bars: a group with fewer bars drawn than the legend has entries
    # - which one is missing can't be told from the heights (#54: the 65+
    # group has no 2003 bar, and both reads shifted 2004's into its place).
    entries = len(block.legend)
    for group in block.bars_drawn:
        if entries >= 2 and 0 < group.bars < entries:
            vals = [v for v in block.values if _norm(v.point) == _norm(group.point) or _norm(v.series) == _norm(group.point)]
            _uncertain(vals, f"only {group.bars} of {entries} bars are drawn - which one is missing is not certain")
            checks.append(f"{name}: {group.point} has {group.bars} of {entries} bars drawn - its figures are not used "
                          "to mark mistakes")
    # Two legend patterns that look alike (#56: "chequered grey" and "dotted
    # grey" - both reads may give one slice the other's label).
    for i, a in enumerate(block.legend):
        for other in block.legend[i + 1:]:
            if _alike(a.colour, other.colour):
                same = {_norm(a.label), _norm(other.label)}
                _uncertain([v for v in block.values if _norm(v.series) in same or _norm(v.point) in same],
                           f"look-alike legend patterns ({a.label} / {other.label})")
                checks.append(f"{name}: {a.label} and {other.label} have look-alike legend patterns ({a.colour} / "
                              f"{other.colour}) - their figures are not used to mark mistakes")
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
    if sheet.chart_key:
        lines[0] = lines[0].replace("read twice and checked by software", "with every figure checked by an examiner")
    elif sheet.reads == 1:
        lines[0] = lines[0].replace("read twice and checked", "read once and checked")
    uncertain = []
    for i, blk in enumerate(sheet.blocks, 1):
        head = f"Visual {i}: {blk.visual_type}"
        extras = [x for x in (blk.title, f"units: {blk.units}" if blk.units else "",
                              f"time points: {', '.join(blk.time_points)}" if blk.time_points else "") if x]
        lines.append(head + (f" ({'; '.join(extras)})" if extras else ""))
        if blk.axis_labels:
            lines.append(f"  Axis labels as printed: {', '.join(blk.axis_labels)}")
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
