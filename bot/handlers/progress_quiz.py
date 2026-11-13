"""The waiting card's one-tap vocabulary questions (bot/utils/progress.py): an answer shows at once on the card,
with the running score; a card that has ended (the report came, the bot restarted) just says so."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from bot.utils import progress as live

router = Router(name="progress_quiz")

DONE = {"uz": "Tekshiruv tugadi 🦖", "ru": "Проверка завершена 🦖", "en": "The check is done 🦖"}


@router.callback_query(F.data.startswith(live.CB_PREFIX))
async def quiz_answer(callback: CallbackQuery, lang: str = "uz") -> None:
    card = live.find(callback.message.chat.id, callback.message.message_id) if callback.message else None
    try:
        question, option = (int(x) for x in callback.data.removeprefix(live.CB_PREFIX).split(":"))
    except ValueError:
        await callback.answer()
        return
    if card is None:
        await callback.answer(DONE.get(lang, DONE["uz"]))
        return
    ok = card.answer(question, option)
    await callback.answer("✅" if ok else ("💡" if ok is False else ""))
