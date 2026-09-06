"""Keyboards for the vocabulary flashcard review flow (bot/handlers/vocabulary.py)."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.i18n import t

CB_VOCAB_START = "vocab:start"
CB_VOCAB_REVEAL = "vocab:reveal"
CB_VOCAB_KNOW = "vocab:know"
CB_VOCAB_AGAIN = "vocab:again"


def start_review_kb(lang: str = "uz") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t("vocab.btn_practice", lang), callback_data=CB_VOCAB_START)]]
    )


def reveal_kb(lang: str = "uz") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t("vocab.btn_reveal", lang), callback_data=CB_VOCAB_REVEAL)]]
    )


def know_again_kb(lang: str = "uz") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("vocab.btn_know", lang), callback_data=CB_VOCAB_KNOW),
                InlineKeyboardButton(text=t("vocab.btn_again", lang), callback_data=CB_VOCAB_AGAIN),
            ]
        ]
    )
