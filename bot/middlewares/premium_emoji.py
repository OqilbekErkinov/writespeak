"""Puts the admins' premium emoji (bot/utils/premium_emoji.py) into every message the bot sends or edits: the
HTML text or caption, and a button's icon - inline buttons and the main menu's (2026-11-22, the owner: "the menu
too"). Registered on the bot's session (bot/main.py, and the panel's bot - webapp/panel.py new_bot), so every
handler, reminder and broadcast gets them without a change.

Never makes a send fail: a send Telegram refuses with them goes again as it was; a message sent with no parse mode,
or with its own entities, is left alone. A main-menu button whose emoji became its icon sends only its words when
pressed; MenuPressText (below) gives the handlers its full text back. When Telegram takes the message but drops the
emoji (the bot's owner no longer has Telegram Premium), the admins' group is told once a day.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from aiogram.client.default import Default
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram import BaseMiddleware
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup

from bot.keyboards.main_menu_kb import MENU_BUTTON_TEXTS
from bot.utils import premium_emoji

logger = logging.getLogger(__name__)

FIELDS = (("text", "entities"), ("caption", "caption_entities"))
NOT_OURS = ("not modified", "chat not found", "message to edit not found", "message can't be edited",
            "query is too old", "message to delete not found")


def _parse_mode(bot: Any, method: Any) -> str | None:
    mode = getattr(method, "parse_mode", None)
    if isinstance(mode, Default):
        mode = getattr(bot.default, mode.name, None) if getattr(bot, "default", None) else None
    # the bot's default is the enum ParseMode.HTML: str() of it is "ParseMode.HTML", its value is "HTML" (2026-11-26:
    # every message sent with the default mode went with no premium emoji in its text)
    return getattr(mode, "value", mode)


def _buttons(markup: Any, emoji_map: dict[str, str]) -> tuple[Any, int]:
    """An inline keyboard's or the main menu's buttons: a leading mapped emoji becomes the button's premium icon.
    A reply button's text is what its press sends back - MenuPressText puts the emoji back on it."""
    if isinstance(markup, InlineKeyboardMarkup):
        kind, field, plain_only = InlineKeyboardButton, "inline_keyboard", False
    elif isinstance(markup, ReplyKeyboardMarkup):
        kind, field, plain_only = KeyboardButton, "keyboard", True
    else:
        return markup, 0
    rows, n = [], 0
    for row in getattr(markup, field):
        new_row = []
        for button in row:
            fields = button.model_dump(exclude_none=True)
            icon = None
            if not fields.get("icon_custom_emoji_id") and not (plain_only and set(fields) - {"text", "style"}):
                icon = premium_emoji.button_icon(button.text, emoji_map)
            if icon:
                n += 1
                button = kind(**{**fields, "text": icon[0], "icon_custom_emoji_id": icon[1]})
            new_row.append(button)
        rows.append(new_row)
    return (markup.model_copy(update={field: rows}) if n else markup), n


async def dress(bot: Any, method: Any) -> tuple[Any, int, int] | None:
    """`method` with the premium emoji put in: (the new method, how many in the text, how many on buttons), or
    None when nothing changes."""
    emoji_map = await premium_emoji.mapping()
    if not emoji_map:
        return None
    update: dict[str, Any] = {}
    in_text = 0
    for field, entities in FIELDS:
        value = getattr(method, field, None)
        if isinstance(value, str) and value and not getattr(method, entities, None) \
                and str(_parse_mode(bot, method) or "").lower() == "html":
            dressed, in_text = premium_emoji.apply(value, emoji_map)
            if in_text:
                update[field] = dressed
            break
    markup, on_buttons = _buttons(getattr(method, "reply_markup", None), emoji_map)
    if on_buttons:
        update["reply_markup"] = markup
    if not update:
        return None
    return method.model_copy(update=update), in_text, on_buttons


def _kept(result: Any) -> int:
    entities = (result.entities or []) + (result.caption_entities or [])
    return sum(1 for e in entities if e.type == "custom_emoji")


class PremiumEmoji(BaseRequestMiddleware):
    async def __call__(self, make_request: Any, bot: Any, method: Any) -> Any:
        try:
            dressed = await dress(bot, method)
        except Exception:  # noqa: BLE001 - the message goes plain
            logger.warning("Premium emoji not put in", exc_info=True)
            dressed = None
        if dressed is None:
            return await make_request(bot, method)
        new, in_text, _on_buttons = dressed
        try:
            result = await make_request(bot, new)
        except TelegramBadRequest as e:
            if any(s in str(e.message).lower() for s in NOT_OURS):
                raise
            logger.warning("Telegram refused a message with premium emoji (%s), sent plain", e.message)
            return await make_request(bot, method)
        if in_text and isinstance(result, Message) and not _kept(result):
            await _stripped()
        return result


_LEADING = re.compile(r"^[\W_]+")


def _words(text: str) -> str:
    """A menu button's words, without the emoji before them: "✍️ Writing" -> "Writing"."""
    return _LEADING.sub("", text).strip()


MENU_BY_WORDS = {_words(text): text for text in MENU_BUTTON_TEXTS}


class MenuPressText(BaseMiddleware):
    """A main-menu press whose emoji is the button's premium icon sends only the words ("Writing"); every handler
    knows the button by its full text ("✍️ Writing"), so it gets that back before any filter looks at it. A student
    typing the bare words ("Writing") gets the same section. Registered as the messages' outer middleware
    (bot/main.py)."""

    async def __call__(self, handler: Any, event: Any, data: dict) -> Any:
        text = getattr(event, "text", None)
        if isinstance(event, Message) and text and text not in MENU_BUTTON_TEXTS and event.chat.type == "private":
            full = MENU_BY_WORDS.get(_words(text))
            if full:
                event = event.model_copy(update={"text": full, "entities": None})
                if data.get("bot") is not None:
                    event = event.as_(data["bot"])
        return await handler(event, data)


async def _stripped() -> None:
    from bot.utils import crm  # noqa: PLC0415 - crm imports a lot; only on this rare path

    try:
        if await premium_emoji.stripped_today(crm.local_now().date().isoformat()):
            logger.warning("Telegram dropped the premium emoji from the bot's messages")
            await crm.post("errors", "⚠️ <b>Premium emoji ko'rinmay qoldi</b>\n\nTelegram botning xabarlaridagi "
                                     "premium emojilarni oddiy emojiga almashtiryapti. Ko'pincha sabab - botni "
                                     "@BotFather'da ochgan akkauntning Telegram Premium obunasi tugagan (yoki "
                                     "tanlangan emoji to'plami o'chirilgan - botda /emoji bilan yangilang). Premium "
                                     "qayta yoqilsa, o'zi tiklanadi; xabarlar oddiy emoji bilan borishda davom etadi.")
    except Exception:  # noqa: BLE001
        logger.warning("Premium emoji warning not sent", exc_info=True)
