from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.keyboards.nav_kb import back_button

CB_TASK1 = "writing:task1"
CB_TASK2 = "writing:task2"
CB_WRITING_MENU = "writing:menu"  # back to the Task 1/Task 2 picker

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
