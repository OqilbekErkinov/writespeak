"""2026-11-16, the owner's choice for the admin panel (bot/utils/panel_access.py): each admin does only what the
owner ticked, and every sign-in needs a code from the bot in the login's own Telegram (teachers too), a browser
remembered for 30 days. Offline: a fake Bot API session, a throwaway Postgres + Redis (test_part2.sh).
"""
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from bot.config import settings  # noqa: E402

settings.web_2fa = True
fails: list[str] = []
OWNER_TG = 111  # ADMIN_TELEGRAM_IDS in test_part2.sh


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


def fake_session():
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
            return Message(message_id=len(self.calls), date=datetime.now(timezone.utc),
                           chat=Chat(id=getattr(method, "chat_id", 1) or 1, type="private"),
                           text=getattr(method, "text", None) or "x")

    return S()


SESSION = fake_session()


def bot():
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode

    return Bot("8888548766:TEST", session=SESSION, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def last_code(chat_id: int) -> str | None:
    for c in reversed(SESSION.calls):
        if getattr(c, "chat_id", None) == chat_id and "kirish kodi" in (getattr(c, "text", "") or ""):
            m = re.search(r"<code>(\d{6})</code>", c.text)
            return m.group(1) if m else None
    return None


def tg(uid: int, name: str):
    return SimpleNamespace(id=uid, full_name=name, username=name.lower())


async def clear_fails() -> None:
    from bot.utils.crm import redis

    async for key in redis().scan_iter("panel:fail:*"):
        await redis().delete(key)


def client():
    from webapp.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://panel")


async def signed_in(c) -> bool:
    r = await c.get("/users", follow_redirects=False)
    return r.status_code == 200


async def password(c, login: str, pw: str):
    return await c.post("/login", data={"username": login, "password": pw}, follow_redirects=False)


async def main() -> int:
    import webapp.main as wm
    from bot.utils import panel_access
    from bot.utils.crm import redis
    from db.database import get_session
    from db.models import AdminAction, PaymentRequest, PaymentStatus, User, UserStatus, WebAdmin
    from sqlalchemy import select

    async def with_fake_bot(send):
        try:
            return await send(bot())
        except Exception as e:  # noqa: BLE001
            print("   (fake bot call raised)", repr(e))
            return None

    async def with_fake_bot_ok(send):
        return await with_fake_bot(send) is not None or True

    wm._with_bot_result = with_fake_bot
    wm._with_bot = with_fake_bot_ok

    owner_login, owner_pw = settings.web_admin_username, settings.web_admin_password
    STUDENT = 9_400_001
    async with get_session() as s:
        s.add(User(id=STUDENT, full_name="Student", coin_balance=8, language="uz", status=UserStatus.approved))
        await s.commit()
        s.add(PaymentRequest(user_id=STUDENT, coins=10, amount=10_000, status=PaymentStatus.pending,
                             receipt_file_id="file-x"))
        await s.commit()
        pay_id = (await s.execute(select(PaymentRequest.id).where(PaymentRequest.user_id == STUDENT))).scalar_one()

    # --- 1. the owner, no Telegram linked yet ---------------------------------------------------------------
    async with client() as c:
        r = await password(c, owner_login, owner_pw)
        check("owner, right password, no Telegram yet: to the link step, not signed in",
              r.headers.get("location") == "/login/link" and not await signed_in(c))
        page = (await c.get("/login/link")).text
        m = re.search(r"start=pl_([A-Za-z0-9_-]+)", page)
        check("...the page has the one-time bot link and the code box", m is not None and 'name="code"' in page
              and f"t.me/{settings.telegram_bot_username}" in page, page[:300])
        token = m.group(1) if m else ""
        reply = await panel_access.link_from_bot(bot(), token, tg(999, "Stranger"))
        check("...the owner's login does not link to a Telegram outside ADMIN_TELEGRAM_IDS",
              "ADMIN_TELEGRAM_IDS" in reply and await panel_access.linked(owner_login) is None)
        reply = await panel_access.link_from_bot(bot(), token, tg(OWNER_TG, "Owner"))
        code = re.search(r"<code>(\d{6})</code>", reply)
        check("...a bot admin starts the bot through it: linked, the code in the same chat",
              "ulandi" in reply and code is not None and (await panel_access.linked(owner_login)).telegram_id == OWNER_TG)
        again = await panel_access.link_from_bot(bot(), token, tg(OWNER_TG, "Owner"))
        check("...the link works once", "eskirgan" in again or "allaqachon" in again, again)

        r = await c.post("/login/code", data={"code": "000000" if code.group(1) != "000000" else "111111"},
                         follow_redirects=False)
        check("a wrong code: refused, still not signed in", r.status_code == 401 and "Kod noto" in r.text
              and not await signed_in(c))
        r = await c.post("/login/code", data={"code": code.group(1), "remember": "1"}, follow_redirects=False)
        device = c.cookies.get(panel_access.DEVICE_COOKIE)
        check("the right code: signed in, this browser remembered", r.status_code == 303
              and r.headers.get("location") == "/" and await signed_in(c) and device)
        r = await c.post("/login/code", data={"code": code.group(1)}, follow_redirects=False)
        check("...the same code a second time does nothing", r.status_code == 401)

        await c.get("/logout")
        r = await password(c, owner_login, owner_pw)
        check("the remembered browser: the password alone signs in", r.headers.get("location") == "/"
              and await signed_in(c))
        async with get_session() as s:
            how = (await s.execute(select(AdminAction.details).where(AdminAction.action == "login")
                                   .order_by(AdminAction.id.desc()).limit(1))).scalar_one()
        check("...and the audit says how", "eslab qolingan" in how, how)

        # recovery codes, made by the owner
        r = await c.post("/security/recovery")
        recovery = re.findall(r"<code>([A-Z2-9]{5}-[A-Z2-9]{5})</code>", r.text)
        check("owner: 8 one-time recovery codes, shown once", r.status_code == 200 and len(recovery) == 8
              and await panel_access.recovery_left(owner_login) == 8)
        page = (await c.get("/admins")).text
        check("...not on the page again; 8 left", recovery[0] not in page and "<b>8</b> ta kod qolgan" in page)

    # --- 2. another browser: a code each time ---------------------------------------------------------------
    async with client() as c:
        n = len(SESSION.calls)
        r = await password(c, owner_login, owner_pw)
        check("a new browser: the code goes to the owner's Telegram", r.headers.get("location") == "/login/code?s=sent"
              and len(SESSION.calls) == n + 1 and SESSION.calls[-1].chat_id == OWNER_TG and not await signed_in(c))
        page = (await c.get("/login/code?s=sent")).text
        check("...the code page: sent to whom, resend, the recovery hint for the owner", "Owner" in page
              and "Kodni qayta yuborish" in page and "tiklash kod" in page)
        r = await c.post("/login/code/resend", follow_redirects=False)
        check("...resend at once: wait (30 s between codes)", r.headers.get("location") == "/login/code?s=wait")
        r = await c.post("/login/code", data={"code": recovery[0]}, follow_redirects=False)
        check("a recovery code instead of the Telegram one: signed in", r.status_code == 303 and await signed_in(c)
              and await panel_access.recovery_left(owner_login) == 7)

    async with client() as c:
        await password(c, owner_login, owner_pw)
        r = await c.post("/login/code", data={"code": recovery[0]}, follow_redirects=False)
        check("...that recovery code a second time: refused", r.status_code == 401 and not await signed_in(c))
        for _ in range(panel_access.MAX_TRIES - 2):
            await c.post("/login/code", data={"code": "999999"})
        r = await c.post("/login/code", data={"code": "999999"}, follow_redirects=False)
        check(f"{panel_access.MAX_TRIES} wrong codes: the password again", r.status_code == 401
              and "login va parolni qayta" in r.text)
        r = await c.post("/login/code", data={"code": last_code(OWNER_TG)}, follow_redirects=False)
        check("...even the right code no longer works", r.status_code == 401 and not await signed_in(c))
    await clear_fails()

    async with client() as c:
        await password(c, owner_login, owner_pw)
        code = last_code(OWNER_TG)
        async for key in redis().scan_iter("panel:2fa:*"):
            if ":link:" in key:
                continue
            raw = await redis().get(key)
            data = json.loads(raw)
            if data.get("code"):
                data["sent_at"] -= panel_access.CODE_TTL + 5
                await redis().set(key, json.dumps(data), ex=300)
        r = await c.post("/login/code", data={"code": code}, follow_redirects=False)
        check("a code older than 5 minutes: 'eskirgan'", r.status_code == 401 and "eskirgan" in r.text)
    await clear_fails()

    # --- 3. a sign-in from before the two-step sign-in is signed out -------------------------------------------
    async with client() as c:
        settings.web_2fa = False
        await password(c, owner_login, owner_pw)
        before = await signed_in(c)
        settings.web_2fa = True
        check("a sign-in made while WEB_2FA=false: signed out once the step is back on", before
              and not await signed_in(c))
    async with client() as c:
        await password(c, owner_login, owner_pw)
        await c.post("/login/code", data={"code": last_code(OWNER_TG)})
        signed = await signed_in(c)
        sid = None
        async for key in redis().scan_iter("panel:sid:*"):
            sid = key
        # the same session as one from before 2026-11-16: no "mfa" in the cookie - rebuilt by hand
        from itsdangerous import TimestampSigner
        import base64

        cookie = c.cookies.get("session")
        payload = json.loads(base64.b64decode(TimestampSigner(settings.web_session_secret).unsign(cookie)))
        payload.pop("mfa", None)
        old = base64.b64encode(json.dumps(payload).encode())
        c.cookies.set("session", TimestampSigner(settings.web_session_secret).sign(old).decode())
        check("a session from before 2026-11-16 (no code step in it): signed out", signed and sid
              and not await signed_in(c))

    # --- 4. an admin with ticks -------------------------------------------------------------------------------------
    async with client() as owner:
        await password(owner, owner_login, owner_pw)
        await owner.post("/login/code", data={"code": last_code(OWNER_TG), "remember": "1"})
        check("(owner signed in)", await signed_in(owner))
        r = await owner.post("/admins/create", data={"username": "ali", "name": "Ali"}, follow_redirects=True)
        pw = re.search(r'<code id="pw">([^<]+)</code>', r.text)
        ali_pw = pw.group(1) if pw else ""
        async with get_session() as s:
            ali = (await s.execute(select(WebAdmin).where(WebAdmin.username == "ali"))).scalar_one()
        check("a new admin with no ticks: view-only (perms '')", ali.perms == "" and ali_pw)
        check("a login from before 2026-11-16 (perms NULL): everything, as before",
              panel_access.perms_of(None) == set(panel_access.PERMS))

        async with client() as c:
            r = await password(c, "ali", ali_pw)
            page = (await c.get("/login/link")).text
            token = re.search(r"start=pl_([A-Za-z0-9_-]+)", page).group(1)
            n = len(SESSION.calls)
            reply = await panel_access.link_from_bot(bot(), token, tg(555, "Ali"))
            told = [x for x in SESSION.calls[n:] if getattr(x, "chat_id", None) == OWNER_TG]
            check("an admin links their own Telegram; the owner is told", "ulandi" in reply and told
                  and "ali" in told[0].text and "555" in told[0].text)
            await c.post("/login/code", data={"code": re.search(r"<code>(\d{6})</code>", reply).group(1)})
            check("...and signs in with the code", await signed_in(c))

            for label, method, path, data in (
                    ("broadcast page", "get", "/broadcast", None),
                    ("approve a payment", "post", f"/payments/{pay_id}/approve", {}),
                    ("coins by hand", "post", f"/users/{STUDENT}/coins", {"delta": "5"}),
                    ("block a user", "post", f"/users/{STUDENT}/block", {"action": "block"}),
                    ("finance page", "get", "/finance", None),
                    ("Excel export", "get", "/export/users.xlsx", None),
                    ("a new Practice question", "post", "/questions/new", {"module": "writing_task2", "question_text": "x"}),
            ):
                r = await (c.get(path, follow_redirects=False) if method == "get"
                           else c.post(path, data=data, follow_redirects=False))
                check(f"view-only admin: {label} refused", r.status_code == 303
                      and r.headers.get("location", "").startswith("/?denied="), (r.status_code, r.headers.get("location")))
            async with get_session() as s:
                p = await s.get(PaymentRequest, pay_id)
                u = await s.get(User, STUDENT)
            check("...nothing happened: the payment pending, the balance and status as they were",
                  p.status == PaymentStatus.pending and u.coin_balance == 8 and u.status == UserStatus.approved)
            page = (await c.get("/?denied=payments")).text
            check("...the note says why", "ruxsatingiz yo" in page and "💳" in page)
            ok_pages = [path for path in ("/users", "/checks", "/support", f"/users/{STUDENT}", "/payments")
                        if (await c.get(path, follow_redirects=False)).status_code == 200]
            check("...looking is open: users, checks, support, a profile, payments", len(ok_pages) == 5, ok_pages)
            page = (await c.get("/payments")).text
            check("...the payments page shows no approve button to them", f"/payments/{pay_id}/approve" not in page)
            page = (await c.get(f"/users/{STUDENT}")).text
            check("...the profile: no coin or block button", f"/users/{STUDENT}/coins" not in page
                  and f"/users/{STUDENT}/block" not in page)

            r = await owner.post(f"/admins/{ali.id}/perms", data={"role": "admin", "perms": ["coins", "payments"]},
                                 follow_redirects=False)
            check("owner ticks 🟡 Coin + 💳 To'lovlar for ali", r.headers.get("location") == "/admins?done=perms")
            r = await c.post(f"/users/{STUDENT}/coins", data={"delta": "10"}, follow_redirects=False)
            check("...ali adds 10 coins: done at once", "done=added" in r.headers.get("location", ""))
            r = await c.post(f"/users/{STUDENT}/coins", data={"delta": "60"}, follow_redirects=False)
            check(f"...60 coins: over the {panel_access.COIN_CAP} cap - only the owner",
                  "done=cap" in r.headers.get("location", ""))
            r = await owner.post(f"/users/{STUDENT}/coins", data={"delta": "60"}, follow_redirects=False)
            check("...the owner adds 60: done", "done=added" in r.headers.get("location", ""))
            async with get_session() as s:
                u = await s.get(User, STUDENT)
            check("...the balance: 8 + 10 + 60", u.coin_balance == 78, u.coin_balance)
            page = (await c.get("/payments")).text
            check("...now the approve button is there for ali", f"/payments/{pay_id}/approve" in page)
            r = await c.get("/broadcast", follow_redirects=False)
            check("...still no broadcast (not ticked)", r.status_code == 303)

            check("bot: ali's Telegram may approve payments in the bot (ticked)",
                  await panel_access.telegram_may(555, "payments"))
            await owner.post(f"/admins/{ali.id}/perms", data={"role": "admin", "perms": ["coins"]})
            check("...untick 💳: the bot's approve refuses ali's Telegram too",
                  not await panel_access.telegram_may(555, "payments"))
            check("...a bot admin linked to no login, or to the owner's: as before",
                  await panel_access.telegram_may(777, "payments") and await panel_access.telegram_may(OWNER_TG, "payments"))

            # unlink: ali is signed out and links again next time
            r = await owner.post(f"/admins/{ali.id}/unlink", follow_redirects=False)
            check("owner unlinks ali's Telegram: ali signed out, no link left", r.headers.get("location") ==
                  "/admins?done=unlinked" and not await signed_in(c) and await panel_access.linked("ali") is None)

        # a remembered browser forgotten by a new password
        async with client() as c:
            await password(c, "ali", ali_pw)
            token = re.search(r"start=pl_([A-Za-z0-9_-]+)", (await c.get("/login/link")).text).group(1)
            reply = await panel_access.link_from_bot(bot(), token, tg(556, "Ali2"))
            await c.post("/login/code", data={"code": re.search(r"<code>(\d{6})</code>", reply).group(1),
                                              "remember": "1"})
            dev = c.cookies.get(panel_access.DEVICE_COOKIE)
            ok_before = await panel_access.device_ok("ali", dev)
            await owner.post(f"/admins/{ali.id}/reset")
            check("a new password for ali: their remembered browser is forgotten too",
                  ok_before and not await panel_access.device_ok("ali", dev) and not await signed_in(c))
            check("...a device of one login never skips another login's code",
                  not await panel_access.device_ok(owner_login, dev))

        # the role: teacher
        await owner.post(f"/admins/{ali.id}/perms", data={"role": "teacher", "perms": ["coins"]})
        async with get_session() as s:
            ali = await s.get(WebAdmin, ali.id)
        check("ali made a teacher: role teacher, no ticks kept", ali.role == "teacher" and ali.perms == "")

        # the teacher signs in with a code too
        async with client() as c:
            r = await owner.post(f"/admins/{ali.id}/reset", follow_redirects=True)
            ali_pw = re.search(r'<code id="pw">([^<]+)</code>', r.text).group(1)
            n = len(SESSION.calls)
            r = await password(c, "ali", ali_pw)
            check("a teacher login: a code too (to the linked Telegram)", r.headers.get("location") == "/login/code?s=sent"
                  and last_code(556) is not None and len(SESSION.calls) == n + 1)
            await c.post("/login/code", data={"code": last_code(556)})
            r = await c.get("/users", follow_redirects=False)
            check("...signed in, the checks page only", (await c.get("/checks")).status_code == 200
                  and r.status_code == 303 and r.headers.get("location") == "/checks")

        # sign out everywhere: remembered browsers too
        owner_dev = owner.cookies.get(panel_access.DEVICE_COOKIE)
        ok_before = await panel_access.device_ok(owner_login, owner_dev)
        await owner.post("/settings/signout-all")
        check("'sign out everywhere': the owner's remembered browser forgotten too",
              ok_before and not await panel_access.device_ok(owner_login, owner_dev))

    # --- 5. the break-glass: WEB_2FA=false ------------------------------------------------------------------------
    settings.web_2fa = False
    async with client() as c:
        r = await password(c, owner_login, owner_pw)
        check("WEB_2FA=false: the password alone signs in", r.headers.get("location") == "/" and await signed_in(c))
    settings.web_2fa = True

    # --- 6. the bot's /start pl_<token> goes to the link ------------------------------------------------------------
    from aiogram import Dispatcher
    from aiogram.types import Chat, Message, Update
    from aiogram.types import User as TgUser

    from bot.handlers import start as start_handlers

    await panel_access.unlink(owner_login)
    async with client() as c:
        await password(c, owner_login, owner_pw)
        token = re.search(r"start=pl_([A-Za-z0-9_-]+)", (await c.get("/login/link")).text).group(1)
        dp = Dispatcher()
        dp.include_router(start_handlers.router)
        b = bot()
        n = len(SESSION.calls)
        msg = Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=OWNER_TG, type="private"),
                      from_user=TgUser(id=OWNER_TG, is_bot=False, first_name="Owner"), text=f"/start pl_{token}")
        await dp.feed_update(b, Update(update_id=1, message=msg), lang="uz")
        answers = [x.text for x in SESSION.calls[n:] if type(x).__name__ == "SendMessage"]
        check("the bot: /start pl_<token> links and answers with the code (nothing else)",
              len(answers) == 1 and "ulandi" in answers[0] and "<code>" in answers[0], answers)
        r = await c.post("/login/code", data={"code": last_code(OWNER_TG)}, follow_redirects=False)
        check("...and that code signs the browser in", r.status_code == 303 and await signed_in(c))

    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    from bot.utils.crm import redis as _r

    await _r().aclose()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
