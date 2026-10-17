"""What the admins' group (bot/utils/crm.py) is told. Each event is called
where the thing happens, after the student has been answered, and never
raises: a failure is only logged - and the group's own log lines are never
forwarded to the group, so that can't loop.

Cards are built from the database, so the same card serves a live event, a
later edit (a payment decided, a voice made) and the one-time backfill of
everything before the group was set up.
"""
from __future__ import annotations

import functools
import html
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from bot.config import settings
from bot.utils import crm
from db import crud
from db.database import get_session
from db.models import (
    FULL_TEST_CHECKS,
    SPEAKING_LABELS,
    PaymentKind,
    PaymentRequest,
    PaymentStatus,
    SpeakingSubmission,
    User,
    UserRole,
    WritingSubmission,
)
from services.ai.essay_marking import count_words

logger = logging.getLogger(__name__)

WEB_REVIEWER_ID = -1  # webapp/main.py's reviewer id for decisions made in the web panel
WEEKDAYS = ["dushanba", "seshanba", "chorshanba", "payshanba", "juma", "shanba", "yakshanba"]
SOURCES = {"text": "matn", "photo": "rasm", "pdf": "PDF", "docx": "Word", "audio": "ovoz"}
PAID_NAMES = {
    "free": "bepul", "coins": "coin", "credit": "kredit (eski)", "subscription": "obuna", "sponsor": "homiy",
    "test": "test", "unknown": "noma'lum",
}
# Before coins (2026-10-17): every user had 2 free checks, and a paid check
# was 1 credit = 5000 so'm - for reading old checks' "paid_with".
LEGACY_FREE_CHECKS = 2
LEGACY_CREDIT_PRICE = 5000
# OpenAI call purposes (services/ai/*) in plain words
PURPOSES = {
    "feedback": "hisobot matni",
    "score": "ball qo'yish",
    "self_check": "ballni qayta tekshirish",
    "missed_errors": "o'tkazib yuborilgan xatolar",
    "verify": "xatolarni tasdiqlash",
    "model_answer": "namuna javob",
    "answer_check": "namuna javobni tekshirish",
    "audio_listen": "ovozni tinglash (talaffuz)",
    "transcribe": "nutqni matnga o'girish",
    "transcribe_check": "nutqni qayta o'qish",
    "fillers": "to'ldiruvchi so'zlar",
    "tts": "namuna javob ovozi",
    "ocr": "rasmdan matn o'qish",
    "embedding": "o'xshash namuna qidirish",
    "label": "namuna turini aniqlash",
    "style_profile": "muallif uslubi",
    "practise_words": "mashq so'zlari",
}
# usage.take_spent() categories in the daily report
CATEGORIES = {
    "writing": "Writing tekshiruvlari",
    "speaking": "Speaking tekshiruvlari",
    "tts": "Namuna javob ovozlari",
    "input": "Savolni o'qish (rasm, fayl, ovoz)",
    "practice": "Practice import (kanallardan savollar)",
    "vocab_translate": "Lug'at: tarjima",
    "vocab_describe": "Lug'at: izoh",
}


def _safe(fn):
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        if not crm.ready():
            return None
        try:
            return await fn(*args, **kwargs)
        except Exception:  # noqa: BLE001
            logger.warning("Admins' group: %s failed", fn.__name__, exc_info=True)
            return None

    return wrapper


def _esc(text) -> str:
    return html.escape(str(text) if text is not None else "?", quote=False)


def _who(user_id: int, full_name: str | None, username: str | None = None) -> str:
    parts = [f'<a href="tg://user?id={user_id}">{_esc(full_name or "?")}</a>']
    if username:
        parts.append(f"@{_esc(username)}")
    parts.append(f"<code>{user_id}</code>")
    return " · ".join(parts)


def _user(user: User | None, user_id: int | None = None) -> str:
    if user is None:
        return f"<code>{user_id}</code> (bazada yo'q)"
    return _who(user.id, user.full_name, user.username)


def _when(dt: datetime | None) -> str:
    return crm.local(dt).strftime("%d.%m.%Y %H:%M") if dt else "?"


def _now() -> str:
    return _when(datetime.now(timezone.utc))


def _som(amount: float) -> str:
    return f"{round(amount):,}".replace(",", " ")


def _usd(value: float) -> str:
    return f"${value:.4f}" if value < 10 else f"${value:.2f}"


def _num(n) -> str:
    return f"{int(n):,}".replace(",", " ")


def _duration(seconds: float) -> str:
    minutes, sec = divmod(int(round(seconds)), 60)
    return f"{minutes} daq {sec:02d} s" if minutes else f"{sec} s"


def _band(value) -> str:
    return f"{value:.1f}" if isinstance(value, (int, float)) else "?"


def _subscribed(user: User | None) -> bool:
    return bool(user and user.subscription_expires_at and user.subscription_expires_at > datetime.now(timezone.utc))


# --- 🆕 Ro'yxatdan o'tganlar ---------------------------------------------------------------------


@_safe
async def user_registered(tg, referrer_id: int | None, start_arg: str | None, join=None, center_join=None) -> None:
    """A first /start (bot/handlers/start.py): `join` is its group invite's
    GroupJoin, `center_join` its center invite's (status, center)."""
    async with get_session() as session:
        total = await crud.count_users(session)
        referrer = await crud.get_user(session, referrer_id) if referrer_id else None
    lines = [f"🆕 <b>Yangi foydalanuvchi #{total}</b>", f"👤 {_who(tg.id, tg.full_name, tg.username)}"]
    lines.append(f"🌐 Telegram tili: {_esc(tg.language_code or '?')}" + (" · ⭐ Premium" if tg.is_premium else ""))
    if referrer is not None:
        lines.append(f"🔗 Referal orqali: {_user(referrer)}")
    if join is not None:
        lines.append(
            f"🏫 Guruh havolasi: «{_esc(join.group_name)}» · o'qituvchi {_esc(join.teacher_name)}"
            if join.group_name else "🏫 Eskirgan guruh havolasi"
        )
    if center_join is not None:
        lines.append(f"🏢 Markaz havolasi: «{_esc(center_join[1].name if center_join[1] else '?')}»")
    if len(lines) == 3:
        lines.append(
            f"🔗 Havola: <code>start={_esc(start_arg)}</code>" if start_arg and not start_arg.startswith("ref_")
            else "📍 To'g'ridan-to'g'ri (havolasiz)"
        )
    lines.append(f"🕒 {_now()}")
    await crm.post("users", "\n".join(lines), key=f"user:{tg.id}")


def _user_history_card(user: User, n: int, activity: dict, referrer: User | None) -> str:
    a = activity.get(user.id, {})
    lines = [f"👤 <b>#{n}</b> {_user(user)}", f"🕒 Ro'yxatdan o'tgan: {_when(user.created_at)} · til: {user.language}"]
    if referrer is not None:
        lines.append(f"🔗 Referal orqali: {_user(referrer)}")
    if user.role != UserRole.student:
        role = "admin" if user.role == UserRole.admin else "o'qituvchi"
        lines.append(f"🎓 Rol: {role}")
    lines.append(
        f"📊 Writing {a.get('writing', 0)} · Speaking {a.get('speaking', 0)} · to'lagan {_som(a.get('paid', 0))} so'm"
        f" · balans {user.coin_balance} coin"
    )
    if _subscribed(user):
        lines.append(f"♾ Obuna: {_when(user.subscription_expires_at)} gacha")
    return "\n".join(lines)


# --- 💳 To'lovlar --------------------------------------------------------------------------------

_PAY_STATUS = {
    PaymentStatus.pending: "⏳ Ko'rib chiqilmoqda",
    PaymentStatus.approved: "✅ Tasdiqlandi",
    PaymentStatus.rejected: "⛔ Rad etildi",
}


async def _payment_card(session, request: PaymentRequest) -> str:
    user = await crud.get_user(session, request.user_id)
    item = (
        f"{request.subscription_months} oylik cheksiz obuna" if request.kind == PaymentKind.subscription
        else f"{request.coins} coin"
    )
    lines = [
        f"💳 <b>To'lov #{request.id}</b> · {_PAY_STATUS[request.status]}",
        f"👤 {_user(user, request.user_id)}",
        f"📦 {item} — <b>{_som(request.amount)} so'm</b>",
        f"🕒 So'rov: {_when(request.created_at)}",
    ]
    if request.receipt_file_id is None:
        lines.append("🧾 Chek yuborilmagan")
    if request.status != PaymentStatus.pending:
        if request.reviewed_by == WEB_REVIEWER_ID:
            by = "web panel"
        else:
            reviewer = await crud.get_user(session, request.reviewed_by) if request.reviewed_by else None
            by = _esc(reviewer.full_name) if reviewer else _esc(request.reviewed_by)
        verb = "✅ Tasdiqladi" if request.status == PaymentStatus.approved else "⛔ Rad etdi"
        lines.append(f"{verb}: {by} · {_when(request.reviewed_at)}")
    if user is not None:
        balance = f"💼 Balans: {user.coin_balance} coin"
        if _subscribed(user):
            balance += f" · ♾ obuna {_when(user.subscription_expires_at)} gacha"
        lines.append(balance)
    return "\n".join(lines)


@_safe
async def payment_receipt(request_id: int) -> None:
    """A receipt photo was sent (bot/handlers/payments.py); the admins still
    decide in their private chats."""
    async with get_session() as session:
        request = await session.get(PaymentRequest, request_id)
        text = await _payment_card(session, request)
    await crm.post("payments", text, photo=request.receipt_file_id, key=f"pay:{request_id}")


@_safe
async def payment_decided(request_id: int) -> None:
    """Approved or rejected - in the bot or the web panel: the card changes."""
    async with get_session() as session:
        request = await session.get(PaymentRequest, request_id)
        text = await _payment_card(session, request)
    await crm.edit(f"pay:{request_id}", "payments", text)


# --- ✍️ Writing / 🎙 Speaking / 💰 Xarajatlar ------------------------------------------------------


async def _old_payment(session, sub) -> dict:
    """How an old check (saved before "paid_with" was) was most likely paid."""
    credits = FULL_TEST_CHECKS if getattr(sub, "part", None) == "full" else 1
    if sub.paid_by_id:
        return {"mode": "sponsor", "credits": credits, "payer_id": sub.paid_by_id}
    if await crud.checks_before(session, sub.user_id, sub.created_at) < LEGACY_FREE_CHECKS:
        return {"mode": "free", "credits": 0}
    return {"mode": "unknown"}


def _paid_line(paid: dict, user: User | None, payer: User | None) -> tuple[str, int | None]:
    """(the line, so'm this check brought in - None when unknown). Revenue
    is estimated at the nominal coin price (settings.coin_price; packs sell
    coins up to 28% cheaper); checks from before coins carry "credits"."""
    mode = paid.get("mode")
    if "coins" in paid:
        n, unit, price = paid.get("coins") or 0, "coin", settings.coin_price
    else:
        n, unit, price = paid.get("credits") or 0, "kredit", LEGACY_CREDIT_PRICE
    if mode == "coins":
        left = user.coin_balance if user is not None else "?"
        return f"🟡 Coin bilan: {n} coin · balansda {left} coin", n * price
    if mode == "credit":
        left = user.coin_balance if user is not None else "?"
        return f"💳 Kredit bilan (eski): {n} ta · hozir balansda {left} coin", n * price
    if mode == "sponsor":
        left = payer.coin_balance if payer is not None else "?"
        return f"🏫 Homiy to'ladi: {_user(payer, paid.get('payer_id'))} · {n} {unit} (qoldi {left} coin)", n * price
    if mode == "subscription":
        return "♾ Obuna (cheksiz)", 0
    if mode == "free":
        return "🎁 Bepul tekshiruv", 0
    if mode == "test":
        return "🧪 Test oynasi (bepul)", 0
    return "💳 Kredit yoki obuna (eski yozuv - aniq saqlanmagan)", None


def _purpose(purpose: str) -> str:
    prefix = ""
    if purpose.startswith("input: "):
        prefix, purpose = "savolni o'qish: ", purpose.removeprefix("input: ")
    return f"{prefix}{PURPOSES.get(purpose, purpose)} <code>{_esc(purpose)}</code>"


def _detail_line(purpose: str, d: dict) -> str:
    models = d.get("models") or []
    calls, flex = d.get("calls", 0), d.get("flex", 0)
    tier = ""
    if flex:
        tier = " · flex" if flex == calls else f" · flex {flex}/{calls}"
    elif any(m.startswith("gpt-5") and not m.startswith(("gpt-5-mini", "gpt-5-nano")) for m in models):
        tier = " · oddiy narx"
    tokens = []
    if d.get("in"):
        tokens.append(f"kirish {_num(d['in'])}" + (f" (keshdan {_num(d['cached'])})" if d.get("cached") else ""))
    if d.get("out"):
        tokens.append(
            f"chiqish {_num(d['out'])}" + (f" (shundan fikrlash {_num(d['reasoning'])})" if d.get("reasoning") else "")
        )
    if d.get("audio_s"):
        tokens.append(f"{d['audio_s']:.0f} s audio")
    line = f"• {_purpose(purpose)} — <b>{_usd(d.get('usd', 0.0))}</b> · {calls}× {_esc(', '.join(models))}{tier}"
    return line + ("\n    " + " · ".join(tokens) if tokens else "")


def _cost_card(head: str, cost: dict, seconds, paid_text: str, revenue: int | None, rate: float) -> str:
    total = cost.get("total_usd", 0.0)
    total_som = total * rate
    lines = [
        f"💰 {head}",
        f"Jami: <b>{_usd(total)}</b> ≈ <b>{_som(total_som)} so'm</b> · {cost.get('calls', 0)} ta so'rov"
        + (f" · ⏱ {_duration(seconds)}" if seconds else ""),
        "",
    ]
    details = cost.get("details")
    if details:
        lines.extend(_detail_line(purpose, d) for purpose, d in details.items())
    else:
        lines.extend(f"• {_purpose(p)} — <b>{_usd(v)}</b>" for p, v in (cost.get("by_purpose") or {}).items())
    lines.append("")
    if revenue is None:
        lines.append("💵 Tushum: noma'lum (eski yozuv)")
    elif revenue == 0:
        lines.append(f"💵 Tushum yo'q ({_esc(paid_text)}) → sof xarajat {_som(total_som)} so'm")
    else:
        lines.append(f"💵 Tushum {_som(revenue)} so'm → foyda ≈ <b>{_som(revenue - total_som)} so'm</b>")
    lines.append(f"<i>1 $ = {_som(rate)} so'm</i>")
    return "\n".join(lines)


async def _check_cards(kind: str, submission_id: int, rate: float | None = None):
    """(card, cost card or None, PDF path, PDF file name) of a saved check."""
    model = WritingSubmission if kind == "w" else SpeakingSubmission
    async with get_session() as session:
        sub = await session.get(model, submission_id)
        if sub is None:
            return None
        user = await crud.get_user(session, sub.user_id)
        data = sub.report_data or {}
        paid = data.get("paid_with") or await _old_payment(session, sub)
        payer = await crud.get_user(session, paid["payer_id"]) if paid.get("payer_id") else None
        essay_number = await crud.writing_essay_number(session, sub.user_id, sub.id) if kind == "w" else None
        homework = None
        if sub.assignment_part_id:
            found = await crud.get_part(session, sub.assignment_part_id)
            if found is not None:
                group = await crud.get_group(session, found[1].group_id)
                homework = (found[1].title, group.name if group else "?")
    rate = rate or await crm.usd_rate()
    name = user.full_name if user else str(sub.user_id)

    if kind == "w":
        task1 = _value(sub.task_type) == "task1"
        label = "Writing Task 1" if task1 else "Writing Task 2"
        first = "TA" if task1 else "TR"
        scores = sub.criteria_scores or {}
        lines = [
            f"✍️ <b>{label}</b> · #{sub.id} · {essay_number}-insho",
            f"👤 {_user(user, sub.user_id)}",
            f"🏅 <b>Band {_band(sub.overall_band)}</b> · {first} {_band(scores.get('TA'))} · "
            f"CC {_band(scores.get('CC'))} · LR {_band(scores.get('LR'))} · GRA {_band(scores.get('GRA'))}",
            f"📝 {count_words(sub.answer_text)} so'z · savol: {SOURCES.get(_value(sub.prompt_source), '?')}, "
            f"javob: {SOURCES.get(_value(sub.answer_source), '?')}",
        ]
        sample = data.get("model_answer")
        if sample:
            lines.append(f"🧩 Namuna javob: Band {_band(sample.get('aimed_at'))} ga mo'ljallangan")
    else:
        label = SPEAKING_LABELS.get(_value(sub.part), "Speaking")
        scores = sub.criteria_scores or {}
        estimated = " (taxminiy)" if data.get("pronunciation_measured") is False else ""
        lines = [
            f"🎙 <b>{label}</b> · #{sub.id} · {data.get('number') or '?'}-sessiya",
            f"👤 {_user(user, sub.user_id)}",
            f"🏅 <b>Band {_band(sub.overall_band)}</b> · FC {_band(scores.get('FC'))} · LR {_band(scores.get('LR'))} · "
            f"GRA {_band(scores.get('GRA'))} · P {_band(scores.get('Pronunciation'))}{estimated}",
        ]
        metrics = data.get("metrics") or {}
        if metrics:
            lines.append(
                f"🗣 {len(data.get('questions') or []) or 1} ta javob · {_duration(metrics.get('speaking_seconds', 0))} nutq"
                f" · {metrics.get('words', 0)} so'z · {metrics.get('wpm', 0)} so'z/daq"
                f" · {metrics.get('fillers', 0)} ta to'ldiruvchi so'z"
            )
        answers = data.get("model_answers") or []
        if answers:
            voices = sum(1 for a in answers if a.get("voice_file_id"))
            lines.append(
                f"🧩 {len(answers)} ta namuna javob (Band {_band(answers[0].get('aimed_at'))})"
                + (f" · 🔊 {voices} tasi tinglandi" if voices else "")
            )

    paid_text, revenue = _paid_line(paid, user, payer)
    lines.append(paid_text)
    cost = data.get("cost")
    seconds = data.get("seconds")
    summary = []
    if seconds:
        summary.append(f"⏱ Kutish {_duration(seconds)}")
    if cost:
        summary.append(f"💰 {_usd(cost.get('total_usd', 0.0))} ≈ {_som(cost.get('total_usd', 0.0) * rate)} so'm")
    if summary:
        lines.append(" · ".join(summary))
    if data.get("resubmission_of"):
        lines.append(f"🔁 Avvalgi #{data['resubmission_of']} bilan deyarli bir xil")
    if sub.practice_question_id:
        lines.append("📚 Practice savoli")
    if homework is not None:
        lines.append(f"🏫 Vazifa: «{_esc(homework[0])}» · guruh «{_esc(homework[1])}»")
    lines.append(f"🕒 {_when(sub.created_at)}")

    cost_card = None
    if cost:
        head = f"<b>{label} · #{sub.id}</b> · {_esc(name)}"
        cost_card = _cost_card(head, cost, seconds, PAID_NAMES.get(paid.get("mode"), "?"), revenue, rate)
    pdf = sub.feedback_pdf_path
    filename = f"{'writing' if kind == 'w' else 'speaking'}_{sub.id}_{sub.user_id}.pdf"
    return "\n".join(lines), cost_card, pdf, filename


def _value(x) -> str:
    return getattr(x, "value", x)


@_safe
async def check_done(kind: str, submission_id: int) -> None:
    """A new report was sent ("w" Writing, "s" Speaking): its card with the
    PDF, and its cost in 💰 Xarajatlar."""
    built = await _check_cards(kind, submission_id)
    if built is None:
        return
    card, cost_card, pdf, filename = built
    await crm.post(
        "writing" if kind == "w" else "speaking", card, document=pdf, filename=filename, key=f"check:{kind}:{submission_id}"
    )
    if cost_card:
        await crm.post("costs", cost_card, key=f"cost:{kind}:{submission_id}")


@_safe
async def cost_updated(kind: str, submission_id: int) -> None:
    """A model answer was spoken on demand: the check's cards show its cost."""
    built = await _check_cards(kind, submission_id)
    if built is None:
        return
    card, cost_card, _, _ = built
    await crm.edit(f"check:{kind}:{submission_id}", "writing" if kind == "w" else "speaking", card)
    if cost_card:
        await crm.edit(f"cost:{kind}:{submission_id}", "costs", cost_card)


@_safe
async def check_repeated(kind: str, user_id: int, earlier_id: int) -> None:
    """The same essay / recordings again: the saved report went out, no new check."""
    model = WritingSubmission if kind == "w" else SpeakingSubmission
    async with get_session() as session:
        user = await crud.get_user(session, user_id)
        earlier = await session.get(model, earlier_id)
    what = "insho" if kind == "w" else "yozuvlar"
    band = _band(earlier.overall_band) if earlier is not None else "?"
    text = (
        f"🔁 <b>Qayta yuborildi</b> · {_user(user, user_id)}\n"
        f"O'sha {what}: saqlangan hisobot (#{earlier_id}, Band {band}) qayta berildi - yangi tekshiruv "
        f"qilinmadi, pul ketmadi.\n🕒 {_now()}"
    )
    await crm.post("writing" if kind == "w" else "speaking", text)


# --- 👨‍🏫 O'qituvchi va guruhlar ---------------------------------------------------------------------


async def _teachers(text: str, key: str | None = None) -> None:
    await crm.post("teachers", f"{text}\n🕒 {_now()}", key=key)


@_safe
async def teacher_request(tg) -> None:
    await _teachers(
        f"🙋 <b>O'qituvchi bo'lish so'rovi</b> · ⏳ kutilmoqda\n👤 {_who(tg.id, tg.full_name, tg.username)}\n"
        "Tasdiqlash tugmalari adminlarning shaxsiy chatida.",
        key=f"teacher:{tg.id}",
    )


@_safe
async def teacher_decided(user_id: int, approve: bool, already: bool, by_name: str) -> None:
    async with get_session() as session:
        user = await crud.get_user(session, user_id)
    status = "ℹ️ allaqachon o'qituvchi edi" if already else ("✅ Tasdiqlandi" if approve else "⛔ Rad etildi")
    await crm.edit(
        f"teacher:{user_id}", "teachers",
        f"🙋 <b>O'qituvchi bo'lish so'rovi</b> · {status}\n👤 {_user(user, user_id)}\n"
        f"Qaror: {_esc(by_name)} · {_now()}",
    )


@_safe
async def teacher_revoked(user_id: int) -> None:
    async with get_session() as session:
        user = await crud.get_user(session, user_id)
    await _teachers(f"🚫 <b>O'qituvchilik huquqi olindi</b> (web panel)\n👤 {_user(user, user_id)}")


@_safe
async def group_created(group_id: int) -> None:
    async with get_session() as session:
        group = await crud.get_group(session, group_id)
        teacher = await crud.get_user(session, group.teacher_id)
        center = await crud.get_center(session, group.center_id) if group.center_id else None
    lines = [f"🏫 <b>Yangi guruh</b>: «{_esc(group.name)}» (#{group.id})", f"👨‍🏫 O'qituvchi: {_user(teacher)}"]
    if center is not None:
        lines.append(f"🏢 Markaz: «{_esc(center.name)}»")
    await _teachers("\n".join(lines))


@_safe
async def group_deleted(group_name: str, by) -> None:
    await _teachers(f"🗑 <b>Guruh o'chirildi</b>: «{_esc(group_name)}»\n👤 {_who(by.id, by.full_name, by.username)}")


@_safe
async def group_joined(tg, group_name: str, teacher_name: str, members: int) -> None:
    await _teachers(
        f"➕ {_who(tg.id, tg.full_name, tg.username)}\n«{_esc(group_name)}» guruhiga qo'shildi · o'qituvchi "
        f"{_esc(teacher_name)} · a'zolar: {members}"
    )


@_safe
async def group_left(group_name: str, student_id: int, by=None) -> None:
    async with get_session() as session:
        student = await crud.get_user(session, student_id)
    how = "guruhdan chiqdi" if by is None else f"guruhdan chiqarildi ({_esc(by.full_name)})"
    await _teachers(f"➖ {_user(student, student_id)}\n«{_esc(group_name)}» {how}")


@_safe
async def center_joined(tg, center_name: str) -> None:
    await _teachers(
        f"🏢 {_who(tg.id, tg.full_name, tg.username)}\n«{_esc(center_name)}» markaziga o'qituvchi bo'lib qo'shildi"
    )


@_safe
async def center_teacher_removed(center_name: str, teacher_id: int, by) -> None:
    async with get_session() as session:
        teacher = await crud.get_user(session, teacher_id)
    await _teachers(
        f"🏢 {_user(teacher, teacher_id)}\n«{_esc(center_name)}» markazidan chiqarildi ({_esc(by.full_name)})"
    )


@_safe
async def center_created(owner_id: int, name: str) -> None:
    async with get_session() as session:
        owner = await crud.get_user(session, owner_id)
    await _teachers(f"🏢 <b>Yangi markaz</b> (web panel): «{_esc(name)}»\n👤 Egasi: {_user(owner, owner_id)}")


@_safe
async def coins_gifted(group_name: str, by, students: list, amount: int) -> None:
    names = ", ".join(_esc(s.full_name) for s in students[:10]) + (" ..." if len(students) > 10 else "")
    await _teachers(
        f"🎁 <b>Coin sovg'a qilindi</b> · guruh «{_esc(group_name)}»\n👤 {_who(by.id, by.full_name, by.username)}\n"
        f"{len(students)} ta o'quvchiga {amount} coindan (jami {amount * len(students)}): {names}"
    )


@_safe
async def assignment_created(group_name: str, kind: str, title: str, due_at: datetime, by, sent: int) -> None:
    what = "Mock test" if kind == "mock" else "Uy vazifasi"
    await _teachers(
        f"📝 <b>{what}</b> · guruh «{_esc(group_name)}»\n«{_esc(title)}»\n"
        f"👤 {_who(by.id, by.full_name, by.username)} · muddat {_when(due_at)} · {sent} ta o'quvchiga yuborildi"
    )


# --- 💬 Murojaatlar --------------------------------------------------------------------------------


@_safe
async def support_message(message) -> None:
    """A student's message to the admins (bot/handlers/support.py); a reply to
    it in the group goes back to them (bot/handlers/crm.py)."""
    tg = message.from_user
    async with get_session() as session:
        user = await crud.get_user(session, tg.id)
        checks = await crud.get_total_submission_count(session, tg.id)
    head = [
        f"💬 <b>Murojaat</b> · {_who(tg.id, tg.full_name, tg.username)}",
        f"📊 Tekshiruvlar: {checks} · balans: {user.coin_balance if user else 0} coin"
        f" · til: {user.language if user else '?'}",
        "<i>↩️ Javob berish uchun shu xabarga reply qiling</i>",
    ]
    if message.text:
        await crm.post("support", "\n".join(head) + "\n\n" + _esc(message.text), support_user=tg.id)
        return
    await crm.post("support", "\n".join(head) + "\n⬇️ <i>xabari quyida</i>", support_user=tg.id)
    await crm.post("support", "", copy_from=(message.chat.id, message.message_id), support_user=tg.id)


# --- 📥 Practice import (importer service) ----------------------------------------------------------


async def import_done(result: dict) -> None:
    """After an import run (services/practice_import/runner.py): its OpenAI
    spend goes to the day's totals, and to 💰 Xarajatlar when there was any."""
    try:
        spent = await crm.flush_spent()
        total = sum(spent.values())
        if total <= 0 or not crm.ready():
            return
        rate = await crm.usd_rate()
        lines = [
            "📥 <b>Practice import</b> (kanallardan yangi savollar)",
            f"Jami: <b>{_usd(total)}</b> ≈ <b>{_som(total * rate)} so'm</b>",
        ]
        for part, counts in result.items():
            if counts:
                lines.append(f"• {part}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
        lines.append(f"🕒 {_now()}")
        await crm.post("costs", "\n".join(lines))
    except Exception:  # noqa: BLE001
        logger.warning("Admins' group: import_done failed", exc_info=True)


# --- 📊 Kunlik hisobot --------------------------------------------------------------------------------


async def daily_report_if_due() -> None:
    """Yesterday's report, once, from 00:05 Tashkent time on."""
    if not crm.ready():
        return
    now = crm.local_now()
    if now.hour == 0 and now.minute < 5:
        return
    day = now.date() - timedelta(days=1)
    if not await crm.redis().set(f"crm:daily:{day.isoformat()}", "1", nx=True, ex=60 * 24 * 3600):
        return
    try:
        await crm.post("daily", await daily_report(day))
    except Exception:
        await crm.redis().delete(f"crm:daily:{day.isoformat()}")  # tried again a minute later
        raise


async def daily_report(day: date, partial: bool = False) -> str:
    start = datetime(day.year, day.month, day.day, tzinfo=crm.TZ)
    end = start + timedelta(days=1)
    async with get_session() as session:
        new_users = await crud.count_users(session, start, end)
        total_users = await crud.count_users(session, None, end)
        writing, speaking = await crud.checks_between(session, start, end)
        decided = await crud.payments_decided_between(session, start, end)
        pending = await crud.count_pending_payments(session)
    r = crm.redis()
    spend = {k: float(v) for k, v in (await r.hgetall(crm.spend_key(day))).items()}
    errors = int(await r.get(f"crm:errors:{day.isoformat()}") or 0)
    rate = await crm.usd_rate()

    lines = [f"📊 <b>Kunlik hisobot · {day.strftime('%d.%m.%Y')}, {WEEKDAYS[day.weekday()]}</b>"
             + (" (hozirgacha)" if partial else ""), ""]
    lines.append(f"👥 Yangi foydalanuvchilar: <b>{new_users}</b> (jami {total_users})")
    lines.append(f"🧑‍🎓 Tekshiruv qilganlar: {len({s.user_id for s in [*writing, *speaking]})} kishi")
    lines.append("")

    def avg_band(rows) -> str:
        bands = [s.overall_band for s in rows if s.overall_band is not None]
        return f" · o'rtacha Band {sum(bands) / len(bands):.1f}" if bands else ""

    w_types = {t: sum(1 for s in writing if _value(s.task_type) == t) for t in ("task1", "task2")}
    lines.append(f"✍️ Writing: <b>{len(writing)}</b> — Task 1: {w_types['task1']} · Task 2: {w_types['task2']}"
                 + avg_band(writing))
    s_parts = {p: sum(1 for s in speaking if _value(s.part) == p) for p in ("part1", "part2", "part3", "full")}
    lines.append(
        f"🎙 Speaking: <b>{len(speaking)}</b> — Part 1: {s_parts['part1']} · Part 2: {s_parts['part2']} · "
        f"Part 3: {s_parts['part3']} · Full: {s_parts['full']}" + avg_band(speaking)
    )
    modes: dict[str, int] = {}
    for s in [*writing, *speaking]:
        mode = ((s.report_data or {}).get("paid_with") or {}).get("mode", "unknown")
        modes[mode] = modes.get(mode, 0) + 1
    if modes:
        lines.append("💳 To'lov turi: " + " · ".join(f"{PAID_NAMES.get(m, m)} {n}" for m, n in modes.items()))
    waits = []
    for name, rows in (("Writing", writing), ("Speaking", speaking)):
        secs = [(s.report_data or {}).get("seconds") for s in rows]
        secs = [x for x in secs if x]
        if secs:
            waits.append(f"{name} {_duration(sum(secs) / len(secs))}")
    if waits:
        lines.append("⏱ O'rtacha kutish: " + " · ".join(waits))
    lines.append("")

    approved = [p for p in decided if p.status == PaymentStatus.approved]
    revenue = sum(p.amount for p in approved)
    lines.append(
        f"💵 To'lovlar: tasdiqlandi <b>{len(approved)}</b> ta — <b>{_som(revenue)} so'm</b> · rad etildi "
        f"{sum(1 for p in decided if p.status == PaymentStatus.rejected)} · hozir kutilmoqda {pending}"
    )
    lines.append("")

    def check_costs(rows) -> list[float]:
        return [((s.report_data or {}).get("cost") or {}).get("total_usd") for s in rows
                if ((s.report_data or {}).get("cost") or {}).get("total_usd") is not None]

    note = ""
    if not spend:
        # Before the spend was recorded by category: the checks' own costs only.
        spend = {"writing": sum(check_costs(writing)), "speaking": sum(check_costs(speaking))}
        note = "<i>(faqat tekshiruvlar narxi - boshqa xarajatlar bu kun yozilmagan)</i>"
    total = sum(spend.values())
    lines.append(f"💰 OpenAI xarajati: <b>{_usd(total)}</b> ≈ <b>{_som(total * rate)} so'm</b>")
    for category, cost in sorted(spend.items(), key=lambda kv: -kv[1]):
        if cost <= 0:
            continue
        line = f"   • {CATEGORIES.get(category, category)}: {_usd(cost)}"
        rows = writing if category == "writing" else speaking if category == "speaking" else None
        if rows is not None:
            known = check_costs(rows)
            if known:
                line += f" ({len(rows)} ta tekshiruv, bittasi o'rtacha {_usd(sum(known) / len(known))})"
        lines.append(line)
    if note:
        lines.append(note)
    lines.append(f"<i>1 $ = {_som(rate)} so'm</i>")
    lines.append("")
    result = revenue - total * rate
    lines.append(
        f"📈 Kun natijasi: tushum {_som(revenue)} − xarajat {_som(total * rate)} = "
        f"<b>{'+' if result >= 0 else '−'}{_som(abs(result))} so'm</b>"
    )
    lines.append(f"⚠️ Xatolar: {errors} ta")
    return "\n".join(lines)


# --- Setup: what each topic is for, and everything from before -------------------------------------

INTROS = {
    "users": "Har bir yangi foydalanuvchi: kimligi, qayerdan kelgani (referal, guruh yoki markaz havolasi).",
    "payments": "Har bir yuborilgan chek. Tasdiqlash avvalgidek adminlarning shaxsiy chatida; qaror "
                "qilinganda shu yerdagi karta o'zgaradi.",
    "writing": "Har bir Writing tekshiruvi: ballar, to'lov turi, kutish vaqti, narxi va o'quvchiga borgan PDF.",
    "speaking": "Har bir Speaking tekshiruvi: ballar, nutq ko'rsatkichlari, to'lov turi, kutish vaqti, narxi va PDF.",
    "costs": "Har bir tekshiruvning OpenAI narxi: qaysi qadam qancha turdi, tokenlar, Flex yoki oddiy narx, "
             "tushum va foyda. Namuna javob ovozi tinglansa, narx yangilanadi.",
    "errors": "Botdagi xatolar (traceback bilan) va Flex ishlamay qolgan holatlar. OpenAI balansi tugasa, "
              "bu yerda 🚨 bilan chiqadi.",
    "daily": "Har kuni 00:05 da kechagi kun: yangi foydalanuvchilar, tekshiruvlar, to'lovlar, OpenAI xarajati, "
             "kun natijasi. /crm_today - bugungi hisobot hozirgacha.",
    "teachers": "O'qituvchi so'rovlari, yangi guruhlar va markazlar, guruhga qo'shilish/chiqish, coin "
                "sovg'alari, vazifalar.",
    "support": "O'quvchilarning adminga yozgan xabarlari (botda «💬 Admin bilan aloqa»). Javob berish uchun "
               "xabarga reply qiling - javob o'quvchiga bot orqali boradi. Javobni faqat bot adminlari va "
               "/crm_agents da belgilanganlar bera oladi.",
}


async def post_intros(keys) -> None:
    for key in keys:
        await crm.post(key, f"<b>{crm.TOPICS[key][0]}</b>\n{INTROS[key]}")


async def backfill(until: datetime) -> None:
    """Everything before the group was set up, once (bot/handlers/crm.py)."""
    try:
        await _backfill(until)
    except Exception:  # noqa: BLE001
        logger.warning("Admins' group: backfill failed", exc_info=True)


async def _backfill(until: datetime) -> None:
    rate = await crm.usd_rate()
    async with get_session() as session:
        users = await crud.users_until(session, until)
        activity = await crud.user_activity(session)
        payments = await crud.payments_until(session, until)
        payment_cards = [(p, await _payment_card(session, p)) for p in payments]
        writing_ids, speaking_ids = await crud.submission_ids_until(session, until)
        teachers = await crud.list_teachers_with_stats(session)
        centers = await crud.list_centers_with_stats(session)
        groups = await crud.list_groups_with_counts(session)
    by_id = {u.id: u for u in users}
    head = f"📦 <b>Eski ma'lumotlar</b> - {_when(until)} gacha, eskisidan yangisiga."
    tail = "✅ <b>Eski ma'lumotlar tugadi.</b> Bundan keyingilari shu yerga real vaqtda tushadi."

    await crm.post("users", f"{head}\n👥 Jami: {len(users)} ta foydalanuvchi")
    for n, user in enumerate(users, 1):
        await crm.post("users", _user_history_card(user, n, activity, by_id.get(user.referred_by)))
    await crm.post("users", tail)

    approved = [p for p in payments if p.status == PaymentStatus.approved]
    await crm.post(
        "payments",
        f"{head}\n💳 Jami: {len(payments)} ta so'rov · tasdiqlangan {len(approved)} ta — "
        f"{_som(sum(p.amount for p in approved))} so'm",
    )
    for p, card in payment_cards:
        await crm.post("payments", card, photo=p.receipt_file_id, key=f"pay:{p.id}")
    await crm.post("payments", tail)

    costs = []
    for kind, ids, topic in (("w", writing_ids, "writing"), ("s", speaking_ids, "speaking")):
        await crm.post(topic, f"{head}\nJami: {len(ids)} ta tekshiruv")
        for submission_id in ids:
            built = await _check_cards(kind, submission_id, rate)
            if built is None:
                continue
            card, cost_card, pdf, filename = built
            await crm.post(topic, card, document=pdf, filename=filename, key=f"check:{kind}:{submission_id}")
            if cost_card:
                costs.append((kind, submission_id, cost_card))
        await crm.post(topic, tail)

    await crm.post(
        "costs",
        f"{head}\nTekshiruv narxi 16.10.2026 dan beri saqlanadi - undan oldingilariniki yozilmagan. "
        f"Saqlanganlari: {len(costs)} ta.",
    )
    for kind, submission_id, cost_card in costs:
        await crm.post("costs", cost_card, key=f"cost:{kind}:{submission_id}")
    await crm.post("costs", tail)

    lines = [head, "", f"👨‍🏫 <b>O'qituvchilar: {len(teachers)}</b>"]
    for t in teachers:
        lines.append(
            f"• {_user(t['user'])} · guruh {t['groups']} · o'quvchi {t['students']} · 30 kunda tekshiruv "
            f"{t['checks_30d']}" + (f" · markaz «{_esc(t['center'].name)}»" if t["center"] else "")
        )
    lines += ["", f"🏢 <b>Markazlar: {len(centers)}</b>"]
    for c in centers:
        lines.append(
            f"• «{_esc(c['center'].name)}» · egasi {_user(c['owner'], c['center'].owner_id)} · o'qituvchi "
            f"{c['teachers']} · guruh {c['groups']} · o'quvchi {c['students']} · balans {c['balance']}"
        )
    lines += ["", f"🏫 <b>Guruhlar: {len(groups)}</b>"]
    for g in groups:
        lines.append(f"• «{_esc(g['name'])}» · o'qituvchi {_esc(g['teacher_name'])} · a'zolar {g['member_count']}")
    await crm.post("teachers", "\n".join(lines))
    await crm.post("teachers", tail)
