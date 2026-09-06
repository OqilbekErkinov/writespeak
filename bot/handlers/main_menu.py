"""Main-menu scaffolding.

The module buttons (Writing/Speaking/Practice/My Works/Vocab/Account) are
each owned by their dedicated handler module and registered on the same
button-text filters defined in bot/keyboards/main_menu_kb.py. This router
only re-shows the menu.
"""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.i18n import t
from bot.keyboards.main_menu_kb import main_menu_kb

router = Router(name="main_menu")


@router.message(Command("menu"))
async def show_menu(message: Message, lang: str) -> None:
    await message.answer(t("menu.shown", lang), reply_markup=main_menu_kb(lang))
