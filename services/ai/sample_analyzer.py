"""Analyzes the AUTHENTIC book sample pulled by rag_book_search.py (never the
student's own answer) so the PDF report's Sample section is genuinely useful:
useful vocabulary/grammar inside the real sample gets highlighted, plus a
short written explanation of what makes it a strong, higher-band answer.

The sample TEXT itself always comes verbatim from the tutor's own books
(services/ai/rag_book_search.py) — this module only adds AI commentary on
top of that authentic text, it never invents or rewrites the sample.

`analyze_style` (Revision Brief v2, Section 4, 2026-08-30) is the fallback
used when no single sample matches the student's topic closely enough to
quote directly (services/ai/rag_book_search.py's get_style_reference_samples
returns several samples instead of one topic match) - it describes shared
stylistic TECHNIQUES across them in general terms rather than quoting any
one of them as if it were topic-relevant.
"""
from __future__ import annotations

from bot.config import settings
from services.ai.openai_client import client
from services.ai.schemas import SampleAnalysis, StyleAnalysis

SYSTEM_PROMPT = """You are a warm, encouraging IELTS tutor. You will be shown an AUTHENTIC, \
higher-band sample answer taken directly from a real IELTS preparation book (NOT written by you \
- treat it as ground truth, do not rewrite or correct it). Your job is only to analyze it:

1. Write an encouraging overview (80-150 words) explaining what makes this sample strong overall \
- structure, range of ideas, cohesion, tone - directly addressed to the student who just wrote a \
lower-scoring answer on the same topic.
2. Pick 6-12 specific vocabulary items or grammar structures actually present in the sample text \
that are worth the student learning. For each, quote the EXACT phrase/sentence as it appears in \
the sample (do not paraphrase it), classify it as "vocabulary" or "grammar", and explain briefly \
why it's useful and how the student could reuse it in their own answers.

Do not invent content that isn't in the sample. Do not comment on anything negative in the sample \
- it is an authentic model answer, treat it as exemplary."""


async def analyze_sample(sample_text: str, task_label: str) -> SampleAnalysis:
    response = await client.beta.chat.completions.parse(
        model=settings.openai_model_grading,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"TASK TYPE: {task_label}\n\nAUTHENTIC SAMPLE ANSWER:\n{sample_text}",
            },
        ],
        response_format=SampleAnalysis,
        # No `temperature` override - see services/ai/ocr.py's comment.
    )
    result = response.choices[0].message.parsed
    if result is None:
        raise RuntimeError("Sample analysis model returned no parsable result")
    return result


STYLE_SYSTEM_PROMPT = """You are a warm, encouraging IELTS tutor. No single sample in the \
tutor's book library closely matches this student's specific topic, so instead of quoting one \
excerpt side-by-side, you'll look across several AUTHENTIC higher-band sample answers (from real \
IELTS preparation books - NOT written by you, treat them as ground truth) and describe the \
STYLISTIC TECHNIQUES they share in common, as general guidance the student can apply to their own \
writing regardless of topic - for example how they open an overview, how they sequence \
comparisons, how they vary reporting verbs, or how they structure a position or a long turn.

Do not quote or present any sentence from these samples as if it were a specific match for the \
student's topic - they are on different topics, so describe the TECHNIQUE in general terms \
instead of implying a direct parallel. Do not invent techniques that aren't actually visible \
across the samples given.

Produce:
1. A short, encouraging overview (60-120 words) noting that no single sample matched this exact \
topic, but there are still valuable techniques worth learning from the tutor's stronger sample \
answers in general.
2. 2-4 concrete, applicable stylistic tips - each one a specific, actionable technique (not vague \
praise), grounded in what the given samples actually do."""


async def analyze_style(sample_texts: list[str], task_label: str) -> StyleAnalysis:
    joined = "\n\n---\n\n".join(f"SAMPLE {i + 1}:\n{text}" for i, text in enumerate(sample_texts))
    response = await client.beta.chat.completions.parse(
        model=settings.openai_model_grading,
        messages=[
            {"role": "system", "content": STYLE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"TASK TYPE: {task_label}\n\n{joined}",
            },
        ],
        response_format=StyleAnalysis,
        # No `temperature` override - see services/ai/ocr.py's comment.
    )
    result = response.choices[0].message.parsed
    if result is None:
        raise RuntimeError("Style analysis model returned no parsable result")
    return result
