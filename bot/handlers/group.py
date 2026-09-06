"""Guruh / Группа (B2B): a teacher opens one group (MVP: 1 group per
teacher, see db.models.Group), students join via a
https://t.me/<bot>?start=group_<id> deep link (bot/handlers/start.py's
_maybe_join_group), and the teacher can see per-student stats and gift
credits from their OWN balance to specific students - this reuses the
existing credit/payment system entirely rather than adding a second one, so
a language center's natural path is: buy a bulk credit bundle for
themselves (bot/handlers/payments.py), then hand it out to their students
here.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import settings
from bot.i18n import t
from bot.keyboards.group_kb import (
    CB_GROUP_CREATE,
    CB_GROUP_GIFT,
    CB_GROUP_GIFT_AMOUNT_PREFIX,
    CB_GROUP_GIFT_STUDENT_PREFIX,
    CB_GROUP_STATS,
    gift_amount_picker_kb,
    gift_student_picker_kb,
    no_group_kb,
    teacher_actions_kb,
)
from bot.keyboards.main_menu_kb import BTN_GROUP_RU, BTN_GROUP_UZ, MENU_BUTTON_TEXTS
from bot.states.group_states import GroupStates
from db import crud
from db.database import get_session

router = Router(name="group")


def _group_link(group_id: int) -> str:
    return f"https://t.me/{settings.telegram_bot_username}?start=group_{group_id}"


@router.message(F.text.in_({BTN_GROUP_UZ, BTN_GROUP_RU}))
async def open_group(message: Message, state: FSMContext, lang: str) -> None:
    await state.clear()
    user_id = message.from_user.id

    async with get_session() as session:
        owned = await crud.get_owned_group(session, user_id)
        if owned is not None:
            count = await crud.get_group_member_count(session, owned.id)
            await message.answer(
                t("group.teacher_view", lang, name=owned.name, count=count, link=_group_link(owned.id)),
                reply_markup=teacher_actions_kb(lang),
            )
            return

        member_group = await crud.get_group_for_member(session, user_id)
        if member_group is not None:
            teacher = await crud.get_user(session, member_group.teacher_id)
            await message.answer(
                t(
                    "group.member_view",
                    lang,
                    group_name=member_group.name,
                    teacher_name=teacher.full_name if teacher is not None else "?",
                )
            )
            return

    await message.answer(t("group.no_group_prompt", lang), reply_markup=no_group_kb(lang))


@router.callback_query(F.data == CB_GROUP_CREATE)
async def start_create_group(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    await state.set_state(GroupStates.awaiting_name)
    await callback.message.answer(t("group.ask_name", lang))


@router.message(GroupStates.awaiting_name, ~F.text.in_(MENU_BUTTON_TEXTS))
async def receive_group_name(message: Message, state: FSMContext, lang: str) -> None:
    name = (message.text or "").strip()[:255]
    await state.clear()
    if not name:
        return

    async with get_session() as session:
        # A teacher can only own one group (db.models.Group.teacher_id is
        # unique) - re-check here for a friendly no-op instead of a DB error
        # in the rare case they somehow reach this twice.
        if await crud.get_owned_group(session, message.from_user.id) is not None:
            return
        group = await crud.create_group(session, message.from_user.id, name)

    await message.answer(t("group.created", lang, name=group.name, link=_group_link(group.id)))


@router.callback_query(F.data == CB_GROUP_STATS)
async def show_group_stats(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    async with get_session() as session:
        group = await crud.get_owned_group(session, callback.from_user.id)
        if group is None:
            await callback.message.answer(t("group.not_found", lang))
            return
        members = await crud.get_group_members(session, group.id)
        if not members:
            await callback.message.answer(t("group.stats_empty", lang))
            return

        lines = [t("group.stats_header", lang, name=group.name)]
        for member in members:
            activity = await crud.get_member_activity(session, member.id)
            if activity["total"] == 0:
                lines.append(t("group.stats_row_no_work", lang, name=member.full_name))
            else:
                last_active = (
                    activity["last_active"].strftime("%d.%m.%Y") if activity["last_active"] else "-"
                )
                lines.append(
                    t(
                        "group.stats_row",
                        lang,
                        name=member.full_name,
                        total=activity["total"],
                        avg=activity["avg_band"],
                        last_active=last_active,
                    )
                )

    await callback.message.answer("\n".join(lines))


@router.callback_query(F.data == CB_GROUP_GIFT)
async def start_gift(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    async with get_session() as session:
        group = await crud.get_owned_group(session, callback.from_user.id)
        if group is None:
            await callback.message.answer(t("group.not_found", lang))
            return
        members = await crud.get_group_members(session, group.id)
        teacher = await crud.get_user(session, callback.from_user.id)

    if not members:
        await callback.message.answer(t("group.gift_no_students", lang))
        return

    balance = teacher.credit_balance if teacher is not None else 0
    await callback.message.answer(
        t("group.gift_choose_student", lang, balance=balance),
        reply_markup=gift_student_picker_kb(members),
    )


@router.callback_query(F.data.startswith(CB_GROUP_GIFT_STUDENT_PREFIX))
async def choose_gift_amount(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    student_id = int(callback.data.removeprefix(CB_GROUP_GIFT_STUDENT_PREFIX))

    async with get_session() as session:
        teacher = await crud.get_user(session, callback.from_user.id)
        student = await crud.get_user(session, student_id)

    if student is None:
        await callback.message.answer(t("group.not_found", lang))
        return

    balance = teacher.credit_balance if teacher is not None else 0
    if balance <= 0:
        await callback.message.answer(t("group.gift_insufficient", lang))
        return

    await callback.message.answer(
        t("group.gift_choose_amount", lang, name=student.full_name),
        reply_markup=gift_amount_picker_kb(student_id, balance),
    )


@router.callback_query(F.data.startswith(CB_GROUP_GIFT_AMOUNT_PREFIX))
async def do_gift(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    student_id_str, amount_str = callback.data.removeprefix(CB_GROUP_GIFT_AMOUNT_PREFIX).split(":")
    student_id = int(student_id_str)
    amount = int(amount_str)

    async with get_session() as session:
        success = await crud.gift_credits(
            session, teacher_id=callback.from_user.id, student_id=student_id, amount=amount
        )
        student = await crud.get_user(session, student_id)

    if not success:
        await callback.message.answer(t("group.gift_insufficient", lang))
        return

    student_name = student.full_name if student is not None else "?"
    await callback.message.answer(t("group.gift_success", lang, name=student_name, amount=amount))
    try:
        student_lang = student.language if student is not None else "uz"
        await callback.bot.send_message(
            student_id, t("group.gift_notify_student", student_lang, amount=amount)
        )
    except Exception:
        pass
