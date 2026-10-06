"""Extracts usable content from any accepted Telegram message type.

`read_text_input` is the handler-facing entry point for the "send it in any
format" steps (Writing prompt/answer, Speaking question): plain text, photos
(including albums and photos sent uncompressed as files), PDF, DOCX, TXT,
and spoken audio (voice, audio file, video, video note - transcribed). It
shows a "reading..." notice while slow extraction (OCR/transcription) runs
and tells the user what went wrong instead of failing silently.

`extract_audio_from_message` covers Speaking answers (voice, video note,
audio/video file, forwarded audio).
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message
from PIL import Image

from bot.i18n import t
from bot.utils.album import collect_album
from db.models import SourceType
from services.ai.document_parser import extract_text_from_docx, extract_text_from_pdf
from services.ai.ocr import extract_text_from_image
from services.ai.transcription import ensure_mp3, transcribe_mp3
from services.storage.file_storage import save_upload

logger = logging.getLogger(__name__)

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
# Image types the vision model accepts as-is; anything else Pillow can open
# is re-encoded to PNG first.
VISION_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


class UnsupportedInputError(Exception):
    """Raised when a message doesn't contain a format we accept, so the
    handler can politely ask the user to resend it."""


@dataclass
class ExtractedInput:
    text: str
    source: SourceType
    file_paths: list[str] = field(default_factory=list)  # saved originals, in order
    image_paths: list[str] = field(default_factory=list)  # the subset that are images


async def _download(bot: Bot, file_id: str) -> bytes:
    file = await bot.get_file(file_id)
    buf = await bot.download_file(file.file_path)
    return buf.read()


async def read_text_input(message: Message, lang: str) -> ExtractedInput | None:
    """Returns the extracted input, or None when there's nothing to act on -
    either the user has already been told why, or `message` is a non-first
    item of an album whose first item's call handles the whole album."""
    messages = await collect_album(message)
    if messages is None:
        return None

    slow = any(not m.text for m in messages)
    notice = await message.answer(t("input.reading", lang)) if slow else None
    result: ExtractedInput | None = None
    error: str | None = None
    try:
        result = await _extract_text(messages, message.bot, message.from_user.id, lang)
    except UnsupportedInputError as e:
        error = str(e)
    except TelegramBadRequest as e:
        logger.warning("Download failed for user %s: %s", message.from_user.id, e)
        error = t("input.file_too_big" if "too big" in str(e).lower() else "writing.could_not_read", lang)
    except Exception:
        logger.exception("Input extraction failed for user %s", message.from_user.id)
        error = t("writing.could_not_read", lang)

    if notice is not None:
        try:
            await notice.delete()
        except TelegramBadRequest:
            pass

    if error is None and not result.text:
        error = t("writing.could_not_read", lang)
    if error is not None:
        await message.answer(error)
        return None
    return result


async def _extract_text(
    messages: list[Message], bot: Bot, user_id: int, lang: str
) -> ExtractedInput:
    texts: list[str] = []
    result = ExtractedInput(text="", source=SourceType.text)
    for message in messages:
        text, source, saved_path, is_image = await _extract_one(message, bot, user_id, lang)
        if text:
            texts.append(text)
        if source != SourceType.text and result.source == SourceType.text:
            result.source = source  # the first non-text part decides (and triggers a confirm step)
        if saved_path:
            result.file_paths.append(saved_path)
            if is_image:
                result.image_paths.append(saved_path)
    result.text = "\n\n".join(texts).strip()
    return result


async def _extract_one(
    message: Message, bot: Bot, user_id: int, lang: str
) -> tuple[str, SourceType, str | None, bool]:
    """Returns (text, source_type, saved_original_path_or_None, is_image)."""
    if message.text:
        return message.text.strip(), SourceType.text, None, False

    caption = (message.caption or "").strip()

    if message.photo:
        content = await _download(bot, message.photo[-1].file_id)  # highest resolution
        saved_path = save_upload(user_id, content, ".jpg")
        text = await extract_text_from_image(content, mime_type="image/jpeg")
        return _join(caption, text), SourceType.photo, saved_path, True

    audio_file_id, audio_name = _audio_like(message)
    if audio_file_id:
        content = await _download(bot, audio_file_id)
        saved_path = save_upload(user_id, content, _safe_suffix(audio_name))
        text = await transcribe_mp3(await ensure_mp3(content, audio_name))
        return _join(caption, text), SourceType.audio, saved_path, False

    if message.document:
        doc = message.document
        name = (doc.file_name or "").lower()
        ext = Path(name).suffix
        mime = (doc.mime_type or "").lower()
        content = await _download(bot, doc.file_id)

        if ext == ".pdf" or mime == "application/pdf":
            saved_path = save_upload(user_id, content, ".pdf")
            return _join(caption, await extract_text_from_pdf(content)), SourceType.pdf, saved_path, False

        if ext == ".docx" or mime == DOCX_MIME:
            saved_path = save_upload(user_id, content, ".docx")
            return _join(caption, extract_text_from_docx(content)), SourceType.docx, saved_path, False

        if ext == ".txt" or mime == "text/plain":
            saved_path = save_upload(user_id, content, ".txt")
            text = content.decode("utf-8", errors="replace").strip()
            return _join(caption, text), SourceType.text, saved_path, False

        if mime.startswith("image/") or ext in IMAGE_EXTENSIONS:
            # A photo sent "as a file" (uncompressed) - common from desktop.
            image_bytes, image_mime, image_ext = _normalize_image(content, mime, lang)
            saved_path = save_upload(user_id, image_bytes, image_ext)
            text = await extract_text_from_image(image_bytes, mime_type=image_mime)
            return _join(caption, text), SourceType.photo, saved_path, True

        raise UnsupportedInputError(t("input.unsupported_file", lang))

    if caption:
        return caption, SourceType.text, None, False

    raise UnsupportedInputError(t("input.send_supported_format", lang))


def _safe_suffix(filename: str) -> str:
    """Only the extension of a user-supplied filename goes into our storage path."""
    suffix = Path(filename).suffix.lower()
    return suffix if suffix[1:].isalnum() else ".bin"


def _join(caption: str, text: str) -> str:
    return "\n\n".join(p for p in (caption, (text or "").strip()) if p)


def _audio_like(message: Message) -> tuple[str | None, str]:
    """(file_id, filename) for anything carrying speech, else (None, "")."""
    if message.voice:
        return message.voice.file_id, "voice.ogg"
    if message.video_note:
        return message.video_note.file_id, "video_note.mp4"
    if message.audio:
        return message.audio.file_id, message.audio.file_name or "audio.mp3"
    if message.video:
        return message.video.file_id, message.video.file_name or "video.mp4"
    doc = message.document
    if doc and (doc.mime_type or "").startswith(("audio/", "video/")):
        return doc.file_id, doc.file_name or "audio.mp3"
    return None, ""


def _normalize_image(content: bytes, mime: str, lang: str) -> tuple[bytes, str, str]:
    """Returns (bytes, mime, extension) in a format the vision model accepts."""
    if mime in VISION_MIMES:
        return content, mime, "." + mime.split("/")[1].replace("jpeg", "jpg")
    try:
        image = Image.open(io.BytesIO(content))
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="PNG")
    except Exception as e:
        raise UnsupportedInputError(t("input.unsupported_file", lang)) from e
    return buf.getvalue(), "image/png", ".png"


async def extract_audio_from_message(
    message: Message, bot: Bot, user_id: int, lang: str = "uz"
) -> tuple[bytes, str, str]:
    """Returns (raw_audio_bytes, original_filename, saved_file_path). Accepts
    voice notes, video notes (round videos), audio/video files, and
    forwarded audio of any of the above."""
    file_id, filename = _audio_like(message)
    if file_id is None:
        raise UnsupportedInputError(t("speaking.audio_only", lang))

    content = await _download(bot, file_id)
    saved_path = save_upload(user_id, content, _safe_suffix(filename))
    return content, filename, saved_path
