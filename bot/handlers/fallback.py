"""The last router: what no other handler took (the 2026-11-15 audit - until then such a message or button got
no reply at all: a second essay sent while one was graded, "ha" typed under "Is this right?", an old button
from a flow left long ago only spun). Each gets a short answer that says what to do now, by the state the
student is in: a check being graded, a check that stopped, a step waiting for a button, or no flow at all.
"""
from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.i18n import t
from bot.keyboards.main_menu_kb import main_menu_kb
from bot.states.speaking_states import SpeakingStates
from bot.states.writing_states import WritingStates
from bot.utils.crm import redis
from bot.utils.flow import GRADING

router = Router(name="fallback")

FAILED = {WritingStates.failed.state, SpeakingStates.failed.state}


@router.callback_query()
async def stale_button(callback: CallbackQuery, lang: str) -> None:
    await callback.answer(t("nav.stale", lang), show_alert=False)


@router.message()
async def anything_else(message: Message, state: FSMContext, lang: str) -> None:
    if message.media_group_id:
        # an album: one answer for the whole of it, not one per picture
        try:
            if not await redis().set(f"fallback:album:{message.media_group_id}", "1", nx=True, ex=120):
                return
        except Exception:  # noqa: BLE001
            pass
    current = await state.get_state()
    if current in GRADING:
        await message.answer(t("check.in_progress", lang))
    elif current in FAILED:
        await message.answer(t("check.failed_hint", lang))
    elif current is not None:
        await message.answer(t("flow.use_buttons", lang))
    else:
        await message.answer(t("flow.menu_hint", lang), reply_markup=main_menu_kb(lang))
