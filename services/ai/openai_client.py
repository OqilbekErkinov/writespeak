"""Single shared AsyncOpenAI client instance used by every AI service module,
and `parse_flex` for the grading calls.

Flex processing (settings.openai_service_tier = "flex", since 2026-10-16) runs
a call at half the token price on the same model; in return OpenAI may queue
it, or turn it down when busy (429 "resource unavailable"). `parse_flex`
then sends the same call on the default tier, so a check never fails or
waits forever because of Flex - it just costs the normal price that time.
"""
import logging
import time

import openai
from openai import AsyncOpenAI

from bot.config import settings

logger = logging.getLogger(__name__)

async def _watch_credit(response) -> None:
    """Every OpenAI answer passes here (an httpx response hook). Its x-ratelimit-* headers tell the check gate
    how much of the account's limit is left this minute (bot/utils/capacity.py, 2026-11-15). A 429
    "insufficient_quota" means the credit is gone - checks pause and the admins' group hears it at once
    (bot/utils/balance.py, 2026-11-14); a 429 "rate limit exceeded" makes the gate let fewer checks run."""
    from bot.utils.capacity import governor, request_model  # it imports this module's callers' helpers

    governor.observe(request_model(response.request), response.headers)
    if response.status_code != 429:
        return
    try:
        await response.aread()
        body = response.text
    except Exception:  # noqa: BLE001 - the SDK reports the 429 itself
        return
    if "insufficient_quota" in body or "credit_balance" in body:
        from bot.utils import balance

        await balance.out_of_credit()
    elif "rate_limit_exceeded" in body:
        governor.rate_limited()


client = AsyncOpenAI(api_key=settings.openai_api_key,
                     # every call's own limit (the SDK's default was 600 s, three tries: a hung call held a
                     # check for half an hour); the grading calls have the check's deadline over them too
                     timeout=settings.openai_default_timeout,
                     http_client=openai.DefaultAsyncHttpxClient(event_hooks={"response": [_watch_credit]}))

# Short gpt-5-mini calls (Speaking model answers, filler words; on Flex since
# 2026-10-17) normally take seconds, so they fall back to the default tier
# much sooner than the long gpt-5 calls.
SHORT_FLEX_TIMEOUT = 60.0


def is_image_error(exc: BaseException) -> bool:
    """OpenAI turned a picture down (a 400 such as invalid_image_format or
    image_parse_error): the sender can fix it by sending the picture again,
    unlike a failed model call - and retrying the same bytes never helps."""
    if not isinstance(exc, openai.BadRequestError):
        return False
    return "image" in f"{getattr(exc, 'code', None) or ''} {exc}".lower()


async def parse_flex(flex_timeout: float | None = None, **kwargs):
    """client.beta.chat.completions.parse(**kwargs) on the Flex tier when
    settings.openai_service_tier is "flex", on the default tier otherwise or
    when Flex is unavailable or slower than `flex_timeout` seconds
    (settings.openai_flex_timeout by default)."""
    if settings.openai_service_tier == "flex" and time.monotonic() >= _flex_off_until:
        timeout = flex_timeout or settings.openai_flex_timeout
        flex_kwargs = dict(kwargs)
        flex_kwargs["extra_body"] = {**(kwargs.get("extra_body") or {}), "service_tier": "flex"}
        try:
            return await client.with_options(timeout=timeout, max_retries=0).beta.chat.completions.parse(**flex_kwargs)
        except openai.RateLimitError as e:
            if getattr(e, "code", None) in ("insufficient_quota", "credit_balance_exhausted"):
                raise  # out of credit - the default tier would fail the same way
            logger.warning("Flex unavailable (%s) - using the default tier", getattr(e, "code", None) or "429")
        except openai.APITimeoutError:
            logger.warning("Flex took over %ss - using the default tier", timeout)
            _flex_slow()
        except (openai.APIConnectionError, openai.InternalServerError) as e:
            # a dropped connection or OpenAI's 5xx on Flex (2026-11-15: these failed the whole check - Flex
            # runs with no retries of its own); the default tier, with its retries, takes the call
            logger.warning("Flex call failed (%s) - using the default tier", type(e).__name__)
    return await client.beta.chat.completions.parse(**kwargs)


# Flex keeps timing out (OpenAI's queue is long): skip it for a while instead of making every call of every
# check wait `flex_timeout` first - FLEX_SLOW_COUNT timeouts within FLEX_SLOW_WINDOW s switch it off for
# FLEX_PAUSE s (the default tier's price meanwhile).
FLEX_SLOW_COUNT, FLEX_SLOW_WINDOW, FLEX_PAUSE = 3, 300.0, 600.0
_flex_timeouts: list[float] = []
_flex_off_until = 0.0


def _flex_slow() -> None:
    global _flex_off_until
    now = time.monotonic()
    _flex_timeouts[:] = [t for t in _flex_timeouts if now - t < FLEX_SLOW_WINDOW] + [now]
    if len(_flex_timeouts) >= FLEX_SLOW_COUNT:
        _flex_off_until = now + FLEX_PAUSE
        _flex_timeouts.clear()
        logger.warning("Flex timed out %d times in %ds - the default tier for the next %ds",
                       FLEX_SLOW_COUNT, int(FLEX_SLOW_WINDOW), int(FLEX_PAUSE))
