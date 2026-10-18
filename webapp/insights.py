"""What the admin panel shows beyond plain tables (the user's request,
2026-10-18: "we must be able to follow everything the bot does"):

- a user's whole story for the profile page (/users/<id>): every check,
  payment, gift, coin change, group and referral, merged into one timeline;
- the Users list with each user's numbers and segments;
- every Writing/Speaking check in one list (/checks);
- the dashboard.

Read-only: the bot's database plus the bot's Redis marks of who was active
when (bot/utils/activity.py). Times are shown in Tashkent time.
"""
from __future__ import annotations

import asyncio
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import load_only

from bot.config import settings
from bot.utils import runtime
from bot.utils.activity import DAY_KEY, LAST_SEEN_KEY, tashkent_day
from bot.utils.crm import redis
from db import crud
from db.models import (
    SPEAKING_LABELS,
    CoinAdjustment,
    CoinGift,
    Group,
    GroupMembership,
    MockTestSession,
    MockTestStatus,
    PaymentKind,
    PaymentRequest,
    PaymentStatus,
    PracticeProgress,
    SpeakingSubmission,
    User,
    UserRole,
    UserStatus,
    WritingSubmission,
)

TASHKENT = timedelta(hours=5)
WRITING_LABELS = {"task1": "Writing Task 1", "task2": "Writing Task 2"}
PAID_NAMES = {
    "free": "bepul", "coins": "coin", "credit": "kredit (eski)", "subscription": "obuna", "sponsor": "guruh to'ladi",
    "test": "test", "unknown": "—",
}
MONTHS = ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr"]
CHECKS_WAITING_KEY = "checks:waiting"  # bot/utils/checks_back.py


# --- small helpers ----------------------------------------------------------------------------------


def now() -> datetime:
    return datetime.now(timezone.utc)


def local(dt: datetime | None) -> datetime | None:
    return dt + TASHKENT if dt else None


def fmt(dt: datetime | None, with_time: bool = True) -> str:
    if dt is None:
        return "—"
    return local(dt).strftime("%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y")


def ago(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    s = (now() - dt).total_seconds()
    if s < 60:
        return "hozirgina"
    if s < 3600:
        return f"{int(s // 60)} daqiqa oldin"
    if s < 86400:
        return f"{int(s // 3600)} soat oldin"
    if s < 86400 * 30:
        return f"{int(s // 86400)} kun oldin"
    return fmt(dt, with_time=False)


def som(amount: float | int | None) -> str:
    return f"{int(round(amount or 0)):,}".replace(",", " ")


def day_start_utc(days_back: int = 0) -> datetime:
    """Midnight in Tashkent, `days_back` days ago, as UTC."""
    today = (now() + TASHKENT).date() - timedelta(days=days_back)
    return datetime(today.year, today.month, today.day, tzinfo=timezone.utc) - TASHKENT


def _cost(sub) -> float:
    """The check's OpenAI cost in dollars (services/ai/usage.summary in report_data)."""
    cost = (sub.report_data or {}).get("cost") or {}
    return float(cost.get("total_usd") or cost.get("usd") or 0.0)


def _paid_mode(sub) -> str:
    paid = (sub.report_data or {}).get("paid_with") or {}
    mode = paid.get("mode")
    if mode is None:
        return "guruh to'ladi" if sub.paid_by_id else "—"
    return PAID_NAMES.get(mode, mode)


def _criteria(scores: dict | None) -> str:
    if not scores:
        return ""
    names = {"Pronunciation": "P", "TR_or_TA": "TA"}
    return " · ".join(f"{names.get(k, k)} {v:g}" for k, v in scores.items() if v is not None)


def _light(model):
    """Only the columns a check's row needs - not the essay, transcript or marks."""
    kind = model.task_type if model is WritingSubmission else model.part
    return load_only(model.id, model.user_id, kind, model.overall_band, model.criteria_scores, model.coins_spent,
                     model.report_data, model.feedback_pdf_path, model.practice_question_id,
                     model.assignment_part_id, model.paid_by_id, model.created_at)


def _check_row(sub, kind: str) -> dict:
    if kind == "w":
        label = WRITING_LABELS.get(getattr(sub.task_type, "value", sub.task_type), "Writing")
    else:
        label = SPEAKING_LABELS.get(getattr(sub.part, "value", sub.part), "Speaking")
    return {
        "kind": kind, "id": sub.id, "user_id": sub.user_id, "label": label, "at": sub.created_at,
        "when": fmt(sub.created_at), "band": sub.overall_band, "criteria": _criteria(sub.criteria_scores),
        "coins": sub.coins_spent or 0, "cost": _cost(sub), "paid": _paid_mode(sub),
        "pdf": bool(sub.feedback_pdf_path), "practice": bool(sub.practice_question_id),
        "assignment": bool(sub.assignment_part_id),
    }


async def last_seen_map() -> dict[int, datetime]:
    try:
        raw = await asyncio.wait_for(redis().hgetall(LAST_SEEN_KEY), 2)
    except Exception:  # noqa: BLE001
        return {}
    return {int(k): datetime.fromtimestamp(int(v), timezone.utc) for k, v in raw.items()}


async def _active_days(user_id: int, days: int = 30) -> int:
    try:
        pipe = redis().pipeline()
        for i in range(days):
            pipe.sismember(DAY_KEY.format(day=tashkent_day((now() - timedelta(days=i)).timestamp())), user_id)
        return sum(1 for x in await asyncio.wait_for(pipe.execute(), 2) if x)
    except Exception:  # noqa: BLE001
        return 0


# --- the Users list ---------------------------------------------------------------------------------

SEGMENTS = {
    "all": "Barchasi", "online": "Hozir faol", "new": "Bugun qo'shilgan", "active": "7 kunda faol",
    "paying": "To'lov qilgan", "checked": "Tekshiruv qilgan", "nochecks": "Hali tekshiruvsiz", "teachers": "O'qituvchilar",
    "blocked": "Bloklangan",
}


async def users_overview(session, q: str | None, segment: str, limit: int = 2000) -> dict:
    users = await crud.list_users(session, search=q, limit=limit)
    w = {uid: (n, last) for uid, n, last in (await session.execute(
        select(WritingSubmission.user_id, func.count(), func.max(WritingSubmission.created_at))
        .group_by(WritingSubmission.user_id))).all()}
    s = {uid: (n, last) for uid, n, last in (await session.execute(
        select(SpeakingSubmission.user_id, func.count(), func.max(SpeakingSubmission.created_at))
        .group_by(SpeakingSubmission.user_id))).all()}
    paid = dict((await session.execute(
        select(PaymentRequest.user_id, func.sum(PaymentRequest.amount))
        .where(PaymentRequest.status == PaymentStatus.approved).group_by(PaymentRequest.user_id))).all())
    seen = await last_seen_map()
    today, week, online_since = day_start_utc(), now() - timedelta(days=7), now() - timedelta(minutes=15)

    rows = []
    for u in users:
        wc, wl = w.get(u.id, (0, None))
        sc, sl = s.get(u.id, (0, None))
        last_active = max((d for d in (seen.get(u.id), wl, sl, u.created_at) if d), default=None)
        rows.append({
            "user": u, "writing": wc, "speaking": sc, "checks": wc + sc, "paid": paid.get(u.id) or 0,
            "paid_str": som(paid.get(u.id)), "joined": fmt(u.created_at, with_time=False),
            "last_active": last_active, "last_active_str": ago(last_active),
            "online": bool(seen.get(u.id) and seen[u.id] >= online_since),
            "is_teacher": u.role == UserRole.teacher, "is_admin": u.id in settings.admin_ids,
        })
    tests = {
        "all": lambda r: True,
        "online": lambda r: r["online"],
        "new": lambda r: r["user"].created_at and r["user"].created_at >= today,
        "active": lambda r: r["last_active"] and r["last_active"] >= week,
        "paying": lambda r: r["paid"] > 0,
        "checked": lambda r: r["checks"] > 0,
        "nochecks": lambda r: r["checks"] == 0,
        "teachers": lambda r: r["is_teacher"],
        "blocked": lambda r: r["user"].status == UserStatus.denied,
    }
    counts = {k: sum(1 for r in rows if f(r)) for k, f in tests.items()}
    segment = segment if segment in tests else "all"
    shown = [r for r in rows if tests[segment](r)]
    shown.sort(key=lambda r: r["last_active"] or r["user"].created_at, reverse=True)
    return {"rows": shown, "counts": counts, "segment": segment}


# --- one user's whole story ----------------------------------------------------------------------------


async def user_profile(session, user_id: int) -> dict | None:
    user = await session.get(User, user_id)
    if user is None:
        return None
    referrer = await session.get(User, user.referred_by) if user.referred_by else None
    referrals = (await session.execute(
        select(User).where(User.referred_by == user_id).order_by(User.created_at.desc()))).scalars().all()
    writings = (await session.execute(
        select(WritingSubmission).options(_light(WritingSubmission)).where(WritingSubmission.user_id == user_id)
        .order_by(WritingSubmission.created_at.desc()))).scalars().all()
    speakings = (await session.execute(
        select(SpeakingSubmission).options(_light(SpeakingSubmission)).where(SpeakingSubmission.user_id == user_id)
        .order_by(SpeakingSubmission.created_at.desc()))).scalars().all()
    payments = (await session.execute(
        select(PaymentRequest).where(PaymentRequest.user_id == user_id)
        .order_by(PaymentRequest.created_at.desc()))).scalars().all()
    gifts_bought = (await session.execute(
        select(CoinGift).where(CoinGift.buyer_id == user_id).order_by(CoinGift.created_at.desc()))).scalars().all()
    gifts_got = (await session.execute(
        select(CoinGift).where(CoinGift.claimed_by == user_id).order_by(CoinGift.claimed_at.desc()))).scalars().all()
    adjustments = await crud.list_coin_adjustments(session, user_id, limit=200)
    memberships = (await session.execute(
        select(GroupMembership, Group).join(Group, Group.id == GroupMembership.group_id)
        .where(GroupMembership.user_id == user_id))).all()
    own_groups = (await session.execute(select(Group).where(Group.teacher_id == user_id))).scalars().all()
    mocks = (await session.execute(
        select(MockTestSession).where(MockTestSession.user_id == user_id)
        .order_by(MockTestSession.created_at.desc()))).scalars().all()
    vocab_total, vocab_learned = await crud.get_vocabulary_counts(session, user_id)
    practice_done = (await session.execute(
        select(func.count()).select_from(PracticeProgress).where(PracticeProgress.user_id == user_id))).scalar() or 0
    people_ids = {g.claimed_by for g in gifts_bought if g.claimed_by} | {g.buyer_id for g in gifts_got}
    people_ids |= {g.teacher_id for _, g in memberships}
    people = {u.id: u for u in (await session.execute(select(User).where(User.id.in_(people_ids)))).scalars()} \
        if people_ids else {}

    checks = [_check_row(x, "w") for x in writings] + [_check_row(x, "s") for x in speakings]
    checks.sort(key=lambda c: c["at"], reverse=True)
    w_bands = [x.overall_band for x in writings if x.overall_band is not None]
    s_bands = [x.overall_band for x in speakings if x.overall_band is not None]
    approved = [p for p in payments if p.status == PaymentStatus.approved]
    seen = (await last_seen_map()).get(user_id)
    last_active = max((d for d in (seen, *(c["at"] for c in checks[:1]), user.created_at) if d), default=None)

    def name(u: User | None, fallback) -> str:
        return u.full_name if u else str(fallback)

    events = [{"at": user.created_at, "icon": "🎉", "tone": "good", "title": "Botga qo'shildi",
               "detail": f"{name(referrer, user.referred_by)} taklifi bilan" if user.referred_by else "o'zi keldi",
               "link": f"/users/{referrer.id}" if referrer else None}]
    for r in referrals:
        events.append({"at": r.created_at, "icon": "🤝", "tone": "", "title": f"{r.full_name} uning havolasi bilan qo'shildi",
                       "detail": "", "link": f"/users/{r.id}"})
    for c in checks:
        band = f"Band {c['band']:.1f}" if c["band"] is not None else "ball yo'q"
        paid = f"{c['coins']} coin" if c["coins"] else (c["paid"] if c["paid"] != "—" else "")
        bits = [paid, f"${c['cost']:.3f}" if c["cost"] else ""]
        bits += ["Practice" if c["practice"] else "", "vazifa" if c["assignment"] else ""]
        events.append({"at": c["at"], "icon": "✍️" if c["kind"] == "w" else "🎙", "tone": "",
                       "title": f"{c['label']} — {band}", "detail": " · ".join(b for b in bits if b),
                       "link": f"/reports/{c['kind']}/{c['id']}.pdf" if c["pdf"] else None, "pdf": c["pdf"]})
    for p in payments:
        item = f"🎁 {p.coins} coin (sovg'a)" if p.kind == PaymentKind.gift else (
            f"{p.subscription_months} oylik obuna" if p.kind == PaymentKind.subscription else f"{p.coins} coin")
        events.append({"at": p.created_at, "icon": "💳", "tone": "", "title": f"To'lov so'rovi #{p.id}: {item}",
                       "detail": f"{som(p.amount)} so'm" + ("" if p.receipt_file_id else " · chek yuborilmagan"),
                       "link": "/payments"})
        if p.status != PaymentStatus.pending and p.reviewed_at:
            ok = p.status == PaymentStatus.approved
            events.append({"at": p.reviewed_at, "icon": "✅" if ok else "⛔", "tone": "good" if ok else "bad",
                           "title": f"To'lov #{p.id} {'tasdiqlandi' if ok else 'rad etildi'}", "detail": item,
                           "link": None})
    for g in gifts_bought:
        if g.claimed_by:
            events.append({"at": g.claimed_at, "icon": "🎁", "tone": "good",
                           "title": f"Sovg'asini {name(people.get(g.claimed_by), g.claimed_by)} oldi",
                           "detail": f"{g.coins} coin", "link": f"/users/{g.claimed_by}"})
    for g in gifts_got:
        events.append({"at": g.claimed_at, "icon": "🎁", "tone": "good",
                       "title": f"{name(people.get(g.buyer_id), g.buyer_id)} sovg'asini oldi: +{g.coins} coin",
                       "detail": "", "link": f"/users/{g.buyer_id}"})
    for a in adjustments:
        events.append({"at": a.created_at, "icon": "🟡", "tone": "good" if a.delta > 0 else "bad",
                       "title": f"Admin: {'+' if a.delta > 0 else ''}{a.delta} coin (balans {a.balance_after})",
                       "detail": a.note or "", "link": f"/users/{user_id}/coins"})
    for m, g in memberships:
        events.append({"at": m.joined_at, "icon": "🏫", "tone": "", "title": f"«{g.name}» guruhiga qo'shildi",
                       "detail": f"o'qituvchi {name(people.get(g.teacher_id), g.teacher_id)}", "link": None})
    for g in own_groups:
        events.append({"at": g.created_at, "icon": "👨‍🏫", "tone": "", "title": f"«{g.name}» guruhini ochdi",
                       "detail": "", "link": None})
    for mt in mocks:
        done = mt.status == MockTestStatus.completed
        events.append({"at": mt.created_at, "icon": "🎯", "tone": "", "title": "Mock testni boshladi",
                       "detail": "tugatdi " + fmt(mt.completed_at) if done else "tugatilmagan", "link": None})
    events = [e for e in events if e["at"]]
    events.sort(key=lambda e: e["at"], reverse=True)
    for e in events:
        e["when"], e["ago"] = fmt(e["at"]), ago(e["at"])

    chronological = sorted((c for c in checks if c["band"] is not None), key=lambda c: c["at"])
    return {
        "user": user, "referrer": referrer, "referrals": referrals, "checks": checks, "payments": payments,
        "gifts_bought": gifts_bought, "gifts_got": gifts_got, "adjustments": adjustments, "people": people,
        "memberships": memberships, "own_groups": own_groups, "mocks": mocks, "events": events,
        "is_admin": user.id in settings.admin_ids,
        "stats": {
            "writing": len(writings), "speaking": len(speakings),
            "avg_w": statistics.mean(w_bands) if w_bands else None, "avg_s": statistics.mean(s_bands) if s_bands else None,
            "best": max(w_bands + s_bands) if (w_bands or s_bands) else None,
            "coins_spent": sum(c["coins"] for c in checks), "cost": sum(c["cost"] for c in checks),
            "paid": sum(p.amount for p in approved), "paid_str": som(sum(p.amount for p in approved)),
            "payments": len(approved), "vocab_total": vocab_total, "vocab_learned": vocab_learned,
            "practice": practice_done, "referrals": len(referrals),
            "joined": fmt(user.created_at), "joined_ago": ago(user.created_at),
            "last_seen": fmt(seen) if seen else "—", "last_active": ago(last_active),
            "active_days": await _active_days(user_id),
            "online": bool(seen and seen >= now() - timedelta(minutes=15)),
        },
        "series": [{"d": local(c["at"]).strftime("%d.%m"), "k": c["kind"], "b": c["band"], "l": c["label"]}
                   for c in chronological],
    }


# --- every check -----------------------------------------------------------------------------------------


async def all_checks(session, kind: str, period: str, limit: int = 300) -> dict:
    since = {"today": day_start_utc(), "7d": now() - timedelta(days=7), "30d": now() - timedelta(days=30)}.get(period)
    rows = []
    if kind in ("all", "w"):
        stmt = (select(WritingSubmission).options(_light(WritingSubmission))
                .order_by(WritingSubmission.created_at.desc()).limit(limit))
        if since:
            stmt = stmt.where(WritingSubmission.created_at >= since)
        rows += [_check_row(x, "w") for x in (await session.execute(stmt)).scalars()]
    if kind in ("all", "s"):
        stmt = (select(SpeakingSubmission).options(_light(SpeakingSubmission))
                .order_by(SpeakingSubmission.created_at.desc()).limit(limit))
        if since:
            stmt = stmt.where(SpeakingSubmission.created_at >= since)
        rows += [_check_row(x, "s") for x in (await session.execute(stmt)).scalars()]
    rows.sort(key=lambda c: c["at"], reverse=True)
    rows = rows[:limit]
    ids = {r["user_id"] for r in rows}
    users = {u.id: u for u in (await session.execute(select(User).where(User.id.in_(ids)))).scalars()} if ids else {}
    for r in rows:
        r["user"] = users.get(r["user_id"])
    bands = [r["band"] for r in rows if r["band"] is not None]
    return {
        "rows": rows, "kind": kind, "period": period,
        "summary": {"n": len(rows), "avg": statistics.mean(bands) if bands else None,
                    "cost": sum(r["cost"] for r in rows), "coins": sum(r["coins"] for r in rows)},
    }


# --- the dashboard -------------------------------------------------------------------------------------------


def _pct(cur: float, prev: float, floor: float = 5) -> int | None:
    """Change against the previous period - not shown while that period is
    too small to compare with (+5500% from 1 to 56 says nothing)."""
    if not prev or prev < floor:
        return None
    return round((cur - prev) / prev * 100)


async def dashboard(session) -> dict:
    t_now = now()
    today = day_start_utc()
    users = (await session.execute(select(User))).scalars().all()
    real = [u for u in users if u.id not in settings.admin_ids]
    writings = (await session.execute(select(WritingSubmission).options(_light(WritingSubmission)))).scalars().all()
    speakings = (await session.execute(select(SpeakingSubmission).options(_light(SpeakingSubmission)))).scalars().all()
    payments = (await session.execute(select(PaymentRequest))).scalars().all()
    approved = [p for p in payments if p.status == PaymentStatus.approved and p.reviewed_at]
    rate = settings.usd_uzs_rate
    checks = [("w", x) for x in writings] + [("s", x) for x in speakings]

    def between(dt, a, b=None):
        return dt is not None and dt >= a and (b is None or dt < b)

    week, prev_week = t_now - timedelta(days=7), t_now - timedelta(days=14)
    month, prev_month = t_now - timedelta(days=30), t_now - timedelta(days=60)

    # daily active users from the bot's Redis marks, oldest first
    days = [day_start_utc(i) for i in range(29, -1, -1)]
    day_keys = [DAY_KEY.format(day=tashkent_day((d + timedelta(hours=12)).timestamp())) for d in days]
    try:
        pipe = redis().pipeline()
        for k in day_keys:
            pipe.scard(k)
        dau = [int(x or 0) for x in await asyncio.wait_for(pipe.execute(), 2)]
        active7 = len(await asyncio.wait_for(redis().sunion(*day_keys[-7:]), 2))
        waiting = int(await asyncio.wait_for(redis().scard(CHECKS_WAITING_KEY), 2) or 0)
    except Exception:  # noqa: BLE001
        dau, active7, waiting = [0] * len(days), 0, 0
    seen = await last_seen_map()
    online = sum(1 for t in seen.values() if t >= t_now - timedelta(minutes=15))

    series = []
    for i, start in enumerate(days):
        end = start + timedelta(days=1)
        day_checks = [x for _, x in checks if between(x.created_at, start, end)]
        series.append({
            "date": f"{local(start).day} {MONTHS[local(start).month - 1][:3]}",
            "users": sum(1 for u in real if between(u.created_at, start, end)),
            "w": sum(1 for k, x in checks if k == "w" and between(x.created_at, start, end)),
            "s": sum(1 for k, x in checks if k == "s" and between(x.created_at, start, end)),
            "revenue": sum(p.amount for p in approved if between(p.reviewed_at, start, end)),
            "cost": round(sum(_cost(x) for x in day_checks) * rate),
            "dau": dau[i],
        })

    def count_checks(a, b=None):
        return sum(1 for _, x in checks if between(x.created_at, a, b))

    def revenue(a, b=None):
        return sum(p.amount for p in approved if between(p.reviewed_at, a, b))

    cost30 = sum(_cost(x) for _, x in checks if between(x.created_at, month))
    rev30 = revenue(month)
    checkers = {x.user_id for _, x in checks}
    payer_counts = Counter(p.user_id for p in approved)
    w_bins, s_bins = Counter(), Counter()
    for k, x in checks:
        if x.overall_band is not None:
            (w_bins if k == "w" else s_bins)[x.overall_band] += 1
    band_labels = [b / 2 for b in range(8, 19)]  # 4.0 .. 9.0
    mix = Counter()
    for x in writings:
        mix[WRITING_LABELS.get(getattr(x.task_type, "value", x.task_type), "Writing")] += 1
    for x in speakings:
        mix[SPEAKING_LABELS.get(getattr(x.part, "value", x.part), "Speaking")] += 1
    langs = Counter(u.language or "uz" for u in real)

    # the latest events across the bot
    by_id = {u.id: u for u in users}
    feed = []
    for u in sorted(real, key=lambda u: u.created_at or t_now, reverse=True)[:12]:
        feed.append({"at": u.created_at, "icon": "🎉", "user": u, "text": "botga qo'shildi"})
    for k, x in sorted(checks, key=lambda c: c[1].created_at, reverse=True)[:15]:
        row = _check_row(x, k)
        band = f" — Band {row['band']:.1f}" if row["band"] is not None else ""
        feed.append({"at": x.created_at, "icon": "✍️" if k == "w" else "🎙", "user": by_id.get(x.user_id),
                     "text": f"{row['label']}{band}", "link": f"/reports/{k}/{x.id}.pdf" if row["pdf"] else None})
    for p in sorted(payments, key=lambda p: p.created_at, reverse=True)[:10]:
        what = (f"🎁 {p.coins} coin sovg'a" if p.kind == PaymentKind.gift else
                f"{p.subscription_months} oylik obuna" if p.kind == PaymentKind.subscription else f"{p.coins} coin")
        feed.append({"at": p.created_at, "icon": "💳", "user": by_id.get(p.user_id),
                     "text": f"to'lov so'radi: {what} — {som(p.amount)} so'm", "link": "/payments"})
    gifts = (await session.execute(select(CoinGift).where(CoinGift.claimed_by.is_not(None))
                                   .order_by(CoinGift.claimed_at.desc()).limit(6))).scalars().all()
    for g in gifts:
        feed.append({"at": g.claimed_at, "icon": "🎁", "user": by_id.get(g.claimed_by),
                     "text": f"{g.coins} coin sovg'ani oldi"})
    adjustments = (await session.execute(select(CoinAdjustment).order_by(CoinAdjustment.created_at.desc())
                                         .limit(6))).scalars().all()
    for a in adjustments:
        feed.append({"at": a.created_at, "icon": "🟡", "user": by_id.get(a.user_id),
                     "text": f"admin: {'+' if a.delta > 0 else ''}{a.delta} coin"})
    feed = sorted((f for f in feed if f["at"]), key=lambda f: f["at"], reverse=True)[:18]
    for f in feed:
        f["ago"] = ago(f["at"])

    top = Counter(x.user_id for _, x in checks if between(x.created_at, month)).most_common(6)
    pending = sorted((p for p in payments if p.status == PaymentStatus.pending), key=lambda p: p.created_at, reverse=True)
    testers = await runtime.testers()  # the panel's switch, else .env

    return {
        "greeting_date": f"{local(t_now).day}-{MONTHS[local(t_now).month - 1]}, {local(t_now).year}",
        "today": {
            "users": sum(1 for u in real if between(u.created_at, today)), "checks": count_checks(today),
            "revenue": som(revenue(today)), "dau": dau[-1], "online": online,
        },
        "kpi": [
            {"label": "Foydalanuvchi", "value": str(len(real)), "sub": f"+{sum(1 for u in real if between(u.created_at, week))} shu hafta",
             "delta": _pct(sum(1 for u in real if between(u.created_at, week)),
                           sum(1 for u in real if between(u.created_at, prev_week, week))), "spark": [d["users"] for d in series]},
            {"label": "Faol · 7 kun", "value": str(active7), "sub": f"bugun {dau[-1]} · hozir {online}",
             "delta": None, "spark": [d["dau"] for d in series]},
            {"label": "Tekshiruv · 7 kun", "value": str(count_checks(week)), "sub": f"jami {len(checks)}",
             "delta": _pct(count_checks(week), count_checks(prev_week, week)), "spark": [d["w"] + d["s"] for d in series]},
            {"label": "Tushum · 30 kun", "value": som(rev30), "unit": "so'm", "sub": f"jami {som(sum(p.amount for p in approved))} so'm",
             "delta": _pct(rev30, revenue(prev_month, month)), "spark": [d["revenue"] for d in series]},
            {"label": "AI xarajat · 30 kun", "value": f"${cost30:.2f}", "sub": f"≈ {som(cost30 * rate)} so'm",
             "delta": None, "spark": [d["cost"] for d in series], "warm": True},
            {"label": "Foyda · 30 kun", "value": som(rev30 - cost30 * rate), "unit": "so'm",
             "sub": f"tekshiruv o'rtacha ${(cost30 / max(1, count_checks(month))):.3f}", "delta": None,
             "spark": [d["revenue"] - d["cost"] for d in series]},
        ],
        "series": series,
        "funnel": [
            {"label": "Ro'yxatdan o'tgan", "n": len(real)},
            {"label": "Tekshiruv qilgan", "n": len(checkers - settings.admin_ids)},
            {"label": "To'lov qilgan", "n": len([u for u in payer_counts if u not in settings.admin_ids])},
            {"label": "Qayta to'lagan", "n": len([u for u, n in payer_counts.items() if n >= 2 and u not in settings.admin_ids])},
        ],
        "bands": {"labels": [f"{b:.1f}" for b in band_labels], "w": [w_bins.get(b, 0) for b in band_labels],
                  "s": [s_bins.get(b, 0) for b in band_labels]},
        "mix": {"labels": list(mix.keys()), "values": list(mix.values())},
        "langs": {"labels": [{"uz": "O'zbekcha", "ru": "Ruscha", "en": "Inglizcha"}.get(k, k) for k in langs],
                  "values": list(langs.values())},
        "feed": feed,
        "top": [{"user": by_id.get(uid), "n": n} for uid, n in top],
        "pending": [{"p": p, "user": by_id.get(p.user_id), "ago": ago(p.created_at), "amount": som(p.amount)}
                    for p in pending[:5]],
        "pending_count": len(pending),
        "test_window": {"on": bool(testers), "testers": len(testers), "waiting": waiting},
    }
