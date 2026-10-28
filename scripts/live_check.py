"""Writing (Task 1, Task 2) and Speaking checks run through the bot as test
runs (the academic lead's acceptance runs, 2026-10-28/29) - the real
dispatcher, handlers, OpenAI and database, the same path as a student's
message, with a fake Telegram connection so nothing reaches Telegram:

    docker compose exec bot python scripts/live_check.py cases.json out_dir [--user 5764504074]

cases.json: [{"name", "kind": "task1" | "task2" | "speaking", ...}]
  task1:    "prompt_text", "answer_text", "chart": path
  task2:    "prompt_text", "answer_text"
  speaking: "part": "part3", "questions": [...], "audio": path (one recording),
            "user": id (optional), "exclude_anchor": id (a recording that is
            itself a scoring anchor is scored without it)
Each check is saved as a test submission (is_test): no coins, never a
"previous essay/session" on the progress page, no word bank entries, no
chart key use, no admins' card. Writes every report's PDF and
out_dir/results.json: scores, every flag with its class (error, slip,
alternative, not a mistake, self-correction), the upgrade ideas, priorities,
notices, the model answer, cost - and for a chart, which figures were
measured from the picture and which come from the AI reads.
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

    import bot.handlers.speaking as speaking
    import bot.handlers.writing as writing
    from bot.keyboards.confirm_kb import CB_CONFIRM
    from bot.keyboards.speaking_part_kb import CB_MODE_ONE, CB_REC_FINISH, CB_SPLIT_OK, PART_CALLBACKS
    from bot.keyboards.writing_task_kb import CB_TASK1, CB_TASK2
    from bot.main import build_dispatcher
    from bot.states.speaking_states import SpeakingStates
    from bot.states.writing_states import WritingStates
    from db.database import get_session
    from db.models import SpeakingSubmission, WritingSubmission
    from services.ai import speaking_grader, speaking_marking, writing_grader

    files: dict[str, bytes] = {}
    now = datetime.now(timezone.utc)
    captured: dict = {}
    flags: list[dict] = []

    real_pdf, real_spdf = writing.build_writing_report_pdf, speaking.build_speaking_report_pdf

    def capture_pdf(report, answer, progress, *args, **kwargs):
        captured.update(report=report, answer=answer, progress=progress)
        return real_pdf(report, answer, progress, *args, **kwargs)

    def capture_spdf(report, answers, progress, *args, **kwargs):
        captured.update(report=report, answers=answers, progress=progress)
        return real_spdf(report, answers, progress, *args, **kwargs)

    writing.build_writing_report_pdf = capture_pdf
    speaking.build_speaking_report_pdf = capture_spdf

    # every flag the filter judged, with its class
    real_wverify, real_sverify, real_corr = (writing_grader._verify_call, speaking_grader.verify_call,
                                            speaking_marking.self_correction)

    async def wverify(source_text, items, vocabulary=None):
        verdicts, words = await real_wverify(source_text, items, vocabulary)
        for i, _, wrong, correct, _why, crit, _note in items:
            kind, crit2, reason, _ = verdicts.get(i, ("error", crit, "no verdict", None))
            flags.append({"wrong": wrong, "correct": correct, "class": kind, "criterion": crit2, "reason": reason})
        return verdicts, words

    async def sverify(source_text, items, vocabulary=None):
        verdicts, words = await real_sverify(source_text, items, vocabulary)
        for i, where, wrong, correct, _why in items:
            kind, reason = verdicts.get(i, ("error", "no verdict"))
            flags.append({"q": where, "wrong": wrong, "correct": correct, "class": kind, "reason": reason})
        return verdicts, words

    def corr(text, wrong, correct):
        found = real_corr(text, wrong, correct)
        if found and not any(f["wrong"] == wrong and f["class"] == "self-correction" for f in flags):
            flags.append({"wrong": wrong, "correct": correct, "class": "self-correction", "reason": "by code"})
        return found

    writing_grader._verify_call = wverify
    speaking_grader.verify_call = sverify
    speaking_marking.self_correction = corr

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
                           chat=Chat(id=bot.uid, type="private"), text=getattr(method, "text", None) or "x").as_(bot)

    session = TgSession()
    tg = Bot("1:LIVECHECK", session=session)  # its own bot id: its FSM keys never touch the real bot's
    dp = build_dispatcher()
    counter = count(10_000)

    def as_user(user_id: int):
        tg.uid = user_id
        return dp.fsm.get_context(bot=tg, chat_id=user_id, user_id=user_id), User(id=user_id, is_bot=False,
                                                                                    first_name="T")

    async def send(sender, **kw):
        start = len(session.calls)
        message = Message(message_id=next(counter), date=now, chat=Chat(id=sender.id, type="private"),
                          from_user=sender, **kw)
        await dp.feed_update(tg, Update(update_id=next(counter), message=message))
        return session.calls[start:]

    async def press(sender, data: str):
        start = len(session.calls)
        query = CallbackQuery(id=str(next(counter)), from_user=sender, chat_instance="ci", data=data,
                              message=Message(message_id=next(counter), date=now,
                                              chat=Chat(id=sender.id, type="private"), text="x"))
        await dp.feed_update(tg, Update(update_id=next(counter), callback_query=query))
        return session.calls[start:]

    def document(path: str, mime: str) -> Document:
        data = Path(path).read_bytes()
        fid = f"f{next(counter)}"
        files[fid] = data
        return Document(file_id=fid, file_unique_id="u" + fid, file_name=f"{fid}{Path(path).suffix}",
                        mime_type=mime, file_size=len(data))

    def texts(calls) -> list[str]:
        return [getattr(c, "text", "") or "" for c in calls if type(c).__name__ in ("SendMessage", "EditMessageText")]

    out.mkdir(parents=True, exist_ok=True)
    results = []
    for case in cases:
        captured.clear()
        flags.clear()
        kind = case.get("kind", "task1")
        user_id = int(case.get("user") or uid)
        state, sender = as_user(user_id)
        await state.clear()
        print(f"== {case['name']} ({kind})", flush=True)
        speaking_grader.EXCLUDED_ANCHORS = {case["exclude_anchor"]} if case.get("exclude_anchor") else set()
        if kind in ("task1", "task2"):
            await press(sender, CB_TASK1 if kind == "task1" else CB_TASK2)
            await send(sender, text=case["prompt_text"])
            if await state.get_state() == WritingStates.awaiting_chart.state:
                path = case["chart"]
                mime = "image/png" if Path(path).read_bytes()[:4] == b"\x89PNG" else "image/jpeg"
                await send(sender, document=document(path, mime))
            if await state.get_state() != WritingStates.awaiting_answer.state:
                results.append({"name": case["name"], "error": "no answer step", "bot": texts(session.calls[-4:])})
                print("   the bot did not ask for the answer:", texts(session.calls[-4:]), flush=True)
                continue
            await state.update_data(test_run=True)  # a test submission: no coins, no progress, no word bank
            calls = await send(sender, text=case["answer_text"])
            table = WritingSubmission
        else:
            part_cb = next(cb for cb, part in PART_CALLBACKS.items() if part == case.get("part", "part3"))
            await press(sender, part_cb)
            await state.update_data(test_run=True)
            await send(sender, text="\n".join(case["questions"]))
            if await state.get_state() == SpeakingStates.confirming_question.state:
                await press(sender, CB_CONFIRM)
            if await state.get_state() == SpeakingStates.choosing_mode.state:
                await press(sender, CB_MODE_ONE)
            await send(sender, document=document(case["audio"], "audio/mpeg"))
            await state.update_data(test_run=True)
            calls = await press(sender, CB_REC_FINISH)
            if await state.get_state() == SpeakingStates.confirming_split.state:
                await state.update_data(test_run=True)
                calls = await press(sender, CB_SPLIT_OK)
            table = SpeakingSubmission
        docs = [c for c in calls if type(c).__name__ == "SendDocument"]
        report = captured.get("report")
        if not docs or report is None:
            print("   no report:", texts(calls)[-3:], flush=True)
            results.append({"name": case["name"], "error": "no report", "bot": texts(calls)[-3:]})
            continue
        async with get_session() as s:
            sub = (await s.execute(select(table).where(table.user_id == user_id)
                                   .order_by(table.id.desc()))).scalars().first()
        (out / f"{case['name']}.pdf").write_bytes(Path(sub.feedback_pdf_path).read_bytes())
        data = sub.report_data or {}
        result = {
            "name": case["name"], "kind": kind, "submission_id": sub.id, "is_test": sub.is_test,
            "coins": sub.coins_spent, "overall": report.overall_band, "scores": sub.criteria_scores,
            "notices": report.notices, "caption": docs[0].caption, "top_band": report.top_band,
            "priorities": [{"n": p["n"], "title": p["title"], "tags": [t[0] for t in p["tags"]],
                            "tip": p.get("tip")} for p in report.priorities],
            "mistakes": [{"n": m["n"], "wrong": m["wrong"], "correct": m["correct"], "criterion": m["criterion"],
                          "why": m["why"]} for m in report.mistakes],
            "upgrades": [{"wrong": u["wrong"], "correct": u["correct"]} for u in report.upgrades],
            "flags": list(flags),
            "criterion_notes": {c["key"]: c["note"] for c in report.criteria},
            "progress": captured.get("progress"),
            "cost": (data.get("cost") or {}).get("total_usd"),
        }
        if kind == "speaking":
            result["model_answers"] = [{"label": a["label"], "alternative": a.get("alternative"), "words": a["words"],
                                        "text": " ".join(a["paragraphs"])} for a in captured.get("answers") or []]
        else:
            answer = captured.get("answer")
            result["words"] = report.word_count
            result["has_copy"] = report.has_copy
            result["model_answer"] = None if answer is None else {
                "band": answer.band, "score": answer.score, "alternative": answer.alternative,
                "text": "\n\n".join(answer.paragraphs)}
        if kind == "task1":
            visual = data.get("visual") or {}
            result["visual"] = {"readability": visual.get("readability"), "chart_key": visual.get("chart_key")}
            result["figures"] = [
                {"series": v["series"], "point": v["point"], "value": v["value"],
                 "source": "measured" if str(v.get("note", "")).startswith("measured")
                 else "printed" if v.get("printed") else f"AI ({v['status']})"}
                for b in visual.get("blocks", []) for v in b.get("values", []) if v.get("value") is not None]
        results.append(result)
        print(f"   #{sub.id} overall {result['overall']} {result['scores']} cost ${result['cost']}", flush=True)
        (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str),
                                          encoding="utf-8")
    writing.build_writing_report_pdf, speaking.build_speaking_report_pdf = real_pdf, real_spdf
    writing_grader._verify_call, speaking_grader.verify_call = real_wverify, real_sverify
    speaking_marking.self_correction = real_corr
    total = sum(r.get("cost") or 0 for r in results)
    print(f"\n{len(results)} checks, ${total:.3f}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cases")
    ap.add_argument("out")
    ap.add_argument("--user", type=int, default=5764504074)
    a = ap.parse_args()
    asyncio.run(run(json.loads(Path(a.cases).read_text(encoding="utf-8")), Path(a.out), a.user))
