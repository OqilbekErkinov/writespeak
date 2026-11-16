"""The last router: what no other handler took (the 2026-11-15 audit - until then such a message or button got
no reply at all: a second essay sent while one was graded, "ha" typed under "Is this right?", an old button
from a flow left long ago only spun). Each gets a short answer that says what to do now, by the state the
student is in: a check being graded, a check that stopped, a step waiting for a button, or no flow at all.

With no flow at all, the smart entry (2026-11-16): what looks like an answer is not turned away with "pick a
section" but taken on -
- a text that looks like an essay (settings.smart_entry_min_words words or more, English): "Task 1 or Task 2?";
  the text is kept (KEPT_KEY) and, once the task is chosen and its question sent, graded as the answer without
  being asked for again (bot/handlers/writing.py start_task / _ask_for_answer). A long essay Telegram split into
  several messages is joined into one kept text;
- a photo or a file: the same Writing buttons; the task starts and says what to send (the question first) -
  a picture can't be told apart as the question or the answer, so nothing is guessed;
- a voice message: "Part 1 / 2 / 3"; for Part 2 (one cue card, one talk) the recording is kept and graded once
  the cue card is in (speaking.py start_part / _use_kept_voice); Part 1/3 start and ask for it again.
Any FSM state at all (a flow of any kind) gets the old answers - the smart entry never steps into a flow.

The entry buttons under messages that stay in the chat (the last onboarding tip, a payment approval -
bot/keyboards/entry_kb.py) open the Writing / Speaking picker as a new message here too.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import defaultdict
from pathlib import PurePath

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import settings
from bot.handlers import speaking, writing
from bot.i18n import t
from bot.keyboards.entry_kb import (
    CB_OPEN_SPEAKING,
    CB_OPEN_WRITING,
    CB_SMART_PREFIX,
    smart_speaking_kb,
    smart_writing_kb,
)
from bot.keyboards.main_menu_kb import main_menu_kb
from bot.keyboards.speaking_part_kb import speaking_part_kb
from bot.keyboards.writing_task_kb import writing_task_kb
from bot.states.speaking_states import SpeakingStates
from bot.states.writing_states import WritingStates
from bot.utils.crm import redis
from bot.utils.flow import GRADING, leave_flow
from bot.utils.input_extraction import AUDIO_EXTENSIONS, DOCX_MIME, IMAGE_EXTENSIONS
from services.ai.essay_marking import count_words

logger = logging.getLogger(__name__)
router = Router(name="fallback")

FAILED = {WritingStates.failed.state, SpeakingStates.failed.state}

# What the smart entry keeps until a task is chosen: {"kind": "text", "text"} or {"kind": "voice", "message"
# (the voice message as JSON)}, under the student's message id (the token in the buttons' data)
KEPT_KEY = "smart:kept:{uid}:{token}"
KEPT_TTL = 24 * 3600
# The last text kept: a long essay Telegram split into several messages arrives as parts, each near its limit
LAST_KEY = "smart:last:{uid}"
SPLIT_CHARS = 3500  # a part this long may be followed by the next part...
SPLIT_GAP = 60  # ...within this many seconds
WRITING_FILES = {".pdf", ".docx", ".txt"}
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")
# Words nearly every English paragraph has: an English essay is ~30% these, an Uzbek (Latin) or other text ~0%
_ENGLISH = frozenset(
    "a an the and or but so because if of to in on at for with by from as about into than then that this these "
    "those it its is are was were be been being have has had do does did can could will would should may might "
    "must not no more most many much some such which who whom whose what when where why how they them their "
    "there people we our you your he she his her i my me also very".split()
)
# The exam's own instructions: in a question (a long Task 2 one passes the word count), never in an answer
_INSTRUCTIONS = re.compile(
    r"write at least|at least (?:150|250) words|you should spend about|give reasons for your answer|"
    r"own knowledge or experience|summari[sz]e the information by selecting|make comparisons where relevant",
    re.IGNORECASE)
_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


@router.callback_query(F.data.startswith(CB_SMART_PREFIX))
async def smart_choice(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    """The task or part chosen under the smart entry's question: that flow starts, with the kept text or talk."""
    await callback.answer()
    module, _, rest = callback.data.removeprefix(CB_SMART_PREFIX).partition(":")
    which, _, token = rest.partition(":")
    uid = callback.from_user.id
    kept = await _kept(uid, token) if token else None
    if module == "w" and which in ("task1", "task2"):
        if kept is not None and kept.get("kind") == "text":
            note = t("smart.writing_kept", lang, words=count_words(kept["text"]))
            started = await writing.start_task(callback, state, which, lang, kept_answer=kept["text"],
                                               note=note + "\n\n")
        else:  # a photo or a file (no token): the question first; a text no longer kept: sent again later
            note = t("smart.gone" if token else "smart.writing_file", lang)
            started = await writing.start_task(callback, state, which, lang, note=note + "\n\n")
    elif module == "s" and which in ("part1", "part2", "part3"):
        if which == "part2" and kept is not None and kept.get("kind") == "voice":
            started = await speaking.start_part(callback, state, which, lang, kept_voice=kept["message"],
                                                note=t("smart.voice_kept", lang) + "\n\n")
        elif which == "part2":  # the talk is no longer kept
            started = await speaking.start_part(callback, state, which, lang, note=t("smart.gone", lang) + "\n\n")
        else:  # Part 1/3: which question the recording answers can't be known - sent again after the questions
            started = await speaking.start_part(callback, state, which, lang,
                                                note=t("smart.voice_again", lang) + "\n\n")
    else:
        return
    if started and kept is not None:
        await _forget(uid, token)  # it is in the flow's data now; a refused start (no coins) keeps it for later


@router.callback_query(F.data == CB_OPEN_WRITING)
async def open_writing(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    """"✍️ Writing" under a message that stays (the onboarding tip, a payment approval): the Task picker as a
    new message, as the menu's "✍️ Writing" opens it (a check being graded goes on)."""
    await callback.answer()
    await leave_flow(state)
    await callback.message.answer(t("writing.choose_type", lang), reply_markup=writing_task_kb(lang))


@router.callback_query(F.data == CB_OPEN_SPEAKING)
async def open_speaking(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    """"🎙 Speaking" under a message that stays: the Part picker as a new message."""
    await callback.answer()
    await leave_flow(state)
    await callback.message.answer(t("speaking.choose_part", lang), reply_markup=speaking_part_kb(lang))


@router.callback_query()
async def stale_button(callback: CallbackQuery, lang: str) -> None:
    await callback.answer(t("nav.stale", lang), show_alert=False)


@router.message()
async def anything_else(message: Message, state: FSMContext, lang: str) -> None:
    if message.media_group_id:
        # an album: one answer for the whole of it, not one per picture
        try:
            if not await redis().set(f"fallback:album:{message.media_group_id}", "1", nx=True, ex=120):
                return
        except Exception:  # noqa: BLE001
            pass
    current = await state.get_state()
    if current in GRADING:
        await message.answer(t("check.in_progress", lang))
    elif current in FAILED:
        await message.answer(t("check.failed_hint", lang))
    elif current is not None:
        await message.answer(t("flow.use_buttons", lang))
    elif not await _smart_entry(message, lang):
        await message.answer(t("flow.menu_hint", lang), reply_markup=main_menu_kb(lang))


# --- the smart entry ----------------------------------------------------------------------------------------


def looks_like_essay(text: str) -> bool:
    """An English answer, not a question to the admins or a chat line: settings.smart_entry_min_words words or
    more, almost all of its letters Latin, and English's own small words among them (an Uzbek text in Latin
    letters has next to none) - and not an exam question (its instructions), which would be kept as the
    answer and then asked for as the question."""
    words = _WORD.findall(text)
    if len(words) < settings.smart_entry_min_words or _INSTRUCTIONS.search(text):
        return False
    letters = [c for c in text if c.isalpha()]
    if sum(1 for c in letters if c.isascii()) < 0.9 * len(letters):
        return False
    return sum(1 for w in words if w.lower() in _ENGLISH) >= 0.15 * len(words)


def _is_audio(message: Message) -> bool:
    if message.voice or message.video_note or message.audio or message.video:
        return True
    doc = message.document
    return bool(doc) and ((doc.mime_type or "").startswith(("audio/", "video/"))
                          or PurePath((doc.file_name or "").lower()).suffix in AUDIO_EXTENSIONS)


def _is_writing_file(message: Message) -> bool:
    if message.photo:
        return True
    doc = message.document
    if not doc:
        return False
    mime = (doc.mime_type or "").lower()
    suffix = PurePath((doc.file_name or "").lower()).suffix
    return mime.startswith("image/") or mime in ("application/pdf", DOCX_MIME, "text/plain") \
        or suffix in IMAGE_EXTENSIONS or suffix in WRITING_FILES


async def _smart_entry(message: Message, lang: str) -> bool:
    """Something sent with no flow: an answer offered its flow (True), or not one (False: "pick a section")."""
    uid = message.chat.id
    try:
        if message.text and not message.text.startswith("/"):
            return await _text_entry(message, lang)
        if _is_audio(message):
            await _keep(uid, str(message.message_id),
                        {"kind": "voice", "message": message.model_dump_json(exclude_none=True)})
            await message.answer(t("smart.voice_offer", lang), reply_markup=smart_speaking_kb(lang,
                                                                                          str(message.message_id)))
            return True
        if _is_writing_file(message):
            await message.answer(t("smart.file_offer", lang), reply_markup=smart_writing_kb(lang))
            return True
    except Exception:  # noqa: BLE001 - Redis away: the plain "pick a section" instead
        logger.warning("Smart entry for user %s failed", uid, exc_info=True)
    return False


async def _text_entry(message: Message, lang: str) -> bool:
    async with _locks[message.chat.id]:  # the parts of a split essay, one after another
        return await _take_text(message, lang)


async def _take_text(message: Message, lang: str) -> bool:
    uid = message.chat.id
    text = message.text
    last = await _last(uid)
    if last and time.time() - last["at"] < SPLIT_GAP and last["long"]:
        # the next part of a long essay Telegram split: joined to the kept text, the offer above stands
        kept = await _kept(uid, last["token"])
        if kept is not None and kept.get("kind") == "text":
            kept["text"] = kept["text"].rstrip() + "\n" + text.strip()
            await _keep(uid, last["token"], kept)
            await _set_last(uid, last["token"], text)
            return True
    if not looks_like_essay(text):
        return False
    token = str(message.message_id)
    await _keep(uid, token, {"kind": "text", "text": text})
    await _set_last(uid, token, text)
    await message.answer(t("smart.essay_offer", lang, words=count_words(text)),
                         reply_markup=smart_writing_kb(lang, token))
    return True


async def _keep(uid: int, token: str, item: dict) -> None:
    await redis().set(KEPT_KEY.format(uid=uid, token=token), json.dumps(item, ensure_ascii=False), ex=KEPT_TTL)


async def _kept(uid: int, token: str) -> dict | None:
    try:
        raw = await redis().get(KEPT_KEY.format(uid=uid, token=token))
        item = json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001 - Redis away, or a damaged value: as if it were gone
        logger.warning("Kept answer %s of user %s not read", token, uid, exc_info=True)
        return None
    return item if isinstance(item, dict) else None


async def _forget(uid: int, token: str) -> None:
    try:
        await redis().delete(KEPT_KEY.format(uid=uid, token=token))
    except Exception:  # noqa: BLE001 - it expires by itself
        pass


async def _last(uid: int) -> dict | None:
    raw = await redis().get(LAST_KEY.format(uid=uid))
    return json.loads(raw) if raw else None


async def _set_last(uid: int, token: str, text: str) -> None:
    await redis().set(LAST_KEY.format(uid=uid), json.dumps({"token": token, "at": time.time(),
                                                            "long": len(text) >= SPLIT_CHARS}), ex=SPLIT_GAP)
