"""Paid-credits top-up flow: pick a bundle -> pay by card -> send a receipt
screenshot -> an admin approves/rejects it against the receipt.

This is where bot/handlers/admin.py's old Approve/Deny-for-registration
pattern moved to once the admin approval gate for new users was removed
(see bot/middlewares/access_control.py) - same shape (inline buttons, a
race guard so two admins/duplicate taps can't double-apply a decision),
different domain.

The admin-facing messages here (payment review caption/buttons, permission
errors) are deliberately left in Uzbek, not run through bot/i18n.py, since
admins are Uzbek speakers in this deployment; only the buyer-facing text is
bilingual. Notifications sent TO the buyer (in `_decide`) use the buyer's
own stored language, not the acting admin's - those are two different
people and `lang` injected by the middleware here reflects the admin.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import settings
from bot.i18n import t
from bot.keyboards.main_menu_kb import MENU_BUTTON_TEXTS
from bot.keyboards.payment_kb import (
    CB_BUY_PREFIX,
    CB_BUY_SUB_PREFIX,
    CB_PAY_APPROVE_PREFIX,
    CB_PAY_REJECT_PREFIX,
    payment_review_kb,
)
from bot.states.payment_states import PaymentStates
from bot.utils.quota import format_som
from db import crud
from db.database import get_session
from db.models import PaymentKind, PaymentRequest

logger = logging.getLogger(__name__)
router = Router(name="payments")


def _is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


@router.callback_query(F.data.startswith(CB_BUY_PREFIX))
async def choose_bundle(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    credits = int(callback.data.removeprefix(CB_BUY_PREFIX))
    amount = credits * settings.price_per_check

    async with get_session() as session:
        request = await crud.create_payment_request(
            session, user_id=callback.from_user.id, credits=credits, amount=amount
        )
    await _start_receipt_flow(callback.message, state, request, lang)


@router.callback_query(F.data.startswith(CB_BUY_SUB_PREFIX))
async def choose_subscription(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    months = int(callback.data.removeprefix(CB_BUY_SUB_PREFIX))
    amount = months * settings.subscription_price_monthly

    async with get_session() as session:
        request = await crud.create_payment_request(
            session,
            user_id=callback.from_user.id,
            amount=amount,
            kind=PaymentKind.subscription,
            subscription_months=months,
        )
    await _start_receipt_flow(callback.message, state, request, lang)


async def _start_receipt_flow(
    message: Message, state: FSMContext, request: PaymentRequest, lang: str
) -> None:
    await state.update_data(payment_request_id=request.id)
    await state.set_state(PaymentStates.awaiting_receipt)
    await message.answer(
        t(
            "pay.instructions",
            lang,
            amount=format_som(request.amount),
            card=settings.payment_card_number,
            holder=settings.payment_card_holder,
        )
    )


@router.message(PaymentStates.awaiting_receipt, F.photo)
async def receive_receipt(message: Message, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    request_id = data.get("payment_request_id")
    if not request_id:
        await message.answer(t("pay.choose_amount_first", lang))
        return

    file_id = message.photo[-1].file_id
    async with get_session() as session:
        request = await crud.set_payment_receipt(session, request_id, file_id)

    await state.clear()
    if request is None:
        await message.answer(t("pay.error_retry", lang))
        return

    await message.answer(t("pay.receipt_accepted", lang))

    username_part = f" (@{message.from_user.username})" if message.from_user.username else ""
    item_line = (
        f"{request.subscription_months} oylik cheksiz obuna"
        if request.kind == PaymentKind.subscription
        else f"{request.credits} ta tekshiruv"
    )
    caption = (
        "💳 <b>Yangi to'lov so'rovi</b>\n\n"
        f"Foydalanuvchi: {message.from_user.full_name}{username_part}\n"
        f"Telegram ID: <code>{message.from_user.id}</code>\n"
        f"Miqdor: {item_line} — {format_som(request.amount)} so'm"
    )
    for admin_id in settings.admin_ids:
        try:
            await message.bot.send_photo(
                admin_id, file_id, caption=caption, reply_markup=payment_review_kb(request.id)
            )
        except Exception:
            # An admin may not have opened a chat with the bot yet, or blocked
            # it. A single failed notification must not break the flow.
            pass


@router.message(PaymentStates.awaiting_receipt, ~F.text.in_(MENU_BUTTON_TEXTS))
async def reject_non_photo_receipt(message: Message, lang: str) -> None:
    await message.answer(t("pay.send_photo_only", lang))


@router.callback_query(F.data.startswith(CB_PAY_APPROVE_PREFIX))
async def on_pay_approve(callback: CallbackQuery) -> None:
    await _decide(callback, approve=True, status_line="✅ Tasdiqlandi")


@router.callback_query(F.data.startswith(CB_PAY_REJECT_PREFIX))
async def on_pay_reject(callback: CallbackQuery) -> None:
    await _decide(callback, approve=False, status_line="⛔ Rad etildi")


async def _decide(callback: CallbackQuery, *, approve: bool, status_line: str) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("Sizda ruxsat yo'q.", show_alert=True)
        return

    prefix = CB_PAY_APPROVE_PREFIX if approve else CB_PAY_REJECT_PREFIX
    request_id = int(callback.data.removeprefix(prefix))

    async with get_session() as session:
        request, applied = await crud.decide_payment_request(
            session, request_id, approve=approve, admin_id=callback.from_user.id
        )
        # The notification below goes to the buyer, not the admin acting here
        # - fetch the buyer's own language, not this handler's injected `lang`.
        buyer = await crud.get_user(session, request.user_id) if request else None
        buyer_lang = buyer.language if buyer is not None else "uz"

    if request is None:
        await callback.answer("So'rov topilmadi.", show_alert=True)
        return
    if not applied:
        # Already decided - another admin's copy of this notification, or a
        # duplicate tap. Don't re-notify the buyer with a possibly
        # contradictory message.
        await callback.answer("Bu to'lov allaqachon ko'rib chiqilgan.", show_alert=True)
        await _clear_buttons(callback, status_line)
        return

    await _clear_buttons(callback, status_line)
    await callback.answer("Tasdiqlandi ✅" if approve else "Rad etildi")
    try:
        if approve:
            text = (
                t("pay.approved_subscription", buyer_lang, months=request.subscription_months)
                if request.kind == PaymentKind.subscription
                else t("pay.approved_credits", buyer_lang, credits=request.credits)
            )
        else:
            text = t("pay.rejected", buyer_lang)
        await callback.bot.send_message(request.user_id, text)
    except Exception:
        pass


async def _clear_buttons(callback: CallbackQuery, status_line: str) -> None:
    """Appends the decision to the admin's photo caption and removes the
    Tasdiqlash/Rad etish buttons, so a stale copy of this notification (this
    admin double-tapping, or a second admin acting on their own copy) can
    never be actioned twice."""
    original = callback.message.caption or ""
    if status_line in original:
        return
    try:
        await callback.message.edit_caption(caption=original + f"\n\n{status_line}", reply_markup=None)
    except Exception:
        pass
