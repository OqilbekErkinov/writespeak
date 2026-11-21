"""The broadcast post an admin made in Telegram itself (the owner, 2026-11-22): sent to the bot after /post
(bot/handlers/admin_tools.py) - bold, links, premium emoji, a picture or a video, exactly as written - and sent
by the panel's broadcast (webapp/panel.py) to every student with copy_message, so it arrives just as it looked.
One post at a time: a new /post replaces it.
"""
from __future__ import annotations

import json
import time

from bot.utils.crm import redis

KEY = "broadcast:tg_post"
KINDS = {"text": "Matn", "photo": "🖼 Rasm", "video": "🎬 Video", "animation": "🎞 GIF", "document": "📎 Fayl"}


def kind_of(message) -> str | None:
    for kind in ("photo", "video", "animation", "document"):
        if getattr(message, kind, None):
            return kind
    return "text" if message.text else None


async def save(message, admin: str) -> dict:
    entities = (message.entities or []) + (message.caption_entities or [])
    post = {
        "chat_id": message.chat.id, "message_id": message.message_id, "kind": kind_of(message),
        "preview": (message.text or message.caption or "").strip()[:200], "by": admin[:80], "at": int(time.time()),
        "premium": sum(1 for e in entities if e.type == "custom_emoji"),
    }
    await redis().set(KEY, json.dumps(post, ensure_ascii=False))
    return post


async def load() -> dict | None:
    raw = await redis().get(KEY)
    try:
        post = json.loads(raw) if raw else None
    except ValueError:
        return None
    return post if post and post.get("chat_id") and post.get("message_id") else None
