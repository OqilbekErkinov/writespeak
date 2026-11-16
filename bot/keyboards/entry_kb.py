"""Ways into a check where students stall (2026-11-16, the growth pass before the launch): the buttons under the
last onboarding tip and a payment approval, under a re-engagement reminder (bot/utils/nudges.py), under the
smart entry's "is this your essay?" (bot/handlers/fallback.py), and the top-up row under a report when the
coins left are fewer than a check costs.

Under a message that should stay in the chat (the onboarding tip, the payment approval) "✍️ Writing" /
"🎙 Speaking" open their picker as a new message (CB_OPEN_WRITING / CB_OPEN_SPEAKING, handled in
bot/handlers/fallback.py); under a reminder the existing Task 2 / Part 1 / Practice callbacks are used, which
turn the reminder itself into the step.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.i18n import t
from bot.keyboards.account_kb import CB_NUDGES_OFF, CB_TOPUP_STUDENT
from bot.keyboards.main_menu_kb import BTN_SPEAKING, BTN_WRITING
from bot.keyboards.practice_kb import CB_MENU, MODULE_LABELS, ready_question_button
from bot.keyboards.speaking_part_kb import CB_PART1
from bot.keyboards.writing_task_kb import CB_TASK2
from db.models import PracticeModule

CB_OPEN_WRITING = "entry:writing"
CB_OPEN_SPEAKING = "entry:speaking"
# The smart entry's choice: + "w:task1:<token>" / "s:part2:<token>" - the token names the kept text or voice
# (fallback.py's KEPT_KEY); "" when nothing is kept (a photo or a file: the flow starts and says what to send)
CB_SMART_PREFIX = "smart:"


def _b(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def start_row(lang: str) -> list[InlineKeyboardButton]:
    """"✍️ Writing" / "🎙 Speaking": the Task or Part picker as a new message (exam names stay in English)."""
    return [_b(BTN_WRITING, CB_OPEN_WRITING), _b(BTN_SPEAKING, CB_OPEN_SPEAKING)]


def onboarding_kb(lang: str) -> InlineKeyboardMarkup:
    """Under the last onboarding message (bot/handlers/language.py), which had no button: start a check, or take
    a ready question."""
    return InlineKeyboardMarkup(inline_keyboard=[start_row(lang), [ready_question_button(lang)]])


def approval_start_kb(lang: str) -> InlineKeyboardMarkup:
    """Under "coins added" when no check is waiting for them (bot/handlers/payments.py approval_keyboard)."""
    return InlineKeyboardMarkup(inline_keyboard=[start_row(lang)])


def topup_button(lang: str) -> InlineKeyboardButton:
    """"💳 Hisobni to'ldirish": straight to the students' packs."""
    return _b(t("acc.btn_topup", lang), CB_TOPUP_STUDENT)


def with_topup(kb: InlineKeyboardMarkup | None, lang: str) -> InlineKeyboardMarkup:
    """`kb` with "💳 Hisobni to'ldirish" on top."""
    return InlineKeyboardMarkup(inline_keyboard=[[topup_button(lang)], *((kb.inline_keyboard if kb else None) or [])])


def nudge_kb(lang: str, topup: bool = False) -> InlineKeyboardMarkup:
    """Under a reminder: a check started right away (Writing Task 2, Speaking Part 1 - the reminder becomes the
    step), a ready question, the top-up when the coins are short, and the way to switch reminders off."""
    rows = [[topup_button(lang)]] if topup else []
    rows += [
        [_b(MODULE_LABELS[PracticeModule.writing_task2], CB_TASK2),
         _b(MODULE_LABELS[PracticeModule.speaking_part1], CB_PART1)],
        [_b(t("entry.btn_practice", lang), CB_MENU)],
        [_b(t("nudge.btn_off", lang), CB_NUDGES_OFF)],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def smart_writing_kb(lang: str, token: str = "") -> InlineKeyboardMarkup:
    """"Task 1 / Task 2" under a text, photo or file sent outside any flow."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        _b("Task 1 { Report }", f"{CB_SMART_PREFIX}w:task1:{token}"),
        _b("Task 2 { Essay }", f"{CB_SMART_PREFIX}w:task2:{token}"),
    ]])


def smart_speaking_kb(lang: str, token: str = "") -> InlineKeyboardMarkup:
    """"Part 1 / 2 / 3" under a voice message sent outside any flow."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        _b(f"Part {n}", f"{CB_SMART_PREFIX}s:part{n}:{token}") for n in (1, 2, 3)
    ]])
