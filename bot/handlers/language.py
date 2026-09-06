"""Handles both language-picker callbacks: onboarding (first /start, see
bot/handlers/start.py - continues into the welcome message + sample report)
and a later change from Hisobim/Профиль (bot/handlers/account.py - just
saves and confirms)."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery

from bot.config import settings
from bot.i18n import t
from bot.keyboards.lang_kb import CB_LANG_CHANGE_PREFIX, CB_LANG_ONBOARD_PREFIX
from bot.keyboards.main_menu_kb import main_menu_kb
from bot.utils.sample_report import build_sample_report_pdf
from db import crud
from db.database import get_session

router = Router(name="language")


async def _save_language(user_id: int, lang: str) -> None:
    async with get_session() as session:
        user = await crud.get_user(session, user_id)
        if user is not None:
            user.language = lang
            await session.commit()


@router.callback_query(F.data.startswith(CB_LANG_ONBOARD_PREFIX))
async def onboard_set_language(callback: CallbackQuery) -> None:
    await callback.answer()
    lang = callback.data.removeprefix(CB_LANG_ONBOARD_PREFIX)
    if lang not in ("uz", "ru"):
        return
    await _save_language(callback.from_user.id, lang)
    await callback.message.edit_reply_markup(reply_markup=None)

    await callback.message.answer(
        t("start.welcome", lang, name=callback.from_user.full_name), reply_markup=main_menu_kb(lang)
    )
    pdf_bytes = build_sample_report_pdf()
    await callback.message.answer_document(
        BufferedInputFile(pdf_bytes, filename="Namuna_Hisobot.pdf"),
        caption=t("start.sample_caption", lang, free_limit=settings.free_daily_limit),
    )


@router.callback_query(F.data.startswith(CB_LANG_CHANGE_PREFIX))
async def change_language(callback: CallbackQuery) -> None:
    await callback.answer()
    lang = callback.data.removeprefix(CB_LANG_CHANGE_PREFIX)
    if lang not in ("uz", "ru"):
        return
    await _save_language(callback.from_user.id, lang)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(t("lang.saved", lang), reply_markup=main_menu_kb(lang))
