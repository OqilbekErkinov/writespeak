"""Small AI helpers for the vocabulary bank (bot/handlers/vocabulary.py):

- translate_words: translations of saved words into the student's UI
  language, fetched only for the words about to be shown that don't have
  one yet, then stored per word (VocabularyEntry.translations).
- describe_words: turns words a student types in (English, or Uzbek/Russian
  to find the English) into full cards - meaning, example, translations.

Both run on settings.openai_model_sample with low reasoning effort - a few
hundred tokens per call - and with a short timeout, since the student is
waiting on a button tap.
"""
from __future__ import annotations

import json

from bot.config import settings
from services.ai import usage
from services.ai.openai_client import client
from services.ai.schemas import DescribedWord, DescribedWords, WordTranslations

TIMEOUT = 45  # seconds
LANG_NAMES = {"uz": "Uzbek (Latin script)", "ru": "Russian"}

_TRANSLATE_PROMPT = """You translate IELTS vocabulary for a learner whose native language is {lang}. \
For each item, give the natural {lang} equivalent of the English word or phrase in the sense its \
meaning describes - what a good bilingual dictionary would list, 1-6 words, no explanations. \
Verbs and verb phrases in the infinitive{uz_note}. Return every id exactly once."""

_DESCRIBE_PROMPT = """You help an IELTS student (B1-B2) build a vocabulary bank. For each input \
item, which is a word or short phrase in English, Uzbek or Russian:
- ok: false if it is not a real word or phrase (gibberish, a whole sentence, an offensive word); \
then fill the other fields with empty strings.
- term: the English word or phrase in dictionary form, spelling fixed, lowercase unless a proper \
noun. If the input is Uzbek or Russian, use its most useful English equivalent for IELTS.
- meaning: plain-English meaning, under 15 words.
- example: one natural English sentence using the term, on a typical IELTS Writing or Speaking topic.
- uz: Uzbek (Latin script) translation, 1-6 words (verbs in the -moq/-mak form).
- ru: Russian translation, 1-6 words.
Return one item per input, in the same order, with `input` copied exactly."""


async def translate_words(items: list[tuple[int, str, str]], lang: str) -> dict[int, str]:
    """items: (entry id, word, English meaning). Returns {entry id: translation}
    for the ones the model translated."""
    if not items or lang not in LANG_NAMES:
        return {}
    payload = [{"id": i, "word": w, "meaning": m} for i, w, m in items]
    response = await client.with_options(timeout=TIMEOUT).beta.chat.completions.parse(
        model=settings.openai_model_sample,
        messages=[
            {
                "role": "system",
                "content": _TRANSLATE_PROMPT.format(
                    lang=LANG_NAMES[lang],
                    uz_note=" (the -moq/-mak form)" if lang == "uz" else "",
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        response_format=WordTranslations,
        # openai 1.57 has no reasoning_effort argument yet (see services/ai/ocr.py).
        extra_body={"reasoning_effort": "low"},
    )
    usage.record("vocab_translate", response)
    parsed = response.choices[0].message.parsed
    wanted = {i for i, _, _ in items}
    if parsed is None:
        return {}
    return {
        item.id: item.translation.strip()[:120]
        for item in parsed.items
        if item.id in wanted and item.translation.strip()
    }


async def describe_words(words: list[str]) -> list[DescribedWord]:
    """One card per input word, in order (inputs the model skipped are left
    out)."""
    response = await client.with_options(timeout=TIMEOUT).beta.chat.completions.parse(
        model=settings.openai_model_sample,
        messages=[
            {"role": "system", "content": _DESCRIBE_PROMPT},
            {"role": "user", "content": json.dumps(words, ensure_ascii=False)},
        ],
        response_format=DescribedWords,
        extra_body={"reasoning_effort": "low"},
    )
    usage.record("vocab_describe", response)
    parsed = response.choices[0].message.parsed
    return list(parsed.words) if parsed is not None else []
