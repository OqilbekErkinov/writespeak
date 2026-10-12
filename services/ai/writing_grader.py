"""Writing (Task 1 / Task 2) grading against the IELTS Writing Band Descriptors -
the "Feedback Coach" (final report, 2026-10-12):

  1. text the student copied from the question is found by code first
     (services/ai/essay_marking.py) and named in the prompt, so the model
     writes a Task mistake with a paraphrase for it;
  2. the feedback call returns WritingFeedback (services/ai/schemas.py) - JSON
     only, the model never produces layout. If essay_marking's `assess` finds
     a problem worth paying for (its marks can't be placed on the essay, a
     section would be short) or the answer runs past its token limit, the
     call is made once more and the better answer kept;
  3. a second call whose only job is to find mistakes the first one missed;
  4. essay_marking.build_report validates everything and assembles the report.

`score_writing` is the same examiner returning scores only - the model
answer's self-check (services/ai/sample_bank.py).

All three run gpt-5 at settings.openai_grading_reasoning_effort, and each has
an output-token limit: on 2026-10-13 a feedback call ran on to 52k tokens
(normal: ~8k at low effort), taking 4.5 minutes and $0.53.
"""
from __future__ import annotations

import base64
import logging

import openai

from bot.config import settings
from services.ai import usage
from services.ai.essay_marking import (
    SCORE_KEYS,
    WritingReport,
    align_segments,
    assess,
    build_report,
    find_copied_spans,
    overall_band,
    snap_band,
)
from services.ai.openai_client import client
from services.ai.schemas import CriterionScores, Mistake, MissedMistake, MissedMistakes, WritingFeedback

logger = logging.getLogger(__name__)

# Output-token limits (reasoning included) - about twice what a long, weak
# essay needs at low effort, so only a runaway answer is cut off.
FEEDBACK_MAX_TOKENS = 20000
MISSED_MAX_TOKENS = 8000
SCORE_MAX_TOKENS = 4000

# Condensed, paraphrased summary of the public IELTS Writing Band Descriptors
# (Task Achievement/Response, Coherence & Cohesion, Lexical Resource,
# Grammatical Range & Accuracy) — grounding context for the grading model,
# not a verbatim reproduction of the official IELTS document.
BAND_DESCRIPTORS = """
Band 9 — Full, precise command: fully addresses the task; ideas are relevant, extended and \
skilfully organized with seamless cohesion and natural paragraphing; wide, precise, natural \
vocabulary with rare minor slips; wide range of accurate, error-free grammar structures.

Band 8 — Very good command: covers all task requirements well, with a clear, well-developed \
position/overview; logically organized with effective cohesion, minor lapses only; wide \
vocabulary used flexibly and precisely, occasional inaccuracies; wide range of structures, the \
majority error-free, only occasional slips.

Band 7 — Good command: addresses all parts of the task, though some ideas could be more fully \
developed; clear progression, logical paragraphing, some overuse/underuse of cohesive devices; \
sufficient vocabulary to allow flexibility and precision, some awareness of style, occasional \
errors in word choice/spelling; a variety of complex structures with good control, some errors \
that rarely reduce communication.

Band 6 — Competent command: addresses the task though some parts may be underdeveloped or the \
format imperfect; arranges information coherently with a clear overall progression, cohesive \
devices used but not always appropriately; adequate vocabulary for the task, some errors in word \
choice/spelling that don't impede communication; a mix of simple and complex structures, some \
grammatical errors but meaning is generally clear.

Band 5 — Modest command: generally addresses the task but the format may be inappropriate in \
places, or the response is repetitive/under-developed; presents information with some \
organization but lacks overall progression, inadequate/inaccurate use of cohesive devices, may be \
repetitive; limited vocabulary, noticeable errors that may cause difficulty for the reader; only a \
limited range of structures, frequent grammatical errors that can cause some difficulty for the \
reader.

Band 4 — Limited command: fails to fully address the task, may misunderstand parts of it, \
conclusions may be unclear or unsupported; ideas are not arranged coherently, minimal/inadequate \
use of cohesive devices; very limited, often inappropriate vocabulary; very limited range of \
structures, frequent errors that severely impede meaning.

Task 1 (Report) — 'Task Achievement': a clear overview of the main trends/differences/stages; all \
key features covered accurately with relevant, accurate data comparisons; NO personal opinion \
(irrelevant for Task 1). Format flexibility matters — line/bar/pie charts, tables, maps, and \
process diagrams each need different overview language.

Task 2 (Essay) — 'Task Response': fully addresses all parts of the prompt; presents a clear, \
consistent position throughout; main ideas are extended, supported and justified with relevant \
examples, not just asserted.
""".strip()

# Revision Brief v2, Section 1 (2026-08-30): calibrate toward the more
# generous end of a band range rather than a literal examiner reading, since
# every WriteSpeak user is an ESL learner, not a native speaker being
# benchmarked against native-level command of English.
ESL_CALIBRATION = """
ESL-CALIBRATED SCORING: When applying the 4 criteria above, weigh communicative effectiveness \
above prescriptive correctness. A single grammar slip, an awkward but understandable collocation, \
or a minor first-language-interference pattern (e.g. article omission, preposition choice, word \
order carried over from Uzbek or Russian) should not by itself pull the band down - only deduct \
when such issues are frequent or severe enough to genuinely interfere with the reader's \
understanding or the response's fluency. Where a response sits between two bands, default to the \
HIGHER one unless the errors are systematic. This is intentionally more generous than a literal \
reading of the descriptors - it's a supportive estimate calibrated for ESL learners, not a \
literal examiner reading, and the report's disclaimer says so.
""".strip()


def _task_instructions(task_type: str) -> str:
    if task_type == "task1":
        return (
            "This is Academic Writing TASK 1 (Report): the student described visual information (a "
            "graph, chart, table, diagram, map, or process) in at least 150 words. Grade 'Task "
            "Achievement' — a clear overview, coverage of key/main features, accurate data "
            "comparisons. Personal opinion is NOT expected and should not raise the score."
        )
    return (
        "This is Writing TASK 2 (Essay): the student responded to an argument/problem/opinion "
        "prompt in at least 250 words. Grade 'Task Response' — full coverage of all parts of the "
        "prompt, a clear position, and ideas that are developed and supported with relevant "
        "examples, not just stated."
    )


def _piece(task_type: str) -> str:
    return "report" if task_type == "task1" else "essay"


def _system_prompt(task_type: str) -> str:
    first = "Task Achievement" if task_type == "task1" else "Task Response"
    piece = _piece(task_type)
    if task_type == "task1":
        tr_scope = "task coverage, the overview, key features, data accuracy, copying the question"
        vocab_scope = "for describing this kind of chart or diagram"
    else:
        tr_scope = "task coverage, position, idea development, copying the question"
        vocab_scope = "for essays on this topic"
    return f"""You are a certified, meticulous IELTS Writing examiner AND a warm, encouraging ESL \
tutor. Grade according to the IELTS Writing Band Descriptors below AND the ESL-calibrated scoring \
note that follows them - the goal is an honest, diagnostic score a student can actually trust and \
act on: not a maximally strict literal reading, and not an inflated, feel-good one either.

{_task_instructions(task_type)}

IELTS WRITING BAND DESCRIPTORS (summary):
{BAND_DESCRIPTORS}

{ESL_CALIBRATION}

Your output is rendered into a feedback report by software, so follow the field rules exactly. \
Write everything in English, in plain words a B1-B2 learner understands.

1. mistakes - EVERY real mistake in the {piece}, in order, ids 1, 2, 3...:
   - wrong: ONLY the part that matters (a few words up to one clause), copied exactly as written.
   - correct: the corrected version of that same part, keeping the student's meaning. It must fit \
back into the sentence: put it in place of `wrong`, read the whole sentence and check that it reads \
correctly, with no repeated words ("argue that that", "the the"). If words must be deleted, \
include a few surrounding words in both wrong and correct.
   - why: one plain-English line, 25 words or fewer.
   - criterion, by the real cause: GRA = grammar (tenses, articles - a missing or unneeded "the" \
is always GRA, agreement, verb patterns, punctuation, sentence structure); LR = vocabulary (word \
choice, collocation, word form, spelling, wordiness, repetition, register); CC = linking, \
referencing, paragraphing and logic gaps (a reason that doesn't lead to its result, e.g. "people \
don't have to search for products, creating many employment opportunities"); TR = {tr_scope}.
   - Real mistakes only: never style preferences ("the economy of the country" is not wrong) and \
never something that is already correct.
   - If you can't tell what the student meant: unclear=true, say in `why` that the meaning is \
unclear, put the most likely meaning in if_you_meant (short, English) and the fix for that meaning \
in `correct`. Otherwise unclear=false and if_you_meant=null.
   - Before you finish the list, do a SECOND pass over the {piece}, sentence by sentence, whose \
only job is to find mistakes you missed. Typical misses: subject-verb agreement in the SECOND verb \
of a clause ("advertising obscures ... and undermines"), historic vs historical, a missing closing \
comma after an inserted phrase, an unneeded "the" before general nouns.
2. essay - the student's {piece} copied EXACTLY (every character, typo and punctuation mark, in \
order), as a list of paragraphs (as in the original), each split into segments:
   - "good": 2-5 genuinely strong phrases in the whole {piece} worth keeping - never on text that \
contains a mistake or that is copied from the question.
   - "error": the exact text of a mistake with its mistake_id. fix = the replacement for that \
text if it is 6 words or fewer (empty string to delete it), otherwise null. Every mistake needs at \
least one error segment.
   - "text": everything else.
   Joining all segment texts must give back the original {piece}: copy punctuation exactly even \
when it is wrong or missing, and never add, drop or repeat words between segments. Never correct \
anything inside "text" or "good" segments, and don't number anything.
3. scores - TR_or_TA ({first}), CC, LR, GRA, each 0-9 in 0.5 steps, per the ESL-calibrated \
approach above. (The overall band is computed by the software from these four.)
4. criterion_notes - one short line per criterion (6-12 words) saying what drives that score.
5. strength - text: 1-2 sentences (40 words at most) on what the {piece} genuinely does well, \
quoting 1-2 of the student's actual strong phrases word for word, in double quotes; \
quoted_phrases: those phrases exactly as in the {piece}. No generic praise - this is the only \
praise in the report.
6. priorities - exactly 3, the most important first, each about ONE specific pattern (never a \
whole category like "grammar"): title (2-6 words), rule (one short line, 15 words at most), \
example_wrong (2-6 words copied exactly from the student's text, no "...") and example_right \
(that piece corrected), criteria (its tag or tags), mistake_ids (2-5 ids from `mistakes` that show THIS pattern; each must have one of \
the priority's criteria; never all the mistakes of a category).
7. vocabulary - 10-12 words or phrases useful {vocab_scope}, about one band above the student's \
level: term, plain-English meaning, an English example sentence on this topic.
8. checklist - 5-6 short lines for "Before you send your next {piece}", built ONLY from this \
report's priorities and mistakes, written as checks the student can tick ("My introduction \
paraphrases the question."); ref = "Priority 01".."Priority 03" or "Mistake <id>".

Plain text only. In why, rule and checklist you may put a key word in **double asterisks** (bold) \
or *single asterisks* (italics); no other markup."""


def _missed_prompt(task_type: str) -> str:
    piece = _piece(task_type)
    return f"""You are a meticulous IELTS Writing proofreader. An examiner has already listed the \
mistakes in a student's {piece} (below). Your ONLY job is to find real mistakes the examiner \
MISSED. Read the {piece} again, sentence by sentence. Typical misses: subject-verb agreement in \
the SECOND verb of a clause ("advertising obscures ... and undermines"), historic vs historical, a \
missing closing comma after an inserted phrase, an unneeded "the" before general nouns, \
prepositions in fixed phrases, word forms.

Rules:
- Don't repeat or overlap a listed mistake, and ignore text copied from the question.
- Only clear errors an IELTS examiner would penalise. Never style preferences, optional commas, \
or choices that are correct in British or American English ("in the streets" and "on the \
streets" are both fine). When in doubt, leave it out.
- wrong: copied exactly from the {piece}, a few words up to one clause, long enough to occur only \
once. correct: that part corrected, keeping the student's meaning; put in place of `wrong`, the \
sentence must read correctly (no "that that").
- why: one plain-English line, 25 words or fewer. criterion: GRA (grammar, articles, agreement, \
punctuation), LR (word choice, collocation, word form, spelling), CC (linking, logic gaps), TR \
(task). unclear=true with if_you_meant only when the meaning can't be determined.
- If nothing was missed, return an empty list. Plain text; you may put a key word in \
**double asterisks**."""


def _with_images(text: str, prompt_images: list[tuple[bytes, str]] | None):
    if not prompt_images:
        return text
    return [
        {
            "type": "text",
            "text": text + "\n\nThe attached image(s) are the visual this task is based on - check "
            "the student's data, comparisons and overview against them.",
        },
        *(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"},
            }
            for data, mime in prompt_images
        ),
    ]


def _copied_note(copied: list[str]) -> str:
    if not copied:
        return ""
    listed = "\n".join(f"- «{c}»" for c in copied)
    return (
        "\n\nCOPIED FROM THE QUESTION (found by software) - these parts of the answer repeat the "
        f"question word for word:\n{listed}\nFor each of them: add one mistake with criterion TR "
        "whose `wrong` is exactly that text, whose `correct` paraphrases it naturally in the "
        "student's own words, and whose `why` says it is copied from the question and examiners "
        "don't count copied words. In `essay` give it as one error segment (fix null) with that "
        "mistake_id, and never mark anything inside it as good."
    )


async def _parse(purpose: str, max_tokens: int, messages: list[dict], response_format):
    """A gpt-5 call at the grading reasoning effort with an output-token limit.
    A call cut off at the limit raises openai.LengthFinishReasonError; its
    cost is logged all the same."""
    try:
        response = await client.beta.chat.completions.parse(
            model=settings.openai_model_grading,
            messages=messages,
            response_format=response_format,
            max_completion_tokens=max_tokens,
            # No `temperature` override - see services/ai/ocr.py's comment.
            # Sent as extra_body: openai 1.57 has no reasoning_effort argument yet.
            extra_body={"reasoning_effort": settings.openai_grading_reasoning_effort},
        )
    except openai.LengthFinishReasonError as e:
        if getattr(e, "completion", None) is not None:
            usage.record(purpose, e.completion)
        raise
    usage.record(purpose, response)
    return response


async def _feedback_call(task_type, prompt_text, answer_text, prompt_images, copied) -> WritingFeedback | None:
    """None when the answer ran past FEEDBACK_MAX_TOKENS."""
    text = f"QUESTION PROMPT:\n{prompt_text}\n\nSTUDENT'S ANSWER:\n{answer_text}{_copied_note(copied)}"
    messages = [
        {"role": "system", "content": _system_prompt(task_type)},
        {"role": "user", "content": _with_images(text, prompt_images)},
    ]
    try:
        response = await _parse("feedback", FEEDBACK_MAX_TOKENS, messages, WritingFeedback)
    except openai.LengthFinishReasonError:
        logger.warning("Feedback ran past %d output tokens - cut off", FEEDBACK_MAX_TOKENS)
        return None
    result = response.choices[0].message.parsed
    if result is None:
        raise RuntimeError("Grading model returned no parsable result")
    return result


async def _missed_errors_call(
    task_type, prompt_text, answer_text, prompt_images, found: list[Mistake]
) -> list[MissedMistake]:
    listed = "\n".join(f"{m.id}. {m.wrong} -> {m.correct}" for m in found) or "(none)"
    text = (
        f"QUESTION PROMPT:\n{prompt_text}\n\nSTUDENT'S ANSWER:\n{answer_text}\n\n"
        f"MISTAKES ALREADY LISTED:\n{listed}"
    )
    messages = [
        {"role": "system", "content": _missed_prompt(task_type)},
        {"role": "user", "content": _with_images(text, prompt_images)},
    ]
    response = await _parse("missed_errors", MISSED_MAX_TOKENS, messages, MissedMistakes)
    parsed = response.choices[0].message.parsed
    return parsed.mistakes if parsed is not None else []


def _rank(feedback: WritingFeedback, problems: list[str], answer_text: str) -> tuple[int, float]:
    return len(problems), -align_segments(answer_text, feedback.essay).share


async def grade_writing(
    task_type: str,
    prompt_text: str,
    answer_text: str,
    prompt_images: list[tuple[bytes, str]] | None = None,
) -> WritingReport:
    """`prompt_images` - (bytes, mime) pairs of the Task 1 chart/diagram, so
    the grader can check the student's figures and overview against the
    actual visual instead of just its text description.

    The report always shows the student's original text; the model's marks
    are located in it (services/ai/essay_marking.py), never an altered copy."""
    copied = [answer_text[s:e] for s, e in find_copied_spans(answer_text, prompt_text)]
    feedback = await _feedback_call(task_type, prompt_text, answer_text, prompt_images, copied)
    problems = assess(feedback, answer_text) if feedback else ["the answer ran past its token limit"]
    if problems:
        logger.warning("Feedback needs another try: %s", "; ".join(problems))
        retry = await _feedback_call(task_type, prompt_text, answer_text, prompt_images, copied)
        if retry is not None:
            retry_problems = assess(retry, answer_text)
            if feedback is None or _rank(retry, retry_problems, answer_text) < _rank(feedback, problems, answer_text):
                feedback, problems = retry, retry_problems
        if feedback is None:
            raise RuntimeError("Feedback ran past its token limit twice")
        if problems:
            logger.warning("Feedback still has problems after the retry: %s", "; ".join(problems))

    try:
        missed = await _missed_errors_call(task_type, prompt_text, answer_text, prompt_images, feedback.mistakes)
    except Exception:
        logger.exception("Missed-errors pass failed - the report goes out with the first pass only")
        missed = []

    report = build_report(task_type, prompt_text, answer_text, feedback, missed)
    if report.notes:
        logger.info("Report validation: %s", "; ".join(report.notes))
    return report


async def score_writing(
    task_type: str,
    prompt_text: str,
    answer_text: str,
    prompt_images: list[tuple[bytes, str]] | None = None,
) -> float:
    """The overall band our examiner gives `answer_text` - scores only."""
    system = (
        "You are a certified IELTS Writing examiner. Score the answer according to the IELTS "
        "Writing Band Descriptors below AND the ESL-calibrated scoring note.\n\n"
        f"{_task_instructions(task_type)}\n\nIELTS WRITING BAND DESCRIPTORS (summary):\n"
        f"{BAND_DESCRIPTORS}\n\n{ESL_CALIBRATION}\n\nReturn only the four criterion scores "
        f"(TR_or_TA = {'Task Achievement' if task_type == 'task1' else 'Task Response'}), each "
        "0-9 in 0.5 steps."
    )
    text = f"QUESTION PROMPT:\n{prompt_text}\n\nANSWER:\n{answer_text}"
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": _with_images(text, prompt_images)},
    ]
    response = await _parse("self_check", SCORE_MAX_TOKENS, messages, CriterionScores)
    scores = response.choices[0].message.parsed
    if scores is None:
        raise RuntimeError("Scoring model returned no parsable result")
    return overall_band([snap_band(getattr(scores, k)) for k in SCORE_KEYS])
