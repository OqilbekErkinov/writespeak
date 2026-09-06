"""Shared free/paid quota gate used by Writing, Speaking and Practice entry
points before they let a user start a new check.

Model, checked in order: (1) an active subscription (bot/handlers/payments.py)
means unlimited checks, no credit spent; (2) otherwise every user gets
`settings.free_daily_limit` free checks per rolling 24h (Writing + Speaking
share one pool); (3) beyond that, a check is only allowed if the user has a
paid credit. The actual credit spend happens later, only once grading
succeeds (see db.crud.save_writing_submission/save_speaking_submission's
`spend_credit` param) - callers here just decide and remember *whether* this
attempt will need to spend one, via the `spend_credit` flag threaded through
FSM state.
"""
from __future__ import annotations

from aiogram.types import Message

from bot.config import settings
from bot.i18n import t
from bot.keyboards.payment_kb import buy_credits_kb
from db import crud
from db.database import get_session


async def check_quota(user_id: int) -> tuple[bool, bool]:
    """Returns (allowed, spend_credit)."""
    async with get_session() as session:
        if await crud.has_active_subscription(session, user_id):
            return True, False
        used = await crud.get_daily_submission_count(session, user_id)
        if used < settings.free_daily_limit:
            return True, False
        user = await crud.get_user(session, user_id)
        if user is not None and user.credit_balance > 0:
            return True, True
        return False, False


async def send_paywall(message: Message, lang: str = "uz") -> None:
    await message.answer(
        t(
            "quota.paywall",
            lang,
            free_limit=settings.free_daily_limit,
            price=format_som(settings.price_per_check),
        ),
        reply_markup=buy_credits_kb(lang),
    )


def format_som(amount: int) -> str:
    return f"{amount:,}".replace(",", " ")
