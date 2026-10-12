"""Token usage and estimated cost of every OpenAI call.

Each AI service reports its response here with `record(...)`, which logs one
line per call. `track(label)` groups the calls of one check (children started
with asyncio.gather share the group) and logs the total when it ends:

    cost writing task2 user=123: $0.0734 in 4 calls (feedback gpt-5 $0.0612, ...)

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
PER_MINUTE_PRICES = {"whisper-1": 0.006}


@dataclass
class Call:
    purpose: str
    model: str
    cost: float


_current: ContextVar[list[Call] | None] = ContextVar("openai_usage_calls", default=None)


def _price(table: dict, model: str):
    matches = [prefix for prefix in table if model.startswith(prefix)]
    return table[max(matches, key=len)] if matches else None


def _detail(details, name: str) -> int:
    return (getattr(details, name, None) or 0) if details is not None else 0


def _add(purpose: str, model: str, cost: float) -> None:
    calls = _current.get()
    if calls is not None:
        calls.append(Call(purpose, model, cost))


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
    cost = 0.0
    if prices is not None:
        p_in, p_cached, p_out = prices
        a_in, a_out = _price(AUDIO_TOKEN_PRICES, model) or (p_in, p_out)
        cost = (
            (prompt - cached - audio_in) * p_in + cached * p_cached + audio_in * a_in
            + (completion - audio_out) * p_out + audio_out * a_out
        ) / 1_000_000
    logger.info(
        "openai %s %s: in=%d (cached %d, audio %d) out=%d (reasoning %d) ~$%.4f%s",
        purpose, model, prompt, cached, audio_in, completion, reasoning, cost,
        "" if prices is not None else " (no price for this model)",
    )
    _add(purpose, model, cost)


def record_minutes(purpose: str, model: str, seconds: float) -> None:
    """For per-minute models (Whisper), whose responses report no tokens."""
    per_minute = _price(PER_MINUTE_PRICES, model) or 0.0
    cost = seconds / 60 * per_minute
    logger.info("openai %s %s: %.1fs audio ~$%.4f", purpose, model, seconds, cost)
    _add(purpose, model, cost)


@contextmanager
def track(label: str):
    """Collects the calls made inside the block and logs their total cost."""
    calls: list[Call] = []
    token = _current.set(calls)
    try:
        yield calls
    finally:
        _current.reset(token)
        if calls:
            total = sum(c.cost for c in calls)
            parts = ", ".join(f"{c.purpose} {c.model} ${c.cost:.4f}" for c in calls)
            logger.info("cost %s: $%.4f in %d calls (%s)", label, total, len(calls), parts)
