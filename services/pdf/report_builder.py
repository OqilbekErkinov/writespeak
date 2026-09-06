"""Builds the WriteSpeak PDF feedback report: Jinja2 HTML -> WeasyPrint PDF.

Section order follows the spec exactly: Score -> Color-Coded Analysis ->
Detailed Feedback -> Useful Vocabulary -> Authentic Book Sample (itself
highlighted + analyzed, see services/ai/sample_analyzer.py) - or, when no
sample matches the topic closely enough, a general style-analysis fallback
instead (Revision Brief v2, Section 4, 2026-08-30; see
services/ai/rag_book_search.py's get_style_reference_samples and
services/ai/sample_analyzer.py's analyze_style).
"""
from __future__ import annotations

import html as html_lib
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from db.models import BookChunk
from services.ai.schemas import (
    Annotation,
    SampleAnalysis,
    SampleHighlight,
    SpeakingGradingResult,
    StyleAnalysis,
    WritingGradingResult,
)

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
