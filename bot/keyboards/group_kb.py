"""Keyboards for the B2B group (teacher/language-center) flow
(bot/handlers/group.py)."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.i18n import t
from bot.keyboards.nav_kb import CB_BACK_MAIN

CB_HOME = "grp:home"
CB_CREATE = "grp:create"
CB_REQUEST = "grp:req"
CB_TEACHER_OK_PREFIX = "grp:t_ok:"  # + user id (admin decision)
CB_TEACHER_NO_PREFIX = "grp:t_no:"
CB_STATS = "grp:stats"
CB_LIST_PREFIX = "grp:list:"  # + page
CB_STUDENT_PREFIX = "grp:stu:"  # + user id:page
CB_PDF_PREFIX = "grp:pdf:"  # + w|s:submission id
CB_GIFT_PREFIX = "grp:gift:"  # + user id:page
CB_GIFT_DO_PREFIX = "grp:gift_do:"  # + user id:amount
CB_GIFT_ALL = "grp:giftall"
CB_GIFT_ALL_DO_PREFIX = "grp:giftall_do:"  # + amount
CB_KICK_PREFIX = "grp:kick:"  # + user id:page
CB_KICK_DO_PREFIX = "grp:kick_do:"  # + user id
CB_DELETE = "grp:del"
CB_DELETE_DO = "grp:del_do"
CB_LEAVE_PREFIX = "grp:leave:"  # + group id
CB_LEAVE_DO_PREFIX = "grp:leave_do:"  # + group id

GIFT_AMOUNT_OPTIONS = (1, 3, 5, 10)
STUDENTS_PER_PAGE = 10


def _button(text: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def _back_row(lang: str, callback_data: str = CB_BACK_MAIN) -> list[InlineKeyboardButton]:
    return [_button(t("nav.back", lang), callback_data)]


def no_group_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_button(t("group.btn_create", lang), CB_CREATE)], _back_row(lang)]
    )


def request_teacher_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_button(t("group.btn_request", lang), CB_REQUEST)], _back_row(lang, CB_HOME)]
    )


def teacher_review_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button("✅ O'qituvchi qilish", f"{CB_TEACHER_OK_PREFIX}{user_id}"),
                _button("⛔ Rad etish", f"{CB_TEACHER_NO_PREFIX}{user_id}"),
            ]
        ]
    )


def teacher_home_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button(t("group.btn_stats", lang), CB_STATS), _button(t("group.btn_students", lang), f"{CB_LIST_PREFIX}0")],
            [_button(t("group.btn_gift_all", lang), CB_GIFT_ALL)],
            [_button(t("group.btn_delete", lang), CB_DELETE)],
            _back_row(lang),
        ]
    )


def member_home_kb(groups: list[tuple[int, str]], can_create: bool, lang: str) -> InlineKeyboardMarkup:
    rows = [[_button(t("group.btn_leave", lang, name=name), f"{CB_LEAVE_PREFIX}{gid}")] for gid, name in groups]
    if can_create:
        rows.append([_button(t("group.btn_create", lang), CB_CREATE)])
    rows.append(_back_row(lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_home_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[_back_row(lang, CB_HOME)])


def students_kb(rows: list[tuple[int, str]], page: int, pages: int, lang: str) -> InlineKeyboardMarkup:
    """`rows`: (student id, button label) for this page."""
    keyboard = [[_button(label, f"{CB_STUDENT_PREFIX}{uid}:{page}")] for uid, label in rows]
    nav = []
    if page > 0:
        nav.append(_button("◀️", f"{CB_LIST_PREFIX}{page - 1}"))
    if page < pages - 1:
        nav.append(_button("▶️", f"{CB_LIST_PREFIX}{page + 1}"))
    if nav:
        keyboard.append(nav)
    keyboard.append(_back_row(lang, CB_HOME))
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def student_card_kb(
    student_id: int, page: int, reports: list[tuple[str, int, str]], lang: str
) -> InlineKeyboardMarkup:
    """`reports`: (kind w|s, submission id, button label) for the recent works."""
    keyboard = [[_button(label, f"{CB_PDF_PREFIX}{kind}:{sid}")] for kind, sid, label in reports]
    keyboard.append(
        [
            _button(t("group.btn_gift_one", lang), f"{CB_GIFT_PREFIX}{student_id}:{page}"),
            _button(t("group.btn_kick", lang), f"{CB_KICK_PREFIX}{student_id}:{page}"),
        ]
    )
    keyboard.append(_back_row(lang, f"{CB_LIST_PREFIX}{page}"))
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def amounts_kb(options: list[int], callback_prefix: str, back: str, lang: str) -> InlineKeyboardMarkup:
    keyboard = [[_button(str(a), f"{callback_prefix}{a}") for a in options]] if options else []
    keyboard.append(_back_row(lang, back))
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def confirm_kb(yes_text: str, yes_callback: str, no_callback: str, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_button(yes_text, yes_callback), _button(t("group.btn_cancel", lang), no_callback)]]
    )
