"""Nobody turned away by a test window is forgotten (the user's request,
2026-10-18: "don't lose the users"). While CHECKS_ONLY_FOR is on, everyone
else who tries a check gets quota.paused, which promises a message when
checks open again, and is remembered in a Redis set. When the window ends
(the CHECKS_ONLY_FOR line removed from .env and the bot restarted), the bot
sends each of them quota.back in their own language, once.

Each user is removed from the set right after their message, so a restart
halfway through doesn't message anyone twice. Never raises.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter

from bot.config import settings
from bot.i18n import t
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


async def notify_back(bot: Bot) -> None:
    """Started with the bot: tells everyone waiting that checks are back -
    only when no test window is on."""
    if settings.checks_only_for_ids:
        return
    try:
        waiting = [int(x) for x in await redis().smembers(WAITING_KEY)]
        if not waiting:
            return
        logger.info("Checks are open again - telling %d waiting users", len(waiting))
        sent = 0
        for user_id in waiting:
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
            await redis().srem(WAITING_KEY, user_id)
            await asyncio.sleep(SEND_PAUSE)
        logger.info("Checks-back messages sent: %d of %d", sent, len(waiting))
    except Exception:
        logger.exception("Checks-back notification failed")
