"""What the admins are reminded of, every minute from the admins' group sender (crm.run_sender ->
crm_events.reminders_due), before the bot opens to everyone (the owner, 2026-11-16: until then a receipt or a
student's question could wait unseen for days, and the student had been told "soon"):

- a receipt waiting for a decision (db/crud.py awaiting_decision): at 20 minutes, at 2 hours (more urgent),
  then every 12 hours while it waits - in 💳 To'lovlar and to every bot admin allowed payments
  (panel_access.telegram_may), each as a reply to that admin's own copy of the review card when it is known;
- a support thread waiting for an answer (crud.support_waiting) for 2 hours: in 💬 Murojaatlar - a reply to
  the reminder goes to the student, as to their own message - and to the support agents (the bot admins and
  the /crm_agents), at most once a thread in 12 hours, and for a week at most (a student who blocked the bot
  can't be answered; the daily report keeps counting the thread);
- the balance warnings, straight to the bot admins (balance.deliver_direct).

A reminder in an admin's private chat comes without a sound at night (QUIET_HOURS); the balance warnings
always ring.

The time a payment has waited is counted from its receipt (RECEIPT_AT, kept when it comes - the request's
created_at is when the pack was tapped), else from the request. Each step is sent once: a Redis flag per step
set before sending (NX, with a TTL) and taken back if the sending fails. Steps missed (the bot down, a receipt
from before this existed) are not sent late one after another: only the one due now. tick() never raises.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone

from aiogram.enums import ParseMode
from aiogram.types import ReplyParameters

from bot.config import settings
from bot.utils import balance, crm, panel_access
from bot.utils.crm_events import _esc, _som, _user
from db import crud
from db.database import get_session
from db.models import PaymentKind, PaymentRequest, SupportMessage

logger = logging.getLogger(__name__)

PAY_FIRST = timedelta(minutes=20)
PAY_URGENT = timedelta(hours=2)
PAY_EVERY = timedelta(hours=12)
SUPPORT_AFTER = timedelta(hours=2)
SUPPORT_EVERY = 12 * 3600  # seconds: one reminder a thread at most this often
SUPPORT_FOR = timedelta(days=7)  # no more reminders for a thread whose last message is older

RECEIPT_AT = "pay:receipt_at:{id}"  # epoch seconds the receipt came (crm_events.payment_receipt)
RECEIPT_KEEP = 60 * 24 * 3600
PAY_FLAG = "remind:pay:{id}:{step}"
SUPPORT_FLAG = "remind:support:{user_id}"
FLAG_KEEP = 2 * 24 * 3600  # a payment step is "current" 12 hours at most

QUIET_HOURS = (23, 8)  # Tashkent: a reminder then arrives without a sound (still there in the morning)

KINDS = {"photo": "🖼 rasm", "voice": "🎤 ovozli xabar", "video": "🎬 video", "video_note": "⏺ video xabar",
         "audio": "🎵 audio", "document": "📎 fayl", "sticker": "stiker", "animation": "GIF"}


async def tick(bot) -> None:
    """Every minute in the bot process (crm.run_sender). Each part on its own: one failing never stops the
    others, nor the daily report."""
    for job in (payment_reminders, support_reminders, balance.deliver_direct):
        try:
            await job(bot)
        except Exception:  # noqa: BLE001 - logged (to the errors topic too), tried again a minute later
            logger.exception("Admin reminders: %s failed", getattr(job, "__name__", job))


def waited(seconds: float) -> str:
    """"25 daqiqa", "2 soat 5 daqiqa", "3 kun 4 soat" (ends with its unit: "...dan beri" reads right)."""
    minutes = max(0, int(seconds // 60))
    if minutes < 60:
        return f"{minutes} daqiqa"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} soat" + (f" {minutes} daqiqa" if minutes else "")
    days, hours = divmod(hours, 24)
    return f"{days} kun" + (f" {hours} soat" if hours else "")


def quiet() -> bool:
    hour = crm.local_now().hour
    return hour >= QUIET_HOURS[0] or hour < QUIET_HOURS[1]


async def _send(bot, chat_id: int, text: str, reply_to: int | None = None) -> bool:
    kwargs = {}
    if reply_to:
        kwargs["reply_parameters"] = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True)
    try:
        await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True,
                               disable_notification=quiet(), **kwargs)
        return True
    except Exception as e:  # noqa: BLE001 - blocked the bot, never started it
        logger.info("Admin reminder to %s not sent: %r", chat_id, e)
        return False


# --- 💳 payments ---------------------------------------------------------------------------------------------


async def receipt_sent(request_id: int) -> None:
    """A receipt came now (crm_events.payment_receipt): the reminders count from it. Never raises."""
    try:
        await crm.redis().set(RECEIPT_AT.format(id=request_id), str(int(time.time())), ex=RECEIPT_KEEP)
    except Exception as e:  # noqa: BLE001 - the request's own time is counted from then
        logger.warning("Payment #%s: the receipt's time not kept (%r)", request_id, e)


async def _receipt_times(requests: list[PaymentRequest]) -> dict[int, datetime]:
    raw = await crm.redis().mget([RECEIPT_AT.format(id=p.id) for p in requests])
    out = {}
    for p, value in zip(requests, raw):
        try:
            out[p.id] = datetime.fromtimestamp(float(value), timezone.utc) if value else p.created_at
        except (TypeError, ValueError):
            out[p.id] = p.created_at
    return out


def pay_step(waited_for: timedelta) -> str | None:
    """The reminder due after `waited_for`: "20m", "2h", then "12h-1" (at 14 h), "12h-2" (at 26 h), ...; None
    before the first."""
    if waited_for < PAY_FIRST:
        return None
    if waited_for < PAY_URGENT:
        return "20m"
    n = int((waited_for - PAY_URGENT) / PAY_EVERY)
    return "2h" if n == 0 else f"12h-{n}"


def _item(request: PaymentRequest) -> str:
    if request.kind == PaymentKind.subscription:
        return f"{request.subscription_months} oylik obuna"
    if request.kind == PaymentKind.gift:
        return f"🎁 {request.coins} coin (sovg'a)"
    return f"{request.coins} coin"


def payment_text(request: PaymentRequest, step: str, waited_s: float) -> str:
    if step == "20m":
        head = "⏰ <b>To'lov tasdiq kutmoqda</b>"
    elif step == "2h":
        head = "🚨 <b>To'lov 2 soatdan beri tasdiq kutmoqda!</b>"
    else:
        head = "🚨 <b>To'lov hali ham ko'rib chiqilmagan</b>"
    lines = [
        f"{head} · #{request.id}",
        f"👤 {_user(request.user, request.user_id)}",
        f"📦 {_item(request)} — <b>{_som(request.amount)} so'm</b>",
        f"⏱ Chek kelganiga {waited(waited_s)} bo'ldi",
    ]
    if step != "20m":
        lines.append("O'quvchi coinlarini kutyapti — iltimos, ko'rib chiqing.")
    lines.append(f"👉 {settings.panel_url}/payments?status=pending")
    return "\n".join(lines)


async def _review_cards(request_id: int) -> dict[int, int]:
    """{admin id: message id} of the admins' copies of the review card (bot/handlers/payments.py)."""
    from bot.handlers.payments import REVIEW_KEY  # noqa: PLC0415 - a handler module: only when needed

    try:
        raw = await crm.redis().get(REVIEW_KEY.format(id=request_id))
        return {int(chat): int(message) for chat, message in (json.loads(raw).get("msgs") or [])} if raw else {}
    except Exception:  # noqa: BLE001 - the reminder goes without the reply
        return {}


async def payment_reminders(bot, now: datetime | None = None) -> int:
    """The receipts waiting for a decision whose next reminder is due: each step once. Returns how many went."""
    now = now or datetime.now(timezone.utc)
    async with get_session() as session:
        waiting = await crud.pending_payments(session)
    if not waiting:
        return 0
    since = await _receipt_times(waiting)
    r = crm.redis()
    admins: list[int] | None = None
    sent = 0
    for request in waiting:
        step = pay_step(now - since[request.id])
        if step is None:
            continue
        flag = PAY_FLAG.format(id=request.id, step=step)
        if not await r.set(flag, "1", nx=True, ex=FLAG_KEEP):
            continue
        try:
            text = payment_text(request, step, (now - since[request.id]).total_seconds())
            await crm.post("payments", text)
            if admins is None:
                admins = [a for a in sorted(settings.admin_ids) if await panel_access.telegram_may(a, "payments")]
            cards = await _review_cards(request.id)
            for admin_id in admins:
                await _send(bot, admin_id, text, reply_to=cards.get(admin_id))
        except Exception:
            await r.delete(flag)  # not sent: the next minute tries again
            raise
        sent += 1
        logger.info("Payment #%s: reminder %s sent", request.id, step)
    return sent


# --- 💬 support ----------------------------------------------------------------------------------------------


async def support_agents() -> list[int]:
    """Who answers students (crm.may_answer): the bot admins and the /crm_agents - those named by @username are
    found among the bot's users (only someone who has started the bot can get a message from it anyway)."""
    ids = set(settings.admin_ids)
    names = set()
    for agent in (crm.config() or {}).get("agents", []):
        agent = str(agent).lower()
        if agent.isdigit():
            ids.add(int(agent))
        elif agent:
            names.add(agent)
    if names:
        async with get_session() as session:
            ids |= await crud.user_ids_by_usernames(session, names)
    return sorted(ids)


def _preview(message: SupportMessage | None) -> str:
    if message is None:
        return "…"
    text = " ".join((message.text or "").split())
    if len(text) > 150:
        text = text[:150].rstrip() + "…"
    parts = [KINDS[message.kind]] if message.kind in KINDS else []
    if text:
        parts.append(f"«{_esc(text)}»")
    return " ".join(parts) or "…"


def support_text(thread: crud.WaitingThread, user, last: SupportMessage | None, waited_s: float) -> str:
    first = waited_s < (SUPPORT_AFTER.total_seconds() + SUPPORT_EVERY)
    head = "💬 <b>Murojaat javobsiz qoldi</b>" if first else "💬 <b>Murojaat hali ham javobsiz</b>"
    lines = [
        f"{head} · {waited(waited_s)}dan beri",
        f"👤 {_user(user, thread.user_id)}",
        f"✉️ {_preview(last)}" + (f" <i>(jami {thread.messages} ta xabar)</i>" if thread.messages > 1 else ""),
        f"<i>↩️ Javob uchun shu xabarga reply qiling</i> · panel: {settings.panel_url}/support/{thread.user_id}",
    ]
    return "\n".join(lines)


async def support_reminders(bot, now: datetime | None = None) -> int:
    """The threads waiting for an answer for SUPPORT_AFTER: one reminder each (at most once in SUPPORT_EVERY)
    in the group, and one message listing them to each agent. Returns how many threads were reminded of."""
    now = now or datetime.now(timezone.utc)
    async with get_session() as session:
        threads = [t for t in await crud.support_waiting(session)
                   if now - t.since >= SUPPORT_AFTER and now - t.last_at <= SUPPORT_FOR]
        if not threads:
            return 0
        r = crm.redis()
        due = []
        for thread in threads:
            if await r.set(SUPPORT_FLAG.format(user_id=thread.user_id), "1", nx=True, ex=SUPPORT_EVERY):
                due.append((thread, await crud.get_user(session, thread.user_id),
                            await session.get(SupportMessage, thread.last_id)))
    if not due:
        return 0
    try:
        lines = []
        for thread, user, last in due:
            waited_s = (now - thread.since).total_seconds()
            # posted as the student's message: a reply to it in the group goes to them (bot/handlers/crm.py)
            await crm.post("support", support_text(thread, user, last, waited_s), support_user=thread.user_id)
            name = _esc(user.full_name if user is not None else thread.user_id)
            lines.append(f"• <b>{name}</b> — {waited(waited_s)}dan beri: {_preview(last)}")
        link = (f"{settings.panel_url}/support/{due[0][0].user_id}" if len(due) == 1
                else f"{settings.panel_url}/support?f=waiting")
        text = "\n".join([f"💬 <b>Javob kutayotgan murojaatlar: {len(due)} ta</b>", *lines, "",
                          "↩️ Javob: adminlar guruhida, «💬 Murojaatlar» bo'limida xabarga reply qiling yoki "
                          f"panelda: {link}"])
        for agent in await support_agents():
            await _send(bot, agent, text)
    except Exception:
        for thread, _, _ in due:
            await r.delete(SUPPORT_FLAG.format(user_id=thread.user_id))  # not sent: tried again a minute later
        raise
    logger.info("Support reminders: %s thread(s)", len(due))
    return len(due)
