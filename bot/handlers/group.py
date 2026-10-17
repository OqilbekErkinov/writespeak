"""Guruh / Группа (B2B): teachers and learning centers.

- Who can open groups: admins, users an admin made a teacher (a student taps
  "Guruh yaratish" and sends a request; admins approve it in Telegram, like
  payment receipts), and teachers who joined a center through its invite.
- A teacher may run several groups. A center's owner (made one by an admin in
  the web panel) invites teachers, sees and manages every group of the
  center, and pays for it: in a center's groups only the owner spends coins
  (gifts, "center pays for checks"); elsewhere the group's teacher does.
- Students join through a group's signed invite link (bot/handlers/start.py).
  What a teacher sees about a student covers only work submitted after the
  student joined: stats, a paged student list, a card per student with their
  recent works (report PDF, teacher comment + band), a monthly report for
  parents, Excel/PDF exports, a leaderboard, announcements, a QR code, and
  coin gifts. Assignments live in bot/handlers/group_assignments.py, the
  weekly digest and reminders in bot/utils/group_scheduler.py.
"""
from __future__ import annotations

import asyncio
import html
import io
import logging
import math
from datetime import datetime, timedelta, timezone

import segno
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from bot.config import settings
from bot.i18n import t
from bot.keyboards import group_kb as kb
from bot.keyboards.main_menu_kb import BTN_GROUP_EN, BTN_GROUP_RU, BTN_GROUP_UZ, MENU_BUTTON_TEXTS
from bot.keyboards.payment_kb import buy_coins_kb
from bot.states.group_states import GroupStates
from bot.utils import crm_events
from bot.utils.cabinet_auth import cabinet_login_url
from bot.utils.group_invite import center_invite_link, invite_link
from bot.utils.messages import show_screen
from db import crud
from db.crud import CRITERIA_LABELS
from db.database import get_session
from db.models import Group, User, UserRole
from services.pdf.group_reports import build_group_excel, build_group_pdf, build_parent_report_pdf
from services.storage.file_storage import read_file

logger = logging.getLogger(__name__)
router = Router(name="group")

RECENT_WORKS = 6
SEND_PAUSE = 0.05
_TASHKENT = timedelta(hours=5)


def _esc(text: str | None) -> str:
    return html.escape(text or "?", quote=False)


def _band(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}"


def _date(value: datetime | None) -> str:
    return (value + _TASHKENT).strftime("%d.%m.%Y") if value else "—"


def _weakest(weakest: tuple[str, float] | None) -> str:
    return f"{CRITERIA_LABELS.get(weakest[0], weakest[0])} ({weakest[1]:.1f})" if weakest else "—"


def _ints(data: str, prefix: str) -> list[int]:
    return [int(x) for x in data.removeprefix(prefix).split(":")]


def _can_open_group(user: User | None, user_id: int) -> bool:
    return user_id in settings.admin_ids or (
        user is not None and user.role in (UserRole.teacher, UserRole.admin)
    )


async def _notify(bot, user: User | None, key: str, **kwargs) -> bool:
    """Messages another user in their own language; a blocked bot is not an error here."""
    if user is None:
        return False
    try:
        await bot.send_message(user.id, t(key, user.language or "uz", **kwargs), disable_web_page_preview=True)
        return True
    except Exception:
        logger.info("Could not notify user %s (%s)", user.id, key)
        return False


async def _managed(callback: CallbackQuery, group_id: int, lang: str) -> Group | None:
    """The group, if this user manages it - else back to their Guruh home."""
    async with get_session() as session:
        group = await crud.get_group(session, group_id)
        allowed = await crud.can_manage_group(session, callback.from_user.id, group)
    if allowed:
        return group
    text, markup = await _home(callback.from_user.id, lang)
    await show_screen(callback, text, markup)
    return None


async def _can_pay(session, user_id: int, group: Group) -> bool:
    """Only the payer (the teacher, or the center's owner for a center group) spends coins."""
    return await crud.get_group_payer_id(session, group) == user_id


# --- Home ------------------------------------------------------------------------------------


async def _home(user_id: int, lang: str):
    """(text, keyboard) of the Guruh screen for this user."""
    async with get_session() as session:
        user = await crud.get_user(session, user_id)
        managed = await crud.get_managed_groups(session, user_id)
        memberships = await crud.get_member_groups(session, user_id)
        center = await crud.get_user_center(session, user_id)
        counts = {g.id: await crud.get_group_member_count(session, g.id) for g in managed}
    can_open = _can_open_group(user, user_id)
    if managed or (can_open and center is not None):
        lines = [t("group.teacher_home_title", lang)]
        if center is not None:
            lines.append(t("group.center_line", lang, name=_esc(center.name)))
        lines.append(t("group.teacher_home_hint", lang) if managed else t("group.teacher_no_groups", lang))
        groups = [(g.id, t("group.group_button", lang, name=g.name, count=counts[g.id])) for g in managed]
        markup = kb.teacher_home_kb(groups, center is not None, bool(memberships), bool(settings.cabinet_base_url), lang)
        return "\n".join(lines), markup
    if memberships:
        return _member_home_text(memberships, lang), kb.member_home_kb(
            [(g.id, g.name, g.leaderboard_on) for g, _ in memberships], can_open, lang
        )
    return t("group.no_group", lang), kb.no_group_kb(lang)


def _member_home_text(memberships, lang: str) -> str:
    lines = [t("group.member_home_title", lang)]
    lines += [
        t("group.member_home_row", lang, name=_esc(g.name), teacher=_esc(teacher.full_name if teacher else None))
        for g, teacher in memberships
    ]
    lines.append(t("group.member_home_note", lang))
    return "\n".join(lines)


@router.message(F.text.in_({BTN_GROUP_UZ, BTN_GROUP_RU, BTN_GROUP_EN}))
async def open_group(message: Message, state: FSMContext, lang: str) -> None:
    await state.clear()
    text, markup = await _home(message.from_user.id, lang)
    await message.answer(text, reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(F.data == kb.CB_HOME)
async def back_home(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    await state.clear()
    text, markup = await _home(callback.from_user.id, lang)
    await show_screen(callback, text, markup)


@router.callback_query(F.data == kb.CB_MEMBER_HOME)
async def member_home(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    await state.clear()
    async with get_session() as session:
        memberships = await crud.get_member_groups(session, callback.from_user.id)
        user = await crud.get_user(session, callback.from_user.id)
    if not memberships:
        text, markup = await _home(callback.from_user.id, lang)
        await show_screen(callback, text, markup)
        return
    await show_screen(
        callback,
        _member_home_text(memberships, lang),
        kb.member_home_kb(
            [(g.id, g.name, g.leaderboard_on) for g, _ in memberships], _can_open_group(user, callback.from_user.id), lang
        ),
    )


# --- Opening a group / becoming a teacher ---------------------------------------------------------


@router.callback_query(F.data == kb.CB_CREATE)
async def start_create_group(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    async with get_session() as session:
        user = await crud.get_user(session, callback.from_user.id)
    if not _can_open_group(user, callback.from_user.id):
        await show_screen(callback, t("group.teachers_only", lang), kb.request_teacher_kb(lang))
        return
    await state.set_state(GroupStates.awaiting_name)
    await show_screen(callback, t("group.ask_name", lang), kb.back_kb(lang, kb.CB_HOME))


@router.message(GroupStates.awaiting_name, ~F.text.in_(MENU_BUTTON_TEXTS))
async def receive_group_name(message: Message, state: FSMContext, lang: str) -> None:
    name = " ".join((message.text or "").split())[:100]
    if not name:
        await message.answer(t("group.ask_name", lang))
        return
    await state.clear()
    async with get_session() as session:
        user = await crud.get_user(session, message.from_user.id)
        if not _can_open_group(user, message.from_user.id):
            return
        group = await crud.create_group(session, message.from_user.id, name)
    await crm_events.group_created(group.id)
    text, markup = await _group_screen(message.from_user.id, group.id, lang)
    await message.answer(t("group.created", lang) + "\n\n" + text, reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(F.data == kb.CB_REQUEST)
async def request_teacher(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    tg = callback.from_user
    username = f" (@{tg.username})" if tg.username else ""
    text = (
        "🏫 <b>O'qituvchi bo'lish so'rovi</b>\n\n"
        f"Foydalanuvchi: {_esc(tg.full_name)}{username}\n"
        f"Telegram ID: <code>{tg.id}</code>\n\n"
        "Tasdiqlansa, u guruh ochib, talabalarini kuzata oladi va ularga o'z coinlarini ulasha oladi."
    )
    for admin_id in settings.admin_ids:
        try:
            await callback.bot.send_message(admin_id, text, reply_markup=kb.teacher_review_kb(tg.id))
        except Exception:
            pass  # an admin may not have opened the bot; others still get it
    await crm_events.teacher_request(tg)
    await show_screen(callback, t("group.request_sent", lang), kb.back_kb(lang, kb.CB_HOME))


async def _decide_teacher(callback: CallbackQuery, approve: bool) -> None:
    if callback.from_user.id not in settings.admin_ids:
        await callback.answer("Sizda ruxsat yo'q.", show_alert=True)
        return
    prefix = kb.CB_TEACHER_OK_PREFIX if approve else kb.CB_TEACHER_NO_PREFIX
    user_id = int(callback.data.removeprefix(prefix))
    async with get_session() as session:
        user = await crud.get_user(session, user_id)
        if user is None:
            await callback.answer("Foydalanuvchi topilmadi.", show_alert=True)
            return
        already = user.role in (UserRole.teacher, UserRole.admin)
        if approve and not already:
            await crud.set_user_role(session, user_id, UserRole.teacher)
    await callback.answer()

    status = "✅ Tasdiqlandi" if approve else "⛔ Rad etildi"
    if already:
        status = "ℹ️ Bu foydalanuvchi allaqachon o'qituvchi"
    try:
        await callback.message.edit_text(
            f"{callback.message.html_text}\n\n{status} ({_esc(callback.from_user.full_name)})", reply_markup=None
        )
    except Exception:
        pass
    if not already:
        await _notify(callback.bot, user, "group.teacher_approved" if approve else "group.teacher_rejected")
    await crm_events.teacher_decided(user_id, approve, already, callback.from_user.full_name)


@router.callback_query(F.data.startswith(kb.CB_TEACHER_OK_PREFIX))
async def approve_teacher(callback: CallbackQuery) -> None:
    await _decide_teacher(callback, approve=True)


@router.callback_query(F.data.startswith(kb.CB_TEACHER_NO_PREFIX))
async def reject_teacher(callback: CallbackQuery) -> None:
    await _decide_teacher(callback, approve=False)


# --- One group --------------------------------------------------------------------------------------


async def _group_screen(user_id: int, group_id: int, lang: str):
    async with get_session() as session:
        group = await crud.get_group(session, group_id)
        count = await crud.get_group_member_count(session, group_id)
        payer = await crud.get_user(session, await crud.get_group_payer_id(session, group))
        center = await crud.get_center(session, group.center_id) if group.center_id else None
        teacher = await crud.get_user(session, group.teacher_id)
        can_pay = payer is not None and payer.id == user_id
    lines = [t("group.group_title", lang, name=_esc(group.name), count=count)]
    if center is not None:
        lines.append(t("group.center_line", lang, name=_esc(center.name)))
    if teacher is not None and teacher.id != user_id:
        lines.append(t("group.teacher_line", lang, name=_esc(teacher.full_name)))
    if group.sponsor_checks:
        limit = t("group.limit_none", lang) if group.daily_limit is None else str(group.daily_limit)
        lines.append(t("group.sponsor_on_line", lang, balance=payer.coin_balance if payer else 0, limit=limit))
    else:
        lines.append(t("group.sponsor_off_line", lang))
    lines.append(t("group.invite_line", lang, link=invite_link(group.id)))
    return "\n".join(lines), kb.group_kb(group.id, can_pay, lang)


@router.callback_query(F.data.startswith(kb.CB_GROUP_PREFIX))
async def open_one_group(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    await state.clear()
    group = await _managed(callback, int(callback.data.removeprefix(kb.CB_GROUP_PREFIX)), lang)
    if group is None:
        return
    text, markup = await _group_screen(callback.from_user.id, group.id, lang)
    await show_screen(callback, text, markup)


@router.callback_query(F.data.startswith(kb.CB_STATS_PREFIX))
async def show_stats(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    group = await _managed(callback, int(callback.data.removeprefix(kb.CB_STATS_PREFIX)), lang)
    if group is None:
        return
    async with get_session() as session:
        overview = await crud.get_group_overview(session, group.id)
    back = kb.back_kb(lang, f"{kb.CB_GROUP_PREFIX}{group.id}")
    if not overview["members"]:
        await show_screen(callback, t("group.no_students", lang), back)
        return
    s = overview["summary"]
    most_active = ", ".join(f"{_esc(name)} ({total})" for name, total in overview["most_active"]) or "—"
    text = t(
        "group.stats", lang,
        name=_esc(group.name), members=overview["members"], active=overview["active_week"], total=s["total"],
        writing=s["writing"], speaking=s["speaking"], writing_avg=_band(s["writing_avg"]),
        speaking_avg=_band(s["speaking_avg"]), weakest=_weakest(s["weakest"]), most_active=most_active,
        inactive=overview["inactive"],
    )
    await show_screen(callback, text, back)


async def _show_students(callback: CallbackQuery, group: Group, page: int, lang: str) -> None:
    async with get_session() as session:
        count = await crud.get_group_member_count(session, group.id)
        if not count:
            await show_screen(callback, t("group.no_students", lang), kb.back_kb(lang, f"{kb.CB_GROUP_PREFIX}{group.id}"))
            return
        pages = math.ceil(count / kb.STUDENTS_PER_PAGE)
        page = max(0, min(page, pages - 1))
        members = await crud.get_group_members(session, group.id, offset=page * kb.STUDENTS_PER_PAGE, limit=kb.STUDENTS_PER_PAGE)
        rows = []
        for user, joined_at in members:
            summary = crud.summarize_works(await crud.get_works_since(session, user.id, joined_at))
            rows.append((user.id, t("group.student_row", lang, name=user.full_name, total=summary["total"], avg=_band(summary["average"]))))
    text = t("group.students_title", lang, name=_esc(group.name), count=count, page=page + 1, pages=pages)
    await show_screen(callback, text, kb.students_kb(group.id, rows, page, pages, lang))


@router.callback_query(F.data.startswith(kb.CB_LIST_PREFIX))
async def list_students(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    group_id, page = _ints(callback.data, kb.CB_LIST_PREFIX)
    group = await _managed(callback, group_id, lang)
    if group is not None:
        await _show_students(callback, group, page, lang)


async def _show_student(callback: CallbackQuery, group: Group, student_id: int, page: int, lang: str) -> None:
    async with get_session() as session:
        membership = await crud.get_membership(session, group.id, student_id)
        if membership is None:
            await _show_students(callback, group, page, lang)
            return
        student = await crud.get_user(session, student_id)
        works = await crud.get_works_since(session, student_id, membership.joined_at)
        comments = await crud.get_comments_for_works(session, works[:RECENT_WORKS])
        can_pay = await _can_pay(session, callback.from_user.id, group)
    s = crud.summarize_works(works)
    text = t(
        "group.student_card", lang,
        name=_esc(student.full_name if student else None), joined=_date(membership.joined_at),
        writing=s["writing"], writing_avg=_band(s["writing_avg"]), speaking=s["speaking"],
        speaking_avg=_band(s["speaking_avg"]), weakest=_weakest(s["weakest"]), last=_date(s["last_active"]),
        streak=crud.activity_streak(works),
    )
    buttons = []
    for w in works[:RECENT_WORKS]:
        mark = " 💬" if (w["kind"], w["id"]) in comments else ""
        buttons.append((w["kind"], w["id"], f"{_date(w['created_at'])[:5]} · {w['label']} · {_band(w['band'])}{mark}"))
    text += "\n\n" + (t("group.works_hint", lang) if works else t("group.no_works_yet", lang))
    await show_screen(callback, text, kb.student_card_kb(group.id, student_id, page, buttons, can_pay, lang))


@router.callback_query(F.data.startswith(kb.CB_STUDENT_PREFIX))
async def student_card(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    group_id, student_id, page = _ints(callback.data, kb.CB_STUDENT_PREFIX)
    group = await _managed(callback, group_id, lang)
    if group is not None:
        await _show_student(callback, group, student_id, page, lang)


# --- One work: report PDF and the teacher's comment --------------------------------------------------


@router.callback_query(F.data.startswith(kb.CB_WORK_PREFIX))
async def work_card(callback: CallbackQuery, lang: str) -> None:
    group_id, student_id, kind, submission_id = callback.data.removeprefix(kb.CB_WORK_PREFIX).split(":")
    async with get_session() as session:
        work = await crud.get_visible_work(session, callback.from_user.id, kind, int(submission_id))
        comments = await crud.get_comments(session, kind, int(submission_id)) if work else []
    if work is None:
        await callback.answer(t("group.report_unavailable", lang), show_alert=True)
        return
    await callback.answer()
    info = crud.work_info(kind, work)
    criteria = ", ".join(f"{k} {_band(v)}" for k, v in info["criteria"].items() if v is not None) or "—"
    lines = [t("group.work_card", lang, label=info["label"], date=_date(info["created_at"]), band=_band(info["band"]), criteria=criteria)]
    for c in comments:
        band = f" · {_band(c.band)}" if c.band is not None else ""
        lines.append(t("group.work_comment", lang, date=_date(c.created_at), band=band, text=_esc(c.text)))
    await show_screen(
        callback, "\n".join(lines),
        kb.work_kb(int(group_id), int(student_id), kind, int(submission_id), bool(work.feedback_pdf_path), lang),
    )


@router.callback_query(F.data.startswith(kb.CB_PDF_PREFIX))
async def send_report(callback: CallbackQuery, lang: str) -> None:
    kind, submission_id = callback.data.removeprefix(kb.CB_PDF_PREFIX).split(":")
    async with get_session() as session:
        path = await crud.get_member_report_pdf(session, callback.from_user.id, kind, int(submission_id))
    content = None
    if path is not None:
        try:
            content = read_file(path)
        except OSError:
            content = None
    if content is None:
        await callback.answer(t("group.report_unavailable", lang), show_alert=True)
        return
    await callback.answer()
    await callback.message.answer_document(BufferedInputFile(content, filename=f"Bandzilla_report_{kind}{submission_id}.pdf"))


@router.callback_query(F.data.startswith(kb.CB_COMMENT_PREFIX))
async def start_comment(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    group_id, student_id, kind, submission_id = callback.data.removeprefix(kb.CB_COMMENT_PREFIX).split(":")
    async with get_session() as session:
        work = await crud.get_visible_work(session, callback.from_user.id, kind, int(submission_id))
    if work is None:
        await callback.answer(t("group.report_unavailable", lang), show_alert=True)
        return
    await callback.answer()
    await state.set_state(GroupStates.awaiting_comment)
    await state.update_data(group_id=int(group_id), student_id=int(student_id), kind=kind, submission_id=int(submission_id))
    await show_screen(
        callback, t("group.ask_comment", lang),
        kb.back_kb(lang, f"{kb.CB_WORK_PREFIX}{group_id}:{student_id}:{kind}:{submission_id}"),
    )


@router.message(GroupStates.awaiting_comment, ~F.text.in_(MENU_BUTTON_TEXTS))
async def receive_comment(message: Message, state: FSMContext, lang: str) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer(t("group.ask_comment", lang))
        return
    await state.update_data(comment_text=text[:3000])
    await message.answer(t("group.ask_comment_band", lang), reply_markup=kb.comment_band_kb(lang))


@router.callback_query(GroupStates.awaiting_comment, F.data.startswith(kb.CB_COMMENT_BAND_PREFIX))
async def save_comment(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    if not data.get("comment_text"):
        await callback.answer()
        return
    raw = callback.data.removeprefix(kb.CB_COMMENT_BAND_PREFIX)
    band = None if raw == "x" else float(raw)
    async with get_session() as session:
        work = await crud.get_visible_work(session, callback.from_user.id, data["kind"], data["submission_id"])
        if work is None:
            await callback.answer(t("group.report_unavailable", lang), show_alert=True)
            return
        await crud.add_comment(
            session, kind=data["kind"], submission_id=data["submission_id"], teacher_id=callback.from_user.id,
            text=data["comment_text"], band=band,
        )
        student = await crud.get_user(session, work.user_id)
        teacher = await crud.get_user(session, callback.from_user.id)
    await callback.answer()
    await state.clear()
    info = crud.work_info(data["kind"], work)
    student_lang = (student.language if student else None) or "uz"
    band_line = t("group.comment_band_line", student_lang, band=_band(band)) if band is not None else ""
    await _notify(
        callback.bot, student, "group.comment_notify",
        teacher=_esc(teacher.full_name if teacher else None), label=info["label"], date=_date(info["created_at"]),
        text=_esc(data["comment_text"]), band_line=band_line,
    )
    await show_screen(
        callback, t("group.comment_saved", lang),
        kb.back_kb(lang, f"{kb.CB_WORK_PREFIX}{data['group_id']}:{data['student_id']}:{data['kind']}:{data['submission_id']}"),
    )


# --- Reports: parents' monthly PDF, group Excel and PDF ------------------------------------------------


@router.callback_query(F.data.startswith(kb.CB_PARENT_PREFIX))
async def parent_report(callback: CallbackQuery, lang: str) -> None:
    group_id, student_id = _ints(callback.data, kb.CB_PARENT_PREFIX)
    group = await _managed(callback, group_id, lang)
    if group is None:
        await callback.answer()
        return
    await callback.answer(t("group.preparing", lang))
    now = datetime.now(timezone.utc)
    month_start, prev_start = now - timedelta(days=30), now - timedelta(days=60)
    async with get_session() as session:
        membership = await crud.get_membership(session, group.id, student_id)
        if membership is None:
            return
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
    done = sum(1 for a in assignments if len(a["done"]) == len(a["parts"]))
    pdf = await asyncio.to_thread(
        build_parent_report_pdf,
        student_name=student.full_name, group_name=group.name, teacher_name=teacher.full_name if teacher else "",
        works_month=works_month, works_prev=works_prev,
        comments=[(w, comments[(w["kind"], w["id"])]) for w in works_month if (w["kind"], w["id"]) in comments],
        assignments_done=done, assignments_total=len(assignments),
        period_start=max(month_start, membership.joined_at), period_end=now, lang=lang,
    )
    await callback.message.answer_document(
        BufferedInputFile(pdf, filename=f"Bandzilla_{student.full_name[:30]}_{_date(now)}.pdf".replace(" ", "_")),
        caption=t("group.parent_report_caption", lang, name=_esc(student.full_name)),
    )


async def _group_data(group: Group):
    async with get_session() as session:
        members = await crud.get_group_works(session, group.id)
        overview = await crud.get_group_overview(session, group.id)
        assignments = []
        for item in await crud.list_group_assignments(session, group.id, limit=50):
            assignments.append({"assignment": item["assignment"], "results": await crud.get_assignment_results(session, item["assignment"])})
        teacher = await crud.get_user(session, group.teacher_id)
    return members, overview, assignments, teacher


@router.callback_query(F.data.startswith(kb.CB_EXCEL_PREFIX))
async def group_excel(callback: CallbackQuery, lang: str) -> None:
    group = await _managed(callback, int(callback.data.removeprefix(kb.CB_EXCEL_PREFIX)), lang)
    if group is None:
        await callback.answer()
        return
    await callback.answer(t("group.preparing", lang))
    members, _, assignments, _ = await _group_data(group)
    content = await asyncio.to_thread(build_group_excel, group.name, members, assignments, lang)
    await callback.message.answer_document(BufferedInputFile(content, filename=f"Bandzilla_{group.name[:40]}.xlsx".replace(" ", "_")))


@router.callback_query(F.data.startswith(kb.CB_GROUP_PDF_PREFIX))
async def group_pdf(callback: CallbackQuery, lang: str) -> None:
    group = await _managed(callback, int(callback.data.removeprefix(kb.CB_GROUP_PDF_PREFIX)), lang)
    if group is None:
        await callback.answer()
        return
    await callback.answer(t("group.preparing", lang))
    members, overview, assignments, teacher = await _group_data(group)
    content = await asyncio.to_thread(
        build_group_pdf, group.name, teacher.full_name if teacher else "", overview, members, assignments, lang
    )
    await callback.message.answer_document(BufferedInputFile(content, filename=f"Bandzilla_{group.name[:40]}.pdf".replace(" ", "_")))


# --- Leaderboard ----------------------------------------------------------------------------------------


@router.callback_query(F.data.startswith(kb.CB_TOP_PREFIX))
async def leaderboard(callback: CallbackQuery, lang: str) -> None:
    group_id = int(callback.data.removeprefix(kb.CB_TOP_PREFIX))
    async with get_session() as session:
        group = await crud.get_group(session, group_id)
        manager = await crud.can_manage_group(session, callback.from_user.id, group)
        member = group is not None and await crud.get_membership(session, group_id, callback.from_user.id) is not None
        board = await crud.get_group_leaderboard(session, group_id) if group else None
    if group is None or not (manager or (member and group.leaderboard_on)):
        await callback.answer(t("group.board_hidden", lang), show_alert=True)
        return
    await callback.answer()
    me = callback.from_user.id

    def rows(items, fmt, limit):
        lines = []
        for place, (user, value) in enumerate(items[:limit], start=1):
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(place, f"{place}.")
            you = " ← " + t("group.board_you", lang) if user.id == me else ""
            lines.append(f"{medal} {_esc(user.full_name)} — {fmt(value)}{you}")
        return "\n".join(lines) or "—"

    text = t(
        "group.board", lang,
        name=_esc(group.name),
        week=rows(board["week"], lambda n: t("group.board_checks", lang, n=n), 10),
        streaks=rows(board["streaks"], lambda n: t("group.board_days", lang, n=n), 5),
        improved=rows(board["improved"], lambda d: f"+{d:.1f}", 5),
    )
    back = f"{kb.CB_GROUP_PREFIX}{group_id}" if manager else kb.CB_MEMBER_HOME
    await show_screen(callback, text, kb.back_kb(lang, back))


# --- Coins ------------------------------------------------------------------------------------------------


async def _payer_or_alert(callback: CallbackQuery, group: Group, lang: str) -> User | None:
    async with get_session() as session:
        payer_id = await crud.get_group_payer_id(session, group)
        payer = await crud.get_user(session, payer_id)
    if payer_id != callback.from_user.id:
        await callback.answer(t("group.center_owner_pays", lang), show_alert=True)
        return None
    return payer


@router.callback_query(F.data.startswith(kb.CB_GIFT_PREFIX))
async def choose_gift_amount(callback: CallbackQuery, lang: str) -> None:
    group_id, student_id, page = _ints(callback.data, kb.CB_GIFT_PREFIX)
    group = await _managed(callback, group_id, lang)
    if group is None:
        await callback.answer()
        return
    payer = await _payer_or_alert(callback, group, lang)
    if payer is None:
        return
    await callback.answer()
    async with get_session() as session:
        student = await crud.get_user(session, student_id)
    if payer.coin_balance <= 0:
        await show_screen(callback, t("group.gift_no_balance", lang), buy_coins_kb(lang, True))
        return
    options = [a for a in kb.GIFT_AMOUNT_OPTIONS if a <= payer.coin_balance]
    text = t("group.gift_one", lang, name=_esc(student.full_name if student else None), balance=payer.coin_balance)
    await show_screen(
        callback, text,
        kb.amounts_kb(options, f"{kb.CB_GIFT_DO_PREFIX}{group_id}:{student_id}:", f"{kb.CB_STUDENT_PREFIX}{group_id}:{student_id}:{page}", lang),
    )


@router.callback_query(F.data.startswith(kb.CB_GIFT_DO_PREFIX))
async def do_gift(callback: CallbackQuery, lang: str) -> None:
    group_id, student_id, amount = _ints(callback.data, kb.CB_GIFT_DO_PREFIX)
    group = await _managed(callback, group_id, lang)
    if group is None or await _payer_or_alert(callback, group, lang) is None:
        return
    async with get_session() as session:
        credited = await crud.gift_coins(session, group=group, student_ids=[student_id], amount=amount)
    if not credited:
        await callback.answer(t("group.gift_failed", lang), show_alert=True)
        return
    await callback.answer()
    student = credited[0]
    await show_screen(
        callback, t("group.gift_done_one", lang, name=_esc(student.full_name), amount=amount),
        kb.back_kb(lang, f"{kb.CB_STUDENT_PREFIX}{group_id}:{student_id}:0"),
    )
    await _notify(callback.bot, student, "group.gift_notify_student", amount=amount)
    await crm_events.coins_gifted(group.name, callback.from_user, credited, amount)


@router.callback_query(F.data.startswith(kb.CB_GIFT_ALL_DO_PREFIX))
async def do_gift_all(callback: CallbackQuery, lang: str) -> None:
    group_id, amount = _ints(callback.data, kb.CB_GIFT_ALL_DO_PREFIX)
    group = await _managed(callback, group_id, lang)
    if group is None or await _payer_or_alert(callback, group, lang) is None:
        return
    async with get_session() as session:
        members = await crud.get_group_members(session, group.id)
        credited = await crud.gift_coins(session, group=group, student_ids=[u.id for u, _ in members], amount=amount)
    if not credited:
        await callback.answer(t("group.gift_failed", lang), show_alert=True)
        return
    await callback.answer()
    text = t("group.gift_done_all", lang, count=len(credited), amount=amount, total=amount * len(credited))
    await show_screen(callback, text, kb.back_kb(lang, f"{kb.CB_GROUP_PREFIX}{group.id}"))
    for student in credited:
        await _notify(callback.bot, student, "group.gift_notify_student", amount=amount)
        await asyncio.sleep(SEND_PAUSE)
    await crm_events.coins_gifted(group.name, callback.from_user, credited, amount)


@router.callback_query(F.data.startswith(kb.CB_GIFT_ALL_PREFIX))
async def choose_gift_all(callback: CallbackQuery, lang: str) -> None:
    group = await _managed(callback, int(callback.data.removeprefix(kb.CB_GIFT_ALL_PREFIX)), lang)
    if group is None:
        await callback.answer()
        return
    payer = await _payer_or_alert(callback, group, lang)
    if payer is None:
        return
    await callback.answer()
    async with get_session() as session:
        count = await crud.get_group_member_count(session, group.id)
    back = f"{kb.CB_GROUP_PREFIX}{group.id}"
    if not count:
        await show_screen(callback, t("group.no_students", lang), kb.back_kb(lang, back))
        return
    options = [a for a in kb.GIFT_AMOUNT_OPTIONS if a * count <= payer.coin_balance]
    if not options:
        await show_screen(callback, t("group.gift_all_not_enough", lang, count=count, balance=payer.coin_balance), buy_coins_kb(lang, True))
        return
    text = t("group.gift_all", lang, count=count, balance=payer.coin_balance)
    await show_screen(callback, text, kb.amounts_kb(options, f"{kb.CB_GIFT_ALL_DO_PREFIX}{group.id}:", back, lang))


# --- Settings: who pays for checks, daily limit, leaderboard ----------------------------------------------


async def _settings_screen(callback: CallbackQuery, group: Group, lang: str) -> None:
    async with get_session() as session:
        group = await crud.get_group(session, group.id)
        can_pay = await _can_pay(session, callback.from_user.id, group)
    limit = t("group.limit_none", lang) if group.daily_limit is None else str(group.daily_limit)
    text = t(
        "group.settings", lang,
        name=_esc(group.name),
        sponsor=t("group.on" if group.sponsor_checks else "group.off", lang),
        limit=limit,
        board=t("group.on" if group.leaderboard_on else "group.off", lang),
    )
    if not can_pay:
        text += "\n\n" + t("group.center_owner_pays", lang)
    await show_screen(callback, text, kb.settings_kb(group.id, group.sponsor_checks, group.daily_limit, group.leaderboard_on, can_pay, lang))


@router.callback_query(F.data.startswith(kb.CB_SETTINGS_PREFIX))
async def group_settings(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    group = await _managed(callback, int(callback.data.removeprefix(kb.CB_SETTINGS_PREFIX)), lang)
    if group is not None:
        await _settings_screen(callback, group, lang)


@router.callback_query(F.data.startswith(kb.CB_SPONSOR_PREFIX))
async def toggle_sponsor(callback: CallbackQuery, lang: str) -> None:
    group = await _managed(callback, int(callback.data.removeprefix(kb.CB_SPONSOR_PREFIX)), lang)
    if group is None or await _payer_or_alert(callback, group, lang) is None:
        return
    await callback.answer()
    async with get_session() as session:
        await crud.update_group(session, group.id, sponsor_checks=not group.sponsor_checks)
    await _settings_screen(callback, group, lang)


@router.callback_query(F.data.startswith(kb.CB_LIMIT_PREFIX))
async def set_limit(callback: CallbackQuery, lang: str) -> None:
    group_id, limit = _ints(callback.data, kb.CB_LIMIT_PREFIX)
    group = await _managed(callback, group_id, lang)
    if group is None or await _payer_or_alert(callback, group, lang) is None:
        return
    await callback.answer()
    async with get_session() as session:
        await crud.update_group(session, group.id, daily_limit=limit or None)
    await _settings_screen(callback, group, lang)


@router.callback_query(F.data.startswith(kb.CB_BOARD_TOGGLE_PREFIX))
async def toggle_board(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    group = await _managed(callback, int(callback.data.removeprefix(kb.CB_BOARD_TOGGLE_PREFIX)), lang)
    if group is None:
        return
    async with get_session() as session:
        await crud.update_group(session, group.id, leaderboard_on=not group.leaderboard_on)
    await _settings_screen(callback, group, lang)


# --- Announcements and the QR code ---------------------------------------------------------------------------


@router.callback_query(F.data == kb.CB_ANNOUNCE_SEND)
async def send_announcement(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    if "source_message" not in data or "group_id" not in data:
        await callback.answer()
        return
    group = await _managed(callback, data["group_id"], lang)
    if group is None:
        await callback.answer()
        return
    await callback.answer()
    await state.clear()
    async with get_session() as session:
        members = await crud.get_group_members(session, group.id)
        teacher = await crud.get_user(session, callback.from_user.id)
    await show_screen(callback, t("group.announce_sending", lang, count=len(members)), None)
    sent = 0
    for user, _ in members:
        header = t("group.announce_header", user.language or "uz", group=_esc(group.name), teacher=_esc(teacher.full_name if teacher else None))
        try:
            await callback.bot.send_message(user.id, header)
            await callback.bot.copy_message(user.id, data["source_chat"], data["source_message"])
            sent += 1
        except Exception:
            logger.info("Announcement to %s failed", user.id)
        await asyncio.sleep(SEND_PAUSE)
    await callback.message.answer(
        t("group.announce_done", lang, sent=sent, failed=len(members) - sent),
        reply_markup=kb.back_kb(lang, f"{kb.CB_GROUP_PREFIX}{group.id}"),
    )


@router.callback_query(F.data.startswith(kb.CB_ANNOUNCE_PREFIX))
async def start_announcement(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    group = await _managed(callback, int(callback.data.removeprefix(kb.CB_ANNOUNCE_PREFIX)), lang)
    if group is None:
        return
    await state.set_state(GroupStates.awaiting_announcement)
    await state.update_data(group_id=group.id)
    await show_screen(callback, t("group.ask_announcement", lang, name=_esc(group.name)), kb.back_kb(lang, f"{kb.CB_GROUP_PREFIX}{group.id}"))


@router.message(GroupStates.awaiting_announcement, ~F.text.in_(MENU_BUTTON_TEXTS))
async def receive_announcement(message: Message, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    async with get_session() as session:
        count = await crud.get_group_member_count(session, data["group_id"])
    await state.update_data(source_chat=message.chat.id, source_message=message.message_id)
    await message.answer(
        t("group.announce_confirm", lang, count=count),
        reply_markup=kb.confirm_kb(t("group.btn_send", lang), kb.CB_ANNOUNCE_SEND, f"{kb.CB_GROUP_PREFIX}{data['group_id']}", lang),
    )


@router.callback_query(F.data.startswith(kb.CB_QR_PREFIX))
async def group_qr(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    group = await _managed(callback, int(callback.data.removeprefix(kb.CB_QR_PREFIX)), lang)
    if group is None:
        return
    link = invite_link(group.id)
    buffer = io.BytesIO()
    segno.make(link, error="m").save(buffer, kind="png", scale=12, border=3)
    await callback.message.answer_photo(
        BufferedInputFile(buffer.getvalue(), filename="qr.png"),
        caption=t("group.qr_caption", lang, name=_esc(group.name), link=link),
    )


# --- Removing a student / deleting a group / leaving ---------------------------------------------------------


@router.callback_query(F.data.startswith(kb.CB_KICK_DO_PREFIX))
async def do_kick(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    group_id, student_id = _ints(callback.data, kb.CB_KICK_DO_PREFIX)
    group = await _managed(callback, group_id, lang)
    if group is None:
        return
    async with get_session() as session:
        removed = await crud.leave_group(session, group.id, student_id)
        student = await crud.get_user(session, student_id)
    if removed:
        await _notify(callback.bot, student, "group.kicked_notify", group=_esc(group.name))
        await crm_events.group_left(group.name, student_id, by=callback.from_user)
    await show_screen(
        callback, t("group.kick_done", lang, name=_esc(student.full_name if student else None)),
        kb.back_kb(lang, f"{kb.CB_LIST_PREFIX}{group.id}:0"),
    )


@router.callback_query(F.data.startswith(kb.CB_KICK_PREFIX))
async def confirm_kick(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    group_id, student_id, page = _ints(callback.data, kb.CB_KICK_PREFIX)
    group = await _managed(callback, group_id, lang)
    if group is None:
        return
    async with get_session() as session:
        student = await crud.get_user(session, student_id)
    await show_screen(
        callback,
        t("group.kick_confirm", lang, name=_esc(student.full_name if student else None)),
        kb.confirm_kb(t("group.btn_kick_yes", lang), f"{kb.CB_KICK_DO_PREFIX}{group_id}:{student_id}", f"{kb.CB_STUDENT_PREFIX}{group_id}:{student_id}:{page}", lang),
    )


@router.callback_query(F.data.startswith(kb.CB_DELETE_DO_PREFIX))
async def do_delete(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    group = await _managed(callback, int(callback.data.removeprefix(kb.CB_DELETE_DO_PREFIX)), lang)
    if group is None:
        return
    async with get_session() as session:
        await crud.delete_group(session, group.id)
    await crm_events.group_deleted(group.name, callback.from_user)
    text, markup = await _home(callback.from_user.id, lang)
    await show_screen(callback, t("group.deleted", lang) + "\n\n" + text, markup)


@router.callback_query(F.data.startswith(kb.CB_DELETE_PREFIX))
async def confirm_delete(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    group = await _managed(callback, int(callback.data.removeprefix(kb.CB_DELETE_PREFIX)), lang)
    if group is None:
        return
    await show_screen(
        callback,
        t("group.delete_confirm", lang, name=_esc(group.name)),
        kb.confirm_kb(t("group.btn_delete_yes", lang), f"{kb.CB_DELETE_DO_PREFIX}{group.id}", f"{kb.CB_GROUP_PREFIX}{group.id}", lang),
    )


@router.callback_query(F.data.startswith(kb.CB_LEAVE_DO_PREFIX))
async def do_leave(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    group_id = int(callback.data.removeprefix(kb.CB_LEAVE_DO_PREFIX))
    async with get_session() as session:
        group = await crud.get_group(session, group_id)
        left = await crud.leave_group(session, group_id, callback.from_user.id)
        teacher = await crud.get_user(session, group.teacher_id) if group else None
    if left and group is not None:
        await _notify(callback.bot, teacher, "group.left_notify_teacher", name=_esc(callback.from_user.full_name))
        await crm_events.group_left(group.name, callback.from_user.id)
    text, markup = await _home(callback.from_user.id, lang)
    await show_screen(callback, t("group.left", lang) + "\n\n" + text, markup)


@router.callback_query(F.data.startswith(kb.CB_LEAVE_PREFIX))
async def confirm_leave(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    group_id = int(callback.data.removeprefix(kb.CB_LEAVE_PREFIX))
    async with get_session() as session:
        group = await crud.get_group(session, group_id)
    if group is None:
        return
    await show_screen(
        callback,
        t("group.leave_confirm", lang, name=_esc(group.name)),
        kb.confirm_kb(t("group.btn_leave_yes", lang), f"{kb.CB_LEAVE_DO_PREFIX}{group_id}", kb.CB_MEMBER_HOME, lang),
    )


# --- Learning center and the web cabinet ---------------------------------------------------------------------


@router.callback_query(F.data == kb.CB_CENTER)
async def center_screen(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    async with get_session() as session:
        center = await crud.get_user_center(session, callback.from_user.id)
        if center is None:
            text, markup = await _home(callback.from_user.id, lang)
            await show_screen(callback, text, markup)
            return
        owner = await crud.get_user(session, center.owner_id)
        teachers = await crud.list_center_teachers(session, center.id)
        groups = [g for g in await crud.get_managed_groups(session, center.owner_id) if g.center_id == center.id]
        students = sum([await crud.get_group_member_count(session, g.id) for g in groups])
    if center.owner_id != callback.from_user.id:
        await show_screen(
            callback, t("group.center_teacher_view", lang, name=_esc(center.name), owner=_esc(owner.full_name if owner else None)),
            kb.back_kb(lang, kb.CB_HOME),
        )
        return
    teacher_lines = "\n".join(
        t("group.center_teacher_row", lang, name=_esc(user.full_name), groups=count) for user, count in teachers
    )
    text = t(
        "group.center_home", lang,
        name=_esc(center.name), teachers=len(teachers), groups=len(groups), students=students,
        balance=owner.coin_balance if owner else 0, link=center_invite_link(center.id), teacher_lines=teacher_lines,
    )
    others = [(user.id, user.full_name) for user, _ in teachers if user.id != center.owner_id]
    await show_screen(callback, text, kb.center_kb(others, lang))


@router.callback_query(F.data.startswith(kb.CB_CENTER_KICK_DO_PREFIX))
async def do_center_kick(callback: CallbackQuery, lang: str) -> None:
    teacher_id = int(callback.data.removeprefix(kb.CB_CENTER_KICK_DO_PREFIX))
    async with get_session() as session:
        center = await crud.get_owned_center(session, callback.from_user.id)
        removed = center is not None and await crud.remove_center_teacher(session, center.id, teacher_id)
        teacher = await crud.get_user(session, teacher_id)
    if removed:
        await _notify(callback.bot, teacher, "group.center_removed_notify", name=_esc(center.name))
        await crm_events.center_teacher_removed(center.name, teacher_id, callback.from_user)
    await center_screen(callback, lang)


@router.callback_query(F.data.startswith(kb.CB_CENTER_KICK_PREFIX))
async def confirm_center_kick(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    teacher_id = int(callback.data.removeprefix(kb.CB_CENTER_KICK_PREFIX))
    async with get_session() as session:
        teacher = await crud.get_user(session, teacher_id)
    await show_screen(
        callback, t("group.center_kick_confirm", lang, name=_esc(teacher.full_name if teacher else None)),
        kb.confirm_kb(t("group.btn_kick_yes", lang), f"{kb.CB_CENTER_KICK_DO_PREFIX}{teacher_id}", kb.CB_CENTER, lang),
    )


@router.callback_query(F.data == kb.CB_CABINET)
async def open_cabinet(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    if not settings.cabinet_base_url:
        return
    await show_screen(callback, t("group.cabinet_text", lang), kb.cabinet_kb(cabinet_login_url(callback.from_user.id), lang))
