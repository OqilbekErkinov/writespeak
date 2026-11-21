"""The admins' tools in their private chat with the bot (the owner, 2026-11-22):

- /emoji: the premium emoji the bot puts into its messages (bot/utils/premium_emoji.py) - bare to see them and
  send new ones, `/emoji o'chir ✍️` to drop one, `/emoji tozalash` for all;
- /post: the next message is kept as the panel's broadcast post (bot/utils/broadcast_post.py) - written or
  forwarded in Telegram, with its bold, links, premium emoji, picture or video - and shown back as the students
  will get it.

Only the bot's admins reach these handlers; for anyone else /emoji and /post are unknown words, as before.
"""
from __future__ import annotations

import html
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.config import settings
from bot.states.admin_states import AdminStates
from bot.utils import broadcast_post, premium_emoji
from bot.utils.crm import redis

logger = logging.getLogger(__name__)
router = Router(name="admin_tools")
router.message.filter(F.from_user.id.in_(settings.admin_ids))

REMOVE_WORDS = ("o'chir", "ochir", "o‘chir", "o`chir", "del", "-")
CLEAR_WORDS = ("tozalash", "tozala", "clear", "hammasi")


def _not_command(message: Message) -> bool:
    return not (message.text or "").startswith("/")


def _code(emoji: str) -> str:
    """A plain emoji shown as it is: in <code>, where the bot puts no premium one."""
    return f"<code>{html.escape(emoji)}</code>"


async def _saved_lines() -> list[str]:
    texts = premium_emoji.bot_texts()
    lines = []
    for item in (await premium_emoji.entries()).values():
        used = premium_emoji.usage(item["alt"], texts)
        lines.append(f"{premium_emoji.tag(item['alt'], item['id'])} — {_code(item['alt'])} o'rniga · "
                     + (f"bot matnlarida {used} joyda" if used else "bot matnlarida yo'q"))
    return lines


@router.message(Command("emoji"))
async def emoji(message: Message, state: FSMContext, command: CommandObject) -> None:
    words = (command.args or "").strip()
    first = words.split(maxsplit=1)[0].lower() if words else ""
    if first in CLEAR_WORDS:
        n = await premium_emoji.clear()
        await message.answer(f"🧹 Premium emojilar olib tashlandi ({n} ta). Bot endi oddiy emojilar bilan yozadi.")
        return
    if first in REMOVE_WORDS:
        target = words.split(maxsplit=1)[1].strip() if len(words.split(maxsplit=1)) > 1 else ""
        done = bool(target) and await premium_emoji.remove(target)
        await message.answer(f"🗑 {_code(target)} endi oddiy emoji bo'lib chiqadi." if done else
                             "Bunday emoji saqlanmagan. Ro'yxat: /emoji")
        return
    saved = await _saved_lines()
    mapped = set((await premium_emoji.entries()).keys())
    top = " ".join(_code(e) + ("✓" if premium_emoji.canon(e) in mapped else "") for e, _n in premium_emoji.most_used())
    await state.set_state(AdminStates.emoji)
    await message.answer(
        "✨ <b>Premium emoji</b>\n\n"
        + (f"Saqlanganlar ({len(saved)}):\n" + "\n".join(saved) if saved else "Hali birorta ham saqlanmagan.")
        + "\n\n<b>Qo'shish:</b> premium emojilarni hozir shu yerga bitta xabarda yuboring. Har biri bot "
          "matnlaridagi xuddi shu oddiy emoji o'rniga chiqadi — matnlarda va tugmalarda (pastki menyu "
          "tugmalaridan tashqari).\n"
          "Boshqa emoji o'rniga qo'ymoqchi bo'lsangiz, har qatorga avval oddiy emoji, keyin premium emoji "
          "yozing, masalan: <code>✍️ = </code>(premium emoji)\n\n"
          f"<b>Bot eng ko'p ishlatadigan emojilar</b> (✓ — premiumi bor):\n{top}\n\n"
          "Bittasini olib tashlash: <code>/emoji o'chir ✍️</code> · hammasini: <code>/emoji tozalash</code>\n"
          "Bekor qilish: /cancel"
    )


@router.message(StateFilter(AdminStates.emoji), _not_command)
async def emoji_picked(message: Message, state: FSMContext) -> None:
    await state.clear()
    picked = premium_emoji.picked_from(message.text or message.caption, message.entities or message.caption_entities)
    if not picked:
        await message.answer("Bu xabarda premium emoji topilmadi. Telegram'ning emoji panelidan premium (jonli) "
                             "emojilarni tanlab yuboring. Qaytadan: /emoji")
        return
    await premium_emoji.save(picked)
    texts = premium_emoji.bot_texts()
    lines = []
    for alt, cid in picked:
        used = premium_emoji.usage(alt, texts)
        lines.append(f"{premium_emoji.tag(alt, cid)} — {_code(alt)} o'rniga · "
                     + (f"bot matnlarida {used} joyda" if used else "⚠️ bot matnlarida yo'q, hech qayerda chiqmaydi"))
    await message.answer(f"✅ <b>Saqlandi ({len(picked)}):</b>\n" + "\n".join(lines)
                         + "\n\nBir daqiqa ichida bot xabarlarida shu emojilar premium bo'lib chiqadi. "
                           "Yana qo'shish yoki ro'yxat: /emoji")


@router.message(Command("post"))
async def post(message: Message, state: FSMContext) -> None:
    await state.set_state(AdminStates.post)
    await message.answer(
        "📨 <b>Ommaviy xabar uchun post</b>\n\n"
        "Tarqatmoqchi bo'lgan postni shu yerga yuboring: o'zingiz yozing yoki kanaldan forward qiling. Qalin va "
        "qiya harf, havola, premium emoji, bitta rasm yoki video — hammasi o'quvchilarga aynan shunday boradi.\n\n"
        "Bekor qilish: /cancel"
    )


@router.message(StateFilter(AdminStates.post), _not_command)
async def post_received(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.media_group_id:
        # an album comes as several messages: one answer for it, and the next message is still awaited
        if await redis().set(f"broadcast:album:{message.media_group_id}", "1", nx=True, ex=120):
            await message.answer("Bir nechta rasm (albom) bo'lmaydi — bitta rasm yoki video bilan yuboring.")
        return
    if broadcast_post.kind_of(message) is None:
        await message.answer("Bunday xabarni tarqatib bo'lmaydi: matn, rasm, video yoki fayl yuboring.")
        return
    await state.clear()
    saved = await broadcast_post.save(message, message.from_user.full_name or str(message.from_user.id))
    await message.answer("👀 O'quvchilar postni shunday ko'radi:")
    try:
        await bot.copy_message(message.chat.id, message.chat.id, message.message_id)
    except Exception as e:  # noqa: BLE001 - e.g. a forward of a protected channel post
        logger.info("Broadcast post preview failed: %r", e)
        await message.answer("⚠️ Bu postni nusxalab bo'lmadi (masalan, kanal postlarni nusxalashni taqiqlagan). "
                             "Postni o'zingiz yozib yuboring: /post")
        return
    note = f"\nPremium emoji: {saved['premium']} ta." if saved["premium"] else ""
    await message.answer(
        f"✅ <b>Post saqlandi.</b>{note}\n\n"
        "Endi admin panel → <b>Ommaviy xabar</b> → «Telegram'dagi post»: kimga yuborishni tanlang, kerak bo'lsa "
        "tekshiruv tugmalarini belgilang va yuboring.\n\n"
        "⚠️ Bu xabarni o'chirmang — panel postni shu yerdan oladi. Boshqa post: /post"
    )
