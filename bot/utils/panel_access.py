"""Who may sign in to the admin panel, and do what (the owner's choice, 2026-11-16).

Permissions. Every admin can look at everything and work the everyday pages (checks, the support inbox,
messages to a user, search, errors); the actions below need the owner's tick for that login on /admins
(web_admins.perms, comma-separated; NULL = all of them, which is what the logins made before 2026-11-16
have until the owner narrows them). The owner's .env login can do everything; a teacher login only marks
checks. A bot admin (ADMIN_TELEGRAM_IDS) whose Telegram is linked to a panel login without "payments" is not
sent the receipts and may not press approve in the bot either.

Two-step sign-in ("Telegram orqali", teachers too, a browser remembered for 30 days). A correct password does
not sign in yet: a *pending* sign-in is kept in Redis and the bot sends a 6-digit code to the Telegram account
linked to that login (panel_second_factor); the code typed on the page finishes it. A login with no Telegram
yet gets a one-time deep link (t.me/<bot>?start=pl_<token>): whoever starts the bot through it becomes that
login's Telegram and gets the code in the same chat, and the owner is told (a stolen password used first
would show up there). The owner's .env login only links a Telegram account that is in ADMIN_TELEGRAM_IDS.
"Remember this browser" keeps a random cookie whose hash lives in Redis for DEVICE_DAYS; a new password, a
switched-off admin, "sign out everywhere" or an unlinked Telegram forgets them.

For the day Telegram is out of reach: the owner's 8 one-time recovery codes (made on /admins, kept as hashes),
and the break-glass WEB_2FA=false in .env + a restart of admin_web.

Shared by the panel (webapp/main.py, webapp/panel.py) and the bot (bot/handlers/start.py, payments.py).
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import secrets
import time

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from bot.config import settings
from bot.utils.crm import redis
from db.database import get_session
from db.models import AdminAction, PanelSecondFactor, WebAdmin

logger = logging.getLogger(__name__)

# --- permissions -----------------------------------------------------------------------------------------

COIN_CAP = 50  # coins one admin may add or take in one go; more only through the owner
PERMS: dict[str, tuple[str, str]] = {
    "payments": ("💳 To'lovlar", "to'lovni tasdiqlash yoki rad etish (panelda ham, botda ham), test deb belgilash"),
    "coins": ("🟡 Coin", f"foydalanuvchiga coin qo'shish yoki olish — bir martada {COIN_CAP} tagacha"),
    "broadcast": ("📣 Ommaviy xabar", "hammaga yoki bir guruhga xabar yuborish"),
    "users": ("👥 Foydalanuvchilar", "bloklash, o'qituvchi va markazlar, Excel yuklab olish"),
    "content": ("📝 Kontent", "Practice savollari va grafik kalitlarini o'zgartirish"),
    "finance": ("💰 Moliya", "Umumiy hisob sahifasi va undagi yozuvlar"),
}


def perms_of(stored: str | None) -> set[str]:
    if stored is None:  # a login from before 2026-11-16: everything, as it had
        return set(PERMS)
    return {p for p in stored.split(",") if p in PERMS}


def perms_value(chosen) -> str:
    return ",".join(p for p in PERMS if p in set(chosen or ()))


async def telegram_may(telegram_id: int, perm: str) -> bool:
    """May this bot admin do `perm` in the bot? Yes unless their Telegram is linked only to panel logins that
    lack it (not linked, or linked to the owner's login: yes)."""
    try:
        async with get_session() as session:
            logins = (await session.execute(select(PanelSecondFactor.login).where(
                PanelSecondFactor.telegram_id == telegram_id))).scalars().all()
            if not logins or settings.web_admin_username in logins:
                return True
            rows = (await session.execute(select(WebAdmin).where(
                WebAdmin.username.in_(logins), WebAdmin.active.is_(True)))).scalars().all()
    except Exception:  # noqa: BLE001 - the database away: as before this existed
        logger.warning("Bot admin %s's permissions not checked", telegram_id, exc_info=True)
        return True
    return any((a.role or "admin") != "teacher" and perm in perms_of(a.perms) for a in rows)


# --- two-step sign-in ---------------------------------------------------------------------------------------

PENDING_TTL = 900   # a password-checked sign-in waits 15 minutes for its code (time to link Telegram)
CODE_TTL = 300      # a code works for 5 minutes
MAX_TRIES = 5       # wrong codes before the pending sign-in is dropped: the password again
RESEND_AFTER = 30   # seconds between two codes
MAX_SENDS = 4
LINK_PREFIX = "pl_"
DEVICE_COOKIE = "bz_device"
DEVICE_DAYS = 30

_PENDING = "panel:2fa:{pid}"
_LINK = "panel:2fa:link:{token}"
_DEVICE = "panel:dev:{h}"
_DEVICES = "panel:devs:{login}"


def enabled() -> bool:
    return bool(settings.web_2fa)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _new_code() -> str:
    return f"{secrets.randbelow(10 ** 6):06d}"


async def linked(login: str) -> PanelSecondFactor | None:
    async with get_session() as session:
        return await session.get(PanelSecondFactor, login)


async def all_linked() -> dict[str, PanelSecondFactor]:
    async with get_session() as session:
        return {r.login: r for r in (await session.execute(select(PanelSecondFactor))).scalars().all()}


async def start(found: dict, ip: str) -> str:
    """A pending sign-in for a login whose password was right; its id goes in the session."""
    pid = secrets.token_urlsafe(18)
    await redis().set(_PENDING.format(pid=pid), json.dumps(
        {"found": found, "ip": ip, "code": None, "sent_at": 0, "tries": 0, "sends": 0, "link": None}), ex=PENDING_TTL)
    return pid


async def pending(pid: str | None) -> dict | None:
    if not pid:
        return None
    raw = await redis().get(_PENDING.format(pid=pid))
    return json.loads(raw) if raw else None


async def _save(pid: str, data: dict) -> None:
    key = _PENDING.format(pid=pid)
    ttl = await redis().ttl(key)
    await redis().set(key, json.dumps(data), ex=ttl if ttl and ttl > 0 else PENDING_TTL)


def code_text(login: str, ip: str, code: str) -> str:
    return (f"🔐 <b>Bandzilla panel</b> — kirish kodi: <code>{code}</code>\n\n"
            f"Login: <b>{html.escape(login)}</b> · IP {html.escape(ip)}\n"
            f"Kod {CODE_TTL // 60} daqiqa amal qiladi. Kirayotgan siz bo'lmasangiz, kodni hech kimga bermang va "
            f"parolni almashtiring.")


async def send_code(bot, pid: str) -> str:
    """A fresh code to the login's Telegram: 'sent', 'wait' (too soon, or too many), 'no_link', 'gone' (the
    pending sign-in expired), 'failed' (Telegram refused)."""
    data = await pending(pid)
    if data is None:
        return "gone"
    login = data["found"]["admin"]
    row = await linked(login)
    if row is None:
        return "no_link"
    if time.time() - data["sent_at"] < RESEND_AFTER or data["sends"] >= MAX_SENDS:
        return "wait"
    code = _new_code()
    data.update(code=_hash(code + pid), sent_at=time.time(), sends=data["sends"] + 1)
    await _save(pid, data)
    try:
        await bot.send_message(row.telegram_id, code_text(login, data["ip"], code))
    except Exception as e:  # noqa: BLE001 - blocked the bot, Telegram away
        logger.warning("Panel sign-in code for %s not sent: %r", login, e)
        return "failed"
    return "sent"


async def check(pid: str | None, code: str) -> tuple[str, dict | None]:
    """The typed code: ('ok' | 'recovery', the sign-in) once - the pending sign-in is used up; else
    ('bad' | 'expired' | 'locked' | 'gone', None). 'locked' = too many wrong ones: the password again."""
    data = await pending(pid)
    if data is None:
        return "gone", None
    key = _PENDING.format(pid=pid)
    typed = "".join(code.split())
    found = data["found"]
    how = None
    if data["code"] and len(typed) == 6 and hmac.compare_digest(data["code"], _hash(typed + pid)):
        how = "ok" if time.time() - data["sent_at"] <= CODE_TTL else "expired"
    elif found.get("owner") and len(typed) > 6 and await use_recovery_code(found["admin"], typed):
        how = "recovery"
    if how in ("ok", "recovery"):
        if not await redis().delete(key):  # another tab got there first
            return "gone", None
        return how, found
    if how == "expired":
        return "expired", None
    data["tries"] += 1
    if data["tries"] >= MAX_TRIES:
        await redis().delete(key)
        return "locked", None
    await _save(pid, data)
    return "bad", None


async def link_url(pid: str) -> str | None:
    """The one-time deep link that links the pending sign-in's login to whoever starts the bot through it."""
    data = await pending(pid)
    if data is None:
        return None
    token = data.get("link")
    if not token or not await redis().exists(_LINK.format(token=token)):
        token = secrets.token_urlsafe(12)
        await redis().set(_LINK.format(token=token), pid, ex=PENDING_TTL)
        data["link"] = token
        await _save(pid, data)
    return f"https://t.me/{settings.telegram_bot_username}?start={LINK_PREFIX}{token}"


async def link_from_bot(bot, token: str, tg_user) -> str:
    """The bot's /start pl_<token>: tg_user becomes the pending sign-in's login's Telegram, gets the code, and
    the owner hears about it. Returns the answer for the chat."""
    pid = await redis().get(_LINK.format(token=token))
    data = await pending(pid) if pid else None
    if data is None:
        return "⌛ Bu havola eskirgan. Panelda login va parolni qayta kiriting — yangi havola chiqadi."
    login, owner = data["found"]["admin"], bool(data["found"].get("owner"))
    if owner and tg_user.id not in settings.admin_ids:
        return "⛔ Egasining logini faqat bot adminining (ADMIN_TELEGRAM_IDS) Telegram hisobiga ulanadi."
    name = (tg_user.full_name or str(tg_user.id)) + (f" (@{tg_user.username})" if tg_user.username else "")
    try:
        async with get_session() as session:
            if await session.get(PanelSecondFactor, login) is not None:
                return "Bu login allaqachon Telegram'ga ulangan. Kod o'sha Telegram'ga keladi."
            session.add(PanelSecondFactor(login=login, telegram_id=tg_user.id, telegram_name=name[:200]))
            session.add(AdminAction(admin=login, action="2fa_linked", details=f"{name} · id {tg_user.id}"[:300]))
            await session.commit()
    except IntegrityError:
        return "Bu login allaqachon Telegram'ga ulangan. Kod o'sha Telegram'ga keladi."
    await redis().delete(_LINK.format(token=token))
    code = _new_code()
    data.update(code=_hash(code + pid), sent_at=time.time(), sends=data["sends"] + 1)
    await _save(pid, data)
    if not owner:
        for admin_id in settings.admin_ids:
            if admin_id == tg_user.id:
                continue
            try:
                await bot.send_message(admin_id, (
                    f"🔐 Panel: <b>{html.escape(login)}</b> logini uchun kirish kodlari endi shu Telegram'ga "
                    f"boradi — {html.escape(name)}, id <code>{tg_user.id}</code>.\n\nBu kutilmagan bo'lsa: "
                    f"Adminlar sahifasida Telegram'ni uzing va parolini yangilang."))
            except Exception:  # noqa: BLE001
                pass
    return (f"✅ Telegram <b>{html.escape(login)}</b> logini uchun ulandi — panelga kirish kodlari endi shu yerga "
            f"keladi.\n\n" + code_text(login, data["ip"], code))


async def unlink(login: str) -> bool:
    async with get_session() as session:
        row = await session.get(PanelSecondFactor, login)
        if row is None:
            return False
        await session.delete(row)
        await session.commit()
    await forget_devices(login)
    return True


# --- remembered browsers -------------------------------------------------------------------------------------


async def remember_device(login: str) -> str:
    token = secrets.token_urlsafe(32)
    h = _hash(token)
    await redis().set(_DEVICE.format(h=h), login, ex=DEVICE_DAYS * 86400)
    await redis().sadd(_DEVICES.format(login=login), h)
    await redis().expire(_DEVICES.format(login=login), DEVICE_DAYS * 86400)
    return token


async def device_ok(login: str, token: str | None) -> bool:
    if not token:
        return False
    try:
        return await redis().get(_DEVICE.format(h=_hash(token))) == login
    except Exception:  # noqa: BLE001 - Redis away: the code then
        return False


async def forget_devices(login: str) -> int:
    try:
        r = redis()
        hashes = await r.smembers(_DEVICES.format(login=login))
        for h in hashes:
            await r.delete(_DEVICE.format(h=h))
        await r.delete(_DEVICES.format(login=login))
        return len(hashes)
    except Exception:  # noqa: BLE001
        logger.warning("Remembered browsers of %s not forgotten", login, exc_info=True)
        return 0


# --- the owner's recovery codes --------------------------------------------------------------------------------

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _norm(code: str) -> str:
    c = "".join(code.split()).upper().replace("-", "")
    return f"{c[:5]}-{c[5:]}"


async def new_recovery_codes(login: str) -> list[str] | None:
    """8 fresh one-time codes (the old ones stop working); None when the login has no Telegram linked yet."""
    codes = [_norm("".join(secrets.choice(_ALPHABET) for _ in range(10))) for _ in range(8)]
    async with get_session() as session:
        row = await session.get(PanelSecondFactor, login)
        if row is None:
            return None
        row.recovery_hashes = json.dumps([_hash(c) for c in codes])
        await session.commit()
    return codes


async def recovery_left(login: str) -> int:
    row = await linked(login)
    return len(json.loads(row.recovery_hashes)) if row is not None and row.recovery_hashes else 0


async def use_recovery_code(login: str, code: str) -> bool:
    h = _hash(_norm(code))
    async with get_session() as session:
        row = await session.get(PanelSecondFactor, login, with_for_update=True)
        hashes = json.loads(row.recovery_hashes) if row is not None and row.recovery_hashes else []
        if h not in hashes:
            return False
        hashes.remove(h)
        row.recovery_hashes = json.dumps(hashes)
        await session.commit()
    return True
