"""2026-11-16, the owner: a story card after every check - different designs, the bands, kind words only
(services/image/story_card.py, bot/handlers/share.py). Offline: a fake Bot API session, a throwaway Postgres +
Redis (test_part2.sh).
"""
import asyncio
import io
import itertools
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


fails: list[str] = []


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


def fake_session(refuse=None):
    from aiogram.client.session.base import BaseSession
    from aiogram.types import Chat, Message

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
            if refuse:
                raise refuse
            return Message(message_id=len(self.calls), date=datetime.now(timezone.utc),
                           chat=Chat(id=getattr(method, "chat_id", 1) or 1, type="private"), text="x")

    return S()


WRITING = {"TA": 7.0, "CC": 6.0, "LR": 6.5, "GRA": 6.0}
SPEAKING = {"FC": 5.0, "LR": 5.5, "GRA": 5.0, "Pronunciation": 6.0}


def unit() -> None:
    from PIL import Image

    from services.image import story_card as sc

    # every design, both modules, every language, a low and a top band: a 1080x1350 (4:5) picture
    bad = []
    for design, (module, task, scores), lang, band in itertools.product(
            sc.DESIGNS, [("writing", "task1", WRITING), ("writing", "task2", WRITING), ("speaking", "part2", SPEAKING)],
            ("uz", "ru", "en"), (4.0, 9.0)):
        png, used = sc.build_story_card(module=module, task=task, band=band, criteria=scores, lang=lang, design=design,
                                        seed=1)
        im = Image.open(io.BytesIO(png))
        if used != design or im.size != (1080, 1350) or im.format != "PNG":
            bad.append((design, module, lang, band, im.size))
    check(f"all {len(sc.DESIGNS)} designs x writing/speaking x uz/ru/en x low/top bands: a 1080x1350 PNG", not bad, bad)

    # the next card never repeats the previous design; over many cards every design shows up
    seen, repeats, prev = set(), 0, None
    for seed in range(200):
        _, used = sc.build_story_card(module="writing", task="task2", band=6.5, criteria=WRITING, avoid=prev, seed=seed)
        repeats += used == prev
        seen.add(used)
        prev = used
    check("a new card is never the student's previous design; all 7 come up", repeats == 0
          and seen == set(sc.DESIGNS), (repeats, seen))

    # kind words only: no "mistake", "bad", "low", "weak" in any language
    harsh = ("xato", "yomon", "past", "zaif", "ошиб", "плох", "низк", "слаб", "mistake", "bad", "low", "weak", "poor",
             "fail")
    words = [w for lang in sc._WORDS.values() for tier in lang["cheers"].values() for w in tier] + \
        [lang[k] for lang in sc._WORDS.values() for k in ("title", "overall", "best", "even", "footer", "scored")]
    found = [w for w in words for h in harsh if h in w.lower()]
    check("the cheers and labels never use a harsh word", not found, found)
    check("...every band tier has kind words in every language",
          all(len(sc._WORDS[lang]["cheers"][tier]) >= 3 for lang in ("uz", "ru", "en")
              for tier in ("top", "high", "mid", "start", "first")))

    # the tiers, the criteria names
    check("tiers: 9/8 top, 7.5/7 high, 6.5/6 mid, 5.5/5 start, 4.5 first",
          [sc.tier(b) for b in (9, 8, 7.5, 7, 6.5, 6, 5.5, 5, 4.5)] ==
          ["top", "top", "high", "high", "mid", "mid", "start", "start", "first"])
    t1 = sc._criteria("writing", "task1", WRITING)
    t2 = sc._criteria("writing", "task2", WRITING)
    sp = sc._criteria("speaking", "part1", SPEAKING)
    check("Task 1 shows Task Achievement, Task 2 Task Response; Speaking its four with Pronunciation",
          t1[0][1] == "Task Achievement" and t2[0][1] == "Task Response" and [r[0] for r in sp] == ["FC", "LR", "GRA", "P"]
          and sp[3][2] == 6.0)
    check("a missing criterion is left out, not drawn as 0", len(sc._criteria("writing", "task2", {"TA": 6.0})) == 1)
    _, used = sc.build_story_card(module="writing", task="task2", band=6.0, criteria={}, seed=3)
    check("no criteria at all (an old check): still a card", used in sc.DESIGNS)


async def bot_side() -> None:
    from aiogram import Bot

    from bot.handlers import share
    from bot.utils.crm import redis
    from db.database import get_session
    from db.models import SourceType, TaskType, User, UserStatus, WritingSubmission

    uid = 9_500_001
    async with get_session() as s:
        s.add(User(id=uid, full_name="Story", coin_balance=8, language="uz", status=UserStatus.approved))
        await s.commit()
        sub = WritingSubmission(user_id=uid, task_type=TaskType.task2, prompt_text="Q", prompt_source=SourceType.text,
                                answer_text="A", answer_source=SourceType.text, overall_band=6.5, criteria_scores=WRITING)
        s.add(sub)
        await s.commit()
        await s.refresh(sub)

    sess = fake_session()
    bot = Bot("8888548766:TEST", session=sess)
    await share.send_story(bot, uid, "writing", sub, "uz")
    photos = [c for c in sess.calls if type(c).__name__ == "SendPhoto"]
    first = await redis().get(share.LAST_KEY.format(uid))
    check("after a report: the story card goes as a photo, with the kind caption and the referral link",
          len(photos) == 1 and photos[0].chat_id == uid and "story" in photos[0].caption
          and f"ref_{uid}" in photos[0].caption, [p.caption for p in photos])
    rows = photos[0].reply_markup.inline_keyboard if photos else []
    check("...asks to post it on Instagram and support us; @bandzilla_uz links to the Instagram page, not to the "
          "Telegram account of that name", photos and "Instagram" in photos[0].caption
          and '<a href="https://www.instagram.com/bandzilla_uz/">@bandzilla_uz</a>' in photos[0].caption,
          photos[0].caption)
    check("...a button to our Instagram, then '🎨 Boshqa dizayn' (the same result, another design)",
          len(rows) == 2 and rows[0][0].url == "https://www.instagram.com/bandzilla_uz/"
          and rows[1][0].callback_data == f"share:writing:{sub.id}" and "dizayn" in rows[1][0].text)

    from bot.keyboards.share_kb import share_card_kb

    kb = share_card_kb("writing", sub.id, "uz").inline_keyboard
    check("under every report: the story button, and our Telegram channel and Instagram",
          kb[0][0].callback_data == f"share:writing:{sub.id}" and [b.url for b in kb[1]] ==
          ["https://t.me/bandzilla_uz", "https://www.instagram.com/bandzilla_uz/"])

    # the button: another design than the last one, every time
    from aiogram import Dispatcher
    from aiogram.types import CallbackQuery, Chat, Message, Update
    from aiogram.types import User as TgUser

    dp = Dispatcher()
    dp.include_router(share.router)
    designs = [first]
    for n in range(6):
        q = CallbackQuery(id=str(n), from_user=TgUser(id=uid, is_bot=False, first_name="S"), chat_instance="c",
                          data=f"share:writing:{sub.id}",
                          message=Message(message_id=100 + n, date=datetime.now(timezone.utc),
                                          chat=Chat(id=uid, type="private"), text="x"))
        await dp.feed_update(bot, Update(update_id=1000 + n, callback_query=q), lang="uz")
        designs.append(await redis().get(share.LAST_KEY.format(uid)))
    photos = [c for c in sess.calls if type(c).__name__ == "SendPhoto"]
    check("'🎨 Boshqa dizayn' six times: six more cards, never the same design twice in a row",
          len(photos) == 7 and all(a != b for a, b in zip(designs, designs[1:])), designs)

    q = CallbackQuery(id="x", from_user=TgUser(id=uid + 1, is_bot=False, first_name="Other"), chat_instance="c",
                      data=f"share:writing:{sub.id}",
                      message=Message(message_id=200, date=datetime.now(timezone.utc),
                                      chat=Chat(id=uid + 1, type="private"), text="x"))
    before = len(sess.calls)
    await dp.feed_update(bot, Update(update_id=2000, callback_query=q), lang="uz")
    others = [c for c in sess.calls[before:] if type(c).__name__ == "SendPhoto"]
    check("someone else's check: no card (only their own results)", not others)

    broken = Bot("8888548766:TEST", session=fake_session(refuse=RuntimeError("telegram away")))
    try:
        await share.send_story(broken, uid, "writing", sub, "uz")
        raised = False
    except Exception:  # noqa: BLE001
        raised = True
    check("Telegram failing: send_story never raises (the report has gone already)", not raised)
    sub.overall_band = None
    sess2 = fake_session()
    await share.send_story(Bot("8888548766:TEST", session=sess2), uid, "writing", sub, "uz")
    check("no band (an unfinished check): no card", not sess2.calls)


async def main() -> int:
    unit()
    await bot_side()
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    from bot.utils.crm import redis

    await redis().aclose()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
