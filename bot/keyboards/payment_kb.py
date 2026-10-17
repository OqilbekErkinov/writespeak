"""Keyboards for the coin purchase flow (bot/handlers/payments.py)."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import settings
from bot.i18n import t
from bot.utils.coins import CENTER_PACKS, COIN_PACKS, discount_percent

CB_BUY_PREFIX = "buy:"  # + coins of the pack
CB_BUY_SUB_PREFIX = "buysub:"  # the retired unlimited subscription (old buttons in chats)
CB_PAY_APPROVE_PREFIX = "pay_approve:"
CB_PAY_REJECT_PREFIX = "pay_reject:"
CB_PAY_NOOP = "pay:noop"  # the "center packs" heading


def pack_button(coins: int, price: int, lang: str) -> InlineKeyboardButton:
    from bot.utils.quota import format_som  # local import: avoids a circular import with quota.py

    off = discount_percent(coins, price, settings.coin_price)
    key = "pay.pack_button_discount" if off else "pay.pack_button"
    return InlineKeyboardButton(
        text=t(key, lang, coins=coins, price=format_som(price), off=off), callback_data=f"{CB_BUY_PREFIX}{coins}"
    )


def buy_coins_kb(lang: str = "uz", with_center_packs: bool = False) -> InlineKeyboardMarkup:
    """The coin packs; teachers and center owners also see the center packs."""
    rows = [[pack_button(coins, price, lang)] for coins, price in COIN_PACKS]
    if with_center_packs:
        rows.append([InlineKeyboardButton(text=t("pay.center_packs_header", lang), callback_data=CB_PAY_NOOP)])
        rows += [[pack_button(coins, price, lang)] for coins, price in CENTER_PACKS]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_review_kb(request_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Tasdiqlash", callback_data=f"{CB_PAY_APPROVE_PREFIX}{request_id}"
                ),
                InlineKeyboardButton(
                    text="⛔ Rad etish", callback_data=f"{CB_PAY_REJECT_PREFIX}{request_id}"
                ),
            ]
        ]
    )
