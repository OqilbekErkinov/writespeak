"""Coin top-up flow: pick a pack (bot/utils/coins.py) -> pay by card -> send
a receipt (a screenshot, or since 2026-11-16 an image file or a PDF) -> an
admin approves/rejects it against the receipt. Teachers and center owners
also see the bigger center packs. The buyer's first approved purchase pays
the referral bonus to them and their inviter.

This is where bot/handlers/admin.py's old Approve/Deny-for-registration
pattern moved to once the admin approval gate for new users was removed
(see bot/middlewares/access_control.py) - same shape (inline buttons, a
race guard so two admins/duplicate taps can't double-apply a decision),
different domain. Since 2026-11-16 ⛔ first asks the admin why (the buyer
is told the reason, with "📸 Chekni qayta yuborish" and the support chat
under it), and a decision clears every admin's copy of the card, not only
the tapped one.

The admin-facing messages here (payment review caption/buttons, permission
errors) are deliberately left in Uzbek, not run through bot/i18n.py, since
admins are Uzbek speakers in this deployment; only the buyer-facing text is
bilingual. Notifications sent TO the buyer (in `_decide`) use the buyer's
own stored language, not the acting admin's - those are two different
people and `lang` injected by the middleware here reflects the admin.
"""
from __future__ import annotations

import html
import json
import logging
from pathlib import PurePath

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Document, InlineKeyboardMarkup, Message

from bot.config import settings
from bot.i18n import t
from bot.keyboards.main_menu_kb import MENU_BUTTON_TEXTS, main_menu_kb
from bot.keyboards.account_kb import gift_packs_kb
from bot.keyboards.payment_kb import (
    CB_BUY_PREFIX,
    CB_BUY_SUB_PREFIX,
    CB_GIFT_BUY_PREFIX,
    CB_PAY_APPROVE_PREFIX,
    CB_PAY_CANCEL_PREFIX,
    CB_PAY_NOOP,
    CB_PAY_REASON_BACK_PREFIX,
    CB_PAY_REASON_PREFIX,
    CB_PAY_REJECT_PREFIX,
    CB_PAY_RESEND_PREFIX,
    REJECT_REASONS,
    buy_coins_kb,
    gift_share_kb,
    payment_review_kb,
    receipt_cancel_kb,
    reject_reasons_kb,
    rejected_kb,
)
from bot.states.payment_states import PaymentStates
from bot.utils import crm, crm_events, panel_access, stickers
from bot.utils.coins import CENTER_PACKS, COIN_PACKS, pack_price
from bot.utils.gifts import gift_link
from bot.utils.quota import format_som
from db import crud
from db.database import get_session
from db.models import PaymentKind, PaymentRequest, PaymentStatus, User, UserRole

logger = logging.getLogger(__name__)
router = Router(name="payments")

# A receipt sent as a file (2026-11-16): bank apps often share an image file or a PDF instead of a photo.
RECEIPT_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "application/pdf"}
RECEIPT_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".pdf"}  # a file Telegram gave no type
MAX_RECEIPT_BYTES = 10 * 1024 * 1024

# Every admin's copy of a payment's review card (the caption, and [chat id, message id] of each), so a decision
# clears them all - in the bot or in the web panel (2026-11-16).
REVIEW_KEY = "pay:review:{id}"
REVIEW_TTL = 14 * 86400


def _is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


def sees_center_packs(user: User | None) -> bool:
    """Teachers (center owners are teachers too) and admins pay for students
    and see the center packs."""
    # an admin's own row may say "student" - settings.admin_ids decides who is one
    return user is not None and (user.role in (UserRole.teacher, UserRole.admin) or user.id in settings.admin_ids)


def _request_id(data: str, prefix: str) -> int | None:
    try:
        return int(data.removeprefix(prefix))
    except ValueError:
        return None


@router.callback_query(F.data == CB_PAY_NOOP)
async def center_packs_heading(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith(CB_BUY_PREFIX))
async def choose_pack(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    await _open_pack(callback, state, lang, coins=int(callback.data.removeprefix(CB_BUY_PREFIX)))


@router.callback_query(F.data.startswith(CB_GIFT_BUY_PREFIX))
async def choose_gift_pack(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    """A students' pack bought as a gift (Hisobim -> "🎁 Sovg'a qilish"): paid
    like any pack; its approval makes a gift link (bot/utils/gifts.py) instead
    of adding the coins to the buyer."""
    await callback.answer()
    await _open_pack(
        callback, state, lang, coins=int(callback.data.removeprefix(CB_GIFT_BUY_PREFIX)), kind=PaymentKind.gift
    )


async def _open_pack(
    callback: CallbackQuery,
    state: FSMContext,
    lang: str,
    *,
    coins: int,
    kind: PaymentKind = PaymentKind.credits,
    amount: int | None = None,
) -> None:
    """A pack chosen: its payment request, then the card number and "send the
    receipt here". Also "📸 Chekni qayta yuborish" under a rejection
    (2026-11-16): the same pack as a new request, at the `amount` already
    transferred. A pack no longer sold (a button from before coins: credits
    1/3/5/10), or a center pack for a student, gets the packs instead."""
    async with get_session() as session:
        if kind == PaymentKind.gift:
            price = dict(COIN_PACKS).get(coins)  # a gift is one of the students' packs
            if price is None:
                await callback.message.answer(t("gift.intro", lang), reply_markup=gift_packs_kb(lang))
                return
        else:
            user = await crud.get_user(session, callback.from_user.id)
            price = pack_price(coins)
            if price is None or (coins in dict(CENTER_PACKS) and not sees_center_packs(user)):
                await callback.message.answer(
                    t("pay.choose_pack", lang), reply_markup=buy_coins_kb(lang, sees_center_packs(user))
                )
                return
        request = await crud.create_payment_request(
            session, user_id=callback.from_user.id, coins=coins, amount=amount or price, kind=kind
        )
    await _start_receipt_flow(callback.message, state, request, lang)


@router.callback_query(F.data.startswith(CB_BUY_SUB_PREFIX))
async def choose_subscription(callback: CallbackQuery, lang: str) -> None:
    """The unlimited subscription is no longer sold (2026-10-17) - an old
    button gets the coin packs instead."""
    await callback.answer()
    async with get_session() as session:
        user = await crud.get_user(session, callback.from_user.id)
    await callback.message.answer(
        t("pay.subscription_retired", lang), reply_markup=buy_coins_kb(lang, sees_center_packs(user))
    )


@router.callback_query(F.data.startswith(CB_PAY_RESEND_PREFIX))
async def resend_receipt(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    """"📸 Chekni qayta yuborish" under a rejection (2026-11-16: until then a
    photo sent after "send it again" reached nothing - the receipt step had
    ended with the first one): a new request for the same pack - same coins,
    amount and kind, a center pack too - and the receipt step, exactly as
    choosing that pack starts it. The rejected request stays as it was."""
    await callback.answer()
    request_id = _request_id(callback.data, CB_PAY_RESEND_PREFIX)
    async with get_session() as session:
        old = await session.get(PaymentRequest, request_id) if request_id is not None else None
        user = await crud.get_user(session, callback.from_user.id)
    if old is None or old.user_id != callback.from_user.id or old.status != PaymentStatus.rejected:
        await callback.message.answer(t("nav.stale", lang), reply_markup=main_menu_kb(lang))
        return
    if old.kind == PaymentKind.subscription:  # no longer sold: the coin packs instead
        await callback.message.answer(
            t("pay.subscription_retired", lang), reply_markup=buy_coins_kb(lang, sees_center_packs(user))
        )
        return
    await _open_pack(callback, state, lang, coins=old.coins, kind=old.kind, amount=old.amount)


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
        ),
        reply_markup=receipt_cancel_kb(lang, request.id),
    )


@router.callback_query(F.data.startswith(CB_PAY_CANCEL_PREFIX))
async def cancel_payment(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    """The buyer gives up before sending a receipt: the request goes, the flow ends."""
    await callback.answer()
    try:
        request_id = int(callback.data.removeprefix(CB_PAY_CANCEL_PREFIX))
    except ValueError:
        return
    async with get_session() as session:
        await crud.cancel_payment_request(session, request_id, callback.from_user.id)
    if (await state.get_data()).get("payment_request_id") == request_id:
        await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.message.answer(t("pay.cancelled", lang), reply_markup=main_menu_kb(lang))


def _is_receipt_file(document: Document) -> bool:
    """An image file or a PDF - by its type, or by its name when Telegram gave none."""
    mime = (document.mime_type or "").lower()
    if mime in RECEIPT_TYPES:
        return True
    if mime and mime != "application/octet-stream":
        return False
    return PurePath(document.file_name or "").suffix.lower() in RECEIPT_EXTENSIONS


@router.message(PaymentStates.awaiting_receipt, F.photo)
@router.message(PaymentStates.awaiting_receipt, F.document)
async def receive_receipt(message: Message, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    request_id = data.get("payment_request_id")
    if not request_id:
        await message.answer(t("pay.choose_amount_first", lang))
        return

    document = message.document
    if document is not None:
        # A file (2026-11-16): an image file or a PDF up to 10 MB, kept like a photo (its file_id; its
        # file_unique_id for the "used before" flag); anything else is asked for again.
        if not _is_receipt_file(document):
            await message.answer(t("pay.send_photo_only", lang))
            return
        if (document.file_size or 0) > MAX_RECEIPT_BYTES:
            await message.answer(t("pay.file_too_big", lang))
            return
        file_id, unique_id = document.file_id, document.file_unique_id
    else:
        file_id, unique_id = message.photo[-1].file_id, message.photo[-1].file_unique_id
    async with get_session() as session:
        request = await crud.set_payment_receipt(session, request_id, file_id, unique_id)
        # the very same receipt sent for another payment: flagged on the admins' card (2026-11-15)
        twins = await crud.same_receipt(session, request) if request is not None else []

    await state.clear()
    if request is None:
        await message.answer(t("pay.error_retry", lang))
        return

    await message.answer(t("pay.receipt_accepted", lang))

    username_part = f" (@{message.from_user.username})" if message.from_user.username else ""
    if request.kind == PaymentKind.subscription:
        item_line = f"{request.subscription_months} oylik cheksiz obuna"
    elif request.kind == PaymentKind.gift:
        item_line = f"🎁 {request.coins} coin — sovg'a (tasdiqlansa, xaridorga sovg'a havolasi boradi)"
    else:
        item_line = f"{request.coins} coin"
    caption = (
        "💳 <b>Yangi to'lov so'rovi</b>\n\n"
        # escaped (2026-11-16): a "<" or "&" in a name broke the caption, and no admin got the receipt
        f"Foydalanuvchi: {html.escape(message.from_user.full_name, quote=False)}{username_part}\n"
        f"Telegram ID: <code>{message.from_user.id}</code>\n"
        f"Miqdor: {item_line} — {format_som(request.amount)} so'm"
    )
    if twins:
        caption += "\n\n⚠️ <b>DIQQAT: bu chek rasmi avval ishlatilgan</b> — " + ", ".join(
            f"#{p.id} ({'tasdiqlangan' if p.status == PaymentStatus.approved else 'kutmoqda'})" for p in twins)
    # a file goes to the admins as a file, with the same caption and buttons
    send = message.bot.send_document if document is not None else message.bot.send_photo
    cards: list[list[int]] = []
    for admin_id in settings.admin_ids:
        if not await panel_access.telegram_may(admin_id, "payments"):
            continue  # linked to a panel login without 💳 To'lovlar (bot/utils/panel_access.py)
        try:
            sent = await send(admin_id, file_id, caption=caption, reply_markup=payment_review_kb(request.id))
            cards.append([admin_id, sent.message_id])
        except Exception:
            # An admin may not have opened a chat with the bot yet, or blocked
            # it. A single failed notification must not break the flow.
            pass
    await _remember_review_cards(request.id, caption, cards)
    await crm_events.payment_receipt(
        request.id, file_message=(message.chat.id, message.message_id) if document is not None else None
    )


@router.message(PaymentStates.awaiting_receipt, ~F.text.in_(MENU_BUTTON_TEXTS), ~F.text.startswith("/"))
async def reject_non_photo_receipt(message: Message, lang: str) -> None:
    """Text, a sticker, a voice message... at the receipt step. A command (/help, /support, /menu) goes on to
    its own handler (2026-11-16: it got "send a photo" here, this router being ahead of theirs)."""
    await message.answer(t("pay.send_photo_only", lang))


# --- the admins' review card ----------------------------------------------------------------------


async def _may_review(callback: CallbackQuery) -> bool:
    if not _is_admin(callback.from_user.id) or not await panel_access.telegram_may(callback.from_user.id, "payments"):
        await callback.answer("Sizda ruxsat yo'q.", show_alert=True)
        return False
    return True


@router.callback_query(F.data.startswith(CB_PAY_APPROVE_PREFIX))
async def on_pay_approve(callback: CallbackQuery) -> None:
    await _decide(callback, _request_id(callback.data, CB_PAY_APPROVE_PREFIX), approve=True)


@router.callback_query(F.data.startswith(CB_PAY_REJECT_PREFIX))
async def on_pay_reject(callback: CallbackQuery) -> None:
    """⛔: the reasons first, on this copy of the card (2026-11-16) - the buyer is told the one picked."""
    if not await _may_review(callback):
        return
    request = await _pending_or_alert(callback, _request_id(callback.data, CB_PAY_REJECT_PREFIX))
    if request is None:
        return
    await callback.answer()
    await _set_buttons(callback, reject_reasons_kb(request.id))


@router.callback_query(F.data.startswith(CB_PAY_REASON_BACK_PREFIX))
async def on_reject_back(callback: CallbackQuery) -> None:
    """↩️ Orqaga from the reasons: ✅/⛔ again."""
    if not await _may_review(callback):
        return
    request = await _pending_or_alert(callback, _request_id(callback.data, CB_PAY_REASON_BACK_PREFIX))
    if request is None:
        return
    await callback.answer()
    await _set_buttons(callback, payment_review_kb(request.id))


@router.callback_query(F.data.startswith(CB_PAY_REASON_PREFIX))
async def on_reject_reason(callback: CallbackQuery) -> None:
    raw_id, _, reason = callback.data.removeprefix(CB_PAY_REASON_PREFIX).partition(":")
    if reason not in REJECT_REASONS or not raw_id.isdigit():
        await callback.answer()
        return
    await _decide(callback, int(raw_id), approve=False, reason=reason)


async def _pending_or_alert(callback: CallbackQuery, request_id: int | None) -> PaymentRequest | None:
    """The request while it waits for a decision; else the admin is told (and a decided one's card cleared)."""
    async with get_session() as session:
        request = await session.get(PaymentRequest, request_id) if request_id is not None else None
    if request is None:
        await callback.answer("So'rov topilmadi.", show_alert=True)
        return None
    if request.status != PaymentStatus.pending:
        await callback.answer("Bu to'lov allaqachon ko'rib chiqilgan.", show_alert=True)
        await _clear_buttons(callback.bot, callback.message, decision_line(request))
        return None
    return request


async def _set_buttons(callback: CallbackQuery, markup: InlineKeyboardMarkup) -> None:
    try:
        await callback.message.edit_reply_markup(reply_markup=markup)
    except Exception:  # noqa: BLE001 - "not modified" (a double tap), a message too old to edit
        pass


async def _decide(callback: CallbackQuery, request_id: int | None, *, approve: bool, reason: str | None = None) -> None:
    if not await _may_review(callback):
        return
    if request_id is None:
        await callback.answer("So'rov topilmadi.", show_alert=True)
        return

    async with get_session() as session:
        decision = await crud.decide_payment_request(
            session, request_id, approve=approve, admin_id=callback.from_user.id,
            referral_coins=settings.referral_coins, reject_reason=reason,
        )
        request, applied = decision.request, decision.applied

    if request is None:
        await callback.answer("So'rov topilmadi.", show_alert=True)
        return
    if not applied:
        # Already decided - another admin's copy of this notification, or a
        # duplicate tap. Don't re-notify the buyer with a possibly
        # contradictory message; this copy shows what was decided.
        await callback.answer("Bu to'lov allaqachon ko'rib chiqilgan.", show_alert=True)
        await _clear_buttons(callback.bot, callback.message, decision_line(request))
        return

    await clear_review_cards(
        callback.bot, request.id, decision_line(request, callback.from_user.full_name), tapped=callback.message
    )
    await callback.answer("Tasdiqlandi ✅" if approve else "Rad etildi")
    await crm_events.payment_decided(request.id)
    await notify_decision(callback.bot, decision)


def decision_line(request: PaymentRequest, by: str | None = None) -> str:
    """The decision as added under each admin's copy of the card (HTML): what, by whom, and a rejection's
    reason."""
    line = "✅ Tasdiqlandi" if request.status == PaymentStatus.approved else "⛔ Rad etildi"
    if by:
        line += f" — {html.escape(by, quote=False)}"
    if request.status == PaymentStatus.rejected and request.reject_reason and request.reject_reason != "none":
        line += f" · Sabab: {html.escape(REJECT_REASONS.get(request.reject_reason, request.reject_reason), quote=False)}"
    return line


async def _remember_review_cards(request_id: int, caption: str, cards: list[list[int]]) -> None:
    if not cards:
        return
    try:
        await crm.redis().set(REVIEW_KEY.format(id=request_id), json.dumps({"caption": caption, "msgs": cards},
                                                                             ensure_ascii=False), ex=REVIEW_TTL)
    except Exception as e:  # noqa: BLE001 - Redis away: only the tapped copy gets cleared, as before
        logger.warning("Payment #%s: the admins' card ids not kept (%r)", request_id, e)


async def clear_review_cards(bot, request_id: int, status_line: str, tapped: Message | None = None) -> None:
    """Every admin's copy of the review card gets the decision and loses its
    buttons (2026-11-16: only the tapped copy did - the other admins kept a
    live ✅/⛔ on a payment already decided). Called in the bot and after a
    decision in the web panel (webapp/main.py); never raises. `tapped`: the
    copy the decision came from - a card sent before the ids were kept is
    still cleared."""
    cleared: set[tuple[int, int]] = set()
    try:
        raw = await crm.redis().get(REVIEW_KEY.format(id=request_id))
        saved = json.loads(raw) if raw else {}
        caption = f"{saved.get('caption', '')}\n\n{status_line}"
        for chat_id, message_id in saved.get("msgs") or []:
            try:
                await bot.edit_message_caption(chat_id=chat_id, message_id=message_id, caption=caption,
                                               reply_markup=None)
            except Exception as e:  # noqa: BLE001 - deleted by that admin, "not modified", ...
                logger.info("Payment #%s: admin %s's card not edited (%r)", request_id, chat_id, e)
            cleared.add((int(chat_id), int(message_id)))
    except Exception as e:  # noqa: BLE001 - Redis away: the tapped copy below is still cleared
        logger.warning("Payment #%s: the admins' cards not cleared (%r)", request_id, e)
    if tapped is not None and (tapped.chat.id, tapped.message_id) not in cleared:
        await _clear_buttons(bot, tapped, status_line)


async def _clear_buttons(bot, message, status_line: str) -> None:
    """Appends the decision to this copy of the card and removes its buttons,
    so a stale copy (this admin double-tapping, a second admin acting on
    their own copy) can never be actioned twice. The caption is rebuilt from
    its entities (html_text, 2026-11-16: the plain caption re-read as HTML
    lost its bold, and a "<" or "&" in a name made the edit fail); a message
    whose content Telegram no longer gives (too old) just loses its buttons."""
    if message is None:
        return
    try:
        if getattr(message, "caption", None):
            original = message.html_text
            if status_line in original:
                return
            await bot.edit_message_caption(chat_id=message.chat.id, message_id=message.message_id,
                                           caption=f"{original}\n\n{status_line}", reply_markup=None)
        else:
            await bot.edit_message_reply_markup(chat_id=message.chat.id, message_id=message.message_id,
                                                reply_markup=None)
    except Exception:  # noqa: BLE001
        pass


# --- the buyer is told ----------------------------------------------------------------------------


async def approval_keyboard(decision: crud.PaymentDecision, lang: str) -> InlineKeyboardMarkup | None:
    """The buttons under an approval message - built only here (a gift's
    share button today), so another button joins in one place."""
    if decision.request.kind == PaymentKind.gift and decision.gift is not None:
        return gift_share_kb(gift_link(decision.gift.code), decision.gift.coins, lang)
    # a check that stopped for want of coins (bot/utils/check_guard.py park(), 2026-11-16): "🔁 Continue my
    # check" right under the approval - the answer is not sent again
    from bot.utils import check_guard  # noqa: PLC0415 - check_guard imports the payments keyboards' world

    return await check_guard.parked_keyboard(decision.request.user_id, lang)


def rejection_text(request: PaymentRequest, lang: str) -> str:
    """"⛔ Not approved. Reason: ..." (no reason for "Sababsiz" or an old rejection), and what to do now."""
    reason = request.reject_reason
    if reason in REJECT_REASONS and reason != "none":
        text = t("pay.rejected_reason", lang, reason=t(f"pay.reason_{reason}", lang))
    else:
        text = t("pay.rejected", lang)
    return text + "\n\n" + t("pay.rejected_hint", lang)


async def notify_decision(bot, decision: crud.PaymentDecision) -> None:
    """Tells the buyer (and, with a referral bonus, their inviter) - in the
    bot or after a decision in the web panel."""
    request = decision.request
    async with get_session() as session:
        buyer = await crud.get_user(session, request.user_id)
        referrer = await crud.get_user(session, decision.referrer_id) if decision.referrer_id else None
    buyer_lang = buyer.language if buyer is not None else "uz"
    try:
        if request.status == PaymentStatus.approved:
            if request.kind == PaymentKind.subscription:
                text = t("pay.approved_subscription", buyer_lang, months=request.subscription_months)
            elif request.kind == PaymentKind.gift and decision.gift is not None:
                # the coins wait behind a one-time link for the buyer to send on
                text = t("gift.ready", buyer_lang, coins=decision.gift.coins, link=gift_link(decision.gift.code))
            else:
                text = t("pay.approved_coins", buyer_lang, coins=request.coins, balance=buyer.coin_balance if buyer else 0)
            if referrer is not None and request.kind != PaymentKind.subscription:
                text += "\n\n" + t("pay.referral_bonus_buyer", buyer_lang, coins=settings.referral_coins)
            markup = await approval_keyboard(decision, buyer_lang)
        else:
            # the reason, "send the receipt again" and the support chat (2026-11-16)
            text = rejection_text(request, buyer_lang)
            markup = rejected_kb(buyer_lang, request.id)
        await bot.send_message(request.user_id, text, reply_markup=markup)
    except Exception as e:  # noqa: BLE001 - blocked the bot, ...
        logger.info("Payment decision message to %s not sent: %r", request.user_id, e)
    if request.status == PaymentStatus.approved:
        await stickers.send(bot, request.user_id, "rahmat")  # thanks (Love), Brand Guidelines p. 26
    if referrer is not None:
        try:
            await bot.send_message(
                referrer.id,
                t("pay.referral_bonus_referrer", referrer.language or "uz", coins=settings.referral_coins,
                  balance=referrer.coin_balance),
            )
        except Exception:
            pass
