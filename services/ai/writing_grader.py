"""Writing (Task 1 / Task 2) grading against the IELTS Writing Band Descriptors -
the "Feedback Coach" (final report, 2026-10-12; reliability rules, Part 4 of
the Speaking report prompt, 2026-10-16; calibrated marking, 2026-10-22):

  1. text the student copied from the question is found by code
     (services/ai/essay_marking.py) and named in the prompt, so the model
     writes a Task mistake with a paraphrase for it;
  2. the feedback call returns WritingFeedback (services/ai/schemas.py) - JSON
     only: the mistakes, the marked essay, priorities, vocabulary. If
     essay_marking's `assess` finds a problem worth paying for (its marks
     can't be placed on the essay, a section would be short) or the answer
     runs past its token limit, the call is made once more and the better
     answer kept;
  3. a second call finds mistakes the first one missed, while the mistake
     filter checks every one of the first pass's mistakes - would an
     examiner count it (not a style preference or valid variant), does the
     fix keep the meaning and fit back into the sentence, and which ONE
     criterion it belongs to - then the second pass's finds the same way;
     what fails is dropped, what is under the wrong criterion is moved;
  4. the scores, from the checked mistakes (mistakes first, 2026-10-23):
     SCORE_RUNS independent scoring calls, whole bands, median per criterion
     (services/ai/scoring.py) - the same essay got 8.0 and then 8.5 when the
     feedback call also scored it (reports #14 and #15). Each run follows the
     marking guide (MARKING_GUIDE: strengths before weaknesses, start from
     Band 7, benefit of the doubt, one problem under one criterion, what is
     NOT an error) against fixed anchor answers with their examiner's
     commentary (services/ai/writing_anchors.py), judges accuracy by the
     checked list and its density, and names what decided each band - the
     cover's criterion notes. The caps of the length and paragraphing policy
     are then applied in code (cap_bands). `on_feedback` then lets the caller
     start the model answer written for these results (services/ai/
     sample_bank.py);
  5. essay_marking.build_report validates everything and assembles the report.

A Task 1 visual is read into a checked data sheet first (services/ai/
visual_reading.py, by the caller): every call above judges the student's
figures by that sheet and Task Achievement by its key features - with the
image still attached for its layout - so the whole report uses the same
figures, and uncertain ones are never held against the student.

Until 2026-10-22 the scorer was told "never round up when between two
bands" (added 2026-10-16 against inflated bands); it then counted mistakes,
style preferences included, and fair 6.5-7.0 essays got 6.0 (Islamov #35-
#38). The guardrails against inflation are now the anchors and the
calibration against the books (scripts/calibrate_writing.py).

`score_writing` is the same examiner returning one run's overall band - the
model answer's self-check (services/ai/sample_bank.py).

All calls run gpt-5 at settings.openai_grading_reasoning_effort, and each has
an output-token limit: on 2026-10-13 a feedback call ran on to 52k tokens
(normal: ~8k at low effort), taking 4.5 minutes and $0.53.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
from dataclasses import dataclass, field

import openai

from bot.config import settings
from services.ai import usage
from services.ai.essay_marking import (
    SCORE_KEYS,
    WritingReport,
    align_segments,
    assess,
    build_report,
    count_words,
    find_copied_spans,
    paragraph_count,
)
from services.ai.openai_client import parse_flex
from services.ai.schemas import Mistake, MissedMistake, MissedMistakes, WritingFeedback, WritingScoring, WritingVerdicts
from services.ai.scoring import median_of_runs, overall_band, target_band, whole_band
from services.ai.visual_reading import JUDGE_RULES
from services.ai.writing_anchors import anchors_text, patterns_text

logger = logging.getLogger(__name__)

# Output-token limits (reasoning included) - about twice what a long, weak
# essay needs at low effort, so only a runaway answer is cut off.
FEEDBACK_MAX_TOKENS = 20000
MISSED_MAX_TOKENS = 8000
VERIFY_MAX_TOKENS = 8000
SCORE_MAX_TOKENS = 8000

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

# What is never a mistake, and where each problem belongs - shared by the
# scorer, the feedback, the missed-errors pass and the mistake filter, so a
# style preference can neither lower a band nor reach the report.
NOT_ERRORS = """NOT ERRORS - never count or list these: style preferences and valid variants \
("different from/than/to", "treated differently than", "live healthier", British vs American \
spelling and usage, "volunteers of art" vs "art volunteers", a plainer word that is still correct, \
an optional comma); anything whose "fix" would change what the student meant."""

ONE_CRITERION = """ONE PROBLEM, ONE CRITERION: word choice, collocation, word form, spelling and \
register -> LR (Lexical); grammar and punctuation -> GRA; linking, referencing, paragraphing and \
logic -> CC; content, task coverage and data -> TR (Task Achievement/Response). A language problem \
never lowers Task Achievement/Response - "were quite passive" in a report is a word-choice (LR) \
issue, not a Task one."""

MARKING_GUIDE = f"""MARKING GUIDE - how to find each criterion's band:
1. List the strengths first, each with a short quote from the answer. Then the weaknesses, each \
with a short quote - real problems of THIS criterion only.
2. Read the Band 7 description. If the answer meets most of it, award 7 unless there is a clear \
Band 7 failure. Then check whether it also meets Band 8, or falls to 6.
3. Benefit of the doubt: when torn between two bands and the answer shows the key features of the \
higher one, give the higher band.
4. Score each criterion independently, on its own evidence. Never lower one criterion because \
another is weak.
5. Credit what works: less common vocabulary used well and complex structures used accurately count \
for the band, not only the absence of errors. Find the band that fits best - don't count mistakes.

PLAIN-LANGUAGE BAND GUIDE
Task Response (Task 2):
  5 - position unclear or inconsistent; ideas limited or not developed.
  6 - relevant position and main ideas, but some insufficiently developed or unclear.
  7 - clear position throughout; main ideas extended and supported (may over-generalise a little).
  8 - well-developed position; ideas relevant, extended and well supported; occasional lapses.
  9 - every part fully addressed; a fully developed position; ideas relevant, fully extended and well \
supported; lapses extremely rare.
  Support is explanation, reasoning, consequences or an example (a hypothetical one is fine): specific \
real-world examples are NOT required - never lower Task Response for "few examples" when the ideas are \
explained and extended.
Coherence & Cohesion:
  5 - some organisation, but no clear progression; missing or inadequate paragraphing; repetitive or \
faulty linking.
  6 - coherent overall; paragraphing used; linking sometimes mechanical or faulty; some unclear referencing.
  7 - logical organisation and clear progression; a range of linking and referencing used flexibly, \
with a few lapses.
  8 - easy to follow; cohesion well managed; paragraphing sufficient and appropriate.
  9 - cohesion so natural it attracts no attention; skilful paragraphing; lapses minimal.
Lexical Resource:
  5 - limited range; frequent errors that cause some difficulty.
  6 - adequate range; tries less common words with some errors; meaning clear.
  7 - enough range for flexibility and precision; some less common items, used with awareness of \
style and collocation; occasional errors.
  8 - wide range used fluently; skilful less common or idiomatic items; rare slips.
  9 - full flexibility and precision; wide, natural, sophisticated vocabulary; rare minor slips only.
Grammatical Range & Accuracy:
  5 - limited range; complex sentences attempted but often faulty; frequent errors.
  6 - a mix of simple and complex forms; errors occur but rarely reduce communication.
  7 - a variety of complex structures; frequent error-free sentences; good control with a few errors.
  8 - wide range; the majority of sentences error-free; only occasional non-systematic errors.
  9 - a wide range used with full flexibility and accuracy; rare minor slips only.
Band 8 is not the ceiling: an answer that meets the Band 9 description gets 9 - a Band 9 answer may \
still contain rare minor slips, and it need not be perfect.

ERROR DENSITY (LR and GRA): a 250-350-word essay with about 3-8 small slips (articles, agreement, \
prepositions) spread across otherwise varied, mostly error-free sentences is Band 7, not 6 (for a \
150-200-word report: about 2-5 such slips). Band 8: only occasional slips (about 1-3) in a wide range; \
Band 9: rare minor slips. Band 6 is when errors appear in many sentences. Band 5 is when errors are \
frequent and sometimes make the reader work to understand. Count only real errors: apply the NOT ERRORS \
list first, and a slip you are not sure of doesn't count.

{NOT_ERRORS}

{ONE_CRITERION}

LENGTH AND PARAGRAPHS (software applies these caps after you): Task 1 - 140 words or more: no \
penalty; 100-139 words: Task Achievement 6 at most; under 100: 5 at most. Task 2 - 240 words or more: \
no penalty; 200-239: Task Response 6 at most; under 200: 5 at most. A Task 2 essay with no paragraph \
breaks: Coherence & Cohesion 5 at most. Apart from these, length is never a reason to lower a band.

CHECKED MISTAKES: the answer comes with the list of its mistakes, found by one examiner and checked \
by a second, who removed everything that is not a real error. It is the complete list of real errors: \
judge accuracy (Grammar, Lexical Resource) by its counts and density per 100 words - never count \
anything that is not on it - and credit the STRONG PHRASES listed with it (less common vocabulary and \
well-built structures used well). As a guide: up to about 2 grammar errors per 100 words in varied, \
mostly error-free sentences is Band 7; errors in many sentences is Band 6; frequent errors that make \
the reader work is Band 5.

THE NOTE for each criterion: 6-14 plain words for the student naming the strength or weakness that \
decided the band - never a language point under Task Achievement/Response; for Lexical Resource and \
Grammar, name what earns credit as well as what holds it back."""

TA_GUIDE = """TASK ACHIEVEMENT (Task 1) - judge the report against the visual's DATA SHEET and its KEY \
FEATURES, in this order:
1. Overview - the biggest factor. Is there one? Is it accurate? Does it state the main trends or \
differences (changes, extremes, contrasts) - not just static facts, or only the first and last steps \
of a process? (A Band 6.5 anchor is error-free and precise, but its overview only gives a process's \
start and end points.) Compare it with the sheet's expected overview. An overview that is limited - \
static, too general, only the start and end, or partly inaccurate - keeps Task Achievement at 6 at \
most, however good the rest is.
2. Key features: does the report cover the main ones? The sheet's key comparisons are a menu, not a \
checklist: a 150-200-word report selects the most significant, and leaving some out is not a weakness \
when its selection is sensible.
3. Comparisons: Task 1 requires them. Count the genuine comparisons (higher/lower than, overtook, the \
gap between, while X ..., Y ..., doubled, the same as). A report that lists figures with no comparisons \
gets Task Achievement 5 at most.
4. Data accuracy: take it from the DATA CHECK, which software ran on every figure of the report \
against the sheet, within its precision ("around 40%" for 41 ±2 is correct): a figure it found on the \
sheet is accurate - never call it inaccurate or invented, and don't re-read the chart to doubt it; look \
only at the figures it lists as not found (some are still fair approximations). Readers round and \
estimate a little differently; a year between two labelled years on a line graph's axis is read from \
the chart, not invented. One or two figure slips in \
an otherwise accurate report are minor slips (Task Achievement 7-8 is still possible); data lowers it \
below 7 only when inaccuracies are frequent or distort a key feature (the overview, a main trend). For a \
map or a process, the sheet's wording is one careful description: a change or step described \
differently but reasonably is not an error.
5. Organisation of content: a logical grouping is good (risers vs fallers; one paragraph per map); a \
different grouping that still works is not a weakness.
6. Speculation: reasons or opinions the visual doesn't show are a Task problem only when they \
replace data description ("this is because people became lazy").
Task Achievement 9 - fully satisfies the task: a clear, fully developed overview; the key features \
skilfully selected, compared and illustrated; lapses extremely rare.
Task Achievement 8 - a clear, well-chosen overview of the main trends or differences; all key features \
covered with well-selected figures; frequent, accurate comparisons; data accurate.
Task Achievement 7 - a clear overview of the main trends or differences; key features covered and \
illustrated; comparisons are made; minor omissions or slips.
Task Achievement 6 - an overview present but limited (static, too general, or partly inaccurate); key \
features covered, but some detail mechanical, irrelevant or inaccurate; comparisons limited.
Task Achievement 5 - no clear overview; mostly lists figures with few or no comparisons; some key \
features missing or wrong.
NEVER lower Task Achievement for: language mistakes (they belong to LR or GRA); a valid grouping or \
order that differs from the sheet's; approximate figures within the reading precision; leaving out a \
minor detail; no conclusion when there is an overview (Task 1 needs none); anything based on an \
UNCERTAIN figure."""

# Every Bandzilla user is an ESL learner: that softens the feedback's
# WORDING only - the bands come from the scoring runs.
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
            "stages; its key features selected, compared and highlighted; figures, units, dates and "
            "categories that match it; nothing it doesn't show."
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
    return (" The Task Achievement line names what the report gets right or wrong about the VISUAL - the "
            "overview, the key features, the comparisons, the figures - never a language point.")


def _system_prompt(task_type: str, bands: dict[str, float], overall: float, target: float,
                   notes: dict[str, str] | None = None) -> str:
    first = "Task Achievement" if task_type == "task1" else "Task Response"
    piece = _piece(task_type)
    if task_type == "task1":
        tr_scope = ("the overview, key features left out, comparisons, data accuracy (by the DATA SHEET), "
                    "copying the question - never word choice or grammar")
        vocab_scope = "for describing this kind of chart or diagram"
        gap_examples = "a sharper overview, comparisons that group the data, more precise trend vocabulary"
    else:
        tr_scope = ("task coverage, position, idea development, a conclusion that matches the body, copying the "
                    "question - never word choice or grammar")
        vocab_scope = "for essays on this topic"
        gap_examples = ("develop your second point with an example, make the conclusion match the body, "
                        "sharpen collocations")
    # The task's fixed rules first - the same for every essay of this task
    # type, so OpenAI's prompt cache bills them at a tenth of the price when
    # checks follow each other - and this essay's scores (if any) at the end.
    if bands is None:
        # Mistakes first (2026-10-23): the bands are decided after this report's
        # mistakes are checked, from the checked list.
        scores = ("THE BANDS ARE DECIDED LATER, from the mistakes you list once they are checked - so list every "
                  "real mistake and only real mistakes, and choose priorities by what holds this answer back most "
                  f"(where real mistakes are few: what separates it from the next band - {gap_examples}).")
        opening = "The bands are decided separately."
    else:
        high_note = (
            f"The overall band is {overall:.1f}: where real mistakes are few, take priorities from what separates "
            f"this band from the next in the descriptors ({gap_examples})."
            if overall >= 7 else ""
        )
        deciding = ""
        if notes:
            deciding = (" What decided each band: " + "; ".join(
                f"{name} - {notes[k]}" for k, name in (("TR_or_TA", first), ("CC", "Coherence & Cohesion"),
                                                       ("LR", "Lexical Resource"), ("GRA", "Grammar"))
                if notes.get(k)) + ".")
        scores = (
            f"THE SCORES ARE ALREADY DECIDED (separate scoring runs): {first} {bands['TR_or_TA']:.0f}, Coherence "
            f"& Cohesion {bands['CC']:.0f}, Lexical Resource {bands['LR']:.0f}, Grammatical Range & Accuracy "
            f"{bands['GRA']:.0f}; overall band {overall:.1f}; the student's next target is {target:.1f}.{deciding} "
            f"Everything you write must be consistent with these scores. {high_note}"
        ).strip()
        opening = "The decided scores are given at the end."
    return f"""You are a certified, meticulous IELTS Writing examiner AND a warm, encouraging ESL \
tutor writing a feedback report. {opening}

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
   - {NOT_ERRORS}
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
    """The scorer's fixed instructions - the same for every answer of a task
    type (prompt-cached): the guide, Task 1's TA guide and overview patterns,
    the anchors."""
    first = "Task Achievement" if task_type == "task1" else "Task Response"
    if task_type == "task1":
        check = (
            "Before you score Task Achievement, read the DATA SHEET (or, without one, the visual) and its key "
            "features, then the report's overview, its comparisons and its figures."
        )
        extra = "\n\n".join(x for x in (TA_GUIDE, patterns_text()) if x)
    else:
        check = (
            "Before you score Task Response, check: does the answer do exactly what the question asks (all of "
            "its parts, its type - advantages AND disadvantages, both views, ...)? Is the position the same "
            "from the introduction to the conclusion? Are main ideas extended and supported?"
        )
        extra = ""
    parts = [
        "You are a certified IELTS Writing examiner. Score the answer against the IELTS Writing Band "
        f"Descriptors, following the marking guide.\n\n{_task_instructions(task_type)}",
        f"IELTS WRITING BAND DESCRIPTORS (summary):\n{BAND_DESCRIPTORS}",
        MARKING_GUIDE,
        extra,
        anchors_text(task_type),
        f"{check} For each criterion (TR_or_TA = {first}) give the strengths, then the weaknesses, then "
        "the whole band and the note. The student is an ESL learner, but that never changes a band.",
    ]
    return "\n\n".join(p for p in parts if p)


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
- Only clear errors an IELTS examiner would penalise. When in doubt, leave it out.
- {NOT_ERRORS}
- wrong: copied exactly from the {piece}, a few words up to one clause, long enough to occur only \
once. correct: that part corrected, keeping the student's meaning; put in place of `wrong`, the \
sentence must read correctly (no "that that").
- why: one plain-English line, 25 words or fewer. criterion: GRA (grammar, articles, agreement, \
punctuation), LR (word choice, collocation, word form, spelling), CC (linking, logic gaps), TR \
(task, content, data - never language). unclear=true with if_you_meant only when the meaning can't \
be determined.
- If nothing was missed, return an empty list. Plain text; you may put a key word in \
**double asterisks**."""


VERIFY_SYSTEM = """You are a senior IELTS Writing examiner double-checking a colleague's list of \
mistakes in a student's essay or report. For each item ask: would an IELTS examiner count this as \
an error? Decide whether to keep it, and under which ONE criterion it belongs.

keep=false when ANY of these is true:
- the original is ALREADY CORRECT, grammatical and natural in its context - read the whole \
sentence: a verb may correctly continue an earlier verb phrase ("people start using it and spend \
hours" is right; "spending" would change the meaning), a negative or future form may be correct as \
it stands;
- it is a STYLE PREFERENCE or a valid variant: a different-but-equally-good choice (a demonstrative \
instead of an article, a near-synonym, "ranging from X to Y", a slightly plainer word), \
"different/differently than", "live healthier", British vs American spelling or usage, "volunteers \
of art" vs "art volunteers", an optional comma;
- the correction CHANGES WHAT THE STUDENT MEANT;
- the correction does NOT FIT BACK into the original sentence (put it in place of the wrong part \
and read the sentence: it must read correctly, with no doubled or missing words).

keep=true for every real error an examiner would penalise, including collocation errors, \
overstated or wrong-meaning words, verb-pattern errors, unclear meaning, linking phrases that \
don't fit the logic, and logic errors (a conclusion that contradicts the body).

criterion - by the real cause, one only: LR = word choice, collocation, word form, spelling, \
register; GRA = grammar and punctuation; CC = linking, referencing, paragraphing, logic; TR = \
content, task coverage, the overview, data. A word-choice problem is LR even when it was listed \
under TR ("were quite passive" -> LR).

A DATA item (a figure, ranking or trend in a Task 1 report) is judged ONLY by the DATA SHEET given \
with the text and its DATA CHECK (a figure the check found on the sheet is correct): keep=false when \
the student's version agrees with the sheet - the printed figure \
(rounding with "about" is fine), any figure within a scale reading's ±range, an UNCERTAIN figure \
or one the sheet can't read, a ranking or trend the sheet shows - or when the "correction" itself \
disagrees with the sheet; keep=true when the verified figures clearly contradict the student. With \
no DATA SHEET and no visual, a mistake about a figure is keep=false (nothing to check it against).

Judge each item on its own; give a short reason. Return a verdict for every id."""


async def _verify_call(source_text: str, items: list[tuple[int, str, str, str, str, str]]
                       ) -> dict[int, tuple[bool, str, str]]:
    """`items`: (id, where, wrong, correct, why, criterion). {id: (keep,
    criterion, reason)}; items without a verdict are kept as they are."""
    if not items:
        return {}
    listed = "\n".join(f"{i}. [{crit}] «{wrong}» -> «{correct}» ({why})" for i, _, wrong, correct, why, crit in items)
    messages = [
        {"role": "system", "content": VERIFY_SYSTEM},
        {"role": "user", "content": f"{source_text}\n\nMISTAKES TO CHECK:\n{listed}"},
    ]
    response = await _parse("verify", VERIFY_MAX_TOKENS, messages, WritingVerdicts)
    parsed = response.choices[0].message.parsed
    return {v.id: (v.keep, v.criterion, v.reason) for v in parsed.verdicts} if parsed else {}


NO_VISUAL_NOTE = (
    "\n\nNO VISUAL WAS PROVIDED - the student sent this Task 1 question as text only, so the figures "
    "can't be checked. Mark Task Achievement on the overview, the key features as far as the question "
    "shows them, the comparisons and the organisation; never lower it for data accuracy, never count a "
    "figure as a mistake and never invent what the visual might show."
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
                "from them twice and checked: take every figure from the sheet; the image shows the layout and "
                "context. Check the answer against it: every figure, unit, date, trend and comparison, whether "
                "the overview matches the expected overview, and which key features are missing.")
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
                         sheet: str = "", notes: dict[str, str] | None = None) -> WritingFeedback | None:
    """None when the answer ran past FEEDBACK_MAX_TOKENS. `bands` None: the
    bands are decided after the mistakes (mistakes first)."""
    overall = overall_band(list(bands.values())) if bands else 0.0
    text = f"QUESTION PROMPT:\n{prompt_text}\n\nSTUDENT'S ANSWER:\n{answer_text}{_copied_note(copied)}"
    messages = [
        {"role": "system", "content": _system_prompt(task_type, bands, overall, target_band(overall), notes)},
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


# --- Scores -----------------------------------------------------------------------------------------------

_COMPARISON_RE = re.compile(
    r"\b(than|while|whereas|compared|comparison|contrast|unlike|similar(ly)?|same|equal(ly)?|both|either|neither|"
    r"double[ds]?|twice|triple[ds]?|half|halved|overtook|overtake[ns]?|surpass\w*|exceed\w*|outnumber\w*|gap|"
    r"difference|differ\w*|more|less|fewer|higher|lower|larger|smaller|bigger|greater|most|least|highest|lowest|"
    r"largest|smallest|biggest|greatest|top|bottom|lead|led|followed by|rank\w*)\b", re.IGNORECASE)


def count_comparisons(text: str) -> int:
    """Comparison words in a Task 1 report - zero means it only lists figures."""
    return len(_COMPARISON_RE.findall(text))


@dataclass
class Scores:
    bands: dict[str, float]  # TR_or_TA, CC, LR, GRA - after the caps
    notes: dict[str, str]  # what decided each band (the cover's criterion notes)
    capped: list[str] = field(default_factory=list)  # the caps applied, for the log
    notices: list[str] = field(default_factory=list)  # shown in the report
    runs: list[dict] = field(default_factory=list)  # every scoring run's bands

    @property
    def overall(self) -> float:
        return overall_band(list(self.bands.values()))


def cap_bands(task_type: str, answer_text: str, bands: dict[str, float], notes: dict[str, str]):
    """The length and paragraphing policy (marking guide B2, chosen to match
    the books - Volume 2 gives Band 8.0 to a 148-word report): returns
    (bands, notes, the caps applied, report notices)."""
    bands, notes = dict(bands), dict(notes)
    words, paragraphs = count_words(answer_text), paragraph_count(answer_text)
    capped, notices = [], []

    def cap(key: str, limit: float, note: str) -> None:
        if bands[key] > limit:
            capped.append(f"{key} {bands[key]:.0f} -> {limit:.0f} ({note})")
            bands[key] = limit
            notes[key] = note

    if task_type == "task1":
        if words < 100:
            cap("TR_or_TA", 5, f"Only {words} words: under 100 words keeps Task Achievement at 5 at most.")
        elif words < 140:
            cap("TR_or_TA", 6, f"Only {words} words: under 140 words keeps Task Achievement at 6 at most.")
        elif words < 150:
            notices.append(f"Your report has {words} words - no penalty this time, but aim for at least 150.")
            if notes.get("TR_or_TA"):
                notes["TR_or_TA"] = f"{notes['TR_or_TA'].rstrip('. ')} ({words} words: aim for 150+)."
        if count_comparisons(answer_text) == 0:
            cap("TR_or_TA", 5, "Figures are listed with no comparisons, so Task Achievement is 5 at most.")
    else:
        if words < 200:
            cap("TR_or_TA", 5, f"Only {words} words: under 200 words keeps Task Response at 5 at most.")
        elif words < 240:
            cap("TR_or_TA", 6, f"Only {words} words: under 240 words keeps Task Response at 6 at most.")
        if paragraphs <= 1:
            cap("CC", 5, "No paragraph breaks: one block of text keeps Coherence at 5 at most.")
    return bands, notes, capped, notices


async def _score_once(task_type, prompt_text, answer_text, prompt_images, sheet: str = "",
                      evidence: str = "") -> dict:
    """One scoring run: {criterion: whole band, ..., "_notes": {criterion: note}}.
    `evidence`: the checked mistakes and strong phrases (mistake_evidence)."""
    facts = f"COUNTED BY SOFTWARE: {count_words(answer_text)} words, {paragraph_count(answer_text)} paragraph(s)."
    text = f"QUESTION PROMPT:\n{prompt_text}\n\nANSWER:\n{answer_text}\n\n{facts}"
    if evidence:
        text += f"\n\n{evidence}"
    messages = [
        {"role": "system", "content": _scoring_system(task_type)},
        {"role": "user", "content": _with_images(text, prompt_images, task_type == "task1", _judged(sheet))},
    ]
    response = await _parse("score", SCORE_MAX_TOKENS, messages, WritingScoring)
    scores = response.choices[0].message.parsed
    if scores is None:
        raise RuntimeError("Scoring model returned no parsable result")
    out: dict = {k: whole_band(getattr(scores, k).band) for k in SCORE_KEYS}
    out["_notes"] = {k: getattr(scores, k).note.strip() for k in SCORE_KEYS}
    return out


_EVIDENCE_NAMES = (("GRA", "Grammar (GRA)"), ("LR", "Vocabulary (LR)"), ("CC", "Coherence (CC)"),
                   ("TR", "Task (TR)"))


def mistake_evidence(answer_text: str, mistakes: list, strong: list[str]) -> str:
    """The checked mistakes (each with .wrong, .correct, .criterion) and the
    strong phrases, as the scoring runs get them (mistakes first, 2026-10-23)."""
    words = max(count_words(answer_text), 1)
    lines = ["CHECKED MISTAKES (found by one examiner, checked by a second - everything that is not a real error "
             f"was removed; this is the complete list). The answer has {words} words."]
    for key, name in _EVIDENCE_NAMES:
        mine = [m for m in mistakes if m.criterion == key]
        if not mine:
            lines.append(f"- {name}: none.")
            continue
        listed = "; ".join(f"«{m.wrong}» -> «{m.correct}»" for m in mine[:14])
        lines.append(f"- {name}: {len(mine)} ({len(mine) * 100 / words:.1f} per 100 words): {listed}")
    if strong:
        lines.append("STRONG PHRASES the examiner marked (credit them): " + "; ".join(f"«{x}»" for x in strong[:6]))
    return "\n".join(lines)


async def decide_scores(
    task_type: str,
    prompt_text: str,
    answer_text: str,
    prompt_images: list[tuple[bytes, str]] | None = None,
    sheet: str = "",
    evidence: str = "",
) -> Scores:
    """The report's criterion bands: the median of SCORE_RUNS scoring runs,
    then the caps; each band's note comes from a run that gave that band.
    `evidence`: the checked mistakes (grade_writing passes them)."""
    medians, runs = await median_of_runs(
        lambda: _score_once(task_type, prompt_text, answer_text, prompt_images, sheet, evidence), SCORE_KEYS,
        label=f"writing {task_type}",
    )
    notes = {}
    for k in SCORE_KEYS:
        notes[k] = next((r["_notes"][k] for r in runs if whole_band(r[k]) == medians[k] and r["_notes"].get(k)), "")
    bands, notes, capped, notices = cap_bands(task_type, answer_text, medians, notes)
    if capped:
        logger.info("Score caps (writing %s): %s", task_type, "; ".join(capped))
    return Scores(bands=bands, notes=notes, capped=capped, notices=notices,
                  runs=[{k: r[k] for k in SCORE_KEYS} for r in runs])


async def score_writing_runs(
    task_type: str,
    prompt_text: str,
    answer_text: str,
    prompt_images: list[tuple[bytes, str]] | None = None,
    sheet: str = "",
) -> dict[str, float]:
    """The report's criterion bands (decide_scores without the notes)."""
    return (await decide_scores(task_type, prompt_text, answer_text, prompt_images, sheet)).bands


async def score_writing(
    task_type: str,
    prompt_text: str,
    answer_text: str,
    prompt_images: list[tuple[bytes, str]] | None = None,
    sheet: str = "",
) -> float:
    """The overall band our examiner gives `answer_text` in one run - the
    model answer's self-check."""
    run = await _score_once(task_type, prompt_text, answer_text, prompt_images, sheet)
    return overall_band([run[k] for k in SCORE_KEYS])


# --- The whole check -------------------------------------------------------------------------------------


async def grade_writing(
    task_type: str,
    prompt_text: str,
    answer_text: str,
    prompt_images: list[tuple[bytes, str]] | None = None,
    on_feedback=None,
    sheet: str = "",
    notices: list[str] | None = None,
) -> WritingReport:
    """`prompt_images` - (bytes, mime) pairs of the Task 1 chart/diagram, so
    the grader can check the student's figures and overview against the
    actual visual instead of just its text description; `sheet` - its data
    sheet (services/ai/visual_reading.py) with the report's data check, by
    which every call judges the figures. `on_feedback(bands, overall,
    feedback)` is called as soon as the bands are decided. `notices`: lines
    the report shows the student (e.g. the chart wasn't checked).

    Mistakes first (the owner's rule, 2026-10-22: #38's Grammar still came
    out 6 with the bands decided first): the feedback lists the mistakes, the
    filter checks them, and only then do the scoring runs decide the bands -
    from the checked list, so accuracy is judged by the real errors and
    their density, never by style preferences.

    The report always shows the student's original text; the model's marks
    are located in it (services/ai/essay_marking.py), never an altered copy."""
    copied = [answer_text[s:e] for s, e in find_copied_spans(answer_text, prompt_text)]
    feedback = await _feedback_call(task_type, prompt_text, answer_text, prompt_images, copied, None, sheet)
    problems = assess(feedback, answer_text) if feedback else ["the answer ran past its token limit"]
    if problems:
        logger.warning("Feedback needs another try: %s", "; ".join(problems))
        retry = await _feedback_call(task_type, prompt_text, answer_text, prompt_images, copied, None, sheet)
        if retry is not None:
            retry_problems = assess(retry, answer_text)
            if feedback is None or _rank(retry, retry_problems, answer_text) < _rank(feedback, problems, answer_text):
                feedback, problems = retry, retry_problems
        if feedback is None:
            raise RuntimeError("Feedback ran past its token limit twice")
        if problems:
            logger.warning("Feedback still has problems after the retry: %s", "; ".join(problems))

    # The mistake filter: "would an examiner count this, and under which
    # criterion?" - the first pass's mistakes are checked while the missed-
    # errors pass runs; its finds are checked after it. Text copied from the
    # question and unclear meaning are not corrections to check. With a
    # chart's data sheet, the data mistakes are checked against it too:
    # report #62's "corrections" of figures the student had read right were
    # never checked.
    copied_norm = [c.lower() for c in copied]
    source = f"QUESTION PROMPT:\n{prompt_text}\n\nSTUDENT'S {_piece(task_type).upper()}:\n{answer_text}"
    if sheet:
        source += f"\n\n{sheet}"
    elif task_type == "task1":
        source += "\n\n(No visual and no data sheet: the figures can't be checked.)"

    def checkable(m) -> bool:
        return not m.unclear and not any(m.wrong.strip().lower() in c for c in copied_norm)

    async def verify(items) -> dict[int, tuple[bool, str, str]]:
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
        verify([(m.id, "essay", m.wrong, m.correct, m.why, m.criterion) for m in feedback.mistakes if checkable(m)]),
        find_missed(),
    )
    missed_verdicts = await verify(
        [(k, "essay", x.wrong, x.correct, x.why, x.criterion) for k, x in enumerate(missed) if checkable(x)])
    for label, found in (("mistake", verdicts), ("missed-pass item", missed_verdicts)):
        for i, (keep, _, reason) in found.items():
            if not keep:
                logger.info("Verification dropped %s %s: %s", label, i, reason)
    dropped = {i for i, (keep, _, _) in verdicts.items() if not keep}
    retag = {i: crit for i, (keep, crit, _) in verdicts.items() if keep}
    kept_missed = []
    for k, x in enumerate(missed):
        keep, crit, _ = missed_verdicts.get(k, (True, x.criterion, ""))
        if keep:
            kept_missed.append(x.model_copy(update={"criterion": crit}) if crit != x.criterion else x)

    # The bands, from the checked mistakes (the report's own list: what the
    # filter kept, under the criterion it belongs to) and the strong phrases.
    checked = [m.model_copy(update={"criterion": retag.get(m.id, m.criterion)})
               for m in feedback.mistakes if m.id not in dropped] + kept_missed
    strong = [seg.text.strip() for paragraph in feedback.essay for seg in paragraph if seg.type == "good"]
    scores = await decide_scores(task_type, prompt_text, answer_text, prompt_images, sheet,
                                 mistake_evidence(answer_text, checked, strong))
    if on_feedback is not None:
        try:
            on_feedback(scores.bands, scores.overall, feedback)
        except Exception:
            logger.exception("on_feedback failed")

    report = build_report(task_type, prompt_text, answer_text, feedback, kept_missed, bands=scores.bands,
                          dropped=dropped, retag=retag, criterion_notes=scores.notes,
                          notices=(notices or []) + scores.notices)
    if report.notes:
        logger.info("Report validation: %s", "; ".join(report.notes))
    return report
