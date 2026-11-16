"""Keyboards for the coin purchase flow (bot/handlers/payments.py)."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import settings
from bot.i18n import t
from bot.keyboards.support_kb import CB_SUPPORT_WRITE
from bot.utils.coins import CENTER_PACKS, COIN_PACKS, discount_percent

CB_BUY_PREFIX = "buy:"  # + coins of the pack
CB_GIFT_BUY_PREFIX = "giftbuy:"  # + coins of the pack bought as a gift (bot/handlers/payments.py)
CB_BUY_SUB_PREFIX = "buysub:"  # the retired unlimited subscription (old buttons in chats)
CB_PAY_APPROVE_PREFIX = "pay_approve:"
CB_PAY_REJECT_PREFIX = "pay_reject:"
CB_PAY_NOOP = "pay:noop"  # the "center packs" heading
CB_PAY_CANCEL_PREFIX = "pay_cancel:"  # + the request id: the buyer gives up before sending a receipt
# 2026-11-16: ⛔ first asks the admin why (none of these may start with "pay_reject:" or "pay_approve:")
CB_PAY_REASON_PREFIX = "pay_rjr:"  # + "<request id>:<REJECT_REASONS key>": reject for this reason
CB_PAY_REASON_BACK_PREFIX = "pay_rjb:"  # + the request id: from the reasons back to ✅/⛔
CB_PAY_RESEND_PREFIX = "pay_resend:"  # + the rejected request's id: the buyer sends a receipt for that pack again

# Why a payment is rejected (2026-11-16): the key is stored (PaymentRequest.reject_reason) and the buyer reads
# it in their own language (bot/i18n.py's "pay.reason_<key>"; "none" names no reason); the labels are the
# admins' (Uzbek), on their card in the bot and on the panel's /payments page.
REJECT_REASONS: dict[str, str] = {
    "amount": "Summa mos emas",
    "unreadable": "Chek o'qilmaydi",
    "reused": "Chek avval ishlatilgan",
    "no_money": "Pul kelib tushmagan",
    "none": "Sababsiz",
}


def pack_button(coins: int, price: int, lang: str, prefix: str = CB_BUY_PREFIX) -> InlineKeyboardButton:
    """A pack: for the buyer (CB_BUY_PREFIX) or as a gift (CB_GIFT_BUY_PREFIX)."""
    from bot.utils.quota import format_som  # local import: avoids a circular import with quota.py

    off = discount_percent(coins, price, settings.coin_price)
    key = "pay.pack_button_discount" if off else "pay.pack_button"
    return InlineKeyboardButton(
        text=t(key, lang, coins=coins, price=format_som(price), off=off), callback_data=f"{prefix}{coins}"
    )


def gift_share_kb(link: str, coins: int, lang: str) -> InlineKeyboardMarkup:
    """Under a ready gift: Telegram's share screen with the link and a line about it."""
    from urllib.parse import urlencode

    url = "https://t.me/share/url?" + urlencode({"url": link, "text": t("gift.share_text", lang, coins=coins)})
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("gift.btn_share", lang), url=url)]])


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


def reject_reasons_kb(request_id: int) -> InlineKeyboardMarkup:
    """⛔ tapped on an admin's card: the reasons, two to a row, and ↩️ back to ✅/⛔ (2026-11-16)."""
    buttons = [InlineKeyboardButton(text=label, callback_data=f"{CB_PAY_REASON_PREFIX}{request_id}:{key}")
               for key, label in REJECT_REASONS.items()]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text="↩️ Orqaga", callback_data=f"{CB_PAY_REASON_BACK_PREFIX}{request_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def rejected_kb(lang: str, request_id: int) -> InlineKeyboardMarkup:
    """Under "your payment wasn't approved" (2026-11-16: a photo sent after a rejection reached nothing): send
    the receipt for the same pack again, and the support chat (bot/handlers/support.py)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("pay.btn_resend", lang), callback_data=f"{CB_PAY_RESEND_PREFIX}{request_id}")],
        [InlineKeyboardButton(text=t("acc.btn_support", lang), callback_data=CB_SUPPORT_WRITE)],
    ])


def receipt_cancel_kb(lang: str, request_id: int) -> InlineKeyboardMarkup:
    """Under the card number: give up before sending a receipt (2026-11-15: the receipt step had no way out)."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("pay.cancel_btn", lang), callback_data=f"{CB_PAY_CANCEL_PREFIX}{request_id}")]])
