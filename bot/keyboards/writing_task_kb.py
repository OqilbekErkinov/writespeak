from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.i18n import t
from bot.keyboards.nav_kb import back_button

CB_TASK1 = "writing:task1"
CB_TASK2 = "writing:task2"
CB_WRITING_MENU = "writing:menu"  # back to the Task 1/Task 2 picker
CB_NO_CHART = "writing:no_chart"  # a typed Task 1 question checked without its chart
CB_SWITCH_SECTION = "writing:switch_section"  # the question belongs to the other Task: mark it there
CB_CHECK_AS_IS = "writing:check_as_is"  # the answer seems to start mid-word, but it is complete
CB_RECHECK_NEW = "writing:recheck_new"  # an essay sent before (admins, testers): mark it again
CB_RECHECK_OLD = "writing:recheck_old"  # ...or send its saved report

TASK_CALLBACKS = {"task1": CB_TASK1, "task2": CB_TASK2}


def writing_task_kb(lang: str = "uz") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Task 1 { Report }", callback_data=CB_TASK1),
                InlineKeyboardButton(text="Task 2 { Essay }", callback_data=CB_TASK2),
            ],
            [back_button(lang)],
        ]
    )


def wrong_section_kb(lang: str, section: str, back_callback: str) -> InlineKeyboardMarkup:
    """Under "this question belongs to the other Task": mark it there, or back."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("writing.btn_switch_section", lang, section=section),
                                  callback_data=CB_SWITCH_SECTION)],
            [back_button(lang, back_callback)],
        ]
    )


def cut_start_kb(lang: str, back_callback: str) -> InlineKeyboardMarkup:
    """Under "your answer seems to start mid-word": check it as it is, or back."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("writing.btn_check_as_is", lang), callback_data=CB_CHECK_AS_IS)],
            [back_button(lang, back_callback)],
        ]
    )


def recheck_kb(lang: str) -> InlineKeyboardMarkup:
    """Under "this essay was checked before - check it again?" (admins and testers only)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("writing.btn_recheck_new", lang), callback_data=CB_RECHECK_NEW)],
            [InlineKeyboardButton(text=t("writing.btn_recheck_old", lang), callback_data=CB_RECHECK_OLD)],
        ]
    )


def chart_kb(lang: str, back_callback: str) -> InlineKeyboardMarkup:
    """Under "send the Task 1 chart": go on without it, or back."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("writing.btn_no_chart", lang), callback_data=CB_NO_CHART)],
            [back_button(lang, back_callback)],
        ]
    )
