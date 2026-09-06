"""/start: the bot is open to everyone - no admin approval gate - so this
just registers first-time users, has them pick a UI language once (see
bot/handlers/language.py, which continues their onboarding with the welcome
message + sample report), and drops returning users straight into the main
menu. Also handles two deep-link payloads (https://t.me/<bot>?start=<arg>):
`ref_<id>` (bot/handlers/account.py's referral link, signup-only - the
reward comes later, on the referred user's first check) and `group_<id>`
(bot/handlers/group.py's B2B class invite link, works for new AND existing
users, since a current student's teacher may share one after they've
already been using the bot on their own).

(A user can still be manually blocked by setting their row's status to
UserStatus.denied - see bot/middlewares/access_control.py - but nothing in
this flow does that automatically any more.)
"""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.i18n import t
from bot.keyboards.lang_kb import CB_LANG_ONBOARD_PREFIX, lang_picker_kb
from bot.keyboards.main_menu_kb import main_menu_kb
from db import crud
from db.database import get_session

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, lang: str) -> None:
    tg = message.from_user

    if tg.id in settings.admin_ids:
        # AccessControlMiddleware already upserted the admin row.
        await message.answer(
            t("start.admin_welcome", lang, name=tg.full_name), reply_markup=main_menu_kb(lang)
        )
        return

    async with get_session() as session:
        user = await crud.get_user(session, tg.id)
        is_new = user is None
        if is_new:
            referrer_id = await _parse_referrer(session, command.args, new_user_id=tg.id)
            await crud.create_user(session, tg.id, tg.username, tg.full_name, referred_by=referrer_id)

        joined_group_name = await _maybe_join_group(session, command.args, user_id=tg.id)

    if is_new:
        # Language not chosen yet - bot/handlers/language.py's onboarding
        # handler continues with the welcome message + sample report once
        # they tap a flag below. A group join (if any) already happened
        # above regardless of language, silently - the teacher will see
        # them show up in their group's member list either way.
        await message.answer(
            "🌐 Tilni tanlang / Выберите язык:",
            reply_markup=lang_picker_kb(CB_LANG_ONBOARD_PREFIX),
        )
        return

    await message.answer(t("start.welcome", lang, name=tg.full_name), reply_markup=main_menu_kb(lang))
    if joined_group_name:
        await message.answer(t("group.joined_notice", lang, name=joined_group_name))


async def _parse_referrer(session: AsyncSession, args: str | None, *, new_user_id: int) -> int | None:
    """Deep-link payload from https://t.me/<bot>?start=ref_<id> (see
    bot/handlers/account.py). Only trusted once the referrer id actually
    exists and isn't the new user themself."""
    if not args or not args.startswith("ref_"):
        return None
    try:
        referrer_id = int(args.removeprefix("ref_"))
    except ValueError:
        return None
    if referrer_id == new_user_id:
        return None
    referrer = await crud.get_user(session, referrer_id)
    return referrer.id if referrer is not None else None


async def _maybe_join_group(session: AsyncSession, args: str | None, *, user_id: int) -> str | None:
    """Deep-link payload from https://t.me/<bot>?start=group_<id> (see
    bot/handlers/group.py). Returns the group's name if a join happened,
    so the caller can confirm it - None if there was no such payload, the
    group doesn't exist, or they were already a member (join_group is a
    no-op then, and we don't want to re-announce it every /start)."""
    if not args or not args.startswith("group_"):
        return None
    try:
        group_id = int(args.removeprefix("group_"))
    except ValueError:
        return None
    group = await crud.get_group(session, group_id)
    if group is None:
        return None
    existing_membership = await crud.get_group_for_member(session, user_id)
    if existing_membership is not None and existing_membership.id == group.id:
        return None
    await crud.join_group(session, group_id=group.id, user_id=user_id)
    return group.name
