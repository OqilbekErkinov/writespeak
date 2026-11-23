"""The admin panel's control room (the user's request, 2026-10-18: "we must
control the bot fully from the panel"):

- /settings      the test window on/off and its testers (bot/utils/runtime.py);
- /broadcast     a message to all users or a segment, in their language;
- /support       the students' messages to the admins and the answers;
- /errors        the bot's, the importer's and the panel's errors;
- /export/*.xlsx users, payments and checks for Excel;
- block / unblock a user;
- /admins        separate logins (owner only), /audit what was done by whom.

Wired into webapp/main.py like webapp/cabinet.py (`templates` is set there).
Every change made here is written to the audit log (admin_actions).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import io
import json
import logging
import secrets
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import BufferedInputFile
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import and_, func, select, update

from bot.config import settings
from bot.utils import balance, broadcast_post, checks_back, crm, panel_access, reach, runtime, support_log
from db import crud
from db.database import get_session
from db.models import (
    AdminAction,
    ChartKey,
    PaymentKind,
    PaymentRequest,
    SupportMessage,
    User,
    UserStatus,
    WebAdmin,
)
from webapp import insights
from services.excel import safe as excel_safe
from bot.utils.capacity import CAP_KEY as CAPACITY_CAP_KEY, STATE_KEY as CAPACITY_STATE_KEY

logger = logging.getLogger(__name__)
router = APIRouter()
templates = None  # set by webapp/main.py

TESTERS_LIST_KEY = "runtime:testers"  # the testers list, kept while the window is off
BROADCAST_KEY = "broadcast:{id}"
BROADCAST_IDS = "broadcast:ids"
MISSED_KEY = "broadcast:{id}:missed"  # user id -> why it did not reach them (bot/utils/reach.py) or "failed:..."
MISSED_TTL = 90 * 86400
BROADCAST_MAX = 4000  # characters of one language's text after escaping (Telegram: 4096)
BROADCAST_PAUSE = 0.05  # 20 messages a second at most - under Telegram's limit
# A post: a picture or a video with the text under it (the owner, 2026-11-22). Telegram's own limits: a photo 10 MB,
# a video 50 MB sent by a bot, a caption 1024 characters (a longer text follows as its own message).
BROADCAST_CAPTION = 1024
MEDIA_TYPES = {"image/jpeg": "photo", "image/png": "photo", "image/webp": "photo",
               "video/mp4": "video", "video/quicktime": "video"}
MEDIA_LIMITS = {"photo": 10 * 1024 * 1024, "video": 50 * 1024 * 1024}


# --- who is signed in, and the audit log ------------------------------------------------------------


def logged_in(request: Request) -> bool:
    return bool(request.session.get("logged_in"))


def admin_name(request: Request) -> str:
    return request.session.get("admin") or settings.web_admin_username


def is_owner(request: Request) -> bool:
    # sessions from before separate logins were the owner's (the .env login)
    return bool(request.session.get("owner", "admin" not in request.session))


def can(request: Request, perm: str) -> bool:
    """May this sign-in do `perm` (bot/utils/panel_access.py's PERMS)? The owner: everything; a teacher login:
    none of them; an admin: what the owner ticked (read by session_valid, at most 15 seconds old)."""
    if is_owner(request):
        return True
    if is_teacher(request):
        return False
    return perm in (getattr(request.state, "perms", None) or set())


def guard(request: Request, owner: bool = False, perm: str | None = None):
    """A redirect when the request may not see the page or do the action, else None."""
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    if owner and not is_owner(request):
        return RedirectResponse("/?denied=owner", status_code=303)
    if perm and not can(request, perm):
        return RedirectResponse(f"/?denied={perm}", status_code=303)
    return None


async def audit(request: Request | str, action: str, target_user_id: int | None = None, details: str | None = None) -> None:
    who = request if isinstance(request, str) else admin_name(request)
    try:
        async with get_session() as session:
            session.add(AdminAction(admin=who, action=action, target_user_id=target_user_id, details=details))
            await session.commit()
    except Exception as e:  # noqa: BLE001 - the action itself has happened
        logger.warning("Audit entry %s by %s not saved: %r", action, who, e)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1)
        return hmac.compare_digest(got.hex(), digest)
    except Exception:  # noqa: BLE001
        return False


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def _pw_tag(password_hash: str) -> str:
    """Kept in a web admin's session: a new password (or a disabled login)
    ends the sessions signed in before it. Not the hash itself - the
    session cookie is signed, not encrypted."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


async def authenticate(username: str, password: str) -> dict | None:
    """The session's sign-in keys for a valid login: the .env owner (always
    works - the break-glass login) or an active web admin."""
    if _same(username, settings.web_admin_username) and _same(password, settings.web_admin_password):
        return {"admin": settings.web_admin_username, "owner": True}
    async with get_session() as session:
        row = (await session.execute(select(WebAdmin).where(WebAdmin.username == username.lower()))).scalar_one_or_none()
        if row is None or not row.active or not check_password(password, row.password_hash):
            return None
        row.last_login_at = datetime.now(timezone.utc)
        await session.commit()
        return {"admin": row.username, "owner": False, "pw": _pw_tag(row.password_hash),
                "teacher": (row.role or "admin") == "teacher"}


def denied_note(request: Request) -> str | None:
    """The top-of-page note after a refused page or action (guard's ?denied=)."""
    what = request.query_params.get("denied")
    if not what:
        return None
    if what == "owner":
        return "Bu sahifa faqat egasiga ochiq."
    label = panel_access.PERMS.get(what, ("",))[0]
    return f"Bu amal uchun ruxsatingiz yo'q{f' ({label})' if label else ''} — egasiga murojaat qiling."


# What a "teacher" login may open (the user, 2026-11-14: the teachers enter their bands themselves): the checks
# page, its reports and recordings - never users, payments or settings.
# Marked blind: no bot band, no PDF report - the answer itself (the essay and its question, the recording).
TEACHER_PATHS = ("/checks", "/audio/", "/logout", "/login")


def is_teacher(request: Request) -> bool:
    return bool(request.session.get("teacher"))


# username -> (checked at, its pw tag (None = disabled), its permissions)
_valid_cache: dict[str, tuple[float, str | None, set[str]]] = {}


async def session_valid(request: Request) -> bool:
    """False for a web admin who was disabled or given a new password since
    signing in (checked at most every 15 seconds) - and for anyone whose
    sign-in the server no longer has (logged out, signed out everywhere,
    older than a week: webapp/sessions.py)."""
    from webapp import sessions

    if not await sessions.valid(request):
        return False
    if panel_access.enabled() and not request.session.get("mfa"):
        return False  # signed in before the two-step sign-in (2026-11-16), or without it
    if is_owner(request):
        return True
    username = request.session.get("admin") or ""
    cached = _valid_cache.get(username)
    if cached is None or time.monotonic() - cached[0] > 15:
        try:
            async with get_session() as session:
                row = (await session.execute(select(WebAdmin).where(WebAdmin.username == username))).scalar_one_or_none()
        except Exception:  # noqa: BLE001 - the database is down: the pages fail anyway
            return True
        cached = (time.monotonic(), _pw_tag(row.password_hash) if row is not None and row.active else None,
                  panel_access.perms_of(row.perms) if row is not None else set())
        _valid_cache[username] = cached
    request.state.perms = cached[2]
    return cached[1] is not None and cached[1] == request.session.get("pw")


def new_bot() -> Bot:
    from bot.middlewares.premium_emoji import PremiumEmoji  # noqa: PLC0415

    bot = Bot(settings.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot.session.middleware(PremiumEmoji())  # the admins' premium emoji, as in the bot's own messages
    return bot


# Pages that never show the top bar - no badges to count for them.
NAV_SKIP = ("/login", "/logout", "/cabinet", "/reports/", "/export/", "/support/file/", "/broadcast/", "/payments/",
            "/questions/", "/chart-image/")


async def nav_counts(request: Request) -> dict:
    """The top bar's badges, and who is signed in."""
    out = {"support": 0, "payments": 0, "charts": 0, "errors": 0, "window": False,
           "owner": is_owner(request), "admin": admin_name(request), "teacher": is_teacher(request),
           "can": {p: can(request, p) for p in panel_access.PERMS}, "denied": denied_note(request)}
    if out["teacher"]:
        return out
    out["errors"] = await errors_today()
    try:
        async with get_session() as session:
            # the threads waiting for an answer - their last message the student's (2026-11-16: unread messages
            # were counted, and a thread opened but never answered dropped out of the badge)
            out["support"] = len(await crud.support_waiting(session))
            # a receipt sent, no decision yet - the one "pending" of every count (a pack tapped is nothing to decide)
            out["payments"] = await crud.count_pending_payments(session)
            out["charts"] = (await session.execute(select(func.count()).select_from(ChartKey).where(
                ChartKey.status == "auto"))).scalar() or 0
        out["window"] = await runtime.window_on()
    except Exception as e:  # noqa: BLE001 - the page itself still opens
        logger.info("Nav badges not counted: %r", e)
    return out


_jobs: set[asyncio.Task] = set()


def _later(coro) -> None:
    """Runs `coro` after the response, in this process; its failure is logged."""
    async def run():
        try:
            await coro
        except Exception:
            logger.exception("Panel background job failed")
    task = asyncio.get_running_loop().create_task(run())
    _jobs.add(task)  # a task nothing refers to may be garbage-collected mid-way
    task.add_done_callback(_jobs.discard)


# --- 1. the test window ------------------------------------------------------------------------------------


async def _testers_list() -> set[int]:
    raw = await crm.redis().get(TESTERS_LIST_KEY)
    if raw is None:  # first use: the .env list
        return set(settings.checks_only_for_ids)
    return runtime._parse(raw)


async def _save_testers_list(ids: set[int]) -> None:
    await crm.redis().set(TESTERS_LIST_KEY, ",".join(str(i) for i in sorted(ids)))


async def _notify_back_job() -> None:
    bot = new_bot()
    try:
        sent = await checks_back.notify_back(bot)
        logger.info("Test window off from the panel: %d users told checks are back", sent)
    finally:
        await bot.session.close()


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, done: str | None = None):
    if (r := guard(request, owner=True)) is not None:
        return r
    return await _settings(request, done)


@router.post("/settings/backup-key", response_class=HTMLResponse)
async def settings_backup_key(request: Request):
    """The backup passphrase, shown to the owner on request (and written in the audit log): the nightly copies
    in the admins' group cannot be opened without it, so it has to be kept off the server too."""
    if (r := guard(request, owner=True)) is not None:
        return r
    await audit(request, "backup_key_viewed")
    return await _settings(request, None, backup_key=settings.backup_passphrase or None)


def offsite_state() -> dict:
    """The last nightly copy sent to the admins' group (deploy/backup/offsite.sh writes data/offsite_status.json)."""
    from pathlib import Path

    try:
        state = json.loads((Path(settings.file_storage_path).parent / "offsite_status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    cfg = crm.config() or {}
    out = {**state, "topic": bool((cfg.get("topics") or {}).get("backup")), "key_set": bool(settings.backup_passphrase)}
    try:
        at = datetime.fromisoformat(state["at"])
        out["when"] = crm.local(at).strftime("%d.%m.%Y %H:%M")
        out["stale"] = datetime.now(timezone.utc) - at > timedelta(hours=30)
        if state.get("full_at"):
            out["full_when"] = crm.local(datetime.fromisoformat(state["full_at"])).strftime("%d.%m.%Y")
    except (KeyError, TypeError, ValueError):
        out["when"] = None
    out["size"] = f"{(state.get('bytes') or 0) / 1024 / 1024:.1f} MB"
    return out


async def _settings(request: Request, done: str | None, backup_key: str | None = None):
    on = await runtime.window_on()
    listed = await _testers_list()
    async with get_session() as session:
        users = {u.id: u for u in (await session.execute(select(User).where(User.id.in_(listed)))).scalars()} if listed else {}
    flash = {
        "on": "Test rejimi yoqildi: endi faqat sinovchilar va adminlar tekshira oladi.",
        "off": "Tekshiruvlar hamma uchun ochildi. Kutayotganlarga «Bandzilla yana ishlayapti» xabari yuborilmoqda.",
        "added": "Sinovchi qo'shildi.", "removed": "Sinovchi olib tashlandi.", "bad": "Bunday foydalanuvchi topilmadi.",
        "empty": "Avval kamida bitta sinovchi qo'shing.",
        "balance": "Balans saqlandi.", "resumed": "Balans saqlandi - tekshiruvlar qayta ochildi, kutganlarga xabar ketmoqda.",
        "bad_amount": "Summani raqam bilan kiriting: OpenAI — so'mda (masalan 70000), Azure — soatda (masalan 5).",
        "capacity": "Saqlandi: bot uni 15 soniya ichida qo'llaydi.",
        "bad_capacity": "1 dan 2000 gacha son kiriting.",
        "cost_limits": "Xarajat chegaralari saqlandi: bot ularni 15 soniya ichida qo'llaydi.",
        "bad_cost_limits": "Raqam kiriting: tekshiruv chegarasi $0.01–100, kunlik chegara $0–10000, sekin tekshiruv "
                           "0–120 daqiqa (0 — o'chiq).",
    }.get(done or "")
    return templates.TemplateResponse(request, "settings.html", {
        "active": "settings", "on": on, "testers": sorted(listed), "users": users, "waiting": await checks_back.waiting_count(),
        "flash": flash, "flash_bad": done in ("bad", "empty", "bad_amount", "bad_capacity", "bad_cost_limits"),
        "env_on": bool(settings.checks_only_for_ids),
        "openai": await balance.openai_status(), "credit_paused": await balance.credit_paused(),
        "azure": await balance.azure_status(), "capacity": await capacity_state(),
        "costs": await cost_limits_state(),
        "offsite": offsite_state(), "backup_key": backup_key,
    })


async def cost_limits_state() -> dict:
    """The «Xarajat chegaralari» card (bot/utils/cost_guard.py, 2026-11-16): each value in force, its default,
    whether the owner set it - and today's OpenAI spend against the daily cap."""
    from bot.utils import cost_guard

    try:
        own = await cost_guard.overrides()
        spend = sum(float(v) for v in (await crm.redis().hgetall(crm.spend_key(crm.local_now().date()))).values())
    except Exception:  # noqa: BLE001 - Redis away: the defaults are shown
        own, spend = {}, None
    defaults = cost_guard.defaults()
    rows = [{"name": name, "label": label, "unit": unit, "value": own.get(name, defaults[name]),
             "default": defaults[name], "own": name in own}
            for name, (label, unit, _) in cost_guard.FIELDS.items()]
    cap = own.get("daily_cap", defaults["daily_cap"])
    return {"rows": rows, "spend": spend, "cap": cap,
            "pct": round(spend / cap * 100) if spend is not None and cap else None}


@router.post("/settings/cost-limits")
async def settings_cost_limits(request: Request, action: str = Form("save"), writing_task1: str = Form(""),
                               writing_task2: str = Form(""), speaking_part: str = Form(""),
                               speaking_full: str = Form(""), daily_cap: str = Form(""), slow_minutes: str = Form("")):
    """The cost guard's limits (owner only): one check's OpenAI cost per kind, the day's spend cap, a slow
    check's minutes. An empty field is left as it is; "reset" brings back the .env defaults."""
    if (r := guard(request, owner=True)) is not None:
        return r
    from bot.utils import cost_guard

    if action == "reset":
        await cost_guard.reset()
        await audit(request, "cost_limits", details="standart qiymatlarga qaytarildi")
        return RedirectResponse("/settings?done=cost_limits#costs", status_code=303)
    typed = {"writing_task1": writing_task1, "writing_task2": writing_task2, "speaking_part": speaking_part,
             "speaking_full": speaking_full, "daily_cap": daily_cap, "slow_minutes": slow_minutes}
    values = {}
    for name, raw in typed.items():
        if not raw.strip():
            continue
        value = cost_guard.parse(name, raw)
        if value is None:
            return RedirectResponse("/settings?done=bad_cost_limits#costs", status_code=303)
        values[name] = value
    before = await cost_guard.limits(fresh=True)
    await cost_guard.save(values)
    changed = [f"{cost_guard.FIELDS[k][0]} {before.get(k, 0):g}→{v:g}" for k, v in values.items()
               if before.get(k) != v]
    await audit(request, "cost_limits", details=", ".join(changed) or "o'zgarishsiz")
    return RedirectResponse("/settings?done=cost_limits#costs", status_code=303)


async def capacity_state() -> dict:
    """The check gate as the bot last published it (bot/utils/capacity.py) - {} when the bot is not running -
    with the cap the owner set."""
    try:
        r = crm.redis()
        state = json.loads(await r.get(CAPACITY_STATE_KEY) or "{}")
        cap = await r.get(CAPACITY_CAP_KEY)
    except Exception:  # noqa: BLE001
        return {}
    state["cap_set"] = int(cap) if cap and str(cap).isdigit() else None
    state["cap_default"] = settings.max_parallel_checks
    for model in (state.get("models") or {}).values():
        lt = model.get("limit_tokens") or 0
        model["left_pct"] = round(model.get("remaining_tokens", 0) / lt * 100) if lt else None
    return state


@router.post("/settings/capacity")
async def settings_capacity(request: Request, cap: str = Form("")):
    """The most checks graded at once (the server's cap; OpenAI's rate limit may hold fewer)."""
    if (r := guard(request, owner=True)) is not None:
        return r
    value = cap.strip()
    if not value.isdigit() or not 1 <= int(value) <= 2000:
        return RedirectResponse("/settings?done=bad_capacity", status_code=303)
    await crm.redis().set(CAPACITY_CAP_KEY, value)
    await audit(request, "capacity", details=f"bir vaqtda ko'pi bilan {value} ta tekshiruv")
    return RedirectResponse("/settings?done=capacity", status_code=303)


@router.post("/settings/signout-all")
async def settings_signout_all(request: Request):
    """Every sign-in to the panel ends - the owner's, every admin's and teacher's (a lost laptop, a shared
    password): each has to sign in again."""
    if (r := guard(request, owner=True)) is not None:
        return r
    from webapp import sessions

    async with get_session() as session:
        logins = [settings.web_admin_username, *(await session.execute(select(WebAdmin.username))).scalars().all()]
    ended = 0
    for login in logins:
        ended += await sessions.end_all(login)
        await panel_access.forget_devices(login)  # a lost laptop: it must not skip the code either
    await audit(request, "signout_all", details=f"{ended} ta sessiya")
    await sessions.end(request)
    return RedirectResponse("/login", status_code=303)


@router.post("/settings/balance")
async def settings_balance(request: Request, kind: str = Form(...), amount: str = Form("")):
    """The OpenAI balance in so'm after a top-up, or Azure's monthly hours (bot/utils/balance.py)."""
    if (r := guard(request, owner=True)) is not None:
        return r
    value = balance.parse_amount(amount) if kind == "azure" else balance.parse_som(amount)
    if value is None or kind not in ("openai", "azure"):
        return RedirectResponse("/settings?done=bad_amount", status_code=303)
    if kind == "azure":
        await balance.set_azure_hours(value)
        await audit(request, "azure_hours", details=f"{value:g} soat")
        return RedirectResponse("/settings?done=balance", status_code=303)
    resumed = await balance.set_openai(value)
    await audit(request, "openai_balance", details=balance.som(value))
    if resumed:
        _later(_credit_back_job())
    return RedirectResponse(f"/settings?done={'resumed' if resumed else 'balance'}", status_code=303)


async def _credit_back_job() -> None:
    bot = new_bot()
    try:
        await crm.credit_back(bot, entered=True)
    finally:
        await bot.session.close()


@router.post("/settings/test-window")
async def settings_test_window(request: Request, action: str = Form(...), user_id: str = Form("")):
    if (r := guard(request, owner=True)) is not None:
        return r
    listed = await _testers_list()
    on = await runtime.window_on()
    done = action
    if action in ("add", "remove"):
        raw = user_id.strip().lstrip("@")
        async with get_session() as session:
            if raw.isdigit():
                uid = int(raw)
                exists = await session.get(User, uid) is not None
            else:  # an @username
                uid = (await session.execute(select(User.id).where(func.lower(User.username) == raw.lower())
                                             .limit(1))).scalar_one_or_none()
                exists = uid is not None
        if action == "add" and not exists:
            return RedirectResponse("/settings?done=bad", status_code=303)
        listed = listed | {uid} if action == "add" else listed - {uid}
        await _save_testers_list(listed)
        if on:
            await runtime.set_testers(listed)
        done = "added" if action == "add" else "removed"
        await audit(request, f"tester_{action}", uid)
    elif action == "on":
        if not listed:
            return RedirectResponse("/settings?done=empty", status_code=303)
        await runtime.set_testers(listed)
        await audit(request, "test_window_on", details=f"{len(listed)} sinovchi")
    elif action == "off":
        await _save_testers_list(listed)
        await runtime.set_testers(set())
        await audit(request, "test_window_off", details=f"{await checks_back.waiting_count()} kishi kutayotgan edi")
        _later(_notify_back_job())
    return RedirectResponse(f"/settings?done={done}", status_code=303)


# --- 2. broadcast --------------------------------------------------------------------------------------------

AUDIENCES = {
    "all": "Barcha foydalanuvchilar", "active": "7 kunda faol bo'lganlar", "checked": "Tekshiruv qilganlar",
    "nochecks": "Hali tekshiruv qilmaganlar", "paying": "To'lov qilganlar", "new": "Bugun qo'shilganlar",
    "teachers": "O'qituvchilar", "admins": "Faqat adminlar (sinov)",
}
LANG_FILTERS = {"any": "Hamma tillar", "uz": "🇺🇿 O'zbekcha", "ru": "🇷🇺 Ruscha", "en": "🇬🇧 Inglizcha"}


async def _audience_all(session, audience: str, lang: str) -> list[User]:
    if audience == "admins":
        rows = (await session.execute(select(User).where(User.id.in_(settings.admin_ids)))).scalars().all()
        return list(rows)
    data = await insights.users_overview(session, None, audience if audience in insights.SEGMENTS else "all", limit=10 ** 6)
    users = [r["user"] for r in data["rows"]]
    users = [u for u in users if u.status != UserStatus.denied and u.id not in settings.admin_ids]
    if lang != "any":
        users = [u for u in users if (u.language or "uz") == lang]
    return users


async def _audience_split(session, audience: str, lang: str) -> tuple[list[User], Counter]:
    """The audience the bot can write to, and {why: how many} of the ones left out - they blocked the bot, deleted
    their account or never started it (bot/utils/reach.py) and have not been back since (2026-11-23)."""
    users = await _audience_all(session, audience, lang)
    if audience == "admins":
        return users, Counter()
    try:
        gone = await reach.out()
    except Exception:  # noqa: BLE001 - Redis unreadable: everyone, as before
        logger.warning("Unreachable students not read", exc_info=True)
        return users, Counter()
    return [u for u in users if u.id not in gone], Counter(gone[u.id] for u in users if u.id in gone)


async def _audience(session, audience: str, lang: str) -> list[User]:
    return (await _audience_split(session, audience, lang))[0]


async def _deliver(bot: Bot, uid: int, text: str, media: tuple[str, bytes, str] | None, kb, cache: dict,
                   copy: tuple[int, int] | None = None) -> None:
    """One student's broadcast: the text - or, with `media` (kind, bytes, file name), the picture or video with the
    text as its caption (a text over Telegram's caption limit follows it as its own message). The file is uploaded
    once: Telegram's file id of the first delivery (`cache`) serves everyone after. `copy`: (chat id, message id) of
    the post an admin made in Telegram (bot/utils/broadcast_post.py), sent as it is - its premium emoji too."""
    if copy is not None:
        await bot.copy_message(uid, copy[0], copy[1], reply_markup=kb)
        return
    if media is None:
        await bot.send_message(uid, text, disable_web_page_preview=True, reply_markup=kb)
        return
    kind, data, name = media
    caption = text if len(text) <= BROADCAST_CAPTION else None
    markup = kb if caption is not None else None
    if cache.get("document"):  # Telegram took the video as a file (not an MP4 it can play)
        msg = await bot.send_document(uid, cache["document"], caption=caption, reply_markup=markup)
    elif kind == "photo":
        msg = await bot.send_photo(uid, cache.get("id") or BufferedInputFile(data, filename=name), caption=caption,
                                   reply_markup=markup)
        cache.setdefault("id", msg.photo[-1].file_id if msg.photo else None)
    else:
        msg = await bot.send_video(uid, cache.get("id") or BufferedInputFile(data, filename=name), caption=caption,
                                   reply_markup=markup, supports_streaming=True)
        if "id" not in cache and "document" not in cache:
            if msg.video:
                cache["id"] = msg.video.file_id
            elif msg.document:
                cache["document"] = msg.document.file_id
    if caption is None:
        await bot.send_message(uid, text, disable_web_page_preview=True, reply_markup=kb)


async def _broadcast_job(bid: str, users: list[tuple[int, str]], texts: dict[str, str],
                         media: tuple[str, bytes, str] | None = None, buttons: bool = False,
                         copy: dict[str, tuple[int, int]] | None = None) -> None:
    """`media`: a picture or video posted with the text; `buttons`: "✍️ Writing / 🎙 Speaking" and a ready question
    under it (bot/keyboards/entry_kb.py onboarding_kb - what a new student sees), so a reminder starts a check;
    `copy`: the Telegram post's versions {lang: (chat id, message id)}, sent instead of `texts` - each student
    their language's, the Uzbek one when theirs is missing."""
    from bot.keyboards.entry_kb import onboarding_kb  # noqa: PLC0415 - the bot's keyboards, only for this job

    key = BROADCAST_KEY.format(id=bid)
    r = crm.redis()
    bot = new_bot()
    sent = failed = blocked = not_started = 0
    missed: dict[str, str] = {}  # who it did not reach, and why (the panel's "Kimlarga yetmadi" page)
    marks: dict[int, str] = {}  # the ones out of reach for good (bot/utils/reach.py)
    cache: dict = {}

    async def save_progress(done: int) -> None:
        await r.hset(key, mapping={"sent": sent, "failed": failed, "blocked": blocked, "not_started": not_started,
                                   "done": done, "updated": int(time.time())})
        if missed:
            await r.hset(MISSED_KEY.format(id=bid), mapping=missed)
            await r.expire(MISSED_KEY.format(id=bid), MISSED_TTL)
            missed.clear()
        if marks:
            await reach.mark(marks)
            marks.clear()

    try:
        for i, (uid, lang) in enumerate(users, 1):
            text = texts.get(lang) or texts.get("uz", "")
            kb = onboarding_kb(lang) if buttons else None
            one = (copy.get(lang) or copy.get("uz")) if copy else None
            try:
                try:
                    await _deliver(bot, uid, text, media, kb, cache, one)
                except TelegramRetryAfter as e:
                    await asyncio.sleep(e.retry_after)
                    await _deliver(bot, uid, text, media, kb, cache, one)
                sent += 1
            except Exception as e:  # noqa: BLE001
                why = reach.why_of(e)
                if why == "not_started":
                    not_started += 1
                elif why:
                    blocked += 1
                else:
                    failed += 1
                    logger.warning("Broadcast %s to %s failed: %r", bid, uid, e)
                missed[str(uid)] = why or ("failed:" + str(getattr(e, "message", None) or repr(e)))[:200]
                if why:
                    marks[uid] = why
            if i % 10 == 0 or i == len(users):
                await save_progress(i)
            await asyncio.sleep(BROADCAST_PAUSE)
        await r.hset(key, mapping={"status": "done", "finished": int(time.time())})
    except Exception:
        await r.hset(key, mapping={"status": "stopped"})
        raise
    finally:
        await bot.session.close()
    await crm.post("support", f"📣 <b>Ommaviy xabar</b> #{bid} tugadi: {sent} ta yetkazildi, {blocked} ta botni bloklagan "
                              f"yoki akkauntini o'chirgan, {not_started} ta yangi botni ochmagan, {failed} ta xato. "
                              "Kimlar: panel → Ommaviy xabar → «Kimlarga yetmadi».")


def _post_mark(post: tuple[str, bytes, str] | None, buttons: str) -> str:
    """The history's and the audit log's note of what went with the text."""
    return ({"photo": "🖼 Rasm · ", "video": "🎬 Video · "}.get(post[0], "") if post else "") + \
        ("🔘 Tugmalar · " if buttons else "")


def _checked(data: dict) -> dict:
    if data.get("status") == "running" and time.time() - int(data.get("updated") or data.get("started") or 0) > 180:
        data["status"] = "stopped"  # the panel restarted mid-way (a deploy): the job is gone
    return data


async def _broadcasts() -> list[dict]:
    r = crm.redis()
    out = []
    for bid in await r.lrange(BROADCAST_IDS, 0, 19):
        data = await r.hgetall(BROADCAST_KEY.format(id=bid))
        if data:
            data["id"] = bid
            data["when"] = insights.fmt(datetime.fromtimestamp(int(data.get("started", 0)), timezone.utc))
            out.append(_checked(data))
    return out


@router.get("/broadcast", response_class=HTMLResponse)
async def broadcast_page(request: Request, audience: str = "all", lang: str = "any", started: str | None = None):
    if (r := guard(request, perm="broadcast")) is not None:
        return r
    audience = audience if audience in AUDIENCES else "all"
    lang = lang if lang in LANG_FILTERS else "any"
    async with get_session() as session:
        users, left_out = await _audience_split(session, audience, lang)
    langs = {code: sum(1 for u in users if (u.language or "uz") == code) for code in ("uz", "ru", "en")}
    posts = await broadcast_post.load()
    tg_post = None
    if "uz" in posts:
        tg_post = {"versions": [{
            "lang": code, "flag": broadcast_post.FLAGS[code], "name": broadcast_post.NAMES[code],
            "post": {**posts[code], "kind_label": broadcast_post.KINDS.get(posts[code].get("kind"), "Post"),
                     "when": insights.fmt(datetime.fromtimestamp(int(posts[code].get("at") or 0), timezone.utc))}
            if code in posts else None} for code in broadcast_post.LANGS]}
    return templates.TemplateResponse(request, "broadcast.html", {
        "active": "broadcast", "audiences": AUDIENCES, "lang_filters": LANG_FILTERS, "audience": audience, "lang": lang,
        "count": len(users), "langs": langs, "history": await _broadcasts(), "started": started, "tg_post": tg_post,
        "left_out": sum(left_out.values()),
        "left_out_detail": " · ".join(f"{reach.LABELS[w]}: {n}" for w, n in left_out.most_common() if w in reach.LABELS),
    })


@router.post("/broadcast/send")
async def broadcast_send(request: Request, audience: str = Form(...), lang: str = Form("any"),
                         text_uz: str = Form(""), text_ru: str = Form(""), text_en: str = Form(""),
                         buttons: str = Form(""), media: UploadFile | None = File(None)):
    if (r := guard(request, perm="broadcast")) is not None:
        return r
    post = None  # (kind, bytes, file name): a picture or a video with the text
    if media is not None and media.filename:
        kind = MEDIA_TYPES.get((media.content_type or "").split(";")[0].strip().lower())
        if kind is None:
            return RedirectResponse(f"/broadcast?audience={audience}&lang={lang}&started=mediatype", status_code=303)
        data = await media.read(MEDIA_LIMITS[kind] + 1)
        if not data or len(data) > MEDIA_LIMITS[kind]:
            return RedirectResponse(f"/broadcast?audience={audience}&lang={lang}&started=mediabig", status_code=303)
        post = (kind, data, media.filename[-80:])
    # Escaped first, then measured (2026-11-15: cut at 3500 and escaped after, a text full of & < > grew past
    # Telegram's 4096 characters and every single message failed).
    texts = {k: html.escape(v.strip(), quote=False) for k, v in (("uz", text_uz), ("ru", text_ru), ("en", text_en))}
    if not texts["uz"]:
        return RedirectResponse(f"/broadcast?audience={audience}&lang={lang}&started=empty", status_code=303)
    if any(len(v) > BROADCAST_MAX for v in texts.values()):
        return RedirectResponse(f"/broadcast?audience={audience}&lang={lang}&started=toolong", status_code=303)
    # A double submit (two clicks, a resent form) starts one broadcast, not two.
    fingerprint = hashlib.sha256("|".join([audience, lang, buttons, hashlib.sha256(post[1]).hexdigest() if post else "",
                                           *texts.values()]).encode()).hexdigest()[:24]
    if not await crm.redis().set(f"broadcast:once:{fingerprint}", "1", nx=True, ex=600):
        return RedirectResponse(f"/broadcast?audience={audience}&lang={lang}&started=dup", status_code=303)
    async with get_session() as session:
        users = await _audience(session, audience if audience in AUDIENCES else "all", lang if lang in LANG_FILTERS else "any")
    targets = [(u.id, u.language or "uz") for u in users]
    bid = datetime.now(timezone.utc).strftime("%y%m%d%H%M%S") + secrets.token_hex(2)
    r = crm.redis()
    await r.hset(BROADCAST_KEY.format(id=bid), mapping={
        "status": "running", "total": len(targets), "sent": 0, "failed": 0, "blocked": 0, "done": 0,
        "audience": AUDIENCES.get(audience, audience), "lang": LANG_FILTERS.get(lang, lang), "admin": admin_name(request),
        "preview": _post_mark(post, buttons) + text_uz.strip()[:160], "started": int(time.time()),
    })
    await r.lpush(BROADCAST_IDS, bid)
    await r.ltrim(BROADCAST_IDS, 0, 49)
    await audit(request, "broadcast", details=f"#{bid} · {AUDIENCES.get(audience)} · {LANG_FILTERS.get(lang)} · "
                                              f"{len(targets)} kishi · {_post_mark(post, buttons)}{text_uz.strip()[:200]}")
    _later(_broadcast_job(bid, targets, texts, post, bool(buttons)))
    return RedirectResponse(f"/broadcast?started={bid}", status_code=303)


@router.post("/broadcast/send_post")
async def broadcast_send_post(request: Request, audience: str = Form(...), lang: str = Form("any"),
                              buttons: str = Form("")):
    """The post an admin sent to the bot after /post (bot/utils/broadcast_post.py), copied to every student as it
    is: bold, links, premium emoji, its picture or video - the version of the student's language, or the Uzbek one."""
    if (r := guard(request, perm="broadcast")) is not None:
        return r
    posts = await broadcast_post.load()
    if "uz" not in posts:
        return RedirectResponse(f"/broadcast?audience={audience}&lang={lang}&started=nopost", status_code=303)
    post = posts["uz"]
    copy = broadcast_post.copies(posts)
    ids = ",".join(f"{code}:{c[0]}:{c[1]}" for code, c in sorted(copy.items()))
    fingerprint = hashlib.sha256(f"post|{audience}|{lang}|{buttons}|{ids}".encode()).hexdigest()[:24]
    if not await crm.redis().set(f"broadcast:once:{fingerprint}", "1", nx=True, ex=600):
        return RedirectResponse(f"/broadcast?audience={audience}&lang={lang}&started=dup", status_code=303)
    async with get_session() as session:
        users = await _audience(session, audience if audience in AUDIENCES else "all", lang if lang in LANG_FILTERS else "any")
    targets = [(u.id, u.language or "uz") for u in users]
    bid = datetime.now(timezone.utc).strftime("%y%m%d%H%M%S") + secrets.token_hex(2)
    mark = (f"📨 Telegram post ({''.join(broadcast_post.FLAGS[code] for code in broadcast_post.LANGS if code in posts)})"
            " · " + ("🔘 Tugmalar · " if buttons else ""))
    r = crm.redis()
    await r.hset(BROADCAST_KEY.format(id=bid), mapping={
        "status": "running", "total": len(targets), "sent": 0, "failed": 0, "blocked": 0, "done": 0,
        "audience": AUDIENCES.get(audience, audience), "lang": LANG_FILTERS.get(lang, lang), "admin": admin_name(request),
        "preview": mark + (post.get("preview") or broadcast_post.KINDS.get(post.get("kind"), ""))[:160],
        "started": int(time.time()),
    })
    await r.lpush(BROADCAST_IDS, bid)
    await r.ltrim(BROADCAST_IDS, 0, 49)
    await audit(request, "broadcast", details=f"#{bid} · {AUDIENCES.get(audience)} · {LANG_FILTERS.get(lang)} · "
                                              f"{len(targets)} kishi · {mark}{(post.get('preview') or '')[:200]}")
    _later(_broadcast_job(bid, targets, {}, None, bool(buttons), copy=copy))
    return RedirectResponse(f"/broadcast?started={bid}", status_code=303)


MISSED_ORDER = ("not_started", "blocked", "deleted")


@router.get("/broadcast/{bid}/missed", response_class=HTMLResponse)
async def broadcast_missed(request: Request, bid: str):
    """Who a broadcast did not reach, and why (2026-11-23): blocked the bot, deleted the account, never started the
    new bot (bot/utils/reach.py), or another error with Telegram's words. "Qaytdi" - the bot has seen them since."""
    if (r := guard(request, perm="broadcast")) is not None:
        return r
    rd = crm.redis()
    data = await rd.hgetall(BROADCAST_KEY.format(id=bid))
    if not data:
        return RedirectResponse("/broadcast", status_code=303)
    missed = await rd.hgetall(MISSED_KEY.format(id=bid))
    ids = [int(k) for k in missed if k.isdigit()]
    async with get_session() as session:
        found = {u.id: u for u in (await session.execute(select(User).where(User.id.in_(ids)))).scalars().all()} \
            if ids else {}
    still_out = await reach.out()
    rows = []
    for uid in ids:
        why = missed[str(uid)]
        u = found.get(uid)
        rows.append({
            "id": uid, "name": (u.full_name if u else None) or str(uid), "username": u.username if u else None,
            "lang": (u.language if u else None) or "uz",
            "created": insights.fmt(u.created_at, with_time=False) if u and u.created_at else "—",
            "_ts": u.created_at.timestamp() if u and u.created_at else 0,
            "why": why if why in reach.LABELS else "failed",
            "label": reach.LABELS.get(why) or "⚠️ Boshqa xato: " + why.removeprefix("failed:"),
            "back": why in reach.LABELS and uid not in still_out,
        })
    order = {w: i for i, w in enumerate(MISSED_ORDER)}
    rows.sort(key=lambda x: (order.get(x["why"], 9), x["_ts"]))
    counts = Counter(x["why"] for x in rows)
    data["id"] = bid
    data["when"] = insights.fmt(datetime.fromtimestamp(int(data.get("started", 0)), timezone.utc))
    return templates.TemplateResponse(request, "broadcast_missed.html", {
        "active": "broadcast", "b": _checked(data), "rows": rows, "counts": counts, "labels": reach.LABELS,
    })


@router.get("/broadcast/{bid}.json")
async def broadcast_status(request: Request, bid: str):
    if not logged_in(request):
        return JSONResponse({}, status_code=401)
    return JSONResponse(_checked(await crm.redis().hgetall(BROADCAST_KEY.format(id=bid))))


# --- 3. support inbox ----------------------------------------------------------------------------------------


@router.get("/support", response_class=HTMLResponse)
async def support_page(request: Request, f: str = "all"):
    if (r := guard(request)) is not None:
        return r
    async with get_session() as session:
        last = select(SupportMessage.user_id, func.max(SupportMessage.id).label("mid"),
                      func.count().filter(and_(SupportMessage.direction == "in", SupportMessage.read.is_(False)))
                      .label("unread"), func.count().label("n")).group_by(SupportMessage.user_id).subquery()
        rows = (await session.execute(
            select(SupportMessage, User, last.c.unread, last.c.n)
            .join(last, last.c.mid == SupportMessage.id).join(User, User.id == SupportMessage.user_id)
            .order_by(SupportMessage.created_at.desc()))).all()
    threads = [{"m": m, "user": u, "unread": unread, "n": n, "ago": insights.ago(m.created_at)} for m, u, unread, n in rows]
    if f == "unread":
        threads = [x for x in threads if x["unread"]]
    elif f == "waiting":
        threads = [x for x in threads if x["m"].direction == "in"]
    return templates.TemplateResponse(request, "support.html", {"active": "support", "threads": threads, "f": f})


@router.get("/support/{user_id}", response_class=HTMLResponse)
async def support_thread(request: Request, user_id: int, sent: str | None = None):
    if (r := guard(request)) is not None:
        return r
    async with get_session() as session:
        user = await session.get(User, user_id)
        if user is None:
            return RedirectResponse("/support", status_code=303)
        messages = (await session.execute(select(SupportMessage).where(SupportMessage.user_id == user_id)
                                          .order_by(SupportMessage.created_at))).scalars().all()
        await session.execute(update(SupportMessage).where(SupportMessage.user_id == user_id,
                                                           SupportMessage.direction == "in").values(read=True))
        await session.commit()
    for m in messages:
        m.when = insights.fmt(m.created_at)
    flash = {"1": "Javob yuborildi.", "0": "Javob yetkazilmadi: foydalanuvchi botni bloklagan bo'lishi mumkin.",
             "empty": "Javob bo'sh.",
             "closed": "Murojaat javobsiz yopildi — endi «javob kutmoqda» sanalmaydi."}.get(sent or "")
    return templates.TemplateResponse(request, "support_thread.html", {
        "active": "support", "user": user, "messages": messages, "flash": flash, "flash_bad": sent in ("0", "empty"),
        # the last message the student's: waiting for an answer - or for «✓ Javob shart emas»
        "waiting": bool(messages) and messages[-1].direction == "in",
    })


@router.post("/support/{user_id}/close")
async def support_close(request: Request, user_id: int):
    """«✓ Javob shart emas» (2026-11-16): a thread that needs no answer (a «rahmat») stops waiting - the badge,
    the reminders and the daily report count only the threads waiting for one. Kept in the thread as a note;
    nothing goes to the student."""
    if (r := guard(request)) is not None:
        return r
    async with get_session() as session:
        last = (await session.execute(select(SupportMessage.direction).where(SupportMessage.user_id == user_id)
                                      .order_by(SupportMessage.id.desc()).limit(1))).scalar_one_or_none()
    if last == "in":  # only a thread that waits (a double click adds nothing)
        await support_log.save(user_id, "out", kind=support_log.CLOSED, via="web", admin=admin_name(request))
        await audit(request, "support_closed", user_id)
    return RedirectResponse(f"/support/{user_id}?sent=closed", status_code=303)


@router.get("/support/file/{message_id}")
async def support_file(request: Request, message_id: int):
    """A photo from the conversation, fetched from Telegram with the bot's token."""
    if not logged_in(request):
        return Response(status_code=401)
    async with get_session() as session:
        m = await session.get(SupportMessage, message_id)
    if m is None or not m.file_id:
        return Response(status_code=404)
    # a photo sent before the move to @bandzilla_bot belongs to the old bot's token (2026-11-15: it 404'd)
    for token in filter(None, (settings.telegram_bot_token, settings.moved_bot_token)):
        bot = Bot(token)
        try:
            file = await bot.get_file(m.file_id)
            data = (await bot.download_file(file.file_path)).read()
            return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
        except Exception:  # noqa: BLE001
            continue
        finally:
            await bot.session.close()
    return Response(status_code=404)


# --- 4. errors -----------------------------------------------------------------------------------------------


async def errors_today() -> int:
    """Errors since midnight in Tashkent (a line repeated within 10 minutes is one)."""
    since = insights.day_start_utc().timestamp()
    try:
        raw = await crm.redis().lrange(crm.ERRORS_LOG_KEY, 0, crm.ERRORS_LOG_CAP - 1)
    except Exception:  # noqa: BLE001
        return 0
    n = 0
    for item in raw:
        try:
            e = json.loads(item)
        except ValueError:
            continue
        if e.get("at", 0) < since:
            break  # newest first
        n += e.get("level") == "ERROR"
    return n


@router.get("/errors", response_class=HTMLResponse)
async def errors_page(request: Request, src: str = "all"):
    if (r := guard(request)) is not None:
        return r
    raw = await crm.redis().lrange(crm.ERRORS_LOG_KEY, 0, crm.ERRORS_LOG_CAP - 1)
    entries = []
    for item in raw:
        try:
            e = json.loads(item)
        except ValueError:
            continue
        e["when"] = insights.fmt(datetime.fromtimestamp(e.get("at", 0), timezone.utc))
        e["ago"] = insights.ago(datetime.fromtimestamp(e.get("at", 0), timezone.utc))
        entries.append(e)
    sources = sorted({e.get("process", "bot") for e in entries})
    if src != "all":
        entries = [e for e in entries if e.get("process", "bot") == src]
    day = datetime.now(timezone.utc) - timedelta(days=1)
    return templates.TemplateResponse(request, "errors.html", {
        "active": "errors", "entries": entries, "src": src, "sources": sources, "owner": is_owner(request),
        "last24": sum(1 for e in entries if e.get("at", 0) >= day.timestamp()),
    })


@router.post("/errors/clear")
async def errors_clear(request: Request):
    if (r := guard(request, owner=True)) is not None:
        return r
    await crm.redis().delete(crm.ERRORS_LOG_KEY)
    await audit(request, "errors_cleared")
    return RedirectResponse("/errors", status_code=303)


# --- 5. block / unblock --------------------------------------------------------------------------------------


@router.post("/users/{user_id}/block")
async def user_block(request: Request, user_id: int, action: str = Form(...)):
    if (r := guard(request, perm="users")) is not None:
        return r
    if user_id in settings.admin_ids:
        return RedirectResponse(f"/users/{user_id}?blocked=admin", status_code=303)
    async with get_session() as session:
        user = await session.get(User, user_id)
        if user is None:
            return RedirectResponse("/users", status_code=303)
        user.status = UserStatus.denied if action == "block" else UserStatus.approved
        name = user.full_name
        await session.commit()
    await audit(request, "user_blocked" if action == "block" else "user_unblocked", user_id)
    await crm.post("users", f"{'⛔ <b>Bloklandi</b>' if action == 'block' else '✅ <b>Blokdan chiqarildi</b>'}: "
                            f"{html.escape(name, quote=False)} (<code>{user_id}</code>) · web panel, {html.escape(admin_name(request))}")
    return RedirectResponse(f"/users/{user_id}?blocked={action}", status_code=303)


# --- 6. Excel ------------------------------------------------------------------------------------------------


def _cell(value):
    """A value Excel won't run: names and texts come from Telegram users, so a
    leading = + - @ (a formula) is quoted (services/excel.py)."""
    return excel_safe(value)


def _xlsx(title: str, headers: list[str], rows: list[list]) -> Response:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="062018")
        c.alignment = Alignment(vertical="center")
    for row in rows:
        ws.append([_cell(v) for v in row])
    for i, h in enumerate(headers, 1):
        width = max([len(str(h))] + [len(str(r[i - 1] or "")) for r in rows[:500]])
        ws.column_dimensions[ws.cell(1, i).column_letter].width = min(max(10, width + 2), 60)
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    name = f"Bandzilla_{title}_{datetime.now(timezone.utc) + timedelta(hours=5):%Y-%m-%d}.xlsx"
    return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _local(dt):
    return (dt + timedelta(hours=5)).replace(tzinfo=None) if dt else None


@router.get("/export/users.xlsx")
async def export_users(request: Request):
    if (r := guard(request, perm="users")) is not None:
        return r
    async with get_session() as session:
        data = await insights.users_overview(session, None, "all", limit=10 ** 6)
    rows = [[r["user"].id, r["user"].full_name, r["user"].username, r["user"].language, r["user"].role.value,
             r["user"].status.value, r["user"].coin_balance, r["writing"], r["speaking"], r["paid"],
             _local(r["user"].created_at), _local(r["last_active"]), r["user"].referred_by] for r in data["rows"]]
    await audit(request, "export", details="users.xlsx")
    return _xlsx("Foydalanuvchilar", ["ID", "Ism", "Username", "Til", "Rol", "Holat", "Coin", "Writing", "Speaking",
                                      "To'lagan (so'm)", "Qo'shilgan", "Oxirgi faollik", "Taklif qilgan ID"], rows)


@router.get("/export/payments.xlsx")
async def export_payments(request: Request):
    if (r := guard(request, perm="users")) is not None:
        return r
    async with get_session() as session:
        payments = (await session.execute(select(PaymentRequest).order_by(PaymentRequest.created_at.desc()))).scalars().all()
        ids = {p.user_id for p in payments}
        users = {u.id: u for u in (await session.execute(select(User).where(User.id.in_(ids)))).scalars()} if ids else {}
    kinds = {PaymentKind.credits: "coin", PaymentKind.gift: "sovg'a", PaymentKind.subscription: "obuna"}
    rows = [[p.id, _local(p.created_at), p.user_id, users[p.user_id].full_name if p.user_id in users else "",
             (users[p.user_id].username or "") if p.user_id in users else "", kinds.get(p.kind, p.kind.value), p.coins,
             p.amount, p.status.value, _local(p.reviewed_at), "web panel" if p.reviewed_by == -1 else p.reviewed_by]
            for p in payments]
    await audit(request, "export", details="payments.xlsx")
    return _xlsx("Tolovlar", ["#", "Sana", "Foydalanuvchi ID", "Ism", "Username", "Turi", "Coin", "Summa (so'm)",
                              "Holat", "Ko'rib chiqildi", "Kim"], rows)


@router.get("/export/checks.xlsx")
async def export_checks(request: Request):
    if (r := guard(request, perm="users")) is not None:
        return r
    async with get_session() as session:
        data = await insights.all_checks(session, "all", "all", limit=100000)
    rows = [[c["kind"].upper(), c["id"], _local(c["at"]), c["user_id"], c["user"].full_name if c["user"] else "",
             c["label"], c["band"], c["criteria"], c["coins"], c["paid"], round(c["cost"], 4)] for c in data["rows"]]
    await audit(request, "export", details="checks.xlsx")
    return _xlsx("Tekshiruvlar", ["Modul", "#", "Sana", "Foydalanuvchi ID", "Ism", "Turi", "Band", "Mezonlar", "Coin",
                                  "To'lov", "AI xarajat ($)"], rows)


# --- 7. admins and the audit log -----------------------------------------------------------------------------


@router.get("/admins", response_class=HTMLResponse)
async def admins_page(request: Request, done: str | None = None):
    if (r := guard(request, owner=True)) is not None:
        return r
    return await _admins_page(request, done)


async def _admins_page(request: Request, done: str | None = None, recovery: list[str] | None = None):
    async with get_session() as session:
        admins = (await session.execute(select(WebAdmin).order_by(WebAdmin.created_at))).scalars().all()
    password = await _take_new_password(request)
    flash = {"created": "Admin qo'shildi.", "reset": "Yangi parol berildi.", "toggled": "Holat o'zgartirildi.",
             "exists": "Bu login band.", "bad": "Login: 3-30 ta lotin harfi, raqam yoki _ .",
             "perms": "Huquqlar saqlandi.", "unlinked": "Telegram uzildi: keyingi kirishda u yangidan ulanadi.",
             "nolink": "Avval Telegram'ni ulang (panelga qayta kirganda), keyin tiklash kodlarini yarating."
             }.get(done or "")
    links = await panel_access.all_linked()
    for a in admins:
        a.last = insights.ago(a.last_login_at) if a.last_login_at else "hali kirmagan"
        a.perm_set = panel_access.perms_of(a.perms)
        a.tg = links.get(a.username)
    owner_tg = links.get(settings.web_admin_username)
    return templates.TemplateResponse(request, "admins.html", {
        "active": "admins", "admins": admins, "flash": flash, "flash_bad": done in ("exists", "bad", "nolink"),
        "password": password, "owner_login": settings.web_admin_username, "perms": panel_access.PERMS,
        "owner_tg": owner_tg, "recovery": recovery, "two_fa": panel_access.enabled(),
        "recovery_left": await panel_access.recovery_left(settings.web_admin_username) if owner_tg else 0,
    })


@router.post("/admins/create")
async def admins_create(request: Request, username: str = Form(...), name: str = Form(...), role: str = Form("admin"),
                        perms: list[str] = Form([])):
    if (r := guard(request, owner=True)) is not None:
        return r
    username = username.strip().lower()
    if not (3 <= len(username) <= 30 and all(c.isascii() and (c.isalnum() or c == "_") for c in username)) \
            or username == settings.web_admin_username:
        return RedirectResponse("/admins?done=bad", status_code=303)
    password = secrets.token_urlsafe(10)
    async with get_session() as session:
        if (await session.execute(select(WebAdmin).where(WebAdmin.username == username))).scalar_one_or_none():
            return RedirectResponse("/admins?done=exists", status_code=303)
        chosen = "" if role == "teacher" else panel_access.perms_value(perms)
        session.add(WebAdmin(username=username, name=name.strip()[:100] or username, password_hash=hash_password(password),
                             role="teacher" if role == "teacher" else "admin", perms=chosen))
        await session.commit()
    await _keep_new_password(request, username, password)
    await audit(request, "admin_created", details=username + (" (ustoz)" if role == "teacher" else (
        " · " + (chosen or VIEW_ONLY))))
    return RedirectResponse("/admins?done=created", status_code=303)


async def _keep_new_password(request: Request, username: str, password: str) -> None:
    """A new admin's password, shown once on the next page: kept on the server for 5 minutes under a random
    reference - never in the session cookie (signed, not encrypted: 2026-11-15)."""
    ref = secrets.token_urlsafe(12)
    await crm.redis().set(f"panel:newpw:{ref}", json.dumps({"username": username, "password": password}), ex=300)
    request.session["new_password_ref"] = ref


async def _take_new_password(request: Request) -> dict | None:
    ref = request.session.pop("new_password_ref", None)
    if not ref:
        return None
    key = f"panel:newpw:{ref}"
    raw = await crm.redis().get(key)
    await crm.redis().delete(key)
    return json.loads(raw) if raw else None


@router.post("/admins/{admin_id}/{action}")
async def admins_change(request: Request, admin_id: int, action: str):
    if (r := guard(request, owner=True)) is not None:
        return r
    from webapp import sessions

    if action in ("perms", "unlink"):
        return await _admins_perms_or_unlink(request, admin_id, action)
    async with get_session() as session:
        a = await session.get(WebAdmin, admin_id)
        if a is None or action not in ("reset", "toggle"):
            return RedirectResponse("/admins", status_code=303)
        if action == "reset":
            password = secrets.token_urlsafe(10)
            a.password_hash = hash_password(password)
            new_password = password
        else:
            a.active = not a.active
            new_password = None
        username, active = a.username, a.active
        await session.commit()
    _valid_cache.pop(username, None)
    if new_password is not None:
        await _keep_new_password(request, username, new_password)
    if action == "reset" or not active:
        await sessions.end_all(username)  # a new password or a switched-off admin: every sign-in of theirs ends
        await panel_access.forget_devices(username)  # and no browser of theirs skips the code any more
    await audit(request, "admin_password_reset" if action == "reset" else ("admin_enabled" if active else "admin_disabled"),
                details=username)
    return RedirectResponse(f"/admins?done={'reset' if action == 'reset' else 'toggled'}", status_code=303)


VIEW_ONLY = "faqat ko'rish"


async def _admins_perms_or_unlink(request: Request, admin_id: int, action: str):
    """The owner's "Huquqlar" form (role + ticks) for one admin, or "Telegram'ni uzish"."""
    from webapp import sessions

    role_changed = False
    async with get_session() as session:
        a = await session.get(WebAdmin, admin_id)
        if a is None:
            return RedirectResponse("/admins", status_code=303)
        username = a.username
        if action == "perms":
            form = await request.form()
            role = "teacher" if form.get("role") == "teacher" else "admin"
            new_perms = "" if role == "teacher" else panel_access.perms_value(form.getlist("perms"))
            role_changed = (a.role or "admin") != role
            a.role, a.perms = role, new_perms
            await session.commit()
    _valid_cache.pop(username, None)
    if action == "unlink":
        if await panel_access.unlink(username):
            await sessions.end_all(username)
            await audit(request, "2fa_unlinked", details=username)
        return RedirectResponse("/admins?done=unlinked", status_code=303)
    if role_changed:
        await sessions.end_all(username)  # the role is read at sign-in: they sign in again
    await audit(request, "admin_perms", details=f"{username}: " + (
        "ustoz" if role == "teacher" else (new_perms or VIEW_ONLY)))
    return RedirectResponse("/admins?done=perms", status_code=303)


@router.post("/security/owner-unlink")
async def security_owner_unlink(request: Request):
    """The owner's own Telegram for the sign-in codes is unlinked (a new phone, a new account): the next
    sign-in links one again - only an ADMIN_TELEGRAM_IDS account."""
    if (r := guard(request, owner=True)) is not None:
        return r
    if await panel_access.unlink(settings.web_admin_username):
        await audit(request, "2fa_unlinked", details=settings.web_admin_username)
    return RedirectResponse("/admins?done=unlinked", status_code=303)


@router.post("/security/recovery", response_class=HTMLResponse)
async def security_recovery(request: Request):
    """8 new one-time recovery codes for the owner (the old ones stop working), shown on this page once."""
    if (r := guard(request, owner=True)) is not None:
        return r
    codes = await panel_access.new_recovery_codes(settings.web_admin_username)
    if codes is None:
        return RedirectResponse("/admins?done=nolink", status_code=303)
    await audit(request, "2fa_recovery", details="8 ta yangi kod")
    return await _admins_page(request, recovery=codes)


ACTION_NAMES = {
    "login": "Kirdi", "login_failed": "Kirish xato", "payment_approve": "To'lovni tasdiqladi",
    "payment_reject": "To'lovni rad etdi", "coins": "Coin o'zgartirdi", "message": "Xabar yozdi",
    "support_closed": "Murojaatni javobsiz yopdi",
    "broadcast": "Ommaviy xabar", "user_blocked": "Blokladi", "user_unblocked": "Blokdan chiqardi",
    "test_window_on": "Test rejimini yoqdi", "test_window_off": "Test rejimini o'chirdi", "tester_add": "Sinovchi qo'shdi",
    "tester_remove": "Sinovchini olib tashladi", "openai_balance": "OpenAI balansini kiritdi",
    "teacher_bands": "Ustoz bahosini qo'ydi", "payment_test": "To'lovni test deb belgiladi",
    "payment_real": "To'lovni haqiqiy deb belgiladi",
    "azure_hours": "Azure limitini kiritdi", "admin_created": "Admin qo'shdi", "admin_password_reset": "Parolni yangiladi",
    "admin_enabled": "Adminni yoqdi", "admin_disabled": "Adminni o'chirdi", "export": "Excel yuklab oldi",
    "errors_cleared": "Xatolarni tozaladi", "center_created": "Markaz yaratdi", "center_deleted": "Markazni o'chirdi",
    "teacher_revoked": "O'qituvchilikni oldi", "finance_entry": "Moliyaga yozuv qo'shdi",
    "signout_all": "Hamma qurilmalardan chiqardi", "backup_key_viewed": "Zaxira kalitini ko'rdi", "capacity": "Bir vaqtdagi tekshiruvlar sonini o'zgartirdi", "report_resent": "Hisobotni qayta yubordi",
    "check_test": "Tekshiruvni test deb belgiladi", "check_real": "Tekshiruvni haqiqiy deb belgiladi",
    "finance_entry_deleted": "Moliyadan yozuvni o'chirdi",
    "admin_perms": "Admin huquqlarini o'zgartirdi", "2fa_linked": "Kirish kodlari uchun Telegram uladi",
    "2fa_unlinked": "Kirish kodlari Telegram'ini uzdi", "2fa_recovery": "Tiklash kodlarini yaratdi",
    "login_code_failed": "Kirish kodi xato",
}


@router.get("/audit", response_class=HTMLResponse)
async def audit_page(request: Request, admin: str = "", action: str = ""):
    if (r := guard(request, owner=True)) is not None:
        return r
    async with get_session() as session:
        stmt = select(AdminAction).order_by(AdminAction.created_at.desc()).limit(400)
        if admin:
            stmt = stmt.where(AdminAction.admin == admin)
        if action:
            stmt = stmt.where(AdminAction.action == action)
        rows = (await session.execute(stmt)).scalars().all()
        admins = (await session.execute(select(AdminAction.admin).distinct())).scalars().all()
        ids = {a.target_user_id for a in rows if a.target_user_id}
        users = {u.id: u for u in (await session.execute(select(User).where(User.id.in_(ids)))).scalars()} if ids else {}
    for a in rows:
        a.when = insights.fmt(a.created_at)
        a.label = ACTION_NAMES.get(a.action, a.action)
    return templates.TemplateResponse(request, "audit.html", {
        "active": "audit", "rows": rows, "admins": sorted(admins), "admin": admin, "action": action,
        "actions": ACTION_NAMES, "users": users,
    })
