"""Speaking module: Part 1 / Part 2 / Part 3.

Flow: pick part -> send question (any format, like Writing) -> [confirm if
needed] -> send a voice message / video note / audio file (any format) ->
grading -> PDF feedback report. Also entered directly from Practice, which
presets part/question_text/practice_question_id and jumps straight to
`awaiting_answer`.

`lang` (bot/i18n.py) is only auto-injected by AccessControlMiddleware into
top-level handlers - the internal helpers below take it as an explicit
parameter instead, since they're plain function calls within the same turn,
not separately-dispatched events (see the matching note in writing.py).
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from bot.i18n import t
from bot.keyboards.confirm_kb import CB_CONFIRM, CB_EDIT, confirm_edit_kb
from bot.keyboards.main_menu_kb import BTN_SPEAKING, MENU_BUTTON_TEXTS
from bot.keyboards.share_kb import share_card_kb
from bot.keyboards.speaking_part_kb import CB_PART1, CB_PART2, CB_PART3, speaking_part_kb
from bot.states.speaking_states import SpeakingStates
from bot.utils.input_extraction import (
    UnsupportedInputError,
    extract_audio_from_message,
    extract_text_from_message,
)
from bot.utils.mock_test_flow import record_mock_test_part
from bot.utils.quota import check_quota, send_paywall
from db import crud
from db.database import get_session
from db.models import BookTaskType, SourceType
from services.ai.rag_book_search import find_authentic_sample, get_style_reference_samples
from services.ai.sample_analyzer import analyze_sample, analyze_style
from services.ai.speaking_grader import grade_speaking
from services.ai.transcription import ensure_mp3, transcribe_mp3
from services.pdf.report_builder import build_feedback_pdf
from services.storage.file_storage import save_report

logger = logging.getLogger(__name__)
router = Router(name="speaking")

PART_LABELS = {"part1": "Speaking Part 1", "part2": "Speaking Part 2", "part3": "Speaking Part 3"}
PART_CALLBACKS = {CB_PART1: "part1", CB_PART2: "part2", CB_PART3: "part3"}


@router.message(F.text == BTN_SPEAKING)
async def open_speaking_menu(message: Message, state: FSMContext, lang: str) -> None:
    await state.clear()  # abandon any in-progress flow so stale FSM data can't leak into a new one
    await message.answer(t("speaking.choose_part", lang), reply_markup=speaking_part_kb())


@router.callback_query(F.data.in_(PART_CALLBACKS))
async def choose_part(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()

    allowed, spend_credit = await check_quota(callback.from_user.id)
    if not allowed:
        await send_paywall(callback.message, lang)
        return

    # See the matching comment in bot/handlers/writing.py's choose_task.
    await state.clear()
    await state.update_data(part=PART_CALLBACKS[callback.data], spend_credit=spend_credit)
    await state.set_state(SpeakingStates.awaiting_question)
    await callback.message.answer(t("speaking.send_question", lang))


@router.message(SpeakingStates.awaiting_question, ~F.text.in_(MENU_BUTTON_TEXTS))
async def receive_question(message: Message, state: FSMContext, lang: str) -> None:
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
        await state.update_data(question_text=text)
        await _ask_for_answer(message, state, lang)
        return

    await state.update_data(question_candidate_text=text)
    await state.set_state(SpeakingStates.confirming_question)
    preview = text if len(text) <= 3500 else text[:3500] + "…"
    await message.answer(
        t("writing.confirm_preview", lang, preview=preview),
        reply_markup=confirm_edit_kb(),
    )


@router.callback_query(SpeakingStates.confirming_question, F.data.in_({CB_CONFIRM, CB_EDIT}))
async def confirm_or_edit_question(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()

    if callback.data == CB_EDIT:
        await callback.message.answer(t("writing.send_correct_text", lang))
        await state.set_state(SpeakingStates.awaiting_question)
        return

    data = await state.get_data()
    await state.update_data(question_text=data["question_candidate_text"])
    await _ask_for_answer(callback.message, state, lang)


async def _ask_for_answer(message: Message, state: FSMContext, lang: str) -> None:
    await state.set_state(SpeakingStates.awaiting_answer)
    await message.answer(t("speaking.send_answer", lang))


@router.message(
    SpeakingStates.awaiting_answer,
    F.voice | F.video_note | F.audio | F.document,
)
async def receive_answer(message: Message, state: FSMContext, lang: str) -> None:
    try:
        audio_bytes, filename, saved_path = await extract_audio_from_message(
            message, message.bot, message.from_user.id, lang
        )
    except UnsupportedInputError as e:
        await message.answer(str(e))
        return

    await state.update_data(audio_file_path=saved_path)
    await state.set_state(SpeakingStates.processing)
    await _run_grading(message, state, audio_bytes, filename, lang)


@router.message(SpeakingStates.awaiting_answer, ~F.text.in_(MENU_BUTTON_TEXTS))
async def reject_non_audio_answer(message: Message, lang: str) -> None:
    await message.answer(t("speaking.audio_only", lang))


async def _run_grading(
    message: Message, state: FSMContext, audio_bytes: bytes, filename: str, lang: str
) -> None:
    data = await state.get_data()
    user_id = message.chat.id
    processing_msg = await message.answer(t("writing.grading_in_progress", lang))

    try:
        mp3_bytes = await ensure_mp3(audio_bytes, filename)
        transcript_text = await transcribe_mp3(mp3_bytes)
        result = await grade_speaking(
            part=data["part"],
            question_text=data["question_text"],
            mp3_audio_bytes=mp3_bytes,
            transcript_text=transcript_text,
        )
        book_sample = await find_authentic_sample(
            topic=result.topic,
            task_type=BookTaskType.speaking,
            min_band=result.overall_band + 1.0,
        )
        sample_analysis = None
        style_analysis = None
        if book_sample is not None:
            sample_analysis = await analyze_sample(book_sample.content_text, PART_LABELS[data["part"]])
        else:
            # No topic match at all for Speaking - fall back to a general
            # style analysis instead of an apology (Revision Brief v2,
            # Section 4).
            style_samples = await get_style_reference_samples(BookTaskType.speaking)
            if style_samples:
                style_analysis = await analyze_style(
                    [s.content_text for s in style_samples], PART_LABELS[data["part"]]
                )
        pdf_bytes = build_feedback_pdf(
            student_name=message.chat.full_name,
            task_label=PART_LABELS[data["part"]],
            answer_text=transcript_text,
            grading=result,
            book_sample=book_sample,
            sample_analysis=sample_analysis,
            style_analysis=style_analysis,
        )
    except Exception:
        logger.exception("Speaking grading failed for user %s", user_id)
        await processing_msg.edit_text(t("writing.grading_failed", lang))
        await state.clear()
        return

    pdf_path = save_report(user_id, pdf_bytes, ".pdf")

    async with get_session() as session:
        submission = await crud.save_speaking_submission(
            session,
            user_id=user_id,
            part=data["part"],
            question_text=data["question_text"],
            audio_file_path=data["audio_file_path"],
            transcript_text=transcript_text,
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
                submission_type="speaking",
            )
        await crud.add_vocabulary_entries(session, user_id, result.vocabulary, source="speaking")
        await crud.reward_referrer_if_eligible(session, user_id)

    await processing_msg.delete()
    await message.answer_document(
        BufferedInputFile(pdf_bytes, filename="IELTS_Feedback.pdf"),
        caption=t("writing.done_caption", lang, band=result.overall_band),
        reply_markup=share_card_kb("speaking", submission.id, lang),
    )

    if data.get("mock_session_id"):
        await record_mock_test_part(
            message,
            state,
            submission_id=submission.id,
            submission_type="speaking",
            band=result.overall_band,
        )
    else:
        await state.clear()
