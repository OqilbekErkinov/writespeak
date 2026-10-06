"""Splits a Speaking question set into the individual questions the bot asks
one at a time, like a real examiner (bot/handlers/speaking.py).

Part 2 is always a single cue card, shown whole. Part 1/3 text - whether
it's a practice question from the admin panel (one question per line) or
whatever the student pasted/photographed - is split on lines and on "?",
with any non-question lead-in ("Let's talk about your hometown.", a
"Part 1 - Hometown" header) folded into the question that follows it.
"""
from __future__ import annotations

import re

MAX_QUESTIONS = 15

_NUMBERING = re.compile(r"^\s*(?:(?:Q|Question\s*)?\d{1,2}\s*[.):-]|[-•*–])\s*", re.IGNORECASE)
_AFTER_QUESTION_MARK = re.compile(r"(?<=\?)\s+")


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
            if not piece:
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
    return questions[:MAX_QUESTIONS]
