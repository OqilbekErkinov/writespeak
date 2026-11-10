"""Uzbek meanings for the report's "Useful vocabulary" table (the user,
2026-11-10: "some beginners may not understand" an English-only meaning).

One small call to the cheap model after the report is final, run beside the
model answer, so a check takes no longer. It never touches the marking: the
feedback call and its schema are unchanged, and a failure only leaves the
table in English, as before.
"""
from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from bot.config import settings
from services.ai import usage
from services.ai.openai_client import client

logger = logging.getLogger(__name__)

MAX_TOKENS = 4000

SYSTEM = """You help Uzbek learners of English (IELTS, about B1-B2) read a vocabulary list.
For every item give its meaning in Uzbek, in the Latin alphabet (write o' and g' with a plain apostrophe):
a short dictionary-style gloss of 1-6 words for the sense the example uses - the way a good English-Uzbek
dictionary would put it, not a word-for-word translation of the English definition. Natural, standard Uzbek;
never leave English words in it, never add notes. Keep the order: exactly one meaning per item."""


class UzMeanings(BaseModel):
    meanings: list[str] = Field(description="One Uzbek meaning per item, in the items' order.")


async def uzbek_meanings(items) -> list[str]:
    """One Uzbek meaning per vocabulary item (each with .term, .meaning and
    .example), "" where none came back. Never raises."""
    items = list(items or [])
    if not items:
        return []
    listed = "\n".join(f"{i}. {v.term} - {v.meaning} (e.g. {v.example})" for i, v in enumerate(items, 1))
    try:
        response = await client.beta.chat.completions.parse(
            model=settings.openai_model_sample,
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": listed}],
            response_format=UzMeanings,
            reasoning_effort="low",
            max_completion_tokens=MAX_TOKENS,
        )
        usage.record("vocab_uz", response)
        parsed = response.choices[0].message.parsed
        meanings = [m.strip() for m in (parsed.meanings if parsed else [])]
    except Exception:
        logger.exception("Uzbek meanings for the vocabulary failed - the table stays in English")
        return [""] * len(items)
    if len(meanings) != len(items):
        logger.warning("Uzbek meanings: %d for %d items - left out", len(meanings), len(items))
        return [""] * len(items)
    return meanings
