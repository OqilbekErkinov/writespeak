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
import difflib
import logging
import re
from dataclasses import dataclass, field
from types import SimpleNamespace

import openai

from bot.config import settings
from services.ai import usage
from services.ai.essay_marking import (
    PASTE_NOTICE,
    SCORE_KEYS,
    TASK_MIN_WORDS,
    WritingReport,
    align_segments,
    assess,
    build_report,
    canon,
    clean_pasted,
    count_words,
    drop_priorities,
    copy_min_words,
    find_copied_spans,
    without_priorities,
    paragraph_count,
)
from services.ai.openai_client import parse_flex
from services.ai.schemas import Mistake, MissedMistake, MissedMistakes, WritingFeedback, WritingScoring, WritingVerdicts
from services.ai.scoring import median_of_runs, overall_band, target_band, whole_band
from services.ai import marking_config
from services.ai.visual_reading import JUDGE_RULES
from services.ai.visual_check import (
    data_visual,
    figures,
    match_figure,
    protected_mistake,
    unchecked_figures,
    visual_kind,
)
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
spelling and usage, "volunteers of art" vs "art volunteers", "hold influence over", "a renewed \
interest in", "drawbacks to", "see a rise", "foods" meaning kinds of food, "others'" with the noun \
left out, "was least popular", "a chart-high / chart-low of", "bear parallels to/with", "reach \
parity", "buck the trend", a plainer word that is still correct, an optional comma); a paraphrase of a \
chart's label ("age at death" for "life expectancy", "proximity to home" for "close to parental \
home"); a word or phrase the question itself uses; a proper name as its owner writes it ("Malala \
Fund"); the writer's own grouping of steps or categories ("in the final stage" of the writer's own \
three stages); anything whose "fix" would change what the student meant.
NEVER PENALISE (the academic lead, 2026-10-29): paraphrasing the question ("available" for "open" is good, \
not a mistake); a free, non-template structure (no "This essay will discuss", a short body paragraph, an \
unusual but logical order, the writer's own conclusion style); British or American spelling used consistently."""

ONE_CRITERION = """ONE PROBLEM, ONE CRITERION: word choice, collocation, word form, spelling and \
register -> LR (Lexical); grammar and punctuation -> GRA; linking, referencing, paragraphing and \
logic -> CC; content, task coverage and data -> TR (Task Achievement/Response). A language problem \
never lowers Task Achievement/Response - "were quite passive" in a report is a word-choice (LR) \
issue, not a Task one."""

# What a scorer looks for on each criterion before it may give 9 (the academic lead, 2026-10-29: #71's
# Coherence 9 though "Taking this into account" followed nothing and one paragraph held two central ideas)
CHECKLISTS = {
    "TR_or_TA": {
        "task1": ["no clear overview of the main trends or differences", "a key feature left out",
                  "a figure or comparison the DATA SHEET contradicts", "details listed without comparisons"],
        "task2": ["a part of the question not answered", "a position that is unclear or changes",
                  "a main idea stated but not extended or supported", "an idea off the question"],
    },
    "CC": ["a linker that doesn't fit the logic of what it joins", "a paragraph with two central ideas",
           "mechanical or repetitive linking (the same linker again, a linker opening every sentence)",
           "unclear referencing (this / it / they with no clear antecedent)",
           "an order of ideas that breaks the progression"],
    "LR": ["a wrong word or collocation among the CHECKED MISTAKES", "repetition where range is expected",
           "a word whose register doesn't fit"],
    "GRA": ["a grammar error among the CHECKED MISTAKES", "a narrow range of structures"],
}


def checklist_text(task_type: str) -> str:
    lines = ["CHECKLIST - run it on every criterion BEFORE choosing its band: answer every item (found or not; "
             "when found, quote the words that show it). A found item is a reason for a band below 9; a band of "
             "9 means you looked and found none. Only real problems count - never anything listed as "
             "ACCEPTABLE AS WRITTEN."]
    for key, name in (("TR_or_TA", "Task Achievement" if task_type == "task1" else "Task Response"),
                      ("CC", "Coherence & Cohesion"), ("LR", "Lexical Resource"), ("GRA", "Grammar")):
        items = CHECKLISTS[key][task_type] if key == "TR_or_TA" else CHECKLISTS[key]
        lines.append(f"{name}: " + "; ".join(items) + ".")
    return "\n".join(lines)


def marking_guide() -> str:
    """The marking guide, its density thresholds from config/writing_marking.json (academic lead)."""
    d = marking_config.density()
    return f"""MARKING GUIDE - how to find each criterion's band:
1. List the strengths first, each with a short quote from the answer. Then the weaknesses, each \
with a short quote - real problems of THIS criterion only.
2. Where to start, from the evidence: if this criterion's weighted checked mistakes are at most \
{d['start_at_band8_max']:g} per 100 words (Grammar and Lexical Resource: the CHECKED MISTAKES; Task and \
Coherence: no more than a minor weakness) and the answer shows less common vocabulary or well-built \
complex structures, start at Band 8 and check whether it meets Band 9. Otherwise start at Band 7 and \
check whether it meets Band 8 or falls to 6. Weaknesses may be an empty list - never invent a weakness \
to fill the list.
3. Benefit of the doubt: when torn between two bands and the answer shows the key features of the \
higher one, give the higher band.
4. Score each criterion independently, on its own evidence. Never lower one criterion because \
another is weak.
5. Credit what works: less common vocabulary used well and complex structures used accurately count \
for the band, not only the absence of errors. Find the band that fits best - don't count mistakes.
6. Below 9 needs evidence: to give a criterion less than 9, its weaknesses must quote the specific \
text, or name the specific omission (e.g. "the 55-64 group is never described"). "Could be richer", \
"not fully developed", "needs more range" with no example are not reasons - with no such weakness, \
give 9. A quote of something listed as ACCEPTABLE AS WRITTEN is no reason either.
7. Judge, don't count (the academic lead, 2026-10-29): weigh errors by their density per 100 words, \
their effect on meaning and the range the writer attempts - an error that comes from attempting less \
common language weighs less than one in a simple structure. A slip is very small: Band 9 allows rare \
slips.
8. A wide range with a few slips is Band 8 or 9, never 7. Lexical Resource 6 or below only for a \
genuinely limited range or words that make the meaning unclear: clear, less common vocabulary whose \
meaning is always clear is at least 7.

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

ERROR DENSITY (Grammar and Lexical Resource) - weighted checked mistakes per 100 words, a slip (a typo, \
a missing full stop, a single article or plural slip) counting {d['slip_weight']:g}:
  Band 9: up to about {d['band9_max']:g} - only slips, no repeated pattern.
  Band 8: up to about {d['band8_max']:g} - non-systematic.
  Band 7: up to about {d['band7_max']:g} - in otherwise varied, mostly error-free sentences.
  Band 6: errors in many sentences, meaning clear.
  Band 5: frequent errors that make the reader work.
Density is half the picture: Band 7 and above also need range - less common vocabulary used well, a \
variety of complex structures.

{NOT_ERRORS}

{ONE_CRITERION}

LENGTH AND PARAGRAPHS (software applies these caps after you): Task 1 - 140 words or more: no \
penalty; 100-139 words: Task Achievement 6 at most; under 100: 5 at most. Task 2 - 240 words or more: \
no penalty; 200-239: Task Response 6 at most; under 200: 5 at most. A Task 2 essay with no paragraph \
breaks: Coherence & Cohesion 5 at most. Apart from these, length is never a reason to lower a band.

CHECKED MISTAKES: the answer comes with the list of its mistakes, found by one examiner and checked \
by a second, who removed everything that is not a real error. It is the complete list of real errors: \
judge accuracy (Grammar, Lexical Resource) by its weighted density per 100 words (the ERROR DENSITY \
table) - never count anything that is not on it - and credit the STRONG PHRASES listed with it (less \
common vocabulary and well-built structures used well).

THE NOTE for each criterion: 6-14 plain words for the student naming the strength or weakness that \
decided the band - never a language point under Task Achievement/Response; for Lexical Resource and \
Grammar, name what earns credit as well as what holds it back."""

TA_GUIDE = """TASK ACHIEVEMENT (Task 1) - judge the report against the visual's DATA SHEET and its KEY \
FEATURES, in this order:
Data accuracy is judged only by the DATA CHECK: a figure listed as NOT CHECKED, or near an UNCERTAIN \
figure, never lowers Task Achievement; a trend or a claim about the writer's own grouping is judged as \
the JUDGE RULES say.
1. Overview - the biggest factor. Is there one? Is it accurate? Does it state the main trends or \
differences (changes, extremes, contrasts) - not just static facts, or only the first and last steps \
of a process? (A Band 6.5 anchor is error-free and precise, but its overview only gives a process's \
start and end points.) Compare it with the sheet's expected overview AND its figures: the expected overview \
is software's summary, so an overview the sheet's figures support is accurate even when it picks another main \
feature or words it differently; it is inaccurate only when it contradicts the sheet's figures (an UNCERTAIN \
figure never makes it inaccurate). An overview that is limited - \
static, too general, only the start and end, or partly inaccurate - keeps Task Achievement at 6 at \
most, however good the rest is.
2. Key features: does the report cover the main ones? The sheet's key comparisons are a menu, not a \
checklist: a 150-200-word report selects the most significant, and leaving some out is not a weakness \
when its selection is sensible.
3. Comparisons: a visual of figures (a line graph, bar chart, pie chart, table, or a mix of them) needs \
them. Count the genuine comparisons (higher/lower than, overtook, the gap between, while X ..., Y ..., \
doubled, the same as); a report on such a visual that lists figures with no comparisons gets Task \
Achievement 5 at most. A process, cycle or map needs NO comparisons (its question doesn't ask for them): \
judge its overview, how fully it covers the stages, the order of the steps and a logical grouping - \
never lower it for "no comparisons" or "no figures".
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
5. priorities - 0 to 3, the most important first - a Task problem that holds the band down (under the \
word minimum, a whole part of the visual or the task left out, no overview) before any language pattern; \
a typing slip (spaces around a hyphen, a missing space after a full stop) is never a priority. Each is about ONE specific pattern (never a \
whole category like "grammar"). Give only as many as the answer really needs: a strong answer \
may have one or none, never a priority made up to reach a number. Each has a title (2-6 words), \
a rule (one short line, 15 words at most) and criteria (its tag or tags), and is one of two types:
   - type "fix" - a pattern of THIS answer's real mistakes: example_wrong (2-6 words copied exactly \
from the student's text, no "...", inside one of the listed mistakes) and example_right (that piece \
corrected), mistake_ids (2-5 ids from `mistakes` that show THIS pattern; each must have one of the \
priority's criteria; never all the mistakes of a category). Never build a priority around one \
trivial word, or around anything that is not a real mistake.
   - type "tip" - only where real mistakes are too few for a fix: what separates the answer from the \
next band in the descriptors; example_wrong = "", example_right = a short example of doing it \
well, mistake_ids = []. A tip never asks the writer to stop paraphrasing the chart's labels ("age at \
death" for "life expectancy" is good paraphrase) and never asks for what the report already does.
6. vocabulary - 10-12 words or phrases useful {vocab_scope}, about one band above the student's \
level: term, plain-English meaning, an English example sentence on this topic.
7. checklist - 5-6 short lines for "Before you send your next {piece}", built ONLY from this \
report's priorities and mistakes, written as forward-looking habits the student can tick ("My \
introduction paraphrases the question.") - never "I fixed ..."; ref = "Priority 01".."Priority 03" \
or "Mistake <id>" (only priorities you gave).

Plain text only. In why, rule and checklist you may put a key word in **double asterisks** (bold) \
or *single asterisks* (italics); no other markup.

{scores}"""


def _scoring_system(task_type: str, kind: str | None = None, answer: str = "") -> str:
    """The scorer's fixed instructions - the same for every answer of a task
    type and visual kind (prompt-cached): the guide, Task 1's TA guide and
    overview patterns, the anchors (with a Band 9 report of the same kind of
    visual, never the answer being scored)."""
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
        marking_guide(),
        extra,
        anchors_text(task_type, kind, answer),
        checklist_text(task_type),
        f"{check} For each criterion (TR_or_TA = {first}) answer its checklist, then give the strengths, the "
        "weaknesses, the whole band and the note - a note for a Band 9 names what works, never a criticism. The "
        "student is an ESL learner, but that never changes a band.",
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
- Only clear errors an IELTS examiner would penalise. When in doubt, leave it out. An answer at Band \
8-9 usually has 0-2 missed mistakes; an empty list is a normal, good result. Return only errors you are \
certain an examiner would penalise.
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


VERIFY_SYSTEM = f"""You are a senior IELTS Writing examiner double-checking a colleague's list of \
mistakes in a student's essay or report. Ask ONE question of every item: is this actually wrong in \
standard British or American written English? Then give its kind and the ONE criterion it belongs to.

kind (the academic lead's classes, 2026-10-29):
- error: wrong, or it makes the meaning unclear - "it should be noticed", "provided" used to mean \
"because", "this turn of events are", "people doesn't"; collocation errors, wrong-meaning or \
overstated words, verb-pattern errors, linking that doesn't fit the logic, a conclusion that \
contradicts the body;
- slip: wrong, but a one-off small mistake the writer clearly controls elsewhere in the text - a \
missing full stop, "Overtime" for "Over time", "feeling of loneliness" for "feelings", a single article \
or plural slip ("Czech Republic" without "the" while "the US" is right; "such society" where the text \
also writes "such a scenario"), a pronoun or "one" that doesn't agree with a plural several words back \
("teenagers want gadgets ... not because they want it", "fixing the previous one" for items), a word \
left out where the meaning stays obvious ("doctors $50,000 and other $25,000" for "other workers"; \
"not only of land but also water"), a particle or an idiom's word slightly off while the meaning is \
plain ("disposed into the oceans" for "disposed of in", "to make the case worse" for "matters" - the \
academic lead's rulings), a typo. The test is control: when the text gets the same point \
right elsewhere, it is a slip; only a pattern that is wrong every time it comes up is an error. A verb \
that doesn't agree with its subject ("people doesn't", "this turn of events are") is always an error;
- alternative: acceptable as written - another option is only more common, more idiomatic or more \
precise ("services are accessible at night" for "open", "eating regimes", "a positive trend", \
"inconceivable" where "unimaginable" would do, "the most inevitable consequence" - an absolute \
adjective intensified in its common, clear sense). A near-synonym in a familiar pattern that a reader \
understands at once is an alternative, not a miscollocation (the academic lead's rulings: "a positive \
tendency", "at certain parts of the day", "sleep regimes", "certain people's freedoms", "shops are \
available"): call a collocation an error only when an educated native reader would find it wrong, not \
when another word is merely more frequent. It is no mistake: it never counts;
- not_a_mistake: correct as written and the suggestion is no better; or the correction CHANGES WHAT \
THE STUDENT MEANT, or does NOT FIT BACK into the sentence (put it in place of the wrong part and read \
the sentence: no doubled or missing words); or you are not sure it is wrong - the student gets the \
benefit of the doubt.

Before calling anything an error or a slip, read the whole sentence: a verb may correctly continue an \
earlier verb phrase ("people start using it and spend hours" is right), a negative or future form may \
be correct as it stands. Correct, so never an error or a slip: a plural for several items of a kind \
("declines in outputs" of several products); a perfect infinitive after seem / appear ("seemed to have \
thrived"); an idiom used in its usual sense ("plant the seed of"); "develop their economy" (one economy \
per country); a fixed expression that keeps its present form in a past account ("the former had an edge \
when it comes to other workers", "as far as pay is concerned") - no tense shift; a plural verb after \
"X along with / coupled with / together with Y" (the academic lead accepted "The automotive industry along \
with the smartphone market are both a testament"). When one flagged stretch holds a single real error, only that error counts ("the football" \
-> "football" - the correct words around it are not wrong). These are never an error or a slip: a style preference or valid variant (a \
demonstrative for an article, a near-synonym, "ranging from X to Y", a plainer word, an optional comma, \
British vs American usage); the QUESTION'S OWN WORDS; a PROPER NAME as its owner writes it ("Malala \
Fund"); an expression whose CONNOTATION matches the writer's intent ("fraught with" before harmful \
things).
{NOT_ERRORS}

criterion - by the real cause, one only: LR = word choice, collocation, word form, spelling, \
register; GRA = grammar and punctuation; CC = linking, referencing, paragraphing, logic; TR = \
content, task coverage, the overview, data. A word-choice problem is LR even when it was listed \
under TR ("were quite passive" -> LR).

A DATA item (a figure, ranking or trend in a Task 1 report) is judged ONLY by the DATA SHEET given \
with the text and its DATA CHECK (a figure the check found on the sheet is correct): not_a_mistake when \
the student's version agrees with the sheet - the printed figure (rounding with "about" is fine), any \
figure within a scale reading's ±range, an UNCERTAIN figure or one the sheet can't read, a ranking or \
trend the sheet shows - or when the "correction" itself disagrees with the sheet; error when the \
verified figures clearly contradict the student. A year printed on the axis, or between the first and \
last labelled years, is never a TR mistake. A trend ("fell", "stayed stable") is wrong only when the \
change between its two figures is larger than both their ±ranges together and goes the other way. A \
claim about the writer's own grouping ("in the final stage" of the writer's own three stages) is never \
a data error: only a wrong step order or a missing step is. A data correction must agree with the sheet: when an item \
carries a CORRECTION CHECK (its correction's figures are not on the sheet), either give fixed_correct - \
the correction rewritten with the sheet's figures for the same series - or not_a_mistake. With no DATA \
SHEET and no visual, a mistake about a figure is not_a_mistake (nothing to check it against).

VOCABULARY (when listed): the words the report teaches the student, each with its meaning and an \
example. For each give its index; meaning = the standard dictionary meaning in plain words only when \
the given one is wrong, incomplete in a misleading way or backwards ("built-in redundancy" is extra \
parts added so that a system keeps working when one fails - never "parts designed to fail"), else \
null; example = a rewritten example only when it misuses the term (a "slump" is a sudden, sharp fall - \
never a steady decline) or carries a CHECK (it disagrees with the DATA SHEET - rewrite it with the \
sheet's verified figures and true comparisons), else null.

Judge each item on its own; give a short reason. Return a verdict for every id."""


async def _verify_call(source_text: str, items: list[tuple],
                       vocabulary: list[tuple[int, str, str, str, str]] | None = None) -> tuple[dict, dict]:
    """`items`: (id, where, wrong, correct, why, criterion, correction check or "");
    `vocabulary`: (index, term, meaning, example, check or ""). Returns
    ({id: (kind, criterion, reason, fixed_correct)} - items without a
    verdict are kept as they are -, {index: (meaning or None, example or None)})."""
    if not items and not vocabulary:
        return {}, {}
    listed = "\n".join(
        f"{i}. [{crit}] «{wrong}» -> «{correct}» ({why})" + (f" CORRECTION CHECK: {note}" if note else "")
        for i, _, wrong, correct, why, crit, note in items) or "(none)"
    text = f"{source_text}\n\nMISTAKES TO CHECK:\n{listed}"
    if vocabulary:
        text += "\n\nVOCABULARY TO CHECK:\n" + "\n".join(
            f"{i}. {term} - {meaning} - e.g. {example}" + (f" CHECK: {note}" if note else "")
            for i, term, meaning, example, note in vocabulary)
    messages = [
        {"role": "system", "content": VERIFY_SYSTEM},
        {"role": "user", "content": text},
    ]
    response = await _parse("verify", VERIFY_MAX_TOKENS, messages, WritingVerdicts,
                            effort=settings.openai_verify_reasoning_effort)
    parsed = response.choices[0].message.parsed
    if not parsed:
        return {}, {}
    verdicts = {v.id: (v.kind, v.criterion, v.reason, (v.fixed_correct or "").strip() or None)
                for v in parsed.verdicts}
    words = {v.index: ((v.meaning or "").strip() or None, (v.example or "").strip() or None)
             for v in parsed.vocabulary}
    return verdicts, words


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


async def _parse(purpose: str, max_tokens: int, messages: list[dict], response_format, effort: str | None = None):
    """A gpt-5 call at the grading reasoning effort (or `effort`) with an
    output-token limit. A call cut off at the limit raises
    openai.LengthFinishReasonError; its cost is logged all the same."""
    try:
        response = await parse_flex(  # the Flex tier, the default as a fallback (openai_client)
            model=settings.openai_model_grading,
            messages=messages,
            response_format=response_format,
            max_completion_tokens=max_tokens,
            # No `temperature` override - see services/ai/ocr.py's comment.
            # Sent as extra_body: openai 1.57 has no reasoning_effort argument yet.
            extra_body={"reasoning_effort": effort or settings.openai_grading_reasoning_effort},
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


def cap_bands(task_type: str, answer_text: str, bands: dict[str, float], notes: dict[str, str],
              figures_visual: bool = True):
    """The length and paragraphing policy (marking guide B2, chosen to match
    the books - Volume 2 gives Band 8.0 to a 148-word report): returns
    (bands, notes, the caps applied, report notices). The "no comparisons"
    cap is for visuals of figures only (`figures_visual`): #58, a life
    cycle, had Task Achievement 5 for "figures listed with no comparisons"
    and not one figure in it."""
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
        if figures_visual and count_comparisons(answer_text) == 0:
            cap("TR_or_TA", 5, "Figures are listed with no comparisons, so Task Achievement is 5 at most.")
    else:
        if words < 200:
            cap("TR_or_TA", 5, f"Only {words} words: under 200 words keeps Task Response at 5 at most.")
        elif words < 240:
            cap("TR_or_TA", 6, f"Only {words} words: under 240 words keeps Task Response at 6 at most.")
        if paragraphs <= 1:
            cap("CC", 5, "No paragraph breaks: one block of text keeps Coherence at 5 at most.")
    return bands, notes, capped, notices


_OMISSION = re.compile(r"\b(omit\w*|omission|missing|leaves? out|left out|not (?:mention|cover|describ|includ)\w*|"
                       r"(?:doesn't|does not|never) (?:mention|cover|describe|include|state|give)\w*|no mention|"
                       r"absen\w*|lacks?|lacking|no overview|ignor\w*|skips?)\b", re.IGNORECASE)
_QUOTED = re.compile(r"[\"“«']([^\"”»']{2,120})[\"”»']")


def evidenced(weaknesses: list[str], answer_text: str, acceptable: list[str] = ()) -> bool:
    """Does a criterion's list of weaknesses quote the answer or name an
    omission? An 8 without that is a 9 (the academic lead, 2026-10-27:
    "could be richer" and "not fully developed" lowered Band 9 reports). A
    quote of something the filter found acceptable as written (`acceptable`)
    is no reason."""
    low = canon(answer_text).lower()
    fine = [canon(a).lower().strip(" .,") for a in acceptable if a.strip()]
    for w in weaknesses:
        if _OMISSION.search(w):
            return True
        for quoted in _QUOTED.findall(w):
            # a quote shortened with "..." counts by its pieces ("In 2005... In 2011... By 2015,")
            for q in re.split(r"\s*(?:\.\.\.|…)\s*", canon(quoted).lower()):
                q = q.strip(" .,")
                if len(q) >= 2 and q in low and not any(q in a or a in q for a in fine):
                    return True
    return False


async def _score_once(task_type, prompt_text, answer_text, prompt_images, sheet: str = "",
                      evidence: str = "", kind: str | None = None, acceptable: list[str] = ()) -> dict:
    """One scoring run: {criterion: whole band, ..., "_notes": {criterion: note}}.
    `evidence`: the checked mistakes and strong phrases (mistake_evidence);
    `kind`: the visual's kind, for its Band 9 anchor; `acceptable`: what the
    filter found acceptable as written - quoting it is no reason for a band."""
    facts = f"COUNTED BY SOFTWARE: {count_words(answer_text)} words, {paragraph_count(answer_text)} paragraph(s)."
    text = f"QUESTION PROMPT:\n{prompt_text}\n\nANSWER:\n{answer_text}\n\n{facts}"
    if evidence:
        text += f"\n\n{evidence}"
    messages = [
        {"role": "system", "content": _scoring_system(task_type, kind, answer_text)},
        {"role": "user", "content": _with_images(text, prompt_images, task_type == "task1", _judged(sheet))},
    ]
    response = await _parse("score", SCORE_MAX_TOKENS, messages, WritingScoring,
                            effort=settings.openai_scoring_reasoning_effort)
    scores = response.choices[0].message.parsed
    if scores is None:
        raise RuntimeError("Scoring model returned no parsable result")
    out: dict = {k: whole_band(getattr(scores, k).band) for k in SCORE_KEYS}
    for k in SCORE_KEYS:
        judged = getattr(scores, k)
        found = [f'"{c.quote}"' for c in judged.checklist if c.found and c.quote.strip()]
        looked = len(judged.checklist) >= len(CHECKLISTS[k][task_type] if k == "TR_or_TA" else CHECKLISTS[k])
        if out[k] == 9 and found and evidenced(found, answer_text, acceptable):
            logger.info("Score %s 9 -> 8: its checklist found %s", k, found)
            out[k] = 8.0
        elif out[k] == 8 and looked and not evidenced(judged.weaknesses + found, answer_text, acceptable):
            # only an 8 is lifted: a 6 or 7 whose reason came back unquoted stays (2026-10-29: #60's and
            # #62's Coherence 6-7 -> 9 with "linking is basic and repetitive" as their reason)
            logger.info("Score %s %g -> 9: no quoted weakness or named omission, checklist clear (%s)", k, out[k],
                        judged.weaknesses)
            out[k] = 9.0
    out["_notes"] = {k: getattr(scores, k).note.strip() for k in SCORE_KEYS}
    return out


_EVIDENCE_NAMES = (("GRA", "Grammar (GRA)"), ("LR", "Vocabulary (LR)"), ("CC", "Coherence (CC)"),
                   ("TR", "Task (TR)"))


def _tokens(text: str) -> list[str]:
    return re.findall(r"[\w'$%]+", canon(text).lower())


def _edits(m) -> list[tuple]:
    """A correction's changes: (words taken out, words put in, the word after them)."""
    a, b = _tokens(m.wrong), _tokens(m.correct)
    return [(tuple(a[i1:i2]), tuple(b[j1:j2]), a[i2] if i2 < len(a) else "")
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
            if tag != "equal"]


def _change(m) -> tuple:
    """What a correction changes first ("Czech Republic" -> "the Czech
    Republic" and "Italy, Czech Republic" -> "Italy, the Czech Republic"
    make one change)."""
    return (_edits(m) or [((), (), "")])[0]


def lead_accepted(wrong: str) -> str | None:
    """The wording the academic lead accepted (config "accepted") that a
    flag is about: it is in the flagged words, or the flagged words are a
    part of it ("are both a testament to this")."""
    low = canon(wrong).lower().strip(" .,")
    for phrase in marking_config.accepted_phrases():
        p = canon(phrase).lower().strip(" .,")
        if p and (p in low or (len(low) >= 12 and low in p)):
            return phrase
    return None


_ARTICLES = {"a", "an", "the"}
_NUMBER = {("it", "them"), ("one", "ones"), ("this", "these"), ("that", "those"), ("other", "others"),
           ("its", "their"), ("another", "other")}
_AGREEMENT = {("doesn't", "don't"), ("does", "do"), ("is", "are"), ("was", "were"), ("has", "have"),
              ("isn't", "aren't"), ("wasn't", "weren't"), ("hasn't", "haven't")}
ARTICLE_SLIPS_MAX = 2  # a third article left out or wrong: the writer doesn't control articles


def small_kind(m) -> str | None:
    """'punctuation', 'article' or 'number' when a correction changes only
    that - the academic lead's slips ("a missing full stop", "a single
    article or plural slip"; 2026-10-29, #66's "such society", "the previous
    one", "they want it" were kept as errors). A verb's agreement ("people
    doesn't", "this turn of events are") is none of them: an error."""
    edits = _edits(m)
    if not edits:
        return "punctuation" if canon(m.wrong) != canon(m.correct) else None
    if len(edits) != 1:
        return None
    out, put, after = edits[0]
    if len(out) <= 1 and len(put) <= 1 and set(out + put) <= _ARTICLES and set(out + put) != {"a", "an"}:
        return "article"  # "an useful" -> "a useful" is the article's form, not its use: an error
    if len(out) == len(put) == 1 and ((out[0], put[0]) in _NUMBER or (put[0], out[0]) in _NUMBER):
        return "number"
    if len(out) == len(put) == 1 and after == "of" and (put[0] in (out[0] + "s", out[0] + "es")
                                                       or out[0] in (put[0] + "s", put[0] + "es")):
        return "number"  # "feeling of worthlessness" -> "feelings of": a noun's number
    if len(out) == len(put) == 1 and ((out[0], put[0]) in _AGREEMENT or (put[0], out[0]) in _AGREEMENT):
        return "agreement"
    return None


def small_slips(checked: list) -> list[str]:
    """Marks as slips the checked errors whose correction is only a full
    stop, an article or a pronoun's number - unless articles go wrong more
    than ARTICLE_SLIPS_MAX times (then the writer doesn't control them) -
    and as an error a verb's agreement the filter called a slip. Never a
    Task (data) mistake. Returns what it changed, for the log."""
    kinds = [small_kind(m) if m.criterion != "TR" else None for m in checked]
    articles = sum(k == "article" for k in kinds)
    changed = []
    for m, k in zip(checked, kinds):
        if k == "agreement":  # "people doesn't": an error, whatever the filter said (T9, 2026-10-29)
            if m.severity == "slip":
                m.severity = "error"
                changed.append(f"{m.wrong!r} (a verb's agreement: an error)")
        elif k and m.severity == "error" and not (k == "article" and articles > ARTICLE_SLIPS_MAX):
            m.severity = "slip"
            changed.append(f"{m.wrong!r} ({k})")
    return changed


def distinct_slips(slips: list) -> list[tuple]:
    """(slip, how many times) - the same slip made again counts once (the
    academic lead, 2026-10-29: "the Czech Republic" left without "the" twice
    and "other" for "others" are 2 slips, not 3)."""
    seen: dict[tuple, list] = {}
    for m in slips:
        key = _change(m)
        if key == ((), (), ""):
            key = (canon(m.wrong).lower(), canon(m.correct).lower())
        seen.setdefault(key, [m, 0])[1] += 1
    return [(m, n) for m, n in seen.values()]


def mistake_evidence(answer_text: str, mistakes: list, strong: list[str], alternatives: list[dict] = ()) -> str:
    """The checked mistakes (each with .wrong, .correct, .criterion and
    .severity - "slip" or "error"), what was found acceptable as written and
    the strong phrases, as the scoring runs get them: per criterion the
    errors, the slips and the weighted density per 100 words, a slip
    counting slip_weight (config/writing_marking.json)."""
    words = max(count_words(answer_text), 1)
    weight = marking_config.density()["slip_weight"]
    lines = ["CHECKED MISTAKES (found by one examiner, checked by a second against standard British and American "
             "English - errors and slips only; this is the complete list). The answer has "
             f"{words} words; a slip counts {weight:g}."]
    for key, name in _EVIDENCE_NAMES:
        mine = [m for m in mistakes if m.criterion == key]
        if not mine:
            lines.append(f"- {name}: none.")
            continue
        slips = distinct_slips([m for m in mine if getattr(m, "severity", "error") == "slip"])
        errors = [m for m in mine if getattr(m, "severity", "error") != "slip"]
        weighted = (len(errors) + len(slips) * weight) * 100 / words
        listed = "; ".join([f"«{m.wrong}» -> «{m.correct}»" for m in errors[:12]]
                           + [f"«{m.wrong}» -> «{m.correct}» (slip{', ' + str(n) + ' times' if n > 1 else ''})"
                              for m, n in slips[:6]])
        lines.append(f"- {name}: {len(errors)} errors, {len(slips)} slips = {weighted:.1f} weighted per 100 words: "
                     f"{listed}")
    if alternatives:
        lines.append("ACCEPTABLE AS WRITTEN (another option is only more common - these are NOT mistakes: never count "
                     "them, never quote them as a weakness): " + "; ".join(f"«{a['wrong']}»" for a in alternatives[:10]))
    if strong:
        lines.append("STRONG PHRASES the examiner marked (credit them): " + "; ".join(f"«{x}»" for x in strong[:6]))
    return "\n".join(lines)


def floors(task_type: str, answer_text: str, bands: dict[str, float], checked: list | None,
           strong: list[str] | None) -> tuple[dict[str, float], list[str]]:
    """The least Lexical Resource and Grammar bands for what the answer shows
    (config/writing_marking.json "floors", the academic lead 2026-10-29):
    a wide range and only slips on a criterion - at least 8 ("wide range + a
    few slips = 8 or 9, never 7"); a wide range, no word that makes the
    meaning unclear and lexical errors under the Band 7 density - Lexical
    Resource at least 7. Never Task Achievement/Response or Coherence, never
    an answer under 150 (Task 1) / 250 (Task 2) words."""
    if checked is None or count_words(answer_text) < TASK_MIN_WORDS.get(task_type, 0):
        return bands, []
    f = marking_config.floors()
    if len(strong or []) < f["wide_range_phrases"]:
        return bands, []
    out, raised = dict(bands), []
    words = max(count_words(answer_text), 1)
    weight = marking_config.density()["slip_weight"]
    for key in ("LR", "GRA"):
        mine = [m for m in checked if m.criterion == key]
        errors = [m for m in mine if getattr(m, "severity", "error") != "slip"]
        density = (len(errors) + len(distinct_slips([m for m in mine if m not in errors])) * weight) * 100 / words
        least, why = (f["slips_only_min"], "only slips") if not errors else (0, "")
        clear = not any(getattr(m, "unclear", False) for m in mine)
        if clear and density <= marking_config.density()["band8_max"] and f.get("band8_density_min", 0) > least:
            least, why = f["band8_density_min"], f"{density:.2f} per 100 words, within Band 8"
        if key == "LR" and clear and density <= marking_config.density()["band7_max"] \
                and f["clear_vocabulary_lr_min"] > least:
            least, why = f["clear_vocabulary_lr_min"], "clear, wide vocabulary"
        if out[key] < least:
            raised.append(f"{key} {out[key]:g} -> {least:g} ({why})")
            out[key] = float(least)
    return out, raised


FLOOR_NOTES = {"LR": "Wide, precise vocabulary; only minor slips.", "GRA": "Wide range of structures; only minor slips."}
NINE_NOTES = {"TR_or_TA": {"task1": "Clear overview; every key feature covered precisely.",
                           "task2": "Every part answered; ideas fully developed and supported."},
              "CC": "Seamless organisation; linking that never draws attention.",
              "LR": "Wide, precise and natural vocabulary.", "GRA": "Wide range of structures, used accurately."}
_CRITICISM = re.compile(r"\b(?:could|should|but|though|although|however|slightly|minor|lapses?|lacks?|limited|"
                        r"occasional|sharper|clearer|more|less|a few|some|weak\w*|errors?|slips?|missing)\b",
                        re.IGNORECASE)


async def decide_scores(
    task_type: str,
    prompt_text: str,
    answer_text: str,
    prompt_images: list[tuple[bytes, str]] | None = None,
    sheet: str = "",
    evidence: str = "",
    visual: dict | None = None,
    checked: list | None = None,
    strong: list[str] | None = None,
    acceptable: list[str] = (),
) -> Scores:
    """The report's criterion bands: the median of SCORE_RUNS scoring runs,
    then the floors (`checked` mistakes and `strong` phrases: floors()) and
    the caps; each band's note comes from a run that gave that band.
    `evidence`: the checked mistakes (grade_writing passes them); `visual`:
    the sheet as data - its kind picks the Band 9 anchor and decides whether
    comparisons are needed (the question's wording when there is none);
    `acceptable`: what the filter found acceptable as written."""
    kind = visual_kind(prompt_text, visual) if task_type == "task1" else None
    medians, runs = await median_of_runs(
        lambda: _score_once(task_type, prompt_text, answer_text, prompt_images, sheet, evidence, kind, acceptable),
        SCORE_KEYS, label=f"writing {task_type}",
    )
    medians, raised = floors(task_type, answer_text, medians, checked, strong)
    if raised:
        logger.info("Band floors (writing %s): %s", task_type, "; ".join(raised))
    notes = {}
    for k in SCORE_KEYS:
        notes[k] = next((r["_notes"][k] for r in runs if whole_band(r[k]) == medians[k] and r["_notes"].get(k)),
                        FLOOR_NOTES.get(k, ""))
        if medians[k] >= 9 and _CRITICISM.search(notes[k]):  # "grouping could be sharper" under a 9
            notes[k] = NINE_NOTES[k][task_type] if k == "TR_or_TA" else NINE_NOTES[k]
    bands, notes, capped, notices = cap_bands(task_type, answer_text, medians, notes,
                                              data_visual(prompt_text, visual) if task_type == "task1" else True)
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
    visual: dict | None = None,
) -> WritingReport:
    """`prompt_images` - (bytes, mime) pairs of the Task 1 chart/diagram, so
    the grader can check the student's figures and overview against the
    actual visual instead of just its text description; `sheet` - its data
    sheet (services/ai/visual_reading.py) with the report's data check, by
    which every call judges the figures; `visual` - that sheet as data
    (VisualSheet.model_dump()), by which code checks the data corrections.
    `on_feedback(bands, overall, feedback)` is called as soon as the bands
    are decided. `notices`: lines the report shows the student (e.g. the
    chart wasn't checked).

    Mistakes first (the owner's rule, 2026-10-22: #38's Grammar still came
    out 6 with the bands decided first): the feedback lists the mistakes, the
    filter checks them, and only then do the scoring runs decide the bands -
    from the checked list, so accuracy is judged by the real errors and
    their density, never by style preferences.

    The report always shows the student's original text; the model's marks
    are located in it (services/ai/essay_marking.py), never an altered copy -
    after pasted-text cleaning (clean_pasted: lines broken mid-sentence
    joined, page headers/footers left out), which the marks then match."""
    answer_text, footers = clean_pasted(answer_text)
    if footers and PASTE_NOTICE not in (notices or []):
        notices = (notices or []) + [PASTE_NOTICE]
    unchecked = set(unchecked_figures(answer_text, visual, prompt_text)) if visual else set()
    copied = [answer_text[s:e] for s, e in find_copied_spans(answer_text, prompt_text, copy_min_words(task_type))]
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

    def question_word(m) -> bool:
        """The question's own words are never an error - by code, before the
        filter: #44's "foods" was flagged though the question says "a wide
        variety of foods"."""
        return uses_question_words(m.wrong, m.correct, prompt_text)

    def correction_note(m) -> str:
        """A data correction whose figures are not on the sheet - #40 renamed
        a series but kept the other series' figures, #48 "corrected" a third
        into figures the chart doesn't show."""
        if m.criterion != "TR" or not visual:
            return ""
        misses = [f["text"] for f in figures(m.correct) if match_figure(f, visual, prompt_text) == "miss"]
        return f"the correction's figures {', '.join(misses)} are not on the DATA SHEET" if misses else ""

    async def verify(items, vocabulary=None) -> tuple[dict, dict]:
        try:
            return await _verify_call(source, items, vocabulary)
        except Exception:
            logger.exception("Verification pass failed - keeping those mistakes")
            return {}, {}

    async def find_missed() -> list[MissedMistake]:
        try:
            return await _missed_errors_call(task_type, prompt_text, answer_text, prompt_images, feedback.mistakes,
                                             sheet)
        except Exception:
            logger.exception("Missed-errors pass failed - the report goes out with the first pass only")
            return []

    alternatives: list[dict] = []  # acceptable as written: the report's "Upgrade ideas", never a mistake

    def settle(item, verdict, label) -> tuple[bool, str, str, str]:
        """(keep, criterion, severity, correct) from the filter's verdict - an
        error or a slip is kept (a slip weighs less), an alternative goes to
        the upgrade ideas, anything else goes; a data correction off the sheet
        keeps only its sheet-checked rewrite."""
        kind, crit, reason, fixed = verdict
        ruled = lead_accepted(item.wrong) if kind in ("error", "slip") and crit != "TR" else None
        if ruled:
            logger.info("%s is an alternative: the academic lead accepted %r (the filter said %s)", label, ruled, kind)
            kind, reason = "alternative", f"Acceptable as written; «{item.correct}» is only a more common option."
        keep, correct = kind in ("error", "slip"), item.correct
        if kind == "alternative" and crit != "TR":
            alternatives.append({"wrong": item.wrong, "correct": item.correct, "why": reason, "criterion": crit})
        if not keep:
            logger.info("Verification set aside %s (%s): %s", label, kind, reason)
        elif correction_note(item):
            if fixed and not correction_note(item.model_copy(update={"correct": fixed})):
                correct = fixed
            else:
                keep = False
                logger.info("Dropped %s: its correction disagrees with the data sheet", label)
        return keep, crit, "slip" if kind == "slip" else "error", correct

    # the vocabulary the report teaches: its meanings, and (Task 1) its examples against the sheet
    def example_check(example: str) -> str:
        from services.ai.sample_bank import example_problems  # the model answer's checks; that module is heavier

        return "; ".join(example_problems(example, prompt_text, visual)) if visual else ""

    words = [(i, v.term, v.meaning, v.example, example_check(v.example)) for i, v in enumerate(feedback.vocabulary)]

    def data_protected(m) -> bool:
        """A Task mistake about a correct figure, one the sheet can't check,
        or a category with an uncertain figure - never kept (the academic
        lead, 2026-10-27)."""
        return m.criterion == "TR" and protected_mistake(m.wrong, m.correct, m.why, visual, prompt_text, unchecked)

    auto = {m.id for m in feedback.mistakes if question_word(m) or data_protected(m)}
    for m in feedback.mistakes:
        if m.id in auto:
            logger.info("Dropped mistake %s by code: the question's own words, or a figure the sheet confirms or "
                        "can't check (%r)", m.id, m.wrong)
    (verdicts, fixes), missed = await asyncio.gather(
        verify([(m.id, "essay", m.wrong, m.correct, m.why, m.criterion, correction_note(m))
                for m in feedback.mistakes if checkable(m) and m.id not in auto], words),
        find_missed(),
    )
    missed = [x for x in missed if not question_word(x) and not data_protected(x)]
    missed_verdicts, _ = await verify(
        [(k, "essay", x.wrong, x.correct, x.why, x.criterion, correction_note(x))
         for k, x in enumerate(missed) if checkable(x)])
    feedback = _vocabulary_fixed(feedback, fixes, example_check)

    dropped, retag, severity, rewritten = set(auto), {}, {}, {}
    for m in feedback.mistakes:
        if m.id in auto:
            continue
        verdict = verdicts.get(m.id)
        if verdict is None:  # copied text, unclear meaning, or the filter failed: kept as listed
            if correction_note(m):
                dropped.add(m.id)
            continue
        keep, crit, sev, correct = settle(m, verdict, f"mistake {m.id}")
        if not keep:
            dropped.add(m.id)
            continue
        retag[m.id], severity[m.id] = crit, sev
        if correct != m.correct:
            rewritten[m.id] = correct
    if rewritten:
        # the sheet-checked corrections replace the model's, and their inline fixes go
        feedback = feedback.model_copy(update={
            "mistakes": [m.model_copy(update={"correct": rewritten[m.id]}) if m.id in rewritten else m
                         for m in feedback.mistakes],
            "essay": [[seg.model_copy(update={"fix": None}) if seg.mistake_id in rewritten else seg
                       for seg in paragraph] for paragraph in feedback.essay],
        })
    kept_missed, missed_severity = [], []
    for k, x in enumerate(missed):
        verdict = missed_verdicts.get(k)
        if verdict is None:
            if not correction_note(x):
                kept_missed.append(x)
                missed_severity.append("error")
            continue
        keep, crit, sev, correct = settle(x, verdict, f"missed-pass item {k}")
        if keep:
            kept_missed.append(x.model_copy(update={"criterion": crit, "correct": correct}))
            missed_severity.append(sev)

    # The bands, from the checked mistakes (the report's own list: what the
    # filter kept, under the criterion it belongs to, slips weighted less)
    # and the strong phrases.
    checked = [SimpleNamespace(wrong=m.wrong, correct=m.correct, criterion=retag.get(m.id, m.criterion),
                               severity=severity.get(m.id, "error"), unclear=m.unclear)
               for m in feedback.mistakes if m.id not in dropped]
    checked += [SimpleNamespace(wrong=x.wrong, correct=x.correct, criterion=x.criterion, severity=sev,
                                unclear=x.unclear)
                for x, sev in zip(kept_missed, missed_severity)]
    for note in small_slips(checked):
        logger.info("Severity by its correction: %s", note)
    strong = [seg.text.strip() for paragraph in feedback.essay for seg in paragraph if seg.type == "good"]
    scores = await decide_scores(task_type, prompt_text, answer_text, prompt_images, sheet,
                                 mistake_evidence(answer_text, checked, strong, alternatives), visual,
                                 checked=checked, strong=strong, acceptable=[a["wrong"] for a in alternatives])
    # Priorities built on mistakes the filter dropped go now, so the model
    # answer and the report teach the same list (the last prompt's B2).
    feedback, priority_notes = drop_priorities(
        feedback, dropped, [m.wrong for m in feedback.mistakes if m.id not in dropped] + [x.wrong for x in kept_missed])
    if task_type == "task1" and visual:
        feedback, tip_notes = true_tips(feedback, example_check)
        priority_notes += tip_notes
    for note in priority_notes:
        logger.info("Feedback: %s", note)
    if on_feedback is not None:
        try:
            on_feedback(scores.bands, scores.overall, feedback)
        except Exception:
            logger.exception("on_feedback failed")

    report = build_report(task_type, prompt_text, answer_text, feedback, kept_missed, bands=scores.bands,
                          dropped=dropped, retag=retag, criterion_notes=scores.notes,
                          notices=(notices or []) + scores.notices, upgrades=alternatives)
    if report.notes:
        logger.info("Report validation: %s", "; ".join(report.notes))
    return report


def true_tips(feedback: WritingFeedback, check) -> tuple[WritingFeedback, list[str]]:
    """Task 1: a next-band tip whose rule or example makes a data claim the
    sheet doesn't bear out goes (#105: "Germany was about a half" - the
    student's one-third was right), with its checklist lines; the rest are
    numbered again."""
    bad = {i for i, p in enumerate(feedback.priorities[:3], 1)
           if p.type == "tip" and check(f"{p.rule} {p.example_right}")}
    if not bad:
        return feedback, []
    notes = [f"tip {i} dropped - its data claim disagrees with the chart: {feedback.priorities[i - 1].title!r}"
             for i in sorted(bad)]
    return without_priorities(feedback, bad), notes


def _vocabulary_fixed(feedback: WritingFeedback, fixes: dict, example_check) -> WritingFeedback:
    """The vocabulary with the filter's corrections: a wrong meaning set right
    (#66's "built-in redundancy" defined backwards); an example rewritten when
    it misused the term or disagreed with the chart - and left out when a
    chart example still disagrees after that."""
    if not fixes and not any(example_check(v.example) for v in feedback.vocabulary):
        return feedback
    kept = []
    for i, v in enumerate(feedback.vocabulary):
        meaning, example = fixes.get(i, (None, None))
        item = v.model_copy(update={"meaning": meaning or v.meaning, "example": example or v.example})
        if meaning or example:
            logger.info("Vocabulary %r set right: %s", v.term, {"meaning": meaning, "example": example})
        if example_check(item.example):
            logger.info("Vocabulary %r left out: its example still disagrees with the chart", v.term)
            continue
        kept.append(item)
    return feedback.model_copy(update={"vocabulary": kept})


_WORD = re.compile(r"[a-z]+(?:'[a-z]+)?")


def uses_question_words(wrong: str, correct: str, question: str) -> bool:
    """Does a "correction" only take out words the question itself uses
    ("transportation of foods" -> "of food" when the question says "a wide
    variety of foods")? Content words only: an article or a preposition the
    question happens to contain proves nothing."""
    q = set(_WORD.findall(question.lower()))
    fixed = set(_WORD.findall(correct.lower()))
    removed = [w for w in _WORD.findall(wrong.lower()) if w not in fixed and len(w) >= 4]
    return bool(removed) and all(w in q for w in removed)
