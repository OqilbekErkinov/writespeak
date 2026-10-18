"""Nobody turned away by a test window is forgotten (the user's request,
2026-10-18: "don't lose the users"). While the window is on
(bot/utils/runtime.py), everyone else who tries a check gets quota.paused,
which promises a message when checks open again, and is remembered in a
Redis set. When the window ends - switched off in the web panel, or the
CHECKS_ONLY_FOR line removed from .env and the bot restarted - each of them
gets quota.back in their own language, once.

Users are taken off the set one at a time (SPOP) before their message, so
the bot's start and the panel's switch running together, or a restart
halfway through, never message anyone twice. Never raises.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter

from bot.i18n import t
from bot.utils import runtime
from bot.utils.crm import redis
from db import crud
from db.database import get_session

logger = logging.getLogger(__name__)

WAITING_KEY = "checks:waiting"
SEND_PAUSE = 0.1  # seconds between messages - well under Telegram's limits


async def remember(user_id: int) -> None:
    try:
        await redis().sadd(WAITING_KEY, user_id)
    except Exception as e:  # noqa: BLE001
        logger.info("Could not remember %s for the checks-back message: %r", user_id, e)


async def waiting_count() -> int:
    try:
        return int(await redis().scard(WAITING_KEY) or 0)
    except Exception:  # noqa: BLE001
        return 0


async def notify_back(bot: Bot) -> int:
    """Tells everyone waiting that checks are back - only when no test
    window is on. Started with the bot, and by the panel's off switch.
    Returns how many got the message."""
    if await runtime.window_on():
        return 0
    sent = tried = 0
    try:
        while True:
            raw = await redis().spop(WAITING_KEY)
            if raw is None:
                break
            user_id = int(raw)
            tried += 1
            async with get_session() as session:
                user = await crud.get_user(session, user_id)
            text = t("quota.back", user.language if user else None)
            try:
                try:
                    await bot.send_message(user_id, text)
                except TelegramRetryAfter as e:  # flood control: wait as Telegram says, then once more
                    await asyncio.sleep(e.retry_after)
                    await bot.send_message(user_id, text)
                sent += 1
            except Exception as e:  # noqa: BLE001 - blocked the bot, deleted account, ...
                logger.info("Checks-back message to %s not sent: %r", user_id, e)
            await asyncio.sleep(SEND_PAUSE)
        if tried:
            logger.info("Checks are open again - checks-back messages sent: %d of %d", sent, tried)
    except Exception:
        logger.exception("Checks-back notification failed")
    return sent
