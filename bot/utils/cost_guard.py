"""What the checks cost, watched (the owner, 2026-11-16, before the launch: every one of these):

1. a check over its kind's limit (settings.cost_limit_*, the owner's own on the panel's settings page - Redis
   runtime:cost_limits) gets a 💸 card in 💰 Xarajatlar instead of its plain cost card: the check, its cost
   against the limit, by purpose and by model - and one in ⚠️ Xatolar too over twice the limit;
2. a check costing over MEDIAN_TIMES the median of the last RECENT_N checks of its kind (MEDIAN_MIN of them at
   least) is flagged the same way, whatever the limit;
3. the Tashkent day's OpenAI spend against a cap (settings.daily_spend_cap_usd, or the owner's): told at 80%
   and at 100%, each once a day, in ⚠️ Xatolar and to the admins directly;
4. more than QUEUE_LONG checks waiting over QUEUE_WAIT seconds for their turn (bot/utils/capacity.py): told
   once an hour, the same way.

And the admins' group's volume (bot/utils/crm.py lanes): while the normal lane has over FOLD_AT messages
waiting - a launch burst, ~2 messages a check at one every 3.1 s - a check's plain cost card is not queued;
its line waits for one "💰 jamlanmasi" card (_digest) instead. Its own check card still says its cost, a 💸
card is never folded, and in normal times every check keeps its own cost card.

watch() is the sender's periodic turn (crm.run_sender); judge()/fold_cost() are crm_events.check_done's.
"""
from __future__ import annotations

import html
import json
import logging
import statistics
import time

from bot.config import settings
from bot.utils import crm
from bot.utils.coins import CHECK_COINS

logger = logging.getLogger(__name__)

LIMITS_KEY = "runtime:cost_limits"  # hash: the owner's values over the settings below
# field -> (the panel's label, its unit, the settings attribute holding the default)
FIELDS = {
    "writing_task1": ("Writing Task 1", "$", "cost_limit_writing_task1"),
    "writing_task2": ("Writing Task 2", "$", "cost_limit_writing_task2"),
    "speaking_part": ("Speaking Part 1, 2 yoki 3", "$", "cost_limit_speaking_part"),
    "speaking_full": ("Speaking Full test", "$", "cost_limit_speaking_full"),
    "daily_cap": ("Kunlik OpenAI xarajati", "$", "daily_spend_cap_usd"),
    "slow_minutes": ("Sekin tekshiruv", "daq", "slow_check_minutes"),
}
BOUNDS = {"daily_cap": (0.0, 10_000.0), "slow_minutes": (0.0, 120.0)}  # 0: off
LIMIT_BOUNDS = (0.01, 100.0)


def _limit_field(kind: str) -> str:
    """The limit a check kind (bot/utils/coins.py CHECK_COINS) is held to."""
    if kind.startswith("writing_"):
        return kind
    return "speaking_full" if kind == "speaking_full" else "speaking_part"


KIND_LIMIT = {kind: _limit_field(kind) for kind in CHECK_COINS}
assert all(field in FIELDS for field in KIND_LIMIT.values())
KINDS = {
    "writing_task1": "Writing Task 1", "writing_task2": "Writing Task 2", "speaking_part1": "Speaking Part 1",
    "speaking_part2": "Speaking Part 2", "speaking_part3": "Speaking Part 3", "speaking_full": "Speaking Full test",
}

REFRESH = 15.0  # seconds the owner's values are cached in each process
RECENT_KEY = "costs:recent:{}"  # + kind: the last RECENT_N checks' costs, newest first
RECENT_N = 50
MEDIAN_MIN = 10
MEDIAN_TIMES = 2.5
SEVERE_TIMES = 2.0  # over this many times its limit: ⚠️ Xatolar too
FLAG_KEY = "costs:flagged:{}:{}"  # + w/s, submission id: its 💸 verdict (cost_updated keeps it flagged)
CAP_TOLD_KEY = "costs:cap_told:{}:{}"  # + Tashkent day, 80 / 100
QUEUE_LONG = 20
QUEUE_WAIT = 300
QUEUE_TOLD_KEY = "costs:queue_told"
QUEUE_EVERY = 3600
FOLD_AT = 40  # normal-lane messages waiting: past this a plain cost card is folded into the digest
FOLDED_KEY = "crm:costs:folded"  # JSON lines of the folded cost cards, oldest first
FOLDED_MARK = "crm:cost_folded:{}:{}"  # + w/s, submission id: its cost card was folded (no edit for it later)
DIGEST_AFTER = 900  # seconds: the oldest folded line waits this long at most
DIGEST_LINES = 45  # check lines a digest message holds
PANEL_URL = "https://admin.bandzilla.uz"

_cache: tuple[float, dict] = (-1e9, {})


def defaults() -> dict[str, float]:
    return {name: float(getattr(settings, attr)) for name, (_, _, attr) in FIELDS.items()}


async def overrides() -> dict[str, float]:
    """The owner's own values (the panel), by field."""
    raw = await crm.redis().hgetall(LIMITS_KEY)
    out = {}
    for name, value in (raw or {}).items():
        try:
            if name in FIELDS:
                out[name] = float(value)
        except (TypeError, ValueError):
            continue
    return out


async def limits(fresh: bool = False) -> dict[str, float]:
    """The values in force: the owner's over the settings' defaults - read at most every REFRESH seconds."""
    global _cache
    if not fresh and time.monotonic() - _cache[0] < REFRESH and _cache[1]:
        return _cache[1]
    values = defaults()
    try:
        values.update(await overrides())
    except Exception:  # noqa: BLE001 - the defaults stand
        logger.warning("Cost limits not read - the defaults stand", exc_info=True)
    _cache = (time.monotonic(), values)
    return values


def parse(name: str, raw: str) -> float | None:
    """A value typed on the panel ("0.4", "0,40", "$2") within its bounds, else None."""
    text = (raw or "").strip().replace(",", ".").lstrip("$").strip()
    try:
        value = float(text)
    except ValueError:
        return None
    low, high = BOUNDS.get(name, LIMIT_BOUNDS)
    return value if low <= value <= high else None


async def save(values: dict[str, float]) -> None:
    """The owner's values (the panel's «Xarajat chegaralari»): every process uses them within REFRESH s. A
    value equal to its default is not kept as the owner's (a later .env default then applies to it)."""
    global _cache
    base = defaults()
    keep = {k: repr(float(v)) for k, v in values.items() if k in FIELDS and float(v) != base[k]}
    drop = [k for k, v in values.items() if k in FIELDS and float(v) == base[k]]
    r = crm.redis()
    if keep:
        await r.hset(LIMITS_KEY, mapping=keep)
    if drop:
        await r.hdel(LIMITS_KEY, *drop)
    _cache = (-1e9, {})


async def reset() -> None:
    global _cache
    await crm.redis().delete(LIMITS_KEY)
    _cache = (-1e9, {})


def limit_for(kind: str, values: dict[str, float]) -> float | None:
    field = KIND_LIMIT.get(kind)
    return values.get(field) if field else None


# --- one check ---------------------------------------------------------------------------------------------


async def judge(kind: str, total: float, *, record: bool = True) -> dict:
    """One check's cost against its limit and against the median of its kind's last RECENT_N checks (this one
    not among them; `record`: it is added to them after). {"limit", "over", "severe", "median", "samples",
    "anomaly", "flagged"}."""
    values = await limits()
    limit = limit_for(kind, values)
    r = crm.redis()
    key = RECENT_KEY.format(kind)
    samples = []
    for raw in await r.lrange(key, 0, RECENT_N - 1):
        try:
            samples.append(float(raw))
        except (TypeError, ValueError):
            continue
    median = statistics.median(samples) if len(samples) >= MEDIAN_MIN else None
    over = bool(limit and total > limit)
    anomaly = bool(median and median > 0 and total > MEDIAN_TIMES * median)
    if record:
        async with r.pipeline(transaction=True) as pipe:
            pipe.lpush(key, repr(round(float(total), 5)))
            pipe.ltrim(key, 0, RECENT_N - 1)
            await pipe.execute()
    return {"limit": limit, "over": over, "severe": bool(limit and total > SEVERE_TIMES * limit),
            "median": median, "samples": len(samples), "anomaly": anomaly, "flagged": over or anomaly,
            "total": total}


async def remember_flag(kind: str, submission_id: int, verdict: dict) -> None:
    await crm.redis().set(FLAG_KEY.format(kind, submission_id), json.dumps(verdict), ex=30 * 24 * 3600)


async def flag_of(kind: str, submission_id: int) -> dict | None:
    raw = await crm.redis().get(FLAG_KEY.format(kind, submission_id))
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def _usd(value: float) -> str:
    return f"${value:.4f}" if value < 10 else f"${value:.2f}"


def _models(cost: dict) -> str:
    by_model = cost.get("by_model")
    if not by_model:  # a check saved before 2026-11-16: each purpose's models, its cost given to the first
        by_model = {}
        for d in (cost.get("details") or {}).values():
            models = d.get("models") or ["?"]
            by_model[models[0]] = by_model.get(models[0], 0.0) + d.get("usd", 0.0)
    return " · ".join(f"{html.escape(m)} {_usd(v)}" for m, v in sorted(by_model.items(), key=lambda kv: -kv[1]))


def alert_lines(verdict: dict, info: dict) -> list[str]:
    """The 💸 head over a flagged check's cost card: what it is, how far over."""
    total, limit, median = verdict["total"], verdict["limit"], verdict["median"]
    lines = [f"💸 <b>Qimmat tekshiruv</b> · {info['label']} · #{info['id']}", f"👤 {info['who']}"]
    if verdict["over"]:
        lines.append(f"Narxi <b>{_usd(total)}</b> — chegara {_usd(limit)} (<b>{total / limit:.1f}×</b>)")
    if verdict["anomaly"]:
        lines.append(f"Odatdagidan <b>{total / median:.1f}×</b> qimmat: oxirgi {verdict['samples']} ta "
                     f"{KINDS.get(info['kind'], info['kind'])} medianasi {_usd(median)}")
    models = _models(info["cost"])
    if models:
        lines.append(f"Modellar: {models}")
    lines.append(f'🔗 <a href="{PANEL_URL}/checks">Admin panel → Tekshiruvlar</a> · chegaralar: Sozlamalar')
    return lines


def severe_card(verdict: dict, info: dict) -> str:
    """⚠️ Xatolar: a check over twice its limit, short - the full breakdown is in 💰 Xarajatlar."""
    from bot.utils.crm_events import PURPOSES  # noqa: PLC0415

    total, limit = verdict["total"], verdict["limit"]
    lines = [f"💸 <b>Tekshiruv narxi chegaradan {total / limit:.1f} barobar oshdi</b> · {info['label']} · "
             f"#{info['id']}", f"👤 {info['who']}", f"Narxi <b>{_usd(total)}</b> — chegara {_usd(limit)}"]
    top = sorted((info["cost"].get("by_purpose") or {}).items(), key=lambda kv: -kv[1])[:3]
    if top:
        lines.append("Eng qimmat qadamlar: " + " · ".join(
            f"{html.escape(PURPOSES.get(p.removeprefix('input: '), p))} {_usd(v)}" for p, v in top))
    models = _models(info["cost"])
    if models:
        lines.append(f"Modellar: {models}")
    lines.append("<i>To'liq tafsilot «💰 Xarajatlar» bo'limida. Tez-tez takrorlansa — model narxi yoki "
                 "promptlar o'zgargan bo'lishi mumkin.</i>")
    return "\n".join(lines)


# --- folded cost cards (a long queue) ------------------------------------------------------------------------


async def fold_cost(kind: str, submission_id: int, info: dict) -> bool:
    """True: the normal lane is long (over FOLD_AT waiting), so this routine cost card is not queued - its line
    waits for the next digest. False: post it as usual."""
    r = crm.redis()
    if int(await r.llen(crm.QUEUE)) <= FOLD_AT:
        return False
    line = {"kind": info["kind"], "id": info["id"], "name": info["name"], "usd": info["cost"].get("total_usd", 0.0),
            "revenue": info.get("revenue"), "at": time.time()}
    async with r.pipeline(transaction=True) as pipe:
        pipe.rpush(FOLDED_KEY, json.dumps(line, ensure_ascii=False))
        pipe.set(FOLDED_MARK.format(kind, submission_id), "1", ex=30 * 24 * 3600)
        await pipe.execute()
    return True


async def was_folded(kind: str, submission_id: int) -> bool:
    return bool(await crm.redis().exists(FOLDED_MARK.format(kind, submission_id)))


async def _digest(force: bool = False) -> int:
    """The folded cost lines as one "💰 jamlanmasi" card (a few when many): when the oldest has waited
    DIGEST_AFTER seconds, or the normal lane is short again. How many lines went."""
    r = crm.redis()
    first = await r.lindex(FOLDED_KEY, 0)
    if not first:
        return 0
    try:
        oldest = float(json.loads(first).get("at") or 0)
    except (ValueError, AttributeError):
        oldest = 0.0
    if not force and time.time() - oldest < DIGEST_AFTER and int(await r.llen(crm.QUEUE)) > FOLD_AT // 2:
        return 0
    async with r.pipeline(transaction=True) as pipe:
        pipe.lrange(FOLDED_KEY, 0, -1)
        pipe.delete(FOLDED_KEY)
        raw, _ = await pipe.execute()
    lines = []
    for item in raw:
        try:
            lines.append(json.loads(item))
        except ValueError:
            continue
    if not lines:
        return 0
    rate = await crm.usd_rate()
    total = sum(x.get("usd") or 0.0 for x in lines)
    revenue = sum(x.get("revenue") or 0 for x in lines)
    span = "–".join(crm.local(_utc(t)).strftime("%H:%M") for t in (lines[0]["at"], lines[-1]["at"]))
    head = [f"💰 <b>Xarajatlar jamlanmasi</b> · {len(lines)} ta tekshiruv · {span}",
            "<i>Guruh navbati uzun bo'lgani uchun bu tekshiruvlarning narx kartasi alohida yuborilmadi (narxi o'z "
            "kartasida bor). Chegaradan oshganlari 💸 bilan alohida chiqadi.</i>",
            f"Jami: <b>{_usd(total)}</b> ≈ <b>{_som(total * rate)} so'm</b> · tushum ≈ {_som(revenue)} so'm → "
            f"foyda ≈ <b>{_som(revenue - total * rate)} so'm</b>"]
    by_kind: dict[str, list] = {}
    for x in lines:
        by_kind.setdefault(x["kind"], []).append(x)
    for kind, rows in sorted(by_kind.items()):
        dear = max(rows, key=lambda x: x.get("usd") or 0.0)
        head.append(f"• {KINDS.get(kind, kind)}: {len(rows)} ta · o'rtacha "
                    f"{_usd(sum(x.get('usd') or 0.0 for x in rows) / len(rows))} · eng qimmati #{dear['id']} "
                    f"{_usd(dear.get('usd') or 0.0)}")
    body = [f"   {KINDS.get(x['kind'], x['kind'])} #{x['id']} · {html.escape(str(x.get('name') or '?'))} · "
            f"{_usd(x.get('usd') or 0.0)}" for x in lines]
    chunks = [body[i:i + DIGEST_LINES] for i in range(0, len(body), DIGEST_LINES)] or [[]]
    for n, chunk in enumerate(chunks):
        text = "\n".join((head + [""] if n == 0 else [f"💰 <b>Xarajatlar jamlanmasi</b> (davomi {n + 1})"]) + chunk)
        await crm.post("costs", text)
    return len(lines)


def _utc(epoch: float):
    from datetime import datetime, timezone  # noqa: PLC0415

    return datetime.fromtimestamp(float(epoch), timezone.utc)


def _som(amount: float) -> str:
    return f"{round(amount):,}".replace(",", " ")


# --- the sender's watch ----------------------------------------------------------------------------------------


async def watch() -> None:
    """crm.run_sender's periodic turn: the day's spend cap, a long queue, the folded cost lines."""
    await _daily_cap()
    await _long_queue()
    await _digest()


async def _daily_cap() -> None:
    values = await limits()
    cap = values.get("daily_cap") or 0.0
    if cap <= 0:
        return
    r = crm.redis()
    day = crm.local_now().date()
    spend = {k: float(v) for k, v in (await r.hgetall(crm.spend_key(day))).items()}
    total = sum(spend.values())
    for pct in (100, 80):
        if total < cap * pct / 100:
            continue
        if not await r.set(CAP_TOLD_KEY.format(day.isoformat(), pct), "1", nx=True, ex=2 * 24 * 3600):
            return  # this step told today already (a jump past both tells only the higher)
        if pct == 100:
            await r.set(CAP_TOLD_KEY.format(day.isoformat(), 80), "1", nx=True, ex=2 * 24 * 3600)
        from bot.utils.crm_events import CATEGORIES  # noqa: PLC0415

        rate = await crm.usd_rate()
        icon = "🚨" if pct == 100 else "⚠️"
        lines = [f"{icon} <b>Bugungi OpenAI xarajati {_usd(total)} — kunlik chegaraning {total / cap * 100:.0f}%i</b> "
                 f"(chegara {_usd(cap)} ≈ {_som(cap * rate)} so'm)"]
        lines += [f"• {html.escape(CATEGORIES.get(k, k))}: {_usd(v)}" for k, v in
                  sorted(spend.items(), key=lambda kv: -kv[1])[:5] if v > 0]
        lines.append("Tekshiruvlar to'xtatilmaydi - bu faqat ogohlantirish. Odatdagidan ko'p bo'lsa, «💸» kartalarni "
                     "va «⚠️ Xatolar»ni ko'ring; chegara: admin panel → Sozlamalar → Xarajat chegaralari.")
        text = "\n".join(lines)
        await crm.post("errors", text)
        crm.tell_admins(f"daily_cap_{pct}", "⚠️ Bandzilla: " + text, every=60)
        logger.warning("The day's OpenAI spend $%.2f passed %d%% of its cap $%.2f", total, pct, cap)
        return


async def _long_queue() -> None:
    from bot.utils.capacity import governor  # noqa: PLC0415

    waiting = governor.waiting_longer(QUEUE_WAIT)
    if waiting <= QUEUE_LONG:
        return
    if not await crm.redis().set(QUEUE_TOLD_KEY, "1", nx=True, ex=QUEUE_EVERY):
        return
    state = governor.state()
    why = []
    if state.get("waiting_for"):
        why.append(f"OpenAI'da «{html.escape(str(state['waiting_for']))}» modelining daqiqalik limiti tugagan")
    if state.get("slowed"):
        why.append("OpenAI 429 (rate limit) qaytargani uchun bot bir vaqtdagi tekshiruvlarni kamaytirgan")
    if not why and state.get("running", 0) >= state.get("cap", 0) > 0:
        why.append(f"panel chegarasi to'lgan (bir vaqtda {state.get('cap')} ta)")
    text = "\n".join([
        f"⏳ <b>Navbat uzun:</b> {waiting} ta tekshiruv {QUEUE_WAIT // 60} daqiqadan ortiq kutyapti "
        f"(eng uzoq: {round(governor.longest_wait() / 60)} daq)",
        f"Navbatda {state.get('queued', 0)} · baholanmoqda {state.get('running', 0)} · hozirgi chegara "
        f"{state.get('limit', 0)} (panel: {state.get('cap', 0)})",
        ("Sabab: " + "; ".join(why)) if why else "Sabab: tekshiruvlar ko'p kelyapti.",
        "Hech biri rad etilmaydi — navbat o'zi yuradi. Uzoq davom etsa: platform.openai.com da limit (tier) yoki "
        "admin panel → Sozlamalar → «Tekshiruvlar sig'imi».",
    ])
    await crm.post("errors", text)
    crm.tell_admins("long_queue", "⚠️ Bandzilla: " + text, every=QUEUE_EVERY)
