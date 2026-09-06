"""Keyboards for the paid-credits purchase flow (bot/handlers/payments.py)."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import settings
from bot.i18n import t

CB_BUY_PREFIX = "buy:"
CB_BUY_SUB_PREFIX = "buysub:"
CB_PAY_APPROVE_PREFIX = "pay_approve:"
CB_PAY_REJECT_PREFIX = "pay_reject:"

# How many checks a student can buy in one go. A single 5000 so'm transfer
# per overage would get tedious fast, so bundling cuts down on repeat top-ups.
CREDIT_BUNDLES = (1, 3, 5, 10)


def buy_credits_kb(lang: str = "uz") -> InlineKeyboardMarkup:
    from bot.utils.quota import format_som  # local import: avoids a circular import with quota.py

    rows = [
        [
            InlineKeyboardButton(
                text=t("pay.bundle_button", lang, n=n, price=format_som(n * settings.price_per_check)),
                callback_data=f"{CB_BUY_PREFIX}{n}",
            )
        ]
        for n in CREDIT_BUNDLES
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text=t(
                    "pay.subscription_button",
                    lang,
                    price=format_som(settings.subscription_price_monthly),
                ),
                callback_data=f"{CB_BUY_SUB_PREFIX}1",
            )
        ]
    )
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
