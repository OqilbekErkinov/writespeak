"""Signed group invite links: https://t.me/<bot>?start=g<group id>_<signature>.

The signature is an HMAC of the group id keyed with the bot's own secret
token, so an invite can't be guessed from a group number - the old
`group_<id>` links could, and are no longer accepted (see bot/handlers/start.py).
"""
from __future__ import annotations

import hashlib
import hmac
import html
import re

from bot.config import settings
from bot.i18n import t

_PAYLOAD_RE = re.compile(r"^g(\d+)_([0-9a-f]{10})$")
LEGACY_PREFIX = "group_"


def _signature(group_id: int) -> str:
    key = settings.telegram_bot_token.encode()
    return hmac.new(key, f"group-invite:{group_id}".encode(), hashlib.sha256).hexdigest()[:10]


def invite_link(group_id: int) -> str:
    return f"https://t.me/{settings.telegram_bot_username}?start=g{group_id}_{_signature(group_id)}"


def parse_invite(payload: str | None) -> int | None:
    """The group id of a valid invite payload, else None."""
    match = _PAYLOAD_RE.match(payload or "")
    if match and hmac.compare_digest(match.group(2), _signature(int(match.group(1)))):
        return int(match.group(1))
    return None


def is_invite_payload(payload: str | None) -> bool:
    """Looks like a group invite at all (valid, forged or an old group_<id> link)."""
    return bool(payload) and (payload.startswith(LEGACY_PREFIX) or bool(re.match(r"^g\d+_", payload)))


def joined_notice(group_name: str, teacher_name: str, lang: str) -> str:
    """Confirms a join to the student - including that their teacher will see their results."""
    return t(
        "group.joined_notice",
        lang,
        name=html.escape(group_name, quote=False),
        teacher=html.escape(teacher_name or "?", quote=False),
    )
