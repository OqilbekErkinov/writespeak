"""Same text = same result (the academic lead, 2026-10-31: one essay got Lexical
7 and Lexical 6 from two students). GPT-5 takes no temperature 0, so a finished
check is kept in Redis by a hash of the cleaned text and the marking version -
the marking code and values - and the next student who sends that text gets
the same marks. Any change to the marking starts afresh. A Redis failure only
means a new check.
"""
from __future__ import annotations

import hashlib
import logging
import pickle
import re
from pathlib import Path

import redis.asyncio as redis

from bot.config import settings
from services.ai.essay_marking import canon

logger = logging.getLogger(__name__)

ENABLED = settings.marking_cache_enabled
TTL_SECONDS = 90 * 24 * 3600
_ROOT = Path(__file__).resolve().parents[2]
_VERSION_FILES = ("services/ai/writing_grader.py", "services/ai/essay_marking.py", "services/ai/mistake_rules.py",
                  "services/ai/schemas.py", "services/ai/scoring.py", "services/ai/visual_check.py",
                  "config/writing_marking.json")
_version: str | None = None
_client: redis.Redis | None = None


def marking_version() -> str:
    global _version
    if _version is None:
        digest = hashlib.sha256()
        for name in _VERSION_FILES:
            path = _ROOT / name
            digest.update(path.read_bytes().replace(b"\r\n", b"\n") if path.exists() else b"")
        _version = digest.hexdigest()[:16]
    return _version


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", canon(text)).strip().lower()


def key(task_type: str, prompt_text: str, answer_text: str, has_visual: bool, source: str) -> str:
    """The question counts by its words only: a photo and a typed copy differ in their punctuation."""
    question = re.sub(r"[^a-z0-9]+", "", _plain(prompt_text))
    scanned = source in ("photo", "pdf")  # a scan's artefacts are ignored, a typed text's are not
    raw = "\n".join((marking_version(), task_type, str(has_visual), str(scanned), question, _plain(answer_text)))
    return "marking:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(settings.redis_url, socket_timeout=3, socket_connect_timeout=3)
    return _client


async def get(cache_key: str):
    if not ENABLED:
        return None
    try:
        raw = await _redis().get(cache_key)
        return pickle.loads(raw) if raw else None
    except Exception as e:
        logger.warning("The marking cache could not be read: %r", e)
        return None


async def put(cache_key: str, value) -> None:
    if not ENABLED:
        return
    try:
        await _redis().set(cache_key, pickle.dumps(value), ex=TTL_SECONDS)
    except Exception as e:
        logger.warning("The marking cache could not be written: %r", e)
