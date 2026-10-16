"""Token usage and estimated cost of every OpenAI call.

Each AI service reports its response here with `record(...)`, which logs one
line per call. `track(label)` groups the calls of one check (children started
with asyncio.gather share the group) and logs the total when it ends:

    cost writing task2 user=123: $0.0734 in 4 calls (feedback gpt-5 $0.0612, ...)

Every call is also added to a running total by category (the first word of
its `track` label - writing, speaking, tts, input, practice - or its purpose
when made outside one), which `take_spent()` hands over for the admins'
group's daily totals (bot/utils/crm.py) - including checks that failed
halfway and inputs that never became a check.

Prices are USD per 1M tokens from platform.openai.com/docs/pricing - update
them here if OpenAI changes them. Token counts in the log are exact; dollars
are an estimate from this table.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# model-name prefix -> (input, cached input, output); the longest matching prefix wins.
TOKEN_PRICES = {
    "gpt-5-mini": (0.25, 0.025, 2.00),
    "gpt-5-nano": (0.05, 0.005, 0.40),
    "gpt-5": (1.25, 0.125, 10.00),
    "gpt-audio": (2.50, 2.50, 10.00),
    "text-embedding-3-small": (0.02, 0.02, 0.0),
}
AUDIO_TOKEN_PRICES = {"gpt-audio": (32.00, 64.00)}  # audio input, audio output
# Per minute of audio: Whisper input; text-to-speech output (estimated from
# the word count at 140 words a minute - the speech API reports no usage).
FLEX_DISCOUNT = 0.5  # Flex processing is half the token price (services/ai/openai_client.py)
PER_MINUTE_PRICES = {"whisper-1": 0.006, "gpt-4o-mini-tts": 0.015, "tts-1": 0.012}


@dataclass
class Call:
    purpose: str
    model: str
    cost: float
    flex: bool = False
    tokens_in: int = 0
    cached: int = 0
    tokens_out: int = 0
    reasoning: int = 0
    audio_seconds: float = 0.0


_current: ContextVar[list[Call] | None] = ContextVar("openai_usage_calls", default=None)
_label: ContextVar[str | None] = ContextVar("openai_usage_label", default=None)
_spent: dict[str, float] = {}  # category -> USD, until take_spent()


def _price(table: dict, model: str):
    matches = [prefix for prefix in table if model.startswith(prefix)]
    return table[max(matches, key=len)] if matches else None


def _detail(details, name: str) -> int:
    return (getattr(details, name, None) or 0) if details is not None else 0


def _add(call: Call) -> None:
    calls = _current.get()
    if calls is not None:
        calls.append(call)
    category = (_label.get() or call.purpose).split()[0]
    _spent[category] = _spent.get(category, 0.0) + call.cost


def take_spent() -> dict[str, float]:
    """The cost recorded since the last call, by category - for the daily
    totals (bot/utils/crm.py); give it back with `give_back` if saving fails."""
    spent = dict(_spent)
    _spent.clear()
    return spent


def give_back(spent: dict[str, float]) -> None:
    for category, cost in spent.items():
        _spent[category] = _spent.get(category, 0.0) + cost


def record(purpose: str, response) -> None:
    """Logs a chat-completion or embedding response's usage and estimated cost."""
    usage = getattr(response, "usage", None)
    model = getattr(response, "model", "") or ""
    if usage is None:
        logger.info("openai %s %s: no usage reported", purpose, model)
        return
    prompt = usage.prompt_tokens or 0
    completion = getattr(usage, "completion_tokens", 0) or 0
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    completion_details = getattr(usage, "completion_tokens_details", None)
    cached = _detail(prompt_details, "cached_tokens")
    audio_in = _detail(prompt_details, "audio_tokens")
    audio_out = _detail(completion_details, "audio_tokens")
    reasoning = _detail(completion_details, "reasoning_tokens")

    prices = _price(TOKEN_PRICES, model)
    flex = getattr(response, "service_tier", None) == "flex"
    cost = 0.0
    if prices is not None:
        p_in, p_cached, p_out = prices
        a_in, a_out = _price(AUDIO_TOKEN_PRICES, model) or (p_in, p_out)
        cost = (
            (prompt - cached - audio_in) * p_in + cached * p_cached + audio_in * a_in
            + (completion - audio_out) * p_out + audio_out * a_out
        ) / 1_000_000
        if flex:
            cost *= FLEX_DISCOUNT
    logger.info(
        "openai %s %s%s: in=%d (cached %d, audio %d) out=%d (reasoning %d) ~$%.4f%s",
        purpose, model, " flex" if flex else "", prompt, cached, audio_in, completion, reasoning, cost,
        "" if prices is not None else " (no price for this model)",
    )
    _add(Call(purpose, model, cost, flex, prompt, cached, completion, reasoning))


def record_minutes(purpose: str, model: str, seconds: float) -> None:
    """For per-minute models (Whisper), whose responses report no tokens."""
    per_minute = _price(PER_MINUTE_PRICES, model) or 0.0
    cost = seconds / 60 * per_minute
    logger.info("openai %s %s: %.1fs audio ~$%.4f", purpose, model, seconds, cost)
    _add(Call(purpose, model, cost, audio_seconds=seconds))


def summary(calls: list[Call]) -> dict:
    """A check's cost for the database (report_data["cost"]): the total, the
    cost per purpose, and how many calls - the log lines are lost whenever
    the bot container is recreated. `details` has each purpose's calls,
    tokens and how many ran on Flex (the admins' group shows them)."""
    by_purpose: dict[str, float] = {}
    details: dict[str, dict] = {}
    for c in calls:
        by_purpose[c.purpose] = by_purpose.get(c.purpose, 0.0) + c.cost
        d = details.setdefault(c.purpose, {
            "usd": 0.0, "calls": 0, "flex": 0, "in": 0, "cached": 0, "out": 0, "reasoning": 0,
            "audio_s": 0.0, "models": [],
        })
        d["usd"] += c.cost
        d["calls"] += 1
        d["flex"] += c.flex
        d["in"] += c.tokens_in
        d["cached"] += c.cached
        d["out"] += c.tokens_out
        d["reasoning"] += c.reasoning
        d["audio_s"] += c.audio_seconds
        if c.model not in d["models"]:
            d["models"].append(c.model)
    order = sorted(by_purpose, key=lambda k: -by_purpose[k])
    for d in details.values():
        d["usd"] = round(d["usd"], 5)
        d["audio_s"] = round(d["audio_s"], 1)
    return {
        "total_usd": round(sum(c.cost for c in calls), 4),
        "by_purpose": {k: round(by_purpose[k], 4) for k in order},
        "calls": len(calls),
        "models": sorted({c.model for c in calls}),
        "details": {k: details[k] for k in order},
    }


def merge(total: dict | None, extra: dict | None, prefix: str = "") -> dict | None:
    """Two summaries as one; `extra`'s purposes renamed with `prefix` (the
    reading of a prompt photo becomes "input: ocr")."""
    if not extra:
        return total
    out = dict(total or {"total_usd": 0.0, "by_purpose": {}, "calls": 0, "models": [], "details": {}})
    by_purpose = dict(out.get("by_purpose") or {})
    details = {k: dict(v) for k, v in (out.get("details") or {}).items()}
    for purpose, value in (extra.get("by_purpose") or {}).items():
        by_purpose[prefix + purpose] = round(by_purpose.get(prefix + purpose, 0.0) + value, 4)
    for purpose, d in (extra.get("details") or {}).items():
        mine = details.get(prefix + purpose)
        if mine is None:
            details[prefix + purpose] = dict(d)
            continue
        for key in ("usd", "calls", "flex", "in", "cached", "out", "reasoning", "audio_s"):
            mine[key] = round(mine.get(key, 0) + d.get(key, 0), 5)
        mine["models"] = list(dict.fromkeys([*mine.get("models", []), *d.get("models", [])]))
    out.update(
        total_usd=round(out.get("total_usd", 0.0) + extra.get("total_usd", 0.0), 4),
        by_purpose=dict(sorted(by_purpose.items(), key=lambda kv: -kv[1])),
        calls=out.get("calls", 0) + extra.get("calls", 0),
        models=sorted({*out.get("models", []), *extra.get("models", [])}),
        details=details,
    )
    return out


@contextmanager
def track(label: str):
    """Collects the calls made inside the block and logs their total cost."""
    calls: list[Call] = []
    token = _current.set(calls)
    label_token = _label.set(label)
    try:
        yield calls
    finally:
        _current.reset(token)
        _label.reset(label_token)
        if calls:
            total = sum(c.cost for c in calls)
            parts = ", ".join(f"{c.purpose} {c.model} ${c.cost:.4f}" for c in calls)
            logger.info("cost %s: $%.4f in %d calls (%s)", label, total, len(calls), parts)
