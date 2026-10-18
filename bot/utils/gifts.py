"""Coin gifts (since 2026-10-18): a student buys a coin pack for someone else
(Hisobim -> "🎁 Sovg'a qilish", bot/handlers/payments.py); once an admin
approves the payment, they get a one-time link t.me/<bot>?start=gift_<code>
(db.crud.decide_payment_request makes the CoinGift) to send to a friend.

Whoever opens the link first - not the buyer - gets the coins
(db.crud.claim_gift). A user who is already in the bot gets them at once
(bot/handlers/start.py); a brand-new one picks a language first, so the
code waits in Redis and is claimed when onboarding ends
(bot/handlers/language.py). Both sides are told, each in their own language.
"""
from __future__ import annotations

import html
import logging

from aiogram import Bot
from aiogram.types import Message

from bot.config import settings
from bot.i18n import t
from bot.utils import crm_events
from bot.utils.crm import redis
from db import crud
from db.database import get_session

logger = logging.getLogger(__name__)

PAYLOAD_PREFIX = "gift_"
PENDING_KEY = "gift:pending:{user_id}"  # a new user's gift code, until onboarding ends
PENDING_TTL = 7 * 24 * 3600


def gift_link(code: str) -> str:
    return f"https://t.me/{settings.telegram_bot_username}?start={PAYLOAD_PREFIX}{code}"


def gift_code(args: str | None) -> str | None:
    """The code of a /start gift_<code> payload, else None."""
    if args and args.startswith(PAYLOAD_PREFIX):
        return args.removeprefix(PAYLOAD_PREFIX).strip() or None
    return None


async def remember_for_onboarding(user_id: int, code: str) -> None:
    try:
        await redis().set(PENDING_KEY.format(user_id=user_id), code, ex=PENDING_TTL)
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not keep gift %s for new user %s: %r", code, user_id, e)


async def pop_onboarding_code(user_id: int) -> str | None:
    key = PENDING_KEY.format(user_id=user_id)
    try:
        code = await redis().get(key)
        if code:
            await redis().delete(key)
        return code
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not read the pending gift of %s: %r", user_id, e)
        return None


async def claim(message: Message, bot: Bot, code: str, lang: str) -> None:
    """Claims gift `code` for the chat's user and tells them and the buyer."""
    user_id = message.chat.id
    async with get_session() as session:
        result = await crud.claim_gift(session, code, user_id)
        claimer = await crud.get_user(session, user_id)
    if result.status == "own":
        await message.answer(t("gift.own", lang))
        await message.answer(gift_link(code), disable_web_page_preview=True)
        return
    if result.status != "claimed":
        await message.answer(t("gift.taken" if result.status == "taken" else "gift.missing", lang))
        return
    buyer = result.buyer
    buyer_name = html.escape((buyer.full_name if buyer else "") or "Bandzilla", quote=False)
    await message.answer(t("gift.received", lang, coins=result.gift.coins, name=buyer_name, balance=result.balance))
    if buyer is not None:
        claimer_name = html.escape((claimer.full_name if claimer else "") or str(user_id), quote=False)
        try:
            await bot.send_message(buyer.id, t("gift.claimed_buyer", buyer.language, name=claimer_name,
                                               coins=result.gift.coins))
        except Exception as e:  # noqa: BLE001 - the buyer blocked the bot, ...
            logger.info("Gift-claimed message to buyer %s not sent: %r", buyer.id, e)
    await crm_events.gift_claimed(result.gift.id)
