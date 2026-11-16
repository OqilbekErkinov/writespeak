"""The student's result card for an Instagram / Telegram story (the owner, 2026-11-16): sent by itself right after
every report (bot/handlers/writing.py, speaking.py: send_story), and again - in another design - from the
report's "📸" button or the card's own "🎨 Boshqa dizayn". services/image/story_card.py draws it: the overall
band and the four criteria, a kind word that fits the result, seven designs; the student's previous design is
never repeated next (Redis story:last:<user>). The caption carries their referral link, so a forwarded card
brings friends in as their invitees (bot/keyboards/account_kb.py referral_link).

Until 2026-11-16 this was one square card, made only on the button, with the overall band and a mistakes count.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from bot.config import settings
from bot.i18n import t
from bot.keyboards.account_kb import referral_link
from bot.keyboards.share_kb import CB_SHARE_PREFIX, instagram_url
from db import crud
from db.database import get_session
from services.image.story_card import build_story_card

logger = logging.getLogger(__name__)
router = Router(name="share")

LAST_KEY = "story:last:{}"  # + user id: the design of their last card, so the next one differs
LAST_TTL = 90 * 86400


def instagram_link() -> str:
    """@bandzilla_uz as a link to our Instagram (the owner, 2026-11-16): a bare @name in a Telegram message opens
    the Telegram account of that name - our channel - not the Instagram page."""
    return f'<a href="{instagram_url()}">@{settings.instagram_username}</a>'


def story_kb(kind: str, submission_id: int, lang: str) -> InlineKeyboardMarkup:
    """Under the card: our Instagram (the owner, 2026-11-16: "post it and support us" - a bot can't open
    Instagram's story editor with the picture, so the button opens the app on our page), and another design of
    the same result."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("story.instagram_btn", lang), url=instagram_url())],
        [InlineKeyboardButton(text=t("story.again", lang), callback_data=f"{CB_SHARE_PREFIX}{kind}:{submission_id}")],
    ])


async def _render(kind: str, submission, user_id: int, lang: str) -> bytes:
    from bot.utils.crm import redis  # noqa: PLC0415

    key = LAST_KEY.format(user_id)
    try:
        avoid = await redis().get(key)
    except Exception:  # noqa: BLE001 - a repeat design at worst
        avoid = None
    task = submission.task_type.value if kind == "writing" else submission.part.value
    # drawn in a thread: ~0.5-1 s of Pillow work that would otherwise hold every other student's updates
    png, design = await asyncio.to_thread(
        build_story_card, module=kind, task=task, band=float(submission.overall_band),
        criteria=dict(submission.criteria_scores or {}), lang=lang, bot_username=settings.telegram_bot_username,
        social=settings.instagram_username, avoid=avoid)
    try:
        await redis().set(key, design, ex=LAST_TTL)
    except Exception:  # noqa: BLE001
        pass
    return png


async def _send(bot: Bot, user_id: int, kind: str, submission, lang: str) -> None:
    png = await _render(kind, submission, user_id, lang)
    await bot.send_photo(
        user_id, BufferedInputFile(png, filename="bandzilla_story.png"),
        caption=t("story.caption", lang, instagram=instagram_link()) + "\n\n"
        + t("share.invite_line", lang, link=referral_link(user_id)),
        reply_markup=story_kb(kind, submission.id, lang),
    )


async def send_story(bot: Bot, user_id: int, kind: str, submission, lang: str) -> None:
    """The story card right after a report. Never raises - the report itself has gone already."""
    if submission is None or submission.overall_band is None:
        return
    try:
        await _send(bot, user_id, kind, submission, lang)
    except Exception:  # noqa: BLE001 - blocked the bot, Telegram away, a drawing bug: never the check's problem
        logger.warning("Story card for %s %s not sent", kind, getattr(submission, "id", "?"), exc_info=True)


@router.callback_query(F.data.startswith(CB_SHARE_PREFIX))
async def send_share_card(callback: CallbackQuery, lang: str) -> None:
    """The report's "📸" button and the card's "🎨 Boshqa dizayn": a card of that result, in a design other than
    the last one."""
    await callback.answer()
    try:
        _, kind, raw_id = callback.data.split(":")
        submission_id = int(raw_id)
    except ValueError:
        return
    user_id = callback.from_user.id
    async with get_session() as session:
        if kind == "writing":
            submission = await crud.get_writing_submission(session, submission_id, user_id)
        else:
            submission = await crud.get_speaking_submission(session, submission_id, user_id)
    if submission is None or submission.overall_band is None:
        await callback.message.answer(t("share.not_found", lang))
        return
    try:
        await _send(callback.bot, user_id, kind, submission, lang)
    except Exception:  # noqa: BLE001
        logger.warning("Story card for %s %s not sent", kind, submission_id, exc_info=True)
