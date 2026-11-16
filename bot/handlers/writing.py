"""Writing module: Task 1 (Report) / Task 2 (Essay).

Flow: pick task -> send prompt (any format) -> [confirm OCR/parse if needed]
-> send answer (any format) -> [confirm] -> grading -> PDF feedback report
-> "check again" / "next question" buttons (instead of dropping the student
back at the main menu). Also entered directly from Practice
(bot/handlers/practice.py), which presets task_type/prompt_text/
practice_question_id (+ the Task 1 chart, if any) and jumps straight to
`awaiting_answer`. Every step has a "⬅️ Orqaga" button. And from the smart
entry (bot/handlers/fallback.py, 2026-11-16): an essay typed outside any
flow is kept ("kept_answer") while the question is asked, then graded as the
answer without asking for it again (start_task, _ask_for_answer).

A Task 1 chart is read once into a data sheet before grading (services/ai/
visual_reading.py) and every call uses its figures. A chart the vision model
can't open or read at all gets the student asked for it again - their
answer kept, nothing charged - instead of a failed check.

`lang` (bot/i18n.py) is only auto-injected by AccessControlMiddleware into
top-level handlers (choose_task/receive_prompt/receive_answer/confirm_or_edit)
- the internal helpers below (_receive_input/_advance/_run_grading) take it
as an explicit parameter instead, since they're plain function calls within
the same turn, not separately-dispatched events.
"""
from __future__ import annotations

import asyncio
import difflib
import html
import logging
import time
from collections import defaultdict
from pathlib import Path

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from bot.config import settings
from bot.i18n import t
from bot.keyboards.confirm_kb import CB_CONFIRM, CB_EDIT, confirm_edit_kb
from bot.keyboards.group_kb import CB_MY_ASSIGNMENTS, assignment_after_kb
from bot.keyboards.main_menu_kb import BTN_WRITING, MENU_BUTTON_TEXTS
from bot.keyboards.nav_kb import CB_BACK_MAIN, back_kb, check_again_kb
from bot.keyboards.practice_kb import CB_BACK_TO_LIST_PREFIX, after_result_kb
from bot.keyboards.share_kb import share_card_kb
from bot.keyboards.writing_task_kb import (
    CB_NO_CHART,
    CB_CHECK_AS_IS,
    CB_LENGTH_OK,
    CB_RECHECK_NEW,
    CB_RECHECK_OLD,
    CB_SWITCH_SECTION,
    cut_start_kb,
    length_kb,
    recheck_kb,
    send_prompt_kb,
    wrong_section_kb,
    CB_TASK1,
    CB_TASK2,
    CB_WRITING_MENU,
    TASK_CALLBACKS,
    chart_kb,
    writing_task_kb,
)
from bot.states.writing_states import WritingStates
from bot.handlers import share  # the story card after a report (2026-11-16)
from bot.utils import check_guard as guard
from bot.utils import crm_events, nudges, runtime, stickers
from bot.utils import progress as live
from bot.utils.input_extraction import read_images, read_text_input
from bot.utils.messages import show_screen, strip_buttons
from bot.utils.mock_test_flow import record_mock_test_part
from bot.utils.report_names import report_filename, with_ai_note
from bot.utils.coins import writing_kind
from bot.utils.quota import check_quota, free_check_note, payment_of, send_paywall
from db import crud
from db.database import get_session
from db.models import SourceType
from services.ai import chart_keys, marking_config, usage
from services.ai.essay_marking import PASTE_NOTICE, canon, clean_pasted, count_words, starts_mid_word, wrong_section
from services.ai.openai_client import is_image_error
from services.ai.sample_bank import build_model_answer, needs_from_feedback
from services.ai.schemas import VocabularyItem
from services.ai.visual_reading import ChartImageRejected, judge_ready, sheet_text
from services.ai.vocab_uz import uzbek_meanings
from services.ai.writing_grader import grade_writing
from services.pdf.report_builder import build_progress, build_writing_report_pdf
from services.storage.file_storage import image_mime, read_file, save_report

logger = logging.getLogger(__name__)
router = Router(name="writing")

MIN_ANSWER_WORDS = {"task1": 150, "task2": 250}
RESUBMISSION_SIMILARITY = 0.9
# A typed answer under this share of the minimum is asked about before it is graded (2026-11-15: an essay sent
# paragraph by paragraph had its first 60 words graded at full price, the rest dropped) - and so is one this
# long, which Telegram may have split into several messages.
SHORT_SHARE = 0.5
SPLIT_CHARS = 3900

# One student's steps into grading are taken one at a time: a double tap on "✅", or two messages arriving
# together, must not both start a check (and race on the same FSM data) - the speaking module's pattern.
_user_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


async def _claim(user_id: int, state: FSMContext, *expected: State) -> bool:
    """Moves the flow from one of `expected` to `processing` - for exactly one of several taps."""
    async with _user_locks[user_id]:
        if await state.get_state() not in {s.state for s in expected}:
            return False
        await state.set_state(WritingStates.processing)
        return True

MAX_PROMPT_IMAGES = 3

# Shown in the report (English, like the rest of it).
CHART_NOT_CHECKED = "Chart not available — figures were not checked."
CHART_PARTLY_CHECKED = ("Some figures on the chart could not be read with certainty — your figures for them were "
                        "not checked.")


@router.message(F.text == BTN_WRITING)
async def open_writing_menu(message: Message, state: FSMContext, lang: str) -> None:
    await state.clear()  # abandon any in-progress flow so stale FSM data can't leak into a new one
    await message.answer(t("writing.choose_type", lang), reply_markup=writing_task_kb(lang))


@router.callback_query(F.data == CB_WRITING_MENU)
async def back_to_writing_menu(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    await state.clear()
    await show_screen(callback, t("writing.choose_type", lang), writing_task_kb(lang))


@router.callback_query(F.data.in_({CB_TASK1, CB_TASK2}))
async def choose_task(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    await start_task(callback, state, "task1" if callback.data == CB_TASK1 else "task2", lang)


async def start_task(callback: CallbackQuery, state: FSMContext, task_type: str, lang: str,
                     kept_answer: str | None = None, note: str = "") -> bool:
    """The task's "send the question" step, in place of the callback's message. Also the smart entry's hand-off
    (bot/handlers/fallback.py, 2026-11-16): `kept_answer` - an essay typed outside any flow - becomes the answer
    as soon as the question is in (_ask_for_answer), never asked for again; `note` (HTML, ending in a blank
    line) heads the step. False when the check can't start now (the paywall or the pause note was sent)."""
    quota = await check_quota(callback.from_user.id, writing_kind(task_type))
    if not quota.allowed:
        await send_paywall(callback.message, lang, quota)
        return False

    # Defends against a stale Task1/Task2 button from an old message being
    # tapped while a Mock Test (bot/utils/mock_test_flow.py) is in progress -
    # without this, a leftover mock_session_id in state would make this
    # one-off check get wrongly recorded as a mock test part. A no-op in the
    # normal flow, since open_writing_menu already cleared state.
    await state.clear()
    await state.update_data(task_type=task_type, spend_credit=quota.spend_credit, payer_id=quota.payer_id,
                            **({"kept_answer": kept_answer} if kept_answer else {}))
    await state.set_state(WritingStates.awaiting_prompt)
    # a ready question from Practice instead (2026-11-16) - not when an answer is kept: it has its own question
    markup = back_kb(lang, CB_WRITING_MENU) if kept_answer else send_prompt_kb(lang, task_type, CB_WRITING_MENU)
    await show_screen(callback, free_check_note(quota, lang) + note + t("writing.send_prompt", lang), markup)
    return True


@router.message(WritingStates.awaiting_prompt, ~F.text.in_(MENU_BUTTON_TEXTS))
async def receive_prompt(message: Message, state: FSMContext, lang: str) -> None:
    async with _user_locks[message.from_user.id]:
        if await state.get_state() != WritingStates.awaiting_prompt.state:
            return  # a second message that came together with the first: the first one is used
        go = await _receive_input(message, state, field="prompt", lang=lang)
    if go:  # an answer kept by the smart entry is graded as soon as its question is in
        await _run_grading(message, state, lang)


@router.message(WritingStates.awaiting_answer, ~F.text.in_(MENU_BUTTON_TEXTS))
async def receive_answer(message: Message, state: FSMContext, lang: str) -> None:
    async with _user_locks[message.from_user.id]:
        current = await state.get_state()
        if current == WritingStates.confirming_length.state and message.text:
            # the next part of a long text Telegram split, arriving while the first was handled
            await _add_more(message, state, lang)
            return
        if current != WritingStates.awaiting_answer.state:
            return
        go = await _receive_input(message, state, field="answer", lang=lang)
    if go:
        await _run_grading(message, state, lang)


def _back_target(data: dict, field: str) -> str:
    """Where "⬅️ Orqaga" leads from the prompt/answer step."""
    if data.get("mock_session_id"):
        return CB_BACK_MAIN
    if field == "prompt":
        return CB_WRITING_MENU
    if data.get("practice_question_id"):
        return f"{CB_BACK_TO_LIST_PREFIX}{data['practice_question_id']}"
    if data.get("assignment_part_id"):
        return CB_MY_ASSIGNMENTS
    return TASK_CALLBACKS[data["task_type"]]  # re-asks for the prompt


async def _receive_input(message: Message, state: FSMContext, field: str, lang: str) -> bool:
    """Reads the prompt or the answer (any format). True when the answer is ready to be graded - the state is
    `processing` then, and the caller grades it (outside its lock)."""
    task1_question = field == "prompt" and (await state.get_data()).get("task_type") == "task1"
    # A photo of a Task 1 task: only its wording is read as text - the chart's
    # own figures come from the chart reading, never from OCR.
    extracted = await read_text_input(message, lang, ocr_mode="writing_task1_question" if task1_question else "verbatim")
    if extracted is None:
        return False
    if extracted.cost:
        # reading a photo/file costs too: it is added to the check's cost
        earlier = (await state.get_data()).get("input_cost")
        await state.update_data(input_cost=usage.merge(earlier, extracted.cost))

    file_path = extracted.file_paths[0] if extracted.file_paths else None
    if field == "prompt":
        # Kept for Task 1, so the grader sees the actual chart, not just its OCR'd
        # labels - also when the student retypes the question after "Edit".
        data = await state.get_data()
        kept = data.get("prompt_image_paths") if data.get("_editing") == "prompt" else None
        await state.update_data(prompt_image_paths=extracted.image_paths or kept or [], _editing=None)

    if extracted.source == SourceType.text:
        await state.update_data(
            **{
                f"{field}_text": extracted.text,
                f"{field}_source": SourceType.text.value,
                f"{field}_file_path": file_path,
            }
        )
        return await _advance(message, state, field, lang)

    # Non-text input: show what was extracted and let the student confirm/fix
    # it before it's used for grading (OCR/parsing isn't perfect).
    await state.update_data(
        **{
            f"{field}_candidate_text": extracted.text,
            f"{field}_source": extracted.source.value,
            f"{field}_file_path": file_path,
            "_confirm_field": field,
        }
    )
    confirm_state = (
        WritingStates.confirming_prompt if field == "prompt" else WritingStates.confirming_answer
    )
    await state.set_state(confirm_state)
    await message.answer(
        t("writing.confirm_preview", lang, preview=_preview(extracted.text)),
        reply_markup=confirm_edit_kb(lang),
    )
    return False


@router.callback_query(WritingStates.confirming_section, F.data == CB_SWITCH_SECTION)
async def switch_section(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    await strip_buttons(callback.message)
    data = await state.get_data()
    task_type = data["switch_to"]
    quota = await check_quota(callback.from_user.id, writing_kind(task_type))
    if not quota.allowed:
        await send_paywall(callback.message, lang, quota)
        return
    async with _user_locks[callback.from_user.id]:
        if await state.get_state() != WritingStates.confirming_section.state:
            return
        await state.update_data(task_type=task_type, spend_credit=quota.spend_credit, payer_id=quota.payer_id,
                                switch_to=None,
                                prompt_image_paths=[] if task_type == "task2" else data.get("prompt_image_paths"))
        go = await _advance(callback.message, state, "prompt", lang)
    if go:  # a kept answer (the smart entry): graded now
        await _run_grading(callback.message, state, lang)


def _preview(text: str) -> str:
    return html.escape(text if len(text) <= 3500 else text[:3500] + "…", quote=False)


@router.callback_query(WritingStates.confirming_prompt, F.data.in_({CB_CONFIRM, CB_EDIT}))
@router.callback_query(WritingStates.confirming_answer, F.data.in_({CB_CONFIRM, CB_EDIT}))
async def confirm_or_edit(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    async with _user_locks[callback.from_user.id]:  # a double tap: only the first one goes on
        if await state.get_state() not in {WritingStates.confirming_prompt.state,
                                           WritingStates.confirming_answer.state}:
            return
        data = await state.get_data()
        field = data["_confirm_field"]
        await strip_buttons(callback.message)  # no double-confirm

        if callback.data == CB_EDIT:
            await callback.message.answer(
                t("writing.send_correct_text", lang), reply_markup=back_kb(lang, _back_target(data, field))
            )
            back_state = (
                WritingStates.awaiting_prompt if field == "prompt" else WritingStates.awaiting_answer
            )
            await state.update_data(_editing=field)
            await state.set_state(back_state)
            return

        await state.update_data(**{f"{field}_text": data[f"{field}_candidate_text"]})
        go = await _advance(callback.message, state, field, lang)
    if go:
        await _run_grading(callback.message, state, lang)


@router.message(WritingStates.confirming_answer, F.photo | F.document)
async def add_page(message: Message, state: FSMContext, lang: str) -> None:
    """Another page of the answer sent after the first one's preview (a handwritten essay on two pages - until
    2026-11-15 page 2 was ignored without a word): read and added to the text, the whole of it shown again.
    Not for the question: a second picture there is usually the Task 1 chart, never more question text."""
    async with _user_locks[message.from_user.id]:
        if await state.get_state() != WritingStates.confirming_answer.state:
            return
        data = await state.get_data()
        field = "answer"
        extracted = await read_text_input(message, lang, ocr_mode="verbatim")
        if extracted is None or not extracted.text.strip():
            return
        if extracted.cost:
            await state.update_data(input_cost=usage.merge(data.get("input_cost"), extracted.cost))
        text = (data.get(f"{field}_candidate_text", "").rstrip() + "\n\n" + extracted.text.strip()).strip()
        await state.update_data(**{f"{field}_candidate_text": text})
        await message.answer(t("writing.page_added", lang) + "\n\n"
                             + t("writing.confirm_preview", lang, preview=_preview(text)),
                             reply_markup=confirm_edit_kb(lang))


def _length_problem(data: dict) -> str | None:
    """A typed answer to ask about before it is graded: "short" (under half the minimum) or "long" (Telegram
    may have split it). Never twice for the same text."""
    if data.get("answer_source") != SourceType.text.value or data.get("_length_ok"):
        return None
    text = data.get("answer_text", "")
    if count_words(text) < MIN_ANSWER_WORDS[data["task_type"]] * SHORT_SHARE:
        return "short"
    if len(text) >= SPLIT_CHARS:
        return "long"
    return None


async def _ask_length(message: Message, state: FSMContext, data: dict, problem: str, lang: str) -> None:
    await state.set_state(WritingStates.confirming_length)
    words = count_words(data.get("answer_text", ""))
    if problem == "short":
        text = t("writing.short_answer", lang, words=words, min_words=MIN_ANSWER_WORDS[data["task_type"]],
                 task="Task 1" if data["task_type"] == "task1" else "Task 2")
    else:
        text = t("writing.long_answer", lang, words=words)
    await message.answer(text, reply_markup=length_kb(lang, _back_target(data, "answer")))


async def _add_more(message: Message, state: FSMContext, lang: str) -> None:
    """More of the answer, sent after "only N words" / "Telegram may have split it": added to it."""
    data = await state.get_data()
    before = data.get("answer_text", "").rstrip()
    joiner = "\n" if before.endswith((".", "!", "?", ":", '"', "»")) else " "
    text = (before + joiner + message.text.strip()).strip()
    await state.update_data(answer_text=text)
    await message.answer(t("writing.more_added", lang, words=count_words(text)),
                         reply_markup=length_kb(lang, _back_target(data, "answer")))


@router.message(WritingStates.confirming_length, F.text, ~F.text.in_(MENU_BUTTON_TEXTS))
async def receive_more(message: Message, state: FSMContext, lang: str) -> None:
    async with _user_locks[message.from_user.id]:
        if await state.get_state() != WritingStates.confirming_length.state:
            return
        await _add_more(message, state, lang)


@router.message(WritingStates.confirming_length, ~F.text)
async def receive_more_not_text(message: Message, lang: str) -> None:
    await message.answer(t("writing.text_only_here", lang))


@router.callback_query(WritingStates.confirming_length, F.data == CB_LENGTH_OK)
async def length_ok(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    """The short (or long) answer is the whole answer: on to grading - its other checks first."""
    await callback.answer()
    async with _user_locks[callback.from_user.id]:
        if await state.get_state() != WritingStates.confirming_length.state:
            return
        await strip_buttons(callback.message)
        await state.update_data(_length_ok=True)
        await state.set_state(WritingStates.awaiting_answer)
        go = await _advance(callback.message, state, "answer", lang)
    if go:
        await _run_grading(callback.message, state, lang)


async def _advance(message: Message, state: FSMContext, field: str, lang: str) -> bool:
    """The next step after the prompt or the answer. True when the answer is ready to be graded (the state is
    `processing` then; the caller grades it, outside its lock)."""
    if field == "prompt":
        data = await state.get_data()
        if data.get("kept_answer") and await _same_as_kept(data.get("prompt_text", ""), data["kept_answer"]):
            # the kept essay sent again as its question: never graded against itself - the question is asked again
            await state.set_state(WritingStates.awaiting_prompt)
            await message.answer(t("smart.same_as_answer", lang), reply_markup=back_kb(lang, CB_WRITING_MENU))
            return False
        own_question = data.get("mock_session_id") or data.get("assignment_part_id") or data.get("practice_question_id")
        section = None if own_question else wrong_section(data["task_type"], data.get("prompt_text", ""))
        if section:
            # A Task 2 essay is never marked against Task Achievement: the student decides first.
            await state.update_data(switch_to=section)
            await state.set_state(WritingStates.confirming_section)
            name = "Task 1" if section == "task1" else "Task 2"
            await message.answer(t("writing.wrong_section", lang, section=name),
                                 reply_markup=wrong_section_kb(lang, name, CB_WRITING_MENU))
            return False
        if data["task_type"] == "task1" and not data.get("prompt_image_paths"):
            # A Task 1 question typed as text: the report is checked against the
            # chart/diagram itself, so ask for its picture first.
            await state.set_state(WritingStates.awaiting_chart)
            await message.answer(t("writing.send_chart", lang), reply_markup=chart_kb(lang, _back_target(data, "answer")))
            return False
        return await _ask_for_answer(message, state, data, lang)
    data = await state.get_data()
    text = data.get("answer_text", "")
    if data.get("answer_source") == SourceType.text.value and starts_mid_word(text) \
            and data.get("_cut_warned") != text[:200]:
        # "e youngest people had..." - the start lost in copying (two teachers' tests, 2026-11-07):
        # the same text again, or the button, goes on
        await state.update_data(_cut_warned=text[:200])
        await message.answer(t("writing.cut_start", lang, start=html.escape(text[:40].strip(), quote=False)),
                             reply_markup=cut_start_kb(lang, _back_target(data, "answer")))
        return False
    problem = _length_problem(data)
    if problem:
        await _ask_length(message, state, data, problem, lang)
        return False
    await state.set_state(WritingStates.processing)
    return True


@router.callback_query(WritingStates.awaiting_answer, F.data == CB_CHECK_AS_IS)
async def check_as_is(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    """The answer that seemed to start mid-word is complete: mark it as it is (its length asked about first)."""
    await callback.answer()
    async with _user_locks[callback.from_user.id]:
        if await state.get_state() != WritingStates.awaiting_answer.state:
            return
        data = await state.get_data()
        if not data.get("answer_text") or data.get("_cut_warned") != data["answer_text"][:200]:
            return
        await strip_buttons(callback.message)
        problem = _length_problem(data)
        if problem:
            await _ask_length(callback.message, state, data, problem, lang)
            return
        await state.set_state(WritingStates.processing)
    await _run_grading(callback.message, state, lang)


@router.callback_query(WritingStates.confirming_recheck, F.data.in_({CB_RECHECK_NEW, CB_RECHECK_OLD}))
async def recheck_choice(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    """An admin or tester sent an essay checked before: mark it again, or
    get its saved report."""
    await callback.answer()
    data = await state.get_data()
    if not data.get("answer_text") or data.get("repeat_of") is None:
        return
    if not await _claim(callback.from_user.id, state, WritingStates.confirming_recheck):
        return
    await strip_buttons(callback.message)
    await state.update_data(repeat_choice={"id": data["repeat_of"], "new": callback.data == CB_RECHECK_NEW})
    await _run_grading(callback.message, state, lang)


async def _ask_for_answer(message: Message, state: FSMContext, data: dict, lang: str) -> bool:
    """The answer step. True when the answer is already here and ready to be graded (the state is `processing`
    then; the caller grades it): the essay the smart entry kept (bot/handlers/fallback.py, 2026-11-16) - it gets
    a typed answer's own checks first (a start lost in copying, the length), as if just sent."""
    await state.set_state(WritingStates.awaiting_answer)
    kept = (await state.get_data()).get("kept_answer")
    if kept:
        await state.update_data(kept_answer=None, answer_text=kept, answer_source=SourceType.text.value,
                                answer_file_path=None)
        return await _advance(message, state, "answer", lang)
    await message.answer(
        t("writing.send_answer", lang, min_words=MIN_ANSWER_WORDS[data["task_type"]])
        + "\n\n" + t("writing.honest_note", lang),
        reply_markup=back_kb(lang, _back_target(data, "answer")),
    )
    return False


async def _same_as_kept(prompt: str, kept: str) -> bool:
    """The "question" is the kept essay again (pasted twice): over 90% the same text. A real question is far
    shorter, so most are told apart by length alone; the character comparison runs off the event loop."""
    if count_words(prompt) < 0.8 * count_words(kept):
        return False
    a, b = canon(prompt).lower(), canon(kept).lower()

    def similar() -> bool:
        matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
        # quick_ratio() >= ratio() (difflib's own upper bound): at or under the threshold already means "not over"
        return a == b or (matcher.quick_ratio() > RESUBMISSION_SIMILARITY and matcher.ratio() > RESUBMISSION_SIMILARITY)

    return await asyncio.to_thread(similar)


@router.message(WritingStates.awaiting_chart, ~F.text.in_(MENU_BUTTON_TEXTS))
async def receive_chart(message: Message, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    if message.text:
        await message.answer(t("writing.chart_photo_only", lang), reply_markup=chart_kb(lang, _back_target(data, "answer")))
        return
    paths = await read_images(message, lang)
    if paths is None:
        return
    async with _user_locks[message.from_user.id]:
        if await state.get_state() != WritingStates.awaiting_chart.state:
            return
        await state.update_data(prompt_image_paths=paths[:MAX_PROMPT_IMAGES])
        go = await _after_chart(message, state, data, lang)
    if go:
        await _run_grading(message, state, lang)


@router.callback_query(WritingStates.awaiting_chart, F.data == CB_NO_CHART)
async def skip_chart(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    async with _user_locks[callback.from_user.id]:
        if await state.get_state() != WritingStates.awaiting_chart.state:
            return
        await strip_buttons(callback.message)
        data = await state.get_data()
        if data.get("rechart"):
            await state.update_data(prompt_image_paths=[])
        go = await _after_chart(callback.message, state, data, lang)
    if go:
        await _run_grading(callback.message, state, lang)


async def _after_chart(message: Message, state: FSMContext, data: dict, lang: str) -> bool:
    """True when the check goes on now (the chart asked for again after the answer, or the answer kept by the
    smart entry)."""
    if data.get("rechart"):
        await state.update_data(rechart=False)
        await state.set_state(WritingStates.processing)
        return True
    return await _ask_for_answer(message, state, data, lang)


class ChartUnreadable(Exception):
    """The chart reading found no usable visual in the picture."""


def _load_prompt_images(paths: list[str]) -> list[tuple[bytes, str]]:
    images = []
    for path in paths[:MAX_PROMPT_IMAGES]:
        try:
            content = read_file(path)
        except FileNotFoundError:
            logger.error("Task 1 chart missing on disk - graded without it: %s", path)
            continue
        images.append((content, image_mime(content, path)))  # by its bytes, whatever the name says
    return images


async def _ask_chart_again(processing_msg: Message, state: FSMContext, data: dict, lang: str, calls,
                           hold: guard.Hold) -> None:
    """The chart couldn't be used: the student sends it again (or goes on
    without it) and the check starts right away - the answer is kept and its
    coins went back (the hold ended); what the reading cost joins the check's cost."""
    await live.end(live.find(processing_msg.chat.id, processing_msg.message_id))
    if not await guard.owns(state, hold):  # they started something else meanwhile: nothing to come back to
        await _edit_or_send(processing_msg, t("check.failed_lost", lang))
        return
    cost = usage.merge(data.get("input_cost"), usage.summary(calls)) if calls else data.get("input_cost")
    await state.update_data(rechart=True, prompt_image_paths=[], input_cost=cost)
    await state.set_state(WritingStates.awaiting_chart)
    await _edit_or_send(processing_msg, t("writing.chart_resend", lang),
                        chart_kb(lang, _back_target(data, "answer")))


async def _edit_or_send(msg: Message, text: str, markup=None) -> None:
    """The check's card becomes `text` - or, when it is gone (the student deleted it), a new message. Never
    raises (guard.show, 2026-11-16: a network error here used to escape past the failure path)."""
    await guard.show(msg, text, markup)


async def _failed(message: Message, state: FSMContext, lang: str, card: Message, hold: guard.Hold, data: dict,
                  refunded: bool) -> None:
    """The check stopped: the card says so - with "🔁 Try again" while the answer is still this check's (the
    state moves to `failed`, which the button needs; bot/handlers/check_retry.py)."""
    note = ("\n\n" + t("check.coins_back", lang, coins=hold.coins)) if refunded else ""
    if data.get("mock_session_id"):
        await state.clear()
        await _edit_or_send(card, t("check.failed_lost", lang) + note)
    elif await guard.owns(state, hold):
        await state.set_state(WritingStates.failed)
        await guard.park(message.chat.id, state)  # "try again" survives a top-up's own steps (C3)
        await _edit_or_send(card, t("check.failed", lang) + note, guard.retry_kb(lang, hold.id))
    else:
        await _edit_or_send(card, t("check.failed_lost", lang) + note)
    await stickers.send(message.bot, message.chat.id, "oops")


async def _failed_early(message: Message, state: FSMContext, lang: str, card: Message | None,
                        error: BaseException, coins_back: int = 0) -> None:
    """A step before grading failed - the database or Redis away, Telegram down (2026-11-16: it escaped to
    aiogram, the card said "analysing…" for good and the state stayed `processing`). Nothing is charged
    (`coins_back`: a hold already taken, given back): the check moves to `failed` with "🔁 Try again" while
    the answer is in the FSM data, else the student is asked to send it again. `card`: the check's card,
    None when there is none yet (a new message then)."""
    logger.error("Writing check of user %s could not start: %s", message.chat.id, _reason(error), exc_info=error)
    target = card or message
    note = ("\n\n" + t("check.coins_back", lang, coins=coins_back)) if coins_back else ""
    try:
        data = await state.get_data()
        if data.get("answer_text") and data.get("task_type") and not data.get("mock_session_id"):
            await guard.refuse(target, state, WritingStates.failed, lang, t("check.failed", lang) + note,
                               edit=card is not None)
            return
        await state.clear()
    except Exception:  # noqa: BLE001 - the FSM is away too: at least the student hears it
        logger.warning("FSM of user %s not updated after a failed start", message.chat.id, exc_info=True)
    await guard.show(target, t("check.failed_lost", lang) + note, edit=card is not None)


def _reason(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"[:300]


async def retry(message: Message, state: FSMContext, lang: str) -> None:
    """"🔁 Try again" after a failed or interrupted check: the same input graded again (the state is already
    `processing`, claimed by bot/handlers/check_retry.py)."""
    await _run_grading(message, state, lang)


def _same_text(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, canon(a).lower(), canon(b).lower(), autojunk=False).ratio()


async def _repeat_of(user_id: int, task_type: str, answer_text: str):
    """(an earlier essay with exactly this text, an earlier essay more than
    90% the same, {submission id: essay number}) - either may be None. The
    character-by-character comparisons run off the event loop (a student with
    many essays: seconds of work)."""
    async with get_session() as session:
        earlier = await crud.get_writing_sessions(session, user_id, task_type)
        numbers = {s.id: await crud.writing_essay_number(session, user_id, s.id) for s in earlier}

    def compare():
        same = next((s for s in earlier if canon(s.answer_text).lower() == canon(answer_text).lower()), None)
        similar = next((s for s in earlier if _same_text(s.answer_text, answer_text) > RESUBMISSION_SIMILARITY), None)
        return same, similar

    same, similar = await asyncio.to_thread(compare)
    return same, similar, numbers


async def _run_grading(message: Message, state: FSMContext, lang: str) -> None:
    # Every step before grading is guarded (2026-11-16): a database or Redis error there moves the check to
    # `failed` with "🔁 Try again" (_failed_early) instead of a card saying "analysing…" for good.
    user_id = message.chat.id
    try:
        data = await state.get_data()
        # Text pasted from a PDF: lines broken mid-sentence joined, page headers
        # and footers left out - and every count, mark and check made on that.
        cleaned, footers = clean_pasted(data["answer_text"])
        if cleaned != data["answer_text"]:
            data["answer_text"] = cleaned
            await state.update_data(answer_text=cleaned)

        # The very same essay again gets its saved report, not a new (differently
        # scored) check - reports #14 and #15 were one essay graded 8.0 and 8.5,
        # with "progress" between them.
        # A test run (scripts/live_check.py) is always graded anew, costs nothing and is
        # kept apart: no progress, word bank, chart key or admins' card from it.
        test_run = bool(data.get("test_run"))
        same, similar, numbers = (None, None, {}) if test_run else \
            await _repeat_of(user_id, data["task_type"], data["answer_text"])
        # Admins and testers are asked first: mark it again (to see a marking change) or send the saved report.
        # Everyone else gets the saved report, as before.
        choice = data.get("repeat_choice") or {}
        fresh = same is not None and choice.get("id") == same.id and bool(choice.get("new"))
        if fresh:
            logger.info("Writing resubmission by user %s: same text as submission %s, marked again on request",
                        user_id, same.id)
            same = None
        elif same is not None and same.feedback_pdf_path and Path(same.feedback_pdf_path).exists() \
                and choice.get("id") != same.id and not data.get("mock_session_id") \
                and await runtime.may_recheck(user_id):
            await state.update_data(repeat_of=same.id)
            await state.set_state(WritingStates.confirming_recheck)
            await message.answer(t("writing.recheck_ask", lang, number=numbers[same.id], band=same.overall_band),
                                 reply_markup=recheck_kb(lang))
            return
        if same is not None and same.feedback_pdf_path and Path(same.feedback_pdf_path).exists():
            logger.info("Writing resubmission by user %s: same text as submission %s", user_id, same.id)
            # a failure sending it: "🔁 Try again" (the answer is still in the FSM data) sends it then
            await message.answer_document(
                BufferedInputFile(read_file(same.feedback_pdf_path), filename=report_filename("writing", same.task_type)),
                caption=with_ai_note(t("writing.resubmitted", lang, number=numbers[same.id], band=same.overall_band), lang),
                reply_markup=share_card_kb("writing", same.id, lang),
            )
            try:  # sent: what follows never turns it into a failed check
                if data.get("mock_session_id"):
                    await record_mock_test_part(message, state, submission_id=same.id, submission_type="writing",
                                                band=same.overall_band)
                else:
                    await state.clear()
                    await nudges.what_next(message, lang, writing_kind(data["task_type"]), data,
                                           _after_result_kb(data, lang))  # with the coins left (2026-11-16)
                await crm_events.check_repeated("w", user_id, same.id)
            except Exception:  # noqa: BLE001
                logger.exception("Writing resubmission of user %s: the steps after the saved report failed", user_id)
            return
        started = time.monotonic()
    except Exception as e:  # noqa: BLE001
        await _failed_early(message, state, lang, None, e)
        return

    try:
        processing_msg = await message.answer(t("writing.grading_in_progress", lang))
    except TelegramAPIError as e:  # Telegram away: no card to show, the flow still leaves `processing`
        await _failed_early(message, state, lang, None, e)
        return
    hold = None
    try:
        # One check at a time, its coins taken now and given back unless the report is saved
        # (bot/utils/check_guard.py) - refused: the card says why, with "🔁 Try again" (the answer is kept).
        hold = await guard.begin(processing_msg, state, data, module="w", kind=writing_kind(data["task_type"]),
                                 lang=lang, failed_state=WritingStates.failed)
        if hold is None:
            return
        data = await state.get_data()  # with its check_token (and the payer begin() settled on)
    except Exception as e:  # noqa: BLE001 - a hold already taken gives its coins back first
        refunded = await guard.end(hold, _reason(e)) if hold is not None else False
        await _failed_early(message, state, lang, processing_msg, e, hold.coins if refunded else 0)
        return
    # the waiting message, alive (bot/utils/progress.py)
    card = live.begin(processing_msg, lang, "writing_task1" if data["task_type"] == "task1" else "writing")
    calls: list = []
    visual = None
    chart_look = None  # how the chart was found: a checked chart key, or read now
    prompt_images = None
    submission = None
    try:
        async with guard.slot(hold, card), asyncio.timeout(settings.check_deadline_seconds):
            async with get_session() as session:
                earlier_essays, previous = await crud.get_writing_history(
                    session, user_id, data["task_type"], min_words=marking_config.progress_min_words(data["task_type"]))
                # the paragraph openers of the student's last model answers: the next one opens differently
                recent = await crud.get_writing_sessions(session, user_id, data["task_type"], limit=3)
            avoid = [o for sub in recent for o in (((sub.report_data or {}).get("model_answer") or {}).get("openers") or [])]
            if data["task_type"] == "task1":
                prompt_images = _load_prompt_images(data.get("prompt_image_paths") or [])
            seed = user_id + earlier_essays
            answer_task: asyncio.Task | None = None
            needs = None
            sheet = judged = ""
            visual_data = None  # the sheet as data: code checks the corrections and the model answer's figures

            def start_answer(bands, overall, feedback) -> None:
                # The model answer is written for this student's results, as soon
                # as the bands are decided.
                nonlocal answer_task, needs
                needs = needs_from_feedback(bands, overall, feedback, data["answer_text"])
                answer_task = asyncio.create_task(build_model_answer(
                    data["task_type"], data["prompt_text"], prompt_images, seed=seed, needs=needs, sheet=sheet,
                    visual=visual_data, avoid=avoid))

            notices = [PASTE_NOTICE] if footers else []
            if data["task_type"] == "task1" and not prompt_images:
                notices.append(CHART_NOT_CHECKED)
            with usage.track(f"writing {data['task_type']} user={user_id}") as calls:
                if prompt_images:
                    # The chart read into a checked data sheet whose figures every call
                    # uses - an admin's checked sheet when the chart has a chart key -
                    # and the first sign of a picture OpenAI can't open, before the
                    # scoring runs spend anything on it.
                    visual, chart_look = await chart_keys.sheet_for(
                        data["prompt_text"], prompt_images, data.get("practice_question_id"))
                    if visual is not None and visual.readability == "unreadable":
                        raise ChartUnreadable(visual.readability_note)
                    sheet = sheet_text(visual)
                    if sheet:
                        # the report's own figures checked against it - every figure the
                        # sheet doesn't show read once more on its own first: for judging
                        # the report (judged), the sheet alone for the model answer
                        visual_data, sheet, judged, unchecked = await judge_ready(
                            data["answer_text"], visual.model_dump(), data["prompt_text"], prompt_images)
                        if unchecked:
                            notices.append(CHART_PARTLY_CHECKED)
                try:
                    report = await grade_writing(
                        task_type=data["task_type"],
                        prompt_text=data["prompt_text"],
                        answer_text=data["answer_text"],
                        prompt_images=prompt_images,
                        on_feedback=start_answer,
                        sheet=judged,
                        notices=notices,
                        visual=visual_data,
                        text_source=data.get("answer_source") or "text",
                        fresh=fresh,
                    )
                except BaseException:
                    if answer_task is not None:
                        answer_task.cancel()
                    raise
                # the vocabulary's Uzbek meanings, written beside the model answer (no longer wait)
                uz_task = asyncio.create_task(uzbek_meanings(report.vocabulary))
                try:
                    if answer_task is None:
                        raise RuntimeError("the feedback never reached the model answer")
                    answer = await answer_task
                except Exception as e:
                    # The model answer page is never left empty: one more try.
                    logger.error("Model answer failed for user %s - trying once more", user_id, exc_info=e)
                    try:
                        answer = await build_model_answer(
                            data["task_type"], data["prompt_text"], prompt_images, seed=seed, needs=needs, sheet=sheet,
                            visual=visual_data, avoid=avoid
                        )
                    except Exception:
                        logger.exception("Model answer failed again for user %s - report goes out without it", user_id)
                        answer = None
                report.vocabulary_uz = await uz_task  # never raises: "" where a meaning is missing

            if similar is not None:
                # Nearly the same essay: no "progress" between an essay and itself.
                progress = {"first": True, "note": f"Same essay as #{numbers[similar.id]}, so there is no progress to "
                            "show. Your next new essay will be compared with this one."}
            else:
                progress = build_progress(report, previous)
            # WeasyPrint is CPU-bound for a few seconds - off the event loop, so
            # other students' messages aren't held up while a report renders.
            pdf_bytes = await asyncio.to_thread(
                build_writing_report_pdf,
                report,
                answer,
                progress,
                message.chat.full_name,
                earlier_essays + 1,
                prompt_images,
            )

            pdf_path = save_report(user_id, pdf_bytes, ".pdf")
            # Report vocabulary also feeds the student's flashcards ("Lug'atim").
            vocabulary = [
                VocabularyItem(word_or_phrase=v.term, meaning=v.meaning, example_sentence=v.example)
                for v in report.vocabulary
            ]
            coins = 0 if test_run else hold.coins
            paid_with = {"mode": "test-run", "coins": 0} if test_run else await payment_of(user_id, coins, hold.sponsor_id)
            async with get_session() as session:
                # paid by the hold, in this same transaction (db/crud.py _charge)
                submission = await crud.save_writing_submission(
                    session,
                    user_id=user_id,
                    task_type=data["task_type"],
                    prompt_text=data["prompt_text"],
                    prompt_source=data["prompt_source"],
                    answer_text=data["answer_text"],
                    answer_source=data["answer_source"],
                    original_file_paths={
                        "prompt": data.get("prompt_file_path"),
                        "answer": data.get("answer_file_path"),
                        # the Task 1 chart(s) the check used - also when sent apart from the question
                        "chart": data.get("prompt_image_paths") or None,
                    },
                    overall_band=report.overall_band,
                    criteria_scores=report.criteria_scores(),
                    annotations=report.mistakes_json(),
                    vocabulary=vocabulary,
                    book_sample_ref=None,  # the model answer is always newly written
                    feedback_pdf_path=pdf_path,
                    practice_question_id=data.get("practice_question_id"),
                    coins=coins,
                    assignment_part_id=data.get("assignment_part_id"),
                    payer_id=hold.sponsor_id,
                    is_test=test_run,
                    hold_id=hold.id if hold.persisted else None,
                    report_data={
                        # where the model answer came from: internal only, never in the report
                        "model_answer": None if answer is None else {
                            "source": answer.source, "aimed_at": answer.band,
                            "provisional": answer.source != "human_reviewed",
                            "shows": [x["n"] for x in answer.shows], "writer": answer.log.get("chosen"),
                            "reference_ids": answer.log.get("reference_ids", []),
                            "alternative": answer.alternative, "openers": answer.log.get("openers", []),
                        },
                        "resubmission_of": similar.id if similar is not None else None,
                        # a Task 1 chart's data sheet: the figures every part of the report used
                        "visual": visual.model_dump() if visual is not None else None,
                        # this check's OpenAI cost (estimated from tokens), reading the
                        # prompt/answer photo or file included ("input: ..."), how long the
                        # student waited, and how it was paid
                        "cost": usage.merge(usage.summary(calls), data.get("input_cost"), prefix="input: "),
                        "seconds": round(time.monotonic() - started),
                        "paid_with": paid_with,
                        "check_token": hold.id,
                    },
                )
    except (ChartImageRejected, ChartUnreadable) as e:
        logger.warning("Task 1 chart of user %s can't be used (%s: %s) - asking for it again",
                       user_id, type(e).__name__, str(e)[:300])
        await guard.end(hold, f"chart unreadable: {_reason(e)}")
        await _ask_chart_again(processing_msg, state, data, lang, calls, hold)
        return
    except Exception as e:
        if submission is None and prompt_images and is_image_error(e):
            logger.warning("OpenAI turned the Task 1 chart of user %s down - asking for it again: %s", user_id, e)
            await guard.end(hold, f"chart refused: {_reason(e)}")
            await _ask_chart_again(processing_msg, state, data, lang, calls, hold)
            return
        if isinstance(e, TimeoutError):
            logger.error("Writing check of user %s ran past %ss - stopped", user_id, settings.check_deadline_seconds)
        else:
            logger.exception("Writing grading failed for user %s", user_id)
        refunded = await guard.end(hold, _reason(e))
        await live.end(card)
        await _failed(message, state, lang, processing_msg, hold, data, refunded)
        return
    finally:
        if submission is None:
            # Not saved, whatever happened (a cancellation at shutdown too): the hold gives its coins back.
            await guard.end(hold, "stopped")

    # The report is saved and paid: what follows only delivers it - a failure here never fails the check
    # (the report stays in "My works"). The check counts as running until it is delivered (a deploy waits).
    try:
        await _deliver_writing(message, state, lang, data, hold, submission, report, vocabulary, pdf_bytes,
                               notices, prompt_images, visual, chart_look, test_run, processing_msg, card)
    finally:
        await guard.end(hold)


async def _deliver_writing(message, state, lang, data, hold, submission, report, vocabulary, pdf_bytes, notices,
                           prompt_images, visual, chart_look, test_run, processing_msg, card) -> None:
    user_id = message.chat.id
    try:
        async with get_session() as session:
            if data.get("practice_question_id"):
                await crud.mark_practice_complete(
                    session,
                    user_id=user_id,
                    practice_question_id=data["practice_question_id"],
                    submission_id=submission.id,
                    submission_type="writing",
                )
            new_words = 0 if test_run else await crud.add_vocabulary_entries(session, user_id, vocabulary,
                                                                             source="writing")
    except Exception:
        logger.exception("Practice mark / word bank of submission %s not saved", submission.id)
        new_words = 0
    if chart_look is not None and not test_run:
        try:
            # one more use of its chart key, or the chart kept for an admin to check
            await chart_keys.remember(data["prompt_text"], prompt_images, visual, chart_look,
                                      data.get("practice_question_id"), submission.id)
        except Exception:
            logger.exception("Chart key of submission %s not remembered", submission.id)

    found = len(report.mistakes)
    caption = (t("writing.done_caption", lang, band=report.overall_band, mistakes=found) if found
               else t("writing.done_caption_clean", lang, band=report.overall_band))
    if new_words:
        caption += "\n" + t("vocab.added_after_check", lang, count=new_words)
    if CHART_PARTLY_CHECKED in notices:  # some of the student's own figures could not be checked
        caption += "\n\n" + t("writing.chart_partly_unclear", lang)
    elif data["task_type"] == "task1" and not prompt_images:
        caption += "\n\n" + t("writing.chart_not_checked", lang)
    await live.end(card)
    await stickers.send(message.bot, user_id, stickers.for_result(report.overall_band, found))  # the Gotcha moment
    sent = await guard.deliver(lambda: message.answer_document(
        BufferedInputFile(pdf_bytes, filename=report_filename("writing", data["task_type"])),
        caption=with_ai_note(caption, lang),
        reply_markup=share_card_kb("writing", submission.id, lang),
    ), f"writing report {submission.id}")
    # the card goes only now the report is there; not delivered: the card says it is saved, with its button
    await guard.report_sent(processing_msg, sent, lang, "writing", submission.id)
    if sent and not test_run and not data.get("mock_session_id"):
        await share.send_story(message.bot, user_id, "writing", submission, lang)  # the story card (2026-11-16)

    if data.get("mock_session_id"):
        await record_mock_test_part(
            message,
            state,
            submission_id=submission.id,
            submission_type="writing",
            band=report.overall_band,
        )
    elif await guard.owns(state, hold):  # not when they started something else meanwhile
        await state.clear()
        await nudges.what_next(message, lang, writing_kind(data["task_type"]), data,
                               _after_result_kb(data, lang))  # with the coins left (2026-11-16)
    if not test_run:
        await crm_events.check_done("w", submission.id)


def _after_result_kb(data: dict, lang: str):
    if data.get("assignment_part_id"):
        return assignment_after_kb(data.get("next_part_id"), lang)
    if data.get("practice_question_id"):
        return after_result_kb(data["practice_question_id"], lang)
    return check_again_kb(TASK_CALLBACKS[data["task_type"]], lang)
