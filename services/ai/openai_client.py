"""Single shared AsyncOpenAI client instance used by every AI service module,
and `parse_flex` for the grading calls.

Flex processing (settings.openai_service_tier = "flex", since 2026-10-16) runs
a call at half the token price on the same model; in return OpenAI may queue
it, or turn it down when busy (429 "resource unavailable"). `parse_flex`
then sends the same call on the default tier, so a check never fails or
waits forever because of Flex - it just costs the normal price that time.
"""
import logging

import openai
from openai import AsyncOpenAI

from bot.config import settings

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=settings.openai_api_key)

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
    if settings.openai_service_tier == "flex":
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
    return await client.beta.chat.completions.parse(**kwargs)
