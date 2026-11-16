"""One check at a time, its coins held while it is graded (the 2026-11-15 audit).

Before: the coins were only looked at when a check started and taken when its report was saved (never below
0), so a student could start a second check while the first was graded - a double tap, a second essay, the
menu - and every one of them ran for one price; 30 students of a sponsored group could all start on 20 coins.
A deploy killed the checks being graded and nobody was told. Now every Writing/Speaking grading runs inside a
CheckHold (db/models.py):

- begin(): the moment grading starts, the price and the payer are decided again (bot/utils/quota.py
  fresh_quota) and one transaction writes the hold (a partial unique index allows one running check per
  student - admins excepted) and takes the price from the payer with a conditional UPDATE
  (`coin_balance >= price`). A second check gets "your check is still being graded"; missing coins get the
  paywall. A sponsor that ran out meanwhile: the student's own coins, if they cover it.
- the report saved: db/crud.py save_*_submission marks the hold `charged` in the same transaction.
- end(): in a `finally`, whatever happened - a hold still `held` gives its coins back (`refunded`).
- recover(): at start-up, the holds a stopped bot left `held`: coins back (`interrupted`), and the student's
  card says so - with "try again" when their answer is still in the FSM data.
- drain(): at shutdown the running checks are waited for, so a deploy finishes them instead of killing them.
- slot(): as many graded at once as OpenAI's limit and the panel's cap allow (bot/utils/capacity.py); the
  others wait, their place on the card.
- sweep(): every few minutes, a hold `held` by no running check (its end() failed) is settled the same way.

Before the launch (2026-11-16):
- begin() no longer trusts the price and payer the FSM data was given when the task was chosen (it lives 30
  days): a "try again" days later charged a sponsor the student had left, and a flow started in a test
  window stayed free after it.
- deliver() waits out Telegram's flood control and tries a network/server error again after 3, 10 and 30 s;
  the "analysing…" card goes only once the report is there, and a report that would not go turns the card
  into "ready and saved", with a button that sends it again (report_sent()).
- a deploy: drain() sets `shutting_down`; nothing new is admitted to grading, and a check cancelled when the
  process exits keeps its hold `held`, so the next start's recover() gives the coins back once and tells the
  student on the card (until then its `finally` refunded it, and nobody was told).
- a check refused or stopped with "🔁 Try again" also leaves its FSM data in Redis for 7 days (park()): a
  top-up's own steps wipe the FSM state, and "try again" after the approval found nothing.
- the admins' group hears of every check that stops - once, with the reason classified from the exception
  end() was called with (sys.exc_info()), the stage and the refund - and of a restart's interrupted checks
  (bot/utils/check_incidents.py); a report saved but not delivered is said on the check's own card.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import aiohttp
from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramEntityTooLarge,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from bot.config import settings
from bot.i18n import t
from bot.keyboards.my_works_kb import CB_DOWNLOAD_PREFIX
from bot.keyboards.nav_kb import CB_BACK_MAIN
from bot.utils import check_incidents
from db.database import get_session
from db.models import CheckHold, User

logger = logging.getLogger(__name__)

CB_RETRY = "chk:retry:"
SWEEP_EVERY = 180  # seconds
STALE_AFTER = 5 * 60  # a held row this old that no running check owns is settled by sweep()
DELIVERY_WAITS = (3, 10, 30)  # seconds before each new try of a send Telegram's network or server failed
FLOOD_TRIES = 3  # Telegram's flood control (RetryAfter) waited out at most this many times for one send
MAX_FLOOD_WAIT = 120  # seconds: a longer flood wait gives the send up
PARKED_KEY = "check:parked:{}"  # + user id: a stopped check's FSM state and data (park())
PARK_TTL = 7 * 24 * 3600

# hold id -> the task grading it (this process only)
running: dict[str, asyncio.Task | None] = {}
# set by drain(): the process is stopping (a deploy) - see end()
shutting_down = False


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
    # what the admins' group is told about it (bot/utils/check_incidents.py, 2026-11-16)
    stage: str = "start"  # start -> queue -> grading -> delivery
    notes: list = field(default_factory=list)  # what went worse than it should (Azure failed, not delivered...)
    calls: list | None = None  # its OpenAI calls (services/ai/usage.py meter)
    reported: bool = False  # its incident is told: one card a check
    card_done: bool = False  # its own card in the module's topic carried the notes (crm_events.check_done)
    delivery_error: BaseException | None = None  # why deliver() last gave a send up
    ended: bool = False

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


def resend_kb(lang: str, kind: str, submission_id: int) -> InlineKeyboardMarkup:
    """A saved report sent again - "My works"' own download button (bot/handlers/my_works.py).
    `kind`: "writing" | "speaking"."""
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=t("check.report_resend_btn", lang), callback_data=f"{CB_DOWNLOAD_PREFIX}{kind}:{submission_id}")]])


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


# --- a stopped check's data, kept apart (C3, 2026-11-16) ------------------------------------------------


def _redis():
    from bot.utils.crm import redis  # noqa: PLC0415

    return redis()


def _failed_states() -> set[str]:
    from bot.states.speaking_states import SpeakingStates  # noqa: PLC0415
    from bot.states.writing_states import WritingStates  # noqa: PLC0415

    return {WritingStates.failed.state, SpeakingStates.failed.state}


async def park(user_id: int, state: FSMContext) -> None:
    """The FSM state and data of a check that stopped with "🔁 Try again" (its `failed` state, its answer,
    its token), copied to Redis for PARK_TTL: choosing a coin pack, sending the receipt or opening the account
    screen overwrite or clear the FSM state, and "try again" after the top-up found nothing (2026-11-16).
    Never raises - the FSM copy works without it."""
    try:
        payload = json.dumps({"state": await state.get_state(), "data": await state.get_data()},
                             ensure_ascii=False, default=str)
        await _redis().set(PARKED_KEY.format(user_id), payload, ex=PARK_TTL)
    except Exception:  # noqa: BLE001
        logger.warning("Stopped check of user %s not parked", user_id, exc_info=True)


async def parked(user_id: int) -> dict | None:
    """{"state", "data"} of the student's parked check, or None."""
    try:
        raw = await _redis().get(PARKED_KEY.format(user_id))
        value = json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        logger.warning("Parked check of user %s not read", user_id, exc_info=True)
        return None
    return value if isinstance(value, dict) and isinstance(value.get("data"), dict) else None


async def restore(user_id: int, state: FSMContext, token: str) -> str | None:
    """The parked check whose "try again" (token) was pressed, back in the FSM: its `failed` state (returned),
    or None when there is no such check."""
    copy = await parked(user_id)
    if copy is None or copy["data"].get("check_token") != token or copy.get("state") not in _failed_states():
        return None
    await state.set_data(copy["data"])
    await state.set_state(copy["state"])
    logger.info("Stopped check of user %s brought back from its parked copy", user_id)
    return copy["state"]


async def unpark(user_id: int, token: str | None) -> None:
    """The parked copy goes once a check started from it has taken its hold (`token`: the stopped attempt's)."""
    if not token:
        return
    try:
        key = PARKED_KEY.format(user_id)
        raw = await _redis().get(key)
        if raw and (json.loads(raw).get("data") or {}).get("check_token") == token:
            await _redis().delete(key)
    except Exception:  # noqa: BLE001 - it expires by itself
        logger.warning("Parked check of user %s not cleared", user_id, exc_info=True)


async def parked_keyboard(user_id: int, lang: str | None) -> InlineKeyboardMarkup | None:
    """"🔁 Continue my check" - the parked check's own "try again" - for a message such as the payment
    approval; None when nothing is parked. Never raises."""
    copy = await parked(user_id)
    token = copy["data"].get("check_token") if copy else None
    if not token:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=t("check.continue_btn", lang), callback_data=f"{CB_RETRY}{token}")]])


# --- telling the student ---------------------------------------------------------------------------------


_TRANSIENT = (TelegramNetworkError, TelegramServerError, asyncio.TimeoutError, aiohttp.ClientError, OSError)


async def deliver(send, what: str) -> bool:
    """Sends part of a finished check (its report, its voices, its card): Telegram's flood control is waited
    out as it asks (TelegramRetryAfter), a network or server error is tried again after 3, 10 and 30 s; a
    bad request (or a blocked bot) is never tried again. False when it did not go - the report is saved
    either way and stays in "My works". The last failure is kept on the running check (check_incidents): a
    report that did not go says why on the check's card in the group."""
    errors = floods = 0
    while True:
        try:
            await send()
            return True
        except TelegramRetryAfter as e:
            floods += 1
            if floods > FLOOD_TRIES or e.retry_after + 1 > MAX_FLOOD_WAIT:
                logger.error("%s not sent: Telegram's flood control asks to wait %s s", what, e.retry_after)
                check_incidents.not_sent(e)
                return False
            logger.warning("%s: Telegram's flood control - waiting %s s", what, e.retry_after + 1)
            await asyncio.sleep(e.retry_after + 1)
        except TelegramBadRequest as e:
            logger.exception("%s not sent", what)
            check_incidents.not_sent(e)
            return False
        except TelegramForbiddenError as e:
            logger.warning("%s not sent: %s", what, e)  # the student blocked the bot
            check_incidents.not_sent(e)
            return False
        except _TRANSIENT as e:
            if isinstance(e, TelegramEntityTooLarge) or errors >= len(DELIVERY_WAITS):
                logger.exception("%s not sent", what)
                check_incidents.not_sent(e)
                return False
            logger.warning("%s not sent (%r) - trying again in %s s", what, e, DELIVERY_WAITS[errors])
            await asyncio.sleep(DELIVERY_WAITS[errors])
            errors += 1
        except Exception as e:  # noqa: BLE001 - never retried: not a network problem
            logger.exception("%s not sent", what)
            check_incidents.not_sent(e)
            return False


async def show(card: Message, text: str, markup=None, *, edit: bool = True) -> bool:
    """The check's card becomes `text` - or, when it can't be edited (the student deleted it, Telegram failed,
    or `edit` is False), a new message is sent (deliver()). Never raises; True when it went."""
    if edit:
        try:
            await card.edit_text(text, reply_markup=markup)
            return True
        except TelegramBadRequest:
            pass
        except Exception:  # noqa: BLE001
            logger.warning("Card of user %s not edited - sending it anew", card.chat.id, exc_info=True)
    return await deliver(lambda: card.answer(text, reply_markup=markup), f"card of user {card.chat.id}")


async def report_sent(card: Message, sent: bool, lang: str, kind: str, submission_id: int) -> None:
    """After a finished check's report was sent (`sent`: deliver()'s answer). The "analysing…" card goes only
    now that the report is there (until 2026-11-16 it was deleted first: a failed send left the student, who
    had paid, with nothing). A report that would not go - saved and paid - turns the card into "ready and
    saved, Telegram did not deliver it", with a button that sends the saved report again."""
    check_incidents.delivered(sent)  # an undelivered report is said on the check's card in the group
    if sent:
        try:
            await card.delete()
        except Exception:  # noqa: BLE001 - the student deleted it meanwhile, or Telegram is away: it stays
            pass
        return
    if not await show(card, t("check.report_undelivered", lang), resend_kb(lang, kind, submission_id)):
        logger.error("User %s not told that %s report %s is saved", card.chat.id, kind, submission_id)


async def refuse(card: Message, state: FSMContext, failed_state, lang: str, text: str, markup=None, *,
                 edit: bool = True) -> None:
    """A check that could not start or stopped before grading: the card says why, with "🔁 Try again" (the
    answer stays in the FSM data - and in its parked copy - the state moves to the module's `failed`: after a
    top-up, say, the button goes on). `edit` False: a new message instead of editing `card` (no card yet -
    `card` is then the student's own message)."""
    token = uuid.uuid4().hex
    await state.update_data(check_token=token)
    await state.set_state(failed_state)
    await park(card.chat.id, state)
    rows = list(markup.inline_keyboard) if markup is not None else []
    rows.append([InlineKeyboardButton(text=t("check.retry_btn", lang), callback_data=f"{CB_RETRY}{token}")])
    await show(card, text, InlineKeyboardMarkup(inline_keyboard=rows), edit=edit)


async def _paywall(card: Message, state: FSMContext, failed_state, lang: str, coins: int, balance) -> None:
    from bot.keyboards.payment_kb import buy_coins_kb  # noqa: PLC0415
    from bot.utils.quota import price_list  # noqa: PLC0415

    logger.info("Check refused for user %s: %s coins needed, %s there", card.chat.id, coins, balance)
    await refuse(card, state, failed_state, lang,
                 t("quota.paywall", lang, price=coins, balance=balance or 0) + "\n\n" + price_list(lang),
                 buy_coins_kb(lang))


async def _refuse_quota(card: Message, state: FSMContext, failed_state, lang: str, quota) -> None:
    """The check is no longer allowed the moment it would start (bot/utils/quota.py fresh_quota)."""
    user_id = card.chat.id
    if quota.busy:
        logger.info("Check refused for user %s: another one is being graded", user_id)
        await refuse(card, state, failed_state, lang, t("check.busy", lang))
    elif quota.paused:  # a test window, or OpenAI's credit gone: told when checks are back (checks_back.py)
        from bot.utils.checks_back import remember  # noqa: PLC0415

        logger.info("Check refused for user %s: checks are paused", user_id)
        await remember(user_id)
        await refuse(card, state, failed_state, lang, t("quota.paused", lang))
    else:
        await _paywall(card, state, failed_state, lang, quota.price, quota.balance)


# --- a check's run -----------------------------------------------------------------------------------------


async def begin(card: Message, state: FSMContext, data: dict, *, module: str, kind: str, lang: str,
                failed_state) -> Hold | None:
    """Starts a paid run: the hold written and its coins taken, or the student told why not (on `card`, the
    "analysing…" message already shown, with "🔁 Try again") and None. `data`: the check's FSM data; what the
    check costs and who pays is decided again now (bot/utils/quota.py fresh_quota) - the FSM data's
    spend_credit/payer_id are from when the task was chosen, maybe weeks ago - and the FSM data updated to
    it. The hold's id is kept there as "check_token" (who owns the FSM state when the check ends).
    `failed_state`: the module's `failed` state, for a refused check. A test run (data["test_run"]) costs
    nothing and writes nothing."""
    from bot.utils.quota import fresh_quota  # noqa: PLC0415 - quota imports this module

    user_id = card.chat.id
    token = uuid.uuid4().hex
    if data.get("test_run"):
        running[token] = asyncio.current_task()
        await state.update_data(check_token=token)
        return Hold(token, user_id, module, kind, persisted=False)  # no incidents: a test run tells nobody
    # Priced again now (2026-11-16) - before the hold is written, so its own check is never "busy" here.
    quota = await fresh_quota(user_id, kind, data)
    if not quota.allowed:
        await _refuse_quota(card, state, failed_state, lang, quota)
        return None
    coins = quota.price if quota.spend_credit else 0
    payer = (quota.payer_id or user_id) if coins else None
    if bool(data.get("spend_credit")) != quota.spend_credit or data.get("payer_id") != quota.payer_id:
        logger.info("Check of user %s priced anew: %s coins from %s (the flow had spend_credit=%s, payer %s)",
                    user_id, coins, payer, data.get("spend_credit"), data.get("payer_id"))
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
                await refuse(card, state, failed_state, lang, t("check.busy", lang))
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
                    await _paywall(card, state, failed_state, lang, coins, balance)
                    return None
            await session.commit()
    except Exception:
        running.pop(token, None)
        raise
    hold = Hold(token, user_id, module, kind, coins=coins, payer_id=payer)
    # from here on this context is the check's: its OpenAI calls metered, its errors and outcome told once
    check_incidents.attach(hold)
    try:
        await state.update_data(check_token=token, spend_credit=quota.spend_credit,
                                payer_id=payer if payer and payer != user_id else None)
    except Exception:
        await end(hold, "the FSM state could not be saved")  # its coins back: the caller says the check failed
        raise
    await unpark(user_id, data.get("check_token"))
    logger.info("Check %s started: user %s, %s, %s coins from %s", token[:8], user_id, kind, coins, payer)
    return hold


def _stopped_by_shutdown() -> bool:
    """The task running this check is being cancelled while the bot shuts down (drain() was called)."""
    if not shutting_down:
        return False
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


async def end(hold: Hold | None, reason: str | None = None) -> bool:
    """Settles a run that is over: a hold not charged (no report saved) gives its coins back. Always safe to
    call (a `finally`); True when coins went back. A check cancelled by a shutdown is the exception: its hold
    stays `held` (and in `running`, so no sweep of this process takes it) - the next start's recover() gives
    the coins back once and tells the student on the card, with "try again" (2026-11-16: refunded here, the
    student was never told and the card said "analysing…" for good)."""
    if hold is None:
        return False
    # the exception being handled where end() was called (its `except`, or a `finally` it passes through): why
    # the check stopped, for the admins' incident card (bot/utils/check_incidents.py, 2026-11-16)
    error = sys.exc_info()[1]
    if hold.persisted and _stopped_by_shutdown():
        logger.info("Check %s stopped by the shutdown - left held for the next start", hold.id[:8])
        return False  # the next start's recover() tells the admins
    running.pop(hold.id, None)
    if not hold.persisted:
        check_incidents.detach(hold)
        return False
    why = check_incidents.explain(hold, error, reason) if not hold.reported else None
    found: dict = {}
    try:
        coins = await _settle(hold.id, "refunded", why.stored(reason) if why else reason or "not finished", found)
    except Exception:  # noqa: BLE001 - sweep() settles it later (and tells the admins then)
        logger.exception("Check %s not settled - the sweep will", hold.id[:8])
        check_incidents.detach(hold)
        return False
    state = "settled" if coins is not None else found.get("state")
    check_incidents.detach(hold)
    check_incidents.report(hold, why, state=state, coins=coins, payer_id=found.get("payer_id"), error=error)
    return bool(coins)


async def _settle(hold_id: str, outcome: str, reason: str, found: dict | None = None) -> int | None:
    """A hold still `held` closed as `outcome`, its coins given back: how many (None: it was not held -
    `found["state"]` then says what it was; `found["payer_id"]`: whose coins they were)."""
    async with get_session() as session:
        row = (await session.execute(select(CheckHold).where(CheckHold.id == hold_id).with_for_update())) \
            .scalar_one_or_none()
        if found is not None and row is not None:
            found.update(state=row.state, payer_id=row.payer_id)
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
    """The check's turn to be graded (bot/utils/capacity.py: as many at once as OpenAI's limit for the account
    and the panel's cap allow); while it waits, the live card shows its place in the queue
    (bot/utils/progress.py `queued`)."""
    from bot.utils.capacity import governor  # noqa: PLC0415

    ticket = hold.id
    if card is not None:
        card.queued = lambda: governor.position(ticket)
    hold.stage = "queue"
    try:
        await governor.acquire(ticket)
    finally:
        if card is not None:
            card.queued = None
    hold.stage = "grading"
    try:
        yield
    finally:
        governor.release()


async def drain(timeout: float | None = None) -> None:
    """At shutdown (aiogram's shutdown event, before the bot session closes): the checks still being graded
    are waited for, so a deploy finishes them. From this moment nothing new is admitted to grading
    (2026-11-16: the queue kept admitting checks whose OpenAI calls were paid and then thrown away) - the
    queued checks are not waited for, they have spent nothing. What is left after `timeout` is cancelled when
    the process exits; its hold stays `held` (end()) and the next start's recover() settles it."""
    global shutting_down
    from bot.utils.capacity import governor  # noqa: PLC0415

    shutting_down = True
    governor.close()
    timeout = settings.check_drain_seconds if timeout is None else timeout
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    def graded() -> list[asyncio.Task]:
        return [task for hold_id, task in running.items()
                if task is not None and not task.done() and not governor.position(hold_id)]

    tasks = graded()
    if tasks:
        logger.info("Shutdown: waiting for %d check(s) being graded, up to %ss (%d queued are left for the next "
                    "start)", len(tasks), timeout, len(governor.queue))
    while tasks:
        left = deadline - loop.time()
        if left <= 0:
            break
        await asyncio.wait(tasks, timeout=min(left, 5.0))  # again every few seconds: a check may queue meanwhile
        tasks = graded()
    left_over = sum(1 for task in running.values() if task is not None and not task.done())
    if left_over:
        logger.info("Shutdown: %d check(s) left for the next start (%d of them queued)", left_over,
                    len(governor.queue))


async def recover(bot: Bot, storage: BaseStorage) -> int:
    """At start-up: the holds a stopped bot left running - coins back, and each student told on the check's
    own card, with "try again" when the answer is still in their FSM data. How many there were."""
    from bot.states.speaking_states import SpeakingStates  # noqa: PLC0415
    from bot.states.writing_states import WritingStates  # noqa: PLC0415

    from bot.utils import crm  # noqa: PLC0415

    crm.use_bot(bot)  # a burst of them is told directly too, before the group's sender runs
    failed = {"w": WritingStates.failed, "s": SpeakingStates.failed}
    processing = {WritingStates.processing.state, SpeakingStates.processing.state}
    async with get_session() as session:
        rows = (await session.execute(select(CheckHold).where(CheckHold.state == "held"))).scalars().all()
    stopped = []
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
        retry = await tell(bot, storage, row, failed.get(row.module), processing,
                           "check.interrupted", "check.interrupted_lost", coins)
        stopped.append((row, coins, retry))
    if rows:
        logger.info("Recovered %d check(s) a stopped bot left running", len(rows))
    await check_incidents.interrupted(stopped)  # one card for them all (2026-11-16: nobody was told)
    return len(rows)


async def _flood_safe(call):
    """A Telegram call that waits out Telegram's flood control as it asks (TelegramRetryAfter), a few times -
    a start-up telling many students at once can meet it."""
    for attempt in range(FLOOD_TRIES + 1):
        try:
            return await call()
        except TelegramRetryAfter as e:
            if attempt == FLOOD_TRIES or e.retry_after + 1 > MAX_FLOOD_WAIT:
                raise
            await asyncio.sleep(e.retry_after + 1)


async def tell(bot: Bot, storage: BaseStorage, row: CheckHold, failed_state, processing: set[str],
               key_retry: str, key_lost: str, coins: int) -> bool:
    """Tells the student their check stopped, on its card: with "try again" when their FSM state is still
    this check's (it is then moved to the module's `failed` state, which the button needs). True when "try
    again" was offered."""
    ctx = FSMContext(storage=storage, key=StorageKey(bot_id=bot.id, chat_id=row.chat_id, user_id=row.user_id))
    retry = False
    try:
        if (await ctx.get_data()).get("check_token") == row.id and await ctx.get_state() in processing \
                and failed_state is not None:
            await ctx.set_state(failed_state)
            retry = True
            await park(row.user_id, ctx)
    except Exception:  # noqa: BLE001
        logger.warning("FSM of user %s not read", row.user_id, exc_info=True)
    note = ("\n\n" + t("check.coins_back", row.lang, coins=coins)) if coins else ""
    text = t(key_retry if retry else key_lost, row.lang) + note
    markup = retry_kb(row.lang, row.id) if retry else None
    try:
        if row.message_id:
            await _flood_safe(lambda: bot.edit_message_text(text, chat_id=row.chat_id, message_id=row.message_id,
                                                            reply_markup=markup))
            return retry
    except TelegramBadRequest:
        pass
    except Exception:  # noqa: BLE001
        logger.warning("Card of check %s not edited", row.id[:8], exc_info=True)
    try:
        await _flood_safe(lambda: bot.send_message(row.chat_id, text, reply_markup=markup))
    except Exception:  # noqa: BLE001 - blocked the bot, etc.
        logger.warning("User %s not told about check %s", row.user_id, row.id[:8], exc_info=True)
    return retry


async def sweep(bot: Bot, storage: BaseStorage) -> int:
    """A hold left `held` by no running check of this process (its end() failed - the database was away):
    settled like a failed check, the student told. Not while shutting down: those holds are the next start's."""
    from bot.states.speaking_states import SpeakingStates  # noqa: PLC0415
    from bot.states.writing_states import WritingStates  # noqa: PLC0415

    if shutting_down:
        return 0
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
        await check_incidents.expired(row, coins)
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
