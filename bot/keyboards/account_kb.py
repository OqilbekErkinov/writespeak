"""Hisobim keyboards (bot/handlers/account.py): the hub, the top-up choice
and its packs, and the history five works to a page."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.i18n import t
from bot.keyboards.group_kb import CB_REQUEST
from bot.keyboards.lang_kb import CB_LANG_CHANGE_PREFIX
from bot.keyboards.my_works_kb import CB_DOWNLOAD_PREFIX
from bot.keyboards.nav_kb import CB_BACK_MAIN
from bot.keyboards.payment_kb import pack_button
from bot.keyboards.support_kb import CB_SUPPORT_WRITE
from bot.utils.coins import CENTER_PACKS, COIN_PACKS

CB_HOME = "acc:home"
CB_TOPUP = "acc:topup"
CB_TOPUP_STUDENT = "acc:topup:st"
CB_TOPUP_CENTER = "acc:topup:ce"
CB_HISTORY_PREFIX = "acc:hist:"  # + page (0-based)
CB_STATS = "acc:stats"
CB_LANG = "acc:lang"
CB_NOOP = "acc:noop"

HISTORY_PAGE = 5


def _b(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def account_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_b(t("acc.btn_topup", lang), CB_TOPUP)],
        [_b(t("acc.btn_history", lang), f"{CB_HISTORY_PREFIX}0"), _b(t("acc.btn_stats", lang), CB_STATS)],
        [_b(t("acc.btn_lang", lang), CB_LANG), _b(t("acc.btn_support", lang), CB_SUPPORT_WRITE)],
        [_b(t("acc.btn_back_main", lang), CB_BACK_MAIN)],
    ])


def back_to_account_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_b(t("acc.btn_back", lang), CB_HOME)]])


def topup_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_b(t("acc.btn_for_students", lang), CB_TOPUP_STUDENT)],
        [_b(t("acc.btn_for_centers", lang), CB_TOPUP_CENTER)],
        [_b(t("acc.btn_back", lang), CB_HOME)],
    ])


def packs_kb(lang: str, center: bool) -> InlineKeyboardMarkup:
    rows = [[pack_button(coins, price, lang)] for coins, price in (CENTER_PACKS if center else COIN_PACKS)]
    rows.append([_b(t("acc.btn_back_topup", lang), CB_TOPUP)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def teacher_needed_kb(lang: str) -> InlineKeyboardMarkup:
    """For a student who opened the centre packs: ask to become a teacher."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [_b(t("acc.btn_become_teacher", lang), CB_REQUEST)],
        [_b(t("acc.btn_back_topup", lang), CB_TOPUP)],
    ])


def lang_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_b("🇺🇿 O‘zbekcha", f"{CB_LANG_CHANGE_PREFIX}uz"), _b("🇷🇺 Русский", f"{CB_LANG_CHANGE_PREFIX}ru")],
        [_b(t("acc.btn_back", lang), CB_HOME)],
    ])


def history_kb(items: list[dict], page: int, pages: int, lang: str) -> InlineKeyboardMarkup:
    rows = []
    for item in items:
        band = f" · Band {item['band']:.1f}" if item["band"] is not None else ""
        rows.append([_b(f"📥 {item['label']} · {item['date']:%d.%m.%Y}{band}",
                        f"{CB_DOWNLOAD_PREFIX}{item['type']}:{item['id']}")])
    if pages > 1:
        nav = []
        nav.append(_b("‹", f"{CB_HISTORY_PREFIX}{page - 1}") if page > 0 else _b(" ", CB_NOOP))
        nav.append(_b(f"{page + 1} / {pages}", CB_NOOP))
        nav.append(_b("›", f"{CB_HISTORY_PREFIX}{page + 1}") if page < pages - 1 else _b(" ", CB_NOOP))
        rows.append(nav)
    rows.append([_b(t("acc.btn_back", lang), CB_HOME)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
