"""Model answers as Telegram voice messages (Speaking report prompt, Part 3.3;
OpenAI text-to-speech approved by the owner on 2026-10-13).

OpenAI returns Ogg/Opus for response_format="opus" - what Telegram plays as
a voice message. gpt-4o-mini-tts takes speaking instructions; the pace is
asked for there (about 140 words a minute), since `speed` is a tts-1 knob.
"""
from __future__ import annotations

import logging

from bot.config import settings
from services.ai import usage
from services.ai.openai_client import client

logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    "You are a confident, friendly IELTS candidate answering an examiner. Speak naturally at a relaxed, "
    "steady pace of about 140 words a minute, with natural pauses between sentences, clear word stress and "
    "a natural rise and fall in your voice. Neutral international English accent."
)


def enabled() -> bool:
    return bool(settings.openai_model_tts)


async def speak(text: str, words: int) -> bytes:
    """Ogg/Opus audio of `text`."""
    kwargs = dict(model=settings.openai_model_tts, voice=settings.openai_tts_voice, input=text,
                  response_format="opus")
    if not settings.openai_model_tts.startswith("tts-1"):
        kwargs["extra_body"] = {"instructions": INSTRUCTIONS}  # openai 1.57 has no `instructions` argument
    response = await client.audio.speech.create(**kwargs)
    audio = response.content
    usage.record_minutes("tts", settings.openai_model_tts, words / 140 * 60)
    return audio
