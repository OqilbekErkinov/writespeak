"""My Works, now part of Hisobim (bot/handlers/account.py): re-downloading a
report from the history, and the old My Works buttons still sitting in
chats, which open the new screens."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery

from bot.handlers.account import account_home, show_history, show_stats
from bot.i18n import t
from bot.keyboards.my_works_kb import CB_DOWNLOAD_PREFIX, CB_HISTORY, CB_MENU, CB_STATS
from bot.utils.report_names import report_filename
from db import crud
from db.database import get_session
from services.storage.file_storage import read_file

router = Router(name="my_works")


@router.callback_query(F.data == CB_MENU)
async def old_menu(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await account_home(callback, state, lang)


@router.callback_query(F.data == CB_HISTORY)
async def old_history(callback: CallbackQuery, lang: str) -> None:
    await show_history(callback, 0, lang)


@router.callback_query(F.data == CB_STATS)
async def old_stats(callback: CallbackQuery, lang: str) -> None:
    await show_stats(callback, lang)


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

    task_or_part = submission.task_type if sub_type == "writing" else submission.part
    await callback.message.answer_document(BufferedInputFile(content, filename=report_filename(sub_type, task_or_part)))
