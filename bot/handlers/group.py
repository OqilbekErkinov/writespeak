"""Guruh / Группа (B2B): a teacher opens one group, students join through
its signed invite link (bot/utils/group_invite.py, handled in
bot/handlers/start.py), and the teacher follows each student's work and
gives them credits from their OWN balance - this reuses the existing
credit/payment system rather than adding a second one, so a language
center's natural path is: buy a bulk credit bundle for themselves
(bot/handlers/payments.py), then hand it out to their students here.

Who can open a group: admins, and users an admin has made a teacher - a
student taps "Guruh yaratish", sends a request, and the admins approve or
reject it in Telegram (same pattern as payment receipts).

What the teacher sees about a student covers only work submitted after the
student joined (db/crud.py's group section): an overview of the group, a
paged student list, and per student their counts, average bands, weakest
criterion, recent reports (as the student's own PDFs), plus gifting credits
and removing them. Students see their groups and can leave one.
"""
from __future__ import annotations

import html
import logging
import math
from datetime import datetime, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from bot.config import settings
from bot.i18n import t
from bot.keyboards.group_kb import (
    CB_CREATE,
    CB_DELETE,
    CB_DELETE_DO,
    CB_GIFT_ALL,
    CB_GIFT_ALL_DO_PREFIX,
    CB_GIFT_DO_PREFIX,
    CB_GIFT_PREFIX,
    CB_HOME,
    CB_KICK_DO_PREFIX,
    CB_KICK_PREFIX,
    CB_LEAVE_DO_PREFIX,
    CB_LEAVE_PREFIX,
    CB_LIST_PREFIX,
    CB_PDF_PREFIX,
    CB_REQUEST,
    CB_STATS,
    CB_STUDENT_PREFIX,
    CB_TEACHER_NO_PREFIX,
    CB_TEACHER_OK_PREFIX,
    GIFT_AMOUNT_OPTIONS,
    STUDENTS_PER_PAGE,
    amounts_kb,
    back_home_kb,
    confirm_kb,
    member_home_kb,
    no_group_kb,
    request_teacher_kb,
    student_card_kb,
    students_kb,
    teacher_home_kb,
    teacher_review_kb,
)
from bot.keyboards.main_menu_kb import BTN_GROUP_RU, BTN_GROUP_UZ, MENU_BUTTON_TEXTS
from bot.keyboards.payment_kb import buy_credits_kb
from bot.states.group_states import GroupStates
from bot.utils.group_invite import invite_link
from bot.utils.messages import show_screen
from db import crud
from db.crud import CRITERIA_LABELS
from db.database import get_session
from db.models import User, UserRole
from services.storage.file_storage import read_file

logger = logging.getLogger(__name__)
router = Router(name="group")

RECENT_REPORTS = 5
_TASHKENT = timedelta(hours=5)


def _esc(text: str | None) -> str:
    return html.escape(text or "?", quote=False)


def _band(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}"


def _date(value: datetime | None) -> str:
    return (value + _TASHKENT).strftime("%d.%m.%Y") if value else "—"


def _weakest(weakest: tuple[str, float] | None) -> str:
    return f"{CRITERIA_LABELS.get(weakest[0], weakest[0])} ({weakest[1]:.1f})" if weakest else "—"


def _can_open_group(user: User | None, user_id: int) -> bool:
    return user_id in settings.admin_ids or (
        user is not None and user.role in (UserRole.teacher, UserRole.admin)
    )


async def _notify(bot, user: User | None, key: str, **kwargs) -> None:
    """Messages another user in their own language; a blocked bot is not an error here."""
    if user is None:
        return
    try:
        await bot.send_message(user.id, t(key, user.language or "uz", **kwargs))
    except Exception:
        logger.info("Could not notify user %s (%s)", user.id, key)


# --- Home ------------------------------------------------------------------------------------


async def _home(user_id: int, lang: str):
    """(text, keyboard) of the Guruh screen for this user."""
    async with get_session() as session:
        user = await crud.get_user(session, user_id)
        owned = await crud.get_owned_group(session, user_id)
        if owned is not None:
            count = await crud.get_group_member_count(session, owned.id)
            text = t(
                "group.teacher_home",
                lang,
                name=_esc(owned.name),
                count=count,
                balance=user.credit_balance if user else 0,
                link=invite_link(owned.id),
            )
            return text, teacher_home_kb(lang)
        memberships = await crud.get_member_groups(session, user_id)

    can_create = _can_open_group(user, user_id)
    if memberships:
        lines = [t("group.member_home_title", lang)]
        lines += [
            t("group.member_home_row", lang, name=_esc(g.name), teacher=_esc(teacher.full_name if teacher else None))
            for g, teacher in memberships
        ]
        lines.append(t("group.member_home_note", lang))
        return "\n".join(lines), member_home_kb([(g.id, g.name) for g, _ in memberships], can_create, lang)
    return t("group.no_group", lang), no_group_kb(lang)


@router.message(F.text.in_({BTN_GROUP_UZ, BTN_GROUP_RU}))
async def open_group(message: Message, state: FSMContext, lang: str) -> None:
    await state.clear()
    text, kb = await _home(message.from_user.id, lang)
    await message.answer(text, reply_markup=kb, disable_web_page_preview=True)


@router.callback_query(F.data == CB_HOME)
async def back_home(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    await state.clear()
    text, kb = await _home(callback.from_user.id, lang)
    await show_screen(callback, text, kb)


# --- Opening a group / becoming a teacher ---------------------------------------------------------


@router.callback_query(F.data == CB_CREATE)
async def start_create_group(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    async with get_session() as session:
        user = await crud.get_user(session, callback.from_user.id)
        owned = await crud.get_owned_group(session, callback.from_user.id)
    if owned is not None:
        text, kb = await _home(callback.from_user.id, lang)
        await show_screen(callback, text, kb)
        return
    if not _can_open_group(user, callback.from_user.id):
        await show_screen(callback, t("group.teachers_only", lang), request_teacher_kb(lang))
        return
    await state.set_state(GroupStates.awaiting_name)
    await show_screen(callback, t("group.ask_name", lang), back_home_kb(lang))


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
        # One group per teacher (groups.teacher_id is unique) - a friendly
        # no-op instead of a DB error if they somehow get here twice.
        if await crud.get_owned_group(session, message.from_user.id) is None:
            await crud.create_group(session, message.from_user.id, name)
    text, kb = await _home(message.from_user.id, lang)
    await message.answer(t("group.created", lang) + "\n\n" + text, reply_markup=kb, disable_web_page_preview=True)


@router.callback_query(F.data == CB_REQUEST)
async def request_teacher(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    tg = callback.from_user
    username = f" (@{tg.username})" if tg.username else ""
    text = (
        "🏫 <b>O'qituvchi bo'lish so'rovi</b>\n\n"
        f"Foydalanuvchi: {_esc(tg.full_name)}{username}\n"
        f"Telegram ID: <code>{tg.id}</code>\n\n"
        "Tasdiqlansa, u guruh ochib, talabalarini kuzata oladi va ularga o'z kreditlarini ulasha oladi."
    )
    for admin_id in settings.admin_ids:
        try:
            await callback.bot.send_message(admin_id, text, reply_markup=teacher_review_kb(tg.id))
        except Exception:
            pass  # an admin may not have opened the bot; others still get it
    await show_screen(callback, t("group.request_sent", lang), back_home_kb(lang))


async def _decide_teacher(callback: CallbackQuery, approve: bool) -> None:
    if callback.from_user.id not in settings.admin_ids:
        await callback.answer("Sizda ruxsat yo'q.", show_alert=True)
        return
    prefix = CB_TEACHER_OK_PREFIX if approve else CB_TEACHER_NO_PREFIX
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


@router.callback_query(F.data.startswith(CB_TEACHER_OK_PREFIX))
async def approve_teacher(callback: CallbackQuery) -> None:
    await _decide_teacher(callback, approve=True)


@router.callback_query(F.data.startswith(CB_TEACHER_NO_PREFIX))
async def reject_teacher(callback: CallbackQuery) -> None:
    await _decide_teacher(callback, approve=False)


# --- Teacher: overview, students, reports ----------------------------------------------------------


async def _owned_group_or_home(callback: CallbackQuery, lang: str):
    async with get_session() as session:
        group = await crud.get_owned_group(session, callback.from_user.id)
    if group is None:
        text, kb = await _home(callback.from_user.id, lang)
        await show_screen(callback, text, kb)
    return group


@router.callback_query(F.data == CB_STATS)
async def show_stats(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    group = await _owned_group_or_home(callback, lang)
    if group is None:
        return
    async with get_session() as session:
        overview = await crud.get_group_overview(session, group.id)
    if not overview["members"]:
        await show_screen(callback, t("group.no_students", lang), back_home_kb(lang))
        return
    s = overview["summary"]
    most_active = ", ".join(f"{_esc(name)} ({total})" for name, total in overview["most_active"]) or "—"
    text = t(
        "group.stats",
        lang,
        name=_esc(group.name),
        members=overview["members"],
        active=overview["active_week"],
        total=s["total"],
        writing=s["writing"],
        speaking=s["speaking"],
        writing_avg=_band(s["writing_avg"]),
        speaking_avg=_band(s["speaking_avg"]),
        weakest=_weakest(s["weakest"]),
        most_active=most_active,
        inactive=overview["inactive"],
    )
    await show_screen(callback, text, back_home_kb(lang))


async def _show_students(callback: CallbackQuery, page: int, lang: str) -> None:
    group = await _owned_group_or_home(callback, lang)
    if group is None:
        return
    async with get_session() as session:
        count = await crud.get_group_member_count(session, group.id)
        if not count:
            await show_screen(callback, t("group.no_students", lang), back_home_kb(lang))
            return
        pages = math.ceil(count / STUDENTS_PER_PAGE)
        page = max(0, min(page, pages - 1))
        members = await crud.get_group_members(
            session, group.id, offset=page * STUDENTS_PER_PAGE, limit=STUDENTS_PER_PAGE
        )
        rows = []
        for user, joined_at in members:
            summary = crud.summarize_works(await crud.get_works_since(session, user.id, joined_at))
            rows.append(
                (user.id, t("group.student_row", lang, name=user.full_name, total=summary["total"], avg=_band(summary["average"])))
            )
    text = t("group.students_title", lang, count=count, page=page + 1, pages=pages)
    await show_screen(callback, text, students_kb(rows, page, pages, lang))


@router.callback_query(F.data.startswith(CB_LIST_PREFIX))
async def list_students(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    await _show_students(callback, int(callback.data.removeprefix(CB_LIST_PREFIX)), lang)


async def _show_student(callback: CallbackQuery, student_id: int, page: int, lang: str) -> None:
    group = await _owned_group_or_home(callback, lang)
    if group is None:
        return
    async with get_session() as session:
        membership = await crud.get_membership(session, group.id, student_id)
        if membership is None:
            await _show_students(callback, page, lang)
            return
        student = await crud.get_user(session, student_id)
        works = await crud.get_works_since(session, student_id, membership.joined_at)
    s = crud.summarize_works(works)
    text = t(
        "group.student_card",
        lang,
        name=_esc(student.full_name if student else None),
        joined=_date(membership.joined_at),
        writing=s["writing"],
        writing_avg=_band(s["writing_avg"]),
        speaking=s["speaking"],
        speaking_avg=_band(s["speaking_avg"]),
        weakest=_weakest(s["weakest"]),
        last=_date(s["last_active"]),
    )
    reports = [
        (w["kind"], w["id"], f"📄 {_date(w['created_at'])[:5]} · {w['label']} · {_band(w['band'])}")
        for w in works
        if w["has_pdf"]
    ][:RECENT_REPORTS]
    if reports:
        text += "\n\n" + t("group.reports_hint", lang)
    elif not works:
        text += "\n\n" + t("group.no_works_yet", lang)
    await show_screen(callback, text, student_card_kb(student_id, page, reports, lang))


@router.callback_query(F.data.startswith(CB_STUDENT_PREFIX))
async def student_card(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    student_id, page = callback.data.removeprefix(CB_STUDENT_PREFIX).split(":")
    await _show_student(callback, int(student_id), int(page), lang)


@router.callback_query(F.data.startswith(CB_PDF_PREFIX))
async def send_report(callback: CallbackQuery, lang: str) -> None:
    kind, submission_id = callback.data.removeprefix(CB_PDF_PREFIX).split(":")
    async with get_session() as session:
        path = await crud.get_member_report_pdf(session, callback.from_user.id, kind, int(submission_id))
    if path is None:
        await callback.answer(t("group.report_unavailable", lang), show_alert=True)
        return
    try:
        content = read_file(path)
    except OSError:
        await callback.answer(t("group.report_unavailable", lang), show_alert=True)
        return
    await callback.answer()
    await callback.message.answer_document(BufferedInputFile(content, filename=f"WriteSpeak_report_{kind}{submission_id}.pdf"))


# --- Teacher: credits ----------------------------------------------------------------------------------


@router.callback_query(F.data.startswith(CB_GIFT_PREFIX))
async def choose_gift_amount(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    student_id, page = (int(x) for x in callback.data.removeprefix(CB_GIFT_PREFIX).split(":"))
    async with get_session() as session:
        teacher = await crud.get_user(session, callback.from_user.id)
        student = await crud.get_user(session, student_id)
    balance = teacher.credit_balance if teacher else 0
    if balance <= 0:
        await show_screen(callback, t("group.gift_no_balance", lang), buy_credits_kb(lang))
        return
    options = [a for a in GIFT_AMOUNT_OPTIONS if a <= balance]
    text = t("group.gift_one", lang, name=_esc(student.full_name if student else None), balance=balance)
    await show_screen(
        callback, text, amounts_kb(options, f"{CB_GIFT_DO_PREFIX}{student_id}:", f"{CB_STUDENT_PREFIX}{student_id}:{page}", lang)
    )


@router.callback_query(F.data.startswith(CB_GIFT_DO_PREFIX))
async def do_gift(callback: CallbackQuery, lang: str) -> None:
    student_id, amount = (int(x) for x in callback.data.removeprefix(CB_GIFT_DO_PREFIX).split(":"))
    async with get_session() as session:
        credited = await crud.gift_credits(
            session, teacher_id=callback.from_user.id, student_ids=[student_id], amount=amount
        )
    if not credited:
        await callback.answer(t("group.gift_failed", lang), show_alert=True)
        return
    await callback.answer()
    student = credited[0]
    await show_screen(callback, t("group.gift_done_one", lang, name=_esc(student.full_name), amount=amount), back_home_kb(lang))
    await _notify(callback.bot, student, "group.gift_notify_student", amount=amount)


@router.callback_query(F.data == CB_GIFT_ALL)
async def choose_gift_all(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    group = await _owned_group_or_home(callback, lang)
    if group is None:
        return
    async with get_session() as session:
        count = await crud.get_group_member_count(session, group.id)
        teacher = await crud.get_user(session, callback.from_user.id)
    balance = teacher.credit_balance if teacher else 0
    if not count:
        await show_screen(callback, t("group.no_students", lang), back_home_kb(lang))
        return
    options = [a for a in GIFT_AMOUNT_OPTIONS if a * count <= balance]
    if not options:
        await show_screen(callback, t("group.gift_all_not_enough", lang, count=count, balance=balance), buy_credits_kb(lang))
        return
    text = t("group.gift_all", lang, count=count, balance=balance)
    await show_screen(callback, text, amounts_kb(options, CB_GIFT_ALL_DO_PREFIX, CB_HOME, lang))


@router.callback_query(F.data.startswith(CB_GIFT_ALL_DO_PREFIX))
async def do_gift_all(callback: CallbackQuery, lang: str) -> None:
    amount = int(callback.data.removeprefix(CB_GIFT_ALL_DO_PREFIX))
    async with get_session() as session:
        group = await crud.get_owned_group(session, callback.from_user.id)
        members = await crud.get_group_members(session, group.id) if group else []
        credited = await crud.gift_credits(
            session, teacher_id=callback.from_user.id, student_ids=[u.id for u, _ in members], amount=amount
        )
    if not credited:
        await callback.answer(t("group.gift_failed", lang), show_alert=True)
        return
    await callback.answer()
    text = t("group.gift_done_all", lang, count=len(credited), amount=amount, total=amount * len(credited))
    await show_screen(callback, text, back_home_kb(lang))
    for student in credited:
        await _notify(callback.bot, student, "group.gift_notify_student", amount=amount)


# --- Teacher: removing a student / deleting the group --------------------------------------------------


@router.callback_query(F.data.startswith(CB_KICK_PREFIX))
async def confirm_kick(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    student_id, page = (int(x) for x in callback.data.removeprefix(CB_KICK_PREFIX).split(":"))
    async with get_session() as session:
        student = await crud.get_user(session, student_id)
    await show_screen(
        callback,
        t("group.kick_confirm", lang, name=_esc(student.full_name if student else None)),
        confirm_kb(t("group.btn_kick_yes", lang), f"{CB_KICK_DO_PREFIX}{student_id}", f"{CB_STUDENT_PREFIX}{student_id}:{page}", lang),
    )


@router.callback_query(F.data.startswith(CB_KICK_DO_PREFIX))
async def do_kick(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    student_id = int(callback.data.removeprefix(CB_KICK_DO_PREFIX))
    async with get_session() as session:
        group = await crud.get_owned_group(session, callback.from_user.id)
        removed = bool(group) and await crud.leave_group(session, group.id, student_id)
        student = await crud.get_user(session, student_id)
    if removed:
        await _notify(callback.bot, student, "group.kicked_notify", group=_esc(group.name))
    await show_screen(callback, t("group.kick_done", lang, name=_esc(student.full_name if student else None)), back_home_kb(lang))


@router.callback_query(F.data == CB_DELETE)
async def confirm_delete(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    group = await _owned_group_or_home(callback, lang)
    if group is None:
        return
    await show_screen(
        callback,
        t("group.delete_confirm", lang, name=_esc(group.name)),
        confirm_kb(t("group.btn_delete_yes", lang), CB_DELETE_DO, CB_HOME, lang),
    )


@router.callback_query(F.data == CB_DELETE_DO)
async def do_delete(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    async with get_session() as session:
        group = await crud.get_owned_group(session, callback.from_user.id)
        if group is not None:
            await crud.delete_group(session, group.id)
    text, kb = await _home(callback.from_user.id, lang)
    await show_screen(callback, t("group.deleted", lang) + "\n\n" + text, kb)


# --- Student: leaving a group ----------------------------------------------------------------------------


@router.callback_query(F.data.startswith(CB_LEAVE_PREFIX))
async def confirm_leave(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    group_id = int(callback.data.removeprefix(CB_LEAVE_PREFIX))
    async with get_session() as session:
        group = await crud.get_group(session, group_id)
    if group is None:
        return
    await show_screen(
        callback,
        t("group.leave_confirm", lang, name=_esc(group.name)),
        confirm_kb(t("group.btn_leave_yes", lang), f"{CB_LEAVE_DO_PREFIX}{group_id}", CB_HOME, lang),
    )


@router.callback_query(F.data.startswith(CB_LEAVE_DO_PREFIX))
async def do_leave(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    group_id = int(callback.data.removeprefix(CB_LEAVE_DO_PREFIX))
    async with get_session() as session:
        group = await crud.get_group(session, group_id)
        left = await crud.leave_group(session, group_id, callback.from_user.id)
        teacher = await crud.get_user(session, group.teacher_id) if group else None
    if left and group is not None:
        await _notify(callback.bot, teacher, "group.left_notify_teacher", name=_esc(callback.from_user.full_name))
    text, kb = await _home(callback.from_user.id, lang)
    await show_screen(callback, t("group.left", lang) + "\n\n" + text, kb)
