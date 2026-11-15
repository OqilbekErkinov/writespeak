"""Handles both language-picker callbacks: onboarding (first /start, see
bot/handlers/start.py - continues into the welcome message + sample report)
and a later change from Hisobim/Профиль (bot/handlers/account.py - just
saves and confirms)."""
from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, CallbackQuery

from bot.config import settings
from bot.i18n import LANGS, t
from bot.keyboards.lang_kb import CB_LANG_CHANGE_PREFIX, CB_LANG_ONBOARD_PREFIX
from bot.keyboards.main_menu_kb import main_menu_kb
from bot.utils import gifts, stickers
from bot.utils.crm import redis
from bot.utils.group_invite import joined_notice
from bot.utils.sample_report import build_sample_report_pdf
from db import crud
from db.database import get_session

logger = logging.getLogger(__name__)
router = Router(name="language")


async def _save_language(user_id: int, lang: str) -> None:
    async with get_session() as session:
        user = await crud.get_user(session, user_id)
        if user is not None:
            user.language = lang
            await session.commit()


@router.callback_query(F.data.startswith(CB_LANG_ONBOARD_PREFIX))
async def onboard_set_language(callback: CallbackQuery) -> None:
    """The first language pick: the welcome, the sample report, the welcome coins, a friend's gift and a group
    joined through a link. Since 2026-11-15 a double tap on a flag runs it once, the gift is claimed first,
    and a step that fails (the sample PDF, a sticker) no longer loses the steps after it - the user exists
    by now, so /start would never show them again."""
    await callback.answer()
    lang = callback.data.removeprefix(CB_LANG_ONBOARD_PREFIX)
    if lang not in LANGS:
        return
    user_id = callback.from_user.id
    try:
        if not await redis().set(f"onboard:{user_id}", lang, nx=True, ex=600):
            return  # the same flag (or another) tapped again while the first tap is handled
    except Exception:  # noqa: BLE001 - Redis away: onboarding still runs
        logger.warning("Onboarding lock not taken for %s", user_id, exc_info=True)
    await _save_language(user_id, lang)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass

    await callback.message.answer(
        t("start.welcome", lang, name=callback.from_user.full_name), reply_markup=main_menu_kb(lang)
    )
    # Came through a friend's gift link (bot/handlers/start.py kept the code): claimed first.
    gift_code = await gifts.pop_onboarding_code(user_id)
    if gift_code:
        await _step("gift", gifts.claim(callback.message, callback.bot, gift_code, lang))
    await _step("hello sticker", stickers.send(callback.bot, user_id, "salom"))  # the brand's first hello (p. 49)
    await _step("sample report", _send_sample(callback, lang))
    # The welcome coins, then - on its own, with the Tip sticker - to spend
    # them on real IELTS tasks, not on a try-out sentence.
    await callback.message.answer(t("start.welcome_coins", lang, coins=settings.welcome_coins))
    await _step("tip sticker", stickers.send(callback.bot, user_id, "maslahat"))
    await callback.message.answer(t("start.real_task", lang))
    # Joined a group through an invite link before picking the language
    # (bot/handlers/start.py): confirm it now, in their language.
    await _step("group notice", _group_notice(callback, lang))


async def _step(name: str, coro) -> None:
    """One onboarding step: its failure is logged, the next steps still run."""
    try:
        await coro
    except Exception:  # noqa: BLE001
        logger.exception("Onboarding step %s failed", name)


async def _send_sample(callback: CallbackQuery, lang: str) -> None:
    # In the student's language; rendered off the event loop (WeasyPrint is CPU-bound).
    pdf_bytes = await asyncio.to_thread(build_sample_report_pdf, lang)
    await callback.message.answer_document(
        BufferedInputFile(pdf_bytes, filename="Sample_Report.pdf" if lang == "en" else "Namuna_Hisobot.pdf"),
        caption=t("start.sample_caption", lang),
    )


async def _group_notice(callback: CallbackQuery, lang: str) -> None:
    async with get_session() as session:
        groups = await crud.get_member_groups(session, callback.from_user.id)
    if groups:
        group, teacher = groups[0]
        await callback.message.answer(
            joined_notice(group.name, teacher.full_name if teacher is not None else "", lang)
        )


@router.callback_query(F.data.startswith(CB_LANG_CHANGE_PREFIX))
async def change_language(callback: CallbackQuery) -> None:
    await callback.answer()
    lang = callback.data.removeprefix(CB_LANG_CHANGE_PREFIX)
    if lang not in LANGS:
        return
    await _save_language(callback.from_user.id, lang)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(t("lang.saved", lang), reply_markup=main_menu_kb(lang))
