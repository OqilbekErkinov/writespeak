"""Structured-output schemas for AI grading calls.

Passed as `response_format` to `client.beta.chat.completions.parse(...)` so the
OpenAI SDK validates and parses the model's JSON output into these Pydantic
models directly — no manual JSON parsing / error-prone regex needed.
"""
from __future__ import annotations

from typing import Literal

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
