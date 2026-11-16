"""The admins' Telegram group: a forum supergroup whose topics show everything
that happens in the bot - new users, payments, every check with its PDF and
cost, errors, a daily summary, teachers and groups, and students' messages
to the admins (answered by replying to them in the group).

Setup: an admin sends /crm_setup in the group's General topic
(bot/handlers/crm.py); the bot creates the topics it doesn't have yet and
remembers them in config_path(). Topics the admins made themselves are
taken over by sending /crm_setup inside each of them first (`topic_key`
reads what it is for from its name). Until the General /crm_setup nothing is
sent anywhere.

Anything may call `post()` - the bot, the importer, the web panel: it only
adds the message to a Redis list. The bot process sends them in order
(`run_sender`), one every PAUSE seconds (Telegram lets a bot post 20
messages a minute in one group), so a burst never fails and a restart loses
nothing that was queued. What the messages say is built in
bot/utils/crm_events.py.

Two lanes (2026-11-16, before the launch): payments, students' messages,
errors, the reports, teachers and backups (HI_TOPICS) go through HI_QUEUE,
always sent before the normal lane (new users, the checks' cards, their
costs) - a burst of checks no longer holds a payment back for tens of
minutes. Each lane is trimmed on its own, and a trimmed message is told
(_report_trims). An ERROR log line becomes a card in ⚠️ Xatolar, its
repeats folded into one "×N" card per 10 minutes (Fold, _ErrorForwarder).
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import aiohttp
import redis.asyncio as aioredis
from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import FSInputFile

from bot.config import settings
from services.ai import usage

logger = logging.getLogger(__name__)

# key -> (topic name, icon colour - one of the six colours Telegram allows)
TOPICS: dict[str, tuple[str, int]] = {
    "users": ("🆕 Ro'yxatdan o'tganlar", 0x6FB9F0),
    "payments": ("💳 To'lovlar", 0x8EEE98),
    "writing": ("✍️ Writing", 0xFFD67E),
    "speaking": ("🎙 Speaking", 0xCB86DB),
    "costs": ("💰 Xarajatlar", 0xFF93B2),
    "errors": ("⚠️ Xatolar", 0xFB6F5F),
    "daily": ("📊 Kunlik hisobot", 0x6FB9F0),
    "teachers": ("👨‍🏫 O'qituvchi va guruhlar", 0x8EEE98),
    "support": ("💬 Murojaatlar", 0xFFD67E),
    "backup": ("💾 Zaxira", 0xCB86DB),  # the nightly encrypted backup (deploy/backup/offsite.sh), off the server
}

# words in a topic's name -> what it is for (`topic_key`); the first match wins
TOPIC_WORDS: dict[str, tuple[str, ...]] = {
    "backup": ("zaxira", "zahira", "backup", "rezerv", "резерв"),
    "writing": ("writing",),
    "speaking": ("speaking",),
    "users": ("ro'yxat", "royxat", "registr", "foydalanuvchi"),
    "payments": ("to'lov", "tolov", "payment", "chek"),
    "costs": ("xarajat", "harajat", "narx", "cost"),
    "errors": ("xato", "hato", "error"),
    "daily": ("kunlik", "hisobot", "daily", "report"),
    "teachers": ("o'qituvchi", "oqituvchi", "ustoz", "teacher", "guruh"),
    "support": ("murojaat", "murojat", "aloqa", "support"),
}

QUEUE = "crm:queue"  # the normal lane: new users, the checks' cards, their costs
# The high lane, always sent first (2026-11-16, before the launch): at PAUSE a message and ~2 messages a check, a
# burst of checks put payments, students' messages and alerts tens of minutes behind the checks' cards. A
# message's lane follows its topic, so a card and its later edits stay in order.
HI_QUEUE = "crm:queue:hi"
HI_TOPICS = frozenset({"errors", "payments", "support", "daily", "teachers", "backup"})
PAUSE = 3.1  # seconds between messages
KEEP = 180 * 24 * 3600  # how long a sent message's id is remembered (edits, replies)
MAX_QUEUE = 5000  # each lane is trimmed (oldest first) to its own cap
MAX_HI = 3000
TRIMMED_KEY = "crm:trimmed"  # lane -> messages trimmed away since the sender last said so (never silent)
TEXT_LIMIT, CAPTION_LIMIT = 4096, 1024
TZ = timezone(timedelta(hours=5), "Tashkent")  # no DST in Uzbekistan
CBU_URL = "https://cbu.uz/uz/arkhiv-kursov-valyut/json/USD/"


def local(dt: datetime) -> datetime:
    return dt.astimezone(TZ)


def local_now() -> datetime:
    return datetime.now(TZ)


# --- Where: the group and its topics ------------------------------------------------------------


def config_path() -> Path:
    """Next to the file storage, on the volume the bot, importer and web
    panel share."""
    return Path(settings.file_storage_path).parent / "crm.json"


_config: tuple[float, dict | None] = (-1.0, None)


def config() -> dict | None:
    """{"chat_id", "title", "topics": {key: thread id}, "setup_at",
    "backfill_at"}, or None until /crm_setup."""
    global _config
    path = config_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    if mtime != _config[0]:
        try:
            _config = (mtime, json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            logger.warning("Admins' group: %s is unreadable", path)
            return None
    return _config[1]


def save_config(cfg: dict) -> None:
    path = config_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def ready() -> bool:
    """/crm_setup has been sent in the group's General topic ("backfill_at"
    is when; everything before it went out as the backfill)."""
    cfg = config()
    return bool(cfg and cfg.get("chat_id") and cfg.get("topics") and cfg.get("backfill_at"))


def topic_key(name: str) -> str | None:
    """What a topic the admins made is for, from its name ("✍️ Writing
    tekshiruvlari" -> "writing"), or None."""
    text = name.lower()
    for apostrophe in "‘’`ʻʼ":
        text = text.replace(apostrophe, "'")
    return next((key for key, words in TOPIC_WORDS.items() if any(w in text for w in words)), None)


def may_answer(user) -> bool:
    """Who may answer students in 💬 Murojaatlar: the bot's admins and the
    people /crm_agents named (by @username or Telegram id)."""
    if user is None:
        return False
    if user.id in settings.admin_ids:
        return True
    agents = {str(a).lower() for a in (config() or {}).get("agents", [])}
    return str(user.id) in agents or (user.username or "").lower() in agents


def _forget_topic(key: str) -> None:
    """A topic someone deleted: /crm_setup makes it again."""
    cfg = config()
    if cfg and key in (cfg.get("topics") or {}):
        cfg = {**cfg, "topics": {k: v for k, v in cfg["topics"].items() if k != key}}
        save_config(cfg)


# --- Queue --------------------------------------------------------------------------------------

_redis: aioredis.Redis | None = None


def redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def post(
    topic: str,
    text: str,
    *,
    document: str | None = None,
    filename: str | None = None,
    photo: str | None = None,
    key: str | None = None,
    support_user: int | None = None,
    copy_from: tuple[int, int] | None = None,
) -> None:
    """Queues an HTML message for `topic`. `document`: a file sent with `text`
    as its caption; `photo`: a Telegram file id; `key`: the sent message is
    remembered under it, so `edit(key, ...)` can change it later;
    `support_user`: a reply to the sent message goes to this user
    (bot/handlers/crm.py); `copy_from`: (chat id, message id) copied instead
    of sending `text`. Never raises - the group must never break what a
    student is doing."""
    if not ready():
        return
    job = {
        "op": "copy" if copy_from else "send", "topic": topic, "text": text, "document": document,
        "filename": filename, "photo": photo, "key": key, "support_user": support_user,
        "copy_from": list(copy_from) if copy_from else None,
    }
    await _push(job)


async def edit(key: str, topic: str, text: str) -> None:
    """Changes the message remembered under `key` (a payment once decided, a
    check's cost once a voice was made); sends `text` to `topic` as a new
    message when there is none."""
    if not ready():
        return
    await _push({"op": "edit", "key": key, "topic": topic, "text": text})


def lane(topic: str | None) -> str:
    """The queue a topic's messages go through (HI_QUEUE is sent first)."""
    return HI_QUEUE if topic in HI_TOPICS else QUEUE


async def queued() -> dict[str, int]:
    """How many messages wait in each lane: {"hi": n, "normal": n}."""
    r = redis()
    return {"hi": int(await r.llen(HI_QUEUE)), "normal": int(await r.llen(QUEUE))}


async def _push(job: dict) -> None:
    key = lane(job.get("topic"))
    cap = MAX_HI if key == HI_QUEUE else MAX_QUEUE
    try:
        r = redis()
        async with r.pipeline(transaction=True) as pipe:
            pipe.rpush(key, json.dumps({k: v for k, v in job.items() if v is not None}, ensure_ascii=False))
            pipe.ltrim(key, -cap, -1)
            length, _ = await pipe.execute()
        if length > cap:
            # the oldest went: counted, and the sender tells the admins (_report_trims) - a lost payment card
            # or alert must never pass unnoticed (2026-11-16)
            await r.hincrby(TRIMMED_KEY, key, int(length) - cap)
            logger.warning("Admins' group: the %s lane is full (%d) - its oldest message dropped", key, cap)
    except Exception as e:  # noqa: BLE001
        logger.warning("Admins' group: could not queue a message (%r)", e)


async def support_user(chat_id: int, message_id: int) -> int | None:
    """The student a message in 💬 Murojaatlar came from."""
    value = await redis().get(f"crm:support:{chat_id}:{message_id}")
    return int(value) if value else None


# --- Sending (bot process) ----------------------------------------------------------------------


async def run_sender(bot: Bot) -> None:
    """Sends the queued messages - the high lane first; also saves the OpenAI spend to the day's
    totals, posts the daily report once a day (bot/utils/crm_events.py) and, every WATCH_EVERY seconds, runs
    the checks' watch (_watch: the day's spend cap, a long queue, folded costs, trimmed lanes)."""
    global _bot
    from bot.utils import balance, crm_events

    _bot = bot  # direct alerts from the rest of this process (tell_admins)
    try:
        moved = await _sort_lanes()
        if moved:
            logger.info("Admins' group: %d queued message(s) moved to the high lane", moved)
    except Exception as e:  # noqa: BLE001 - they are sent from the normal lane then
        logger.warning("Admins' group: the queued messages were not sorted into lanes (%r)", e)
    last_daily_check = last_probe = last_watch = 0.0
    failures = report_failures = 0
    while True:
        try:
            await flush_spent()
            if time.monotonic() - last_watch > WATCH_EVERY:
                last_watch = time.monotonic()
                await _watch(bot)
            if time.monotonic() - last_daily_check > 60:
                last_daily_check = time.monotonic()
                try:
                    await crm_events.daily_report_if_due()
                    await crm_events.teacher_report_if_due()
                    report_failures = 0
                except Exception as e:  # noqa: BLE001 - its own count: the loop's next turn succeeds anyway
                    report_failures += 1
                    logger.warning("Admins' group: the daily/teachers' report failed (%r)", e)
                    if report_failures >= REPORT_ALERT_AFTER:
                        await _alert_admins(bot, "report", "⚠️ Bandzilla: kunlik hisobotni guruhga yuborib "
                                            f"bo'lmayapti ({report_failures} marta ketma-ket): "
                                            f"{html.escape(repr(e))[:300]}")
            if time.monotonic() - last_probe > PROBE_EVERY and await balance.credit_paused():
                last_probe = time.monotonic()
                if await balance.probe_credit():
                    await credit_back(bot)
            cfg = config()
            if not cfg:
                await asyncio.sleep(5)
                continue
            item = await redis().blpop([HI_QUEUE, QUEUE], timeout=3)  # the high lane first, whenever it has any
            if item is not None and await _deliver_with_retries(bot, cfg, json.loads(item[1]), item[0]) == "waiting":
                continue  # the group can't be reached: not a healthy turn (no beat, the count stays)
            # a whole turn went well (2026-11-16: the count was reset at the top of every turn before, so it
            # never reached its limit and the direct alert below could not fire)
            failures = 0
            await _beat()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("Admins' group sender: %r", e)
            failures += 1
            if failures >= SENDER_ALERT_AFTER:
                # this loop is how every alert reaches the group - when it keeps failing (Redis away, a broken
                # message), the admins are told directly, once an hour (2026-11-15)
                await _alert_admins(bot, "sender", "⚠️ Bandzilla: adminlar guruhiga xabar yuborish to'xtab "
                                    f"qoldi ({failures} marta ketma-ket xato): {html.escape(repr(e))[:300]}")
            await asyncio.sleep(5)


PROBE_EVERY = 600  # seconds between test calls while checks are paused for want of OpenAI credit
SENDER_ALERT_AFTER = 10  # consecutive failures of the sender loop before the admins are told directly
REPORT_ALERT_AFTER = 3  # the daily/teachers' report failing this many minutes in a row: told directly
WAIT_ALERT_AFTER = 600  # seconds the group has been unreachable before the admins are told directly
DIRECT_ALERT_EVERY = 3600  # one direct alert of a kind an hour at most
WATCH_EVERY = 30  # seconds between the sender's watch turns (_watch)
WATCH_ALERT_AFTER = 5  # the watch failing this many turns in a row: told directly
NORMAL_TRIM_EVERY = 600  # seconds between two cards about the normal lane's trimmed messages

# The sender's own beat (2026-11-16): written after every turn that went well - a message delivered, or nothing
# to send. /healthz (webapp/main.py) reports "down: group" when it is older than SENDER_STALE, so UptimeRobot
# e-mails the owner even when the group (or Telegram for the bot) is the thing that is broken.
SENDER_BEAT_KEY = "health:crm_sender"
SENDER_STALE = 900
_last_beat = 0.0
_direct_sent: dict[str, float] = {}
_waiting_since: float | None = None


async def _beat() -> None:
    global _last_beat
    if time.monotonic() - _last_beat < 30:
        return
    _last_beat = time.monotonic()
    try:
        await redis().set(SENDER_BEAT_KEY, str(int(time.time())), ex=7 * 86400)
    except Exception:  # noqa: BLE001
        pass


async def _alert_admins(bot: Bot, kind: str, text: str, every: float = DIRECT_ALERT_EVERY) -> None:
    """A message straight to the bot admins, not through the group: for when the group itself is the problem,
    or an alert that must not wait in its queue. One of a kind every `every` seconds. Never raises."""
    if time.monotonic() - _direct_sent.get(kind, -every) < every:
        return
    _direct_sent[kind] = time.monotonic()
    logger.warning("Admins told directly (%s): %s", kind, re.sub(r"<[^>]+>", "", text)[:200])
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception:  # noqa: BLE001
            pass


_bot: Bot | None = None  # the sender's bot (run_sender): direct alerts from anywhere in the bot process
_background: set[asyncio.Task] = set()


def spawn(coro) -> asyncio.Task | None:
    """Runs `coro` in the background, kept referenced until it ends (an unreferenced task may be collected
    mid-way). Never raises."""
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except Exception:  # noqa: BLE001 - no loop: nothing to run it on
        coro.close()
        return None
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


def use_bot(bot: Bot) -> None:
    """The bot direct alerts go out with, before the sender runs (check_guard.recover at start-up)."""
    global _bot
    _bot = _bot or bot


def tell_admins(kind: str, text: str, every: float = DIRECT_ALERT_EVERY) -> None:
    """A direct message to the bot admins from anywhere in the bot process (2026-11-16: the spend cap, a burst
    of failed checks, a long queue) - sent in the background, so a check never waits for it; one of a kind
    every `every` seconds. Nothing in a process that runs no sender (the panel, the importer)."""
    if _bot is not None:
        spawn(_alert_admins(_bot, kind, text, every))


async def settle_background() -> None:
    """Waits for the background sends (tests; the shutdown)."""
    while _background:
        await asyncio.gather(*list(_background), return_exceptions=True)


# The messages queued before the lanes (2026-11-16) all sit in the normal lane: the high lane's topics are
# moved to it once, in their order, when the sender starts - so a payment waiting there is not sent after its
# own later edit.
_SORT_LANES = """
local items = redis.call('LRANGE', KEYS[1], 0, -1)
local keep, moved = {}, 0
for _, item in ipairs(items) do
  local ok, job = pcall(cjson.decode, item)
  if ok and type(job) == 'table' and type(job['topic']) == 'string'
      and string.find(ARGV[1], ',' .. job['topic'] .. ',', 1, true) then
    redis.call('RPUSH', KEYS[2], item)
    moved = moved + 1
  else
    keep[#keep + 1] = item
  end
end
if moved > 0 then
  redis.call('DEL', KEYS[1])
  for _, item in ipairs(keep) do redis.call('RPUSH', KEYS[1], item) end
end
return moved
"""


async def _sort_lanes() -> int:
    return int(await redis().eval(_SORT_LANES, 2, QUEUE, HI_QUEUE, "," + ",".join(sorted(HI_TOPICS)) + ","))


_watch_failures = 0
_last_normal_trim = 0.0


async def _watch(bot: Bot) -> None:
    """The sender's watch turn (every WATCH_EVERY s): the day's OpenAI spend against its cap, checks waiting
    long in the queue, the per-check cost cards folded while the queue was long (bot/utils/cost_guard.py),
    and the messages a full lane dropped. A turn that keeps failing is told directly. Never raises."""
    global _watch_failures
    from bot.utils import cost_guard  # noqa: PLC0415 - it imports this module

    try:
        await cost_guard.watch()
        await _report_trims(bot)
        _watch_failures = 0
    except Exception as e:  # noqa: BLE001
        _watch_failures += 1
        logger.warning("Admins' group: the watch turn failed (%r)", e)
        if _watch_failures >= WATCH_ALERT_AFTER:
            await _alert_admins(bot, "watch", "⚠️ Bandzilla: xarajat va navbat nazorati ishlamayapti "
                                f"({_watch_failures} marta ketma-ket): {html.escape(repr(e))[:300]}")


async def _report_trims(bot: Bot) -> None:
    """A full lane dropped its oldest messages (_push): the high lane's loss is told at once - in the group and
    directly - the normal lane's every NORMAL_TRIM_EVERY seconds at most."""
    global _last_normal_trim
    r = redis()
    hi = int(await r.hget(TRIMMED_KEY, HI_QUEUE) or 0)
    if hi:
        await r.hincrby(TRIMMED_KEY, HI_QUEUE, -hi)
        text = (f"🚨 <b>Adminlar guruhi navbati to'lib ketdi:</b> muhim bo'limlarning (to'lovlar, murojaatlar, "
                f"xatolar) eng eski <b>{hi}</b> ta xabari o'chib ketdi (navbatda {MAX_HI} tadan ortig'i "
                "saqlanmaydi). Guruh ishlayotganini va bot admin ekanini tekshiring; to'lovlarni admin "
                "paneldan ko'ring.")
        logger.warning("Admins' group: %d high-lane message(s) were dropped by the cap", hi)
        await _alert_admins(bot, "trim_hi", "⚠️ Bandzilla: " + text, every=600)
        if await r.llen(HI_QUEUE) < MAX_HI:  # a lane still full would drop one more for this very card
            await post("errors", text)
    if time.monotonic() - _last_normal_trim < NORMAL_TRIM_EVERY:
        return
    normal = int(await r.hget(TRIMMED_KEY, QUEUE) or 0)
    if normal:
        _last_normal_trim = time.monotonic()
        await r.hincrby(TRIMMED_KEY, QUEUE, -normal)
        await post("errors", f"⚠️ Adminlar guruhi navbati uzun: yangi foydalanuvchi va tekshiruv kartalaridan eng "
                             f"eskilari — <b>{normal}</b> ta — yuborilmay o'chdi (navbatda {MAX_QUEUE} tadan "
                             "ortig'i saqlanmaydi). Ular admin panelda bor.")

HEARTBEAT_KEY = "health:bot"  # deploy/watchdog/watchdog.sh alerts when it is older than its expiry
HEARTBEAT_EVERY = 60


async def run_heartbeat() -> None:
    """The bot's "I'm alive" for the host's watchdog (2026-11-15): every alert path goes through this process,
    so a dead or stuck bot could not report itself - the watchdog notices the key expiring instead."""
    while True:
        try:
            await redis().set(HEARTBEAT_KEY, str(int(time.time())), ex=HEARTBEAT_EVERY * 5)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("Heartbeat not written: %r", e)
        await asyncio.sleep(HEARTBEAT_EVERY)


async def credit_back(bot: Bot, entered: bool = False) -> None:
    """OpenAI works again (a top-up entered, or the test call went through): the group hears it and everyone
    who waited gets quota.back (bot/utils/checks_back.py)."""
    from bot.utils import checks_back

    await post("errors", "✅ <b>OpenAI yana ishlayapti - tekshiruvlar hamma uchun qayta ochildi.</b>"
               + ("" if entered else "\nBalans summasini kiritishni unutmang: <code>/balans SUMMA</code> - "
                  "shunda $5 va $3 qolganda yana ogohlantiraman."))
    sent = await checks_back.notify_back(bot)
    if sent:
        await post("errors", f"🔔 Kutib turgan {sent} kishiga «Bandzilla yana ishlayapti» xabari yuborildi.")


async def _deliver_with_retries(bot: Bot, cfg: dict, job: dict, source: str | None = None) -> str:
    """'sent', 'waiting' (the bot is not in the group: the message went back to the head of its lane - `source`,
    the lane it was taken from; by its topic when not given), or 'dropped'. A message lost for good, or a group
    unreachable for long, is told to the admins directly (2026-11-16: before, only a log line nobody sees said
    so)."""
    global _waiting_since
    topic = job.get("topic", "")
    source = source or lane(topic)
    for attempt in range(5):
        try:
            if await _deliver(bot, cfg, job) == "no_topic":
                await _alert_admins(bot, f"topic:{topic}", (
                    f"⚠️ Bandzilla: adminlar guruhida «{html.escape(str(topic))}» bo'limi yo'q — uning xabarlari "
                    "yo'qolyapti. Guruhning General qismida /crm_setup yuboring (yo'q bo'limlarni qayta ochadi)."))
                return "dropped"
            _waiting_since = None
            await asyncio.sleep(PAUSE)
            return "sent"
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except (TelegramNetworkError, TelegramServerError) as e:
            logger.warning("Admins' group: %r - trying again", e)
            await asyncio.sleep(5 * (attempt + 1))
        except (TelegramForbiddenError, TelegramBadRequest) as e:
            reason = str(e).lower()
            # (a copy's "chat not found" can be about the student's chat instead)
            if job.get("op") != "copy" and any(s in reason for s in ("not a member", "kicked", "chat not found")):
                # The bot isn't in the group (yet) - a new bot before it's added:
                # the message waits at the head of the queue.
                await _wait_for_group(job, e, source)
                if _waiting_since is None:
                    _waiting_since = time.monotonic()
                elif time.monotonic() - _waiting_since > WAIT_ALERT_AFTER:
                    waiting = sum((await queued()).values())
                    await _alert_admins(bot, "group_gone", (
                        "⚠️ Bandzilla: bot adminlar guruhiga yoza olmayapti "
                        f"({html.escape(str(e))[:200]}). Botni guruhga qayta qo'shing va admin qiling — "
                        f"{waiting} ta xabar (to'lovlar, murojaatlar, xatolar) navbatda kutyapti."))
                return "waiting"
            if isinstance(e, TelegramForbiddenError):
                logger.warning("Admins' group: the bot can't post there (%s) - message dropped", e)
                await _alert_admins(bot, "forbidden", (
                    f"⚠️ Bandzilla: bot adminlar guruhiga yoza olmayapti ({html.escape(str(e))[:200]}) — "
                    "xabarlar yo'qolyapti. Botni guruhda admin qiling (xabar yozish huquqi bilan)."))
                await asyncio.sleep(PAUSE)
                return "dropped"
            if "thread not found" in reason:
                _forget_topic(topic)
                await _alert_admins(bot, f"topic:{topic}", (
                    f"⚠️ Bandzilla: adminlar guruhidagi «{html.escape(str(topic))}» bo'limi o'chirilgan. "
                    "Guruhning General qismida /crm_setup yuboring — bo'lim qayta ochiladi."))
            logger.warning("Admins' group: %s - a %s message dropped", e, topic)
            await asyncio.sleep(PAUSE)
            return "dropped"
    logger.warning("Admins' group: gave up on a %s message", topic)
    return "dropped"


_last_wait_log = 0.0


async def _wait_for_group(job: dict, error: Exception, source: str | None = None) -> None:
    """The message back at the head of the lane it came from (each lane keeps its order; the high lane's are
    still the first tried once the group is back)."""
    global _last_wait_log
    await redis().lpush(source or lane(job.get("topic")), json.dumps(job, ensure_ascii=False))
    if time.monotonic() - _last_wait_log > 600:
        _last_wait_log = time.monotonic()
        logger.warning("Admins' group: the bot isn't in the group (%s) - messages wait until it is added", error)
    await asyncio.sleep(60)


def _cut(text: str, limit: int) -> str:
    """Telegram counts the visible characters; past the limit the formatting
    goes and the text is shortened."""
    if len(text) <= limit:
        return text
    plain = html.unescape(re.sub(r"<[^>]+>", "", text))
    return html.escape(plain[: limit - 2] + "…", quote=False)


async def _deliver(bot: Bot, cfg: dict, job: dict) -> str | None:
    chat_id = cfg["chat_id"]
    text = job.get("text") or ""
    r = redis()
    if job["op"] == "edit":
        saved = await r.get(f"crm:msg:{job['key']}")
        if saved:
            message_id, kind = saved.split("|")
            try:
                if kind == "c":
                    await bot.edit_message_caption(
                        chat_id=chat_id, message_id=int(message_id), caption=_cut(text, CAPTION_LIMIT)
                    )
                else:
                    await bot.edit_message_text(
                        _cut(text, TEXT_LIMIT), chat_id=chat_id, message_id=int(message_id),
                        disable_web_page_preview=True,
                    )
                return
            except TelegramBadRequest as e:
                if "not modified" in str(e):
                    return
                logger.info("Admins' group: could not edit %s (%s) - sending it anew", job["key"], e)
        job = {**job, "op": "send"}

    thread = (cfg.get("topics") or {}).get(job.get("topic"))
    if thread is None:
        logger.info("Admins' group has no %s topic - /crm_setup makes it", job.get("topic"))
        return "no_topic"
    where = {"message_thread_id": thread}
    kind = "t"
    if job["op"] == "copy":
        from_chat, from_message = job["copy_from"]
        sent_id = (await bot.copy_message(chat_id, from_chat, from_message, **where)).message_id
    elif job.get("document") and Path(job["document"]).is_file():
        caption = text if len(text) <= CAPTION_LIMIT else None
        if caption is None:
            await bot.send_message(chat_id, _cut(text, TEXT_LIMIT), disable_web_page_preview=True, **where)
        document = FSInputFile(job["document"], filename=job.get("filename"))
        sent_id = (await bot.send_document(chat_id, document, caption=caption, **where)).message_id
        kind = "c"
    elif job.get("photo"):
        try:
            sent = await bot.send_photo(chat_id, job["photo"], caption=_cut(text, CAPTION_LIMIT), **where)
            kind = "c"
        except TelegramBadRequest as e:
            if "file" not in str(e).lower():
                raise
            # A file id belongs to the bot it was sent to: a receipt sent to the
            # previous bot can't be shown by this one.
            text += "\n\n<i>(chek rasmi boshqa botga yuborilgan - bu yerda ko'rsatib bo'lmadi)</i>"
            sent = await bot.send_message(chat_id, _cut(text, TEXT_LIMIT), disable_web_page_preview=True, **where)
        sent_id = sent.message_id
    else:
        if job.get("document"):
            text += "\n\n<i>(PDF fayl serverda topilmadi)</i>"
        sent = await bot.send_message(chat_id, _cut(text, TEXT_LIMIT), disable_web_page_preview=True, **where)
        sent_id = sent.message_id
    if job.get("key"):
        await r.set(f"crm:msg:{job['key']}", f"{sent_id}|{kind}", ex=KEEP)
    if job.get("support_user"):
        await r.set(f"crm:support:{chat_id}:{sent_id}", job["support_user"], ex=KEEP)


# --- OpenAI spend by day (the daily report) -----------------------------------------------------


def spend_key(day: date) -> str:
    return f"crm:spend:{day.isoformat()}"


async def flush_spent() -> dict[str, float]:
    """Adds this process's OpenAI spend since the last flush to today's
    totals in Redis (by category - see services.ai.usage) and returns it."""
    from bot.utils import balance  # it imports this module

    azure = usage.take_azure()
    if azure:
        try:
            await balance.add_azure_seconds(azure)
        except Exception as e:  # noqa: BLE001
            usage.give_back_azure(azure)
            logger.warning("Admins' group: could not save Azure's audio seconds (%r)", e)
    spent = {k: v for k, v in usage.take_spent().items() if v}
    if not spent:
        return {}
    key = spend_key(local_now().date())
    try:
        async with redis().pipeline(transaction=False) as pipe:
            for category, cost in spent.items():
                pipe.hincrbyfloat(key, category, cost)
            # kept for good: the dashboard adds up every day since the start (2026-11-14)
            await pipe.execute()
    except Exception as e:  # noqa: BLE001
        usage.give_back(spent)
        logger.warning("Admins' group: could not save the spend (%r)", e)
        return {}
    try:  # the OpenAI balance counted down, the group told at $5, $3 and $0 (bot/utils/balance.py)
        await balance.add_spent(sum(spent.values()))
    except Exception as e:  # noqa: BLE001
        logger.warning("Admins' group: could not count the OpenAI balance down (%r)", e)
    return spent


_rate: dict = {}


async def usd_rate() -> float:
    """So'm per dollar: the Central Bank's rate of the day, else the setting."""
    today = local_now().date()
    if _rate.get("day") == today:
        return _rate["rate"]
    rate = _rate.get("rate") or settings.usd_uzs_rate
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as http:
            async with http.get(CBU_URL) as response:
                data = await response.json(content_type=None)
        rate = float(data[0]["Rate"])
    except Exception as e:  # noqa: BLE001
        logger.info("Admins' group: no CBU rate today (%r) - using %s", e, rate)
    _rate.update(day=today, rate=rate)
    return rate


# --- Errors -------------------------------------------------------------------------------------


class Fold:
    """The same thing again within `window` seconds is counted instead of posted (2026-11-16: a repeat within
    10 minutes used to vanish - not posted, not on the panel's errors page, not counted, and the "again" count
    showed only if it came back after the 10 minutes - so 40 failures looked like 1). When a window that saw
    repeats ends, `summary(entry)` runs once and the next window starts at once: a burst that goes on gives one
    summary per window, never a card per line; a window that saw none ends quietly, and the next one is posted
    in full again. `entry`: {"key", "head" (the first one's details), "n" repeats, "items" (the first ITEMS
    repeats' details), "sums" (the repeats' numbers added up), "since", "last" (epoch seconds)}. On the event
    loop's thread only."""

    ITEMS = 12

    def __init__(self, window: float, summary):
        self.window = window
        self.summary = summary
        self.open: dict = {}

    def hit(self, key, head=None, item=None, add: dict | None = None) -> bool:
        """True: the first in its window - post it in full; False: counted for the window's summary (`add`'s
        numbers summed into its "sums")."""
        entry = self.open.get(key)
        if entry is None:
            self._start(key, head)
            return True
        entry["n"] += 1
        entry["last"] = time.time()
        if item is not None and len(entry["items"]) < self.ITEMS:
            entry["items"].append(item)
        for name, value in (add or {}).items():
            entry["sums"][name] = entry["sums"].get(name, 0) + value
        return False

    def _start(self, key, head) -> None:
        self.open[key] = {"key": key, "head": head, "n": 0, "items": [], "sums": {}, "since": time.time(),
                          "last": None}
        asyncio.get_running_loop().call_later(self.window, self._close, key)

    def _close(self, key) -> None:
        entry = self.open.pop(key, None)
        if entry is None or not entry["n"]:
            return
        self._start(key, entry["head"])  # it goes on: folded into the next window's summary
        spawn(self._summarise(entry))

    async def _summarise(self, entry: dict) -> None:
        try:
            await self.summary(entry)
        except Exception:  # noqa: BLE001 - this module's own log lines never reach the group
            logger.warning("Admins' group: a repeats summary was not sent", exc_info=True)


def _minutes(seconds: float) -> int:
    return max(1, round(seconds / 60))


COUNT_FLUSH = 2.0  # seconds: the day's error count is added up in Redis this often at most


class _ErrorForwarder(logging.Handler):
    """ERROR log lines - and the Flex fallback warnings - to the ⚠️ topic and the panel's errors page: the same
    line from the same place once in full, its repeats folded (Fold: one "×N in the last 10 minutes" card - and
    errors-page entry - per window that had any). Every ERROR line counts in the day's error count (the daily
    report), the folded ones too.

    An error logged inside a running check (bot/utils/check_incidents.py running()) waits CLAIM_WAIT seconds:
    when the check ends because of that very exception, its incident card says it all - the reason, the stage,
    where in the code - and this card is not sent (one card per failed check, the owner's request
    2026-11-16); the errors page keeps its traceback either way."""

    REPEAT = 600
    CLAIM_WAIT = 20.0
    WARNINGS_FROM = {"services.ai.openai_client"}

    def __init__(self, loop: asyncio.AbstractEventLoop):
        super().__init__(logging.WARNING)
        self.loop = loop
        self.fold = Fold(self.REPEAT, self._summary)
        self.deferred: dict[int, tuple] = {}  # id(exception) -> (exception, record, exc text, message)
        self.claimed: dict[int, tuple] = {}  # id(exception) -> (exception, when): an incident card covers it
        self.counts: dict[str, int] = {}  # Tashkent day -> errors not yet added to its count
        self._count_due = False

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("bot.utils.crm"):
            return  # the group's own trouble, never sent to the group
        if record.levelno < logging.ERROR and record.name not in self.WARNINGS_FROM:
            return
        try:
            exc = logging.Formatter().formatException(record.exc_info) if record.exc_info else record.exc_text
            # the bot token sits in a failed file download's URL, a key or a DSN in a traceback: never shown
            # on the panel or in the group (2026-11-15)
            exc, message = redact(exc), redact(record.getMessage())
            error = None
            if record.levelno >= logging.ERROR and _in_check():
                # the exception it is about - also for a plain logger.error() inside an `except` (the deadline)
                error = record.exc_info[1] if record.exc_info else sys.exc_info()[1]
            self.loop.call_soon_threadsafe(self._handle, record, exc, message, error)
        except Exception:  # noqa: BLE001 - logging must never fail
            pass

    # --- on the event loop's thread ---

    def _handle(self, record: logging.LogRecord, exc: str | None, message: str, error) -> None:
        if record.levelno >= logging.ERROR:
            self._count(record)
        if error is not None:
            key = id(error)
            if self._claimed(key, error):
                spawn(log_error(record, 0, exc, message))
                return
            self.deferred[key] = (error, record, exc, message)
            self.loop.call_later(self.CLAIM_WAIT, self._release, key)
            return
        self._forward(record, exc, message)

    def _forward(self, record: logging.LogRecord, exc: str | None, message: str) -> None:
        head = {"name": record.name, "line": record.lineno, "level": record.levelname, "levelno": record.levelno,
                "message": message, "created": record.created, "exc": exc}
        if not self.fold.hit((record.name, record.lineno, str(record.msg)), head):
            return  # counted: the window's summary says how many
        spawn(log_error(record, 0, exc, message))
        spawn(_post_error(error_card(record, 0, exc, message)))

    def _claimed(self, key: int, error) -> bool:
        found = self.claimed.get(key)
        return found is not None and found[0] is error

    def _release(self, key: int) -> None:
        found = self.deferred.pop(key, None)
        if found is None:
            return
        error, record, exc, message = found
        if self._claimed(key, error):
            spawn(log_error(record, 0, exc, message))
            return
        self._forward(record, exc, message)

    def claim(self, error: BaseException) -> None:
        """A check's incident card covers `error`: its log line goes to the errors page only."""
        now = time.monotonic()
        self.claimed = {k: v for k, v in self.claimed.items() if now - v[1] < self.CLAIM_WAIT * 3}
        self.claimed[id(error)] = (error, now)
        found = self.deferred.pop(id(error), None)
        if found is not None and found[0] is error:
            _, record, exc, message = found
            spawn(log_error(record, 0, exc, message))

    def _count(self, record: logging.LogRecord) -> None:
        day = datetime.fromtimestamp(record.created, TZ).date().isoformat()
        self.counts[day] = self.counts.get(day, 0) + 1
        if not self._count_due:
            self._count_due = True
            self.loop.call_later(COUNT_FLUSH, lambda: spawn(self.flush_counts()))

    async def flush_counts(self) -> None:
        counts, self.counts, self._count_due = self.counts, {}, False
        if not counts:
            return
        try:
            async with redis().pipeline(transaction=False) as pipe:
                for day, n in counts.items():
                    pipe.incrby(f"crm:errors:{day}", n)
                    pipe.expire(f"crm:errors:{day}", 60 * 24 * 3600)
                await pipe.execute()
        except Exception:  # noqa: BLE001 - added with the next error's count
            for day, n in counts.items():
                self.counts[day] = self.counts.get(day, 0) + n

    async def _summary(self, entry: dict) -> None:
        head, n = entry["head"], entry["n"]
        minutes = _minutes(self.fold.window)
        await _log_entry({
            "at": entry.get("last") or time.time(), "level": head["level"], "logger": head["name"],
            "line": head["line"], "process": PROCESS, "again": n,
            "message": f"×{n} oxirgi {minutes} daqiqada: {head['message']}"[:2000], "exc": "",
        })
        await _post_error(error_summary_card(head, n, entry["since"], entry.get("last"), minutes))


_forwarder: _ErrorForwarder | None = None


def _in_check() -> bool:
    """A check is running in this context (bot/utils/check_incidents.py - only in the bot process)."""
    incidents = sys.modules.get("bot.utils.check_incidents")
    return bool(incidents is not None and incidents.running() is not None)


def claim_error(error: BaseException | None) -> None:
    """A check's incident card is about `error` (bot/utils/check_incidents.py): the error card its log line
    would make is not sent (the line stays on the errors page). On the event loop's thread."""
    if error is not None and _forwarder is not None:
        try:
            _forwarder.claim(error)
        except Exception:  # noqa: BLE001
            pass


_SECRET_PATTERNS = (
    re.compile(r"\d{6,12}:[A-Za-z0-9_-]{30,}"),  # a Telegram bot token (also inside .../bot<token>/...)
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),  # OpenAI keys
    re.compile(r"(?<=://)[^:/@\s]+:[^@/\s]+(?=@)"),  # user:password in a DSN / URL
)


def _secret_values() -> list[str]:
    """The .env values that must never be shown: every token, key, secret and password setting."""
    values = []
    for name, value in settings.model_dump().items():
        if isinstance(value, str) and len(value) >= 12 and any(w in name for w in ("token", "key", "secret",
                                                                                  "password")):
            values.append(value)
    return values


def redact(text: str | None) -> str | None:
    """`text` with every secret replaced by «***»."""
    if not text:
        return text
    for value in _secret_values():
        text = text.replace(value, "***")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("***", text)
    return text


ERRORS_LOG_KEY = "errors:log"  # the web panel's errors page (webapp/panel.py), newest first
ERRORS_LOG_CAP = 500


async def log_error(record: logging.LogRecord, again: int, exc: str | None, message: str | None = None) -> None:
    """Keeps an error for the web panel's errors page - the last
    ERRORS_LOG_CAP of them, with or without the admins' group."""
    await _log_entry({
        "at": record.created, "level": record.levelname, "logger": record.name, "line": record.lineno,
        "process": PROCESS, "message": (message if message is not None else redact(record.getMessage()))[:2000],
        "again": again,
        "exc": (exc or "")[-4000:],
    })


async def _log_entry(entry: dict) -> None:
    try:
        pipe = redis().pipeline()
        pipe.lpush(ERRORS_LOG_KEY, json.dumps(entry, ensure_ascii=False))
        pipe.ltrim(ERRORS_LOG_KEY, 0, ERRORS_LOG_CAP - 1)
        await pipe.execute()
    except Exception:  # noqa: BLE001 - logging must never fail
        pass


async def _post_error(text: str) -> None:
    """(The day's error count is the forwarder's: every ERROR line, the folded ones too.)"""
    if ready():
        await post("errors", text)


def _banners(message: str, exc: str | None) -> list[str]:
    lines = []
    if "insufficient_quota" in f"{message}\n{exc or ''}":
        lines.append("🚨 <b>OpenAI balansi tugadi!</b> Tekshiruvlar ishlamayapti - platform.openai.com da "
                     "balansni to'ldiring.\n")
    if message.startswith("Azure Pronunciation is not working"):  # services/ai/pronunciation.py
        lines.append("🚨 <b>Azure (Pronunciation) ishlamayapti!</b> Speaking tekshiruvlari davom etyapti, lekin "
                     "talaffuz o'lchanmayapti - Pronunciation bandi taxminiy qo'yilmoqda. portal.azure.com da "
                     "kalit, oylik limit va to'lovni tekshiring (sababi pastda).\n")
    return lines


def error_summary_card(head: dict, n: int, since: float, last: float | None, minutes: int) -> str:
    """The repeats of one error line in a window (Fold): how many, from when to when."""
    span = "–".join(datetime.fromtimestamp(t, TZ).strftime("%H:%M") for t in (since, last or time.time()))
    icon = "❌" if head.get("levelno", logging.ERROR) >= logging.ERROR else "⚠️"
    lines = [line.strip() for line in _banners(head["message"], head.get("exc"))]
    lines.append(f"🔁 {icon} <b>×{n}</b> — oxirgi {minutes} daqiqada yana · <code>{html.escape(head['name'])}</code>")
    lines.append(html.escape(head["message"][:400], quote=False))
    lines.append(f"<i>Bir xil xato ({span}): to'liq kartasi avvalroq yuborilgan, bu oraliqdagilari alohida "
                 "yuborilmadi. Har biri admin paneldagi «Xatolar» sahifasida bor.</i>")
    return "\n".join(lines)


def error_card(record: logging.LogRecord, again: int, exc: str | None, message: str | None = None) -> str:
    message = message if message is not None else redact(record.getMessage())
    lines = _banners(message, exc)
    icon = "❌" if record.levelno >= logging.ERROR else "⚠️"
    when = datetime.fromtimestamp(record.created, TZ).strftime("%d.%m %H:%M:%S")
    lines.append(f"{icon} <b>{record.levelname}</b> · <code>{html.escape(record.name)}</code> · {when}")
    lines.append(html.escape(message[:800], quote=False))
    user = re.search(r"user[ =](\d{5,})", message)
    if user:
        lines.append(f'👤 <a href="tg://user?id={user[1]}">{user[1]}</a>')
    if again:
        lines.append(f"<i>(oldingi xabardan beri yana {again} marta)</i>")
    if exc:
        lines.append(f"<pre>{html.escape(exc[-2500:], quote=False)}</pre>")
    return "\n".join(lines)


PROCESS = "bot"  # which process an error came from, on the panel's errors page


def install_error_forwarding(process: str = "bot") -> None:
    """Called once from inside the running event loop of a process (the bot,
    the importer, the web panel)."""
    global PROCESS, _forwarder
    PROCESS = process
    _forwarder = _ErrorForwarder(asyncio.get_running_loop())
    logging.getLogger().addHandler(_forwarder)
