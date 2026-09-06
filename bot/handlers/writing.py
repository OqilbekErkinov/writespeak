"""Writing module: Task 1 (Report) / Task 2 (Essay).

Flow: pick task -> send prompt (any format) -> [confirm OCR/parse if needed]
-> send answer (any format) -> [confirm] -> grading -> PDF feedback report.
Also entered directly from Practice (bot/handlers/practice.py), which presets
task_type/prompt_text/practice_question_id and jumps straight to
`awaiting_answer`.

`lang` (bot/i18n.py) is only auto-injected by AccessControlMiddleware into
top-level handlers (choose_task/receive_prompt/receive_answer/confirm_or_edit)
- the internal helpers below (_receive_input/_advance/_run_grading) take it
as an explicit parameter instead, since they're plain function calls within
the same turn, not separately-dispatched events.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from bot.i18n import t
from bot.keyboards.confirm_kb import CB_CONFIRM, CB_EDIT, confirm_edit_kb
from bot.keyboards.main_menu_kb import BTN_WRITING, MENU_BUTTON_TEXTS
from bot.keyboards.share_kb import share_card_kb
from bot.keyboards.writing_task_kb import CB_TASK1, CB_TASK2, writing_task_kb
from bot.states.writing_states import WritingStates
from bot.utils.input_extraction import UnsupportedInputError, extract_text_from_message
from bot.utils.mock_test_flow import record_mock_test_part
from bot.utils.quota import check_quota, send_paywall
from db import crud
from db.database import get_session
from db.models import BookTaskType, SourceType
from services.ai.rag_book_search import find_authentic_sample, get_style_reference_samples
from services.ai.sample_analyzer import analyze_sample, analyze_style
from services.ai.writing_grader import grade_writing
from services.pdf.report_builder import build_feedback_pdf
from services.storage.file_storage import save_report

logger = logging.getLogger(__name__)
router = Router(name="writing")

MIN_ANSWER_WORDS = {"task1": 150, "task2": 250}
TASK_LABELS = {"task1": "Writing Task 1 (Report)", "task2": "Writing Task 2 (Essay)"}


@router.message(F.text == BTN_WRITING)
async def open_writing_menu(message: Message, state: FSMContext, lang: str) -> None:
    await state.clear()  # abandon any in-progress flow so stale FSM data can't leak into a new one
    await message.answer(t("writing.choose_type", lang), reply_markup=writing_task_kb())


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
    await callback.message.answer(t("writing.send_prompt", lang))


@router.message(WritingStates.awaiting_prompt, ~F.text.in_(MENU_BUTTON_TEXTS))
async def receive_prompt(message: Message, state: FSMContext, lang: str) -> None:
    await _receive_input(message, state, field="prompt", lang=lang)


@router.message(WritingStates.awaiting_answer, ~F.text.in_(MENU_BUTTON_TEXTS))
async def receive_answer(message: Message, state: FSMContext, lang: str) -> None:
    await _receive_input(message, state, field="answer", lang=lang)


async def _receive_input(message: Message, state: FSMContext, field: str, lang: str) -> None:
    try:
        text, source, saved_path = await extract_text_from_message(
            message, message.bot, message.from_user.id, lang
        )
    except UnsupportedInputError as e:
        await message.answer(str(e))
        return

    if not text:
        await message.answer(t("writing.could_not_read", lang))
        return

    if source == SourceType.text:
        await state.update_data(**{f"{field}_text": text, f"{field}_source": source.value})
        await _advance(message, state, field, lang)
        return

    # Non-text input: show what was extracted and let the student confirm/fix
    # it before it's used for grading (OCR/parsing isn't perfect).
    await state.update_data(
        **{
            f"{field}_candidate_text": text,
            f"{field}_source": source.value,
            f"{field}_file_path": saved_path,
            "_confirm_field": field,
        }
    )
    confirm_state = (
        WritingStates.confirming_prompt if field == "prompt" else WritingStates.confirming_answer
    )
    await state.set_state(confirm_state)
    preview = text if len(text) <= 3500 else text[:3500] + "…"
    await message.answer(
        t("writing.confirm_preview", lang, preview=preview),
        reply_markup=confirm_edit_kb(),
    )


@router.callback_query(WritingStates.confirming_prompt, F.data.in_({CB_CONFIRM, CB_EDIT}))
@router.callback_query(WritingStates.confirming_answer, F.data.in_({CB_CONFIRM, CB_EDIT}))
async def confirm_or_edit(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    field = data["_confirm_field"]
    await callback.answer()

    if callback.data == CB_EDIT:
        await callback.message.answer(t("writing.send_correct_text", lang))
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
        await message.answer(t("writing.send_answer", lang, min_words=min_words))
    else:
        await state.set_state(WritingStates.processing)
        await _run_grading(message, state, lang)


async def _run_grading(message: Message, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    user_id = message.chat.id
    processing_msg = await message.answer(t("writing.grading_in_progress", lang))

    try:
        result = await grade_writing(
            task_type=data["task_type"],
            prompt_text=data["prompt_text"],
            answer_text=data["answer_text"],
        )
        book_task_type = BookTaskType(f"writing_{data['task_type']}")
        book_sample = await find_authentic_sample(
            topic=result.topic,
            task_type=book_task_type,
            min_band=result.overall_band + 1.0,
        )
        sample_analysis = None
        style_analysis = None
        if book_sample is not None:
            sample_analysis = await analyze_sample(
                book_sample.content_text, TASK_LABELS[data["task_type"]]
            )
        else:
            # No topic match at all for this task type - fall back to a
            # general style analysis instead of an apology (Revision Brief
            # v2, Section 4).
            style_samples = await get_style_reference_samples(book_task_type)
            if style_samples:
                style_analysis = await analyze_style(
                    [s.content_text for s in style_samples], TASK_LABELS[data["task_type"]]
                )
        pdf_bytes = build_feedback_pdf(
            student_name=message.chat.full_name,
            task_label=TASK_LABELS[data["task_type"]],
            answer_text=data["answer_text"],
            grading=result,
            book_sample=book_sample,
            sample_analysis=sample_analysis,
            style_analysis=style_analysis,
        )
    except Exception:
        logger.exception("Writing grading failed for user %s", user_id)
        await processing_msg.edit_text(t("writing.grading_failed", lang))
        await state.clear()
        return

    pdf_path = save_report(user_id, pdf_bytes, ".pdf")

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
            result=result,
            book_sample_ref=book_sample.id if book_sample else None,
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
        await crud.add_vocabulary_entries(session, user_id, result.vocabulary, source="writing")
        await crud.reward_referrer_if_eligible(session, user_id)

    await processing_msg.delete()
    await message.answer_document(
        BufferedInputFile(pdf_bytes, filename="IELTS_Feedback.pdf"),
        caption=t("writing.done_caption", lang, band=result.overall_band),
        reply_markup=share_card_kb("writing", submission.id, lang),
    )

    if data.get("mock_session_id"):
        await record_mock_test_part(
            message,
            state,
            submission_id=submission.id,
            submission_type="writing",
            band=result.overall_band,
        )
    else:
        await state.clear()
