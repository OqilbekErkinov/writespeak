"""Premium emoji in the bot's messages (the owner, 2026-11-22): bot/utils/premium_emoji.py, its session middleware
(bot/middlewares/premium_emoji.py) and the admins' /emoji and /post (bot/handlers/admin_tools.py). Offline: a fake
Bot API session, a throwaway Redis (run_test.sh).
"""
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.config import settings  # noqa: E402

if not settings.admin_ids:
    settings.admin_telegram_ids = "777"
ADMIN = sorted(settings.admin_ids)[0]

fails: list[str] = []


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


def u16(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def fake_session(refuse=None, strip=False):
    from aiogram.client.session.base import BaseSession
    from aiogram.types import Chat, Message, MessageEntity

    class S(BaseSession):
        def __init__(self):
            super().__init__()
            self.calls = []

        async def close(self):
            pass

        async def stream_content(self, *a, **k):
            yield b""

        async def make_request(self, bot, method, timeout=None):
            self.calls.append(method)
            err = refuse(method) if refuse else None
            if err is not None:
                raise err
            text = getattr(method, "text", None) or getattr(method, "caption", None) or "x"
            ents = [MessageEntity(type="custom_emoji", offset=0, length=1, custom_emoji_id="k")] \
                if "tg-emoji" in text and not strip else None
            return Message(message_id=len(self.calls), date=datetime.now(timezone.utc),
                           chat=Chat(id=getattr(method, "chat_id", 1) or 1, type="private"), text=text, entities=ents)

    return S()


def pure() -> None:
    from bot.utils import premium_emoji as pe

    m = {"✍": "111", "🎙": "222"}
    out, n = pe.apply("✍️ Writing <b>🎙 Speaking</b> <code>✍️ x</code> ✍ end", m)
    check("text: each mapped emoji wrapped (with or without U+FE0F), inside <b> too, not inside <code>",
          out == '<tg-emoji emoji-id="111">✍️</tg-emoji> Writing <b><tg-emoji emoji-id="222">🎙</tg-emoji> Speaking</b> '
                 '<code>✍️ x</code> <tg-emoji emoji-id="111">✍</tg-emoji> end' and n == 3, out)
    out, n = pe.apply('<tg-emoji emoji-id="9">🎙</tg-emoji> <pre><code class="x">🎙</code></pre> 🎙', m)
    check("an existing <tg-emoji> and <pre><code> left alone", out.count("222") == 1 and n == 1, out)
    out, n = pe.apply("👨‍💻 coder, 👍🏽 ok, ❤️‍🔥 hot, 👨 man, 👍 like, ❤ love",
                      {"👨": "3", "👍": "4", "❤": "5"})
    check("never inside a longer emoji (joined, skin tone): only the lone ones",
          n == 3 and out.startswith("👨‍💻 coder, 👍🏽 ok, ❤️‍🔥 hot, <tg-emoji"), out)
    out, n = pe.apply("1️⃣ birinchi, 1 dona", {"1⃣": "6"})
    check("a keycap wrapped, the digit alone not", n == 1 and out.endswith(", 1 dona"), out)
    out, n = pe.apply("✍" * 60, m)
    check("at most 40 premium emoji in one message", n == 40 and out.count("tg-emoji emoji-id") == 40)
    check("nothing mapped: the text unchanged", pe.apply("✍️ hi", {}) == ("✍️ hi", 0))
    check("a button starting with a mapped emoji: its text without it + the icon",
          pe.button_icon("✍️ Writing", m) == ("Writing", "111") and pe.button_icon("Writing ✍️", m) is None
          and pe.button_icon("✍️", m) is None)

    from aiogram.types import MessageEntity

    line1, line2, line3 = "✍️ = 📝", "🔥 ⭐", "🎯"
    text = f"{line1}\n{line2}\n{line3}"
    ents = [MessageEntity(type="custom_emoji", offset=u16("✍️ = "), length=u16("📝"), custom_emoji_id="a"),
            MessageEntity(type="custom_emoji", offset=u16(f"{line1}\n"), length=u16("🔥"), custom_emoji_id="b"),
            MessageEntity(type="custom_emoji", offset=u16(f"{line1}\n🔥 "), length=u16("⭐"), custom_emoji_id="c"),
            MessageEntity(type="custom_emoji", offset=u16(f"{line1}\n{line2}\n"), length=u16("🎯"), custom_emoji_id="d"),
            MessageEntity(type="bold", offset=0, length=1)]
    got = pe.picked_from(text, ents)
    check("an admin's message: '✍️ = <premium>' takes ✍️'s place; the others their own emoji's",
          got == [("✍️", "a"), ("🔥", "b"), ("⭐", "c"), ("🎯", "d")], got)
    two = "✍️ 🔥 📝"
    got = pe.picked_from(two, [MessageEntity(type="custom_emoji", offset=u16("✍️ 🔥 "), length=u16("📝"),
                                             custom_emoji_id="x")])
    check("two plain emoji beside it: the premium one takes its own emoji's place", got == [("📝", "x")], got)
    check("one emoji: a flag, a joined one, a skin tone, a keycap - not two side by side",
          all(pe.single_emoji(e) for e in ("🇺🇿", "👨‍💻", "👍🏽", "1️⃣", "✍️"))
          and not any(pe.single_emoji(e) for e in ("✍️🔥", "🇺🇿🇬🇧", "ab", "")))
    check("no premium emoji in it: nothing", pe.picked_from("✍️ hello", [MessageEntity(type="bold", offset=0, length=2)])
          == [])
    top = pe.most_used(5, texts=["✍️ a ✍️ b 🎙", "✍ c 📊"])
    check("the most used emoji of the bot's texts, as written", top[0] == ("✍️", 3) and ("🎙", 1) in top, top)
    check("...and how many texts have one", pe.usage("✍", ["✍️ a", "b", "✍ c"]) == 2)


async def middleware() -> None:
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, MessageEntity,
                               ReplyKeyboardMarkup)

    from bot.middlewares import premium_emoji as mw
    from bot.utils import crm
    from bot.utils import premium_emoji as pe

    await pe.clear()
    await pe.save([("✍️", "111"), ("🎙", "222")])

    def build(refuse=None, strip=False):
        s = fake_session(refuse, strip)
        bot = Bot("123456:TEST", session=s, default=DefaultBotProperties(parse_mode="HTML"))
        bot.session.middleware(mw.PremiumEmoji())
        return bot, s

    bot, s = build()
    await bot.send_message(5, "✍️ Writing tekshiruvi")
    check("a message: the premium emoji put in before it goes", s.calls[-1].text.startswith('<tg-emoji emoji-id="111">'))
    await bot.send_message(5, "✍️ plain", parse_mode=None)
    await bot.send_message(5, "✍️ own", entities=[MessageEntity(type="bold", offset=0, length=3)])
    check("no parse mode, or its own entities: left as it is",
          s.calls[-2].text == "✍️ plain" and s.calls[-1].text == "✍️ own")
    await bot.send_photo(5, "PHOTO", caption="🎙 Speaking")
    await bot.edit_message_text("🎙 live", chat_id=5, message_id=3)
    check("a caption and an edited message too", "222" in s.calls[-2].caption and "222" in s.calls[-1].text)
    await bot.send_message(5, "Tanlang", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Writing", callback_data="w"), InlineKeyboardButton(text="Help", callback_data="h")]]))
    b1, b2 = s.calls[-1].reply_markup.inline_keyboard[0]
    check("an inline button: the emoji becomes its premium icon, the rest unchanged",
          b1.text == "Writing" and b1.model_extra.get("icon_custom_emoji_id") == "111" and b1.callback_data == "w"
          and b2.text == "Help" and not (b2.model_extra or {}).get("icon_custom_emoji_id"), (b1, b2))
    sent = bot.session.prepare_value(s.calls[-1].reply_markup, bot=bot, files={})
    check("...and the icon goes to Telegram", '"icon_custom_emoji_id": "111"' in sent or
          '"icon_custom_emoji_id":"111"' in sent, sent[:200])
    await bot.send_message(5, "Menyu", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="✍️ Writing")]]))
    check("a reply keyboard left alone (its text is what a press sends back)",
          s.calls[-1].reply_markup.keyboard[0][0].text == "✍️ Writing")

    def refuse(method):
        if "tg-emoji" in (getattr(method, "text", None) or ""):
            return TelegramBadRequest(method=method, message="Bad Request: DOCUMENT_INVALID")
        return None

    bot, s = build(refuse)
    msg = await bot.send_message(5, "✍️ Salom")
    check("Telegram refuses it with premium emoji: sent again plain, the student gets it",
          len(s.calls) == 2 and "tg-emoji" in s.calls[0].text and s.calls[1].text == "✍️ Salom" and msg.text == "✍️ Salom")

    def not_modified(method):
        return TelegramBadRequest(method=method, message="Bad Request: message is not modified")

    bot, s = build(not_modified)
    try:
        await bot.edit_message_text("✍️ same", chat_id=5, message_id=1)
        raised = False
    except TelegramBadRequest:
        raised = True
    check("'message is not modified' raised as before, not sent twice", raised and len(s.calls) == 1)

    posted = []

    async def fake_post(topic, text, **kw):
        posted.append((topic, text))

    real_post, crm.post = crm.post, fake_post
    await crm.redis().delete(pe.STRIPPED_KEY.format(day=crm.local_now().date().isoformat()))
    try:
        bot, s = build(strip=True)
        await bot.send_message(5, "✍️ one")
        await bot.send_message(5, "✍️ two")
        check("Telegram drops them (the owner's Premium ended): the group told once a day, the messages still go",
              len(posted) == 1 and posted[0][0] == "errors" and "Premium" in posted[0][1] and len(s.calls) == 2, posted)
    finally:
        crm.post = real_post

    await pe.clear()
    bot, s = build()
    await bot.send_message(5, "✍️ none")
    check("no premium emoji saved: messages go untouched", s.calls[-1].text == "✍️ none")


async def handlers() -> None:
    from aiogram import Bot, Dispatcher
    from aiogram.types import Chat, Message, MessageEntity, PhotoSize, Update, User

    from bot.handlers import admin_tools
    from bot.utils import broadcast_post
    from bot.utils import premium_emoji as pe
    from bot.utils.crm import redis

    await pe.clear()
    await redis().delete(broadcast_post.KEY)
    s = fake_session()
    bot = Bot("123456:TEST", session=s, default=None)
    dp = Dispatcher()
    dp.include_router(admin_tools.router)
    n = [0]

    def upd(uid=ADMIN, **kw):
        n[0] += 1
        return Update(update_id=n[0], message=Message(
            message_id=100 + n[0], date=datetime.now(timezone.utc), chat=Chat(id=uid, type="private"),
            from_user=User(id=uid, is_bot=False, first_name="Admin"), **kw))

    def replies():
        return [c.text for c in s.calls if type(c).__name__ == "SendMessage"]

    await dp.feed_update(bot, upd(text="/emoji"))
    check("/emoji: the list (none yet), how to add, the bot's most used emoji",
          "Premium emoji" in replies()[-1] and "Hali birorta" in replies()[-1] and "eng ko'p" in replies()[-1])
    text = "✍️ = 📝"
    ents = [MessageEntity(type="custom_emoji", offset=u16("✍️ = "), length=u16("📝"), custom_emoji_id="a1")]
    await dp.feed_update(bot, upd(text=text, entities=ents))
    saved = await pe.entries()
    check("the premium emoji sent next: saved for ✍️, the answer shows it",
          saved.get("✍", {}).get("id") == "a1" and "Saqlandi (1)" in replies()[-1] and "a1" in replies()[-1], saved)
    before = len(s.calls)
    await dp.feed_update(bot, upd(text="An essay text with no premium emoji at all"))
    check("after that the flow is over: the next message is not taken", len(s.calls) == before)
    await dp.feed_update(bot, upd(text="/emoji o'chir ✍️"))
    check("/emoji o'chir ✍️: that one dropped", "✍" not in await pe.entries() and "oddiy emoji" in replies()[-1])
    await pe.save([("🎙", "z")])
    await dp.feed_update(bot, upd(text="/emoji tozalash"))
    check("/emoji tozalash: all dropped", await pe.entries() == {} and "olib tashlandi (1" in replies()[-1])

    before = len(s.calls)
    await dp.feed_update(bot, upd(uid=ADMIN + 1, text="/emoji"))
    check("a student's /emoji: these handlers say nothing", len(s.calls) == before)

    await dp.feed_update(bot, upd(text="/post"))
    check("/post: asks for the post", "Ommaviy xabar uchun post" in replies()[-1])
    photo = [PhotoSize(file_id="P", file_unique_id="p", width=1, height=1)]
    await dp.feed_update(bot, upd(photo=photo, caption="a", media_group_id="g1"))
    await dp.feed_update(bot, upd(photo=photo, caption="b", media_group_id="g1"))
    check("an album: refused once, the post still awaited",
          sum("albom" in (r or "") for r in replies()) == 1 and await broadcast_post.load() is None)
    cap = "🎁 8 coin sovg'a"
    await dp.feed_update(bot, upd(photo=photo, caption=cap, caption_entities=[
        MessageEntity(type="custom_emoji", offset=0, length=u16("🎁"), custom_emoji_id="g")]))
    post = await broadcast_post.load()
    copies = [c for c in s.calls if type(c).__name__ == "CopyMessage"]
    check("a picture post: kept (its chat, message, kind, premium count), shown back as students get it",
          post and post["chat_id"] == ADMIN and post["kind"] == "photo" and post["premium"] == 1
          and post["preview"] == cap and copies and copies[-1].message_id == post["message_id"]
          and "Post saqlandi" in replies()[-1], post)
    await redis().delete(broadcast_post.KEY)


async def main() -> int:
    pure()
    await middleware()
    await handlers()
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    from bot.utils.crm import redis

    await redis().aclose()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
