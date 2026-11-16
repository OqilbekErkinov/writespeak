"""Re-engagement reminders, and the coins left under a report (2026-11-16, the growth pass before the launch - the
owner approved automatic reminders). A background task of the bot process (bot/main.py), like the vocabulary
reminders (bot/utils/vocab_reminders.py), whose pattern it follows: marked before sending, switched off on a
real "blocked".

Four steps, each sent to a student at most once (its Redis flag is set before the message goes):
- free1 / free2: settings.nudge_signup_first_hours / nudge_signup_second_hours after signup, to a student who has
  never had a check and still has the coins for one (the welcome gift): "your free check is waiting";
- lapsed1 / lapsed2: settings.nudge_lapsed_first_days / nudge_lapsed_second_days after the student's last check:
  their weakest criterion (bot/handlers/account.py weakest_area, the Hisobim line), "send a new answer" - and,
  when their coins are short of a check (and no sponsor pays for it), the top-up button and the packs. A new
  check starts the two over: their flags carry the last check's time.
Under each: a check started right away (Writing Task 2, Speaking Part 1), a ready question, "🔕 off".

A step whose time came while the bot was not sending (quiet hours, a deploy) may still go up to
settings.nudge_late_hours later, never after - and never once the next step is due: nobody gets the "24 hours"
message on day 4, or two steps at once. Only students (teachers, center owners, admins and accounts blocked in
the panel are left out), only between settings.nudge_from_hour and nudge_to_hour Tashkent time, never in a test
window or while checks are paused (bot/utils/runtime.py, balance.py) - nor for nudge_min_gap_hours after they
open again, when the launch message and "checks are back" go out (bot/launch.py, checks_back.py) - two
reminders to one student at least settings.nudge_min_gap_hours apart, and a student using the bot right now (seen in the last
nudge_skip_active_minutes, bot/utils/activity.py) is left for a later round. At most settings.nudge_per_second
messages a second. A student who blocked the bot is not written to again until the bot sees them back.

The student switches the reminders off under any of them ("🔕 Eslatmalarni o'chirish") and back on in Hisobim
(bot/handlers/account.py) - a Redis set, no migration.

Redis: nudge:optout (set of user ids), nudge:blocked (hash user id -> when they blocked), nudge:sent:<step>:<user
id>[:<last check, unix>] (the once-only flags, FLAG_TTL), nudge:gap:<user id> (the spacing, nudge_min_gap_hours),
nudge:closed_at (when checks were last seen closed).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup, Message
from sqlalchemy import exists, func, select, union_all

from bot.config import settings
from bot.i18n import t
from bot.keyboards.entry_kb import nudge_kb, with_topup
from bot.utils import activity, runtime
from bot.utils.coins import COIN_PACKS, TYPICAL_CHECK, check_coins
from bot.utils.crm import redis
from db import crud
from db.database import get_session
from db.models import Center, SpeakingSubmission, User, UserRole, UserStatus, WritingSubmission

logger = logging.getLogger(__name__)

OPTOUT_KEY = "nudge:optout"
BLOCKED_KEY = "nudge:blocked"
FLAG_KEY = "nudge:sent:{step}:{uid}"
GAP_KEY = "nudge:gap:{uid}"
CLOSED_KEY = "nudge:closed_at"  # when checks were last seen closed (a test window, a pause), unix time
CLOSED_TTL = 7 * 24 * 3600
FLAG_TTL = 60 * 24 * 3600  # longer than any step's window, so a flag never expires while its step could go
START_DELAY = 60  # seconds after the bot starts (a deploy) before the first round
MAX_RETRY_AFTER = 60  # Telegram's flood control asking for longer: that message is given up
TASHKENT = timedelta(hours=5)  # no DST in Uzbekistan
SIGNUP_STEPS = ("free1", "free2")


@dataclass
class Target:
    user_id: int
    lang: str
    balance: int
    step: str  # free1 | free2 | lapsed1 | lapsed2
    flag: str  # its once-only Redis key
    days: int = 0  # lapsed: days since the last check


# --- on / off -----------------------------------------------------------------------------------------------


async def reminders_on(user_id: int) -> bool:
    """False when the student switched the reminders off. Redis away: shown as on (nothing is sent then either)."""
    try:
        return not await redis().sismember(OPTOUT_KEY, user_id)
    except Exception:  # noqa: BLE001
        logger.warning("Reminder switch of %s not read", user_id, exc_info=True)
        return True


async def set_reminders(user_id: int, on: bool) -> None:
    if on:
        await redis().srem(OPTOUT_KEY, user_id)
    else:
        await redis().sadd(OPTOUT_KEY, user_id)


# --- who is due ---------------------------------------------------------------------------------------------


def in_hours(now: datetime) -> bool:
    """Between settings.nudge_from_hour and nudge_to_hour, Tashkent time."""
    return settings.nudge_from_hour <= (now + TASHKENT).hour < settings.nudge_to_hour


async def _closed() -> bool:
    """A test window or a pause (OpenAI's credit gone): checks are closed, so nobody is asked to send one."""
    from bot.utils import balance  # noqa: PLC0415 - it imports bot.utils.crm, as runtime does

    return await runtime.window_on() or await balance.credit_paused()


def _students() -> list:
    """Students only: not teachers (center owners are teachers too, and owning a center says so again), not admins,
    not an account blocked in the panel (UserStatus.denied) or never approved."""
    conditions = [User.role == UserRole.student, User.status == UserStatus.approved,
                  ~exists().where(Center.owner_id == User.id)]
    if settings.admin_ids:
        conditions.append(User.id.not_in(sorted(settings.admin_ids)))
    return conditions


def _step(age: timedelta, first: timedelta, second: timedelta) -> int | None:
    """1 or 2: which step is due `age` after its start, None outside both windows. Each step may go up to
    nudge_late_hours late, the first never once the second is due."""
    late = timedelta(hours=settings.nudge_late_hours)
    if second <= age < second + late:
        return 2
    if first <= age < min(first + late, second):
        return 1
    return None


async def _signup_targets(session, now: datetime) -> list[Target]:
    """Never had a check, still has the coins for one."""
    first = timedelta(hours=settings.nudge_signup_first_hours)
    second = timedelta(hours=settings.nudge_signup_second_hours)
    late = timedelta(hours=settings.nudge_late_hours)
    rows = (await session.execute(
        select(User.id, User.language, User.coin_balance, User.created_at)
        .where(*_students(), User.created_at <= now - first, User.created_at > now - second - late,
               User.coin_balance >= TYPICAL_CHECK,
               ~exists().where(WritingSubmission.user_id == User.id),
               ~exists().where(SpeakingSubmission.user_id == User.id))
        .order_by(User.created_at)
    )).all()
    targets = []
    for user_id, lang, coins, created in rows:
        step = _step(now - created, first, second)
        if step is not None:
            targets.append(Target(user_id, lang or "uz", coins, f"free{step}",
                                  FLAG_KEY.format(step=f"free{step}", uid=user_id)))
    return targets


async def _lapsed_targets(session, now: datetime) -> list[Target]:
    """The last check (Writing or Speaking) nudge_lapsed_first_days / _second_days ago."""
    first = timedelta(days=settings.nudge_lapsed_first_days)
    second = timedelta(days=settings.nudge_lapsed_second_days)
    late = timedelta(hours=settings.nudge_late_hours)
    checks = union_all(
        select(WritingSubmission.user_id.label("uid"), WritingSubmission.created_at.label("at")),
        select(SpeakingSubmission.user_id.label("uid"), SpeakingSubmission.created_at.label("at")),
    ).subquery()
    last = select(checks.c.uid, func.max(checks.c.at).label("at")).group_by(checks.c.uid).subquery()
    rows = (await session.execute(
        select(User.id, User.language, User.coin_balance, last.c.at)
        .join(last, last.c.uid == User.id)
        .where(*_students(), last.c.at <= now - first, last.c.at > now - second - late)
        .order_by(last.c.at)
    )).all()
    targets = []
    for user_id, lang, coins, at in rows:
        step = _step(now - at, first, second)
        if step is not None:
            # the flag names the check: a newer check starts the steps over
            flag = FLAG_KEY.format(step=f"lapsed{step}", uid=user_id) + f":{int(at.timestamp())}"
            targets.append(Target(user_id, lang or "uz", coins, f"lapsed{step}", flag, days=(now - at).days))
    return targets


# --- sending ------------------------------------------------------------------------------------------------


async def run_nudges(bot: Bot) -> None:
    """The background task (bot/main.py): a round every settings.nudge_every_minutes."""
    if not settings.nudges_enabled:
        logger.info("Re-engagement reminders are off (NUDGES_ENABLED=false)")
        return
    await asyncio.sleep(START_DELAY)
    while True:
        try:
            await send_round(bot)
        except Exception:  # noqa: BLE001 - the next round tries again
            logger.exception("Sending re-engagement reminders failed")
        await asyncio.sleep(max(1, settings.nudge_every_minutes) * 60)


async def send_round(bot: Bot, now: datetime | None = None) -> dict[str, int]:
    """Sends every reminder due now. {"sent", "blocked", "failed"}: what happened to the ones tried."""
    now = now or datetime.now(timezone.utc)
    counts = {"sent": 0, "blocked": 0, "failed": 0}
    r = redis()
    if await _closed():
        # remembered (real time, in quiet hours too): the day checks open again belongs to the launch message
        # and "checks are back" (bot/launch.py, checks_back.py) - no reminder on top of them for a gap
        await r.set(CLOSED_KEY, int(time.time()), ex=CLOSED_TTL)
        return counts
    if not in_hours(now):
        return counts
    closed_at = await r.get(CLOSED_KEY)
    if closed_at and time.time() - float(closed_at) < settings.nudge_min_gap_hours * 3600:
        return counts
    async with get_session() as session:
        targets = await _signup_targets(session, now) + await _lapsed_targets(session, now)
    if not targets:
        return counts

    optout = {int(x) for x in await r.smembers(OPTOUT_KEY)}
    blocked = {int(k): float(v) for k, v in (await r.hgetall(BLOCKED_KEY)).items()}
    ids = [target.user_id for target in targets]
    seen = {uid: float(v) for uid, v in zip(ids, await r.hmget(activity.LAST_SEEN_KEY, ids)) if v}
    started = time.monotonic()
    pause = 1 / max(settings.nudge_per_second, 0.1)
    tried: set[int] = set()
    for target in targets:
        uid = target.user_id
        clock = now + timedelta(seconds=time.monotonic() - started)
        if not in_hours(clock) or await _closed():
            break  # quiet hours began, or checks closed, during the round: the rest wait (the next round notes it)
        if uid in tried or uid in optout:
            continue
        if uid in blocked:
            if seen.get(uid, 0) <= blocked[uid]:
                continue
            await r.hdel(BLOCKED_KEY, uid)  # the bot has seen them since: they are back
        if seen.get(uid, 0) > clock.timestamp() - settings.nudge_skip_active_minutes * 60:
            continue  # using the bot right now: not interrupted, a later round
        if await r.exists(GAP_KEY.format(uid=uid)):
            continue  # another reminder went lately: this one when the gap is over (still in its window)
        # marked before sending: a restart halfway never sends one twice
        if not await r.set(target.flag, int(clock.timestamp()), nx=True, ex=FLAG_TTL):
            continue
        await r.set(GAP_KEY.format(uid=uid), 1, ex=max(1, settings.nudge_min_gap_hours) * 3600)
        tried.add(uid)
        try:
            text, markup = await _message(target)
        except Exception:  # noqa: BLE001 - this one is not sent (its flag stays: at most once)
            logger.exception("Reminder %s for %s not written", target.step, uid)
            counts["failed"] += 1
            continue
        result = await _send(bot, uid, text, markup)
        counts[result] += 1
        if result == "blocked":
            await r.hset(BLOCKED_KEY, uid, clock.timestamp())
        await asyncio.sleep(pause)
    if any(counts.values()):
        logger.info("Re-engagement reminders: %(sent)d sent, %(blocked)d blocked, %(failed)d failed", counts)
    return counts


async def _message(target: Target) -> tuple[str, InlineKeyboardMarkup]:
    lang = target.lang
    if target.step in SIGNUP_STEPS:
        key = "nudge.free_first" if target.step == "free1" else "nudge.free_second"
        return t(key, lang, balance=target.balance), nudge_kb(lang)
    from bot.handlers.account import weakest_line  # noqa: PLC0415 - the handlers import this module

    async with get_session() as session:
        weakest = await weakest_line(session, target.user_id, "nudge.weakest", lang)
        short = not await _covered(session, target.user_id, target.balance, TYPICAL_CHECK)
    key = "nudge.lapsed_first" if target.step == "lapsed1" else "nudge.lapsed_second"
    text = t(key, lang, days=target.days, weakest=weakest + "\n\n" if weakest else "")
    if short:
        text += "\n\n" + t("nudge.low_balance", lang, balance=target.balance, price=TYPICAL_CHECK,
                           pack=COIN_PACKS[0][0])
    return text, nudge_kb(lang, topup=short)


async def _covered(session, user_id: int, balance: int, price: int) -> bool:
    """The student's next check is paid: their own coins, a running subscription, or a sponsor (a group whose
    teacher or center pays - db/crud.py pick_sponsor, which only looks: nobody is told anything here)."""
    if balance >= price or await crud.has_active_subscription(session, user_id):
        return True
    return (await crud.pick_sponsor(session, user_id, coins=price)).payer_id is not None


async def _send(bot: Bot, user_id: int, text: str, markup: InlineKeyboardMarkup) -> str:
    """"sent", "blocked" (blocked the bot, or the account is deleted) or "failed"."""
    for attempt in (1, 2):
        try:
            await bot.send_message(user_id, text, reply_markup=markup, disable_web_page_preview=True)
            return "sent"
        except TelegramRetryAfter as e:
            if attempt == 2 or e.retry_after > MAX_RETRY_AFTER:
                logger.warning("Reminder to %s given up: flood control asks %ss", user_id, e.retry_after)
                return "failed"
            await asyncio.sleep(e.retry_after + 1)
        except TelegramForbiddenError as e:
            if "blocked" in str(e).lower() or "deactivated" in str(e).lower():
                return "blocked"
            # "can't initiate conversation": never started this bot (the move to @bandzilla_bot) - not a "no"
            logger.info("Reminder to %s not sent: %s", user_id, e)
            return "failed"
        except TelegramBadRequest as e:
            logger.info("Reminder to %s not sent: %s", user_id, e)
            return "failed"
        except Exception:  # noqa: BLE001
            logger.exception("Reminder to %s failed", user_id)
            return "failed"
    return "failed"


# --- after a report -----------------------------------------------------------------------------------------


async def what_next(message: Message, lang: str, kind: str, data: dict, markup: InlineKeyboardMarkup | None) -> None:
    """"Davom etamizmi? 👇" under a report (writing.py, speaking.py), with the coins left (2026-11-16): enough for
    N more checks - or fewer than the next check costs, and "💳 Hisobni to'ldirish" on top of `markup`. Nothing
    about coins when they don't decide the next check (a sponsor, a test window, a pause, an assignment, a mock
    test, a test run). Its own failure leaves the plain line."""
    text = t("nav.what_next", lang)
    try:
        wallet = None if data.get("assignment_part_id") or data.get("mock_session_id") or data.get("test_run") \
            else await _wallet(message.chat.id, kind)
        if wallet is not None:
            coins, price = wallet
            if coins >= price:
                text = t("next.balance_ok", lang, balance=coins, checks=coins // price) + "\n" + text
            else:
                text = t("next.balance_low", lang, balance=coins, price=price) + "\n" + text
                markup = with_topup(markup, lang)
    except Exception:  # noqa: BLE001
        logger.warning("Coins left under the report of %s not shown", message.chat.id, exc_info=True)
    await message.answer(text, reply_markup=markup)


async def _wallet(user_id: int, kind: str) -> tuple[int, int] | None:
    """(the student's coins, what the next such check costs) - None when their coins don't decide it."""
    if await runtime.window_on() or await runtime.checks_paused_for(user_id):
        return None
    price = check_coins(kind)
    async with get_session() as session:
        user = await crud.get_user(session, user_id)
        if user is None or await crud.has_active_subscription(session, user_id):
            return None
        if (await crud.pick_sponsor(session, user_id, coins=price)).payer_id is not None:
            return None  # their group pays for it (pick_sponsor only looks: nobody is told anything)
    return user.coin_balance, price
