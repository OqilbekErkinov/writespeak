"""2026-11-16, before the bot opens to everyone - the admins' side runs itself (bot/utils/admin_reminders.py,
crm_events.daily_report, balance.py): a receipt waiting is chased at 20 minutes, 2 hours, then every 12 hours,
each step once (D1); a pack tapped with no receipt for a day expires, and every "pending" count is "a receipt
waiting" (D1); a student's message unanswered for 2 hours is chased once a thread in 12 hours, an answer from
the group or the panel marks the thread read, the badge counts the threads waiting (D2); the daily report puts
problems first - stopped checks, the backups, the importer, support, payments, teachers' marks (D6); the
balance warnings reach the admins' private chats, once per threshold, the "out of credit" one pinned (D7).
Offline: a fake Bot API session, a throwaway Postgres + Redis (run_test.sh).
"""
import asyncio
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from bot.config import settings  # noqa: E402

settings.web_2fa = False  # the password alone signs in (the two-step sign-in: tests/panel_access_test.py)
settings.admin_telegram_ids = "111,222"  # 222 is linked to a panel login without 💳 To'lovlar
fails: list[str] = []

GROUP = -100555
TOPICS = {"payments": 5, "support": 6, "errors": 7, "daily": 8}
ST, ST2, ST3, ST4, ST5, AGENT = 9_600_001, 9_600_002, 9_600_003, 9_600_004, 9_600_005, 9_600_444
BOT_ID = 8888548766


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


posted: list[tuple[str, str, dict]] = []


async def fake_post(topic: str, text: str = "", **kwargs) -> None:
    posted.append((topic, text, kwargs))


def of_topic(topic: str) -> list[str]:
    return [text for t, text, _ in posted if t == topic]


async def main() -> int:
    from aiogram import Bot
    from aiogram.client.session.base import BaseSession
    from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
    from aiogram.types import CallbackQuery, Chat, Message, PhotoSize, Update, User
    from sqlalchemy import select, update

    import webapp.main as wm
    from bot.i18n import t
    from bot.main import build_dispatcher
    from bot.utils import admin_reminders as ar
    from bot.utils import balance, crm, crm_events, support_log
    from db import crud
    from db.database import get_session
    from db.models import (
        CheckHold, PanelSecondFactor, PaymentKind, PaymentRequest, PaymentStatus, SpeakingSubmission,
        SupportMessage, User as DbUser, UserStatus, WebAdmin, WritingSubmission,
    )
    from webapp import finance, insights, panel

    r = crm.redis()
    await r.flushdb()
    crm.post = fake_post

    async def rate() -> float:
        return 12500.0

    crm.usd_rate = rate  # no Central Bank call: $5 = 62 500 so'm, $3 = 37 500
    cfg_path = crm.config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps({"chat_id": GROUP, "topics": TOPICS, "backfill_at": "2026-11-16T00:00:00+00:00",
                                    "agents": ["helperagent", "333"]}), encoding="utf-8")
    check("setup: the admins' group counts as set up", crm.ready())
    data_dir = cfg_path.parent
    for name in ("backup_status.json", "offsite_status.json"):
        (data_dir / name).unlink(missing_ok=True)

    now = datetime.now(timezone.utc)
    async with get_session() as s:
        for uid, name, username in ((ST, "Ali Valiyev", "ali"), (ST2, "Bek", None), (ST3, "Gul", None),
                                    (ST4, "Dilshod", None), (ST5, "Eski", None), (AGENT, "Helper", "HelperAgent"),
                                    (111, "Admin111", None), (222, "Admin222", None)):
            s.add(DbUser(id=uid, full_name=name, username=username, coin_balance=2, language="uz",
                         status=UserStatus.approved))
        s.add(WebAdmin(username="helper", name="Helper", password_hash=panel.hash_password("pw"), perms="coins"))
        s.add(PanelSecondFactor(login="helper", telegram_id=222))
        await s.commit()

    class Tg(BaseSession):
        def __init__(self):
            super().__init__()
            self.calls, self.refuse, self.n = [], None, 70_000

        async def close(self):
            pass

        async def stream_content(self, *a, **k):
            yield b""

        async def make_request(self, bot, method, timeout=None):
            self.calls.append(method)
            err = self.refuse(method) if self.refuse else None
            if err is not None:
                raise err
            if type(method).__name__ in ("PinChatMessage", "UnpinChatMessage", "SetMessageReaction",
                                         "AnswerCallbackQuery", "EditMessageReplyMarkup", "SendChatAction"):
                return True
            self.n += 1
            return Message(message_id=self.n, date=now, chat=Chat(id=getattr(method, "chat_id", None) or 1,
                                                                  type="private"),
                           text=getattr(method, "text", None) or "x").as_(bot)

    tg = Tg()
    bot = Bot(f"{BOT_ID}:TEST", session=tg)

    def sent_from(mark: int, name: str = "SendMessage") -> list:
        return [c for c in tg.calls[mark:] if type(c).__name__ == name]

    def dms(mark: int) -> dict[int, list]:
        out: dict[int, list] = {}
        for c in sent_from(mark):
            out.setdefault(c.chat_id, []).append(c)
        return out

    # --- D1: the reminders of a receipt waiting --------------------------------------------------------------
    async with get_session() as s:
        p1 = PaymentRequest(user_id=ST, coins=10, amount=10_000, receipt_file_id="f1", receipt_unique_id="u1")
        s.add(p1)
        await s.commit()
        p1_id = p1.id
    await crm_events.payment_receipt(p1_id)  # what the receipt flow calls: when it came is kept
    check("a receipt came: its time is kept for the reminders", await r.get(ar.RECEIPT_AT.format(id=p1_id)))
    await r.set(f"pay:review:{p1_id}", json.dumps({"caption": "x", "msgs": [[111, 5555], [222, 6666]]}))
    received = datetime.fromtimestamp(int(await r.get(ar.RECEIPT_AT.format(id=p1_id))), timezone.utc)
    posted.clear()

    async def remind(minutes: float) -> tuple[int, list[str], dict[int, list]]:
        mark, start = len(tg.calls), len(posted)
        n = await ar.payment_reminders(bot, now=received + timedelta(minutes=minutes))
        return n, [text for topic, text, _ in posted[start:] if topic == "payments"], dms(mark)

    n, group, direct = await remind(10)
    check("10 minutes: nothing yet", n == 0 and not group and not direct, (n, group))
    n, group, direct = await remind(21)
    check("20 minutes: one reminder in 💳 To'lovlar - who, how much, how long, the panel link",
          n == 1 and len(group) == 1 and "⏰" in group[0] and f"#{p1_id}" in group[0] and "Ali Valiyev" in group[0]
          and "10 000 so'm" in group[0] and "21 daqiqa" in group[0]
          and f"{settings.panel_url}/payments?status=pending" in group[0], group)
    check("...by direct message to the admins allowed payments only (222's login lacks it)",
          list(direct) == [111] and direct[111][0].text == group[0], list(direct))
    reply = direct.get(111, [None])[0]
    check("...as a reply to that admin's own copy of the review card (its ✅/⛔ right there)",
          reply is not None and reply.reply_parameters is not None and reply.reply_parameters.message_id == 5555
          and reply.reply_parameters.allow_sending_without_reply, reply and reply.reply_parameters)
    check("...with a sound in the day, silent at night", reply is not None
          and reply.disable_notification == ar.quiet())
    real_now = crm.local_now
    crm.local_now = lambda: datetime(2026, 11, 16, 3, 0, tzinfo=crm.TZ)
    night = ar.quiet()
    crm.local_now = lambda: datetime(2026, 11, 16, 14, 0, tzinfo=crm.TZ)
    day = ar.quiet()
    crm.local_now = real_now
    check("...quiet hours: 03:00 yes, 14:00 no", night and not day)
    n, group, direct = await remind(30)
    check("...once: 30 minutes, nothing new", n == 0 and not group and not direct)
    n, group, direct = await remind(121)
    check("2 hours: the urgent one", n == 1 and len(group) == 1 and "🚨" in group[0] and "2 soatdan" in group[0]
          and "ko'rib chiqing" in group[0] and list(direct) == [111], group)
    n, group, _ = await remind(150)
    check("...once", n == 0 and not group)
    n, group, _ = await remind(14 * 60 + 1)
    check("14 hours (2 h + 12 h): again", n == 1 and len(group) == 1 and "hali ham" in group[0], group)
    n, group, _ = await remind(20 * 60)
    check("...not again before the next 12 hours", n == 0 and not group)
    n, group, _ = await remind(26 * 60 + 1)
    check("26 hours: again - every 12 hours while it waits", n == 1 and "1 kun 2 soat" in group[0], group)
    async with get_session() as s:
        await crud.decide_payment_request(s, p1_id, approve=True, admin_id=111)
    n, group, _ = await remind(38 * 60 + 1)
    check("decided: no more reminders", n == 0 and not group)

    async with get_session() as s:
        late = PaymentRequest(user_id=ST2, coins=25, amount=23_000, receipt_file_id="f2",
                              created_at=now - timedelta(hours=5))
        fresh = PaymentRequest(user_id=ST3, coins=10, amount=10_000, receipt_file_id="f3",
                               created_at=now - timedelta(minutes=25))
        bare = PaymentRequest(user_id=ST4, coins=10, amount=10_000, created_at=now - timedelta(hours=3))
        s.add_all([late, fresh, bare])
        await s.commit()
        late_id, fresh_id, bare_id = late.id, fresh.id, bare.id
    start = len(posted)
    n = await ar.payment_reminders(bot)
    texts = [text for topic, text, _ in posted[start:] if topic == "payments"]
    check("no receipt time kept (from before, Redis lost): counted from the request - 5 h ago gets ONE reminder, "
          "the 2-hour one, not the missed 20-minute one too", sum(f"#{late_id}" in x for x in texts) == 1
          and any(f"#{late_id}" in x and "🚨" in x for x in texts), texts)
    check("...25 minutes ago: the first one", any(f"#{fresh_id}" in x and "⏰" in x for x in texts), texts)
    check("...a pack tapped with no receipt is never chased", not any(f"#{bare_id}" in x for x in texts) and n == 2,
          (n, texts))

    # the one call line in crm.run_sender reaches the reminders
    await r.delete(ar.PAY_FLAG.format(id=fresh_id, step="20m"))
    mark = len(tg.calls)
    task = asyncio.create_task(crm.run_sender(bot))
    for _ in range(300):
        await asyncio.sleep(0.01)
        if any(f"#{fresh_id}" in (c.text or "") for c in sent_from(mark)):
            break
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    check("crm.run_sender's minute runs the reminders (a direct message went)",
          any(f"#{fresh_id}" in (c.text or "") and c.chat_id == 111 for c in sent_from(mark)))

    # --- D1: one "pending" everywhere -----------------------------------------------------------------------------
    async with get_session() as s:
        expired = PaymentRequest(user_id=ST5, coins=10, amount=10_000, created_at=now - timedelta(hours=25))
        s.add(expired)
        await s.commit()
        expired_id = expired.id
    async with get_session() as s:
        counted = await crud.count_pending_payments(s)
        stats = await crud.get_admin_stats(s)
        dash = await insights.dashboard(s)
        fin = await finance.overview(s, "all")
        listed = [p.id for p in await crud.list_payment_requests(s, status=PaymentStatus.pending)]
        everything = [p.id for p in await crud.list_payment_requests(s)]
    check("pending = a receipt waiting: 2 (the bot's /stats, the daily report's count)",
          counted == 2 and stats["pending_payments"] == 2, (counted, stats["pending_payments"]))
    check("...the dashboard and the finance page count the same 2 (they counted packs tapped)",
          dash["pending_count"] == 2 and fin["totals"]["n_pending"] == 2 and fin["totals"]["pending"] == 33_000,
          (dash["pending_count"], fin["totals"]["n_pending"]))
    check("the panel's Kutilmoqda list: the receipts and a fresh pack tapped, never one expired (24 h, no receipt)",
          set(listed) == {late_id, fresh_id, bare_id} and expired_id in everything, listed)
    async with get_session() as s:
        e = await s.get(PaymentRequest, expired_id)
        b = await s.get(PaymentRequest, bare_id)
        l_ = await s.get(PaymentRequest, late_id)
        check("payment_expired: 25 h with no receipt yes; 3 h no; a receipt never",
              crud.payment_expired(e) and not crud.payment_expired(b) and not crud.payment_expired(l_)
              and not crud.payment_expired(l_, now + timedelta(days=30)))
        got, outcome = await crud.attach_receipt(s, expired_id, "late-file", "late-u")
    async with get_session() as s:
        e = await s.get(PaymentRequest, expired_id)
    check("...an expired request takes no receipt (nothing saved)", outcome == "expired" and got is not None
          and e.receipt_file_id is None, (outcome, e.receipt_file_id))
    async with get_session() as s:
        none = await crud.set_payment_receipt(s, expired_id, "late-file")
    check("...set_payment_receipt says None for it", none is None)

    # the student's own receipt flow (the real handlers)
    dp = build_dispatcher()
    counter = iter(range(800_000, 900_000))

    def person(uid):
        return User(id=uid, is_bot=False, first_name=f"U{uid}")

    async def send(uid, **kw):
        mark = len(tg.calls)
        message = Message(message_id=next(counter), date=now, chat=Chat(id=uid, type="private"), from_user=person(uid),
                          **kw)
        await dp.feed_update(bot, Update(update_id=next(counter), message=message))
        return tg.calls[mark:]

    async def press(uid, data):
        mark = len(tg.calls)
        message = Message(message_id=next(counter), date=now, chat=Chat(id=uid, type="private"), text="x")
        query = CallbackQuery(id=str(next(counter)), from_user=person(uid), chat_instance="ci", data=data,
                              message=message)
        await dp.feed_update(bot, Update(update_id=next(counter), callback_query=query))
        return tg.calls[mark:]

    async def latest(uid) -> PaymentRequest:
        async with get_session() as s:
            return (await s.execute(select(PaymentRequest).where(PaymentRequest.user_id == uid)
                                    .order_by(PaymentRequest.id.desc()))).scalars().first()

    def photo(n):
        return [PhotoSize(file_id=f"ph{n}", file_unique_id=f"phu{n}", width=10, height=10)]

    await press(ST5, "buy:10")
    req = await latest(ST5)
    async with get_session() as s:
        await s.execute(update(PaymentRequest).where(PaymentRequest.id == req.id)
                        .values(created_at=now - timedelta(hours=25)))
        await s.commit()
    out = await send(ST5, photo=photo(1))
    to_student = [c for c in out if type(c).__name__ == "SendMessage" and c.chat_id == ST5]
    to_admins = [c for c in out if type(c).__name__ in ("SendPhoto", "SendDocument")]
    markup = to_student[0].reply_markup if to_student else None
    datas = [b.callback_data for row in (getattr(markup, "inline_keyboard", None) or []) for b in row]
    check("a receipt 25 h after tapping the pack: told it expired, with the packs again",
          len(to_student) == 1 and to_student[0].text == t("pay.request_expired", "uz")
          and any(d.startswith("buy:") for d in datas), [c.text for c in to_student])
    check("...the receipt is not taken, no admin gets a card", (await latest(ST5)).receipt_file_id is None
          and not to_admins and (await latest(ST5)).id == req.id)
    await press(ST5, "buy:10")
    out = await send(ST5, photo=photo(2))
    fresh_req = await latest(ST5)
    check("...the pack chosen again and the receipt sent: accepted as always",
          fresh_req.id != req.id and fresh_req.receipt_file_id == "ph2"
          and any(type(c).__name__ == "SendPhoto" and c.chat_id == 111 for c in out))
    await press(ST5, "giftbuy:10")
    gift = await latest(ST5)
    async with get_session() as s:
        await s.execute(update(PaymentRequest).where(PaymentRequest.id == gift.id)
                        .values(created_at=now - timedelta(hours=30)))
        await s.commit()
    out = await send(ST5, photo=photo(3))
    msg = [c for c in out if type(c).__name__ == "SendMessage" and c.chat_id == ST5]
    datas = [b.callback_data for row in (getattr(msg[0].reply_markup, "inline_keyboard", None) or []) for b in row] \
        if msg else []
    check("...a gift's expired request: the gift packs again", gift.kind == PaymentKind.gift and msg
          and "⌛" in msg[0].text and any(d.startswith("giftbuy:") for d in datas), datas)
    for lang in ("ru", "en"):
        check(f"the expired text exists in {lang}", t("pay.request_expired", lang) not in ("pay.request_expired",
                                                                                         t("pay.request_expired", "uz")))

    # the nav badge and the dashboard through the panel
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=wm.app), base_url="http://panel") as owner:
        await owner.post("/login", data={"username": settings.web_admin_username,
                                         "password": settings.web_admin_password})
        page = (await owner.get("/")).text
        async with get_session() as s:
            should = await crud.count_pending_payments(s)
        badge = re.search(r'title="to\'lov tasdiq kutmoqda">💳 (\d+)<', page)
        check("the nav badge and the dashboard say the same number of receipts waiting",
              badge is not None and int(badge.group(1)) == should and f"{should} ta to'lov tasdiq kutmoqda" in page,
              (badge and badge.group(1), should))

        # --- D2: support threads waiting ------------------------------------------------------------------------
        posted.clear()

        async def say(uid, text, hours_ago, direction="in", kind="text"):
            await support_log.save(uid, direction, kind=kind, text=text, via="bot" if direction == "in" else "web",
                                   admin=None if direction == "in" else "admin")
            async with get_session() as s:
                last = (await s.execute(select(SupportMessage).where(SupportMessage.user_id == uid)
                                        .order_by(SupportMessage.id.desc()))).scalars().first()
                last.created_at = now - timedelta(hours=hours_ago)
                await s.commit()

        await say(ST, "Coin qachon tushadi?", 3.0)
        await say(ST, "Iltimos javob bering", 2.5)
        await say(ST2, "Salom", 5.0)
        await say(ST2, "Javob", 4.0, direction="out")  # answered
        await say(ST3, "Yangi savol", 0.5)  # 30 minutes: not yet
        await say(ST4, "Eski savol", 24 * 8)  # 8 days of silence: no more reminders
        mark = len(tg.calls)
        n = await ar.support_reminders(bot)
        group = of_topic("support")
        check("2 hours unanswered: one reminder in 💬 Murojaatlar, the thread's student behind it (a reply goes "
              "to them)", n == 1 and len(group) == 1 and "Ali Valiyev" in group[0] and "3 soatdan" in group[0]
              and "Iltimos javob bering" in group[0] and "jami 2 ta" in group[0]
              and posted[-1][2].get("support_user") == ST, group)
        check("...an answered thread, a 30-minute one and one silent for 8 days: not reminded",
              not any(x in group[0] for x in ("Bek", "Gul", "Dilshod")))
        told = dms(mark)
        check("...and by direct message to the agents: the bot admins and /crm_agents (by id and by @username)",
              set(told) == {111, 222, 333, AGENT} and "Ali Valiyev" in told[111][0].text
              and f"{settings.panel_url}/support/{ST}" in told[111][0].text, sorted(told))
        ttl = await r.ttl(ar.SUPPORT_FLAG.format(user_id=ST))
        n = await ar.support_reminders(bot)
        check("...once a thread in 12 hours", n == 0 and len(of_topic("support")) == 1 and 11 * 3600 < ttl <= 12 * 3600,
              (n, ttl))
        await r.delete(ar.SUPPORT_FLAG.format(user_id=ST))  # 12 hours later
        n = await ar.support_reminders(bot, now=now + timedelta(hours=12))
        again = [x for x in of_topic("support")[1:] if "Ali Valiyev" in x]
        check("...12 hours on, still unanswered: again ('hali ham'); the 30-minute one now due its first",
              n == 2 and len(again) == 1 and "hali ham" in again[0]
              and any("Gul" in x and "javobsiz qoldi" in x for x in of_topic("support")[1:]), of_topic("support")[1:])

        async with get_session() as s:
            waiting = {w.user_id: w for w in await crud.support_waiting(s)}
        check("waiting threads: the student's messages since the last answer", set(waiting) == {ST, ST3, ST4}
              and waiting[ST].messages == 2 and ST2 not in waiting, sorted(waiting))

        def support_badge(html: str) -> int:
            m = re.search(r'Murojaatlar<span class="nb" title="javob kutmoqda">(\d+)</span>', html)
            return int(m.group(1)) if m else 0

        page = (await owner.get("/support")).text
        check("the nav badge counts the threads waiting (3), not the unread messages (4)", support_badge(page) == 3,
              support_badge(page))
        await owner.get(f"/support/{ST3}")  # opened in the panel: read, but not answered
        async with get_session() as s:
            unread3 = (await s.execute(select(SupportMessage).where(SupportMessage.user_id == ST3,
                                                                    SupportMessage.read.is_(False)))).scalars().all()
        page = (await owner.get("/support")).text
        check("...a thread opened but never answered stays in the badge", not unread3 and support_badge(page) == 3,
              support_badge(page))

        # an answer from the group (the real handler: a reply in 💬 Murojaatlar)
        await r.set(f"crm:support:{GROUP}:4040", str(ST))
        replied = Message(message_id=4040, date=now, chat=Chat(id=GROUP, type="supergroup"),
                          from_user=User(id=BOT_ID, is_bot=True, first_name="Bandzilla"), text="💬 Murojaat",
                          message_thread_id=TOPICS["support"])
        answer = Message(message_id=4041, date=now, chat=Chat(id=GROUP, type="supergroup", is_forum=True),
                         from_user=User(id=111, is_bot=False, first_name="Admin"), text="Coinlar tushdi 👍",
                         message_thread_id=TOPICS["support"], is_topic_message=True, reply_to_message=replied)
        mark = len(tg.calls)
        await dp.feed_update(bot, Update(update_id=next(counter), message=answer))
        async with get_session() as s:
            unread = (await s.execute(select(SupportMessage).where(SupportMessage.user_id == ST,
                                                                   SupportMessage.read.is_(False)))).scalars().all()
            waiting = {w.user_id for w in await crud.support_waiting(s)}
        check("an answer from the group: delivered, the student's messages read, the thread no longer waiting",
              any(c.chat_id == ST for c in sent_from(mark)) and not unread and ST not in waiting, (len(unread), waiting))

        await say(ST4, "Yana savol", 1)
        await r.set(f"crm:support:{GROUP}:4050", str(ST4))
        replied4 = replied.model_copy(update={"message_id": 4050})
        answer4 = answer.model_copy(update={"message_id": 4051, "reply_to_message": replied4})

        def blocked(method):
            if getattr(method, "chat_id", None) == ST4:
                return TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
            return None

        tg.refuse = blocked
        mark = len(tg.calls)
        await dp.feed_update(bot, Update(update_id=next(counter), message=answer4))
        tg.refuse = None
        async with get_session() as s:
            unread = (await s.execute(select(SupportMessage).where(SupportMessage.user_id == ST4,
                                                                   SupportMessage.read.is_(False)))).scalars().all()
            waiting = {w.user_id for w in await crud.support_waiting(s)}
        check("...a group answer that could not be delivered (blocked): read, but still waiting",
              not unread and ST4 in waiting
              and any("bloklagan" in (c.text or "") for c in sent_from(mark) if c.chat_id == GROUP), waiting)

        # an answer from the panel
        await say(ST2, "Yana bir savol", 1)

        async def delivered(send):
            return True

        async def refused(send):
            return False

        wm._with_bot = delivered
        r1 = await owner.post(f"/users/{ST2}/message", data={"text": "Javobimiz", "back": "support"},
                              follow_redirects=False)
        async with get_session() as s:
            unread = (await s.execute(select(SupportMessage).where(SupportMessage.user_id == ST2,
                                                                   SupportMessage.read.is_(False)))).scalars().all()
            waiting = {w.user_id for w in await crud.support_waiting(s)}
        check("an answer from the panel: the student's messages read, the thread no longer waiting",
              r1.status_code == 303 and not unread and ST2 not in waiting, (r1.status_code, len(unread), waiting))
        await say(ST2, "Va yana", 1)
        wm._with_bot = refused
        await owner.post(f"/users/{ST2}/message", data={"text": "Javob", "back": ""}, follow_redirects=False)
        async with get_session() as s:
            unread = (await s.execute(select(SupportMessage).where(SupportMessage.user_id == ST2,
                                                                   SupportMessage.read.is_(False)))).scalars().all()
            waiting = {w.user_id for w in await crud.support_waiting(s)}
        check("...from the profile page, not delivered: read, still waiting", not unread and ST2 in waiting, waiting)

        # «✓ Javob shart emas»
        thread = (await owner.get(f"/support/{ST3}")).text
        check("a waiting thread offers «✓ Javob shart emas»", f'action="/support/{ST3}/close"' in thread)
        mark = len(tg.calls)
        rc = await owner.post(f"/support/{ST3}/close", follow_redirects=False)
        await owner.post(f"/support/{ST3}/close", follow_redirects=False)  # a double click
        async with get_session() as s:
            waiting = {w.user_id for w in await crud.support_waiting(s)}
            notes = (await s.execute(select(SupportMessage).where(SupportMessage.user_id == ST3,
                                                                  SupportMessage.kind == "closed"))).scalars().all()
        thread = (await owner.get(f"/support/{ST3}?sent=closed")).text
        check("...closed: no longer waiting, one note in the thread, nothing sent to the student",
              rc.status_code == 303 and ST3 not in waiting and len(notes) == 1 and not sent_from(mark)
              and "Javob shart emas deb yopildi" in thread and f'action="/support/{ST3}/close"' not in thread,
              (waiting, len(notes)))
        page = (await owner.get("/support")).text
        async with get_session() as s:
            should = len(await crud.support_waiting(s))
        check("the badge follows: the threads still waiting", support_badge(page) == should == 2,
              (support_badge(page), should))

    # --- D6: the daily report --------------------------------------------------------------------------------------
    today = crm.local_now().date()

    async def report() -> str:
        return await crm_events.daily_report(today, partial=True)

    def hold(i, state, reason, minutes_ago=30):
        at = now - timedelta(minutes=minutes_ago)
        return CheckHold(id=f"h{i:031d}", user_id=ST, chat_id=ST, module="w", kind="writing_task2", payer_id=ST,
                         coins=5, state=state, reason=reason, created_at=at, finished_at=at)

    async with get_session() as s:
        s.add_all([hold(1, "refunded", "chart unreadable: ChartUnreadable: blurry")])
        await s.commit()
    text = await report()
    check("a day with only a chart asked again: an ℹ️ line, not a problem",
          "ℹ️ To'xtagan tekshiruvlar: <b>1 ta</b>" in text and "grafik o'qilmadi" in text
          and "⚠️ To'xtagan" not in text, text[:600])
    async with get_session() as s:
        s.add_all([hold(2, "refunded", "RuntimeError: boom"), hold(3, "refunded", "RuntimeError: other"),
                   hold(4, "interrupted", "the bot restarted"), hold(5, "charged", None),
                   hold(6, "refunded", "RuntimeError: two days ago", minutes_ago=60 * 50)])
        await s.commit()
    text = await report()
    stop_line = next((x for x in text.split("\n") if "To'xtagan" in x), "")
    check("checks stopped in the day, by reason: errors by type, a restart - a ⚠️ problem",
          stop_line.startswith("⚠️") and "<b>4 ta</b>" in stop_line and "<code>RuntimeError</code> ×2" in stop_line
          and "bot qayta ishga tushdi ×1" in stop_line and "grafik o'qilmadi" in stop_line, stop_line)

    lines = text.split("\n")
    first_body = next(i for i, x in enumerate(lines) if x.startswith("👥 Yangi foydalanuvchilar"))
    check("problems come first, before the day's figures",
          all(i < first_body for i, x in enumerate(lines) if x.startswith("⚠️")), lines[:12])

    pay_line = next((x for x in lines if "To'lov tasdiq kutmoqda" in x), "")
    check("the oldest payment waiting: how many, which, how long - a ⚠️ when over 20 minutes",
          pay_line.startswith("⚠️") and f"#{late_id}" in pay_line and "Bek" in pay_line and "23 000 so'm" in pay_line
          and "5 soat" in pay_line, pay_line)
    sup_line = next((x for x in lines if x.lstrip("⚠️💬 ").startswith("Murojaatlar:")), "")
    check("unanswered support threads: the count and the oldest (⚠️ over 2 hours)",
          sup_line.startswith("⚠️") and "<b>2 tasi</b>" in sup_line and "8 kun" in sup_line
          and "Dilshod" in sup_line, sup_line)
    check("no teachers' marks waiting: no line", "Ustoz baholari" not in text)
    async with get_session() as s:
        s.add(WritingSubmission(user_id=ST, task_type="task2", prompt_text="Q", prompt_source="text",
                                answer_text="A " * 30, answer_source="text", overall_band=6.5,
                                teacher_bands={"bands": {}, "overall": 6.0, "role": "teacher", "approved": False}))
        s.add(SpeakingSubmission(user_id=ST, part="part1", question_text="Q", overall_band=6.0, audio_file_path="/tmp/a",
                                 teacher_bands={"bands": {}, "overall": 6.0, "role": "teacher", "approved": True}))
        s.add(SpeakingSubmission(user_id=ST, part="part1", question_text="Q", overall_band=6.0, audio_file_path="/tmp/b",
                                 teacher_bands={"bands": {}, "overall": 6.0, "role": "admin", "approved": True}))
        await s.commit()
    text = await report()
    check("teachers' marks waiting for the owner's approval: counted (an approved one, an admin's not)",
          "Ustoz baholari tasdig'ingizni kutmoqda: <b>1 ta</b>" in text, text[:900])

    # the backups
    def write(name, state):
        (data_dir / name).write_text(json.dumps(state), encoding="utf-8")

    text = await report()
    check("the backups, missing: both flagged", "⚠️ 💾 Zaxira (serverda): holati haqida yozuv yo'q" in text
          and "⚠️ ☁️ Zaxira nusxasi (guruhga): holati haqida yozuv yo'q" in text, text)
    at = (now - timedelta(hours=20)).astimezone(timezone(timedelta(hours=1))).isoformat(timespec="seconds")
    write("backup_status.json", {"at": at, "ok": True, "ts": "20261116-0050", "db": "7.1M", "files": "160M",
                                 "error": ""})
    write("offsite_status.json", {"at": at, "ok": True, "error": "", "parts": 4, "bytes": 167772160, "full": True,
                                  "full_at": at})
    text = await report()
    tail = text.split("\n")[-6:]
    check("...fine: ✅ at the end, when and how big", any(x.startswith("✅ 💾 Zaxira (serverda):")
                                                          and "baza 7.1M, fayllar 160M" in x for x in tail)
          and any(x.startswith("✅ ☁️ Zaxira nusxasi (guruhga):") and "4 qism, 160 MB (to'liq)" in x for x in tail)
          and "⚠️ 💾" not in text and "⚠️ ☁️" not in text, tail)
    write("backup_status.json", {"at": at, "ok": False, "ts": "x", "db": "", "files": "", "error": "pg_dump: disk full"})
    old = (now - timedelta(hours=31)).isoformat(timespec="seconds")
    write("offsite_status.json", {"at": old, "ok": True, "error": "", "parts": 4, "bytes": 1, "full": False})
    text = await report()
    check("...failed: ⚠️ with its error", "⚠️ 💾 Zaxira (serverda) xato bilan tugadi" in text
          and "pg_dump: disk full" in text, text[:800])
    check("...stale (over 30 h): ⚠️ how long", "⚠️ ☁️ Zaxira nusxasi (guruhga) 31 soatdan beri ishlamagan" in text,
          text[:800])
    (data_dir / "backup_status.json").write_text("{broken", encoding="utf-8")
    text = await report()
    check("...an unreadable status file: flagged, the report still made",
          "⚠️ 💾 Zaxira (serverda): holat yozuvi o'qilmadi" in text)

    # the importer's last run (services/practice_import/runner.py)
    from services.practice_import import runner

    text = await report()
    check("importer, no run recorded: flagged", "⚠️ 📥 Practice import: holati haqida yozuv yo'q" in text)

    async def sync_ok():
        return {"added": 3, "updated": 1, "removed": 0, "unchanged": 40}

    async def posts_down(**kwargs):
        raise RuntimeError("channel unreachable (test)")

    async def posts_ok(**kwargs):
        return {"done": 2, "skipped": 5}

    runner.speaking.sync = sync_ok
    runner.writing.import_new_posts = posts_down
    await runner.run_and_record()
    state = json.loads(await r.get(crm_events.IMPORTER_STATUS_KEY))
    check("importer: a part failing - stored ok false, why, and what the other part did",
          state["ok"] is False and "writing: RuntimeError: channel unreachable" in state["error"]
          and state["speaking"]["added"] == 3 and state["writing"] == {} and state["at"], state)
    text = await report()
    check("...the report flags it with the error", "⚠️ 📥 Practice import xato bilan tugadi" in text
          and "channel unreachable" in text)
    runner.writing.import_new_posts = posts_ok
    await runner.run_and_record()
    state = json.loads(await r.get(crm_events.IMPORTER_STATUS_KEY))
    text = await report()
    check("...a good run: ok, and the report's ✅ line says what came", state["ok"] is True and not state["error"]
          and "✅ 📥 Practice import:" in text and "Writing yangi 2" in text and "Speaking yangi 3, yangilangan 1" in text,
          [x for x in text.split("\n") if "Practice" in x])
    state["at"] = (now - timedelta(hours=9)).isoformat(timespec="seconds")
    await r.set(crm_events.IMPORTER_STATUS_KEY, json.dumps(state))
    text = await report()
    check("...no run for 9 hours (over 8): flagged", "⚠️ 📥 Practice import 9 soatdan beri ishlamagan" in text)
    await r.set(balance.EMPTY_KEY, "16.11.2026 10:00")
    balance._paused.update(at=0.0)
    await runner.run_and_record()
    state = json.loads(await r.get(crm_events.IMPORTER_STATUS_KEY))
    text = await report()
    check("...skipped while the OpenAI credit is out: recorded, and said so", state.get("skipped") == "credit"
          and "Practice import to'xtab turibdi: OpenAI balansi tugagan" in text)
    await r.delete(balance.EMPTY_KEY)
    balance._paused.update(at=0.0)

    await r.set(f"crm:errors:{today.isoformat()}", 3)
    text = await report()
    check("the bot's errors of the day: a ⚠️ problem at the top", "⚠️ Xatolar: <b>3 ta</b>" in text
          and text.index("⚠️ Xatolar") < text.index("👥 Yangi"))
    await r.delete(f"crm:errors:{today.isoformat()}")
    text = await report()
    check("...none: a quiet ✅ line near the end", "✅ Xatolar: 0" in text and "⚠️ Xatolar" not in text)
    print("\n--- a sample report ---\n" + text + "\n---")

    # --- D7: the balance warnings straight to the admins ------------------------------------------------------------
    posted.clear()
    await balance.set_openai(125000)
    await balance.deliver_direct(bot)  # nothing paused: nothing to say
    mark = len(tg.calls)
    await balance.add_spent(5.1)  # 61 250 so'm left: under $5
    sent = await balance.deliver_direct(bot)
    told = dms(mark)
    check("$5 left: the errors topic and every bot admin's private chat",
          len(of_topic("errors")) == 1 and sent == 1 and set(told) == {111, 222}
          and "61 250 so'm" in told[111][0].text and told[111][0].parse_mode == "HTML", (sent, sorted(told)))
    mark = len(tg.calls)
    await balance.add_spent(0.1)
    for _ in range(3):  # three more minutes
        await balance.deliver_direct(bot)
    check("...once: no more messages minute after minute", not dms(mark) and len(of_topic("errors")) == 1)
    await balance.add_spent(2.0)
    await balance.deliver_direct(bot)
    await balance.deliver_direct(bot)
    told = dms(mark)
    check("$3 left: one more each", all(len(v) == 1 and "🟠" in v[0].text for v in told.values()) and len(told) == 2,
          {k: len(v) for k, v in told.items()})
    mark = len(tg.calls)
    await balance.add_spent(3.0)
    await balance.deliver_direct(bot)
    check("$0 (the bot's estimate): one more each", all(len(v) == 1 and "🔴" in v[0].text for v in dms(mark).values())
          and len(dms(mark)) == 2)

    mark = len(tg.calls)
    await balance.set_azure_hours(1.0)
    await balance.add_azure_seconds(1900)
    await balance.add_azure_seconds(10)
    await balance.deliver_direct(bot)
    await balance.deliver_direct(bot)
    told = dms(mark)
    check("Azure: half an hour left - one message each, however many flushes", len(told) == 2
          and all(len(v) == 1 and "Azure" in v[0].text for v in told.values()), {k: len(v) for k, v in told.items()})

    # OpenAI's own "out of credit": pinned in the errors topic, unpinned when it is back
    posted.clear()
    mark = len(tg.calls)
    await balance.out_of_credit()
    await balance.out_of_credit()  # many calls fail at once
    key = next((kw.get("key") for topic, _, kw in posted if topic == "errors"), None)
    await balance.deliver_direct(bot)
    check("out of credit: the errors topic once, every admin told directly", len(of_topic("errors")) == 1
          and key and len(dms(mark)) == 2 and all("🚨" in v[0].text for v in dms(mark).values()))
    check("...not pinned before the sender has posted it", not sent_from(mark, "PinChatMessage")
          and await r.get(balance.PIN_KEY) == key)
    await r.set(f"crm:msg:{key}", "4242|t")  # the sender posted it
    await balance.deliver_direct(bot)
    pins = sent_from(mark, "PinChatMessage")
    check("...pinned once it is posted", len(pins) == 1 and pins[0].chat_id == GROUP and pins[0].message_id == 4242
          and await r.get(balance.PINNED_KEY) == f"{GROUP}|4242", [(p.chat_id, p.message_id) for p in pins])
    await balance.deliver_direct(bot)
    check("...pinned once", len(sent_from(mark, "PinChatMessage")) == 1)
    mark = len(tg.calls)
    await balance.set_openai(300000)
    await balance.deliver_direct(bot)
    unpins = sent_from(mark, "UnpinChatMessage")
    check("the credit back: unpinned, the admins told it works again",
          len(unpins) == 1 and unpins[0].message_id == 4242 and all("yana ishlayapti" in v[0].text
                                                                    for v in dms(mark).values())
          and len(dms(mark)) == 2 and not await r.get(balance.PINNED_KEY))

    def no_pin(method):
        if type(method).__name__ == "PinChatMessage":
            return TelegramBadRequest(method=method, message="Bad Request: not enough rights to manage pinned messages")
        return None

    posted.clear()
    await balance.out_of_credit()
    key = next((kw.get("key") for topic, _, kw in posted if topic == "errors"), None)
    await r.set(f"crm:msg:{key}", "4343|t")
    tg.refuse = no_pin
    ok = True
    try:
        await balance.deliver_direct(bot)
    except Exception as e:  # noqa: BLE001
        ok = repr(e)
    tg.refuse = None
    check("...a bot that may not pin: no failure, no retry every minute", ok is True
          and not await r.get(balance.PIN_KEY) and not await r.get(balance.PINNED_KEY), ok)

    # one part failing never stops the others
    real = ar.payment_reminders

    async def broken(bot_, now=None):
        raise RuntimeError("db away (test)")

    ar.payment_reminders = broken
    await balance.tell_admins("🟡 test xabari")
    mark = len(tg.calls)
    await crm_events.reminders_due(bot)
    ar.payment_reminders = real
    check("the minute's job: the payments part failing, the balance messages still go, nothing raised",
          any("test xabari" in (c.text or "") for c in sent_from(mark)))

    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    await r.aclose()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
