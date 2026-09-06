"""A hand-written example PDF report shown to brand-new users at their very
first /start, before they've spent a single check - so they see exactly
what they'd get before committing anything (see bot/handlers/start.py).
Built from hardcoded example data, no OpenAI call involved - purely
illustrative, never used for real grading.
"""
from __future__ import annotations

from services.ai.schemas import Annotation, VocabularyItem, WritingGradingResult
from services.pdf.report_builder import build_feedback_pdf

_SAMPLE_ANSWER = (
    "In recent years, technology has become deeply embedded in almost every aspect of daily "
    "life. While some people believe this has made our lives unnecessarily complicated, others "
    "argue that it has genuinely simplified how we live and work. This essay will discuss both "
    "views before presenting my own opinion.\n\n"
    "On one hand, critics of modern technology point out that constant connectivity often leads "
    "to stress and information overload. Many people feel obligated to check their phones "
    "throughout the day, which can make it harder to relax or focus on a single task. "
    "Furthermore, the rapid pace of technological change means that people must continuously "
    "learn new systems, which some find overwhelming.\n\n"
    "On the other hand, supporters of technology highlight the enormous convenience it provides. "
    "Tasks that once took hours, such as paying bills or researching information, can now be "
    "completed in seconds. Additionally, technology has made it possible for people to stay in "
    "touch with family and friends regardless of distance, which has arguably strengthened "
    "relationships rather than complicated them.\n\n"
    "In my opinion, the complexity people experience usually comes from how technology is used "
    "rather than the technology itself. When used thoughtfully, it remains an overwhelmingly "
    "positive force in modern life."
)

_SAMPLE_ANNOTATIONS = [
    Annotation(
        sentence="This essay will discuss both views before presenting my own opinion.",
        color="green",
        comment="Aniq va tabiiy uslubda insho tuzilishini belgilab qo'yadi - examiner uchun "
        "juda foydali.",
    ),
    Annotation(
        sentence="Many people feel obligated to check their phones throughout the day, which "
        "can make it harder to relax or focus on a single task.",
        color="green",
        comment="Murakkab gap tuzilishi (relative clause) to'g'ri va tabiiy ishlatilgan.",
    ),
    Annotation(
        sentence="Furthermore, the rapid pace of technological change means that people must "
        "continuously learn new systems, which some find overwhelming.",
        color="yellow",
        comment="Fikr yaxshi, lekin \"overwhelming\" so'zi bu paragrafda ikkinchi marta "
        "ishlatildi - sinonim (\"exhausting\", \"daunting\") ishlatish lug'at boyligini oshiradi.",
    ),
    Annotation(
        sentence="In my opinion, the complexity people experience usually comes from how "
        "technology is used rather than the technology itself.",
        color="green",
        comment="Kuchli, aniq shaxsiy fikr - xulosa uchun ideal.",
    ),
]

_SAMPLE_VOCABULARY = [
    VocabularyItem(
        word_or_phrase="deeply embedded",
        meaning="chuqur singib ketgan",
        example_sentence="Smartphones have become deeply embedded in modern communication.",
    ),
    VocabularyItem(
        word_or_phrase="information overload",
        meaning="ma'lumotlar bilan haddan tashqari yuklanish",
        example_sentence="Constant notifications can cause information overload.",
    ),
    VocabularyItem(
        word_or_phrase="a positive force",
        meaning="ijobiy kuch/omil",
        example_sentence="Education remains a positive force for social change.",
    ),
]

_SAMPLE_FEEDBACK = (
    "Juda yaxshi boshlangan insho! Kirish qismida ikkala nuqtai nazarni aniq belgilab, o'z "
    "fikringizni ochiq bildirgansiz - bu Task Response uchun kuchli asos yaratadi. Paragraflar "
    "orasidagi mantiqiy bog'lanish (\"On one hand\" / \"On the other hand\") juda tabiiy chiqqan, "
    "va xulosa qismi aniq, ishonchli fikr bilan yakunlangan.\n\n"
    "Yaxshilash mumkin bo'lgan joy - lug'at boyligi: ba'zi so'zlar (\"overwhelming\") bir necha "
    "marta takrorlangan, buning o'rniga sinonimlardan foydalanish Lexical Resource ballini "
    "oshiradi. Grammatika deyarli xatosiz, murakkab gap tuzilishlari to'g'ri ishlatilgan.\n\n"
    "Davom eting - bu daraja bilan Band 7+ ga erishish butunlay real!"
)


def build_sample_report_pdf() -> bytes:
    grading = WritingGradingResult(
        topic="Technology and modern life",
        overall_band=7.0,
        task_achievement_or_response=7.0,
        coherence_and_cohesion=7.5,
        lexical_resource=6.5,
        grammatical_range_and_accuracy=7.0,
        annotations=_SAMPLE_ANNOTATIONS,
        vocabulary=_SAMPLE_VOCABULARY,
        feedback_summary=_SAMPLE_FEEDBACK,
    )
    return build_feedback_pdf(
        student_name="Namuna Talaba",
        task_label="Writing Task 2 (Essay) — NAMUNA",
        answer_text=_SAMPLE_ANSWER,
        grading=grading,
        book_sample=None,
        sample_analysis=None,
    )
