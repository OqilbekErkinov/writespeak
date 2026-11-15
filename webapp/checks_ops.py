"""The checks behind the scenes (the 2026-11-15 audit): what is being graded right now - and whether a deploy
would wait for it - and every check that stopped (an error, a restart, a refused start), from the check holds
(bot/utils/check_guard.py): why, whether its coins went back, whether the student got a report later. Plus two
actions on a check: send its report to the student again, and mark it a test (left out of the student's
progress and of the Speaking anchors). Admins only - never a teacher login.

Wired into webapp/main.py like webapp/panel.py (`templates` is set there).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram.types import FSInputFile
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from bot.i18n import t
from bot.utils.report_names import report_filename
from db.database import get_session
from db.models import CheckHold, SpeakingSubmission, User, WritingSubmission
from webapp import insights, panel

logger = logging.getLogger(__name__)
router = APIRouter()
templates = None  # set by webapp/main.py

KINDS = {
    "writing_task1": "Writing Task 1", "writing_task2": "Writing Task 2", "speaking_part1": "Speaking Part 1",
    "speaking_part2": "Speaking Part 2", "speaking_part3": "Speaking Part 3", "speaking_full": "Speaking Full test",
}
STATES = {"refunded": ("xato", "pill-bad"), "interrupted": ("bot qayta ishga tushdi", "pill-warn"),
          "expired": ("tugallanmay qoldi", "pill-warn")}


def _admin_only(request: Request):
    """Admins (and the owner) only: a teacher login is sent back to its checks page."""
    if (r := panel.guard(request)) is not None:
        return r
    if panel.is_teacher(request):
        return RedirectResponse("/checks", status_code=303)
    return None


@router.get("/checks/stopped", response_class=HTMLResponse)
async def stopped_checks(request: Request):
    if (r := _admin_only(request)) is not None:
        return r
    now = datetime.now(timezone.utc)
    async with get_session() as session:
        running = (await session.execute(select(CheckHold).where(CheckHold.state == "held")
                                         .order_by(CheckHold.created_at))).scalars().all()
        stopped = (await session.execute(
            select(CheckHold).where(CheckHold.state.in_(tuple(STATES)), CheckHold.created_at >= now - timedelta(days=30))
            .order_by(CheckHold.created_at.desc()).limit(300))).scalars().all()
        later = {}
        if stopped:
            # a report the student got afterwards: their next charged check of the same module
            charged = (await session.execute(select(CheckHold.user_id, CheckHold.module, CheckHold.created_at).where(
                CheckHold.state == "charged", CheckHold.user_id.in_({h.user_id for h in stopped}),
                CheckHold.created_at >= now - timedelta(days=31)))).all()
            for h in stopped:
                later[h.id] = any(u == h.user_id and m == h.module and at > h.created_at for u, m, at in charged)
        week = now - timedelta(days=7)
        counts = dict((await session.execute(select(CheckHold.state, func.count()).where(CheckHold.created_at >= week)
                                             .group_by(CheckHold.state))).all())
        ids = {h.user_id for h in running} | {h.user_id for h in stopped}
        users = {u.id: u for u in (await session.execute(select(User).where(User.id.in_(ids)))).scalars()} if ids else {}
    total = sum(counts.values())
    failed = sum(counts.get(s, 0) for s in STATES)

    def row(h: CheckHold) -> dict:
        label, tone = STATES.get(h.state, (h.state, ""))
        return {"h": h, "user": users.get(h.user_id), "kind": KINDS.get(h.kind, h.kind), "when": insights.fmt(h.created_at),
                "minutes": int((now - h.created_at).total_seconds() // 60), "label": label, "tone": tone,
                "later": later.get(h.id, False)}

    return templates.TemplateResponse(request, "checks_stopped.html", {
        "active": "checks_stopped", "running": [row(h) for h in running], "stopped": [row(h) for h in stopped],
        "capacity": await panel.capacity_state(),
        "week": {"total": total, "failed": failed, "charged": counts.get("charged", 0),
                 "rate": round(failed / total * 100) if total else 0},
    })


async def _submission(session, kind: str, submission_id: int):
    model = WritingSubmission if kind == "w" else SpeakingSubmission if kind == "s" else None
    return await session.get(model, submission_id) if model is not None else None


@router.post("/checks/{kind}/{submission_id}/resend")
async def resend_report(request: Request, kind: str, submission_id: int, back: str = Form("/checks")):
    """The check's PDF report sent to the student again (a report fixed after a bug, a lost chat)."""
    if (r := _admin_only(request)) is not None:
        return r
    back = back if back.startswith("/checks") else "/checks"
    sep = "&" if "?" in back else "?"
    async with get_session() as session:
        sub = await _submission(session, kind, submission_id)
        user = await session.get(User, sub.user_id) if sub is not None else None
    if sub is None or user is None or not sub.feedback_pdf_path or not Path(sub.feedback_pdf_path).exists():
        return RedirectResponse(f"{back}{sep}done=nopdf#c-{kind}{submission_id}", status_code=303)
    lang = user.language or "uz"
    what = sub.task_type if kind == "w" else sub.part
    bot = panel.new_bot()
    try:
        await bot.send_document(user.id, FSInputFile(sub.feedback_pdf_path,
                                                     filename=report_filename("writing" if kind == "w" else "speaking",
                                                                              what)),
                                caption=t("check.report_resent", lang))
        ok = True
    except Exception:  # noqa: BLE001 - blocked the bot, etc.
        logger.warning("Report %s%s not resent to %s", kind, submission_id, user.id, exc_info=True)
        ok = False
    finally:
        await bot.session.close()
    await panel.audit(request, "report_resent", user.id,
                      details=f"{kind.upper()}#{submission_id}" + ("" if ok else " (yetkazilmadi)"))
    return RedirectResponse(f"{back}{sep}done={'resent' if ok else 'notsent'}#c-{kind}{submission_id}", status_code=303)


@router.post("/checks/{kind}/{submission_id}/test")
async def toggle_test(request: Request, kind: str, submission_id: int, back: str = Form("/checks")):
    """A check marked a test (or back): left out of the student's progress and of the Speaking anchors."""
    if (r := _admin_only(request)) is not None:
        return r
    back = back if back.startswith("/checks") else "/checks"
    sep = "&" if "?" in back else "?"
    async with get_session() as session:
        sub = await _submission(session, kind, submission_id)
        if sub is None:
            return RedirectResponse(back, status_code=303)
        sub.is_test = not sub.is_test
        now_test, user_id = sub.is_test, sub.user_id
        await session.commit()
    await panel.audit(request, "check_test" if now_test else "check_real", user_id,
                      details=f"{kind.upper()}#{submission_id}")
    return RedirectResponse(f"{back}{sep}done={'marked_test' if now_test else 'marked_real'}#c-{kind}{submission_id}",
                            status_code=303)
