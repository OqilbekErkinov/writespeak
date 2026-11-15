"""2026-11-15, before opening to everyone: a button pressed during a deploy still works
(bot/middlewares/stale_callbacks.py); the backup topic in the admins' group and the panel's Zaxira card with
the passphrase behind a button (bot/utils/crm.py, webapp/panel.py); /healthz for an outside uptime monitor
(webapp/main.py); the opening-day message from the new bot or, to those who never started it, from the
previous one (bot/launch.py). Offline: fake Telegram sessions, a throwaway Postgres + Redis (test_part2.sh).
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

fails: list[str] = []


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


def fake_session(refuse=None):
    """A Bot API stand-in: `refuse(method)` returns an exception to raise, or None to answer."""
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
            if type(method).__name__ == "AnswerCallbackQuery":
                return True
            return Message(message_id=len(self.calls), date=datetime.now(timezone.utc),
                           chat=Chat(id=getattr(method, "chat_id", 1) or 1, type="private"),
                           text=getattr(method, "text", None) or "x")

    return S()


async def stale_buttons() -> None:
    from aiogram import Bot, Dispatcher, Router
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import AnswerCallbackQuery
    from aiogram.types import CallbackQuery, Chat, Message, Update, User

    from bot.middlewares.stale_callbacks import SkipStaleAnswers

    too_old = "Bad Request: query is too old and response timeout expired or query ID is invalid"

    def refuse(method):
        if isinstance(method, AnswerCallbackQuery):
            return TelegramBadRequest(method=method, message=too_old if method.callback_query_id != "x" else
                                      "Bad Request: something else")
        if getattr(method, "text", None) == "boom":
            return TelegramBadRequest(method=method, message="Bad Request: query is too old")
        return None

    def build(with_fix: bool):
        session = fake_session(refuse)
        bot = Bot("123456:TEST", session=session)
        if with_fix:
            bot.session.middleware(SkipStaleAnswers())
        seen = []
        router = Router()

        @router.callback_query()
        async def press(callback: CallbackQuery):
            await callback.answer()
            seen.append(callback.data)
            await callback.message.answer("next screen")

        dp = Dispatcher()
        dp.include_router(router)
        return bot, dp, seen, session

    def update(n):
        msg = Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=5, type="private"), text="x")
        return Update(update_id=n, callback_query=CallbackQuery(
            id=str(n), from_user=User(id=5, is_bot=False, first_name="A"), chat_instance="c", data="acc:topup",
            message=msg))

    bot, dp, seen, _ = build(False)
    try:
        await dp.feed_update(bot, update(1))
        raised = False
    except TelegramBadRequest:
        raised = True
    check("without the fix: a press during a deploy dies at callback.answer()", raised and seen == [])

    bot, dp, seen, session = build(True)
    await dp.feed_update(bot, update(2))
    check("with it: the late answer skipped, the handler runs and shows its screen", seen == ["acc:topup"]
          and any(type(c).__name__ == "SendMessage" and c.text == "next screen" for c in session.calls))
    try:
        await bot.answer_callback_query("x")
        other = False
    except TelegramBadRequest:
        other = True
    check("...another error on the answer is still raised", other)
    try:
        await bot.send_message(5, "boom")
        same_text_other_method = False
    except TelegramBadRequest:
        same_text_other_method = True
    check("...and the same words from another method too", same_text_other_method)


async def backup_topic_and_panel() -> None:
    from bot.utils import crm, crm_events
    from db.database import get_session
    from db.models import AdminAction
    from sqlalchemy import select
    from webapp.main import app

    check("a topic named «💾 Zaxira» (or Backup) is the backup topic", crm.topic_key("💾 Zaxira") == "backup"
          and crm.topic_key("Backup nusxalar") == "backup" and crm.topic_key("📊 Kunlik hisobot") == "daily")
    check("every topic has its intro (General's /crm_setup posts them)", set(crm.TOPICS) == set(crm_events.INTROS))

    status_path = Path(settings.file_storage_path).parent / "offsite_status.json"
    cfg_path = crm.config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as owner:
        await owner.post("/login", data={"username": settings.web_admin_username, "password": settings.web_admin_password})

        cfg_path.write_text(json.dumps({"chat_id": -1, "topics": {"errors": 3}}), encoding="utf-8")
        status_path.unlink(missing_ok=True)
        settings.backup_passphrase = ""
        html = (await owner.get("/settings")).text
        check("Zaxira card: no topic yet - says how to connect it", 'id="backup"' in html
              and "hali ulanmagan" in html and "/crm_setup" in html)
        check("...no passphrase on the server: said, no reveal button", "BACKUP_PASSPHRASE" in html
              and "Zaxira kalitini ko'rsatish" not in html)

        cfg_path.write_text(json.dumps({"chat_id": -1, "topics": {"errors": 3, "backup": 9}}), encoding="utf-8")
        time.sleep(0.01)
        settings.backup_passphrase = "s3cret-Passphrase-for-test"
        html = (await owner.get("/settings")).text
        check("topic connected, nothing sent yet: 'tonight'", "Hali yuborilmagan" in html)
        check("...the passphrase is not on the page until asked for", "s3cret-Passphrase-for-test" not in html
              and "Zaxira kalitini ko'rsatish" in html)

        status_path.write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "ok": True, "parts": 3,
                                           "bytes": 9 * 1024 * 1024, "full_at": datetime.now(timezone.utc).isoformat()}))
        html = (await owner.get("/settings")).text
        check("a fresh copy: ✅ when, how many files, size", "✅ Oxirgi yuborish" in html and "3 ta fayl" in html
              and "9.0 MB" in html)
        status_path.write_text(json.dumps({"at": "2026-11-01T00:50:00+00:00", "ok": True, "parts": 1, "bytes": 10}))
        html = (await owner.get("/settings")).text
        check("a copy older than a day: flagged", "bir kundan oshdi" in html)
        status_path.write_text(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "ok": False,
                                           "error": "Telegram faylni qabul qilmadi"}))
        html = (await owner.get("/settings")).text
        check("a failed copy: the reason shown", "xato bilan tugadi" in html and "qabul qilmadi" in html)

        r = await owner.post("/settings/backup-key")
        check("🔑 asked for: the passphrase shown", r.status_code == 200 and "s3cret-Passphrase-for-test" in r.text)
        async with get_session() as s:
            logged = (await s.execute(select(AdminAction).where(AdminAction.action == "backup_key_viewed"))).scalars().all()
        check("...and written in the audit log", len(logged) == 1)
        r = await owner.get("/settings/backup-key")
        check("...only by POST (a link or a prefetch never shows it)", r.status_code in (404, 405)
              and "s3cret" not in r.text)

    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as stranger:
        r = await stranger.post("/settings/backup-key", follow_redirects=False)
        check("not signed in: no passphrase", "s3cret" not in r.text and r.status_code in (302, 303, 401, 403))

    # /healthz: public, says ok or what is down
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as anyone:
        await crm.redis().set(crm.HEARTBEAT_KEY, str(int(time.time())))
        r = await anyone.get("/healthz")
        check("/healthz: the bot's heartbeat fresh, the DB answering: 200 ok", r.status_code == 200 and r.text == "ok"
              and r.headers.get("cache-control") == "no-store")
        await crm.redis().set(crm.HEARTBEAT_KEY, str(int(time.time()) - 600))
        r = await anyone.get("/healthz")
        check("...a stale heartbeat: 503 'down: bot'", r.status_code == 503 and r.text == "down: bot")
        await crm.redis().delete(crm.HEARTBEAT_KEY)
        r = await anyone.get("/healthz")
        check("...no heartbeat at all: 503", r.status_code == 503)
    settings.backup_passphrase = ""


async def launch_message() -> None:
    from aiogram import Bot
    from aiogram.exceptions import TelegramForbiddenError

    from bot import launch
    from bot.utils import checks_back, runtime
    from bot.utils.crm import redis
    from db.database import get_session
    from db.models import User, UserStatus

    NEW, OLD_ONLY, NOBODY, DENIED, TOLD_BACK, RU = 9_300_001, 9_300_002, 9_300_003, 9_300_004, 9_300_005, 9_300_006
    async with get_session() as s:
        for uid, status, lang in ((NEW, UserStatus.approved, "uz"), (OLD_ONLY, UserStatus.approved, "uz"),
                                  (NOBODY, UserStatus.approved, "uz"), (DENIED, UserStatus.denied, "uz"),
                                  (TOLD_BACK, UserStatus.approved, "uz"), (RU, UserStatus.pending, "ru")):
            s.add(User(id=uid, full_name=f"U{uid}", coin_balance=8, language=lang, status=status))
        await s.commit()
    ours = {NEW, OLD_ONLY, NOBODY, DENIED, TOLD_BACK, RU}

    def new_refuses(method):
        if getattr(method, "chat_id", None) in (OLD_ONLY, NOBODY):
            return TelegramForbiddenError(method=method, message="Forbidden: bot can't initiate conversation with a user")
        return None

    def old_refuses(method):
        if getattr(method, "chat_id", None) == NOBODY:
            return TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
        return None

    new_s, old_s = fake_session(new_refuses), fake_session(old_refuses)
    new_bot, old_bot = Bot("111:NEW", session=new_s), Bot("222:OLD", session=old_s)

    await runtime.set_testers({111})
    stats = await launch.run(True, new_bot, old_bot)
    check("the test window still on: refused, nothing sent", "refused" in stats and not new_s.calls and not old_s.calls)
    await runtime.set_testers(set())

    await redis().sadd(checks_back.TOLD_KEY, TOLD_BACK)  # already got "checks are back" from the panel's switch
    dry = await launch.run(False, new_bot, old_bot)
    check("a dry run sends nothing and counts", not new_s.calls and not old_s.calls and dry["todo"] >= 4)

    stats = await launch.run(True, new_bot, old_bot)
    to_new = {c.chat_id: c for c in new_s.calls if c.chat_id in ours and type(c).__name__ == "SendMessage"}
    to_old = {c.chat_id: c for c in old_s.calls if c.chat_id in ours}
    check("a user of the new bot: 'Bandzilla yana ishlayapti' from the new bot, the price in it",
          NEW in to_new and "yana ishlayapti" in to_new[NEW].text and "5 coin" in to_new[NEW].text and NEW not in to_old)
    check("one who never started the new bot: 'we've moved' from the old bot, with the button",
          OLD_ONLY in to_old and "@" + settings.telegram_bot_username in to_old[OLD_ONLY].text
          and to_old[OLD_ONLY].reply_markup.inline_keyboard[0][0].url.startswith("https://t.me/"))
    check("in their own language", RU in to_new and "снова работает" in to_new[RU].text)
    check("denied users and those the panel already told: skipped", DENIED not in to_new and DENIED not in to_old
          and TOLD_BACK not in to_new and TOLD_BACK not in to_old)
    check("unreachable by both: counted, not marked told", stats["missed"] >= 1
          and not await redis().sismember(launch.TOLD, NOBODY) and await redis().sismember(launch.TOLD, NEW))

    before_new, before_old = len(new_s.calls), len(old_s.calls)
    again = await launch.run(True, new_bot, old_bot)
    retried = [c.chat_id for c in new_s.calls[before_new:] + old_s.calls[before_old:]]
    check("run again: only the unreached are tried again", set(retried) == {NOBODY} and again["new_bot"] == 0
          and again["old_bot"] == 0, retried)


async def main() -> int:
    await stale_buttons()
    await backup_topic_and_panel()
    await launch_message()
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
