"""💬 Admin bilan aloqa: a student writes to the admins - text, a photo, a
file, a voice message - and it lands in the admins' group's 💬 Murojaatlar
topic (bot/utils/crm_events.py). An admin's reply there comes back to this
chat (bot/handlers/crm.py) with a "✍️ Javob yozish" button. The student
stays in this mode until they leave it with ⬅️ or a menu button, so a
question can take several messages.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.i18n import t
from bot.keyboards.main_menu_kb import BTN_SUPPORT_EN, BTN_SUPPORT_RU, BTN_SUPPORT_UZ, MENU_BUTTON_TEXTS
from bot.keyboards.nav_kb import back_kb
from bot.keyboards.support_kb import CB_SUPPORT_WRITE
from bot.states.support_states import SupportStates
from bot.utils import crm, crm_events

router = Router(name="support")

MAX_PER_HOUR = 30  # messages a student may send the admins in an hour


@router.message(F.text.in_({BTN_SUPPORT_UZ, BTN_SUPPORT_RU, BTN_SUPPORT_EN}))
async def open_support(message: Message, state: FSMContext, lang: str) -> None:
    await state.clear()
    await _ask(message, state, lang)


@router.callback_query(F.data == CB_SUPPORT_WRITE)
async def write_again(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    await state.clear()
    await _ask(callback.message, state, lang)


async def _ask(message: Message, state: FSMContext, lang: str) -> None:
    if not crm.ready():
        await message.answer(t("support.unavailable", lang))
        return
    await state.set_state(SupportStates.writing)
    await message.answer(t("support.ask", lang), reply_markup=back_kb(lang))


@router.message(SupportStates.writing, ~F.text.in_(MENU_BUTTON_TEXTS), ~F.text.startswith("/"))
async def receive(message: Message, state: FSMContext, lang: str) -> None:
    if not crm.ready():
        await state.clear()
        await message.answer(t("support.unavailable", lang))
        return
    r = crm.redis()
    key = f"crm:support_rate:{message.from_user.id}:{crm.local_now():%Y%m%d%H}"
    if await r.incr(key) > MAX_PER_HOUR:
        await message.answer(t("support.too_many", lang))
        return
    await r.expire(key, 3600)
    await crm_events.support_message(message)
    if message.media_group_id:
        # An album arrives as one message per photo: one confirmation for it.
        if (await state.get_data()).get("album") == message.media_group_id:
            return
        await state.update_data(album=message.media_group_id)
    await message.answer(t("support.sent", lang), reply_markup=back_kb(lang))
