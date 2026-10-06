"""Speaking module: Part 1 / Part 2 / Part 3.

Flow: pick part -> send question/s (any format, like Writing) -> [confirm if
needed] -> the bot asks the questions ONE AT A TIME like a real examiner
(bot/utils/speaking_questions.py splits Part 1/3 sets; Part 2 is one cue
card), collecting a voice/video/audio answer to each -> after the last one
(or "✅ Yakunlash" early) the whole set is graded as one performance, one
check, one PDF -> "check again" / "next question" buttons. Also entered
directly from Practice, which presets part/practice_question_id and calls
`start_question_set` with the stored question set.

`lang` (bot/i18n.py) is only auto-injected by AccessControlMiddleware into
top-level handlers - the internal helpers below take it as an explicit
parameter instead, since they're plain function calls within the same turn,
not separately-dispatched events (see the matching note in writing.py).
"""
from __future__ import annotations

import asyncio
import html
import logging
from collections import defaultdict

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from bot.i18n import t
from bot.keyboards.confirm_kb import CB_CONFIRM, CB_EDIT, confirm_edit_kb
from bot.keyboards.main_menu_kb import BTN_SPEAKING, MENU_BUTTON_TEXTS
from bot.keyboards.nav_kb import CB_BACK_MAIN, back_kb, check_again_kb
from bot.keyboards.practice_kb import CB_BACK_TO_LIST_PREFIX, after_result_kb
from bot.keyboards.share_kb import share_card_kb
from bot.keyboards.speaking_part_kb import (
    CB_FINISH_NOW,
    CB_SPEAKING_MENU,
    PART_CALLBACKS,
    question_kb,
    speaking_part_kb,
)
from bot.states.speaking_states import SpeakingStates
from bot.utils.input_extraction import (
    UnsupportedInputError,
    extract_audio_from_message,
    read_text_input,
)
from bot.utils.messages import show_screen, strip_buttons
from bot.utils.mock_test_flow import record_mock_test_part
from bot.utils.quota import check_quota, send_paywall
from bot.utils.speaking_questions import split_questions
from db import crud
from db.database import get_session
from db.models import BookTaskType, SourceType
from services.ai.rag_book_search import find_authentic_sample, get_style_reference_samples
from services.ai.sample_analyzer import analyze_sample, analyze_style
from services.ai.speaking_grader import grade_speaking
from services.ai.transcription import concat_mp3, ensure_mp3, transcribe_mp3
from services.pdf.report_builder import build_feedback_pdf
from services.storage.file_storage import read_file, save_report, save_upload

logger = logging.getLogger(__name__)
router = Router(name="speaking")

PART_LABELS = {"part1": "Speaking Part 1", "part2": "Speaking Part 2", "part3": "Speaking Part 3"}
PART_TO_CALLBACK = {part: cb for cb, part in PART_CALLBACKS.items()}

# Serializes one user's answer handling, so several voice messages sent in
# quick succession are recorded against consecutive questions instead of
# racing on the same FSM snapshot.
_user_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


@router.message(F.text == BTN_SPEAKING)
async def open_speaking_menu(message: Message, state: FSMContext, lang: str) -> None:
    await state.clear()  # abandon any in-progress flow so stale FSM data can't leak into a new one
    await message.answer(t("speaking.choose_part", lang), reply_markup=speaking_part_kb(lang))


@router.callback_query(F.data == CB_SPEAKING_MENU)
async def back_to_speaking_menu(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    await state.clear()
    await show_screen(callback, t("speaking.choose_part", lang), speaking_part_kb(lang))


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
    await show_screen(callback, t("speaking.send_question", lang), back_kb(lang, CB_SPEAKING_MENU))


@router.message(SpeakingStates.awaiting_question, ~F.text.in_(MENU_BUTTON_TEXTS))
async def receive_question(message: Message, state: FSMContext, lang: str) -> None:
    extracted = await read_text_input(message, lang)
    if extracted is None:
        return

    if extracted.source == SourceType.text:
        await start_question_set(message, state, extracted.text, lang)
        return

    await state.update_data(question_candidate_text=extracted.text)
    await state.set_state(SpeakingStates.confirming_question)
    text = extracted.text
    preview = html.escape(text if len(text) <= 3500 else text[:3500] + "…", quote=False)
    await message.answer(
        t("writing.confirm_preview", lang, preview=preview),
        reply_markup=confirm_edit_kb(),
    )


@router.callback_query(SpeakingStates.confirming_question, F.data.in_({CB_CONFIRM, CB_EDIT}))
async def confirm_or_edit_question(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    await strip_buttons(callback.message)  # no double-confirm
    data = await state.get_data()

    if callback.data == CB_EDIT:
        back = CB_BACK_MAIN if data.get("mock_session_id") else CB_SPEAKING_MENU
        await callback.message.answer(t("writing.send_correct_text", lang), reply_markup=back_kb(lang, back))
        await state.set_state(SpeakingStates.awaiting_question)
        return

    await start_question_set(callback.message, state, data["question_candidate_text"], lang)


async def start_question_set(
    message: Message, state: FSMContext, text: str, lang: str, intro: str | None = None
) -> None:
    """Begins asking `text`'s questions one by one. Expects `part` (and
    optionally practice_question_id / mock_session_id) already in state.
    `intro` (HTML) heads the first question, e.g. the practice topic."""
    data = await state.get_data()
    await state.update_data(
        questions=split_questions(text, data["part"]), q_index=0, answers=[], intro=intro
    )
    await state.set_state(SpeakingStates.awaiting_answer)
    await _ask_current_question(message, state, lang)


def _back_target(data: dict) -> str:
    """Where "⬅️ Orqaga" leads from a question."""
    if data.get("mock_session_id"):
        return CB_BACK_MAIN
    if data.get("practice_question_id"):
        return f"{CB_BACK_TO_LIST_PREFIX}{data['practice_question_id']}"
    return PART_TO_CALLBACK[data["part"]]  # asks for a new question set


async def _ask_current_question(message: Message, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    questions = data["questions"]
    index = data["q_index"]
    question = html.escape(questions[index], quote=False)

    if data["part"] == "part2":
        text = t("speaking.cue_card", lang, question=question)
    elif len(questions) == 1:
        text = t("speaking.question_single", lang, question=question)
    else:
        text = t("speaking.question_n", lang, n=index + 1, total=len(questions), question=question)
    if index == 0 and data.get("intro"):
        text = f"{data['intro']}\n\n{text}"

    # Only the current question keeps its buttons.
    if data.get("question_msg_id"):
        try:
            await message.bot.edit_message_reply_markup(
                chat_id=message.chat.id, message_id=data["question_msg_id"], reply_markup=None
            )
        except TelegramBadRequest:
            pass

    sent = await message.answer(
        text,
        reply_markup=question_kb(lang, _back_target(data), can_finish=bool(data.get("answers"))),
    )
    await state.update_data(question_msg_id=sent.message_id)


@router.message(
    SpeakingStates.awaiting_answer,
    F.voice | F.video_note | F.audio | F.video | F.document,
)
async def receive_answer(message: Message, state: FSMContext, lang: str) -> None:
    try:
        _, filename, saved_path = await extract_audio_from_message(
            message, message.bot, message.from_user.id, lang
        )
    except UnsupportedInputError as e:
        await message.answer(str(e))
        return
    except TelegramBadRequest as e:
        logger.warning("Audio download failed for user %s: %s", message.from_user.id, e)
        key = "input.file_too_big" if "too big" in str(e).lower() else "writing.could_not_read"
        await message.answer(t(key, lang))
        return

    async with _user_locks[message.from_user.id]:
        if await state.get_state() != SpeakingStates.awaiting_answer.state:
            return  # the set was finished/abandoned while this one was downloading
        data = await state.get_data()
        if "questions" not in data:
            # A flow started before question sets existed (pre-2026-10-06
            # deploy): one question in `question_text`, no answers yet.
            data = {**data, "questions": [data.get("question_text", "")], "q_index": 0}
            await state.update_data(questions=data["questions"], q_index=0)
        answers = [*data.get("answers", []), {"path": saved_path, "filename": filename}]
        next_index = data["q_index"] + 1
        await state.update_data(answers=answers, q_index=next_index)

        if next_index < len(data["questions"]):
            await _ask_current_question(message, state, lang)
            return
        await state.set_state(SpeakingStates.processing)

    await _run_grading(message, state, lang)


@router.callback_query(SpeakingStates.awaiting_answer, F.data == CB_FINISH_NOW)
async def finish_now(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    async with _user_locks[callback.from_user.id]:
        if await state.get_state() != SpeakingStates.awaiting_answer.state:
            return
        if not (await state.get_data()).get("answers"):
            return
        await state.set_state(SpeakingStates.processing)
    await strip_buttons(callback.message)
    await _run_grading(callback.message, state, lang)


@router.message(SpeakingStates.awaiting_answer, ~F.text.in_(MENU_BUTTON_TEXTS))
async def reject_non_audio_answer(message: Message, lang: str) -> None:
    await message.answer(t("speaking.audio_only", lang))


def _combine(questions: list[str], transcripts: list[str]) -> tuple[str, str]:
    """(question_text, transcript_text) for grading/storage - a multi-question
    set is labelled Q/A so the grader and the PDF keep each answer with
    its question."""
    if len(questions) == 1:
        return questions[0], transcripts[0]
    question_text = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1))
    transcript_text = "\n\n".join(
        f"Q{i}: {q}\nA: {a}" for i, (q, a) in enumerate(zip(questions, transcripts), 1)
    )
    return question_text, transcript_text


async def _run_grading(message: Message, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    answers = data["answers"]
    questions = data["questions"][: len(answers)]
    user_id = message.chat.id
    processing_msg = await message.answer(t("writing.grading_in_progress", lang))

    try:
        clips = [await ensure_mp3(read_file(a["path"]), a["filename"]) for a in answers]
        transcripts = list(await asyncio.gather(*(transcribe_mp3(c) for c in clips)))
        mp3_bytes = await concat_mp3(clips)
        question_text, transcript_text = _combine(questions, transcripts)
        result = await grade_speaking(
            part=data["part"],
            question_text=question_text,
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
        if not data.get("mock_session_id"):
            await message.answer(t("nav.what_next", lang), reply_markup=_after_result_kb(data, lang))
        return

    pdf_path = save_report(user_id, pdf_bytes, ".pdf")
    audio_path = answers[0]["path"] if len(answers) == 1 else save_upload(user_id, mp3_bytes, ".mp3")

    async with get_session() as session:
        submission = await crud.save_speaking_submission(
            session,
            user_id=user_id,
            part=data["part"],
            question_text=question_text,
            audio_file_path=audio_path,
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
        await message.answer(t("nav.what_next", lang), reply_markup=_after_result_kb(data, lang))


def _after_result_kb(data: dict, lang: str):
    if data.get("practice_question_id"):
        return after_result_kb(data["practice_question_id"], lang)
    return check_again_kb(PART_TO_CALLBACK[data["part"]], lang)
