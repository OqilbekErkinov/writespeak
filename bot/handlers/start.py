"""/start: the bot is open to everyone - no admin approval gate - so this
just registers first-time users, has them pick a UI language once (see
bot/handlers/language.py, which continues their onboarding with the welcome
message + sample report), and drops returning users straight into the main
menu. Also handles two deep-link payloads (https://t.me/<bot>?start=<arg>):
`ref_<id>` (bot/handlers/account.py's referral link, signup-only - the
reward comes later, on the referred user's first check) and a group invite
(bot/utils/group_invite.py's signed `g<id>_<sig>`; works for new AND existing
users, since a current student's teacher may share one after they've
already been using the bot on their own). Old unsigned `group_<id>` links
are refused with a "ask your teacher for a new link" note. A coin gift's
`gift_<code>` (bot/utils/gifts.py) is claimed at once by a user already in
the bot, and after the language pick by a new one, whose referrer becomes
the gift's buyer.

(A user blocked from the admin panel - UserStatus.denied, see
bot/middlewares/access_control.py - only gets the "blocked" note here;
nothing in this flow blocks anyone automatically any more.)
"""
from __future__ import annotations

import html
from dataclasses import dataclass

from aiogram import Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.i18n import DEFAULT_LANG, LANGS, t
from bot.keyboards.lang_kb import CB_LANG_ONBOARD_PREFIX, lang_picker_kb
from bot.keyboards.main_menu_kb import main_menu_kb
from bot.middlewares.access_control import DENIED_TEXT
from bot.utils import crm_events, gifts
from bot.utils.group_invite import (
    is_center_payload,
    is_invite_payload,
    joined_notice,
    parse_center_invite,
    parse_invite,
)
from db import crud
from db.database import get_session
from db.models import UserStatus

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, lang: str) -> None:
    tg = message.from_user
    gift_code = gifts.gift_code(command.args)

    if tg.id in settings.admin_ids:
        # AccessControlMiddleware already upserted the admin row.
        await message.answer(
            t("start.admin_welcome", lang, name=tg.full_name), reply_markup=main_menu_kb(lang)
        )
        if gift_code:
            await gifts.claim(message, message.bot, gift_code, lang)
        return

    referrer_id = None
    async with get_session() as session:
        user = await crud.get_user(session, tg.id)
        if user is not None and user.status == UserStatus.denied:
            # Blocked from the admin panel: no menu - and no group join, gift
            # or referral through the link either.
            await message.answer(DENIED_TEXT.get(user.language, DENIED_TEXT["uz"]))
            return
        is_new = user is None
        if is_new:
            referrer_id = await _parse_referrer(session, command.args, new_user_id=tg.id)
            if referrer_id is None and gift_code:
                # Joined through a friend's gift: the friend invited them.
                gift = await crud.get_gift(session, gift_code)
                if gift is not None and gift.claimed_by is None and gift.buyer_id != tg.id:
                    referrer_id = gift.buyer_id
            await crud.create_user(
                session, tg.id, tg.username, tg.full_name, referred_by=referrer_id, coins=settings.welcome_coins
            )

        join = await _maybe_join_group(session, command.args, user_id=tg.id)
        center_join = await _maybe_join_center(session, command.args, user_id=tg.id)

    if join is not None and join.status == "joined":
        await _notify_teacher(message, join)
        await crm_events.group_joined(tg, join.group_name, join.teacher_name, join.member_count)
    if center_join is not None and center_join[0] == "joined":
        await _notify_center_owner(message, center_join[1])
        await crm_events.center_joined(tg, center_join[1].name)
    if is_new:
        await crm_events.user_registered(tg, referrer_id, command.args, join, center_join)

    if is_new:
        # Language not chosen yet - bot/handlers/language.py's onboarding
        # handler continues with the welcome message + sample report once
        # they tap a flag below, and confirms a group join (if any) then.
        if join is not None and join.status == "expired":
            await message.answer(
                "\n\n".join(t("group.join_expired", code) for code in LANGS)
            )
        if center_join is not None:
            await message.answer("\n\n".join(_center_notice(center_join, code) for code in LANGS))
        if gift_code:
            await gifts.remember_for_onboarding(tg.id, gift_code)  # claimed after the language pick
        await message.answer(
            t("account.language_button", DEFAULT_LANG),
            reply_markup=lang_picker_kb(CB_LANG_ONBOARD_PREFIX),
        )
        return

    await message.answer(t("start.welcome", lang, name=tg.full_name), reply_markup=main_menu_kb(lang))
    if gift_code:
        await gifts.claim(message, message.bot, gift_code, lang)
    if join is not None:
        if join.status == "joined":
            await message.answer(joined_notice(join.group_name, join.teacher_name, lang))
        elif join.status == "own":
            await message.answer(t("group.join_own_link", lang))
        elif join.status == "expired":
            await message.answer(t("group.join_expired", lang))
    if center_join is not None:
        await message.answer(_center_notice(center_join, lang))


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


@dataclass
class GroupJoin:
    status: str  # "joined" | "already" | "own" (their own group's link) | "expired"
    group_name: str = ""
    teacher_id: int | None = None
    teacher_name: str = ""
    member_count: int = 0


async def _maybe_join_group(session: AsyncSession, args: str | None, *, user_id: int) -> GroupJoin | None:
    """Deep-link payload of a group invite (bot/utils/group_invite.py). None
    when there's no invite payload at all; "already" isn't re-announced on
    every /start."""
    if not is_invite_payload(args):
        return None
    group_id = parse_invite(args)
    group = await crud.get_group(session, group_id) if group_id is not None else None
    if group is None:
        return GroupJoin("expired")
    teacher = await crud.get_user(session, group.teacher_id)
    teacher_name = teacher.full_name if teacher is not None else ""
    if group.teacher_id == user_id:
        return GroupJoin("own", group.name)
    if not await crud.join_group(session, group_id=group.id, user_id=user_id):
        return GroupJoin("already", group.name)
    count = await crud.get_group_member_count(session, group.id)
    return GroupJoin("joined", group.name, group.teacher_id, teacher_name, count)


async def _maybe_join_center(session: AsyncSession, args: str | None, *, user_id: int):
    """A center's teacher invite (bot/utils/group_invite.py): (status, center)
    with status "joined" | "already" | "owner" | "other" | "expired"; None
    when the payload isn't a center invite at all."""
    if not is_center_payload(args):
        return None
    center_id = parse_center_invite(args)
    center = await crud.get_center(session, center_id) if center_id is not None else None
    if center is None:
        return "expired", None
    return await crud.join_center(session, center.id, user_id), center


def _center_notice(center_join, lang: str) -> str:
    status, center = center_join
    name = html.escape(center.name, quote=False) if center is not None else ""
    return t(f"group.center_join_{status}", lang, name=name)


async def _notify_center_owner(message: Message, center) -> None:
    async with get_session() as session:
        owner = await crud.get_user(session, center.owner_id)
    if owner is None:
        return
    try:
        await message.bot.send_message(
            owner.id,
            t("group.center_new_teacher", owner.language or "uz", name=html.escape(message.from_user.full_name, quote=False)),
        )
    except Exception:
        pass


async def _notify_teacher(message: Message, join: GroupJoin) -> None:
    async with get_session() as session:
        teacher = await crud.get_user(session, join.teacher_id)
    if teacher is None:
        return
    try:
        await message.bot.send_message(
            teacher.id,
            t(
                "group.new_member_notify",
                teacher.language or "uz",
                name=html.escape(message.from_user.full_name, quote=False),
                count=join.member_count,
            ),
        )
    except Exception:
        pass  # the teacher may have blocked the bot - joining still worked
