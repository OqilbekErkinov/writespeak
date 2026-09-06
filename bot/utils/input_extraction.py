"""Extracts usable content from any accepted Telegram message type.

`extract_text_from_message` covers the Writing module's "any format" rule
(text, photo, PDF, DOCX). `extract_audio_from_message` covers the Speaking
module's "any audio format" rule (voice, video note, audio file, forwarded
audio).
"""
from __future__ import annotations

from aiogram import Bot
from aiogram.types import Message

from bot.i18n import t
from db.models import SourceType
from services.ai.document_parser import extract_text_from_docx, extract_text_from_pdf
from services.ai.ocr import extract_text_from_image
from services.storage.file_storage import save_upload

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class UnsupportedInputError(Exception):
    """Raised when a message doesn't contain a format we accept, so the
    handler can politely ask the user to resend it."""


async def _download(bot: Bot, file_id: str) -> bytes:
    file = await bot.get_file(file_id)
    buf = await bot.download_file(file.file_path)
    return buf.read()


async def extract_text_from_message(
    message: Message, bot: Bot, user_id: int, lang: str = "uz"
) -> tuple[str, SourceType, str | None]:
    """Returns (extracted_text, source_type, saved_original_file_path_or_None)."""
    if message.text:
        return message.text.strip(), SourceType.text, None

    if message.photo:
        content = await _download(bot, message.photo[-1].file_id)  # highest resolution
        saved_path = save_upload(user_id, content, ".jpg")
        text = await extract_text_from_image(content, mime_type="image/jpeg")
        return text, SourceType.photo, saved_path

    if message.document:
        doc = message.document
        name = (doc.file_name or "").lower()
        content = await _download(bot, doc.file_id)

        if name.endswith(".pdf") or doc.mime_type == "application/pdf":
            saved_path = save_upload(user_id, content, ".pdf")
            text = await extract_text_from_pdf(content)
            return text, SourceType.pdf, saved_path

        if name.endswith(".docx") or doc.mime_type == DOCX_MIME:
            saved_path = save_upload(user_id, content, ".docx")
            text = extract_text_from_docx(content)
            return text, SourceType.docx, saved_path

        raise UnsupportedInputError(t("input.pdf_docx_only", lang))

    raise UnsupportedInputError(t("input.send_supported_format", lang))


async def extract_audio_from_message(
    message: Message, bot: Bot, user_id: int, lang: str = "uz"
) -> tuple[bytes, str, str]:
    """Returns (raw_audio_bytes, original_filename, saved_file_path). Accepts
    voice notes, video notes (round videos), audio files, and forwarded audio
    of any of the above."""
    if message.voice:
        content = await _download(bot, message.voice.file_id)
        filename = "voice.ogg"
    elif message.video_note:
        content = await _download(bot, message.video_note.file_id)
        filename = "video_note.mp4"
    elif message.audio:
        content = await _download(bot, message.audio.file_id)
        filename = message.audio.file_name or "audio.mp3"
    elif message.document and (message.document.mime_type or "").startswith(("audio/", "video/")):
        content = await _download(bot, message.document.file_id)
        filename = message.document.file_name or "audio.mp3"
    else:
        raise UnsupportedInputError(t("speaking.audio_only", lang))

    saved_path = save_upload(user_id, content, f"_{filename}")
    return content, filename, saved_path
