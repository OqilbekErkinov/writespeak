"""The opening-day message (the user, 2026-11-15): once the test window is off and checks are open to everyone,
every user hears it once.

- From the new bot (@bandzilla_bot): launch.open - "Bandzilla is working again".
- A user who never started the new bot gets launch.moved from the previous bot instead (@writespeak_bot,
  settings.moved_bot_token): "we've moved to @bandzilla_bot, checks are open again", with the button to the
  new bot. A bot may not write first, so the new bot's refusal is what tells the two apart.

Who got it is kept in Redis (launch:told), so a second run only reaches the rest. The users the panel's
"checks are back" message already reached (bot/utils/checks_back.py, checks:back_told) are skipped too. It
refuses to run while the test window is on.

    docker compose exec bot python -m bot.launch          how many would get it (sends nothing)
    docker compose exec bot python -m bot.launch --send   sends
"""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import select

from bot.config import settings
from bot.i18n import t
from bot.utils.coins import TYPICAL_CHECK
from db.database import get_session
from db.models import User, UserStatus

logger = logging.getLogger(__name__)

TOLD = "launch:told"
PAUSE = 0.06  # ~16 messages a second, under Telegram's 30


async def _deliver(bot: Bot, user_id: int, text: str, markup=None) -> bool:
    """True when it reached the user; False when this bot may not write to them (never started, blocked,
    deleted account)."""
    for _ in range(3):
        try:
            await bot.send_message(user_id, text, reply_markup=markup)
            return True
        except TelegramRetryAfter as e:  # flood control: wait as Telegram says, then again
            await asyncio.sleep(e.retry_after + 1)
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            logger.info("Launch message to %s via bot %s not sent: %s", user_id, bot.id, e.message)
            return False
    return False


async def run(send: bool, new_bot: Bot, old_bot: Bot | None) -> dict:
    from bot.moved import _keyboard
    from bot.utils import checks_back, runtime
    from bot.utils.crm import redis

    if await runtime.window_on():
        return {"refused": "Test rejimi yoqilgan: avval uni o'chiring (panel → Sozlamalar), keyin qayta ishga tushiring."}
    r = redis()
    async with get_session() as session:
        users = (await session.execute(
            select(User.id, User.language).where(User.status != UserStatus.denied).order_by(User.id))).all()
    done = await r.smembers(TOLD) | await r.smembers(checks_back.TOLD_KEY)
    todo = [(uid, lang or "uz") for uid, lang in users if str(uid) not in done]
    stats = {"users": len(users), "already": len(users) - len(todo), "todo": len(todo),
             "new_bot": 0, "old_bot": 0, "missed": 0, "old_bot_ready": old_bot is not None}
    if not send:
        return stats
    for uid, lang in todo:
        if await r.sismember(TOLD, uid):  # another run got there first
            continue
        if await _deliver(new_bot, uid, t("launch.open", lang, price=TYPICAL_CHECK)):
            stats["new_bot"] += 1
        elif old_bot is not None and await _deliver(
                old_bot, uid, t("launch.moved", lang, bot=settings.telegram_bot_username), _keyboard(lang)):
            stats["old_bot"] += 1
        else:
            stats["missed"] += 1
            await asyncio.sleep(PAUSE)
            continue
        await r.sadd(TOLD, uid)
        await asyncio.sleep(PAUSE)
    logger.info("Launch message: %s", stats)
    return stats


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    send = "--send" in sys.argv
    new_bot = Bot(settings.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    old_bot = (Bot(settings.moved_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
               if settings.moved_bot_token else None)
    from bot.utils.crm import redis

    try:
        stats = await run(send, new_bot, old_bot)
    finally:
        await new_bot.session.close()
        if old_bot is not None:
            await old_bot.session.close()
        await redis().aclose()  # before the loop ends: no "Event loop is closed" noise under the result
    if "refused" in stats:
        print(stats["refused"])
        sys.exit(1)
    if send:
        print(f"Yuborildi: yangi botdan {stats['new_bot']}, eski botdan {stats['old_bot']}, yetmadi {stats['missed']} "
              f"(oldin olganlar {stats['already']}, jami {stats['users']}).")
    else:
        print(f"Sinov (hech narsa yuborilmadi): jami {stats['users']} foydalanuvchi, {stats['todo']} tasiga boradi, "
              f"{stats['already']} tasi oldin olgan. Eski bot {'ulangan' if stats['old_bot_ready'] else 'ULANMAGAN'}. "
              f"Yuborish: --send")


if __name__ == "__main__":
    asyncio.run(main())
