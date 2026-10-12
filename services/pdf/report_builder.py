"""Builds the WriteSpeak PDF feedback reports: Jinja2 HTML -> WeasyPrint PDF.

Writing reports: build_writing_report_pdf at the end of this file (the final
design, templates/writing_report.html). Speaking reports, build_feedback_pdf:
section order follows the spec exactly: Score -> Color-Coded Analysis ->
Detailed Feedback -> Useful Vocabulary -> Authentic Book Sample (itself
highlighted + analyzed, see services/ai/sample_analyzer.py) - or, when no
sample matches the topic closely enough, a general style-analysis fallback
instead (Revision Brief v2, Section 4, 2026-08-30; see
services/ai/rag_book_search.py's get_style_reference_samples and
services/ai/sample_analyzer.py's analyze_style).
"""
from __future__ import annotations

import base64
import html as html_lib
import logging
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from db.models import BookChunk
from services.ai.essay_marking import WritingReport
from services.ai.sample_bank import ModelAnswer, names_in
from services.ai.schemas import (
    Annotation,
    SampleAnalysis,
    SampleHighlight,
    SpeakingGradingResult,
    StyleAnalysis,
    WritingGradingResult,
)

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
)

# Keys match Annotation.color ("red" stays the internal/stored name for
# schema and historical-data stability - see schemas.py's field comment) but
# the CSS classes they map to are styled amber/gold, not red - Revision
# Brief v2, Section 2 (2026-08-30).
ANSWER_COLOR_CLASS = {"green": "hl-green", "yellow": "hl-yellow", "red": "hl-red"}
SAMPLE_COLOR_CLASS = {"vocabulary": "hl-vocab", "grammar": "hl-grammar"}

GradingResult = Union[WritingGradingResult, SpeakingGradingResult]


def _find_span(haystack: str, needle: str, start: int) -> tuple[int, int] | None:
    """Locates `needle` inside `haystack` starting at `start`. Falls back to a
    whitespace-tolerant regex match, since the model may reproduce a
    sentence/phrase with slightly normalized spacing/punctuation."""
    idx = haystack.find(needle, start)
    if idx != -1:
        return idx, idx + len(needle)

    tokens = needle.split()
    if not tokens:
        return None
    pattern = r"\s+".join(re.escape(t) for t in tokens)
    match = re.search(pattern, haystack[start:], flags=re.IGNORECASE)
    if match:
        return start + match.start(), start + match.end()
    return None


def _render_highlights(
    text: str,
    phrases: list[str],
    css_classes: list[str],
    extra_fields: list[dict],
    include_in_list: list[bool] | None = None,
) -> tuple[str, list[dict]]:
    """Shared engine behind both highlight views: finds each phrase in order,
    wraps matches in a <mark>, and returns a parallel numbered list (for a
    detailed-notes section below the highlighted text).

    When `include_in_list` is given, only entries flagged True get a
    sequence number - both the inline <sup> badge in the passage and an
    entry in the returned list. Entries flagged False are still highlighted
    in the passage (so the color itself remains visible), just with no
    number and no list entry - used to skip "correct/strong" sentences from
    the Detailed Feedback section per Revision Brief v2, Section 3
    (2026-08-30), since they're already visible, highlighted, in the passage
    itself. Entries in the list whose phrase can't be located in the text
    are still listed, just without an inline mark."""
    if include_in_list is None:
        include_in_list = [True] * len(phrases)

    numbered: list[dict] = []
    spans: list[tuple[int, int, int | None, str]] = []

    cursor = 0
    next_number = 1
    for phrase, css_class, extra, listed in zip(phrases, css_classes, extra_fields, include_in_list):
        found = _find_span(text, phrase, cursor)
        number = None
        if listed:
            number = next_number
            next_number += 1
            numbered.append({"number": number, **extra})
        if found:
            start, end = found
            spans.append((start, end, number, css_class))
            cursor = end

    spans.sort(key=lambda s: s[0])

    parts: list[str] = []
    pos = 0
    for start, end, number, css_class in spans:
        if start < pos:
            continue  # overlapping match with a previous span - skip to keep HTML valid
        parts.append(html_lib.escape(text[pos:start]))
        badge = f"<sup>[{number}]</sup>" if number is not None else ""
        parts.append(f'<mark class="{css_class}">{html_lib.escape(text[start:end])}{badge}</mark>')
        pos = end
    parts.append(html_lib.escape(text[pos:]))

    return "".join(parts).replace("\n", "<br>"), numbered


def _render_highlighted_answer(
    answer_text: str, annotations: list[Annotation]
) -> tuple[str, list[dict]]:
    return _render_highlights(
        answer_text,
        phrases=[a.sentence for a in annotations],
        css_classes=[ANSWER_COLOR_CLASS[a.color] for a in annotations],
        extra_fields=[{"color": a.color, "sentence": a.sentence, "comment": a.comment} for a in annotations],
        # Skip "green" (correct/strong) from the numbered Detailed Feedback
        # list - Revision Brief v2, Section 3. Still highlighted above, just
        # without a number/entry.
        include_in_list=[a.color != "green" for a in annotations],
    )


def _detailed_feedback_note(total: int, flagged: int) -> str | None:
    """A short acknowledgement line shown above the Detailed Feedback list
    when most/all sentences needed no correction, per Revision Brief v2,
    Section 3 ('if most or all sentences in the response are strong, add one
    short line... rather than listing each one individually') - the list
    itself already omits green sentences, this just avoids the section
    looking oddly empty or unexplained when few/no entries follow."""
    if total == 0:
        return None
    if flagged == 0:
        return (
            "🎉 Ajoyib! Ushbu javobingizdagi deyarli barcha gaplar kuchli va to'g'ri chiqqan — "
            "alohida ko'rsatiladigan jiddiy muammo topilmadi."
        )
    if flagged / total <= 0.2:
        return (
            "👏 Juda yaxshi natija! Javobingizning katta qismi kuchli chiqqan, faqat quyidagi bir "
            "necha joyga e'tibor bering:"
        )
    return None


def _render_highlighted_sample(
    sample_text: str, highlights: list[SampleHighlight]
) -> tuple[str, list[dict]]:
    return _render_highlights(
        sample_text,
        phrases=[h.phrase for h in highlights],
        css_classes=[SAMPLE_COLOR_CLASS[h.highlight_type] for h in highlights],
        extra_fields=[
            {"type": h.highlight_type, "phrase": h.phrase, "note": h.note} for h in highlights
        ],
    )


def _criteria_rows(grading: GradingResult) -> list[tuple[str, float]]:
    if isinstance(grading, WritingGradingResult):
        return [
            ("Task Achievement / Response", grading.task_achievement_or_response),
            ("Coherence & Cohesion", grading.coherence_and_cohesion),
            ("Lexical Resource", grading.lexical_resource),
            ("Grammatical Range & Accuracy", grading.grammatical_range_and_accuracy),
        ]
    return [
        ("Fluency & Coherence", grading.fluency_and_coherence),
        ("Lexical Resource", grading.lexical_resource),
        ("Grammatical Range & Accuracy", grading.grammatical_range_and_accuracy),
        ("Pronunciation", grading.pronunciation),
    ]


def build_feedback_pdf(
    student_name: str,
    task_label: str,
    answer_text: str,
    grading: GradingResult,
    book_sample: Optional[BookChunk],
    sample_analysis: Optional[SampleAnalysis] = None,
    style_analysis: Optional[StyleAnalysis] = None,
) -> bytes:
    highlighted_html, numbered_annotations = _render_highlighted_answer(
        answer_text, grading.annotations
    )
    detailed_feedback_note = _detailed_feedback_note(
        total=len(grading.annotations), flagged=len(numbered_annotations)
    )

    highlighted_sample_html = None
    sample_highlight_notes: list[dict] = []
    if book_sample is not None and sample_analysis is not None:
        highlighted_sample_html, sample_highlight_notes = _render_highlighted_sample(
            book_sample.content_text, sample_analysis.highlights
        )

    template = _env.get_template("feedback_report.html")
    html_str = template.render(
        student_name=student_name,
        date_str=datetime.now().strftime("%d %B %Y"),
        task_label=task_label,
        overall_band=grading.overall_band,
        criteria=_criteria_rows(grading),
        highlighted_answer_html=highlighted_html,
        annotations=numbered_annotations,
        detailed_feedback_note=detailed_feedback_note,
        vocabulary=grading.vocabulary,
        feedback_summary=grading.feedback_summary,
        book_sample=book_sample,
        sample_overview=sample_analysis.overview if sample_analysis else None,
        highlighted_sample_html=highlighted_sample_html,
        sample_highlight_notes=sample_highlight_notes,
        # Style-analysis fallback (Revision Brief v2, Section 4) - only ever
        # populated when book_sample is None (see writing.py/speaking.py).
        style_overview=style_analysis.overview if style_analysis else None,
        style_tips=style_analysis.tips if style_analysis else [],
    )
    return HTML(string=html_str).write_pdf()


# --- Writing report (final design, 2026-10-12) ------------------------------------------
# templates/writing_report.html is the approved Appendix A design, English
# only; build_feedback_pdf above still renders Speaking reports.

FONT_SANS = "Inter, InterExt, InterCyr, Noto Sans, sans-serif"
FONT_SERIF = "Fraunces, FrauncesExt, Noto Serif, serif"
RING_RADIUS = 52  # the score ring's circle in the 130x130 SVG
RING_TICK = (43, 61)  # the target tick runs between these radii


def _ring(overall: float, target: float) -> dict:
    """SVG numbers for the cover's score ring: filled to overall/9, a tick at the target."""
    circumference = 2 * math.pi * RING_RADIUS
    angle = target / 9 * 2 * math.pi  # clockwise from the top
    (r1, r2), sin, cos = RING_TICK, math.sin(angle), math.cos(angle)
    return {
        "dash": round(overall / 9 * circumference, 1),
        "circ": round(circumference, 1),
        "x1": round(65 + r1 * sin, 1), "y1": round(65 - r1 * cos, 1),
        "x2": round(65 + r2 * sin, 1), "y2": round(65 - r2 * cos, 1),
    }


def _name_size(name: str) -> int:
    """Font size that keeps the student's name clear of the score ring
    (longer names still wrap inside their 118mm column)."""
    n = len(name)
    return 40 if n <= 12 else 34 if n <= 16 else 28 if n <= 20 else 23 if n <= 26 else 19


def _piece(task_type: str) -> str:
    return "report" if task_type == "task1" else "essay"


def _chip(change: float, higher_is_better: bool) -> dict:
    if change == 0:
        return {"chip": "= no change" if higher_is_better else "= same", "cls": "eq"}
    better = change > 0 if higher_is_better else change < 0
    if higher_is_better:
        return {"chip": "✓ higher", "cls": "up"} if better else {"chip": "↓ lower", "cls": "down"}
    return {"chip": "✓ fewer", "cls": "up"} if better else {"chip": "↑ more", "cls": "down"}


def build_progress(report: WritingReport, previous: dict | None) -> dict:
    """The progress page, computed here: the student's previous Writing report
    of the same task type ({"band", "mistake_counts", "words"}; mistake_counts
    is None for reports made before 2026-10, words None when unknown) against
    this one."""
    piece = _piece(report.task_type)
    if previous is None:
        return {
            "first": True,
            "note": f"This is your first {piece}. Your results are saved, and your next report will "
            "show your progress.",
        }
    before, now = float(previous["band"]), report.overall_band
    rows = [{"label": "Overall band", "value": f"{before:.1f} → {now:.1f}", **_chip(now - before, True)}]
    gains = []  # (size, sentence) - the biggest is named first
    if now > before:
        gains.append(((now - before) / 2, f"your overall band rose from {before:.1f} to {now:.1f}"))
    counts = previous.get("mistake_counts")
    if counts is not None:
        now_counts = report.mistake_counts()
        per_100 = bool(previous.get("words")) and report.word_count > 0
        for key, label, noun in (("GRA", "Grammar", "grammar"), ("LR", "Vocabulary", "vocabulary")):
            old_n, new_n = counts.get(key, 0), now_counts.get(key, 0)
            if per_100:
                old_v = round(old_n * 100 / previous["words"], 1)
                new_v = round(new_n * 100 / report.word_count, 1)
                rows.append({"label": f"{label} mistakes per 100 words", "value": f"{old_v:.1f} → {new_v:.1f}",
                             **_chip(new_v - old_v, False)})
                unit = " per 100 words"
            else:
                old_v, new_v = old_n, new_n
                rows.append({"label": f"{label} mistakes (total)", "value": f"{old_v} → {new_v}",
                             **_chip(new_v - old_v, False)})
                unit = ""
            if new_v < old_v:
                gains.append(((old_v - new_v) / old_v, f"your {noun} mistakes dropped from {old_v} to {new_v}{unit}"))
    if gains:
        note = f"Great work: {max(gains)[1]}."
    elif now == before:
        note = f"Your band held steady at {now:.1f}."
    else:
        note = "Scores move up and down from one essay to the next - keep going."
    if report.priorities:
        note += f" Next time, focus on “{report.priorities[0]['title']}” (priority 01)."
    return {"first": False, "rows": rows, "note": note}


def _format_date(when: datetime) -> str:
    return f"{when.day} {when.strftime('%B %Y')}"


def _generated_text(report: WritingReport, answer: ModelAnswer | None) -> str:
    """Everything in the report the models wrote (not the student's own text)."""
    parts = [c["note"] for c in report.criteria] + [report.strength_html]
    parts += [f"{p['title']} {p['rule_html']} {p['ex_right']}" for p in report.priorities]
    parts += [f"{m['correct']} {m['why']} {m['if_you_meant'] or ''}" for m in report.mistakes]
    parts += [f"{v.term} {v.meaning} {v.example}" for v in report.vocabulary]
    parts += [c["html"] for c in report.checklist]
    if answer is not None:
        parts += answer.paragraphs
    return "\n".join(parts)


def build_writing_report_pdf(
    report: WritingReport,
    answer: ModelAnswer | None,
    progress: dict,
    student_name: str,
    essay_number: int | None,
    chart_images: list[tuple[bytes, str]] | None = None,
    when: datetime | None = None,
) -> bytes:
    leaked = names_in(_generated_text(report, answer))
    if leaked:
        logger.error("Writing report names the source (%s) - check the model output", ", ".join(leaked))
    context = dict(
        sans=FONT_SANS,
        serif=FONT_SERIF,
        piece=_piece(report.task_type),
        task_label="Task 1 Report" if report.task_type == "task1" else "Task 2 Essay",
        student_name=student_name,
        name_size=_name_size(student_name),
        date_str=_format_date(when or datetime.now()),
        essay_number=essay_number,
        report=report,
        ring=_ring(report.overall_band, report.target_band),
        answer=answer,
        chart_images=[
            f"data:{mime};base64,{base64.b64encode(data).decode()}" for data, mime in chart_images or []
        ],
        progress=progress,
    )
    template = _env.get_template("writing_report.html")
    document = HTML(string=template.render(**context), base_url=str(TEMPLATE_DIR)).render()
    if _page_of(document, "s-essay") != 1:  # the cover ran onto a second page
        logger.warning("Report cover overflowed - rendering it compact")
        document = HTML(string=template.render(**context, compact=True), base_url=str(TEMPLATE_DIR)).render()
    return document.write_pdf()


def _page_of(document, anchor: str) -> int | None:
    for i, page in enumerate(document.pages):
        if anchor in page.anchors:
            return i
    return None
