"""Speaking module: Part 1 / Part 2 / Part 3 / Full test.

Flow: pick part -> send question/s (any format, like Writing) -> [confirm if
needed] -> the bot asks the questions ONE AT A TIME like a real examiner
(bot/utils/speaking_questions.py splits Part 1/3 sets and drops repeated
questions; Part 2 is one cue card), collecting a voice/video/audio answer to
each -> after the last one (or "✅ Yakunlash" early) the whole set is graded
as one performance, one check, one PDF -> the model answers follow as voice
messages -> "check again" / "next question" buttons. Also entered directly
from Practice and group assignments, which preset part/practice_question_id
and call `start_question_set` with the stored question set.

A Full test takes a Part 1 topic, a cue card and its Part 3 questions from
the bank and is graded as one report with a Speaking band; it costs the
"speaking_full" coins (bot/utils/coins.py), less than the three parts apart.

Grading is services/ai/speaking_check.py (voice analysis, scores, feedback,
model answers); this module adds Telegram and the database: the student's
history (progress page), resubmissions (the same recordings again get their
saved report back, free; near-identical answers are labelled, with no
progress shown), the report, the voice messages.

`lang` (bot/i18n.py) is only auto-injected by AccessControlMiddleware into
top-level handlers - the internal helpers below take it as an explicit
parameter instead, since they're plain function calls within the same turn,
not separately-dispatched events (see the matching note in writing.py).
"""
from __future__ import annotations

import asyncio
import difflib
import hashlib
import html
import logging
import re
import time
from collections import defaultdict
from pathlib import Path

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from bot.i18n import t
from bot.keyboards.confirm_kb import CB_CONFIRM, CB_EDIT, confirm_edit_kb
from bot.keyboards.group_kb import CB_MY_ASSIGNMENTS, assignment_after_kb
from bot.keyboards.main_menu_kb import BTN_SPEAKING, MENU_BUTTON_TEXTS
from bot.keyboards.nav_kb import CB_BACK_MAIN, back_kb, check_again_kb
from bot.keyboards.practice_kb import CB_BACK_TO_LIST_PREFIX, after_result_kb
from bot.keyboards.share_kb import share_card_kb
from bot.keyboards.speaking_part_kb import (
    CB_FINISH_NOW,
    CB_FULL_TEST,
    CB_LISTEN_PREFIX,
    CB_SPEAKING_MENU,
    PART_CALLBACKS,
    listen_kb,
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
from bot.utils import crm_events, stickers
from bot.utils.coins import speaking_kind
from bot.utils.quota import check_price, check_quota, free_check_note, payment_of, send_paywall
from bot.utils.speaking_questions import split_questions
from db import crud
from db.database import get_session
from db.models import SourceType
from services.ai import tts, usage
from services.ai.essay_marking import canon
from services.ai.schemas import VocabularyItem
from services.ai.speaking_check import run_check
from services.ai.speaking_marking import PART_NAMES, build_progress
from services.ai.transcription import concat_mp3, ensure_mp3
from services.pdf.report_builder import build_speaking_report_pdf
from services.storage.file_storage import read_file, save_report, save_upload

logger = logging.getLogger(__name__)
router = Router(name="speaking")

PART_TO_CALLBACK = {part: cb for cb, part in PART_CALLBACKS.items()}
FULL_TEST_QUESTIONS = 5  # Part 1 and Part 3 questions asked in a full test, each
RESUBMISSION_SIMILARITY = 0.9

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

    quota = await check_quota(callback.from_user.id, speaking_kind(PART_CALLBACKS[callback.data]))
    if not quota.allowed:
        await send_paywall(callback.message, lang, quota)
        return

    # See the matching comment in bot/handlers/writing.py's choose_task.
    await state.clear()
    await state.update_data(part=PART_CALLBACKS[callback.data], spend_credit=quota.spend_credit, payer_id=quota.payer_id)
    await state.set_state(SpeakingStates.awaiting_question)
    await show_screen(
        callback, free_check_note(quota, lang) + t("speaking.send_question", lang), back_kb(lang, CB_SPEAKING_MENU)
    )


@router.callback_query(F.data == CB_FULL_TEST)
async def choose_full_test(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    """Part 1 topic + cue card + its Part 3 questions, from the bank."""
    await callback.answer()
    quota = await check_quota(callback.from_user.id, "speaking_full")
    if not quota.allowed:
        await send_paywall(callback.message, lang, quota)
        return
    async with get_session() as session:
        picked = await crud.pick_full_test(session)
    if picked is None:
        await callback.message.answer(t("speaking.full_no_questions", lang))
        return
    part1, card, follow_up = picked
    p1 = split_questions(part1.question_text, "part1")[:FULL_TEST_QUESTIONS]
    p3 = split_questions(follow_up.question_text, "part3")[:FULL_TEST_QUESTIONS]

    await state.clear()
    await state.update_data(
        part="full", spend_credit=quota.spend_credit, payer_id=quota.payer_id,
        questions=[*p1, card.question_text, *p3],
        question_parts=["part1"] * len(p1) + ["part2"] + ["part3"] * len(p3),
        q_index=0, answers=[], intro=None,
    )
    await state.set_state(SpeakingStates.awaiting_answer)
    await show_screen(callback, free_check_note(quota, lang) + t("speaking.full_intro", lang), None)
    await _ask_current_question(callback.message, state, lang)


@router.message(SpeakingStates.awaiting_question, ~F.text.in_(MENU_BUTTON_TEXTS))
async def receive_question(message: Message, state: FSMContext, lang: str) -> None:
    extracted = await read_text_input(message, lang)
    if extracted is None:
        return
    if extracted.cost:
        # reading a photo/file/voice costs too: it is added to the check's cost
        earlier = (await state.get_data()).get("input_cost")
        await state.update_data(input_cost=usage.merge(earlier, extracted.cost))

    if extracted.source == SourceType.text:
        await start_question_set(message, state, extracted.text, lang)
        return

    await state.update_data(question_candidate_text=extracted.text)
    await state.set_state(SpeakingStates.confirming_question)
    text = extracted.text
    preview = html.escape(text if len(text) <= 3500 else text[:3500] + "…", quote=False)
    await message.answer(
        t("writing.confirm_preview", lang, preview=preview),
        reply_markup=confirm_edit_kb(lang),
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
    questions = split_questions(text, data["part"])
    await state.update_data(
        questions=questions, question_parts=[data["part"]] * len(questions), q_index=0, answers=[], intro=intro
    )
    await state.set_state(SpeakingStates.awaiting_answer)
    await _ask_current_question(message, state, lang)


def _back_target(data: dict) -> str:
    """Where "⬅️ Orqaga" leads from a question."""
    if data.get("mock_session_id"):
        return CB_BACK_MAIN
    if data.get("practice_question_id"):
        return f"{CB_BACK_TO_LIST_PREFIX}{data['practice_question_id']}"
    if data.get("assignment_part_id"):
        return CB_MY_ASSIGNMENTS
    if data["part"] == "full":
        return CB_SPEAKING_MENU
    return PART_TO_CALLBACK[data["part"]]  # asks for a new question set


def _question_parts(data: dict) -> list[str]:
    # Flows started before full tests existed have no question_parts.
    return data.get("question_parts") or [data["part"]] * len(data["questions"])


async def _ask_current_question(message: Message, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    questions = data["questions"]
    parts = _question_parts(data)
    index = data["q_index"]
    question = html.escape(questions[index], quote=False)

    if parts[index] == "part2":
        text = t("speaking.cue_card", lang, question=question)
    elif len(questions) == 1:
        text = t("speaking.question_single", lang, question=question)
    else:
        text = t("speaking.question_n", lang, n=index + 1, total=len(questions), question=question)
    if data["part"] == "full" and (index == 0 or parts[index] != parts[index - 1]):
        text = t("speaking.part_start", lang, part=PART_NAMES[parts[index]]) + "\n\n" + text
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

    can_finish = bool(data.get("answers")) and data["part"] != "full"  # a full test is answered in full
    sent = await message.answer(text, reply_markup=question_kb(lang, _back_target(data), can_finish=can_finish))
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
        data = await state.get_data()
        if not data.get("answers") or data.get("part") == "full":
            return
        await state.set_state(SpeakingStates.processing)
    await strip_buttons(callback.message)
    await _run_grading(callback.message, state, lang)


@router.message(SpeakingStates.awaiting_answer, ~F.text.in_(MENU_BUTTON_TEXTS))
async def reject_non_audio_answer(message: Message, lang: str) -> None:
    await message.answer(t("speaking.audio_only", lang))


def _combine(questions: list[str], transcripts: list[str]) -> tuple[str, str]:
    """(question_text, transcript_text) for storage - a multi-question set is
    labelled Q/A so each answer stays with its question."""
    if len(questions) == 1:
        return questions[0], transcripts[0]
    question_text = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1))
    transcript_text = "\n\n".join(
        f"Q{i}: {q}\nA: {a}" for i, (q, a) in enumerate(zip(questions, transcripts), 1)
    )
    return question_text, transcript_text


def _fingerprint(clips: list[bytes]) -> str:
    digest = hashlib.sha256()
    for clip in clips:
        digest.update(hashlib.sha256(clip).digest())
    return digest.hexdigest()


def _said(texts: list[str]) -> str:
    return canon(" ".join(texts)).lower()


def _find_resubmission(sessions, audio_hash: str, transcripts: list[str] | None = None):
    """The earlier session these answers repeat: the very same recordings
    (by fingerprint), or - with `transcripts` - answers more than 90% the same."""
    for s in sessions:
        data = s.report_data or {}
        if transcripts is None:
            if data.get("audio_hash") == audio_hash:
                return s
            continue
        before = data.get("transcripts")
        if not before or len(before) != len(transcripts):
            continue
        if difflib.SequenceMatcher(None, _said(before), _said(transcripts), autojunk=False).ratio() > RESUBMISSION_SIMILARITY:
            return s
    return None


def _session_number(submission) -> int:
    return (submission.report_data or {}).get("number") or submission.id


async def _send_saved_report(message: Message, state: FSMContext, data: dict, earlier, lang: str) -> bool:
    """The same recordings again: the saved report, no new check. False if
    the saved PDF is gone (then the answers are graded as new)."""
    path = earlier.feedback_pdf_path
    if not path or not Path(path).exists():
        return False
    await message.answer_document(
        BufferedInputFile(read_file(path), filename="IELTS_Feedback.pdf"),
        caption=t("speaking.resubmitted", lang, number=_session_number(earlier), band=earlier.overall_band),
        reply_markup=share_card_kb("speaking", earlier.id, lang),
    )
    if data.get("mock_session_id"):
        await record_mock_test_part(message, state, submission_id=earlier.id, submission_type="speaking",
                                    band=earlier.overall_band)
    else:
        await state.clear()
        await message.answer(t("nav.what_next", lang), reply_markup=_after_result_kb(data, lang))
    return True


async def _run_grading(message: Message, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    answers = data["answers"]
    questions = data["questions"][: len(answers)]
    parts = _question_parts(data)[: len(answers)]
    user_id = message.chat.id
    part = data["part"]
    coins = check_price(data, speaking_kind(part))

    clips = [read_file(a["path"]) for a in answers]
    audio_hash = _fingerprint(clips)
    async with get_session() as session:
        sessions = await crud.get_speaking_sessions(session, user_id, part)
    earlier = _find_resubmission(sessions, audio_hash)
    if earlier is not None and await _send_saved_report(message, state, data, earlier, lang):
        logger.info("Speaking resubmission by user %s: same recordings as submission %s", user_id, earlier.id)
        await crm_events.check_repeated("s", user_id, earlier.id)
        return

    started = time.monotonic()
    processing_msg = await message.answer(t("speaking.grading_in_progress", lang))
    try:
        async with get_session() as session:
            earlier_sessions, previous = await crud.get_speaking_history(session, user_id, part)
        number = earlier_sessions + 1
        with usage.track(f"speaking {part} user={user_id}") as calls:
            check = await run_check(
                part,
                [{"part": p, "text": q} for p, q in zip(parts, questions)],
                [(clip, a["filename"]) for clip, a in zip(clips, answers)],
                seed=user_id + earlier_sessions,
            )
        report = check.report
        transcripts = [item.audio.text for item in check.items]
        repeat = _find_resubmission(sessions, audio_hash, transcripts)
        progress = build_progress(
            part, report.overall_band, check.metrics, None if repeat else previous, report.priorities,
            _session_number(repeat) if repeat else None,
        )
        listen = tts.enabled() and bool(check.answers)
        for a in check.answers:
            a["voice"] = listen  # spoken on demand: a "🔊" button under the report
        # WeasyPrint is CPU-bound for a few seconds - off the event loop.
        pdf_bytes = await asyncio.to_thread(
            build_speaking_report_pdf, report, check.answers, progress, message.chat.full_name,
            listen,
        )
        mp3s = [await ensure_mp3(clip, a["filename"]) for clip, a in zip(clips, answers)]
        joined = mp3s[0] if len(mp3s) == 1 else await concat_mp3(mp3s)
    except Exception:
        logger.exception("Speaking grading failed for user %s", user_id)
        await processing_msg.edit_text(t("writing.grading_failed", lang))
        await stickers.send(message.bot, user_id, "oops")
        await state.clear()
        if not data.get("mock_session_id"):
            await message.answer(t("nav.what_next", lang), reply_markup=_after_result_kb(data, lang))
        return

    pdf_path = save_report(user_id, pdf_bytes, ".pdf")
    audio_path = answers[0]["path"] if len(answers) == 1 else save_upload(user_id, joined, ".mp3")
    question_text, transcript_text = _combine(questions, transcripts)
    vocabulary = [
        VocabularyItem(word_or_phrase=v.term, meaning=v.meaning, example_sentence=v.example)
        for v in report.vocabulary
    ]
    report_data = {
        "number": number,
        "metrics": check.metrics.as_json(),
        "audio_hash": audio_hash,
        "transcripts": transcripts,
        "questions": questions,
        "question_parts": parts,
        "pronunciation_measured": not report.pron_estimated,
        "resubmission_of": repeat.id if repeat else None,
        # this check's OpenAI cost (estimated from tokens), reading the question
        # photo/file included ("input: ..."; the voices, made on demand, are
        # added later), how long the student waited, and how it was paid
        "cost": usage.merge(usage.summary(calls), data.get("input_cost"), prefix="input: "),
        "seconds": round(time.monotonic() - started),
        "paid_with": await payment_of(user_id, coins, data.get("payer_id")),
        # what each sample is: its source, the band it was aimed at (provisional),
        # the priorities it demonstrates - and its text, spoken when the student
        # taps its button (voice_file_id: Telegram's copy, reused after that)
        "model_answers": [
            {"label": a["label"], "source": a.get("source", "ai_generated"), "aimed_at": a["band"],
             "provisional": a.get("source") != "human_reviewed", "words": a["words"],
             "shows": [s["n"] for s in a.get("shows") or []],
             "text": " ".join(a["paragraphs"]), "voice_file_id": None}
            for a in check.answers
        ],
    }

    async with get_session() as session:
        submission = await crud.save_speaking_submission(
            session,
            user_id=user_id,
            part=part,
            question_text=question_text,
            audio_file_path=audio_path,
            transcript_text=transcript_text,
            overall_band=report.overall_band,
            criteria_scores=report.criteria_scores(),
            mistakes=report.mistakes_json(),
            vocabulary=vocabulary,
            report_data=report_data,
            feedback_pdf_path=pdf_path,
            practice_question_id=data.get("practice_question_id"),
            coins=coins,
            assignment_part_id=data.get("assignment_part_id"),
            payer_id=data.get("payer_id"),
        )
        if data.get("practice_question_id"):
            await crud.mark_practice_complete(
                session,
                user_id=user_id,
                practice_question_id=data["practice_question_id"],
                submission_id=submission.id,
                submission_type="speaking",
            )
        new_words = await crud.add_vocabulary_entries(session, user_id, vocabulary, source="speaking")

    found = len(report.mistakes)
    caption = (t("writing.done_caption", lang, band=report.overall_band, mistakes=found) if found
               else t("writing.done_caption_clean", lang, band=report.overall_band))
    if new_words:
        caption += "\n" + t("vocab.added_after_check", lang, count=new_words)
    await processing_msg.delete()
    await message.answer_document(
        BufferedInputFile(pdf_bytes, filename="IELTS_Feedback.pdf"),
        caption=caption,
        reply_markup=share_card_kb("speaking", submission.id, lang),
    )
    await stickers.send(message.bot, user_id, stickers.for_result(report.overall_band, found))
    if listen:
        await message.answer(
            t("speaking.listen_prompt", lang),
            reply_markup=listen_kb(submission.id, [a["label"] for a in check.answers]),
        )

    if data.get("mock_session_id"):
        await record_mock_test_part(
            message,
            state,
            submission_id=submission.id,
            submission_type="speaking",
            band=report.overall_band,
        )
    else:
        await state.clear()
        await message.answer(t("nav.what_next", lang), reply_markup=_after_result_kb(data, lang))
    await crm_events.check_done("s", submission.id)


_listen_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


@router.callback_query(F.data.startswith(CB_LISTEN_PREFIX))
async def listen_model_answer(callback: CallbackQuery, lang: str) -> None:
    """A model answer spoken (OpenAI TTS) the first time it is asked for, then
    Telegram's saved copy - so a voice is paid for at most once, and only
    when a student actually wants to hear it."""
    await callback.answer()
    try:
        submission_id, index = (int(x) for x in callback.data.removeprefix(CB_LISTEN_PREFIX).split(":"))
    except ValueError:
        return
    user_id = callback.from_user.id
    async with _listen_locks[user_id]:  # a double tap waits and reuses the first voice
        async with get_session() as session:
            submission = await crud.get_speaking_submission(session, submission_id, user_id)
        answers = ((submission.report_data or {}).get("model_answers") or []) if submission else []
        if not (0 <= index < len(answers)) or not answers[index].get("text"):
            await callback.message.answer(t("speaking.listen_gone", lang))
            return
        a = answers[index]
        caption = t("speaking.model_voice_caption", lang, label=a["label"], band=f"{a['aimed_at']:.1f}")
        if a.get("voice_file_id"):
            try:
                await callback.message.answer_voice(a["voice_file_id"], caption=caption)
                return
            except TelegramBadRequest:
                pass  # Telegram no longer has it - speak it again
        try:
            with usage.track(f"tts user={user_id}") as calls:
                audio = await tts.speak(a["text"], a["words"])
            sent = await callback.message.answer_voice(
                BufferedInputFile(audio, filename=f"model_answer_{re.sub(r'[^A-Za-z0-9]+', '_', a['label'])}.ogg"),
                caption=caption,
            )
        except Exception:
            logger.exception("Model answer voice %s for submission %s failed", a["label"], submission_id)
            await callback.message.answer(t("speaking.listen_failed", lang))
            return
        async with get_session() as session:
            await crud.save_model_answer_voice(
                session, submission_id, index, sent.voice.file_id if sent.voice else None, usage.summary(calls)
            )
        await crm_events.cost_updated("s", submission_id)


def _after_result_kb(data: dict, lang: str):
    if data.get("assignment_part_id"):
        return assignment_after_kb(data.get("next_part_id"), lang)
    if data.get("practice_question_id"):
        return after_result_kb(data["practice_question_id"], lang)
    if data.get("part") == "full":
        return check_again_kb(CB_FULL_TEST, lang)
    return check_again_kb(PART_TO_CALLBACK[data["part"]], lang)
