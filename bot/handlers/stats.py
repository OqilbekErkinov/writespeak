"""/stats: an admin-only snapshot of today's activity and revenue - the
closest thing to a dashboard until a real one gets built."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from bot.config import settings
from bot.utils.quota import format_som
from db import crud
from db.database import get_session

router = Router(name="stats")


@router.message(Command("stats"))
async def show_stats(message: Message) -> None:
    if message.from_user.id not in settings.admin_ids:
        await message.answer("Bu buyruq faqat adminlar uchun.")
        return

    async with get_session() as session:
        s = await crud.get_admin_stats(session)

    text = (
        "📊 <b>Bugungi statistika</b>\n\n"
        f"🆕 Yangi foydalanuvchilar: <b>{s['new_users_today']}</b>\n"
        f"✍️ Writing tekshiruvlari: <b>{s['writing_today']}</b>\n"
        f"🎙 Speaking tekshiruvlari: <b>{s['speaking_today']}</b>\n"
        f"💰 Bugungi tushum: <b>{format_som(s['revenue_today'])} so'm</b>\n"
        f"⏳ Ko'rib chiqilmagan to'lovlar: <b>{s['pending_payments']}</b>\n\n"
        f"👥 Jami foydalanuvchilar: {s['total_users']}"
    )
    await message.answer(text)
