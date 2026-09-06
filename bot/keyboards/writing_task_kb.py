from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

CB_TASK1 = "writing:task1"
CB_TASK2 = "writing:task2"


def writing_task_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Task 1 { Report }", callback_data=CB_TASK1),
                InlineKeyboardButton(text="Task 2 { Essay }", callback_data=CB_TASK2),
            ]
        ]
    )
