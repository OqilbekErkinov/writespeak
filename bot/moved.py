"""The previous bot (@writespeak_bot) since the move to the new one
(settings.telegram_bot_username, 2026-10-17): every message and button there
gets a "we've moved" note with a button to the new bot - a /start link's
payload (a referral, a group or center invite) carried over - so old links,
QR codes and chats keep leading somewhere. Accounts are the same: the new
bot finds a user by their Telegram id, with their credits and history.

    python -m bot.moved              the `moved_bot` service (docker-compose.yml)
    python -m bot.moved --announce   the one-time announcement to every user

The announcement remembers who got it (Redis), so running it again only
reaches the rest. With no settings.moved_bot_token the service just idles.
"""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from bot.config import settings
from bot.i18n import t
from db import crud
from db.database import get_session
from db.models import User

logger = logging.getLogger(__name__)
router = Router(name="moved")
router.message.filter(F.chat.type == "private")

ANNOUNCED = "moved:announced"  # Redis set of the users the announcement reached
SEND_PAUSE = 0.05


def _keyboard(lang: str, payload: str | None = None) -> InlineKeyboardMarkup:
    url = f"https://t.me/{settings.telegram_bot_username}" + (f"?start={payload}" if payload else "")
    button = InlineKeyboardButton(text=t("moved.button", lang, bot=settings.telegram_bot_username), url=url)
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


async def _lang(tg_user) -> str:
    try:
        async with get_session() as session:
            user = await crud.get_user(session, tg_user.id)
        if user is not None and user.language:
            return user.language
    except Exception:  # noqa: BLE001 - the note goes out whatever happens
        logger.warning("Language lookup failed for %s", tg_user.id, exc_info=True)
    return "ru" if (tg_user.language_code or "").startswith("ru") else "uz"


@router.message(CommandStart())
async def start(message: Message, command: CommandObject) -> None:
    lang = await _lang(message.from_user)
    await message.answer(
        t("moved.text", lang, bot=settings.telegram_bot_username), reply_markup=_keyboard(lang, command.args)
    )


@router.message()
async def anything(message: Message) -> None:
    lang = await _lang(message.from_user)
    await message.answer(t("moved.text", lang, bot=settings.telegram_bot_username), reply_markup=_keyboard(lang))


@router.callback_query()
async def old_button(callback: CallbackQuery) -> None:
    lang = await _lang(callback.from_user)
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer(
            t("moved.text", lang, bot=settings.telegram_bot_username), reply_markup=_keyboard(lang)
        )


async def announce(bot: Bot) -> None:
    from bot.utils.crm import redis

    async with get_session() as session:
        users = (await session.execute(select(User.id, User.language))).all()
    r = redis()
    sent = skipped = failed = 0
    for user_id, lang in users:
        if await r.sismember(ANNOUNCED, user_id):
            skipped += 1
            continue
        lang = lang or "uz"
        try:
            await bot.send_message(user_id, t("moved.text", lang, bot=settings.telegram_bot_username),
                                   reply_markup=_keyboard(lang))
            await r.sadd(ANNOUNCED, user_id)
            sent += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            failed += 1
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            logger.info("Announcement to %s not sent: %s", user_id, e)
            failed += 1
        await asyncio.sleep(SEND_PAUSE)
    print(f"announcement: sent {sent}, already had it {skipped}, not delivered {failed} (of {len(users)})")
    await r.aclose()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    if not settings.moved_bot_token:
        logger.info("No MOVED_BOT_TOKEN - nothing to do")
        while True:
            await asyncio.sleep(3600)
    bot = Bot(settings.moved_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    if "--announce" in sys.argv:
        try:
            await announce(bot)
        finally:
            await bot.session.close()
        return
    me = await bot.get_me()
    logger.info("@%s now points everyone to @%s", me.username, settings.telegram_bot_username)
    dp = Dispatcher()
    dp.include_router(router)
    # Messages sent while it was switching over are answered too.
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
