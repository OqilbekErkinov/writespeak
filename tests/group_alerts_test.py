"""2026-11-16: when the admins' group itself is the problem, the admins are told directly, and /healthz says so
(bot/utils/crm.py, webapp/main.py). Before: the sender's failure count was reset at the top of every turn (its
direct alert could never fire), and a bot removed from the group, a deleted topic or a bot that may not post
left only a log line nobody reads. Offline: a fake Bot API session, a throwaway Postgres + Redis (test_part2.sh).
"""
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from bot.config import settings  # noqa: E402

settings.web_2fa = False
fails: list[str] = []
ADMIN = 111  # ADMIN_TELEGRAM_IDS in test_part2.sh
GROUP = -100777


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


def fake_session(refuse=None):
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

    return S()


def direct(session) -> list[str]:
    return [c.text for c in session.calls if getattr(c, "chat_id", None) == ADMIN]


async def main() -> int:
    from aiogram import Bot
    from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

    from bot.utils import crm, crm_events

    real_sleep = asyncio.sleep

    async def fast_sleep(seconds, *a, **k):
        await real_sleep(0)

    crm.asyncio.sleep = fast_sleep  # the module's pauses (60 s waiting for the group, 5 s after an error)
    cfg_path = crm.config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps({"chat_id": GROUP, "topics": {"errors": 5, "payments": 6}}), encoding="utf-8")
    cfg = crm.config()

    def gone(method):
        if getattr(method, "chat_id", None) == GROUP:
            return TelegramBadRequest(method=method, message="Bad Request: chat not found")
        return None

    s = fake_session(gone)
    bot = Bot("8888548766:TEST", session=s)
    await crm.redis().delete(crm.QUEUE, crm.HI_QUEUE)
    job = {"op": "send", "topic": "errors", "text": "💳 test"}
    r1 = await crm._deliver_with_retries(bot, cfg, job)
    check("the bot removed from the group: the message waits at the head of the queue", r1 == "waiting"
          and await crm.redis().llen(crm.HI_QUEUE) == 1)  # an errors card: the high lane (2026-11-16)
    check("...no direct alert in the first minutes", not direct(s))
    crm._waiting_since = time.monotonic() - crm.WAIT_ALERT_AFTER - 5
    await crm.redis().lpop(crm.HI_QUEUE)
    r2 = await crm._deliver_with_retries(bot, cfg, job)
    told = direct(s)
    check("...unreachable for over 10 minutes: the admins told directly, how many wait", r2 == "waiting"
          and len(told) == 1 and "qayta qo'shing" in told[0] and "navbatda" in told[0], told)
    await crm.redis().lpop(crm.HI_QUEUE)
    await crm._deliver_with_retries(bot, cfg, job)
    check("...once an hour, not on every message", len(direct(s)) == 1)

    # a topic deleted in the group / never made
    s2 = fake_session()
    bot2 = Bot("8888548766:TEST", session=s2)
    r = await crm._deliver_with_retries(bot2, cfg, {"op": "send", "topic": "support", "text": "x"})
    check("a message for a topic the group lacks: dropped, the admins told to send /crm_setup", r == "dropped"
          and any("support" in m and "/crm_setup" in m for m in direct(s2)), direct(s2))

    def thread_gone(method):
        if getattr(method, "chat_id", None) == GROUP:
            return TelegramBadRequest(method=method, message="Bad Request: message thread not found")
        return None

    s3 = fake_session(thread_gone)
    bot3 = Bot("8888548766:TEST", session=s3)
    r = await crm._deliver_with_retries(bot3, cfg, {"op": "send", "topic": "payments", "text": "x"})
    check("a topic someone deleted: forgotten, the admins told", r == "dropped"
          and "payments" not in (crm.config().get("topics") or {})
          and any("o'chirilgan" in m for m in direct(s3)), direct(s3))

    def forbidden(method):
        if getattr(method, "chat_id", None) == GROUP:
            return TelegramForbiddenError(method=method, message="Forbidden: not enough rights to send text messages")
        return None

    s4 = fake_session(forbidden)
    bot4 = Bot("8888548766:TEST", session=s4)
    r = await crm._deliver_with_retries(bot4, cfg, {"op": "send", "topic": "errors", "text": "x"})
    check("the bot may not post in the group: dropped, the admins told", r == "dropped"
          and any("admin qiling" in m for m in direct(s4)), direct(s4))

    s5 = fake_session()
    bot5 = Bot("8888548766:TEST", session=s5)
    crm._waiting_since = time.monotonic() - 9999
    r = await crm._deliver_with_retries(bot5, cfg, {"op": "send", "topic": "errors", "text": "fine"})
    check("a delivered message: 'sent', the waiting clock reset", r == "sent" and crm._waiting_since is None
          and any(getattr(c, "chat_id", None) == GROUP for c in s5.calls))

    # the sender loop: a run of failures reaches its limit; a broken daily report is told too
    crm._direct_sent.clear()
    s6 = fake_session()
    bot6 = Bot("8888548766:TEST", session=s6)
    real_flush = crm.flush_spent

    async def broken_flush():
        raise RuntimeError("redis away (test)")

    crm.flush_spent = broken_flush
    task = asyncio.create_task(crm.run_sender(bot6))
    for _ in range(200):
        await real_sleep(0.01)
        if direct(s6):
            break
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    crm.flush_spent = real_flush
    check(f"the sender failing {crm.SENDER_ALERT_AFTER} turns in a row: the admins told directly (it never fired "
          "before)", any("to'xtab" in m for m in direct(s6)), direct(s6))

    crm._direct_sent.clear()
    s7 = fake_session()
    bot7 = Bot("8888548766:TEST", session=s7)
    real_daily = crm_events.daily_report_if_due

    async def broken_daily():
        raise ValueError("report broken (test)")

    crm_events.daily_report_if_due = broken_daily
    crm.REPORT_ALERT_AFTER = 1
    await crm.redis().delete(crm.SENDER_BEAT_KEY)
    crm._last_beat = 0.0
    task = asyncio.create_task(crm.run_sender(bot7))
    for _ in range(800):  # the alert first, then the turn's idle 3-second wait for the queue, then the beat
        await real_sleep(0.01)
        if direct(s7) and await crm.redis().get(crm.SENDER_BEAT_KEY):
            break
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    crm_events.daily_report_if_due = real_daily
    check("the daily report failing: the admins told directly, the sender keeps going",
          any("kunlik hisobot" in m for m in direct(s7)), direct(s7))
    beat = await crm.redis().get(crm.SENDER_BEAT_KEY)
    check("...a turn that went well writes the sender's beat", beat is not None and time.time() - int(beat) < 60)

    # /healthz: "down: group" when the sender's beat is stale
    from webapp.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://panel") as c:
        await crm.redis().set(crm.HEARTBEAT_KEY, str(int(time.time())))
        await crm.redis().set(crm.SENDER_BEAT_KEY, str(int(time.time())))
        r = await c.get("/healthz")
        check("/healthz: bot and group fine: 200 ok", r.status_code == 200 and r.text == "ok", r.text)
        await crm.redis().set(crm.SENDER_BEAT_KEY, str(int(time.time()) - crm.SENDER_STALE - 60))
        r = await c.get("/healthz")
        check("...the group's sender stale for 15 minutes: 503 'down: group' (UptimeRobot e-mails)",
              r.status_code == 503 and r.text == "down: group", r.text)
        await crm.redis().delete(crm.SENDER_BEAT_KEY)
        r = await c.get("/healthz")
        check("...no beat yet (a bot from before): not counted", r.status_code == 200, r.text)

    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    await crm.redis().aclose()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
