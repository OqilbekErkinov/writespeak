"""The student <-> admins conversation kept in the database (SupportMessage),
for the web panel's support inbox (webapp/panel.py) - until 2026-10-18 it
lived only in the admins' Telegram group. Saved from the bot's support
handler (in), the group's answers (out, bot/handlers/crm.py) and the panel's
answers (out). Never raises: the conversation itself must not break.
"""
from __future__ import annotations

import logging

from aiogram.types import Message

from db.database import get_session
from db.models import SupportMessage

logger = logging.getLogger(__name__)


def describe(message: Message) -> tuple[str, str | None, str | None]:
    """(kind, text, photo file id) of a Telegram message."""
    text = message.text or message.caption
    if message.photo:
        return "photo", text, message.photo[-1].file_id
    for kind in ("voice", "video_note", "video", "audio", "document", "sticker", "animation"):
        if getattr(message, kind, None) is not None:
            return kind, text, None
    return "text", text, None


async def save(user_id: int, direction: str, *, kind: str = "text", text: str | None = None,
               file_id: str | None = None, via: str = "bot", admin: str | None = None) -> None:
    try:
        async with get_session() as session:
            session.add(SupportMessage(user_id=user_id, direction=direction, kind=kind, text=text,
                                       file_id=file_id, via=via, admin=admin, read=direction == "out"))
            await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("Support message of %s not saved: %r", user_id, e)


async def save_incoming(message: Message) -> None:
    kind, text, file_id = describe(message)
    await save(message.from_user.id, "in", kind=kind, text=text, file_id=file_id)
