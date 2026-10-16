from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.i18n import t

CB_SUPPORT_WRITE = "support:write"  # under an admin's answer: write to the admins again


def support_reply_kb(lang: str = "uz") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t("support.btn_reply", lang), callback_data=CB_SUPPORT_WRITE)]]
    )
