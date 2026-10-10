"""A hand-written example PDF report shown to brand-new users at their very
first /start, before they've spent a single check - so they see exactly
what they'd get before committing anything (see bot/handlers/language.py).
Built from hardcoded example data in the same report format real Writing
checks use (2026-10 redesign), no OpenAI call involved - purely illustrative,
never used for real grading. The marked essay still goes through the real
segment validation (services/ai/essay_marking.py).
"""
from __future__ import annotations

from services.ai.essay_marking import align_segments, build_report
from services.ai.sample_bank import SampleSection
from services.ai.schemas import (
    CriterionNotes,
    CriterionScores,
    EssaySegment,
    FocusItem,
    Mistake,
    VocabEntry,
    WritingFeedback,
)
from services.pdf.report_builder import build_writing_report_pdf, progress_note

_QUESTION = (
    "Some people believe that modern technology has made life more complicated, while others "
    "think it has made life easier. Discuss both views and give your own opinion."
)

_ESSAY = (
    "In recent years, technology has become deeply embedded in almost every aspect of daily life. "
    "While some people believe this has made our lives unnecessarily complicated, others argue that "
    "it has genuinely simplified how we live and work. This essay will discuss both views before "
    "presenting my own opinion.\n\n"
    "On the one hand, critics of modern technology point out that constant connectivity often leads "
    "to stress and information overload. Many people feel obligated to check their phones throughout "
    "the day, and some even make an addiction to social media. Furthermore, the rapid pace of "
    "technological change mean that people must continuously learn new systems, which older people "
    "especially find overwhelming.\n\n"
    "On the other hand, supporters of technology highlight the enormous convenience it provides. "
    "Tasks that once took hours, such as paying bills, can now be completed in second. In addition, "
    "technology made it possible for families to stay in touch regardless of distance. This also "
    "gives a chance for lonely people.\n\n"
    "In my opinion, the complexity people experience usually comes from how technology is used "
    "rather than the technology itself. When it used thoughtfully, it remains a positive force in "
    "the modern life."
)

# (type, exact text, fix, mistake id) in essay order; everything between is plain text.
_MARKS = [
    ("good", "deeply embedded in almost every aspect of daily life", None, None),
    ("good", "This essay will discuss both views before presenting my own opinion.", None, None),
    ("good", "constant connectivity often leads to stress and information overload", None, None),
    ("error", "make an addiction to", "develop an addiction to", 1),
    ("error", "mean", "means", 2),
    ("error", "in second", "in seconds", 3),
    ("error", "technology made it possible", "technology has made it possible", 4),
    ("error", "This also gives a chance for lonely people.", None, 5),
    ("good", "rather than the technology itself", None, None),
    ("error", "When it used", "When it is used", 6),
    ("error", "the modern life", "modern life", 7),
]

_MISTAKES = [
    (1, "some even make an addiction to social media", "some even develop an addiction to social media",
     "\"Addiction\" collocates with develop or have, never make.", "LR", False),
    (2, "the rapid pace of technological change mean", "the rapid pace of technological change means",
     "The subject is \"the pace\" (singular), so the verb needs -s.", "GRA", False),
    (3, "can now be completed in second", "can now be completed in seconds",
     "\"In seconds\" is a fixed phrase meaning very quickly; it is always plural.", "LR", False),
    (4, "technology made it possible", "technology has made it possible",
     "A change that started in the past and still matters now takes the present perfect.", "GRA", False),
    (5, "This also gives a chance for lonely people.", "This also gives lonely people a chance to stay connected.",
     "The meaning is unclear: a chance to do what? Say what the chance is for.", "CC", True),
    (6, "When it used thoughtfully", "When it is used thoughtfully",
     "A passive needs a form of \"be\": it is used.", "GRA", False),
    (7, "a positive force in the modern life", "a positive force in modern life",
     "General ideas such as \"modern life\" take no article.", "GRA", False),
]

_LIKELY_MEANING = {
    "uz": "yolg'iz odamlarga boshqalar bilan aloqada bo'lish imkonini beradi",
    "ru": "даёт одиноким людям возможность оставаться на связи",
}

_NOTES = {
    "uz": CriterionNotes(
        TR_or_TA="Ikkala fikr va aniq pozitsiya bor",
        CC="Mantiqiy tartib, bitta noaniq gap",
        LR="Mavzuga mos so'zlar, ikki kollokatsiya xatosi",
        GRA="Zamon va moslashuv xatolari",
    ),
    "ru": CriterionNotes(
        TR_or_TA="Обе точки зрения и ясная позиция",
        CC="Логичный порядок, одно неясное предложение",
        LR="Лексика по теме, две ошибки сочетаемости",
        GRA="Ошибки времени и согласования",
    ),
}

_VOCABULARY = [
    ("digital fatigue", "Tiredness caused by spending too much time using devices.",
     "Constant notifications can lead to digital fatigue."),
    ("to streamline", "To make a process simpler and more efficient.",
     "Online banking has streamlined everyday payments."),
    ("a double-edged sword", "Something with both advantages and disadvantages.",
     "Social media is a double-edged sword for young people."),
    ("to bridge the distance", "To keep people connected despite living far apart.",
     "Video calls help families bridge the distance between countries."),
    ("screen time", "The amount of time spent looking at a screen.",
     "Parents are increasingly concerned about children's screen time."),
    ("to adapt to change", "To adjust to new conditions.",
     "Older employees sometimes struggle to adapt to technological change."),
    ("information overload", "Receiving more information than one can process.",
     "Information overload makes it harder to focus on important tasks."),
    ("to foster connections", "To help relationships develop.",
     "Online communities can foster connections between isolated people."),
    ("user-friendly", "Easy to use or understand.",
     "Banking apps have become far more user-friendly in recent years."),
    ("in moderation", "Within reasonable limits; not too much.",
     "Technology is most beneficial when it is used in moderation."),
]

_SAMPLE = [
    "It is often argued that modern technology has complicated our lives, whereas others maintain "
    "that it has made them considerably easier. Although technology undeniably brings new pressures, "
    "I believe its overall effect is to simplify daily life when it is used wisely.",
    "Those who see technology as a burden point to the constant stream of notifications and the need "
    "to master unfamiliar systems. Many employees, for instance, are expected to answer messages long "
    "after working hours, which blurs the line between work and rest. Older people in particular may "
    "feel left behind as banking, shopping and even medical appointments move online, turning simple "
    "errands into stressful tasks.",
    "However, the convenience technology offers is difficult to dispute. Tasks that once required a "
    "trip to an office, such as paying bills or renewing documents, can now be completed in minutes "
    "from home. Moreover, video calls allow families separated by thousands of kilometres to see each "
    "other every day, which strengthens rather than weakens relationships. For lonely or elderly "
    "people, these tools can be a vital link to the outside world.",
    "In conclusion, while technology can create stress and demand constant learning, these problems "
    "stem largely from how it is used rather than from the technology itself. With sensible limits "
    "and basic training, it makes everyday life simpler, faster and more connected.",
]


def _segments() -> list[list[EssaySegment]]:
    """Splits _ESSAY into paragraphs of segments around _MARKS (in order)."""
    paragraphs = []
    marks = iter(_MARKS)
    pending = next(marks, None)
    for para in _ESSAY.split("\n\n"):
        segments, pos = [], 0
        while pending and (idx := para.find(pending[1], pos)) != -1:
            kind, text, fix, mistake_id = pending
            if idx > pos:
                segments.append(EssaySegment(type="text", text=para[pos:idx], fix=None, mistake_id=None))
            segments.append(EssaySegment(type=kind, text=text, fix=fix, mistake_id=mistake_id))
            pos = idx + len(text)
            pending = next(marks, None)
        if pos < len(para):
            segments.append(EssaySegment(type="text", text=para[pos:], fix=None, mistake_id=None))
        paragraphs.append(segments)
    return paragraphs


def build_sample_report_pdf(lang: str = "uz") -> bytes:
    feedback = WritingFeedback(
        mistakes=[
            Mistake(id=i, wrong=w, correct=c, why=why, criterion=crit, unclear=unclear,
                    likely_meaning=_LIKELY_MEANING.get(lang, _LIKELY_MEANING["uz"]) if unclear else None)
            for i, w, c, why, crit, unclear in _MISTAKES
        ],
        essay=_segments(),
        scores=CriterionScores(TR_or_TA=7.0, CC=7.0, LR=6.5, GRA=6.0),
        criterion_notes=_NOTES.get(lang, _NOTES["uz"]),
        strength="You discuss both views and give a clear opinion, and each paragraph has one "
        "clear purpose. Phrases like \"constant connectivity\" and \"information overload\" show "
        "good topic vocabulary.",
        focus=[
            FocusItem(title="Verb forms", rule="the pace means; technology has made; it is used",
                      criterion="GRA", mistake_ids=[2, 4, 6]),
            FocusItem(title="Word partners", rule="develop an addiction; in seconds",
                      criterion="LR", mistake_ids=[1, 3]),
            FocusItem(title="Say exactly what you mean", rule="Name the purpose: a chance to stay connected",
                      criterion="CC", mistake_ids=[5]),
        ],
        vocabulary=[VocabEntry(term=t, meaning=m, example=e) for t, m, e in _VOCABULARY],
        topic="Technology and modern life",
    )
    report = build_report("task2", _ESSAY, feedback, align_segments(_ESSAY, feedback.essay).spans)
    sample = SampleSection(kind="generic", question=_QUESTION, paragraphs=_SAMPLE)
    student = "Namuna Talaba" if lang != "ru" else "Пример Студент"
    return build_writing_report_pdf(
        report, sample, progress_note(lang, report, None), student, essay_number=None, lang=lang
    )
