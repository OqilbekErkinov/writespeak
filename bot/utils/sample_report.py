"""A hand-written example PDF report shown to brand-new users at their very
first /start, before they've spent a single check - so they see exactly
what they'd get before committing anything (see bot/handlers/language.py).
Built from hardcoded example data in the same report format real Writing
checks use (final report design, 2026-10-12), no OpenAI call involved -
purely illustrative, never used for real grading. The data goes through the
real validation (services/ai/essay_marking.py).
"""
from __future__ import annotations

from services.ai.essay_marking import build_report
from services.ai.sample_bank import ModelAnswer
from services.ai.schemas import (
    ChecklistItem,
    CriterionNotes,
    CriterionScores,
    EssaySegment,
    Mistake,
    Priority,
    Strength,
    VocabEntry,
    WritingFeedback,
)
from services.pdf.report_builder import build_progress, build_writing_report_pdf

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
    "technological change mean that people must continuously learn new systems, which the older "
    "people especially find overwhelming.\n\n"
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
    ("error", "the older people", "older people", 8),
    ("error", "in second", "in seconds", 3),
    ("error", "technology made it possible", "technology has made it possible", 4),
    ("error", "This also gives a chance for lonely people.", None, 5),
    ("good", "rather than the technology itself", None, None),
    ("error", "When it used", "When it is used", 6),
    ("error", "the modern life", "modern life", 7),
]

_MISTAKES = [
    (1, "some even make an addiction to social media", "some even develop an addiction to social media",
     "“Addiction” goes with **develop** or **have**, never *make*.", "LR", False),
    (2, "the rapid pace of technological change mean", "the rapid pace of technological change means",
     "The subject is “the pace” (singular), so the verb needs **-s**.", "GRA", False),
    (8, "which the older people especially find overwhelming", "which older people especially find overwhelming",
     "No “the” when you mean older people in general.", "GRA", False),
    (3, "can now be completed in second", "can now be completed in seconds",
     "“In seconds” is a fixed phrase meaning very quickly; it is always plural.", "LR", False),
    (4, "technology made it possible", "technology has made it possible",
     "A change that started in the past and still matters now takes the **present perfect**.", "GRA", False),
    (5, "This also gives a chance for lonely people.", "This also gives lonely people a chance to stay connected.",
     "The meaning is unclear: a chance to do what? Say what the chance is for.", "CC", True),
    (6, "When it used thoughtfully", "When it is used thoughtfully",
     "A passive needs a form of **be**: *it is used*.", "GRA", False),
    (7, "a positive force in the modern life", "a positive force in modern life",
     "General ideas such as “modern life” take no article.", "GRA", False),
]

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
    """`lang` is kept for the caller; the report itself is English only."""
    feedback = WritingFeedback(
        mistakes=[
            Mistake(id=i, wrong=w, correct=c, why=why, criterion=crit, unclear=unclear,
                    if_you_meant="it gives lonely people a way to stay in touch with others" if unclear else None)
            for i, w, c, why, crit, unclear in _MISTAKES
        ],
        essay=_segments(),
        scores=CriterionScores(TR_or_TA=7.0, CC=7.0, LR=6.5, GRA=6.0),
        criterion_notes=CriterionNotes(
            TR_or_TA="Both views covered and a clear opinion.",
            CC="Logical order, but one unclear sentence.",
            LR="Good topic words; two word-partner mistakes.",
            GRA="Verb forms and articles need attention.",
        ),
        strength=Strength(
            text="You discuss both views and give a clear opinion. Phrases like “constant connectivity "
            "often leads to stress and information overload” and “deeply embedded in almost every aspect "
            "of daily life” show strong topic vocabulary.",
            quoted_phrases=[
                "constant connectivity often leads to stress and information overload",
                "deeply embedded in almost every aspect of daily life",
            ],
        ),
        priorities=[
            Priority(title="Match the verb to its subject", rule="Singular subjects take **-s**; a change that still matters takes **has/have**; a passive needs **be**.",
                     example_wrong="When it used", example_right="When it is used", criteria=["GRA"], mistake_ids=[2, 4, 6]),
            Priority(title="Learn word partners", rule="Learn nouns together with their usual verbs and fixed phrases.",
                     example_wrong="make an addiction to", example_right="develop an addiction to", criteria=["LR"], mistake_ids=[1, 3]),
            Priority(title="No “the” for things in general", rule="Use no article when you mean a whole group or idea.",
                     example_wrong="the modern life", example_right="modern life", criteria=["GRA"], mistake_ids=[8, 7]),
        ],
        vocabulary=[VocabEntry(term=t, meaning=m, example=e) for t, m, e in _VOCABULARY],
        checklist=[
            ChecklistItem(text="Every verb **matches its subject**: *the pace means*.", ref="Priority 01"),
            ChecklistItem(text="Changes that still matter use **has/have + past participle**.", ref="Priority 01"),
            ChecklistItem(text="Nouns come with their **usual verb**: *develop an addiction*.", ref="Priority 02"),
            ChecklistItem(text="No **“the”** before general ideas: *modern life, older people*.", ref="Priority 03"),
            ChecklistItem(text="Each sentence says **exactly what I mean** - a chance to do what?", ref="Mistake 5"),
        ],
    )
    report = build_report("task2", _QUESTION, _ESSAY, feedback)
    answer = ModelAnswer(question=_QUESTION, paragraphs=_SAMPLE, band=8.0)
    return build_writing_report_pdf(report, answer, build_progress(report, None), "Sample Student", essay_number=None)
