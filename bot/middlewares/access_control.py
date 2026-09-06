"""Gatekeeper middleware: only `approved` users (and configured admins) may
reach any handler other than /start. Applied to both message and
callback_query pipelines in bot/main.py. Also injects `lang` (the user's
stored UI language, "uz"/"ru" - see bot/i18n.py) into every handler's data,
so handlers never need to fetch the user row again just for that.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.config import settings
from db import crud
from db.database import get_session
from db.models import UserStatus

PENDING_TEXT = "⏳ Hisobingiz hali admin tomonidan tasdiqlanmagan. Iltimos, kuting."
DENIED_TEXT = "⛔ Sizning so'rovingiz rad etilgan. Savollar bo'lsa, admin bilan bog'laning."
UNKNOWN_TEXT = "Botdan foydalanish uchun avval /start buyrug'ini yuboring."


class AccessControlMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        tg_user = event.from_user
        if tg_user is None or tg_user.is_bot:
            data["lang"] = "uz"
            return await handler(event, data)

        is_start = isinstance(event, Message) and (event.text or "").startswith("/start")

        async with get_session() as session:
            if tg_user.id in settings.admin_ids:
                # Admins are auto-approved so they're never blocked by their own gate.
                admin = await crud.get_or_create_admin(
                    session, tg_user.id, tg_user.username, tg_user.full_name
                )
                data["lang"] = admin.language
                return await handler(event, data)

            user = await crud.get_user(session, tg_user.id)

        data["lang"] = user.language if user is not None else "uz"

        if is_start:
            # start.py owns messaging for every status (new/pending/denied/approved).
            return await handler(event, data)

        if user is None:
            return await self._block(event, UNKNOWN_TEXT)
        if user.status == UserStatus.pending:
            return await self._block(event, PENDING_TEXT)
        if user.status == UserStatus.denied:
            return await self._block(event, DENIED_TEXT)

        return await handler(event, data)

    @staticmethod
    async def _block(event: TelegramObject, text: str) -> None:
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        elif isinstance(event, Message):
            await event.answer(text)
