""""🔁 Try again" on a check that stopped (bot/utils/check_guard.py, the 2026-11-15 audit): it failed, it was
interrupted by a restart, or it could not start (another check running, coins short). Its input is still in the
student's FSM data (the module's `failed` state, with the stopped check's token), so it is graded again from
the last step whose result is kept - nothing to send again. An old button (the data gone, another flow
started) says so instead of doing nothing.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from bot.handlers import speaking, writing
from bot.i18n import t
from bot.states.speaking_states import SpeakingStates
from bot.states.writing_states import WritingStates
from bot.utils import check_guard as guard
from bot.utils import runtime
from bot.utils.messages import strip_buttons

router = Router(name="check_retry")


@router.callback_query(F.data.startswith(guard.CB_RETRY))
async def try_again(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    token = callback.data.removeprefix(guard.CB_RETRY)
    user_id = callback.from_user.id
    current = await state.get_state()
    module = {WritingStates.failed.state: writing, SpeakingStates.failed.state: speaking}.get(current)
    if module is None or (await state.get_data()).get("check_token") != token:
        await callback.answer(t("check.retry_gone", lang), show_alert=True)
        await strip_buttons(callback.message)
        return
    if await runtime.checks_paused_for(user_id):  # OpenAI's credit ran out meanwhile, or a test window
        await callback.answer()
        await callback.message.answer(t("quota.paused", lang))
        return
    processing = WritingStates.processing if module is writing else SpeakingStates.processing
    async with module._user_locks[user_id]:  # a double tap: one retry
        if await state.get_state() != current:
            await callback.answer()
            return
        await state.set_state(processing)
    await callback.answer()
    await strip_buttons(callback.message)
    await module.retry(callback.message, state, lang)
