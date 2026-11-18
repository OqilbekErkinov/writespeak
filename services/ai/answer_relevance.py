"""Is this essay an answer to THIS question? Asked before a Writing answer is marked (the owner, 2026-11-18).

#197: a ready Band 9 essay written for another question on the same theme ("laws against age discrimination in
hiring: positive or negative?" sent for "more people compete with young people for jobs: problems and solutions")
was marked 8.0 - its English really is Band 8-9 and IELTS marks the language apart from the task. The owner's
ruling: an answer to another question, or one with nothing to do with the question, is not marked at all - the
coins go back and the student is told the answer doesn't fit the question (bot/handlers/writing.py).

Two independent readings; the answer is refused only when both say so - a real answer, however weak, partial or
off in places, is always marked. Never raises: a failure here lets the check go on.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Literal

from pydantic import BaseModel, Field

from bot.config import settings
from services.ai import usage
from services.ai.openai_client import client

logger = logging.getLogger(__name__)

VOTES = 2
MAX_TOKENS = 1500
TIMEOUT = 60.0

SYSTEM = """You check one thing before an IELTS Writing answer is marked: was this text written as an answer to \
THIS question? You do not mark it.

- "answers": the text responds to the given question or task - also when it does so badly, partly, briefly, with \
weak, wrong or one-sided ideas, a misread detail, a missing part, an unusual structure or poor English. A student \
trying to answer this question always gets "answers".
- "other_question": the text was clearly written for a DIFFERENT question: its introduction states or paraphrases \
another question or issue, and the body argues that other issue instead of what this question asks. Sharing the \
topic area is not answering: e.g. the question asks for the problems and solutions of X, the essay argues whether \
a law about Y is a positive development. Task 1: it describes a different chart, table, process or map (another \
subject, other categories, years or places than the task shows).
- "unrelated": nothing to do with the question - another topic altogether, a story, a letter on another subject, \
random or copied text, not an essay at all.

When in doubt, "answers". Write `asks` and `addresses` as one short line each, in English."""


class Relevance(BaseModel):
    asks: str = Field(description="What the question asks the student to do, in one short line.")
    addresses: str = Field(description="What the text actually argues or describes, in one short line.")
    verdict: Literal["answers", "other_question", "unrelated"]


SPEAKING_SYSTEM = """You check one thing before an IELTS Speaking answer is marked: did the student answer THESE \
questions? You get each question with the student's spoken answer (an automatic transcript - expect misheard words \
and no punctuation). You do not mark it.

- "answers": the answers respond to their questions - also when short, hesitant, simple, off in places, with \
a misheard word, poor English, or one answer drifting from its question. A student trying to answer these questions \
always gets "answers".
- "other_question": the answers were clearly given to DIFFERENT questions - a Part 2 talk on another cue card \
topic, or most Part 1/3 answers about other questions than the ones asked. Sharing the topic area is not answering.
- "unrelated": the speech has nothing to do with the questions - talk on another subject altogether, a text read \
aloud, a song, random speech.

Judge the answers together: one off answer among several fitting ones is "answers". When in doubt, "answers". Write \
`asks` and `addresses` as one short line each, in English."""


class OffQuestion(Exception):
    """The answer was written or spoken for another question, or has nothing to do with it: not marked (the owner,
    2026-11-18). Raised by the checks (bot/handlers/writing.py, services/ai/speaking_check.py); the admins' group
    reads it as "Javob savolga mos emas" (bot/utils/check_incidents.py)."""


async def _ask(system: str, content: str) -> Relevance | None:
    try:
        response = await asyncio.wait_for(client.beta.chat.completions.parse(
            model=settings.openai_model_grading,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": content}],
            response_format=Relevance,
            max_completion_tokens=MAX_TOKENS,
            extra_body={"reasoning_effort": "low"},  # openai 1.57: no reasoning_effort argument (vocab_uz.py)
        ), TIMEOUT)
        usage.record("relevance", response)
        return response.choices[0].message.parsed
    except Exception:  # noqa: BLE001 - never the check's problem
        logger.warning("Answer relevance reading failed - the check goes on", exc_info=True)
        return None


async def _decide(label: str, system: str, content: str) -> str | None:
    """Two readings; a reason only when both say the answer doesn't fit (a failed reading counts as "answers")."""
    readings = await asyncio.gather(*(_ask(system, content) for _ in range(VOTES)))
    verdicts = [r.verdict if r is not None else "answers" for r in readings]
    logger.info("Answer relevance (%s): %s", label, ", ".join(verdicts))
    if all(v != "answers" for v in verdicts):
        first = readings[0]
        return f"{verdicts[0]} - asks: {first.asks[:140]}; addresses: {first.addresses[:140]}"
    return None


async def off_question(task_type: str, prompt_text: str, answer_text: str, sheet: str = "") -> str | None:
    """A short reason (for the log and the admins) when the text is clearly not an answer to the question - both
    readings say "other_question" or "unrelated"; None when it is an answer, or when unsure, or on any failure."""
    if not (prompt_text or "").strip() or not (answer_text or "").strip():
        return None
    task = "Writing Task 1 (describe the visual)" if task_type == "task1" else "Writing Task 2 (essay)"
    content = f"{task}\n\nQUESTION:\n{prompt_text.strip()}\n"
    if sheet:
        content += f"\nTHE VISUAL (read from the task's picture):\n{sheet.strip()[:4000]}\n"
    content += f"\nTHE STUDENT'S TEXT:\n{answer_text.strip()}"
    return await _decide(task_type, SYSTEM, content)


async def off_question_speaking(part: str, pairs: list[tuple[str, str]]) -> str | None:
    """As off_question, for a Speaking check: `pairs` = (question, transcript) per answer with speech. None when
    there is nothing to judge."""
    pairs = [(q.strip(), a.strip()) for q, a in pairs if (q or "").strip() and (a or "").strip()]
    if not pairs:
        return None
    names = {"part1": "Part 1", "part2": "Part 2 (cue card)", "part3": "Part 3", "full": "full test"}
    content = f"IELTS Speaking {names.get(part, part)}\n\n" + "\n\n".join(
        f"QUESTION {i}:\n{q[:1500]}\nTHE STUDENT'S ANSWER {i}:\n{a[:3000]}" for i, (q, a) in enumerate(pairs, 1))
    return await _decide(f"speaking {part}", SPEAKING_SYSTEM, content)
