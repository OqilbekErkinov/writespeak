"""The broadcast post an admin made in Telegram itself (the owner, 2026-11-22): sent to the bot after /post
(bot/handlers/admin_tools.py) - bold, links, premium emoji, a picture or a video, exactly as written - and sent
by the panel's broadcast (webapp/panel.py) to every student with copy_message, so it arrives just as it looked.

One post at a time, in up to three versions (the owner, the same day: "the Russian and English users get the same
message"): the Uzbek one, required, and a Russian and an English one; a student gets the version of their language,
the Uzbek one when theirs is missing. A new /post starts a new post (the old versions go); `/post ru` and the
buttons under the bot's answer change one version.
"""
from __future__ import annotations

import json
import time

from bot.utils.crm import redis

KEY = "broadcast:tg_post"
LANGS = ("uz", "ru", "en")
FLAGS = {"uz": "🇺🇿", "ru": "🇷🇺", "en": "🇬🇧"}
NAMES = {"uz": "O'zbekcha", "ru": "Ruscha", "en": "Inglizcha"}
KINDS = {"text": "Matn", "photo": "🖼 Rasm", "video": "🎬 Video", "animation": "🎞 GIF", "document": "📎 Fayl"}


def kind_of(message) -> str | None:
    for kind in ("photo", "video", "animation", "document"):
        if getattr(message, kind, None):
            return kind
    return "text" if message.text else None


def _valid(post) -> bool:
    return isinstance(post, dict) and bool(post.get("chat_id")) and bool(post.get("message_id"))


async def load() -> dict[str, dict]:
    """{lang: post} of the versions there are; {} when there is no post. A post kept before the versions (one
    message, 2026-11-22) is the Uzbek one."""
    raw = await redis().get(KEY)
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        return {}
    if _valid(data):
        return {"uz": data}
    if not isinstance(data, dict):
        return {}
    return {lang: data[lang] for lang in LANGS if _valid(data.get(lang))}


async def save(message, admin: str, lang: str = "uz", fresh: bool = False) -> dict:
    """Keeps `message` as the post's `lang` version; `fresh`: a new post, the other versions dropped."""
    entities = (message.entities or []) + (message.caption_entities or [])
    post = {
        "chat_id": message.chat.id, "message_id": message.message_id, "kind": kind_of(message),
        "preview": (message.text or message.caption or "").strip()[:200], "by": admin[:80], "at": int(time.time()),
        "premium": sum(1 for e in entities if e.type == "custom_emoji"),
    }
    posts = {} if fresh else await load()
    posts[lang if lang in LANGS else "uz"] = post
    await redis().set(KEY, json.dumps(posts, ensure_ascii=False))
    return post


def copies(posts: dict[str, dict]) -> dict[str, tuple[int, int]]:
    """{lang: (chat id, message id)} for the broadcast."""
    return {lang: (int(p["chat_id"]), int(p["message_id"])) for lang, p in posts.items()}


def versions_line(posts: dict[str, dict]) -> str:
    """"🇺🇿 ✅ · 🇷🇺 ✅ · 🇬🇧 — o'zbekchasi boradi" """
    return " · ".join(f"{FLAGS[lang]} ✅" if lang in posts else
                      f"{FLAGS[lang]} — o'zbekchasi boradi" for lang in LANGS)
