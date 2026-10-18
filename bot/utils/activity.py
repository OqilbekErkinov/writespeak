"""Who uses the bot and when, for the admin panel (webapp/insights.py):
every update a user sends marks them as seen - the last time in one Redis
hash, and the Tashkent day in that day's set (kept 60 days, for the daily
active users chart). One pipelined round trip per update; never raises.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from bot.utils.crm import redis

logger = logging.getLogger(__name__)

LAST_SEEN_KEY = "seen:last"  # user id -> unix time
DAY_KEY = "seen:day:{day}"  # user ids active that Tashkent day (YYYY-MM-DD)
DAY_TTL = 60 * 24 * 3600
MAX_WAIT = 0.5  # seconds
TASHKENT = timedelta(hours=5)


def tashkent_day(ts: float | None = None) -> str:
    now = datetime.fromtimestamp(ts, timezone.utc) if ts is not None else datetime.now(timezone.utc)
    return (now + TASHKENT).strftime("%Y-%m-%d")


async def seen(user_id: int) -> None:
    now = time.time()
    key = DAY_KEY.format(day=tashkent_day(now))
    try:
        pipe = redis().pipeline()
        pipe.hset(LAST_SEEN_KEY, user_id, int(now))
        pipe.sadd(key, user_id)
        pipe.expire(key, DAY_TTL)
        # a slow Redis must never hold the student's update up
        await asyncio.wait_for(pipe.execute(), MAX_WAIT)
    except Exception as e:  # noqa: BLE001
        logger.debug("Activity of %s not recorded: %r", user_id, e)
