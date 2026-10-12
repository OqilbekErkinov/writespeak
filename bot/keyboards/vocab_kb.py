"""Keyboards for the vocabulary bank (bot/handlers/vocabulary.py).

Review/test buttons carry the card's position in the session ("vocab:know:3")
so a double tap or a tap on an older card can't answer the wrong word.
Everything starts with "vocab:" - the handler's last callback catches stale
ones from finished sessions.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.i18n import t
from bot.keyboards.nav_kb import back_button

CB_VOCAB_PREFIX = "vocab:"
CB_VOCAB_MENU = "vocab:menu"
CB_VOCAB_START = "vocab:start"
CB_VOCAB_REVEAL = "vocab:reveal:"  # + card index
CB_VOCAB_KNOW = "vocab:know:"  # + card index
CB_VOCAB_FORGOT = "vocab:forgot:"  # + card index
CB_VOCAB_STOP = "vocab:stop"
CB_VOCAB_TEST = "vocab:test"
CB_VOCAB_ANSWER = "vocab:ans:"  # + question index + ":" + option index
CB_VOCAB_NEXT = "vocab:next:"  # + question index
CB_VOCAB_LIST = "vocab:list:"  # + page
CB_VOCAB_WORD = "vocab:word:"  # + entry id + ":" + page
CB_VOCAB_DELETE = "vocab:del:"  # + entry id + ":" + page  (asks to confirm)
CB_VOCAB_DELETE_OK = "vocab:delok:"  # + entry id + ":" + page
CB_VOCAB_RELEARN = "vocab:relearn:"  # + entry id + ":" + page
CB_VOCAB_ADD = "vocab:add"
CB_VOCAB_REMIND = "vocab:remind"

OPTION_TEXT_MAX = 60


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _menu_row(lang: str) -> list[InlineKeyboardButton]:
    return [_btn(t("vocab.btn_back_menu", lang), CB_VOCAB_MENU)]


def vocab_menu_kb(lang: str, *, due: int, total: int, reminders: bool) -> InlineKeyboardMarkup:
    rows = []
    if due:
        rows.append([_btn(t("vocab.btn_review", lang, due=due), CB_VOCAB_START)])
    if total:
        rows.append([_btn(t("vocab.btn_test", lang), CB_VOCAB_TEST), _btn(t("vocab.btn_list", lang), CB_VOCAB_LIST + "0")])
    remind_key = "vocab.btn_remind_on" if reminders else "vocab.btn_remind_off"
    rows.append([_btn(t("vocab.btn_add", lang), CB_VOCAB_ADD), _btn(t(remind_key, lang), CB_VOCAB_REMIND)])
    rows.append([back_button(lang)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reveal_kb(lang: str, index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(t("vocab.btn_reveal", lang), f"{CB_VOCAB_REVEAL}{index}")],
            [_btn(t("vocab.btn_stop", lang), CB_VOCAB_STOP)],
        ]
    )


def know_forgot_kb(lang: str, index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(t("vocab.btn_forgot", lang), f"{CB_VOCAB_FORGOT}{index}"),
                _btn(t("vocab.btn_know", lang), f"{CB_VOCAB_KNOW}{index}"),
            ],
            [_btn(t("vocab.btn_stop", lang), CB_VOCAB_STOP)],
        ]
    )


def _short(text: str) -> str:
    return text if len(text) <= OPTION_TEXT_MAX else text[: OPTION_TEXT_MAX - 1] + "…"


def test_options_kb(lang: str, index: int, options: list[str]) -> InlineKeyboardMarkup:
    rows = [[_btn(_short(o), f"{CB_VOCAB_ANSWER}{index}:{i}")] for i, o in enumerate(options)]
    rows.append([_btn(t("vocab.btn_stop", lang), CB_VOCAB_STOP)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def test_next_kb(lang: str, index: int, last: bool) -> InlineKeyboardMarkup:
    label = t("vocab.btn_result", lang) if last else t("vocab.btn_next", lang)
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(label, f"{CB_VOCAB_NEXT}{index}")]])


def session_done_kb(lang: str, *, due: int, can_test: bool) -> InlineKeyboardMarkup:
    """Under a finished review/test: carry on with what's left, or go back."""
    row = []
    if due:
        row.append(_btn(t("vocab.btn_review", lang, due=due), CB_VOCAB_START))
    if can_test:
        row.append(_btn(t("vocab.btn_test", lang), CB_VOCAB_TEST))
    rows = [row] if row else []
    rows.append(_menu_row(lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def word_list_kb(lang: str, entries: list[tuple[int, str]], page: int, pages: int) -> InlineKeyboardMarkup:
    """entries: (entry id, button label)."""
    rows = [[_btn(label, f"{CB_VOCAB_WORD}{entry_id}:{page}")] for entry_id, label in entries]
    nav = []
    if page > 0:
        nav.append(_btn("◀️", f"{CB_VOCAB_LIST}{page - 1}"))
    if page < pages - 1:
        nav.append(_btn("▶️", f"{CB_VOCAB_LIST}{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append(_menu_row(lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def word_detail_kb(lang: str, entry_id: int, page: int, *, can_relearn: bool) -> InlineKeyboardMarkup:
    row = [_btn(t("vocab.btn_delete", lang), f"{CB_VOCAB_DELETE}{entry_id}:{page}")]
    if can_relearn:
        row.append(_btn(t("vocab.btn_relearn", lang), f"{CB_VOCAB_RELEARN}{entry_id}:{page}"))
    return InlineKeyboardMarkup(
        inline_keyboard=[row, [_btn(t("vocab.btn_back_list", lang), f"{CB_VOCAB_LIST}{page}")]]
    )


def delete_confirm_kb(lang: str, entry_id: int, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _btn(t("vocab.btn_delete_yes", lang), f"{CB_VOCAB_DELETE_OK}{entry_id}:{page}"),
                _btn(t("vocab.btn_cancel", lang), f"{CB_VOCAB_WORD}{entry_id}:{page}"),
            ]
        ]
    )


def add_words_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[_menu_row(lang)])


def reminder_kb(lang: str, due: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn(t("vocab.btn_review", lang, due=due), CB_VOCAB_START)],
            [_btn(t("vocab.btn_remind_stop", lang), CB_VOCAB_REMIND)],
        ]
    )
