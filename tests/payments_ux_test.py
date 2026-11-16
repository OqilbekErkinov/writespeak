"""2026-11-16, before opening to everyone - the student-facing and payment fixes, offline (a fake Telegram, a
throwaway Postgres + Redis): a Practice question is shown without its answer step, with the paywall or the
pause note, when a check can't start (C1); a rejection carries its reason, "send the receipt again" and the
support chat, every admin's copy of the card is cleared and a panel decision names its login (C2); /support and
the help texts (C4); a receipt sent as an image file or a PDF (C5); a group gift, a coin change by hand and an
approval never write a stale balance back (B10).
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from bot.config import settings  # noqa: E402

settings.web_2fa = False  # the password alone signs in (the two-step sign-in: tests/panel_access_test.py)
settings.admin_telegram_ids = "111,222,333"  # three bot admins: each gets a copy of a receipt's card
ADMINS = (111, 222, 333)

fails: list[str] = []


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


ST, RICH, RU, TEACH, OTHER, GT, GS = 9_500_001, 9_500_002, 9_500_003, 9_500_004, 9_500_005, 9_500_006, 9_500_007
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PDF = b"%PDF-1.4\n%fake receipt\n"


def buttons(markup) -> list[str]:
    rows = getattr(markup, "inline_keyboard", None) or []
    return [b.callback_data for row in rows for b in row]


def button_texts(markup) -> list[str]:
    rows = getattr(markup, "inline_keyboard", None) or []
    return [b.text for row in rows for b in row]


def of(calls, name: str) -> list:
    return [c for c in calls if type(c).__name__ == name]


def texts(calls) -> list[str]:
    return [c.text for c in calls if type(c).__name__ in ("SendMessage", "EditMessageText")]


async def main() -> int:
    from aiogram import Bot
    from aiogram.client.session.base import BaseSession
    from aiogram.types import CallbackQuery, Chat, Document, Message, PhotoSize, Update, User
    from sqlalchemy import select, update

    import webapp.main as wm
    from bot.handlers import payments as pay
    from bot.i18n import t
    from bot.keyboards.payment_kb import REJECT_REASONS
    from bot.main import build_dispatcher
    from bot.states.payment_states import PaymentStates
    from bot.states.support_states import SupportStates
    from bot.states.writing_states import WritingStates
    from bot.utils import crm, crm_events, runtime
    from bot.utils.crm import redis
    from db import crud
    from db.database import get_session
    from db.models import (
        AdminAction, CheckHold, CoinAdjustment, Group, GroupMembership, PaymentKind, PaymentRequest, PaymentStatus,
        PracticeModule, PracticeQuestion, User as DbUser, UserRole, UserStatus,
    )

    await redis().flushdb()
    async with get_session() as s:
        for uid, coins, lang, role in ((ST, 2, "uz", UserRole.student), (RICH, 100, "uz", UserRole.student),
                                       (RU, 2, "ru", UserRole.student), (TEACH, 0, "uz", UserRole.teacher),
                                       (OTHER, 2, "uz", UserRole.student), (GT, 100, "uz", UserRole.teacher),
                                       (GS, 10, "uz", UserRole.student)):
            s.add(DbUser(id=uid, full_name=f"U{uid}", coin_balance=coins, language=lang, status=UserStatus.approved,
                         role=role))
        s.add(DbUser(id=111, full_name="Admin111", coin_balance=0, language="uz", status=UserStatus.approved,
                     role=UserRole.admin))  # an admin with no coins of their own
        await s.commit()
    # the admins' group set up: its cards are queued in Redis (no sender runs here), the support chat is open
    cfg_path = crm.config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps({"chat_id": -100, "topics": {"payments": 5, "support": 6},
                                    "backfill_at": "2026-11-16T00:00:00+00:00"}), encoding="utf-8")
    check("setup: the admins' group counts as set up", crm.ready())

    now = datetime.now(timezone.utc)

    class TgSession(BaseSession):
        def __init__(self):
            super().__init__()
            self.calls, self.sent, self.n = [], [], 7000

        async def close(self):
            pass

        async def stream_content(self, *a, **k):
            yield b""

        async def make_request(self, bot, method, timeout=None):
            self.calls.append(method)
            self.n += 1
            name = type(method).__name__
            if name in ("AnswerCallbackQuery", "DeleteMessage", "EditMessageReplyMarkup", "EditMessageCaption",
                        "EditMessageText", "SendChatAction", "SendSticker"):
                return True
            self.sent.append((method, self.n))
            return Message(message_id=self.n, date=now,
                           chat=Chat(id=getattr(method, "chat_id", None) or ST, type="private"),
                           text=getattr(method, "text", None) or "x").as_(bot)

    session = TgSession()
    bot = Bot("8888548766:TEST", session=session)
    dp = build_dispatcher()
    counter = iter(range(500_000, 900_000))

    def ctx(uid):
        return dp.fsm.get_context(bot=bot, chat_id=uid, user_id=uid)

    def who(uid):
        return User(id=uid, is_bot=False, first_name=f"Admin{uid}" if uid in ADMINS else "S")

    async def send(uid, **kw):
        start = len(session.calls)
        message = Message(message_id=next(counter), date=now, chat=Chat(id=uid, type="private"), from_user=who(uid),
                          **kw)
        await dp.feed_update(bot, Update(update_id=next(counter), message=message))
        return session.calls[start:], message.message_id

    async def press(uid, data, message=None):
        start = len(session.calls)
        message = message or Message(message_id=next(counter), date=now, chat=Chat(id=uid, type="private"), text="x")
        query = CallbackQuery(id=str(next(counter)), from_user=who(uid), chat_instance="ci", data=data,
                              message=message)
        await dp.feed_update(bot, Update(update_id=next(counter), callback_query=query))
        return session.calls[start:]

    def alerts(calls):
        return [c.text for c in of(calls, "AnswerCallbackQuery") if c.text]

    def to(calls, uid) -> list:
        return [c for c in calls if getattr(c, "chat_id", None) == uid]

    async def latest(uid) -> PaymentRequest | None:
        async with get_session() as s:
            return (await s.execute(select(PaymentRequest).where(PaymentRequest.user_id == uid)
                                    .order_by(PaymentRequest.id.desc()))).scalars().first()

    async def get(model, key):
        async with get_session() as s:
            return await s.get(model, key)

    async def receipt_photo(uid, file_id, unique, pack=10):
        """A pack chosen and its receipt photo sent: (the request, the admins' cards {admin: message id})."""
        await press(uid, f"buy:{pack}")
        mark = len(session.sent)
        await send(uid, photo=[PhotoSize(file_id=file_id, file_unique_id=unique, width=10, height=10)])
        req = await latest(uid)
        cards = {m.chat_id: mid for m, mid in session.sent[mark:] if type(m).__name__ in ("SendPhoto", "SendDocument")}
        return req, cards

    def card(admin, message_id, caption="💳 Yangi to'lov so'rovi"):
        return Message(message_id=message_id, date=now, chat=Chat(id=admin, type="private"), caption=caption)

    # --- C1: Practice for a student who can't start a check ------------------------------------------------------
    chart = Path(settings.file_storage_path) / "payments_ux_chart.png"
    chart.parent.mkdir(parents=True, exist_ok=True)
    chart.write_bytes(PNG)
    async with get_session() as s:
        w2 = PracticeQuestion(module=PracticeModule.writing_task2,
                              question_text="Some people think robots will replace teachers. Discuss both views.")
        w1 = PracticeQuestion(module=PracticeModule.writing_task1, question_text="The chart shows sales in 2020.",
                              image_path=str(chart))
        s1 = PracticeQuestion(module=PracticeModule.speaking_part1, topic="Work & study",
                              question_text="Do you work or study?\nWhat do you like about it?")
        s2 = PracticeQuestion(module=PracticeModule.speaking_part2, topic="A book",
                              question_text="Describe a book you read.\nYou should say: what it was about")
        s.add_all([w2, w1, s1, s2])
        await s.commit()
        w2_id, w1_id, s1_id, s2_id = w2.id, w1.id, s1.id, s2.id
    st = ctx(ST)

    await st.set_state(WritingStates.awaiting_answer)  # left at an earlier question: that gives way
    out = await press(ST, f"practice:q:{w2_id}")
    msgs = of(out, "SendMessage")
    question = [m for m in msgs if "robots will replace teachers" in (m.text or "")]
    paywall = [m for m in msgs if "Coin yetarli emas" in (m.text or "")]
    check("2 coins, Task 2: the question shown, with the back-to-list button",
          len(question) == 1 and "📋 Savol" in question[0].text and buttons(question[0].reply_markup)
          == [f"practice:back:{w2_id}"], texts(out))
    check("...not asked for an answer (no 'send your answer', no honest-work note)",
          question and "Javobingizni yuboring" not in question[0].text and "o'zingiz yozing" not in question[0].text)
    check("...the paywall under it, with the packs", question and len(paywall) == 1
          and msgs.index(paywall[0]) > msgs.index(question[0])
          and "buy:10" in buttons(paywall[0].reply_markup))
    check("...no answer state: nothing will be graded", await st.get_state() is None, await st.get_state())

    out = await press(ST, f"practice:q:{w1_id}")
    photos = of(out, "SendPhoto")
    check("Task 1: its chart with the question as the caption, then the paywall",
          len(photos) == 1 and "The chart shows sales" in (photos[0].caption or "")
          and any("Coin yetarli emas" in x for x in texts(out)) and await st.get_state() is None)

    out = await press(ST, f"practice:q:{s1_id}")
    shown = [x for x in texts(out) if "Do you work or study?" in x]
    check("Speaking Part 1: the topic and the whole set, numbered, no 'send a voice message'",
          len(shown) == 1 and "Work &amp; study" in shown[0] and "1. Do you work or study?" in shown[0]
          and "2. What do you like about it?" in shown[0] and "ovozli xabar" not in shown[0], texts(out))
    check("...then the paywall, and no answer state", any("Coin yetarli emas" in x for x in texts(out))
          and await st.get_state() is None, await st.get_state())
    out = await press(ST, f"practice:q:{s2_id}")
    check("Speaking Part 2: the cue card", any("Cue card" in x and "Describe a book" in x for x in texts(out))
          and await st.get_state() is None, texts(out))

    adm = ctx(111)
    await adm.set_state(WritingStates.processing)  # an admin (never "busy") with a check being graded, 0 coins
    out = await press(111, f"practice:q:{w2_id}")
    check("an admin's check being graded is not dropped by looking at a question",
          await adm.get_state() == WritingStates.processing.state
          and any("robots will replace teachers" in x for x in texts(out)), await adm.get_state())
    await adm.clear()

    await runtime.set_testers({111})  # a test window: checks only for its testers
    out = await press(ST, f"practice:q:{w2_id}")
    check("checks paused: the question, then the pause note (no paywall)",
          any("robots will replace teachers" in x for x in texts(out))
          and any("Bandzilla yangilanmoqda" in x for x in texts(out))
          and not any("Coin yetarli emas" in x for x in texts(out)) and await st.get_state() is None, texts(out))
    await runtime.set_testers(set())

    async with get_session() as s:
        s.add(CheckHold(id="ux-busy-hold", user_id=ST, chat_id=ST, module="w", kind="writing_task2", coins=0,
                        state="held", exclusive=True))
        await s.commit()
    out = await press(ST, f"practice:q:{w2_id}")
    check("a check of theirs being graded: only 'wait for it', as before (no question)",
          any("hali baholanmoqda" in x for x in texts(out))
          and not any("robots" in x for x in texts(out)) and not of(out, "SendPhoto"), texts(out))
    async with get_session() as s:
        await s.delete(await s.get(CheckHold, "ux-busy-hold"))
        await s.commit()

    out = await press(RICH, f"practice:q:{w2_id}")
    check("with coins: the question starts as before (answer state, 'send your answer')",
          await ctx(RICH).get_state() == WritingStates.awaiting_answer.state
          and any("Javobingizni yuboring" in x for x in texts(out)), texts(out))
    await ctx(RICH).clear()

    await press(ST, "buy:10")
    waiting = await latest(ST)
    await press(ST, f"practice:q:{w2_id}")
    check("a receipt being waited for stays while they look at a question",
          await st.get_state() == PaymentStates.awaiting_receipt.state
          and (await st.get_data()).get("payment_request_id") == waiting.id, await st.get_state())
    await press(ST, f"pay_cancel:{waiting.id}")

    # --- C2: reject with a reason in the bot ------------------------------------------------------------------
    req1, cards = await receipt_photo(ST, "ux-photo-1", "UX-P1", pack=25)
    check("a receipt photo: each of the three admins gets the card with ✅/⛔",
          set(cards) == set(ADMINS) and all(buttons(m.reply_markup) == [f"pay_approve:{req1.id}", f"pay_reject:{req1.id}"]
                                             for m, _ in session.sent if type(m).__name__ == "SendPhoto"
                                             and getattr(m, "photo", None) == "ux-photo-1"), cards)
    saved = json.loads(await redis().get(f"pay:review:{req1.id}") or "{}")
    ttl = await redis().ttl(f"pay:review:{req1.id}")
    check("...their card ids kept in Redis for about 14 days",
          sorted(tuple(x) for x in saved.get("msgs", [])) == sorted(cards.items())
          and "Yangi to'lov so'rovi" in saved.get("caption", "") and 13 * 86400 < ttl <= 14 * 86400, (saved, ttl))

    out = await press(111, f"pay_reject:{req1.id}", card(111, cards[111]))
    edits = of(out, "EditMessageReplyMarkup")
    wanted = {f"pay_rjr:{req1.id}:{k}" for k in REJECT_REASONS} | {f"pay_rjb:{req1.id}"}
    check("⛔: the reasons on that card (and ↩️ back); nothing decided, the student told nothing",
          len(edits) == 1 and set(buttons(edits[0].reply_markup)) == wanted
          and {"Summa mos emas", "Chek o'qilmaydi", "Chek avval ishlatilgan", "Pul kelib tushmagan", "Sababsiz",
               "↩️ Orqaga"} == set(button_texts(edits[0].reply_markup))
          and (await get(PaymentRequest, req1.id)).status == PaymentStatus.pending and not to(out, ST),
          [buttons(e.reply_markup) for e in edits])
    out = await press(111, f"pay_rjb:{req1.id}", card(111, cards[111]))
    edits = of(out, "EditMessageReplyMarkup")
    check("↩️ Orqaga: ✅/⛔ again", len(edits) == 1
          and buttons(edits[0].reply_markup) == [f"pay_approve:{req1.id}", f"pay_reject:{req1.id}"])

    out = await press(111, f"pay_rjr:{req1.id}:amount", card(111, cards[111]))
    r1 = await get(PaymentRequest, req1.id)
    check("a reason picked: rejected, the reason stored", r1.status == PaymentStatus.rejected
          and r1.reject_reason == "amount" and r1.reviewed_by == 111, (r1.status, r1.reject_reason))
    caps = {c.chat_id: c for c in of(out, "EditMessageCaption")}
    check("every admin's copy cleared: the decision, who, the reason; no buttons left",
          set(caps) == set(ADMINS) and all(caps[a].message_id == cards[a] and caps[a].reply_markup is None
                                           and "Yangi to'lov so'rovi" in caps[a].caption
                                           and "⛔ Rad etildi — Admin111 · Sabab: Summa mos emas" in caps[a].caption
                                           for a in ADMINS), {a: c.caption for a, c in caps.items()})
    told = [c for c in to(out, ST) if type(c).__name__ == "SendMessage"]
    check("the student: '⛔ ... Sabab: summa mos emas' with 📸 resend and 💬 support",
          len(told) == 1 and "To'lovingiz tasdiqlanmadi. Sabab: <b>summa mos emas</b>" in told[0].text
          and "Chekni qayta yuborish" in told[0].text
          and buttons(told[0].reply_markup) == [f"pay_resend:{req1.id}", "support:write"]
          and button_texts(told[0].reply_markup) == ["📸 Chekni qayta yuborish", "💬 Admin bilan aloqa"],
          [(c.text, buttons(c.reply_markup)) for c in told])

    balance = (await get(DbUser, ST)).coin_balance
    out = await press(222, f"pay_approve:{req1.id}", card(222, cards[222]))
    check("another admin's stale ✅ afterwards: 'already decided', nothing changes, the student not told again",
          any("allaqachon" in x for x in alerts(out)) and not to(out, ST)
          and (await get(PaymentRequest, req1.id)).status == PaymentStatus.rejected
          and (await get(DbUser, ST)).coin_balance == balance, alerts(out))
    check("...and that copy shows what was decided, not 'approved'",
          all("⛔ Rad etildi" in c.caption and "Tasdiqlandi" not in c.caption for c in of(out, "EditMessageCaption")))
    out = await press(333, f"pay_reject:{req1.id}", card(333, cards[333]))
    check("a stale ⛔ afterwards: no reasons shown, 'already decided'", any("allaqachon" in x for x in alerts(out))
          and not any(buttons(e.reply_markup) for e in of(out, "EditMessageReplyMarkup")))

    out = await press(ST, f"pay_resend:{req1.id}")
    req2 = await latest(ST)
    check("📸 resend: a NEW pending request for the same pack (coins, amount, kind)",
          req2.id != req1.id and (req2.coins, req2.amount, req2.kind) == (req1.coins, req1.amount, req1.kind)
          and req2.status == PaymentStatus.pending and req2.receipt_file_id is None, (req2.id, req2.coins, req2.amount))
    check("...the receipt step exactly as choosing the pack: the card number, ❌, waiting for the receipt",
          await st.get_state() == PaymentStates.awaiting_receipt.state
          and (await st.get_data()).get("payment_request_id") == req2.id
          and any(settings.payment_card_number in x for x in texts(out))
          and any(f"pay_cancel:{req2.id}" in buttons(c.reply_markup) for c in of(out, "SendMessage")), texts(out))
    check("...the rejected one stays as it was", (await get(PaymentRequest, req1.id)).status == PaymentStatus.rejected)
    mark = len(session.sent)
    out, _ = await send(ST, photo=[PhotoSize(file_id="ux-photo-2", file_unique_id="UX-P2", width=10, height=10)])
    check("...and the next photo is taken as its receipt, to the admins", (await get(PaymentRequest, req2.id))
          .receipt_file_id == "ux-photo-2" and any("Chek qabul qilindi" in x for x in texts(out))
          and len([m for m, _ in session.sent[mark:] if type(m).__name__ == "SendPhoto"]) == 3, texts(out))

    out = await press(OTHER, f"pay_resend:{req1.id}")
    check("someone else's resend button: out of date, no request made", any("eskirgan" in x for x in texts(out))
          and await latest(OTHER) is None, texts(out))

    # a center pack, a gift, a Russian speaker, "Sababsiz"
    await ctx(TEACH).clear()
    treq, tcards = await receipt_photo(TEACH, "ux-photo-t", "UX-PT", pack=500)
    await press(111, f"pay_rjr:{treq.id}:reused", card(111, tcards[111]))
    await press(TEACH, f"pay_resend:{treq.id}")
    t2 = await latest(TEACH)
    check("a center pack sent again: 500 coins at 400 000 so'm, again", t2.id != treq.id and t2.coins == 500
          and t2.amount == 400_000 and await ctx(TEACH).get_state() == PaymentStates.awaiting_receipt.state)
    await press(TEACH, f"pay_cancel:{t2.id}")

    await press(ST, "giftbuy:10")
    await send(ST, photo=[PhotoSize(file_id="ux-photo-g", file_unique_id="UX-PG", width=10, height=10)])
    greq = await latest(ST)
    out = await press(222, f"pay_rjr:{greq.id}:none", card(222, 1))
    told = [c for c in to(out, ST) if type(c).__name__ == "SendMessage"]
    check("'Sababsiz': '⛔ not approved.' with no reason line, the two buttons still there",
          len(told) == 1 and told[0].text.startswith("⛔ To'lovingiz tasdiqlanmadi.\n\n") and "Sabab:" not in told[0].text
          and buttons(told[0].reply_markup) == [f"pay_resend:{greq.id}", "support:write"]
          and (await get(PaymentRequest, greq.id)).reject_reason == "none", [c.text for c in told])
    await press(ST, f"pay_resend:{greq.id}")
    g2 = await latest(ST)
    check("a gift sent again stays a gift", g2.id != greq.id and g2.kind == PaymentKind.gift and g2.coins == 10)
    await press(ST, f"pay_cancel:{g2.id}")

    rreq, rcards = await receipt_photo(RU, "ux-photo-r", "UX-PR")
    out = await press(333, f"pay_rjr:{rreq.id}:no_money", card(333, rcards[333]))
    told = [c for c in to(out, RU) if type(c).__name__ == "SendMessage"]
    check("in the student's own language (ru): 'Причина: деньги не поступили', the buttons in Russian",
          len(told) == 1 and "Причина: <b>деньги не поступили</b>" in told[0].text
          and button_texts(told[0].reply_markup) == ["📸 Отправить чек заново", "💬 Связь с админом"],
          [c.text for c in told])

    # an approval: every copy cleared too, the student told as before - since 2026-11-16 with "✍️ Writing" /
    # "🎙 Speaking" under it when no check waits for the coins (tests/growth_test.py)
    from bot.keyboards.entry_kb import CB_OPEN_SPEAKING, CB_OPEN_WRITING

    areq, acards = await receipt_photo(ST, "ux-photo-a", "UX-PA")
    before = (await get(DbUser, ST)).coin_balance
    out = await press(333, f"pay_approve:{areq.id}", card(333, acards[333]))
    caps = {c.chat_id: c.caption for c in of(out, "EditMessageCaption")}
    told = [c for c in to(out, ST) if type(c).__name__ == "SendMessage"]
    check("✅: coins added, every copy '✅ Tasdiqlandi — Admin333', the student told",
          (await get(DbUser, ST)).coin_balance == before + 10 and set(caps) == set(ADMINS)
          and all("✅ Tasdiqlandi — Admin333" in c for c in caps.values())
          and len(told) == 1 and "tasdiqlandi" in told[0].text
          and buttons(told[0].reply_markup) == [CB_OPEN_WRITING, CB_OPEN_SPEAKING], (caps, told))
    async with get_session() as s:
        decision = crud.PaymentDecision(await s.get(PaymentRequest, areq.id), True)
    check("approval_keyboard: Writing/Speaking under a coins approval with nothing parked (built in one place)",
          buttons(await pay.approval_keyboard(decision, "uz")) == [CB_OPEN_WRITING, CB_OPEN_SPEAKING])

    # a card sent before the ids were kept (no Redis entry): the tapped copy is still cleared
    oreq, ocards = await receipt_photo(ST, "ux-photo-o", "UX-PO")
    await redis().delete(f"pay:review:{oreq.id}")
    out = await press(111, f"pay_rjr:{oreq.id}:unreadable", card(111, ocards[111]))
    caps = of(out, "EditMessageCaption")
    check("an old card (no ids kept): the tapped copy still gets the decision", len(caps) == 1
          and caps[0].chat_id == 111 and "Sabab: Chek o&#x27;qilmaydi" not in caps[0].caption
          and "Sabab: Chek o'qilmaydi" in caps[0].caption, [c.caption for c in caps])

    # the admins' group card: the reason, the bot admin's name
    async with get_session() as s:
        r = await s.get(PaymentRequest, req1.id)
        card_text = await crm_events._payment_card(s, r)
    check("group card: '⛔ Rad etdi: <admin>' and '💬 Sabab: Summa mos emas'",
          "⛔ Rad etdi: Admin111" in card_text and "💬 Sabab: Summa mos emas" in card_text, card_text)

    # --- C2 in the panel --------------------------------------------------------------------------------------
    async def fake_with_bot(send):
        try:
            await send(bot)
            return True
        except Exception as e:  # noqa: BLE001
            print("   (fake bot call raised)", repr(e))
            return False

    wm._with_bot = fake_with_bot
    owner = settings.web_admin_username
    transport = httpx.ASGITransport(app=wm.app)
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as c:
        await c.post("/login", data={"username": owner, "password": settings.web_admin_password})
        preq, pcards = await receipt_photo(ST, "ux-photo-p", "UX-PP")
        page = (await c.get("/payments?status=pending")).text
        check("panel, pending: the reject form has the reasons to choose from",
              f'action="/payments/{preq.id}/reject"' in page and 'name="reason"' in page
              and all(f'value="{k}"' in page for k in REJECT_REASONS) and "Sababni tanlang" in page)
        start = len(session.calls)
        r = await c.post(f"/payments/{preq.id}/reject", data={"reason": "unreadable"}, follow_redirects=False)
        out = session.calls[start:]
        p = await get(PaymentRequest, preq.id)
        check("panel reject with a reason: rejected, stored", r.status_code == 303 and p.status == PaymentStatus.rejected
              and p.reject_reason == "unreadable" and p.reviewed_by == wm.WEB_REVIEWER_ID, (r.status_code, p.reject_reason))
        caps = {x.chat_id: x for x in of(out, "EditMessageCaption")}
        check("...every admin's copy in the bot cleared, naming the panel login and the reason",
              set(caps) == set(ADMINS) and all(caps[a].message_id == pcards[a] and caps[a].reply_markup is None
                                               and f"⛔ Rad etildi — {owner} (web panel) · Sabab: Chek o'qilmaydi"
                                               in caps[a].caption for a in ADMINS),
              {a: x.caption for a, x in caps.items()})
        told = [x for x in to(out, ST) if type(x).__name__ == "SendMessage"]
        check("...the student: the reason and both buttons", len(told) == 1
              and "Sabab: <b>chek o'qilmaydi</b>" in told[0].text
              and buttons(told[0].reply_markup) == [f"pay_resend:{preq.id}", "support:write"], [x.text for x in told])
        page = (await c.get("/payments?status=rejected")).text
        check("...the payments page shows the reason", "Sabab: Chek o&#39;qilmaydi" in page
              or "Sabab: Chek o'qilmaydi" in page)
        async with get_session() as s:
            logged = (await s.execute(select(AdminAction.details).where(AdminAction.action == "payment_reject")
                                      .order_by(AdminAction.id.desc()).limit(1))).scalar_one()
            card_text = await crm_events._payment_card(s, await s.get(PaymentRequest, preq.id), panel_admin=owner)
        check("...the audit log has the reason", logged == f"#{preq.id} · Chek o'qilmaydi", logged)
        check("...the group card names the panel login, not a bare 'web panel'",
              f"⛔ Rad etdi: {owner} (web panel)" in card_text and "💬 Sabab: Chek o'qilmaydi" in card_text, card_text)
        queued = [json.loads(x) for x in await redis().lrange(crm.QUEUE, 0, -1)]
        edit = [j for j in queued if j.get("op") == "edit" and j.get("key") == f"pay:{preq.id}"]
        check("...and the card edit queued for the group says so", edit and f"{owner} (web panel)" in edit[-1]["text"]
              and "Sabab: Chek o'qilmaydi" in edit[-1]["text"], edit)

        oldreq, _ = await receipt_photo(ST, "ux-photo-q", "UX-PQ")
        start = len(session.calls)
        await c.post(f"/payments/{oldreq.id}/reject", follow_redirects=False)  # a page from before the reasons
        told = [x for x in to(session.calls[start:], ST) if type(x).__name__ == "SendMessage"]
        check("panel reject with no reason (an older page): 'Sababsiz', no reason line",
              (await get(PaymentRequest, oldreq.id)).reject_reason == "none" and len(told) == 1
              and "Sabab:" not in told[0].text, [x.text for x in told])
        start = len(session.calls)
        await c.post(f"/payments/{oldreq.id}/approve", follow_redirects=False)
        check("...a second decision on it does nothing", (await get(PaymentRequest, oldreq.id)).status
              == PaymentStatus.rejected and not to(session.calls[start:], ST))

        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as stranger:
            r = await stranger.post(f"/payments/{req2.id}/reject", data={"reason": "amount"}, follow_redirects=False)
            check("not signed in: the panel decides nothing", r.status_code in (302, 303)
                  and (await get(PaymentRequest, req2.id)).status == PaymentStatus.pending)

        # --- C5: receipts sent as a file ------------------------------------------------------------------------
        await press(ST, "buy:10")
        doc_req = await latest(ST)
        mark = len(session.sent)
        out, doc_msg = await send(ST, document=Document(file_id="ux-doc-png", file_unique_id="UX-D1", file_name="chek.png",
                                                        mime_type="image/png", file_size=2_000_000))
        docs = [m for m, _ in session.sent[mark:] if type(m).__name__ == "SendDocument"]
        p = await get(PaymentRequest, doc_req.id)
        check("an image file: accepted, kept like a photo (file id, unique id)", p.receipt_file_id == "ux-doc-png"
              and p.receipt_unique_id == "UX-D1" and any("Chek qabul qilindi" in x for x in texts(out)), texts(out))
        check("...to the admins with send_document, the same caption and buttons",
              {m.chat_id for m in docs} == set(ADMINS) and all(m.document == "ux-doc-png"
                                                            and "Yangi to'lov so'rovi" in m.caption
                                                            and buttons(m.reply_markup) == [f"pay_approve:{doc_req.id}",
                                                                                            f"pay_reject:{doc_req.id}"]
                                                            for m in docs)
              and not [m for m, _ in session.sent[mark:] if type(m).__name__ == "SendPhoto"], docs)
        queued = [json.loads(x) for x in await redis().lrange(crm.QUEUE, 0, -1)]
        copies = [j for j in queued if j.get("op") == "copy" and j.get("copy_from") == [ST, doc_msg]]
        cards_q = [j for j in queued if j.get("key") == f"pay:{doc_req.id}" and j.get("op") == "send"]
        check("...the group: the file copied into 💳 To'lovlar, the card after it as text (no photo)",
              len(copies) == 1 and copies[0]["topic"] == "payments" and len(cards_q) == 1
              and "photo" not in cards_q[0] and queued.index(copies[0]) < queued.index(cards_q[0]), (copies, cards_q))

        await press(ST, "buy:10")
        pdf_req = await latest(ST)
        mark = len(session.sent)
        out, _ = await send(ST, document=Document(file_id="ux-doc-pdf", file_unique_id="UX-D2", file_name="chek.pdf",
                                                  mime_type="application/pdf", file_size=300_000))
        check("a PDF: accepted and sent to the admins as a file", (await get(PaymentRequest, pdf_req.id))
              .receipt_file_id == "ux-doc-pdf" and len([m for m, _ in session.sent[mark:]
                                                        if type(m).__name__ == "SendDocument"]) == 3)

        await press(ST, "buy:25")
        mark = len(session.sent)
        await send(ST, document=Document(file_id="ux-doc-png-2", file_unique_id="UX-D1", file_name="chek (1).png",
                                         mime_type="image/png", file_size=2_000_000))
        captions = [m.caption for m, _ in session.sent[mark:] if type(m).__name__ == "SendDocument"]
        check("the same file sent for a second payment: flagged 'used before' on the admins' card",
              captions and all("avval ishlatilgan" in x and f"#{doc_req.id}" in x for x in captions), captions)

        await press(ST, "buy:10")
        noname_req = await latest(ST)
        await send(ST, document=Document(file_id="ux-doc-nomime", file_unique_id="UX-D3", file_name="Chek.PDF"))
        check("a file with no type but a .pdf name: accepted", (await get(PaymentRequest, noname_req.id))
              .receipt_file_id == "ux-doc-nomime")

        await press(ST, "buy:10")
        big_req = await latest(ST)
        mark = len(session.sent)
        out, _ = await send(ST, document=Document(file_id="ux-doc-big", file_unique_id="UX-D4", file_name="big.pdf",
                                                  mime_type="application/pdf", file_size=11 * 1024 * 1024))
        check("a file over 10 MB: refused with its own message, still waiting for a receipt",
              any("juda katta" in x and "10 MB" in x for x in texts(out))
              and (await get(PaymentRequest, big_req.id)).receipt_file_id is None
              and await st.get_state() == PaymentStates.awaiting_receipt.state
              and not [m for m, _ in session.sent[mark:] if type(m).__name__ == "SendDocument"], texts(out))
        out, _ = await send(ST, document=Document(file_id="ux-doc-zip", file_unique_id="UX-D5", file_name="x.zip",
                                                  mime_type="application/zip", file_size=1000))
        check("another kind of file: 'a photo or a PDF', still waiting", any("rasm (skrinshot) yoki PDF" in x
                                                                            for x in texts(out))
              and await st.get_state() == PaymentStates.awaiting_receipt.state, texts(out))
        out, _ = await send(ST, text="salom")
        check("text: 'a photo or a PDF'", any("rasm (skrinshot) yoki PDF" in x for x in texts(out)), texts(out))
        out, _ = await send(ST, text="/help")
        check("/help at the receipt step: the help, not 'send a photo'; still waiting",
              any("Bandzilla — yordam" in x for x in texts(out)) and not any("skrinshot" in x for x in texts(out))
              and await st.get_state() == PaymentStates.awaiting_receipt.state, texts(out))
        await press(ST, f"pay_cancel:{big_req.id}")

        # the panel serves each receipt as what it is
        files = {"ux-doc-pdf": PDF, "ux-doc-png": PNG, "ux-photo-2": JPEG, "ux-doc-nomime": b"\x00\x01junk"}

        async def fake_download(file_id):
            return files.get(file_id)

        wm._download_receipt = fake_download
        wm._receipts.clear()
        r = await c.get(f"/payments/{pdf_req.id}/receipt")
        check("panel: a PDF receipt served as application/pdf, inline", r.status_code == 200
              and r.headers["content-type"] == "application/pdf" and r.content == PDF
              and r.headers["content-disposition"].startswith("inline")
              and r.headers.get("x-content-type-options") == "nosniff", dict(r.headers))
        r = await c.get(f"/payments/{doc_req.id}/receipt")
        check("...an image file as image/png", r.status_code == 200 and r.headers["content-type"] == "image/png")
        r = await c.get(f"/payments/{req2.id}/receipt")
        check("...a photo as image/jpeg, as before", r.status_code == 200 and r.headers["content-type"] == "image/jpeg")
        r = await c.get(f"/payments/{pdf_req.id}/receipt?info=1")
        check("...?info=1: its type, for the page to link a PDF", r.status_code == 200
              and r.json() == {"type": "application/pdf"}, r.text)
        r = await c.get(f"/payments/{noname_req.id}/receipt")
        check("...bytes of no known kind: only downloaded, never shown", r.status_code == 200
              and r.headers["content-type"] == "application/octet-stream"
              and r.headers["content-disposition"].startswith("attachment"))
        r = await c.get(f"/payments/{big_req.id}/receipt")
        check("...no receipt: 404", r.status_code == 404)
        page = (await c.get("/payments")).text
        check("...the payments page turns a receipt it can't show as a picture into a link",
              "receiptNotPicture" in page and "?info=1" in page and "📄 PDF" in page)
        async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as stranger:
            r = await stranger.get(f"/payments/{pdf_req.id}/receipt", follow_redirects=False)
            check("...not signed in: no receipt", r.status_code in (302, 303) and r.content != PDF)

    # --- C4: /support and the help texts -------------------------------------------------------------------
    await st.clear()
    out, _ = await send(ST, text="/support")
    check("/support: the support chat opens (as 💬 Admin bilan aloqa does)",
          await st.get_state() == SupportStates.writing.state and any("Savolingiz" in x for x in texts(out)), texts(out))
    await st.clear()
    await press(ST, "buy:10")
    out, _ = await send(ST, text="/support")
    check("/support at the receipt step: the support chat, not 'send a photo'",
          await st.get_state() == SupportStates.writing.state and not any("skrinshot" in x for x in texts(out)),
          texts(out))
    await st.clear()
    out = await press(ST, "support:write")
    check("a rejection's 💬 button: the support chat", await st.get_state() == SupportStates.writing.state)
    await st.clear()
    out, _ = await send(ST, text="/help")
    help_uz = next((x for x in texts(out) if "yordam" in x), "")
    check("/help: points to 👤 Hisobim → 💬 Admin bilan aloqa (or /support)",
          "«👤 Hisobim» → «💬 Admin bilan aloqa» (yoki /support)" in help_uz, help_uz)
    for lang, account in (("uz", "👤 Hisobim"), ("ru", "👤 Профиль"), ("en", "👤 My account")):
        text = t("help.text", lang, coin_price=1000)
        check(f"help.text [{lang}]: names {account} and /support", account in text and "/support" in text)
        rejected = t("group.teacher_rejected", lang)
        check(f"group.teacher_rejected [{lang}]: says where to write", account in rejected and "/support" in rejected)
    check("pay.rejected_hint points at the buttons under it",
          all("📸" in t("pay.rejected_hint", lang) for lang in ("uz", "ru", "en")))

    # --- B10: a stale balance is never written back ------------------------------------------------------
    async with get_session() as s:
        g = Group(name="UX", teacher_id=GT)
        s.add(g)
        await s.flush()
        s.add(GroupMembership(group_id=g.id, user_id=GS))
        await s.commit()
        gid = g.id
    async with get_session() as a:
        group = await a.get(Group, gid)
        members = await crud.get_group_members(a, gid)  # "gift to all" loads the students first (10 coins)
        async with get_session() as b:  # a check's charge commits meanwhile
            await b.execute(update(DbUser).where(DbUser.id == GS).values(coin_balance=DbUser.coin_balance - 5))
            await b.commit()
        credited = await crud.gift_coins(a, group=group, student_ids=[u.id for u, _ in members], amount=3)
    gs, gt = await get(DbUser, GS), await get(DbUser, GT)
    async with get_session() as s:
        note = (await s.execute(select(CoinAdjustment).where(CoinAdjustment.user_id == GS)
                                .order_by(CoinAdjustment.id.desc()))).scalars().first()
    check("gift to all after a check's charge: both kept (10 - 5 + 3 = 8), the payer 100 - 3",
          credited and gs.coin_balance == 8 and gt.coin_balance == 97 and note.balance_after == 8,
          (gs.coin_balance, gt.coin_balance, note.balance_after if note else None))

    async with get_session() as a:
        await crud.get_user(a, GS)  # this session holds the student at 8
        async with get_session() as b:  # another check's charge commits meanwhile
            await b.execute(update(DbUser).where(DbUser.id == GS).values(coin_balance=DbUser.coin_balance - 2))
            await b.commit()
        adj = await crud.adjust_coins(a, GS, 4, "ux test")
    check("a coin change by hand after a charge: both kept (8 - 2 + 4 = 10)", adj is not None
          and adj.balance_after == 10 and (await get(DbUser, GS)).coin_balance == 10,
          (adj.balance_after if adj else None, (await get(DbUser, GS)).coin_balance))

    async with get_session() as s:
        s.add(PaymentRequest(user_id=GS, coins=10, amount=10_000, status=PaymentStatus.pending))
        await s.commit()
    preq_gs = await latest(GS)
    async with get_session() as a:
        await crud.get_user(a, GS)  # 10
        await a.get(PaymentRequest, preq_gs.id)
        async with get_session() as b:
            await b.execute(update(DbUser).where(DbUser.id == GS).values(coin_balance=DbUser.coin_balance - 5))
            await b.commit()
        d = await crud.decide_payment_request(a, preq_gs.id, approve=True, admin_id=111)
    check("an approval after a charge: both kept (10 - 5 + 10 = 15)", d.applied
          and (await get(DbUser, GS)).coin_balance == 15, (await get(DbUser, GS)).coin_balance)

    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
