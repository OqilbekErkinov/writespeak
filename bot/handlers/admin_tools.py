"""The admins' tools in their private chat with the bot (the owner, 2026-11-22):

- /emoji: the premium emoji the bot puts into its messages, buttons and main menu (bot/utils/premium_emoji.py) -
  bare to see how many of the bot's emoji are premium and to send new ones: premium emoji themselves, or a pack's
  link (t.me/addemoji/...) that brings every emoji of the pack the bot's texts use; `/emoji o'chir ✍️` drops one,
  `/emoji tozalash` all;
- /post: the next message is kept as the panel's broadcast post (bot/utils/broadcast_post.py) - written or
  forwarded in Telegram, with its bold, links, premium emoji, picture or video - and shown back as the students
  will get it; that is its Uzbek version, and the buttons under the answer (or `/post ru`, `/post en`) add the
  Russian and the English one, each student getting their language's.

Only the bot's admins reach these handlers; for anyone else /emoji and /post are unknown words, as before.
"""
from __future__ import annotations

import html
import json
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import settings
from bot.states.admin_states import AdminStates
from bot.utils import broadcast_post, premium_emoji
from bot.utils.crm import redis

logger = logging.getLogger(__name__)
router = Router(name="admin_tools")
router.message.filter(F.from_user.id.in_(settings.admin_ids))
router.callback_query.filter(F.from_user.id.in_(settings.admin_ids))

REMOVE_WORDS = ("o'chir", "ochir", "o‘chir", "o`chir", "del", "-")
CLEAR_WORDS = ("tozalash", "tozala", "clear", "hammasi")
CB_PACK = "pe:pack:"
CB_POST = "bp:lang:"  # add or change one language version of the broadcast post
OFFER_KEY = "premium_emoji:offer:{uid}"  # the packs offered under the last answer
SHOWN = 30  # premium emoji drawn in one answer at most (the middleware may add its 40 more)
MAX_PACKS = 3


def _not_command(message: Message) -> bool:
    return not (message.text or "").startswith("/")


def _code(emoji: str) -> str:
    """A plain emoji shown as it is: in <code>, where the bot puts no premium one."""
    return f"<code>{html.escape(emoji)}</code>"


async def _progress() -> tuple[int, int, list[str], dict[str, dict]]:
    """(premium ones, all the bot's emoji, the plain ones left, the saved map)."""
    saved = await premium_emoji.entries()
    used = [e for e, _n in premium_emoji.most_used()]
    left = [e for e in used if premium_emoji.canon(e) not in saved]
    return len(used) - len(left), len(used), left, saved


def _drawn(saved: dict[str, dict]) -> str:
    items = list(saved.values())
    line = " ".join(premium_emoji.tag(i["alt"], i["id"]) for i in items[:SHOWN])
    return line + (f" +{len(items) - SHOWN} ta" if len(items) > SHOWN else "")


async def _status() -> str:
    done, total, left, saved = await _progress()
    lines = [f"Bot matnlarida <b>{total}</b> xil emoji bor: <b>{done}</b> tasi premium, {len(left)} tasi hali oddiy."]
    if saved:
        lines.append(f"Premium: {_drawn(saved)}")
    if left:
        lines.append(f"Hali oddiy: <code>{html.escape(' '.join(left))}</code>")
    return "\n\n".join(lines)


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
    await state.set_state(AdminStates.emoji)
    await message.answer(
        "✨ <b>Premium emoji</b>\n\n" + await _status() + "\n\n"
        "<b>Qo'shishning 2 yo'li</b> (hozir shu yerga yuboring):\n"
        "1) <b>To'plam havolasi.</b> Premium emoji to'plamini oching → «Ulashish» → havolani (t.me/addemoji/...) "
        "yuboring. Bot to'plamdagi mos emojilarning hammasini o'zi oladi.\n"
        "2) <b>Emojilarning o'zi.</b> Premium emojilarni bitta xabarda yuboring: har biri bot matnlaridagi xuddi shu "
        "oddiy emoji o'rniga chiqadi. Boshqa emoji o'rniga qo'yish uchun har qatorga avval oddiy emoji, keyin "
        "premium emoji yozing, masalan: <code>✍️ = </code>(premium emoji)\n\n"
        "Premium emojilar bot xabarlarida, xabar ostidagi tugmalarda va pastki menyuda chiqadi.\n"
        "Bittasini olib tashlash: <code>/emoji o'chir ✍️</code> · hammasini: <code>/emoji tozalash</code> · "
        "bekor qilish: /cancel"
    )


async def _pack(bot: Bot, name: str, have: set[str]) -> tuple[str, list[tuple[str, str]], int] | None:
    """A pack's title, its emoji for the bot's plain ones `have` has not got, and its size; None if no such pack."""
    try:
        pack = await bot.get_sticker_set(name)
    except Exception as e:  # noqa: BLE001
        logger.info("Emoji pack %s not loaded: %r", name, e)
        return None
    if getattr(pack, "sticker_type", "") != "custom_emoji":
        return None
    return pack.title, premium_emoji.from_pack(pack.stickers, have), len(pack.stickers)


async def _offer(bot: Bot, uid: int, picked: list[tuple[str, str]], skip: set[str]) -> InlineKeyboardMarkup | None:
    """Under the answer: "take the rest of this pack" for the packs the sent premium emoji come from."""
    try:
        stickers = await bot.get_custom_emoji_stickers([cid for _alt, cid in picked][:200])
    except Exception as e:  # noqa: BLE001
        logger.info("Premium emoji packs not found: %r", e)
        return None
    names = []
    for st in stickers:
        if st.set_name and st.set_name not in names and st.set_name not in skip:
            names.append(st.set_name)
    have = set(await premium_emoji.entries())
    offers, rows = [], []
    for name in names[:MAX_PACKS]:
        found = await _pack(bot, name, have)
        if found and found[1]:
            rows.append([InlineKeyboardButton(text=f"📦 «{found[0][:30]}» dan yana {len(found[1])} ta",
                                              callback_data=f"{CB_PACK}{len(offers)}")])
            offers.append(name)
    if not offers:
        return None
    await redis().set(OFFER_KEY.format(uid=uid), json.dumps(offers), ex=86400)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _saved_lines(picked: list[tuple[str, str]], texts: list[str]) -> list[str]:
    lines = []
    for alt, cid in picked[:30]:
        used = premium_emoji.usage(alt, texts)
        lines.append(f"{premium_emoji.tag(alt, cid)} — {_code(alt)} o'rniga · "
                     + (f"{used} joyda" if used else "⚠️ bot matnlarida yo'q, hech qayerda chiqmaydi"))
    if len(picked) > 30:
        lines.append(f"… va yana {len(picked) - 30} ta")
    return lines


@router.message(StateFilter(AdminStates.emoji), _not_command)
async def emoji_picked(message: Message, state: FSMContext, bot: Bot) -> None:
    await state.clear()
    text = message.text or message.caption or ""
    picked = premium_emoji.picked_from(text, message.entities or message.caption_entities)
    links = list(dict.fromkeys(premium_emoji.PACK_LINK.findall(text)))[:MAX_PACKS]
    if not picked and not links:
        await message.answer("Bu xabarda premium emoji ham, to'plam havolasi ham topilmadi. Premium (jonli) "
                             "emojilarni yoki to'plam havolasini (t.me/addemoji/...) yuboring. Qaytadan: /emoji")
        return
    parts = []
    if picked:
        await premium_emoji.save(picked)  # chosen one by one: they win over what a pack brought
        parts.append(f"✅ <b>Saqlandi ({len(picked)}):</b>\n" + "\n".join(_saved_lines(picked, premium_emoji.bot_texts())))
    for name in links:
        found = await _pack(bot, name, set(await premium_emoji.entries()))
        if found is None:
            parts.append(f"⚠️ «{html.escape(name)}» premium emoji to'plami topilmadi.")
            continue
        title, new, size = found
        await premium_emoji.save(new)
        parts.append(f"📦 <b>«{html.escape(title)}»</b>: {size} ta emojidan <b>{len(new)}</b> tasi bot matnlaridagi "
                     "emoji o'rniga olindi" + (f": {_drawn({a: {'alt': a, 'id': c} for a, c in new})}" if new else
                                               " (bu to'plamda bot ishlatadigan emojilar yo'q)."))
    kb = await _offer(bot, message.from_user.id, picked, set(links)) if picked else None
    done, total, left, _saved = await _progress()
    await message.answer("\n\n".join(parts) + f"\n\nEndi <b>{done}/{total}</b> premium. Bir daqiqada bot xabarlarida "
                         "chiqadi." + (f"\nHali oddiy: <code>{html.escape(' '.join(left))}</code>" if left else "")
                         + "\nYana qo'shish: /emoji", reply_markup=kb)


@router.callback_query(F.data.startswith(CB_PACK))
async def take_pack(callback: CallbackQuery, bot: Bot) -> None:
    raw = await redis().get(OFFER_KEY.format(uid=callback.from_user.id))
    try:
        name = json.loads(raw)[int(callback.data[len(CB_PACK):])] if raw else None
    except (ValueError, IndexError):
        name = None
    if name is None:
        await callback.answer("Bu taklif eskirgan - /emoji ni qayta yuboring.", show_alert=True)
        return
    await callback.answer()
    found = await _pack(bot, name, set(await premium_emoji.entries()))
    if found is None:
        await callback.message.answer("⚠️ To'plamni ochib bo'lmadi. Havolasini /emoji orqali yuboring.")
        return
    title, new, _size = found
    await premium_emoji.save(new)
    done, total, _left, _saved = await _progress()
    await callback.message.answer(
        f"📦 <b>«{html.escape(title)}»</b>: yana <b>{len(new)}</b> ta olindi"
        + (f": {_drawn({a: {'alt': a, 'id': c} for a, c in new})}" if new else " (yangi mos emoji qolmagan).")
        + f"\n\nEndi <b>{done}/{total}</b> premium. Ro'yxat: /emoji")


def _version(lang: str) -> str:
    return f"{broadcast_post.FLAGS[lang]} {broadcast_post.NAMES[lang].lower()}"


def _versions_kb(posts: dict[str, dict]) -> InlineKeyboardMarkup:
    """Under the bot's answer: add (or change) the Russian and the English version, change the Uzbek one."""
    rows = []
    for lang in ("ru", "en", "uz"):
        name = f"{broadcast_post.FLAGS[lang]} {broadcast_post.NAMES[lang]}"
        rows.append([InlineKeyboardButton(text=f"🔁 {name} versiyani almashtirish" if lang in posts else
                                          f"➕ {name} versiya qo'shish", callback_data=f"{CB_POST}{lang}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _ask_post(message: Message, state: FSMContext, lang: str, fresh: bool) -> None:
    posts = await broadcast_post.load()
    if not fresh and lang != "uz" and "uz" not in posts:
        await message.answer("Avval postning o'zbekcha versiyasi kerak: /post")
        return
    await state.set_state(AdminStates.post)
    await state.update_data(post_lang=lang, post_fresh=fresh)
    if fresh:
        text = ("📨 <b>Ommaviy xabar uchun yangi post</b>\n\n"
                "Avval postning <b>🇺🇿 o'zbekcha</b> versiyasini shu yerga yuboring: o'zingiz yozing yoki kanaldan "
                "forward qiling. Qalin va qiya harf, havola, premium emoji, bitta rasm yoki video — hammasi "
                "o'quvchilarga aynan shunday boradi.\n\nKeyin ruscha va inglizcha versiyalarini qo'shasiz "
                "(ixtiyoriy).")
    else:
        text = (f"📨 <b>Postning {_version(lang)} versiyasi</b>\n\n"
                f"{broadcast_post.NAMES[lang]} versiyani shu yerga yuboring"
                + (" — eskisi almashtiriladi." if lang in posts else ".")
                + (f" Uni tili {broadcast_post.NAMES[lang].lower()} bo'lgan o'quvchilar oladi." if lang != "uz" else ""))
    await message.answer(text + "\n\nBekor qilish: /cancel")


@router.message(Command("post"))
async def post(message: Message, state: FSMContext, command: CommandObject) -> None:
    """/post: a new post (its Uzbek version first); `/post ru`, `/post en`, `/post uz`: one version of it."""
    lang = (command.args or "").strip().lower()[:2]
    if lang in broadcast_post.LANGS:
        await _ask_post(message, state, lang, fresh=False)
    else:
        await _ask_post(message, state, "uz", fresh=True)


@router.callback_query(F.data.startswith(CB_POST))
async def post_version(callback: CallbackQuery, state: FSMContext) -> None:
    lang = callback.data[len(CB_POST):]
    await callback.answer()
    if lang in broadcast_post.LANGS:
        await _ask_post(callback.message, state, lang, fresh=False)


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
    data = await state.get_data()
    lang = data.get("post_lang") if data.get("post_lang") in broadcast_post.LANGS else "uz"
    fresh = bool(data.get("post_fresh", "post_lang" not in data))  # a /post from before the versions: a new post
    await state.clear()
    await message.answer(f"👀 O'quvchilar postni shunday ko'radi ({_version(lang)}):")
    try:
        await bot.copy_message(message.chat.id, message.chat.id, message.message_id)
    except Exception as e:  # noqa: BLE001 - e.g. a forward of a protected channel post
        logger.info("Broadcast post preview failed: %r", e)
        await message.answer("⚠️ Bu postni nusxalab bo'lmadi (masalan, kanal postlarni nusxalashni taqiqlagan). "
                             "Postni o'zingiz yozib yuboring: /post" + (f" {lang}" if not fresh else ""))
        return
    replaced = fresh and len(await broadcast_post.load()) > 0
    saved = await broadcast_post.save(message, message.from_user.full_name or str(message.from_user.id), lang, fresh)
    posts = await broadcast_post.load()
    note = f"\nPremium emoji: {saved['premium']} ta." if saved["premium"] else ""
    await message.answer(
        f"✅ <b>{broadcast_post.FLAGS[lang]} {broadcast_post.NAMES[lang]} versiya saqlandi.</b>{note}"
        + ("\nBu yangi post — oldingi post va uning versiyalari o'chirildi." if replaced else "")
        + f"\n\nVersiyalar: {broadcast_post.versions_line(posts)}\n"
        "Har bir o'quvchi o'z tilidagi versiyani oladi; versiya bo'lmasa — o'zbekchasini.\n\n"
        "Yuborish: admin panel → <b>Ommaviy xabar</b> → «Telegram'dagi post».\n"
        "⚠️ Bu xabarlarni o'chirmang — panel postni shu yerdan oladi. Yangi post: /post",
        reply_markup=_versions_kb(posts),
    )
