"""The check gate (bot/utils/capacity.py, 2026-11-15: "100 at once at launch, thousands later") - offline, no
OpenAI, no Telegram; a throwaway Redis (tests run by test_part2.sh).

The rules: first come first served under the cap; a model short of its OpenAI limit holds new checks until its
window resets; a "rate limit exceeded" 429 cuts the number at once, which grows back; the cap set on the panel
is read without a restart. Then two loads: 300 checks sent at the same moment (none fails, never more than the
cap at once, the order kept), and 300 live cards (the edits they send Telegram stay near EDITS_PER_SECOND).
"""
import asyncio
import random
import sys
import time
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

fails: list[str] = []


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


def headers(limit_tokens, remaining_tokens, reset="1s", limit_requests=10000, remaining_requests=9000):
    return {"x-ratelimit-limit-tokens": str(limit_tokens), "x-ratelimit-remaining-tokens": str(remaining_tokens),
            "x-ratelimit-reset-tokens": reset, "x-ratelimit-limit-requests": str(limit_requests),
            "x-ratelimit-remaining-requests": str(remaining_requests), "x-ratelimit-reset-requests": "6ms"}


async def main() -> int:
    from bot.utils import capacity
    from bot.utils.capacity import Governor, _seconds
    from bot.utils.crm import redis

    await redis().flushdb()
    check("reset times read", (_seconds("6m0s"), _seconds("1.5s"), _seconds("250ms"), _seconds("1h2m")) ==
          (360.0, 1.5, 0.25, 3720.0))

    # the cap, first come first served
    g = Governor()
    g.cap, g.cap_read_at = 3, time.monotonic() + 3600
    order, gate = [], asyncio.Event()

    async def job(i):
        await g.acquire(f"t{i}")
        order.append(i)
        await gate.wait()
        g.release()

    tasks = [asyncio.create_task(job(i)) for i in range(6)]
    await asyncio.sleep(0.05)
    check("cap 3: three run, three wait in order", order == [0, 1, 2] and g.running == 3
          and [g.position(f"t{i}") for i in (3, 4, 5)] == [1, 2, 3], (order, g.running))
    gate.set()
    await asyncio.gather(*tasks)
    check("...then the rest, in the order they came", order == [0, 1, 2, 3, 4, 5] and g.running == 0, order)

    # OpenAI's own limit: a model under its headroom holds new checks until its window resets
    g = Governor()
    g.cap, g.cap_read_at = 100, time.monotonic() + 3600
    g.observe("gpt-5", headers(4_000_000, 100_000, reset="1.2s"))  # 2.5% left
    started = time.monotonic()
    await g.acquire("x")
    waited = time.monotonic() - started
    check("a model nearly out of tokens: the check waits for its window (~1.2 s), then runs", 1.0 <= waited <= 3.0
          and g.running == 1, round(waited, 2))
    g.release()
    g.observe("gpt-5", headers(4_000_000, 3_000_000))
    started = time.monotonic()
    await g.acquire("y")
    check("plenty left: no wait", time.monotonic() - started < 0.2)
    g.release()

    # a 429 "rate limit exceeded": fewer at once, growing back
    g = Governor()
    g.cap, g.cap_read_at = 100, time.monotonic() + 3600
    g.running = 40
    g.rate_limited()
    check("a 429 with 40 running: 28 at once now", g.limit() == 28, g.limit())
    capacity.GROW_EVERY = 0.05
    for _ in range(5):
        await asyncio.sleep(0.06)
        g.limit()
    check("...growing back without 429s", 28 < g.limit() <= 34, g.limit())
    capacity.GROW_EVERY = 5.0

    # the panel's cap, read without a restart
    await redis().set(capacity.CAP_KEY, "250")
    g = Governor()
    await g.refresh_cap()
    check("the panel's cap is read (Redis runtime key)", g.cap == 250 and g.limit() == 250, g.cap)
    await redis().delete(capacity.CAP_KEY)

    # 300 checks sent at once, cap 150, "OpenAI" reporting its limits on every call
    g = Governor()
    g.cap, g.cap_read_at = 150, time.monotonic() + 3600
    peak, done, finished_order = 0, [], []
    remaining = {"t": 4_000_000}

    async def check_run(i):
        nonlocal peak
        await asyncio.sleep(random.random() * 0.02)  # they arrive within a moment
        await g.acquire(i)
        peak = max(peak, g.running)
        try:
            for _ in range(5):  # five "model calls"
                await asyncio.sleep(random.uniform(0.005, 0.02))
                remaining["t"] = max(0, remaining["t"] - 6000)
                g.observe("gpt-5", headers(4_000_000, remaining["t"], reset="0.3s"))
            finished_order.append(i)
        finally:
            g.release()
        done.append(i)

    async def refill():  # the minute window resetting
        while len(done) < 300:
            await asyncio.sleep(0.3)
            remaining["t"] = 4_000_000

    started = time.monotonic()
    await asyncio.gather(refill(), *[check_run(i) for i in range(300)])
    took = time.monotonic() - started
    check("300 at once: every one graded, none failed", sorted(done) == list(range(300)), len(done))
    check("...never more than the cap at once", peak <= 150, peak)
    check("...the gate let the full cap run (not a handful)", peak >= 140, peak)
    check("...and it took seconds, not minutes, for the queue to move", took < 20, round(took, 1))

    # 300 live cards: the edits sent to Telegram stay near EDITS_PER_SECOND in all
    from bot.utils import progress
    edits = []

    class Msg:
        def __init__(self, n):
            self.chat = NS(id=n)
            self.message_id = n

        async def edit_text(self, text, reply_markup=None):
            edits.append(time.monotonic())

    progress.TICK, progress.FIRST = 0.4, 0.15  # a fast clock for the test: EDITS_PER_SECOND scaled the same way
    progress.EDITS_PER_SECOND = 80.0  # = 8/s at the real TICK of 4 s, ten times faster here
    cards = [progress.begin(Msg(10_000 + i), "uz", "writing") for i in range(300)]
    await asyncio.sleep(6.0)
    for c in cards:
        await progress.end(c)
    window = [t for t in edits if edits and t >= edits[0] + 1.0]
    rate = len(window) / max(0.1, (edits[-1] - edits[0] - 1.0)) if window else 0
    check("300 live cards: edits paced near the limit (not 300 every tick)", 0 < rate <= 80 * 1.35, round(rate, 1))
    check("...every card still updates", len({id(c) for c in cards}) == 300 and len(edits) >= 300, len(edits))

    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
