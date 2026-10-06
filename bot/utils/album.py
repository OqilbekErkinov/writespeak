"""Folds a Telegram album (several photos/files sent together) into one
input. Telegram delivers each album item as its own message sharing a
`media_group_id`; aiogram dispatches them concurrently, so the first one to
arrive waits briefly for its siblings and then handles them all, while the
rest return immediately.
"""
from __future__ import annotations

import asyncio

from aiogram.types import Message

ALBUM_WAIT_SECONDS = 1.5

_pending: dict[tuple[int, str], list[Message]] = {}


async def collect_album(message: Message) -> list[Message] | None:
    """Returns every message of `message`'s album (just `[message]` if it
    isn't part of one) to exactly one caller - None to all the others, which
    should then do nothing."""
    if not message.media_group_id:
        return [message]

    key = (message.chat.id, message.media_group_id)
    if key in _pending:
        _pending[key].append(message)
        return None

    _pending[key] = [message]
    await asyncio.sleep(ALBUM_WAIT_SECONDS)
    return sorted(_pending.pop(key), key=lambda m: m.message_id)
