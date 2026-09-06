"""Generic Confirm/Edit inline keyboard, shown after OCR/parsing so the
student can catch extraction mistakes before grading runs."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

CB_CONFIRM = "confirm_text"
CB_EDIT = "edit_text"


def confirm_edit_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ To'g'ri", callback_data=CB_CONFIRM),
                InlineKeyboardButton(text="✏️ Tahrirlash", callback_data=CB_EDIT),
            ]
        ]
    )
