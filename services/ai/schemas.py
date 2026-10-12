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
# prompt, minus what code adds itself (task type, question, overall band,
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
    TR_or_TA: float
    CC: float
    LR: float
    GRA: float


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
    scores: CriterionScores
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


class GeneratedSample(BaseModel):
    paragraphs: list[str]


class SpeakingGradingResult(BaseModel):
    topic: str
    overall_band: float
    fluency_and_coherence: float
    lexical_resource: float
    grammatical_range_and_accuracy: float
    pronunciation: float
    annotations: list[Annotation]
    vocabulary: list[VocabularyItem]
    feedback_summary: str


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
