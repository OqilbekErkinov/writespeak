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
from collections import Counter
import base64
import difflib
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from types import SimpleNamespace

import openai
from pydantic import BaseModel, ConfigDict, Field, create_model

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
    locate,
    clean_pasted,
    count_words,
    drop_priorities,
    copy_min_words,
    find_copied_spans,
    rubric_spans,
    without_priorities,
    paragraph_count,
)
from services.ai.openai_client import parse_flex
from services.ai.schemas import (CheckFinding, CriterionJudgement, Mistake, MissedMistake, MissedMistakes,
                                 WritingFeedback, WritingScoring, WritingVerdicts)
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
from services.ai.mistake_rules import (  # noqa: F401 - the tests and scripts reach them here
    distinct_slips,
    final_kind,
    lead_accepted,
    only_adds,
    praised,
    scan_artefact,
    SCANNED_SOURCES,
    small_kind,
    small_slips,
)
from services.ai import result_cache
from services.ai.writing_anchors import anchors_text, patterns_text

logger = logging.getLogger(__name__)

# Output-token limits (reasoning included) - about twice what a long, weak
# essay needs at low effort, so only a runaway answer is cut off.
FEEDBACK_MAX_TOKENS = 20000
MISSED_MAX_TOKENS = 8000
# 8000 ran out on a 306-word script (2026-11-01): the whole check was then scored on the unfiltered marks, too strictly
VERIFY_MAX_TOKENS = 14000
SCORE_MAX_TOKENS = 8000

# Condensed, paraphrased summary of the public IELTS Writing Band Descriptors
# (Task Achievement/Response, Coherence & Cohesion, Lexical Resource,
# Grammatical Range & Accuracy) — grounding context for the grading model,
# not a verbatim reproduction of the official IELTS document.
# The official IELTS Writing band descriptors (public version, updated May 2023), close to word for word -
# the user sent them on 2026-11-09 to mark "like a real examiner". Until then the prompts carried a loose
# summary of an older version (no "must fully fit", no limiting features, "error-free" at Band 9). The
# Academic lines only; [LIMIT] is the official bold: a negative feature that limits the rating.
_TA_T1 = """TASK ACHIEVEMENT (Task 1)
9: All the requirements of the task are fully and appropriately satisfied. There may be extremely rare lapses in content.
8: The response covers all the requirements of the task appropriately, relevantly and sufficiently. Key features are \
skilfully selected, and clearly presented, highlighted and illustrated. There may be occasional omissions or lapses in content.
7: The response covers the requirements of the task. The content is relevant and accurate - there may be a few \
omissions or lapses. The format is appropriate. Key features which are selected are covered and clearly highlighted \
but could be more fully or more appropriately illustrated or extended. It presents a clear overview, the data are \
appropriately categorised, and main trends or differences are identified.
6: The response focuses on the requirements of the task and an appropriate format is used. Key features which are \
selected are covered and adequately highlighted. A relevant overview is attempted. Information is appropriately \
selected and supported using figures/data. Some irrelevant, inappropriate or inaccurate information may occur in areas \
of detail or when illustrating or extending the main points. Some details may be missing (or excessive) and further \
extension or illustration may be needed.
5: The response generally addresses the requirements of the task. The format may be inappropriate in places. Key \
features which are selected are not adequately covered. The recounting of detail is mainly mechanical. [LIMIT] There \
may be no data to support the description. There may be a tendency to focus on details (without referring to the \
bigger picture). The inclusion of irrelevant, inappropriate or inaccurate material in key areas detracts from the task \
achievement. There is limited detail when extending and illustrating the main points.
4: The response is an attempt to address the task. Few key features have been selected. [LIMIT] The format may be \
inappropriate. Key features which are presented may be irrelevant, repetitive, inaccurate or inappropriate.
3: The response does not address the requirements of the task (possibly because of misunderstanding of the \
data/diagram). Key features which are presented may be largely irrelevant. Limited information is presented, and this \
may be used repetitively.
2: The content barely relates to the task.
1: [LIMIT] Responses of 20 words or fewer are rated at Band 1. [LIMIT] The content is wholly unrelated to the task. \
Any copied rubric must be discounted."""

_TR_T2 = """TASK RESPONSE (Task 2)
9: The prompt is appropriately addressed and explored in depth. A clear and fully developed position is presented \
which directly answers the question/s. Ideas are relevant, fully extended and well supported. Any lapses in content or \
support are extremely rare.
8: The prompt is appropriately and sufficiently addressed. A clear and well-developed position is presented in \
response to the question/s. Ideas are relevant, well extended and supported. There may be occasional omissions or \
lapses in content.
7: The main parts of the prompt are appropriately addressed. A clear and developed position is presented. Main ideas \
are extended and supported but there may be a tendency to over-generalise or there may be a lack of focus and \
precision in supporting ideas/material.
6: The main parts of the prompt are addressed (though some may be more fully covered than others). An appropriate \
format is used. A position is presented that is directly relevant to the prompt, although the conclusions drawn may be \
unclear, unjustified or repetitive. Main ideas are relevant, but some may be insufficiently developed or may lack \
clarity, while some supporting arguments and evidence may be less relevant or inadequate.
5: [LIMIT] The main parts of the prompt are incompletely addressed. The format may be inappropriate in places. The \
writer expresses a position, but the development is not always clear. Some main ideas are put forward, but they are \
limited and are not sufficiently developed and/or there may be irrelevant detail. There may be some repetition.
4: The prompt is tackled in a minimal way, or the answer is tangential, possibly due to some misunderstanding of the \
prompt. [LIMIT] The format may be inappropriate. A position is discernible, but the reader has to read carefully to \
find it. Main ideas are difficult to identify and such ideas that are identifiable may lack relevance, clarity and/or \
support. Large parts of the response may be repetitive.
3: No part of the prompt is adequately addressed, or the prompt has been misunderstood. No relevant position can be \
identified, and/or there is little direct response to the question/s. There are few ideas, and these may be irrelevant \
or insufficiently developed.
2: The content is barely related to the prompt. No position can be identified. There may be glimpses of one or two \
ideas without development.
1: [LIMIT] Responses of 20 words or fewer are rated at Band 1. [LIMIT] The content is wholly unrelated to the prompt. \
Any copied rubric must be discounted."""


def _cc(task2: bool) -> str:
    def p(text: str) -> str:
        return f" {text}" if task2 else ""
    return f"""COHERENCE & COHESION
9: The message can be followed effortlessly. Cohesion is used in such a way that it very rarely attracts attention. Any \
lapses in coherence or cohesion are minimal. Paragraphing is skilfully managed.
8: The message can be followed with ease. Information and ideas are logically sequenced, and cohesion is well managed. \
Occasional lapses in coherence or cohesion may occur. Paragraphing is used sufficiently and appropriately.
7: Information and ideas are logically organised and there is a clear progression throughout the response. A few \
lapses may occur. A range of cohesive devices including reference and substitution is used flexibly but with some \
inaccuracies or some over/under use.{p("Paragraphing is generally used effectively to support overall coherence, and the sequencing of ideas within a paragraph is generally logical.")}
6: Information and ideas are generally arranged coherently and there is a clear overall progression. Cohesive devices \
are used to some good effect but cohesion within and/or between sentences may be faulty or mechanical due to misuse, \
overuse or omission. The use of reference and substitution may lack flexibility or clarity and result in some \
repetition or error.{p("Paragraphing may not always be logical and/or the central topic may not always be clear.")}
5: Organisation is evident but is not wholly logical and there may be a lack of overall progression. Nevertheless, \
there is a sense of underlying coherence to the response. The relationship of ideas can be followed but the sentences \
are not fluently linked to each other. There may be limited/overuse of cohesive devices with some inaccuracy. The \
writing may be repetitive due to inadequate and/or inaccurate use of reference and substitution.{p("[LIMIT] Paragraphing may be inadequate or missing.")}
4: Information and ideas are evident but not arranged coherently, and there is no clear progression within the \
response. Relationships between ideas can be unclear and/or inadequately marked. There is some use of basic cohesive \
devices, which may be inaccurate or repetitive. There is inaccurate use or a lack of substitution or \
referencing.{p("There may be no paragraphing and/or no clear main topic within paragraphs.")}
3: There is no apparent logical organisation. Ideas are discernible but difficult to relate to each other. Minimal use \
of sequencers or cohesive devices. There is difficulty in identifying referencing.
2: There is little relevant message, or [LIMIT] the entire response may be off-topic. There is little evidence of \
control of organisational features."""


_LR_GRA = """LEXICAL RESOURCE
9: Full flexibility and precise use are evident. A wide range of vocabulary is used accurately and appropriately with \
very natural and sophisticated control of lexical features. Minor errors in spelling and word formation are extremely \
rare and have minimal impact on communication.
8: A wide resource is fluently and flexibly used to convey precise meanings. There is skilful use of uncommon and/or \
idiomatic items when appropriate, despite occasional inaccuracies in word choice and collocation. Occasional errors in \
spelling and/or word formation may occur, but have minimal impact on communication.
7: The resource is sufficient to allow some flexibility and precision. There is some ability to use less common and/or \
idiomatic items. An awareness of style and collocation is evident, though inappropriacies occur. There are only a few \
errors in spelling and/or word formation, and they do not detract from overall clarity.
6: The resource is generally adequate and appropriate for the task. The meaning is generally clear in spite of a \
rather restricted range or a lack of precision in word choice. If the writer is a risk-taker, there will be a wider \
range of vocabulary used but higher degrees of inaccuracy or inappropriacy. There are some errors in spelling and/or \
word formation, but these do not impede communication.
5: The resource is limited but minimally adequate for the task. Simple vocabulary may be used accurately but the range \
does not permit much variation in expression. There may be frequent lapses in the appropriacy of word choice, and a \
lack of flexibility is apparent in frequent simplifications and/or repetitions. Errors in spelling and/or word \
formation may be noticeable and may cause some difficulty for the reader.
4: The resource is limited and inadequate for or [LIMIT] unrelated to the task. Vocabulary is basic and may be used \
repetitively. There may be inappropriate use of lexical chunks (e.g. memorised phrases, formulaic language and/or \
language from the input material). Inappropriate word choice and/or errors in word formation and/or in spelling may \
impede meaning.
3: The resource is inadequate (which may be due to the response being significantly underlength). Possible \
over-dependence on input material or memorised language. Control of word choice and/or spelling is very limited, and \
errors predominate. These errors may severely impede meaning.

GRAMMATICAL RANGE & ACCURACY
9: A wide range of structures is used with full flexibility and control. Punctuation and grammar are used \
appropriately throughout. Minor errors are extremely rare and have minimal impact on communication.
8: A wide range of structures is flexibly and accurately used. The majority of sentences are error-free, and \
punctuation is well managed. Occasional, non-systematic errors and inappropriacies occur, but have minimal impact on \
communication.
7: A variety of complex structures is used with some flexibility and accuracy. Grammar and punctuation are generally \
well controlled, and error-free sentences are frequent. A few errors in grammar may persist, but these do not impede \
communication.
6: A mix of simple and complex sentence forms is used but flexibility is limited. Examples of more complex structures \
are not marked by the same level of accuracy as in simple structures. Errors in grammar and punctuation occur, but \
rarely impede communication.
5: The range of structures is limited and rather repetitive. Although complex sentences are attempted, they tend to \
be faulty, and the greatest accuracy is achieved on simple sentences. Grammatical errors may be frequent and cause \
some difficulty for the reader. Punctuation may be faulty.
4: A very limited range of structures is used. [LIMIT] Subordinate clauses are rare and simple sentences predominate. \
Some structures are produced accurately but grammatical errors are frequent and may impede meaning. Punctuation is \
often faulty or inadequate.
3: Sentence forms are attempted, but errors in grammar and punctuation predominate (except in memorised phrases or \
those taken from the input material). This prevents most meaning from coming through. [LIMIT] Length may be \
insufficient to provide evidence of control of sentence forms."""


def band_descriptors(task_type: str) -> str:
    """The official band descriptors for this task (Academic), with how they are applied."""
    task2 = task_type != "task1"
    return ("A script must FULLY fit the positive features of the descriptor at a band to get that band. [LIMIT] marks "
            "a negative feature that limits the rating to that band or below, whatever else the answer does well. "
            "Band 0 only for no attempt, another language throughout, or a wholly memorised answer.\n\n"
            + "\n\n".join((_TR_T2 if task2 else _TA_T1, _cc(task2), _LR_GRA)))

# What is never a mistake, and where each problem belongs - shared by the
# scorer, the feedback, the missed-errors pass and the mistake filter, so a
# style preference can neither lower a band nor reach the report.
NOT_ERRORS = """NOT ERRORS - never count or list these: style preferences and valid variants \
("different from/than/to", "treated differently than", "live healthier", British vs American \
spelling and usage, "volunteers of art" vs "art volunteers", "hold influence over", "a renewed \
interest in", "drawbacks to", "see a rise", "foods" meaning kinds of food, "others'" with the noun \
left out, "was least popular", "a chart-high / chart-low of", "bear parallels to/with", "reach \
parity", "buck the trend", a plainer word that is still correct, an optional comma, a plural verb after \
"the majority / a share / a proportion of" + a plural noun - "the majority of people prefer"); a paraphrase of a \
chart's label ("age at death" for "life expectancy", "proximity to home" for "close to parental \
home"); a word or phrase the question itself uses; a proper name as its owner writes it ("Malala \
Fund"); the writer's own grouping of steps or categories ("in the final stage" of the writer's own \
three stages); anything whose "fix" would change what the student meant; a standard phrase of data \
reports ("had the upper hand", "took the lead", "by far the most popular") - never a register lapse; in a \
process or diagram, two adjacent parts named together ("lined with a layer of clay and synthetic material" \
for a clay liner and a synthetic lining) or a step put in fewer words ("pressed and filtered using a \
hydraulic press"); a detail a correct sentence leaves out (at most a tip, never a mistake). A Task mistake \
in a process is only a wrong step, a wrong order, a wrong part or a step the diagram doesn't show \
("bottled, labeled" with no labelling step).
NEVER PENALISE (the academic lead, 2026-10-29): paraphrasing the question ("available" for "open" is good, \
not a mistake); a free, non-template structure (no "This essay will discuss", a short body paragraph, an \
unusual but logical order, the writer's own conclusion style); British or American spelling used consistently."""

ONE_CRITERION = """ONE PROBLEM, ONE CRITERION: word choice, collocation, word form, spelling and \
register -> LR (Lexical); grammar and punctuation -> GRA; linking, referencing, paragraphing and \
logic -> CC; content, task coverage and data -> TR (Task Achievement/Response). A language problem \
never lowers Task Achievement/Response - "were quite passive" in a report is a word-choice (LR) \
issue, not a Task one."""

# What real examiners count as a mistake - general rules drawn from every examiner's comment in the
# calibration books (the official Cambridge samples' comments, books 13-18, and the commented Band
# 6.5-8.5 collection), 2026-11-09: the user's aim is that the finder and the filter learn what a
# mistake is from the books, not from one essay at a time. The finder, the missed-errors pass and
# the filter all get it; the scorer has the band-level part in its guide (item 9).
EXAMINER_PRACTICE = """WHAT REAL EXAMINERS COUNT AS A MISTAKE (their comments on dozens of marked answers, Band 5 to \
9) - apply these rules to every answer:
An ERROR is a form that is wrong in standard English, or a word that gives the wrong or an unclear meaning. \
The kinds examiners name again and again:
- spelling ("acomodate", "biuldings"); word formation ("substantially growth" for substantial growth, \
"slightly" for slight, "approximate" for approximately);
- agreement and verb forms ("almost all the country have", "people who pushes", a missing third-person -s, \
"get harvested" for is harvested, "after separated" for after separating, "it is can be");
- tense against the time the visual or the plan shows (present forms for 2017-2018 data; past forms for \
changes planned for the future);
- reference ("This two features"); countable forms ("less problems"); a preposition in a fixed pattern; \
articles used inconsistently; a missing capital letter in a name; an incomplete sentence; a missing full stop;
- a word whose meaning is wrong for the context ("overed by" for overtaken by, "object" for objective, \
"spent rates" for expenditure), or so vague that the meaning is lost ("stuff and things").
In a Task 1 report, a figure the visual contradicts, or a map or process described wrongly (a feature in the \
wrong place or state, a wrong or missing step), is a Task error.
NOT AN ERROR - examiners name these as ways to improve, never as mistakes:
- a correct word that is plain, general or simple ("buy", "popular", "have fun"), a correct linker that is \
formal or formulaic ("Moreover", "Furthermore", "Finally"), a correct sentence pattern used often: a point \
about the criterion's range, an upgrade idea at most;
- a correct word or structure used too often ("features", "going to" for every plan): one range point for \
that criterion, never one mistake for each use;
- the writer's own sound structure (one-sided or two-sided, no introduction in Task 1, the opinion in its \
own paragraph) and an idea that could be developed further: Task points, never language mistakes.
HOW MUCH IT WEIGHS: an error that does not get in the reader's way still counts, but weighs little ("a \
spelling error ... does not cause any difficulty for the reader"; one misspelling the writer gets right \
elsewhere is "probably a slip"). Band 6-6.5 scripts have many errors and are still clear, Band 7-7.5 keep \
"occasional" or "a few" errors, Band 8 rare ones. Never invent mistakes in a strong answer to fill a list, \
and never let a real error pass because the answer is strong.
WHEN UNSURE: if you can't name the rule of standard English the words break, or the wrong meaning they \
give, it is not an error - at most an upgrade idea."""

# What a scorer looks for on each criterion before it may give 9 (the academic lead, 2026-10-29: #71's
# Coherence 9 though "Taking this into account" followed nothing and one paragraph held two central ideas)
CHECKLISTS = {
    "TR_or_TA": {
        "task1": ["no clear overview of the main trends or differences", "a key feature left out",
                  "a figure or comparison the DATA SHEET contradicts", "details listed without comparisons",
                  "information, explanations or opinions the visual doesn't show",
                  "a need of this kind of visual (WHAT THIS KIND OF VISUAL NEEDS) not met"],
        "task2": ["a part of the question not answered", "a position that is unclear or changes",
                  "a main idea stated but not extended or supported", "an idea off the question"],
    },
    "CC": ["a linker that doesn't fit the logic of what it joins", "a paragraph with two central ideas",
           "mechanical or repetitive linking (the same linker again, a linker opening every sentence)",
           "unclear referencing (this / it / they with no clear antecedent)",
           "an order of ideas that breaks the progression",
           "only basic linkers (First, Second, Also, But, Then) or sentences that follow each other like a list"],
    "LR": ["a wrong word or collocation among the CHECKED MISTAKES", "repetition where range is expected",
           "a word whose register doesn't fit", "everyday words throughout, few or no less common ones",
           "a word that is grammatical but unnatural or imprecise for its meaning"],
    "GRA": ["a grammar error among the CHECKED MISTAKES", "a narrow range of structures (mostly simple sentences)"],
}


class ItemCheck(BaseModel):
    # "additionalProperties": false in its schema - OpenAI's strict mode demands it of an object it inlines
    # (a field with its own description), and the SDK adds it only to the shared definitions
    model_config = ConfigDict(extra="forbid")

    found: bool = Field(description="True when the answer has this problem.")
    quote: str = Field(description="When found: the words of the answer that show it, copied exactly; else ''.")


_STRICT = ConfigDict(extra="forbid")
# The checklist items that say a range is narrow: a run that answers "not found" to its criterion's item has
# seen a wide enough range to lift an unexplained 8 (2026-11-12); PER_CRITERION_WIDE switches that on.
RANGE_ITEMS = {"LR": "everyday words throughout, few or no less common ones",
               "GRA": "a narrow range of structures (mostly simple sentences)"}
PER_CRITERION_WIDE = True
# Task 2 Response: a run's own reason that is Band 7's in the descriptors - an idea not extended or supported, a
# claim over-generalised or without support; the checklist's "a main idea stated but not extended or supported".
# Mild remarks ("could be developed further", "depth could be expanded") are Band 8's "occasional lapses".
TR_DEVELOPMENT_CAP = True
_BAND7_DEVELOPMENT = re.compile(
    r"\bnot (?:fully |well |sufficiently |really |properly )?(?:developed|extended|supported|exemplified|"
    r"explained|elaborated|justified)\b|\bunder-?developed\b|\bunsupported\b|"
    r"\blacks? (?:of )?(?:development|support|supporting|examples?|elaboration|justification)\b|"
    r"\black(?:ing)? (?:of )?(?:development|support|examples?|elaboration)\b|\bover-?generali[sz]|"
    r"\bover-?stat|\bover-?strong|\bwithout (?:support|supporting|examples?|evidence|elaboration|justification)\b|"
    r"\bmain idea stated but not extended or supported\b", re.IGNORECASE)


def band7_development(reasons: list[str]) -> bool:
    """Does a Task Response reason name a Band 7 feature of development (see _BAND7_DEVELOPMENT)?"""
    return any(_BAND7_DEVELOPMENT.search(r) for r in reasons)


def _checklist_items(key: str, task_type: str) -> list[str]:
    return CHECKLISTS[key][task_type] if key == "TR_or_TA" else CHECKLISTS[key]


@lru_cache(maxsize=None)
def scoring_schema(task_type: str) -> type[BaseModel]:
    """The scoring run's answer with one required field per checklist item (2026-11-12: a run returned 4 of
    Lexical Resource's 5 items, so its 8 could not become the 9 it was - #164). Read back into
    WritingScoring by _as_scoring."""
    def judgement(key: str) -> type[BaseModel]:
        items = _checklist_items(key, task_type)
        checklist = create_model(f"Checklist{key}", __config__=_STRICT, **{
            f"item{i + 1}": (ItemCheck, Field(description=item)) for i, item in enumerate(items)})
        base = CriterionJudgement.model_fields
        return create_model(
            f"Judgement{key}", __config__=_STRICT,
            checklist=(checklist, Field(description="Every item of this criterion's CHECKLIST, answered.")),
            strengths=(list[str], Field(description=base["strengths"].description)),
            weaknesses=(list[str], Field(description=base["weaknesses"].description)),
            band=(int, Field(description=base["band"].description)),
            note=(str, Field(description=base["note"].description)))

    fields = WritingScoring.model_fields
    return create_model(
        "WritingScoring", __config__=_STRICT,
        **{key: (judgement(key), Field(description=fields[key].description)) for key in SCORE_KEYS},
        overview=(fields["overview"].annotation, Field(default="clear", description=fields["overview"].description)))


def _as_scoring(parsed, task_type: str) -> WritingScoring:
    """A scoring_schema answer as WritingScoring: each checklist a list with the items' own words."""
    if isinstance(parsed, WritingScoring):
        return parsed
    out = {}
    for key in SCORE_KEYS:
        j = getattr(parsed, key)
        out[key] = CriterionJudgement(
            checklist=[CheckFinding(item=item, found=getattr(j.checklist, f"item{i + 1}").found,
                                    quote=getattr(j.checklist, f"item{i + 1}").quote)
                       for i, item in enumerate(_checklist_items(key, task_type))],
            strengths=j.strengths, weaknesses=j.weaknesses, band=j.band, note=j.note)
    return WritingScoring(**out, overview=parsed.overview)


def checklist_text(task_type: str) -> str:
    lines = ["CHECKLIST - run it on every criterion BEFORE choosing its band: answer every item (found or not; "
             "when found, quote the words that show it). A found item is a reason for a band below 9; a band of "
             "9 means you looked and found none. Only real problems count - never anything listed as "
             "ACCEPTABLE AS WRITTEN."]
    for key, name in (("TR_or_TA", "Task Achievement" if task_type == "task1" else "Task Response"),
                      ("CC", "Coherence & Cohesion"), ("LR", "Lexical Resource"), ("GRA", "Grammar")):
        items = CHECKLISTS[key][task_type] if key == "TR_or_TA" else CHECKLISTS[key]
        lines.append(f"{name}: " + "; ".join(items) + ".")
    if task_type == "task1":
        lines.append("overview: clear when a sentence names the main trends, differences or stages; generic when it "
                     "only says that things changed (\"the economy changed significantly\") or names no feature; "
                     "none when no sentence sums up the visual. It may stand at the start or at the end.")
    return "\n".join(lines)


def marking_guide() -> str:
    """The marking guide, its density thresholds from config/writing_marking.json (academic lead).
    Item 3 read "benefit of the doubt: give the higher band" until 2026-11-09, against the official
    descriptors' "a script must fully fit the positive features of the descriptor at a particular level"."""
    d = marking_config.density()
    return f"""MARKING GUIDE - how to find each criterion's band:
1. List the strengths first, each with a short quote from the answer. Then the weaknesses, each \
with a short quote - real problems of THIS criterion only.
2. Where to start, from the evidence: start at Band 8 only when this criterion's weighted checked \
mistakes are at most {d['start_at_band8_max']:g} per 100 words (Grammar and Lexical Resource: the CHECKED \
MISTAKES; Task and Coherence: no more than a minor weakness) AND the answer shows this criterion's range - \
less common vocabulary used precisely (Lexical Resource), a variety of well-built complex structures \
(Grammar), linking and referencing used flexibly (Coherence), fully extended ideas (Task) - then check \
whether it meets Band 9. Otherwise start at Band 7 and check whether it meets Band 8 or falls to 6 or 5. \
Few errors alone never start a criterion at 8: simple, accurate sentences in everyday words start at 6. \
Weaknesses may be an empty list - never invent a weakness to fill the list.
3. The official rule (May 2023 descriptors): a band is given only when the answer FULLY fits the \
positive features of that band's descriptor, and a [LIMIT] feature caps the criterion at its band. When \
torn between two bands, check every positive feature of the higher one: all of them shown - the higher \
band; any one missing - the lower.
4. Score each criterion independently, on its own evidence. Never lower one criterion because \
another is weak.
5. Credit what works: less common vocabulary used well and complex structures used accurately count \
for the band, not only the absence of errors. Find the highest band whose positive features the answer \
fully shows - don't count mistakes.
6. Band 9 needs positive evidence, a lower band an honest reason: give 9 only when the strengths show \
the Band 9 features - Lexical Resource: a wide, natural and precise range of less common words; Grammar: \
a wide range of structures, almost all error-free; Coherence: cohesion that attracts no attention and \
skilful paragraphing; Task: every part fully developed. A limited range is a real reason, quoted or \
not: "mostly simple sentences", "everyday vocabulary", "the same few linkers" lower Grammar, Lexical \
Resource or Coherence. "Could be richer" or "not fully developed" with nothing behind them are no \
reason, and neither is a quote of something listed as ACCEPTABLE AS WRITTEN.
7. Judge, don't count (the academic lead, 2026-10-29): weigh errors by their density per 100 words, \
their effect on meaning and the range the writer attempts - an error that comes from attempting less \
common language weighs less than one in a simple structure. A slip is very small: Band 9 allows rare \
slips.
8. A wide range with a few slips is Band 8 or 9, never 7. Range comes first for Lexical Resource and \
Grammar, errors only lower it further. Lexical Resource: everyday words used again and again ("the \
number", "went up", "good", "bad") are 5-6 even with no error; some less common words with awareness \
of collocation 7; a wide, precise and natural range 8-9; clear, less common vocabulary whose meaning is \
always clear is at least 7. A word that is grammatical but unnatural or imprecise for its meaning \
("highly irrelevant", "learn good") is a Lexical weakness - but a word choice or collocation that is \
off while its meaning stays clear is what the descriptors expect even at 7 ("inappropriacies occur") and 8 \
("occasional inaccuracies in word choice and collocation"): a few of them never take a good range below 7 \
(the CHECKED MISTAKES mark them "word choice off, meaning clear"; they weigh half). Memorised phrases, formulaic templates and \
language lifted from the question earn no Lexical credit; used inappropriately they are a Band 4 feature. \
Grammar (the descriptors): simple sentences predominating, with subordinate clauses rare, is a [LIMIT] \
at 4 even when accurate; a limited, repetitive range whose complex sentences tend to be faulty 5; a mix \
of simple and complex forms with limited flexibility, the complex ones less accurate, 6; a variety of \
complex structures with some flexibility, error-free sentences frequent, 7; a wide range used flexibly \
and accurately, the majority of sentences error-free, 8.
9. What real examiners do (their comments on real candidates' answers, 2026-11-01):
   - Communication first. A language error costs a band only as far as it gets in the reader's way: "frequent \
spelling errors that do not impede communication" are still a Band 6 script, "a handful of errors that don't get in \
the way" Band 6.5; a single misspelling is "probably a slip". Errors remain at Band 7 and 7.5: never ask for clean \
sentences to give 7.
   - Credit before errors. Less common vocabulary and collocation earn Lexical Resource even with errors elsewhere; \
vocabulary is often the strongest criterion of a weak script. A word slightly off with a clear meaning is "less \
precise", not a fault that sinks the band. Only a high level of error holds a good range down (about 6.5).
   - Task Achievement (Task 1): information the visual doesn't show, explanations and opinions lower it; a mechanical \
list of figures in order, with nothing compared, is about Band 5; one wrong detail or a wrong tense for a future plan \
is a small loss, not a band. A plan's own labels must be used: repeating them is no lack of range. Words copied from \
the question earn no credit, but are no mistake.
   - Task Response (Task 2): off the question is what sinks a script (a tangential answer 4-4.5, even with clear \
grammar); a part of the question covered thinly is about 6-6.5, but a main part not addressed at all (only the \
causes of a causes-and-solutions question, one view of a discuss-both-views question, no opinion where one is asked) \
is the descriptors' [LIMIT]: Task Response 5 at most. An opinion question needs no counter-argument, and \
missing specific examples are a way to improve, not a fault. No title is needed.
   - Coherence: linkers opening most sentences make cohesion mechanical - a Band 6 feature in the descriptors (Band 7 \
allows only some over/under use); a one-sentence paragraph can be a stylistic variant; no paragraphing at all hits \
this criterion alone.
   - Length: the word count never lowers a band (the report reminds the student of the minimum); a long answer is \
never a fault.
10. Coherence is judged by how the answer is organised and linked, never by its error count: basic \
linkers (First, Second, Also, But, Then) and sentences that follow each other like a list are Coherence \
6 at most; the same few linkers again and again, or paragraphs without one clear central idea, 5-6; \
Band 7 needs a clear progression with a range of linking and referencing; Band 8 cohesion that is well \
managed and never mechanical. Reference and substitution count (pronouns, "this", "the former", a \
synonym for a noun used before): the same nouns repeated where a reference or substitute was needed is a \
Band 6 feature, Band 5 when it makes the writing repetitive. Task 1: paragraphing counts only from Band 8 \
("paragraphing is used sufficiently and appropriately") - one long block holding the whole report keeps \
Coherence at 7 at most.

NOTES ON THE DESCRIPTORS (the official text is above; these say how to read it, never instead of it):
- Task Response support is explanation, reasoning, consequences or an example (a hypothetical one is \
fine): specific real-world examples are NOT required - never lower Task Response for "few examples" when \
the ideas are explained and extended.
- Band 8 is not the ceiling: an answer that fully fits the Band 9 descriptor gets 9 - a Band 9 answer may \
still contain extremely rare minor errors, and it need not be perfect.

ERROR DENSITY (Grammar and Lexical Resource) - weighted checked mistakes per 100 words, a slip (a typo, \
a missing full stop, a single article or plural slip) counting {d['slip_weight']:g}:
  Band 9: up to about {d['band9_max']:g} - only slips, no repeated pattern.
  Band 8: up to about {d['band8_max']:g} - non-systematic.
  Band 7: up to about {d['band7_max']:g} - in otherwise varied, mostly error-free sentences.
  Band 6: errors in many sentences, meaning clear.
  Band 5: frequent errors that make the reader work.
Density is half the picture: Band 7 and above also need range - less common vocabulary used well, a \
variety of complex structures. Grammar 8 also needs "the majority of sentences error-free" (software \
counts them for you below, from the CHECKED MISTAKES), Grammar 7 "frequent" error-free sentences.

{NOT_ERRORS}

{ONE_CRITERION}

LENGTH AND PARAGRAPHS (software applies these caps after you): the word count itself never lowers a \
band - an answer under the word minimum (Task 1: 150, Task 2: 250) is told so in the report, and is \
judged on what it does (how fully its ideas or key features are developed), never on how many words \
it has. Words copied from the question are not counted (the descriptors: "any copied rubric must be \
discounted"); 20 words or fewer of the writer's own: Band 1 on every criterion. A Task 2 essay with no paragraph breaks: Coherence & Cohesion 5 \
at most; a Task 1 report in one block: 7 at most. A Task 1 report on a visual of figures with no figure \
in it ("no data to support the description"): Task Achievement 5 at most. Grammar with fewer than half \
of its sentences error-free: 7 at most. Apart from these, length is never a reason to lower a band.

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

# What examiners ask of each kind of Task 1 visual - drawn from every examiner's comment on that kind in
# the calibration books, never from one essay (2026-11-09; maps: four official samples at 5.5-6.5 and two
# commented reports at 7.5-8; processes: two official at 5.5-6 and three commented at 6.5-8; graphs and
# charts: eight official at 5-7.5 and about a dozen commented at 7.5-8.5).
_KIND_NEEDS = {
    "map": "A MAP (two dates, or now and a plan): an overview naming the main changes (what is added, removed and "
           "kept, how the site grows); BOTH maps described - the earlier layout too, not only the changes; WHERE the "
           "main features are or will be (north, south, next to, opposite, in place of), so the reader can picture "
           "the layout; every feature in its right place and state; verb forms that fit each map's time (future "
           "forms for a plan, past forms for a past map). Examiners hold a map report at Task Achievement 6 or below "
           "when it has no summary of the main changes, leaves out the earlier layout, or doesn't say where things "
           "are (\"could be improved by adding a brief description of the centre as it is at present\", \"the reader "
           "cannot know where ... will be located\", \"adding a summary of the main changes\"); a feature described "
           "wrongly lowers it further. A Band 7.5-8 map report describes one map and then the other, with accurate "
           "spatial references.",
    "process": "A PROCESS OR CYCLE: an overview that groups the steps into the main stages (not only the first and "
               "last step); every main stage, in order; nothing the diagram doesn't show; the diagram's own labels "
               "may be used. Examiners hold it at Task Achievement 6 or below when the overview gives only the start "
               "and end points or there is no summary of the main stages, and lower it for irrelevant detail. A "
               "Band 8 process report groups the steps into stages, a paragraph each.",
    "trend": "A GRAPH OR CHART OVER TIME: an overview of the main trends (rises and falls, the highest and lowest, what "
             "changed most) - not a sentence that only says things changed; the key figures with their periods, the "
             "peaks, low points and fluctuations; comparisons between the series; tenses that fit the time shown. No "
             "overview, or one that names no trend: Task Achievement 5-6; figures listed in order with nothing "
             "compared: about 5.",
    "static": "A CHART OR TABLE OF CATEGORIES: an overview of the main differences (the largest and smallest, the clear "
              "contrasts and exceptions); categories grouped and compared, not listed one by one; accurate figures. "
              "No overview, or figures listed with nothing compared: about Task Achievement 5.",
    "mixed": "SEVERAL VISUALS: the key features of each one, compared, and an overview that brings them together; "
             "one visual covered thinly keeps Task Achievement below 8 (\"key features in the table could be more "
             "fully extended\").",
}
_NEEDS_OF_KIND = {"map": ("map",), "process": ("process",), "cycle": ("process",), "line": ("trend",),
                  "bar": ("static", "trend"), "table": ("static", "trend"), "pie": ("static", "trend"),
                  "mixed": ("mixed", "trend", "static")}


def kind_needs(kind: str | None) -> str:
    """What examiners ask of this kind of Task 1 visual ("" when the kind is unknown)."""
    parts = [_KIND_NEEDS[k] for k in _NEEDS_OF_KIND.get(kind or "", ())]
    if not parts:
        return ""
    return ("WHAT THIS KIND OF VISUAL NEEDS (from real examiners' comments on reports of this kind; a chart of "
            "categories that also shows change over time needs both):\n" + "\n".join(f"- {p}" for p in parts))

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

IELTS WRITING BAND DESCRIPTORS (official, May 2023):
{band_descriptors(task_type)}

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
   - {EXAMINER_PRACTICE}
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
5. priorities - 0 to 3, the most important first - a Task problem that holds the band down (a whole \
part of the visual or the task left out, no overview) or an answer under the word minimum (a reminder: \
no band is lowered for the word count itself) before any language pattern; \
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


def _scoring_system(task_type: str, kind: str | None = None, answer: str = "", question: str = "",
                    band: float | None = None) -> str:
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
        extra = "\n\n".join(x for x in (TA_GUIDE, kind_needs(kind), patterns_text()) if x)
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
        f"IELTS WRITING BAND DESCRIPTORS (official, May 2023):\n{band_descriptors(task_type)}",
        marking_guide(),
        extra,
        anchors_text(task_type, kind, answer, question, band),
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
- {EXAMINER_PRACTICE}
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
standard British or American written English - ungrammatical, or a word or collocation an educated \
reader finds wrong, unnatural or imprecise for its meaning? Then give its kind and the ONE criterion it belongs to.

kind (the academic lead's classes, 2026-10-29):
- error: wrong, or it makes the meaning unclear - "it should be noticed", "provided" used to mean \
"because", "this turn of events are", "people doesn't"; collocation errors and words an educated \
reader finds unnatural or imprecise for the meaning ("highly irrelevant" for "increasingly", "commuting \
charges" for "costs", "learn good", "have good and bad"), wrong-meaning or overstated words, verb-pattern errors, linking that doesn't fit the logic, a conclusion that \
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
available"): call a collocation an error when an educated native reader would find it wrong or \
unnatural, not when another word is merely more frequent. It is no mistake: it never counts;
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
with the smartphone market are both a testament"); a plural verb after "the majority / a share / a \
proportion / a number of" + a plural noun, which agrees with the people ("the overwhelming share of people \
from all countries prefer" - only "the number of" and "the percentage of" naming the figure take a singular \
verb). When one flagged stretch holds a single real error, only that error counts ("the football" \
-> "football" - the correct words around it are not wrong). These are never an error or a slip: a style preference or valid variant (a \
demonstrative for an article, a near-synonym, "ranging from X to Y", a plainer word, an optional comma, \
British vs American usage); the QUESTION'S OWN WORDS; a PROPER NAME as its owner writes it ("Malala \
Fund"); an expression whose CONNOTATION matches the writer's intent ("fraught with" before harmful \
things).
{NOT_ERRORS}

{EXAMINER_PRACTICE}

An error needs a reason: ungrammatical, the wrong meaning, or it confuses the reader. "Another phrase is more \
common" is an alternative, always, and so is a synonym swapped in with no grammar changed - unless the student's \
word means something else or sounds unnatural there (basis: meaning). A phrase the essay uses well is not a mistake because you would write it otherwise. \
basis says which reason it is; less_common says whether it is in less common language the writer attempted, which \
weighs less than a mistake in basic language.

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
a data error: only a wrong step order or a missing step is. On a map, what the student says about a feature whose place \
or change the sheet marks UNCERTAIN is never a mistake, and a claim about a feature's size or shape ("larger", \
"extended") is a mistake only when the sheet's own detail says the opposite - "present" or "retained" says nothing \
about size. A data correction must agree with the sheet: when an item \
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
                       vocabulary: list[tuple[int, str, str, str, str]] | None = None,
                       model: str | None = None) -> tuple[dict, dict]:
    """`items`: (id, where, wrong, correct, why, criterion, correction check or "");
    `vocabulary`: (index, term, meaning, example, check or ""). Returns
    ({id: (kind, criterion, reason, fixed_correct, basis, less_common)} - items
    without a verdict are kept as they are -, {index: (meaning or None,
    example or None)})."""
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
    response = await _parse("verify" if model is None else "verify_second", VERIFY_MAX_TOKENS, messages,
                            WritingVerdicts, effort=settings.openai_verify_reasoning_effort, model=model)
    parsed = response.choices[0].message.parsed
    if not parsed:
        return {}, {}
    verdicts = {v.id: (v.kind, v.criterion, v.reason, (v.fixed_correct or "").strip() or None, v.basis, v.less_common)
                for v in parsed.verdicts}
    words = {v.index: ((v.meaning or "").strip() or None, (v.example or "").strip() or None)
             for v in parsed.vocabulary}
    return verdicts, words


# Voting (the user's go-ahead, 2026-11-09): one filter run decided every mistake, so the same essay sent twice
# came back with different lists (#137: "appear" twice an error, #138: not listed). Now two runs judge every
# item, and a third only the items they disagree on; an item counts when most runs say error or slip.
VERIFY_VOTES = 3
MISSED_ID_BASE = 1000  # the missed-errors pass's finds in the one filter set: ids from here (the first pass: 1, 2, ...)
# The second vote's model (2026-11-11, cost): None - gpt-5 like the first. Set from the measured agreement
# with gpt-5's own second vote (see the commit); the deciding third vote is always gpt-5.
VERIFY_SECOND_MODEL: str | None = None
_COUNTED = ("error", "slip")


def _vote_class(verdict) -> str:
    return verdict[0] if verdict[0] in _COUNTED else "no"


def disputed(given: list) -> bool:
    """Do the runs disagree on an item (a missing verdict, counted or not, error or slip)?"""
    return any(g is None for g in given) or len({_vote_class(g) for g in given}) > 1


def vote(given: list[tuple]) -> tuple:
    """One verdict from several runs' verdicts (kind, criterion, reason, fixed, basis, less_common): it
    counts only when most runs say error or slip - a tie is the student's benefit of the doubt; it is an
    error only when most runs say error, else a slip. The criterion is the counted runs' most common one."""
    n = len(given)
    counted = [g for g in given if g[0] in _COUNTED]
    if len(counted) * 2 > n:
        kind = "error" if sum(g[0] == "error" for g in counted) * 2 > n else "slip"
        crits = [g[1] for g in counted]
        crit = max(dict.fromkeys(crits), key=crits.count)  # the first among equals
        pool = [g for g in counted if g[0] == kind] or counted
        rep = next((g for g in pool if g[1] == crit), pool[0])
        return (kind, crit) + tuple(rep[2:])
    rest = [g for g in given if g[0] not in _COUNTED]
    kinds = [g[0] for g in rest]
    kind = max(dict.fromkeys(kinds), key=kinds.count)
    return next(g for g in rest if g[0] == kind)


async def _voted_verify(source_text: str, items: list[tuple],
                        vocabulary: list[tuple[int, str, str, str, str]] | None = None,
                        runs: int = VERIFY_VOTES) -> tuple[dict, dict]:
    """_verify_call by vote: two runs in parallel (the vocabulary only in the first), a third on the items
    they disagree on. Same return as _verify_call."""
    if not items or runs < 2:
        return await _verify_call(source_text, items, vocabulary)
    first = await asyncio.gather(_verify_call(source_text, items, vocabulary),
                                 _verify_call(source_text, items, model=VERIFY_SECOND_MODEL),
                                 return_exceptions=True)
    good = [r for r in first if not isinstance(r, BaseException)]
    if not good:
        raise first[0]
    for r in first:
        if isinstance(r, BaseException):
            logger.warning("A verification run failed: %r", r)
    words = first[0][1] if not isinstance(first[0], BaseException) else {}
    results = [r[0] for r in good]
    if runs >= 3 and len(results) == 2:
        split = [it for it in items if disputed([res.get(it[0]) for res in results])]
        if split:
            try:
                results.append((await _verify_call(source_text, split))[0])
            except Exception:
                logger.exception("The deciding verification run failed - a split item gets the benefit of the doubt")
    verdicts, notes = {}, []
    for it in items:
        given = [res[it[0]] for res in results if it[0] in res]
        if not given:
            continue
        verdicts[it[0]] = vote(given)
        if len(given) > 1 and disputed(given):
            notes.append(f"{it[0]} «{it[2]}»: {', '.join(g[0] for g in given)} -> {verdicts[it[0]][0]}")
    if notes:
        logger.info("Verification votes: %s", "; ".join(notes))
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


async def _parse(purpose: str, max_tokens: int, messages: list[dict], response_format, effort: str | None = None,
                 model: str | None = None):
    """A gpt-5 call (or `model`) at the grading reasoning effort (or `effort`) with an
    output-token limit. A call cut off at the limit raises
    openai.LengthFinishReasonError; its cost is logged all the same."""
    try:
        response = await parse_flex(  # the Flex tier, the default as a fallback (openai_client)
            model=model or settings.openai_model_grading,
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


MARKS_ONLY = ("\n\nCALIBRATION RUN - only the marks are used: give the mistakes and the marked essay as asked; give "
              "priorities, vocabulary and checklist as empty lists and one short line for every note.")


@dataclass
class MarksOnly:
    """What grade_writing(marks_only=True) returns."""

    bands: dict
    overall: float
    runs: list
    capped: list
    flags: list
    strong: list


async def _feedback_call(task_type, prompt_text, answer_text, prompt_images, copied, bands,
                         sheet: str = "", notes: dict[str, str] | None = None,
                         marks_only: bool = False, needs: str = "") -> WritingFeedback | None:
    """None when the answer ran past FEEDBACK_MAX_TOKENS. `bands` None: the
    bands are decided after the mistakes (mistakes first). `needs`: what
    examiners ask of this kind of Task 1 visual (kind_needs), for the Task
    mistakes and priorities."""
    overall = overall_band(list(bands.values())) if bands else 0.0
    text = f"QUESTION PROMPT:\n{prompt_text}\n\nSTUDENT'S ANSWER:\n{answer_text}{_copied_note(copied)}"
    if needs:
        text += f"\n\n{needs}"
    messages = [
        {"role": "system", "content": _system_prompt(task_type, bands, overall, target_band(overall), notes)
         + (MARKS_ONLY if marks_only else "")},
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
              figures_visual: bool = True, overview: str = "clear", question: str = ""):
    """The length and paragraphing policy (marking guide B2, chosen to match
    the books - Volume 2 gives Band 8.0 to a 148-word report): returns
    (bands, notes, the caps applied, report notices). The "no comparisons"
    cap is for visuals of figures only (`figures_visual`): #58, a life
    cycle, had Task Achievement 5 for "figures listed with no comparisons"
    and not one figure in it. With the `question`, words copied from it are
    not counted; the official descriptors' limits (2026-11-09) are here too:
    20 words or fewer, no data in a report on figures, a Task 1 report in one
    block."""
    bands, notes = dict(bands), dict(notes)
    copied_words = sum(count_words(answer_text[s:e])
                       for s, e in rubric_spans(answer_text, question, copy_min_words(task_type))) if question else 0
    words, paragraphs = count_words(answer_text) - copied_words, paragraph_count(answer_text)
    data_figures = sum(1 for f in figures(answer_text) if not f["year"]) if task_type == "task1" else 0
    capped, notices = [], []

    def cap(key: str, limit: float, note: str) -> None:
        if bands[key] > limit:
            capped.append(f"{key} {bands[key]:.0f} -> {limit:.0f} ({note})")
            bands[key] = limit
            notes[key] = note

    if task_type == "task1" and overview == "none":
        cap("TR_or_TA", 5, "No overview of the main features keeps Task Achievement at 5 at most.")
    elif task_type == "task1" and overview == "generic":
        cap("TR_or_TA", 6, "A general overview naming no main feature keeps Task Achievement at 6 at most.")
    # "Any copied rubric must be discounted" (the official descriptors): words copied from the question
    # are not the writer's own. Under the word minimum the report says so, but no band is lowered for the
    # word count itself (the user's ruling, 2026-11-09: "kam so'z yozgani uchun ball ayirish kerak emas,
    # shunchaki eslatma bersa bo'ladi" - the Task 1 <140 / <100 and Task 2 <240 / <200 caps are gone);
    # only the descriptors' own limit stays, 20 words or fewer -> Band 1
    own = f"{words} words of your own" if copied_words else f"{words} words"
    minimum = TASK_MIN_WORDS.get(task_type, 0)
    if words <= 20:
        for key in SCORE_KEYS:
            cap(key, 1, f"Only {own}: 20 words or fewer are rated at Band 1.")
    elif words < minimum:
        piece = "report" if task_type == "task1" else "essay"
        notices.append(f"Your {piece} has {own} - no band was lowered for it, but aim for at least {minimum}.")
    if task_type == "task1":
        if figures_visual and count_comparisons(answer_text) == 0:
            cap("TR_or_TA", 5, "Figures are listed with no comparisons, so Task Achievement is 5 at most.")
        if figures_visual and data_figures == 0:
            cap("TR_or_TA", 5, "No figures support the description, so Task Achievement is 5 at most.")
        if paragraphs <= 1:
            # Task 1 Coherence 8 needs "paragraphing used sufficiently and appropriately" (the descriptors)
            cap("CC", 7, "The whole report is one block of text, so Coherence stays at 7 at most.")
    elif paragraphs <= 1:
        cap("CC", 5, "No paragraph breaks: one block of text keeps Coherence at 5 at most.")
    # "Cohesion attracts no attention" doesn't stand with language at 7 or below (#71: Coherence 9, both 7; the lead: 7-8)
    if max(bands["LR"], bands["GRA"]) <= 7:
        cap("CC", 8, "Language errors draw some attention, so Coherence stays at 8 at most.")
    # Under the word minimum the Task note says so too (2026-11-09: #127, a 205-word essay, was never told
    # Task 2 needs 250 words)
    if words < minimum and notes.get("TR_or_TA") and f"{words} words" not in notes["TR_or_TA"]:
        notes["TR_or_TA"] = f"{notes['TR_or_TA'].rstrip('. ')} ({words} words: aim for {minimum}+)."
    return bands, notes, capped, notices


# an adverb may stand between ("not explicitly highlighted", "does not clearly note" - #137's Task 8 went to 9
# on both; 2026-11-09)
_OMITTED_VERBS = r"(?:mention|cover|describ|includ|state|give|note|highlight|specif|address|locat|compar|summari[sz])\w*"
_OMISSION = re.compile(rf"\b(omit\w*|omission|missing|leaves? out|left out|not (?:\w+ly )?{_OMITTED_VERBS}|"
                       rf"(?:doesn't|does not|never|fails? to) (?:\w+ly )?{_OMITTED_VERBS}|no mention|"
                       r"absen\w*|lacks?|lacking|no overview|ignor\w*|skips?)\b", re.IGNORECASE)
_QUOTED = re.compile(r"[\"“«']([^\"”»']{2,120})[\"”»']")


_RANGE_LIMIT = re.compile(r"\b(repeat\w*|repetit\w*|overus\w*|limited|narrow|everyday|mostly simple|"
                          r"(?:simple|basic) (?:structures?|sentences?|sentence patterns?|vocabulary|words?|range|"
                          r"linkers?|linking)|few (?:complex|less common)|lacks? (?:range|variety|complex)|"
                          r"wider range|could be wider)\b", re.IGNORECASE)


def _stem(word: str) -> str:
    word = word.lower()
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def given_words(text: str) -> set[str]:
    """The words a question and its data sheet give (titles, categories, units) - stemmed."""
    return {_stem(w) for w in re.findall(r"[A-Za-z]+", text) if len(w) > 2}


_INNER_QUOTE = re.compile(r"[\"“«]([^\"”»]{1,60})[\"”»]")


def only_given(text: str, given: set[str]) -> bool:
    """Does a remark quote only words the question or the visual gives? Its quoted terms, or all its words
    when nothing is quoted (then meta words like "frequent" make it False - the remark stands)."""
    terms = _INNER_QUOTE.findall(text) or [text]
    words = [_stem(w) for t in terms for w in re.findall(r"[A-Za-z]+", t) if len(w) > 2]
    return bool(words) and all(w in given for w in words)


def _real_quotes(weakness: str, low: str, fine: list[str]) -> list[str]:
    """The words a weakness quotes that are in the answer and not found acceptable."""
    out = []
    for quoted in _QUOTED.findall(weakness):
        # a quote shortened with "..." counts by its pieces ("In 2005... In 2011... By 2015,")
        for q in re.split(r"\s*(?:\.\.\.|…)\s*", canon(quoted).lower()):
            q = q.strip(" .,")
            if len(q) >= 2 and q in low and not any(q in a or a in q for a in fine):
                out.append(q)
    return out


def evidenced(weaknesses: list[str], answer_text: str, acceptable: list[str] = ()) -> bool:
    """Does a criterion's list of weaknesses quote the answer or name an
    omission? An 8 without that is a 9 (the academic lead, 2026-10-27:
    "could be richer" and "not fully developed" lowered Band 9 reports). A
    quote of something the filter found acceptable as written (`acceptable`)
    is no reason."""
    low = canon(answer_text).lower()
    fine = [canon(a).lower().strip(" .,") for a in acceptable if a.strip()]
    return any(_OMISSION.search(w) or _real_quotes(w, low, fine) for w in weaknesses)


def language_reason(key: str, weaknesses: list[str], answer_text: str, acceptable: list[str], checked: list,
                    wide: bool) -> bool:
    """Is a Lexical Resource or Grammar band below 9 justified (the academic
    lead, 2026-10-31)? By a range limitation, quoted or not (the teachers, 2026-11-09: "range of
    complex structures is limited" was no reason, and a 128-word report of simple sentences came out
    Grammar 9), or by the classified
    list: at least 2 distinct real errors; one isolated error only when the
    range isn't wide or the meaning unclear; slips only past "rare" (the Band
    9 density). A phrase nobody flagged is no reason (#74: "had the upper
    hand" called a register lapse)."""
    low = canon(answer_text).lower()
    fine = [canon(a).lower().strip(" .,") for a in acceptable if a.strip()]
    if any(_RANGE_LIMIT.search(w) for w in weaknesses):
        return True
    mine = [m for m in checked if m.criterion == key]
    errors = [m for m in mine if getattr(m, "severity", "error") != "slip"]
    distinct = len(distinct_slips(errors))
    if distinct >= 2:
        return True
    if distinct == 1:
        return not wide or any(getattr(m, "unclear", False) for m in errors)
    density = marking_config.density()
    slips = len(distinct_slips(mine)) * density["slip_weight"] * 100 / max(count_words(answer_text), 1)
    return slips > density["band9_max"]


async def _score_once(task_type, prompt_text, answer_text, prompt_images, sheet: str = "",
                      evidence: str = "", kind: str | None = None, acceptable: list[str] = (),
                      checked: list | None = None, wide: bool = False) -> dict:
    """One scoring run: {criterion: whole band, ..., "_notes": {criterion: note}}.
    `evidence`: the checked mistakes and strong phrases (mistake_evidence);
    `kind`: the visual's kind, for its Band 9 anchor; `acceptable`: what the
    filter found acceptable as written - quoting it is no reason for a band;
    `checked`, `wide`: the checked mistakes and whether the range is wide -
    a Lexical or Grammar band below 9 needs language_reason."""
    facts = f"COUNTED BY SOFTWARE: {count_words(answer_text)} words, {paragraph_count(answer_text)} paragraph(s)."
    if checked is not None:
        free, total = error_free_sentences(answer_text, checked)
        facts += f" Sentences with no checked grammar or punctuation mistake: {free} of {total}."
    text = f"QUESTION PROMPT:\n{prompt_text}\n\nANSWER:\n{answer_text}\n\n{facts}"
    if evidence:
        text += f"\n\n{evidence}"
    messages = [
        {"role": "system", "content": _scoring_system(task_type, kind, answer_text, prompt_text,
                                                      estimated_band(answer_text, checked, wide))},
        {"role": "user", "content": _with_images(text, prompt_images, task_type == "task1", _judged(sheet))},
    ]
    response = await _parse("score", SCORE_MAX_TOKENS, messages, scoring_schema(task_type),
                            effort=settings.openai_scoring_reasoning_effort)
    scores = response.choices[0].message.parsed
    if scores is None:
        raise RuntimeError("Scoring model returned no parsable result")
    scores = _as_scoring(scores, task_type)
    out: dict = {k: whole_band(getattr(scores, k).band) for k in SCORE_KEYS}
    out["_overview"] = scores.overview if task_type == "task1" else "clear"

    def looked(k: str) -> bool:
        return len(getattr(scores, k).checklist) >= len(CHECKLISTS[k][task_type] if k == "TR_or_TA" else CHECKLISTS[k])

    # A Task 1 report has to name what the visual measures: repeating its own terms ("route", "passengers"
    # in a table of route kilometres and passengers) is no lack of range (2026-11-12, #164 - a real Band 9
    # report held at Lexical 8 for "system(s)", "route", "passengers")
    given = given_words(prompt_text + "\n" + sheet) if task_type == "task1" else set()

    def label_repetition(text: str) -> bool:
        return bool(given) and _RANGE_LIMIT.search(text) is not None and only_given(text, given)

    def found(k: str) -> list[str]:
        # with the item's words: "a narrow range of structures" tells what the quote shows
        return [f'"{c.quote}" ({c.item})' for c in getattr(scores, k).checklist if c.found and c.quote.strip()
                and not (k == "LR" and given and _RANGE_LIMIT.search(c.item) and only_given(c.quote, given))]

    # Whether Lexical Resource / Grammar shows a wide range: the strong phrases the report shows (another call's
    # count, 3 or more), or - since every checklist item is answered - this run itself finding no range item
    # ("everyday words throughout", "a narrow range of structures"). #164, a real Band 9 report, had Lexical and
    # Grammar 8 with no reason at all, held there because the feedback marked 2 strong phrases that time.
    def range_shown(k: str) -> bool:
        item = RANGE_ITEMS.get(k)
        return bool(item) and looked(k) and not any(c.found and c.item == item for c in getattr(scores, k).checklist)

    wide_of = {k: wide or (PER_CRITERION_WIDE and range_shown(k)) for k in SCORE_KEYS}

    def reason(k: str, weaknesses: list[str]) -> bool:
        if k == "LR":
            weaknesses = [w for w in weaknesses if not label_repetition(w)]
        if k in ("LR", "GRA") and checked is not None:
            return language_reason(k, weaknesses, answer_text, acceptable, checked, wide_of[k])
        return evidenced(weaknesses, answer_text, acceptable)

    # Task 9 "fully satisfies all the requirements" (the descriptors): an 8 is lifted only with the word
    # minimum, no Task mistake on the checked list and (Task 1) a clear overview - #137, a Band 6.5 map report
    # with neither layout located, went 8 -> 9 for want of a quoted weakness
    task_full = count_words(answer_text) >= TASK_MIN_WORDS.get(task_type, 0) \
        and not any(m.criterion == "TR" for m in (checked or [])) \
        and (task_type != "task1" or scores.overview == "clear")
    # what each run gave and why, before the lifts - the one place a band's reasons can be read afterwards
    # (2026-11-12: #164, a real Band 9 report, kept Lexical 8 and nothing showed why)
    logger.info("Writing scoring run (wide range: %s; shown by this run: %s): %s", wide,
                [k for k in ("LR", "GRA") if wide_of[k]], "; ".join(
        f"{k} {out[k]:g}{'' if looked(k) else ' (checklist incomplete)'} {getattr(scores, k).weaknesses} "
        f"found {found(k)}"[:400] for k in SCORE_KEYS))
    for k in SCORE_KEYS:
        judged = getattr(scores, k)
        if k == "TR_or_TA" and task_type == "task2" and out[k] == 8 and TR_DEVELOPMENT_CAP \
                and band7_development(judged.weaknesses + found(k)):
            # Task Response 8 is "ideas well extended and supported"; 7 is "a tendency to over-generalise or a
            # lack of focus and precision in supporting ideas". A run that names that itself gave a 7
            # (2026-11-12, #166: the teachers' 6.5-7.0 essay, Task 8 in all three runs with "benefits not
            # developed", "claim without support", "over-generalised" as their own reasons)
            logger.info("Score %s 8 -> 7: its own reasons are Band 7's (%s)", k, judged.weaknesses + found(k))
            out[k] = 7.0
        if out[k] == 9 and found(k) and reason(k, found(k)):
            logger.info("Score %s 9 -> 8: its checklist found %s", k, found(k))
            out[k] = 8.0
        elif out[k] == 8 and k != "CC" and looked(k) and not reason(k, judged.weaknesses + found(k)) \
                and ((task_full and not any(w.strip() for w in judged.weaknesses)) if k == "TR_or_TA"
                     else wide_of[k] or checked is None):
            # Task: only when the run named no weakness at all (2026-11-11, #153: "depth could be further
            # expanded" and "some points could be probed further" were lifted to Task 9 in all three runs - the
            # teachers' 7.0-7.5 essay came out 8.0; Task 9 is "explored in depth")
            # only an 8 is lifted: a 6 or 7 whose reason came back unquoted stays (2026-10-29: #60's and
            # #62's Coherence 6-7 -> 9 with "linking is basic and repetitive" as their reason). Never
            # Coherence, and Lexical/Grammar only with a wide range shown (the teachers, 2026-11-09: a
            # 128-word report of simple sentences came out Coherence 9 and Grammar 9)
            logger.info("Score %s %g -> 9: no quoted weakness or named omission, checklist clear (%s)", k, out[k],
                        judged.weaknesses)
            out[k] = 9.0
    if checked is not None:
        # three 9s and one lower: Lexical or Grammar must show the errors behind it, or it is a 9 too
        for k in ("LR", "GRA"):
            if out[k] < 9 and wide_of[k] and looked(k) and all(out[o] == 9 for o in SCORE_KEYS if o != k) \
                    and not reason(k, getattr(scores, k).weaknesses + found(k)):
                logger.info("Score %s %g -> 9: the other three are 9 and no error justifies it", k, out[k])
                out[k] = 9.0
    out["_notes"] = {k: getattr(scores, k).note.strip() for k in SCORE_KEYS}
    # a Coherence 8 with its checklist run and no quoted weakness: coherence_nine() may make it a 9
    out["_cc_clear"] = out["CC"] == 8 and looked("CC") and not evidenced(
        scores.CC.weaknesses + found("CC"), answer_text, acceptable)
    return out


_EVIDENCE_NAMES = (("GRA", "Grammar (GRA)"), ("LR", "Vocabulary (LR)"), ("CC", "Coherence (CC)"),
                   ("TR", "Task (TR)"))


CLEAR_CHOICE = "clear_choice"  # a checked mistake's less_common: a word choice or collocation off, its meaning clear


def _weight(m, slip: float, less_common: float) -> float:
    """What a checked mistake counts for in the density: an error 1, in less
    common language (or a word choice off with its meaning clear) less, a
    slip least."""
    if getattr(m, "severity", "error") == "slip":
        return slip
    return less_common if getattr(m, "less_common", False) else 1.0


def estimated_band(answer_text: str, checked: list | None, wide: bool = True) -> float | None:
    """Where the checked mistakes point, by the density guide - it only picks
    the official samples a scoring run sees, never a band. Without a wide
    range at most 7: few errors in simple language is no Band 8 (the teachers,
    2026-11-09)."""
    if checked is None:
        return None
    d = marking_config.density()
    half = d["less_common_weight"]
    errors = sum(_weight(m, d["slip_weight"], half) for m in checked if m.criterion in ("LR", "GRA"))
    density = errors * 100 / max(count_words(answer_text), 1) / 2  # two criteria
    band = 8.0 if density <= d["band8_max"] else 7.0 if density <= d["band7_max"] else 6.0 if density <= 5 else 5.0
    return band if wide else min(band, 7.0)


def shown_strong(answer_text: str, strong: list[str], checked: list | None) -> list[str]:
    """The strong phrases the report will show: in the text and free of any
    mistake (the official cafe sample: the floor counted 3, the report showed 1)."""
    spans = [loc for m in checked or [] if (loc := locate(answer_text, m.wrong.strip(" .…"))) is not None]
    wrongs = [canon(m.wrong).lower() for m in checked or []]
    out: list[str] = []
    for phrase in dict.fromkeys(x.strip() for x in strong):
        loc = locate(answer_text, phrase.strip(" .,…")) if phrase else None
        low = canon(phrase).lower()
        if loc is None or any(loc[0] < e and b < loc[1] for b, e in spans) or any(w and w in low for w in wrongs):
            continue
        out.append(phrase)
    return out


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
        half = marking_config.density()["less_common_weight"]
        weighted = (sum(_weight(m, weight, half) for m in errors) + len(slips) * weight) * 100 / words
        listed = "; ".join([f"«{m.wrong}» -> «{m.correct}»" + (
            " (word choice off, meaning clear)" if getattr(m, "less_common", False) == CLEAR_CHOICE
            else " (less common language)" if getattr(m, "less_common", False) else "") for m in errors[:12]]
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
    meaning unclear and at most about 1 word-choice or spelling mistake per
    100 words - Lexical Resource at least 7. The range counts only the strong
    phrases the report shows. Never Task Achievement/Response or Coherence, never
    an answer under 150 (Task 1) / 250 (Task 2) words."""
    if checked is None or count_words(answer_text) < TASK_MIN_WORDS.get(task_type, 0):
        return bands, []
    f = marking_config.floors()
    strong = shown_strong(answer_text, strong or [], checked)
    if len(strong) < f["wide_range_phrases"]:
        return bands, []
    out, raised = dict(bands), []
    words = max(count_words(answer_text), 1)
    weight = marking_config.density()["slip_weight"]
    for key in ("LR", "GRA"):
        mine = [m for m in checked if m.criterion == key]
        errors = [m for m in mine if getattr(m, "severity", "error") != "slip"]
        half = marking_config.density()["less_common_weight"]
        slips = distinct_slips([m for m in mine if m not in errors])
        density = (sum(_weight(m, weight, half) for m in errors) + len(slips) * weight) * 100 / words
        # Lexical: every word-choice and spelling mistake counts here, errors and slips alike - a word choice
        # off with its meaning clear as half of one (the descriptors allow "inappropriacies" at Lexical 7)
        counted = sum(half if getattr(m, "less_common", False) == CLEAR_CHOICE else 1 for m in errors) + len(slips)
        vocabulary_ok = key != "LR" or counted * 100 / words <= f["clear_vocabulary_max_per_100"]
        least, why = (f["slips_only_min"], "only slips") if not errors and vocabulary_ok else (0, "")
        clear = not any(getattr(m, "unclear", False) for m in mine)
        # Grammar only: on Lexical Resource it lifted a real Band 6 script from 7 to 8 (2026-11-01)
        if key == "GRA" and clear and density <= marking_config.density()["band8_max"] \
                and f.get("band8_density_min", 0) > least:
            least, why = f["band8_density_min"], f"{density:.2f} per 100 words, within Band 8"
        if key == "LR" and clear and vocabulary_ok and f["clear_vocabulary_lr_min"] > least:
            least, why = f["clear_vocabulary_lr_min"], "clear, wide vocabulary"
        if out[key] < least:
            raised.append(f"{key} {out[key]:g} -> {least:g} ({why})")
            out[key] = float(least)
    return out, raised


def grammar_ceiling(answer_text: str, bands: dict[str, float], checked: list | None) -> tuple[dict[str, float], list[str]]:
    """Grammar stays at 6 or below past density.grammar_band6_above, however the
    runs voted (2026-11-01): a real Band 6 script at 2.86 per 100 words came out
    Grammar 7 and overall 7. Density doesn't divide the bands cleanly - real
    Band 7 and 7.5 scripts have 2.70 and 3.33 - so the limit stays as high as
    that Band 6 case allows. Small slips weigh little, so they don't bring it on."""
    if checked is None or bands["GRA"] <= 6:
        return bands, []
    d = marking_config.density()
    mine = [m for m in checked if m.criterion == "GRA"]
    errors = [m for m in mine if getattr(m, "severity", "error") != "slip"]
    slips = distinct_slips([m for m in mine if m not in errors])
    density = (sum(_weight(m, d["slip_weight"], d["less_common_weight"]) for m in errors)
               + len(slips) * d["slip_weight"]) * 100 / max(count_words(answer_text), 1)
    if density <= d["grammar_band6_above"]:
        return bands, []
    return {**bands, "GRA": 6.0}, [f"GRA {bands['GRA']:g} -> 6 ({density:.1f} per 100 words)"]


_SENTENCE_END = re.compile(r"(?<=[.!?])[\"'”’)]*\s+|\n+")


def error_free_sentences(answer_text: str, checked: list | None) -> tuple[int, int]:
    """(sentences with no checked grammar or punctuation mistake, sentences) - the descriptors judge Grammar
    by them: "the majority of sentences are error-free" (8), "error-free sentences are frequent" (7).
    A fragment of fewer than three words is no sentence."""
    sentences = [s for s in _SENTENCE_END.split(canon(answer_text)) if len(s.split()) >= 3]
    wrongs = [canon(m.wrong).lower().strip(" .,") for m in (checked or []) if m.criterion == "GRA" and m.wrong.strip()]
    flawed = sum(1 for s in sentences if any(w and w in s.lower() for w in wrongs))
    return len(sentences) - flawed, len(sentences)


def sentence_ceiling(answer_text: str, bands: dict[str, float], checked: list | None) -> tuple[dict[str, float], list[str]]:
    """Grammar 8 or 9 needs "the majority of sentences error-free" (the official descriptors, 2026-11-09):
    with half of them or fewer clean, Grammar is 7 at most. Four sentences at least, so a short answer's one
    slip doesn't decide it."""
    if checked is None or bands["GRA"] <= 7:
        return bands, []
    free, total = error_free_sentences(answer_text, checked)
    if total < 4 or free * 2 > total:
        return bands, []
    return {**bands, "GRA": 7.0}, [f"GRA {bands['GRA']:g} -> 7 (only {free} of {total} sentences error-free)"]


COHERENCE_NINE_PARAGRAPHS = {"task1": 3, "task2": 4}
TASK_STEADY_RUNS = 5  # Task runs two bands apart or more: up to this many runs in all (median_of_runs)


def coherence_nine(task_type: str, answer_text: str, bands: dict[str, float], runs: list[dict],
                   checked: list | None, wide: bool) -> tuple[dict[str, float], list[str]]:
    """Coherence 8 -> 9 for a strong, full answer only (2026-11-09, #128: a
    Band 9 report came out Coherence 8 in both runs - "easy to follow
    throughout", no weakness - once 5e8092d stopped every Coherence lift).
    All of: the task's word minimum and a full paragraph plan (Task 1: 3,
    Task 2: 4), a wide range shown, no Coherence error on the checked list,
    most runs with no quoted Coherence weakness, and Task, Lexical and Grammar
    at 8 or more after the floors. The teachers' cases stay out: #121 (126
    words, Task 6) and #119 (Task 6) came out Coherence 9 before 5e8092d."""
    if bands.get("CC") != 8 or checked is None or not wide or not runs:
        return bands, []
    if count_words(answer_text) < TASK_MIN_WORDS.get(task_type, 0) \
            or paragraph_count(answer_text) < COHERENCE_NINE_PARAGRAPHS.get(task_type, 3):
        return bands, []
    if any(m.criterion == "CC" and getattr(m, "severity", "error") != "slip" for m in checked):
        return bands, []
    if any(bands[k] < 8 for k in SCORE_KEYS if k != "CC"):
        return bands, []
    if sum(bool(r.get("_cc_clear")) for r in runs) * 2 <= len(runs):
        return bands, []
    return {**bands, "CC": 9.0}, ["CC 8 -> 9 (a full, strong answer; no Coherence weakness quoted)"]


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
    wide = len(shown_strong(answer_text, strong or [], checked)) >= marking_config.floors()["wide_range_phrases"]
    medians, runs = await median_of_runs(
        lambda: _score_once(task_type, prompt_text, answer_text, prompt_images, sheet, evidence, kind, acceptable,
                            checked, wide),
        SCORE_KEYS, label=f"writing {task_type}",
        steady=("TR_or_TA", TASK_STEADY_RUNS),
    )
    votes = Counter(r.get("_overview", "clear") for r in runs)
    overview = "none" if votes["none"] * 2 > len(runs) else \
        "generic" if (votes["none"] + votes["generic"]) * 2 > len(runs) else "clear"
    medians, raised = floors(task_type, answer_text, medians, checked, strong)
    if raised:
        logger.info("Band floors (writing %s): %s", task_type, "; ".join(raised))
    medians, lowered = grammar_ceiling(answer_text, medians, checked)
    medians, fewer = sentence_ceiling(answer_text, medians, checked)
    lowered += fewer
    if lowered:
        logger.info("Grammar ceiling (writing %s): %s", task_type, "; ".join(lowered))
    medians, lifted = coherence_nine(task_type, answer_text, medians, runs, checked, wide)
    if lifted:
        logger.info("Coherence (writing %s): %s", task_type, "; ".join(lifted))
    notes = {}
    for k in SCORE_KEYS:
        nine = (NINE_NOTES[k][task_type] if k == "TR_or_TA" else NINE_NOTES[k]) if medians[k] >= 9 else ""
        notes[k] = next((r["_notes"][k] for r in runs if whole_band(r[k]) == medians[k] and r["_notes"].get(k)),
                        FLOOR_NOTES.get(k) or nine)
        if medians[k] >= 9 and _CRITICISM.search(notes[k]):  # "grouping could be sharper" under a 9
            notes[k] = NINE_NOTES[k][task_type] if k == "TR_or_TA" else NINE_NOTES[k]
    bands, notes, capped, notices = cap_bands(task_type, answer_text, medians, notes,
                                              data_visual(prompt_text, visual) if task_type == "task1" else True,
                                              overview, prompt_text)
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
    text_source: str = "text",
    marks_only: bool = False,
    fresh: bool = False,
):
    """`prompt_images` - (bytes, mime) pairs of the Task 1 chart/diagram, so
    the grader can check the student's figures and overview against the
    actual visual instead of just its text description; `sheet` - its data
    sheet (services/ai/visual_reading.py) with the report's data check, by
    which every call judges the figures; `visual` - that sheet as data
    (VisualSheet.model_dump()), by which code checks the data corrections.
    `on_feedback(bands, overall, feedback)` is called as soon as the bands
    are decided. `notices`: lines the report shows the student (e.g. the
    chart wasn't checked). `text_source`: how the answer reached us - scan
    artefacts are ignored only in a photo or a PDF. The same text gets the
    same result (services/ai/result_cache.py). `marks_only`: for calibration
    (scripts/marks_check.py) - the marks and the bands, no vocabulary,
    priorities or report: returns MarksOnly. `fresh`: marked anew even when
    this version marked the same text before (an admin or tester asked to
    check it again); the new result replaces the kept one.

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
    cache_key = result_cache.key(task_type, prompt_text, answer_text, bool(prompt_images), text_source)
    cached = None if marks_only or fresh else await result_cache.get(cache_key)
    if cached is not None:
        bands, overall, kept_feedback, report = cached
        logger.info("Writing %s: this text was already marked by this version - the same result", task_type)
        if on_feedback is not None:
            try:
                on_feedback(bands, overall, kept_feedback)
            except Exception:
                logger.exception("on_feedback failed")
        return report
    unchecked = set(unchecked_figures(answer_text, visual, prompt_text)) if visual else set()
    copied = [answer_text[s:e] for s, e in rubric_spans(answer_text, prompt_text, copy_min_words(task_type))]
    needs = kind_needs(visual_kind(prompt_text, visual)) if task_type == "task1" else ""
    feedback = await _feedback_call(task_type, prompt_text, answer_text, prompt_images, copied, None, sheet,
                                    marks_only=marks_only, needs=needs)
    problems = assess(feedback, answer_text) if feedback else ["the answer ran past its token limit"]
    if marks_only and feedback:
        problems = []  # nothing but the marks is used
    if problems:
        logger.warning("Feedback needs another try: %s", "; ".join(problems))
        retry = await _feedback_call(task_type, prompt_text, answer_text, prompt_images, copied, None, sheet,
                                     marks_only=marks_only, needs=needs)
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
            return await _voted_verify(source, items, vocabulary)
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
    flags: list[dict] = []  # every flag with its final class, kept with the report

    def scan_noise(m) -> bool:
        """A trace of scanning in a text from a photo or a PDF: "afford.able", "stilljuggling". In typed text
        the same marks are the student's own."""
        return text_source in SCANNED_SOURCES and m.criterion != "TR" and scan_artefact(m.wrong, m.correct, answer_text)

    def settle(item, verdict, label) -> tuple[bool, str, str, str]:
        """(keep, criterion, severity, correct, less common) from the filter's verdict - an
        error or a slip is kept (a slip weighs less), an alternative goes to
        the upgrade ideas, anything else goes; a data correction off the sheet
        keeps only its sheet-checked rewrite."""
        kind, crit, reason, fixed, basis, less_common = verdict
        less_common = less_common or basis == "spelling"  # a misspelling rarely gets in the reader's way
        if not less_common and crit == "LR" and basis == "meaning" and not getattr(item, "unclear", False):
            # an inaccuracy in word choice or collocation whose meaning is clear: the descriptors allow
            # "occasional inaccuracies in word choice and collocation" at Lexical 8 and "inappropriacies" at 7
            # (2026-11-09: Band 7-9 scripts went to Lexical 6-8 on three or four such flags)
            less_common = CLEAR_CHOICE
        said = kind
        kind, decided = final_kind(item.wrong, item.correct, kind, crit, basis)
        if decided:
            logger.info("%s is %s, not %s: %s", label, kind, said, decided)
            reason = decided
            if kind == "alternative":
                reason = f"Acceptable as written; «{item.correct}» is only a more common option."
        keep, correct = kind in ("error", "slip"), item.correct
        flags.append({"wrong": item.wrong, "correct": item.correct, "class": kind, "criterion": crit,
                      "reason": reason, "less_common": less_common,
                      "source": "missed" if label.startswith("missed") else "first"})
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
        return keep, crit, "slip" if kind == "slip" else "error", correct, less_common

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

    labels = visual_label_text(visual) if visual else ""

    def visual_label(m) -> bool:
        """The visual's own words are never a wording or grammar error either - its title, unit, axes and
        categories as printed (2026-11-12, #169: "per 1000-population", the chart's own unit, flagged four
        times as grammar). Never a Task mistake: naming the wrong category is one."""
        return m.criterion in ("GRA", "LR") and copies_visual_label(m.wrong, labels)

    auto = {m.id for m in feedback.mistakes
            if question_word(m) or data_protected(m) or scan_noise(m) or visual_label(m)}
    for m in feedback.mistakes:
        if m.id in auto:
            logger.info("Dropped mistake %s by code: the question's or the visual's own words, a figure the sheet "
                        "confirms or can't check, or a trace of scanning (%r)", m.id, m.wrong)
            flags.append({"wrong": m.wrong, "correct": m.correct, "class": "not_a_mistake", "criterion": m.criterion,
                          "reason": "set aside by code", "less_common": False})
    # The first pass's mistakes and the missed-errors pass's finds go to the filter together, as one voted set
    # (2026-11-11, cost: two sets meant two to three more calls, each with its own fixed share of reasoning);
    # the missed finds' ids are offset so the two lists never share one.
    missed = await find_missed()
    unfiltered_missed = missed
    missed = [x for x in missed if not question_word(x) and not data_protected(x) and not scan_noise(x)
              and not visual_label(x)]
    all_verdicts, fixes = await verify(
        [(m.id, "essay", m.wrong, m.correct, m.why, m.criterion, correction_note(m))
         for m in feedback.mistakes if checkable(m) and m.id not in auto]
        + [(MISSED_ID_BASE + k, "essay", x.wrong, x.correct, x.why, x.criterion, correction_note(x))
           for k, x in enumerate(missed) if checkable(x)], words)
    verdicts = {i: v for i, v in all_verdicts.items() if i < MISSED_ID_BASE}
    missed_verdicts = {i - MISSED_ID_BASE: v for i, v in all_verdicts.items() if i >= MISSED_ID_BASE}
    feedback = _vocabulary_fixed(feedback, fixes, example_check)

    dropped, retag, severity, rewritten, less = set(auto), {}, {}, {}, {}
    for m in feedback.mistakes:
        if m.id in auto:
            continue
        verdict = verdicts.get(m.id)
        if verdict is None:  # copied text, unclear meaning, or the filter failed: kept as listed
            if correction_note(m):
                dropped.add(m.id)
            continue
        keep, crit, sev, correct, less_common = settle(m, verdict, f"mistake {m.id}")
        if not keep:
            dropped.add(m.id)
            continue
        retag[m.id], severity[m.id], less[m.id] = crit, sev, less_common
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
    kept_missed, missed_severity, missed_less = [], [], []
    for k, x in enumerate(missed):
        verdict = missed_verdicts.get(k)
        if verdict is None:
            if not correction_note(x):
                kept_missed.append(x)
                missed_severity.append("error")
                missed_less.append(False)
            continue
        keep, crit, sev, correct, less_common = settle(x, verdict, f"missed-pass item {k}")
        if keep:
            kept_missed.append(x.model_copy(update={"criterion": crit, "correct": correct}))
            missed_severity.append(sev)
            missed_less.append(less_common)

    # The bands, from the checked mistakes (the report's own list: what the
    # filter kept, under the criterion it belongs to, slips weighted less)
    # and the strong phrases.
    checked = [SimpleNamespace(wrong=m.wrong, correct=m.correct, criterion=retag.get(m.id, m.criterion),
                               severity=severity.get(m.id, "error"), unclear=m.unclear,
                               less_common=less.get(m.id, False))
               for m in feedback.mistakes if m.id not in dropped]
    checked += [SimpleNamespace(wrong=x.wrong, correct=x.correct, criterion=x.criterion, severity=sev,
                                unclear=x.unclear, less_common=low)
                for x, sev, low in zip(kept_missed, missed_severity, missed_less)]
    for note in small_slips(checked):
        logger.info("Severity by its correction: %s", note)
    strong = [seg.text.strip() for paragraph in feedback.essay for seg in paragraph if seg.type == "good"]
    # a data "mistake" code set aside (a figure the sheet confirms or can't check, a reader's rounding)
    # is no reason for a lower Task band either (#128: "slightly above 80%")
    set_aside = [m.wrong for m in feedback.mistakes if m.id in auto and data_protected(m)] \
        + [x.wrong for x in unfiltered_missed if data_protected(x)]
    scores = await decide_scores(task_type, prompt_text, answer_text, prompt_images, sheet,
                                 mistake_evidence(answer_text, checked, strong, alternatives), visual,
                                 checked=checked, strong=strong,
                                 acceptable=[a["wrong"] for a in alternatives] + set_aside)
    if marks_only:
        return MarksOnly(scores.bands, scores.overall, scores.runs, scores.capped, flags,
                         shown_strong(answer_text, strong, checked))
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
    report.flags = flags
    if report.notes:
        logger.info("Report validation: %s", "; ".join(report.notes))
    await result_cache.put(cache_key, (scores.bands, scores.overall, feedback, report))
    return report


_WORD_COUNT = re.compile(r"\b\d+(?:\s*[-–]\s*\d+)?(?:\s+more)?[\s-]*words?\b", re.IGNORECASE)


def true_tips(feedback: WritingFeedback, check) -> tuple[WritingFeedback, list[str]]:
    """Task 1: a next-band tip whose rule or example makes a data claim the
    sheet doesn't bear out goes (#105: "Germany was about a half" - the
    student's one-third was right), with its checklist lines; the rest are
    numbered again. A word count is no data claim (#125: "Meet the 150-word
    minimum" went for its 150, under a 126-word report)."""
    bad = {i for i, p in enumerate(feedback.priorities[:3], 1)
           if p.type == "tip" and check(_WORD_COUNT.sub("", f"{p.rule} {p.example_right}"))}
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


def visual_label_text(visual: dict) -> str:
    """Every label the visual prints - titles, units, axis labels, legend, series and category names."""
    parts: list[str] = []
    for b in visual.get("blocks", []):
        parts += [b.get("title") or "", b.get("units") or "", *(b.get("axis_labels") or []),
                  *(b.get("time_points") or [])]
        parts += [x.get("label") or "" for x in b.get("legend") or []]
        parts += [v.get(k) or "" for v in b.get("values") or [] for k in ("series", "point")]
    return " | ".join(str(p) for p in parts if p)


def copies_visual_label(wrong: str, labels: str) -> bool:
    """Is the flagged text the visual's own wording, copied as printed?"""
    def norm(text: str) -> str:
        return re.sub(r"\s+", " ", canon(text).lower()).strip(" .,;:")
    w = norm(wrong)
    # whole words only: "number of student" is no copy of the label "Number of students"
    return bool(labels) and len(w) >= 4 and bool(
        re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", norm(labels)))


def uses_question_words(wrong: str, correct: str, question: str) -> bool:
    """Does a "correction" only take out words the question itself uses
    ("transportation of foods" -> "of food" when the question says "a wide
    variety of foods")? Content words only: an article or a preposition the
    question happens to contain proves nothing."""
    q = set(_WORD.findall(question.lower()))
    fixed = set(_WORD.findall(correct.lower()))
    removed = [w for w in _WORD.findall(wrong.lower()) if w not in fixed and len(w) >= 4]
    return bool(removed) and all(w in q for w in removed)
