"""My Works: personal archive (history + feedback-file re-download) and a
progress-trend statistics chart (Bosqich 8)."""
from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from bot.i18n import t
from bot.keyboards.main_menu_kb import BTN_MY_WORKS
from bot.keyboards.my_works_kb import (
    CB_DOWNLOAD_PREFIX,
    CB_HISTORY,
    CB_MENU,
    CB_STATS,
    history_list_kb,
    my_works_menu_kb,
)
from bot.utils.messages import show_screen
from db import crud
from db.database import get_session
from services.storage.file_storage import read_file

router = Router(name="my_works")


@router.message(F.text == BTN_MY_WORKS)
async def open_my_works(message: Message, state: FSMContext, lang: str) -> None:
    await state.clear()  # abandon any in-progress Writing/Speaking flow
    await message.answer(t("mywork.title", lang), reply_markup=my_works_menu_kb(lang))


@router.callback_query(F.data == CB_MENU)
async def back_to_my_works(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    await show_screen(callback, t("mywork.title", lang), my_works_menu_kb(lang))


@router.callback_query(F.data == CB_HISTORY)
async def show_history(callback: CallbackQuery, lang: str) -> None:
    async with get_session() as session:
        items = await crud.get_user_history(session, callback.from_user.id, limit=20)

    if not items:
        await callback.answer(t("mywork.empty", lang), show_alert=True)
        return

    await callback.answer()
    await show_screen(callback, t("mywork.history_header", lang), history_list_kb(items, lang))


@router.callback_query(F.data.startswith(CB_DOWNLOAD_PREFIX))
async def download_report(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    sub_type, sub_id_str = callback.data.removeprefix(CB_DOWNLOAD_PREFIX).split(":")
    sub_id = int(sub_id_str)

    async with get_session() as session:
        if sub_type == "writing":
            submission = await crud.get_writing_submission(session, sub_id, callback.from_user.id)
        else:
            submission = await crud.get_speaking_submission(session, sub_id, callback.from_user.id)

    if submission is None or not submission.feedback_pdf_path:
        await callback.message.answer(t("mywork.file_not_found", lang))
        return

    try:
        content = read_file(submission.feedback_pdf_path)
    except FileNotFoundError:
        await callback.message.answer(t("mywork.file_missing_on_server", lang))
        return

    await callback.message.answer_document(BufferedInputFile(content, filename="IELTS_Feedback.pdf"))


@router.callback_query(F.data == CB_STATS)
async def show_stats(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    async with get_session() as session:
        history = await crud.get_score_history(session, callback.from_user.id)

    if len(history) < 2:
        await callback.message.answer(t("mywork.need_more_data", lang))
        return

    dates = [row[0] for row in history]
    scores = [row[1] for row in history]

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(dates, scores, marker="o", color="#0f1c3f", linewidth=2)
    ax.set_ylim(3.5, 9.5)
    ax.set_ylabel(t("mywork.chart_ylabel", lang))
    ax.set_title(t("mywork.chart_title", lang))
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)

    # Trend: average of the last 3 scores vs. the 3 before that (or all
    # earlier ones if fewer than 3 are available).
    recent = scores[-3:]
    previous = scores[-6:-3] if len(scores) >= 6 else scores[:-3]
    trend_text = t("mywork.trend_stable", lang)
    if previous:
        recent_avg = sum(recent) / len(recent)
        previous_avg = sum(previous) / len(previous)
        if recent_avg - previous_avg >= 0.25:
            trend_text = t("mywork.trend_up", lang)
        elif previous_avg - recent_avg >= 0.25:
            trend_text = t("mywork.trend_down", lang)

    await callback.message.answer_photo(
        BufferedInputFile(buf.read(), filename="stats.png"),
        caption=t("mywork.stats_caption", lang, score=scores[-1], trend=trend_text),
    )
