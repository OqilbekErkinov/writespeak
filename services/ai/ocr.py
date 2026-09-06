"""Image / handwriting -> text, via GPT-4o vision.

Used directly for photo uploads, and as a fallback by document_parser.py for
scanned (image-only) PDF pages.
"""
from __future__ import annotations

import base64

from bot.config import settings
from services.ai.openai_client import client

OCR_SYSTEM_PROMPT = (
    "You are a precise OCR engine specialized in reading both handwritten and typed English "
    "IELTS answers. Transcribe the text in the image EXACTLY as written, including the "
    "student's own spelling, grammar, and punctuation mistakes — do NOT correct, paraphrase, "
    "summarize, or omit anything, since this text will be graded as-is. If a word is truly "
    "illegible, write [illegible] in its place. Output ONLY the transcribed text — no preamble, "
    "no commentary, no markdown formatting."
)


async def extract_text_from_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    """OCR a single image (photo of handwriting, or a rasterized PDF page)."""
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    response = await client.chat.completions.create(
        model=settings.openai_model_vision,
        messages=[
            {"role": "system", "content": OCR_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Transcribe this image."},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
                ],
            },
        ],
        # No `temperature` override: gpt-5-family models only accept the
        # default value (1) and reject any other, per a live 400 test on
        # 2026-08-28 - see bot/config.py's model comment.
    )
    return (response.choices[0].message.content or "").strip()
