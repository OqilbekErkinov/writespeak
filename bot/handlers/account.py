"""Hisobim / Профиль: the student's profile hub - remaining free checks,
credit balance, subscription status, referral link, weakest scoring area
across their most recent work, and a way to switch UI language.
"""
from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import Message

from bot.config import settings
from bot.i18n import t
from bot.keyboards.lang_kb import CB_LANG_CHANGE_PREFIX, lang_picker_kb
from bot.keyboards.main_menu_kb import BTN_ACCOUNT_RU, BTN_ACCOUNT_UZ
from bot.keyboards.payment_kb import buy_credits_kb
from bot.utils.quota import format_som
from db import crud
from db.crud import CRITERIA_LABELS, CRITERIA_TIPS
from db.database import get_session

router = Router(name="account")

RECENT_WORKS_FOR_WEAKNESS = 5


@router.message(F.text.in_({BTN_ACCOUNT_UZ, BTN_ACCOUNT_RU}))
async def show_account(message: Message, lang: str) -> None:
    user_id = message.from_user.id

    async with get_session() as session:
        used_today = await crud.get_daily_submission_count(session, user_id)
        user = await crud.get_user(session, user_id)
        referral_count = await crud.get_referral_count(session, user_id)
        vocab_total, vocab_learned = await crud.get_vocabulary_counts(session, user_id)
        criteria_averages = await crud.get_criteria_averages(
            session, user_id, limit=RECENT_WORKS_FOR_WEAKNESS
        )

    credit_balance = user.credit_balance if user is not None else 0
    remaining_free = max(0, settings.free_daily_limit - used_today)

    lines = [t("account.title", lang), ""]

    now = datetime.now(timezone.utc)
    if user is not None and user.subscription_expires_at and user.subscription_expires_at > now:
        days_left = (user.subscription_expires_at - now).days
        lines.append(t("account.subscription_active", lang, days=days_left))
    else:
        lines.append(t("account.free_remaining", lang, remaining=remaining_free, total=settings.free_daily_limit))
        lines.append(t("account.credit_balance", lang, credits=credit_balance))

    lines.append(t("account.vocab_progress", lang, learned=vocab_learned, total=vocab_total))

    if criteria_averages:
        weakest_key = min(criteria_averages, key=criteria_averages.get)
        weakest_label = CRITERIA_LABELS.get(weakest_key, weakest_key)
        tip = CRITERIA_TIPS.get(weakest_key, "")
        lines.append(
            t(
                "account.weakest",
                lang,
                label=weakest_label,
                avg=criteria_averages[weakest_key],
                tip=tip,
            )
        )

    lines.append("")
    lines.append(t("account.price_line", lang, price=format_som(settings.price_per_check)))
    lines.append("")
    lines.append(
        t(
            "account.referral",
            lang,
            count=referral_count,
            link=f"https://t.me/{settings.telegram_bot_username}?start=ref_{user_id}",
        )
    )

    await message.answer("\n".join(lines), reply_markup=buy_credits_kb(lang))
    await message.answer(
        t("account.language_button", lang), reply_markup=lang_picker_kb(CB_LANG_CHANGE_PREFIX)
    )
