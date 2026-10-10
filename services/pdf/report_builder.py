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
from services.ai.essay_marking import WritingReport
from services.ai.sample_bank import SampleSection
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


# --- Writing report (2026-10 redesign) ------------------------------------------------
# Writing reports use templates/writing_report.html (the approved reference
# design); build_feedback_pdf above still renders Speaking reports unchanged.

_RU_MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)

WRITING_LABELS: dict[str, dict] = {
    "uz": {
        "title_task1": "Writing Task 1 — Report",
        "title_task2": "Writing Task 2 — Essay",
        "essay_number": "{n}-insho",
        "overall": "Umumiy ball",
        "next_goal": "Keyingi maqsad: {x}",
        "tick_legend": "▮ chiziq = {x} maqsad",
        "strength": "Kuchli tomoningiz",
        "focus": "{x} uchun asosiy e'tibor",
        "essay_h": "Inshongiz",
        "essay_sub": "Qisqa tuzatishlar matn ichida. Har bir xatoning tushuntirishi — Batafsil tahlil bo'limida.",
        "legend_good": "Kuchli ibora — saqlang",
        "legend_err": "Xato",
        "legend_word": "so'z",
        "legend_fix": "tuzatish",
        "unmarked": "Bu safar matnni belgilab bo'lmadi, shuning uchun inshongiz o'zgarishsiz ko'rsatilgan. Barcha xatolar — Batafsil tahlil bo'limida.",
        "mist_h": "Batafsil tahlil",
        "mist_sub": "Faqat xatolar ko'rsatilgan. To'g'ri yozilgan gaplar takrorlanmaydi.",
        "no_mistakes": "Bu inshoda tuzatish talab qiladigan xato topilmadi.",
        "ask_prefix": "Agar \"{meaning}\" demoqchi bo'lsangiz:",
        "voc_h": "Foydali lug'at",
        "voc_sub": "Shu mavzudagi insholar uchun foydali so'z va iboralar.",
        "voc_term": "So'z / Ibora",
        "voc_meaning": "Ma'nosi",
        "voc_example": "Misol jumla",
        "sample_book_h": "Kitobdan namuna",
        "sample_h": "Namuna",
        "style_en": "{author} uslubida",
        "question": "Savol:",
        "source": "Manba: {author}, «{book}»",
        "source_page": ", {page}-bet",
        "style_note": "Bu namuna kitobdagi asl matn emas. U {author}ning yozish uslubi asosida aynan sizning savolingiz uchun tayyorlangan.",
        "generic_note": "Bu namuna kitobdagi asl matn emas. U aynan sizning savolingiz uchun tayyorlangan.",
        "words": "~{n} so'z",
        "progress": "Rivojlanish kuzatuvi.",
        "progress_first": "Bu sizning birinchi inshongiz — natija saqlandi. Keyingi hisobotda {areas} xatolaringiz qanday o'zgarganini ko'rsatamiz.",
        "progress_band": "Oldingi inshoga nisbatan umumiy ball: <b>{prev} → {now}</b>.",
        "progress_counts": "Xatolar soni: {items}.",
        "progress_criteria": "Mezonlar: {items}.",
        "progress_later": "Xatolar turlari bo'yicha solishtirish keyingi inshodan boshlanadi.",
        "and": "va",
        "areas": {"LR": "so'z tanlash", "GRA": "grammatika", "CC": "bog'lanish", "TR_or_TA": "topshiriq"},
        # Kept exactly as in the previous report.
        "disclaimer": "Ushbu ball — AI tomonidan IELTS Band Descriptors asosida, ESL (ikkinchi til sifatida ingliz tili) o'quvchilari uchun moslashtirilgan holda baholangan taxminiy, qo'llab-quvvatlovchi natija bo'lib, rasmiy IELTS imtihon natijasi emas.",
    },
    "ru": {
        "title_task1": "Writing Task 1 — Report",
        "title_task2": "Writing Task 2 — Essay",
        "essay_number": "эссе №{n}",
        "overall": "Общий балл",
        "next_goal": "Следующая цель: {x}",
        "tick_legend": "▮ отметка = цель {x}",
        "strength": "Ваша сильная сторона",
        "focus": "Главное для {x}",
        "essay_h": "Ваше эссе",
        "essay_sub": "Короткие исправления — прямо в тексте. Объяснение каждой ошибки — в разделе «Подробный разбор».",
        "legend_good": "Сильная фраза — сохраните",
        "legend_err": "Ошибка",
        "legend_word": "слово",
        "legend_fix": "исправление",
        "unmarked": "В этот раз текст не удалось разметить, поэтому эссе показано без изменений. Все ошибки — в разделе «Подробный разбор».",
        "mist_h": "Подробный разбор",
        "mist_sub": "Показаны только ошибки. Правильные предложения не повторяются.",
        "no_mistakes": "В этом эссе не найдено ошибок, требующих исправления.",
        "ask_prefix": "Если вы хотели сказать «{meaning}»:",
        "voc_h": "Полезная лексика",
        "voc_sub": "Полезные слова и выражения для эссе на эту тему.",
        "voc_term": "Слово / выражение",
        "voc_meaning": "Значение",
        "voc_example": "Пример",
        "sample_book_h": "Образец из книги",
        "sample_h": "Образец",
        "style_en": "в стиле {author}",
        "question": "Вопрос:",
        "source": "Источник: {author}, «{book}»",
        "source_page": ", стр. {page}",
        "style_note": "Это не текст из книги. Образец написан именно для вашего вопроса на основе стиля автора {author}.",
        "generic_note": "Это не текст из книги. Образец написан именно для вашего вопроса.",
        "words": "~{n} слов",
        "progress": "Отслеживание прогресса.",
        "progress_first": "Это ваше первое эссе — результат сохранён. В следующем отчёте покажем, как изменилось число ошибок в {areas}.",
        "progress_band": "По сравнению с прошлым эссе общий балл: <b>{prev} → {now}</b>.",
        "progress_counts": "Число ошибок: {items}.",
        "progress_criteria": "Критерии: {items}.",
        "progress_later": "Сравнение по типам ошибок начнётся со следующего эссе.",
        "and": "и",
        "areas": {"LR": "лексике", "GRA": "грамматике", "CC": "связности", "TR_or_TA": "выполнении задания"},
        "disclaimer": "Этот балл — ориентировочная поддерживающая оценка ИИ по IELTS Band Descriptors, адаптированная для изучающих английский как второй язык (ESL); это не официальный результат экзамена IELTS.",
    },
}

_TAG_NAMES = {"LR": "Lexical", "GRA": "Grammar", "CC": "Coherence", "TR_or_TA": "Task"}
_DB_TO_CRITERION = {"TA": "TR_or_TA", "CC": "CC", "LR": "LR", "GRA": "GRA"}


def _format_date(when: datetime, lang: str) -> str:
    if lang == "ru":
        return f"{when.day:02d} {_RU_MONTHS[when.month - 1]} {when.year}"
    return when.strftime("%d %B %Y")


def progress_note(
    lang: str,
    report: WritingReport,
    previous: dict | None,
) -> str:
    """HTML for the 'Rivojlanish kuzatuvi' box. `previous` is the student's
    previous Writing submission: {"band", "criteria_scores", "mistake_counts"}
    (mistake_counts is None for reports made before the 2026-10 redesign)."""
    L = WRITING_LABELS.get(lang, WRITING_LABELS["uz"])
    if previous is None:
        counts = report.mistake_counts()
        top = sorted(counts, key=lambda k: -counts[k])[:2] or ["LR", "GRA"]
        areas = f" {L['and']} ".join(L["areas"][k] for k in top)
        return html_lib.escape(L["progress_first"].format(areas=areas)).replace("&lt;b&gt;", "<b>")

    parts = [L["progress_band"].format(prev=f"{previous['band']:.1f}", now=f"{report.overall_band:.1f}")]
    prev_counts = previous.get("mistake_counts")
    if prev_counts is not None:
        now_counts = report.mistake_counts()
        items = []
        for key in ("LR", "GRA", "CC", "TR_or_TA"):
            before, after = prev_counts.get(key, 0), now_counts.get(key, 0)
            if before or after:
                arrow = " ↓" if after < before else " ↑" if after > before else ""
                items.append(f"{_TAG_NAMES[key]} {before} → {after}{arrow}")
        if items:
            parts.append(L["progress_counts"].format(items=" · ".join(items)))
    else:
        now_scores = report.criteria_scores()
        items = [
            f"{_TAG_NAMES[_DB_TO_CRITERION[k]]} {float(v):.1f} → {float(now_scores[k]):.1f}"
            for k, v in (previous.get("criteria_scores") or {}).items()
            if k in now_scores
        ]
        if items:
            parts.append(L["progress_criteria"].format(items=" · ".join(items)))
        parts.append(L["progress_later"])
    return " ".join(parts)


def build_writing_report_pdf(
    report: WritingReport,
    sample: SampleSection | None,
    progress_html: str,
    student_name: str,
    essay_number: int | None,
    lang: str = "uz",
    when: datetime | None = None,
) -> bytes:
    labels = WRITING_LABELS.get(lang, WRITING_LABELS["uz"])
    template = _env.get_template("writing_report.html")
    html_str = template.render(
        L=labels,
        lang=lang,
        title=labels["title_task1"] if report.task_type == "task1" else labels["title_task2"],
        student_name=student_name,
        date_str=_format_date(when or datetime.now(), lang),
        essay_number=essay_number,
        report=report,
        sample=sample,
        progress_html=progress_html,
    )
    return HTML(string=html_str).write_pdf()
