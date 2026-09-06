"""Inline 'get a shareable image' button attached to a finished PDF report
(bot/handlers/share.py)."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.i18n import t

CB_SHARE_PREFIX = "share:"


def share_card_kb(submission_type: str, submission_id: int, lang: str = "uz") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("share.button", lang),
                    callback_data=f"{CB_SHARE_PREFIX}{submission_type}:{submission_id}",
                )
            ]
        ]
    )
