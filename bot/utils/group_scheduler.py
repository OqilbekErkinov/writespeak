"""Background jobs for groups, run by the bot process (bot/main.py) since they
send with the bot itself:

- assignment reminders: once an assignment is due within REMIND_BEFORE, the
  students who haven't finished it get one reminder (db.crud.take_due_reminders
  marks it first, so a restart never reminds twice);
- the weekly digest: from Monday DIGEST_HOUR (Tashkent) on, every group's
  teacher - and the owner of the group's center, if that's someone else -
  gets last week's summary once (db.crud.take_digest_groups marks the week).
"""
from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot

from bot.i18n import t
from bot.keyboards.group_kb import my_assignments_kb
from db import crud
from db.database import get_session

logger = logging.getLogger(__name__)

CHECK_EVERY = 300  # seconds
REMIND_BEFORE = timedelta(hours=24)
DIGEST_HOUR = 9
SEND_PAUSE = 0.05
_TASHKENT = timedelta(hours=5)


async def run_group_jobs(bot: Bot) -> None:
    while True:
        try:
            await send_assignment_reminders(bot)
            now = datetime.now(timezone.utc) + _TASHKENT
            if now.weekday() == 0 and now.hour >= DIGEST_HOUR:
                await send_weekly_digests(bot)
        except Exception:
            logger.exception("Group background jobs failed")
        await asyncio.sleep(CHECK_EVERY)


async def _send(bot: Bot, chat_id: int, text: str, **kwargs) -> bool:
    try:
        await bot.send_message(chat_id, text, disable_web_page_preview=True, **kwargs)
        return True
    except Exception:
        logger.info("Group message to %s failed", chat_id)
        return False
    finally:
        await asyncio.sleep(SEND_PAUSE)


def _esc(text: str | None) -> str:
    return html.escape(text or "?", quote=False)


def _day(value: datetime) -> str:
    return (value + _TASHKENT).strftime("%d.%m.%Y %H:%M")


async def send_assignment_reminders(bot: Bot) -> int:
    async with get_session() as session:
        due = await crud.take_due_reminders(session, REMIND_BEFORE)
    sent = 0
    for item in due:
        for student in item["pending"]:
            lang = student.language or "uz"
            text = t(
                "as.reminder", lang,
                title=_esc(item["assignment"].title), group=_esc(item["group"].name),
                due=_day(item["assignment"].due_at),
            )
            sent += await _send(bot, student.id, text, reply_markup=my_assignments_kb(lang))
    if due:
        logger.info("Assignment reminders: %d sent for %d assignments", sent, len(due))
    return sent


def last_week_label() -> str:
    year, week, _ = (datetime.now(timezone.utc) + _TASHKENT - timedelta(days=7)).isocalendar()
    return f"{year}-W{week:02d}"


def _band(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}"


async def build_digest(session, group, lang: str) -> str:
    """Last week's summary of one group (Monday-Sunday, Tashkent)."""
    start = crud.week_start_tashkent_as_utc(1)
    end = crud.week_start_tashkent_as_utc(0)
    prev_start = crud.week_start_tashkent_as_utc(2)
    members = await crud.get_group_members(session, group.id)
    week_rows, prev_works, week_works, inactive = [], [], [], []
    for user, joined_at in members:
        works = await crud.get_works_since(session, user.id, max(joined_at, prev_start))
        this = [w for w in works if start <= w["created_at"] < end]
        prev_works += [w for w in works if prev_start <= w["created_at"] < start]
        week_works += this
        if this:
            week_rows.append((user, len(this)))
        else:
            inactive.append(user)
    summary = crud.summarize_works(week_works)
    prev = crud.summarize_works(prev_works)
    trend = ""
    if summary["average"] is not None and prev["average"] is not None:
        delta = summary["average"] - prev["average"]
        trend = f" ({'↑' if delta >= 0 else '↓'}{abs(delta):.1f})" if abs(delta) >= 0.05 else " (=)"
    leaderboard = await crud.get_group_leaderboard(session, group.id)
    top = ", ".join(f"{_esc(u.full_name)} ({n})" for u, n in sorted(week_rows, key=lambda r: -r[1])[:3]) or "—"
    improved = ", ".join(f"{_esc(u.full_name)} (+{d:.1f})" for u, d in leaderboard["improved"][:3]) or "—"
    sleeping = ", ".join(_esc(u.full_name) for u in inactive[:10]) + (f" +{len(inactive) - 10}" if len(inactive) > 10 else "")
    lines = [
        t("gr.digest", lang,
          name=_esc(group.name), start=(start + _TASHKENT).strftime("%d.%m"), end=(end + _TASHKENT - timedelta(days=1)).strftime("%d.%m"),
          active=len(week_rows), members=len(members), total=summary["total"], writing=summary["writing"],
          speaking=summary["speaking"], avg=_band(summary["average"]), trend=trend, top=top, improved=improved,
          weakest=crud.CRITERIA_LABELS.get(summary["weakest"][0], summary["weakest"][0]) if summary["weakest"] else "—",
          inactive=sleeping or "—"),
    ]
    for item in await crud.list_group_assignments(session, group.id, limit=5):
        assignment = item["assignment"]
        if assignment.due_at >= start:
            lines.append(t("gr.digest_assignment", lang, title=_esc(assignment.title), complete=item["complete"], members=item["members"]))
    if group.sponsor_checks:
        payer = await crud.get_user(session, await crud.get_group_payer_id(session, group))
        lines.append(t("gr.digest_balance", lang, balance=payer.coin_balance if payer else 0))
    return "\n".join(lines)


async def send_weekly_digests(bot: Bot) -> int:
    week = last_week_label()
    async with get_session() as session:
        groups = await crud.take_digest_groups(session, week)
        messages = []
        for group in groups:
            recipients = {group.teacher_id}
            payer_id = await crud.get_group_payer_id(session, group)
            recipients.add(payer_id)
            for user_id in recipients:
                user = await crud.get_user(session, user_id)
                if user is not None:
                    messages.append((user.id, await build_digest(session, group, user.language or "uz")))
    sent = 0
    for chat_id, text in messages:
        sent += await _send(bot, chat_id, text)
    if groups:
        logger.info("Weekly digests %s: %d sent for %d groups", week, sent, len(groups))
    return sent
