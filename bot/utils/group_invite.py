"""Signed invite links:

- a group's students:   https://t.me/<bot>?start=g<group id>_<signature>
- a center's teachers:  https://t.me/<bot>?start=c<center id>_<signature>

The signature is an HMAC of the id keyed with the bot's own secret token, so an
invite can't be guessed from a number - the old `group_<id>` links could, and
are no longer accepted (see bot/handlers/start.py).
"""
from __future__ import annotations

import hashlib
import hmac
import html
import re

from bot.config import settings
from bot.i18n import t

_GROUP_RE = re.compile(r"^g(\d+)_([0-9a-f]{10})$")
_CENTER_RE = re.compile(r"^c(\d+)_([0-9a-f]{10})$")
LEGACY_PREFIX = "group_"


def _signature(kind: str, object_id: int) -> str:
    key = settings.telegram_bot_token.encode()
    return hmac.new(key, f"{kind}-invite:{object_id}".encode(), hashlib.sha256).hexdigest()[:10]


def _link(payload: str) -> str:
    return f"https://t.me/{settings.telegram_bot_username}?start={payload}"


def invite_link(group_id: int) -> str:
    return _link(f"g{group_id}_{_signature('group', group_id)}")


def center_invite_link(center_id: int) -> str:
    return _link(f"c{center_id}_{_signature('center', center_id)}")


def _parse(pattern: re.Pattern, kind: str, payload: str | None) -> int | None:
    match = pattern.match(payload or "")
    if match and hmac.compare_digest(match.group(2), _signature(kind, int(match.group(1)))):
        return int(match.group(1))
    return None


def parse_invite(payload: str | None) -> int | None:
    """The group id of a valid group invite payload, else None."""
    return _parse(_GROUP_RE, "group", payload)


def parse_center_invite(payload: str | None) -> int | None:
    return _parse(_CENTER_RE, "center", payload)


def is_invite_payload(payload: str | None) -> bool:
    """Looks like a group invite at all (valid, forged or an old group_<id> link)."""
    return bool(payload) and (payload.startswith(LEGACY_PREFIX) or bool(re.match(r"^g\d+_", payload)))


def is_center_payload(payload: str | None) -> bool:
    return bool(payload) and bool(re.match(r"^c\d+_", payload))


def joined_notice(group_name: str, teacher_name: str, lang: str) -> str:
    """Confirms a join to the student - including that their teacher will see their results."""
    return t(
        "group.joined_notice",
        lang,
        name=html.escape(group_name, quote=False),
        teacher=html.escape(teacher_name or "?", quote=False),
    )
