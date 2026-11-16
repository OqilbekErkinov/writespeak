"""The student <-> admins conversation kept in the database (SupportMessage),
for the web panel's support inbox (webapp/panel.py) - until 2026-10-18 it
lived only in the admins' Telegram group. Saved from the bot's support
handler (in), the group's answers (out, bot/handlers/crm.py) and the panel's
answers (out). Never raises: the conversation itself must not break.

An answer - from the group or the panel - marks the student's messages read,
as opening the thread in the panel does (2026-11-16: an answer from the group
left them unread, and the panel's badge kept counting them). Whether a thread
still waits for an answer is db/crud.py's support_waiting.
"""
from __future__ import annotations

import logging

from aiogram.types import Message
from sqlalchemy import update

from db.database import get_session
from db.models import SupportMessage

logger = logging.getLogger(__name__)

# The panel's «✓ Javob shart emas» (a «rahmat» needs no answer): an "out" row of this kind ends the wait -
# nothing goes to the student (webapp/panel.py, 2026-11-16).
CLOSED = "closed"


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
            if direction == "out":  # answered: the student's messages are read
                await session.execute(_read(user_id))
            await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("Support message of %s not saved: %r", user_id, e)


async def save_incoming(message: Message) -> None:
    kind, text, file_id = describe(message)
    await save(message.from_user.id, "in", kind=kind, text=text, file_id=file_id)


def _read(user_id: int):
    return (update(SupportMessage).where(SupportMessage.user_id == user_id, SupportMessage.direction == "in",
                                         SupportMessage.read.is_(False)).values(read=True))


async def mark_read(user_id: int) -> None:
    """The student's messages seen by an admin - an answer that could not be delivered (the student blocked
    the bot) was still written after reading them."""
    try:
        async with get_session() as session:
            await session.execute(_read(user_id))
            await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("Support messages of %s not marked read: %r", user_id, e)
