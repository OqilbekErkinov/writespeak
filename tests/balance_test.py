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
    r = crm.redis()
    for key in await r.keys("balance:*"):
        await r.delete(key)

    def reset():
        posted.clear()
        balance._paused.update(at=0.0)

    check("before any top-up: no OpenAI status, the summary asks for one",
          await balance.openai_status() is None and "kiritilmagan" in " ".join(await balance.summary_lines()))

    reset()
    await balance.set_openai(10)
    await balance.add_spent(4.5)
    check("$10 entered, $4.50 spent: $5.50 left, nothing said",
          abs((await balance.openai_status())["left"] - 5.5) < 1e-6 and not posted, posted)
    await balance.add_spent(0.6)
    check("...$4.90 left: the $5 message, in the errors topic", len(posted) == 1 and posted[0][0] == "errors"
          and "4.90" in posted[0][1] and "/balans" in posted[0][1], posted)
    await balance.add_spent(0.1)
    check("...said once", len(posted) == 1, posted)
    await balance.add_spent(2.0)
    check("...$2.80 left: the $3 message", len(posted) == 2 and "2.80" in posted[1][1] and "🟠" in posted[1][1], posted)
    await balance.add_spent(3.0)
    check("...spent past zero: the red message, checks not paused on an estimate",
          len(posted) == 3 and "🔴" in posted[2][1] and not await balance.credit_paused(), posted)

    reset()
    await balance.set_openai(10)
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
    resumed = await balance.set_openai(25)
    balance._paused.update(at=0.0)
    check("a top-up entered: checks open again (the caller tells the waiting users)",
          resumed and not await balance.credit_paused() and not await runtime.checks_paused_for(student))
    check("...entered again with nothing paused: nothing to reopen", not await balance.set_openai(25))

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
    check("the summary: OpenAI's estimate and Azure's hours", "OpenAI balansi" in lines and "Azure" in lines, lines)
    check("amounts: 20, $20, 12,5; not 'abc' or a minus",
          [balance.parse_amount(x) for x in ("20", "$20", "12,5", "abc", "-3")] == [20.0, 20.0, 12.5, None, None])

    for key in await r.keys("balance:*"):
        await r.delete(key)


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    sys.exit(1 if fails else 0)
