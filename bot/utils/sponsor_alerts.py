"""A group sponsor that can't pay, told - and its student told why they pay (2026-11-16).

A teacher's or center's group may pay for its students' checks (db/crud.py pick_sponsor). A sponsor whose
coins can't cover a check, or whose daily limit for the student is used up, is skipped: the student pays with
their own coins or meets the paywall. Until now nobody was told - the sponsor found out from angry students,
the student never knew why a "free" group check cost them coins. bot/utils/quota.py check_quota hands every
pick here (after_quota), and in the background:

- the payer (the teacher, or the center's owner) gets a direct message when their coins could not cover a
  check, and a warning when a check they pay for leaves them under LOW_COINS - what to do: top up in
  👤 Hisobim; the admins' group's 👨‍🏫 topic hears of it too;
- the student gets a short note when their group's sponsor could not pay ("the sponsor's coins ran out" /
  "today's group limit is used up") and this check is on their own coins (or the paywall).

Each at most once a day per person and reason (Redis flags until the next Tashkent midnight). Nothing here
ever raises into the check.
"""
from __future__ import annotations

import asyncio
import html
import logging
from datetime import timedelta

from aiogram import Bot

from bot.i18n import t
from db import crud
from db.database import get_session

logger = logging.getLogger(__name__)

LOW_COINS = 50  # a paying sponsor left under this many coins by a check is warned
FLAG_KEY = "sponsor:told:{}:{}"  # + who, why ("empty" / "low" / "student"): set until the next Tashkent midnight

_bot: Bot | None = None
_tasks: set[asyncio.Task] = set()


def use(bot: Bot) -> None:
    """The bot that sends the messages (bot/main.py, at start-up)."""
    global _bot
    _bot = bot


def after_quota(student_id: int, pick, price: int) -> None:
    """check_quota's sponsor pick (crud.SponsorPick) for a check costing `price`: whoever should hear of it is
    told in the background. Never raises."""
    try:
        low = pick.payer_id is not None and pick.balance - price < LOW_COINS
        if not (pick.skipped or low):
            return
        task = asyncio.get_running_loop().create_task(_notify(student_id, pick, price, low))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except Exception:  # noqa: BLE001
        logger.warning("Sponsor alerts for user %s not started", student_id, exc_info=True)


async def settle() -> None:
    """Waits until the messages started so far are sent (tests)."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


def _until_midnight() -> int:
    """Seconds to the next Tashkent midnight."""
    from bot.utils.crm import local_now  # noqa: PLC0415

    now = local_now()
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60, int((midnight - now).total_seconds()))


async def _first_today(who: int, why: str) -> bool:
    from bot.utils.crm import redis  # noqa: PLC0415

    return bool(await redis().set(FLAG_KEY.format(who, why), "1", nx=True, ex=_until_midnight()))


async def _notify(student_id: int, pick, price: int, low: bool) -> None:
    try:
        told: set[int] = set()
        for payer_id, group, reason in pick.skipped:
            if reason == "coins" and payer_id != student_id and payer_id not in told:
                told.add(payer_id)
                await _tell_payer(payer_id, "empty", group, price, None)
        if low and pick.payer_id != student_id:
            await _tell_payer(pick.payer_id, "low", pick.group, price, pick.balance - price)
        if pick.payer_id is None and pick.skipped:
            await _tell_student(student_id, pick.skipped[0][2])
    except Exception:  # noqa: BLE001
        logger.warning("Sponsor alerts for user %s not sent", student_id, exc_info=True)


async def _tell_payer(payer_id: int, why: str, group, price: int, left: int | None) -> None:
    """`why`: "empty" (their coins could not cover a check) or "low" (`left` coins after this one)."""
    if not await _first_today(payer_id, why):
        return
    async with get_session() as session:
        payer = await crud.get_user(session, payer_id)
    if payer is None:
        return
    lang = payer.language or "uz"
    name = html.escape(getattr(group, "name", "") or "", quote=False)
    coins = payer.coin_balance if left is None else max(0, left)
    if why == "empty":
        text = t("sponsor.payer_empty", lang, group=name, balance=coins, price=price)
    else:
        text = t("sponsor.payer_low", lang, balance=coins)
    await _send(payer_id, text)
    from bot.utils import crm  # noqa: PLC0415 - the admins' group (never raises)

    who = f"{html.escape(payer.full_name or '', quote=False)} (<code>{payer_id}</code>)"
    if why == "empty":
        line = (f"⚠️ <b>Homiyning coinlari tugadi</b>: {who}, «{name}» guruhi - balans {coins} coin. O'quvchilar "
                "endi tekshiruvni o'z coinlari bilan to'laydi.")
    else:
        line = f"🟡 <b>Homiyning coinlari kam qoldi</b>: {who}, «{name}» guruhi - {coins} coin qoldi."
    await crm.post("teachers", line)


async def _tell_student(student_id: int, reason: str) -> None:
    """`reason`: why their group's sponsor could not pay - "coins" or "limit"."""
    if not await _first_today(student_id, "student"):
        return
    async with get_session() as session:
        user = await crud.get_user(session, student_id)
    lang = (user.language if user is not None else None) or "uz"
    await _send(student_id, t("sponsor.student_coins" if reason == "coins" else "sponsor.student_limit", lang))


async def _send(chat_id: int, text: str) -> None:
    if _bot is None:
        logger.info("Sponsor alert to %s not sent: no bot set (sponsor_alerts.use)", chat_id)
        return
    try:
        await _bot.send_message(chat_id, text)
    except Exception as e:  # noqa: BLE001 - blocked the bot, Telegram away: the admins' group still hears
        logger.info("Sponsor alert to %s not sent: %r", chat_id, e)
