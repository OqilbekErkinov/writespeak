"""One check at a time, its coins held while it is graded (the 2026-11-15 audit).

Before: the coins were only looked at when a check started and taken when its report was saved (never below
0), so a student could start a second check while the first was graded - a double tap, a second essay, the
menu - and every one of them ran for one price; 30 students of a sponsored group could all start on 20 coins.
A deploy killed the checks being graded and nobody was told. Now every Writing/Speaking grading runs inside a
CheckHold (db/models.py):

- begin(): the moment grading starts, one transaction writes the hold (a partial unique index allows one
  running check per student - admins excepted) and takes the price from the payer with a conditional UPDATE
  (`coin_balance >= price`). A second check gets "your check is still being graded"; missing coins get the
  paywall. A sponsor that ran out meanwhile: the student's own coins, if they cover it.
- the report saved: db/crud.py save_*_submission marks the hold `charged` in the same transaction.
- end(): in a `finally`, whatever happened - a hold still `held` gives its coins back (`refunded`).
- recover(): at start-up, the holds a stopped bot left `held`: coins back (`interrupted`), and the student's
  card says so - with "try again" when their answer is still in the FSM data.
- drain(): at shutdown the running checks are waited for, so a deploy finishes them instead of killing them.
- slot(): at most settings.max_parallel_checks graded at once; the others wait, their place on the card.
- sweep(): every few minutes, a hold `held` by no running check (its end() failed) is settled the same way.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from bot.config import settings
from bot.i18n import t
from bot.keyboards.nav_kb import CB_BACK_MAIN
from db.database import get_session
from db.models import CheckHold, User

logger = logging.getLogger(__name__)

CB_RETRY = "chk:retry:"
SWEEP_EVERY = 180  # seconds
STALE_AFTER = 5 * 60  # a held row this old that no running check owns is settled by sweep()

# hold id -> the task grading it (this process only)
running: dict[str, asyncio.Task | None] = {}
_slots: asyncio.Semaphore | None = None
_queue: list[str] = []  # holds waiting for a slot, in order


@dataclass
class Hold:
    id: str
    user_id: int
    module: str  # "w" | "s"
    kind: str
    coins: int = 0
    payer_id: int | None = None  # whose coins were taken (None: free)
    persisted: bool = True  # False for a test run (scripts/live_check.py): nothing written, nothing charged
    started: float = field(default_factory=time.monotonic)
    saved: bool = False  # the report is saved (and paid): a later error is only a delivery problem

    @property
    def sponsor_id(self) -> int | None:
        """The group sponsor who paid, None when the student paid or it was free."""
        return self.payer_id if self.coins and self.payer_id and self.payer_id != self.user_id else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


def retry_kb(lang: str, hold_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("check.retry_btn", lang), callback_data=f"{CB_RETRY}{hold_id}")],
        [InlineKeyboardButton(text=t("nav.main_menu", lang), callback_data=CB_BACK_MAIN)],
    ])


async def busy(user_id: int) -> bool:
    """A check of this student's is being graded (admins never wait)."""
    if is_admin(user_id):
        return False
    async with get_session() as session:
        row = (await session.execute(select(CheckHold.id).where(
            CheckHold.user_id == user_id, CheckHold.state == "held", CheckHold.exclusive.is_(True)).limit(1))).first()
    return row is not None


async def _take(session, payer_id: int, coins: int) -> bool:
    """The price off `payer_id`'s balance - only if it is there (one statement: no two checks can both spend
    the same coins)."""
    result = await session.execute(
        update(User).where(User.id == payer_id, User.coin_balance >= coins)
        .values(coin_balance=User.coin_balance - coins).returning(User.id))
    return result.scalar_one_or_none() is not None


async def _give(session, payer_id: int, coins: int) -> None:
    await session.execute(update(User).where(User.id == payer_id).values(coin_balance=User.coin_balance + coins))


async def _refuse(card: Message, state: FSMContext, failed_state, lang: str, text: str, markup=None) -> None:
    """A check that could not start: the card says why, with "🔁 Try again" (the answer stays in the FSM
    data, the state moves to the module's `failed` - after a top-up, say, the button goes on)."""
    token = uuid.uuid4().hex
    await state.update_data(check_token=token)
    await state.set_state(failed_state)
    rows = list(markup.inline_keyboard) if markup is not None else []
    rows.append([InlineKeyboardButton(text=t("check.retry_btn", lang), callback_data=f"{CB_RETRY}{token}")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await card.edit_text(text, reply_markup=keyboard)
    except TelegramBadRequest:
        await card.answer(text, reply_markup=keyboard)


async def deliver(send, what: str) -> bool:
    """Sends part of a finished check (its report, its voices): once more after a moment if Telegram fails
    (its 5xx, a timeout) - the report is saved either way and stays in "My works"."""
    for attempt in (1, 2):
        try:
            await send()
            return True
        except TelegramBadRequest:
            logger.exception("%s not sent", what)
            return False
        except Exception:  # noqa: BLE001 - network errors, Telegram's server errors
            if attempt == 2:
                logger.exception("%s not sent", what)
                return False
            await asyncio.sleep(3)
    return False


async def begin(card: Message, state: FSMContext, data: dict, *, module: str, kind: str, lang: str,
                failed_state) -> Hold | None:
    """Starts a paid run: the hold written and its coins taken, or the student told why not (on `card`, the
    "analysing…" message already shown, with "🔁 Try again") and None. `data`: the check's FSM data
    (spend_credit, payer_id, test_run); the hold's id is kept there as "check_token" (who owns the FSM state
    when the check ends). `failed_state`: the module's `failed` state, for a refused check."""
    from bot.utils.quota import check_price  # noqa: PLC0415 - quota imports this module

    user_id = card.chat.id
    token = uuid.uuid4().hex
    if data.get("test_run"):
        running[token] = asyncio.current_task()
        await state.update_data(check_token=token)
        return Hold(token, user_id, module, kind, persisted=False)
    coins = check_price(data, kind)
    payer = (data.get("payer_id") or user_id) if coins else None
    running[token] = asyncio.current_task()  # before the commit: sweep() never takes a fresh hold for stale
    try:
        async with get_session() as session:
            row = CheckHold(id=token, user_id=user_id, chat_id=card.chat.id, message_id=card.message_id,
                            module=module, kind=kind, lang=lang or "uz", payer_id=payer, coins=coins,
                            exclusive=not is_admin(user_id), state="held")
            session.add(row)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                running.pop(token, None)
                logger.info("Check refused for user %s: another one is being graded", user_id)
                await _refuse(card, state, failed_state, lang, t("check.busy", lang))
                return None
            if coins:
                paid = await _take(session, payer, coins)
                if not paid and payer != user_id:
                    # the group's sponsor ran out since the check started: the student's own coins, if enough
                    paid = await _take(session, user_id, coins)
                    if paid:
                        row.payer_id = payer = user_id
                if not paid:
                    await session.rollback()
                    running.pop(token, None)
                    balance = (await session.execute(select(User.coin_balance).where(User.id == user_id))).scalar()
                    from bot.keyboards.payment_kb import buy_coins_kb  # noqa: PLC0415
                    from bot.utils.quota import price_list  # noqa: PLC0415

                    logger.info("Check refused for user %s: %s coins needed, payer %s short", user_id, coins, payer)
                    await _refuse(card, state, failed_state, lang,
                                  t("quota.paywall", lang, price=coins, balance=balance or 0)
                                  + "\n\n" + price_list(lang), buy_coins_kb(lang))
                    return None
            await session.commit()
    except Exception:
        running.pop(token, None)
        raise
    await state.update_data(check_token=token, payer_id=payer if payer and payer != user_id else None)
    logger.info("Check %s started: user %s, %s, %s coins from %s", token[:8], user_id, kind, coins, payer)
    return Hold(token, user_id, module, kind, coins=coins, payer_id=payer)


async def end(hold: Hold | None, reason: str | None = None) -> bool:
    """Settles a run that is over: a hold not charged (no report saved) gives its coins back. Always safe to
    call (a `finally`); True when coins went back."""
    if hold is None:
        return False
    running.pop(hold.id, None)
    if not hold.persisted:
        return False
    try:
        return bool(await _settle(hold.id, "refunded", reason or "not finished"))
    except Exception:  # noqa: BLE001 - sweep() settles it later
        logger.exception("Check %s not settled - the sweep will", hold.id[:8])
        return False


async def _settle(hold_id: str, outcome: str, reason: str) -> int | None:
    """A hold still `held` closed as `outcome`, its coins given back: how many (None: it was not held)."""
    async with get_session() as session:
        row = (await session.execute(select(CheckHold).where(CheckHold.id == hold_id).with_for_update())) \
            .scalar_one_or_none()
        if row is None or row.state != "held":
            return None
        if row.coins and row.payer_id:
            await _give(session, row.payer_id, row.coins)
        row.state, row.reason, row.finished_at = outcome, (reason or "")[:300] or None, _now()
        await session.commit()
    logger.info("Check %s %s: %s coins back to %s (%s)", hold_id[:8], outcome, row.coins, row.payer_id, reason)
    return row.coins


async def owns(state: FSMContext, hold: Hold) -> bool:
    """The FSM state is still this check's: the student has not started something else meanwhile (only then
    may the check clear it, or leave its "try again" in it)."""
    return (await state.get_data()).get("check_token") == hold.id


@asynccontextmanager
async def slot(hold: Hold, card=None):
    """At most settings.max_parallel_checks graded at once; while waiting, the live card shows the place in
    the queue (bot/utils/progress.py `queued`)."""
    global _slots
    if _slots is None:
        _slots = asyncio.Semaphore(max(1, settings.max_parallel_checks))
    _queue.append(hold.id)
    if card is not None:
        card.queued = lambda: (_queue.index(hold.id) + 1) if hold.id in _queue else 0
    try:
        await _slots.acquire()
    finally:
        if hold.id in _queue:
            _queue.remove(hold.id)
        if card is not None:
            card.queued = None
    try:
        yield
    finally:
        _slots.release()


async def drain(timeout: float | None = None) -> None:
    """At shutdown (aiogram's shutdown event, before the bot session closes): the checks still being graded
    are waited for, so a deploy finishes them. What is left after `timeout` is recovered at the next start."""
    tasks = [task for task in running.values() if task is not None and not task.done()]
    if not tasks:
        return
    timeout = settings.check_drain_seconds if timeout is None else timeout
    logger.info("Shutdown: waiting for %d running check(s), up to %ss", len(tasks), timeout)
    done, pending = await asyncio.wait(tasks, timeout=timeout)
    logger.info("Shutdown: %d check(s) finished, %d left for the next start", len(done), len(pending))


async def recover(bot: Bot, storage: BaseStorage) -> int:
    """At start-up: the holds a stopped bot left running - coins back, and each student told on the check's
    own card, with "try again" when the answer is still in their FSM data. How many there were."""
    from bot.states.speaking_states import SpeakingStates  # noqa: PLC0415
    from bot.states.writing_states import WritingStates  # noqa: PLC0415

    failed = {"w": WritingStates.failed, "s": SpeakingStates.failed}
    processing = {WritingStates.processing.state, SpeakingStates.processing.state}
    async with get_session() as session:
        rows = (await session.execute(select(CheckHold).where(CheckHold.state == "held"))).scalars().all()
    for row in rows:
        if row.id in running:  # never at start-up; only if called again later
            continue
        try:
            coins = await _settle(row.id, "interrupted", "the bot restarted")
        except Exception:  # noqa: BLE001
            logger.exception("Check %s not recovered", row.id[:8])
            continue
        if coins is None:
            continue
        await tell(bot, storage, row, failed.get(row.module), processing,
                   "check.interrupted", "check.interrupted_lost", coins)
    if rows:
        logger.info("Recovered %d check(s) a stopped bot left running", len(rows))
    return len(rows)


async def tell(bot: Bot, storage: BaseStorage, row: CheckHold, failed_state, processing: set[str],
               key_retry: str, key_lost: str, coins: int) -> None:
    """Tells the student their check stopped, on its card: with "try again" when their FSM state is still
    this check's (it is then moved to the module's `failed` state, which the button needs)."""
    ctx = FSMContext(storage=storage, key=StorageKey(bot_id=bot.id, chat_id=row.chat_id, user_id=row.user_id))
    retry = False
    try:
        if (await ctx.get_data()).get("check_token") == row.id and await ctx.get_state() in processing \
                and failed_state is not None:
            await ctx.set_state(failed_state)
            retry = True
    except Exception:  # noqa: BLE001
        logger.warning("FSM of user %s not read", row.user_id, exc_info=True)
    note = ("\n\n" + t("check.coins_back", row.lang, coins=coins)) if coins else ""
    text = t(key_retry if retry else key_lost, row.lang) + note
    markup = retry_kb(row.lang, row.id) if retry else None
    try:
        if row.message_id:
            await bot.edit_message_text(text, chat_id=row.chat_id, message_id=row.message_id, reply_markup=markup)
            return
    except TelegramBadRequest:
        pass
    except Exception:  # noqa: BLE001
        logger.warning("Card of check %s not edited", row.id[:8], exc_info=True)
    try:
        await bot.send_message(row.chat_id, text, reply_markup=markup)
    except Exception:  # noqa: BLE001 - blocked the bot, etc.
        logger.warning("User %s not told about check %s", row.user_id, row.id[:8], exc_info=True)


async def sweep(bot: Bot, storage: BaseStorage) -> int:
    """A hold left `held` by no running check of this process (its end() failed - the database was away):
    settled like a failed check, the student told."""
    from bot.states.speaking_states import SpeakingStates  # noqa: PLC0415
    from bot.states.writing_states import WritingStates  # noqa: PLC0415

    cutoff = _now() - timedelta(seconds=STALE_AFTER)
    async with get_session() as session:
        rows = (await session.execute(select(CheckHold).where(
            CheckHold.state == "held", CheckHold.created_at < cutoff))).scalars().all()
    settled = 0
    for row in rows:
        if row.id in running:
            continue
        coins = await _settle(row.id, "expired", "left running")
        if coins is None:
            continue
        settled += 1
        await tell(bot, storage, row,
                   {"w": WritingStates.failed, "s": SpeakingStates.failed}.get(row.module),
                   {WritingStates.processing.state, SpeakingStates.processing.state},
                   "check.failed", "check.failed_lost", coins)
    return settled


async def run_sweeper(bot: Bot, storage: BaseStorage) -> None:
    while True:
        await asyncio.sleep(SWEEP_EVERY)
        try:
            n = await sweep(bot, storage)
            if n:
                logger.warning("Sweep settled %d check(s) left running", n)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Check sweep failed")
