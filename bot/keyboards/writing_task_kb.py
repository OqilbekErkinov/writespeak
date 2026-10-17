from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.i18n import t
from bot.keyboards.nav_kb import back_button

CB_TASK1 = "writing:task1"
CB_TASK2 = "writing:task2"
CB_WRITING_MENU = "writing:menu"  # back to the Task 1/Task 2 picker
CB_NO_CHART = "writing:no_chart"  # a typed Task 1 question checked without its chart

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


def chart_kb(lang: str, back_callback: str) -> InlineKeyboardMarkup:
    """Under "send the Task 1 chart": go on without it, or back."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("writing.btn_no_chart", lang), callback_data=CB_NO_CHART)],
            [back_button(lang, back_callback)],
        ]
    )
