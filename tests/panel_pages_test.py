"""The web panel's pages open with the sidebar (2026-11-14: the top bar became a sidebar, its sections in
groups): every admin page renders, the open page's group is open, a teacher sees only the checks. Offline - a
throwaway Postgres and Redis (tests run by test_part2.sh). With an argument, the pages are also saved there
as HTML (to look at in a browser):

    python tests/panel_pages_test.py [out_dir]
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from bot.config import settings  # noqa: E402

fails: list[str] = []
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else None


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


PAGES = {"/": "dashboard", "/users": "users", "/checks": "checks", "/payments": "payments", "/support": "support",
         "/groups": "groups", "/teachers": "teachers", "/questions": "questions", "/charts": "charts",
         "/broadcast": "broadcast", "/errors": "errors", "/settings": "settings", "/admins": "admins", "/audit": "audit",
         "/finance": "finance", "/finance?p=all": "finance_all", "/finance?p=prev": "finance_prev"}
GROUP = {"users": "users", "support": "users", "broadcast": "users", "checks": "checks", "questions": "checks",
         "charts": "checks", "teachers": "centers", "groups": "centers", "errors": "admin", "settings": "admin",
         "admins": "admin", "audit": "admin", "payments": "money", "finance": "money", "finance_all": "money",
         "finance_prev": "money"}


async def main() -> None:
    from db.database import get_session
    from db.models import WebAdmin
    from webapp import panel
    from webapp.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as owner:
        await owner.post("/login", data={"username": settings.web_admin_username,
                                         "password": settings.web_admin_password})
        for path, active in PAGES.items():
            r = await owner.get(path)
            html = r.text
            group = GROUP.get(active)
            opened = f'data-group="{group}" open' in html if group else True
            check(f"{path}: opens with the sidebar, no top bar; its group open", r.status_code == 200
                  and 'class="side"' in html and "topbar" not in html and opened
                  and html.count('data-group="') == 5, r.status_code)
            check(f"{path}: the fold button, a flyout title per group, full width", 'id="bzFold"' in html
                  and html.count('class="fly-h"') == 5 and ".wrap.wide" not in html and "max-width: 1440px" in html)
            if OUT:
                OUT.mkdir(parents=True, exist_ok=True)
                (OUT / f"{active}.html").write_text(html, encoding="utf-8")
        await finance_checks(owner)
        await checks_modals(owner)
    async with get_session() as session:
        session.add(WebAdmin(username="ustoz9", name="Ustoz", password_hash=panel.hash_password("pw9"), role="teacher"))
        await session.commit()
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as teacher:
        await teacher.post("/login", data={"username": "ustoz9", "password": "pw9"})
        html = (await teacher.get("/checks")).text
        check("a teacher: the checks only, no groups, no payments", 'class="side"' in html
              and 'data-group="' not in html and 'href="/payments"' not in html and "ustoz" in html)
        check("a teacher's marking window never carries the bot's bands", '"bot": null' in html.replace("&#34;", '"')
              or "data-mark" not in html)
        r = await teacher.get("/finance", follow_redirects=False)
        check("a teacher cannot open the finance page", r.status_code in (302, 303) and "/checks" in r.headers.get("location", ""))
        if OUT:
            (OUT / "teacher.html").write_text(html, encoding="utf-8")


async def finance_checks(owner: httpx.AsyncClient) -> None:
    """Money entered by hand counts on the finance page, a test payment never does, and an entry goes away."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from bot.utils.crm import redis
    from db.database import get_session
    from db.models import FinanceEntry, PaymentRequest, PaymentStatus, User

    today = (datetime.now(timezone.utc) + timedelta(hours=5)).date()
    await redis().hset(f"crm:spend:{today.isoformat()}", mapping={"writing": "0.5", "speaking": "0.25"})
    async with get_session() as session:
        session.add(User(id=5550001, full_name="Pul Test", coin_balance=40))
        await session.flush()
        now = datetime.now(timezone.utc)
        session.add(PaymentRequest(user_id=5550001, coins=50, amount=45000, status=PaymentStatus.approved, reviewed_at=now))
        session.add(PaymentRequest(user_id=5550001, coins=10, amount=10000, status=PaymentStatus.approved, reviewed_at=now,
                                   is_test=True))
        session.add(PaymentRequest(user_id=5550001, coins=25, amount=23000, status=PaymentStatus.pending))
        await session.commit()
    r = await owner.post("/finance/entry", data={"kind": "expense", "category": "server", "amount": "120 000",
                                                 "day": today.isoformat(), "note": "VPS", "back": "/finance?p=month"})
    check("an expense is added", r.status_code == 303 and "done=added" in r.headers["location"], r.headers.get("location"))
    r = await owner.post("/finance/entry", data={"kind": "income", "category": "cash", "amount": "30000", "back": "/finance"})
    check("an income is added", "done=added" in r.headers["location"])
    r = await owner.post("/finance/entry", data={"kind": "expense", "category": "nope", "amount": "5", "back": "/finance"})
    check("an unknown category is refused", "done=bad" in r.headers["location"])
    r = await owner.post("/finance/entry", data={"kind": "expense", "category": "ads", "amount": "abc", "back": "//evil"})
    check("a bad amount is refused, the back link stays on the page", r.headers["location"] == "/finance?done=bad",
          r.headers["location"])

    from webapp import finance
    async with get_session() as session:
        data = await finance.overview(session, "month")
    t = data["totals"]
    rate = data["rate"]
    check("revenue: the real payment and the hand income, never the test one", t["rev_pay"] == 45000 and t["rev_hand"] == 30000
          and t["n_test"] == 1 and t["test"] == 10000, t)
    check("AI cost from the ledger in so'm", abs(t["ai_usd"] - 0.75) < 1e-9 and round(t["ai"]) == round(0.75 * rate), t)
    check("profit = revenue - AI - other", round(t["profit"]) == round(75000 - 0.75 * rate - 120000), t["profit"])
    check("pending payments are shown apart", t["n_pending"] == 1 and t["pending"] == 23000, t)
    check("coins in the users' hands", data["coins"]["in_hands"] >= 40, data["coins"])
    check("the month table has this month", data["by_month"] and data["by_month"][0]["current"]
          and data["by_month"][0]["revenue"] == 75000, data["by_month"][:1])
    check("each check kind has a price row", len(data["units"]) == 6 and data["units"][1]["retail"] == 5000, data["units"][:2])

    html = (await owner.get("/finance?p=month")).text
    check("the page shows the entries and the totals", "VPS" in html and "Server va hosting" in html and "75 000" in html
          and "moneyChart" in html, "")
    r = await owner.get("/export/finance.xlsx")
    check("the Excel file downloads", r.status_code == 200 and r.content[:2] == b"PK", r.status_code)
    async with get_session() as session:
        entry = (await session.execute(select(FinanceEntry).where(FinanceEntry.note == "VPS"))).scalar_one()
    r = await owner.post(f"/finance/entry/{entry.id}/delete", data={"back": "/finance?p=all"})
    async with get_session() as session:
        gone = await session.get(FinanceEntry, entry.id)
    check("an entry is deleted", gone is None and "done=deleted" in r.headers["location"])
    if OUT:
        (OUT / "finance_filled.html").write_text((await owner.get("/finance?p=all")).text, encoding="utf-8")


async def checks_modals(owner: httpx.AsyncClient) -> None:
    """The checks page: the marking and listening windows instead of the inline form."""
    from db.database import get_session
    from db.models import SpeakingPart, SpeakingSubmission

    async with get_session() as session:
        session.add(SpeakingSubmission(user_id=5550001, part=SpeakingPart.part3, overall_band=6.0, question_text="Q",
                                       criteria_scores={"FC": 6, "LR": 6, "GRA": 5.5, "Pronunciation": 6},
                                       audio_file_path="/tmp/none.mp3"))
        await session.commit()
    html = (await owner.get("/checks")).text
    check("the checks page: one marking window, one listening window, no inline form", 'id="mkModal"' in html
          and 'id="auModal"' in html and 't-form' not in html and "t-open" in html and "a-open" in html)
    check("the admin's marking window carries the bot's bands", "&#34;GRA&#34;: 5.5" in html or '"GRA": 5.5' in html
          or "\\u0022GRA\\u0022: 5.5" in html, "")
    r = await owner.post("/checks/s/999999/teacher", data={"FC": "7", "LR": "7", "GRA": "7", "P": "x", "back": "/checks"})
    check("a bad band is refused", "done=bad" in r.headers["location"])
    if OUT:
        (OUT / "checks_filled.html").write_text(html, encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    sys.exit(1 if fails else 0)
