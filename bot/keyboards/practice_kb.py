"""Practice Session keyboards: module picker + a paginated, checkmark-aware
question list (completed questions show ✅ per the spec), plus the
back/next navigation around a single practice question."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.i18n import t
from bot.keyboards.nav_kb import back_button, main_menu_button
from db.models import PracticeModule, PracticeQuestion

CB_MENU = "practice:menu"  # back to the module picker
CB_MODULE_PREFIX = "practice:module:"
CB_QUESTION_PREFIX = "practice:q:"
CB_PAGE_PREFIX = "practice:page:"
CB_BACK_TO_LIST_PREFIX = "practice:back:"  # + question id -> the list page holding it
CB_NEXT_PREFIX = "practice:next:"  # + question id -> the question after it

MODULE_LABELS = {
    PracticeModule.writing_task1: "✍️ Writing Task 1",
    PracticeModule.writing_task2: "✍️ Writing Task 2",
    PracticeModule.speaking_part1: "🎙 Speaking Part 1",
    PracticeModule.speaking_part2: "🎙 Speaking Part 2",
    PracticeModule.speaking_part3: "🎙 Speaking Part 3",
}

QUESTIONS_PER_PAGE = 10


def module_picker_kb(lang: str = "uz") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"{CB_MODULE_PREFIX}{module.value}")]
        for module, label in MODULE_LABELS.items()
    ]
    rows.append([back_button(lang)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def page_of(questions: list[PracticeQuestion], question_id: int) -> int:
    for i, q in enumerate(questions):
        if q.id == question_id:
            return i // QUESTIONS_PER_PAGE
    return 0


def question_list_kb(
    module: PracticeModule,
    questions: list[PracticeQuestion],
    completed_ids: set[int],
    page: int = 0,
    lang: str = "uz",
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

    rows.append([back_button(lang, CB_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def after_result_kb(question_id: int, lang: str) -> InlineKeyboardMarkup:
    """Shown under a graded practice answer, so the student carries on
    instead of being dropped back at the main menu."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("nav.next_question", lang), callback_data=f"{CB_NEXT_PREFIX}{question_id}")],
            [
                InlineKeyboardButton(
                    text=t("nav.question_list", lang), callback_data=f"{CB_BACK_TO_LIST_PREFIX}{question_id}"
                )
            ],
            [main_menu_button(lang)],
        ]
    )
