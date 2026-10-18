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
    title: str = Field(description="2-6 words naming ONE specific pattern, e.g. 'Say it with one word'.")
    rule: str = Field(description="One line.")
    example_wrong: str = Field(description="A short piece copied exactly from the student's text.")
    example_right: str = Field(description="The same piece, corrected.")
    criteria: list[MistakeCriterion]
    mistake_ids: list[int] = Field(description="2-5 ids from `mistakes` that show this pattern.")


class Strength(BaseModel):
    text: str = Field(description="1-2 sentences quoting 1-2 of the student's strong phrases.")
    quoted_phrases: list[str] = Field(description="The quoted phrases, exactly as in the essay.")


class CriterionScores(BaseModel):
    """One Writing scoring run: whole bands only (reliability rules, 2026-10-16)."""

    TR_or_TA: int = Field(description="Task Achievement (Task 1) or Task Response (Task 2), a whole band 0-9.")
    CC: int = Field(description="Coherence & Cohesion, a whole band 0-9.")
    LR: int = Field(description="Lexical Resource, a whole band 0-9.")
    GRA: int = Field(description="Grammatical Range & Accuracy, a whole band 0-9.")


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
    ref: str = Field(description='"Priority 01".."Priority 03", or "Mistake <id>" with an id from `mistakes`.')


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


class VisualFact(BaseModel):
    label: str = Field(description="The category and its series, year or stage, e.g. 'Shopping - Girls', "
                                   "'1980 - PhD Science', 'Stage 3', 'North of the river - 1995'.")
    value: str = Field(description="printed: exactly as written on the visual. estimated: your best estimate and "
                                   "the range a careful reader could defend from the scale, e.g. 'about 27% "
                                   "(25-29%)'. unclear: what can't be read - never a guessed number.")
    status: Literal["printed", "estimated", "unclear"]


class VisualReading(BaseModel):
    """A Task 1 visual read once (services/ai/visual_reading.py), so every
    part of the report uses the same figures."""

    visual_type: str = Field(description="e.g. 'bar chart', 'two pie charts', 'line graph', 'table', "
                                         "'process diagram', 'two maps'.")
    subject: str = Field(description="What it shows: what is measured, of whom or what, where and when.")
    units: str = Field(description="e.g. '%', 'millions of tonnes'; 'none' for maps and processes.")
    facts: list[VisualFact]
    main_features: list[str] = Field(description="3-6 overview-level features the visual clearly shows.")
    cautions: list[str] = Field(description="What is easy to misread: values that look equal or too close to rank, "
                                            "3D bars, stacked parts, two different scales. Empty if none.")
    readability: Literal["clear", "partly_unclear", "unreadable"]
    readability_note: str = Field(description="Empty when clear; otherwise, in one line, what can't be read.")


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


class SpeakingScores(BaseModel):
    """One scoring run: whole bands only (Speaking prompt section 1.9)."""

    FC: int = Field(description="Fluency & Coherence, a whole band 0-9.")
    LR: int = Field(description="Lexical Resource, a whole band 0-9.")
    GRA: int = Field(description="Grammatical Range & Accuracy, a whole band 0-9.")
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
