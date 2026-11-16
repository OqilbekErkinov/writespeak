"""The panel's business pages (the owner's request before opening the bot to everyone, 2026-11-16: "where do
users come from, and what do they do next"):

- /sources  📣 Manbalar: per source (users.source - bot/utils/signup_source.py) the signups, the share who made a
            first check and who paid, the revenue (perm "finance" only), the signups of the last 7 and 30 days;
            and the link builder - one t.me/<bot>?start=src_<name> link per channel;
- /funnel   📈 Voronka: signup -> first check -> first purchase (all time and the last 30 days, with the median
            times), the weekly cohorts of the last 8 weeks, the top error causes (the errors:log the /errors page
            reads, by logger:line) and the stopped checks by reason (check_holds).

The numbers are honest ones: the students only - no admin, no teacher - and never a test check or a test
payment (db/crud.py's analytics helpers, a few aggregate queries); each page says what it leaves out.
Every admin may look; a teacher's login never (it only has /checks). Read-only.
Registered in webapp/main.py; the templates are the panel's own (webapp.main.templates, with `can`).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from bot.config import settings
from bot.utils import crm, signup_source
from db import crud
from db.database import get_session
from webapp import insights, panel

logger = logging.getLogger(__name__)
router = APIRouter()

TOP_ERRORS = 20
STATES = {"refunded": ("xato", "pill-bad"), "interrupted": ("bot qayta ishga tushdi", "pill-warn"),
          "expired": ("tugallanmay qoldi", "pill-warn")}  # as webapp/checks_ops.py names them
# bot/utils/check_guard.py's own reasons (the rest are "<ExceptionType>: <message>")
CAUSES = {
    "the bot restarted": "Bot qayta ishga tushdi", "left running": "Tugallanmay qoldi (javob kelmadi)",
    "not graded": "Baholanmadi", "not finished": "Tugamadi", "stopped": "To'xtatildi",
    "chart unreadable": "Grafik o'qilmadi", "chart refused": "Grafik rad etildi",
    "the FSM state could not be saved": "Holat saqlanmadi", "": "Sababi yozilmagan",
}
_EXCEPTION = re.compile(r"^[A-Za-z_][\w.]*$")


def _templates():
    from webapp.main import templates  # noqa: PLC0415 - the panel's own, set up there with its globals

    return templates


def _admin_only(request: Request):
    """Admins (and the owner) only: a teacher's login goes back to its checks page."""
    if (r := panel.guard(request)) is not None:
        return r
    if panel.is_teacher(request):
        return RedirectResponse("/checks", status_code=303)
    return None


def now() -> datetime:
    return datetime.now(timezone.utc)


def pct(n: int | None, of: int | None) -> int | None:
    """n of `of` in whole per cent, a half rounded up (5 of 8 is 63%, not Python's round-half-even 62%); None
    when there is nothing to divide by."""
    return ((n or 0) * 200 + of) // (2 * of) if of else None


def duration(value: timedelta | None) -> str:
    """A median time in words: "45 daqiqa", "13 soat 30 daqiqa", "2 kun 4 soat"."""
    if value is None:
        return "—"
    minutes = int(value.total_seconds() // 60)
    if minutes < 1:
        return "1 daqiqadan kam"
    days, rest = divmod(minutes, 24 * 60)
    hours, minutes = divmod(rest, 60)
    if days:
        return f"{days} kun" + (f" {hours} soat" if hours else "")
    if hours:
        return f"{hours} soat" + (f" {minutes} daqiqa" if minutes else "")
    return f"{minutes} daqiqa"


# --- 📣 Manbalar ----------------------------------------------------------------------------------------------


async def sources_overview(session, at: datetime, with_revenue: bool) -> dict:
    """The sources table, biggest first ("noma'lum" - the users from before 2026-11-16 - last), and its total.
    Without `with_revenue` (no perm "finance") no revenue is read out to the page at all."""
    rows = []
    for r in await crud.source_stats(session, settings.admin_ids, at):
        key = signup_source.key(r["source"])
        rows.append({
            "key": key, "label": signup_source.label(r["source"]), "is_link": signup_source.is_link(r["source"]),
            "link": signup_source.link(key) if signup_source.is_link(r["source"]) else None,
            "signups": r["signups"], "d7": r["d7"], "d30": r["d30"],
            "checked": r["checked"], "checked_pct": pct(r["checked"], r["signups"]),
            "paid": r["paid"], "paid_pct": pct(r["paid"], r["signups"]),
            "revenue": int(r["revenue"] or 0),
        })
    rows.sort(key=lambda x: (x["key"] == signup_source.UNKNOWN, -x["signups"], x["key"]))
    total = {k: sum(x[k] for x in rows) for k in ("signups", "d7", "d30", "checked", "paid", "revenue")}
    total["checked_pct"], total["paid_pct"] = pct(total["checked"], total["signups"]), pct(total["paid"], total["signups"])
    if not with_revenue:
        for x in (*rows, total):
            x.pop("revenue")
    for x in (*rows, total):
        if "revenue" in x:
            x["revenue_str"] = insights.som(x["revenue"])
    return {"rows": rows, "total": total, "left_out": await crud.analytics_left_out(session, settings.admin_ids)}


def link_builder(typed: str | None) -> dict | None:
    """The link builder's answer to what was typed: {"typed", "name", "link"} or {"typed", "error"}."""
    if typed is None or not typed.strip():
        return None
    typed = typed.strip()[:200]
    name = signup_source.slug(typed)
    if not name:
        return {"typed": typed, "error": "Nomda kamida bitta lotin harfi yoki raqam bo'lsin (a-z, 0-9, _)."}
    if name in signup_source.RESERVED:
        return {"typed": typed, "error": f"«{name}» — botning o'z manba nomi, boshqa nom tanlang."}
    return {"typed": typed, "name": name, "link": signup_source.link(name)}


@router.get("/sources", response_class=HTMLResponse)
async def sources_page(request: Request, name: str | None = None):
    if (r := _admin_only(request)) is not None:
        return r
    finance = panel.can(request, "finance")
    async with get_session() as session:
        data = await sources_overview(session, now(), finance)
    return _templates().TemplateResponse(request, "sources.html", {
        "active": "sources", "d": data, "finance": finance, "built": link_builder(name),
        "bot": settings.telegram_bot_username, "prefix": signup_source.PREFIX, "max_name": signup_source.MAX_NAME,
        "reserved_json": json.dumps(sorted(signup_source.RESERVED)),
    })


# --- 📈 Voronka ------------------------------------------------------------------------------------------------


def _funnel_view(f: dict) -> dict:
    steps = [
        {"label": "Ro'yxatdan o'tdi", "n": f["signups"], "of_all": 100 if f["signups"] else None, "of_prev": None},
        {"label": "Birinchi tekshiruv", "n": f["checked"], "of_all": pct(f["checked"], f["signups"]),
         "of_prev": pct(f["checked"], f["signups"])},
        # of those who made a check, how many paid - a payer who never made a check is not one of them
        {"label": "Birinchi to'lov", "n": f["paid"], "of_all": pct(f["paid"], f["signups"]),
         "of_prev": pct(f["paid_checked"], f["checked"])},
    ]
    return {"steps": steps, "to_check": duration(f["to_check"]), "to_pay": duration(f["to_pay"]),
            "paid_unchecked": f["paid"] - f["paid_checked"]}


def _cell(n: int, of: int, partial: bool) -> dict:
    return {"pct": pct(n, of), "n": n, "of": of, "partial": partial and bool(of)}


def _cohort_view(c: dict, current: bool) -> dict:
    end = c["start"] + timedelta(days=6)
    weeks = []
    for k in range(1, crud.ANALYTICS_ACTIVE_WEEKS + 1):
        started, ended = c[f"started{k}"], c[f"ended{k}"]
        weeks.append(_cell(c[f"active{k}"], started, ended < c["size"]) if started else None)
    return {
        "label": f"{c['start']:%d.%m} – {end:%d.%m}", "current": current, "size": c["size"],
        "checked7": _cell(c["checked7"], c["size"], c["open7"] > 0) if c["size"] else None,
        "paid30": _cell(c["paid30"], c["size"], c["open30"] > 0) if c["size"] else None,
        "weeks": weeks,
    }


def stop_cause(reason: str | None) -> str:
    """A stopped check's reason without the details that differ every time: "TimeoutError: Request timed out."
    -> "TimeoutError", "chart unreadable: ValueError: ..." -> "chart unreadable: ValueError"."""
    parts = (reason or "").strip().split(": ")
    head = parts[0].strip()[:80]
    if " " in head and len(parts) > 1 and _EXCEPTION.match(parts[1].strip()):
        head = f"{head}: {parts[1].strip()[:60]}"
    return head


def cause_label(cause: str) -> str:
    head, _, rest = cause.partition(": ")
    name = CAUSES.get(head, head)
    return f"{name}: {rest}" if rest else name


def group_stops(rows: list[dict]) -> dict:
    """db/crud.py's stopped_check_groups (by state and the whole reason) merged by state and cause."""
    groups: dict[tuple[str, str], dict] = {}
    for r in rows:
        cause = stop_cause(r["reason"])
        g = groups.setdefault((r["state"], cause), {
            "state": r["state"], "cause": cause, "label": cause_label(cause), "n7": 0, "n30": 0, "coins": 0,
            "last": None, "sample": None,
            "state_label": STATES.get(r["state"], (r["state"], ""))[0], "tone": STATES.get(r["state"], ("", ""))[1],
        })
        g["n7"] += r["n7"]
        g["n30"] += r["n30"]
        g["coins"] += int(r["coins"] or 0)
        if g["last"] is None or (r["last"] and r["last"] > g["last"]):
            g["last"], g["sample"] = r["last"], r["reason"]
    out = sorted(groups.values(), key=lambda g: (-g["n30"], -g["n7"], g["label"]))
    for g in out:
        g["last_str"], g["ago"] = insights.fmt(g["last"]), insights.ago(g["last"])
    return {"groups": out, "n7": sum(g["n7"] for g in out), "n30": sum(g["n30"] for g in out),
            "coins": sum(g["coins"] for g in out)}


def group_errors(raw: list, limit: int = TOP_ERRORS) -> dict:
    """The stored errors (bot/utils/crm.py log_error: newest first, already redacted) by where they come from
    (logger:line): how many times - an entry stands for itself and its `again` repeats, the ones the forwarder
    held back - when last, and the latest one's message as a sample."""
    groups: dict[str, dict] = {}
    oldest = None
    entries = 0
    for item in raw:
        try:
            e = json.loads(item)
        except (TypeError, ValueError):
            continue
        if not isinstance(e, dict):
            continue
        entries += 1
        try:
            at = float(e.get("at") or 0)
            again = max(0, int(e.get("again") or 0))
        except (TypeError, ValueError):
            at, again = 0.0, 0
        oldest = at if oldest is None else min(oldest, at)
        key = f"{e.get('logger') or '?'}:{e.get('line') or '?'}"
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"key": key, "count": 0, "last": -1.0, "first": at, "level": "", "message": "",
                               "exc": "", "processes": set()}
        g["count"] += 1 + again
        g["first"] = min(g["first"], at)
        g["processes"].add(e.get("process") or "bot")
        if e.get("level") == "ERROR" or not g["level"]:
            g["level"] = e.get("level") or "ERROR"
        if at > g["last"]:
            g["last"] = at
            g["message"] = str(e.get("message") or "")
            g["exc"] = next((line.strip() for line in reversed(str(e.get("exc") or "").splitlines()) if line.strip()), "")
    out = sorted(groups.values(), key=lambda g: (-g["count"], -g["last"]))[:limit]
    for g in out:
        last = datetime.fromtimestamp(max(g["last"], 0), timezone.utc)
        g["last_str"], g["ago"] = insights.fmt(last), insights.ago(last)
        g["processes"] = sorted(g["processes"])
    since = datetime.fromtimestamp(oldest, timezone.utc) if oldest else None
    return {"groups": out, "places": len(groups), "entries": entries, "since": insights.fmt(since) if since else "—"}


async def stored_errors() -> list:
    try:
        return await asyncio.wait_for(crm.redis().lrange(crm.ERRORS_LOG_KEY, 0, crm.ERRORS_LOG_CAP - 1), 3)
    except Exception:  # noqa: BLE001 - the page opens without them
        logger.warning("Funnel page: the errors log not read", exc_info=True)
        return []


async def funnel_overview(session, at: datetime) -> dict:
    admins = settings.admin_ids
    f = await crud.funnel_stats(session, admins, at)
    cohorts = await crud.weekly_cohorts(session, admins, at)
    stops = await crud.stopped_check_groups(session, admins, at)
    return {
        "all": _funnel_view(f["all"]), "recent": _funnel_view(f["recent"]),
        "cohorts": [_cohort_view(c, i == len(cohorts) - 1) for i, c in enumerate(cohorts)][::-1],  # newest first
        "stops": group_stops(stops),
        "left_out": await crud.analytics_left_out(session, admins),
    }


@router.get("/funnel", response_class=HTMLResponse)
async def funnel_page(request: Request):
    if (r := _admin_only(request)) is not None:
        return r
    async with get_session() as session:
        data = await funnel_overview(session, now())
    data["errors"] = group_errors(await stored_errors())
    return _templates().TemplateResponse(request, "funnel.html", {"active": "funnel", "d": data})
