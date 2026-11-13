"""The waiting card (bot/utils/progress.py, the user 2026-11-14): its stages follow the check's own calls, the bar
never shows 100% before the report, a tip or a one-tap question keeps the student company, and it stops the
moment the check takes its message back. Offline, no Telegram:

    python tests/progress_test.py
"""
import asyncio
import re
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiogram.exceptions import TelegramBadRequest  # noqa: E402

from bot.utils import progress as live  # noqa: E402
from services.ai import usage  # noqa: E402

fails: list[str] = []


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


class FakeMessage:
    def __init__(self, mid: int = 1):
        self.chat = NS(id=42)
        self.message_id = mid
        self.edits: list[tuple[str, object]] = []
        self.gone = False

    async def edit_text(self, text, reply_markup=None):
        if self.gone:
            raise TelegramBadRequest(method=None, message="Bad Request: message to edit not found")
        self.edits.append((text, reply_markup))


def percent(text: str) -> int:
    return int(re.search(r"<b>(\d+)%</b>", text).group(1))


def balanced(text: str) -> bool:
    return all(text.count(f"<{t}>") == text.count(f"</{t}>") for t in ("b", "i"))


async def main() -> None:
    live.TICK, live.FIRST, live.QUIZ_AFTER, live.CREEP = 0.05, 0.01, 0.25, 0.2

    msg = FakeMessage()
    card = live.begin(msg, "uz", "writing")
    await asyncio.sleep(0.12)
    first = msg.edits[-1][0] if msg.edits else ""
    check("the card: brand title, a bar, the first stage in progress, the others waiting, a tip",
          "Bandzilla tekshiryapti" in first and ("🟨" in first or "🟧" in first)
          and "Javobingizni o'qiyapman" in first and "▫️" in first and "💡" in first, first)
    usage._add(usage.Call("feedback", "gpt-5", 0.01))
    await asyncio.sleep(0.12)
    after = msg.edits[-1][0]
    check("a feedback call recorded: reading done, hunting for mistakes", "✅ Javobingiz o'qildi" in after
          and "Xatolarni ovlayapman" in after, after)
    usage._add(usage.Call("verify", "gpt-5", 0.01))
    usage._add(usage.Call("score", "gpt-5", 0.01))
    await asyncio.sleep(0.6)
    texts = [t for t, _ in msg.edits]
    shares = [percent(t) for t in texts]
    check("the bar only goes forward, and never to 100% before the report", shares == sorted(shares)
          and max(shares) < 100, shares)
    asked = [(t, k) for t, k in msg.edits if k is not None]
    check("after a while: a one-tap vocabulary question with three options", asked
          and len(asked[-1][1].inline_keyboard[0]) == 3 and "🧠" in asked[-1][0], texts[-1][-200:])
    q = card.quiz[-1]
    right = live._choices(q).index(live.QUIZ[q][1])
    check("...the right option counts", card.answer(q, right) is True and card.answer(q, right) is None)
    await asyncio.sleep(0.08)
    check("...shown at once: 'To'g'ri!' and the score", "To'g'ri!" in msg.edits[-1][0] and "1/1" in msg.edits[-1][0]
          and msg.edits[-1][1] is None, msg.edits[-1][0][-160:])
    await asyncio.sleep(0.3)
    check("...then the next question", len(card.quiz) == 2 and msg.edits[-1][1] is not None, card.quiz)
    check("every frame is valid HTML for Telegram and short", all(balanced(t) and len(t) < 1500 for t in texts))
    await live.end(card)
    n = len(msg.edits)
    await asyncio.sleep(0.2)
    check("ended: no more edits, the card forgotten, its calls no longer heard", len(msg.edits) == n
          and live.find(42, 1) is None and usage._listener.get() is None)

    # the check deleted the message: the card stops by itself
    msg2 = FakeMessage(2)
    card2 = live.begin(msg2, "ru", "speaking")
    await asyncio.sleep(0.08)
    check("Russian, Speaking: listening first", "Слушаю вашу запись" in msg2.edits[-1][0], msg2.edits[-1][0][:200])
    msg2.gone = True
    await asyncio.sleep(0.15)
    check("a deleted message: the card stops itself", card2.task.done())
    await live.end(card2)

    msg3 = FakeMessage(3)
    card3 = live.begin(msg3, "en", "writing_task1")
    check("the same message again: the same card", live.begin(msg3, "en", "writing_task1") is card3)
    await asyncio.sleep(0.08)
    check("English, Task 1: its own usual time", "usually 3–6 minutes" in msg3.edits[-1][0])
    await live.end(card3)


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    sys.exit(1 if fails else 0)
