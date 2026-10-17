"""A sample Speaking report (Part 1, "Hometown") for showing what a check
gives - the Speaking twin of bot/utils/sample_report.py. Built from fixed,
hand-written data through the real report code (no OpenAI call): a band-6
student's four answers with real-looking mistakes, fillers, one long pause
and a fairly flat intonation, and model answers aimed at Band 7.0.
"""
from __future__ import annotations

import re
from datetime import datetime

from services.ai.schemas import (
    AnswerMarking,
    AnswerSegment,
    ChecklistItem,
    SpeakingCriterionNotes,
    SpeakingFeedback,
    SpeakingMistake,
    SpeakingPriority,
    Strength,
    VocabEntry,
)
from services.ai.pronunciation import Assessment
from services.ai.speaking_grader import SessionItem
from services.ai.speaking_marking import build_progress, build_report
from services.ai.voice import AnswerAudio, Filler, Pause, Pitch, _tokens, measure
from services.pdf.report_builder import build_speaking_report_pdf

WPM = 132  # the pace the sample speaks at

# (question, transcript, marks, pause before this word or None)
# marks: (exact text, "good"|"error", mistake id, short fix or None)
_ANSWERS = [
    (
        "Where are you from?",
        "I'm from Samarkand, it's, uh, one of the oldest city in Uzbekistan. It is very famous with its "
        "historical buildings like Registan, and many tourists are coming there every year.",
        [("one of the oldest city", "error", 1, "one of the oldest cities"),
         ("famous with", "error", 2, "famous for"),
         ("historical buildings", "good", None, None),
         ("are coming there every year", "error", 3, "come there every year")],
        None,
    ),
    (
        "What do you like most about your hometown?",
        "I like that people are very friendly and, you know, hospitable. When you visit someone, they always "
        "give you tea and bread, even they don't know you.",
        [("they always give you tea and bread", "good", None, None),
         ("even they don't know you", "error", 4, "even if they don't know you")],
        None,
    ),
    (
        "Has your hometown changed much in recent years?",
        "Yes, it changed a lot. Now there are more new roads and, uh, modern buildings, so the city looks "
        "more... more beautiful than before. But some old places was destroyed, which is quite sad for me.",
        [("it changed a lot", "error", 5, "it has changed a lot"),
         ("some old places was destroyed", "error", 6, "some old places were destroyed")],
        "beautiful",
    ),
    (
        "Would you like to live there in the future?",
        "Maybe yes. I think Samarkand is good place for living, because it is calm and not so expensive like "
        "Tashkent. But for my career I need to stay in the capital, like, for some years.",
        [("is good place for living", "error", 7, "is a good place to live"),
         ("not so expensive like Tashkent", "error", 8, "not as expensive as Tashkent")],
        None,
    ),
]

_MISTAKES = [
    (1, "Q1", "one of the oldest city", "one of the oldest cities", "After “one of the”, the noun is always plural.", "GRA"),
    (2, "Q1", "famous with", "famous for", "We say a place is famous **for** something.", "LR"),
    (3, "Q1", "are coming there every year", "come there every year",
     "“Every year” is a habit, so it takes the present simple.", "GRA"),
    (4, "Q2", "even they don't know you", "even if they don't know you",
     "“Even” alone can't join two clauses - use **even if**.", "GRA"),
    (5, "Q3", "it changed a lot", "it has changed a lot",
     "A change that still matters now takes the **present perfect**.", "GRA"),
    (6, "Q3", "some old places was destroyed", "some old places were destroyed",
     "“Places” is plural, so the verb is **were**.", "GRA"),
    (7, "Q4", "is good place for living", "is a good place to live",
     "Say **a good place to live** - with “a”, and “to live”, not “for living”.", "LR"),
    (8, "Q4", "not so expensive like Tashkent", "not as expensive as Tashkent",
     "Comparisons of equality use **as … as**.", "GRA"),
]

_VOCAB = [
    ("be renowned for", "be famous for something special", "Samarkand is renowned for its blue-tiled madrasahs."),
    ("a historic city", "a city with an important past", "It's a historic city that still feels very alive."),
    ("hospitable", "friendly and generous to guests", "People there are incredibly hospitable to visitors."),
    ("a laid-back atmosphere", "a relaxed, calm feeling", "I love the laid-back atmosphere in the evenings."),
    ("undergo rapid development", "change and grow very quickly", "The city has undergone rapid development lately."),
    ("high-rise buildings", "very tall modern buildings", "High-rise buildings now stand next to old mosques."),
    ("preserve heritage sites", "protect important old places", "The city should do more to preserve heritage sites."),
    ("the cost of living", "how much money you need for daily life", "The cost of living is much lower than in Tashkent."),
    ("career prospects", "chances to build a good career", "Career prospects are better in the capital."),
    ("settle down", "start living in one place for a long time", "I'd like to settle down in Samarkand one day."),
    ("a sense of community", "a feeling of belonging to a group", "There's a strong sense of community in my street."),
]

_CHECKLIST = [
    ("For a change up to now, I use **has/have + past participle**: *it has changed*.", "Priority 01"),
    ("After *one of the*, my noun is plural; before a singular noun I add **a/an**.", "Priority 02"),
    ("I check word partners: famous **for**, a good place **to live**.", "Priority 03"),
    ("I say **were** with plural subjects: *places were destroyed*.", "Mistake 6"),
    ("I pause silently instead of saying *uh* or *like*.", "Your voice"),
]

_MODEL = [
    ("I'm from Samarkand, which is one of the oldest cities in Central Asia. It's renowned for its stunning "
     "historical buildings, especially the Registan, so tourists come there all year round.",
     [("one of the oldest cities", "02", "A plural noun after “one of the”."),
      ("renowned for", "03", "The right word partner, and a stronger word than “famous”.")]),
    ("What I like most is how hospitable people are. If you visit someone, even if they hardly know you, "
     "they'll sit you down and give you tea and fresh bread straight away.",
     [("even if they hardly know you", "02", "“Even if” joins the two ideas correctly.")]),
    ("Yes, it has changed a lot. There are wider roads and plenty of modern buildings now, so the centre "
     "looks much smarter. Sadly, though, some old neighbourhoods were knocked down, which I really miss.",
     [("it has changed a lot", "01", "Present perfect for a change that still matters now."),
      ("were knocked down", "02", "A plural subject with “were”.")]),
    ("Definitely, at some point. Samarkand is a great place to live because it's calm and not as expensive "
     "as Tashkent. For now, though, I need to stay in the capital for my career.",
     [("a great place to live", "03", "A natural word partner: a place to live."),
      ("not as expensive as Tashkent", "02", "As … as for comparing two cities.")]),
]

_FILLER_RE = re.compile(r"\buh\b|\byou know\b|(?<=, )like(?=,)", re.IGNORECASE)


def _audio(text: str, pause_before: str | None) -> AnswerAudio:
    tokens = _tokens(text)
    t = 0.4
    pauses = []
    for i, tok in enumerate(tokens):
        if pause_before and tok.text.strip(".,!?").lower() == pause_before:
            pauses.append(Pause(t, t + 1.8, True, tok.text))
            t += 1.8
        step = 60 / WPM
        tok.start, tok.end = t, t + step * 0.85
        t += step
    fillers = [Filler(m.start(), m.end(), m.group().lower()) for m in _FILLER_RE.finditer(text)]
    return AnswerAudio(text=text, tokens=tokens, duration=t + 0.5, fillers=fillers, pauses=pauses)


def _segments(text: str, marks) -> list[AnswerSegment]:
    found = sorted((text.index(phrase), phrase, kind, mid, fix) for phrase, kind, mid, fix in marks)
    segments, pos = [], 0
    for start, phrase, kind, mid, fix in found:
        if start > pos:
            segments.append(AnswerSegment(type="text", text=text[pos:start], fix=None, mistake_id=None))
        segments.append(AnswerSegment(type=kind, text=phrase, fix=fix if kind == "error" else None,
                                      mistake_id=mid if kind == "error" else None))
        pos = start + len(phrase)
    if pos < len(text):
        segments.append(AnswerSegment(type="text", text=text[pos:], fix=None, mistake_id=None))
    return segments


def build_speaking_sample_pdf(student_name: str = "Sample Student", when: datetime | None = None) -> bytes:
    items = [
        SessionItem(f"Q{i}", "part1", question, _audio(text, pause))
        for i, (question, text, _, pause) in enumerate(_ANSWERS, 1)
    ]
    feedback = SpeakingFeedback(
        answers=[AnswerMarking(id=f"Q{i}", segments=_segments(text, marks))
                 for i, (_, text, marks, _) in enumerate(_ANSWERS, 1)],
        mistakes=[SpeakingMistake(id=i, question=q, wrong=w, correct=c, why=why, criterion=crit,
                                  unclear=False, if_you_meant=None) for i, q, w, c, why, crit in _MISTAKES],
        criterion_notes=SpeakingCriterionNotes(
            FC="Answers go on with reasons; a few fillers and one long pause.",
            LR="Good topic words like “hospitable”; some wrong word partners.",
            GRA="Simple sentences mostly right; tense and plural slips.",
            P="Clear and easy to follow; the voice could rise and fall more.",
        ),
        strength=Strength(
            text="You extend your answers with reasons and real details: phrases like “historical "
                 "buildings” and “they always give you tea and bread” paint a clear picture.",
            quoted_phrases=["historical buildings", "they always give you tea and bread"],
        ),
        priorities=[
            SpeakingPriority(title="Present perfect for changes", rule="A change that still matters now takes "
                             "**has/have + past participle**.", example_wrong="it changed a lot",
                             example_right="it has changed a lot", criteria=["GRA"], mistake_ids=[5, 3],
                             voice_ref=False),
            SpeakingPriority(title="Plurals and agreement", rule="Plural after **one of the**; plural subjects "
                             "take **were/are**.", example_wrong="some old places was destroyed",
                             example_right="some old places were destroyed", criteria=["GRA"],
                             mistake_ids=[1, 6, 4, 8], voice_ref=False),
            SpeakingPriority(title="Natural word partners", rule="Learn words with their partners: famous "
                             "**for**, a place **to live**.", example_wrong="famous with",
                             example_right="famous for", criteria=["LR"], mistake_ids=[2, 7], voice_ref=False),
        ],
        vocabulary_topic="your hometown",
        vocabulary=[VocabEntry(term=t, meaning=m, example=e) for t, m, e in _VOCAB],
        checklist=[ChecklistItem(text=t, ref=r) for t, r in _CHECKLIST],
        cue_coverage=[],
    )
    metrics = measure([item.audio for item in items])
    bands = {"FC": 6.0, "LR": 6.0, "GRA": 5.0, "P": 6.0}
    contour = [0.0, 0.4, 0.9, 1.1, 0.8, 0.5, 0.6, 1.0, 1.4, 1.2, 0.9, 0.7, 0.8, 1.1, 1.5, 1.3, 1.0, 0.6, 0.4, 0.7,
               1.0, 1.2, 0.9, 0.6, 0.5, 0.8, 1.2, 1.6, 1.4, 1.1, 0.8, 0.6, 0.9, 1.3, 1.1, 0.7, 0.4, 0.3, 0.6, 1.0,
               1.3, 1.0, 0.7, 0.5, 0.3, 0.1, -0.2, -0.4]
    pitch = Pitch(span=4.2, contour=contour, voiced_seconds=sum(i.audio.speaking_seconds for i in items))
    # the voice measured, as in a real check (its Pronunciation band is set above)
    pron = Assessment(seconds=60.0, accuracy=82.0, fluency=78.0, completeness=96.0, prosody=71.0, pron=79.0, words=[])
    report = build_report("part1", items, feedback, [], set(), set(), bands, metrics, pron, None, pitch)
    answers = []
    for i, ((question, *_), (text, shows)) in enumerate(zip(_ANSWERS, _MODEL), 1):
        words = len(text.split())
        answers.append({
            "label": f"Q{i}", "qids": [f"Q{i}"], "part": "part1", "question": question, "paragraphs": [text],
            "words": words, "seconds": round(words / WPM * 60), "band": 7.0, "source": "ai_generated",
            "shows": [{"n": n, "title": next(p.title for k, p in enumerate(feedback.priorities, 1) if f"0{k}" == n),
                       "phrase": phrase, "note": note} for phrase, n, note in shows],
        })
    progress = build_progress("part1", report.overall_band, metrics, None, report.priorities, None)
    return build_speaking_report_pdf(report, answers, progress, student_name, voice_sent=True, when=when)
