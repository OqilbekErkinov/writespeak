"""Practice Session: curated question bank with per-question completion
checkmarks (Bosqich 7). Picking a question presets the prompt/question for
the Writing or Speaking FSM and jumps straight to `awaiting_answer`, so the
student doesn't have to retype a prompt that's already on file.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.i18n import t
from bot.keyboards.main_menu_kb import BTN_PRACTICE
from bot.keyboards.practice_kb import (
    CB_MODULE_PREFIX,
    CB_PAGE_PREFIX,
    CB_QUESTION_PREFIX,
    MODULE_LABELS,
    module_picker_kb,
    question_list_kb,
)
from bot.states.speaking_states import SpeakingStates
from bot.states.writing_states import WritingStates
from bot.utils.quota import check_quota, send_paywall
from db import crud
from db.database import get_session
from db.models import PracticeModule

router = Router(name="practice")

WRITING_MODULES = {PracticeModule.writing_task1, PracticeModule.writing_task2}
SPEAKING_MODULES = {PracticeModule.speaking_part1, PracticeModule.speaking_part2, PracticeModule.speaking_part3}


@router.message(F.text == BTN_PRACTICE)
async def open_practice_menu(message: Message, state: FSMContext, lang: str) -> None:
    await state.clear()  # abandon any in-progress Writing/Speaking flow
    await message.answer(t("practice.choose_module", lang), reply_markup=module_picker_kb())


async def _show_question_list(module: PracticeModule, user_id: int, page: int, lang: str):
    async with get_session() as session:
        questions = await crud.get_practice_questions(session, module)
        completed_ids = await crud.get_completed_question_ids(session, user_id, module)
    text = t(
        "practice.module_header",
        lang,
        label=MODULE_LABELS[module],
        done=len(completed_ids),
        total=len(questions),
    )
    return text, question_list_kb(module, questions, completed_ids, page)


@router.callback_query(F.data.startswith(CB_MODULE_PREFIX))
async def choose_module(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    module = PracticeModule(callback.data.removeprefix(CB_MODULE_PREFIX))
    text, kb = await _show_question_list(module, callback.from_user.id, page=0, lang=lang)
    await callback.message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith(CB_PAGE_PREFIX))
async def paginate(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    module_value, page_str = callback.data.removeprefix(CB_PAGE_PREFIX).rsplit(":", 1)
    module = PracticeModule(module_value)
    text, kb = await _show_question_list(module, callback.from_user.id, page=int(page_str), lang=lang)
    await callback.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data.startswith(CB_QUESTION_PREFIX))
async def choose_question(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    question_id = int(callback.data.removeprefix(CB_QUESTION_PREFIX))

    allowed, spend_credit = await check_quota(callback.from_user.id)
    if not allowed:
        await send_paywall(callback.message, lang)
        return

    async with get_session() as session:
        question = await crud.get_practice_question(session, question_id)

    if question is None:
        await callback.message.answer(t("practice.question_not_found", lang))
        return

    # See the matching comment in bot/handlers/writing.py's choose_task.
    await state.clear()
    module = question.module

    if module in WRITING_MODULES:
        task_type = module.value.removeprefix("writing_")  # "task1" | "task2"
        await state.update_data(
            task_type=task_type,
            prompt_text=question.question_text,
            prompt_source="text",
            practice_question_id=question.id,
            spend_credit=spend_credit,
        )
        await state.set_state(WritingStates.awaiting_answer)
        min_words = "150" if task_type == "task1" else "250"
        await callback.message.answer(
            t("practice.writing_question", lang, question=question.question_text, min_words=min_words)
        )
        return

    if module in SPEAKING_MODULES:
        part = module.value.removeprefix("speaking_")  # "part1" | "part2" | "part3"
        await state.update_data(
            part=part,
            question_text=question.question_text,
            practice_question_id=question.id,
            spend_credit=spend_credit,
        )
        await state.set_state(SpeakingStates.awaiting_answer)
        await callback.message.answer(
            t("practice.speaking_question", lang, question=question.question_text)
        )
