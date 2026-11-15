"""The panel's sign-ins kept on the server too (the 2026-11-15 audit). The session cookie is signed, not
stored: until then "Chiqish" only cleared the browser's copy - a copied cookie kept working after a logout or
a new password, for ever (Starlette re-signs it on every response). Now every sign-in gets a random id kept in
Redis for MAX_AGE; a request whose id is gone (logged out, signed out everywhere, password changed, admin
switched off) or older than that is signed out.

And the login throttle (it lived in one process's memory, per IP only, and every deploy reset it): failures
are counted in Redis per IP and per login name.

Redis away: the checks are skipped (logged) - the panel stays usable when it is needed most.
"""
from __future__ import annotations

import logging
import secrets
import time

from fastapi import Request

from bot.utils.crm import redis

logger = logging.getLogger(__name__)

MAX_AGE = 7 * 24 * 3600  # a sign-in lasts a week at most
_SID = "panel:sid:{sid}"
_SIDS = "panel:sids:{login}"

LOGIN_WINDOW = 900  # seconds
MAX_FAILS_IP = 10
MAX_FAILS_LOGIN = 8


async def issue(request: Request, found: dict) -> None:
    """A fresh session for a sign-in, its id kept on the server."""
    sid = secrets.token_urlsafe(24)
    login = found.get("admin") or ""
    request.session.clear()
    request.session.update(logged_in=True, sid=sid, issued=int(time.time()), **found)
    try:
        r = redis()
        await r.set(_SID.format(sid=sid), login, ex=MAX_AGE)
        await r.sadd(_SIDS.format(login=login), sid)
        await r.expire(_SIDS.format(login=login), MAX_AGE)
    except Exception:  # noqa: BLE001
        logger.warning("Panel session %s… not stored", sid[:6], exc_info=True)


async def valid(request: Request) -> bool:
    """This sign-in still stands: its id is on the server for this login and it is not older than MAX_AGE."""
    sid = request.session.get("sid")
    issued = int(request.session.get("issued") or 0)
    if not sid or time.time() - issued > MAX_AGE:
        return False
    try:
        return await redis().get(_SID.format(sid=sid)) == (request.session.get("admin") or "")
    except Exception:  # noqa: BLE001
        logger.warning("Panel session not checked (Redis)", exc_info=True)
        return True


async def end(request: Request) -> None:
    """This sign-in only (Chiqish)."""
    sid = request.session.get("sid")
    login = request.session.get("admin") or ""
    request.session.clear()
    if sid:
        try:
            await redis().delete(_SID.format(sid=sid))
            await redis().srem(_SIDS.format(login=login), sid)
        except Exception:  # noqa: BLE001
            logger.warning("Panel session not ended on the server", exc_info=True)


async def end_all(login: str) -> int:
    """Every sign-in of `login` (sign out everywhere, a new password, an admin switched off). How many."""
    try:
        r = redis()
        sids = await r.smembers(_SIDS.format(login=login))
        for sid in sids:
            await r.delete(_SID.format(sid=sid))
        await r.delete(_SIDS.format(login=login))
        return len(sids)
    except Exception:  # noqa: BLE001
        logger.warning("Sessions of %s not ended", login, exc_info=True)
        return 0


async def too_many_fails(ip: str, login: str) -> bool:
    try:
        r = redis()
        by_ip = int(await r.get(f"panel:fail:ip:{ip}") or 0)
        by_login = int(await r.get(f"panel:fail:login:{login.lower()[:64]}") or 0)
    except Exception:  # noqa: BLE001
        return False
    return by_ip >= MAX_FAILS_IP or by_login >= MAX_FAILS_LOGIN


async def failed(ip: str, login: str) -> None:
    try:
        r = redis()
        for key in (f"panel:fail:ip:{ip}", f"panel:fail:login:{login.lower()[:64]}"):
            await r.incr(key)
            await r.expire(key, LOGIN_WINDOW)
    except Exception:  # noqa: BLE001
        logger.warning("Login failure not counted", exc_info=True)
