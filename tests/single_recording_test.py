"""Speaking: one recording with every answer (the last prompt's package D,
2026-10-26) - offline: OpenAI and Telegram are fakes, nothing is sent and
nothing is paid for.

Part 1 (the split itself: units, spans, the cut audio, the transcripts, the
times a student sends) runs anywhere the bot's requirements are installed.
Part 2 drives the real bot handlers with synthetic recordings, so it needs
ffmpeg and a THROWAWAY database and Redis, never production's - inside the
bot image:

    DATABASE_URL=... REDIS_URL=... OPENAI_API_KEY=sk-dummy python tests/single_recording_test.py

Part 2 flushes that Redis and writes Speaking submissions into that database.
The grading itself (speaking_check.run_check's model calls) is replaced by a
stand-in that builds the report from what the split gives it - pauses and
fillers measured for real - so what is tested is the recording's way there.
"""
import asyncio
import io
import math
import os
import re
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

fails: list[str] = []
RUN = os.urandom(3).hex()  # every run's recordings are new, never "the same recording again"
RATE = 16000


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


# --- A scripted recording -------------------------------------------------------------------------


def line(text, start, end, role="answer", q=0, ask=False):
    """One sentence of the recording: who says it, when, which question it answers."""
    return {"text": text, "start": start, "end": end, "role": role, "q": q, "ask": ask}


_UNIQUE = iter(range(1, 30_000))


def tone_wav(seconds: float, speech: list[tuple[float, float]], seed: int = 0) -> bytes:
    """A 16 kHz WAV: a tone where someone speaks, digital silence elsewhere -
    each one different in its last sample, so no two are "the same recording"."""
    n = int(seconds * RATE)
    samples = bytearray(n * 2)
    samples[-2:] = next(_UNIQUE).to_bytes(2, "little", signed=True)
    freq = 180 + seed % 7 * 10
    for s, e in speech:
        for i in range(max(0, int(s * RATE)), min(n, int(e * RATE))):
            v = int(7000 * math.sin(2 * math.pi * freq * i / RATE))
            samples[2 * i: 2 * i + 2] = v.to_bytes(2, "little", signed=True)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(samples))
    return buf.getvalue()


def recording(lines: list[dict], cuts: list[float] = (), tail: float = 1.5, seed: int = 0):
    """(pieces as WAV bytes, the lines with their times in the joined
    recording, its length): the timeline cut at `cuts` (inside silences),
    shifted as single_recording.join_wavs joins the pieces."""
    from services.ai.single_recording import GAP

    total = max(x["end"] for x in lines) + tail
    edges = [0.0, *cuts, total]
    pieces = []
    for k in range(len(edges) - 1):
        a, b = edges[k], edges[k + 1]
        speech = [(max(x["start"], a) - a, min(x["end"], b) - a) for x in lines if x["end"] > a and x["start"] < b]
        pieces.append(tone_wav(b - a, speech, seed + k))
    shifted = []
    for x in lines:
        k = sum(x["start"] >= c for c in cuts)
        shifted.append({**x, "start": x["start"] + GAP * k, "end": x["end"] + GAP * k})
    return pieces, shifted, total + GAP * len(cuts)


class FakeAI:
    """Whisper reads back the scripted recording; the split call labels each
    unit from the script; the filler call finds none."""

    def __init__(self):
        self.lines: list[dict] = []
        self.language = "english"
        self.duration = 0.0
        self.confidence = "high"
        self.whisper_calls = 0
        self.split_calls = 0
        self.answer = "I like reading books because they are interesting and relaxing."

    def _response(self, lines):
        words, segments = [], []
        for x in lines:
            tokens = x["text"].split()
            step = (x["end"] - x["start"]) / max(1, len(tokens))
            for i, tok in enumerate(tokens):
                words.append(NS(word=re.sub(r"[^\w'%-]", "", tok), start=x["start"] + i * step,
                                end=x["start"] + (i + 1) * step - 0.05))
            segments.append(NS(start=x["start"], end=x["end"], text=x["text"], avg_logprob=-0.2,
                               no_speech_prob=0.01))
        return NS(text=" ".join(x["text"] for x in lines), words=words, segments=segments,
                  language=self.language, duration=self.duration)

    async def transcribe(self, **kw):
        self.whisper_calls += 1
        if self.lines:  # one recording
            return self._response(self.lines)
        # an answer sent on its own (question by question)
        return self._response([line(self.answer, 0.3, 4.3)])

    async def parse(self, **kw):
        name = kw["response_format"].__name__
        if name == "_FillerCall":
            parsed = NS(filler_ids=[])
        elif name == "_Alignment":
            self.split_calls += 1
            parsed = self._align(kw["messages"][-1]["content"])
        else:
            raise AssertionError(f"unexpected OpenAI call {name}")
        return NS(choices=[NS(message=NS(parsed=parsed, refusal=None, content=None))],
                  usage=None, model="gpt-5-mini", service_tier="flex")

    def _align(self, content: str):
        from services.ai.single_recording import _AskedQuestion, _UnitLabel

        by_text = {x["text"].strip(): x for x in self.lines}
        labels = []
        for m in re.finditer(r"^U(\d+) \[[^\]]*\] (.*)$", content, re.MULTILINE):
            x = by_text.get(m.group(2).strip())
            role, q = (x["role"], x["q"]) if x else ("other", 0)
            labels.append(_UnitLabel(unit=int(m.group(1)), role=role, question=q if role == "answer" else 0))
        asked = []
        if "NO QUESTIONS ARE GIVEN" in content:
            part = "part3" if "Part 3" in content else "part2" if "Part 2" in content else "part1"
            asked = [_AskedQuestion(text=x["text"], part=part) for x in self.lines if x["ask"]]
        return NS(asked=asked, labels=labels, confidence=self.confidence, doubt="" if self.confidence == "high"
                  else "unsure where one answer ends")


# --- Part 1: the split -----------------------------------------------------------------------------


def part1_split() -> None:
    from services.ai import single_recording as one

    lines = [
        line("Let's talk about your hometown. Where are you from?", 0.5, 4.0, "question"),
        line("I'm from Samarkand, a very old city.", 5.5, 8.5, "answer", 1),
        line("It is famous for its historical buildings.", 8.9, 12.0, "answer", 1),
        line("Do you like living there?", 13.5, 15.0, "question"),
        line("Yes, I really like it.", 16.5, 18.5, "answer", 2),
        line("Why?", 19.8, 20.4, "question"),
        line("Because my family and friends are there.", 21.8, 25.0, "answer", 2),
    ]
    fake = FakeAI()
    resp = fake._response(lines)
    rec = one.Recording(text=resp.text, words=[[w.word, w.start, w.end] for w in resp.words],
                        segments=[[s.start, s.end, s.avg_logprob] for s in resp.segments],
                        plain_words=[[w.word, w.start, w.end] for w in resp.words], language="english", duration=27.0)
    found = one.units(rec)
    check("units: one per sentence, the follow-up 'Why?' its own unit",
          [u.text for u in found] == [
              "Let's talk about your hometown.", "Where are you from?", "I'm from Samarkand, a very old city.",
              "It is famous for its historical buildings.", "Do you like living there?", "Yes, I really like it.",
              "Why?", "Because my family and friends are there."], [u.text for u in found])
    roles = {"Let's talk about your hometown.": ("question", 0), "Where are you from?": ("question", 0)}
    labels = []
    for i, u in enumerate(found, 1):
        x = next((y for y in lines if u.text in y["text"]), None)
        role, q = roles.get(u.text, (x["role"], x["q"]))
        labels.append(one._UnitLabel(unit=i, role=role, question=q if role == "answer" else 0))
    spans = one.spans_of(found, labels, 2)
    check("spans: Q1 is one span (two sentences of one answer)", len(spans[0]) == 1, spans)
    check("spans: Q2 is two spans - the examiner's 'Why?' is cut out", len(spans[1]) == 2, spans)
    check("spans: never into the examiner's words",
          spans[1][0][1] <= 19.8 and spans[1][1][0] >= 20.4 and spans[0][0][0] >= 4.0, spans)
    a2 = one.answer_audio(rec, spans[1])
    check("answer: Q2's transcript is the student's words only",
          a2.text == "Yes, I really like it. Because my family and friends are there.", a2.text)
    check("answer: times are in the cut clip, starting near 0 and in order",
          a2.tokens[0].start < 0.5 and all(t.start <= u.start for t, u in zip(a2.tokens, a2.tokens[1:])),
          [(t.text, round(t.start, 2)) for t in a2.tokens][:4])
    check("answer: the clip leaves out the 'Why?' and its silences",
          a2.duration < (25.0 - 16.5) - 1.5, a2.duration)
    check("answer: words counted as the grader counts them", a2.word_count == 12, a2.word_count)
    ok = NS(confidence="high", doubt="")
    check("problems: a clear split has none", one.problems(spans, ok, rec, ["part1", "part1"]) == [],
          one.problems(spans, ok, rec, ["part1", "part1"]))
    check("problems: a question with no answer is a reason to show the split",
          any("Q2" in p for p in one.problems([spans[0], []], ok, rec, ["part1", "part1"])))
    check("problems: answers out of order", any("order" in p for p in one.problems(
        [spans[1], spans[0]], ok, rec, ["part1", "part1"])))
    check("problems: the model's own doubt", one.problems(spans, NS(confidence="low", doubt="x"), rec,
                                                         ["part1", "part1"]) == ["x"])
    check("problems: a failed split call", one.problems(spans, None, rec, ["part1"]) != [])

    shown = one.format_split([spans[0], []], ["Where are you from?", "Do you like it?"], "javob topilmadi")
    check("format: times per question, 'javob topilmadi' for none",
          shown.splitlines()[0].startswith("Q1: 0:05–0:12 — Where") and "Q2: javob topilmadi" in shown, shown)
    fixed = one.parse_split("Q2: 0:16–0:19, 0:21-0:25\nQ1: -", 2, 27.0, [spans[0], []])
    check("parse: the student's times, several spans, '-' for no answer",
          fixed == [[], [(16.0, 19.0), (21.0, 25.0)]], fixed)
    check("parse: a copied message with one line changed keeps the rest",
          one.parse_split(shown.replace("Q2: javob topilmadi", "Q2: 0:16–0:25"), 2, 27.0, [spans[0], []])
          == [[(5.0, 12.0)], [(16.0, 25.0)]])
    check("parse: nothing readable is None", one.parse_split("hello", 2, 27.0, spans) is None)
    check("parse: question lines without times or a dash are not 'no answer'",
          one.parse_split("Q1: Where are you from?", 2, 27.0, spans) is None)

    joined = one.join_wavs([tone_wav(2.0, [(0, 2)]), tone_wav(3.0, [(0, 3)])])
    check("join: pieces in order with a short gap", abs(one._frames(joined).__len__() / 2 / RATE - 5.3) < 0.01)
    clip = one.cut(joined, [(0.5, 1.5), (2.5, 3.0)])
    check("cut: just the spans, a short gap between", abs(len(one._frames(clip)) / 2 / RATE - 1.8) < 0.01)
    to_clip = one.clip_time([(0.5, 1.5), (2.5, 3.0)])
    check("cut: times map into the clip", (to_clip(0.5), to_clip(1.5), round(to_clip(2.75), 2)) == (0.0, 1.0, 1.55))
    data = rec.as_json()
    check("recording: saved and read back the same", one.Recording.from_json(data) == rec
          and rec.has_speech and rec.english)
    check("recording: another language is caught", not one.Recording(**{**data, "language": "uzbek"}).english)
    check("limits: the academic lead's values from config/speaking_single.json",
          one.limits()["max_minutes"]["full"] == 20 and one.limits()["min_answer_words"]["part2"] == 30)


# --- Part 2: the bot ---------------------------------------------------------------------------------


async def part2_bot() -> None:
    from aiogram import Bot
    from aiogram.client.session.base import BaseSession
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.types import CallbackQuery, Chat, Document, File, Message, Update, User
    from sqlalchemy import select

    from bot.config import settings
    from bot.handlers import speaking as handler
    from bot.i18n import t
    from bot.keyboards.speaking_part_kb import (
        CB_MODE_EACH, CB_MODE_ONE, CB_NO_QUESTIONS, CB_PART1, CB_PART2, CB_PART3, CB_REC_ANYWAY, CB_REC_FINISH,
        CB_SPLIT_FIX, CB_SPLIT_OK,
    )
    from bot.main import build_dispatcher
    from bot.states.speaking_states import SpeakingStates
    from bot.utils.crm import redis
    from db.database import get_session
    from db.models import SpeakingSubmission, User as DbUser
    from services.ai import schemas, single_recording as one
    from services.ai.openai_client import client
    from services.ai.speaking_check import SpeakingCheck, build_items
    from services.ai.speaking_marking import build_report
    from services.ai.voice import analyse_answers, measure, to_wav16k

    await redis().flushdb()
    fake = FakeAI()
    client.with_options = lambda **kw: client
    client.beta.chat.completions.parse = fake.parse
    client.audio.transcriptions.create = fake.transcribe

    graded: list[dict] = []

    async def fake_run_check(part, questions, clips, seed=0, transcribed=None):
        """The grading's stand-in: the real pauses and fillers of what it is
        given, a report with no mistakes."""
        wavs = [await to_wav16k(a, n) for a, n in clips]
        audios = await analyse_answers(wavs, [q["text"] for q in questions], transcribed)
        items = build_items(questions, audios)
        metrics = measure(audios)
        fb = schemas.SpeakingFeedback(
            answers=[schemas.AnswerMarking(id=i.qid, segments=[schemas.AnswerSegment(
                type="text", text=i.audio.text, fix=None, mistake_id=None)]) for i in items if not i.audio.no_speech],
            mistakes=[], criterion_notes=schemas.SpeakingCriterionNotes(FC="a", LR="b", GRA="c", P="d"),
            strength=schemas.Strength(text="Clear answers.", quoted_phrases=[]), priorities=[],
            vocabulary_topic="daily life",
            vocabulary=[schemas.VocabEntry(term=f"t{k}", meaning="m", example="e") for k in range(10)],
            checklist=[], cue_coverage=[])
        report = build_report(part, items, fb, [], set(), set(), {"FC": 6.0, "LR": 6.0, "GRA": 6.0, "P": 6.0},
                              metrics, None, None, None)
        graded.append({"part": part, "questions": questions, "audios": audios, "clips": clips,
                       "transcribed": transcribed is not None, "report": report})
        return SpeakingCheck(report=report, answers=[], items=items, metrics=metrics)

    handler.run_check = fake_run_check
    real_limits = one.limits

    files: dict[str, bytes] = {}
    now = datetime.now(timezone.utc)

    class TgSession(BaseSession):
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
                if method.file_id.startswith("too_big"):
                    raise TelegramBadRequest(method=method, message="Bad Request: file is too big")
                return File(file_id=method.file_id, file_unique_id="u" + method.file_id,
                            file_size=len(files[method.file_id]), file_path=f"documents/{method.file_id}")
            if name in ("AnswerCallbackQuery", "DeleteMessage", "EditMessageReplyMarkup", "SetMessageReaction",
                        "SendChatAction"):
                return True
            chat_id = getattr(method, "chat_id", None) or uid
            return Message(message_id=getattr(method, "message_id", None) or self.n, date=now,
                           chat=Chat(id=chat_id, type="private"), text=getattr(method, "text", None) or "x").as_(bot)

    uid = next(iter(settings.admin_ids))
    session = TgSession()
    bot = Bot("8888548766:TEST", session=session)
    dp = build_dispatcher()
    state = dp.fsm.get_context(bot=bot, chat_id=uid, user_id=uid)
    counter = iter(range(10_000, 100_000))
    sender = User(id=uid, is_bot=False, first_name="T")

    async def send(**kw):
        start = len(session.calls)
        message = Message(message_id=next(counter), date=now, chat=Chat(id=uid, type="private"), from_user=sender, **kw)
        await dp.feed_update(bot, Update(update_id=next(counter), message=message))
        return session.calls[start:]

    async def press(data: str):
        start = len(session.calls)
        query = CallbackQuery(id=str(next(counter)), from_user=sender, chat_instance="ci", data=data,
                              message=Message(message_id=next(counter), date=now, chat=Chat(id=uid, type="private"),
                                              text="x"))
        await dp.feed_update(bot, Update(update_id=next(counter), callback_query=query))
        return session.calls[start:]

    def texts(calls) -> list[str]:
        return [c.text for c in calls if type(c).__name__ in ("SendMessage", "EditMessageText")]

    def buttons(calls) -> list[str]:
        out = []
        for c in calls:
            markup = getattr(c, "reply_markup", None)
            for row in getattr(markup, "inline_keyboard", None) or []:
                out += [b.callback_data for b in row]
        return out

    def documents(calls):
        return [c for c in calls if type(c).__name__ == "SendDocument"]

    async def set_language(code: str) -> str:
        from db import crud
        async with get_session() as s:
            user = await crud.get_user(s, uid)
            if user is None:
                user = await crud.get_or_create_admin(s, uid, "tester", "T")
                user.coin_balance = 10_000
            user.language = code
            await s.commit()
        return code

    async def balance() -> int:
        async with get_session() as s:
            return (await s.execute(select(DbUser.coin_balance).where(DbUser.id == uid))).scalar()

    async def submissions() -> int:
        async with get_session() as s:
            return len((await s.execute(select(SpeakingSubmission.id).where(SpeakingSubmission.user_id == uid))).all())

    async def last_submission():
        async with get_session() as s:
            return (await s.execute(select(SpeakingSubmission).where(SpeakingSubmission.user_id == uid)
                                    .order_by(SpeakingSubmission.id.desc()))).scalars().first()

    piece_n = iter(range(1, 1000))

    def doc(data: bytes, name: str = "piece.wav", mime: str = "audio/wav", file_id: str | None = None) -> Document:
        file_id = file_id or f"rec{next(piece_n)}"
        files[file_id] = data
        return Document(file_id=file_id, file_unique_id="u" + file_id, file_name=name, mime_type=mime,
                        file_size=len(data))

    async def send_pieces(pieces, shifted, duration):
        fake.lines, fake.duration = shifted, duration
        out = []
        for p in pieces:
            out += await send(document=doc(p))
        return out

    L = await set_language("uz")
    p1 = ["Where are you from?", "Do you like living there?", "What do you do in your free time?",
          "Do you prefer mornings or evenings? Why?"]
    p1_text = "\n".join(p1)

    def part1_lines(tag: str, skip: int | None = None):
        script = [
            ("I'm from Samarkand, a very old city in Uzbekistan.", "It is famous for its beautiful historical buildings."),
            ("Yes, I really like living there.", "My family and my best friends all live nearby."),
            ("In my free time I usually read books or play football.", f"Sometimes I go hiking with friends {tag}."),
            ("I definitely prefer mornings.", "I feel fresh and I can concentrate on my studies much better."),
        ]
        out, t0 = [], 1.0
        for q, (a, b) in enumerate(script, 1):
            if q == skip:
                t0 += 2.0
                continue
            out.append(line(a, t0, t0 + 3.5, "answer", q))
            out.append(line(b, t0 + 3.9, t0 + 7.9, "answer", q))
            t0 += 10.0
        return out

    # 1. Question by question still works (the regression).
    await press(CB_PART1)
    calls = await send(text=p1_text)
    check("[each] the questions are taken and the two ways offered",
          await state.get_state() == SpeakingStates.choosing_mode.state
          and {CB_MODE_EACH, CB_MODE_ONE} <= set(buttons(calls)), texts(calls)[-1:])
    calls = await press(CB_MODE_EACH)
    check("[each] question by question: Q1 asked", await state.get_state() == SpeakingStates.awaiting_answer.state
          and any("1/4" in x for x in texts(calls)), texts(calls))
    fake.lines = []
    for i in range(4):
        fake.answer = f"My answer number {i + 1} is about something I really enjoy {RUN}."
        calls = await send(document=doc(tone_wav(5.0, [(0.3, 4.3)], i), "voice.ogg" if False else "a.wav"))
    check("[each] all four answers graded, the report sent", len(documents(calls)) == 1 and graded
          and not graded[-1]["transcribed"] and len(graded[-1]["questions"]) == 4, texts(calls)[-2:])

    # 2. Part 1 in one recording, two pieces, a clear split.
    graded.clear()
    await press(CB_PART1)
    await send(text=p1_text)
    calls = await press(CB_MODE_ONE)
    check("[one] the question list shown and a recording asked for",
          await state.get_state() == SpeakingStates.awaiting_recording.state
          and all(q.split("?")[0] in texts(calls)[-1] for q in p1), texts(calls)[-1:])
    pieces, shifted, duration = recording(part1_lines(RUN), cuts=[20.5])
    before_coins = await balance()
    calls = await send_pieces(pieces, shifted, duration)
    check("[one] each piece acknowledged with the running total, the newest with 'finish'",
          len([x for x in texts(calls) if "bo'lak qabul qilindi" in x]) == 2 and "Jami" in texts(calls)[-1]
          and CB_REC_FINISH in buttons(calls[-3:]), texts(calls))
    fake.whisper_calls = fake.split_calls = 0
    calls = await press(CB_REC_FINISH)
    check("[one] graded straight away (a clear split is not shown)", len(documents(calls)) == 1
          and not any("ajratdim" in x for x in texts(calls)), texts(calls))
    check("[one] the whole recording read twice by Whisper, split by one call",
          (fake.whisper_calls, fake.split_calls) == (2, 1), (fake.whisper_calls, fake.split_calls))
    g = graded[-1]
    check("[one] four answers, each with its own transcript",
          g["transcribed"] and [a.text.split(".")[0] for a in g["audios"]] == [
              "I'm from Samarkand, a very old city in Uzbekistan", "Yes, I really like living there",
              "In my free time I usually read books or play football", "I definitely prefer mornings"],
          [a.text for a in g["audios"]])
    check("[one] each clip is just that answer (about 8 s), not the whole recording",
          all(6.5 < a.duration < 9.5 for a in g["audios"]), [round(a.duration, 1) for a in g["audios"]])
    check("[one] the gaps between answers are no pauses", sum(len(a.pauses) for a in g["audios"]) == 0,
          [[round(p.seconds, 1) for p in a.pauses] for a in g["audios"]])
    check("[one] the report says the answers were split from one recording",
          g["report"].notices == [one.NOTICE_ONE_RECORDING], g["report"].notices)
    sub = await last_submission()
    meta = (sub.report_data or {}).get("one_recording") or {}
    check("[one] saved: the split, the pieces, the questions as sent, the whole recording as audio",
          meta.get("pieces") == 2 and len(meta.get("split", [])) == 4 and meta.get("missing") == []
          and sub.report_data["questions"] == p1 and sub.audio_file_path.endswith(".mp3"), meta)
    check("[one] the split's cost is part of the check's cost",
          "split recording" in str(sub.report_data.get("cost")) or sub.report_data.get("cost") is not None)
    check("[one] coins only once the report is ready (as for any check)", await balance() <= before_coins)

    # 3. The very same pieces again: the saved report, nothing paid.
    await press(CB_PART1)
    await send(text=p1_text)
    await press(CB_MODE_ONE)
    await send_pieces(pieces, shifted, duration)
    fake.whisper_calls = 0
    calls = await press(CB_REC_FINISH)
    check("[again] the same recording: its saved report, no new reading", fake.whisper_calls == 0
          and len(documents(calls)) == 1 and "o'sha yozuv" in (documents(calls)[0].caption or ""),
          [d.caption for d in documents(calls)])

    # 4. A missing answer: the split is shown, 'javob topilmadi', confirmed; the question left out.
    graded.clear()
    await press(CB_PART1)
    await send(text=p1_text)
    await press(CB_MODE_ONE)
    pieces, shifted, duration = recording(part1_lines(RUN + "m", skip=3))
    await send_pieces(pieces, shifted, duration)
    calls = await press(CB_REC_FINISH)
    shown = texts(calls)[-1] if texts(calls) else ""
    check("[missing] the split is shown first, Q3 'javob topilmadi'",
          await state.get_state() == SpeakingStates.confirming_split.state and "Q3: javob topilmadi" in shown
          and {CB_SPLIT_OK, CB_SPLIT_FIX} <= set(buttons(calls)), shown)
    calls = await press(CB_SPLIT_OK)
    g = graded[-1]
    check("[missing] graded without the missing question - nothing invented",
          len(documents(calls)) == 1 and [q["text"] for q in g["questions"]] == [p1[0], p1[1], p1[3]],
          [q["text"] for q in g["questions"]])
    check("[missing] the report names the question with no answer",
          g["report"].notices[1:] == [one.NOTICE_NO_ANSWER.format(question=p1[2])], g["report"].notices)

    # 5. An uncertain split corrected with the student's own times.
    graded.clear()
    fake.confidence = "low"
    await press(CB_PART1)
    await send(text=p1_text)
    await press(CB_MODE_ONE)
    pieces, shifted, duration = recording(part1_lines(RUN + "f"))
    await send_pieces(pieces, shifted, duration)
    calls = await press(CB_REC_FINISH)
    check("[fix] an uncertain split is shown even with every answer found",
          await state.get_state() == SpeakingStates.confirming_split.state, texts(calls)[-1:])
    calls = await press(CB_SPLIT_FIX)
    check("[fix] the format for the times is explained",
          await state.get_state() == SpeakingStates.awaiting_split_fix.state and "Q1: 0:00–0:41" in texts(calls)[-1])
    calls = await send(text="it's wrong")
    check("[fix] unreadable times: asked again", texts(calls) == [t("speaking.one_split_bad", L)], texts(calls))
    calls = await send(text="Q4: -")
    check("[fix] the corrected split shown again to confirm", "Q4: javob topilmadi" in texts(calls)[-1]
          and await state.get_state() == SpeakingStates.confirming_split.state, texts(calls)[-1:])
    calls = await press(CB_SPLIT_OK)
    check("[fix] graded with the student's split", len(graded[-1]["questions"]) == 3 and len(documents(calls)) == 1)
    fake.confidence = "high"

    # 6. Part 2: the minute of preparation is no pause; the examiner's words are not the answer.
    graded.clear()
    card = "Describe a book you enjoyed reading.\nYou should say:\nwhat it was\nwho wrote it\nand explain why you enjoyed it."
    await press(CB_PART2)
    await send(text=card)
    await press(CB_MODE_ONE)
    lines2 = [line("Now I'm going to give you a topic. You have one minute to prepare.", 0.5, 5.5, "question"),
              line("All right, please start speaking now.", 64.0, 66.0, "question")]
    t0 = 67.5
    for k in range(8):
        lines2.append(line(f"This is sentence {k + 1} of my talk about a wonderful novel I read last summer {RUN}.",
                           t0, t0 + 6.0, "answer", 1))
        t0 += 6.6
    pieces, shifted, duration = recording(lines2)
    await send_pieces(pieces, shifted, duration)
    calls = await press(CB_REC_FINISH)
    a = graded[-1]["audios"][0] if graded else None
    check("[part2] graded; the clip is the long turn only (no minute of preparation)",
          a is not None and 50 < a.duration < 56 and len(documents(calls)) == 1, a and a.duration)
    check("[part2] no long pause found in it", a is not None and all(p.seconds < 3 for p in a.pauses),
          a and [round(p.seconds, 1) for p in a.pauses])
    check("[part2] the examiner's words are not in the answer", a is not None and "prepare" not in a.text
          and "start speaking" not in a.text, a and a.text[:80])

    # 7. Part 3 with the examiner's voice and no questions sent, in three pieces.
    graded.clear()
    calls = await press(CB_PART3)
    check("[examiner] the part screen offers a recording with the questions in it",
          CB_NO_QUESTIONS in buttons(calls))
    calls = await press(CB_NO_QUESTIONS)
    check("[examiner] a recording asked for; its questions will be found in it",
          await state.get_state() == SpeakingStates.awaiting_recording.state
          and texts(calls)[-1] == t("speaking.one_intro_noq", L))
    lines3 = [
        line("Good afternoon. Can you tell me your full name, please?", 0.5, 4.0, "other"),
        line("My name is Aziz Karimov.", 5.0, 7.0, "other"),
        line("Why do people read less these days?", 9.0, 11.5, "question", ask=True),
        line(f"I think people read less because phones take all their free time {RUN}.", 13.0, 18.0, "answer", 1),
        line("Should schools teach children to love reading?", 20.0, 23.0, "question", ask=True),
        line("Yes, schools should give children interesting books.", 24.5, 28.5, "answer", 2),
        line("Why?", 30.0, 30.6, "question"),
        line("Because a child who loves books learns everything more easily.", 32.0, 36.5, "answer", 2),
        line("Will printed books disappear in the future?", 38.5, 41.0, "question", ask=True),
        line("I don't think so, because many people still love paper books.", 42.5, 47.5, "answer", 3),
    ]
    pieces, shifted, duration = recording(lines3, cuts=[19.0, 37.5])
    await send_pieces(pieces, shifted, duration)
    calls = await press(CB_REC_FINISH)
    g = graded[-1] if graded else None
    check("[examiner] the examiner's three questions taken from the recording",
          g is not None and [q["text"] for q in g["questions"]] == [x["text"] for x in lines3 if x["ask"]],
          g and [q["text"] for q in g["questions"]])
    check("[examiner] the follow-up's answer stays with its question; the 'Why?' and the ID check are left out",
          g is not None and g["audios"][1].text == "Yes, schools should give children interesting books. Because a "
          "child who loves books learns everything more easily." and all(
              "Why?" not in a.text and "Aziz" not in a.text for a in g["audios"]), g and [a.text for a in g["audios"]])
    sub = await last_submission()
    check("[examiner] saved as questions taken from the recording",
          (sub.report_data.get("one_recording") or {}).get("questions_from_recording") is True)

    # 8. Too big, silent, not English, too long, too short.
    await press(CB_PART1)
    await send(text=p1_text)
    await press(CB_MODE_ONE)
    calls = await send(document=doc(b"x" * 10, file_id="too_big_1"))
    check("[limits] a file over 20 MB: told to send pieces or compress it",
          texts(calls) == [t("speaking.one_too_big", L)], texts(calls))
    count, coins = await submissions(), await balance()
    fake.lines, fake.duration = [], 30.0
    fake.lines = [line("", 0, 0)]  # Whisper hears nothing
    fake.lines[0]["text"] = ""
    await send(document=doc(tone_wav(40.0, [])))
    calls = await press(CB_REC_FINISH)
    check("[limits] a silent recording: told so, no coins, nothing saved, another recording asked for",
          t("speaking.one_no_speech", L) in texts(calls) and await submissions() == count
          and await balance() == coins and await state.get_state() == SpeakingStates.awaiting_recording.state,
          texts(calls))
    pieces, shifted, duration = recording(part1_lines(RUN + "u"))
    fake.language = "uzbek"
    await send_pieces(pieces, shifted, duration)
    calls = await press(CB_REC_FINISH)
    check("[limits] not English: told so, no coins, nothing saved",
          t("speaking.one_not_english", L) in texts(calls) and await submissions() == count, texts(calls))
    fake.language = "english"
    one.limits = lambda: {**real_limits(), "max_minutes": {"part1": 0.5, "part2": 5, "part3": 10, "full": 20}}
    handler.one.limits = one.limits
    calls = await send(document=doc(tone_wav(40.0, [(1, 39)])))
    check("[limits] a piece past the longest allowed recording is refused",
          "juda uzun" in (texts(calls) or [""])[-1] and not (await state.get_data()).get("pieces"), texts(calls))
    one.limits = handler.one.limits = real_limits
    pieces, shifted, duration = recording(part1_lines(RUN + "s")[:2])  # 8 s of answers for 4 questions
    await send_pieces(pieces, shifted, duration)
    calls = await press(CB_REC_FINISH)
    check("[limits] a recording too short for its questions: a warning, and the choice to go on",
          "juda qisqa" in texts(calls)[-1] and CB_REC_ANYWAY in buttons(calls)
          and await state.get_state() == SpeakingStates.awaiting_recording.state, texts(calls)[-1:])
    calls = await press(CB_REC_ANYWAY)
    check("[limits] ...going on anyway: the split is shown (three answers missing)",
          await state.get_state() == SpeakingStates.confirming_split.state, texts(calls)[-1:])

    # 9. All three interface languages.
    for code in ("ru", "en", "uz"):
        await set_language(code)
        await press(CB_PART3)
        calls = await press(CB_NO_QUESTIONS)
        check(f"[{code}] the texts in the student's language", texts(calls)[-1] == t("speaking.one_intro_noq", code))
    await state.clear()
    await redis().aclose()


async def main() -> int:
    part1_split()
    if "--split-only" not in sys.argv:
        await part2_bot()
    print("FAILURES:", fails or "none")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
