"""The check pipeline hardened before the launch (2026-11-16) - offline: a THROWAWAY Postgres and Redis (tests run
by test_part2.sh / the agent runner), Telegram, OpenAI and Azure are fakes; the grading itself is untouched.

B2 a report Telegram won't take: flood control waited out, the card kept until the report is there, a report
   that never goes turns the card into "ready and saved" with a button that sends it again.
B3 a voice answer or a photo whose download fails: tried again, else the student is asked to send it again,
   the flow kept.
B4 a database/Redis error just before grading: the check is `failed` with "🔁 Try again", nothing charged.
B5 a deploy: nothing new admitted while draining, the queued are not waited for, a check stopped by the exit
   keeps its hold for the next start, whose recover() gives the coins back once and offers "try again".
B6 the Speaking repeat check's pre-filter gives the very same decisions as the full ratio.
B7 Azure: one limit for every check, 429/5xx tried again after Retry-After, one check's whole assessment
   bounded (then the estimated band, the admins' alert).
B8 the price and payer decided again when the coins are held (a test window switched off, a sponsor gone).
C3 a refused check's data parked: "try again" after the FSM was cleared (a top-up) still grades it.
C6 a sponsor out of coins (or nearly) and its student are told - once a day.
"""
import asyncio
import io
import logging
import math
import os
import random
import sys
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

fails: list[str] = []


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


BASE = 7_300_000
_DP = []


def dispatcher():
    if not _DP:
        from bot.main import build_dispatcher

        _DP.append(build_dispatcher())
    return _DP[0]


async def make_user(uid: int, coins: int, lang: str = "uz") -> None:
    from db.database import get_session
    from db.models import User, UserStatus

    async with get_session() as s:
        u = await s.get(User, uid)
        if u is None:
            s.add(User(id=uid, full_name=f"U{uid}", coin_balance=coins, language=lang, status=UserStatus.approved))
        else:
            u.coin_balance = coins
        await s.commit()


async def set_coins(uid: int, coins: int) -> None:
    await make_user(uid, coins)


async def balance(uid: int) -> int:
    from sqlalchemy import select

    from db.database import get_session
    from db.models import User

    async with get_session() as s:
        return (await s.execute(select(User.coin_balance).where(User.id == uid))).scalar()


async def holds_of(uid: int) -> list:
    from sqlalchemy import select

    from db.database import get_session
    from db.models import CheckHold

    async with get_session() as s:
        return list((await s.execute(select(CheckHold).where(CheckHold.user_id == uid)
                                     .order_by(CheckHold.created_at))).scalars().all())


async def group_with(payer: int, students: list[int], **kw) -> int:
    from db.database import get_session
    from db.models import Group, GroupMembership

    async with get_session() as s:
        g = Group(name=kw.pop("name", f"G{payer}"), teacher_id=payer, sponsor_checks=kw.pop("sponsor", True), **kw)
        s.add(g)
        await s.flush()
        for uid in students:
            s.add(GroupMembership(group_id=g.id, user_id=uid))
        await s.commit()
        return g.id


class Card:
    """A stand-in for the check's "analysing…" message: records what it was edited into."""

    def __init__(self, uid: int, message_id: int = 1):
        self.chat = NS(id=uid)
        self.message_id = message_id
        self.edits: list[tuple[str, object]] = []

    async def edit_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))

    async def answer(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))


class State:
    """A stand-in FSMContext."""

    def __init__(self, data=None):
        self.data, self.state = dict(data or {}), None

    async def update_data(self, **kw):
        self.data.update(kw)

    async def get_data(self):
        return dict(self.data)

    async def set_state(self, s):
        self.state = getattr(s, "state", s)

    async def get_state(self):
        return self.state


def _wav(seconds: float = 2.0) -> bytes:
    rate, n = 16000, int(seconds * 16000)
    frames = bytearray()
    for i in range(n):
        frames += int(6000 * math.sin(2 * math.pi * 200 * i / rate)).to_bytes(2, "little", signed=True)
    frames[-2:] = os.urandom(2)  # never "the same recording again"
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


# --- a fake Telegram, the real handlers ---------------------------------------------------------------------


class World:
    """The bot's dispatcher over a fake Bot API session that records every call (and its answer), can refuse
    a call (`refuse(method)` -> an exception) and serves downloads (`files`; `stream_fail` downloads fail)."""

    def __init__(self):
        import aiohttp
        from aiogram import Bot
        from aiogram.client.session.base import BaseSession
        from aiogram.types import Chat, File, Message

        world = self
        self.files: dict[str, bytes] = {}
        self.stream_fail = 0
        self.refuse = None
        now = datetime.now(timezone.utc)

        class Session(BaseSession):
            def __init__(self):
                super().__init__()
                self.calls, self.results, self.n = [], [], 60_000

            async def close(self):
                pass

            async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
                if world.stream_fail:
                    world.stream_fail -= 1
                    raise aiohttp.ClientConnectionError("connection dropped")
                yield world.files[url.rsplit("/", 1)[-1]]

            async def make_request(self, bot, method, timeout=None):
                self.calls.append(method)
                self.n += 1
                err = world.refuse(method) if world.refuse else None
                if err is not None:
                    self.results.append(err)
                    raise err
                name = type(method).__name__
                if name == "GetFile":
                    result = File(file_id=method.file_id, file_unique_id="u" + method.file_id,
                                  file_size=len(world.files.get(method.file_id, b"")),
                                  file_path=f"documents/{method.file_id}")
                elif name in ("AnswerCallbackQuery", "DeleteMessage", "EditMessageReplyMarkup", "SendChatAction",
                              "SetMessageReaction"):
                    result = True
                else:
                    result = Message(message_id=getattr(method, "message_id", None) or self.n, date=now,
                                     chat=Chat(id=getattr(method, "chat_id", None) or 1, type="private"),
                                     text=getattr(method, "text", None) or "x").as_(bot)
                self.results.append(result)
                return result

        self.session = Session()
        self.bot = Bot("8888548766:TEST", session=self.session)
        self.dp = dispatcher()
        self.counter = iter(range(500_000, 900_000))

    def state(self, uid: int):
        return self.dp.fsm.get_context(bot=self.bot, chat_id=uid, user_id=uid)

    async def send(self, uid: int, **kw):
        from aiogram.types import Chat, Message, Update, User

        start = len(self.session.calls)
        message = Message(message_id=next(self.counter), date=datetime.now(timezone.utc),
                          chat=Chat(id=uid, type="private"), from_user=User(id=uid, is_bot=False, first_name="S"),
                          **kw)
        await self.dp.feed_update(self.bot, Update(update_id=next(self.counter), message=message))
        return self.session.calls[start:]

    async def press(self, uid: int, data: str):
        from aiogram.types import CallbackQuery, Chat, Message, Update, User

        start = len(self.session.calls)
        query = CallbackQuery(id=str(next(self.counter)), from_user=User(id=uid, is_bot=False, first_name="S"),
                              chat_instance="ci", data=data,
                              message=Message(message_id=next(self.counter), date=datetime.now(timezone.utc),
                                              chat=Chat(id=uid, type="private"), text="x"))
        await self.dp.feed_update(self.bot, Update(update_id=next(self.counter), callback_query=query))
        return self.session.calls[start:]

    def card_id(self, calls, text: str) -> int | None:
        """The message id Telegram gave the message sent with `text` (the check's card)."""
        for c in calls:
            if type(c).__name__ == "SendMessage" and c.text == text:
                i = next(j for j, x in enumerate(self.session.calls) if x is c)
                return getattr(self.session.results[i], "message_id", None)
        return None


def texts(calls) -> list[str]:
    return [c.text for c in calls if type(c).__name__ in ("SendMessage", "EditMessageText")]


def buttons(calls) -> list[str]:
    out = []
    for c in calls:
        for row in getattr(getattr(c, "reply_markup", None), "inline_keyboard", None) or []:
            out += [b.callback_data for b in row if b.callback_data]
    return out


def retry_token(calls) -> str | None:
    from bot.utils import check_guard as guard

    return next((b for b in reversed(buttons(calls)) if b.startswith(guard.CB_RETRY)), None)


def docs(calls):
    return [c for c in calls if type(c).__name__ == "SendDocument"]


PROMPT = ("Some people believe that university education should be free for everyone. Others think students should "
          "pay for it. Discuss both views and give your own opinion.")
SENTENCE = "Universities give young people the knowledge and skills they need for a successful career. "


def essay(tag: str) -> str:
    return SENTENCE * 12 + f"In the end {tag} matters."


GRADED: list[str] = []
CONTROL = {"gate": None, "fail": 0}


def fake_writing() -> None:
    from bot.handlers import writing
    from bot.utils import progress

    progress.ENABLED = False

    async def fake_grade(**kw):
        GRADED.append(kw["answer_text"])
        if CONTROL["gate"] is not None:
            await CONTROL["gate"].wait()
        if CONTROL["fail"]:
            CONTROL["fail"] -= 1
            raise RuntimeError("model call failed")
        kw["on_feedback"](None, None, None)
        return NS(overall_band=6.5, vocabulary=[NS(term="t", meaning="m", example="e")], mistakes=[],
                  criteria_scores=lambda: {"TR": 6.5, "CC": 6.5, "LR": 6.5, "GRA": 6.5},
                  mistakes_json=lambda: [], vocabulary_uz=None)

    async def fake_answer(*a, **k):
        return NS(source="ai_generated", band=7.0, shows=[], log={}, alternative=None)

    async def fake_uz(vocab):
        return [""] * len(vocab)

    writing.grade_writing = fake_grade
    writing.build_model_answer = fake_answer
    writing.uzbek_meanings = fake_uz
    writing.needs_from_feedback = lambda *a: None
    writing.build_progress = lambda *a: {"first": True}
    writing.build_writing_report_pdf = lambda *a: b"%PDF-1.4 fake"


SPOKEN: list[int] = []


def fake_speaking() -> None:
    from bot.handlers import speaking
    from bot.utils import progress

    progress.ENABLED = False

    async def fake_run_check(part, questions, clips, seed=0, transcribed=None):
        SPOKEN.append(len(clips))
        report = NS(notices=[], overall_band=6.0, mistakes=[], vocabulary=[], pron_estimated=True, priorities=[],
                    criteria_scores=lambda: {"FC": 6.0, "LR": 6.0, "GRA": 6.0, "Pronunciation": 6.0},
                    mistakes_json=lambda: [])
        return NS(report=report, items=[NS(audio=NS(text="I like reading books."))] * len(clips),
                  metrics=NS(as_json=lambda: {}), answers=[], pron=None)

    speaking.run_check = fake_run_check
    speaking.short_sample_size = lambda *a: 0
    speaking.build_progress = lambda *a: {"first": True}
    speaking.build_speaking_report_pdf = lambda *a: b"%PDF-1.4 fake"
    speaking.tts = NS(enabled=lambda: False)


async def start_task2(w: World, uid: int) -> None:
    from bot.keyboards.main_menu_kb import BTN_WRITING
    from bot.keyboards.writing_task_kb import CB_TASK2

    await w.send(uid, text=BTN_WRITING)
    await w.press(uid, CB_TASK2)
    await w.send(uid, text=PROMPT)


async def wait_for(cond, seconds: float = 5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return cond()


# --- B2 ------------------------------------------------------------------------------------------------------


async def b2_delivery(w: World) -> None:
    from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
    from sqlalchemy import select

    from bot.i18n import t
    from bot.keyboards.my_works_kb import CB_DOWNLOAD_PREFIX
    from bot.utils import check_guard as guard
    from db.database import get_session
    from db.models import WritingSubmission

    uid = BASE + 1
    await make_user(uid, 50)
    card_text = t("writing.grading_in_progress", "uz")

    # Telegram's flood control on the report: waited out, sent, and only then the card goes
    refused = {"n": 0}

    def flood_once(method):
        if type(method).__name__ == "SendDocument" and refused["n"] == 0:
            refused["n"] += 1
            return TelegramRetryAfter(method=method, message="Too Many Requests: retry after 0", retry_after=0)
        return None

    w.refuse = flood_once
    await start_task2(w, uid)
    began = time.monotonic()
    out = await w.send(uid, text=essay("flood"))
    w.refuse = None
    sends = [i for i, c in enumerate(out) if type(c).__name__ == "SendDocument"]
    card = w.card_id(out, card_text)
    deletes = [i for i, c in enumerate(out) if type(c).__name__ == "DeleteMessage" and c.message_id == card]
    check("B2 flood control on the report: waited out (retry_after + 1 s), then sent",
          len(sends) == 2 and time.monotonic() - began >= 1.0, (len(sends), round(time.monotonic() - began, 2)))
    check("B2 ...the card deleted only after the report went", deletes and deletes[0] > sends[-1], (deletes, sends))

    # the network down for the report: tried 4 times, the card says "ready and saved" with a resend button
    guard.DELIVERY_WAITS = (0.01, 0.01, 0.01)
    w.refuse = lambda m: TelegramNetworkError(method=m, message="connection reset") \
        if type(m).__name__ == "SendDocument" else None
    coins = await balance(uid)
    await start_task2(w, uid)
    out = await w.send(uid, text=essay("network"))
    w.refuse = None
    card = w.card_id(out, card_text)
    async with get_session() as s:
        sub = (await s.execute(select(WritingSubmission).where(WritingSubmission.user_id == uid)
                               .order_by(WritingSubmission.id.desc()))).scalars().first()
    edits = [c for c in out if type(c).__name__ == "EditMessageText" and c.message_id == card]
    resend = f"{CB_DOWNLOAD_PREFIX}writing:{sub.id}"
    check("B2 report never delivered: 4 tries, the check still saved and paid once",
          len(docs(out)) == 4 and await balance(uid) == coins - 5, (len(docs(out)), await balance(uid)))
    check("B2 ...the card (not deleted) says it is ready and saved, with a button that sends it again",
          edits and "Hisobotingiz tayyor va saqlandi" in edits[-1].text and resend in buttons(edits)
          and not any(type(c).__name__ == "DeleteMessage" and c.message_id == card for c in out),
          [e.text[:60] for e in edits])
    out = await w.press(uid, resend)
    check("B2 ...the button sends the saved report", len(docs(out)) == 1, [type(c).__name__ for c in out])

    # deliver() itself: a bad request is never tried again; a flood wait too long is given up
    tries = {"n": 0}

    async def bad():
        tries["n"] += 1
        raise TelegramBadRequest(method=None, message="Bad Request: wrong file")

    async def long_flood():
        tries["n"] += 1
        raise TelegramRetryAfter(method=None, message="flood", retry_after=3600)

    ok = await guard.deliver(bad, "test bad")
    check("B2 deliver: a bad request is not tried again", ok is False and tries["n"] == 1, tries)
    tries["n"] = 0
    ok = await guard.deliver(long_flood, "test flood")
    check("B2 deliver: an hour-long flood wait is given up at once", ok is False and tries["n"] == 1, tries)


# --- B3 ------------------------------------------------------------------------------------------------------


async def b3_downloads(w: World) -> None:
    from aiogram.types import Document, PhotoSize

    from bot.keyboards.main_menu_kb import BTN_SPEAKING, BTN_WRITING
    from bot.keyboards.speaking_part_kb import CB_MODE_EACH, CB_PART1
    from bot.keyboards.writing_task_kb import CB_TASK1
    from bot.states.speaking_states import SpeakingStates
    from bot.states.writing_states import WritingStates
    from bot.utils import input_extraction

    input_extraction.RETRY_PAUSE = 0.01
    uid = BASE + 2
    await make_user(uid, 50)
    st = w.state(uid)
    await w.send(uid, text=BTN_SPEAKING)
    await w.press(uid, CB_PART1)
    await w.send(uid, text="Do you like reading books?\nWhat kind of books do you read?")
    await w.press(uid, CB_MODE_EACH)

    def voice():
        fid = f"v{next(w.counter)}"
        w.files[fid] = _wav()
        return Document(file_id=fid, file_unique_id="u" + fid, file_name="a.wav", mime_type="audio/wav",
                        file_size=len(w.files[fid]))

    w.stream_fail = 1
    out = await w.send(uid, document=voice())
    data = await st.get_data()
    check("B3 a voice answer's download fails once: tried again, the answer taken",
          len(data.get("answers") or []) == 1 and data.get("q_index") == 1
          and await st.get_state() == SpeakingStates.awaiting_answer.state, (data.get("q_index"), texts(out)))

    w.stream_fail = 2
    out = await w.send(uid, document=voice())
    data = await st.get_data()
    check("B3 ...failing twice: the student is asked to send it again, the flow kept",
          any("Ovozli xabaringizni qabul qila olmadim" in x for x in texts(out))
          and len(data.get("answers") or []) == 1 and await st.get_state() == SpeakingStates.awaiting_answer.state,
          texts(out))

    real_save = input_extraction.save_upload

    def disk_full(*a, **k):
        raise OSError(28, "No space left on device")

    input_extraction.save_upload = disk_full
    try:
        out = await w.send(uid, document=voice())
    finally:
        input_extraction.save_upload = real_save
    data = await st.get_data()
    check("B3 ...the disk full: asked again too, nothing lost", any("qabul qila olmadim" in x for x in texts(out))
          and len(data.get("answers") or []) == 1, texts(out))
    SPOKEN.clear()
    out = await w.send(uid, document=voice())
    check("B3 ...sent again: taken, and the set graded", SPOKEN == [2] and docs(out), (SPOKEN, texts(out)))

    # Writing: a photo answer, and a Task 1 chart, whose download fails twice
    await start_task2(w, uid)
    w.stream_fail = 2
    out = await w.send(uid, photo=[PhotoSize(file_id="ph1", file_unique_id="uph1", width=800, height=600)])
    check("B3 Writing: a photo answer not received - asked again, still waiting for the answer",
          any("Rasm yoki faylingizni qabul qila olmadim" in x for x in texts(out))
          and await w.state(uid).get_state() == WritingStates.awaiting_answer.state, texts(out))
    await w.send(uid, text=BTN_WRITING)
    await w.press(uid, CB_TASK1)
    await w.send(uid, text="The chart below shows the favourite leisure activities of teenagers in Canada. Summarise "
                           "the information by selecting and reporting the main features.")
    w.stream_fail = 2
    out = await w.send(uid, photo=[PhotoSize(file_id="ph2", file_unique_id="uph2", width=800, height=600)])
    check("B3 Writing: a Task 1 chart not received - asked again, still waiting for the chart",
          any("Rasm yoki faylingizni qabul qila olmadim" in x for x in texts(out))
          and await w.state(uid).get_state() == WritingStates.awaiting_chart.state, texts(out))
    w.stream_fail = 0
    await w.state(uid).clear()


# --- B4 ------------------------------------------------------------------------------------------------------


async def b4_before_grading(w: World) -> None:
    import redis.exceptions
    import sqlalchemy.exc

    from bot.handlers import speaking, writing
    from bot.i18n import t
    from bot.keyboards.main_menu_kb import BTN_SPEAKING
    from bot.keyboards.speaking_part_kb import CB_MODE_EACH, CB_PART1
    from bot.states.speaking_states import SpeakingStates
    from bot.states.writing_states import WritingStates
    from bot.utils import check_guard as guard
    from bot.utils import quota
    from aiogram.types import Document

    uid = BASE + 3
    await make_user(uid, 50)
    st = w.state(uid)

    # the database pool timing out while the earlier essays are read
    real_repeat = writing._repeat_of

    async def pool_timeout(*a, **k):
        raise sqlalchemy.exc.TimeoutError("QueuePool limit of size 5 overflow 10 reached")

    writing._repeat_of = pool_timeout
    coins = await balance(uid)
    await start_task2(w, uid)
    try:
        out = await w.send(uid, text=essay("pool"))
    finally:
        writing._repeat_of = real_repeat
    token = retry_token(out)
    check("B4 a pool timeout before grading: `failed` with 🔁 Try again, nothing charged, no hold",
          token is not None and await st.get_state() == WritingStates.failed.state and await balance(uid) == coins
          and not [h for h in await holds_of(uid) if h.state == "held"]
          and any("yakunlanmadi" in x for x in texts(out)), texts(out))
    GRADED.clear()
    out = await w.press(uid, token)
    check("B4 ...try again: graded, charged once, report sent", len(GRADED) == 1 and docs(out)
          and await balance(uid) == coins - 5, (GRADED, await balance(uid)))

    # Redis away while the price is decided (inside begin()): the card itself says so
    real_fresh = quota.fresh_quota

    async def redis_down(*a, **k):
        raise redis.exceptions.ConnectionError("Error 111 connecting to redis")

    quota.fresh_quota = redis_down
    coins = await balance(uid)
    await start_task2(w, uid)
    try:
        out = await w.send(uid, text=essay("redis"))
    finally:
        quota.fresh_quota = real_fresh
    card = w.card_id(out, t("writing.grading_in_progress", "uz"))
    edits = [c for c in out if type(c).__name__ == "EditMessageText" and c.message_id == card]
    check("B4 Redis away inside begin(): the 'analysing…' card becomes 'failed' + 🔁 Try again, nothing charged",
          edits and guard.CB_RETRY in str(edits[-1].reply_markup) and await balance(uid) == coins
          and await st.get_state() == WritingStates.failed.state, texts(out))
    await st.clear()

    # Speaking: the earlier sessions unreadable
    real_repeated = speaking._repeated

    async def broken(*a, **k):
        raise redis.exceptions.ConnectionError("Redis away")

    await w.send(uid, text=BTN_SPEAKING)
    await w.press(uid, CB_PART1)
    await w.send(uid, text="Do you like reading books?")
    await w.press(uid, CB_MODE_EACH)
    fid = f"b4{next(w.counter)}"
    w.files[fid] = _wav()
    speaking._repeated = broken
    try:
        out = await w.send(uid, document=Document(file_id=fid, file_unique_id="u" + fid, file_name="a.wav",
                                                  mime_type="audio/wav", file_size=len(w.files[fid])))
    finally:
        speaking._repeated = real_repeated
    token = retry_token(out)
    check("B4 Speaking: an error before grading - `failed` with 🔁 Try again, the recording kept",
          token is not None and await st.get_state() == SpeakingStates.failed.state
          and len((await st.get_data()).get("answers") or []) == 1, texts(out))
    SPOKEN.clear()
    coins = await balance(uid)
    out = await w.press(uid, token)
    check("B4 ...try again grades it, charged once", SPOKEN == [1] and docs(out) and await balance(uid) == coins - 5,
          (SPOKEN, await balance(uid)))


# --- B5 ------------------------------------------------------------------------------------------------------


async def b5_shutdown(w: World) -> None:
    import time as _time

    from bot.states.writing_states import WritingStates
    from bot.utils import check_guard as guard
    from bot.utils.capacity import governor

    ua, ub, uc, ud = BASE + 51, BASE + 52, BASE + 53, BASE + 54
    for uid in (ua, ub, uc, ud):
        await make_user(uid, 20)
        await start_task2(w, uid)
    governor.cap, governor.cap_read_at = 1, _time.monotonic() + 3600
    GRADED.clear()
    CONTROL["gate"] = asyncio.Event()

    # UA being graded, UB queued behind it (one at a time here)
    task_a = asyncio.create_task(w.send(ua, text=essay("A")))
    await wait_for(lambda: any("A matters" in g for g in GRADED))
    task_b = asyncio.create_task(w.send(ub, text=essay("B")))
    await wait_for(lambda: len(governor.queue) == 1)
    check("B5 setup: one check graded, one queued", governor.running == 1 and len(governor.queue) == 1,
          (governor.running, governor.queue))
    began = time.monotonic()
    drain = asyncio.create_task(guard.drain(timeout=10))
    await asyncio.sleep(0.4)
    check("B5 drain: still waiting for the check being graded; the queued one not admitted",
          not drain.done() and guard.shutting_down and governor.closed
          and not any("B matters" in g for g in GRADED) and len(governor.queue) == 1)
    CONTROL["gate"].set()
    await asyncio.wait_for(drain, 5)
    await asyncio.gather(asyncio.wait_for(task_a, 5), return_exceptions=True)
    check("B5 ...the graded one finishes and drain returns, not waiting for the queued (nothing admitted)",
          time.monotonic() - began < 4 and not any("B matters" in g for g in GRADED) and len(governor.queue) == 1
          and await balance(ua) == 15, round(time.monotonic() - began, 2))
    task_b.cancel()  # the process exits: asyncio.run cancels what is left
    await asyncio.gather(task_b, return_exceptions=True)
    hb = (await holds_of(ub))[-1]
    check("B5 the queued check cancelled at exit: its hold stays held, its coins still held",
          hb.state == "held" and await balance(ub) == 15, (hb.state, await balance(ub)))

    # a check still graded when the drain times out
    guard.shutting_down, governor.closed = False, False
    CONTROL["gate"] = asyncio.Event()
    task_c = asyncio.create_task(w.send(uc, text=essay("C")))
    await wait_for(lambda: any("C matters" in g for g in GRADED))
    began = time.monotonic()
    await guard.drain(timeout=0.3)
    check("B5 drain gives up after its timeout", 0.25 <= time.monotonic() - began < 2 and not task_c.done())
    task_c.cancel()
    await asyncio.gather(task_c, return_exceptions=True)
    hc = (await holds_of(uc))[-1]
    check("B5 the check cancelled at exit: hold held, coins held (no refund, no message yet)",
          hc.state == "held" and await balance(uc) == 15, (hc.state, await balance(uc)))

    # the next start
    guard.shutting_down, governor.closed = False, False
    guard.running.clear()
    governor.cap, governor.cap_read_at = 0, -1e9
    governor.running = 0
    CONTROL["gate"] = None
    start = len(w.session.calls)
    await guard.recover(w.bot, w.dp.storage)
    told = [c for c in w.session.calls[start:] if type(c).__name__ in ("EditMessageText", "SendMessage")]
    to_b = [c for c in told if c.chat_id == ub]
    to_c = [c for c in told if c.chat_id == uc]
    check("B5 next start: both holds interrupted, the coins back once",
          (await holds_of(ub))[-1].state == "interrupted" and (await holds_of(uc))[-1].state == "interrupted"
          and await balance(ub) == 20 and await balance(uc) == 20, (await balance(ub), await balance(uc)))
    check("B5 ...each student told once, on the check's own card, with 🔁 Try again",
          len(to_b) == 1 and len(to_c) == 1 and to_c[0].message_id == hc.message_id
          and guard.CB_RETRY in str(to_c[0].reply_markup) and "yangilanayotgan" in to_c[0].text
          and await w.state(uc).get_state() == WritingStates.failed.state, [x.text[:40] for x in told])
    start = len(w.session.calls)
    n = await guard.recover(w.bot, w.dp.storage)
    check("B5 ...recover again: nothing more, nobody told twice", n == 0 and len(w.session.calls) == start)
    GRADED.clear()
    out = await w.press(uc, retry_token(to_c))
    check("B5 ...try again grades the kept answer, charged once", len(GRADED) == 1 and "C matters" in GRADED[0]
          and docs(out) and await balance(uc) == 15, (GRADED, await balance(uc)))

    # outside a shutdown a cancelled check still gives its coins back at once
    CONTROL["gate"] = asyncio.Event()
    task_d = asyncio.create_task(w.send(ud, text=essay("D")))
    await wait_for(lambda: any("D matters" in g for g in GRADED))
    task_d.cancel()
    await asyncio.gather(task_d, return_exceptions=True)
    CONTROL["gate"] = None
    check("B5 a cancellation outside a shutdown: refunded as before", (await holds_of(ud))[-1].state == "refunded"
          and await balance(ud) == 20, ((await holds_of(ud))[-1].state, await balance(ud)))
    for uid in (ua, ub, uc, ud):
        await w.state(uid).clear()


# --- B6 ------------------------------------------------------------------------------------------------------


def b6_prefilter() -> None:
    import difflib

    from bot.handlers.speaking import RESUBMISSION_SIMILARITY, _find_resubmission, _said

    rng = random.Random(16)
    words = ("i think reading is very important because it helps me relax and learn new things about the world "
             "my favourite books are novels and sometimes history books when i have free time").split()

    def text(n):
        return " ".join(rng.choice(words) for _ in range(n))

    def mutate(s, share):
        chars = list(s)
        for _ in range(int(len(chars) * share)):
            i = rng.randrange(len(chars))
            op = rng.random()
            if op < 0.4:
                chars[i] = rng.choice("abcdefghijklmnopqrstuvwxyz ")
            elif op < 0.7:
                del chars[i]
            else:
                chars.insert(i, rng.choice("abcdefghijklmnopqrstuvwxyz "))
        return "".join(chars)

    same = differ = mismatch = 0
    for _ in range(300):
        base = text(rng.randint(5, 120))
        other = mutate(base, rng.choice((0.0, 0.01, 0.03, 0.05, 0.08, 0.12, 0.3))) if rng.random() < 0.8 \
            else text(rng.randint(5, 120))
        sessions = [NS(report_data={"transcripts": [base]})]
        new = _find_resubmission(sessions, "h", [other]) is sessions[0]
        old = difflib.SequenceMatcher(None, _said([base]), _said([other]), autojunk=False).ratio() \
            > RESUBMISSION_SIMILARITY
        same += old
        differ += not old
        mismatch += new != old
    check("B6 the pre-filter decides exactly as the full ratio (300 pairs, both outcomes present)",
          mismatch == 0 and same > 20 and differ > 20, (mismatch, same, differ))

    # several sessions: the same first match as the old loop
    base = text(80)
    sessions = [NS(id=i, report_data={"transcripts": [t]}) for i, t in
                enumerate([text(80), mutate(base, 0.02), base, text(3)])]
    found = _find_resubmission(sessions, "h", [base])
    old = next((s for s in sessions if difflib.SequenceMatcher(
        None, _said(s.report_data["transcripts"]), _said([base]), autojunk=False).ratio() > RESUBMISSION_SIMILARITY),
        None)
    check("B6 ...several earlier sessions: the same one found", found is old and found is not None,
          (getattr(found, "id", None), getattr(old, "id", None)))
    sessions = [NS(report_data={"audio_hash": "x"}), NS(report_data={"audio_hash": "h"})]
    check("B6 ...the very same recordings: still by fingerprint", _find_resubmission(sessions, "h") is sessions[1])


# --- B7 ------------------------------------------------------------------------------------------------------


async def b7_azure() -> None:
    import httpx

    from bot.config import settings
    from services.ai import pronunciation

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wv:
        wv.setnchannels(1)
        wv.setsampwidth(2)
        wv.setframerate(16000)
        wv.writeframes(b"\x00\x00" * 16000)
    clip = (buf.getvalue(), [NS(text="hello", start=0.1, end=0.4), NS(text="world", start=0.5, end=0.9)], 1.0)
    good = {"RecognitionStatus": "Success", "NBest": [{
        "AccuracyScore": 80, "FluencyScore": 70, "CompletenessScore": 90, "PronScore": 75, "ProsodyScore": 70,
        "Words": [{"Word": "hello", "AccuracyScore": 80, "ErrorType": "None"}]}]}
    records: list = []
    catch = logging.Handler()
    catch.emit = records.append
    log = logging.getLogger("services.ai.pronunciation")
    log.addHandler(catch)
    real = (httpx.AsyncClient, pronunciation.configured, pronunciation._endpoint, pronunciation.RETRY_WAIT_CAP,
            settings.azure_max_parallel, settings.azure_timeout_seconds)

    def serve(handler):
        httpx.AsyncClient = lambda **kw: real[0](transport=httpx.MockTransport(handler), **kw)

    try:
        pronunciation.configured = lambda: True
        pronunciation._endpoint = lambda: "https://eastus.stt.speech.microsoft.com/v1"
        settings.azure_max_parallel = 2
        pronunciation._gate_by_loop = None
        flight = {"now": 0, "max": 0, "calls": 0}

        async def ok(request):
            flight["calls"] += 1
            flight["now"] += 1
            flight["max"] = max(flight["max"], flight["now"])
            await asyncio.sleep(0.05)
            flight["now"] -= 1
            return httpx.Response(200, json=good)

        serve(ok)
        results = await asyncio.gather(*(pronunciation.assess_answers([clip, clip, clip]) for _ in range(3)))
        check("B7 one limit over every check: 9 requests, never more than azure_max_parallel (2) at once",
              flight["calls"] == 9 and flight["max"] == 2 and all(r is not None for r in results), flight)

        pronunciation.RETRY_WAIT_CAP = 0.05
        replies: list = []

        async def scripted(request):
            flight["calls"] += 1
            return replies.pop(0) if replies else httpx.Response(200, json=good)

        serve(scripted)
        flight["calls"] = 0
        replies[:] = [httpx.Response(429, headers={"Retry-After": "0"}, json={})]
        r = await pronunciation.assess_answers([clip])
        check("B7 a 429 tried again after Retry-After, then measured", r is not None and flight["calls"] == 2, flight)
        flight["calls"] = 0
        replies[:] = [httpx.Response(503, json={}), httpx.Response(500, json={})]
        began = time.monotonic()
        r = await pronunciation.assess_answers([clip])
        check("B7 5xx twice: tried twice more, measured", r is not None and flight["calls"] == 3, flight)
        flight["calls"] = 0
        records.clear()
        replies[:] = [httpx.Response(429, headers={"Retry-After": "100"}, json={}) for _ in range(3)]
        began = time.monotonic()
        r = await pronunciation.assess_answers([clip])
        errors = [x for x in records if x.levelno >= logging.ERROR]
        check("B7 429 three times: at most 2 retries, each wait capped (Retry-After 100 s not waited), then the "
              "estimated band and the admins' alert",
              r is None and flight["calls"] == 3 and time.monotonic() - began < 2 and errors
              and errors[0].getMessage().startswith("Azure Pronunciation is not working (HTTP 429"),
              (flight, [x.getMessage()[:80] for x in records]))
        flight["calls"] = 0
        replies[:] = [httpx.Response(401, json={})]
        r = await pronunciation.assess_answers([clip])
        check("B7 a 401 is not tried again", r is None and flight["calls"] == 1, flight)

        settings.azure_timeout_seconds = 0.3

        async def slow(request):
            await asyncio.sleep(3)
            return httpx.Response(200, json=good)

        serve(slow)
        records.clear()
        began = time.monotonic()
        r = await pronunciation.assess_answers([clip, clip])
        errors = [x for x in records if x.levelno >= logging.ERROR]
        check("B7 a slow Azure: the whole assessment stopped at azure_timeout_seconds - estimated band, ERROR alert",
              r is None and time.monotonic() - began < 1.5 and errors
              and errors[0].getMessage().startswith("Azure Pronunciation is not working (no result within"),
              (round(time.monotonic() - began, 2), [x.getMessage()[:90] for x in records]))
    finally:
        (httpx.AsyncClient, pronunciation.configured, pronunciation._endpoint, pronunciation.RETRY_WAIT_CAP,
         settings.azure_max_parallel, settings.azure_timeout_seconds) = real
        pronunciation._gate_by_loop = None
        log.removeHandler(catch)


# --- B8 ------------------------------------------------------------------------------------------------------


async def b8_price(w: World) -> None:
    from bot.states.writing_states import WritingStates
    from bot.utils import check_guard as guard
    from bot.utils import runtime
    from bot.utils.crm import redis
    from bot.utils.quota import fresh_quota
    from db.database import get_session
    from db.models import Assignment, AssignmentPart, Group, GroupMembership

    student, sponsor = BASE + 81, BASE + 82
    await make_user(student, 20)
    await make_user(sponsor, 100)

    async def start(data):
        st = State(data)
        card = Card(student)
        h = await guard.begin(card, st, st.data, module="w", kind="writing_task2", lang="uz",
                              failed_state=WritingStates.failed)
        return h, st, card

    # a flow started in the test window (free), its check after the window was switched off
    await runtime.set_testers({student})
    h, st, _ = await start({"spend_credit": False})
    check("B8 in a test window, a tester's check is free", h is not None and h.coins == 0 and await balance(student) == 20)
    await guard.end(h)
    await runtime.set_testers(set())
    h, st, _ = await start({"spend_credit": False, "payer_id": None})
    check("B8 the window switched off since the flow began: priced now (5 coins), the FSM data updated",
          h is not None and h.coins == 5 and await balance(student) == 15 and st.data.get("spend_credit") is True)
    await guard.end(h)
    await redis().delete(runtime.TESTERS_KEY)
    runtime._cache.update(at=0.0, value=None)

    # a sponsor: pays while sponsoring; turned it off since -> the student's own coins; the student left -> same
    gid = await group_with(sponsor, [student])
    h, st, _ = await start({"spend_credit": True, "payer_id": None})
    check("B8 a sponsored group the flow did not know of: the sponsor pays", h is not None and h.sponsor_id == sponsor
          and await balance(sponsor) == 95 and st.data.get("payer_id") == sponsor)
    await guard.end(h)
    async with get_session() as s:
        (await s.get(Group, gid)).sponsor_checks = False
        await s.commit()
    h, st, _ = await start({"spend_credit": True, "payer_id": sponsor})
    check("B8 the sponsor stopped paying since: the student pays, not the sponsor", h is not None
          and h.sponsor_id is None and await balance(sponsor) == 100 and await balance(student) == 15
          and st.data.get("payer_id") is None)
    await guard.end(h)
    async with get_session() as s:
        (await s.get(Group, gid)).sponsor_checks = True
        await s.commit()
        from sqlalchemy import delete
        await s.execute(delete(GroupMembership).where(GroupMembership.group_id == gid))
        await s.commit()
    await set_coins(student, 2)
    h, st, card = await start({"spend_credit": True, "payer_id": sponsor})
    parked = await guard.parked(student)
    check("B8 the student left the group and has 2 coins: refused with the paywall and 🔁 Try again, parked",
          h is None and "Coin yetarli emas" in card.edits[-1][0] and guard.CB_RETRY in str(card.edits[-1][1])
          and st.state == WritingStates.failed.state and await balance(sponsor) == 100
          and parked is not None and parked["data"]["check_token"] == st.data["check_token"])
    await set_coins(student, 20)

    # the re-check never sees its own new check as "busy" - another one running is
    h1, _, _ = await start({"spend_credit": True})
    h2, st2, card2 = await start({"spend_credit": True})
    check("B8 a check of theirs being graded: the second refused as busy (the first was not)", h1 is not None
          and h2 is None and "baholanmoqda" in card2.edits[-1][0] and await balance(student) == 15)
    await guard.end(h1)

    # an assignment of a group with its daily limit used up: still the group's (assignments are never limited)
    async with get_session() as s:
        g = await s.get(Group, gid)
        g.daily_limit = 0
        s.add(GroupMembership(group_id=gid, user_id=student))
        a = Assignment(group_id=gid, kind="homework", title="HW", due_at=datetime.now(timezone.utc), created_by=sponsor)
        s.add(a)
        await s.flush()
        part = AssignmentPart(assignment_id=a.id, position=1, module="writing_task2", prompt_text="Q")
        s.add(part)
        await s.commit()
        part_id = part.id
    plain = await fresh_quota(student, "writing_task2", {})
    homework = await fresh_quota(student, "writing_task2", {"assignment_part_id": part_id})
    check("B8 the same assignment group is asked again: its homework is the sponsor's, other checks are not",
          plain.payer_id is None and homework.payer_id == sponsor, (plain, homework))
    async with get_session() as s:
        from sqlalchemy import delete
        await s.execute(delete(GroupMembership).where(GroupMembership.group_id == gid))
        await s.commit()
    from bot.utils import sponsor_alerts
    await sponsor_alerts.settle()


# --- C3 ------------------------------------------------------------------------------------------------------


async def c3_parked(w: World) -> None:
    from bot.states.writing_states import WritingStates
    from bot.utils import check_guard as guard

    uid = BASE + 91
    await make_user(uid, 20)
    st = w.state(uid)
    await start_task2(w, uid)
    await set_coins(uid, 2)  # spent elsewhere meanwhile: the check is refused at its start
    out = await w.send(uid, text=essay("parked"))
    token = retry_token(out)
    check("C3 refused for coins: the paywall with 🔁 Try again, the answer in `failed`", token is not None
          and await st.get_state() == WritingStates.failed.state and any("Coin yetarli emas" in x for x in texts(out)))
    await st.clear()  # the account screen / a receipt sent: the FSM state wiped
    kb = await guard.parked_keyboard(uid, "uz")
    continue_data = kb.inline_keyboard[0][0].callback_data if kb else None
    check("C3 parked_keyboard: '🔁 Continue my check' with the stopped check's own retry button",
          kb is not None and continue_data == token and "davom ettirish" in kb.inline_keyboard[0][0].text,
          continue_data)
    # merged with the payments work (2026-11-16): never over a receipt step; the approval offers the button
    from bot.handlers import payments as pay
    from bot.states.payment_states import PaymentStates
    from db import crud as db_crud
    from db.database import get_session as db_session

    await st.set_state(PaymentStates.awaiting_receipt)
    GRADED.clear()
    out = await w.press(uid, continue_data)
    alerts = [c.text for c in out if type(c).__name__ == "AnswerCallbackQuery" and getattr(c, "show_alert", False)]
    check("C3 'Continue' pressed at the receipt step: told to send the receipt first, the payment step kept",
          alerts and "chekni yuboring" in alerts[0] and await st.get_state() == PaymentStates.awaiting_receipt.state
          and not GRADED, alerts)
    await st.clear()
    async with db_session() as s:
        req = await db_crud.create_payment_request(s, user_id=uid, coins=10, amount=10_000)
        await db_crud.set_payment_receipt(s, req.id, "file-c3", "uniq-c3")
        decision = await db_crud.decide_payment_request(s, req.id, approve=True, admin_id=111)
    start_at = len(w.session.calls)
    await pay.notify_decision(w.bot, decision)
    approval = [c for c in w.session.calls[start_at:] if type(c).__name__ == "SendMessage" and c.chat_id == uid]
    check("C3 the payment approval message carries '🔁 Continue my check'",
          approval and continue_data in buttons(approval), [buttons(approval)])
    await set_coins(uid, 20)  # the top-up approved
    GRADED.clear()
    out = await w.press(uid, continue_data)
    check("C3 after the FSM was cleared and the top-up: the parked answer graded, charged once", len(GRADED) == 1
          and "parked matters" in GRADED[0] and docs(out) and await balance(uid) == 15, (GRADED, await balance(uid)))
    check("C3 ...the parked copy is gone once its check took its hold", await guard.parked(uid) is None
          and await guard.parked_keyboard(uid, "uz") is None)
    out = await w.press(uid, continue_data)
    check("C3 ...the old button again: 'gone', nothing graded", len(GRADED) == 1 and any(
        getattr(c, "show_alert", False) for c in out if type(c).__name__ == "AnswerCallbackQuery"))


# --- C6 ------------------------------------------------------------------------------------------------------


async def c6_sponsor_alerts(w: World) -> None:
    from bot.utils import crm, sponsor_alerts
    from bot.utils.crm import redis
    from bot.utils.quota import check_quota

    sponsor_alerts.use(w.bot)
    posts: list = []
    real_post = crm.post

    async def post(topic, text, **kw):
        posts.append((topic, text))

    crm.post = post
    try:
        empty_payer, empty_student = BASE + 101, BASE + 102
        await make_user(empty_payer, 3, "ru")
        await make_user(empty_student, 20)
        await group_with(empty_payer, [empty_student], name="Ielts A")

        def sent_to(start, uid):
            return [c.text for c in w.session.calls[start:] if type(c).__name__ == "SendMessage" and c.chat_id == uid]

        start = len(w.session.calls)
        q = await check_quota(empty_student, "writing_task2")
        await sponsor_alerts.settle()
        payer_msgs, student_msgs = sent_to(start, empty_payer), sent_to(start, empty_student)
        check("C6 a sponsor with 3 coins: the check is on the student's coins", q.allowed and q.payer_id is None)
        check("C6 ...the sponsor told in their language what to do (top up in the account)", len(payer_msgs) == 1
          and "закончились" in payer_msgs[0] and "Ielts A" in payer_msgs[0] and "Пополнить" in payer_msgs[0],
          payer_msgs)
        check("C6 ...the student told why they pay", len(student_msgs) == 1
              and "homiysining coinlari tugadi" in student_msgs[0], student_msgs)
        check("C6 ...the admins' 👨‍🏫 topic hears of it", any(tp == "teachers" and "tugadi" in tx for tp, tx in posts),
              posts)
        start = len(w.session.calls)
        await check_quota(empty_student, "writing_task2")
        await check_quota(empty_student, "speaking_part1")
        await sponsor_alerts.settle()
        check("C6 ...again the same day: nobody told twice", not sent_to(start, empty_payer)
              and not sent_to(start, empty_student) and len(posts) == 1, posts)
        ttl = await redis().ttl(sponsor_alerts.FLAG_KEY.format(empty_payer, "empty"))
        check("C6 ...the flag lasts until the next Tashkent midnight", 0 < ttl <= 24 * 3600, ttl)

        low_payer, low_student = BASE + 103, BASE + 104
        await make_user(low_payer, 52)
        await make_user(low_student, 20)
        await group_with(low_payer, [low_student])
        start = len(w.session.calls)
        q = await check_quota(low_student, "writing_task2")
        await check_quota(low_student, "writing_task2")
        await sponsor_alerts.settle()
        msgs = sent_to(start, low_payer)
        check("C6 a sponsor left under 50 coins by a check: warned once, the check still theirs",
              q.payer_id == low_payer and len(msgs) == 1 and "kam qoldi: 47 coin" in msgs[0]
              and not sent_to(start, low_student), msgs)

        limit_payer, limit_student = BASE + 105, BASE + 106
        await make_user(limit_payer, 500)
        await make_user(limit_student, 20)
        await group_with(limit_payer, [limit_student], daily_limit=0)
        start = len(w.session.calls)
        q = await check_quota(limit_student, "writing_task2")
        await sponsor_alerts.settle()
        check("C6 the group's daily limit used up: the student told (no message to the sponsor)",
              q.payer_id is None and not sent_to(start, limit_payer)
              and any("limitingiz tugadi" in x for x in sent_to(start, limit_student)),
              (sent_to(start, limit_payer), sent_to(start, limit_student)))

        # never raises: Telegram refusing the message
        from aiogram.exceptions import TelegramForbiddenError
        quiet_payer, quiet_student = BASE + 107, BASE + 108
        await make_user(quiet_payer, 1)
        await make_user(quiet_student, 20)
        await group_with(quiet_payer, [quiet_student])
        w.refuse = lambda m: TelegramForbiddenError(method=m, message="Forbidden: bot was blocked by the user")
        try:
            q = await check_quota(quiet_student, "writing_task2")
            await sponsor_alerts.settle()
        finally:
            w.refuse = None
        check("C6 a sponsor who blocked the bot: the check goes on, nothing raised", q.allowed)
    finally:
        crm.post = real_post


async def main() -> int:
    from bot.utils.crm import redis

    await redis().flushdb()
    fake_writing()
    fake_speaking()
    w = World()
    await b2_delivery(w)
    await b3_downloads(w)
    await b4_before_grading(w)
    await b5_shutdown(w)
    b6_prefilter()
    await b7_azure()
    await b8_price(w)
    await c3_parked(w)
    await c6_sponsor_alerts(w)
    await w.bot.session.close()
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
