"""Builds the WriteSpeak PDF feedback reports: Jinja2 HTML -> WeasyPrint PDF.

Writing: build_writing_report_pdf (templates/writing_report.html, the final
design of 2026-10-12). Speaking: build_speaking_report_pdf
(templates/speaking_report.html, 2026-10-13, the same design system - its
base stylesheet templates/report_styles.css is a copy of Writing's).
"""
from __future__ import annotations

import base64
import html as html_lib
import logging
import math
import re
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from services.ai.essay_marking import WritingReport
from services.ai.sample_bank import ModelAnswer, names_in

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
)

# --- Writing report (final design, 2026-10-12) ------------------------------------------
# templates/writing_report.html is the approved Appendix A design, English only.

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


# --- Speaking report (2026-10-13) --------------------------------------------------------------
# templates/speaking_report.html is the approved Appendix A design of the
# Speaking report prompt, English only.


def _speaking_generated_text(report, answers: list[dict]) -> str:
    parts = [c["note"] for c in report.criteria] + [report.strength_html]
    parts += [f"{p['title']} {p['rule_html']} {p['ex_right']}" for p in report.priorities]
    parts += [f"{m['correct']} {m['why']} {m['if_you_meant'] or ''}" for m in report.mistakes]
    parts += [f"{v.term} {v.meaning} {v.example}" for v in report.vocabulary]
    parts += [c["html"] for c in report.checklist]
    parts += [p for a in answers for p in a["paragraphs"]]
    parts += [s["note"] for a in answers for s in a.get("shows") or []]
    return "\n".join(parts)


def _answer_paragraphs_html(answer: dict) -> list[str]:
    """The model answer's paragraphs, escaped, with each "what this shows"
    phrase marked and numbered (its first occurrence)."""
    out = [html_lib.escape(p, quote=False) for p in answer["paragraphs"]]
    for k, item in enumerate(answer.get("shows") or [], 1):
        words = html_lib.escape(item["phrase"], quote=False).split()
        pattern = re.compile(r"\s+".join(re.escape(w) for w in words), re.IGNORECASE)
        for i, para in enumerate(out):
            new, hits = pattern.subn(lambda m: f'<span class="shw">{m.group(0)}<sup class="k">{k}</sup></span>', para, count=1)
            if hits:
                out[i] = new
                break
    return out


def build_speaking_report_pdf(
    report,
    answers: list[dict],
    progress: dict,
    student_name: str,
    voice_sent: bool,
    when: datetime | None = None,
) -> bytes:
    """`report`: a services.ai.speaking_marking.SpeakingReport; `answers`: the
    model answers ({"label", "question", "paragraphs", "words", "seconds",
    "band", "source", "shows", "voice"}) - one "Aimed at" badge when they all
    share a band."""
    for a in answers:
        a["html_paragraphs"] = _answer_paragraphs_html(a)
        a.setdefault("voice", voice_sent)
    leaked = names_in(_speaking_generated_text(report, answers))
    if leaked:
        logger.error("Speaking report names the source (%s) - check the model output", ", ".join(leaked))
    bands = {a["band"] for a in answers}
    context = dict(
        sans=FONT_SANS,
        serif=FONT_SERIF,
        student_name=student_name,
        name_size=_name_size(student_name),
        date_str=_format_date(when or datetime.now()),
        report=report,
        ring=_ring(report.overall_band, report.target_band),
        answers=answers,
        answers_band=bands.pop() if len(bands) == 1 else None,
        voice_sent=voice_sent,
        progress=progress,
    )
    template = _env.get_template("speaking_report.html")
    document = HTML(string=template.render(**context), base_url=str(TEMPLATE_DIR)).render()
    if _page_of(document, "s-ans") != 1:  # the cover ran onto a second page
        logger.warning("Speaking report cover overflowed - rendering it compact")
        document = HTML(string=template.render(**context, compact=True), base_url=str(TEMPLATE_DIR)).render()
    return document.write_pdf()
