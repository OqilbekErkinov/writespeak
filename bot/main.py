"""Application entrypoint: builds the Bot/Dispatcher and starts polling."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage

from bot.config import settings
from db.database import init_models

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
# WeasyPrint lowers the fontTools logger to DEBUG while it subsets a report's
# fonts (weasyprint.logger.capture_logs), and those records reached this
# handler: hundreds of lines per report PDF, burying the lines that matter.
for _handler in logging.getLogger().handlers:
    _handler.setLevel(logging.INFO)
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
        check_retry,
        crm,
        group,
        group_assignments,
        language,
        main_menu,
        my_works,
        payments,
        practice,
        progress_quiz,
        share,
        speaking,
        start,
        stats,
        support,
        vocabulary,
        writing,
    )

    # The admins' group (bot/handlers/crm.py) is the only group the bot is in;
    # everything else is for private chats only, so nothing said in the group
    # is ever taken for a student's input.
    dp.include_router(crm.router)
    private = Router(name="private_chats")
    private.message.filter(F.chat.type == "private")
    private.include_router(progress_quiz.router)  # the waiting card's questions, in any state
    private.include_router(check_retry.router)  # "🔁 Try again" on a stopped check, in its `failed` state
    private.include_router(start.router)
    private.include_router(language.router)
    private.include_router(stats.router)
    private.include_router(payments.router)
    private.include_router(share.router)
    private.include_router(account.router)
    private.include_router(vocabulary.router)
    private.include_router(group.router)
    private.include_router(group_assignments.router)
    private.include_router(main_menu.router)
    private.include_router(support.router)
    private.include_router(writing.router)
    private.include_router(speaking.router)
    private.include_router(practice.router)
    private.include_router(my_works.router)
    dp.include_router(private)

    return dp


async def main() -> None:
    await init_models()

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = build_dispatcher()

    from bot.utils import check_guard, crm
    from bot.utils.checks_back import notify_back
    from bot.utils.group_scheduler import run_group_jobs
    from bot.utils.vocab_reminders import run_vocab_reminders

    logger.info("Starting Bandzilla bot (polling mode)")
    # The messages sent while the bot was down (a deploy) are handled now, not dropped: an essay or a voice
    # answer sent during a restart used to vanish without a reply (until 2026-11-15).
    await bot.delete_webhook(drop_pending_updates=False)
    crm.install_error_forwarding()
    # The checks a stopped bot left running: coins back, "🔁 Try again" on their cards (bot/utils/check_guard.py).
    try:
        await check_guard.recover(bot, dp.storage)
    except Exception:  # noqa: BLE001 - the bot still starts; the sweep settles them later
        logger.exception("Recovering the interrupted checks failed")

    async def drain_checks() -> None:
        # aiogram's shutdown event, before the bot session closes: a deploy (SIGTERM) waits for the checks
        # being graded instead of killing them - docker-compose.yml's stop_grace_period is longer than this
        await check_guard.drain()
        await crm.flush_spent()  # the last checks' OpenAI spend into the ledger before the process exits

    dp.shutdown.register(drain_checks)
    background = [
        asyncio.create_task(run_vocab_reminders(bot)),
        asyncio.create_task(run_group_jobs(bot)),
        asyncio.create_task(crm.run_sender(bot)),
        asyncio.create_task(notify_back(bot)),  # after a test window: "checks are back"
        asyncio.create_task(check_guard.run_sweeper(bot, dp.storage)),
        asyncio.create_task(crm.run_heartbeat()),  # deploy/watchdog/watchdog.sh watches it
    ]
    try:
        await dp.start_polling(bot)
    finally:
        for task in background:
            task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
