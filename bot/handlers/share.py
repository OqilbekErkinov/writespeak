"""Renders the shareable band-score card for a finished submission - see
bot/keyboards/share_kb.py for where the triggering button is attached."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery

from bot.config import settings
from bot.i18n import t
from bot.keyboards.share_kb import CB_SHARE_PREFIX
from db import crud
from db.database import get_session
from services.image.share_card import build_share_card

router = Router(name="share")

_TASK_LABELS = {"task1": "Writing Task 1", "task2": "Writing Task 2"}
_PART_LABELS = {"part1": "Speaking Part 1", "part2": "Speaking Part 2", "part3": "Speaking Part 3"}


@router.callback_query(F.data.startswith(CB_SHARE_PREFIX))
async def send_share_card(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    _, submission_type, submission_id_str = callback.data.split(":")
    submission_id = int(submission_id_str)
    user_id = callback.from_user.id

    async with get_session() as session:
        if submission_type == "writing":
            submission = await crud.get_writing_submission(session, submission_id, user_id)
            task_label = _TASK_LABELS.get(submission.task_type.value) if submission else None
        else:
            submission = await crud.get_speaking_submission(session, submission_id, user_id)
            task_label = _PART_LABELS.get(submission.part.value) if submission else None

    if submission is None or submission.overall_band is None:
        await callback.message.answer(t("share.not_found", lang))
        return

    png_bytes = build_share_card(
        student_name=callback.from_user.full_name,
        band=submission.overall_band,
        task_label=task_label or "IELTS",
        bot_username=settings.telegram_bot_username,
    )
    await callback.message.answer_photo(
        BufferedInputFile(png_bytes, filename="band_score.png"),
        caption=t("share.caption", lang),
    )
