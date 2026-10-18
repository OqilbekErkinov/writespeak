"""Splits a Speaking question set into the individual questions the bot asks
one at a time, like a real examiner (bot/handlers/speaking.py), and finds
repeated questions.

Part 2 is always a single cue card, shown whole. Part 1/3 text - whether
it's a practice question from the admin panel (one question per line) or
whatever the student pasted/photographed - is split on lines and on "?",
with any non-question lead-in ("Let's talk about your hometown.", a
"Part 1 - Hometown" header) folded into the question that follows it.
Screenshot leftovers that are never part of a question - "Answers here"
buttons, "NEW Topic" badges, "2 questions" counters, "(Predicted &
Reported: ...)" headers, bare numbers and dates - are dropped (2026-10-18;
a photo's are mostly gone already: services/ai/ocr.py reads Speaking
question photos in "speaking_questions" mode).

Repeated questions ("Do you think you are an ambitious person?" right after
"Are you an ambitious person?" - the channel's own topic post had both) are
dropped from a session: two questions are the same when their content words
match after the question frame ("do you think", "would you say", ...) is
removed, and both are the same kind of question (yes/no, why, how, ...) - so
"Is it important to save money?" and "Why is it important to save money?"
stay two questions.
"""
from __future__ import annotations

import re

MAX_QUESTIONS = 15

_NUMBERING = re.compile(r"^\s*(?:(?:Q|Question\s*)?\d{1,2}\s*[.):-]|[-•*–])\s*", re.IGNORECASE)
_AFTER_QUESTION_MARK = re.compile(r"(?<=\?)\s+")
_DATE = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{4}"
# A line that is not a question and never belongs to one (checked only on
# lines without "?", so "Have you ever reported a problem?" stays).
_NOISE = re.compile(
    r"^(?:(?:show|see|view|sample|model)\s+)?answers?(?:\s+here)?$|^new(?:\s+topic)?$|^topic$|"
    rf"^\d+\s+questions?$|^\d+$|\b(?:predicted|reported)\b|^\(?{_DATE}(?:\s*(?:-|–|—|to)\s*{_DATE})?\)?$",
    re.IGNORECASE,
)

_FRAMES = (
    r"do you think( that)?", r"would you say( that)?", r"in your opinion,?", r"do you believe( that)?",
    r"would you describe yourself as", r"can you tell me", r"could you tell me", r"tell me",
)
_STOP = {
    "a", "an", "the", "you", "your", "yours", "are", "is", "am", "be", "do", "does", "did", "to", "of",
    "it", "that", "this", "these", "those", "and", "or", "any", "some", "kind", "type", "person", "really",
    "usually", "often", "think", "there", "in", "on", "at", "for", "with", "i", "me", "my", "we", "they",
}
_KINDS = ("why", "how", "what", "when", "where", "who", "which")


def _normal(question: str) -> str:
    q = question.lower().replace("’", "'")
    q = re.sub(r"[^a-z' ]", " ", q)
    for frame in _FRAMES:
        q = re.sub(rf"\b{frame}\b", " ", q)
    return " ".join(q.split())


def _kind(question: str) -> str:
    words = _normal(question).split()
    for w in words[:3]:
        if w in _KINDS:
            return "how much" if w == "how" and len(words) > 1 and words[1] in ("much", "many", "often") else w
    return "yes/no"


def _content(question: str) -> set[str]:
    return {w.rstrip("s") for w in _normal(question).split() if w not in _STOP and len(w) > 1}


def same_question(a: str, b: str) -> bool:
    """Is `b` the same question as `a`, asked again in other words? Only when
    the content words are exactly the same: one different word ("NOT use",
    "do people" / "do you", "what colour" / "what kind") makes it a different
    question - near-matches were tried on the bank on 2026-10-13 and flagged
    three real pairs like these."""
    if _kind(a) != _kind(b):
        return False
    if _normal(a) == _normal(b):
        return True
    ca, cb = _content(a), _content(b)
    return bool(ca) and ca == cb


def duplicate_of(questions: list[str]) -> list[int | None]:
    """For each question, the index of the earlier question it repeats (None if new)."""
    out: list[int | None] = []
    for i, q in enumerate(questions):
        out.append(next((j for j in range(i) if out[j] is None and same_question(questions[j], q)), None))
    return out


def dedupe(questions: list[str]) -> list[str]:
    return [q for q, dup in zip(questions, duplicate_of(questions)) if dup is None]


def split_questions(text: str, part: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if part == "part2":
        return [text]

    questions: list[str] = []
    lead_in: list[str] = []
    for line in text.splitlines():
        for piece in _AFTER_QUESTION_MARK.split(line):
            piece = _NUMBERING.sub("", piece).strip()
            if not piece or (not piece.endswith("?") and _NOISE.search(piece)):
                continue
            if piece.endswith("?"):
                questions.append("\n".join([*lead_in, piece]))
                lead_in = []
            else:
                lead_in.append(piece)

    if not questions:
        return [text]  # no "?" anywhere - treat the whole thing as one prompt
    if lead_in:
        questions.append("\n".join(lead_in))  # e.g. a trailing "Tell me about ..." line
    return dedupe(questions)[:MAX_QUESTIONS]


_TOPIC_START = re.compile(r"^\W*(describe|talk about|tell me about|speak about)\b", re.IGNORECASE)
# Examiner instructions some cards carry ("Take a moment, then start speaking.")
_INSTRUCTIONS = re.compile(
    r"(take a moment|you (will )?have (one|1|a) minute|you will have to talk|then start speaking|"
    r"you can make (some )?notes)[^.\n]*\.?",
    re.IGNORECASE,
)


def parse_cue_card(text: str) -> dict:
    """{"topic": "Describe ...", "points": [...]} from a Part 2 card: the
    first line is the topic; "You should say" is dropped; the rest are the
    points ("• who it was", "and explain why ...")."""
    text = _INSTRUCTIONS.sub("", text)
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if not lines:
        return {"topic": "", "points": []}
    # Headers before the topic ("2022-06-14: Part 2", "Cue card 5") are dropped.
    start = next((i for i, ln in enumerate(lines) if _TOPIC_START.match(ln)), 0)
    lines = lines[start:]
    topic, points = lines[0], []
    for line in lines[1:]:
        clean = re.sub(r"^[•\-–*●▪◦]\s*", "", line).strip()
        if re.fullmatch(r"you should say:?", clean, re.IGNORECASE):
            continue
        points.append(clean)
    if not points:  # one-line card: "Describe X. You should say: a, b, and explain c"
        m = re.search(r"you should say:?\s*(.+)", topic, re.IGNORECASE)
        if m:
            topic = topic[: m.start()].strip(" .,")
            parts = re.split(r",\s*|\s+and\s+(?=explain)", m.group(1))
            points = [p.strip(" .") for p in parts if p.strip(" .")]
    return {"topic": topic[:1].upper() + topic[1:], "points": points}
