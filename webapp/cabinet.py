"""Teachers' web cabinet: https://writespeak.uz/cabinet (the host nginx proxies
only /cabinet/ of that domain to this app - see deploy/nginx/writespeak.uz).

Sign-in is one tap from the bot's "Web-kabinet" button: it opens
/cabinet/login with a token signed by the web session secret
(bot/utils/cabinet_auth.py), valid for 10 minutes. The session then holds
`cabinet_uid`, kept apart from the admin login (and the cookie is per host).

A teacher sees the groups they manage - their own, and for a center owner
every group of the center - with the same rules as in the bot
(db/crud.py's group section): only work submitted after a student joined.
Read-only: tables, charts, assignment results, report PDFs, Excel/PDF exports.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from bot.utils.cabinet_auth import read_login_token
from db import crud
from db.crud import CRITERIA_LABELS
from db.database import get_session
from services.pdf.group_reports import band_chart, build_group_excel, build_group_pdf, build_parent_report_pdf

router = APIRouter(prefix="/cabinet")
templates = None  # set by webapp.main (shared Jinja2Templates)

_TASHKENT = timedelta(hours=5)


def _day(value: datetime | None) -> str:
    return (value + _TASHKENT).strftime("%d.%m.%Y") if value else "—"


def _band(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}"


def _weakest(weakest) -> str:
    return f"{CRITERIA_LABELS.get(weakest[0], weakest[0])} ({weakest[1]:.1f})" if weakest else "—"


def _render(request: Request, name: str, context: dict, status_code: int = 200):
    context.update(day=_day, band=_band, weakest=_weakest, user_name=request.session.get("cabinet_name", ""))
    return templates.TemplateResponse(request, f"cabinet/{name}", context, status_code=status_code)


def _uid(request: Request) -> int | None:
    return request.session.get("cabinet_uid")


async def _group_or_none(request: Request, group_id: int):
    uid = _uid(request)
    if uid is None:
        return None
    async with get_session() as session:
        group = await crud.get_group(session, group_id)
        if not await crud.can_manage_group(session, uid, group):
            return None
    return group


def _login_redirect() -> RedirectResponse:
    return RedirectResponse("/cabinet/expired", status_code=303)


@router.get("/login")
async def login(request: Request, token: str = ""):
    uid = read_login_token(token)
    if uid is None:
        return _login_redirect()
    async with get_session() as session:
        user = await crud.get_user(session, uid)
        groups = await crud.get_managed_groups(session, uid)
        center = await crud.get_user_center(session, uid)
    if user is None or (not groups and center is None):
        return _login_redirect()
    request.session["cabinet_uid"] = uid
    request.session["cabinet_name"] = user.full_name
    return RedirectResponse("/cabinet/", status_code=303)


@router.get("/expired", response_class=HTMLResponse)
async def expired(request: Request):
    return _render(request, "expired.html", {}, status_code=401)


@router.get("/logout")
async def logout(request: Request):
    request.session.pop("cabinet_uid", None)
    request.session.pop("cabinet_name", None)
    return _login_redirect()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    uid = _uid(request)
    if uid is None:
        return _login_redirect()
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    async with get_session() as session:
        center = await crud.get_user_center(session, uid)
        cards = []
        for group in await crud.get_managed_groups(session, uid):
            members = await crud.get_group_works(session, group.id)
            works = [w for _, _, ws in members for w in ws]
            teacher = await crud.get_user(session, group.teacher_id)
            cards.append({
                "group": group,
                "teacher": teacher.full_name if teacher else "",
                "students": len(members),
                "week": sum(1 for w in works if w["created_at"] >= week_ago),
                "summary": crud.summarize_works(works),
            })
        owner = await crud.get_user(session, center.owner_id) if center else None
    return _render(request, "index.html", {
        "cards": cards, "center": center, "is_owner": center is not None and center.owner_id == uid,
        "balance": owner.coin_balance if owner else None,
    })


@router.get("/group/{group_id}", response_class=HTMLResponse)
async def group_page(request: Request, group_id: int):
    group = await _group_or_none(request, group_id)
    if group is None:
        return _login_redirect()
    async with get_session() as session:
        members = await crud.get_group_works(session, group.id)
        overview = await crud.get_group_overview(session, group.id)
        board = await crud.get_group_leaderboard(session, group.id)
        assignments = await crud.list_group_assignments(session, group.id, limit=50)
        teacher = await crud.get_user(session, group.teacher_id)
    rows = []
    for user, joined_at, works in members:
        s = crud.summarize_works(works)
        rows.append({"user": user, "joined": joined_at, "s": s, "streak": crud.activity_streak(works)})
    rows.sort(key=lambda r: -r["s"]["total"])
    all_works = [w for _, _, ws in members for w in ws]
    return _render(request, "group.html", {
        "group": group, "teacher": teacher, "overview": overview, "rows": rows, "board": board,
        "assignments": assignments, "now": datetime.now(timezone.utc),
        "chart": await asyncio.to_thread(band_chart, all_works, "Ballar dinamikasi (kunlik o'rtacha)", True),
    })


@router.get("/group/{group_id}/student/{student_id}", response_class=HTMLResponse)
async def student_page(request: Request, group_id: int, student_id: int):
    group = await _group_or_none(request, group_id)
    if group is None:
        return _login_redirect()
    async with get_session() as session:
        membership = await crud.get_membership(session, group.id, student_id)
        if membership is None:
            return RedirectResponse(f"/cabinet/group/{group.id}", status_code=303)
        student = await crud.get_user(session, student_id)
        works = await crud.get_works_since(session, student_id, membership.joined_at)
        comments = await crud.get_comments_for_works(session, works)
    s = crud.summarize_works(works)
    criteria = sorted(s["criteria"].items(), key=lambda kv: kv[1])
    return _render(request, "student.html", {
        "group": group, "student": student, "joined": membership.joined_at, "s": s, "works": works,
        "comments": comments, "criteria": [(CRITERIA_LABELS.get(k, k), v) for k, v in criteria],
        "streak": crud.activity_streak(works),
        "chart": await asyncio.to_thread(band_chart, works, "Ballar dinamikasi"),
    })


@router.get("/group/{group_id}/assignment/{assignment_id}", response_class=HTMLResponse)
async def assignment_page(request: Request, group_id: int, assignment_id: int):
    group = await _group_or_none(request, group_id)
    if group is None:
        return _login_redirect()
    async with get_session() as session:
        assignment = await crud.get_assignment(session, assignment_id)
        if assignment is None or assignment.group_id != group.id:
            return RedirectResponse(f"/cabinet/group/{group.id}", status_code=303)
        results = await crud.get_assignment_results(session, assignment)
    labels = {
        "writing_task1": "W1", "writing_task2": "W2",
        "speaking_part1": "S1", "speaking_part2": "S2", "speaking_part3": "S3",
    }
    return _render(request, "assignment.html", {
        "group": group, "assignment": assignment, "results": results,
        "part_labels": [labels.get(p.module, p.module) for p in results["parts"]],
    })


@router.get("/report/{kind}/{submission_id}.pdf")
async def report_pdf(request: Request, kind: str, submission_id: int):
    uid = _uid(request)
    if uid is None or kind not in ("w", "s"):
        return _login_redirect()
    async with get_session() as session:
        path = await crud.get_member_report_pdf(session, uid, kind, submission_id)
    if path is None:
        return Response(status_code=404)
    return FileResponse(path, media_type="application/pdf", filename=f"Bandzilla_report_{kind}{submission_id}.pdf")


async def _export_data(group):
    async with get_session() as session:
        members = await crud.get_group_works(session, group.id)
        overview = await crud.get_group_overview(session, group.id)
        assignments = []
        for item in await crud.list_group_assignments(session, group.id, limit=50):
            assignments.append({"assignment": item["assignment"], "results": await crud.get_assignment_results(session, item["assignment"])})
        teacher = await crud.get_user(session, group.teacher_id)
    return members, overview, assignments, teacher


def _download(content: bytes, filename: str, media_type: str) -> Response:
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)
    return Response(content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{safe}"'})


@router.get("/group/{group_id}/export.xlsx")
async def export_excel(request: Request, group_id: int):
    group = await _group_or_none(request, group_id)
    if group is None:
        return _login_redirect()
    members, _, assignments, _ = await _export_data(group)
    content = await asyncio.to_thread(build_group_excel, group.name, members, assignments, "uz")
    return _download(content, f"Bandzilla_{group.name}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.get("/group/{group_id}/report.pdf")
async def export_pdf(request: Request, group_id: int):
    group = await _group_or_none(request, group_id)
    if group is None:
        return _login_redirect()
    members, overview, assignments, teacher = await _export_data(group)
    content = await asyncio.to_thread(build_group_pdf, group.name, teacher.full_name if teacher else "", overview, members, assignments, "uz")
    return _download(content, f"Bandzilla_{group.name}.pdf", "application/pdf")


@router.get("/group/{group_id}/student/{student_id}/monthly.pdf")
async def monthly_pdf(request: Request, group_id: int, student_id: int):
    group = await _group_or_none(request, group_id)
    if group is None:
        return _login_redirect()
    now = datetime.now(timezone.utc)
    month_start, prev_start = now - timedelta(days=30), now - timedelta(days=60)
    async with get_session() as session:
        membership = await crud.get_membership(session, group.id, student_id)
        if membership is None:
            return Response(status_code=404)
        student = await crud.get_user(session, student_id)
        teacher = await crud.get_user(session, group.teacher_id)
        works_month = await crud.get_works_since(session, student_id, max(month_start, membership.joined_at))
        works_prev = (
            await crud.get_works_since(session, student_id, max(prev_start, membership.joined_at), until=month_start)
            if membership.joined_at < month_start else []
        )
        comments = await crud.get_comments_for_works(session, works_month)
        assignments = await crud.get_student_assignments(session, student_id, closed_days=30)
    assignments = [a for a in assignments if a["group"].id == group.id and a["assignment"].created_at >= month_start]
    content = await asyncio.to_thread(
        build_parent_report_pdf,
        student_name=student.full_name, group_name=group.name, teacher_name=teacher.full_name if teacher else "",
        works_month=works_month, works_prev=works_prev,
        comments=[(w, comments[(w["kind"], w["id"])]) for w in works_month if (w["kind"], w["id"]) in comments],
        assignments_done=sum(1 for a in assignments if len(a["done"]) == len(a["parts"])),
        assignments_total=len(assignments), period_start=max(month_start, membership.joined_at), period_end=now, lang="uz",
    )
    return _download(content, f"Bandzilla_{student.full_name}_{_day(now)}.pdf", "application/pdf")
