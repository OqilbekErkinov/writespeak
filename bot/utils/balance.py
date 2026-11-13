"""The OpenAI and Azure balances, watched for the admins' group (the user, 2026-11-14: "not only when it runs
out - a message when $5 is left, and $3, and when it is gone; the same for Azure").

OpenAI tells an API key nothing about the credit left, so the bot counts it down: an admin enters the balance
after a top-up (the panel's settings page, or /balans 20 in the group), and every dollar recorded as spent
(services/ai/usage.py in every process, gathered by bot/utils/crm.flush_spent) comes off it. Prices come from
token counts, so it is an estimate - enter the real figure from platform.openai.com now and then. When OpenAI
itself answers "insufficient_quota" (services/ai/openai_client.py) the credit is gone for sure: the group hears
at once and checks pause for everyone but the admins (quota.paused, bot/utils/runtime.py), to come back -
with quota.back to everyone who waited - when a top-up is entered or a test call goes through again.

Azure (Pronunciation) has no money balance on its free tier, but a monthly allowance of audio: the seconds
assessed this month are counted against the hours the admins enter (the free F0 tier: 5; 0 = no limit).
"""
from __future__ import annotations

import logging
import time

from bot.utils import crm

logger = logging.getLogger(__name__)

OPENAI_KEY = "balance:openai"  # hash: usd (entered), base (spent total then), set_at, warned ("5,3,0")
SPENT_KEY = "balance:openai:spent"  # every USD recorded as spent, all processes, since the first flush
EMPTY_KEY = "balance:openai:empty"  # OpenAI said insufficient_quota: when (checks paused while it is there)
AZURE_KEY = "balance:azure"  # hash: hours (the monthly allowance; 0 = none), warned:<YYYY-MM>
OPENAI_WARN = (5.0, 3.0)  # dollars left that the group hears about (and 0)
AZURE_WARN = (1.0, 0.5)  # hours left of the month's allowance that the group hears about (and 0)
AZURE_DEFAULT_HOURS = 5.0  # Azure Speech's free F0 tier: 5 audio hours a month
CHECK_USD = 0.15  # about one check's OpenAI cost, for "about N checks left" (Writing $0.13-0.30, Speaking ~$0.1)
CACHE = 5  # seconds the pause flag is kept in memory
_paused: dict = {"at": 0.0, "value": False}


def _month() -> str:
    return crm.local_now().strftime("%Y-%m")


def azure_seconds_key(month: str | None = None) -> str:
    return f"balance:azure:seconds:{month or _month()}"


def _usd(value: float) -> str:
    return f"${value:,.2f}"


# --- OpenAI --------------------------------------------------------------------------------------------


async def spent_total() -> float:
    return float(await crm.redis().get(SPENT_KEY) or 0.0)


async def openai_status() -> dict | None:
    """{"entered", "set_at", "spent", "left"} since the last top-up entered, None before the first one."""
    data = await crm.redis().hgetall(OPENAI_KEY)
    if not data or "usd" not in data:
        return None
    entered, base = float(data["usd"]), float(data.get("base") or 0.0)
    spent = max(0.0, await spent_total() - base)
    return {"entered": entered, "set_at": data.get("set_at", ""), "spent": spent, "left": entered - spent}


async def set_openai(usd: float) -> bool:
    """An admin entered the balance (after a top-up, or the real figure from OpenAI's site). True when checks
    were paused for want of credit and now go on - the caller tells the waiting users (checks_back)."""
    r = crm.redis()
    await r.hset(OPENAI_KEY, mapping={"usd": f"{usd:.4f}", "base": f"{await spent_total():.6f}",
                                      "set_at": crm.local_now().strftime("%d.%m.%Y %H:%M"), "warned": ""})
    resumed = bool(await r.delete(EMPTY_KEY))
    _paused.update(at=0.0)
    logger.info("OpenAI balance entered: $%.2f%s", usd, " - checks open again" if resumed else "")
    return resumed


async def add_spent(usd: float) -> None:
    """Called with each flush of the recorded spend (crm.flush_spent): counts the balance down and tells the
    group when it passes $5, $3 and $0."""
    if usd <= 0:
        return
    r = crm.redis()
    await r.incrbyfloat(SPENT_KEY, usd)
    status = await openai_status()
    if status is None:
        return
    warned = set(filter(None, (await r.hget(OPENAI_KEY, "warned") or "").split(",")))
    passed = [limit for limit in (*OPENAI_WARN, 0.0) if status["left"] <= limit and f"{limit:g}" not in warned]
    if passed:  # one message, for the lowest limit passed - one big spend may pass two
        await r.hset(OPENAI_KEY, "warned", ",".join(sorted(warned | {f"{limit:g}" for limit in passed})))
        await crm.post("errors", openai_warning(status, min(passed)))


def openai_warning(status: dict, limit: float) -> str:
    left = max(0.0, status["left"])
    checks = int(left / CHECK_USD)
    if limit > 0:
        head = "🟡" if limit >= max(OPENAI_WARN) else "🟠"
        return (f"{head} <b>OpenAI balansi kamaymoqda: taxminan {_usd(left)} qoldi</b> (≈ {checks} ta tekshiruv).\n"
                f"platform.openai.com da to'ldiring, keyin guruhga <code>/balans SUMMA</code> yozing "
                f"(masalan <code>/balans 20</code>) - hisob yangidan boshlanadi.\n"
                f"<i>{status['set_at']} da {_usd(status['entered'])} kiritilgan, shundan beri {_usd(status['spent'])} "
                f"sarflandi. Bu bot hisobi (taxminiy) - aniq summa platform.openai.com da.</i>")
    return (f"🔴 <b>Bot hisobi bo'yicha OpenAI balansi tugadi</b> ({status['set_at']} da {_usd(status['entered'])} "
            f"kiritilgan, {_usd(status['spent'])} sarflandi).\nHozircha tekshiruvlar ishlayapti - OpenAI rad etsa, "
            f"bot ularni darhol to'xtatadi va shu yerga yozadi. platform.openai.com da to'ldirib, "
            f"<code>/balans SUMMA</code> yozing.")


async def out_of_credit() -> None:
    """OpenAI answered "insufficient_quota" (services/ai/openai_client.py, any process): checks pause for
    everyone but the admins, and the group hears it - once, however many calls fail."""
    try:
        r = crm.redis()
        if not await r.set(EMPTY_KEY, crm.local_now().strftime("%d.%m.%Y %H:%M"), nx=True):
            return
        _paused.update(at=0.0)
        logger.warning("OpenAI: insufficient_quota - checks paused for everyone but the admins")
        await crm.post("errors", (
            "🚨 <b>OpenAI balansi tugadi - OpenAI so'rovlarni rad etyapti.</b>\n"
            "Tekshiruvlar adminlardan boshqa hamma uchun avtomatik to'xtatildi: talabalarga «tez orada qaytamiz, "
            "coinlaringiz joyida» degan xabar chiqadi va ular kutayotganlar ro'yxatiga yoziladi.\n"
            "👉 platform.openai.com da balansni to'ldiring, keyin guruhga <code>/balans SUMMA</code> yozing "
            "(yoki panel → Sozlamalar). Bot har 10 daqiqada o'zi ham tekshiradi va OpenAI ishlashi bilan "
            "tekshiruvlarni qayta ochadi, kutganlarga xabar yuboradi."))
    except Exception:  # noqa: BLE001 - the failing check itself is reported anyway
        logger.warning("Could not record OpenAI's out-of-credit answer", exc_info=True)


async def credit_paused() -> bool:
    """Checks are paused because OpenAI said the credit is gone (kept CACHE seconds)."""
    if time.monotonic() - _paused["at"] < CACHE:
        return _paused["value"]
    try:
        value = bool(await crm.redis().exists(EMPTY_KEY))
    except Exception:  # noqa: BLE001 - Redis down: never pause on that alone
        value = False
    _paused.update(at=time.monotonic(), value=value)
    return value


async def probe_credit() -> bool:
    """While paused: one tiny call. True when OpenAI answers again - the pause is lifted (the caller tells
    the group and the waiting users)."""
    from services.ai.openai_client import client

    from bot.config import settings

    try:
        await client.chat.completions.create(model=settings.openai_model_sample, max_completion_tokens=200,
                                             messages=[{"role": "user", "content": "Reply with: ok"}],
                                             extra_body={"reasoning_effort": "minimal"})
    except Exception as e:  # noqa: BLE001 - still out of credit, or OpenAI down: try again later
        logger.info("OpenAI credit probe: still failing (%r)", e)
        return False
    deleted = await crm.redis().delete(EMPTY_KEY)
    _paused.update(at=0.0)
    return bool(deleted)


# --- Azure ---------------------------------------------------------------------------------------------


async def azure_hours() -> float:
    raw = await crm.redis().hget(AZURE_KEY, "hours")
    return AZURE_DEFAULT_HOURS if raw is None else float(raw)


async def set_azure_hours(hours: float) -> None:
    await crm.redis().hset(AZURE_KEY, "hours", f"{hours:g}")
    logger.info("Azure monthly allowance entered: %g hours", hours)


async def azure_status() -> dict:
    """{"hours" (the monthly allowance, 0 = none), "used" (hours this month), "left" (None without one)}."""
    hours = await azure_hours()
    used = float(await crm.redis().get(azure_seconds_key()) or 0.0) / 3600
    return {"hours": hours, "used": used, "left": (hours - used) if hours > 0 else None}


async def add_azure_seconds(seconds: float) -> None:
    """Called with each flush (crm.flush_spent): this month's assessed audio, and the group told when 1 hour,
    half an hour and nothing is left of the allowance."""
    if seconds <= 0:
        return
    r = crm.redis()
    key = azure_seconds_key()
    await r.incrbyfloat(key, seconds)
    await r.expire(key, 400 * 24 * 3600)
    status = await azure_status()
    if status["left"] is None:
        return
    field = f"warned:{_month()}"
    warned = set(filter(None, (await r.hget(AZURE_KEY, field) or "").split(",")))
    passed = [limit for limit in (*AZURE_WARN, 0.0) if status["left"] <= limit and f"{limit:g}" not in warned]
    if passed:
        await r.hset(AZURE_KEY, field, ",".join(sorted(warned | {f"{limit:g}" for limit in passed})))
        await crm.post("errors", azure_warning(status, min(passed)))


def azure_warning(status: dict, limit: float) -> str:
    used, hours = status["used"], status["hours"]
    if limit > 0:
        minutes = max(0, round(status["left"] * 60))
        return (f"🟡 <b>Azure (Pronunciation): bu oyning limitidan taxminan {minutes} daqiqa audio qoldi</b> "
                f"({used:.1f} / {hours:g} soat ishlatildi).\nLimit tugasa, Speaking tekshiruvlari davom etadi, "
                f"lekin talaffuz o'lchanmaydi (Pronunciation taxminiy qo'yiladi). Bepul (F0) tarifda limit har oy "
                f"1-sanada yangilanadi; uzluksiz ishlash uchun portal.azure.com da pullik (S0) tarifga o'ting va "
                f"guruhga <code>/balans azure 0</code> yozing.")
    return (f"🔴 <b>Azure (Pronunciation): bu oylik {hours:g} soatlik limit tugadi</b> ({used:.1f} soat ishlatildi).\n"
            f"Speaking tekshiruvlari davom etyapti, lekin talaffuz endi o'lchanmaydi. portal.azure.com da tarifni "
            f"tekshiring (pullik S0 da limit yo'q) va keyin <code>/balans azure 0</code> yozing.")


# --- Shown in the group, the panel and the daily report --------------------------------------------------


async def summary_lines() -> list[str]:
    """The balances in two lines (/balans, the daily report)."""
    out = []
    try:
        status = await openai_status()
        if await credit_paused():
            since = await crm.redis().get(EMPTY_KEY)
            out.append(f"🔴 OpenAI balansi tugagan ({since} dan beri) - tekshiruvlar to'xtatilgan")
        elif status is None:
            out.append("💳 OpenAI balansi: kiritilmagan - to'ldirgach <code>/balans SUMMA</code> yozing")
        else:
            out.append(f"💳 OpenAI balansi: taxminan <b>{_usd(max(0.0, status['left']))}</b> qoldi "
                       f"(≈ {int(max(0.0, status['left']) / CHECK_USD)} ta tekshiruv; {status['set_at']} da "
                       f"{_usd(status['entered'])} kiritilgan)")
        az = await azure_status()
        if az["left"] is None:
            out.append(f"🎙 Azure: bu oy {az['used']:.1f} soat audio (limit yo'q)")
        else:
            out.append(f"🎙 Azure: bu oy {az['used']:.1f} / {az['hours']:g} soat audio "
                       f"(≈ {max(0, round(az['left'] * 60))} daqiqa qoldi)")
    except Exception:  # noqa: BLE001
        logger.warning("Balance summary failed", exc_info=True)
    return out


def parse_amount(text: str) -> float | None:
    """"20", "$20", "20.5", "20,5" -> 20.5; None for anything else."""
    raw = text.strip().lstrip("$").replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if 0 <= value < 100000 else None
