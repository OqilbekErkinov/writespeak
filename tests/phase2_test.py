"""The 2026-11-15 audit's second package, offline - a THROWAWAY Postgres and Redis (tests run by test_part2.sh),
Telegram a fake: /start, /menu and /cancel leave a flow (never a check being graded); what no handler took gets
a "what now" answer; the receipt step can be cancelled; one receipt picture sent for two payments is flagged;
a sponsor pays its own group's homework first and another group's homework counts against its limit;
switching sponsorship on sets a daily limit; a center join switches the moved groups' sponsorship off; a group
gift is recorded and its button works once; a double tap on the onboarding flag welcomes once.
"""
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

fails: list[str] = []


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


U1, U2, TEACHER = 7_200_001, 7_200_002, 7_200_003


async def main() -> int:
    from aiogram import Bot
    from aiogram.client.session.base import BaseSession
    from aiogram.types import CallbackQuery, Chat, Message, PhotoSize, Update, User
    from sqlalchemy import select

    from bot.keyboards.lang_kb import CB_LANG_ONBOARD_PREFIX
    from bot.keyboards.payment_kb import CB_BUY_PREFIX, CB_PAY_CANCEL_PREFIX
    from bot.main import build_dispatcher
    from bot.states.writing_states import WritingStates
    from bot.utils.crm import redis
    from db import crud
    from db.database import get_session
    from db.models import (
        Assignment, AssignmentPart, Center, CoinAdjustment, Group, GroupMembership, PaymentRequest, User as DbUser,
        UserRole, UserStatus, WritingSubmission,
    )

    await redis().flushdb()
    async with get_session() as s:
        for uid, coins, role in ((U1, 30, UserRole.student), (U2, 10, UserRole.student), (TEACHER, 100, UserRole.teacher)):
            s.add(DbUser(id=uid, full_name=f"U{uid}", coin_balance=coins, language="uz", status=UserStatus.approved,
                         role=role))
        await s.commit()

    now = datetime.now(timezone.utc)

    class TgSession(BaseSession):
        def __init__(self):
            super().__init__()
            self.calls, self.n = [], 7000

        async def close(self):
            pass

        async def stream_content(self, *a, **k):
            yield b""

        async def make_request(self, bot, method, timeout=None):
            self.calls.append(method)
            self.n += 1
            if type(method).__name__ in ("AnswerCallbackQuery", "DeleteMessage", "EditMessageReplyMarkup",
                                         "SendChatAction", "SendSticker"):
                return True
            return Message(message_id=getattr(method, "message_id", None) or self.n, date=now,
                           chat=Chat(id=getattr(method, "chat_id", None) or U1, type="private"),
                           text=getattr(method, "text", None) or "x").as_(bot)

    session = TgSession()
    bot = Bot("8888548766:TEST", session=session)
    dp = build_dispatcher()
    counter = iter(range(500_000, 900_000))

    def ctx(uid):
        return dp.fsm.get_context(bot=bot, chat_id=uid, user_id=uid)

    async def send(uid, **kw):
        start = len(session.calls)
        message = Message(message_id=next(counter), date=now, chat=Chat(id=uid, type="private"),
                          from_user=User(id=uid, is_bot=False, first_name="S"), **kw)
        await dp.feed_update(bot, Update(update_id=next(counter), message=message))
        return session.calls[start:]

    async def press(uid, data, message_id=None):
        start = len(session.calls)
        query = CallbackQuery(id=str(next(counter)), from_user=User(id=uid, is_bot=False, first_name="S"),
                              chat_instance="ci", data=data,
                              message=Message(message_id=message_id or next(counter), date=now,
                                              chat=Chat(id=uid, type="private"), text="x"))
        await dp.feed_update(bot, Update(update_id=next(counter), callback_query=query))
        return session.calls[start:]

    def texts(calls):
        return [c.text for c in calls if type(c).__name__ in ("SendMessage", "EditMessageText")]

    def alerts(calls):
        return [c.text for c in calls if type(c).__name__ == "AnswerCallbackQuery" and c.text]

    # 1. /start, /menu, /cancel leave a flow - never a check being graded
    st = ctx(U1)
    await st.set_state(WritingStates.awaiting_answer)
    await st.update_data(task_type="task2")
    await send(U1, text="/start")
    check("/start: the flow left (no more 'send your answer')", await st.get_state() is None)
    await st.set_state(WritingStates.processing)
    await send(U1, text="/start")
    check("/start: a check being graded is kept", await st.get_state() == WritingStates.processing.state)
    out = await send(U1, text="/cancel")
    check("/cancel while graded: said it can't be stopped, kept", any("to'xtatib bo'lmaydi" in x for x in texts(out))
          and await st.get_state() == WritingStates.processing.state, texts(out))
    await st.set_state(WritingStates.confirming_answer)
    out = await send(U1, text="/cancel")
    check("/cancel: the flow cleared, the menu shown", await st.get_state() is None
          and any("Bekor qilindi" in x for x in texts(out)), texts(out))
    out = await send(U1, text="/help")
    check("/help: the commands listed", any("/cancel" in x and "/menu" in x for x in texts(out)), texts(out))

    # 2. what no handler took
    out = await send(U1, text="salom bot")
    check("no flow: 'pick a section' with the menu", any("menyudan" in x for x in texts(out)), texts(out))
    await st.set_state(WritingStates.processing)
    out = await send(U1, text="yana bir essay")
    check("a check being graded: 'your check is running'", any("baholanmoqda" in x for x in texts(out)), texts(out))
    await st.set_state(WritingStates.confirming_prompt)
    out = await send(U1, text="ha")
    check("a step waiting for a button: 'pick a button above'", any("tugmalardan" in x for x in texts(out)), texts(out))
    await st.set_state(WritingStates.failed)
    out = await send(U1, text="?")
    check("a stopped check: 'tap Try again'", any("Qayta urinish" in x for x in texts(out)), texts(out))
    await st.clear()
    out = await press(U1, "writing:check_as_is")
    check("an old button: a short 'out of date' answer, not a silent spinner",
          any("eskirgan" in x for x in alerts(out)), alerts(out))

    # 3. the receipt step: cancel; one picture for two payments flagged
    out = await press(U1, f"{CB_BUY_PREFIX}10")
    async with get_session() as s:
        req = (await s.execute(select(PaymentRequest).where(PaymentRequest.user_id == U1)
                               .order_by(PaymentRequest.id.desc()))).scalars().first()
    buttons = [b.callback_data for c in out for row in (getattr(getattr(c, "reply_markup", None), "inline_keyboard",
                                                                    None) or []) for b in row]
    check("receipt step: a cancel button under the card number", f"{CB_PAY_CANCEL_PREFIX}{req.id}" in buttons, buttons)
    out = await press(U1, f"{CB_PAY_CANCEL_PREFIX}{req.id}")
    async with get_session() as s:
        gone = await s.get(PaymentRequest, req.id)
    check("cancel: the request without a receipt is gone, the flow left", gone is None
          and await st.get_state() is None and any("bekor qilindi" in x for x in texts(out)), texts(out))

    photo = [PhotoSize(file_id="rcpt-file-1", file_unique_id="UNIQ-1", width=10, height=10)]
    await press(U1, f"{CB_BUY_PREFIX}10")
    await send(U1, photo=photo)
    await press(U1, f"{CB_BUY_PREFIX}25")
    out = await send(U1, photo=[PhotoSize(file_id="rcpt-file-2", file_unique_id="UNIQ-1", width=10, height=10)])
    captions = [c.caption for c in out if type(c).__name__ == "SendPhoto"]
    check("the same receipt picture for a second payment: flagged on the admins' card",
          any("avval ishlatilgan" in (x or "") for x in captions), captions)

    # 4. sponsors: the homework's own group first; another group's homework counts against the limit
    async with get_session() as s:
        g_own = Group(name="Own", teacher_id=TEACHER, sponsor_checks=True, daily_limit=1)
        g_other = Group(name="Other", teacher_id=TEACHER, sponsor_checks=False)
        s.add_all([g_own, g_other])
        await s.flush()
        s.add_all([GroupMembership(group_id=g_own.id, user_id=U2), GroupMembership(group_id=g_other.id, user_id=U2)])
        a = Assignment(group_id=g_other.id, kind="homework", title="HW", due_at=now, created_by=TEACHER)
        s.add(a)
        await s.flush()
        part = AssignmentPart(assignment_id=a.id, position=1, module="writing_task2", prompt_text="Q")
        s.add(part)
        await s.flush()
        # another group's homework, paid by this sponsor today: it counts against the daily limit now
        s.add(WritingSubmission(user_id=U2, task_type="task2", prompt_text="Q", prompt_source="text", answer_text="A",
                                answer_source="text", original_file_paths={}, overall_band=6.0, criteria_scores={},
                                annotations=[], vocabulary_json=[], feedback_pdf_path="/tmp/x.pdf",
                                assignment_part_id=part.id, paid_by_id=TEACHER, coins_spent=5))
        await s.commit()
        own_id, other_id = g_own.id, g_other.id
    async with get_session() as s:
        used = await crud.count_sponsored_today(s, U2, TEACHER)
        payer = await crud.find_sponsor(s, U2, None, coins=5)
    check("another group's homework counts against the sponsor's daily limit", used == 1 and payer is None,
          (used, payer))
    async with get_session() as s:
        payer = await crud.find_sponsor(s, U2, own_id, coins=5)
    check("the sponsor's own group's homework: never limited", payer == TEACHER, payer)

    # 5. switching sponsorship on sets a daily limit; a center join switches it off
    from bot.handlers import group as group_handler
    async with get_session() as s:
        g = await s.get(Group, other_id)
        g.daily_limit = None
        await s.commit()
    from bot.keyboards import group_kb
    await press(TEACHER, f"{group_kb.CB_SPONSOR_PREFIX}{other_id}")
    async with get_session() as s:
        g = await s.get(Group, other_id)
    check("sponsorship switched on: with a daily limit", g.sponsor_checks and g.daily_limit
          == group_handler.DEFAULT_SPONSOR_LIMIT, (g.sponsor_checks, g.daily_limit))
    async with get_session() as s:
        center = Center(name="C", owner_id=U1)
        s.add(center)
        await s.commit()
        result = await crud.join_center(s, center.id, TEACHER)
        g = await s.get(Group, other_id)
        await s.refresh(g)
    check("a center join: the moved groups stop sponsoring until the owner says so", result == "joined"
          and not g.sponsor_checks and g.center_id is not None, (result, g.sponsor_checks))

    # 6. a group gift: recorded, its button works once
    async with get_session() as s:
        g = await s.get(Group, own_id)
        g.center_id = None
        await s.commit()
        before = (await s.get(DbUser, U2)).coin_balance
    gift = f"{group_kb.CB_GIFT_DO_PREFIX}{own_id}:{U2}:3"
    await asyncio.gather(press(TEACHER, gift, message_id=4242), press(TEACHER, gift, message_id=4242))
    async with get_session() as s:
        after = (await s.get(DbUser, U2)).coin_balance
        rows = (await s.execute(select(CoinAdjustment).where(CoinAdjustment.user_id.in_([U2, TEACHER]),
                                                             CoinAdjustment.note.like("%sovg'a%")))).scalars().all()
    check("a double tap on a gift: given once", after == before + 3, (before, after))
    check("the gift recorded for the student and the payer", {r.user_id for r in rows} == {U2, TEACHER}
          and any(r.delta == 3 for r in rows) and any(r.delta == -3 for r in rows), [(r.user_id, r.delta) for r in rows])

    # 7. onboarding: a double tap on the flag welcomes once
    NEW = 7_200_009
    async with get_session() as s:
        s.add(DbUser(id=NEW, full_name="New", coin_balance=8, language="uz", status=UserStatus.approved))
        await s.commit()
    outs = await asyncio.gather(press(NEW, f"{CB_LANG_ONBOARD_PREFIX}ru"), press(NEW, f"{CB_LANG_ONBOARD_PREFIX}ru"))
    from bot.i18n import t
    last_step = t("start.real_task", "ru")  # sent once per onboarding, at its end
    welcomes = sum(1 for out in outs for x in texts(out) if x == last_step)
    check("a double tap on the onboarding flag: onboarding runs once", welcomes == 1, welcomes)

    await bot.session.close()
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
