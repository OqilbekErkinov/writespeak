"""Practice Session: curated question bank (managed from the web admin
panel, webapp/main.py) with per-question completion checkmarks (Bosqich 7).
Picking a question presets the prompt/question for the Writing or Speaking
FSM and jumps straight to answering, so the student doesn't have to retype
a prompt that's already on file - Writing Task 1 questions come with their
chart image, Speaking sets are asked one question at a time. After grading,
"➡️ Keyingi savol" moves on to the next question in the same module.
"""
from __future__ import annotations

import html
import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from bot.handlers.speaking import start_question_set
from bot.i18n import t
from bot.keyboards.main_menu_kb import BTN_PRACTICE
from bot.keyboards.nav_kb import back_kb
from bot.keyboards.practice_kb import (
    CB_BACK_TO_LIST_PREFIX,
    CB_MENU,
    CB_MODULE_PREFIX,
    CB_NEXT_PREFIX,
    CB_PAGE_PREFIX,
    CB_QUESTION_PREFIX,
    MODULE_LABELS,
    module_picker_kb,
    page_of,
    question_list_kb,
)
from bot.states.writing_states import WritingStates
from bot.utils.messages import remove_message, show_screen
from bot.utils.quota import check_quota, send_paywall
from db import crud
from db.database import get_session
from db.models import PracticeModule, PracticeQuestion
from services.storage.file_storage import read_file

logger = logging.getLogger(__name__)
router = Router(name="practice")

WRITING_MODULES = {PracticeModule.writing_task1, PracticeModule.writing_task2}
SPEAKING_MODULES = {PracticeModule.speaking_part1, PracticeModule.speaking_part2, PracticeModule.speaking_part3}

CAPTION_LIMIT = 1024  # Telegram's photo caption limit


@router.message(F.text == BTN_PRACTICE)
async def open_practice_menu(message: Message, state: FSMContext, lang: str) -> None:
    await state.clear()  # abandon any in-progress Writing/Speaking flow
    await message.answer(t("practice.choose_module", lang), reply_markup=module_picker_kb(lang))


@router.callback_query(F.data == CB_MENU)
async def back_to_modules(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    await state.clear()
    await show_screen(callback, t("practice.choose_module", lang), module_picker_kb(lang))


async def _question_list_screen(
    module: PracticeModule, user_id: int, lang: str, page: int = 0, around_question_id: int | None = None
):
    async with get_session() as session:
        questions = await crud.get_practice_questions(session, module)
        completed_ids = await crud.get_completed_question_ids(session, user_id, module)
    if around_question_id is not None:
        page = page_of(questions, around_question_id)
    text = t(
        "practice.module_header",
        lang,
        label=MODULE_LABELS[module],
        done=len(completed_ids),
        total=len(questions),
    )
    if not questions:
        text += "\n\n" + t("practice.no_questions", lang)
    return text, question_list_kb(module, questions, completed_ids, page, lang)


@router.callback_query(F.data.startswith(CB_MODULE_PREFIX))
async def choose_module(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    module = PracticeModule(callback.data.removeprefix(CB_MODULE_PREFIX))
    text, kb = await _question_list_screen(module, callback.from_user.id, lang)
    await show_screen(callback, text, kb)


@router.callback_query(F.data.startswith(CB_PAGE_PREFIX))
async def paginate(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    module_value, page_str = callback.data.removeprefix(CB_PAGE_PREFIX).rsplit(":", 1)
    module = PracticeModule(module_value)
    text, kb = await _question_list_screen(module, callback.from_user.id, lang, page=int(page_str))
    await show_screen(callback, text, kb)


@router.callback_query(F.data.startswith(CB_BACK_TO_LIST_PREFIX))
async def back_to_list(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    """From a practice question (or its result) back to the list page holding it."""
    await callback.answer()
    await state.clear()
    question_id = int(callback.data.removeprefix(CB_BACK_TO_LIST_PREFIX))
    async with get_session() as session:
        question = await crud.get_practice_question(session, question_id)
    if question is None:  # deleted from the admin panel meanwhile
        await show_screen(callback, t("practice.choose_module", lang), module_picker_kb(lang))
        return
    text, kb = await _question_list_screen(
        question.module, callback.from_user.id, lang, around_question_id=question_id
    )
    await show_screen(callback, text, kb)


@router.callback_query(F.data.startswith(CB_QUESTION_PREFIX))
async def choose_question(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    question_id = int(callback.data.removeprefix(CB_QUESTION_PREFIX))
    async with get_session() as session:
        question = await crud.get_practice_question(session, question_id)
    await _start_practice_question(callback, state, question, lang)


@router.callback_query(F.data.startswith(CB_NEXT_PREFIX))
async def next_question(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    question_id = int(callback.data.removeprefix(CB_NEXT_PREFIX))
    async with get_session() as session:
        current = await crud.get_practice_question(session, question_id)
        following = await crud.get_next_practice_question(session, current) if current else None

    if current is None:
        await show_screen(callback, t("practice.choose_module", lang), module_picker_kb(lang))
        return
    if following is None:
        await state.clear()
        text, kb = await _question_list_screen(
            current.module, callback.from_user.id, lang, around_question_id=current.id
        )
        await show_screen(callback, t("practice.all_done", lang) + "\n\n" + text, kb)
        return
    await _start_practice_question(callback, state, following, lang)


async def _start_practice_question(
    callback: CallbackQuery, state: FSMContext, question: PracticeQuestion | None, lang: str
) -> None:
    if question is None:
        await callback.message.answer(t("practice.question_not_found", lang))
        return

    allowed, spend_credit = await check_quota(callback.from_user.id)
    if not allowed:
        await send_paywall(callback.message, lang)
        return

    # See the matching comment in bot/handlers/writing.py's choose_task.
    await state.clear()
    # The list (or previous result's buttons) gives way to the question itself.
    await remove_message(callback.message)
    message = callback.message
    module = question.module

    if module in WRITING_MODULES:
        task_type = module.value.removeprefix("writing_")  # "task1" | "task2"
        image_path = question.image_path if question.image_path and Path(question.image_path).exists() else None
        if question.image_path and image_path is None:
            logger.warning("Practice question %s image missing on disk: %s", question.id, question.image_path)
        await state.update_data(
            task_type=task_type,
            prompt_text=question.question_text,
            prompt_source="text",
            prompt_image_paths=[image_path] if image_path else [],
            practice_question_id=question.id,
            spend_credit=spend_credit,
        )
        await state.set_state(WritingStates.awaiting_answer)
        min_words = "150" if task_type == "task1" else "250"
        text = t(
            "practice.writing_question",
            lang,
            question=html.escape(question.question_text, quote=False),
            min_words=min_words,
        )
        kb = back_kb(lang, f"{CB_BACK_TO_LIST_PREFIX}{question.id}")
        if image_path is None:
            await message.answer(text, reply_markup=kb)
            return
        photo = BufferedInputFile(read_file(image_path), filename=Path(image_path).name)
        if len(text) <= CAPTION_LIMIT:
            await message.answer_photo(photo, caption=text, reply_markup=kb)
        else:
            await message.answer_photo(photo)
            await message.answer(text, reply_markup=kb)
        return

    if module in SPEAKING_MODULES:
        part = module.value.removeprefix("speaking_")  # "part1" | "part2" | "part3"
        await state.update_data(
            part=part,
            practice_question_id=question.id,
            spend_credit=spend_credit,
        )
        intro = None
        if question.topic:
            intro = t(
                "practice.speaking_topic",
                lang,
                label=MODULE_LABELS[module],
                topic=html.escape(question.topic, quote=False),
            )
        await start_question_set(message, state, question.question_text, lang, intro=intro)
