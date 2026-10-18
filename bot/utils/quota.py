"""Shared paid-check gate used by Writing, Speaking, Practice and group
assignment entry points before they let a user start a new check.

Every check costs its own number of coins (bot/utils/coins.py). Checked in
order: (0) a test window (bot/utils/runtime.py: switched from the web panel,
or CHECKS_ONLY_FOR in .env) lets only the listed users and the admins
check, free of charge; (1) a running unlimited
subscription (no longer sold since 2026-10-17) makes it free; (2) a group
whose teacher/center pays for its students' checks covers it, within that
group's daily limit (its assignments are never limited - see
db.crud.find_sponsor); (3) otherwise the student's own coins must cover the
price. The coins are only spent later, once grading succeeds and the
report is saved (db.crud.save_writing_submission/save_speaking_submission's
`coins`) - so an abandoned or failed attempt costs nothing. Callers here
just decide and remember *whether* this attempt is paid, and by whom
(`payer_id`, None = the student), via the `spend_credit` and `payer_id`
keys threaded through FSM state; the price itself is looked up again from
the check's kind when it is saved (check_price).
"""
from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import Message

from bot.config import settings
from bot.i18n import t
from bot.keyboards.payment_kb import buy_coins_kb
from bot.utils import runtime
from bot.utils.coins import check_coins
from db import crud
from db.database import get_session
from db.models import FULL_TEST_CHECKS


@dataclass
class Quota:
    allowed: bool
    spend_credit: bool  # the check is paid (by the student's or the payer's coins)
    payer_id: int | None = None  # a group's sponsor paying for this check
    price: int = 0  # coins this check costs
    balance: int | None = None  # the student's coins, when they pay themselves


async def check_quota(user_id: int, kind: str, assignment_group_id: int | None = None) -> Quota:
    """`kind`: what is being checked - a bot/utils/coins.py CHECK_COINS key
    ("writing_task2", "speaking_full", ...). `assignment_group_id`: the check
    answers that group's assignment."""
    price = check_coins(kind)
    if await runtime.window_on():
        # Test window: the testers check free of charge, nobody else at all.
        # Nothing is changed in the database, so ending it restores everyone.
        return Quota(not await runtime.checks_paused_for(user_id), False, price=price)
    async with get_session() as session:
        if await crud.has_active_subscription(session, user_id):
            return Quota(True, False, price=price)
        checks = FULL_TEST_CHECKS if kind == "speaking_full" else 1
        payer_id = await crud.find_sponsor(session, user_id, assignment_group_id, coins=price, checks=checks)
        if payer_id is not None:
            return Quota(True, True, payer_id=payer_id, price=price)
        user = await crud.get_user(session, user_id)
        balance = user.coin_balance if user is not None else 0
        return Quota(balance >= price, balance >= price, price=price, balance=balance)


def check_price(data: dict, kind: str) -> int:
    """Coins a check that is about to be saved costs (0 unless it was started
    as a paid one - FSM data's "spend_credit")."""
    return check_coins(kind) if data.get("spend_credit") else 0


async def payment_of(user_id: int, coins: int, payer_id: int | None) -> dict:
    """How a check that is about to be saved is paid, for its report_data
    ("paid_with") and the admins' group: {"mode": "sponsor" | "coins" |
    "test" | "subscription" | "free", "coins", "payer_id"}."""
    if coins and payer_id:
        return {"mode": "sponsor", "coins": coins, "payer_id": payer_id}
    if coins:
        return {"mode": "coins", "coins": coins}
    if await runtime.window_on():
        return {"mode": "test", "coins": 0}
    async with get_session() as session:
        if await crud.has_active_subscription(session, user_id):
            return {"mode": "subscription", "coins": 0}
    return {"mode": "free", "coins": 0}


def free_check_note(quota: Quota, lang: str) -> str:
    """A note shown as a check starts: who pays and what it costs ("" when
    it is free)."""
    if quota.payer_id is not None:
        return t("quota.sponsored_note", lang) + "\n\n"
    if not quota.spend_credit:
        return ""
    return t("quota.price_note", lang, price=quota.price, left=(quota.balance or 0) - quota.price) + "\n\n"


async def send_paywall(message: Message, lang: str = "uz", quota: Quota | None = None) -> None:
    if await runtime.checks_paused_for(message.chat.id):
        await message.answer(t("quota.paused", lang))
        # It promises a message when checks are back: bot/utils/checks_back.py.
        from bot.utils.checks_back import remember

        await remember(message.chat.id)
        return
    if quota is not None and quota.balance is not None:
        text = t("quota.paywall", lang, price=quota.price, balance=quota.balance)
    else:
        text = t("quota.paywall_generic", lang)
    await message.answer(text + "\n\n" + price_list(lang), reply_markup=buy_coins_kb(lang))


def price_list(lang: str) -> str:
    """What each check costs, for the paywall, the account screen and /start."""
    return t(
        "quota.price_list",
        lang,
        w1=check_coins("writing_task1"),
        w2=check_coins("writing_task2"),
        s1=check_coins("speaking_part1"),
        s2=check_coins("speaking_part2"),
        s3=check_coins("speaking_part3"),
        full=check_coins("speaking_full"),
        coin=format_som(settings.coin_price),
    )


def format_som(amount: int) -> str:
    return f"{amount:,}".replace(",", " ")
