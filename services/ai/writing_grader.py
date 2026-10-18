"""Writing (Task 1 / Task 2) grading against the IELTS Writing Band Descriptors -
the "Feedback Coach" (final report, 2026-10-12; reliability rules, Part 4 of
the Speaking report prompt, 2026-10-16):

  1. scores: SCORE_RUNS independent scoring calls, whole bands, median per
     criterion (services/ai/scoring.py) - the same essay got 8.0 and then 8.5
     when the feedback call also scored it (reports #14 and #15);
  2. text the student copied from the question is found by code
     (services/ai/essay_marking.py) and named in the prompt, so the model
     writes a Task mistake with a paraphrase for it;
  3. the feedback call returns WritingFeedback (services/ai/schemas.py) - JSON
     only, written for the scores already decided. If essay_marking's
     `assess` finds a problem worth paying for (its marks can't be placed on
     the essay, a section would be short) or the answer runs past its token
     limit, the call is made once more and the better answer kept. Once it
     is final, `on_feedback` lets the caller start the model answer written
     for these results (services/ai/sample_bank.py);
  4. a second call finds mistakes the first one missed, while a third checks
     the first pass's mistakes - is each definitely wrong, and does the
     correction keep the meaning? - then the second pass's finds are checked
     the same way; anything that isn't is dropped;
  5. essay_marking.build_report validates everything and assembles the report.

A Task 1 chart is read once into a data sheet first (services/ai/
visual_reading.py, by the caller), and every call above judges the
student's figures by that sheet - with the image still attached for its
layout - so the whole report uses the same figures, and the verification
pass checks the data mistakes against it too.

`score_writing` is the same examiner returning scores only - the model
answer's self-check (services/ai/sample_bank.py).

All calls run gpt-5 at settings.openai_grading_reasoning_effort, and each has
an output-token limit: on 2026-10-13 a feedback call ran on to 52k tokens
(normal: ~8k at low effort), taking 4.5 minutes and $0.53.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re

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
)
from services.ai.openai_client import parse_flex
from services.ai.schemas import CriterionScores, Mistake, MissedMistake, MissedMistakes, Verdicts, WritingFeedback
from services.ai.scoring import LENIENCY_NOTE, median_of_runs, overall_band, target_band, whole_band
from services.ai.visual_reading import JUDGE_RULES

logger = logging.getLogger(__name__)

# Output-token limits (reasoning included) - about twice what a long, weak
# essay needs at low effort, so only a runaway answer is cut off.
FEEDBACK_MAX_TOKENS = 20000
MISSED_MAX_TOKENS = 8000
VERIFY_MAX_TOKENS = 8000
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

# Every Bandzilla user is an ESL learner. Since 2026-10-16 that softens the
# feedback's WORDING only - the bands come from strict scoring runs
# (scoring.LENIENCY_NOTE): "default to the higher band" had inflated them
# (an essay a careful marking puts at 7.5 got 8.0 and 8.5).
ESL_WORDING = """
ESL-FRIENDLY WORDING: the student is an ESL learner. Be warm and encouraging, explain in plain \
words, and acknowledge what works - but stay honest: never call something correct that isn't, and \
never soften a real problem into nothing.
""".strip()


def _task_instructions(task_type: str) -> str:
    if task_type == "task1":
        return (
            "This is Academic Writing TASK 1 (Report): the student described visual information (a "
            "graph, chart, table, diagram, map, or process) in at least 150 words. Grade 'Task "
            "Achievement' — a clear overview, coverage of key/main features, accurate data "
            "comparisons. Personal opinion is NOT expected and should not raise the score.\n"
            "The VISUAL is the source of truth, not the report's own wording: Task Achievement is how "
            "faithfully the report presents it - an overview of its main trends, differences or "
            "stages; its key features selected and highlighted; figures, units, dates, categories and "
            "comparisons that match it; nothing it doesn't show. In the descriptors' terms: no clear "
            "overview keeps Task Achievement at 5 at most; key features inadequately covered keep it at "
            "5 at most; inaccurate or irrelevant details keep it at 6 at most; 7+ needs a clear overview "
            "of the main trends, differences or stages and key features clearly presented and highlighted."
        )
    return (
        "This is Writing TASK 2 (Essay): the student responded to an argument/problem/opinion "
        "prompt in at least 250 words. Grade 'Task Response' — full coverage of all parts of the "
        "prompt, a clear position, and ideas that are developed and supported with relevant "
        "examples, not just stated."
    )


def _piece(task_type: str) -> str:
    return "report" if task_type == "task1" else "essay"


def _ta_note(task_type: str) -> str:
    if task_type != "task1":
        return ""
    return (" The Task Achievement line names what the report gets right or wrong about the VISUAL - a "
            "missing or unclear overview, a key feature left out, wrong figures.")


def _system_prompt(task_type: str, bands: dict[str, float], overall: float, target: float) -> str:
    first = "Task Achievement" if task_type == "task1" else "Task Response"
    piece = _piece(task_type)
    if task_type == "task1":
        tr_scope = "task coverage, the overview, key features, data accuracy, copying the question"
        vocab_scope = "for describing this kind of chart or diagram"
        gap_examples = "a sharper overview, comparisons that group the data, more precise trend vocabulary"
    else:
        tr_scope = "task coverage, position, idea development, a conclusion that matches the body, copying the question"
        vocab_scope = "for essays on this topic"
        gap_examples = ("develop your second point with an example, make the conclusion match the body, "
                        "sharpen collocations")
    high_note = (
        f"The overall band is {overall:.1f}: where real mistakes are few, take priorities from what separates "
        f"this band from the next in the descriptors ({gap_examples})."
        if overall >= 7 else ""
    )
    # The task's fixed rules first - the same for every essay of this task
    # type, so OpenAI's prompt cache bills them at a tenth of the price when
    # checks follow each other - and this essay's scores at the end.
    scores = (
        f"THE SCORES ARE ALREADY DECIDED (separate scoring runs): {first} {bands['TR_or_TA']:.0f}, Coherence "
        f"& Cohesion {bands['CC']:.0f}, Lexical Resource {bands['LR']:.0f}, Grammatical Range & Accuracy "
        f"{bands['GRA']:.0f}; overall band {overall:.1f}; the student's next target is {target:.1f}. Everything "
        f"you write must be consistent with these scores. {high_note}"
    ).strip()
    return f"""You are a certified, meticulous IELTS Writing examiner AND a warm, encouraging ESL \
tutor writing a feedback report. The decided scores are given at the end.

{_task_instructions(task_type)}

IELTS WRITING BAND DESCRIPTORS (summary):
{BAND_DESCRIPTORS}

{ESL_WORDING}

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
choice, collocation, word form, spelling, wordiness, repetition, register, overstated words); CC = \
linking, referencing, paragraphing and logic gaps (a reason that doesn't lead to its result, e.g. \
"people don't have to search for products, creating many employment opportunities"); TR = {tr_scope}.
   - Real mistakes only: never style preferences ("the economy of the country" is not wrong), never \
something that is already correct, and never a "fix" that changes what the student meant. Read the \
whole sentence before flagging: a verb may correctly continue an earlier verb phrase, a negative or \
future form may be right as it stands.
   - If you can't tell what the student meant: unclear=true, say in `why` that the meaning is \
unclear, put the most likely meaning in if_you_meant (short, English) and the fix for that meaning \
in `correct`. Otherwise unclear=false and if_you_meant=null.
   - Before you finish the list, do a SECOND pass over the {piece}, sentence by sentence, whose \
only job is to find mistakes you missed. Typical misses: subject-verb agreement in the SECOND verb \
of a clause ("advertising obscures ... and undermines"), historic vs historical, a missing closing \
comma after an inserted phrase, an unneeded "the" before general nouns, wrong word partners \
(collocations), overstated words, a fixed phrase used with the wrong meaning, a linking phrase \
that doesn't fit the logic ("In other words" before a new idea), and a conclusion that takes a \
position the body never argued.
2. essay - the student's {piece} copied EXACTLY (every character, typo and punctuation mark, in \
order), as a list of paragraphs (as in the original), each split into segments:
   - "good": 2-5 of the {piece}'s BEST phrases - language above the student's level worth keeping \
(a precise collocation, an idiomatic expression, a well-built complex structure), never an ordinary \
phrase, never text that contains a mistake or that is copied from the question.
   - "error": the exact text of a mistake with its mistake_id. fix = the replacement for that \
text if it is 6 words or fewer (empty string to delete it), otherwise null. Every mistake needs at \
least one error segment.
   - "text": everything else.
   Joining all segment texts must give back the original {piece}: copy punctuation exactly even \
when it is wrong or missing, and never add, drop or repeat words between segments. Never correct \
anything inside "text" or "good" segments, and don't number anything.
3. criterion_notes - one short line per criterion (6-12 words) saying what drives that score.{_ta_note(task_type)}
4. strength - text: 1-2 sentences (40 words at most) on what the {piece} genuinely does well, \
quoting 1-2 of the phrases you marked "good", word for word, in double quotes; quoted_phrases: \
those phrases exactly as in the {piece}. No generic praise - this is the only praise in the report.
5. priorities - exactly 3, the most important first, each about ONE specific pattern (never a \
whole category like "grammar"): title (2-6 words), rule (one short line, 15 words at most), \
example_wrong (2-6 words copied exactly from the student's text, no "...") and example_right \
(that piece corrected), criteria (its tag or tags), mistake_ids (2-5 ids from `mistakes` that show \
THIS pattern; each must have one of the priority's criteria; never all the mistakes of a \
category). Never build a priority around one trivial word.
6. vocabulary - 10-12 words or phrases useful {vocab_scope}, about one band above the student's \
level: term, plain-English meaning, an English example sentence on this topic.
7. checklist - 5-6 short lines for "Before you send your next {piece}", built ONLY from this \
report's priorities and mistakes, written as forward-looking habits the student can tick ("My \
introduction paraphrases the question.") - never "I fixed ..."; ref = "Priority 01".."Priority 03" \
or "Mistake <id>".

Plain text only. In why, rule and checklist you may put a key word in **double asterisks** (bold) \
or *single asterisks* (italics); no other markup.

{scores}"""


def _scoring_system(task_type: str) -> str:
    first = "Task Achievement" if task_type == "task1" else "Task Response"
    if task_type == "task1":
        check = (
            "Check before you score: read the visual FIRST - its type, units, period and categories, "
            "its main trends, differences or stages, and its key figures. Then compare the report with "
            "it: is there a clear overview of what the visual mainly shows? Are all its key features "
            "covered and compared? Does every figure, unit, date and trend match the visual? Is anything "
            "described that the visual doesn't show?"
        )
    else:
        check = (
            "Check before you score: does the answer do exactly what the question asks (all of its "
            "parts, its type - advantages AND disadvantages, both views, ...)? Is the position the same "
            "from the introduction to the conclusion? Are main ideas extended and supported?"
        )
    return (
        "You are a certified IELTS Writing examiner. Score the answer against the IELTS Writing Band "
        f"Descriptors below.\n\n{_task_instructions(task_type)}\n\nIELTS WRITING BAND DESCRIPTORS "
        f"(summary):\n{BAND_DESCRIPTORS}\n\n{LENIENCY_NOTE}\n\n{check} Return the four bands (TR_or_TA = "
        f"{first}) as whole numbers."
    )


def _missed_prompt(task_type: str) -> str:
    piece = _piece(task_type)
    return f"""You are a meticulous IELTS Writing proofreader. An examiner has already listed the \
mistakes in a student's {piece} (below). Your ONLY job is to find real mistakes the examiner \
MISSED. Read the {piece} again, sentence by sentence. Typical misses: subject-verb agreement in \
the SECOND verb of a clause ("advertising obscures ... and undermines"), historic vs historical, a \
missing closing comma after an inserted phrase, an unneeded "the" before general nouns, \
prepositions in fixed phrases, word forms, wrong word partners (collocations: "make a research" -> \
"do research"), overstated words (an absolute word where a softer one is meant), a fixed phrase \
used with the wrong meaning, a linking phrase that doesn't fit the logic, and a conclusion that \
takes a position the body never argued (TR).

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


VERIFY_SYSTEM = """You are a senior IELTS Writing examiner double-checking a colleague's list of \
mistakes in a student's essay or report. For each item decide whether to keep it.

keep=false when:
- the original is ALREADY CORRECT, grammatical and natural in its context - read the whole \
sentence: a verb may correctly continue an earlier verb phrase ("people start using it and spend \
hours" is right; "spending" would change the meaning), a negative or future form may be correct as \
it stands;
- it is only a STYLE PREFERENCE or a different-but-equally-good choice (a demonstrative instead of \
an article, a near-synonym, "ranging from X to Y", a slightly plainer word);
- the correction CHANGES WHAT THE STUDENT MEANT;
- it is an optional comma, British vs American usage, or a choice that is correct either way.

keep=true for every real error an examiner would penalise, including collocation errors, \
overstated or wrong-meaning words, verb-pattern errors, unclear meaning, linking phrases that \
don't fit the logic, and logic errors (a conclusion that contradicts the body).

A DATA item (a figure, ranking or trend in a Task 1 report) is judged ONLY by the DATA SHEET given \
with the text: keep=false when the student's version agrees with the sheet - the printed value \
(rounding with "about" is fine), any figure inside an estimated value's range, a value the sheet \
can't read, a ranking or trend the sheet shows - or when the "correction" itself disagrees with the \
sheet; keep=true when the sheet clearly contradicts the student.

Judge each item on its own; give a short reason. Return a verdict for every id."""


async def _verify_call(source_text: str, items: list[tuple[int, str, str, str, str]]) -> dict[int, tuple[bool, str]]:
    """`items`: (id, where, wrong, correct, why). {id: (keep, reason)};
    items without a verdict are kept."""
    if not items:
        return {}
    listed = "\n".join(f"{i}. «{wrong}» -> «{correct}» ({why})" for i, _, wrong, correct, why in items)
    messages = [
        {"role": "system", "content": VERIFY_SYSTEM},
        {"role": "user", "content": f"{source_text}\n\nMISTAKES TO CHECK:\n{listed}"},
    ]
    response = await _parse("verify", VERIFY_MAX_TOKENS, messages, Verdicts)
    parsed = response.choices[0].message.parsed
    return {v.id: (v.keep, v.reason) for v in parsed.verdicts} if parsed else {}


NO_VISUAL_NOTE = (
    "\n\nNO VISUAL WAS PROVIDED - the student sent this Task 1 question as text only. Check figures "
    "only against the question text and for consistency within the report; never invent what the "
    "visual might show, and don't count figures you can't verify as mistakes."
)


def _judged(sheet: str) -> str:
    """The data sheet with the rules for judging a student's figures by it."""
    return f"{sheet}\n\n{JUDGE_RULES}" if sheet else ""


def _with_images(text: str, prompt_images: list[tuple[bytes, str]] | None, task1: bool = False, sheet: str = ""):
    """The user message: the text, plus the Task 1 visual in full detail (the
    figures on a chart are small) - or, for a Task 1 without one, a note.
    `sheet`: the visual's data sheet (and rules), when it was read."""
    if sheet:
        text = f"{text}\n\n{sheet}"
    if not prompt_images:
        return text + NO_VISUAL_NOTE if task1 else text
    if sheet:
        look = ("The attached image(s) are the visual this task is based on. Its DATA SHEET above was read "
                "from them once: take every figure from the sheet; the image shows the layout and context. "
                "Check the answer against it: every figure, unit, date, trend and comparison, whether the "
                "overview matches what the visual mainly shows, and which key features are missing.")
    else:
        look = ("The attached image(s) are the visual this task is based on - the source of truth for its "
                "data. Read it first: its type, units, period and categories, the main trends, differences or "
                "stages, and the key figures. Then check the answer against it: every figure, unit, date, "
                "trend and comparison, whether the overview matches what the visual mainly shows, and which "
                "key features are missing.")
    return [
        {"type": "text", "text": f"{text}\n\n{look}"},
        *(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}", "detail": "high"},
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
        response = await parse_flex(  # the Flex tier, the default as a fallback (openai_client)
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


async def _feedback_call(task_type, prompt_text, answer_text, prompt_images, copied, bands,
                         sheet: str = "") -> WritingFeedback | None:
    """None when the answer ran past FEEDBACK_MAX_TOKENS."""
    overall = overall_band(list(bands.values()))
    text = f"QUESTION PROMPT:\n{prompt_text}\n\nSTUDENT'S ANSWER:\n{answer_text}{_copied_note(copied)}"
    messages = [
        {"role": "system", "content": _system_prompt(task_type, bands, overall, target_band(overall))},
        {"role": "user", "content": _with_images(text, prompt_images, task_type == "task1", _judged(sheet))},
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
    task_type, prompt_text, answer_text, prompt_images, found: list[Mistake], sheet: str = ""
) -> list[MissedMistake]:
    listed = "\n".join(f"{m.id}. {m.wrong} -> {m.correct}" for m in found) or "(none)"
    text = (
        f"QUESTION PROMPT:\n{prompt_text}\n\nSTUDENT'S ANSWER:\n{answer_text}\n\n"
        f"MISTAKES ALREADY LISTED:\n{listed}"
    )
    messages = [
        {"role": "system", "content": _missed_prompt(task_type)},
        {"role": "user", "content": _with_images(text, prompt_images, task_type == "task1", _judged(sheet))},
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
    on_feedback=None,
    sheet: str = "",
) -> WritingReport:
    """`prompt_images` - (bytes, mime) pairs of the Task 1 chart/diagram, so
    the grader can check the student's figures and overview against the
    actual visual instead of just its text description; `sheet` - its data
    sheet (services/ai/visual_reading.py), by which every call judges the
    figures. `on_feedback(bands, overall, feedback)` is called as soon as the
    feedback is final.

    The report always shows the student's original text; the model's marks
    are located in it (services/ai/essay_marking.py), never an altered copy."""
    bands = await score_writing_runs(task_type, prompt_text, answer_text, prompt_images, sheet)
    copied = [answer_text[s:e] for s, e in find_copied_spans(answer_text, prompt_text)]
    feedback = await _feedback_call(task_type, prompt_text, answer_text, prompt_images, copied, bands, sheet)
    problems = assess(feedback, answer_text) if feedback else ["the answer ran past its token limit"]
    if problems:
        logger.warning("Feedback needs another try: %s", "; ".join(problems))
        retry = await _feedback_call(task_type, prompt_text, answer_text, prompt_images, copied, bands, sheet)
        if retry is not None:
            retry_problems = assess(retry, answer_text)
            if feedback is None or _rank(retry, retry_problems, answer_text) < _rank(feedback, problems, answer_text):
                feedback, problems = retry, retry_problems
        if feedback is None:
            raise RuntimeError("Feedback ran past its token limit twice")
        if problems:
            logger.warning("Feedback still has problems after the retry: %s", "; ".join(problems))
    if on_feedback is not None:
        try:
            on_feedback(bands, overall_band(list(bands.values())), feedback)
        except Exception:
            logger.exception("on_feedback failed")

    # "Is this definitely wrong?" - the first pass's mistakes are checked while
    # the missed-errors pass runs; its finds are checked after it. Text copied
    # from the question and unclear meaning are not corrections to check. With
    # a chart's data sheet, the data mistakes (TR, with a figure) are checked
    # against it too: report #62's "corrections" of figures the student had
    # read right were never checked.
    copied_norm = [c.lower() for c in copied]
    source = f"QUESTION PROMPT:\n{prompt_text}\n\nSTUDENT'S {_piece(task_type).upper()}:\n{answer_text}"
    if sheet:
        source += f"\n\n{sheet}"

    def checkable(m) -> bool:
        data_item = bool(sheet) and m.criterion == "TR" and bool(re.search(r"\d", f"{m.wrong} {m.correct}"))
        return not m.unclear and (m.criterion in ("GRA", "LR", "CC") or data_item) and not any(
            m.wrong.strip().lower() in c for c in copied_norm)

    async def verify(items) -> dict[int, tuple[bool, str]]:
        try:
            return await _verify_call(source, items)
        except Exception:
            logger.exception("Verification pass failed - keeping those mistakes")
            return {}

    async def find_missed() -> list[MissedMistake]:
        try:
            return await _missed_errors_call(task_type, prompt_text, answer_text, prompt_images, feedback.mistakes,
                                             sheet)
        except Exception:
            logger.exception("Missed-errors pass failed - the report goes out with the first pass only")
            return []

    verdicts, missed = await asyncio.gather(
        verify([(m.id, "essay", m.wrong, m.correct, m.why) for m in feedback.mistakes if checkable(m)]),
        find_missed(),
    )
    missed_verdicts = await verify([(k, "essay", x.wrong, x.correct, x.why) for k, x in enumerate(missed) if checkable(x)])
    for label, found in (("mistake", verdicts), ("missed-pass item", missed_verdicts)):
        for i, (keep, reason) in found.items():
            if not keep:
                logger.info("Verification dropped %s %s: %s", label, i, reason)
    dropped = {i for i, (keep, _) in verdicts.items() if not keep}
    missed = [x for k, x in enumerate(missed) if missed_verdicts.get(k, (True, ""))[0]]

    report = build_report(task_type, prompt_text, answer_text, feedback, missed, bands=bands, dropped=dropped)
    if report.notes:
        logger.info("Report validation: %s", "; ".join(report.notes))
    return report


async def _score_once(task_type, prompt_text, answer_text, prompt_images, sheet: str = "") -> dict[str, float]:
    text = f"QUESTION PROMPT:\n{prompt_text}\n\nANSWER:\n{answer_text}"
    messages = [
        {"role": "system", "content": _scoring_system(task_type)},
        {"role": "user", "content": _with_images(text, prompt_images, task_type == "task1", _judged(sheet))},
    ]
    response = await _parse("score", SCORE_MAX_TOKENS, messages, CriterionScores)
    scores = response.choices[0].message.parsed
    if scores is None:
        raise RuntimeError("Scoring model returned no parsable result")
    return {k: whole_band(getattr(scores, k)) for k in SCORE_KEYS}


async def score_writing_runs(
    task_type: str,
    prompt_text: str,
    answer_text: str,
    prompt_images: list[tuple[bytes, str]] | None = None,
    sheet: str = "",
) -> dict[str, float]:
    """The report's criterion bands: the median of SCORE_RUNS scoring runs."""
    bands, _ = await median_of_runs(
        lambda: _score_once(task_type, prompt_text, answer_text, prompt_images, sheet), SCORE_KEYS,
        label=f"writing {task_type}",
    )
    return bands


async def score_writing(
    task_type: str,
    prompt_text: str,
    answer_text: str,
    prompt_images: list[tuple[bytes, str]] | None = None,
    sheet: str = "",
) -> float:
    """The overall band our examiner gives `answer_text` in one run - the
    model answer's self-check."""
    return overall_band(list((await _score_once(task_type, prompt_text, answer_text, prompt_images, sheet)).values()))
