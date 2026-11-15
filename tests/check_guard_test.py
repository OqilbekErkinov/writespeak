"""A running check's coins and turn (bot/utils/check_guard.py, the 2026-11-15 audit) - offline: a THROWAWAY
Postgres and Redis (tests run by test_part2.sh), Telegram and the grading are fakes.

Part 1 - the hold itself: coins taken atomically when grading starts, given back unless the report is saved,
one running check per student (admins excepted), a sponsor that ran out falls back to the student's coins,
a restart's recovery, the sweep, the queue.
Part 2 - the real Writing handlers: a short typed answer is asked about first (more text is added to it), a
double tap grades once, a second check while one is graded is refused before anything is typed, a failed
check gives its coins back and "🔁 Try again" grades the same answer, a check whose student moved on leaves
their new state alone.
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

fails: list[str] = []


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


STUDENT, OTHER, SPONSOR = 7_100_001, 7_100_002, 7_100_003
_DP = []


def dispatcher():
    """The bot's dispatcher - built once: its routers can be attached to one dispatcher only."""
    if not _DP:
        from bot.main import build_dispatcher

        _DP.append(build_dispatcher())
    return _DP[0]


async def setup_users() -> None:
    from db.database import get_session
    from db.models import User, UserStatus

    async with get_session() as s:
        for uid, coins in ((STUDENT, 20), (OTHER, 3), (SPONSOR, 4)):
            u = await s.get(User, uid)
            if u is None:
                s.add(User(id=uid, full_name=f"U{uid}", coin_balance=coins, language="uz",
                           status=UserStatus.approved))
            else:
                u.coin_balance = coins
        await s.commit()


async def balance(uid: int) -> int:
    from sqlalchemy import select

    from db.database import get_session
    from db.models import User

    async with get_session() as s:
        return (await s.execute(select(User.coin_balance).where(User.id == uid))).scalar()


async def hold_state(hold_id: str) -> str | None:
    from db.database import get_session
    from db.models import CheckHold

    async with get_session() as s:
        row = await s.get(CheckHold, hold_id)
        return row.state if row else None


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


async def part1_hold() -> None:
    from bot.config import settings
    from bot.states.writing_states import WritingStates
    from bot.utils import check_guard as guard
    from db import crud
    from db.database import get_session
    from services.ai.schemas import VocabularyItem

    await setup_users()
    paid = {"spend_credit": True}

    # coins taken at the start, one running check per student
    st = State(paid)
    card = Card(STUDENT)
    h = await guard.begin(card, st, st.data, module="w", kind="writing_task2", lang="uz",
                          failed_state=WritingStates.failed)
    check("begin: the hold written, its 5 coins taken at once", h is not None and h.coins == 5
          and await balance(STUDENT) == 15 and await hold_state(h.id) == "held", await balance(STUDENT))
    check("begin: the hold's id kept in the FSM data", st.data.get("check_token") == h.id)
    check("busy: the student has a check being graded", await guard.busy(STUDENT))
    st2, card2 = State(paid), Card(STUDENT, 2)
    h2 = await guard.begin(card2, st2, st2.data, module="s", kind="speaking_part1", lang="uz",
                           failed_state=WritingStates.failed)
    check("a second check while one is graded: refused, nothing taken", h2 is None and await balance(STUDENT) == 15
          and "baholanmoqda" in card2.edits[-1][0], card2.edits)
    check("the refusal offers 🔁 Try again (the answer kept, state failed)",
          st2.state == WritingStates.failed.state and guard.CB_RETRY in str(card2.edits[-1][1]))

    # given back unless saved; idempotent
    check("end: not saved - the coins go back", await guard.end(h, "boom") and await balance(STUDENT) == 20
          and await hold_state(h.id) == "refunded")
    check("end twice: nothing more", not await guard.end(h, "again") and await balance(STUDENT) == 20)
    check("busy no more after it ended", not await guard.busy(STUDENT))

    # saved: charged once, in the save's transaction
    st = State(paid)
    h = await guard.begin(Card(STUDENT), st, st.data, module="w", kind="writing_task2", lang="uz",
                          failed_state=WritingStates.failed)
    async with get_session() as s:
        sub = await crud.save_writing_submission(
            s, user_id=STUDENT, task_type="task2", prompt_text="P", prompt_source="text", answer_text="A",
            answer_source="text", original_file_paths={}, overall_band=6.0, criteria_scores={}, annotations=[],
            vocabulary=[VocabularyItem(word_or_phrase="w", meaning="m", example_sentence="e")],
            book_sample_ref=None, feedback_pdf_path="/tmp/x.pdf", coins=5, hold_id=h.id)
    check("saved with its hold: charged, the coins not taken again", await hold_state(h.id) == "charged"
          and sub.coins_spent == 5 and sub.paid_by_id is None and await balance(STUDENT) == 15, sub.coins_spent)
    check("end after the save: no refund", not await guard.end(h) and await balance(STUDENT) == 15)

    # not enough coins: refused, nothing written
    st, card = State(paid), Card(OTHER)
    h = await guard.begin(card, st, st.data, module="w", kind="writing_task2", lang="uz",
                          failed_state=WritingStates.failed)
    check("not enough coins: the paywall on the card, nothing taken", h is None and await balance(OTHER) == 3
          and "Coin yetarli emas" in card.edits[-1][0] and not await guard.busy(OTHER))

    # a sponsor that ran out: the student's own coins
    st = State({"spend_credit": True, "payer_id": SPONSOR})
    h = await guard.begin(Card(STUDENT), st, st.data, module="w", kind="writing_task2", lang="uz",
                          failed_state=WritingStates.failed)
    check("sponsor short of coins: the student pays, the sponsor untouched", h is not None and h.payer_id == STUDENT
          and h.sponsor_id is None and await balance(SPONSOR) == 4 and await balance(STUDENT) == 10
          and st.data.get("payer_id") is None)
    await guard.end(h, "test")
    st = State({"spend_credit": True, "payer_id": SPONSOR})
    h = await guard.begin(Card(STUDENT), st, st.data, module="w", kind="writing_task1", lang="uz",
                          failed_state=WritingStates.failed)
    check("sponsor with enough: the sponsor pays", h is not None and h.sponsor_id == SPONSOR
          and await balance(SPONSOR) == 0 and await balance(STUDENT) == 15)
    await guard.end(h, "test")
    check("the sponsor's coins back too", await balance(SPONSOR) == 4)

    # admins may run several at once
    admin = next(iter(settings.admin_ids))
    from db.models import User
    async with get_session() as s:
        if await s.get(User, admin) is None:
            s.add(User(id=admin, full_name="Admin", coin_balance=100, language="uz"))
            await s.commit()
    a1 = await guard.begin(Card(admin), State(), {}, module="w", kind="writing_task2", lang="uz",
                           failed_state=WritingStates.failed)
    a2 = await guard.begin(Card(admin, 2), State(), {}, module="w", kind="writing_task2", lang="uz",
                           failed_state=WritingStates.failed)
    check("an admin: two checks at once (free)", a1 is not None and a2 is not None and a1.coins == 0)
    await guard.end(a1)
    await guard.end(a2)

    # a test run: nothing written, nothing charged
    t = await guard.begin(Card(STUDENT), State(), {"test_run": True, "spend_credit": True}, module="w",
                          kind="writing_task2", lang="uz", failed_state=WritingStates.failed)
    check("a test run: no hold row, no coins", t is not None and not t.persisted and await hold_state(t.id) is None
          and await balance(STUDENT) == 15)
    await guard.end(t)

    # the save without a hold (or a hold settled meanwhile) never goes below 0 and never records unpaid as paid
    async with get_session() as s:
        sub = await crud.save_writing_submission(
            s, user_id=OTHER, task_type="task2", prompt_text="P", prompt_source="text", answer_text="A",
            answer_source="text", original_file_paths={}, overall_band=6.0, criteria_scores={}, annotations=[],
            vocabulary=[], book_sample_ref=None, feedback_pdf_path="/tmp/x.pdf", coins=5)
    check("no coins and no hold: saved as unpaid, balance not below 0", sub.coins_spent == 0
          and await balance(OTHER) == 3, (sub.coins_spent, await balance(OTHER)))

    # the queue: at most N graded at once (bot/utils/capacity.py's gate, its cap set to 2 here)
    from bot.utils.capacity import governor
    import time as _time
    governor.cap, governor.cap_read_at = 2, _time.monotonic() + 3600
    order, gate = [], asyncio.Event()
    holds = [guard.Hold(f"q{i}", STUDENT, "w", "k") for i in range(3)]
    cards = [NS(queued=None) for _ in range(3)]

    async def job(i):
        async with guard.slot(holds[i], cards[i]):
            order.append(i)
            await gate.wait()

    tasks = [asyncio.create_task(job(i)) for i in range(3)]
    await asyncio.sleep(0.05)
    place = cards[2].queued() if callable(cards[2].queued) else 0
    check("slot: two graded, the third waits - its place shown on the card", order == [0, 1] and place == 1,
          (order, place))
    gate.set()
    await asyncio.gather(*tasks)
    check("slot: the third runs when one ends, its card leaves the queue", order == [0, 1, 2]
          and cards[2].queued is None and governor.running == 0)
    governor.cap, governor.cap_read_at = 0, -1e9  # read from settings/Redis again


async def part1_recovery() -> None:
    """A stopped bot's checks: coins back, the card says so, "try again" while the FSM still holds the input."""
    from aiogram import Bot
    from aiogram.client.session.base import BaseSession
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.redis import RedisStorage
    from aiogram.types import Chat, Message

    from bot.config import settings
    from bot.states.writing_states import WritingStates
    from bot.utils import check_guard as guard
    from db.database import get_session
    from db.models import CheckHold

    await setup_users()
    now = datetime.now(timezone.utc)

    class Session(BaseSession):
        def __init__(self):
            super().__init__()
            self.calls = []

        async def close(self):
            pass

        async def stream_content(self, *a, **k):
            yield b""

        async def make_request(self, bot, method, timeout=None):
            self.calls.append(method)
            return Message(message_id=getattr(method, "message_id", None) or 99, date=now,
                           chat=Chat(id=getattr(method, "chat_id", STUDENT), type="private"), text="x").as_(bot)

    session = Session()
    bot = Bot("8888548766:TEST", session=session)
    storage = RedisStorage.from_url(settings.redis_url)
    ctx_key = StorageKey(bot_id=bot.id, chat_id=STUDENT, user_id=STUDENT)
    await storage.set_state(ctx_key, WritingStates.processing.state)
    await storage.set_data(ctx_key, {"check_token": "r" * 32, "answer_text": "kept", "task_type": "task2"})
    async with get_session() as s:
        s.add(CheckHold(id="r" * 32, user_id=STUDENT, chat_id=STUDENT, message_id=555, module="w",
                        kind="writing_task2", lang="uz", payer_id=STUDENT, coins=5, exclusive=True, state="held"))
        s.add(CheckHold(id="s" * 32, user_id=OTHER, chat_id=OTHER, message_id=556, module="s",
                        kind="speaking_part1", lang="ru", payer_id=OTHER, coins=6, exclusive=True, state="held"))
        await s.commit()
    before = await balance(STUDENT), await balance(OTHER)
    n = await guard.recover(bot, storage)
    edits = [c for c in session.calls if type(c).__name__ == "EditMessageText"]
    mine = next((c for c in edits if c.chat_id == STUDENT), None)
    theirs = next((c for c in edits if c.chat_id == OTHER), None)
    check("recover: both held checks settled as interrupted, coins back", n == 2
          and await hold_state("r" * 32) == "interrupted" and await hold_state("s" * 32) == "interrupted"
          and await balance(STUDENT) == before[0] + 5 and await balance(OTHER) == before[1] + 6)
    check("recover: the student whose answer is kept gets 🔁 Try again on the check's own card",
          mine is not None and mine.message_id == 555 and guard.CB_RETRY in str(mine.reply_markup)
          and "qaytarildi" in mine.text and await storage.get_state(ctx_key) == WritingStates.failed.state)
    check("recover: no answer kept - asked to send it again, in their language", theirs is not None
          and theirs.reply_markup is None and "заново" in theirs.text)
    check("recover again: nothing left to do", await guard.recover(bot, storage) == 0)

    # the sweep: a hold nobody runs, older than STALE_AFTER
    async with get_session() as s:
        s.add(CheckHold(id="t" * 32, user_id=STUDENT, chat_id=STUDENT, module="w", kind="writing_task2",
                        lang="uz", payer_id=STUDENT, coins=5, exclusive=True, state="held",
                        created_at=now - timedelta(minutes=10)))
        s.add(CheckHold(id="u" * 32, user_id=SPONSOR, chat_id=SPONSOR, module="w", kind="writing_task2",
                        lang="uz", payer_id=SPONSOR, coins=4, exclusive=True, state="held"))
        await s.commit()
    b = await balance(STUDENT)
    guard.running["u" * 32] = None  # a fresh one: never swept
    swept = await guard.sweep(bot, storage)
    check("sweep: the stale hold settled (expired, coins back), a fresh one left alone", swept == 1
          and await hold_state("t" * 32) == "expired" and await balance(STUDENT) == b + 5
          and await hold_state("u" * 32) == "held")
    guard.running.pop("u" * 32, None)
    async with get_session() as s:
        (await s.get(CheckHold, "u" * 32)).state = "refunded"
        await s.commit()
    await bot.session.close()


async def part2_writing() -> None:
    from aiogram import Bot
    from aiogram.client.session.base import BaseSession
    from aiogram.types import CallbackQuery, Chat, Message, Update, User
    from sqlalchemy import select

    from bot.handlers import writing
    from bot.keyboards.main_menu_kb import BTN_WRITING
    from bot.keyboards.writing_task_kb import CB_LENGTH_OK, CB_TASK2
    from bot.states.speaking_states import SpeakingStates
    from bot.states.writing_states import WritingStates
    from bot.utils import check_guard as guard
    from bot.utils import progress
    from db.database import get_session
    from db.models import CheckHold, WritingSubmission

    await setup_users()
    progress.ENABLED = False  # the live card's edits would only add noise here
    now = datetime.now(timezone.utc)
    uid = STUDENT

    class TgSession(BaseSession):
        def __init__(self):
            super().__init__()
            self.calls, self.n = [], 5000

        async def close(self):
            pass

        async def stream_content(self, *a, **k):
            yield b""

        async def make_request(self, bot, method, timeout=None):
            self.calls.append(method)
            self.n += 1
            name = type(method).__name__
            if name in ("AnswerCallbackQuery", "DeleteMessage", "EditMessageReplyMarkup", "SendChatAction"):
                return True
            return Message(message_id=getattr(method, "message_id", None) or self.n, date=now,
                           chat=Chat(id=getattr(method, "chat_id", None) or uid, type="private"),
                           text=getattr(method, "text", None) or "x").as_(bot)

    # the grading: fakes that can be held at a gate, or made to fail
    graded: list[str] = []
    control = {"gate": None, "fail": 0}

    async def fake_grade(**kw):
        graded.append(kw["answer_text"])
        if control["gate"] is not None:
            await control["gate"].wait()
        if control["fail"]:
            control["fail"] -= 1
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

    session = TgSession()
    bot = Bot("8888548766:TEST", session=session)
    dp = dispatcher()
    state = dp.fsm.get_context(bot=bot, chat_id=uid, user_id=uid)
    counter = iter(range(20_000, 200_000))
    sender = User(id=uid, is_bot=False, first_name="S")

    async def send(text: str):
        start = len(session.calls)
        message = Message(message_id=next(counter), date=now, chat=Chat(id=uid, type="private"), from_user=sender,
                          text=text)
        await dp.feed_update(bot, Update(update_id=next(counter), message=message))
        return session.calls[start:]

    async def press(data: str):
        start = len(session.calls)
        query = CallbackQuery(id=str(next(counter)), from_user=sender, chat_instance="ci", data=data,
                              message=Message(message_id=next(counter), date=now, chat=Chat(id=uid, type="private"),
                                              text="x"))
        await dp.feed_update(bot, Update(update_id=next(counter), callback_query=query))
        return session.calls[start:]

    def texts(calls) -> list[str]:
        return [c.text for c in calls if type(c).__name__ in ("SendMessage", "EditMessageText")]

    def retry_data(calls) -> str | None:
        for c in calls:
            for row in getattr(getattr(c, "reply_markup", None), "inline_keyboard", None) or []:
                for b in row:
                    if (b.callback_data or "").startswith(guard.CB_RETRY):
                        return b.callback_data
        return None

    def docs(calls):
        return [c for c in calls if type(c).__name__ == "SendDocument"]

    async def submissions() -> int:
        async with get_session() as s:
            return len((await s.execute(select(WritingSubmission.id).where(WritingSubmission.user_id == uid))).all())

    prompt = ("Some people believe that university education should be free for everyone. Others think students "
              "should pay for it. Discuss both views and give your own opinion.")
    sentence = "Universities give young people the knowledge and skills they need for a successful career. "
    long_answer = sentence * 12  # ~170 words: no length question for Task 2
    short_answer = "Universities are important for everyone in the modern world today."  # ~10 words

    async def start_task2():
        await send(BTN_WRITING)
        await press(CB_TASK2)
        await send(prompt)

    # 1. a short typed answer: asked first, nothing charged; more text added; then graded once
    coins0, subs0 = await balance(uid), await submissions()
    await start_task2()
    out = await send(short_answer)
    check("short answer: asked before anything is charged", await state.get_state()
          == WritingStates.confirming_length.state and any("ta so'z bor" in x for x in texts(out))
          and not graded and await balance(uid) == coins0, texts(out))
    out = await send(long_answer)
    data = await state.get_data()
    check("more text: added to the answer, its new length said", data["answer_text"].startswith(short_answer)
          and long_answer.strip()[:30] in data["answer_text"] and any("Qo'shildi" in x for x in texts(out)))
    # a double tap on "✅ Check it as it is": one grading, one charge
    await asyncio.gather(press(CB_LENGTH_OK), press(CB_LENGTH_OK))
    check("double tap: graded once, charged once (5 coins), one report", len(graded) == 1
          and await balance(uid) == coins0 - 5 and await submissions() == subs0 + 1,
          (len(graded), await balance(uid)))
    check("after the report: the flow cleared", await state.get_state() is None)

    # 2. a second check while one is graded: refused at once, before typing anything
    graded.clear()
    control["gate"] = asyncio.Event()
    await start_task2()
    running = asyncio.create_task(send(long_answer + " First."))
    for _ in range(100):
        await asyncio.sleep(0.02)
        if graded:
            break
    await state.set_state(None)  # the student taps the menu meanwhile
    out = await send(BTN_WRITING)
    out = await press(CB_TASK2)
    check("while a check is graded: a new one is refused at once", any("baholanmoqda" in x for x in texts(out))
          and await state.get_state() is None, texts(out))
    # the student moved on to something else: the finished check leaves their state alone
    await state.set_state(SpeakingStates.awaiting_question)
    await state.update_data(part="part1")
    control["gate"].set()
    await running
    control["gate"] = None
    check("the check finished after they moved on: their new state untouched",
          await state.get_state() == SpeakingStates.awaiting_question.state and len(docs(session.calls[-30:])) >= 1)
    await state.clear()

    # 3. a failed check: coins back, "🔁 Try again" grades the same answer
    graded.clear()
    control["fail"] = 1
    coins1 = await balance(uid)
    await start_task2()
    out = await send(long_answer + " Second.")
    token = retry_data(out)
    check("failed: coins back, the card offers 🔁 Try again, state failed", token is not None
          and await balance(uid) == coins1 and await state.get_state() == WritingStates.failed.state
          and any("yakunlanmadi" in x and "qaytarildi" in x for x in texts(out)), texts(out))
    async with get_session() as s:
        failed_hold = (await s.execute(select(CheckHold).where(CheckHold.user_id == uid)
                                       .order_by(CheckHold.created_at.desc()))).scalars().first()
    check("failed: its hold says why", failed_hold.state == "refunded" and "model call failed" in failed_hold.reason)
    out = await press(token)
    check("try again: the same answer graded, charged once, report sent", len(graded) == 2
          and graded[0] == graded[1] and await balance(uid) == coins1 - 5 and docs(out), (len(graded), await balance(uid)))
    out = await press(token)
    check("an old 🔁 button: says the check is gone, does nothing", len(graded) == 2
          and any(getattr(c, "show_alert", False) for c in out if type(c).__name__ == "AnswerCallbackQuery"))
    check("no hold left running", not await guard.busy(uid))
    await bot.session.close()


def _wav(seconds: float = 3.0) -> bytes:
    import io
    import math
    import os
    import wave

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


async def part3_speaking() -> None:
    """Speaking answered question by question: a failed grading gives the coins back and keeps the recordings;
    "🔁 Try again" grades them without asking for them again."""
    from aiogram import Bot
    from aiogram.client.session.base import BaseSession
    from aiogram.types import CallbackQuery, Chat, Document, File, Message, Update, User

    from bot.handlers import speaking
    from bot.keyboards.main_menu_kb import BTN_SPEAKING
    from bot.keyboards.speaking_part_kb import CB_MODE_EACH, CB_PART1
    from bot.states.speaking_states import SpeakingStates
    from bot.utils import check_guard as guard
    from bot.utils import progress

    await setup_users()
    progress.ENABLED = False
    now = datetime.now(timezone.utc)
    uid = STUDENT
    files: dict[str, bytes] = {}

    class TgSession(BaseSession):
        def __init__(self):
            super().__init__()
            self.calls, self.n = [], 9000

        async def close(self):
            pass

        async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
            yield files[url.rsplit("/", 1)[-1]]

        async def make_request(self, bot, method, timeout=None):
            self.calls.append(method)
            self.n += 1
            name = type(method).__name__
            if name == "GetFile":
                return File(file_id=method.file_id, file_unique_id="u" + method.file_id,
                            file_size=len(files[method.file_id]), file_path=f"documents/{method.file_id}")
            if name in ("AnswerCallbackQuery", "DeleteMessage", "EditMessageReplyMarkup", "SendChatAction"):
                return True
            return Message(message_id=getattr(method, "message_id", None) or self.n, date=now,
                           chat=Chat(id=getattr(method, "chat_id", None) or uid, type="private"),
                           text=getattr(method, "text", None) or "x").as_(bot)

    graded, control = [], {"fail": 1}

    async def fake_run_check(part, questions, clips, seed=0, transcribed=None):
        graded.append(len(clips))
        if control["fail"]:
            control["fail"] -= 1
            raise RuntimeError("speaking model call failed")
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

    session = TgSession()
    bot = Bot("8888548766:TEST", session=session)
    dp = dispatcher()
    state = dp.fsm.get_context(bot=bot, chat_id=uid, user_id=uid)
    counter = iter(range(300_000, 400_000))
    sender = User(id=uid, is_bot=False, first_name="S")

    async def send(**kw):
        start = len(session.calls)
        message = Message(message_id=next(counter), date=now, chat=Chat(id=uid, type="private"), from_user=sender, **kw)
        await dp.feed_update(bot, Update(update_id=next(counter), message=message))
        return session.calls[start:]

    async def press(data: str):
        start = len(session.calls)
        query = CallbackQuery(id=str(next(counter)), from_user=sender, chat_instance="ci", data=data,
                              message=Message(message_id=next(counter), date=now, chat=Chat(id=uid, type="private"),
                                              text="x"))
        await dp.feed_update(bot, Update(update_id=next(counter), callback_query=query))
        return session.calls[start:]

    def retry_data(calls):
        for c in calls:
            for row in getattr(getattr(c, "reply_markup", None), "inline_keyboard", None) or []:
                for b in row:
                    if (b.callback_data or "").startswith(guard.CB_RETRY):
                        return b.callback_data
        return None

    coins = await balance(uid)
    await send(text=BTN_SPEAKING)
    await press(CB_PART1)
    await send(text="Do you like reading books?")
    await press(CB_MODE_EACH)
    fid = f"ans{next(counter)}"
    files[fid] = _wav()
    out = await send(document=Document(file_id=fid, file_unique_id="u" + fid, file_name="a.wav",
                                       mime_type="audio/wav", file_size=len(files[fid])))
    token = retry_data(out)
    data = await state.get_data()
    check("speaking failed: coins back, recordings kept, 🔁 Try again", token is not None and graded == [1]
          and await balance(uid) == coins and await state.get_state() == SpeakingStates.failed.state
          and len(data.get("answers") or []) == 1, (graded, await balance(uid), await state.get_state()))
    out = await press(token)
    check("speaking try again: the kept recording graded, 6 coins charged once, report sent", graded == [1, 1]
          and await balance(uid) == coins - 6 and any(type(c).__name__ == "SendDocument" for c in out)
          and await state.get_state() is None, (graded, await balance(uid)))
    check("speaking: no hold left running", not await guard.busy(uid))
    await bot.session.close()


async def main() -> int:
    from bot.utils.crm import redis

    await redis().flushdb()
    await part1_hold()
    await part1_recovery()
    await part2_writing()
    await part3_speaking()
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
