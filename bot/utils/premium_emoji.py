"""Premium (custom) emoji in the bot's own messages (the owner, 2026-11-22).

Since Bot API 9.4 (2026-02-09) a bot whose owner - the @BotFather account - has Telegram Premium may put custom
emoji in what it sends to private chats and groups, and every reader sees them, Premium or not. Tried on
@bandzilla_bot the same day: kept in a message, on an inline button's icon, and through copy_message.

The admins choose them in the bot (/emoji, bot/handlers/admin_tools.py): a premium emoji takes the place of its
own plain emoji - or of the plain emoji written before it on the same line ("✍️ = <premium>") - wherever the
bot's HTML texts have it, and in front of an inline button's text (as the button's icon). The middleware
(bot/middlewares/premium_emoji.py) dresses every message the bot sends or edits; a send that fails is sent again
plain, and a send Telegram stripped of them (the owner's Premium ended) is told to the admins' group once a day.

The map lives in Redis (KEY: plain emoji, without U+FE0F -> {"id", "alt"}), read at most every CACHE seconds.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter

from bot.utils.crm import redis

logger = logging.getLogger(__name__)

KEY = "runtime:premium_emoji"
STRIPPED_KEY = "premium_emoji:stripped:{day}"  # one warning a day
CACHE = 30  # seconds
MAX_PER_MESSAGE = 40  # premium emoji put into one message at most; the rest stay plain
VS16 = "️"
_SKIP_TAGS = ("code", "pre", "tg-emoji")  # Telegram takes no entity inside code/pre; tg-emoji is one already
_TAG = re.compile(r"(<[^>]*>)")
_TAG_NAME = re.compile(r"</?\s*([a-zA-Z-]+)")
# after a match: no variation selector, joiner, skin tone or keycap - the emoji goes on, it is a longer one
_TAIL = "(?![️‍⃣\U0001F3FB-\U0001F3FF])"
_SEPARATORS = re.compile(r"[\s=:\-–—>→⇒,;]+")
# what counts as an emoji in the bot's texts (the /emoji list of the most used ones)
_EMOJI = re.compile("[←-⇿⌀-⏿①-⓿■-➿⤀-⥿⬀-⯿"
                    "\U0001F000-\U0001FAFF]️?")

_cache: dict = {"at": 0.0, "map": {}}
_patterns: dict[tuple, re.Pattern] = {}


def canon(emoji: str) -> str:
    """The map's key: the emoji without its variation selector ("✍️" and "✍" are one emoji)."""
    return emoji.replace(VS16, "")


def _modifier(c: str) -> bool:
    return c in "⃣" or "\U0001F3FB" <= c <= "\U0001F3FF" or "\U000E0020" <= c <= "\U000E007F"


def single_emoji(text: str) -> bool:
    """One emoji, not two side by side: one base character with its modifiers (skin tone, keycap, tags), a flag,
    or such pieces joined by U+200D."""
    if not is_emoji(text):
        return False
    for part in canon(text).split("‍"):
        bases = [c for c in part if not _modifier(c)]
        flag = len(bases) == 2 and all("\U0001F1E6" <= c <= "\U0001F1FF" for c in bases)
        if not (len(bases) == 1 or flag):
            return False
    return True


def is_emoji(text: str) -> bool:
    """A plain emoji a premium one may take the place of: no letters or digits, short, not plain ASCII."""
    key = canon(text)
    if len(key) == 2 and key[1] == "⃣":  # a keycap: 1️⃣ #️⃣
        return True
    return 0 < len(key) <= 10 and not key.isascii() and not any(c.isalnum() for c in key)


async def entries() -> dict[str, dict]:
    """The saved map: plain emoji (canon) -> {"id": custom emoji id, "alt": the plain emoji as written}."""
    raw = await redis().hgetall(KEY)
    out = {}
    for key, value in raw.items():
        try:
            item = json.loads(value)
            if item.get("id"):
                out[key] = item
        except ValueError:
            continue
    return out


async def mapping() -> dict[str, str]:
    """canon -> custom emoji id, cached; {} when Redis is unreadable (the messages go plain, never fail)."""
    if time.monotonic() - _cache["at"] < CACHE:
        return _cache["map"]
    try:
        value = {k: v["id"] for k, v in (await entries()).items()}
    except Exception as e:  # noqa: BLE001
        logger.warning("Premium emoji map unreadable, plain emoji for now: %r", e)
        value = {}
    _cache.update(at=time.monotonic(), map=value)
    return value


def _forget() -> None:
    _cache.update(at=0.0, map={})


async def save(picked: list[tuple[str, str]]) -> None:
    """`picked`: (plain emoji, custom emoji id) pairs; a plain emoji saved again gets the new premium one."""
    if picked:
        await redis().hset(KEY, mapping={canon(alt): json.dumps({"id": cid, "alt": alt}, ensure_ascii=False)
                                         for alt, cid in picked})
    _forget()


async def remove(emoji: str) -> bool:
    removed = bool(await redis().hdel(KEY, canon(emoji)))
    _forget()
    return removed


async def clear() -> int:
    n = len(await redis().hkeys(KEY))
    await redis().delete(KEY)
    _forget()
    return n


def tag(emoji: str, custom_id: str) -> str:
    return f'<tg-emoji emoji-id="{custom_id}">{emoji}</tg-emoji>'


def _pattern(keys: tuple[str, ...]) -> re.Pattern:
    pat = _patterns.get(keys)
    if pat is None:
        alts = ("".join(re.escape(c) + VS16 + "?" for c in key) for key in keys)
        pat = re.compile("(?<!‍)(?:" + "|".join(alts) + ")" + _TAIL)
        if len(_patterns) > 20:
            _patterns.clear()
        _patterns[keys] = pat
    return pat


def apply(html: str, emoji_map: dict[str, str], limit: int = MAX_PER_MESSAGE) -> tuple[str, int]:
    """`html` (Telegram HTML) with each mapped emoji in its text wrapped as a premium one - not inside a tag,
    <code>, <pre> or an existing <tg-emoji>, never in the middle of a longer emoji (a joined one, a skin tone).
    Returns the text and how many were put in."""
    if not html or not emoji_map:
        return html, 0
    pat = _pattern(tuple(sorted(emoji_map, key=len, reverse=True)))
    count = 0

    def wrap(m: re.Match) -> str:
        nonlocal count
        if count >= limit:
            return m.group(0)
        custom_id = emoji_map.get(canon(m.group(0)))
        if not custom_id:
            return m.group(0)
        count += 1
        return tag(m.group(0), custom_id)

    out, skip = [], 0
    for part in _TAG.split(html):
        if part.startswith("<") and part.endswith(">"):
            name = _TAG_NAME.match(part)
            if name and name.group(1).lower() in _SKIP_TAGS:
                skip = max(0, skip + (-1 if part.startswith("</") else 1))
            out.append(part)
        elif skip or count >= limit:
            out.append(part)
        else:
            out.append(pat.sub(wrap, part))
    return "".join(out), count


def button_icon(text: str, emoji_map: dict[str, str]) -> tuple[str, str] | None:
    """An inline button whose text starts with a mapped emoji: (the text without it, the icon's id)."""
    if not text or not emoji_map:
        return None
    m = _pattern(tuple(sorted(emoji_map, key=len, reverse=True))).match(text)
    if not m:
        return None
    rest = text[m.end():].lstrip()
    custom_id = emoji_map.get(canon(m.group(0)))
    return (rest, custom_id) if rest and custom_id else None


def _utf16_slice(raw: bytes, offset: int, length: int) -> str:
    return raw[offset * 2:(offset + length) * 2].decode("utf-16-le", errors="ignore")


def picked_from(text: str | None, entities) -> list[tuple[str, str]]:
    """The premium emoji in an admin's message, as (plain emoji it takes the place of, custom emoji id). On a line
    with one premium emoji and one plain emoji ("✍️ = <premium>", either order) it takes the plain one's place;
    otherwise each takes its own emoji's place. Telegram's offsets count UTF-16 units."""
    text = text or ""
    raw = text.encode("utf-16-le")
    lines: dict[int, list[tuple[int, int, str, str]]] = {}
    for e in entities or []:
        if e.type != "custom_emoji" or not e.custom_emoji_id:
            continue
        before = _utf16_slice(raw, 0, e.offset)
        alt = _utf16_slice(raw, e.offset, e.length)
        start = len(before)
        lines.setdefault(before.count("\n"), []).append((start, start + len(alt), alt, e.custom_emoji_id))
    out: list[tuple[str, str]] = []
    starts, pos = [], 0
    rows = text.split("\n")
    for row in rows:
        starts.append(pos)
        pos += len(row) + 1
    for no in sorted(lines):
        items = sorted(lines[no])
        chars = list(rows[no] if no < len(rows) else "")
        for s, end, _alt, _cid in reversed(items):
            del chars[s - starts[no]:end - starts[no]]
        plain = _SEPARATORS.sub("", "".join(chars))
        if len(items) == 1 and single_emoji(plain):
            out.append((plain, items[0][3]))
        else:
            out.extend((alt, cid) for _s, _e, alt, cid in items if is_emoji(alt))
    return out


def bot_texts() -> list[str]:
    from bot.i18n import _T  # noqa: PLC0415 - the texts are big; only /emoji needs them

    return [text for entry in _T.values() for text in entry.values()]


def usage(emoji: str, texts: list[str] | None = None) -> int:
    """How many of the bot's texts (every language) have `emoji`."""
    key = canon(emoji)
    return sum(1 for text in (texts if texts is not None else bot_texts()) if key in canon(text))


def most_used(n: int = 24, texts: list[str] | None = None) -> list[tuple[str, int]]:
    """The emoji the bot's Uzbek texts use most (as written, with the variation selector when they have it)."""
    from bot.i18n import _T  # noqa: PLC0415

    uz = texts if texts is not None else [entry.get("uz", "") for entry in _T.values()]
    counts: Counter = Counter()
    shown: dict[str, Counter] = {}
    for text in uz:
        for m in _EMOJI.finditer(text):
            key = canon(m.group(0))
            counts[key] += 1
            shown.setdefault(key, Counter())[m.group(0)] += 1
    return [(shown[key].most_common(1)[0][0], c) for key, c in counts.most_common(n)]


async def stripped_today(day: str) -> bool:
    """True the first time today: Telegram sent our premium emoji as plain ones."""
    return bool(await redis().set(STRIPPED_KEY.format(day=day), "1", nx=True, ex=2 * 86400))
