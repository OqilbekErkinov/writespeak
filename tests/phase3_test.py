"""The 2026-11-15 audit's third package - the web panel - offline: a THROWAWAY Postgres and Redis (tests run by
test_part2.sh), Telegram a fake. No /docs; a cross-site POST refused; a logged-out or signed-out-everywhere
cookie no longer works; failed logins counted per login name; a cabinet link works once; Excel never gets a
formula; secrets never reach the errors page; the stopped-checks page; a report resent and a check marked a
test; the search box.
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from bot.config import settings  # noqa: E402

settings.web_2fa = False  # signs in with the password alone: the two-step sign-in is tests/panel_access_test.py

fails: list[str] = []


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


STUDENT, TEACHER = 7_300_001, 7_300_002


async def login(client: httpx.AsyncClient, username=None, password=None):
    return await client.post("/login", data={"username": username or settings.web_admin_username,
                                             "password": password or settings.web_admin_password})


async def main() -> int:
    from aiogram import Bot
    from aiogram.client.session.base import BaseSession
    from aiogram.types import Chat, Message

    from bot.utils import crm
    from bot.utils.cabinet_auth import cabinet_login_url
    from db.database import get_session
    from db.models import CheckHold, Group, User, UserRole, UserStatus, WebAdmin, WritingSubmission
    from services.excel import safe
    from webapp import panel
    from webapp.main import app

    await crm.redis().flushdb()
    async with get_session() as s:
        s.add(User(id=STUDENT, full_name="Talaba Qidiruv", username="talaba_q", coin_balance=5, language="uz",
                   status=UserStatus.approved))
        s.add(User(id=TEACHER, full_name="Ustoz", coin_balance=0, language="uz", status=UserStatus.approved,
                   role=UserRole.teacher))
        s.add(WebAdmin(username="ustoz3", name="Ustoz", password_hash=panel.hash_password("pw3"), role="teacher"))
        await s.flush()
        s.add(Group(name="G", teacher_id=TEACHER))
        await s.commit()
    transport = httpx.ASGITransport(app=app)
    base = "https://testserver"

    def client():
        return httpx.AsyncClient(transport=transport, base_url=base)

    # 1. no API docs
    async with client() as anon:
        codes = [(await anon.get(p)).status_code for p in ("/docs", "/redoc", "/openapi.json")]
    check("no /docs, /redoc, /openapi.json", codes == [404, 404, 404], codes)

    # 2. a cross-site POST is refused; a same-origin one goes through
    async with client() as owner:
        await login(owner)
        r = await owner.post("/finance/entry", data={"kind": "expense", "category": "server", "amount": "1000"},
                             headers={"Origin": "https://evil.example"})
        check("a POST from another site's page: 403", r.status_code == 403, r.status_code)
        r = await owner.post("/finance/entry", data={"kind": "expense", "category": "server", "amount": "1000"},
                             headers={"Sec-Fetch-Site": "same-site"})
        check("a POST from the sibling domain (same-site): 403", r.status_code == 403, r.status_code)
        r = await owner.post("/finance/entry", data={"kind": "expense", "category": "server", "amount": "1000",
                                                     "back": "/finance"},
                             headers={"Origin": base, "Sec-Fetch-Site": "same-origin"})
        check("a same-origin POST goes through", r.status_code == 303 and "done=added" in r.headers["location"],
              r.headers.get("location"))

    # 3. sessions: a logged-out cookie, and everyone after "sign out everywhere"
    async with client() as a:
        await login(a)
        cookie = a.cookies.get("session")
        await a.get("/logout")
    async with client() as replay:
        replay.cookies.set("session", cookie)
        r = await replay.get("/", follow_redirects=False)
    check("a copied cookie after logout: signed out", r.status_code in (302, 303) and "/login" in r.headers["location"],
          r.status_code)
    async with client() as a, client() as b, client() as t:
        await login(a)
        await login(b)
        await login(t, "ustoz3", "pw3")
        check("two owner sign-ins and a teacher's work", (await b.get("/")).status_code == 200
              and (await t.get("/checks")).status_code == 200)
        r = await a.post("/settings/signout-all")
        rb = await b.get("/", follow_redirects=False)
        rt = await t.get("/checks", follow_redirects=False)
    check("sign out everywhere: every sign-in ends (the other owner's, the teacher's)",
          r.status_code == 303 and rb.status_code in (302, 303) and rt.status_code in (302, 303),
          (r.status_code, rb.status_code, rt.status_code))

    # 4. failed logins counted per login name (in Redis: a restart doesn't reset them)
    from webapp import sessions
    for _ in range(sessions.MAX_FAILS_LOGIN):
        await sessions.failed("1.2.3.4", "someone")
    check("failed logins: the login name is held back", await sessions.too_many_fails("5.6.7.8", "someone")
          and not await sessions.too_many_fails("5.6.7.8", "other"))

    # 5. a cabinet link works once
    url = cabinet_login_url(TEACHER)
    path = "/cabinet/login?" + url.split("?", 1)[1]
    async with client() as c1:
        first = await c1.get(path, follow_redirects=False)
    async with client() as c2:
        second = await c2.get(path, follow_redirects=False)
    check("a cabinet link: opens once, then 'expired'", first.headers.get("location") == "/cabinet/"
          and second.headers.get("location") == "/cabinet/expired", (first.headers.get("location"),
                                                                     second.headers.get("location")))

    # 6. Excel never gets a formula; 7. secrets never reach the errors page
    check("Excel: a name starting a formula is quoted", safe('=HYPERLINK("http://x","ok")').startswith("'")
          and safe("Ali") == "Ali" and safe(5) == 5)
    settings.openai_api_key = "sk-proj-" + "Ab3" * 15  # a real key's length (the test env's is "sk-dummy")
    leaked = f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/x.oga key {settings.openai_api_key}"
    clean = crm.redact(leaked)
    check("secrets redacted from an error", settings.telegram_bot_token not in clean
          and settings.openai_api_key not in clean, clean)

    # 8. the stopped-checks page
    now = datetime.now(timezone.utc)
    async with get_session() as s:
        s.add(CheckHold(id="a" * 32, user_id=STUDENT, chat_id=STUDENT, module="w", kind="writing_task2", coins=5,
                        payer_id=STUDENT, state="held"))
        s.add(CheckHold(id="b" * 32, user_id=STUDENT, chat_id=STUDENT, module="s", kind="speaking_part1", coins=6,
                        payer_id=STUDENT, state="refunded", reason="RuntimeError: model call failed",
                        created_at=now - timedelta(hours=1)))
        pdf = Path("/tmp/storage/phase3_report.pdf")
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"%PDF-1.4 test")
        sub = WritingSubmission(user_id=STUDENT, task_type="task2", prompt_text="Q", prompt_source="text",
                                answer_text="A", answer_source="text", original_file_paths={}, overall_band=6.0,
                                criteria_scores={}, annotations=[], vocabulary_json=[], feedback_pdf_path=str(pdf),
                                report_data={"cost": {"total_usd": 0.21}, "paid_with": {"mode": "coins"}})
        s.add(sub)
        await s.commit()
        sid = sub.id
    async with client() as owner, client() as teacher:
        await login(owner)
        html = (await owner.get("/checks/stopped")).text
        check("stopped page: the running check, 'deploy waits', the failed one with its reason", "baholanmoqda" in html
              and "model call failed" in html and "Talaba Qidiruv" in html and "6 <span" in html)
        await login(teacher, "ustoz3", "pw3")
        r = await teacher.get("/checks/stopped", follow_redirects=False)
        check("a teacher login cannot open it", r.status_code in (302, 303) and r.headers["location"] == "/checks")

        # the checks list reads cost and payment out of report_data in SQL
        html = (await owner.get("/checks")).text
        check("checks list: the cost and the payment mode from Postgres", "$0.210" in html and "coin" in html)

        # 9. resend a report, mark a check a test
        sent = []

        class TgSession(BaseSession):
            async def close(self):
                pass

            async def stream_content(self, *a, **k):
                yield b""

            async def make_request(self, bot, method, timeout=None):
                sent.append(method)
                return Message(message_id=1, date=now, chat=Chat(id=STUDENT, type="private"), text="x").as_(bot)

        real_new_bot = panel.new_bot
        panel.new_bot = lambda: Bot("8888548766:TEST", session=TgSession())
        try:
            r = await owner.post(f"/checks/w/{sid}/resend", data={"back": "/checks"})
        finally:
            panel.new_bot = real_new_bot
        check("resend: the PDF goes to the student, in their language", "done=resent" in r.headers["location"]
              and any(type(m).__name__ == "SendDocument" and m.chat_id == STUDENT for m in sent), r.headers["location"])
        r = await owner.post(f"/checks/w/{sid}/test", data={"back": "/checks"})
        async with get_session() as s:
            marked = (await s.get(WritingSubmission, sid)).is_test
        check("a check marked a test", "done=marked_test" in r.headers["location"] and marked)
        r = await teacher.post(f"/checks/w/{sid}/test", data={"back": "/checks"}, follow_redirects=False)
        async with get_session() as s:
            still = (await s.get(WritingSubmission, sid)).is_test
        check("a teacher cannot mark one", still and r.headers.get("location") == "/checks")

        # 10. the search box
        html = (await owner.get("/search?q=@talaba_q")).text
        check("search by @username", "Talaba Qidiruv" in html and str(STUDENT) in html)
        html = (await owner.get(f"/search?q=%23{sid}")).text
        check("search by #number: the check", f"#{sid}" in html and "Writing Task 2" in html)
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
