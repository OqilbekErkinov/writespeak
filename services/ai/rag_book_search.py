"""Text embeddings (OpenAI) for the book library and question matching.

`book_chunks` (scripts/ingest_books.py) is still filled with one row per
book sample, each scoped to one author's own answer. The reports no longer
search it: Writing model answers come from services/ai/sample_bank.py and
Speaking model answers from services/ai/speaking_answers.py.
"""
from __future__ import annotations

from bot.config import settings
from services.ai import usage
from services.ai.openai_client import client


async def embed_text(text: str) -> list[float]:
    response = await client.embeddings.create(model=settings.openai_embedding_model, input=text)
    usage.record("embedding", response)
    return response.data[0].embedding
