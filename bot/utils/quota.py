"""Shared free/paid quota gate used by Writing, Speaking and Practice entry
points before they let a user start a new check.

Model, checked in order: (1) an active subscription (bot/handlers/payments.py)
means unlimited checks, no credit spent; (2) otherwise every user gets
`settings.free_checks` free checks once, right after registering - not per
day - to spend on any mix of Writing and Speaking; a check counts once its
report is delivered (a saved submission), so an abandoned or failed attempt
doesn't use one up; (3) then a group whose teacher/center pays for its
students' checks covers it, within that group's daily limit (its assignments
are never limited - see db.crud.find_sponsor); (4) beyond that, a check is
only allowed if the user has a paid credit. The actual credit spend happens later, only once grading
succeeds (see db.crud.save_writing_submission/save_speaking_submission's
`spend_credit` param) - callers here just decide and remember *whether* this
attempt will need to spend one, and whose (`payer_id`, None = the student's
own), via the `spend_credit` and `payer_id` keys threaded through FSM state.
"""
from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import Message

from bot.config import settings
from bot.i18n import t
from bot.keyboards.payment_kb import buy_credits_kb
from db import crud
from db.database import get_session


@dataclass
class Quota:
    allowed: bool
    spend_credit: bool
    free_left: int | None = None  # free checks left, this one included, when it is a free check
    payer_id: int | None = None  # a group's sponsor paying for this check


async def free_checks_left(session, user_id: int) -> int:
    return max(0, settings.free_checks - await crud.get_total_submission_count(session, user_id))


async def check_quota(user_id: int, assignment_group_id: int | None = None) -> Quota:
    """`assignment_group_id`: the check answers that group's assignment."""
    async with get_session() as session:
        if await crud.has_active_subscription(session, user_id):
            return Quota(True, False)
        free_left = await free_checks_left(session, user_id)
        if free_left > 0:
            return Quota(True, False, free_left)
        payer_id = await crud.find_sponsor(session, user_id, assignment_group_id)
        if payer_id is not None:
            return Quota(True, True, payer_id=payer_id)
        user = await crud.get_user(session, user_id)
        if user is not None and user.credit_balance > 0:
            return Quota(True, True)
        return Quota(False, False)


def free_check_note(quota: Quota, lang: str) -> str:
    """A note shown when a check will use one of the free ones, or is paid by
    the student's teacher/center ("" otherwise)."""
    if quota.payer_id is not None:
        return t("quota.sponsored_note", lang) + "\n\n"
    if not quota.free_left:
        return ""
    return t("quota.free_note", lang, left=quota.free_left, total=settings.free_checks) + "\n\n"


async def send_paywall(message: Message, lang: str = "uz") -> None:
    await message.answer(
        t(
            "quota.paywall",
            lang,
            free_limit=settings.free_checks,
            price=format_som(settings.price_per_check),
        ),
        reply_markup=buy_credits_kb(lang),
    )


def format_som(amount: int) -> str:
    return f"{amount:,}".replace(",", " ")
