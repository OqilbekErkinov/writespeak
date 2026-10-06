"""Generic Confirm/Edit inline keyboard, shown after OCR/parsing so the
student can catch extraction mistakes before grading runs."""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.i18n import t

CB_CONFIRM = "confirm_text"
CB_EDIT = "edit_text"


def confirm_edit_kb(lang: str = "uz") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("confirm.ok", lang), callback_data=CB_CONFIRM),
                InlineKeyboardButton(text=t("confirm.edit", lang), callback_data=CB_EDIT),
            ]
        ]
    )
