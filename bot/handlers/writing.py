"""Writing module: Task 1 (Report) / Task 2 (Essay).

Flow: pick task -> send prompt (any format) -> [confirm OCR/parse if needed]
-> send answer (any format) -> [confirm] -> grading -> PDF feedback report
-> "check again" / "next question" buttons (instead of dropping the student
back at the main menu). Also entered directly from Practice
(bot/handlers/practice.py), which presets task_type/prompt_text/
practice_question_id (+ the Task 1 chart, if any) and jumps straight to
`awaiting_answer`. Every step has a "⬅️ Orqaga" button.

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
from pathlib import Path

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from bot.i18n import t
from bot.keyboards.confirm_kb import CB_CONFIRM, CB_EDIT, confirm_edit_kb
from bot.keyboards.group_kb import CB_MY_ASSIGNMENTS, assignment_after_kb
from bot.keyboards.main_menu_kb import BTN_WRITING, MENU_BUTTON_TEXTS
from bot.keyboards.nav_kb import CB_BACK_MAIN, back_kb, check_again_kb
from bot.keyboards.practice_kb import CB_BACK_TO_LIST_PREFIX, after_result_kb
from bot.keyboards.share_kb import share_card_kb
from bot.keyboards.writing_task_kb import (
    CB_NO_CHART,
    CB_TASK1,
    CB_TASK2,
    CB_WRITING_MENU,
    TASK_CALLBACKS,
    chart_kb,
    writing_task_kb,
)
from bot.states.writing_states import WritingStates
from bot.utils import crm_events, stickers
from bot.utils.input_extraction import read_images, read_text_input
from bot.utils.messages import show_screen, strip_buttons
from bot.utils.mock_test_flow import record_mock_test_part
from bot.utils.report_names import report_filename
from bot.utils.coins import writing_kind
from bot.utils.quota import check_price, check_quota, free_check_note, payment_of, send_paywall
from db import crud
from db.database import get_session
from db.models import SourceType
from services.ai import marking_config, usage
from services.ai.essay_marking import PASTE_NOTICE, canon, clean_pasted
from services.ai.openai_client import is_image_error
from services.ai.sample_bank import build_model_answer, needs_from_feedback
from services.ai.schemas import VocabularyItem
from services.ai.visual_reading import ChartImageRejected, judge_ready, read_visual, sheet_text
from services.ai.writing_grader import grade_writing
from services.pdf.report_builder import build_progress, build_writing_report_pdf
from services.storage.file_storage import image_mime, read_file, save_report

logger = logging.getLogger(__name__)
router = Router(name="writing")

MIN_ANSWER_WORDS = {"task1": 150, "task2": 250}
RESUBMISSION_SIMILARITY = 0.9

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

    task_type = "task1" if callback.data == CB_TASK1 else "task2"
    quota = await check_quota(callback.from_user.id, writing_kind(task_type))
    if not quota.allowed:
        await send_paywall(callback.message, lang, quota)
        return

    # Defends against a stale Task1/Task2 button from an old message being
    # tapped while a Mock Test (bot/utils/mock_test_flow.py) is in progress -
    # without this, a leftover mock_session_id in state would make this
    # one-off check get wrongly recorded as a mock test part. A no-op in the
    # normal flow, since open_writing_menu already cleared state.
    await state.clear()
    await state.update_data(task_type=task_type, spend_credit=quota.spend_credit, payer_id=quota.payer_id)
    await state.set_state(WritingStates.awaiting_prompt)
    await show_screen(
        callback, free_check_note(quota, lang) + t("writing.send_prompt", lang), back_kb(lang, CB_WRITING_MENU)
    )


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
    if data.get("assignment_part_id"):
        return CB_MY_ASSIGNMENTS
    return TASK_CALLBACKS[data["task_type"]]  # re-asks for the prompt


async def _receive_input(message: Message, state: FSMContext, field: str, lang: str) -> None:
    task1_question = field == "prompt" and (await state.get_data()).get("task_type") == "task1"
    # A photo of a Task 1 task: only its wording is read as text - the chart's
    # own figures come from the chart reading, never from OCR.
    extracted = await read_text_input(message, lang, ocr_mode="writing_task1_question" if task1_question else "verbatim")
    if extracted is None:
        return
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
        await state.update_data(_editing=field)
        await state.set_state(back_state)
        return

    await state.update_data(**{f"{field}_text": data[f"{field}_candidate_text"]})
    await _advance(callback.message, state, field, lang)


async def _advance(message: Message, state: FSMContext, field: str, lang: str) -> None:
    if field == "prompt":
        data = await state.get_data()
        if data["task_type"] == "task1" and not data.get("prompt_image_paths"):
            # A Task 1 question typed as text: the report is checked against the
            # chart/diagram itself, so ask for its picture first.
            await state.set_state(WritingStates.awaiting_chart)
            await message.answer(t("writing.send_chart", lang), reply_markup=chart_kb(lang, _back_target(data, "answer")))
            return
        await _ask_for_answer(message, state, data, lang)
    else:
        await state.set_state(WritingStates.processing)
        await _run_grading(message, state, lang)


async def _ask_for_answer(message: Message, state: FSMContext, data: dict, lang: str) -> None:
    await state.set_state(WritingStates.awaiting_answer)
    await message.answer(
        t("writing.send_answer", lang, min_words=MIN_ANSWER_WORDS[data["task_type"]]),
        reply_markup=back_kb(lang, _back_target(data, "answer")),
    )


@router.message(WritingStates.awaiting_chart, ~F.text.in_(MENU_BUTTON_TEXTS))
async def receive_chart(message: Message, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    if message.text:
        await message.answer(t("writing.chart_photo_only", lang), reply_markup=chart_kb(lang, _back_target(data, "answer")))
        return
    paths = await read_images(message, lang)
    if paths is None:
        return
    await state.update_data(prompt_image_paths=paths[:MAX_PROMPT_IMAGES])
    await _after_chart(message, state, data, lang)


@router.callback_query(WritingStates.awaiting_chart, F.data == CB_NO_CHART)
async def skip_chart(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    await strip_buttons(callback.message)
    data = await state.get_data()
    if data.get("rechart"):
        await state.update_data(prompt_image_paths=[])
    await _after_chart(callback.message, state, data, lang)


async def _after_chart(message: Message, state: FSMContext, data: dict, lang: str) -> None:
    if data.get("rechart"):
        # The chart asked for again after the answer: check it now.
        await state.update_data(rechart=False)
        await state.set_state(WritingStates.processing)
        await _run_grading(message, state, lang)
        return
    await _ask_for_answer(message, state, data, lang)


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


async def _ask_chart_again(processing_msg: Message, state: FSMContext, data: dict, lang: str, calls) -> None:
    """The chart couldn't be used: the student sends it again (or goes on
    without it) and the check starts right away - the answer is kept and
    nothing was charged; what the reading cost joins the check's cost."""
    cost = usage.merge(data.get("input_cost"), usage.summary(calls)) if calls else data.get("input_cost")
    await state.update_data(rechart=True, prompt_image_paths=[], input_cost=cost)
    await state.set_state(WritingStates.awaiting_chart)
    await processing_msg.edit_text(t("writing.chart_resend", lang), reply_markup=chart_kb(lang, _back_target(data, "answer")))


def _same_text(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, canon(a).lower(), canon(b).lower(), autojunk=False).ratio()


async def _repeat_of(user_id: int, task_type: str, answer_text: str):
    """(an earlier essay with exactly this text, an earlier essay more than
    90% the same, {submission id: essay number}) - either may be None."""
    async with get_session() as session:
        earlier = await crud.get_writing_sessions(session, user_id, task_type)
        numbers = {s.id: await crud.writing_essay_number(session, user_id, s.id) for s in earlier}
    same = next((s for s in earlier if canon(s.answer_text).lower() == canon(answer_text).lower()), None)
    similar = next((s for s in earlier if _same_text(s.answer_text, answer_text) > RESUBMISSION_SIMILARITY), None)
    return same, similar, numbers


async def _run_grading(message: Message, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    user_id = message.chat.id
    # Text pasted from a PDF: lines broken mid-sentence joined, page headers
    # and footers left out - and every count, mark and check made on that.
    cleaned, footers = clean_pasted(data["answer_text"])
    if cleaned != data["answer_text"]:
        data["answer_text"] = cleaned
        await state.update_data(answer_text=cleaned)

    # The very same essay again gets its saved report, not a new (differently
    # scored) check - reports #14 and #15 were one essay graded 8.0 and 8.5,
    # with "progress" between them.
    same, similar, numbers = await _repeat_of(user_id, data["task_type"], data["answer_text"])
    if same is not None and same.feedback_pdf_path and Path(same.feedback_pdf_path).exists():
        logger.info("Writing resubmission by user %s: same text as submission %s", user_id, same.id)
        await message.answer_document(
            BufferedInputFile(read_file(same.feedback_pdf_path), filename=report_filename("writing", same.task_type)),
            caption=t("writing.resubmitted", lang, number=numbers[same.id], band=same.overall_band),
            reply_markup=share_card_kb("writing", same.id, lang),
        )
        if data.get("mock_session_id"):
            await record_mock_test_part(message, state, submission_id=same.id, submission_type="writing",
                                        band=same.overall_band)
        else:
            await state.clear()
            await message.answer(t("nav.what_next", lang), reply_markup=_after_result_kb(data, lang))
        await crm_events.check_repeated("w", user_id, same.id)
        return

    started = time.monotonic()
    processing_msg = await message.answer(t("writing.grading_in_progress", lang))
    calls: list = []
    visual = None
    prompt_images = None

    try:
        async with get_session() as session:
            earlier_essays, previous = await crud.get_writing_history(
                session, user_id, data["task_type"], min_words=marking_config.progress_min_words(data["task_type"]))
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
                visual=visual_data))

        notices = [PASTE_NOTICE] if footers else []
        if data["task_type"] == "task1" and not prompt_images:
            notices.append(CHART_NOT_CHECKED)
        with usage.track(f"writing {data['task_type']} user={user_id}") as calls:
            if prompt_images:
                # The chart read into a checked data sheet whose figures every call
                # uses - and the first sign of a picture OpenAI can't open, before
                # the scoring runs spend anything on it.
                visual = await read_visual(data["prompt_text"], prompt_images)
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
                )
            except BaseException:
                if answer_task is not None:
                    answer_task.cancel()
                raise
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
                        visual=visual_data
                    )
                except Exception:
                    logger.exception("Model answer failed again for user %s - report goes out without it", user_id)
                    answer = None

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
    except (ChartImageRejected, ChartUnreadable) as e:
        logger.warning("Task 1 chart of user %s can't be used (%s: %s) - asking for it again",
                       user_id, type(e).__name__, str(e)[:300])
        await _ask_chart_again(processing_msg, state, data, lang, calls)
        return
    except Exception as e:
        if prompt_images and is_image_error(e):
            logger.warning("OpenAI turned the Task 1 chart of user %s down - asking for it again: %s", user_id, e)
            await _ask_chart_again(processing_msg, state, data, lang, calls)
            return
        logger.exception("Writing grading failed for user %s", user_id)
        await processing_msg.edit_text(t("writing.grading_failed", lang))
        await stickers.send(message.bot, user_id, "oops")
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

    coins = check_price(data, writing_kind(data["task_type"]))
    paid_with = await payment_of(user_id, coins, data.get("payer_id"))
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
            payer_id=data.get("payer_id"),
            report_data={
                # where the model answer came from: internal only, never in the report
                "model_answer": None if answer is None else {
                    "source": answer.source, "aimed_at": answer.band,
                    "provisional": answer.source != "human_reviewed",
                    "shows": [x["n"] for x in answer.shows], "writer": answer.log.get("chosen"),
                    "reference_ids": answer.log.get("reference_ids", []),
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
            },
        )
        if data.get("practice_question_id"):
            await crud.mark_practice_complete(
                session,
                user_id=user_id,
                practice_question_id=data["practice_question_id"],
                submission_id=submission.id,
                submission_type="writing",
            )
        new_words = await crud.add_vocabulary_entries(session, user_id, vocabulary, source="writing")

    found = len(report.mistakes)
    caption = (t("writing.done_caption", lang, band=report.overall_band, mistakes=found) if found
               else t("writing.done_caption_clean", lang, band=report.overall_band))
    if new_words:
        caption += "\n" + t("vocab.added_after_check", lang, count=new_words)
    if CHART_PARTLY_CHECKED in notices:  # some of the student's own figures could not be checked
        caption += "\n\n" + t("writing.chart_partly_unclear", lang)
    elif data["task_type"] == "task1" and not prompt_images:
        caption += "\n\n" + t("writing.chart_not_checked", lang)
    await processing_msg.delete()
    await stickers.send(message.bot, user_id, stickers.for_result(report.overall_band, found))  # the Gotcha moment
    await message.answer_document(
        BufferedInputFile(pdf_bytes, filename=report_filename("writing", data["task_type"])),
        caption=caption,
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
    await crm_events.check_done("w", submission.id)


def _after_result_kb(data: dict, lang: str):
    if data.get("assignment_part_id"):
        return assignment_after_kb(data.get("next_part_id"), lang)
    if data.get("practice_question_id"):
        return after_result_kb(data["practice_question_id"], lang)
    return check_again_kb(TASK_CALLBACKS[data["task_type"]], lang)
