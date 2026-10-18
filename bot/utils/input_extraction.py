"""Extracts usable content from any accepted Telegram message type.

`read_text_input` is the handler-facing entry point for the "send it in any
format" steps (Writing prompt/answer, Speaking question): plain text, photos
(including albums and photos sent uncompressed as files), PDF, DOCX, TXT,
and spoken audio (voice, audio file, video, video note - transcribed). It
shows a "reading..." notice while slow extraction (OCR/transcription) runs
and tells the user what went wrong instead of failing silently.

Every image - a photo, or a picture sent as a file - is checked by its
bytes and prepared for the vision model before it is saved or read
(services/storage/file_storage.py's prepare_vision_image): a corrupt file, a
format we can't open (HEIC) or a thumbnail gets a clear message here, not a
failed check later.

`extract_audio_from_message` covers Speaking answers (voice, video note,
audio/video file, forwarded audio).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from bot.i18n import t
from bot.utils.album import collect_album
from db.models import SourceType
from services.ai import usage
from services.ai.document_parser import extract_text_from_docx, extract_text_from_pdf
from services.ai.ocr import extract_text_from_image
from services.ai.openai_client import is_image_error
from services.ai.transcription import ensure_mp3, transcribe_mp3
from services.storage.file_storage import BadImage, prepare_vision_image, save_upload

logger = logging.getLogger(__name__)

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
# Files taken for pictures (HEIC too: it is told to come as a photo instead).
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif"}


class UnsupportedInputError(Exception):
    """Raised when a message doesn't contain a format we accept, so the
    handler can politely ask the user to resend it."""


@dataclass
class ExtractedInput:
    text: str
    source: SourceType
    file_paths: list[str] = field(default_factory=list)  # saved originals, in order
    image_paths: list[str] = field(default_factory=list)  # the subset that are images
    cost: dict | None = None  # reading it with OpenAI (usage.summary), added to the check's cost


async def _download(bot: Bot, file_id: str) -> bytes:
    file = await bot.get_file(file_id)
    buf = await bot.download_file(file.file_path)
    return buf.read()


async def read_text_input(message: Message, lang: str, ocr_mode: str = "verbatim") -> ExtractedInput | None:
    """Returns the extracted input, or None when there's nothing to act on -
    either the user has already been told why, or `message` is a non-first
    item of an album whose first item's call handles the whole album.
    `ocr_mode` (services/ai/ocr.py): how photos are read - "speaking_questions"
    keeps only the questions of a screenshot."""
    messages = await collect_album(message)
    if messages is None:
        return None

    slow = any(not m.text for m in messages)
    notice = await message.answer(t("input.reading", lang)) if slow else None
    result: ExtractedInput | None = None
    error: str | None = None
    try:
        with usage.track(f"input user={message.from_user.id}") as calls:
            result = await _extract_text(messages, message.bot, message.from_user.id, lang, ocr_mode)
        if calls:
            result.cost = usage.summary(calls)
    except UnsupportedInputError as e:
        error = str(e)
    except TelegramBadRequest as e:
        logger.warning("Download failed for user %s: %s", message.from_user.id, e)
        error = t("input.file_too_big" if "too big" in str(e).lower() else "writing.could_not_read", lang)
    except Exception as e:
        if is_image_error(e):  # OpenAI couldn't open the picture: the sender can fix that
            logger.warning("The vision model turned an image of user %s down: %s", message.from_user.id, e)
            error = t("input.bad_image", lang)
        else:
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


async def read_images(message: Message, lang: str) -> list[str] | None:
    """Saves the photos (or images sent as files) of `message` or its album
    without reading their text - a Task 1 chart sent after a typed question.
    None when there's nothing to act on: a non-first album item, or no image
    (the user has been told)."""
    messages = await collect_album(message)
    if messages is None:
        return None
    paths: list[str] = []
    try:
        for m in messages:
            if m.photo:
                content = await _download(m.bot, m.photo[-1].file_id)  # highest resolution
            elif m.document is not None and _is_image_document(m.document):
                content = await _download(m.bot, m.document.file_id)
            else:
                continue
            image_bytes, _, image_ext = _prepare_image(content, lang)
            paths.append(save_upload(m.from_user.id, image_bytes, image_ext))
    except UnsupportedInputError as e:
        await message.answer(str(e))
        return None
    except TelegramBadRequest as e:
        logger.warning("Chart download failed for user %s: %s", message.from_user.id, e)
        await message.answer(t("input.file_too_big" if "too big" in str(e).lower() else "writing.could_not_read", lang))
        return None
    if not paths:
        await message.answer(t("writing.chart_photo_only", lang))
        return None
    return paths


async def _extract_text(
    messages: list[Message], bot: Bot, user_id: int, lang: str, ocr_mode: str = "verbatim"
) -> ExtractedInput:
    texts: list[str] = []
    result = ExtractedInput(text="", source=SourceType.text)
    for message in messages:
        text, source, saved_path, is_image = await _extract_one(message, bot, user_id, lang, ocr_mode)
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
    message: Message, bot: Bot, user_id: int, lang: str, ocr_mode: str = "verbatim"
) -> tuple[str, SourceType, str | None, bool]:
    """Returns (text, source_type, saved_original_path_or_None, is_image)."""
    if message.text:
        return message.text.strip(), SourceType.text, None, False

    caption = (message.caption or "").strip()

    if message.photo:
        content = await _download(bot, message.photo[-1].file_id)  # highest resolution
        image_bytes, image_mime, image_ext = _prepare_image(content, lang)
        saved_path = save_upload(user_id, image_bytes, image_ext)
        text = await extract_text_from_image(image_bytes, mime_type=image_mime, mode=ocr_mode)
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

        if _is_image_document(doc):
            # A photo sent "as a file" (uncompressed) - common from desktop.
            image_bytes, image_mime, image_ext = _prepare_image(content, lang)
            saved_path = save_upload(user_id, image_bytes, image_ext)
            text = await extract_text_from_image(image_bytes, mime_type=image_mime, mode=ocr_mode)
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


def _is_image_document(doc) -> bool:
    mime = (doc.mime_type or "").lower()
    return mime.startswith("image/") or Path((doc.file_name or "").lower()).suffix in IMAGE_EXTENSIONS


def _prepare_image(content: bytes, lang: str) -> tuple[bytes, str, str]:
    """(bytes, mime, extension) the vision model accepts; a picture it can't
    take is told to the sender in their language."""
    try:
        return prepare_vision_image(content)
    except BadImage as e:
        logger.info("Image refused (%s), %d bytes", e.reason, len(content))
        raise UnsupportedInputError(t("input.image_too_small" if e.reason == "too_small" else "input.bad_image", lang)) from e


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
