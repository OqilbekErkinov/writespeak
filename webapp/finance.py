"""The panel's finance page (the user, 2026-11-14: "we need one finance section for full control - every
expense, every income, everything about money in one place"):

- revenue: the approved payments - real money only, a test payment is shown apart and never counted - and the
  income entered by hand (a cash payment from a center...);
- expenses: every OpenAI dollar from the spend ledger (bot/utils/crm.py flush_spent, by category) in so'm at
  the day's rate, and the expenses entered by hand (server, domain, ads...: FinanceEntry);
- the profit month by month, the balances (OpenAI, Azure), the coins in the users' hands and what using them
  up would cost, and each kind of check's AI cost against its price.

Wired into webapp/main.py like webapp/panel.py (`templates` is set there).
"""
from __future__ import annotations

import asyncio
import io
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select

from bot.config import settings
from bot.utils import balance
from bot.utils.coins import CENTER_PACKS, CHECK_COINS, COIN_PACKS
from bot.utils.crm import redis, usd_rate
from db.database import get_session
from db.models import (
    FinanceEntry,
    PaymentKind,
    PaymentRequest,
    PaymentStatus,
    SpeakingSubmission,
    User,
    WritingSubmission,
)
from webapp import insights, panel

logger = logging.getLogger(__name__)

router = APIRouter()
templates = None  # set by webapp/main.py

TASHKENT = timedelta(hours=5)
PERIODS = {"month": "Bu oy", "prev": "O'tgan oy", "30d": "30 kun", "all": "Hammasi"}

# What the hand-entered money is (FinanceEntry.category)
EXPENSES = {
    "server": "Server va hosting", "domain": "Domen", "ads": "Reklama", "salary": "Ish haqi", "teachers": "Ustozlar",
    "azure": "Azure", "services": "Boshqa servislar", "other": "Boshqa xarajat",
}
INCOMES = {"cash": "Naqd / karta (botdan tashqari)", "center": "Markaz to'lovi", "other_in": "Boshqa tushum"}

# The spend ledger's categories (as bot/utils/crm_events.CATEGORIES names them in the daily report)
AI_CATEGORIES = {
    "writing": "Writing tekshiruvlari", "speaking": "Speaking tekshiruvlari", "tts": "Namuna javob ovozlari",
    "input": "Savolni o'qish (rasm, fayl, ovoz)", "practice": "Practice import", "vocab_translate": "Lug'at: tarjima",
    "vocab_describe": "Lug'at: izoh", "tests": "Testlar va kalibrlash", "early": "Dastlabki davr (taxminiy)",
}

CHECK_NAMES = {
    "writing_task1": "Writing Task 1", "writing_task2": "Writing Task 2", "speaking_part1": "Speaking Part 1",
    "speaking_part2": "Speaking Part 2", "speaking_part3": "Speaking Part 3", "speaking_full": "Speaking Full test",
}
# so'm per coin: the smallest pack anyone buys, and the biggest a center buys
RETAIL_COIN = COIN_PACKS[0][1] / COIN_PACKS[0][0]
CENTER_COIN = CENTER_PACKS[-1][1] / CENTER_PACKS[-1][0]
MONTHS = insights.MONTHS


def today() -> date:
    return (datetime.now(timezone.utc) + TASHKENT).date()


def tday(dt: datetime | None) -> date | None:
    """The Tashkent day of a UTC moment."""
    return (dt + TASHKENT).date() if dt else None


def bounds(period: str, now: date) -> tuple[date, date]:
    if period == "month":
        return now.replace(day=1), now
    if period == "prev":
        end = now.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if period == "30d":
        return now - timedelta(days=29), now
    return date(2000, 1, 1), now


def month_name(d: date) -> str:
    return f"{MONTHS[d.month - 1].capitalize()} {d.year}"


async def ledger() -> dict[date, dict[str, float]]:
    """The OpenAI spend of every Tashkent day, by category, in dollars (crm:spend:<day>)."""
    try:
        keys = [k async for k in redis().scan_iter("crm:spend:*")]
        pipe = redis().pipeline()
        for k in keys:
            pipe.hgetall(k)
        values = await asyncio.wait_for(pipe.execute(), 3)
    except Exception:  # noqa: BLE001 - the page still opens, without the AI costs
        logger.warning("Finance: the spend ledger not read", exc_info=True)
        return {}
    out: dict[date, dict[str, float]] = {}
    for k, h in zip(keys, values):
        try:
            out[date.fromisoformat(k.rsplit(":", 1)[-1])] = {c: float(v) for c, v in h.items()}
        except ValueError:
            continue
    return out


def payment_what(p: PaymentRequest) -> str:
    if p.kind == PaymentKind.gift:
        return f"🎁 {p.coins} coin sovg'a"
    if p.kind == PaymentKind.subscription:
        return f"{p.subscription_months or ''} oylik obuna"
    if p.coins >= CENTER_PACKS[0][0]:
        return f"🏫 {p.coins} coin (markaz)"
    return f"{p.coins} coin"


def payment_group(p: PaymentRequest) -> str:
    if p.kind == PaymentKind.gift:
        return "Sovg'a coinlar"
    if p.kind == PaymentKind.subscription:
        return "Obuna (eski)"
    return "Markaz paketlari" if p.coins >= CENTER_PACKS[0][0] else "Coin paketlari"


def check_kind(kind: str, sub) -> str:
    if kind == "w":
        return f"writing_{getattr(sub.task_type, 'value', sub.task_type)}"
    return f"speaking_{getattr(sub.part, 'value', sub.part)}"


async def overview(session, period: str) -> dict:
    now = today()
    start, end = bounds(period, now)
    rate = await usd_rate()

    def inside(d: date | None) -> bool:
        return d is not None and start <= d <= end

    payments = (await session.execute(select(PaymentRequest))).scalars().all()
    entries = (await session.execute(select(FinanceEntry).order_by(FinanceEntry.day.desc(), FinanceEntry.id.desc())))\
        .scalars().all()
    users = (await session.execute(select(User))).scalars().all()
    by_id = {u.id: u for u in users}
    writings = (await session.execute(select(WritingSubmission).options(insights._light(WritingSubmission))))\
        .scalars().all()
    speakings = (await session.execute(select(SpeakingSubmission).options(insights._light(SpeakingSubmission))))\
        .scalars().all()
    checks = [("w", x) for x in writings] + [("s", x) for x in speakings]
    spend = await ledger()

    approved = [p for p in payments if p.status == PaymentStatus.approved and p.reviewed_at]
    real = [p for p in approved if not p.is_test]
    real_in = [p for p in real if inside(tday(p.reviewed_at))]
    test_in = [p for p in approved if p.is_test and inside(tday(p.reviewed_at))]
    pending = [p for p in payments if p.status == PaymentStatus.pending]
    income_in = [e for e in entries if e.kind == "income" and inside(e.day)]
    expense_in = [e for e in entries if e.kind == "expense" and inside(e.day)]

    # --- the period's totals
    rev_pay = sum(p.amount for p in real_in)
    rev_hand = sum(e.amount for e in income_in)
    ai_cat: dict[str, float] = defaultdict(float)
    for d, cats in spend.items():
        if inside(d):
            for c, usd in cats.items():
                ai_cat[c] += usd
    ai_usd = sum(ai_cat.values())
    ai_som = ai_usd * rate
    other = sum(e.amount for e in expense_in)
    revenue = rev_pay + rev_hand
    costs = ai_som + other
    profit = revenue - costs

    # --- the series: by day, or by month for "all"
    first = min([d for d in spend] + [tday(p.reviewed_at) for p in real] + [e.day for e in entries] + [now])
    lo = max(start, first)

    def money(a: date, b: date) -> dict:
        r = sum(p.amount for p in real if a <= tday(p.reviewed_at) <= b) \
            + sum(e.amount for e in entries if e.kind == "income" and a <= e.day <= b)
        ai = sum(sum(c.values()) for d, c in spend.items() if a <= d <= b)
        o = sum(e.amount for e in entries if e.kind == "expense" and a <= e.day <= b)
        n = sum(1 for _, x in checks if a <= tday(x.created_at) <= b)
        return {"revenue": r, "ai_usd": ai, "ai": round(ai * rate), "other": o,
                "profit": round(r - ai * rate - o), "checks": n}

    def months(a: date, b: date) -> list[tuple[date, date]]:
        out, m = [], a.replace(day=1)
        while m <= b:
            nxt = (m + timedelta(days=32)).replace(day=1)
            out.append((m, min(b, nxt - timedelta(days=1))))
            m = nxt
        return out

    if period == "all":
        series = [{"label": f"{MONTHS[a.month - 1][:3]} {str(a.year)[2:]}", **money(a, b)} for a, b in months(lo, end)]
    else:
        series = []
        d = start
        while d <= end:
            series.append({"label": f"{d.day} {MONTHS[d.month - 1][:3]}", **money(d, d)})
            d += timedelta(days=1)
    by_month = [{"label": month_name(a), "current": b == now, **money(a, b)}
                for a, b in reversed(months(first, now))]

    # --- what each kind of check costs against its price (the last 30 days' checks; all of them if none)
    recent = [(k, x) for k, x in checks if insights._cost(x) > 0 and tday(x.created_at) >= now - timedelta(days=29)]
    pool = recent or [(k, x) for k, x in checks if insights._cost(x) > 0]
    costs_by: dict[str, list[float]] = defaultdict(list)
    for k, x in pool:
        costs_by[check_kind(k, x)].append(insights._cost(x))
    units = []
    for key, coins in CHECK_COINS.items():
        got = costs_by.get(key) or []
        avg = sum(got) / len(got) if got else None
        retail, center = coins * RETAIL_COIN, coins * CENTER_COIN
        cost = avg * rate if avg is not None else None
        units.append({"name": CHECK_NAMES.get(key, key), "coins": coins, "n": len(got), "usd": avg, "cost": cost,
                      "retail": retail, "center": center,
                      "margin": round((retail - cost) / retail * 100) if cost is not None else None,
                      "margin_center": round((center - cost) / center * 100) if cost is not None else None})

    # --- the period's checks: who paid for them, and what they cost
    modes: dict[str, dict] = {}
    for k, x in checks:
        if not inside(tday(x.created_at)):
            continue
        m = modes.setdefault(insights._paid_mode(x), {"n": 0, "usd": 0.0, "coins": 0})
        m["n"] += 1
        m["usd"] += insights._cost(x)
        m["coins"] += x.coins_spent or 0
    modes_list = sorted(({"name": k, **v, "som": v["usd"] * rate} for k, v in modes.items()),
                        key=lambda m: m["usd"], reverse=True)

    # --- coins: in the users' hands now, and what using them up would cost in AI
    holders = [u for u in users if u.id not in settings.admin_ids and (u.coin_balance or 0) > 0]
    in_hands = sum(u.coin_balance for u in holders)
    paid_checks = [(k, x) for k, x in pool if (x.coins_spent or 0) > 0]
    per_coin = (sum(insights._cost(x) for _, x in paid_checks) / sum(x.coins_spent for _, x in paid_checks)
                if paid_checks else None)
    coins = {
        "in_hands": in_hands, "holders": len(holders), "per_coin_usd": per_coin,
        "liability_usd": in_hands * per_coin if per_coin is not None else None,
        "sold": sum(p.coins for p in real_in), "spent": sum(x.coins_spent or 0 for _, x in checks
                                                            if inside(tday(x.created_at))),
    }

    groups: dict[str, dict] = {}
    for p in real_in:
        g = groups.setdefault(payment_group(p), {"n": 0, "amount": 0})
        g["n"] += 1
        g["amount"] += p.amount
    ai_rows = sorted(({"key": c, "name": AI_CATEGORIES.get(c, c), "usd": v, "som": v * rate,
                       "share": v / ai_usd * 100 if ai_usd else 0} for c, v in ai_cat.items() if v > 0),
                     key=lambda r: r["usd"], reverse=True)

    def pay_row(p: PaymentRequest) -> dict:
        return {"p": p, "user": by_id.get(p.user_id), "what": payment_what(p), "when": insights.fmt(p.reviewed_at)}

    return {
        "period": period, "periods": PERIODS, "start": start, "end": end, "rate": rate,
        "range": f"{first:%d.%m.%Y} — {end:%d.%m.%Y}" if period == "all" else f"{start:%d.%m.%Y} — {end:%d.%m.%Y}",
        "totals": {"revenue": revenue, "rev_pay": rev_pay, "rev_hand": rev_hand, "n_pay": len(real_in),
                   "ai_usd": ai_usd, "ai": ai_som, "other": other, "n_other": len(expense_in), "costs": costs,
                   "profit": profit, "margin": round(profit / revenue * 100) if revenue else None,
                   "pending": sum(p.amount for p in pending), "n_pending": len(pending),
                   "test": sum(p.amount for p in test_in), "n_test": len(test_in)},
        "series": series, "by_month": by_month, "units": units, "units_recent": bool(recent),
        "modes": modes_list, "coins": coins, "groups": groups, "ai_rows": ai_rows,
        "payments": [pay_row(p) for p in sorted(real_in + test_in, key=lambda p: p.reviewed_at, reverse=True)[:60]],
        "entries": [e for e in entries if inside(e.day)],
        "openai": await balance.openai_status(), "credit_paused": await balance.credit_paused(),
        "azure": await balance.azure_status(),
        "expense_names": EXPENSES, "income_names": INCOMES, "today": now.isoformat(),
        "retail_coin": RETAIL_COIN, "center_coin": CENTER_COIN,
    }


@router.get("/finance", response_class=HTMLResponse)
async def finance_page(request: Request, p: str = "month", done: str = ""):
    if (r := panel.guard(request)) is not None:
        return r
    period = p if p in PERIODS else "month"
    async with get_session() as session:
        data = await overview(session, period)
    flash = {"added": "Yozuv qo'shildi.", "deleted": "Yozuv o'chirildi.",
             "bad": "Summani so'mda kiriting (masalan 150000) va turini tanlang."}.get(done)
    chart = {"labels": [s["label"] for s in data["series"]], "revenue": [s["revenue"] for s in data["series"]],
             "ai": [s["ai"] for s in data["series"]], "other": [s["other"] for s in data["series"]],
             "profit": [s["profit"] for s in data["series"]]}
    return templates.TemplateResponse(request, "finance.html", {
        "active": "finance", "f": data, "chart": chart, "flash": flash, "flash_bad": done == "bad"})


def _back(raw: str) -> str:
    raw = str(raw or "")
    return raw if raw.startswith("/finance") else "/finance"


def _done(back: str, what: str) -> RedirectResponse:
    return RedirectResponse(f"{back}{'&' if '?' in back else '?'}done={what}", status_code=303)


@router.post("/finance/entry")
async def finance_add(request: Request, kind: str = Form(...), category: str = Form(""), amount: str = Form(""),
                      day: str = Form(""), note: str = Form(""), back: str = Form("/finance")):
    """Money entered by hand: an expense (server, domain, ads...) or an income from outside the bot."""
    if (r := panel.guard(request)) is not None:
        return r
    back = _back(back)
    names = EXPENSES if kind == "expense" else INCOMES if kind == "income" else None
    value = balance.parse_som(amount)
    if names is None or category not in names or not value:
        return _done(back, "bad")
    try:
        when = date.fromisoformat(day) if day else today()
    except ValueError:
        when = today()
    when = min(when, today())
    async with get_session() as session:
        session.add(FinanceEntry(kind=kind, category=category, amount=int(value), day=when,
                                 note=(note or "").strip()[:300] or None, created_by=panel.admin_name(request)))
        await session.commit()
    await panel.audit(request, "finance_entry", details=f"{'Xarajat' if kind == 'expense' else 'Tushum'}: "
                      f"{names[category]} — {insights.som(value)} so'm, {when:%d.%m.%Y}" + (f" ({note.strip()[:80]})"
                                                                                           if note.strip() else ""))
    return _done(back, "added")


@router.post("/finance/entry/{entry_id}/delete")
async def finance_delete(request: Request, entry_id: int, back: str = Form("/finance")):
    if (r := panel.guard(request)) is not None:
        return r
    back = _back(back)
    async with get_session() as session:
        entry = await session.get(FinanceEntry, entry_id)
        if entry is None:
            return RedirectResponse(back, status_code=303)
        names = EXPENSES if entry.kind == "expense" else INCOMES
        details = (f"{'Xarajat' if entry.kind == 'expense' else 'Tushum'}: {names.get(entry.category, entry.category)}"
                   f" — {insights.som(entry.amount)} so'm, {entry.day:%d.%m.%Y}")
        await session.delete(entry)
        await session.commit()
    await panel.audit(request, "finance_entry_deleted", details=details)
    return _done(back, "deleted")


@router.get("/export/finance.xlsx")
async def finance_export(request: Request):
    """Everything on the page for Excel: month by month, the payments, the AI spend by day, the entries."""
    if (r := panel.guard(request)) is not None:
        return r
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    async with get_session() as session:
        data = await overview(session, "all")
        payments = (await session.execute(select(PaymentRequest).where(PaymentRequest.status == PaymentStatus.approved)
                                          .order_by(PaymentRequest.reviewed_at.desc()))).scalars().all()
        ids = {p.user_id for p in payments}
        users = {u.id: u for u in (await session.execute(select(User).where(User.id.in_(ids)))).scalars()} if ids else {}
        entries = (await session.execute(select(FinanceEntry).order_by(FinanceEntry.day.desc()))).scalars().all()
    spend = await ledger()
    rate = data["rate"]
    cats = sorted({c for v in spend.values() for c in v})

    def local(dt):
        return (dt + TASHKENT).replace(tzinfo=None) if dt else None

    sheets = [
        ("Oylar", ["Oy", "Tushum (so'm)", "AI xarajat ($)", "AI xarajat (so'm)", "Boshqa xarajat (so'm)", "Foyda (so'm)",
                   "Tekshiruvlar"],
         [[m["label"], m["revenue"], round(m["ai_usd"], 2), m["ai"], m["other"], m["profit"], m["checks"]]
          for m in data["by_month"]]),
        ("Tushumlar", ["#", "Tasdiqlangan", "Foydalanuvchi ID", "Ism", "Nima", "Coin", "Summa (so'm)", "Haqiqiymi"],
         [[p.id, local(p.reviewed_at), p.user_id, users[p.user_id].full_name if p.user_id in users else "",
           payment_what(p), p.coins, p.amount, "test" if p.is_test else "ha"] for p in payments]),
        ("AI xarajat", ["Kun", *[AI_CATEGORIES.get(c, c) + " ($)" for c in cats], "Jami ($)", f"Jami (so'm, 1$={rate:.0f})"],
         [[d, *[round(spend[d].get(c, 0.0), 4) for c in cats], round(sum(spend[d].values()), 4),
           round(sum(spend[d].values()) * rate)] for d in sorted(spend, reverse=True)]),
        ("Qo'lda kiritilgan", ["Kun", "Turi", "Nima", "Summa (so'm)", "Izoh", "Kim kiritdi"],
         [[e.day, "Xarajat" if e.kind == "expense" else "Tushum",
           (EXPENSES if e.kind == "expense" else INCOMES).get(e.category, e.category), e.amount, e.note or "", e.created_by]
          for e in entries]),
    ]
    wb = Workbook()
    wb.remove(wb.active)
    for title, headers, rows in sheets:
        ws = wb.create_sheet(title[:31])
        ws.append(headers)
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="062018")
            c.alignment = Alignment(vertical="center", wrap_text=True)
        for row in rows:
            ws.append(row)
        for i, h in enumerate(headers, 1):
            width = max([len(str(h))] + [len(str(r[i - 1] if r[i - 1] is not None else "")) for r in rows[:500]])
            ws.column_dimensions[ws.cell(1, i).column_letter].width = min(max(10, width + 2), 45)
        ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    await panel.audit(request, "export", details="finance.xlsx")
    name = f"Bandzilla_Moliya_{today():%Y-%m-%d}.xlsx"
    return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
