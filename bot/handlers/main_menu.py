"""Main-menu scaffolding.

The module buttons (Writing/Speaking/Practice/My Works/Vocab/Account) are
each owned by their dedicated handler module and registered on the same
button-text filters defined in bot/keyboards/main_menu_kb.py. This router
only re-shows the menu - via /menu, or any section's "⬅️ Orqaga" /
"🏠 Asosiy menyu" button (bot/keyboards/nav_kb.py's CB_BACK_MAIN).
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.i18n import t
from bot.keyboards.main_menu_kb import main_menu_kb
from bot.keyboards.nav_kb import CB_BACK_MAIN
from bot.utils.messages import remove_message

router = Router(name="main_menu")


@router.message(Command("menu"))
async def show_menu(message: Message, lang: str) -> None:
    await message.answer(t("menu.shown", lang), reply_markup=main_menu_kb(lang))


@router.callback_query(F.data == CB_BACK_MAIN)
async def back_to_main_menu(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    # Leaving a section abandons whatever flow was in progress there.
    await state.clear()
    await remove_message(callback.message)
    await callback.message.answer(t("menu.shown", lang), reply_markup=main_menu_kb(lang))
