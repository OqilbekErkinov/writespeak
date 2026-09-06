"""Application entrypoint: builds the Bot/Dispatcher and starts polling."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage

from bot.config import settings
from db.database import init_models

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def build_dispatcher() -> Dispatcher:
    storage = RedisStorage.from_url(settings.redis_url)
    dp = Dispatcher(storage=storage)

    # Middlewares (order matters: access control must run before routers below).
    from bot.middlewares.access_control import AccessControlMiddleware

    dp.message.middleware(AccessControlMiddleware())
    dp.callback_query.middleware(AccessControlMiddleware())

    # Routers
    # NOTE: Mock Test (bot/handlers/mock_test.py, bot/utils/mock_test_flow.py)
    # is intentionally not registered here - built and DB-backed, just
    # switched off pending a later pass, per the user's request on
    # 2026-08-29. Re-enable by importing it and adding its router back below.
    from bot.handlers import (
        account,
        group,
        language,
        main_menu,
        my_works,
        payments,
        practice,
        share,
        speaking,
        start,
        stats,
        vocabulary,
        writing,
    )

    dp.include_router(start.router)
    dp.include_router(language.router)
    dp.include_router(stats.router)
    dp.include_router(payments.router)
    dp.include_router(share.router)
    dp.include_router(account.router)
    dp.include_router(vocabulary.router)
    dp.include_router(group.router)
    dp.include_router(main_menu.router)
    dp.include_router(writing.router)
    dp.include_router(speaking.router)
    dp.include_router(practice.router)
    dp.include_router(my_works.router)

    return dp


async def main() -> None:
    await init_models()

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = build_dispatcher()

    logger.info("Starting WriteSpeak bot (polling mode)")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
