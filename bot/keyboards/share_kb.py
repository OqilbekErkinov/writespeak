"""The buttons under a finished PDF report (bot/handlers/share.py): the story card, and since 2026-11-16 (the owner)
our Telegram channel and Instagram - every report brings the student to them."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import settings
from bot.i18n import t

CB_SHARE_PREFIX = "share:"


def instagram_url() -> str:
    return f"https://www.instagram.com/{settings.instagram_username}/"


def channel_url() -> str:
    return f"https://t.me/{settings.telegram_channel}"


def social_row(lang: str = "uz") -> list[InlineKeyboardButton]:
    """📣 Telegram kanal · 📸 Instagram."""
    return [InlineKeyboardButton(text=t("social.telegram", lang), url=channel_url()),
            InlineKeyboardButton(text=t("social.instagram", lang), url=instagram_url())]


def share_card_kb(submission_type: str, submission_id: int, lang: str = "uz") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("share.button", lang),
                    callback_data=f"{CB_SHARE_PREFIX}{submission_type}:{submission_id}",
                )
            ],
            social_row(lang),
        ]
    )
