"""Small helpers for moving between inline-keyboard "screens" without
littering the chat with stale menus."""
from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message


async def remove_message(message: Message) -> None:
    """Deletes a menu message being navigated away from; falls back to just
    stripping its buttons when Telegram refuses the delete (e.g. a message
    older than 48h)."""
    try:
        await message.delete()
    except TelegramBadRequest:
        try:
            await message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass


async def strip_buttons(message: Message) -> None:
    try:
        await message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass


async def show_screen(
    callback: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup | None = None
) -> None:
    """Replaces the callback's message with a new screen: edited in place
    when it's a plain text message, otherwise (a photo/document can't be
    edited into text) removed and re-sent."""
    message = callback.message
    if getattr(message, "text", None) is not None:
        try:
            await message.edit_text(text, reply_markup=reply_markup)
            return
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                return
    await remove_message(message)
    await message.answer(text, reply_markup=reply_markup)
