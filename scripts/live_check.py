"""A Writing Task 1 check run through the LIVE bot as a test run (the
academic lead's acceptance runs, 2026-10-28) - the real dispatcher,
handlers, OpenAI and database, the same path as a student's message, with a
fake Telegram connection so nothing reaches Telegram:

    docker compose exec bot python scripts/live_check.py cases.json out_dir [--user 5764504074]

cases.json: [{"name", "prompt_text", "answer_text", "chart": path}, ...].
Each check is saved as a test submission (writing_submissions.is_test): no
coins, never a "previous essay" on the progress page, no word bank entries,
no chart key use, no admins' card. Writes every report's PDF and
out_dir/results.json (scores, mistakes, priorities, notices, model answer,
cost) for checking against the expected results.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from itertools import count
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def run(cases: list[dict], out: Path, uid: int) -> None:
    from aiogram import Bot
    from aiogram.client.session.base import BaseSession
    from aiogram.types import CallbackQuery, Chat, Document, File, Message, Update, User
    from sqlalchemy import select

    import bot.handlers.writing as writing
    from bot.keyboards.writing_task_kb import CB_TASK1
    from bot.main import build_dispatcher
    from bot.states.writing_states import WritingStates
    from db.database import get_session
    from db.models import WritingSubmission

    files: dict[str, bytes] = {}
    now = datetime.now(timezone.utc)
    captured: dict = {}
    real_pdf = writing.build_writing_report_pdf

    def capture_pdf(report, answer, progress, *args, **kwargs):
        captured.update(report=report, answer=answer, progress=progress)
        return real_pdf(report, answer, progress, *args, **kwargs)

    writing.build_writing_report_pdf = capture_pdf

    class TgSession(BaseSession):
        """Telegram as the handlers see it: every call answered here, the PDFs kept."""

        def __init__(self):
            super().__init__()
            self.calls, self.n = [], 5000

        async def close(self):
            pass

        async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
            yield files[url.rsplit("/", 1)[-1]]

        async def make_request(self, bot, method, timeout=None):
            self.calls.append(method)
            self.n += 1
            name = type(method).__name__
            if name == "GetFile":
                return File(file_id=method.file_id, file_unique_id="u" + method.file_id,
                            file_size=len(files[method.file_id]), file_path=f"documents/{method.file_id}")
            if name in ("AnswerCallbackQuery", "DeleteMessage", "EditMessageReplyMarkup", "SetMessageReaction",
                        "SendChatAction", "SendSticker"):
                return True
            return Message(message_id=getattr(method, "message_id", None) or self.n, date=now,
                           chat=Chat(id=uid, type="private"), text=getattr(method, "text", None) or "x").as_(bot)

    session = TgSession()
    tg = Bot("1:LIVECHECK", session=session)  # its own bot id: its FSM keys never touch the real bot's
    dp = build_dispatcher()
    state = dp.fsm.get_context(bot=tg, chat_id=uid, user_id=uid)
    counter = count(10_000)
    sender = User(id=uid, is_bot=False, first_name="T")

    async def send(**kw):
        start = len(session.calls)
        message = Message(message_id=next(counter), date=now, chat=Chat(id=uid, type="private"), from_user=sender, **kw)
        await dp.feed_update(tg, Update(update_id=next(counter), message=message))
        return session.calls[start:]

    async def press(data: str):
        start = len(session.calls)
        query = CallbackQuery(id=str(next(counter)), from_user=sender, chat_instance="ci", data=data,
                              message=Message(message_id=next(counter), date=now, chat=Chat(id=uid, type="private"),
                                              text="x"))
        await dp.feed_update(tg, Update(update_id=next(counter), callback_query=query))
        return session.calls[start:]

    def texts(calls) -> list[str]:
        return [getattr(c, "text", "") or "" for c in calls if type(c).__name__ in ("SendMessage", "EditMessageText")]

    out.mkdir(parents=True, exist_ok=True)
    results = []
    for case in cases:
        captured.clear()
        await state.clear()
        print(f"== {case['name']}", flush=True)
        await press(CB_TASK1)
        await send(text=case["prompt_text"])
        if await state.get_state() == WritingStates.awaiting_chart.state:
            data = Path(case["chart"]).read_bytes()
            fid = f"chart{next(counter)}"
            files[fid] = data
            mime = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
            await send(document=Document(file_id=fid, file_unique_id="u" + fid, file_name=f"{fid}.{mime[6:]}",
                                         mime_type=mime, file_size=len(data)))
        if await state.get_state() != WritingStates.awaiting_answer.state:
            print("   the bot did not ask for the answer:", texts(session.calls[-4:]), flush=True)
            results.append({"name": case["name"], "error": "no answer step", "bot": texts(session.calls[-4:])})
            continue
        await state.update_data(test_run=True)  # a test submission: no coins, no progress, no word bank
        calls = await send(text=case["answer_text"])
        docs = [c for c in calls if type(c).__name__ == "SendDocument"]
        report, answer = captured.get("report"), captured.get("answer")
        if not docs or report is None:
            print("   no report:", texts(calls)[-3:], flush=True)
            results.append({"name": case["name"], "error": "no report", "bot": texts(calls)[-3:]})
            continue
        async with get_session() as s:
            sub = (await s.execute(select(WritingSubmission).where(WritingSubmission.user_id == uid)
                                   .order_by(WritingSubmission.id.desc()))).scalars().first()
        pdf = out / f"{case['name']}.pdf"
        pdf.write_bytes(Path(sub.feedback_pdf_path).read_bytes())
        result = {
            "name": case["name"], "submission_id": sub.id, "is_test": sub.is_test,
            "overall": report.overall_band, "scores": sub.criteria_scores, "words": report.word_count,
            "notices": report.notices, "caption": docs[0].caption,
            "priorities": [{"n": p["n"], "title": p["title"], "tags": [t[0] for t in p["tags"]], "tip": p["tip"]}
                           for p in report.priorities],
            "mistakes": [{"n": m["n"], "wrong": m["wrong"], "correct": m["correct"], "criterion": m["criterion"],
                          "why": m["why"]} for m in report.mistakes],
            "has_copy": report.has_copy,
            "criterion_notes": {c["key"]: c["note"] for c in report.criteria},
            "model_answer": None if answer is None else {"band": answer.band, "score": answer.score,
                                                          "text": "\n\n".join(answer.paragraphs)},
            "progress": captured.get("progress"),
            "visual": {k: (sub.report_data or {}).get("visual", {}).get(k) for k in ("readability", "chart_key")},
            "cost": (sub.report_data or {}).get("cost", {}).get("total_usd"),
        }
        results.append(result)
        print(f"   #{sub.id} overall {result['overall']} {result['scores']} cost ${result['cost']}", flush=True)
        (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str),
                                          encoding="utf-8")
    writing.build_writing_report_pdf = real_pdf
    total = sum(r.get("cost") or 0 for r in results)
    print(f"\n{len(results)} checks, ${total:.3f}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cases")
    ap.add_argument("out")
    ap.add_argument("--user", type=int, default=5764504074)
    a = ap.parse_args()
    asyncio.run(run(json.loads(Path(a.cases).read_text(encoding="utf-8")), Path(a.out), a.user))
