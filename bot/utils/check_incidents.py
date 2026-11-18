"""What went wrong with a check, told once and clearly to the admins' group (the owner, 2026-11-16, before the
launch: "if something goes wrong during a check, detect it and tell us the exact reason").

- A check that stops - its coins given back (bot/utils/check_guard.py end()), a restart (recover()), a hold the
  sweep closed - gets ONE card in ⚠️ Xatolar: which check (kind, the student, the panel's link), the stage it
  stopped at, the reason in plain words (classify(): OpenAI's 429 / no credit / timeout / 5xx / bad request,
  Telegram, the PDF, an unreadable input, the deadline, a restart, or a bug with its file:line), whether the
  coins went back, and what it had already cost. Its own ERROR log line's card is then not sent
  (crm.claim_error) - the errors page keeps the traceback.
- A check whose input could not be read (an unreadable chart) is the student's side: a card in its module's
  topic, not an alarm.
- A check that ended with its report but degraded - the Pronunciation band estimated because Azure failed
  (services/ai/pronunciation.py), the report saved but not delivered (report_sent), slower than
  settings.slow_check_minutes - says so on its own card in its module's topic (crm_events.check_done ->
  card_lines), or on a card of its own there when that card never went.
- Normal refusals (not enough coins, busy, paused) never come here: they are not incidents.

The same reason again within 10 minutes is folded into one "×N" card (crm.Fold), and 3 checks stopping for a
reason not the student's within 15 minutes tell the admins - in the group and directly - at most once in 30
minutes (_burst). Nothing here ever raises into a check.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import time
import traceback
import uuid
from collections import Counter
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from bot.config import settings
from bot.utils import crm
from services.ai import usage

logger = logging.getLogger(__name__)

PANEL_URL = "https://admin.bandzilla.uz"
KINDS = {
    "writing_task1": "Writing Task 1", "writing_task2": "Writing Task 2", "speaking_part1": "Speaking Part 1",
    "speaking_part2": "Speaking Part 2", "speaking_part3": "Speaking Part 3", "speaking_full": "Speaking Full test",
}
STAGES = {
    "start": "boshlanish (baholashdan oldin)", "queue": "navbatda kutish", "grading": "baholash (OpenAI)",
    "pdf": "PDF hisobot yaratish", "saving": "bazaga saqlash", "delivery": "o'quvchiga yuborish",
    "restart": "bot qayta ishga tushishi",
}
FOLD_WINDOW = 600  # seconds: the same reason again within this is folded into one "×N" card
BURST_KEY = "incidents:failures"  # sorted set: the checks stopped lately (score: when)
BURST_TOLD_KEY = "incidents:burst_told"
BURST_WINDOW = 15 * 60
BURST_AT = 3  # this many stopped checks (not the student's fault) within BURST_WINDOW: the admins are told
BURST_EVERY = 30 * 60  # ...at most once in this long

ROOT = Path(__file__).resolve().parents[2]


# --- the running check -----------------------------------------------------------------------------------------

_running: ContextVar = ContextVar("running_check", default=None)


def running():
    """The check (check_guard.Hold) being run in this context - its task and the tasks it started - or None."""
    hold = _running.get()
    return hold if hold is not None and not getattr(hold, "ended", False) else None


def attach(hold) -> None:
    """begin(): the check runs in this context from now on - its OpenAI calls metered (usage.start_meter)."""
    try:
        hold.calls = usage.start_meter()
        _running.set(hold)
    except Exception:  # noqa: BLE001
        logger.warning("Check %s not attached", getattr(hold, "id", "?")[:8], exc_info=True)


def detach(hold) -> None:
    hold.ended = True
    if _running.get() is hold:
        _running.set(None)
    usage.stop_meter(getattr(hold, "calls", None))


def spent(hold) -> float:
    """What the check's own OpenAI calls cost so far (USD; its question's reading before it not included)."""
    return sum(c.cost for c in (getattr(hold, "calls", None) or []))


# --- why: an exception in plain words ------------------------------------------------------------------------


@dataclass
class Reason:
    code: str
    title: str  # Uzbek, for the card
    detail: str = ""  # the exception, short and redacted
    hint: str = ""  # what to do about it
    where: str = ""  # file:line in our code - for a bug
    user: bool = False  # the student's input: no alarm, not counted in a burst
    stage: str | None = None  # where it happened, when the exception says so (else the check's own stage)

    def stored(self, reason: str | None) -> str:
        """The hold's `reason` column (the panel's «To'xtagan tekshiruvlar» shows it)."""
        return f"{self.title} · {reason or self.detail}"[:300]


def _detail(e: BaseException) -> str:
    text = f"{type(e).__name__}: {e}".strip().rstrip(":")
    return (crm.redact(text) or "")[:240]


def _chain(error: BaseException) -> list[BaseException]:
    """The exception, its causes and contexts, an exception group's members - nearest first."""
    out, todo, seen = [], [error], set()
    while todo and len(out) < 12:
        e = todo.pop(0)
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        out.append(e)
        todo += list(getattr(e, "exceptions", None) or [])
        todo += [e.__cause__, e.__context__]
    return out


def _frames(error: BaseException) -> list[traceback.FrameSummary]:
    frames = []
    for e in _chain(error):
        try:
            frames += traceback.extract_tb(e.__traceback__)
        except Exception:  # noqa: BLE001
            pass
    return frames


def _ours(frame: traceback.FrameSummary) -> str | None:
    """`frame`'s path in this project ("services/ai/x.py"), None for a library's."""
    try:
        path = Path(frame.filename).resolve()
        rel = path.relative_to(ROOT).as_posix()
    except (ValueError, OSError):
        return None
    return None if "site-packages" in rel or rel.startswith(".") else rel


def where(error: BaseException) -> str:
    """The deepest line of our own code the exception went through: "bot/handlers/writing.py:812 (fn)"."""
    best = ""
    try:
        for frame in traceback.extract_tb(error.__traceback__):
            rel = _ours(frame)
            if rel:
                best = f"{rel}:{frame.lineno} ({frame.name})"
    except Exception:  # noqa: BLE001
        pass
    return best


def _through(frames, *parts: str) -> bool:
    """The exception passed through a file (a part of its path) or a function (a part of its name) in `parts`."""
    return any(any(p in f.filename.replace("\\", "/") or p in (f.name or "") for p in parts) for f in frames)


def _openai(e: BaseException) -> Reason | None:
    try:
        import openai  # noqa: PLC0415
    except ImportError:
        return None
    if not isinstance(e, openai.OpenAIError):
        return None
    status = getattr(e, "status_code", None)
    text = f"{getattr(e, 'code', '') or ''} {e}".lower()
    detail = _detail(e)
    if isinstance(e, openai.RateLimitError) or status == 429:
        if "insufficient_quota" in text or "credit_balance" in text:
            return Reason("openai_quota", "OpenAI: balans tugagan (insufficient_quota)", detail,
                          "platform.openai.com da balansni to'ldiring, keyin /balans SUMMA — tekshiruvlar o'zi "
                          "qayta ochiladi.")
        return Reason("openai_rate_limit", "OpenAI: so'rovlar limiti (429 rate limit)", detail,
                      "Bir vaqtda juda ko'p tekshiruv: bot o'zi kamaytiradi. Tez-tez bo'lsa — platform.openai.com "
                      "da hisob limitini (tier) oshiring yoki panelda «Tekshiruvlar sig'imi»ni kamaytiring.")
    if isinstance(e, openai.APITimeoutError):
        return Reason("openai_timeout", "OpenAI: javob kelmadi (timeout)", detail,
                      "OpenAI sekin ishlayapti — status.openai.com ni tekshiring.")
    if isinstance(e, openai.APIConnectionError):
        return Reason("openai_connection", "OpenAI: ulanib bo'lmadi (tarmoq)", detail,
                      "Server internetini va status.openai.com ni tekshiring.")
    if isinstance(e, openai.AuthenticationError) or status in (401, 403):
        return Reason("openai_auth", "OpenAI: API kalit rad etildi (401/403)", detail,
                      "OPENAI_API_KEY ni tekshiring (kalit o'chirilgan yoki almashtirilgan bo'lishi mumkin).")
    if isinstance(e, openai.InternalServerError) or (status or 0) >= 500:
        return Reason("openai_server", f"OpenAI: server xatosi ({status or '5xx'})", detail,
                      "OpenAI tomonidagi nosozlik — odatda o'zi o'tib ketadi; status.openai.com.")
    if isinstance(e, openai.BadRequestError) or status in (400, 404, 422):
        if "image" in text:
            return Reason("input_unreadable", "Rasm OpenAI tomonidan qabul qilinmadi", detail,
                          "O'quvchidan rasmni qayta yuborish so'raldi.", user=True)
        return Reason("openai_bad_request", f"OpenAI: so'rov rad etildi ({status or 400} bad request)", detail,
                      "Kod yoki model nomi bilan bog'liq — dasturchiga yuboring.")
    return Reason("openai_other", f"OpenAI: {type(e).__name__}" + (f" (HTTP {status})" if status else ""), detail)


def classify(error: BaseException | None, *, elapsed: float | None = None) -> Reason:
    """The reason a check stopped, from the exception that stopped it (and how long it had run)."""
    if error is None:
        return Reason("unknown", "Sababi noma'lum (xatosiz to'xtadi)")
    deadline = settings.check_deadline_seconds
    detail = _detail(error)
    if isinstance(error, asyncio.CancelledError):
        return Reason("cancelled", "Tekshiruv bekor qilindi (jarayon to'xtatildi)", detail)
    if type(error) is TimeoutError and elapsed is not None and elapsed >= deadline * 0.9:
        return Reason("deadline", f"Tekshiruv muddati o'tdi ({_minutes(deadline)} daqiqa)", detail,
                      "Odatda OpenAI juda sekin bo'lganda yoki navbat uzun bo'lganda. Tez-tez takrorlansa — "
                      "status.openai.com va panelda «Tekshiruvlar sig'imi»ni tekshiring.")
    frames = _frames(error)
    chain = _chain(error)
    for e in chain:
        found = _openai(e)
        if found is not None:
            return found
    names = {type(e).__name__ for e in chain}
    if names & {"ChartUnreadable", "ChartImageRejected", "UnsupportedInputError", "TooManyPages"} \
            or _through(frames, "services/ai/ocr", "document_parser.py"):
        return Reason("input_unreadable", "O'quvchi yuborgan rasm/fayl o'qilmadi (OCR)", detail,
                      "O'quvchidan qayta yuborish so'raldi, coin qaytarildi.", user=True)
    if names & {"OffQuestion"}:  # bot/handlers/writing.py, services/ai/answer_relevance.py (2026-11-18)
        return Reason("off_question", "Javob savolga mos emas (boshqa savolga yozilgan)", detail,
                      "Baholanmadi, coin qaytarildi; o'quvchidan shu savolga yangi javob so'raldi.", user=True)
    if names & {"ReceiveFailed"}:
        return Reason("telegram_download", "Telegram: faylni yuklab bo'lmadi", detail,
                      "Telegram yoki server tarmog'i — odatda vaqtinchalik.", stage="start")
    for e in chain:
        module = type(e).__module__ or ""
        if module.startswith("aiogram"):
            if _through(frames, "download", "input_extraction.py", "get_file"):
                return Reason("telegram_download", "Telegram: faylni yuklab bo'lmadi", detail,
                              "Telegram yoki server tarmog'i — odatda vaqtinchalik.")
            return Reason("telegram_delivery", "Telegram: o'quvchiga yuborib bo'lmadi", detail,
                          "O'quvchi botni bloklagan yoki Telegram ishlamayapti.", stage="delivery")
    if _through(frames, "services/pdf/", "weasyprint"):
        return Reason("pdf_render", "PDF hisobotni yaratib bo'lmadi", detail,
                      "Dasturchiga yuboring (PDF shabloni yoki shrift).", where=where(error), stage="pdf")
    for e in chain:
        module = type(e).__module__ or ""
        if module.startswith(("sqlalchemy", "asyncpg")) \
                or (isinstance(e, ConnectionRefusedError) and _through(frames, "asyncpg")):
            return Reason("db_unavailable", "Ma'lumotlar bazasi javob bermadi", detail,
                          "Postgres konteynerini va server diskini tekshiring.", where=where(error))
        if module.startswith("redis"):
            return Reason("redis_unavailable", "Redis javob bermadi", detail,
                          "Redis konteynerini va server xotirasini tekshiring.", where=where(error))
    if _through(frames, "pronunciation.py") and names & {"HTTPStatusError", "TimeoutException", "ConnectError",
                                                          "ReadTimeout", "ConnectTimeout"}:
        return Reason("azure_down", "Azure (Pronunciation) ishlamadi", detail,
                      "portal.azure.com da kalit, limit va to'lovni tekshiring.")
    if isinstance(error, FileNotFoundError):
        return Reason("file_missing", "Fayl serverda topilmadi", detail, "Dasturchiga yuboring.",
                      where=where(error))
    if isinstance(error, TimeoutError):
        return Reason("timeout", "Ichki kutish muddati o'tdi (TimeoutError)", detail, "Dasturchiga yuboring.",
                      where=where(error))
    return Reason("bug", f"Kutilmagan xato (bug): {type(error).__name__}", detail,
                  "Dasturchiga yuboring — traceback admin paneldagi «Xatolar» sahifasida.", where=where(error))


def explain(hold, error: BaseException | None, reason: str | None) -> Reason | None:
    """end(): the incident's reason when the check is stopping because of an exception (the one being handled
    where end() was called), None otherwise - a normal stop (no speech in the recording, a flow the student left)
    is no incident. Never raises."""
    if error is None:
        return None
    try:
        return classify(error, elapsed=time.monotonic() - hold.started)
    except Exception:  # noqa: BLE001
        logger.warning("Check incident not classified", exc_info=True)
        return Reason("bug", f"Kutilmagan xato: {type(error).__name__}", (reason or "")[:240])


# --- degraded: a report, but not as good as it should be -----------------------------------------------------

AZURE = {"azure_timeout": "Azure javob bermadi (vaqt tugadi)", "azure_429": "Azure so'rovlar limiti (429)",
         "azure_down": "Azure ishlamayapti"}


def degraded(code: str, detail: str = "") -> None:
    """The running check goes on worse than it should - services/ai/pronunciation.py: the Pronunciation band
    estimated because Azure failed (`code` azure_timeout / azure_429 / azure_down). Said on the check's card.
    Never raises."""
    try:
        hold = running()
        if hold is None:
            return
        if code in AZURE:
            note = {"code": code, "text": f"🎙 Pronunciation taxminiy qo'yildi — {AZURE[code]}", "detail": detail}
        else:
            note = {"code": code, "text": code, "detail": detail}
        if all(n["code"] != code for n in hold.notes):
            hold.notes.append({k: (crm.redact(str(v)) or "")[:200] for k, v in note.items()})
    except Exception:  # noqa: BLE001
        pass


def not_sent(error: BaseException) -> None:
    """check_guard.deliver() gave a send up: kept on the running check, for delivered()."""
    hold = running()
    if hold is not None:
        hold.delivery_error = error


def delivered(sent: bool) -> None:
    """report_sent(): the report is saved and paid; `sent` False - Telegram did not deliver it: said on the
    check's card, with why (that failure's own error card is then not sent: crm.claim_error)."""
    try:
        hold = running()
        if hold is None:
            return
        hold.saved = True
        hold.stage = "delivery"
        if sent or any(n["code"] == "undelivered" for n in hold.notes):
            return
        error = hold.delivery_error
        hold.notes.append({"code": "undelivered", "text": "📭 Hisobot saqlandi, lekin Telegram o'quvchiga "
                           "yetkazmadi", "detail": (_detail(error) + "; " if error is not None else "")
                           + "o'quvchida «qayta yuborish» tugmasi bor; admin panel → Tekshiruvlar → qayta yuborish"})
        crm.claim_error(error)
    except Exception:  # noqa: BLE001
        pass


async def _notes(hold) -> list[dict]:
    from bot.utils import cost_guard  # noqa: PLC0415

    notes = list(hold.notes)
    minutes = (await cost_guard.limits())["slow_minutes"]
    took = time.monotonic() - hold.started
    if minutes and took > minutes * 60:
        notes.append({"code": "slow", "text": f"🐢 Sekin: {_duration(took)} davom etdi (chegara {minutes:g} daq)",
                      "detail": ""})
    return notes


async def card_lines(hold=None) -> list[str]:
    """For the check's own card in its module's topic (crm_events.check_done): what went worse than it should,
    "" when nothing did. The check's incident is then told (no card of its own at its end). Never raises."""
    hold = hold or running()
    if hold is None:
        return []
    try:
        notes = await _notes(hold)
        hold.card_done = True
        if not notes:
            return []
        hold.reported = True
        lines = [f"⚠️ <b>Muammo:</b> {_esc(n['text'])}" + (f" <i>({_esc(n['detail'])})</i>" if n["detail"] else "")
                 for n in notes]
        return lines + [f'🔗 <a href="{PANEL_URL}/checks">Admin panel → Tekshiruvlar</a>']
    except Exception:  # noqa: BLE001
        logger.warning("Check %s: degraded lines not built", hold.id[:8], exc_info=True)
        return []


# --- told: the cards ---------------------------------------------------------------------------------------------


def _esc(text) -> str:
    return html.escape(str(text) if text is not None else "?", quote=False)


def _minutes(seconds: float) -> int:
    return max(1, round(seconds / 60))


def _duration(seconds: float) -> str:
    minutes, sec = divmod(int(round(seconds)), 60)
    return f"{minutes} daq {sec:02d} s" if minutes else f"{sec} s"


def _topic(hold_module: str) -> str:
    return "writing" if hold_module == "w" else "speaking"


async def _people(ids) -> dict:
    from db.database import get_session  # noqa: PLC0415
    from db.models import User  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415

    ids = {i for i in ids if i}
    if not ids:
        return {}
    try:
        async with get_session() as session:
            return {u.id: u for u in (await session.execute(select(User).where(User.id.in_(ids)))).scalars()}
    except Exception:  # noqa: BLE001 - the card still goes, with the ids alone
        logger.warning("Incident card: the students' names not read", exc_info=True)
        return {}


def _who(user_id: int, people: dict) -> str:
    from bot.utils.crm_events import _user  # noqa: PLC0415

    return _user(people.get(user_id), user_id)


def _name(user_id: int, people: dict) -> str:
    user = people.get(user_id)
    return _esc(user.full_name if user is not None and user.full_name else user_id)


def _coins_line(coins: int | None, payer_id: int | None, user_id: int, people: dict) -> str:
    if coins is None:
        return "↩️ Coin: holati noma'lum (sweep keyinroq yopadi)"
    if not coins:
        return "🆓 Coin olinmagan edi (bepul yoki test) — qaytariladigan narsa yo'q"
    if payer_id and payer_id != user_id:
        return f"↩️ <b>{coins} coin qaytarildi</b> — homiyga ({_name(payer_id, people)})"
    return f"↩️ <b>{coins} coin qaytarildi</b> o'quvchiga"


async def _usd_line(usd: float) -> str:
    if usd <= 0:
        return ""
    rate = await crm.usd_rate()
    return f"💸 OpenAI'ga ketgan: ${usd:.4f} ≈ {round(usd * rate):,} so'm (qaytmaydi)".replace(",", " ")


async def _post_summary(entry: dict) -> None:
    """A folded window's "×N" card (crm.Fold): the same reason's repeats."""
    head, n, sums = entry["head"], entry["n"], entry.get("sums") or {}
    people = await _people(i["user_id"] for i in entry["items"])
    lines = [f"🔁 <b>×{n}</b> tekshiruv yana shu sabab bilan to'xtadi — oxirgi {_minutes(FOLD.window)} daqiqada",
             f"❗ {_esc(head['title'])}"]
    for item in entry["items"]:
        lines.append(f"• {KINDS.get(item['kind'], item['kind'])} · {_name(item['user_id'], people)} "
                     f"<code>{item['user_id']}</code>" + (f" · {item['coins']} coin qaytdi" if item["coins"] else ""))
    if n > len(entry["items"]):
        lines.append(f"• … yana {n - len(entry['items'])} ta")
    if sums.get("coins"):
        lines.append(f"↩️ Jami qaytarilgan: {sums['coins']} coin")
    usd = await _usd_line(sums.get("usd", 0.0))
    if usd:
        lines.append(usd.replace("OpenAI'ga ketgan", "Jami OpenAI'ga ketgan"))
    lines.append(f'🔗 <a href="{PANEL_URL}/checks/stopped">Admin panel → To\'xtagan tekshiruvlar</a>')
    await crm.post(head["topic"], "\n".join(lines))


FOLD = crm.Fold(FOLD_WINDOW, _post_summary)


async def failed(hold, why: Reason, coins: int | None, payer_id: int | None, error: BaseException | None) -> None:
    """A check stopped (end() gave its coins back): its card, folded with the same reason's repeats, counted for
    a burst. Its own log line's card is claimed (not sent)."""
    usd = spent(hold)
    topic = _topic(hold.module) if why.user else "errors"
    first = FOLD.hit(("fail", why.code, why.where, topic), {"title": why.title, "topic": topic},
                     {"kind": hold.kind, "user_id": hold.user_id, "coins": coins or 0},
                     {"coins": coins or 0, "usd": usd})
    crm.claim_error(error)
    if not why.user:
        await _burst(why, hold.kind, hold.user_id)
    if not first or not crm.ready():
        return
    people = await _people({hold.user_id, payer_id})
    stage = STAGES.get(why.stage or hold.stage, why.stage or hold.stage)
    head_line = ("ℹ️ <b>Tekshiruv to'xtadi — o'quvchi yuborgan narsa o'qilmadi</b>" if why.user
                 else "🚨 <b>Tekshiruv to'xtadi</b>")
    lines = [f"{head_line} · {KINDS.get(hold.kind, hold.kind)}", f"👤 {_who(hold.user_id, people)}",
             f"🧭 Bosqich: {stage}", f"❗ Sabab: <b>{_esc(why.title)}</b>"]
    if why.detail:
        lines.append(f"<code>{_esc(why.detail[:240])}</code>")
    if why.where:
        lines.append(f"📍 Kod: <code>{_esc(why.where)}</code>")
    if why.hint:
        lines.append(f"💡 {_esc(why.hint)}")
    lines.append(_coins_line(coins, payer_id, hold.user_id, people))
    usd_line = await _usd_line(usd)
    if usd_line:
        lines.append(usd_line)
    lines.append(f"⏱ {_duration(time.monotonic() - hold.started)} · 🕒 {crm.local_now().strftime('%d.%m %H:%M')}")
    lines.append(f'🔗 <a href="{PANEL_URL}/checks/stopped">Admin panel → To\'xtagan tekshiruvlar</a>')
    await crm.post(topic, "\n".join(lines))


def report(hold, why: Reason | None, **kw) -> None:
    """end()'s report (ended()), in the background: the student's own card never waits for the admins' one (a
    slow database, the day's first so'm rate from the Central Bank)."""
    crm.spawn(ended(hold, why, **kw))


async def ended(hold, why: Reason | None, *, state: str | None, coins: int | None = None,
                payer_id: int | None = None, error: BaseException | None = None) -> None:
    """end() of a check (bot/utils/check_guard.py). `state`: what end() found its hold to be - "settled" (its
    `coins` given back to `payer_id` just now), "charged" (the report was saved and paid), anything else (closed
    by an earlier end(), or unknown). `why`: explain()'s reason, None for a stop without an exception. One card
    at most per check. Never raises."""
    try:
        if hold.reported:
            return
        if state == "settled":
            if why is not None:
                hold.reported = True
                await failed(hold, why, coins, payer_id, error)
            return  # without an exception: a normal stop (no speech heard, ...), no incident
        if state != "charged":
            return
        if why is not None:  # saved and paid, then a step after it failed
            hold.notes.append({"code": why.code, "text": f"Hisobot saqlangandan keyin: {why.title}",
                               "detail": why.detail})
            crm.claim_error(error)
        elif hold.card_done:
            return  # its own card in the module's topic said it all (card_lines)
        notes = await _notes(hold)
        if not notes:
            return
        hold.reported = True
        people = await _people({hold.user_id})
        lines = [f"⚠️ <b>Tekshiruv muammo bilan tugadi</b> · {KINDS.get(hold.kind, hold.kind)}",
                 f"👤 {_who(hold.user_id, people)}", f"🧭 Bosqich: {STAGES.get(hold.stage, hold.stage)}"]
        lines += [f"• {_esc(n['text'])}" + (f" <i>({_esc(n['detail'])})</i>" if n["detail"] else "") for n in notes]
        lines.append("🧾 Hisobot saqlangan, coin yechilgan")
        lines.append(f'🔗 <a href="{PANEL_URL}/checks">Admin panel → Tekshiruvlar</a>')
        await crm.post(_topic(hold.module), "\n".join(lines))
    except Exception:  # noqa: BLE001
        logger.warning("Check %s: its incident was not told", getattr(hold, "id", "?")[:8], exc_info=True)


async def interrupted(rows: list[tuple]) -> None:
    """recover(): the checks a stopped bot left running - (CheckHold row, coins given back, "try again"
    offered) - in one card. Never raises."""
    if not rows:
        return
    try:
        people = await _people({row.user_id for row, _, _ in rows} | {row.payer_id for row, _, _ in rows})
        lines = [f"♻️ <b>Bot qayta ishga tushdi:</b> {len(rows)} ta tekshiruv to'xtab qolgan edi",
                 f"❗ Sabab: <b>{_esc(STAGES['restart'])}</b> — deploy yoki restart paytida baholanayotgan "
                 "(yoki navbatda) edi", "Coinlar qaytarildi, o'quvchilar kartasida xabar va «qayta urinish» bor:"]
        for row, coins, retry in rows[:25]:
            paid = f" · {coins} coin qaytdi" if coins else " · bepul"
            if coins and row.payer_id and row.payer_id != row.user_id:
                paid += f" (homiyga: {_name(row.payer_id, people)})"
            lines.append(f"• {KINDS.get(row.kind, row.kind)} · {_who(row.user_id, people)}{paid}"
                         + ("" if retry else " · <i>javobi saqlanmagan — qayta yuborishi kerak</i>"))
        if len(rows) > 25:
            lines.append(f"• … yana {len(rows) - 25} ta")
        lines.append(f'🔗 <a href="{PANEL_URL}/checks/stopped">Admin panel → To\'xtagan tekshiruvlar</a>')
        await crm.post("errors", "\n".join(lines))
        why = Reason("restart", "Bot qayta ishga tushdi (deploy yoki restart)", stage="restart")
        for row, _, _ in rows:
            await _burst(why, row.kind, row.user_id)
    except Exception:  # noqa: BLE001
        logger.warning("Interrupted checks not told", exc_info=True)


async def expired(row, coins: int) -> None:
    """sweep(): a hold no running check owned any more (its end() failed - the database was away). Never
    raises."""
    try:
        people = await _people({row.user_id, row.payer_id})
        why = Reason("swept", "Tekshiruv tugallanmay qoldi (oxiri bazaga yozilmadi)",
                     "end() ishlamadi — masalan, baza bir muddat javob bermagan", stage="grading")
        first = FOLD.hit(("fail", why.code, "", "errors"), {"title": why.title, "topic": "errors"},
                         {"kind": row.kind, "user_id": row.user_id, "coins": coins or 0}, {"coins": coins or 0})
        await _burst(why, row.kind, row.user_id)
        if not first:
            return
        lines = [f"🚨 <b>Tekshiruv to'xtadi</b> · {KINDS.get(row.kind, row.kind)}", f"👤 {_who(row.user_id, people)}",
                 f"❗ Sabab: <b>{_esc(why.title)}</b>", f"<i>{_esc(why.detail)}; sweep uni yopdi.</i>",
                 _coins_line(coins, row.payer_id, row.user_id, people),
                 f'🔗 <a href="{PANEL_URL}/checks/stopped">Admin panel → To\'xtagan tekshiruvlar</a>']
        await crm.post("errors", "\n".join(lines))
    except Exception:  # noqa: BLE001
        logger.warning("Expired check not told", exc_info=True)


async def _burst(why: Reason, kind: str, user_id: int) -> None:
    """BURST_AT checks stopped (not the student's fault) within BURST_WINDOW: one alert in ⚠️ Xatolar and to
    the admins directly, with the reasons - at most once in BURST_EVERY (across restarts: Redis)."""
    try:
        r = crm.redis()
        now = time.time()
        member = json.dumps({"code": why.code, "title": why.title, "kind": kind, "user": user_id,
                             "id": uuid.uuid4().hex[:8]}, ensure_ascii=False)
        async with r.pipeline(transaction=True) as pipe:
            pipe.zadd(BURST_KEY, {member: now})
            pipe.zremrangebyscore(BURST_KEY, "-inf", now - BURST_WINDOW)
            pipe.zrange(BURST_KEY, 0, -1)
            pipe.expire(BURST_KEY, BURST_WINDOW * 2)
            _, _, members, _ = await pipe.execute()
        if len(members) < BURST_AT:
            return
        if not await r.set(BURST_TOLD_KEY, "1", nx=True, ex=BURST_EVERY):
            return
        events = [json.loads(m) for m in members]
        reasons = Counter(e["title"] for e in events)
        kinds = Counter(KINDS.get(e["kind"], e["kind"]) for e in events)
        students = len({e["user"] for e in events})
        lines = [f"🚨 <b>{len(events)} ta tekshiruv oxirgi {BURST_WINDOW // 60} daqiqada to'xtadi</b> "
                 f"({students} o'quvchi) — tizimda muammo bo'lishi mumkin.", "Sabablar:"]
        lines += [f"• {_esc(title)} — {n} ta" for title, n in reasons.most_common()]
        lines.append("Turi: " + ", ".join(f"{k} {n}" for k, n in kinds.most_common()))
        lines.append("Coinlar qaytarildi. Har birining kartasi shu bo'limda; keyingi ogohlantirish 30 daqiqadan "
                     "keyin.")
        lines.append(f'🔗 <a href="{PANEL_URL}/checks/stopped">Admin panel → To\'xtagan tekshiruvlar</a>')
        text = "\n".join(lines)
        await crm.post("errors", text)
        crm.tell_admins("check_burst", "⚠️ Bandzilla: " + text, every=BURST_EVERY)
    except Exception:  # noqa: BLE001
        logger.warning("Check burst not counted", exc_info=True)
