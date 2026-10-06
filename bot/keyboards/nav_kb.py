"""Shared "⬅️ Orqaga" / post-result navigation buttons.

`CB_BACK_MAIN` is handled once, in bot/handlers/main_menu.py (re-shows the
main reply keyboard, which collapses itself after every tap - see
main_menu_kb's `one_time_keyboard`). Every other back target is a callback
owned by the section it returns to, passed in here as plain callback data.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.i18n import t

CB_BACK_MAIN = "nav:main"


def back_button(lang: str, callback_data: str = CB_BACK_MAIN) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=t("nav.back", lang), callback_data=callback_data)


def main_menu_button(lang: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=t("nav.main_menu", lang), callback_data=CB_BACK_MAIN)


def back_kb(lang: str, callback_data: str = CB_BACK_MAIN) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[back_button(lang, callback_data)]])


def check_again_kb(again_callback: str, lang: str) -> InlineKeyboardMarkup:
    """Shown under a graded (non-practice) answer: start another check of
    the same task/part, or leave."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("nav.check_again", lang), callback_data=again_callback)],
            [main_menu_button(lang)],
        ]
    )


def with_back(
    kb: InlineKeyboardMarkup, lang: str, callback_data: str = CB_BACK_MAIN
) -> InlineKeyboardMarkup:
    """Returns a copy of `kb` with a back row appended."""
    return InlineKeyboardMarkup(
        inline_keyboard=[*kb.inline_keyboard, [back_button(lang, callback_data)]]
    )
