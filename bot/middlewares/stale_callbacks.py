"""A button pressed while the bot was down - a deploy restarts it in ~15 s - reaches the bot once it is back,
when Telegram no longer takes an answer to that press: "query is too old and response timeout expired or
query ID is invalid". The answer only stops the button's spinner; what the handler does next (a new screen,
a reply) still works. Until 2026-11-15 that failed answer raised at the handler's first line and the press
was lost without a word; now a stale answer is skipped and the handler carries on.

Registered on the bot's session (bot/main.py), so it covers every `callback.answer()` in every handler.
Any other error - and any other method - is raised as before.
"""
from __future__ import annotations

import logging
from typing import Any

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery

logger = logging.getLogger(__name__)

STALE = ("query is too old", "query id is invalid")


class SkipStaleAnswers(BaseRequestMiddleware):
    async def __call__(self, make_request: Any, bot: Any, method: Any) -> Any:
        try:
            return await make_request(bot, method)
        except TelegramBadRequest as e:
            if isinstance(method, AnswerCallbackQuery) and any(s in str(e.message).lower() for s in STALE):
                logger.info("A button pressed while the bot was away: its late answer skipped, the press handled")
                return True
            raise
