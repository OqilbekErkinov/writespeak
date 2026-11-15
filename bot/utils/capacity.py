"""How many checks are graded at once (the user, 2026-11-15: "at launch at least 100 at the same time, later
thousands" - the first version's fixed 6 was far too few).

A check is ~15-25 model calls over ~4 minutes and 22-37 thousand tokens a minute (measured 2026-11-15); what
limits how many run together is OpenAI's rate limit for the account (then: gpt-5 4M tokens/min, gpt-5-mini
10M - about 110-150 Writing checks at once), not this server. So the gate follows OpenAI itself:

- every OpenAI response carries the account's limit and what is left of it this minute (x-ratelimit-*
  headers, read by services/ai/openai_client.py's response hook): while any model is under HEADROOM of its
  tokens or requests, new checks wait for its window to reset;
- a "rate limit exceeded" 429 cuts the number allowed at once to ~70% of what was running (the account
  is busier than the headers said), which grows back by one every few seconds without 429s;
- over everything, a cap the owner sets on the panel's settings page (Redis runtime:max_parallel_checks,
  default settings.max_parallel_checks) - the server's own safety limit, changed without a deploy.

A check that has to wait keeps its place in a first-come queue, shown on its live card; none is refused or
failed for it. state() is what the panel shows (written to Redis by run_publisher).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field

from bot.config import settings

logger = logging.getLogger(__name__)

HEADROOM = 0.12  # stop admitting new checks when a model has under 12% of its tokens/requests left this window
MIN_LIMIT = 4  # never fewer allowed at once after 429s
GROW_EVERY = 5.0  # seconds without a 429 for one more allowed at once
FRESH = 90.0  # seconds a model's rate-limit reading counts
CAP_KEY = "runtime:max_parallel_checks"
STATE_KEY = "capacity:state"
CAP_REFRESH = 15.0


def _seconds(text: str | None) -> float:
    """OpenAI's reset times: "6m0s", "1.5s", "250ms" -> seconds."""
    if not text:
        return 0.0
    total = 0.0
    for value, unit in re.findall(r"([\d.]+)(ms|s|m|h)", text):
        total += float(value) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
    return total


@dataclass
class ModelRate:
    limit_tokens: int = 0
    remaining_tokens: int = 0
    limit_requests: int = 0
    remaining_requests: int = 0
    reset_at: float = 0.0  # monotonic time the tighter of the two windows refills
    seen_at: float = 0.0

    def short(self, now: float) -> bool:
        """Under HEADROOM of its tokens or requests, and its window not reset yet."""
        if now - self.seen_at > FRESH or now >= self.reset_at:
            return False
        tokens = self.limit_tokens and self.remaining_tokens < self.limit_tokens * HEADROOM
        requests = self.limit_requests and self.remaining_requests < self.limit_requests * HEADROOM
        return bool(tokens or requests)


@dataclass
class Governor:
    running: int = 0
    queue: list = field(default_factory=list)  # tickets waiting, first come first served
    rates: dict = field(default_factory=dict)  # model -> ModelRate
    aimd: float = float("inf")
    last_429: float = 0.0
    last_grow: float = 0.0
    cap: int = 0
    cap_read_at: float = -1e9
    changed: asyncio.Event | None = None
    rate_limited_total: int = 0

    # --- the limits -------------------------------------------------------------------------------------

    def _event(self) -> asyncio.Event:
        if self.changed is None:
            self.changed = asyncio.Event()
        return self.changed

    def _poke(self) -> None:
        self._event().set()

    async def refresh_cap(self) -> None:
        if time.monotonic() - self.cap_read_at < CAP_REFRESH and self.cap:
            return
        self.cap_read_at = time.monotonic()
        cap = settings.max_parallel_checks
        try:
            from bot.utils.crm import redis  # noqa: PLC0415

            raw = await redis().get(CAP_KEY)
            if raw and str(raw).isdigit() and int(raw) > 0:
                cap = int(raw)
        except Exception:  # noqa: BLE001 - the default stands
            pass
        self.cap = max(1, cap)

    def limit(self) -> int:
        now = time.monotonic()
        if self.aimd < float("inf") and now - self.last_429 > GROW_EVERY and now - self.last_grow > GROW_EVERY:
            self.aimd += 1
            self.last_grow = now
            if self.aimd >= self.cap:
                self.aimd = float("inf")
        return max(1, min(self.cap or settings.max_parallel_checks, int(min(self.aimd, 10 ** 9))))

    def openai_short(self) -> str | None:
        """The model that has (almost) no room left this minute, if any."""
        now = time.monotonic()
        for model, rate in self.rates.items():
            if rate.short(now):
                return model
        return None

    # --- what OpenAI says -------------------------------------------------------------------------------

    def observe(self, model: str | None, headers) -> None:
        """An OpenAI response's x-ratelimit-* headers (services/ai/openai_client.py)."""
        try:
            limit_tokens = int(headers.get("x-ratelimit-limit-tokens") or 0)
            limit_requests = int(headers.get("x-ratelimit-limit-requests") or 0)
            if not (limit_tokens or limit_requests):
                return
            now = time.monotonic()
            rate = self.rates.setdefault(model or "?", ModelRate())
            rate.limit_tokens, rate.limit_requests = limit_tokens, limit_requests
            rate.remaining_tokens = int(headers.get("x-ratelimit-remaining-tokens") or limit_tokens)
            rate.remaining_requests = int(headers.get("x-ratelimit-remaining-requests") or limit_requests)
            rate.reset_at = now + max(_seconds(headers.get("x-ratelimit-reset-tokens")),
                                      _seconds(headers.get("x-ratelimit-reset-requests")))
            rate.seen_at = now
            self._poke()
        except Exception:  # noqa: BLE001 - never breaks a call
            pass

    def rate_limited(self) -> None:
        """OpenAI said "rate limit exceeded": fewer at once, for a while."""
        self.rate_limited_total += 1
        self.last_429 = time.monotonic()
        before = self.aimd
        self.aimd = max(MIN_LIMIT, int(max(self.running, MIN_LIMIT) * 0.7))
        if before != self.aimd:
            logger.warning("OpenAI rate limit hit: at most %d checks at once for now (%d running)", self.aimd,
                           self.running)

    # --- the gate ---------------------------------------------------------------------------------------

    async def acquire(self, ticket: object) -> None:
        """Waits for this check's turn (first come, first served); its place: position(ticket)."""
        await self.refresh_cap()
        self.queue.append(ticket)
        try:
            while True:
                room = self.limit() - self.running
                place = self.queue.index(ticket)
                if place < room and self.openai_short() is None:
                    break
                event = self._event()
                event.clear()
                try:
                    await asyncio.wait_for(event.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
                await self.refresh_cap()
        finally:
            if ticket in self.queue:
                self.queue.remove(ticket)
        self.running += 1
        self._poke()

    def release(self) -> None:
        self.running = max(0, self.running - 1)
        self._poke()

    def position(self, ticket: object) -> int:
        """1-based place in the queue, 0 when not waiting."""
        return self.queue.index(ticket) + 1 if ticket in self.queue else 0

    def state(self) -> dict:
        now = time.monotonic()
        return {
            "running": self.running, "queued": len(self.queue), "limit": self.limit(), "cap": self.cap,
            "slowed": self.aimd < float("inf"), "rate_limited_total": self.rate_limited_total,
            "waiting_for": self.openai_short(),
            "models": {m: {"limit_tokens": r.limit_tokens, "remaining_tokens": r.remaining_tokens,
                           "limit_requests": r.limit_requests, "remaining_requests": r.remaining_requests,
                           "fresh": now - r.seen_at <= FRESH}
                       for m, r in self.rates.items()},
            "at": int(time.time()),
        }


governor = Governor()


def request_model(request) -> str | None:
    """The model a request to OpenAI names (JSON bodies; a file upload has none)."""
    try:
        body = json.loads(request.content or b"{}")
        return body.get("model")
    except Exception:  # noqa: BLE001
        return None


async def run_publisher() -> None:
    """The gate's state for the panel (webapp/checks_ops.py), every few seconds."""
    from bot.utils.crm import redis  # noqa: PLC0415

    while True:
        try:
            await governor.refresh_cap()
            await redis().set(STATE_KEY, json.dumps(governor.state()), ex=120)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.debug("Capacity state not published", exc_info=True)
        await asyncio.sleep(5)
