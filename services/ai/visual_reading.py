"""A Task 1 visual (chart, graph, table, diagram, map, process) read ONCE into
a data sheet that every later call uses: the scoring runs, the feedback, the
missed-errors and verification passes, the model answer and its checks.

Until 2026-10-19 each of those calls read the picture on its own, and they
disagreed. Report #62's bar chart has no printed values; the student's
correct "7%" and "an equal share ... at 20%" were marked wrong and
"corrected" to 8% and 22%, while its model answer said 8%, 18/16 and "girls
lead marginally" - the chart shows about 7%, 18/15 and two equal bars. The
sheet keeps printed values apart from values estimated from a scale (with
the range a careful reader could defend) and from what can't be read, so a
reasonable estimate is never "corrected" into another estimate, nothing is
invented, and the whole report uses the same figures.
"""
from __future__ import annotations

import base64
import logging
from collections import Counter

import openai

from bot.config import settings
from services.ai import usage
from services.ai.openai_client import is_image_error, parse_flex
from services.ai.schemas import VisualReading

logger = logging.getLogger(__name__)

READ_MAX_TOKENS = 16000

READ_SYSTEM = """You read the visual of an IELTS Academic Writing Task 1 question - a chart, graph, \
table, diagram, map or process - and write its DATA SHEET. Everything that follows (the scores, the \
feedback on a student's report, the model answer) takes its figures from your sheet, so be careful \
and honest.

- visual_type, subject, units: what kind of visual it is, what it measures - of whom or what, where \
and when - and in what units.
- facts: every data point you can see, one per item, in a fixed order (category by category, each \
series or year in turn). For maps and processes: each change, feature or stage.
  - status "printed": the value is written on the visual (a data label, a table cell, a number on a \
diagram or map). Copy it exactly.
  - status "estimated": read from an axis or scale. Find the nearest gridlines and give your best \
estimate AND the range a careful reader could defend, e.g. "about 27% (25-29%)". On 3D bars read the \
flat top face against the back gridlines; on a line, the centre of each point.
  - status "unclear": blurred, cropped, hidden or too small to read. Say what can't be read; never \
guess a precise number.
- main_features: 3-6 lines - the overview a Band 9 report would give: the largest and smallest, the \
main trends, the clearest contrasts, the main stages or changes. Only what the visual clearly shows.
- cautions: what is easy to misread - values that look equal or too close to rank ("Music: boys and \
girls look equal, about 20%"), stacked parts, two different scales. Empty if none.
- readability: "clear"; "partly_unclear" when some values can't be read; "unreadable" when the image \
holds no usable visual at all (only the question text, an unrelated picture, far too blurred or \
cropped). readability_note: one line on what can't be read, empty when clear.

Never invent data the visual doesn't show. The question text is context only: if it states figures, \
they must agree with what you read."""

# Added after the sheet wherever a student's report is judged (scores, feedback,
# missed-errors pass) - the same rules everywhere, so the same figure is never
# right in one part of the report and wrong in another.
JUDGE_RULES = """HOW TO JUDGE THE STUDENT'S FIGURES - by the DATA SHEET, never by a new reading of \
the image:
- a printed value: the student's figure must be that value (rounding with "about", "nearly", "just \
over" is fine);
- an estimated value: ANY figure inside its range is correct - never "correct" a reasonable estimate \
into another estimate; a figure is a mistake only when it is clearly outside the range;
- a value the sheet can't read: never count the student's figure as a mistake;
- rankings, equal values, trends and the overview: a mistake only when the sheet contradicts them \
(e.g. "boys lead" where the sheet says they look equal).
A mistake about the data has criterion TR, and its `correct` gives the sheet's figure (e.g. "about 27%")."""

# Added after the sheet where the model answer is written and checked.
ANSWER_RULES = """Use ONLY the figures on this DATA SHEET: a printed value as printed; an estimated \
value with "about" or "around" and the sheet's estimate - never more precise than the sheet; leave \
out what the sheet can't read; never rank values the sheet says look equal."""

_STATUS = {"printed": "printed on the visual", "estimated": "estimated from the scale", "unclear": "can't be read"}


class ChartImageRejected(Exception):
    """OpenAI couldn't open the chart picture - every other call would fail the
    same way, so the student is asked for the picture again."""


def _image_parts(images: list[tuple[bytes, str]]) -> list[dict]:
    return [
        {"type": "image_url",
         "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}", "detail": "high"}}
        for data, mime in images
    ]


async def read_visual(question: str, images: list[tuple[bytes, str]]) -> VisualReading | None:
    """The data sheet of the Task 1 visual in `images`. None when the reading
    failed for some other reason: the check then goes on as before 2026-10-19,
    each call reading the picture itself. Raises ChartImageRejected when
    OpenAI can't open the picture."""
    messages = [
        {"role": "system", "content": READ_SYSTEM},
        {"role": "user", "content": [
            {"type": "text", "text": f"QUESTION:\n{question}\n\nRead the visual in the attached image(s)."},
            *_image_parts(images),
        ]},
    ]
    try:
        response = await parse_flex(  # the Flex tier, the default as a fallback (openai_client)
            model=settings.openai_model_vision,
            messages=messages,
            response_format=VisualReading,
            max_completion_tokens=READ_MAX_TOKENS,
            # Sent as extra_body: openai 1.57 has no reasoning_effort argument yet.
            extra_body={"reasoning_effort": settings.openai_chart_reasoning_effort},
        )
    except openai.LengthFinishReasonError as e:
        if getattr(e, "completion", None) is not None:
            usage.record("chart", e.completion)
        logger.warning("Chart reading ran past %d output tokens - grading without a data sheet", READ_MAX_TOKENS)
        return None
    except Exception as e:
        if is_image_error(e):
            raise ChartImageRejected(str(e)) from e
        logger.warning("Chart reading failed - grading without a data sheet: %r", e)
        return None
    usage.record("chart", response)
    message = response.choices[0].message
    reading = message.parsed
    if reading is None:
        logger.warning("Chart reading gave nothing usable (refusal: %r) - grading without a data sheet",
                       getattr(message, "refusal", None))
        return None
    if reading.readability != "unreadable" and not reading.facts:
        logger.warning("Chart reading listed no data - grading without a data sheet")
        return None
    counts = Counter(f.status for f in reading.facts)
    logger.info("Chart read: %s, %d facts (%s), %s%s", reading.visual_type, len(reading.facts),
                ", ".join(f"{n} {k}" for k, n in counts.items()), reading.readability,
                f" - {reading.readability_note}" if reading.readability_note else "")
    return reading


def sheet_text(reading: VisualReading | None) -> str:
    """The data sheet as the text every later call gets ("" without one)."""
    if reading is None or reading.readability == "unreadable":
        return ""
    lines = [
        "DATA SHEET - the visual, read once and carefully. Every figure in the scores, the feedback and "
        "the model answer comes from it.",
        f"Type: {reading.visual_type}. Shows: {reading.subject}. Units: {reading.units}.",
        "Figures:",
        *(f"- {f.label}: {f.value} ({_STATUS[f.status]})" for f in reading.facts),
    ]
    if reading.main_features:
        lines += ["Main features:", *(f"- {x}" for x in reading.main_features)]
    if reading.cautions:
        lines += ["Easy to misread:", *(f"- {x}" for x in reading.cautions)]
    if reading.readability == "partly_unclear" and reading.readability_note:
        lines.append(f"Partly unclear: {reading.readability_note}")
    return "\n".join(lines)
