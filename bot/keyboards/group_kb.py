"""Keyboards for the B2B group (teacher/language-center) flow
(bot/handlers/group.py)."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.i18n import t
from db.models import User

CB_GROUP_CREATE = "group:create"
CB_GROUP_STATS = "group:stats"
CB_GROUP_GIFT = "group:gift"
CB_GROUP_GIFT_STUDENT_PREFIX = "group:gift_student:"
CB_GROUP_GIFT_AMOUNT_PREFIX = "group:gift_amount:"

GIFT_AMOUNT_OPTIONS = (1, 3, 5, 10)


def no_group_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t("group.btn_create", lang), callback_data=CB_GROUP_CREATE)]]
    )


def teacher_actions_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("group.btn_stats", lang), callback_data=CB_GROUP_STATS),
                InlineKeyboardButton(text=t("group.btn_gift", lang), callback_data=CB_GROUP_GIFT),
            ]
        ]
    )


def gift_student_picker_kb(students: list[User]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=s.full_name, callback_data=f"{CB_GROUP_GIFT_STUDENT_PREFIX}{s.id}")]
        for s in students
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def gift_amount_picker_kb(student_id: int, max_amount: int) -> InlineKeyboardMarkup:
    options = [a for a in GIFT_AMOUNT_OPTIONS if a <= max_amount]
    if not options and max_amount > 0:
        options = [max_amount]
    rows = [
        [
            InlineKeyboardButton(
                text=str(a), callback_data=f"{CB_GROUP_GIFT_AMOUNT_PREFIX}{student_id}:{a}"
            )
        ]
        for a in options
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)
