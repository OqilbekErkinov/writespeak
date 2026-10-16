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


async def parse_flex(**kwargs):
    """client.beta.chat.completions.parse(**kwargs) on the Flex tier when
    settings.openai_service_tier is "flex", on the default tier otherwise or
    when Flex is unavailable or slower than settings.openai_flex_timeout."""
    if settings.openai_service_tier == "flex":
        flex_kwargs = dict(kwargs)
        flex_kwargs["extra_body"] = {**(kwargs.get("extra_body") or {}), "service_tier": "flex"}
        try:
            return await client.with_options(
                timeout=settings.openai_flex_timeout, max_retries=0
            ).beta.chat.completions.parse(**flex_kwargs)
        except openai.RateLimitError as e:
            if getattr(e, "code", None) == "insufficient_quota":
                raise  # out of credit - the default tier would fail the same way
            logger.warning("Flex unavailable (%s) - using the default tier", getattr(e, "code", None) or "429")
        except openai.APITimeoutError:
            logger.warning("Flex took over %ss - using the default tier", settings.openai_flex_timeout)
    return await client.beta.chat.completions.parse(**kwargs)
