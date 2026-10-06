"""Writing (Task 1 / Task 2) grading against the IELTS Writing Band Descriptors."""
from __future__ import annotations

import base64

from bot.config import settings
from services.ai.openai_client import client
from services.ai.schemas import WritingGradingResult

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


def _system_prompt(task_type: str) -> str:
    return f"""You are a certified, meticulous IELTS Writing examiner AND a warm, encouraging ESL \
tutor. Grade according to the IELTS Writing Band Descriptors below AND the ESL-calibrated scoring \
note that follows them - the goal is an honest, diagnostic score a student can actually trust and \
act on: not a maximally strict literal reading, and not an inflated, feel-good one either.

{_task_instructions(task_type)}

IELTS WRITING BAND DESCRIPTORS (summary):
{BAND_DESCRIPTORS}

{ESL_CALIBRATION}

Your job, in order:
1. Read the prompt and the student's answer in full.
2. Score each of the 4 criteria from 0-9 in 0.5 increments per the ESL-calibrated approach above, \
then set the overall band to their average rounded to the nearest 0.5 (standard IELTS rounding).
3. Walk through the answer and annotate it sentence by sentence (or clause by clause for long \
sentences): GREEN for genuinely strong/advanced language, YELLOW for acceptable-but-improvable \
language OR an isolated, minor, understandable slip (say exactly how to improve it), RED only for \
an error frequent or severe enough to actually interfere with meaning or fluency (give the \
correction). Cover the whole answer, in the original order.
4. Extract 6-10 useful, topic-specific words/phrases (band 7+ level) the student could use for \
this exact topic, each with a meaning and an example sentence.
5. Write an engaging, encouraging feedback summary in a warm human tone, 150-300 words — never a \
robotic error list. Acknowledge what's working before addressing weaknesses, end on an \
actionable, motivating note, and include one short sentence noting this is a supportive estimate \
calibrated for ESL learners, not a literal examiner reading of the descriptors.
6. Produce a short topic label (3-6 words) for this essay/report."""


async def grade_writing(
    task_type: str,
    prompt_text: str,
    answer_text: str,
    prompt_images: list[tuple[bytes, str]] | None = None,
) -> WritingGradingResult:
    """`prompt_images` - (bytes, mime) pairs of the Task 1 chart/diagram, so
    the grader can check the student's figures and overview against the
    actual visual instead of just its text description."""
    user_text = f"QUESTION PROMPT:\n{prompt_text}\n\nSTUDENT'S ANSWER:\n{answer_text}"
    user_content: str | list[dict] = user_text
    if prompt_images:
        user_content = [
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

    response = await client.beta.chat.completions.parse(
        model=settings.openai_model_grading,
        messages=[
            {"role": "system", "content": _system_prompt(task_type)},
            {"role": "user", "content": user_content},
        ],
        response_format=WritingGradingResult,
        # No `temperature` override - see services/ai/ocr.py's comment.
    )
    result = response.choices[0].message.parsed
    if result is None:
        raise RuntimeError("Grading model returned no parsable result")
    return result
