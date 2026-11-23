"""Who a broadcast did not reach, and why (the owner, 2026-11-23: #261123132311a1f5 - 18 "blocked", 22 "errors",
nobody could tell who): bot/utils/reach.py, the panel's broadcast job, its "Kimlarga yetmadi" page, and the
audience leaving out the ones the bot cannot write to. Offline: a fake Bot API session, a throwaway Postgres + Redis
(run_test.sh).
"""
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from bot.config import settings  # noqa: E402

settings.web_2fa = False

fails: list[str] = []


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


REFUSE = {
    11: ("forbidden", "Forbidden: bot was blocked by the user"),
    12: ("forbidden", "Forbidden: user is deactivated"),
    13: ("bad", "Bad Request: chat not found"),
    14: ("bad", "Bad Request: something odd"),
    16: ("forbidden", "Forbidden: bot can't initiate conversation with a user"),
}


def fake_session():
    from aiogram.client.session.base import BaseSession
    from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
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
            refuse = REFUSE.get(getattr(method, "chat_id", None))
            if refuse:
                kind, message = refuse
                raise (TelegramForbiddenError if kind == "forbidden" else TelegramBadRequest)(method=method,
                                                                                              message=message)
            return Message(message_id=len(self.calls), date=datetime.now(timezone.utc),
                           chat=Chat(id=getattr(method, "chat_id", 1) or 1, type="private"), text="x")

    return S()


async def main() -> int:
    from aiogram import Bot
    from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
    from aiogram.methods import SendMessage

    from bot.utils import reach
    from bot.utils.activity import LAST_SEEN_KEY
    from bot.utils.crm import redis
    from webapp import panel

    m = SendMessage(chat_id=1, text="x")
    check("Telegram's refusals sorted: blocked, deleted account, never started (two wordings), anything else",
          reach.why_of(TelegramForbiddenError(method=m, message="Forbidden: bot was blocked by the user")) == "blocked"
          and reach.why_of(TelegramForbiddenError(method=m, message="Forbidden: user is deactivated")) == "deleted"
          and reach.why_of(TelegramBadRequest(method=m, message="Bad Request: chat not found")) == "not_started"
          and reach.why_of(TelegramForbiddenError(method=m, message="Forbidden: bot can't initiate conversation "
                                                                    "with a user")) == "not_started"
          and reach.why_of(TelegramBadRequest(method=m, message="Bad Request: message is too long")) is None
          and reach.why_of(RuntimeError("down")) is None)

    r = redis()
    await r.delete(reach.KEY, LAST_SEEN_KEY)
    await reach.mark({1: "blocked", 2: "not_started"}, at=time.time() - 60)
    await r.hset(LAST_SEEN_KEY, mapping={"2": int(time.time()), "1": int(time.time()) - 3600})
    check("out of reach: still those the bot has not seen since; one seen after (started the new bot) is back",
          await reach.out() == {1: "blocked"} and not await r.hexists(reach.KEY, "2"))
    await r.delete(reach.KEY, LAST_SEEN_KEY)

    # the job: every refusal written down with its reason, the lasting ones marked out of reach
    s = fake_session()
    real_new_bot = panel.new_bot
    panel.new_bot = lambda: Bot("8888548766:TEST", session=s)
    try:
        await panel._broadcast_job("rt1", [(11, "uz"), (12, "uz"), (13, "ru"), (14, "en"), (15, "uz"), (16, "uz")],
                                   {"uz": "Salom"})
    finally:
        panel.new_bot = real_new_bot
    data = await r.hgetall(panel.BROADCAST_KEY.format(id="rt1"))
    missed = await r.hgetall(panel.MISSED_KEY.format(id="rt1"))
    check("the counts: 1 sent, 2 blocked (blocked + deleted), 2 never started the bot, 1 other error",
          (data.get("sent"), data.get("blocked"), data.get("not_started"), data.get("failed")) == ("1", "2", "2", "1"),
          data)
    check("...who and why kept for the panel's page (another error with Telegram's words)",
          missed == {"11": "blocked", "12": "deleted", "13": "not_started", "16": "not_started",
                     "14": "failed:Bad Request: something odd"}, missed)
    check("...the lasting ones out of reach for the next broadcast, not the odd error",
          await reach.out() == {11: "blocked", 12: "deleted", 13: "not_started", 16: "not_started"})

    # the audience leaves them out, and says how many and why
    real_all = panel._audience_all

    async def fake_all(session, audience, lang):
        return [NS(id=i, language="uz") for i in (11, 12, 13, 14, 15, 16)]

    panel._audience_all = fake_all
    try:
        kept, left = await panel._audience_split(None, "nochecks", "any")
        check("the audience: the reachable ones (14, 15), the others counted by reason",
              [u.id for u in kept] == [14, 15] and left == {"not_started": 2, "blocked": 1, "deleted": 1}, (kept, left))
        kept, left = await panel._audience_split(None, "admins", "any")
        check("...'admins only' never filtered", len(kept) == 6 and not left)

        from webapp.main import app

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as owner:
            await owner.post("/login", data={"username": settings.web_admin_username,
                                             "password": settings.web_admin_password})
            # what /broadcast/send writes before the job starts
            await r.hset(panel.BROADCAST_KEY.format(id="rt1"), mapping={
                "total": 6, "status": "done", "started": int(time.time()), "audience": "Hali tekshiruv qilmaganlar",
                "lang": "Hamma tillar", "admin": "admin", "preview": "Salom"})
            await r.lpush(panel.BROADCAST_IDS, "rt1")
            page = (await owner.get("/broadcast?audience=nochecks")).text
            check("the page: 2 people it goes to, 4 left out and why",
                  '<b class="num">2</b> kishiga boradi' in page and "<b>4</b> kishi chiqarib tashlandi" in page
                  and "Yangi botni ochmagan (eski botda" in page and "tgan): 2 · " in page,
                  page[page.find("b-count"):][:600])
            check("...the history: the 'never started' count and the link to who",
                  'class="not_started">2<' in page and 'href="/broadcast/rt1/missed"' in page)
            await r.hset(LAST_SEEN_KEY, "13", int(time.time()) + 5)  # 13 started the new bot since
            missed_page = (await owner.get("/broadcast/rt1/missed")).text
            check("'Kimlarga yetmadi': everyone with the reason, never-started first, Telegram's words for the odd one",
                  missed_page.count('href="/users/') == 5 and "🚪 Yangi botni ochmagan" in missed_page
                  and "⛔ Botni bloklagan" in missed_page and "🗑 Telegram akkauntini" in missed_page
                  and "Boshqa xato: Bad Request: something odd" in missed_page
                  and missed_page.index("/users/13") < missed_page.index("/users/11"), missed_page[:400])
            check("...one who came back since marked '✅ qaytdi'", missed_page.count('pill-good">✅ qaytdi') == 1
                  and missed_page.index('pill-good">✅ qaytdi') > missed_page.index("/users/13"))
            r2 = await owner.get("/broadcast/nosuch/missed", follow_redirects=False)
            check("...an unknown broadcast: back to the list", r2.status_code == 303)
    finally:
        panel._audience_all = real_all
        await r.delete(reach.KEY, LAST_SEEN_KEY, panel.MISSED_KEY.format(id="rt1"), panel.BROADCAST_KEY.format(id="rt1"))
        await r.lrem(panel.BROADCAST_IDS, 0, "rt1")

    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    await r.aclose()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
