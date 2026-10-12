"""Practice Session keyboards: module picker (with the student's progress),
a paginated question list (numbers match the list in the message text,
completed questions show ✅), a random-question button, a question-type
filter for Writing, plus the back/next navigation around a single
practice question."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.i18n import t
from bot.keyboards.nav_kb import back_button, main_menu_button
from db.models import PracticeModule, PracticeQuestion

CB_MENU = "practice:menu"  # back to the module picker
CB_MODULE_PREFIX = "practice:module:"
CB_QUESTION_PREFIX = "practice:q:"
CB_PAGE_PREFIX = "practice:page:"  # + module:kind:page
CB_KINDS_PREFIX = "practice:kinds:"  # + module -> the question-type filter
CB_RANDOM_PREFIX = "practice:rand:"  # + module:kind
CB_BACK_TO_LIST_PREFIX = "practice:back:"  # + question id -> the list page holding it
CB_NEXT_PREFIX = "practice:next:"  # + question id -> the next question not done yet
CB_NOOP = "practice:noop"  # the page counter between the arrows

ALL_KINDS = "all"

MODULE_LABELS = {
    PracticeModule.writing_task1: "✍️ Writing Task 1",
    PracticeModule.writing_task2: "✍️ Writing Task 2",
    PracticeModule.speaking_part1: "🎙 Speaking Part 1",
    PracticeModule.speaking_part2: "🎙 Speaking Part 2",
    PracticeModule.speaking_part3: "🎙 Speaking Part 3",
}

# Question types students filter Writing by (services/ai/sample_bank.py's
# regex_subtype names them), in the order shown. Exam terms stay in English.
KIND_LABELS = {
    PracticeModule.writing_task1: {
        "bar": "📊 Bar chart",
        "line": "📈 Line graph",
        "pie": "🥧 Pie chart",
        "table": "🔢 Table",
        "map": "🗺 Map",
        "process": "⚙️ Process / diagram",
        "mixed": "🧩 Mixed / other",
    },
    PracticeModule.writing_task2: {
        "opinion": "💬 Opinion (agree / disagree)",
        "discussion": "⚖️ Discussion (both views)",
        "advantages_disadvantages": "➕➖ Advantages & disadvantages",
        "problem_solution": "🛠 Problem & solution",
        "two_part": "❓ Two-part question",
    },
}

QUESTIONS_PER_PAGE = 10


def module_picker_kb(lang: str = "uz", stats: dict[PracticeModule, tuple[int, int]] | None = None) -> InlineKeyboardMarkup:
    rows = []
    for module, label in MODULE_LABELS.items():
        if stats and stats.get(module, (0, 0))[1]:
            done, total = stats[module]
            label = f"{label} · {done}/{total}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"{CB_MODULE_PREFIX}{module.value}")])
    rows.append([back_button(lang)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def page_of(questions: list[PracticeQuestion], question_id: int) -> int:
    for i, q in enumerate(questions):
        if q.id == question_id:
            return i // QUESTIONS_PER_PAGE
    return 0


def page_count(questions: list[PracticeQuestion]) -> int:
    return max(1, -(-len(questions) // QUESTIONS_PER_PAGE))


def question_list_kb(
    module: PracticeModule,
    questions: list[PracticeQuestion],
    completed_ids: set[int],
    page: int = 0,
    lang: str = "uz",
    kind: str = ALL_KINDS,
) -> InlineKeyboardMarkup:
    start = page * QUESTIONS_PER_PAGE
    page_items = questions[start : start + QUESTIONS_PER_PAGE]

    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, q in enumerate(page_items, start=start + 1):
        mark = "✅" if q.id in completed_ids else ""
        row.append(InlineKeyboardButton(text=f"{mark}{i}", callback_data=f"{CB_QUESTION_PREFIX}{q.id}"))
        if len(row) == 5:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    pages = page_count(questions)
    if pages > 1:
        page_cb = f"{CB_PAGE_PREFIX}{module.value}:{kind}:"
        rows.append(
            [
                InlineKeyboardButton(text="⬅️", callback_data=f"{page_cb}{(page - 1) % pages}"),
                InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data=CB_NOOP),
                InlineKeyboardButton(text="➡️", callback_data=f"{page_cb}{(page + 1) % pages}"),
            ]
        )

    actions = []
    if questions:
        actions.append(
            InlineKeyboardButton(
                text=t("practice.btn_random", lang), callback_data=f"{CB_RANDOM_PREFIX}{module.value}:{kind}"
            )
        )
    if module in KIND_LABELS:
        actions.append(
            InlineKeyboardButton(text=t("practice.btn_filter", lang), callback_data=f"{CB_KINDS_PREFIX}{module.value}")
        )
    if actions:
        rows.append(actions)

    rows.append([back_button(lang, CB_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kind_picker_kb(module: PracticeModule, counts: dict[str, int], lang: str) -> InlineKeyboardMarkup:
    """The question types of a Writing module, with how many questions each has."""
    page_cb = f"{CB_PAGE_PREFIX}{module.value}:"
    total = sum(counts.values())
    rows = [[InlineKeyboardButton(text=f"{t('practice.kind_all', lang)} ({total})", callback_data=f"{page_cb}{ALL_KINDS}:0")]]
    for kind, label in KIND_LABELS[module].items():
        if counts.get(kind):
            rows.append([InlineKeyboardButton(text=f"{label} ({counts[kind]})", callback_data=f"{page_cb}{kind}:0")])
    rows.append([back_button(lang, f"{page_cb}{ALL_KINDS}:0")])
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
