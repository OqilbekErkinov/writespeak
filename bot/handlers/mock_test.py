"""Mock Test: chains Writing Task 1+2 and Speaking Part 1-3 into one
simulated IELTS session - see bot/utils/mock_test_flow.py for how each part
hands off to the next, and its overall-band caveat (Reading/Listening
aren't covered by this bot).
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.config import settings
from bot.keyboards.main_menu_kb import BTN_MOCK_TEST
from bot.keyboards.payment_kb import buy_credits_kb
from bot.utils.mock_test_flow import advance_mock_test
from db import crud
from db.database import get_session

router = Router(name="mock_test")

PART_COUNT = 5  # Writing Task 1+2, Speaking Part 1-3


@router.message(F.text == BTN_MOCK_TEST)
async def start_mock_test(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_id = message.from_user.id

    async with get_session() as session:
        has_subscription = await crud.has_active_subscription(session, user_id)
        enough = has_subscription
        if not has_subscription:
            used_today = await crud.get_daily_submission_count(session, user_id)
            user = await crud.get_user(session, user_id)
            remaining_free = max(0, settings.free_daily_limit - used_today)
            credit_balance = user.credit_balance if user is not None else 0
            enough = (remaining_free + credit_balance) >= PART_COUNT

    if not enough:
        await message.answer(
            f"🎯 <b>Mock Test</b> — Writing Task 1+2 va Speaking Part 1-3'ni ketma-ket "
            f"topshirasiz, bu {PART_COUNT} ta tekshiruv sarflaydi. Sizda yetarli bepul/kredit "
            "imkoniyat yo'q — avval sotib oling:",
            reply_markup=buy_credits_kb(),
        )
        return

    async with get_session() as session:
        mock = await crud.create_mock_test_session(session, user_id)

    await message.answer(
        "🎯 <b>Mock Test boshlandi!</b>\n\n5 qismdan iborat: Writing Task 1, Writing Task 2, "
        "Speaking Part 1, 2, 3. Har birini alohida tekshiruvdek to'ldirasiz, oxirida umumiy "
        "natija chiqadi."
    )
    await advance_mock_test(message, state, mock.id)
