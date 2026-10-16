"""Daily "words to review" reminder for the vocabulary bank (bot/handlers/
vocabulary.py), run as a background task of the bot process (bot/main.py),
since it sends with the bot itself.

Once a day, during settings.vocab_reminder_hour (Tashkent time), students
with at least MIN_DUE words due who haven't reviewed yet that day get one
message with a button that starts the review. Only students who reviewed in
the last ACTIVE_DAYS days are reminded every day; one who never has gets a
single first reminder, so nobody who ignores the feature is nagged daily
(see db.crud.get_vocab_reminder_targets). Students switch it off/on from the
Lug'atim menu or the reminder itself; it is switched off for anyone who has
blocked the bot.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

from bot.config import settings
from bot.i18n import t
from bot.keyboards.vocab_kb import reminder_kb
from db import crud
from db.database import get_session

logger = logging.getLogger(__name__)

CHECK_EVERY = 300  # seconds
MIN_DUE = 5
ACTIVE_DAYS = 14
SEND_PAUSE = 0.05  # seconds between messages - well under Telegram's 30/s
_TASHKENT = timedelta(hours=5)  # no DST in Uzbekistan


async def run_vocab_reminders(bot: Bot) -> None:
    if not 0 <= settings.vocab_reminder_hour <= 23:
        logger.info("Vocabulary reminders are off (VOCAB_REMINDER_HOUR=%s)", settings.vocab_reminder_hour)
        return
    while True:
        try:
            if (datetime.now(timezone.utc) + _TASHKENT).hour == settings.vocab_reminder_hour:
                await send_due_reminders(bot)
        except Exception:
            logger.exception("Sending vocabulary reminders failed")
        await asyncio.sleep(CHECK_EVERY)


async def send_due_reminders(bot: Bot) -> int:
    today = (datetime.now(timezone.utc) + _TASHKENT).date()
    async with get_session() as session:
        targets = await crud.get_vocab_reminder_targets(session, min_due=MIN_DUE, active_days=ACTIVE_DAYS)
        # Marked before sending: if the bot restarts halfway, nobody gets two.
        await crud.mark_vocab_reminded(session, [user_id for user_id, _, _ in targets], today)

    sent, blocked = 0, []
    for user_id, lang, due in targets:
        result = await _send(bot, user_id, lang, due)
        if result is None:
            blocked.append(user_id)
        elif result:
            sent += 1
        await asyncio.sleep(SEND_PAUSE)

    if blocked:
        async with get_session() as session:
            for user_id in blocked:
                await crud.set_vocab_reminders(session, user_id, False)
    if targets:
        logger.info(
            "Vocabulary reminders: %d sent, %d blocked, %d failed",
            sent, len(blocked), len(targets) - sent - len(blocked),
        )
    return sent


async def _send(bot: Bot, user_id: int, lang: str, due: int, retry: bool = True) -> bool | None:
    """True = sent, False = failed, None = the user has blocked the bot."""
    try:
        await bot.send_message(user_id, t("vocab.reminder", lang, due=due), reply_markup=reminder_kb(lang, due))
        return True
    except TelegramRetryAfter as e:
        if not retry:
            return False
        await asyncio.sleep(e.retry_after)
        return await _send(bot, user_id, lang, due, retry=False)
    except TelegramForbiddenError as e:
        if "blocked" in str(e).lower():
            return None
        # "can't initiate conversation": hasn't opened this bot yet - since the
        # move to a new bot, everyone until they press Start there. Not a "no".
        logger.info("Vocabulary reminder to %s not sent: %s", user_id, e)
        return False
    except TelegramBadRequest as e:
        if "chat not found" in str(e).lower():
            logger.info("Vocabulary reminder to %s not sent: %s", user_id, e)
            return False
        logger.warning("Vocabulary reminder to %s failed: %s", user_id, e)
        return False
    except Exception:
        logger.exception("Vocabulary reminder to %s failed", user_id)
        return False
