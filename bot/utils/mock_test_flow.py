"""Shared orchestration for Mock Test sessions (bot/handlers/mock_test.py):
chains Writing Task 1+2 and Speaking Part 1-3 back to back, reusing each
module's existing single-check flow (WritingStates/SpeakingStates) rather
than duplicating it. writing.py/speaking.py call `record_mock_test_part`
right after a successful grading - if `mock_session_id` isn't in the FSM
state, it's a no-op and the caller handles the normal single-check ending
itself.
"""
from __future__ import annotations

from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.keyboards.main_menu_kb import main_menu_kb
from bot.states.speaking_states import SpeakingStates
from bot.states.writing_states import WritingStates
from bot.utils.quota import check_quota, send_paywall
from db import crud
from db.database import get_session
from db.models import MockTestPart, MockTestPartType

TASK_LABELS = {"task1": "Writing Task 1 (Report)", "task2": "Writing Task 2 (Essay)"}
PART_LABELS = {"part1": "Speaking Part 1", "part2": "Speaking Part 2", "part3": "Speaking Part 3"}

# part_type -> (which handler module owns it, the task_type/part key it expects)
_PART_TO_FLOW: dict[MockTestPartType, tuple[str, str]] = {
    MockTestPartType.writing_task1: ("writing", "task1"),
    MockTestPartType.writing_task2: ("writing", "task2"),
    MockTestPartType.speaking_part1: ("speaking", "part1"),
    MockTestPartType.speaking_part2: ("speaking", "part2"),
    MockTestPartType.speaking_part3: ("speaking", "part3"),
}


async def record_mock_test_part(
    message: Message,
    state: FSMContext,
    *,
    submission_id: int,
    submission_type: str,
    band: float | None,
) -> None:
    """Call right after saving a Writing/Speaking submission, only if
    `data["mock_session_id"]` is set. Logs this part's result, then advances
    to the next part or shows the final summary."""
    data = await state.get_data()
    mock_session_id = data["mock_session_id"]
    part_type = MockTestPartType(data["mock_part_type"])

    async with get_session() as session:
        await crud.complete_mock_test_part(
            session, mock_session_id, part_type, submission_id, submission_type, band
        )

    await advance_mock_test(message, state, mock_session_id)


async def advance_mock_test(message: Message, state: FSMContext, mock_session_id: int) -> None:
    """Starts the next incomplete part of the session, or finishes it if all
    5 are done. Also the entry point mock_test.py uses to kick off part 1."""
    async with get_session() as session:
        parts = await crud.get_mock_test_parts(session, mock_session_id)
    next_part = next((p for p in parts if p.submission_id is None), None)

    if next_part is None:
        await _finish(message, state, mock_session_id, parts)
        return

    allowed, spend_credit = await check_quota(message.chat.id)
    if not allowed:
        await state.clear()
        await message.answer(
            "🎯 Mock Test davom etishi uchun imkoniyat yetmadi. Kredit sotib olib, "
            "\"🎯 Mock Test\" tugmasi orqali qaytadan boshlashingiz mumkin (avval "
            "tugatgan qismlaringiz natijalari saqlanib qoladi)."
        )
        await send_paywall(message)
        return

    kind, sub_key = _PART_TO_FLOW[next_part.part_type]
    await state.update_data(
        mock_session_id=mock_session_id,
        mock_part_type=next_part.part_type.value,
        spend_credit=spend_credit,
    )
    if kind == "writing":
        await state.update_data(task_type=sub_key)
        await state.set_state(WritingStates.awaiting_prompt)
        await message.answer(
            f"🎯 <b>Mock Test</b> — {TASK_LABELS[sub_key]}\n\n"
            "✏️ Savol (prompt) matnini yuboring — matn, rasm, PDF yoki DOCX shaklida."
        )
    else:
        await state.update_data(part=sub_key)
        await state.set_state(SpeakingStates.awaiting_question)
        await message.answer(
            f"🎯 <b>Mock Test</b> — {PART_LABELS[sub_key]}\n\n"
            "✏️ Savol matnini yuboring — matn, rasm, PDF yoki DOCX shaklida."
        )


async def _finish(
    message: Message, state: FSMContext, mock_session_id: int, parts: list[MockTestPart]
) -> None:
    async with get_session() as session:
        await crud.finish_mock_test_session(session, mock_session_id)
    await state.clear()

    writing_bands = [p.band for p in parts if p.part_type.value.startswith("writing") and p.band]
    speaking_bands = [p.band for p in parts if p.part_type.value.startswith("speaking") and p.band]
    w_avg = sum(writing_bands) / len(writing_bands) if writing_bands else None
    s_avg = sum(speaking_bands) / len(speaking_bands) if speaking_bands else None
    section_avgs = [b for b in (w_avg, s_avg) if b is not None]
    overall = round(sum(section_avgs) / len(section_avgs) * 2) / 2 if section_avgs else None

    lines = ["🎯 <b>Mock Test yakunlandi!</b>", ""]
    if w_avg is not None:
        lines.append(f"✍️ Writing (o'rtacha): <b>{w_avg:.1f}</b>")
    if s_avg is not None:
        lines.append(f"🎙 Speaking (o'rtacha): <b>{s_avg:.1f}</b>")
    if overall is not None:
        lines.append("")
        lines.append(f"📊 Taxminiy umumiy ball: <b>{overall}</b>")
        lines.append(
            "\n<i>Eslatma: haqiqiy IELTS umumiy bali 4 ta bo'lim (shu jumladan Reading va "
            "Listening) asosida hisoblanadi — bu faqat Writing+Speaking bo'yicha taxmin.</i>"
        )
    await message.answer("\n".join(lines), reply_markup=main_menu_kb())
