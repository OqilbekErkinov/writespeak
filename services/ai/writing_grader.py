"""Writing (Task 1 / Task 2) grading against the IELTS Writing Band Descriptors -
the "Feedback Coach". Returns structured JSON (services/ai/schemas.py's
WritingFeedback) that services/ai/essay_marking.py validates against the
student's original essay and services/pdf/report_builder.py renders; the
model never produces layout (report redesign, 2026-10).
"""
from __future__ import annotations

import base64
import logging

from bot.config import settings
from services.ai.essay_marking import (
    MIN_ANCHORED_SHARE,
    MIN_USABLE_SHARE,
    WritingReport,
    align_segments,
    build_report,
)
from services.ai.openai_client import client
from services.ai.schemas import WritingFeedback

logger = logging.getLogger(__name__)

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
            "This is Writing TASK 1 (Report): the student described visual information (a graph, "
            "chart, table, diagram, map, or process) in at least 150 words. Grade 'Task "
            "Achievement' — a clear overview, coverage of key/main features, accurate data "
            "comparisons. Personal opinion is NOT expected and should not raise the score."
        )
    return (
        "This is Writing TASK 2 (Essay): the student responded to an argument/problem/opinion "
        "prompt in at least 250 words. Grade 'Task Response' — full coverage of all parts of the "
        "prompt, a clear position, and ideas that are developed and supported with relevant "
        "examples, not just stated."
    )


REPORT_LANGUAGES = {"uz": "Uzbek (Latin script)", "ru": "Russian"}

# Standard Uzbek grammar terms - without them the model e.g. renders
# "articles" as "maqola" (a newspaper article).
UZ_TERMS = (
    " Use the usual Uzbek terms: article = artikl, tense = zamon, preposition = predlog, "
    "collocation = so'z birikmasi, word choice = so'z tanlash, linking words = bog'lovchilar, "
    "punctuation = tinish belgilari, sentence structure = gap tuzilishi, agreement = moslashuv."
)


def _system_prompt(task_type: str, lang: str) -> str:
    report_language = REPORT_LANGUAGES.get(lang, REPORT_LANGUAGES["uz"])
    first = "Task Achievement" if task_type == "task1" else "Task Response"
    return f"""You are a certified, meticulous IELTS Writing examiner AND a warm, encouraging ESL \
tutor. Grade according to the IELTS Writing Band Descriptors below AND the ESL-calibrated scoring \
note that follows them - the goal is an honest, diagnostic score a student can actually trust and \
act on: not a maximally strict literal reading, and not an inflated, feel-good one either.

{_task_instructions(task_type)}

IELTS WRITING BAND DESCRIPTORS (summary):
{BAND_DESCRIPTORS}

{ESL_CALIBRATION}

Your output is rendered into a feedback report by software, so follow the field rules exactly.

1. mistakes - every real mistake in the essay, in essay order, ids 1, 2, 3...:
   - wrong: ONLY the part that matters (a few words up to one clause), copied exactly as written.
   - correct: the corrected version of that same part. Always keep the student's meaning. Never \
leave it empty: if words must be deleted, include a few surrounding words in both wrong and correct.
   - why: one plain-English line, 25 words or fewer.
   - criterion: LR (word choice, collocation, spelling, repetition, register), GRA (grammar, \
tense, articles, agreement, punctuation, sentence structure), CC (linking, referencing, \
paragraphing, logical flow), TR_or_TA (task coverage, position, idea development, overview/data).
   - If you can't tell what the student meant, don't guess silently: set unclear=true, say in \
`why` that the meaning is unclear, give the most likely intended meaning in likely_meaning \
(short, in {report_language}), and put the correction for that meaning in `correct`. Otherwise \
unclear=false and likely_meaning=null.
   - Never list something that is already correct.
2. essay - the student's essay copied EXACTLY (every character, typo and punctuation mark, in \
order), as a list of paragraphs (as in the original), each split into segments:
   - "good": 2-5 genuinely strong phrases in the whole essay worth keeping.
   - "error": the exact text of a mistake with its mistake_id. fix = the replacement for that \
text if it is 6 words or fewer (empty string to delete it), otherwise null.
   - "text": everything else.
   Joining all segment texts must give back the original essay: copy punctuation exactly even \
when it is wrong or missing, and never add, drop or repeat words between segments. Never \
correct anything inside "text" or "good" segments, and don't number anything in the essay.
3. scores - TR_or_TA ({first}), CC, LR, GRA, each 0-9 in 0.5 steps, per the ESL-calibrated \
approach above. (The overall band is computed by the software from these four.)
4. criterion_notes - one short line per criterion (about 4-10 words) in {report_language}, \
saying what drives that score.{UZ_TERMS if lang == 'uz' else ''}
5. strength - 1-2 specific English sentences about what this essay genuinely does well. This is \
the only praise in the report.
6. focus - at most 3 items for the next essay, most important first: title (2-5 English words), \
rule (one English line with the key correct forms), criterion, and mistake_ids (the ids from \
`mistakes` that show this problem; an empty list if none). Don't repeat examples in the rule.
7. vocabulary - 10-12 words/phrases useful for this topic, about one band above the student's \
level: term, plain-English meaning, English example sentence on this topic.
8. topic - a 3-6 word English label for the essay's topic.

All explanations (why, strength, focus, vocabulary) are in English. Only criterion_notes and \
likely_meaning are in {report_language}."""


def _user_content(prompt_text: str, answer_text: str, prompt_images: list[tuple[bytes, str]] | None):
    user_text = f"QUESTION PROMPT:\n{prompt_text}\n\nSTUDENT'S ANSWER:\n{answer_text}"
    if not prompt_images:
        return user_text
    return [
        {
            "type": "text",
            "text": user_text + "\n\nThe attached image(s) are the visual this task is "
            "based on - check the student's data, comparisons and overview against them.",
        },
        *(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"},
            }
            for data, mime in prompt_images
        ),
    ]


async def _feedback_call(task_type, prompt_text, answer_text, prompt_images, lang) -> WritingFeedback:
    response = await client.beta.chat.completions.parse(
        model=settings.openai_model_grading,
        messages=[
            {"role": "system", "content": _system_prompt(task_type, lang)},
            {"role": "user", "content": _user_content(prompt_text, answer_text, prompt_images)},
        ],
        response_format=WritingFeedback,
        # No `temperature` override - see services/ai/ocr.py's comment.
    )
    result = response.choices[0].message.parsed
    if result is None:
        raise RuntimeError("Grading model returned no parsable result")
    return result


async def grade_writing(
    task_type: str,
    prompt_text: str,
    answer_text: str,
    prompt_images: list[tuple[bytes, str]] | None = None,
    lang: str = "uz",
) -> WritingReport:
    """`prompt_images` - (bytes, mime) pairs of the Task 1 chart/diagram, so
    the grader can check the student's figures and overview against the
    actual visual instead of just its text description.

    The report always shows the student's original essay; the model's marks
    are located in it (services/ai/essay_marking.py). If too few can be
    found, the model is asked once more, and if that doesn't help the report
    shows the plain essay without marks - never an altered one."""
    feedback = await _feedback_call(task_type, prompt_text, answer_text, prompt_images, lang)
    alignment = align_segments(answer_text, feedback.essay)
    if alignment.share < MIN_ANCHORED_SHARE:
        logger.warning(
            "Only %d/%d essay marks found in the original - retrying the feedback call once",
            alignment.anchored, alignment.marked,
        )
        retry = await _feedback_call(task_type, prompt_text, answer_text, prompt_images, lang)
        retry_alignment = align_segments(answer_text, retry.essay)
        if retry_alignment.share > alignment.share:
            feedback, alignment = retry, retry_alignment
    spans = alignment.spans if alignment.share >= MIN_USABLE_SHARE else None
    if spans is None:
        logger.warning("Essay marks still don't line up - report shows the plain essay")
    return build_report(task_type, answer_text, feedback, spans)
