"""Leaving whatever flow a student is in (the 2026-11-15 audit): /start, /menu and /cancel used to show the menu
but keep the FSM state - a student left at "send your answer" had their next "salom" graded and charged, one
left in the support chat had their next text sent to the admins. A check being graded (`processing`) is never
dropped: it finishes and clears its own state (bot/utils/check_guard.py owns it).
"""
from __future__ import annotations

from aiogram.fsm.context import FSMContext

from bot.states.speaking_states import SpeakingStates
from bot.states.writing_states import WritingStates

GRADING = {WritingStates.processing.state, SpeakingStates.processing.state}


async def leave_flow(state: FSMContext) -> bool:
    """Clears the student's flow unless a check of theirs is being graded. False when one is (kept)."""
    if await state.get_state() in GRADING:
        return False
    await state.clear()
    return True
