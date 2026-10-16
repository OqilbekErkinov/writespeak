"""Practice Session: real exam questions imported from Telegram channels
(services/practice_import/, editable in the web admin panel) with
per-question completion checkmarks.

The list shows each question's topic, ✅ when done, 🔥 when it has been
reported from the exam many times, 🆕 when it is fresh (Writing: reported
in the last NEW_DAYS days; Speaking: the newest question season). Writing
lists can be narrowed to one question type (bar chart, map, opinion essay,
...), and "🎲" picks a random question not done yet.

Picking a question presets the prompt/question for the Writing or Speaking
FSM and jumps straight to answering, so the student doesn't have to retype
a prompt that's already on file - Writing Task 1 questions come with their
chart image, Speaking sets are asked one question at a time. After grading,
"➡️ Keyingi savol" moves on to the next question not done yet.
"""
from __future__ import annotations

import html
import logging
import random
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from bot.handlers.speaking import start_question_set
from bot.i18n import t
from bot.keyboards.main_menu_kb import BTN_PRACTICE
from bot.keyboards.nav_kb import back_kb
from bot.keyboards.practice_kb import (
    ALL_KINDS,
    CB_BACK_TO_LIST_PREFIX,
    CB_KINDS_PREFIX,
    CB_MENU,
    CB_MODULE_PREFIX,
    CB_NEXT_PREFIX,
    CB_NOOP,
    CB_PAGE_PREFIX,
    CB_QUESTION_PREFIX,
    CB_RANDOM_PREFIX,
    KIND_LABELS,
    MODULE_LABELS,
    QUESTIONS_PER_PAGE,
    kind_picker_kb,
    module_picker_kb,
    page_count,
    page_of,
    question_list_kb,
)
from bot.states.writing_states import WritingStates
from bot.utils.messages import remove_message, show_screen
from bot.utils.quota import check_quota, free_check_note, send_paywall
from db import crud
from db.database import get_session
from db.models import PracticeModule, PracticeQuestion
from services.ai.sample_bank import regex_subtype
from services.storage.file_storage import read_file

logger = logging.getLogger(__name__)
router = Router(name="practice")

WRITING_MODULES = {PracticeModule.writing_task1, PracticeModule.writing_task2}
SPEAKING_MODULES = {PracticeModule.speaking_part1, PracticeModule.speaking_part2, PracticeModule.speaking_part3}
SUBTYPE_TASKS = {PracticeModule.writing_task1: "task1_academic", PracticeModule.writing_task2: "task2"}

CAPTION_LIMIT = 1024  # Telegram's photo caption limit
HOT_TIMES = 3  # reported from the exam at least this often: 🔥
NEW_DAYS = 14  # Writing reported within this many days: 🆕
TITLE_WIDTH = 42


def question_kind(question: PracticeQuestion) -> str | None:
    """The Writing question type the list can be filtered by: the one the
    importer saw on the chart, else a guess from the wording."""
    task = SUBTYPE_TASKS.get(question.module)
    if task is None:
        return None
    return question.kind or regex_subtype(question.question_text, task)


def _title(question: PracticeQuestion) -> str:
    title = (question.topic or question.question_text).strip().splitlines()[0]
    title = title[:1].upper() + title[1:]
    if len(title) > TITLE_WIDTH:
        title = title[: TITLE_WIDTH - 1].rstrip() + "…"
    return html.escape(title, quote=False)


def _new_since(module: PracticeModule, questions: list[PracticeQuestion]) -> datetime | None:
    """Questions seen on or after this are 🆕."""
    if module in WRITING_MODULES:
        return datetime.now(timezone.utc) - timedelta(days=NEW_DAYS)
    seasons = [q.seen_at for q in questions if q.seen_at]
    return max(seasons) if seasons else None


async def _picker_screen(user_id: int, lang: str):
    async with get_session() as session:
        stats = await crud.get_practice_stats(session, user_id)
    return t("practice.choose_module", lang), module_picker_kb(lang, stats)


@router.message(F.text == BTN_PRACTICE)
async def open_practice_menu(message: Message, state: FSMContext, lang: str) -> None:
    await state.clear()  # abandon any in-progress Writing/Speaking flow
    text, kb = await _picker_screen(message.from_user.id, lang)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == CB_MENU)
async def back_to_modules(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    await state.clear()
    await show_screen(callback, *await _picker_screen(callback.from_user.id, lang))


@router.callback_query(F.data == CB_NOOP)
async def page_counter(callback: CallbackQuery) -> None:
    await callback.answer()


async def _load(module: PracticeModule, user_id: int, kind: str):
    """(questions of `kind` in list order, all the module's questions, ids done)."""
    async with get_session() as session:
        questions = await crud.get_practice_questions(session, module)
        completed_ids = await crud.get_completed_question_ids(session, user_id, module)
    shown = questions
    if kind != ALL_KINDS and module in KIND_LABELS:
        shown = [q for q in questions if question_kind(q) == kind]
    return shown, questions, completed_ids


async def _question_list_screen(
    module: PracticeModule,
    user_id: int,
    lang: str,
    page: int = 0,
    around_question_id: int | None = None,
    kind: str = ALL_KINDS,
):
    questions, everything, completed_ids = await _load(module, user_id, kind)
    if around_question_id is not None:
        page = page_of(questions, around_question_id)
    page = max(0, min(page, page_count(questions) - 1))
    kb = question_list_kb(module, questions, completed_ids, page, lang, kind)

    lines = [t("practice.module_header", lang, label=MODULE_LABELS[module], done=len(completed_ids), total=len(everything))]
    if kind != ALL_KINDS and kind in KIND_LABELS.get(module, {}):
        label = html.escape(KIND_LABELS[module][kind], quote=False)
        lines.append(t("practice.filter_line", lang, kind=label, count=len(questions)))
    if not questions:
        lines += ["", t("practice.no_questions", lang)]
        return "\n".join(lines), kb

    new_since = _new_since(module, everything)
    start = page * QUESTIONS_PER_PAGE
    items, any_hot, any_new = [], False, False
    for number, question in enumerate(questions[start : start + QUESTIONS_PER_PAGE], start=start + 1):
        hot = (question.times_seen or 1) >= HOT_TIMES
        new = new_since is not None and question.seen_at is not None and question.seen_at >= new_since
        any_hot, any_new = any_hot or hot, any_new or new
        marks = ("✅ " if question.id in completed_ids else "") + ("🔥 " if hot else "") + ("🆕 " if new else "")
        seen = f" ({question.times_seen}×)" if hot else ""
        items.append(f"{number}. {marks}{_title(question)}{seen}")
    lines += ["", *items]
    legend = []
    if any_hot:
        legend.append(t("practice.legend_hot", lang))
    if any_new:
        legend.append(t("practice.legend_new" if module in WRITING_MODULES else "practice.legend_season", lang))
    if legend:
        lines += ["", " · ".join(legend)]
    lines += ["", t("practice.pick_hint", lang)]
    return "\n".join(lines), kb


@router.callback_query(F.data.startswith(CB_MODULE_PREFIX))
async def choose_module(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    module = PracticeModule(callback.data.removeprefix(CB_MODULE_PREFIX))
    text, kb = await _question_list_screen(module, callback.from_user.id, lang)
    await show_screen(callback, text, kb)


@router.callback_query(F.data.startswith(CB_PAGE_PREFIX))
async def paginate(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    parts = callback.data.removeprefix(CB_PAGE_PREFIX).split(":")
    if len(parts) == 2:  # a list sent before question-type filters existed
        parts = [parts[0], ALL_KINDS, parts[1]]
    module_value, kind, page_str = parts
    module = PracticeModule(module_value)
    text, kb = await _question_list_screen(module, callback.from_user.id, lang, page=int(page_str), kind=kind)
    await show_screen(callback, text, kb)


@router.callback_query(F.data.startswith(CB_KINDS_PREFIX))
async def choose_kind(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    module = PracticeModule(callback.data.removeprefix(CB_KINDS_PREFIX))
    async with get_session() as session:
        questions = await crud.get_practice_questions(session, module)
    counts = Counter(question_kind(q) for q in questions)
    await show_screen(
        callback,
        t("practice.choose_kind", lang, label=MODULE_LABELS[module]),
        kind_picker_kb(module, dict(counts), lang),
    )


@router.callback_query(F.data.startswith(CB_RANDOM_PREFIX))
async def random_question(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    """A random question of the list being looked at, preferring ones not done yet."""
    await callback.answer()
    module_value, kind = callback.data.removeprefix(CB_RANDOM_PREFIX).split(":")
    questions, _, completed_ids = await _load(PracticeModule(module_value), callback.from_user.id, kind)
    fresh = [q for q in questions if q.id not in completed_ids]
    question = random.choice(fresh or questions) if questions else None
    await _start_practice_question(callback, state, question, lang)


@router.callback_query(F.data.startswith(CB_BACK_TO_LIST_PREFIX))
async def back_to_list(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    """From a practice question (or its result) back to the list page holding it."""
    await callback.answer()
    await state.clear()
    question_id = int(callback.data.removeprefix(CB_BACK_TO_LIST_PREFIX))
    async with get_session() as session:
        question = await crud.get_practice_question(session, question_id)
    if question is None:  # deleted from the admin panel meanwhile
        await show_screen(callback, *await _picker_screen(callback.from_user.id, lang))
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
        following = (
            await crud.get_next_practice_question(session, current, callback.from_user.id) if current else None
        )

    if current is None:
        await show_screen(callback, *await _picker_screen(callback.from_user.id, lang))
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

    quota = await check_quota(callback.from_user.id, question.module.value)
    if not quota.allowed:
        await send_paywall(callback.message, lang, quota)
        return
    spend_credit = quota.spend_credit

    # See the matching comment in bot/handlers/writing.py's choose_task.
    await state.clear()
    # The list (or previous result's buttons) gives way to the question itself.
    await remove_message(callback.message)
    message = callback.message
    module = question.module
    note = free_check_note(quota, lang)
    if note:
        await message.answer(note.strip())

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
            payer_id=quota.payer_id,
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
            payer_id=quota.payer_id,
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
