"""Writing module: Task 1 (Report) / Task 2 (Essay).

Flow: pick task -> send prompt (any format) -> [confirm OCR/parse if needed]
-> send answer (any format) -> [confirm] -> grading -> PDF feedback report
-> "check again" / "next question" buttons (instead of dropping the student
back at the main menu). Also entered directly from Practice
(bot/handlers/practice.py), which presets task_type/prompt_text/
practice_question_id (+ the Task 1 chart, if any) and jumps straight to
`awaiting_answer`. Every step has a "⬅️ Orqaga" button.

`lang` (bot/i18n.py) is only auto-injected by AccessControlMiddleware into
top-level handlers (choose_task/receive_prompt/receive_answer/confirm_or_edit)
- the internal helpers below (_receive_input/_advance/_run_grading) take it
as an explicit parameter instead, since they're plain function calls within
the same turn, not separately-dispatched events.
"""
from __future__ import annotations

import asyncio
import html
import logging
import mimetypes

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from bot.i18n import t
from bot.keyboards.confirm_kb import CB_CONFIRM, CB_EDIT, confirm_edit_kb
from bot.keyboards.main_menu_kb import BTN_WRITING, MENU_BUTTON_TEXTS
from bot.keyboards.nav_kb import CB_BACK_MAIN, back_kb, check_again_kb
from bot.keyboards.practice_kb import CB_BACK_TO_LIST_PREFIX, after_result_kb
from bot.keyboards.share_kb import share_card_kb
from bot.keyboards.writing_task_kb import (
    CB_TASK1,
    CB_TASK2,
    CB_WRITING_MENU,
    TASK_CALLBACKS,
    writing_task_kb,
)
from bot.states.writing_states import WritingStates
from bot.utils.input_extraction import read_text_input
from bot.utils.messages import show_screen, strip_buttons
from bot.utils.mock_test_flow import record_mock_test_part
from bot.utils.quota import check_quota, send_paywall
from db import crud
from db.database import get_session
from db.models import SourceType
from services.ai.sample_bank import build_sample_section
from services.ai.schemas import VocabularyItem
from services.ai.writing_grader import grade_writing
from services.pdf.report_builder import build_writing_report_pdf, progress_note
from services.storage.file_storage import read_file, save_report

logger = logging.getLogger(__name__)
router = Router(name="writing")

MIN_ANSWER_WORDS = {"task1": 150, "task2": 250}

MAX_PROMPT_IMAGES = 3


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

    allowed, spend_credit = await check_quota(callback.from_user.id)
    if not allowed:
        await send_paywall(callback.message, lang)
        return

    # Defends against a stale Task1/Task2 button from an old message being
    # tapped while a Mock Test (bot/utils/mock_test_flow.py) is in progress -
    # without this, a leftover mock_session_id in state would make this
    # one-off check get wrongly recorded as a mock test part. A no-op in the
    # normal flow, since open_writing_menu already cleared state.
    await state.clear()
    task_type = "task1" if callback.data == CB_TASK1 else "task2"
    await state.update_data(task_type=task_type, spend_credit=spend_credit)
    await state.set_state(WritingStates.awaiting_prompt)
    await show_screen(callback, t("writing.send_prompt", lang), back_kb(lang, CB_WRITING_MENU))


@router.message(WritingStates.awaiting_prompt, ~F.text.in_(MENU_BUTTON_TEXTS))
async def receive_prompt(message: Message, state: FSMContext, lang: str) -> None:
    await _receive_input(message, state, field="prompt", lang=lang)


@router.message(WritingStates.awaiting_answer, ~F.text.in_(MENU_BUTTON_TEXTS))
async def receive_answer(message: Message, state: FSMContext, lang: str) -> None:
    await _receive_input(message, state, field="answer", lang=lang)


def _back_target(data: dict, field: str) -> str:
    """Where "⬅️ Orqaga" leads from the prompt/answer step."""
    if data.get("mock_session_id"):
        return CB_BACK_MAIN
    if field == "prompt":
        return CB_WRITING_MENU
    if data.get("practice_question_id"):
        return f"{CB_BACK_TO_LIST_PREFIX}{data['practice_question_id']}"
    return TASK_CALLBACKS[data["task_type"]]  # re-asks for the prompt


async def _receive_input(message: Message, state: FSMContext, field: str, lang: str) -> None:
    extracted = await read_text_input(message, lang)
    if extracted is None:
        return

    file_path = extracted.file_paths[0] if extracted.file_paths else None
    if field == "prompt":
        # Kept for Task 1, so the grader sees the actual chart, not just its OCR'd labels.
        await state.update_data(prompt_image_paths=extracted.image_paths)

    if extracted.source == SourceType.text:
        await state.update_data(
            **{
                f"{field}_text": extracted.text,
                f"{field}_source": SourceType.text.value,
                f"{field}_file_path": file_path,
            }
        )
        await _advance(message, state, field, lang)
        return

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


def _preview(text: str) -> str:
    return html.escape(text if len(text) <= 3500 else text[:3500] + "…", quote=False)


@router.callback_query(WritingStates.confirming_prompt, F.data.in_({CB_CONFIRM, CB_EDIT}))
@router.callback_query(WritingStates.confirming_answer, F.data.in_({CB_CONFIRM, CB_EDIT}))
async def confirm_or_edit(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    field = data["_confirm_field"]
    await callback.answer()
    await strip_buttons(callback.message)  # no double-confirm

    if callback.data == CB_EDIT:
        await callback.message.answer(
            t("writing.send_correct_text", lang), reply_markup=back_kb(lang, _back_target(data, field))
        )
        back_state = (
            WritingStates.awaiting_prompt if field == "prompt" else WritingStates.awaiting_answer
        )
        await state.set_state(back_state)
        return

    await state.update_data(**{f"{field}_text": data[f"{field}_candidate_text"]})
    await _advance(callback.message, state, field, lang)


async def _advance(message: Message, state: FSMContext, field: str, lang: str) -> None:
    if field == "prompt":
        data = await state.get_data()
        min_words = MIN_ANSWER_WORDS[data["task_type"]]
        await state.set_state(WritingStates.awaiting_answer)
        await message.answer(
            t("writing.send_answer", lang, min_words=min_words),
            reply_markup=back_kb(lang, _back_target(data, "answer")),
        )
    else:
        await state.set_state(WritingStates.processing)
        await _run_grading(message, state, lang)


def _load_prompt_images(paths: list[str]) -> list[tuple[bytes, str]]:
    images = []
    for path in paths[:MAX_PROMPT_IMAGES]:
        try:
            content = read_file(path)
        except FileNotFoundError:
            logger.warning("Prompt image missing on disk: %s", path)
            continue
        images.append((content, mimetypes.guess_type(path)[0] or "image/jpeg"))
    return images


async def _run_grading(message: Message, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    user_id = message.chat.id
    processing_msg = await message.answer(t("writing.grading_in_progress", lang))

    try:
        async with get_session() as session:
            earlier_essays, previous = await crud.get_writing_history(session, user_id)
        prompt_images = None
        if data["task_type"] == "task1":
            prompt_images = _load_prompt_images(data.get("prompt_image_paths") or [])

        # The Sample section only needs the question, so it's written while
        # the essay is being graded.
        report, sample = await asyncio.gather(
            grade_writing(
                task_type=data["task_type"],
                prompt_text=data["prompt_text"],
                answer_text=data["answer_text"],
                prompt_images=prompt_images,
                lang=lang,
            ),
            build_sample_section(
                task_type=data["task_type"],
                question=data["prompt_text"],
                prompt_images=prompt_images,
                seed=user_id + earlier_essays,
            ),
            return_exceptions=True,
        )
        if isinstance(report, BaseException):
            raise report
        if isinstance(sample, BaseException):
            logger.error("Sample section failed for user %s - report goes out without it", user_id, exc_info=sample)
            sample = None

        # WeasyPrint is CPU-bound for a few seconds - off the event loop, so
        # other students' messages aren't held up while a report renders.
        pdf_bytes = await asyncio.to_thread(
            build_writing_report_pdf,
            report,
            sample,
            progress_note(lang, report, previous),
            message.chat.full_name,
            earlier_essays + 1,
            lang,
        )
    except Exception:
        logger.exception("Writing grading failed for user %s", user_id)
        await processing_msg.edit_text(t("writing.grading_failed", lang))
        await state.clear()
        if not data.get("mock_session_id"):
            await message.answer(t("nav.what_next", lang), reply_markup=_after_result_kb(data, lang))
        return

    pdf_path = save_report(user_id, pdf_bytes, ".pdf")
    # Report vocabulary also feeds the student's flashcards ("Lug'atim").
    vocabulary = [
        VocabularyItem(word_or_phrase=v.term, meaning=v.meaning, example_sentence=v.example)
        for v in report.vocabulary
    ]

    async with get_session() as session:
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
            },
            overall_band=report.overall_band,
            criteria_scores=report.criteria_scores(),
            annotations=report.mistakes_json(),
            vocabulary=vocabulary,
            book_sample_ref=sample.source_id if sample is not None else None,
            feedback_pdf_path=pdf_path,
            practice_question_id=data.get("practice_question_id"),
            spend_credit=data.get("spend_credit", False),
        )
        if data.get("practice_question_id"):
            await crud.mark_practice_complete(
                session,
                user_id=user_id,
                practice_question_id=data["practice_question_id"],
                submission_id=submission.id,
                submission_type="writing",
            )
        await crud.add_vocabulary_entries(session, user_id, vocabulary, source="writing")
        await crud.reward_referrer_if_eligible(session, user_id)

    await processing_msg.delete()
    await message.answer_document(
        BufferedInputFile(pdf_bytes, filename="IELTS_Feedback.pdf"),
        caption=t("writing.done_caption", lang, band=report.overall_band),
        reply_markup=share_card_kb("writing", submission.id, lang),
    )

    if data.get("mock_session_id"):
        await record_mock_test_part(
            message,
            state,
            submission_id=submission.id,
            submission_type="writing",
            band=report.overall_band,
        )
    else:
        await state.clear()
        await message.answer(t("nav.what_next", lang), reply_markup=_after_result_kb(data, lang))


def _after_result_kb(data: dict, lang: str):
    if data.get("practice_question_id"):
        return after_result_kb(data["practice_question_id"], lang)
    return check_again_kb(TASK_CALLBACKS[data["task_type"]], lang)
