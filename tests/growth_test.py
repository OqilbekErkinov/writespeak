"""Growth and conversion before the launch (2026-11-16) - offline: a THROWAWAY Postgres and Redis (the agent
runner), Telegram and OpenAI are fakes; the grading itself is untouched (mocked as in pipeline_hardening_test).

E1 the re-engagement reminders (bot/utils/nudges.py): who gets each step and who doesn't, once only, quiet hours,
   a test window or a pause, opt-out and back in, a blocked bot, a student using the bot right now, the spacing.
E2 the smart entry (bot/handlers/fallback.py): an essay, a photo, a voice sent outside any flow - the kept essay
   graded as the answer, the kept Part 2 talk graded - and every other state left alone.
E3 the next step: the coins left under a report (enough / short), the pack labels, the paywall's carry-over,
   the payment approval's buttons.
E4 "📝 Tayyor savol tanlash" on the "send the question" steps and the last onboarding message.
E5 the referral link in the share card's caption, the invite button in Hisobim.
"""
import asyncio
import io
import math
import os
import sys
import time
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

fails: list[str] = []


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


BASE = 7_400_000
_DP = []


def dispatcher():
    if not _DP:
        from bot.main import build_dispatcher

        _DP.append(build_dispatcher())
    return _DP[0]


async def make_user(uid: int, coins: int, lang: str = "uz", **kw) -> None:
    from db.database import get_session
    from db.models import User, UserStatus

    async with get_session() as s:
        u = await s.get(User, uid)
        if u is None:
            s.add(User(id=uid, full_name=f"U{uid}", coin_balance=coins, language=lang,
                       status=kw.pop("status", UserStatus.approved), **kw))
        else:
            u.coin_balance = coins
            for k, v in kw.items():
                setattr(u, k, v)
        await s.commit()


async def balance(uid: int) -> int:
    from db.database import get_session
    from db.models import User

    async with get_session() as s:
        return (await s.get(User, uid)).coin_balance


async def add_check(uid: int, at: datetime, scores: dict | None = None) -> int:
    from db.database import get_session
    from db.models import SourceType, TaskType, WritingSubmission

    async with get_session() as s:
        row = WritingSubmission(user_id=uid, task_type=TaskType.task2, prompt_text="p", prompt_source=SourceType.text,
                                answer_text="a", answer_source=SourceType.text, overall_band=6.0,
                                criteria_scores=scores or {"TA": 6.0, "CC": 6.0, "LR": 6.0, "GRA": 6.0},
                                created_at=at)
        s.add(row)
        await s.commit()
        return row.id


async def group_with(payer: int, students: list[int]) -> int:
    from db.database import get_session
    from db.models import Group, GroupMembership

    async with get_session() as s:
        g = Group(name=f"G{payer}", teacher_id=payer, sponsor_checks=True)
        s.add(g)
        await s.flush()
        for uid in students:
            s.add(GroupMembership(group_id=g.id, user_id=uid))
        await s.commit()
        return g.id


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


# --- a fake Telegram, the real handlers (as tests/pipeline_hardening_test.py) --------------------------------


class World:
    def __init__(self):
        from aiogram import Bot
        from aiogram.client.session.base import BaseSession
        from aiogram.types import Chat, File, Message

        world = self
        self.files: dict[str, bytes] = {}
        self.refuse = None
        now = datetime.now(timezone.utc)

        class Session(BaseSession):
            def __init__(self):
                super().__init__()
                self.calls, self.n = [], 60_000

            async def close(self):
                pass

            async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
                yield world.files[url.rsplit("/", 1)[-1]]

            async def make_request(self, bot, method, timeout=None):
                self.calls.append(method)
                self.n += 1
                err = world.refuse(method) if world.refuse else None
                if err is not None:
                    raise err
                name = type(method).__name__
                if name == "GetFile":
                    return File(file_id=method.file_id, file_unique_id="u" + method.file_id,
                                file_size=len(world.files.get(method.file_id, b"")),
                                file_path=f"documents/{method.file_id}")
                if name in ("AnswerCallbackQuery", "DeleteMessage", "EditMessageReplyMarkup", "SendChatAction",
                            "SetMessageReaction"):
                    return True
                return Message(message_id=getattr(method, "message_id", None) or self.n, date=now,
                               chat=Chat(id=getattr(method, "chat_id", None) or 1, type="private"),
                               text=getattr(method, "text", None) or "x").as_(bot)

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


def texts(calls) -> list[str]:
    return [c.text for c in calls if type(c).__name__ in ("SendMessage", "EditMessageText")]


def buttons(calls) -> list[str]:
    calls = calls if isinstance(calls, list) else [calls]
    out = []
    for c in calls:
        markup = c if hasattr(c, "inline_keyboard") else getattr(c, "reply_markup", None)
        for row in getattr(markup, "inline_keyboard", None) or []:
            out += [b.callback_data for b in row if b.callback_data]
    return out


def sends(calls) -> list:
    return [c for c in calls if type(c).__name__ == "SendMessage"]


def docs(calls):
    return [c for c in calls if type(c).__name__ == "SendDocument"]


PROMPT = ("Some people believe that university education should be free for everyone. Others think students should "
          "pay for it. Discuss both views and give your own opinion.")
SENTENCE = "Universities give young people the knowledge and skills they need for a successful career. "
CUE_CARD = ("Describe a book you enjoyed reading.\nYou should say: what it was, when you read it, what it was about, "
            "and explain why you enjoyed it.")


def essay(tag: str) -> str:
    return SENTENCE * 12 + f"In the end {tag} matters."


GRADED: list[str] = []
SPOKEN: list[int] = []


def fake_grading() -> None:
    from bot.handlers import language, speaking, writing
    from bot.utils import progress

    progress.ENABLED = False

    async def fake_grade(**kw):
        GRADED.append(kw["answer_text"])
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

    async def no_sample(callback, lang):  # the onboarding's sample PDF (WeasyPrint) is not what is tested here
        return None

    language._send_sample = no_sample


# --- E1 the re-engagement reminders --------------------------------------------------------------------------


async def e1_nudges(w: World) -> None:
    from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

    from bot.config import settings
    from bot.keyboards.account_kb import CB_NUDGES_OFF, CB_NUDGES_TOGGLE, CB_TOPUP_STUDENT
    from bot.keyboards.main_menu_kb import BTN_ACCOUNT_UZ
    from bot.keyboards.practice_kb import CB_MENU, CB_OPEN_PREFIX
    from bot.keyboards.speaking_part_kb import CB_PART1
    from bot.keyboards.writing_task_kb import CB_TASK2
    from bot.utils import activity, nudges, runtime
    from bot.utils import balance as credit
    from bot.utils.crm import redis
    from db.database import get_session
    from db.models import Center, UserRole, UserStatus

    settings.nudge_per_second = 1000.0
    r = redis()
    now = datetime.now(timezone.utc).replace(hour=7, minute=0, second=0, microsecond=0)  # 12:00 in Tashkent
    H, D = timedelta(hours=1), timedelta(days=1)
    names = ["free1", "free2", "poor", "young", "teacher", "owner", "denied", "checked", "late", "lapsed1",
             "lapsed2", "sponsored", "recent", "old", "optout", "blocks", "failing", "active", "gapped", "back",
             "still_blocked", "payer"]
    u = {name: BASE + i for i, name in enumerate(names, 1)}
    for name in ("free1", "poor", "teacher", "owner", "denied", "checked", "optout", "blocks", "failing", "active",
                 "gapped"):
        await make_user(u[name], 3 if name == "poor" else 8, created_at=now - 25 * H,
                        role=UserRole.teacher if name == "teacher" else UserRole.student,
                        status=UserStatus.denied if name == "denied" else UserStatus.approved)
    await make_user(111, 8, created_at=now - 25 * H)  # an admin (ADMIN_TELEGRAM_IDS=111)
    for name in ("free2", "back", "still_blocked"):
        await make_user(u[name], 8, created_at=now - 73 * H)
    await make_user(u["young"], 8, created_at=now - 10 * H)
    await make_user(u["late"], 8, created_at=now - 6 * D)
    async with get_session() as s:
        s.add(Center(name="C", owner_id=u["owner"]))
        await s.commit()
    await add_check(u["checked"], now - H)
    await make_user(u["lapsed1"], 20, created_at=now - 40 * D)
    await add_check(u["lapsed1"], now - 20 * D)
    await add_check(u["lapsed1"], now - 8 * D, {"TA": 6.0, "CC": 6.5, "LR": 5.0, "GRA": 6.0})
    for name in ("lapsed2", "sponsored", "recent", "old"):
        await make_user(u[name], 2, created_at=now - 40 * D)
    await add_check(u["lapsed2"], now - 15 * D)
    await add_check(u["sponsored"], now - 15 * D)
    await add_check(u["recent"], now - 3 * D)
    await add_check(u["old"], now - 30 * D)
    await make_user(u["payer"], 500, role=UserRole.teacher, created_at=now - 40 * D)
    await group_with(u["payer"], [u["sponsored"]])
    await nudges.set_reminders(u["optout"], False)
    await r.set(nudges.GAP_KEY.format(uid=u["gapped"]), 1, ex=3600)
    await r.hdel(activity.LAST_SEEN_KEY, *u.values(), 111)
    await r.hset(activity.LAST_SEEN_KEY, mapping={u["active"]: int((now - 5 * timedelta(minutes=1)).timestamp()),
                                                  u["back"]: int((now - 2 * H).timestamp()),
                                                  u["still_blocked"]: int((now - 2 * H).timestamp())})
    await r.hset(nudges.BLOCKED_KEY, mapping={u["back"]: (now - 3 * H).timestamp(),
                                              u["still_blocked"]: (now - H).timestamp()})

    def refuse(method):
        if type(method).__name__ != "SendMessage":
            return None
        if method.chat_id == u["blocks"]:
            return TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
        if method.chat_id == u["failing"]:
            return TelegramBadRequest(method=method, message="Bad Request: chat not found")
        return None

    w.refuse = refuse
    start = len(w.session.calls)
    counts = await nudges.send_round(w.bot, now=now)
    w.refuse = None
    out = sends(w.session.calls[start:])
    to = {c.chat_id: c for c in out}
    expected = {u[n] for n in ("free1", "free2", "lapsed1", "lapsed2", "sponsored", "back")}
    check("E1 the due ones written to: free 24 h, free 72 h, lapsed 7 d / 14 d, a blocked one who came back",
          set(to) - {u["blocks"], u["failing"]} == expected, sorted(set(to) - expected))
    check("E1 ...nobody else: too few coins, too new, a teacher, a center owner, an admin, a blocked account, "
          "already checked, past the window, a recent / long-ago check, opted out, active now, spaced, still blocked",
          not ({u[n] for n in ("poor", "young", "teacher", "owner", "denied", "checked", "late", "recent", "old",
                               "optout", "active", "gapped", "still_blocked", "payer")} | {111}) & set(to),
          sorted(set(to) & {u[n] for n in names}))
    check("E1 ...counted: 6 sent, 1 blocked, 1 failed", counts == {"sent": 6, "blocked": 1, "failed": 1}, counts)
    free1 = to.get(u["free1"])
    check("E1 free 24 h: 'your free check is waiting', the coins, Task 2 / Part 1 / ready question / 🔕",
          free1 is not None and "Bepul tekshiruvingiz sizni kutyapti" in free1.text and "8 coin" in free1.text
          and buttons(free1) == [CB_TASK2, CB_PART1, CB_MENU, CB_NUDGES_OFF], buttons(free1))
    check("E1 free 72 h: its own text", u["free2"] in to and "Sovg'a coinlaringiz hali joyida" in to[u["free2"]].text)
    lapsed1 = to.get(u["lapsed1"])
    check("E1 lapsed 7 d: days since, the weakest criterion over the last checks (LR (6.0+5.0)/2) with its tip, no top-up",
          lapsed1 is not None and "8 kundan beri" in lapsed1.text and "<b>Lexical Resource</b> (o'rtacha 5.5)"
          in lapsed1.text and "💡" in lapsed1.text and CB_TOPUP_STUDENT not in buttons(lapsed1),
          lapsed1.text if lapsed1 else None)
    lapsed2 = to.get(u["lapsed2"])
    check("E1 lapsed 14 d with 2 coins: the packs mentioned, '💳 Hisobni to'ldirish' first",
          lapsed2 is not None and "15 kun bo'ldi" in lapsed2.text and "Paketlar 10 coin" in lapsed2.text
          and buttons(lapsed2)[0] == CB_TOPUP_STUDENT, (lapsed2.text, buttons(lapsed2)) if lapsed2 else None)
    sponsored = to.get(u["sponsored"])
    check("E1 ...2 coins but a group pays: no top-up, no packs", sponsored is not None
          and CB_TOPUP_STUDENT not in buttons(sponsored) and "Paketlar" not in sponsored.text)
    check("E1 a blocked bot: remembered, a blocked one seen since: forgotten",
          await r.hexists(nudges.BLOCKED_KEY, u["blocks"]) and not await r.hexists(nudges.BLOCKED_KEY, u["back"])
          and await r.hexists(nudges.BLOCKED_KEY, u["still_blocked"]))
    check("E1 marked before sending (a failed one too); the active and the spaced ones not marked",
          await r.exists(nudges.FLAG_KEY.format(step="free1", uid=u["free1"]))
          and await r.exists(nudges.FLAG_KEY.format(step="free1", uid=u["failing"]))
          and not await r.exists(nudges.FLAG_KEY.format(step="free1", uid=u["active"]))
          and not await r.exists(nudges.FLAG_KEY.format(step="free1", uid=u["gapped"])))

    start = len(w.session.calls)
    counts = await nudges.send_round(w.bot, now=now)
    check("E1 a second round: nobody twice (the failed and the blocked ones not tried again)",
          not sends(w.session.calls[start:]) and counts == {"sent": 0, "blocked": 0, "failed": 0}, counts)

    await r.hset(activity.LAST_SEEN_KEY, u["active"], int((now - 2 * H).timestamp()))
    await r.delete(nudges.GAP_KEY.format(uid=u["gapped"]))
    start = len(w.session.calls)
    await nudges.send_round(w.bot, now=now)
    check("E1 ...the active one (no longer active) and the spaced one (gap over) get theirs later",
          {c.chat_id for c in sends(w.session.calls[start:])} == {u["active"], u["gapped"]})

    # two days on (the gap long over - its key is real time, so it goes by hand): free1's 72 h step
    later = now + 48 * H
    await r.delete(nudges.GAP_KEY.format(uid=u["free1"]))
    start = len(w.session.calls)
    await nudges.send_round(w.bot, now=later)
    got = {c.chat_id for c in sends(w.session.calls[start:])}
    check("E1 two days later: free1's 72 h step (its 24 h flag does not stop it)", u["free1"] in got, got)

    # quiet hours, a test window, a pause
    q = BASE + 60
    await make_user(q, 8, created_at=now - 26 * H)
    await r.hdel(activity.LAST_SEEN_KEY, q)
    start = len(w.session.calls)
    night = await nudges.send_round(w.bot, now=now.replace(hour=16))  # 21:00 in Tashkent
    early = await nudges.send_round(w.bot, now=now.replace(hour=4, minute=30))  # 09:30
    check("E1 quiet hours (21:00, 09:30 Tashkent): nothing sent, nothing marked",
          not sends(w.session.calls[start:]) and night == early == {"sent": 0, "blocked": 0, "failed": 0}
          and not await r.exists(nudges.FLAG_KEY.format(step="free1", uid=q)))
    await runtime.set_testers({999})
    windowed = await nudges.send_round(w.bot, now=now)
    await runtime.set_testers(set())
    await r.set(credit.EMPTY_KEY, "1")
    credit._paused.update(at=0.0)
    paused = await nudges.send_round(w.bot, now=now)
    await r.delete(credit.EMPTY_KEY)
    credit._paused.update(at=0.0)
    check("E1 a test window, a pause: nothing sent", windowed["sent"] == paused["sent"] == 0
          and not await r.exists(nudges.FLAG_KEY.format(step="free1", uid=q)), (windowed, paused))
    start = len(w.session.calls)
    reopened = await nudges.send_round(w.bot, now=now)
    check("E1 ...checks just opened again: no reminder for a gap (the launch / 'checks are back' messages' day)",
          not sends(w.session.calls[start:]) and reopened["sent"] == 0 and await r.exists(nudges.CLOSED_KEY))
    await r.set(nudges.CLOSED_KEY, int(time.time()) - settings.nudge_min_gap_hours * 3600 - 60)  # the gap gone by
    start = len(w.session.calls)
    await nudges.send_round(w.bot, now=now)
    check("E1 ...after the gap: sent", [c.chat_id for c in sends(w.session.calls[start:])] == [q])

    # off under a reminder, back on in Hisobim
    ux = BASE + 61
    await make_user(ux, 8, created_at=now - 27 * H)
    out = await w.press(ux, CB_NUDGES_OFF)
    check("E1 '🔕 Eslatmalarni o'chirish': off, told where to turn them back on",
          not await nudges.reminders_on(ux) and any("Eslatmalar o'chirildi" in x and "Hisobim" in x
                                                     for x in texts(out)), texts(out))
    await r.hdel(activity.LAST_SEEN_KEY, ux)
    start = len(w.session.calls)
    await nudges.send_round(w.bot, now=now)
    check("E1 ...switched off: no reminder", not [c for c in sends(w.session.calls[start:]) if c.chat_id == ux])
    out = await w.send(ux, text=BTN_ACCOUNT_UZ)
    hub = [c for c in sends(out) if CB_NUDGES_TOGGLE in buttons(c)]
    labels = [b.text for row in hub[0].reply_markup.inline_keyboard for b in row] if hub else []
    check("E1 Hisobim shows '🔔 Eslatmalarni yoqish'", "🔔 Eslatmalarni yoqish" in labels, labels)
    out = await w.press(ux, CB_NUDGES_TOGGLE)
    redrawn = [c for c in out if type(c).__name__ == "EditMessageReplyMarkup"]
    check("E1 ...pressed: on again, the switch now says '🔕'", await nudges.reminders_on(ux) and redrawn
          and "🔕 Eslatmalarni o'chirish" in [b.text for row in redrawn[0].reply_markup.inline_keyboard for b in row])
    await r.hdel(activity.LAST_SEEN_KEY, ux)
    start = len(w.session.calls)
    await nudges.send_round(w.bot, now=now)
    check("E1 ...and the reminder goes", [c.chat_id for c in sends(w.session.calls[start:])] == [ux])

    # the reminder's buttons start a check right away
    await make_user(u["free1"], 8)
    out = await w.press(u["free1"], CB_TASK2)
    step = [c for c in out if type(c).__name__ == "EditMessageText"]
    check("E1 its 'Writing Task 2': the 'send the question' step", step and "send the question" in step[0].text
          and f"{CB_OPEN_PREFIX}writing_task2" in buttons(step), [x[:40] for x in texts(out)])


# --- E2 the smart entry ----------------------------------------------------------------------------------------


async def e2_smart(w: World) -> None:
    from aiogram.types import PhotoSize, Voice

    from bot.handlers import fallback
    from bot.keyboards.entry_kb import CB_SMART_PREFIX
    from bot.keyboards.main_menu_kb import BTN_WRITING
    from bot.keyboards.writing_task_kb import CB_NO_CHART, CB_TASK2
    from bot.states.speaking_states import SpeakingStates
    from bot.states.writing_states import WritingStates
    from bot.utils.crm import redis
    from services.ai.essay_marking import count_words

    uid = BASE + 100
    await make_user(uid, 50)
    st = w.state(uid)
    await st.clear()

    text = essay("smart one")
    out = await w.send(uid, text=text)
    offer = [c for c in sends(out) if "Writing javobiga o'xshaydi" in c.text]
    smart = [b for b in buttons(offer) if b.startswith(CB_SMART_PREFIX)]
    token = smart[0].rsplit(":", 1)[-1] if smart else ""
    check("E2 an essay sent outside any flow: 'Task 1 or Task 2?' with its word count",
          offer and smart == [f"{CB_SMART_PREFIX}w:task1:{token}", f"{CB_SMART_PREFIX}w:task2:{token}"]
          and f"<b>{count_words(text)}</b> so'z" in offer[0].text, texts(out))
    out = await w.press(uid, f"{CB_SMART_PREFIX}w:task2:{token}")
    data = await st.get_data()
    check("E2 ...Task 2: the essay kept, the question asked (no Practice button: it has its own question)",
          any("Javobingiz saqlandi" in x and "send the question" in x for x in texts(out))
          and await st.get_state() == WritingStates.awaiting_prompt.state and data.get("kept_answer") == text
          and data.get("task_type") == "task2" and not any(b.startswith("practice:open:") for b in buttons(out)),
          texts(out))
    check("E2 ...the kept copy handed over (gone from Redis)",
          not await redis().exists(fallback.KEPT_KEY.format(uid=uid, token=token)))
    GRADED.clear()
    out = await w.send(uid, text=PROMPT)
    check("E2 ...the question sent: the kept essay graded as the answer, not asked for again, paid once",
          len(GRADED) == 1 and "smart one" in GRADED[0] and docs(out) and await balance(uid) == 45
          and not any("Endi javobingizni" in x for x in texts(out)), (GRADED, await balance(uid), texts(out)))

    # the kept essay sent again as the "question": asked for the question again, nothing graded
    out = await w.send(uid, text=essay("smart two"))
    token = [b for b in buttons(out) if b.startswith(CB_SMART_PREFIX)][0].rsplit(":", 1)[-1]
    await w.press(uid, f"{CB_SMART_PREFIX}w:task2:{token}")
    GRADED.clear()
    out = await w.send(uid, text=essay("smart two"))
    check("E2 the kept essay sent as its own question: 'that's your answer, send the question', nothing graded",
          not GRADED and any("saqlangan javobingiz" in x for x in texts(out))
          and await st.get_state() == WritingStates.awaiting_prompt.state, texts(out))
    out = await w.send(uid, text=PROMPT)
    check("E2 ...then the real question: graded", len(GRADED) == 1 and "smart two" in GRADED[0] and docs(out))

    # Task 1: a typed question asks for its chart first; "go on without it" grades the kept report right away
    out = await w.send(uid, text=essay("task one"))
    token = [b for b in buttons(out) if b.startswith(CB_SMART_PREFIX)][0].rsplit(":", 1)[-1]
    await w.press(uid, f"{CB_SMART_PREFIX}w:task1:{token}")
    GRADED.clear()
    await w.send(uid, text="The chart below shows the number of students at three universities between 2000 and "
                           "2020. Summarise the information by selecting and reporting the main features.")
    asked_chart = await st.get_state() == WritingStates.awaiting_chart.state and not GRADED
    out = await w.press(uid, CB_NO_CHART)
    check("E2 Task 1 kept: the chart asked for, then (without it) the kept report graded, not asked for again",
          asked_chart and len(GRADED) == 1 and "task one" in GRADED[0] and docs(out), (asked_chart, GRADED))

    # what is not an essay, and other states
    out = await w.send(uid, text="salom, qalesiz? coin qanday olinadi")
    check("E2 a short line: 'pick a section', as before", any("menyudan" in x for x in texts(out))
          and not any(b.startswith(CB_SMART_PREFIX) for b in buttons(out)), texts(out))
    uzbek = ("Men har kuni ertalab soat yettida turaman va nonushta qilaman. Keyin maktabga boraman, u yerda "
             "do'stlarim bilan uchrashaman va darslarni tinglayman. ") * 5
    out = await w.send(uid, text=uzbek)
    check("E2 a long Uzbek text in Latin letters: not taken for an essay", any("menyudan" in x for x in texts(out))
          and fallback.looks_like_essay(text) and not fallback.looks_like_essay(uzbek), texts(out))
    question = (PROMPT + " Give reasons for your answer and include any relevant examples from your own knowledge "
                "or experience. Write at least 250 words. You should spend about 40 minutes on this task, and "
                "your essay will be assessed on how well you answer the question.")
    check("E2 a long exam question (its instructions): not taken for an essay",
          len(question.split()) >= 60 and not fallback.looks_like_essay(question), len(question.split()))
    await w.send(uid, text=BTN_WRITING)
    await w.press(uid, CB_TASK2)
    out = await w.send(uid, text=essay("as a question"))
    check("E2 in the Writing flow an essay-like text is that step's input (the question), no offer",
          not any(b.startswith(CB_SMART_PREFIX) for b in buttons(out))
          and await st.get_state() != WritingStates.awaiting_prompt.state, texts(out))
    await st.clear()
    await st.set_state(WritingStates.confirming_section)
    out = await w.send(uid, text=essay("button wait"))
    check("E2 a step waiting for a button: 'use the buttons', no offer", any("tugmalardan" in x for x in texts(out))
          and not any(b.startswith(CB_SMART_PREFIX) for b in buttons(out)), texts(out))
    await st.set_state(SpeakingStates.awaiting_answer)
    out = await w.send(uid, text=essay("speaking step"))
    check("E2 the Speaking answer step: its own 'send a voice' reply, no offer",
          not any(b.startswith(CB_SMART_PREFIX) for b in buttons(out)) and texts(out), texts(out))
    await st.clear()

    # a long essay Telegram split into two messages: one offer, one kept text
    part1 = SENTENCE * 45
    part2 = "And this is the rest of my essay, which finishes the argument. " * 3
    out1 = await w.send(uid, text=part1)
    out2 = await w.send(uid, text=part2)
    token = [b for b in buttons(out1) if b.startswith(CB_SMART_PREFIX)][0].rsplit(":", 1)[-1]
    kept = await fallback._kept(uid, token)
    check("E2 an essay split by Telegram: one offer, both parts kept as one text", not sends(out2)
          and kept is not None and "rest of my essay" in kept["text"] and kept["text"].startswith(SENTENCE.strip()))

    # no coins: the paywall (with the carry-over line); the text stays kept for after the top-up
    poor = BASE + 101
    await make_user(poor, 3)
    out = await w.send(poor, text=essay("poor"))
    token = [b for b in buttons(out) if b.startswith(CB_SMART_PREFIX)][0].rsplit(":", 1)[-1]
    out = await w.press(poor, f"{CB_SMART_PREFIX}w:task2:{token}")
    check("E2 3 coins: the paywall, the essay still kept", any("Coin yetarli emas" in x for x in texts(out))
          and await fallback._kept(poor, token) is not None, texts(out))
    check("E3 the paywall: '3 coin + 10 coin = 13 coin = 2 tekshiruv'",
          any("3 coin + 10 coin = 13 coin = 2 tekshiruv" in x for x in texts(out)), texts(out))
    await make_user(poor, 20)
    out = await w.press(poor, f"{CB_SMART_PREFIX}w:task2:{token}")
    check("E2 ...after the top-up the same button goes on with the kept essay",
          any("Javobingiz saqlandi" in x for x in texts(out))
          and (await w.state(poor).get_data()).get("kept_answer", "").endswith("poor matters."))

    # a photo: the Writing buttons, the flow says to send the question first (nothing guessed)
    out = await w.send(uid, photo=[PhotoSize(file_id="gph1", file_unique_id="ugph1", width=800, height=600)])
    check("E2 a photo outside any flow: the Writing buttons (nothing kept)",
          any("Rasm yoki faylni oldim" in x for x in texts(out))
          and [b for b in buttons(out) if b.startswith(CB_SMART_PREFIX)]
          == [f"{CB_SMART_PREFIX}w:task1:", f"{CB_SMART_PREFIX}w:task2:"], texts(out))
    out = await w.press(uid, f"{CB_SMART_PREFIX}w:task1:")
    check("E2 ...Task 1: its question step, saying to send the question first",
          any("Avval topshiriqning <b>savolini</b>" in x for x in texts(out))
          and await st.get_state() == WritingStates.awaiting_prompt.state
          and not (await st.get_data()).get("kept_answer"), texts(out))
    await st.clear()

    # a voice: Part 1/2/3; Part 2 keeps it and grades it once the cue card is in
    def voice():
        fid = f"gv{next(w.counter)}"
        w.files[fid] = _wav()
        return Voice(file_id=fid, file_unique_id="u" + fid, duration=2)

    out = await w.send(uid, voice=voice())
    smart = [b for b in buttons(out) if b.startswith(CB_SMART_PREFIX)]
    token = smart[0].rsplit(":", 1)[-1] if smart else ""
    check("E2 a voice outside any flow: 'Part 1 / 2 / 3'", any("Ovozli javobingizni oldim" in x for x in texts(out))
          and smart == [f"{CB_SMART_PREFIX}s:part{n}:{token}" for n in (1, 2, 3)], texts(out))
    out = await w.press(uid, f"{CB_SMART_PREFIX}s:part2:{token}")
    check("E2 ...Part 2: the talk kept, the cue card asked", any("Yozuvingiz saqlandi" in x for x in texts(out))
          and (await st.get_data()).get("kept_voice") and await st.get_state()
          == SpeakingStates.awaiting_question.state, texts(out))
    SPOKEN.clear()
    before = await balance(uid)
    out = await w.send(uid, text=CUE_CARD)
    check("E2 ...the cue card sent: the kept talk graded as its answer, paid once", SPOKEN == [1] and docs(out)
          and await balance(uid) == before - 5 and await st.get_state() is None, (SPOKEN, texts(out)))
    check("E3 ...under the Speaking report too: the coins left",
          any(f"Balansingizda <b>{before - 5} coin</b>" in x and "Davom etamizmi" in x for x in texts(out)),
          texts(out)[-2:])
    out = await w.send(uid, voice=voice())
    token = [b for b in buttons(out) if b.startswith(CB_SMART_PREFIX)][0].rsplit(":", 1)[-1]
    out = await w.press(uid, f"{CB_SMART_PREFIX}s:part1:{token}")
    check("E2 a voice for Part 1: the part starts and asks for the answers again (not kept)",
          any("Avval savollarni yuboring" in x for x in texts(out)) and not (await st.get_data()).get("kept_voice")
          and await st.get_state() == SpeakingStates.awaiting_question.state, texts(out))
    await st.clear()


# --- E3 the next step -----------------------------------------------------------------------------------------


async def e3_next_step(w: World) -> None:
    from bot.handlers import payments as pay
    from bot.keyboards.account_kb import CB_TOPUP_STUDENT
    from bot.keyboards.entry_kb import CB_OPEN_SPEAKING, CB_OPEN_WRITING
    from bot.keyboards.main_menu_kb import BTN_WRITING
    from bot.keyboards.payment_kb import buy_coins_kb
    from bot.keyboards.writing_task_kb import CB_TASK1, CB_TASK2
    from bot.states.writing_states import WritingStates
    from bot.utils import check_guard as guard
    from bot.utils.quota import carry_over_note
    from db import crud
    from db.database import get_session
    from db.models import PaymentKind

    async def task2_check(uid: int, tag: str):
        await w.send(uid, text=BTN_WRITING)
        await w.press(uid, CB_TASK2)
        await w.send(uid, text=PROMPT)
        return await w.send(uid, text=essay(tag))

    rich, low, kid, payer = BASE + 200, BASE + 201, BASE + 202, BASE + 203
    await make_user(rich, 13)
    await make_user(low, 7)
    await make_user(kid, 2)
    await make_user(payer, 300)
    await group_with(payer, [kid])
    out = await task2_check(rich, "rich")
    last = [c for c in sends(out) if "Davom etamizmi" in c.text]
    check("E3 a report with 8 coins left: 'enough for 1 more check', '🔁 Yana tekshirish' as before, no top-up",
          last and "Balansingizda <b>8 coin</b> — yana <b>1</b> ta tekshiruvga yetadi" in last[0].text
          and buttons(last)[0] == CB_TASK2 and CB_TOPUP_STUDENT not in buttons(last),
          [c.text for c in last])
    out = await task2_check(low, "low")
    last = [c for c in sends(out) if "Davom etamizmi" in c.text]
    check("E3 a report with 2 coins left: fewer than a check, '💳 Hisobni to'ldirish' on top, Yana tekshirish kept",
          last and "<b>2 coin</b> qoldi — keyingi tekshiruv <b>5 coin</b>" in last[0].text
          and buttons(last)[:2] == [CB_TOPUP_STUDENT, CB_TASK2], [(c.text, buttons(c)) for c in last])
    out = await task2_check(kid, "kid")
    last = [c for c in sends(out) if "Davom etamizmi" in c.text]
    check("E3 a sponsored student: no coins line, no top-up", last and "coin" not in last[0].text
          and CB_TOPUP_STUDENT not in buttons(last), [c.text for c in last])

    # the pack labels
    uz = [b.text for row in buy_coins_kb("uz", with_center_packs=True).inline_keyboard for b in row]
    ru = [b.text for row in buy_coins_kb("ru").inline_keyboard for b in row]
    en = [b.text for row in buy_coins_kb("en").inline_keyboard for b in row]
    check("E3 pack buttons: '= N tekshiruv'", uz[0] == "🟡 10 coin = 2 tekshiruv — 10 000 so'm"
          and uz[1] == "🟡 25 coin = 5 tekshiruv — 23 000 so'm (−8%)"
          and "🟡 5000 coin = 1000 tekshiruv — 3 750 000 so'm (−25%)" in uz, uz)
    check("E3 ...in Russian and English", ru[0] == "🟡 10 coin — 10 000 сум · проверок: 2"
          and en[0] == "🟡 10 coin = 2 checks — 10 000 UZS", (ru[0], en[0]))

    # the paywall's carry-over
    zero = BASE + 204
    await make_user(zero, 0)
    out = await w.press(zero, CB_TASK1)
    check("E3 the paywall with 0 coins: no carry-over line (nothing to carry)",
          any("Coin yetarli emas" in x for x in texts(out)) and not any("yo'qolmaydi" in x for x in texts(out)))
    check("E3 carry_over_note: 4 coins in Russian, none at 5", "4 coin + 10 coin = 14 coin" in carry_over_note(4, 5, "ru")
          and "проверок: 2" in carry_over_note(4, 5, "ru") and carry_over_note(5, 5, "uz") == "")

    # the approval's buttons
    buyer = BASE + 205
    await make_user(buyer, 0)
    async with get_session() as s:
        req = await crud.create_payment_request(s, user_id=buyer, coins=10, amount=10_000)
        gift_req = await crud.create_payment_request(s, user_id=buyer, coins=10, amount=10_000, kind=PaymentKind.gift)
    plain = await pay.approval_keyboard(crud.PaymentDecision(req, True), "uz")
    check("E3 approval, nothing parked: '✍️ Writing' / '🎙 Speaking'",
          buttons(plain) == [CB_OPEN_WRITING, CB_OPEN_SPEAKING], buttons(plain))
    st = w.state(buyer)
    await st.set_state(WritingStates.failed)
    await st.update_data(task_type="task2", answer_text=essay("parked"), check_token="tokE3")
    await guard.park(buyer, st)
    parked = await pay.approval_keyboard(crud.PaymentDecision(req, True), "uz")
    check("E3 approval with a parked check: '🔁 Tekshiruvni davom ettirish' first (alone)",
          buttons(parked) == [f"{guard.CB_RETRY}tokE3"], buttons(parked))
    gift = await pay.approval_keyboard(crud.PaymentDecision(gift_req, True, gift=NS(code="giftE3", coins=10)), "uz")
    urls = [b.url for row in gift.inline_keyboard for b in row]
    check("E3 a gift's approval: its share button, as before", len(urls) == 1
          and urls[0].startswith("https://t.me/share/url?") and "giftE3" in urls[0], urls)
    await st.clear()
    out = await w.press(buyer, CB_OPEN_WRITING)
    check("E3 ...'✍️ Writing' under the approval: the Task picker as a new message (the approval stays)",
          any(c.text == "Qaysi turini tekshirmoqchisiz?" and buttons(c)[:2] == [CB_TASK1, CB_TASK2] for c in sends(out))
          and not [c for c in out if type(c).__name__ == "EditMessageText"], texts(out))
    out = await w.press(buyer, CB_OPEN_SPEAKING)
    check("E3 ...'🎙 Speaking': the Part picker as a new message",
          any(c.text == "Qaysi qismini tekshirmoqchisiz?" for c in sends(out)), texts(out))


# --- E4 the ready-question buttons -----------------------------------------------------------------------------


async def e4_ready_question(w: World) -> None:
    from bot.i18n import t
    from bot.keyboards.entry_kb import CB_OPEN_SPEAKING, CB_OPEN_WRITING
    from bot.keyboards.lang_kb import CB_LANG_ONBOARD_PREFIX
    from bot.keyboards.main_menu_kb import BTN_SPEAKING, BTN_WRITING
    from bot.keyboards.practice_kb import CB_OPEN_PREFIX, CB_PRACTICE_NEW
    from bot.keyboards.speaking_part_kb import CB_NO_QUESTIONS, CB_PART1, CB_SPEAKING_MENU
    from bot.keyboards.writing_task_kb import CB_TASK2, CB_WRITING_MENU

    uid = BASE + 300
    await make_user(uid, 50)
    st = w.state(uid)
    await w.send(uid, text=BTN_WRITING)
    out = await w.press(uid, CB_TASK2)
    labels = [b.text for c in out for row in (getattr(getattr(c, "reply_markup", None), "inline_keyboard", None) or [])
              for b in row]
    check("E4 Writing Task 2 'send the question': '📝 Tayyor savol tanlash' (that task's list), then back",
          buttons(out) == [f"{CB_OPEN_PREFIX}writing_task2", CB_WRITING_MENU] and labels[0] == "📝 Tayyor savol tanlash",
          (buttons(out), labels))
    out = await w.press(uid, f"{CB_OPEN_PREFIX}writing_task2")
    check("E4 ...pressed: the step left, Writing Task 2's question list in its place",
          await st.get_state() is None and any("Writing Task 2" in x for x in texts(out))
          and [c for c in out if type(c).__name__ == "EditMessageText"], texts(out))
    await w.send(uid, text=BTN_SPEAKING)
    out = await w.press(uid, CB_PART1)
    check("E4 Speaking Part 1 'send the question': '📝 Tayyor savol tanlash', the recording button, back",
          buttons(out) == [f"{CB_OPEN_PREFIX}speaking_part1", CB_NO_QUESTIONS, CB_SPEAKING_MENU], buttons(out))
    await st.clear()

    new = BASE + 301
    await make_user(new, 8)
    out = await w.press(new, f"{CB_LANG_ONBOARD_PREFIX}uz")
    last = [c for c in sends(out) if c.text == t("start.real_task", "uz")]
    check("E4 the last onboarding message: Writing / Speaking / ready question",
          last and buttons(last) == [CB_OPEN_WRITING, CB_OPEN_SPEAKING, CB_PRACTICE_NEW], buttons(last))
    out = await w.press(new, CB_PRACTICE_NEW)
    check("E4 ...'📝 Tayyor savol tanlash': the Practice picker as a new message (the tip stays)",
          any(c.text == t("practice.choose_module", "uz") for c in sends(out))
          and not [c for c in out if type(c).__name__ in ("EditMessageText", "DeleteMessage")], texts(out))


# --- E5 the referral link ----------------------------------------------------------------------------------------


async def e5_referral(w: World) -> None:
    from bot.config import settings
    from bot.i18n import t
    from bot.keyboards.main_menu_kb import BTN_ACCOUNT_UZ

    uid = BASE + 400
    await make_user(uid, 8)
    sub = await add_check(uid, datetime.now(timezone.utc))
    link = f"https://t.me/{settings.telegram_bot_username}?start=ref_{uid}"
    out = await w.press(uid, f"share:writing:{sub}")
    photos = [c for c in out if type(c).__name__ == "SendPhoto"]
    check("E5 the share card's caption carries the student's referral link (it travels with a forward)",
          photos and link in photos[0].caption and "birinchi tekshiruv bepul" in photos[0].caption,
          [p.caption for p in photos])
    out = await w.send(uid, text=BTN_ACCOUNT_UZ)
    invite = [b for c in sends(out) for row in (getattr(c.reply_markup, "inline_keyboard", None) or []) for b in row
              if b.url]
    query = parse_qs(urlparse(invite[0].url).query) if invite else {}
    check("E5 Hisobim: '🎁 Do'stni taklif qilish' opens Telegram's share with the link and an invite line",
          invite and invite[0].text == "🎁 Do'stni taklif qilish" and invite[0].url.startswith("https://t.me/share/url?")
          and query.get("url") == [link] and query.get("text") == [t("acc.invite_text", "uz")], invite)


async def main() -> int:
    from bot.utils.crm import redis

    await redis().flushdb()
    fake_grading()
    w = World()
    started = time.monotonic()
    await e1_nudges(w)
    await e2_smart(w)
    await e3_next_step(w)
    await e4_ready_question(w)
    await e5_referral(w)
    await w.bot.session.close()
    print(f"\n({time.monotonic() - started:.0f} s)")
    print(f"{len(fails)} failed" if fails else "all passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
