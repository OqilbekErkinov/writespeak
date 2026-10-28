"""Structured-output schemas for AI grading calls.

Passed as `response_format` to `client.beta.chat.completions.parse(...)` so the
OpenAI SDK validates and parses the model's JSON output into these Pydantic
models directly — no manual JSON parsing / error-prone regex needed.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

HighlightColor = Literal["green", "yellow", "red"]


class Annotation(BaseModel):
    sentence: str = Field(
        description="The exact sentence or phrase from the student's answer being annotated."
    )
    color: HighlightColor = Field(
        description="green = correct/advanced usage, yellow = acceptable but improvable OR an "
        "isolated, minor slip that doesn't block understanding, red = an error frequent or "
        "severe enough to actually interfere with meaning or fluency (per the ESL-calibrated "
        "scoring approach - a single understandable slip should be 'yellow' instead). Note: "
        "'red' is this tier's internal name for schema stability; the report renders it in a "
        "calm amber/gold, not an alarming red - see services/pdf/report_builder.py."
    )
    comment: str = Field(
        description="A short, encouraging explanation of why this got this color, and how to "
        "improve it if not green."
    )


class VocabularyItem(BaseModel):
    word_or_phrase: str
    meaning: str
    example_sentence: str = Field(
        description="An example sentence using the word/phrase, related to the topic."
    )


class WritingGradingResult(BaseModel):
    topic: str = Field(
        description="A short 3-6 word topic label for the essay/report, used later to search for "
        "a matching authentic sample answer."
    )
    overall_band: float
    task_achievement_or_response: float
    coherence_and_cohesion: float
    lexical_resource: float
    grammatical_range_and_accuracy: float
    annotations: list[Annotation]
    vocabulary: list[VocabularyItem]
    feedback_summary: str = Field(
        description="An encouraging, engaging overview of the essay's strengths and weaknesses, "
        "written directly to the student, 150-300 words."
    )


# --- Writing feedback (final report, 2026-10-12) --------------------------------
# The Feedback Coach returns this JSON - the data contract of the final report
# prompt, minus what code adds itself (the criterion bands - separate scoring
# runs since 2026-10-16 -, task type, question, overall band,
# copied-from-the-question spans, stats, progress).
# services/ai/essay_marking.py validates it against the student's original
# essay and services/pdf/report_builder.py renders it; the model never
# produces layout.

MistakeCriterion = Literal["LR", "GRA", "CC", "TR"]


class EssaySegment(BaseModel):
    type: Literal["text", "good", "error"]
    text: str = Field(
        description="Characters copied EXACTLY from the student's essay (typos included). Joining "
        "every segment's text in order must give back the original essay."
    )
    fix: Optional[str] = Field(
        description="Error segments only: the replacement for `text` when the fix is 6 words or "
        "fewer (empty string = delete it). null for longer problems (shown underlined only) and "
        "for text/good segments."
    )
    mistake_id: Optional[int] = Field(
        description="Error segments only: the id of the matching item in `mistakes`; null otherwise."
    )


class Mistake(BaseModel):
    id: int
    wrong: str = Field(
        description="Only the part that matters, copied exactly from the essay (not the whole "
        "sentence unless needed)."
    )
    correct: str = Field(
        description="The corrected version of that part, keeping the student's meaning. Put in "
        "place of `wrong`, the sentence must read correctly."
    )
    why: str = Field(description="One plain-English line, 25 words or fewer.")
    criterion: MistakeCriterion
    unclear: bool = Field(
        description="True when the student's meaning can't be determined; then `correct` is for "
        "the most likely meaning."
    )
    if_you_meant: Optional[str] = Field(
        description="Only when unclear: the most likely intended meaning, short, in English; null "
        "otherwise."
    )


class MissedMistake(BaseModel):
    """A mistake found by the second, missed-errors-only pass."""

    wrong: str = Field(description="Copied exactly from the essay, long enough to be found once.")
    correct: str
    why: str = Field(description="One plain-English line, 25 words or fewer.")
    criterion: MistakeCriterion
    unclear: bool
    if_you_meant: Optional[str]


class MissedMistakes(BaseModel):
    mistakes: list[MissedMistake]


class Priority(BaseModel):
    type: Literal["fix", "tip"] = Field(
        description='"fix": a pattern of this answer\'s own mistakes; "tip": what separates it from the next '
                    "band, when there are too few real mistakes for a fix.")
    title: str = Field(description="2-6 words naming ONE specific pattern, e.g. 'Say it with one word'.")
    rule: str = Field(description="One line.")
    example_wrong: str = Field(
        description="fix: a short piece copied exactly from the student's text; tip: an empty string.")
    example_right: str = Field(description="fix: the same piece, corrected; tip: a short example of doing it well.")
    criteria: list[MistakeCriterion]
    mistake_ids: list[int] = Field(description="fix: 2-5 ids from `mistakes` that show this pattern; tip: [].")


class Strength(BaseModel):
    text: str = Field(description="1-2 sentences quoting 1-2 of the student's strong phrases.")
    quoted_phrases: list[str] = Field(description="The quoted phrases, exactly as in the essay.")


class CriterionScores(BaseModel):
    """One Writing scoring run: whole bands only (reliability rules, 2026-10-16)."""

    TR_or_TA: int = Field(description="Task Achievement (Task 1) or Task Response (Task 2), a whole band 0-9.")
    CC: int = Field(description="Coherence & Cohesion, a whole band 0-9.")
    LR: int = Field(description="Lexical Resource, a whole band 0-9.")
    GRA: int = Field(description="Grammatical Range & Accuracy, a whole band 0-9.")


class CriterionJudgement(BaseModel):
    """One criterion of a Writing scoring run (marking guide, 2026-10-22):
    strengths first, then weaknesses, then the band - in that order, so the
    band is decided after both are listed."""

    strengths: list[str] = Field(
        description="What earns credit on THIS criterion, each with a short quote from the answer.")
    weaknesses: list[str] = Field(
        description="Real problems of THIS criterion only, each with a short quote; never style preferences.")
    band: int = Field(description="The whole band 0-9 that fits best.")
    note: str = Field(
        description="6-14 plain words for the student naming the strength or weakness that decided this band.")


class WritingScoring(BaseModel):
    TR_or_TA: CriterionJudgement = Field(description="Task Achievement (Task 1) or Task Response (Task 2).")
    CC: CriterionJudgement = Field(description="Coherence & Cohesion.")
    LR: CriterionJudgement = Field(description="Lexical Resource.")
    GRA: CriterionJudgement = Field(description="Grammatical Range & Accuracy.")


class CriterionNotes(BaseModel):
    TR_or_TA: str
    CC: str
    LR: str
    GRA: str


class VocabEntry(BaseModel):
    term: str
    meaning: str = Field(description="Plain-English meaning.")
    example: str = Field(description="An English example sentence on this topic.")


class ChecklistItem(BaseModel):
    text: str
    ref: str = Field(description='"Priority 01".."Priority 03" (only priorities you gave), or "Mistake <id>" with '
                                 "an id from `mistakes`.")


class WritingFeedback(BaseModel):
    mistakes: list[Mistake]
    essay: list[list[EssaySegment]] = Field(
        description="The student's essay as a list of paragraphs, each a list of segments."
    )
    criterion_notes: CriterionNotes
    strength: Strength
    priorities: list[Priority]
    vocabulary: list[VocabEntry]
    checklist: list[ChecklistItem]


# --- Model answer (services/ai/sample_bank.py) ------------------------------------


class StyleProfile(BaseModel):
    """One library writer's style for one task type, derived from their own
    samples only and cached in data/sample_library/styles/."""

    introduction: str = Field(
        description="How they paraphrase the question and state the thesis (Task 1: the overview)."
    )
    body_paragraphs: str = Field(description="Their body-paragraph pattern.")
    examples: str = Field(description="How they give examples and support ideas (Task 1: data).")
    linkers: list[str] = Field(description="Their typical linking words and phrases.")
    sentences: str = Field(description="Typical sentence length and complexity.")
    vocabulary_register: str = Field(description="Their vocabulary register and tone.")
    conclusion: str = Field(description="Their conclusion pattern.")
    signature_phrases: list[str] = Field(
        description="5-10 short functional phrases they habitually use (frames, not content)."
    )


Task2Subtype = Literal["opinion", "discussion", "advantages_disadvantages", "problem_solution", "two_part"]
Task1Subtype = Literal["line", "bar", "pie", "table", "map", "process", "mixed"]


class Task2Label(BaseModel):
    subtype: Task2Subtype
    topic: str = Field(description="One or two lowercase words, e.g. education, advertising, environment.")


class Task1Label(BaseModel):
    subtype: Task1Subtype
    topic: str = Field(description="One or two lowercase words, e.g. population, energy, transport.")


# --- Task 1 visual extraction (services/ai/visual_reading.py, 2026-10-22) ---------
# The visual described as data, read twice; code compares the two reads,
# checks them and computes the key features (services/ai/visual_features.py).

VisualKind = Literal["line", "bar", "pie", "table", "map", "process", "other"]


class LegendEntry(BaseModel):
    label: str
    colour: str = Field(description="Its colour or pattern exactly as drawn, telling shades apart, e.g. 'dark blue', "
                                    "'light blue', 'striped grey'.")


class DataValue(BaseModel):
    series: str = Field(description="The legend item it belongs to (e.g. 'Environmental', 'Boys'); without a "
                                    "legend, the bar's, slice's or row's label.")
    point: str = Field(description="Where it sits on the other dimension: the time point, the x-axis category, the "
                                   "panel or the table column (e.g. '2008', 'Sports', 'Law'); '' when there is only one.")
    value: Optional[float] = Field(description="The figure in the visual's units (27 for 27%, 40 for $40,000 on a "
                                               "scale in thousands); null when it is not a number or can't be read.")
    text: str = Field(description="A table cell that is words, as written (e.g. '5-star hotel'); '' for a number.")
    printed: bool = Field(description="True when this figure is written on the visual (a data label, a table cell).")
    precision: float = Field(description="0 for a printed figure; for one read off a scale, how far off a careful "
                                         "reading could be, in the same units (e.g. 1, 2 or 5 - from the gridlines).")


class MapFeatureState(BaseModel):
    time: str = Field(description="The year or map label, e.g. '1957'.")
    status: Literal["present", "absent", "changed"]
    detail: str = Field(description="What it is like then, e.g. 'small, beside the entrance'; '' if nothing to add.")


class MapFeature(BaseModel):
    name: str
    location: str = Field(description="Where it is, e.g. 'west side, beside the entrance'.")
    states: list[MapFeatureState] = Field(description="One per map, in time order.")


class ProcessStage(BaseModel):
    name: str = Field(description="A logical stage, e.g. 'energy generation', 'storage'.")
    steps: list[int] = Field(description="The step numbers (1, 2, ...) in this stage.")


class ProcessInfo(BaseModel):
    steps: list[str] = Field(description="Every step in order, one short line each.")
    numbered: bool = Field(description="True when the diagram itself numbers its steps.")
    stages: list[ProcessStage]
    kind: Literal["linear", "cyclical"]
    natural_or_manmade: Literal["natural", "man-made"]


class GroupBars(BaseModel):
    point: str = Field(description="The group (x-axis category), as in `data`.")
    bars: int = Field(description="How many bars are actually DRAWN in this group (a missing bar is not drawn).")


class VisualBlock(BaseModel):
    visual_type: VisualKind
    title: str = Field(description="The visual's title or what it shows; '' if none.")
    units: str = Field(description="e.g. '%', 'thousands of dollars', 'millions of tonnes'; '' for maps and processes.")
    time_points: list[str] = Field(description="Years or dates in order, exactly as printed, e.g. ['2008', '2014']; "
                                               "[] if none.")
    axis_labels: list[str] = Field(description="The axis tick labels exactly as printed, in order (e.g. ['1984', "
                                               "'1988', '1992'] or ['0', '20', '40']); [] for pies, tables, maps, "
                                               "processes.")
    legend: list[LegendEntry]
    data: list[DataValue] = Field(description="Every value, one per series and point, in a fixed order; [] for "
                                              "maps and processes.")
    map_features: list[MapFeature] = Field(description="Maps only: every feature; [] otherwise.")
    overall: str = Field(description="The main point of this visual in one line - a map's overall direction of "
                                     "development ('expansion and modernisation at the cost of green space'), a "
                                     "diagram's or table's main contrast, a chart's main trend.")
    process: Optional[ProcessInfo] = Field(description="Processes only; null otherwise.")
    bars_drawn: list[GroupBars] = Field(
        description="Grouped bar charts only: every group with the number of bars drawn in it; [] otherwise.")


class VisualExtraction(BaseModel):
    visuals: list[VisualBlock] = Field(description="One block per visual (a table and two pie charts = 3 blocks; "
                                                   "a chart split into panels = one block per panel).")
    unreadable: list[str] = Field(description="Everything that can't be read; [] if all is clear.")
    readability: Literal["clear", "partly_unclear", "unreadable"]
    readability_note: str = Field(description="'' when clear; otherwise, in one line, what can't be read.")
    chart_box: list[float] = Field(description="Where the visual(s) sit in the image: [left, top, right, bottom] as "
                                               "fractions 0-1 of its width and height.")
    rotation: int = Field(description="Degrees (0, 90, 180 or 270) the image must be turned clockwise so its text "
                                      "reads normally; 0 if it is upright.")


class SheetValue(BaseModel):
    """A figure after both reads and the code checks."""

    series: str
    point: str
    value: Optional[float]
    text: str
    printed: bool
    precision: float
    status: Literal["verified", "uncertain"]
    note: str = ""  # why it is uncertain


class SheetBlock(BaseModel):
    visual_type: VisualKind
    title: str
    units: str
    time_points: list[str]
    axis_labels: list[str] = []  # as printed (2026-10-26: #45's axis was read as 1987, 1990, 1993...)
    legend: list[LegendEntry]
    values: list[SheetValue]
    map_features: list[MapFeature]
    overall: str
    process: Optional[ProcessInfo]
    bars_drawn: list[GroupBars] = []  # grouped bars: how many each group has drawn


class SettledFigure(BaseModel):
    series: str = Field(description="As listed.")
    point: str = Field(description="As listed ('' when listed as '(one point)').")
    value: Optional[float] = Field(description="The figure in the visual's units; null when you can't read it.")
    how: str = Field(description="One line: the printed figure, or the axis labels around it and how far between.")


class SettleReads(BaseModel):
    """A third read of the figures the two reads disagree on (visual_reading.settle)."""

    figures: list[SettledFigure]


class FocusedRead(BaseModel):
    """One figure read again on its own, before a student's figure is called
    wrong (services/ai/visual_reading.py read_one)."""

    series: str = Field(description="The series the student's figure is about, as the visual labels it (legend item, "
                                    "or the bar's, slice's or row's label).")
    point: str = Field(description="Its point as the visual labels it (year, x-axis category, column); '' if none.")
    found: bool = Field(description="True when you can tell which figure the student means and can see it.")
    missing: bool = Field(description="True when its bar, point or cell is not drawn or is empty.")
    value: Optional[float] = Field(description="The figure in the visual's units; 0 when it is missing; null when "
                                               "you can't read it.")
    how: str = Field(description="One line: how you read it (the legend match, the gridlines around it, the "
                                 "printed label).")


class KeyFeatures(BaseModel):
    """What a Band 7-8 report would have to mention (services/ai/visual_features.py)."""

    expected_overview: list[str]
    key_comparisons: list[str]
    suggested_grouping: str


class VisualSheet(BaseModel):
    """A Task 1 visual read twice, checked and analysed - saved in the
    submission's report_data["visual"] and the source of every figure in
    the report."""

    blocks: list[SheetBlock]
    readability: Literal["clear", "partly_unclear", "unreadable"]
    readability_note: str
    unreadable: list[str]
    checks: list[str]  # what the code checks found
    features: KeyFeatures
    reads: int  # how many reads agreed on it (1 when the second failed)
    chart_key: int = 0  # the checked chart key it comes from (services/ai/chart_keys.py); 0: read for this check


class SampleShow(BaseModel):
    priority: str = Field(description='The priority number it demonstrates, "01".."03".')
    phrase: str = Field(description="3-14 words copied exactly from your answer, where it shows it.")
    note: str = Field(description="25 words at most: what the student should notice there.")


class GeneratedSample(BaseModel):
    paragraphs: list[str]
    shows: list[SampleShow] = Field(
        description="1-3 places where the answer demonstrates the student's priorities (empty if none were given)."
    )


# --- Speaking feedback (Speaking report prompt, 2026-10-13) ------------------------
# The model returns this JSON - section 1.11's data contract minus what code
# adds itself (questions, duplicates, cue cards, fillers, voice metrics,
# stats, timings, band rounding, chips, progress). services/ai/
# speaking_marking.py validates it against the transcripts.

SpeakingCriterion = Literal["LR", "GRA", "FLU", "PRON"]


class SpeakingJudgement(BaseModel):
    """One criterion of a Speaking scoring run (2026-10-29: judged, not
    counted - strengths, then weaknesses with quotes, then the band)."""

    strengths: list[str] = Field(description="What earns credit on THIS criterion, each with a short quote.")
    weaknesses: list[str] = Field(
        description="Real problems of THIS criterion only, each with a short quote from the transcript - never a "
        "self-correction, a restart, a filler or anything listed as acceptable.")
    band: int = Field(description="The whole band 0-9 that fits best.")
    note: str = Field(description="6-14 plain words for the student naming what decided this band.")


class SpeakingScoring(BaseModel):
    """One Speaking scoring run."""

    FC: SpeakingJudgement = Field(description="Fluency & Coherence.")
    LR: SpeakingJudgement = Field(description="Lexical Resource.")
    GRA: SpeakingJudgement = Field(description="Grammatical Range & Accuracy.")
    P: int = Field(description="Pronunciation, a whole band 0-9.")


class AnswerSegment(BaseModel):
    type: Literal["text", "good", "error"]
    text: str = Field(
        description="Characters copied EXACTLY from this answer's transcript (fillers, repeats and "
        "false starts included). Joining the answer's segments in order must give back its transcript."
    )
    fix: Optional[str] = Field(
        description="Error segments only: the replacement for `text` when the fix is 6 words or "
        "fewer (empty string = delete it). null for longer problems and for text/good segments."
    )
    mistake_id: Optional[int] = Field(description="Error segments only: the matching mistake id; null otherwise.")


class AnswerMarking(BaseModel):
    id: str = Field(description='The answer\'s id exactly as given, e.g. "Q1".')
    segments: list[AnswerSegment]


class SpeakingMistake(BaseModel):
    id: int
    question: str = Field(description='The id of the answer it is in, e.g. "Q2".')
    wrong: str = Field(description="Only the part that matters, copied exactly from that answer's transcript.")
    correct: str = Field(
        description="The corrected version of that part, keeping the student's meaning; put in place "
        "of `wrong`, the spoken sentence must be correct and natural."
    )
    why: str = Field(description="One plain-English line, 25 words or fewer.")
    criterion: SpeakingCriterion
    unclear: bool = Field(description="True when the student's meaning can't be determined.")
    if_you_meant: Optional[str] = Field(
        description="Only when unclear: the most likely intended meaning, short, in English; null otherwise."
    )


class SpeakingMissedMistake(BaseModel):
    question: str
    wrong: str = Field(description="Copied exactly from that answer's transcript, long enough to be found once.")
    correct: str
    why: str = Field(description="One plain-English line, 25 words or fewer.")
    criterion: SpeakingCriterion
    unclear: bool
    if_you_meant: Optional[str]


class SpeakingMissedMistakes(BaseModel):
    mistakes: list[SpeakingMissedMistake]


class SpeakingPriority(BaseModel):
    title: str = Field(description="2-6 words naming ONE specific pattern.")
    rule: str = Field(description="One line.")
    example_wrong: str = Field(description="A short piece copied exactly from the student's answers.")
    example_right: str = Field(description="The same piece, improved.")
    criteria: list[SpeakingCriterion]
    mistake_ids: list[int] = Field(description="2-5 ids from `mistakes` showing this pattern; empty when voice_ref.")
    voice_ref: bool = Field(
        description="True for a priority about the measured voice (fillers, pace, pauses, a pronunciation "
        "pattern) - it then points to the 'Your voice, measured' page instead of mistakes."
    )


class SpeakingCriterionNotes(BaseModel):
    FC: str
    LR: str
    GRA: str
    P: str


class CuePointCoverage(BaseModel):
    point: str = Field(description="The cue-card point, as written on the card.")
    covered: bool


class SpeakingFeedback(BaseModel):
    answers: list[AnswerMarking]
    mistakes: list[SpeakingMistake]
    criterion_notes: SpeakingCriterionNotes
    strength: Strength
    priorities: list[SpeakingPriority]
    vocabulary_topic: str = Field(description='3-6 words naming the topic, e.g. "dreams, ambition and careers".')
    vocabulary: list[VocabEntry]
    checklist: list[ChecklistItem]
    cue_coverage: list[CuePointCoverage] = Field(description="Part 2 only: every cue-card point; empty otherwise.")


class Verdict(BaseModel):
    id: int
    keep: bool = Field(description="False when it is not definitely an error an examiner would penalise.")
    reason: str = Field(description="Short: why it is or isn't an error.")


class Verdicts(BaseModel):
    verdicts: list[Verdict]


class SpeakingVerdict(BaseModel):
    """The Speaking mistake filter: one question for every flag (the academic
    lead, 2026-10-29) - is it actually wrong in natural spoken English?"""

    id: int
    kind: Literal["error", "slip", "alternative", "not_a_mistake"] = Field(
        description="error: wrong, or the meaning is unclear. slip: a one-off small mistake the speaker clearly "
        "controls elsewhere. alternative: acceptable as said - the suggestion is only more common. not_a_mistake: "
        "natural spoken English, a self-correction or restart, or the suggestion is no better.")
    reason: str = Field(description="Short: why it is or isn't wrong; for an alternative, why the suggestion is a "
                                    "small upgrade although both are fine.")


MistakeKind = Literal["error", "slip", "alternative", "not_a_mistake"]


class WritingVerdict(BaseModel):
    """The Writing mistake filter: one question for every flag (the academic
    lead, 2026-10-29) - is it actually wrong in standard British or American
    English? - and under which ONE criterion it belongs."""

    id: int
    kind: MistakeKind = Field(
        description="error: wrong, or the meaning is unclear. slip: a one-off small mistake the writer clearly "
        "controls elsewhere (a missing full stop, 'Overtime' for 'Over time', one article or plural slip). "
        "alternative: acceptable as written - the suggestion is only more common or more idiomatic. "
        "not_a_mistake: correct as written and the suggestion is no better, or it changes the meaning or doesn't fit.")
    criterion: MistakeCriterion = Field(
        description="The one criterion it belongs to: LR word choice, GRA grammar/punctuation, CC linking/"
        "paragraphing/logic, TR content, task coverage or data.")
    reason: str = Field(description="Short: why it is or isn't wrong; for an alternative, why the suggestion is a "
                                    "small upgrade although both are correct.")
    fixed_correct: Optional[str] = Field(
        description="Only for a kept data (TR) item whose correction disagrees with the DATA SHEET: the correction "
        "rewritten with the sheet's figures. null otherwise.")


class VocabVerdict(BaseModel):
    """A vocabulary item the report teaches, checked like a mistake is."""

    index: int
    meaning: Optional[str] = Field(description="The standard dictionary meaning in plain words when the given one is "
                                               "wrong or backwards; null when it is right.")
    example: Optional[str] = Field(description="The example rewritten when it misuses the term or, for a chart, "
                                               "disagrees with the DATA SHEET (a CHECK says how); null when it is fine.")


class SpeakingVerdicts(BaseModel):
    verdicts: list[SpeakingVerdict]
    vocabulary: list[VocabVerdict] = Field(default_factory=list, description="One per VOCABULARY item listed; [] "
                                                                             "when none are listed.")


class WritingVerdicts(BaseModel):
    verdicts: list[WritingVerdict]
    vocabulary: list[VocabVerdict] = Field(default_factory=list, description="One per VOCABULARY item listed; [] "
                                                                             "when none are listed.")


class SampleHighlight(BaseModel):
    phrase: str = Field(
        description="The exact phrase or sentence from the AUTHENTIC BOOK SAMPLE text to "
        "highlight (must be copied verbatim from the sample, not paraphrased)."
    )
    highlight_type: Literal["vocabulary", "grammar"] = Field(
        description="Whether this highlight is showcasing strong vocabulary/collocation use, or "
        "an advanced grammar structure."
    )
    note: str = Field(
        description="A short, concrete note explaining why this is band-8+ level and how the "
        "student could reuse this word/structure in their own writing or speaking."
    )


class SampleAnalysis(BaseModel):
    overview: str = Field(
        description="An encouraging 80-150 word paragraph explaining what makes this authentic "
        "sample answer strong overall, written directly to the student."
    )
    highlights: list[SampleHighlight] = Field(
        description="6-12 useful vocabulary/grammar highlights drawn from the sample text, in "
        "the order they appear."
    )


class StyleAnalysis(BaseModel):
    """Fallback for when no single sample bank entry matches the student's
    topic closely enough to quote directly (see services/ai/rag_book_search.
    py's get_style_reference_samples and services/ai/sample_analyzer.py's
    analyze_style) - general stylistic guidance drawn from several higher-
    band samples instead of one topic-matched excerpt."""

    overview: str = Field(
        description="An encouraging 60-120 word paragraph noting that no single sample matched "
        "this exact topic, but there are still valuable techniques worth learning from the "
        "tutor's stronger sample answers in general."
    )
    tips: list[str] = Field(
        description="2-4 concrete, applicable stylistic tips (structural patterns, cohesion "
        "devices, vocabulary range, sentence variety) drawn from patterns shared across the "
        "given samples - described in general terms, not presented as a quote from any one of "
        "them."
    )


# --- Vocabulary bank (services/ai/vocab_helper.py) ---------------------------------


class WordTranslation(BaseModel):
    id: int
    translation: str = Field(description="The translation, 1-6 words.")


class WordTranslations(BaseModel):
    items: list[WordTranslation]


class DescribedWord(BaseModel):
    input: str = Field(description="The student's input, exactly as given.")
    ok: bool = Field(
        description="False when the input is not a real word or phrase (gibberish, a whole sentence, "
        "an offensive word)."
    )
    term: str = Field(
        description="The English word or phrase in dictionary form, spelling fixed, lowercase unless "
        "a proper noun. For Uzbek or Russian input: its most useful English equivalent."
    )
    meaning: str = Field(description="Plain-English meaning a B1-B2 learner understands, under 15 words.")
    example: str = Field(description="One natural IELTS-style English example sentence using the term.")
    uz: str = Field(description="Uzbek (Latin script) translation, 1-6 words.")
    ru: str = Field(description="Russian translation, 1-6 words.")


class DescribedWords(BaseModel):
    words: list[DescribedWord]
