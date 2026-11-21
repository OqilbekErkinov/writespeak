"""Puts the admins' premium emoji (bot/utils/premium_emoji.py) into every message the bot sends or edits: the
HTML text or caption, and an inline button's icon. Registered on the bot's session (bot/main.py, and the panel's
bot - webapp/panel.py new_bot), so every handler, reminder and broadcast gets them without a change.

Never makes a send fail: a send Telegram refuses with them goes again as it was; a message sent with no parse mode,
or with its own entities, is left alone; a reply keyboard is left alone (its button's text is what the student's
press sends back, and the handlers match it). When Telegram takes the message but drops them (the bot's owner no
longer has Telegram Premium), the admins' group is told once a day.
"""
from __future__ import annotations

import logging
from typing import Any

from aiogram.client.default import Default
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.utils import premium_emoji

logger = logging.getLogger(__name__)

FIELDS = (("text", "entities"), ("caption", "caption_entities"))
NOT_OURS = ("not modified", "chat not found", "message to edit not found", "message can't be edited",
            "query is too old", "message to delete not found")


def _parse_mode(bot: Any, method: Any) -> str | None:
    mode = getattr(method, "parse_mode", None)
    if isinstance(mode, Default):
        mode = getattr(bot.default, mode.name, None) if getattr(bot, "default", None) else None
    return mode


def _buttons(markup: Any, emoji_map: dict[str, str]) -> tuple[Any, int]:
    if not isinstance(markup, InlineKeyboardMarkup):
        return markup, 0
    rows, n = [], 0
    for row in markup.inline_keyboard:
        new_row = []
        for button in row:
            icon = None if (button.model_extra or {}).get("icon_custom_emoji_id") else \
                premium_emoji.button_icon(button.text, emoji_map)
            if icon:
                n += 1
                button = InlineKeyboardButton(**{**button.model_dump(exclude_none=True), "text": icon[0],
                                                 "icon_custom_emoji_id": icon[1]})
            new_row.append(button)
        rows.append(new_row)
    return (InlineKeyboardMarkup(inline_keyboard=rows) if n else markup), n


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
