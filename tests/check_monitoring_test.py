"""The checks and what they cost, watched before the launch (2026-11-16) - offline: a THROWAWAY Postgres and Redis
(the agent runner), Telegram and OpenAI are fakes, the grading itself untouched (tests/pipeline_hardening_test.py's
fake world and fake grading).

D3 error bursts: a repeated error line is folded - one "×N in the last 10 minutes" card and errors-page entry per
   window that had repeats, rolling while it goes on - and the day's error count includes the repeats; 3 checks
   stopped for a reason not the student's within 15 minutes: one alert, in the group and directly.
D4 the group's two lanes: the high lane sent first, the group-unreachable wait keeps each lane's order, each lane
   trimmed on its own and a trimmed high-lane message told; old queued messages sorted into lanes once.
D8 cost guard rails: a check over its limit (💸, ⚠️ too over twice it), far over its kind's median, the day's spend
   cap at 80% and 100% once each, a long queue; a routine cost card folded into a digest while the queue is long.
Incidents: one card per check that stopped - each classified reason - its own log line's card not sent; degraded
   checks (Azure, undelivered, slow) said on their own card; normal refusals are no incident.
The panel's «Xarajat chegaralari» card: the owner's only, saved, applied.
"""
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import httpx  # noqa: E402

from bot.config import settings  # noqa: E402

settings.web_2fa = False

import pipeline_hardening_test as ph  # noqa: E402 - the fake Telegram world, the faked grading

fails: list[str] = []
ADMIN = 111  # ADMIN_TELEGRAM_IDS on the runner
GROUP = -100777
BASE = 7_400_000
POSTS: list[tuple[str, str]] = []  # (topic, text) the group was given
EDITS: list[tuple[str, str, str]] = []  # (key, topic, text)


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


def posted(topic: str | None = None, has: str = "") -> list[str]:
    return [text for tp, text in POSTS if (topic is None or tp == topic) and has in text]


async def settle(seconds: float = 0.15) -> None:
    from bot.utils import crm

    await asyncio.sleep(seconds)
    await crm.settle_background()


def direct(session, start: int = 0) -> list[str]:
    return [c.text for c in session.calls[start:] if type(c).__name__ == "SendMessage"
            and getattr(c, "chat_id", None) == ADMIN]


# --- D3: error bursts ------------------------------------------------------------------------------------------


async def d3_bursts() -> None:
    from bot.utils import crm

    r = crm.redis()
    fw = crm._forwarder
    fw.fold.window = 0.6
    today = crm.local_now().date().isoformat()
    await r.delete(crm.ERRORS_LOG_KEY, f"crm:errors:{today}")
    log = logging.getLogger("bot.handlers.fake_d3")

    def fail(uid: int) -> None:  # one line in the code, as a real call site
        log.error("Grading failed for user %s", uid)

    POSTS.clear()
    fail(BASE + 1)
    await settle(0.1)
    check("D3 an error: its card in ⚠️ Xatolar", len(posted("errors", "Grading failed")) == 1, POSTS)
    for i in range(5):
        fail(BASE + 2 + i)
    await settle(0.1)
    check("D3 ...5 repeats within the window: no card each", len(posted("errors", "Grading failed")) == 1, POSTS)
    await settle(0.7)
    summary = posted("errors", "×5")
    check("D3 ...the window ends: one '×5 in the last N minutes' card", len(summary) == 1
          and "oxirgi" in summary[0] and "Grading failed" in summary[0], POSTS)
    entries = [json.loads(x) for x in await r.lrange(crm.ERRORS_LOG_KEY, 0, -1)]
    check("D3 ...the errors page: the first in full and the summary (again=5)", len(entries) == 2
          and entries[0]["again"] == 5 and entries[0]["message"].startswith("×5") and entries[1]["again"] == 0,
          entries)
    await settle(0.1)
    check("D3 ...the day's error count includes the repeats (6)", int(await r.get(f"crm:errors:{today}") or 0) == 6,
          await r.get(f"crm:errors:{today}"))
    fail(BASE + 9)
    fail(BASE + 10)
    await settle(0.8)
    check("D3 ...it goes on: the next window's own summary (×2), still no full card",
          len(posted("errors", "×2")) == 1 and len(posted("errors", "❌ <b>ERROR</b>")) == 1, POSTS)
    await settle(0.8)  # a window with no repeats ends quietly
    POSTS.clear()
    fail(BASE + 11)
    await settle(0.1)
    check("D3 ...after a quiet window: posted in full again", len(posted("errors", "❌ <b>ERROR</b>")) == 1
          and not posted("errors", "×"), POSTS)
    log.error("Another line entirely")
    await settle(0.1)
    check("D3 a different line: its own card", len(posted("errors", "Another line entirely")) == 1, POSTS)
    fw.fold.window = 600


# --- D4: the lanes ---------------------------------------------------------------------------------------------


def fake_bot(refuse=None):
    from aiogram import Bot
    from aiogram.client.session.base import BaseSession
    from aiogram.types import Chat, Message

    class S(BaseSession):
        def __init__(self):
            super().__init__()
            self.calls = []

        async def close(self):
            pass

        async def stream_content(self, *a, **k):
            yield b""

        async def make_request(self, bot, method, timeout=None):
            self.calls.append(method)
            err = refuse(method) if refuse else None
            if err is not None:
                raise err
            return Message(message_id=len(self.calls), date=datetime.now(timezone.utc),
                           chat=Chat(id=getattr(method, "chat_id", 1) or 1, type="private"),
                           text=getattr(method, "text", None) or "x")

    session = S()
    return Bot("8888548766:TEST", session=session), session


async def d4_lanes(real_post) -> None:
    from aiogram.exceptions import TelegramBadRequest

    from bot.utils import crm

    r = crm.redis()
    await r.delete(crm.QUEUE, crm.HI_QUEUE, crm.TRIMMED_KEY)
    for topic in ("users", "writing", "payments", "support", "costs", "errors", "daily"):
        await real_post(topic, f"msg-{topic}")
    check("D4 each topic in its lane: payments/support/errors/daily high, users/writing/costs normal",
          await r.llen(crm.HI_QUEUE) == 4 and await r.llen(crm.QUEUE) == 3,
          (await r.llen(crm.HI_QUEUE), await r.llen(crm.QUEUE)))
    check("D4 ...an edit follows its topic's lane", crm.lane("payments") == crm.HI_QUEUE
          and crm.lane("costs") == crm.QUEUE and crm.lane(None) == crm.QUEUE)

    bot, session = fake_bot()
    pause = crm.PAUSE
    crm.PAUSE = 0
    task = asyncio.create_task(crm.run_sender(bot))
    for _ in range(300):
        await asyncio.sleep(0.02)
        if len([c for c in session.calls if getattr(c, "chat_id", None) == GROUP]) >= 7:
            break
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    order = [c.text for c in session.calls if getattr(c, "chat_id", None) == GROUP]
    check("D4 the sender: the high lane first (in its order), then the normal lane",
          order == ["msg-payments", "msg-support", "msg-errors", "msg-daily", "msg-users", "msg-writing",
                    "msg-costs"], order)

    # the group unreachable: a message goes back to the head of its own lane
    real_sleep = asyncio.sleep

    async def fast_sleep(seconds, *a, **k):
        await real_sleep(0)

    await r.delete(crm.QUEUE, crm.HI_QUEUE)
    await real_post("payments", "p-1")
    await real_post("payments", "p-2")
    await real_post("users", "u-1")
    await real_post("users", "u-2")
    gone = lambda m: TelegramBadRequest(method=m, message="Bad Request: chat not found") \
        if getattr(m, "chat_id", None) == GROUP else None  # noqa: E731
    bot2, _ = fake_bot(gone)
    cfg = crm.config()
    asyncio.sleep = fast_sleep
    try:
        key, raw = await r.blpop([crm.HI_QUEUE, crm.QUEUE], timeout=1)
        first = await crm._deliver_with_retries(bot2, cfg, json.loads(raw), key)
        key2, raw2 = await r.blpop([crm.QUEUE], timeout=1)
        second = await crm._deliver_with_retries(bot2, cfg, json.loads(raw2), key2)
    finally:
        asyncio.sleep = real_sleep
    hi = [json.loads(x)["text"] for x in await r.lrange(crm.HI_QUEUE, 0, -1)]
    normal = [json.loads(x)["text"] for x in await r.lrange(crm.QUEUE, 0, -1)]
    check("D4 the group unreachable: a high-lane message back at the head of the high lane",
          first == "waiting" and key == crm.HI_QUEUE and hi == ["p-1", "p-2"], (first, key, hi))
    check("D4 ...a normal one back at the head of the normal lane (each lane keeps its order)",
          second == "waiting" and normal == ["u-1", "u-2"], (second, normal))

    # each lane trimmed on its own; the high lane's loss is told (directly too), never silently
    caps = crm.MAX_HI, crm.MAX_QUEUE
    crm.MAX_HI, crm.MAX_QUEUE = 3, 3
    await r.delete(crm.QUEUE, crm.HI_QUEUE, crm.TRIMMED_KEY)
    try:
        for i in range(5):
            await real_post("payments", f"pay-{i}")
        for i in range(4):
            await real_post("users", f"user-{i}")
        hi = [json.loads(x)["text"] for x in await r.lrange(crm.HI_QUEUE, 0, -1)]
        normal = [json.loads(x)["text"] for x in await r.lrange(crm.QUEUE, 0, -1)]
        check("D4 trimming: each lane to its own cap, the oldest go", hi == ["pay-2", "pay-3", "pay-4"]
              and normal == ["user-1", "user-2", "user-3"], (hi, normal))
        counted = await r.hgetall(crm.TRIMMED_KEY)
        check("D4 ...every trimmed message counted by lane", counted == {crm.HI_QUEUE: "2", crm.QUEUE: "1"}, counted)
        bot3, s3 = fake_bot()
        crm._direct_sent.clear()
        POSTS.clear()
        crm._last_normal_trim = 0.0
        await crm._report_trims(bot3)
        told = direct(s3)
        check("D4 ...the high lane's loss: the admins told directly how many", len(told) == 1 and "2</b> ta" in told[0],
              told)
        check("D4 ...and the normal lane's in ⚠️ Xatolar (no group card while the high lane is still full)",
              len(posted("errors", "1</b> ta")) == 1 and not posted("errors", "muhim bo'limlarning"), POSTS)
        check("D4 ...the counts reset once told", int((await r.hgetall(crm.TRIMMED_KEY)).get(crm.HI_QUEUE, 0)) == 0)
    finally:
        crm.MAX_HI, crm.MAX_QUEUE = caps

    # messages queued before the lanes: sorted once at the sender's start, in order
    await r.delete(crm.QUEUE, crm.HI_QUEUE)
    for topic, text in (("users", "a"), ("payments", "b"), ("writing", "c"), ("errors", "d"), ("payments", "e")):
        await r.rpush(crm.QUEUE, json.dumps({"op": "send", "topic": topic, "text": text}))
    moved = await crm._sort_lanes()
    hi = [json.loads(x)["text"] for x in await r.lrange(crm.HI_QUEUE, 0, -1)]
    normal = [json.loads(x)["text"] for x in await r.lrange(crm.QUEUE, 0, -1)]
    check("D4 the old single queue sorted into lanes once, each in order", moved == 3 and hi == ["b", "d", "e"]
          and normal == ["a", "c"], (moved, hi, normal))
    crm.PAUSE = pause
    await r.delete(crm.QUEUE, crm.HI_QUEUE, crm.TRIMMED_KEY)


# --- D8: cost guard rails ---------------------------------------------------------------------------------------


def cost(total: float) -> dict:
    return {"total_usd": total, "by_purpose": {"feedback": round(total * 0.7, 4), "score": round(total * 0.3, 4)},
            "calls": 6, "models": ["gpt-5", "gpt-5-mini"],
            "by_model": {"gpt-5": round(total * 0.8, 4), "gpt-5-mini": round(total * 0.2, 4)},
            "details": {"feedback": {"usd": round(total * 0.7, 4), "calls": 2, "flex": 2, "in": 9000, "cached": 0,
                                     "out": 3000, "reasoning": 1000, "audio_s": 0.0, "models": ["gpt-5"]},
                        "score": {"usd": round(total * 0.3, 4), "calls": 4, "flex": 0, "in": 4000, "cached": 0,
                                  "out": 900, "reasoning": 300, "audio_s": 0.0, "models": ["gpt-5-mini"]}}}


async def make_submission(uid: int, total: float, task: str = "task2") -> int:
    from db.database import get_session
    from db.models import SourceType, TaskType, WritingSubmission

    async with get_session() as s:
        sub = WritingSubmission(user_id=uid, task_type=TaskType(task), prompt_text=ph.PROMPT,
                                prompt_source=SourceType.text, answer_text=ph.essay("cost"),
                                answer_source=SourceType.text, overall_band=6.5,
                                criteria_scores={"TA": 6.5, "CC": 6.5, "LR": 6.5, "GRA": 6.5},
                                report_data={"cost": cost(total), "paid_with": {"mode": "coins", "coins": 5},
                                             "seconds": 130})
        s.add(sub)
        await s.commit()
        return sub.id


async def d8_costs(bot_session) -> None:
    from bot.utils import cost_guard, crm, crm_events

    r = crm.redis()
    uid = BASE + 50
    await ph.make_user(uid, 20)
    for kind in ("writing_task1", "writing_task2"):
        await r.delete(cost_guard.RECENT_KEY.format(kind))
    await cost_guard.reset()

    # 1. the per-check limit
    POSTS.clear()
    sid = await make_submission(uid, 0.30)
    await crm_events.check_done("w", sid)
    costs = posted("costs")
    check("D8.1 a check within its limit: its plain cost card, no 💸", len(costs) == 1 and "💸" not in costs[0]
          and "hisobot matni" in costs[0], costs)
    POSTS.clear()
    sid = await make_submission(uid, 0.55)
    await crm_events.check_done("w", sid)
    costs = posted("costs")
    check("D8.1 over its limit ($0.40): a 💸 card - the check, the cost against the limit, by purpose and model",
          len(costs) == 1 and "💸 <b>Qimmat tekshiruv</b> · Writing Task 2" in costs[0] and f"#{sid}" in costs[0]
          and f"U{uid}" in costs[0] and "chegara $0.4000" in costs[0] and "1.4×" in costs[0]
          and "Modellar: gpt-5 $0.4400 · gpt-5-mini $0.1100" in costs[0] and "hisobot matni" in costs[0]
          and "ball qo'yish" in costs[0], costs)
    check("D8.1 ...not over twice it: nothing in ⚠️ Xatolar", not posted("errors", "barobar"), posted("errors"))
    POSTS.clear()
    sid_severe = await make_submission(uid, 0.95)
    await crm_events.check_done("w", sid_severe)
    severe = posted("errors", "barobar oshdi")
    check("D8.1 over twice its limit: ⚠️ Xatolar too, short, with its dearest steps and models",
          len(posted("costs", "💸")) == 1 and len(severe) == 1 and "2.4 barobar" in severe[0]
          and "Eng qimmat qadamlar: hisobot matni" in severe[0] and "gpt-5" in severe[0], (severe, POSTS))
    verdict = await cost_guard.flag_of("w", sid_severe)
    check("D8.1 ...its verdict kept for later edits", verdict and verdict["severe"] and verdict["over"], verdict)
    EDITS.clear()
    POSTS.clear()
    await crm_events.cost_updated("w", sid_severe)  # a voice made later: the 💸 card edited, no second alert
    check("D8.1 ...a later cost edit keeps it 💸 and does not alert again",
          any(k == f"cost:w:{sid_severe}" and "💸" in t for k, _, t in EDITS) and not posted("errors"), (EDITS, POSTS))
    check("D8.1 the limits map onto every check kind (coins.CHECK_COINS)", cost_guard.KIND_LIMIT == {
        "writing_task1": "writing_task1", "writing_task2": "writing_task2", "speaking_part1": "speaking_part",
        "speaking_part2": "speaking_part", "speaking_part3": "speaking_part", "speaking_full": "speaking_full"}
        and (await cost_guard.limits(fresh=True))["speaking_full"] == 2.0
        and (await cost_guard.limits())["speaking_part"] == 0.75, cost_guard.KIND_LIMIT)

    # 2. far over its kind's median (whatever the limit)
    key = cost_guard.RECENT_KEY.format("writing_task1")
    await r.delete(key)
    for _ in range(5):
        await r.lpush(key, "0.1")
    POSTS.clear()
    await crm_events.check_done("w", await make_submission(uid, 0.30, "task1"))
    check("D8.2 3× the median but only 5 checks of its kind yet: not flagged", not posted("costs", "💸"),
          posted("costs"))
    await r.delete(key)
    for _ in range(12):
        await r.lpush(key, "0.1")
    POSTS.clear()
    await crm_events.check_done("w", await make_submission(uid, 0.30, "task1"))
    flagged = posted("costs", "💸")
    check("D8.2 3× the median of the last 12 Task 1 checks (within its $0.40 limit): flagged 💸",
          len(flagged) == 1 and "Odatdagidan <b>3.0×</b>" in flagged[0] and "medianasi $0.1000" in flagged[0]
          and "chegara" not in flagged[0].split("\n")[2], flagged)
    POSTS.clear()
    await crm_events.check_done("w", await make_submission(uid, 0.24, "task1"))
    check("D8.2 2.4× the median: not flagged (over 2.5× only)", not posted("costs", "💸"), posted("costs"))
    check("D8.2 ...each check adds itself to its kind's last 50", await r.llen(key) == 14)

    # a long group queue: a routine cost card folded into a digest (a 💸 never is)
    await r.delete(crm.QUEUE, cost_guard.FOLDED_KEY)
    for i in range(cost_guard.FOLD_AT + 1):
        await r.rpush(crm.QUEUE, json.dumps({"op": "send", "topic": "users", "text": f"backlog {i}"}))
    POSTS.clear()
    sid_folded = await make_submission(uid, 0.20)
    await crm_events.check_done("w", sid_folded)
    check("D4 the normal lane over 40 waiting: the check's card goes, its routine cost card is folded",
          len(posted("writing")) == 1 and not posted("costs") and await r.llen(cost_guard.FOLDED_KEY) == 1,
          (POSTS, await r.llen(cost_guard.FOLDED_KEY)))
    POSTS.clear()
    await crm_events.check_done("w", await make_submission(uid, 0.70))
    check("D4 ...a 💸 card is never folded", len(posted("costs", "💸")) == 1)
    EDITS.clear()
    await crm_events.cost_updated("w", sid_folded)
    check("D4 ...a folded card is not 'edited' into a new message later",
          not [k for k, _, _ in EDITS if k == f"cost:w:{sid_folded}"], EDITS)
    POSTS.clear()
    n = await cost_guard._digest()
    check("D4 ...queue still long, the oldest line fresh: no digest yet", n == 0 and not posted("costs"))
    await r.delete(crm.QUEUE)
    n = await cost_guard._digest()
    digest = posted("costs", "Xarajatlar jamlanmasi")
    check("D4 ...the queue short again: one digest with the folded check's line and the totals",
          n == 1 and len(digest) == 1 and f"Writing Task 2 #{sid_folded}" in digest[0] and "$0.2000" in digest[0]
          and "foyda" in digest[0] and await r.llen(cost_guard.FOLDED_KEY) == 0, digest)

    # 3. the day's spend cap, 80% and 100%, each once a Tashkent day
    day = crm.local_now().date()
    spend = crm.spend_key(day)
    await r.delete(spend, *[cost_guard.CAP_TOLD_KEY.format(day.isoformat(), p) for p in (80, 100)])
    await r.hset(spend, mapping={"writing": 10.0, "speaking": 6.5})
    crm._direct_sent.clear()
    start = len(bot_session.calls)
    POSTS.clear()
    await cost_guard.watch()
    await settle()
    cards = posted("errors", "kunlik chegaraning")
    check("D8.3 82% of the $20 cap: ⚠️ Xatolar, by category", len(cards) == 1 and "82%" in cards[0]
          and "Writing tekshiruvlari: $10.00" in cards[0], cards)
    check("D8.3 ...and the admins directly", any("82%" in t for t in direct(bot_session, start)),
          direct(bot_session, start))
    await cost_guard.watch()
    await settle()
    check("D8.3 ...told once that day", len(posted("errors", "kunlik chegaraning")) == 1)
    await r.hincrbyfloat(spend, "writing", 4.5)
    await cost_guard.watch()
    await settle()
    cards = posted("errors", "kunlik chegaraning")
    check("D8.3 105%: told again (100%), in the group and directly", len(cards) == 2 and "105%" in cards[1]
          and "🚨" in cards[1] and any("105%" in t for t in direct(bot_session, start)), cards)
    await cost_guard.watch()
    await settle()
    check("D8.3 ...and only once", len(posted("errors", "kunlik chegaraning")) == 2)
    real_now = crm.local_now
    crm.local_now = lambda: real_now() + timedelta(days=40)
    try:
        later = crm.local_now().date()
        crm._rate.update(day=later, rate=12000.0)  # no CBU call offline
        await r.hset(crm.spend_key(later), mapping={"writing": 25.0})
        POSTS.clear()
        await cost_guard.watch()
        await cost_guard.watch()
        cards = posted("errors", "kunlik chegaraning")
        check("D8.3 a jump past both on a new day: one card (the 100% one)", len(cards) == 1 and "125%" in cards[0],
              cards)
        await r.delete(crm.spend_key(later))
    finally:
        crm.local_now = real_now
    await r.delete(spend)

    # 4. a long queue: more than 20 checks waiting over 5 minutes, once an hour
    from bot.utils.capacity import governor

    await r.delete(cost_guard.QUEUE_TOLD_KEY)
    tickets = [f"lq{i}" for i in range(21)]
    governor.queue.extend(tickets[:20])
    for t in tickets:
        governor.since[t] = time.monotonic() - 400
    POSTS.clear()
    await cost_guard.watch()
    check("D8.4 20 checks waiting 6 minutes: not yet (over 20 only)", not posted("errors", "Navbat uzun"))
    governor.queue.append(tickets[20])
    start = len(bot_session.calls)
    await cost_guard.watch()
    await settle()
    cards = posted("errors", "Navbat uzun")
    check("D8.4 21 waiting over 5 minutes: ⚠️ Xatolar and directly", len(cards) == 1 and "21 ta" in cards[0]
          and any("Navbat uzun" in t for t in direct(bot_session, start)), cards)
    await cost_guard.watch()
    check("D8.4 ...once an hour", len(posted("errors", "Navbat uzun")) == 1)
    for t in tickets:
        governor.queue.remove(t)
        governor.since.pop(t, None)


# --- incidents --------------------------------------------------------------------------------------------------


def classify_cases() -> None:
    import httpx as hx
    import openai

    from bot.handlers.writing import ChartUnreadable
    from bot.utils import check_incidents as ci
    from bot.utils.input_extraction import ReceiveFailed, UnsupportedInputError

    req = hx.Request("POST", "https://api.openai.com/v1/chat/completions")

    def status(cls, code, body=None):
        return cls("err", response=hx.Response(code, request=req), body=body)

    cases = {
        "openai_rate_limit": status(openai.RateLimitError, 429, {"code": "rate_limit_exceeded"}),
        "openai_quota": status(openai.RateLimitError, 429, {"code": "insufficient_quota"}),
        "openai_timeout": openai.APITimeoutError(request=req),
        "openai_server": status(openai.InternalServerError, 503),
        "openai_bad_request": status(openai.BadRequestError, 400, {"code": "invalid_value"}),
        "openai_auth": status(openai.AuthenticationError, 401),
        "input_unreadable": ChartUnreadable("no chart in the picture"),
        "telegram_download": ReceiveFailed("download failed"),
    }
    for code, error in cases.items():
        got = ci.classify(error, elapsed=5)
        check(f"classify: {type(error).__name__} -> {code}", got.code == code, (got.code, got.title))
    check("classify: an OpenAI picture refusal is the student's input", ci.classify(
        status(openai.BadRequestError, 400, {"code": "invalid_image_format"})).user is True)
    check("classify: an unsupported file is the student's input",
          ci.classify(UnsupportedInputError("x")).code == "input_unreadable")
    wrapped = RuntimeError("grading failed")
    wrapped.__cause__ = cases["openai_quota"]
    check("classify: the cause of a wrapped exception (no credit inside a RuntimeError)",
          ci.classify(wrapped).code == "openai_quota")
    check("classify: a TimeoutError at the check's deadline",
          ci.classify(TimeoutError(), elapsed=settings.check_deadline_seconds).code == "deadline")
    check("classify: ...a TimeoutError long before it is not the deadline",
          ci.classify(TimeoutError(), elapsed=3).code == "timeout")
    check("classify: a cancellation", ci.classify(asyncio.CancelledError()).code == "cancelled")

    from aiogram.exceptions import TelegramForbiddenError

    check("classify: Telegram refusing a send: delivery",
          ci.classify(TelegramForbiddenError(method=None, message="Forbidden: bot was blocked by the user")).code
          == "telegram_delivery")
    from services.pdf import report_builder

    try:
        report_builder.build_writing_report_pdf(None, None, None, None, None, None)
        pdf_error = None
    except Exception as e:  # noqa: BLE001
        pdf_error = e
    got = ci.classify(pdf_error) if pdf_error else None
    check("classify: an error inside the PDF builder: pdf_render, with its code line",
          got is not None and got.code == "pdf_render" and "services/pdf/" in got.where, got)

    def buggy():
        return {}["missing"]

    try:
        buggy()
    except KeyError as e:
        bug = ci.classify(e)
    check("classify: anything else: a bug, its type and file:line", bug.code == "bug" and "KeyError" in bug.title
          and bug.where.startswith("tests/check_monitoring_test.py:") and "buggy" in bug.where, bug)

    import redis.exceptions

    check("classify: Redis away", ci.classify(redis.exceptions.ConnectionError("refused")).code == "redis_unavailable")


async def azure_degraded() -> None:
    import httpx as hx

    from bot.utils import check_guard as guard
    from bot.utils import check_incidents as ci
    from services.ai import pronunciation

    req = hx.Request("POST", "https://x.stt.speech.microsoft.com")
    real = pronunciation._assess_clip
    keys = settings.azure_speech_key, settings.azure_speech_region, settings.azure_timeout_seconds
    settings.azure_speech_key, settings.azure_speech_region = "k" * 32, "westeurope"
    results = {}

    async def run(name, clip):
        hold = guard.Hold(f"az{name}", BASE + 60, "s", "speaking_part1")
        ci.attach(hold)
        pronunciation._assess_clip = clip
        got = await pronunciation.assess_answers([(b"", [], 2.0)])
        results[name] = (got, await ci.card_lines(hold))
        ci.detach(hold)

    async def too_many(*a):
        raise hx.HTTPStatusError("429", request=req, response=hx.Response(429, request=req))

    async def slow(*a):
        await asyncio.sleep(2)

    async def down(*a):
        raise hx.ConnectError("unreachable", request=req)

    try:
        await asyncio.create_task(run("429", too_many))
        settings.azure_timeout_seconds = 0.2
        await asyncio.create_task(run("timeout", slow))
        settings.azure_timeout_seconds = keys[2]
        await asyncio.create_task(run("down", down))
    finally:
        pronunciation._assess_clip = real
        settings.azure_speech_key, settings.azure_speech_region, settings.azure_timeout_seconds = keys
    for name, text in (("429", "Azure so'rovlar limiti (429)"), ("timeout", "Azure javob bermadi (vaqt tugadi)"),
                       ("down", "Azure ishlamayapti")):
        got, lines = results[name]
        check(f"degraded: Azure {name} - Pronunciation estimated, the check's card says why",
              got is None and lines and "Pronunciation taxminiy" in lines[0] and text in lines[0]
              and "admin.bandzilla.uz/checks" in lines[-1], lines)


async def incidents(w) -> None:
    import httpx as hx
    import openai
    from sqlalchemy import select

    from bot.handlers import writing
    from bot.utils import check_guard as guard
    from bot.utils import check_incidents as ci
    from bot.utils import crm
    from db.database import get_session
    from db.models import CheckHold

    r = crm.redis()
    await r.delete(ci.BURST_KEY, ci.BURST_TOLD_KEY)
    ci.FOLD.window = 600  # distinct reasons below; the folding case sets its own window
    req = hx.Request("POST", "https://api.openai.com/v1/chat/completions")
    base_grade = writing.grade_writing
    act = {"fn": None}

    async def grade(**kw):
        if act["fn"] is not None:
            return await act["fn"](kw)
        return await base_grade(**kw)

    writing.grade_writing = grade
    guard.DELIVERY_WAITS = (0.01, 0.01, 0.01)

    async def one(uid: int, fn, tag: str):
        act["fn"] = fn
        await ph.make_user(uid, 20)
        await ph.start_task2(w, uid)
        try:
            return await w.send(uid, text=ph.essay(tag))
        finally:
            act["fn"] = None

    async def no_credit(kw):
        raise openai.RateLimitError("You exceeded your current quota", response=hx.Response(429, request=req),
                                    body={"code": "insufficient_quota"})

    POSTS.clear()
    uid = BASE + 101
    await one(uid, no_credit, "quota")
    await settle(0.8)  # past CLAIM_WAIT: the handler's own error card would be out by now
    cards = posted("errors", "Tekshiruv to'xtadi")
    card = cards[0] if cards else ""
    check("incident: OpenAI out of credit - one card in ⚠️ Xatolar with the check, stage, reason, refund",
          len(cards) == 1 and "Writing Task 2" in card and f"U{uid}" in card and f"<code>{uid}</code>" in card
          and "Bosqich: baholash (OpenAI)" in card and "OpenAI: balans tugagan (insufficient_quota)" in card
          and "5 coin qaytarildi</b> o'quvchiga" in card and "admin.bandzilla.uz/checks/stopped" in card, cards)
    check("incident: ...its own 'grading failed' log line sends no second card (claimed)",
          not posted("errors", "Writing grading failed"), posted("errors"))
    entries = [json.loads(x)["message"] for x in await r.lrange(crm.ERRORS_LOG_KEY, 0, 20)]
    check("incident: ...the errors page keeps that line", any("Writing grading failed" in m for m in entries))
    async with get_session() as s:
        held = (await s.execute(select(CheckHold).where(CheckHold.user_id == uid))).scalars().first()
    check("incident: ...the hold's reason (the panel's stopped checks) starts with the classified reason",
          held.state == "refunded" and held.reason.startswith("OpenAI: balans tugagan"), held.reason)

    async def rate_limited(kw):
        raise openai.RateLimitError("Rate limit reached", response=hx.Response(429, request=req),
                                    body={"code": "rate_limit_exceeded"})

    async def broken(kw):
        return {}["no such key"]

    start = len(w.session.calls)
    for n, (fn, text) in enumerate(((rate_limited, "OpenAI: so'rovlar limiti (429 rate limit)"),
                                    (broken, "Kutilmagan xato (bug): KeyError"))):
        POSTS.clear()
        await one(BASE + 102 + n, fn, f"case{n}")
        await settle(0.8)
        cards = posted("errors", "Tekshiruv to'xtadi")
        check(f"incident: {text}", len(cards) == 1 and text in cards[0], posted("errors"))
    check("incident: ...a bug names its code line", "📍 Kod: <code>tests/check_monitoring_test.py:" in cards[0]
          and "(broken)" in cards[0], cards)
    burst = posted("errors", "ta tekshiruv oxirgi 15 daqiqada")
    told = direct(w.session, start)
    check("D3 burst: the 3rd stopped check within 15 minutes - one alert listing the reasons, and directly",
          len(burst) == 1 and "OpenAI: balans tugagan" in burst[0] and "Kutilmagan xato" in burst[0]
          and "3 ta tekshiruv" in burst[0] and any("oxirgi 15 daqiqada" in t for t in told), (burst, told))

    # the deadline
    deadline = settings.check_deadline_seconds
    settings.check_deadline_seconds = 1

    async def hangs(kw):
        await asyncio.sleep(3)

    POSTS.clear()
    try:
        await one(BASE + 105, hangs, "deadline")
    finally:
        settings.check_deadline_seconds = deadline
    await settle(0.8)
    cards = posted("errors", "Tekshiruv to'xtadi")
    check("incident: the check's deadline", len(cards) == 1 and "Tekshiruv muddati o'tdi" in cards[0]
          and not posted("errors", "ran past"), posted("errors"))
    check("D3 burst: ...a 4th within the 30 minutes: no second alert",
          not posted("errors", "ta tekshiruv oxirgi 15 daqiqada"))

    # an unreadable chart is the student's side: the module's topic, no alarm
    async def unreadable(kw):
        raise writing.ChartUnreadable("no chart found")

    POSTS.clear()
    await one(BASE + 106, unreadable, "chart")
    await settle(0.8)
    check("incident: an unreadable input - ℹ️ in ✍️ Writing, nothing in ⚠️ Xatolar",
          len(posted("writing", "o'qilmadi")) == 1 and not posted("errors", "Tekshiruv to'xtadi"), POSTS)

    # degraded: not delivered, slow - said on the check's own card
    POSTS.clear()

    async def slow_check(kw):
        ci.running().started -= 9 * 60  # it "took" 9 minutes
        return await base_grade(**kw)

    w.refuse = lambda m: __import__("aiogram").exceptions.TelegramBadRequest(method=m, message="Bad Request: x") \
        if type(m).__name__ == "SendDocument" else None
    try:
        await one(BASE + 107, slow_check, "slow")
    finally:
        w.refuse = None
    await settle(0.8)
    cards = posted("writing", "✍️")
    check("degraded: undelivered and slow - both on the check's own card (⚠️ at its head), no card of its own",
          len(cards) == 1 and cards[0].startswith("⚠️ ✍️") and "📭 Hisobot saqlandi" in cards[0]
          and "🐢 Sekin: 9 daq" in cards[0] and "admin.bandzilla.uz/checks" in cards[0]
          and not posted("writing", "muammo bilan tugadi") and not posted("errors", "Tekshiruv"), POSTS)
    POSTS.clear()
    await one(BASE + 108, None, "fine")
    await settle(0.3)
    cards = posted("writing", "✍️")
    check("a check that went well: its card, no ⚠️", len(cards) == 1 and not cards[0].startswith("⚠️")
          and "Muammo" not in cards[0], cards)

    # a normal refusal is no incident
    POSTS.clear()
    poor = BASE + 109
    await ph.make_user(poor, 0)
    await ph.start_task2(w, poor)
    await w.send(poor, text=ph.essay("poor"))
    await settle(0.3)
    check("a check refused for want of coins: no incident card anywhere", not [t for _, t in POSTS if
                                                                             "to'xtadi" in t or "Muammo" in t], POSTS)

    # the same reason again within the window: folded into one "×N"
    ci.FOLD.window = 2.0

    async def server_down(kw):
        raise openai.InternalServerError("Server error", response=hx.Response(502, request=req), body=None)

    POSTS.clear()
    for n in range(3):
        await one(BASE + 110 + n, server_down, f"fold{n}")
    await settle(0.3)
    first = posted("errors", "OpenAI: server xatosi (502)")
    check("incident: 3 checks stopped by OpenAI's 502 at once - one full card", len(first) == 1
          and "Tekshiruv to'xtadi" in first[0], first)
    await settle(2.2)
    folded = posted("errors", "×2")
    check("incident: ...the other two in one '×2' card when the window ends, with who and the coins back",
          len(folded) == 1 and f"<code>{BASE + 111}</code>" in folded[0] and f"<code>{BASE + 112}</code>" in folded[0]
          and "Jami qaytarilgan: 10 coin" in folded[0], folded)
    ci.FOLD.window = 600

    # a restart: the holds a stopped bot left running, in one card
    async with get_session() as s:
        for n in range(2):
            uid = BASE + 120 + n
            await ph.make_user(uid, 15)
            s.add(CheckHold(id=f"rst{n}{int(time.time())}"[:32], user_id=uid, chat_id=uid, message_id=None,
                            module="w", kind="writing_task1", lang="uz", payer_id=uid, coins=5, exclusive=True,
                            state="held"))
        await s.commit()
    POSTS.clear()
    await guard.recover(w.bot, w.dp.storage)
    cards = posted("errors", "Bot qayta ishga tushdi")
    check("incident: a restart - one card for the checks it stopped, coins back", len(cards) == 1
          and "2 ta tekshiruv" in cards[0] and "5 coin qaytdi" in cards[0] and f"U{BASE + 120}" in cards[0], cards)
    writing.grade_writing = base_grade


# --- the panel's «Xarajat chegaralari» -------------------------------------------------------------------------


async def settings_card() -> None:
    from bot.utils import cost_guard
    from db.database import get_session
    from db.models import WebAdmin
    from webapp import panel
    from webapp.main import app

    await cost_guard.reset()
    transport = httpx.ASGITransport(app=app)
    async with get_session() as s:
        s.add(WebAdmin(username="cg_admin", name="Admin", password_hash=panel.hash_password("pw-cg"), role="admin"))
        await s.commit()
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as owner, \
            httpx.AsyncClient(transport=transport, base_url="https://testserver") as admin:
        await owner.post("/login", data={"username": settings.web_admin_username,
                                         "password": settings.web_admin_password})
        await admin.post("/login", data={"username": "cg_admin", "password": "pw-cg"})
        page = (await owner.get("/settings")).text
        check("settings: the owner sees «Xarajat chegaralari» with the values in force", "Xarajat chegaralari" in page
              and 'name="writing_task2"' in page and 'value="0.4"' in page and 'value="2"' in page
              and 'value="20"' in page and 'value="8"' in page, page[:200])
        r = await admin.post("/settings/cost-limits", data={"writing_task2": "0.05"}, follow_redirects=False)
        check("settings: another admin may not change them", r.status_code in (302, 303)
              and "denied" in r.headers.get("location", "") and not await cost_guard.overrides(),
              (r.status_code, r.headers.get("location")))
        r = await owner.post("/settings/cost-limits", data={"writing_task2": "0,55", "daily_cap": "$30",
                                                            "slow_minutes": "8", "speaking_full": ""},
                             follow_redirects=False)
        own = await cost_guard.overrides()
        check("settings: the owner saves them (a comma, a $ taken; a default not kept as the owner's)",
              r.headers.get("location") == "/settings?done=cost_limits#costs"
              and own == {"writing_task2": 0.55, "daily_cap": 30.0}, own)
        page = (await owner.get("/settings?done=cost_limits")).text
        check("settings: ...shown as set, with the default beside it", 'value="0.55"' in page
              and "siz o'rnatgansiz · standart 0.4" in page and "saqlandi" in page)
        values = await cost_guard.limits(fresh=True)
        v1 = await cost_guard.judge("writing_task2", 0.50, record=False)
        v2 = await cost_guard.judge("writing_task2", 0.60, record=False)
        check("settings: ...applied: $0.50 is within the new $0.55, $0.60 over it", values["writing_task2"] == 0.55
              and not v1["over"] and v2["over"] and v2["limit"] == 0.55, (v1, v2))
        r = await owner.post("/settings/cost-limits", data={"writing_task1": "abc"}, follow_redirects=False)
        check("settings: a wrong value: refused, nothing changed", "bad_cost_limits" in r.headers.get("location", "")
              and await cost_guard.overrides() == own)
        r = await owner.post("/settings/cost-limits", data={"speaking_part": "500"}, follow_redirects=False)
        check("settings: ...out of bounds too", "bad_cost_limits" in r.headers.get("location", ""))
        await owner.post("/settings/cost-limits", data={"action": "reset"}, follow_redirects=False)
        check("settings: reset - the defaults again", not await cost_guard.overrides()
              and (await cost_guard.limits(fresh=True))["writing_task2"] == 0.40)


async def main() -> int:
    from bot.utils import check_incidents, crm

    r = crm.redis()
    await r.flushdb()
    cfg_path = crm.config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps({"chat_id": GROUP, "topics": {k: i + 2 for i, k in enumerate(crm.TOPICS)},
                                    "backfill_at": "2026-11-16T00:00:00+00:00"}), encoding="utf-8")
    crm._rate.update(day=crm.local_now().date(), rate=12000.0)  # no CBU call offline
    real_post, real_edit = crm.post, crm.edit

    async def record_post(topic, text, **kw):
        POSTS.append((topic, text))

    async def record_edit(key, topic, text):
        EDITS.append((key, topic, text))

    crm.install_error_forwarding("bot")
    crm.COUNT_FLUSH = 0.05
    crm._forwarder.CLAIM_WAIT = 0.6
    check("the error forwarder is installed with the folding", crm._forwarder is not None
          and check_incidents.running() is None)
    crm.post, crm.edit = record_post, record_edit
    try:
        await d4_lanes(real_post)
        await d3_bursts()
        ph.fake_writing()
        w = ph.World()
        crm._bot = w.bot
        await d8_costs(w.session)
        classify_cases()
        await azure_degraded()
        await incidents(w)
        await settings_card()
        await w.bot.session.close()
    finally:
        crm.post, crm.edit = real_post, real_edit
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    await crm.settle_background()
    await r.aclose()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
