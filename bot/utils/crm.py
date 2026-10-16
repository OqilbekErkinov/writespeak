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
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
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
}

# words in a topic's name -> what it is for (`topic_key`); the first match wins
TOPIC_WORDS: dict[str, tuple[str, ...]] = {
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

QUEUE = "crm:queue"
PAUSE = 3.1  # seconds between messages
KEEP = 180 * 24 * 3600  # how long a sent message's id is remembered (edits, replies)
MAX_QUEUE = 5000
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


async def _push(job: dict) -> None:
    try:
        r = redis()
        await r.rpush(QUEUE, json.dumps({k: v for k, v in job.items() if v is not None}, ensure_ascii=False))
        await r.ltrim(QUEUE, -MAX_QUEUE, -1)
    except Exception as e:  # noqa: BLE001
        logger.warning("Admins' group: could not queue a message (%r)", e)


async def support_user(chat_id: int, message_id: int) -> int | None:
    """The student a message in 💬 Murojaatlar came from."""
    value = await redis().get(f"crm:support:{chat_id}:{message_id}")
    return int(value) if value else None


# --- Sending (bot process) ----------------------------------------------------------------------


async def run_sender(bot: Bot) -> None:
    """Sends the queued messages; also saves the OpenAI spend to the day's
    totals and posts the daily report once a day (bot/utils/crm_events.py)."""
    from bot.utils import crm_events

    last_daily_check = 0.0
    while True:
        try:
            await flush_spent()
            if time.monotonic() - last_daily_check > 60:
                last_daily_check = time.monotonic()
                await crm_events.daily_report_if_due()
            cfg = config()
            if not cfg:
                await asyncio.sleep(5)
                continue
            item = await redis().blpop([QUEUE], timeout=3)
            if item is None:
                continue
            await _deliver_with_retries(bot, cfg, json.loads(item[1]))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("Admins' group sender: %r", e)
            await asyncio.sleep(5)


async def _deliver_with_retries(bot: Bot, cfg: dict, job: dict) -> None:
    for attempt in range(5):
        try:
            await _deliver(bot, cfg, job)
            await asyncio.sleep(PAUSE)
            return
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except (TelegramNetworkError, TelegramServerError) as e:
            logger.warning("Admins' group: %r - trying again", e)
            await asyncio.sleep(5 * (attempt + 1))
        except TelegramForbiddenError as e:
            logger.warning("Admins' group: the bot can't post there (%s) - message dropped", e)
            await asyncio.sleep(PAUSE)
            return
        except TelegramBadRequest as e:
            if "thread not found" in str(e).lower():
                _forget_topic(job.get("topic", ""))
            logger.warning("Admins' group: %s - a %s message dropped", e, job.get("topic"))
            await asyncio.sleep(PAUSE)
            return
    logger.warning("Admins' group: gave up on a %s message", job.get("topic"))


def _cut(text: str, limit: int) -> str:
    """Telegram counts the visible characters; past the limit the formatting
    goes and the text is shortened."""
    if len(text) <= limit:
        return text
    plain = html.unescape(re.sub(r"<[^>]+>", "", text))
    return html.escape(plain[: limit - 2] + "…", quote=False)


async def _deliver(bot: Bot, cfg: dict, job: dict) -> None:
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
        return
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
    spent = {k: v for k, v in usage.take_spent().items() if v}
    if not spent:
        return {}
    key = spend_key(local_now().date())
    try:
        async with redis().pipeline(transaction=False) as pipe:
            for category, cost in spent.items():
                pipe.hincrbyfloat(key, category, cost)
            pipe.expire(key, 400 * 24 * 3600)
            await pipe.execute()
    except Exception as e:  # noqa: BLE001
        usage.give_back(spent)
        logger.warning("Admins' group: could not save the spend (%r)", e)
        return {}
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


class _ErrorForwarder(logging.Handler):
    """ERROR log lines - and the Flex fallback warnings - to the ⚠️ topic:
    the same line from the same place at most once every REPEAT seconds,
    saying how many more times it came in between."""

    REPEAT = 600
    WARNINGS_FROM = {"services.ai.openai_client"}

    def __init__(self, loop: asyncio.AbstractEventLoop):
        super().__init__(logging.WARNING)
        self.loop = loop
        self.seen: dict[tuple, list] = {}

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("bot.utils.crm"):
            return  # the group's own trouble, never sent to the group
        if record.levelno < logging.ERROR and record.name not in self.WARNINGS_FROM:
            return
        try:
            key = (record.name, record.lineno, str(record.msg))
            now = time.monotonic()
            seen = self.seen.get(key)
            if seen is not None and now - seen[0] < self.REPEAT:
                seen[1] += 1
                return
            again = seen[1] if seen is not None else 0
            self.seen[key] = [now, 0]
            exc = logging.Formatter().formatException(record.exc_info) if record.exc_info else record.exc_text
            asyncio.run_coroutine_threadsafe(_post_error(error_card(record, again, exc)), self.loop)
        except Exception:  # noqa: BLE001 - logging must never fail
            pass


async def _post_error(text: str) -> None:
    if not ready():
        return
    try:
        key = f"crm:errors:{local_now().date().isoformat()}"
        await redis().incr(key)
        await redis().expire(key, 60 * 24 * 3600)
    except Exception:  # noqa: BLE001
        pass
    await post("errors", text)


def error_card(record: logging.LogRecord, again: int, exc: str | None) -> str:
    message = record.getMessage()
    lines = []
    if "insufficient_quota" in f"{message}\n{exc or ''}":
        lines.append("🚨 <b>OpenAI balansi tugadi!</b> Tekshiruvlar ishlamayapti - platform.openai.com da "
                     "balansni to'ldiring.\n")
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


def install_error_forwarding() -> None:
    """Called once from inside the running event loop of a process (the bot,
    the importer)."""
    logging.getLogger().addHandler(_ErrorForwarder(asyncio.get_running_loop()))
