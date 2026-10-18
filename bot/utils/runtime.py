"""Settings the admins change from the web panel without a restart (since
2026-10-18). For now: the test window - who may run checks while everyone
else gets quota.paused (bot/utils/quota.py).

The value lives in Redis and overrides CHECKS_ONLY_FOR from .env: no key ->
the .env value; "" -> the window is off; "1,2,3" -> on, for those Telegram
IDs (the admins always may). The bot reads it at most every CACHE seconds.
"""
from __future__ import annotations

import logging
import time

from bot.config import settings
from bot.utils.crm import redis

logger = logging.getLogger(__name__)

TESTERS_KEY = "runtime:checks_only_for"
CACHE = 5  # seconds
_cache: dict = {"at": 0.0, "value": None}


def _parse(raw: str) -> set[int]:
    return {int(x) for x in raw.replace(" ", "").split(",") if x.strip().isdigit()}


async def testers() -> set[int]:
    """The test window's testers; empty = the window is off."""
    if time.monotonic() - _cache["at"] < CACHE and _cache["value"] is not None:
        return _cache["value"]
    try:
        raw = await redis().get(TESTERS_KEY)
    except Exception as e:  # noqa: BLE001 - Redis down: the .env value
        logger.warning("Test window setting unreadable, using .env: %r", e)
        raw = None
    value = settings.checks_only_for_ids if raw is None else _parse(raw)
    _cache.update(at=time.monotonic(), value=value)
    return value


async def set_testers(ids: set[int]) -> None:
    """Turns the window on for `ids`, or off when `ids` is empty."""
    await redis().set(TESTERS_KEY, ",".join(str(i) for i in sorted(ids)))
    _cache.update(at=0.0, value=None)


async def window_on() -> bool:
    return bool(await testers())


async def checks_paused_for(user_id: int) -> bool:
    """True during a test window for everyone but its testers and the admins."""
    allowed = await testers()
    return bool(allowed) and user_id not in allowed | settings.admin_ids
