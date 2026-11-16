"""Where a user came from (the owner's request before opening to everyone, 2026-11-16): users.source, written
once - with the user row, at the first /start (bot/handlers/start.py) - from that /start's deep-link payload,
and never changed afterwards, so a later link opened by the same person does not steal the credit.

- `src_<name>`: a link the admins made on the panel's 📣 Manbalar page (webapp/analytics.py) for one channel -
  an Instagram bio, a Telegram channel's post, a center's poster - name [a-z0-9_]{1,40}, lower-cased;
- the bot's own links: `ref_<id>` "referral", `gift_<code>` "gift", a group invite "group", a center invite
  "center" (bot/utils/group_invite.py) - by the payload's shape, so an expired invite or a gift already taken
  still says where the person came from;
- no payload: "direct"; any other payload (a mistyped link, a src_ name that breaks the rule): "other".

NULL: the users who joined before 2026-11-16, when nothing was written (the panel shows "noma'lum").
"""
from __future__ import annotations

import re

from bot.config import settings
from bot.utils.group_invite import is_center_payload, is_invite_payload

PREFIX = "src_"
REF_PREFIX = "ref_"  # bot/handlers/account.py's referral link
# bot/utils/gifts.py's PAYLOAD_PREFIX - not imported: that module pulls in the database layer, and this one is
# read by the panel's templates helpers too (tests/sources_funnel_test.py checks the two stay the same)
GIFT_PREFIX = "gift_"
NAME_RE = re.compile(r"^[a-z0-9_]{1,40}$")
MAX_NAME = 40

# The values the bot writes by itself - never a src_ name, or a channel called "referral" would be counted with
# the real referrals. "unknown" is the panel's key for NULL (the users from before 2026-11-16).
BUILT_IN = ("direct", "referral", "gift", "group", "center", "other")
UNKNOWN = "unknown"
RESERVED = frozenset(BUILT_IN) | {UNKNOWN}

# The panel's names for them (admin texts are Uzbek only)
LABELS = {
    "direct": "To'g'ridan-to'g'ri (havolasiz)",
    "referral": "Do'st taklifi (referal)",
    "gift": "Sovg'a havolasi",
    "group": "Guruh taklifi",
    "center": "Markaz taklifi",
    "other": "Boshqa havola",
    UNKNOWN: "Noma'lum (2026-11-16 gacha)",
}
SHORT_LABELS = {  # the users list's narrow column
    "direct": "to'g'ridan", "referral": "referal", "gift": "sovg'a", "group": "guruh", "center": "markaz",
    "other": "boshqa havola", UNKNOWN: "noma'lum",
}


def from_payload(args: str | None) -> str:
    """The source of a first /start with this payload (CommandObject.args)."""
    payload = (args or "").strip()
    if not payload:
        return "direct"
    if payload.startswith(PREFIX):
        name = payload[len(PREFIX):].lower()
        return name if NAME_RE.match(name) and name not in RESERVED else "other"
    if payload.startswith(REF_PREFIX):
        return "referral"
    if payload.startswith(GIFT_PREFIX) and payload[len(GIFT_PREFIX):].strip():
        return "gift"
    if is_invite_payload(payload):
        return "group"
    if is_center_payload(payload):
        return "center"
    return "other"


def slug(name: str | None) -> str:
    """What an admin typed in the panel's link builder, made a src_ name: lower-cased, every run of other signs
    (spaces, "-", "'", Cyrillic...) one "_", at most MAX_NAME long ("Instagram Bio" -> "instagram_bio"). May be
    empty, or one of the RESERVED names - normalize() says whether it can be used.
    webapp/templates/sources.html does the same in the browser."""
    text = re.sub(r"[^a-z0-9_]+", "_", (name or "").strip().lower()).strip("_")
    return text[:MAX_NAME].strip("_")


def normalize(name: str | None) -> str | None:
    """slug(name) when it makes a usable src_ name, else None (nothing left, or one of the bot's own names)."""
    text = slug(name)
    return text if NAME_RE.match(text) and text not in RESERVED else None


def link(name: str) -> str:
    return f"https://t.me/{settings.telegram_bot_username}?start={PREFIX}{name}"


def key(source: str | None) -> str:
    """The panel's key of a stored source: NULL is "unknown"."""
    return source or UNKNOWN


def label(source: str | None) -> str:
    """How the panel names a source: the bot's own ones in words, a src_ link by its name."""
    k = key(source)
    return LABELS.get(k, k)


def short_label(source: str | None) -> str:
    k = key(source)
    return SHORT_LABELS.get(k, k)


def is_link(source: str | None) -> bool:
    """A src_ link made on the panel (not one of the bot's own sources)."""
    return bool(source) and source not in RESERVED
