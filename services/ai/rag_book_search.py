"""Semantic search over `book_chunks` (pgvector) to find an authentic sample
answer from the tutor's own IELTS books for the 'Authentic Book Sample'
section — never an AI hallucination (see scripts/ingest_books.py for how the
table gets populated).

Each row is scoped to exactly one author's own essay (see ingest_books.py's
chunker), so picking the single closest-matching row by similarity is
sufficient to satisfy "pick the best one instructor's sample, never blend
two instructors' work together" — there is nothing to blend within a row.
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from bot.config import settings
from db.database import get_session
from db.models import BookChunk, BookTaskType
from services.ai.openai_client import client

logger = logging.getLogger(__name__)


async def embed_text(text: str) -> list[float]:
    response = await client.embeddings.create(model=settings.openai_embedding_model, input=text)
    return response.data[0].embedding


async def find_authentic_sample(
    topic: str, task_type: BookTaskType | str, min_band: float
) -> BookChunk | None:
    """Returns the closest topical match that is >= min_band; if none clears
    that bar, the closest match of any band; if the library has nothing at
    all for this task_type yet, None - the report then falls back to a
    general style analysis instead (see get_style_reference_samples below
    and services/ai/sample_analyzer.py's analyze_style; Revision Brief v2,
    Section 4, 2026-08-30), only showing an honest "bank is empty" note if
    that has nothing either."""
    task_type_value = task_type.value if isinstance(task_type, BookTaskType) else task_type
    query_embedding = await embed_text(topic)

    async with get_session() as session:
        stmt = (
            select(BookChunk)
            .where(BookChunk.task_type == task_type_value, BookChunk.band_level >= min_band)
            .order_by(BookChunk.embedding.cosine_distance(query_embedding))
            .limit(1)
        )
        result = (await session.execute(stmt)).scalar_one_or_none()
        if result is not None:
            return result

        stmt = (
            select(BookChunk)
            .where(BookChunk.task_type == task_type_value)
            .order_by(BookChunk.embedding.cosine_distance(query_embedding))
            .limit(1)
        )
        return (await session.execute(stmt)).scalar_one_or_none()


async def get_style_reference_samples(
    task_type: BookTaskType | str, limit: int = 3
) -> list[BookChunk]:
    """Used when find_authentic_sample finds nothing for this exact
    task_type: pulls a handful of the highest-band chunks from the same
    broad modality (Writing Task 1 + Task 2 pooled together, since general
    essay-structure technique transfers reasonably well between them;
    Speaking kept on its own, since it's too different a skill from
    Writing to usefully pool) to ground a general style analysis instead of
    one specific topic-matched excerpt. Empty list if the bank has nothing
    at all yet for that modality - the caller then shows an honest
    "bank is empty" note rather than inventing content."""
    task_type_value = task_type.value if isinstance(task_type, BookTaskType) else task_type
    modality_types = (
        [BookTaskType.writing_task1.value, BookTaskType.writing_task2.value]
        if task_type_value.startswith("writing_")
        else [BookTaskType.speaking.value]
    )
    async with get_session() as session:
        stmt = (
            select(BookChunk)
            .where(BookChunk.task_type.in_(modality_types))
            .order_by(BookChunk.band_level.desc().nulls_last())
            .limit(limit)
        )
        return list((await session.execute(stmt)).scalars().all())
