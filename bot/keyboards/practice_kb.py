"""Practice Session keyboards: module picker + a paginated, checkmark-aware
question list (completed questions show ✅ per the spec)."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.models import PracticeModule, PracticeQuestion

CB_MODULE_PREFIX = "practice:module:"
CB_QUESTION_PREFIX = "practice:q:"
CB_PAGE_PREFIX = "practice:page:"

MODULE_LABELS = {
    PracticeModule.writing_task1: "✍️ Writing Task 1",
    PracticeModule.writing_task2: "✍️ Writing Task 2",
    PracticeModule.speaking_part1: "🎙 Speaking Part 1",
    PracticeModule.speaking_part2: "🎙 Speaking Part 2",
    PracticeModule.speaking_part3: "🎙 Speaking Part 3",
}

QUESTIONS_PER_PAGE = 10


def module_picker_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"{CB_MODULE_PREFIX}{module.value}")]
        for module, label in MODULE_LABELS.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def question_list_kb(
    module: PracticeModule,
    questions: list[PracticeQuestion],
    completed_ids: set[int],
    page: int = 0,
) -> InlineKeyboardMarkup:
    start = page * QUESTIONS_PER_PAGE
    page_items = questions[start : start + QUESTIONS_PER_PAGE]

    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, q in enumerate(page_items, start=start + 1):
        mark = "✅ " if q.id in completed_ids else ""
        row.append(InlineKeyboardButton(text=f"{mark}Q{i}", callback_data=f"{CB_QUESTION_PREFIX}{q.id}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    nav_row = []
    if page > 0:
        nav_row.append(
            InlineKeyboardButton(text="⬅️", callback_data=f"{CB_PAGE_PREFIX}{module.value}:{page - 1}")
        )
    if start + QUESTIONS_PER_PAGE < len(questions):
        nav_row.append(
            InlineKeyboardButton(text="➡️", callback_data=f"{CB_PAGE_PREFIX}{module.value}:{page + 1}")
        )
    if nav_row:
        rows.append(nav_row)

    return InlineKeyboardMarkup(inline_keyboard=rows)
