"""Language picker inline keyboard - two different callback prefixes so
bot/handlers/language.py can tell first-time onboarding (which continues
into the welcome message + sample report) apart from a later change from
Hisobim/Профиль (which just saves and confirms)."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

CB_LANG_ONBOARD_PREFIX = "lang_onboard:"
CB_LANG_CHANGE_PREFIX = "lang_change:"


def lang_picker_kb(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🇺🇿 O'zbekcha", callback_data=f"{prefix}uz"),
                InlineKeyboardButton(text="🇷🇺 Русский", callback_data=f"{prefix}ru"),
            ]
        ]
    )
