"""Bandzilla's mascot stickers in the chat (Brand Guidelines p. 49: a sticker
gives the feeling - Chomp when mistakes are found, Cool for Band 7+ - but
not on every message).

The static WEBP stickers are in branding/bandzilla/stickers/. Each is
uploaded once per bot and then sent by the file id Telegram gave it (kept in
Redis). Never raises: a sticker is decoration, a failure only gets logged.
"""
from __future__ import annotations

import logging
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile

from bot.utils.crm import redis

logger = logging.getLogger(__name__)

STICKER_DIR = Path(__file__).resolve().parents[2] / "branding" / "bandzilla" / "stickers"


def for_result(band: float, mistakes: int) -> str:
    """The sticker after a report: Band 7+ (Cool, its bubble saying the band
    itself - band7, band7_5 ... band9), mistakes eaten (Gotcha), nothing to
    eat (Yum). Band 7 is the brand's own sticker; 7.5-9 were drawn from it
    with the same bubble, tilt and Unbounded lettering."""
    if band >= 7:
        half = round(min(band, 9) * 2) / 2
        return "band" + f"{half:g}".replace(".", "_")
    return "gotcha" if mistakes else "yum"


async def send(bot: Bot, chat_id: int, name: str) -> None:
    path = STICKER_DIR / f"{name}.webp"
    if not path.is_file():
        return
    key = f"stickers:{bot.id}:{name}"
    try:
        file_id = await redis().get(key)
        if file_id:
            try:
                await bot.send_sticker(chat_id, file_id)
                return
            except TelegramBadRequest:
                await redis().delete(key)  # Telegram forgot it: upload again
        sent = await bot.send_sticker(chat_id, FSInputFile(path))
        if sent.sticker is not None:
            await redis().set(key, sent.sticker.file_id)
    except Exception as e:  # noqa: BLE001
        logger.info("Sticker %s to %s not sent: %r", name, chat_id, e)
