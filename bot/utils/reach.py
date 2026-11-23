"""Who the bot can no longer write to (the owner, 2026-11-23: broadcast #261123132311a1f5 reached 114 of 154 - 18
"blocked" and 22 "errors", and nobody could tell who or why). A send Telegram refuses for good marks the student:

- blocked: "bot was blocked by the user";
- deleted: "user is deactivated" - the Telegram account is gone;
- not_started: "chat not found" / "can't initiate conversation" - signed up on the previous bot (@writespeak_bot)
  and never started this one; only the previous bot can write to them (bot/moved.py).

The panel's broadcasts (webapp/panel.py) leave them out, until the bot sees them again (bot/utils/activity.py, any
message or button after the mark): one who unblocks and writes, or starts the new bot, is back.

Redis: reach:out (hash user id -> {"why", "at"}).
"""
from __future__ import annotations

import json
import time

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bot.utils.activity import LAST_SEEN_KEY
from bot.utils.crm import redis

KEY = "reach:out"
LABELS = {
    "blocked": "⛔ Botni bloklagan",
    "deleted": "🗑 Telegram akkauntini o'chirgan",
    "not_started": "🚪 Yangi botni ochmagan (eski botda ro'yxatdan o'tgan)",
}


def why_of(error: BaseException) -> str | None:
    """blocked / deleted / not_started for a refusal that will not change by itself; None for anything else."""
    message = str(getattr(error, "message", error)).lower()
    if isinstance(error, TelegramForbiddenError):
        if "deactivated" in message:
            return "deleted"
        if "initiate" in message:
            return "not_started"
        if "blocked" in message:
            return "blocked"
        return None
    if isinstance(error, TelegramBadRequest) and "chat not found" in message:
        return "not_started"
    return None


async def mark(marks: dict[int, str], at: float | None = None) -> None:
    """{user id: why} - the students the bot may not write to now."""
    if marks:
        now = at or time.time()
        await redis().hset(KEY, mapping={str(uid): json.dumps({"why": why, "at": now}) for uid, why in marks.items()})


async def out() -> dict[int, str]:
    """{user id: why} of the students still out of reach: marked, and not seen by the bot since."""
    r = redis()
    raw = await r.hgetall(KEY)
    if not raw:
        return {}
    seen = await r.hmget(LAST_SEEN_KEY, list(raw))
    result, back = {}, []
    for (uid, value), last in zip(raw.items(), seen):
        try:
            item = json.loads(value)
        except ValueError:
            continue
        if last is not None and float(last) > float(item.get("at") or 0):
            back.append(uid)  # the bot saw them after the refusal: unblocked / started the new bot
            continue
        result[int(uid)] = item.get("why", "blocked")
    if back:
        await r.hdel(KEY, *back)
    return result
