"""Hisobim / Профиль: one hub for the student's account - the balance and a
short summary on top, buttons below: top up (first explained, then the
students' or the teachers'/centres' packs), the history five works to a
page, a statistics card in the brand, the language, and a message to the
admins (bot/handlers/support.py).

"📂 My Works" and "💬 Admin bilan aloqa" used to be main-menu buttons; the
old texts (still on some keyboards) open this hub and the support chat.
"""
from __future__ import annotations

import asyncio
import html
import math
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from bot.config import settings
from bot.handlers.payments import sees_center_packs
from bot.i18n import t
from bot.keyboards.account_kb import (
    CB_HISTORY_PREFIX,
    CB_HOME,
    CB_LANG,
    CB_NOOP,
    CB_STATS,
    CB_TOPUP,
    CB_TOPUP_CENTER,
    CB_TOPUP_STUDENT,
    HISTORY_PAGE,
    account_kb,
    back_to_account_kb,
    history_kb,
    lang_kb,
    packs_kb,
    teacher_needed_kb,
    topup_kb,
)
from bot.keyboards.main_menu_kb import BTN_ACCOUNT_RU, BTN_ACCOUNT_UZ, BTN_MY_WORKS
from bot.utils.messages import remove_message, show_screen
from bot.utils.quota import price_list
from db import crud
from db.crud import CRITERIA_LABELS, CRITERIA_TIPS
from db.database import get_session
from services.image.stats_card import build_stats_card

router = Router(name="account")

RECENT_WORKS_FOR_WEAKNESS = 5
_EVER = datetime(2000, 1, 1, tzinfo=timezone.utc)


async def _home_text(tg, lang: str) -> str:
    async with get_session() as session:
        user = await crud.get_user(session, tg.id)
        referral_count = await crud.get_referral_count(session, tg.id)
        vocab_total, vocab_learned = await crud.get_vocabulary_counts(session, tg.id)
        works = await crud.get_works_since(session, tg.id, _EVER)
        criteria_averages = await crud.get_criteria_averages(session, tg.id, limit=RECENT_WORKS_FOR_WEAKNESS)

    lines = [t("acc.title", lang, name=html.escape(tg.first_name or tg.full_name or "", quote=False)), ""]
    lines.append(t("account.coin_balance", lang, coins=user.coin_balance if user is not None else 0))
    now = datetime.now(timezone.utc)
    if user is not None and user.subscription_expires_at and user.subscription_expires_at > now:
        lines.append(t("account.subscription_active", lang, days=(user.subscription_expires_at - now).days))
    writing = sum(1 for w in works if w["kind"] == "w")
    lines.append(t("acc.checks_line", lang, total=len(works), w=writing, s=len(works) - writing))
    lines.append(t("account.vocab_progress", lang, learned=vocab_learned, total=vocab_total))
    if criteria_averages:
        key = min(criteria_averages, key=criteria_averages.get)
        lines.append(t("account.weakest", lang, label=CRITERIA_LABELS.get(key, key), avg=criteria_averages[key],
                       tip=CRITERIA_TIPS.get(key, "")))
    lines += ["", t("account.referral", lang, count=referral_count, coins=settings.referral_coins,
                    link=f"https://t.me/{settings.telegram_bot_username}?start=ref_{tg.id}"),
              "", t("acc.choose", lang)]
    return "\n".join(lines)


@router.message(F.text.in_({BTN_ACCOUNT_UZ, BTN_ACCOUNT_RU, BTN_MY_WORKS}))
async def show_account(message: Message, state: FSMContext, lang: str) -> None:
    await state.clear()  # leaving whatever flow was in progress
    await message.answer(await _home_text(message.from_user, lang), reply_markup=account_kb(lang),
                         disable_web_page_preview=True)


@router.callback_query(F.data == CB_HOME)
async def account_home(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    await state.clear()
    text = await _home_text(callback.from_user, lang)
    if getattr(callback.message, "text", None) is not None:
        await show_screen(callback, text, account_kb(lang))
        return
    # from the statistics photo: a new text message, without a link preview
    await remove_message(callback.message)
    await callback.message.answer(text, reply_markup=account_kb(lang), disable_web_page_preview=True)


@router.callback_query(F.data == CB_NOOP)
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()


# --- top up ---------------------------------------------------------------------------------------


@router.callback_query(F.data == CB_TOPUP)
async def topup(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    await show_screen(callback, t("acc.topup", lang, prices=price_list(lang)), topup_kb(lang))


@router.callback_query(F.data == CB_TOPUP_STUDENT)
async def topup_students(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    await show_screen(callback, t("acc.packs_students", lang), packs_kb(lang, center=False))


@router.callback_query(F.data == CB_TOPUP_CENTER)
async def topup_centers(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    async with get_session() as session:
        user = await crud.get_user(session, callback.from_user.id)
    if sees_center_packs(user):
        await show_screen(callback, t("acc.packs_centers", lang), packs_kb(lang, center=True))
    else:
        await show_screen(callback, t("acc.centers_only", lang), teacher_needed_kb(lang))


# --- history, five to a page --------------------------------------------------------------------


async def show_history(callback: CallbackQuery, page: int, lang: str) -> None:
    async with get_session() as session:
        items = await crud.get_user_history(session, callback.from_user.id, limit=10_000)
    if not items:
        await callback.answer(t("mywork.empty", lang), show_alert=True)
        return
    await callback.answer()
    pages = math.ceil(len(items) / HISTORY_PAGE)
    page = min(max(page, 0), pages - 1)
    chunk = items[page * HISTORY_PAGE:(page + 1) * HISTORY_PAGE]
    text = t("acc.history", lang, start=page * HISTORY_PAGE + 1, end=page * HISTORY_PAGE + len(chunk), total=len(items))
    await show_screen(callback, text, history_kb(chunk, page, pages, lang))


@router.callback_query(F.data.startswith(CB_HISTORY_PREFIX))
async def history(callback: CallbackQuery, lang: str) -> None:
    try:
        page = int(callback.data.removeprefix(CB_HISTORY_PREFIX))
    except ValueError:
        page = 0
    await show_history(callback, page, lang)


# --- statistics ------------------------------------------------------------------------------------


def _trend(bands: list[float], lang: str) -> str:
    """The last 3 bands against the 3 before them (or all the earlier ones)."""
    recent = bands[-3:]
    previous = bands[-6:-3] if len(bands) >= 6 else bands[:-3]
    if previous:
        change = sum(recent) / len(recent) - sum(previous) / len(previous)
        if change >= 0.25:
            return t("mywork.trend_up", lang)
        if change <= -0.25:
            return t("mywork.trend_down", lang)
    return t("mywork.trend_stable", lang)


async def show_stats(callback: CallbackQuery, lang: str) -> None:
    async with get_session() as session:
        works = await crud.get_works_since(session, callback.from_user.id, _EVER)
        criteria_averages = await crud.get_criteria_averages(
            session, callback.from_user.id, limit=RECENT_WORKS_FOR_WEAKNESS
        )
    if not works:
        await callback.answer(t("mywork.empty", lang), show_alert=True)
        return
    await callback.answer(t("acc.stats_wait", lang))
    png = await asyncio.to_thread(
        build_stats_card, student_name=callback.from_user.full_name, works=works,
        bot_username=settings.telegram_bot_username, lang=lang,
    )
    chronological = [w["band"] for w in reversed(works)]
    caption = t("acc.stats_caption", lang, last=f"{works[0]['band']:.1f}", trend=_trend(chronological, lang))
    if criteria_averages:
        key = min(criteria_averages, key=criteria_averages.get)
        caption += "\n💡 " + CRITERIA_TIPS.get(key, "")
    await remove_message(callback.message)
    await callback.message.answer_photo(
        BufferedInputFile(png, filename="bandzilla_statistika.png"), caption=caption,
        reply_markup=back_to_account_kb(lang),
    )


@router.callback_query(F.data == CB_STATS)
async def stats(callback: CallbackQuery, lang: str) -> None:
    await show_stats(callback, lang)


# --- language ----------------------------------------------------------------------------------------


@router.callback_query(F.data == CB_LANG)
async def choose_language(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    await show_screen(callback, t("account.language_button", lang), lang_kb(lang))
