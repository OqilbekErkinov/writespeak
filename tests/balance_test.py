"""The OpenAI and Azure balances (bot/utils/balance.py, the user 2026-11-14): the group hears at $5, $3 and
$0 and when OpenAI refuses for want of credit; checks pause for everyone but the admins until a top-up is
entered; Azure's monthly hours the same way. Offline - a throwaway Redis (tests run by test_part2.sh), the
group's messages caught instead of sent:

    python tests/balance_test.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from bot.config import settings  # noqa: E402
from bot.utils import balance, crm, runtime  # noqa: E402
from services.ai import openai_client, usage  # noqa: E402

fails: list[str] = []


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


posted: list[tuple[str, str]] = []


async def fake_post(topic: str, text: str = "", **kwargs) -> None:
    posted.append((topic, text))


async def main() -> None:
    crm.post = fake_post

    async def rate() -> float:
        return 12500.0
    crm.usd_rate = rate  # so'm per dollar: $5 = 62 500, $3 = 37 500
    r = crm.redis()
    for key in await r.keys("balance:*"):
        await r.delete(key)

    def reset():
        posted.clear()
        balance._paused.update(at=0.0)

    check("before any top-up: no OpenAI status, the summary asks for one",
          await balance.openai_status() is None and "kiritilmagan" in " ".join(await balance.summary_lines()))

    reset()
    await balance.set_openai(125000)
    await balance.add_spent(4.5)
    check("125 000 so'm entered, $4.50 spent (56 250 so'm): 68 750 left, nothing said",
          abs((await balance.openai_status())["left"] - 68750) < 1e-6 and not posted, posted)
    await balance.add_spent(0.6)
    check("...61 250 so'm left (under $5): the first message, in so'm, in the errors topic",
          len(posted) == 1 and posted[0][0] == "errors" and "61 250 so'm" in posted[0][1]
          and "/balans 70000" in posted[0][1], posted)
    await balance.add_spent(0.1)
    check("...said once", len(posted) == 1, posted)
    await balance.add_spent(2.0)
    check("...35 000 so'm left (under $3): the second message", len(posted) == 2 and "35 000 so'm" in posted[1][1]
          and "🟠" in posted[1][1], posted)
    await balance.add_spent(3.0)
    check("...spent past zero: the red message, checks not paused on an estimate",
          len(posted) == 3 and "🔴" in posted[2][1] and not await balance.credit_paused(), posted)

    reset()
    await balance.set_openai(125000)
    await balance.add_spent(8.0)
    check("one big spend past $5 and $3: one message, for $3", len(posted) == 1 and "🟠" in posted[0][1], posted)
    await balance.add_spent(0.5)
    check("...and no $5 message after it", len(posted) == 1, posted)

    reset()
    student, admin = 123456789, next(iter(settings.admin_ids))
    response = httpx.Response(429, json={"error": {"code": "insufficient_quota", "message": "You exceeded your quota"}},
                              request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))
    await openai_client._watch_credit(response)
    await openai_client._watch_credit(response)
    check("OpenAI's insufficient_quota: checks paused, the group told once",
          await balance.credit_paused() and len(posted) == 1 and "🚨" in posted[0][1], posted)
    check("...for students, never for the admins", await runtime.checks_paused_for(student)
          and not await runtime.checks_paused_for(admin))
    busy = httpx.Response(429, json={"error": {"code": "rate_limit_exceeded"}},
                          request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"))
    await r.delete(balance.EMPTY_KEY)
    balance._paused.update(at=0.0)
    await openai_client._watch_credit(busy)
    check("...a plain rate limit pauses nothing", not await balance.credit_paused())
    await openai_client._watch_credit(response)
    resumed = await balance.set_openai(300000)
    balance._paused.update(at=0.0)
    check("a top-up entered: checks open again (the caller tells the waiting users)",
          resumed and not await balance.credit_paused() and not await runtime.checks_paused_for(student))
    check("...entered again with nothing paused: nothing to reopen", not await balance.set_openai(300000))

    reset()
    await balance.set_azure_hours(1.0)
    usage.record_azure(1900)  # 0.53 hours of a 1-hour allowance
    await crm.flush_spent()
    check("Azure: past 1 hour and half an hour left at once - one message, for half an hour",
          len(posted) == 1 and "🟡" in posted[0][1] and "daqiqa" in posted[0][1], posted)
    usage.record_azure(1800)
    await crm.flush_spent()
    check("...the allowance used up: the red message", len(posted) == 2 and "🔴" in posted[1][1], posted)
    await balance.set_azure_hours(0)
    usage.record_azure(600)
    await crm.flush_spent()
    status = await balance.azure_status()
    check("...0 hours = a paid tier, no limit: counted, nothing said", len(posted) == 2 and status["left"] is None
          and status["used"] > 1.1, (posted, status))

    lines = " ".join(await balance.summary_lines())
    check("the summary: OpenAI's estimate in so'm and Azure's hours", "300 000 so'm" in lines and "Azure" in lines,
          lines)
    check("so'm: 70000, 70 000, 70.000, 70,000 so'm; not 'abc' or a minus",
          [balance.parse_som(x) for x in ("70000", "70 000", "70.000", "70,000 so'm", "abc", "-3")]
          == [70000, 70000, 70000, 70000, None, None])
    check("Azure's hours: 5, 2,5; not 'abc'", [balance.parse_amount(x) for x in ("5", "2,5", "abc")] == [5, 2.5, None])

    from webapp.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver") as owner:
        await owner.post("/login", data={"username": settings.web_admin_username,
                                         "password": settings.web_admin_password})
        page = await owner.get("/settings")
        saved = await owner.post("/settings/balance", data={"kind": "openai", "amount": "80 000"})
        status = await balance.openai_status()
    check("the panel: the settings page shows the balance in so'm; 80 000 entered there",
          page.status_code == 200 and "300 000 so'm" in page.text and saved.status_code == 303
          and status["entered"] == 80000, (page.status_code, saved.status_code, status))

    for key in await r.keys("balance:*"):
        await r.delete(key)
    await dashboard(r)


async def dashboard(r) -> None:
    """2026-11-14 (the user): the dashboard's AI cost is every OpenAI dollar (the ledger, tests and the first
    weeks included), its revenue only real money - a payment approved to try the bot out is no revenue."""
    from datetime import datetime, timezone

    from db.database import get_session
    from db.models import PaymentKind, PaymentRequest, PaymentStatus, User
    from webapp.main import app

    today = crm.local_now().strftime("%Y-%m-%d")
    await r.hset(f"crm:spend:{today}", mapping={"writing": 1.0, "tests": 0.5})
    await r.hset("crm:spend:2026-09-01", mapping={"early": 0.25})
    async with get_session() as session:
        session.add(User(id=777000222, full_name="Payer"))
        await session.flush()
        now = datetime.now(timezone.utc)
        real = PaymentRequest(user_id=777000222, coins=10, amount=5000, status=PaymentStatus.approved,
                              kind=PaymentKind.credits, reviewed_at=now)
        test = PaymentRequest(user_id=777000222, coins=100, amount=50000, status=PaymentStatus.approved,
                              kind=PaymentKind.credits, reviewed_at=now, is_test=True)
        session.add_all([real, test])
        await session.commit()
        test_id = test.id
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver") as owner:
        await owner.post("/login", data={"username": settings.web_admin_username,
                                         "password": settings.web_admin_password})
        page = (await owner.get("/")).text
        listed = (await owner.get("/payments?status=approved")).text
        await owner.post(f"/payments/{test_id}/test")
        page2 = (await owner.get("/")).text
    check("dashboard: revenue is real money only (the 50 000 test payment left out)",
          "jami 5 000 so&#39;m" in page and "jami 55 000" not in page, page[page.find("Tushum"):][:600])
    check("...AI cost from the ledger, tests and the first weeks included: all-time $1.75",
          "($1.75)" in page and "$1.50 · jami" in page, "")
    check("...the payments page marks the test payment, and it can be made real again",
          "Sinov deb belgilash" in listed and ">test<" in listed and "jami 55 000 so&#39;m" in page2,
          (listed.count("Sinov deb"), listed.count(">test<"), page2[page2.find("Tushum"):][:300]))
    await r.delete(f"crm:spend:{today}", "crm:spend:2026-09-01")


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    sys.exit(1 if fails else 0)
