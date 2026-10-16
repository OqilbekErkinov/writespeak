"""The admins' group (bot/utils/crm.py), in the group itself:

- /crm_setup (bot admins): inside a topic the admins made, takes it over for
  what its name says (or `/crm_setup writing`); in the General topic, makes
  the topics still missing, and the first time sends everything from before;
- /crm_agents (bot admins): who else may answer students - `/crm_agents
  @username 123456`, `/crm_agents -` for nobody, bare to see the list;
- /crm_status, /crm_today: the queue, today's report so far;
- a reply, in 💬 Murojaatlar, to a student's message goes to that student
  (with a button to write back) - from the bot's admins and the agents only.

Only supergroup messages reach this router; every other router only gets
private chats (bot/main.py), so nothing in the group is ever taken for a
student's answer.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, ReactionTypeEmoji

from bot.config import settings
from bot.i18n import t
from bot.keyboards.support_kb import support_reply_kb
from bot.utils import crm, crm_events
from db import crud
from db.database import get_session

logger = logging.getLogger(__name__)
router = Router(name="crm")
router.message.filter(F.chat.type == ChatType.SUPERGROUP)

_tasks: set[asyncio.Task] = set()


def _is_admin(message: Message) -> bool:
    return message.from_user is not None and message.from_user.id in settings.admin_ids


def _our_group(message: Message) -> bool:
    cfg = crm.config()
    return bool(cfg) and cfg.get("chat_id") == message.chat.id


def _group_config(message: Message) -> dict:
    """This group's config so far - a fresh one if the bot was set up in
    another group before."""
    old = crm.config() or {}
    if old.get("chat_id") == message.chat.id:
        return {**old, "topics": dict(old.get("topics") or {})}
    return {"chat_id": message.chat.id, "title": message.chat.title, "topics": {}}


@router.message(Command("crm_setup"))
async def setup(message: Message, bot: Bot, command: CommandObject) -> None:
    if not _is_admin(message):
        await message.reply(
            "Bu buyruq faqat bot adminlari uchun. Anonim admin bo'lsangiz, guruhda anonimlikni o'chiring."
        )
        return
    if not message.chat.is_forum:
        await message.reply(
            "Bu guruhda «Mavzular» (Topics) yoqilmagan: guruh sozlamalarida Topics ni yoqing va /crm_setup ni "
            "qayta yuboring."
        )
        return
    if message.is_topic_message and message.message_thread_id:
        await _take_over_topic(message, (command.args or "").strip().lower())
        return

    cfg = _group_config(message)
    missing = [key for key in crm.TOPICS if key not in cfg["topics"]]
    if missing:
        me = await bot.get_chat_member(message.chat.id, bot.id)
        if me.status != ChatMemberStatus.ADMINISTRATOR or not getattr(me, "can_manage_topics", False):
            await message.reply(
                "Botni guruhga admin qiling va unga «Mavzularni boshqarish» (Manage topics) huquqini bering, "
                "keyin /crm_setup ni qayta yuboring."
            )
            return
    now = datetime.now(timezone.utc).isoformat()
    first = not cfg.get("backfill_at")
    for key in missing:
        name, colour = crm.TOPICS[key]
        topic = await bot.create_forum_topic(message.chat.id, name, icon_color=colour)
        cfg["topics"][key] = topic.message_thread_id
        crm.save_config(cfg)  # after each one: a failure halfway never makes a topic twice
        await asyncio.sleep(1)
    cfg.update(title=message.chat.title, setup_by=message.from_user.id)
    cfg.setdefault("setup_at", now)
    if first:
        cfg["backfill_at"] = now
    crm.save_config(cfg)

    lines = ["✅ Guruh ulandi." if first else "✅ Guruh tekshirildi."]
    lines.append(
        "Yangi ochilgan bo'limlar: " + ", ".join(crm.TOPICS[k][0] for k in missing) if missing
        else "Barcha bo'limlar joyida."
    )
    if first:
        lines.append(
            "Eski ma'lumotlar (foydalanuvchilar, to'lovlar, tekshiruvlar PDF bilan) bo'limlarga yuborilmoqda - "
            "Telegram cheklovi sabab 10-15 daqiqa davom etadi."
        )
    lines.append("/crm_status - navbat holati, /crm_today - bugungi hisobot, /crm_agents - murojaatlarga kim javob beradi.")
    await message.reply("\n".join(lines))
    await crm_events.post_intros(list(crm.TOPICS) if first else missing)
    if first:
        task = asyncio.create_task(crm_events.backfill(datetime.fromisoformat(now)))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)


async def _take_over_topic(message: Message, arg: str) -> None:
    """/crm_setup inside a topic the admins made: it is used for what its name
    says, or for `arg`."""
    root = message.reply_to_message  # in a topic, a plain message "replies" to the topic's first one
    name = root.forum_topic_created.name if root is not None and root.forum_topic_created else ""
    key = arg if arg in crm.TOPICS else crm.topic_key(name)
    if key is None:
        await message.reply(
            "Bu bo'lim nima uchunligini nomidan topolmadim. Shu bo'limda quyidagilardan birini yuboring:\n"
            + "\n".join(f"/crm_setup {k} - {topic_name}" for k, (topic_name, _) in crm.TOPICS.items())
        )
        return
    cfg = _group_config(message)
    cfg["topics"][key] = message.message_thread_id
    crm.save_config(cfg)
    text = f"✅ Bu bo'lim «{crm.TOPICS[key][0]}» uchun ulandi."
    if cfg.get("backfill_at"):
        await message.reply(text)
        await crm_events.post_intros([key])
        return
    await message.reply(
        f"{text}\nQolgan bo'limlaringizda ham /crm_setup yuboring. Oxirida General bo'limida /crm_setup - "
        "yo'q bo'limlarni bot o'zi ochadi va eski ma'lumotlarni yuboradi."
    )


@router.message(Command("crm_agents"))
async def agents(message: Message, command: CommandObject) -> None:
    """Who besides the bot's admins may answer in 💬 Murojaatlar."""
    if not _our_group(message) or not _is_admin(message):
        return
    cfg = crm.config()
    args = (command.args or "").split()
    if args:
        cfg = {**cfg, "agents": [] if args == ["-"] else [a.lstrip("@").lower() for a in args]}
        crm.save_config(cfg)
    listed = ", ".join(a if a.isdigit() else f"@{a}" for a in cfg.get("agents", [])) or "hech kim"
    await message.reply(
        f"💬 Murojaatlarga javob beradi: bot adminlari + {listed}.\n"
        "O'zgartirish: /crm_agents @username (bir nechta bo'lsa, probel bilan), hammasini olib tashlash: /crm_agents -"
    )


@router.message(Command("crm_status"))
async def status(message: Message) -> None:
    if not _our_group(message):
        return
    cfg = crm.config()
    r = crm.redis()
    queued = await r.llen(crm.QUEUE)
    spend = await r.hgetall(crm.spend_key(crm.local_now().date()))
    total = sum(float(v) for v in spend.values())
    lines = [
        f"📡 Navbatda: {queued} ta xabar" + (f" (~{queued * crm.PAUSE / 60:.0f} daqiqa)" if queued else ""),
        f"💰 Bugungi OpenAI xarajati: ${total:.4f}",
        f"💱 1 $ = {await crm.usd_rate():,.0f} so'm".replace(",", " "),
    ]
    missing = [crm.TOPICS[k][0] for k in crm.TOPICS if k not in cfg["topics"]]
    if missing:
        lines.append("Yo'q bo'limlar: " + ", ".join(missing) + " - General'da /crm_setup qayta ochadi.")
    if not cfg.get("backfill_at"):
        lines.append("Sozlash tugamagan: General bo'limida /crm_setup yuboring.")
    await message.reply("\n".join(lines))


@router.message(Command("crm_today"))
async def today(message: Message) -> None:
    if not _our_group(message):
        return
    await message.reply(await crm_events.daily_report(crm.local_now().date(), partial=True))


@router.message(F.reply_to_message)
async def answer_student(message: Message, bot: Bot) -> None:
    """A reply, in 💬 Murojaatlar, to a student's message the bot posted."""
    cfg = crm.config()
    if not cfg or message.chat.id != cfg.get("chat_id"):
        return
    if message.message_thread_id != (cfg.get("topics") or {}).get("support"):
        return
    replied = message.reply_to_message
    # In a topic, a plain message "replies" to the topic's first message. A bot's
    # message - this one's, or the previous bot's before the move - is a
    # student's if it is in the map of support messages.
    if replied.forum_topic_created is not None or replied.from_user is None or not replied.from_user.is_bot:
        return
    user_id = await crm.support_user(message.chat.id, replied.message_id)
    if user_id is None:
        await message.reply("Bu xabar qaysi o'quvchiniki ekanini topa olmadim - o'quvchining xabariga reply qiling.")
        return
    if not crm.may_answer(message.from_user):
        await message.reply("⛔ Murojaatlarga faqat belgilangan adminlar javob beradi - bu xabar o'quvchiga yuborilmadi.")
        return
    problem = await _deliver(bot, user_id, message)
    if problem:
        await message.reply(problem)
        return
    try:
        await bot.set_message_reaction(message.chat.id, message.message_id, [ReactionTypeEmoji(emoji="👍")])
    except TelegramBadRequest:
        await message.reply("✅ Yetkazildi")


async def _deliver(bot: Bot, user_id: int, message: Message) -> str | None:
    """The admin's answer to the student; what went wrong, if anything."""
    async with get_session() as session:
        user = await crud.get_user(session, user_id)
    lang = user.language if user is not None else "uz"
    header = t("support.answer", lang)
    keyboard = support_reply_kb(lang)
    try:
        if message.text:
            await bot.send_message(user_id, f"{header}\n\n{message.html_text}", reply_markup=keyboard)
        elif message.sticker or message.video_note or message.dice:
            await bot.send_message(user_id, header)
            await bot.copy_message(user_id, message.chat.id, message.message_id, reply_markup=keyboard)
        else:
            caption = header + (f"\n\n{message.html_text}" if message.caption else "")
            await bot.copy_message(user_id, message.chat.id, message.message_id, caption=caption, reply_markup=keyboard)
    except TelegramForbiddenError:
        return "⛔ Yetkazilmadi: o'quvchi botni bloklagan."
    except TelegramBadRequest as e:
        return f"⛔ Yetkazilmadi: {e.message}"
    logger.info("Admins' group: an answer went to user %s", user_id)
    return None
