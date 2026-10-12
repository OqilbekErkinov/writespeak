"""Lug'atim / Мой словарь: the student's personal word bank.

Words arrive by themselves from every graded Writing/Speaking report (db.crud.
add_vocabulary_entries, called from writing.py/speaking.py's _run_grading),
or the student types them in ("➕ So'z qo'shish" - English, or Uzbek/Russian
to find the English; services/ai/vocab_helper.py fills in the card). Three
ways to work with them:

- 🔄 Takrorlash: flip cards for the words due today, on a Leitner schedule
  (db.crud's VOCAB_INTERVAL_DAYS). "Bildim" moves a word to a longer
  interval; "Bilmadim" sends it back to the start and shows it once more
  before the session ends.
- 🎯 Test: multiple choice - which word has this meaning, which word fills
  the gap in the example, which translation is right - with the student's
  own other words as the wrong options. Right answers only move words that
  are due; wrong ones send any word back.
- 📋 So'zlarim: every word, page by page, to look one up, delete it or learn
  it again.

Translations into the UI language are fetched lazily (one cheap call for the
words about to be shown that have none yet) and kept per word. The daily
"words to review" reminder is bot/utils/vocab_reminders.py.
"""
from __future__ import annotations

import asyncio
import html
import logging
import math
import random
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bot.i18n import t
from bot.keyboards.main_menu_kb import BTN_VOCAB_RU, BTN_VOCAB_UZ, MENU_BUTTON_TEXTS
from bot.keyboards.vocab_kb import (
    CB_VOCAB_ADD,
    CB_VOCAB_ANSWER,
    CB_VOCAB_DELETE,
    CB_VOCAB_DELETE_OK,
    CB_VOCAB_FORGOT,
    CB_VOCAB_KNOW,
    CB_VOCAB_LIST,
    CB_VOCAB_MENU,
    CB_VOCAB_NEXT,
    CB_VOCAB_PREFIX,
    CB_VOCAB_RELEARN,
    CB_VOCAB_REMIND,
    CB_VOCAB_REVEAL,
    CB_VOCAB_START,
    CB_VOCAB_STOP,
    CB_VOCAB_TEST,
    CB_VOCAB_WORD,
    add_words_kb,
    delete_confirm_kb,
    know_forgot_kb,
    reveal_kb,
    session_done_kb,
    test_next_kb,
    test_options_kb,
    vocab_menu_kb,
    word_detail_kb,
    word_list_kb,
)
from bot.states.vocab_states import VocabStates
from bot.utils.messages import show_screen
from db import crud
from db.crud import VOCAB_MANUAL_DAILY_LIMIT, VOCAB_MASTERED_BOX, vocab_key
from db.database import get_session
from db.models import VocabularyEntry
from services.ai.vocab_helper import describe_words, translate_words

logger = logging.getLogger(__name__)

router = Router(name="vocabulary")

SESSION_SIZE = 10  # cards per review, questions per test
TEST_MIN_WORDS = 4  # one right + three wrong options
TEST_DISTRACTOR_POOL = 30
PAGE_SIZE = 10
ADD_MAX_WORDS = 10  # per message
ADD_WORD_MAX_LEN = 60
LIST_LABEL_MAX = 48
GAP = "_____"

_TASHKENT = timedelta(hours=5)  # no DST in Uzbekistan
_FLAGS = {"uz": "🇺🇿", "ru": "🇷🇺"}

# Serialises one student's answer taps, so a double tap can't record the
# same answer twice (aiogram handles updates concurrently).
_answer_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


# --- Helpers ---------------------------------------------------------------------


def _esc(text: str | None) -> str:
    return html.escape(text or "", quote=False)


def _ints(data: str, prefix: str) -> list[int]:
    return [int(x) for x in data[len(prefix):].split(":")]


def _local_date(dt: datetime):
    return (dt.astimezone(timezone.utc) + _TASHKENT).date()


def _when(dt: datetime | None, lang: str) -> str:
    if dt is None:
        return "—"
    today = _local_date(datetime.now(timezone.utc))
    day = _local_date(dt)
    if day <= today:
        return t("vocab.today", lang)
    if day == today + timedelta(days=1):
        return t("vocab.tomorrow", lang)
    return day.strftime("%d.%m.%Y")


def _card(entry: VocabularyEntry, translation: str | None) -> dict:
    return {
        "id": entry.id,
        "key": entry.word_key,
        "word": entry.word_or_phrase,
        "meaning": entry.meaning,
        "example": entry.example_sentence,
        "tr": translation or "",
    }


def _card_back(card: dict, lang: str) -> str:
    lines = [f"📖 <b>{_esc(card['word'])}</b>", "", f"💬 {_esc(card['meaning'])}"]
    if card.get("tr"):
        lines.append(f"{_FLAGS.get(lang, '🌐')} {_esc(card['tr'])}")
    if card.get("example"):
        lines.append(f"✏️ <i>{_esc(card['example'])}</i>")
    return "\n".join(lines)


def _needs_translation(entries: list[VocabularyEntry], lang: str) -> bool:
    return any(not (e.translations or {}).get(lang) for e in entries)


async def _translations(user_id: int, entries: list[VocabularyEntry], lang: str) -> dict[int, str]:
    """Each entry's translation into `lang`, fetching (and saving) the missing
    ones in one call. If that call fails the cards just go without."""
    known = {e.id: (e.translations or {}).get(lang) for e in entries}
    missing = [(e.id, e.word_or_phrase, e.meaning) for e in entries if not known[e.id]]
    if missing:
        try:
            fetched = await translate_words(missing, lang)
        except Exception:
            logger.exception("Translating %d vocabulary words failed", len(missing))
            fetched = {}
        if fetched:
            async with get_session() as session:
                await crud.save_vocabulary_translations(session, user_id, lang, fetched)
            known.update(fetched)
    return {entry_id: tr for entry_id, tr in known.items() if tr}


def gap_sentence(word: str, example: str) -> str | None:
    """`example` with `word` blanked out, or None when it isn't in there in a
    recognisable form. Allows regular endings (mitigate -> mitigated,
    mitigating; study -> studies) on words of 4+ letters."""
    words = word.split()
    if len(words) > 1 and words[0].lower() == "to":
        words = words[1:]
    if not words:
        return None
    parts = []
    for w in words:
        if len(w) < 4:
            parts.append(re.escape(w) + r"\b")
        elif w[-1].lower() in "ey":
            parts.append(re.escape(w[:-1]) + r"\w*")
        else:
            parts.append(re.escape(w) + r"\w*")
    pattern = re.compile(r"\b" + r"\s+".join(parts), re.IGNORECASE)
    if not pattern.search(example):
        return None
    return pattern.sub(GAP, example)


def _distinct(values: list[str], exclude: str) -> list[str]:
    seen = {exclude.strip().lower()}
    out = []
    for value in values:
        key = value.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(value)
    return out


def build_test(cards: list[dict], pool: list[dict], rng: random.Random | None = None) -> list[dict]:
    """One question per card: 'gap' (pick the word that fills the example),
    'meaning' (pick the word) or 'translate' (pick the translation), with
    three wrong options from `pool` - the student's own other words. Weighted
    towards using the word in context, which is what the exam asks for."""
    rng = rng or random.Random()
    questions = []
    for card in cards:
        others = [p for p in pool if p["key"] != card["key"]]
        words = _distinct([p["word"] for p in others], card["word"])
        if len(words) < 3:
            continue
        kinds = {"meaning": 2}
        gap = gap_sentence(card["word"], card["example"])
        if gap:
            kinds["gap"] = 3
        translations = _distinct([p["tr"] for p in others if p.get("tr")], card.get("tr") or "")
        if card.get("tr") and len(translations) >= 3:
            kinds["translate"] = 1
        kind = rng.choices(list(kinds), weights=list(kinds.values()))[0]
        if kind == "translate":
            right, wrong = card["tr"], rng.sample(translations, 3)
        else:
            right, wrong = card["word"], rng.sample(words, 3)
        options = [right, *wrong]
        rng.shuffle(options)
        questions.append(
            {**card, "kind": kind, "gap": gap or "", "options": options, "answer": options.index(right)}
        )
    return questions


# --- Menu --------------------------------------------------------------------------


async def _menu_screen(user_id: int, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    async with get_session() as session:
        stats = await crud.get_vocabulary_stats(session, user_id)
        user = await crud.get_user(session, user_id)
    reminders = user.vocab_reminders if user is not None else True
    kb = vocab_menu_kb(lang, due=stats.due, total=stats.total, reminders=reminders)
    if stats.total == 0:
        return t("vocab.empty", lang), kb

    lines = [
        t(
            "vocab.summary",
            lang,
            total=stats.total,
            new=stats.new,
            learning=stats.learning,
            mastered=stats.mastered,
        ),
        "",
    ]
    if stats.due:
        lines.append(t("vocab.due_now", lang, due=stats.due))
    elif stats.next_due_at is not None:
        lines.append(t("vocab.next_due", lang, when=_when(stats.next_due_at, lang)))
    else:
        lines.append(t("vocab.all_mastered", lang))
    lines += ["", t("vocab.how_it_works", lang)]
    return "\n".join(lines), kb


async def _show_menu(callback: CallbackQuery, lang: str) -> None:
    text, kb = await _menu_screen(callback.from_user.id, lang)
    await show_screen(callback, text, kb)


@router.message(F.text.in_({BTN_VOCAB_UZ, BTN_VOCAB_RU}))
async def show_vocab_menu(message: Message, state: FSMContext, lang: str) -> None:
    await state.clear()
    text, kb = await _menu_screen(message.from_user.id, lang)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data == CB_VOCAB_MENU)
async def back_to_vocab_menu(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    await state.clear()
    await _show_menu(callback, lang)


@router.callback_query(F.data == CB_VOCAB_REMIND)
async def toggle_reminders(callback: CallbackQuery, lang: str) -> None:
    user_id = callback.from_user.id
    async with get_session() as session:
        user = await crud.get_user(session, user_id)
        enabled = not (user.vocab_reminders if user is not None else True)
        await crud.set_vocab_reminders(session, user_id, enabled)
    await callback.answer(t("vocab.remind_on" if enabled else "vocab.remind_off", lang))
    await _show_menu(callback, lang)


# --- Review (flip cards) -----------------------------------------------------------


@router.callback_query(F.data == CB_VOCAB_START)
async def start_review(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    user_id = callback.from_user.id
    async with get_session() as session:
        entries = await crud.get_vocabulary_due(session, user_id, limit=SESSION_SIZE)
    if not entries:
        await callback.answer(t("vocab.nothing_due", lang), show_alert=True)
        await _show_menu(callback, lang)
        return

    await callback.answer()
    if _needs_translation(entries, lang):
        await show_screen(callback, t("vocab.preparing", lang))
    translations = await _translations(user_id, entries, lang)
    await state.set_state(VocabStates.reviewing)
    await state.set_data(
        {
            "queue": [_card(e, translations.get(e.id)) for e in entries],
            "index": 0,
            "answered": [],  # ids of the words answered this session
            "missed": [],  # ids of the ones answered "Bilmadim" at least once
            "mastered": 0,
        }
    )
    await _show_card(callback, state, lang)


async def _show_card(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    queue, index = data["queue"], data["index"]
    if index >= len(queue):
        await _finish_review(callback, state, lang)
        return
    text = t("vocab.card_front", lang, index=index + 1, total=len(queue), word=_esc(queue[index]["word"]))
    await show_screen(callback, text, reveal_kb(lang, index))


@router.callback_query(VocabStates.reviewing, F.data.startswith(CB_VOCAB_REVEAL))
async def reveal_card(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    index = _ints(callback.data, CB_VOCAB_REVEAL)[0]
    data = await state.get_data()
    if index != data["index"]:
        return
    queue = data["queue"]
    header = t("vocab.review_header", lang, index=index + 1, total=len(queue))
    await show_screen(callback, f"{header}\n\n{_card_back(queue[index], lang)}", know_forgot_kb(lang, index))


@router.callback_query(VocabStates.reviewing, F.data.startswith((CB_VOCAB_KNOW, CB_VOCAB_FORGOT)))
async def answer_card(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    user_id = callback.from_user.id
    knew = callback.data.startswith(CB_VOCAB_KNOW)
    index = _ints(callback.data, CB_VOCAB_KNOW if knew else CB_VOCAB_FORGOT)[0]

    async with _answer_locks[user_id]:
        data = await state.get_data()
        if index != data.get("index"):
            return
        card = data["queue"][index]
        async with get_session() as session:
            entry = await crud.record_vocabulary_review(session, user_id, card["id"], knew)

        updates: dict = {"index": index + 1}
        if card["id"] not in data["answered"]:
            updates["answered"] = data["answered"] + [card["id"]]
        if knew:
            if entry is not None and entry.is_learned:
                updates["mastered"] = data["mastered"] + 1
        elif card["id"] not in data["missed"]:
            # Shown once more at the end of this session.
            updates["missed"] = data["missed"] + [card["id"]]
            updates["queue"] = data["queue"] + [card]
        await state.update_data(**updates)

    await _show_card(callback, state, lang)


@router.callback_query(VocabStates.reviewing, F.data == CB_VOCAB_STOP)
async def stop_review(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    await _finish_review(callback, state, lang)


async def _finish_review(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    await state.clear()
    words, missed = len(data.get("answered", [])), len(data.get("missed", []))
    if not words:
        await _show_menu(callback, lang)
        return

    async with get_session() as session:
        stats = await crud.get_vocabulary_stats(session, callback.from_user.id)
    parts = [t("vocab.review_done", lang, words=words, known=words - missed, missed=missed)]
    if data.get("mastered"):
        parts.append(t("vocab.review_mastered", lang, count=data["mastered"]))
    if stats.due:
        parts.append(t("vocab.more_due", lang, due=stats.due))
    elif stats.next_due_at is not None:
        parts.append(t("vocab.next_due", lang, when=_when(stats.next_due_at, lang)))
    await show_screen(
        callback,
        "\n\n".join(parts),
        session_done_kb(lang, due=stats.due, can_test=stats.total >= TEST_MIN_WORDS),
    )


# --- Test (multiple choice) --------------------------------------------------------


@router.callback_query(F.data == CB_VOCAB_TEST)
async def start_test(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    user_id = callback.from_user.id
    async with get_session() as session:
        entries = await crud.get_vocabulary_for_test(session, user_id, limit=SESSION_SIZE)
        pool = await crud.get_random_vocabulary(
            session, user_id, [e.id for e in entries], limit=TEST_DISTRACTOR_POOL
        )
    if len(entries) + len(pool) < TEST_MIN_WORDS:
        await callback.answer(t("vocab.test_need_words", lang, min=TEST_MIN_WORDS), show_alert=True)
        return

    await callback.answer()
    # Only the asked words get translated now; the wrong options use the
    # translations they already have.
    if _needs_translation(entries, lang):
        await show_screen(callback, t("vocab.preparing", lang))
    translations = await _translations(user_id, entries, lang)
    cards = [_card(e, translations.get(e.id)) for e in entries]
    others = [_card(e, (e.translations or {}).get(lang)) for e in pool]
    questions = build_test(cards, cards + others)
    if not questions:  # can't happen with TEST_MIN_WORDS distinct words, but just in case
        await _show_menu(callback, lang)
        return

    await state.set_state(VocabStates.testing)
    await state.set_data({"questions": questions, "index": 0, "answered": -1, "done": 0, "correct": 0})
    await _show_question(callback, state, lang)


async def _show_question(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    questions, index = data["questions"], data["index"]
    if index >= len(questions):
        await _finish_test(callback, state, lang)
        return
    q = questions[index]
    if q["kind"] == "gap":
        body = t("vocab.q_gap", lang, sentence=_esc(q["gap"]))
    elif q["kind"] == "translate":
        body = t("vocab.q_translate", lang, word=_esc(q["word"]))
    else:
        body = t("vocab.q_meaning", lang, meaning=_esc(q["meaning"]))
    header = t("vocab.test_header", lang, index=index + 1, total=len(questions))
    await show_screen(callback, f"{header}\n\n{body}", test_options_kb(lang, index, q["options"]))


@router.callback_query(VocabStates.testing, F.data.startswith(CB_VOCAB_ANSWER))
async def answer_question(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    user_id = callback.from_user.id
    index, option = _ints(callback.data, CB_VOCAB_ANSWER)

    async with _answer_locks[user_id]:
        data = await state.get_data()
        if index != data.get("index") or data.get("answered") == index:
            await callback.answer()
            return
        questions = data["questions"]
        q = questions[index]
        correct = option == q["answer"]
        async with get_session() as session:
            await crud.record_vocabulary_review(session, user_id, q["id"], correct, only_if_due=True)
        await state.update_data(answered=index, done=data["done"] + 1, correct=data["correct"] + int(correct))

    await callback.answer("✅" if correct else "❌")
    if correct:
        verdict = t("vocab.correct", lang)
    else:
        verdict = t("vocab.wrong", lang, right=_esc(q["options"][q["answer"]]))
    header = t("vocab.test_header", lang, index=index + 1, total=len(questions))
    await show_screen(
        callback,
        f"{header}\n\n{verdict}\n\n{_card_back(q, lang)}",
        test_next_kb(lang, index, last=index == len(questions) - 1),
    )


@router.callback_query(VocabStates.testing, F.data.startswith(CB_VOCAB_NEXT))
async def next_question(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    index = _ints(callback.data, CB_VOCAB_NEXT)[0]
    data = await state.get_data()
    if index != data.get("index") or data.get("answered") != index:
        return
    await state.update_data(index=index + 1)
    await _show_question(callback, state, lang)


@router.callback_query(VocabStates.testing, F.data == CB_VOCAB_STOP)
async def stop_test(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    await _finish_test(callback, state, lang)


async def _finish_test(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    await state.clear()
    done, correct = data.get("done", 0), data.get("correct", 0)
    if not done:
        await _show_menu(callback, lang)
        return

    percent = round(100 * correct / done)
    if percent >= 90:
        comment = t("vocab.test_great", lang)
    elif percent >= 60:
        comment = t("vocab.test_good", lang)
    else:
        comment = t("vocab.test_low", lang)
    async with get_session() as session:
        stats = await crud.get_vocabulary_stats(session, callback.from_user.id)
    text = t("vocab.test_done", lang, correct=correct, total=done, percent=percent) + "\n\n" + comment
    await show_screen(callback, text, session_done_kb(lang, due=stats.due, can_test=True))


# --- Word list -------------------------------------------------------------------


def _status_icon(entry: VocabularyEntry) -> str:
    if entry.is_learned:
        return "✅"
    return "🆕" if entry.review_count == 0 else "🔄"


def _word_status(entry: VocabularyEntry, lang: str) -> str:
    lines = [
        t(
            "vocab.word_source",
            lang,
            source=t(f"vocab.source_{entry.source}", lang),
            date=_local_date(entry.created_at).strftime("%d.%m.%Y"),
        )
    ]
    if entry.is_learned:
        lines.append(t("vocab.status_mastered", lang))
    elif entry.review_count == 0:
        lines.append(t("vocab.status_new", lang))
    else:
        lines.append(
            t(
                "vocab.status_learning",
                lang,
                box=entry.box,
                max=VOCAB_MASTERED_BOX,
                when=_when(entry.due_at, lang),
            )
        )
    return "\n".join(lines)


async def _show_list(callback: CallbackQuery, lang: str, page: int) -> None:
    user_id = callback.from_user.id
    async with get_session() as session:
        entries, total = await crud.list_vocabulary(
            session, user_id, offset=page * PAGE_SIZE, limit=PAGE_SIZE
        )
        pages = max(1, math.ceil(total / PAGE_SIZE))
        if not entries and total:  # e.g. the last word of the last page was deleted
            page = pages - 1
            entries, total = await crud.list_vocabulary(
                session, user_id, offset=page * PAGE_SIZE, limit=PAGE_SIZE
            )
    if not total:
        await _show_menu(callback, lang)
        return

    labels = []
    for e in entries:
        word = e.word_or_phrase
        if len(word) > LIST_LABEL_MAX:
            word = word[: LIST_LABEL_MAX - 1] + "…"
        labels.append((e.id, f"{_status_icon(e)} {word}"))
    text = t("vocab.list_header", lang, total=total, page=page + 1, pages=pages)
    await show_screen(callback, text, word_list_kb(lang, labels, page, pages))


@router.callback_query(F.data.startswith(CB_VOCAB_LIST))
async def show_word_list(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    await _show_list(callback, lang, max(0, _ints(callback.data, CB_VOCAB_LIST)[0]))


async def _show_word(callback: CallbackQuery, lang: str, entry_id: int, page: int) -> None:
    user_id = callback.from_user.id
    async with get_session() as session:
        entry = await crud.get_vocabulary_entry(session, user_id, entry_id)
    if entry is None:
        await _show_list(callback, lang, page)
        return
    translations = await _translations(user_id, [entry], lang)
    text = _card_back(_card(entry, translations.get(entry.id)), lang) + "\n\n" + _word_status(entry, lang)
    can_relearn = entry.is_learned or entry.box > 0
    await show_screen(callback, text, word_detail_kb(lang, entry.id, page, can_relearn=can_relearn))


@router.callback_query(F.data.startswith(CB_VOCAB_WORD))
async def show_word(callback: CallbackQuery, lang: str) -> None:
    await callback.answer()
    entry_id, page = _ints(callback.data, CB_VOCAB_WORD)
    await _show_word(callback, lang, entry_id, page)


@router.callback_query(F.data.startswith(CB_VOCAB_DELETE_OK))
async def delete_word(callback: CallbackQuery, lang: str) -> None:
    entry_id, page = _ints(callback.data, CB_VOCAB_DELETE_OK)
    async with get_session() as session:
        await crud.delete_vocabulary_entry(session, callback.from_user.id, entry_id)
    await callback.answer(t("vocab.deleted", lang))
    await _show_list(callback, lang, page)


@router.callback_query(F.data.startswith(CB_VOCAB_DELETE))
async def confirm_delete_word(callback: CallbackQuery, lang: str) -> None:
    entry_id, page = _ints(callback.data, CB_VOCAB_DELETE)
    async with get_session() as session:
        entry = await crud.get_vocabulary_entry(session, callback.from_user.id, entry_id)
    await callback.answer()
    if entry is None:
        await _show_list(callback, lang, page)
        return
    await show_screen(
        callback,
        t("vocab.delete_confirm", lang, word=_esc(entry.word_or_phrase)),
        delete_confirm_kb(lang, entry_id, page),
    )


@router.callback_query(F.data.startswith(CB_VOCAB_RELEARN))
async def relearn_word(callback: CallbackQuery, lang: str) -> None:
    entry_id, page = _ints(callback.data, CB_VOCAB_RELEARN)
    async with get_session() as session:
        await crud.reset_vocabulary_entry(session, callback.from_user.id, entry_id)
    await callback.answer(t("vocab.relearned", lang))
    await _show_word(callback, lang, entry_id, page)


# --- Adding words by hand --------------------------------------------------------


@router.callback_query(F.data == CB_VOCAB_ADD)
async def ask_for_words(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    await state.set_state(VocabStates.adding)
    await state.set_data({})
    await show_screen(callback, t("vocab.add_prompt", lang, max=ADD_MAX_WORDS), add_words_kb(lang))


@router.message(VocabStates.adding, F.text, ~F.text.in_(MENU_BUTTON_TEXTS), ~F.text.startswith("/"))
async def add_words(message: Message, lang: str) -> None:
    user_id = message.from_user.id
    words = list(dict.fromkeys(w.strip() for w in re.split(r"[\n,;]+", message.text) if w.strip()))
    invalid = [w for w in words if len(w) > ADD_WORD_MAX_LEN]
    words = [w for w in words if len(w) <= ADD_WORD_MAX_LEN]
    notes = []
    if len(words) > ADD_MAX_WORDS:
        words = words[:ADD_MAX_WORDS]
        notes.append(t("vocab.add_truncated", lang, max=ADD_MAX_WORDS))

    async with get_session() as session:
        left = VOCAB_MANUAL_DAILY_LIMIT - await crud.count_manual_vocabulary_today(session, user_id)
    if left <= 0:
        await message.answer(t("vocab.add_limit", lang, limit=VOCAB_MANUAL_DAILY_LIMIT), reply_markup=add_words_kb(lang))
        return
    if len(words) > left:
        words = words[:left]
        notes.append(t("vocab.add_limit", lang, limit=VOCAB_MANUAL_DAILY_LIMIT))
    if not words:
        await message.answer(t("vocab.add_text_only", lang), reply_markup=add_words_kb(lang))
        return

    progress = await message.answer(t("vocab.add_working", lang))
    try:
        described = await describe_words(words)
    except Exception:
        logger.exception("Describing %d vocabulary words failed for user %s", len(words), user_id)
        await progress.edit_text(t("vocab.add_failed", lang), reply_markup=add_words_kb(lang))
        return

    rows: dict[str, dict] = {}  # word_key -> row, so two inputs for one word count once
    for d in described:
        if not (d.ok and d.term.strip() and d.meaning.strip()):
            invalid.append(d.input)
            continue
        key = vocab_key(d.term)
        if key and key not in rows:
            translations = {k: v.strip() for k, v in (("uz", d.uz), ("ru", d.ru)) if v.strip()}
            rows[key] = {"word": d.term, "meaning": d.meaning, "example": d.example, "translations": translations}
    async with get_session() as session:
        added = await crud.add_manual_vocabulary(session, user_id, list(rows.values()))

    parts = []
    new_rows = [row for key, row in rows.items() if key in added]
    if new_rows:
        lines = [
            f"• <b>{_esc(r['word'])}</b> — {_esc(r['translations'].get(lang) or r['meaning'])}"
            for r in new_rows
        ]
        parts.append(t("vocab.add_added", lang) + "\n" + "\n".join(lines))
    existing = [row["word"] for key, row in rows.items() if key not in added]
    if existing:
        parts.append(t("vocab.add_exists", lang, words=", ".join(_esc(w) for w in existing)))
    if invalid:
        shown = (w if len(w) <= 30 else w[:29] + "…" for w in invalid)
        parts.append(t("vocab.add_invalid", lang, words=", ".join(_esc(w) for w in shown)))
    parts += notes
    parts.append(t("vocab.add_more", lang))
    await progress.edit_text("\n\n".join(parts), reply_markup=add_words_kb(lang))


@router.message(VocabStates.adding, ~F.text)
async def add_words_not_text(message: Message, lang: str) -> None:
    await message.answer(t("vocab.add_text_only", lang), reply_markup=add_words_kb(lang))


# --- Stale buttons -----------------------------------------------------------------


@router.callback_query(F.data.startswith(CB_VOCAB_PREFIX))
async def stale_vocab_button(callback: CallbackQuery, lang: str) -> None:
    """A review/test button from a session that has ended (or from before
    this flow changed): reopen the Lug'atim menu in its place."""
    await callback.answer(t("vocab.session_expired", lang))
    await _show_menu(callback, lang)
