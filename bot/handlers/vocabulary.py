"""Lug'atim / Мой словарь: a personal flashcard bank built from the
vocabulary every graded Writing/Speaking submission already produces
(services/ai/schemas.py's VocabularyItem list) - see db.crud.
add_vocabulary_entries, called from writing.py/speaking.py's _run_grading.
Review is a simple flip-card loop, not full spaced repetition: show the
word, reveal the meaning+example on tap, then "know" (mark learned, won't
resurface) or "later" (stays in the pool for a future review).
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.i18n import t
from bot.keyboards.main_menu_kb import BTN_VOCAB_RU, BTN_VOCAB_UZ
from bot.keyboards.vocab_kb import (
    CB_VOCAB_AGAIN,
    CB_VOCAB_KNOW,
    CB_VOCAB_REVEAL,
    CB_VOCAB_START,
    know_again_kb,
    reveal_kb,
    start_review_kb,
)
from bot.states.vocab_states import VocabStates
from db import crud
from db.database import get_session

router = Router(name="vocabulary")

REVIEW_BATCH_SIZE = 10


@router.message(F.text.in_({BTN_VOCAB_UZ, BTN_VOCAB_RU}))
async def show_vocab_menu(message: Message, state: FSMContext, lang: str) -> None:
    await state.clear()
    async with get_session() as session:
        total, learned = await crud.get_vocabulary_counts(session, message.from_user.id)

    due = total - learned
    if total == 0:
        await message.answer(t("vocab.empty", lang))
        return

    text = t("vocab.summary", lang, total=total, learned=learned)
    if due == 0:
        await message.answer(text + "\n\n" + t("vocab.all_learned", lang))
        return

    await message.answer(
        text + "\n\n" + t("vocab.ready_to_review", lang, due=due),
        reply_markup=start_review_kb(lang),
    )


@router.callback_query(F.data == CB_VOCAB_START)
async def start_review(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    async with get_session() as session:
        entries = await crud.get_vocabulary_for_review(
            session, callback.from_user.id, limit=REVIEW_BATCH_SIZE
        )
    if not entries:
        await callback.message.answer(t("vocab.none_left", lang))
        return

    queue = [
        {"id": e.id, "word": e.word_or_phrase, "meaning": e.meaning, "example": e.example_sentence}
        for e in entries
    ]
    await state.update_data(queue=queue, index=0, reviewed=0, learned_now=0)
    await state.set_state(VocabStates.reviewing)
    await _show_card(callback.message, state, lang)


@router.callback_query(VocabStates.reviewing, F.data == CB_VOCAB_REVEAL)
async def reveal_card(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    data = await state.get_data()
    card = data["queue"][data["index"]]
    await callback.message.edit_text(
        f"📖 <b>{card['word']}</b>\n\n{card['meaning']}\n\n<i>{card['example']}</i>",
        reply_markup=know_again_kb(lang),
    )


@router.callback_query(VocabStates.reviewing, F.data.in_({CB_VOCAB_KNOW, CB_VOCAB_AGAIN}))
async def answer_card(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    data = await state.get_data()
    card = data["queue"][data["index"]]

    if callback.data == CB_VOCAB_KNOW:
        async with get_session() as session:
            await crud.mark_vocabulary_learned(session, card["id"], callback.from_user.id)
        await state.update_data(learned_now=data["learned_now"] + 1)

    await state.update_data(index=data["index"] + 1, reviewed=data["reviewed"] + 1)
    await _show_card(callback.message, state, lang)


async def _show_card(message: Message, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    queue = data["queue"]
    index = data["index"]

    if index >= len(queue):
        await message.edit_text(
            t("vocab.finished", lang, reviewed=data["reviewed"], learned=data["learned_now"]),
            reply_markup=None,
        )
        await state.clear()
        return

    card = queue[index]
    progress = t("vocab.card_progress", lang, index=index + 1, total=len(queue))
    await message.edit_text(f"📖 <b>{card['word']}</b>\n\n{progress}", reply_markup=reveal_kb(lang))
