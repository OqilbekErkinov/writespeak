"""Speaking: one recording with all the answers (the last prompt's package D,
2026-10-26) - a mock test with the examiner's voice, or a self-recording.

    the pieces, joined into one 16 kHz WAV (join_wavs)
      -> Whisper twice over the whole recording (transcribe_recording): the
         verbatim reading with word times and the plain one, as
         voice.transcribe_answer does for each answer - so the split costs
         one cheap model call more than question-by-question answers
      -> sentence units with their times (units)
      -> one model call (align): is each unit a question (the examiner's, or
         the student reading one out), an answer, or neither (the ID check,
         small talk), and which question an answer answers; with no
         questions given, the examiner's questions are taken from the
         recording
      -> each question's answer as time spans (spans_of), its audio cut to
         just those spans (cut) and its transcript built from the readings
         above (answer_audio) - the gaps, the examiner's speech and the Part
         2 minute of preparation are never pauses
    and from there the same grading as question-by-question answers
    (speaking_check.run_check with `transcribed`).

An answer the recording doesn't have is never invented: the question is left
out of the grading and the report says so. When the split is uncertain
(problems), the student sees it ("Q1: 0:00–0:41 ...") and confirms it or
sends the right times (parse_split). The limits are the academic lead's, in
config/speaking_single.json.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import tempfile
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

from pydantic import BaseModel, Field

from bot.config import settings
from services.ai import usage
from services.ai.openai_client import client, parse_flex
from services.ai.voice import (
    FILLER_PROMPT,
    AnswerAudio,
    _attach_times,
    _set_confidence,
    _tokens,
)

logger = logging.getLogger(__name__)

CONFIG = Path(__file__).resolve().parents[2] / "config" / "speaking_single.json"
DEFAULTS = {
    "max_minutes": {"part1": 8, "part2": 5, "part3": 10, "full": 20},
    "min_seconds_per_question": {"part1": 8, "part2": 45, "part3": 12},
    "min_answer_words": {"part1": 5, "part2": 30, "part3": 8},
}
RATE = 16000  # voice.to_wav16k's 16 kHz mono 16-bit
GAP = 0.3  # seconds of silence between joined pieces, and between one answer's spans
PAD = 0.2  # seconds kept around an answer's words, never into the next speaker's
UNIT_GAP = 1.2  # a silence this long ends a sentence unit (a speaker change often has no full stop)
UNIT_MAX_WORDS = 40
MIN_WORDS = 5  # fewer in the whole recording: no speech
ENGLISH = {"english", "en"}
# The report is in English, like the rest of it.
NOTICE_ONE_RECORDING = ("Your answers were split from one recording. Only your own answers were graded and "
                        "measured: the examiner's voice and the gaps between answers were left out.")
NOTICE_NO_ANSWER = "No answer to this question was found in the recording, so it was not graded: “{question}”"


def limits() -> dict:
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Could not read %s - using the defaults", CONFIG)
        data = {}
    return {key: {**value, **data.get(key, {})} for key, value in DEFAULTS.items()}


# --- Audio ------------------------------------------------------------------------------------


def _frames(wav: bytes) -> bytes:
    with wave.open(io.BytesIO(wav)) as w:
        return w.readframes(w.getnframes())


def _wav(frames: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(frames)
    return buf.getvalue()


def _silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(RATE * seconds)


def join_wavs(wavs: list[bytes]) -> bytes:
    """The pieces in the order they came, a short silence between them."""
    return _wav(_silence(GAP).join(_frames(w) for w in wavs))


def cut(wav: bytes, spans: list[tuple[float, float]]) -> bytes:
    """Just the spans of a recording, joined with a short silence."""
    frames = _frames(wav)
    parts = [frames[int(s * RATE) * 2 : int(e * RATE) * 2] for s, e in spans]
    return _wav(_silence(GAP).join(parts))


def clip_time(spans: list[tuple[float, float]]):
    """Maps a time in the recording to the time in cut(wav, spans); a time
    outside every span goes to the nearest span's edge."""
    def to_clip(t: float) -> float:
        offset = 0.0
        for k, (s, e) in enumerate(spans):
            if t <= e or k == len(spans) - 1:
                return offset + min(max(t, s), e) - s
            offset += e - s + GAP
        return offset
    return to_clip


async def to_mp3(wav: bytes) -> bytes:
    """Mono 48 kbps: a 20-minute recording is about 7 MB, well under
    Whisper's 25 MB limit (the same recording as WAV is 38 MB)."""
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "in.wav", Path(tmp) / "out.mp3"
        src.write_bytes(wav)
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-v", "error", "-i", str(src), "-ac", "1", "-b:a", "48k", str(dst),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        if proc.returncode != 0 or not dst.exists():
            raise RuntimeError("ffmpeg could not encode the recording")
        return dst.read_bytes()


# --- Transcription ----------------------------------------------------------------------------


@dataclass
class Recording:
    """Both Whisper readings of the whole recording, kept as plain data (the
    student may confirm the split later, from a saved file)."""

    text: str  # the verbatim reading
    words: list[list]  # [word, start, end], verbatim
    segments: list[list]  # [start, end, avg_logprob], verbatim
    plain_words: list[list]  # [word, start, end], the plain reading
    language: str  # Whisper's guess, from the plain reading
    duration: float

    def as_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> Recording:
        return cls(**data)

    @property
    def has_speech(self) -> bool:
        return len(self.words) >= MIN_WORDS

    @property
    def english(self) -> bool:
        return self.language.lower() in ENGLISH


async def _whisper(mp3: bytes, prompt: str | None):
    kwargs = {"model": settings.openai_model_transcribe, "file": ("recording.mp3", io.BytesIO(mp3)),
              "response_format": "verbose_json", "timestamp_granularities": ["word", "segment"]}
    if prompt:
        kwargs["prompt"] = prompt  # no language is forced: a recording that isn't English must show up as such
    response = await client.audio.transcriptions.create(**kwargs)
    usage.record_minutes("transcribe" if prompt else "transcribe_check", settings.openai_model_transcribe,
                         float(getattr(response, "duration", 0) or 0))
    return response


def _words(response) -> list[list]:
    return [[w.word, float(w.start), float(w.end)] for w in getattr(response, "words", None) or []]


async def transcribe_recording(wav: bytes) -> Recording:
    mp3 = await to_mp3(wav)
    verbatim, plain = await asyncio.gather(_whisper(mp3, FILLER_PROMPT), _whisper(mp3, None))
    return Recording(
        text=(verbatim.text or "").strip(),
        words=_words(verbatim),
        segments=[[float(s.start), float(s.end), float(getattr(s, "avg_logprob", 0) or 0)]
                  for s in getattr(verbatim, "segments", None) or []],
        plain_words=_words(plain),
        language=str(getattr(plain, "language", "") or getattr(verbatim, "language", "") or "english"),
        duration=len(_frames(wav)) / 2 / RATE,
    )


def _timed_tokens(rec: Recording):
    """The verbatim reading's words with their times - worked out once per
    recording (a full test has a few thousand words; each answer and each
    check of the split needs them)."""
    cached = rec.__dict__.get("_tokens")
    if cached is None:
        cached = _tokens(rec.text)
        _attach_times(cached, [SimpleNamespace(word=w, start=s, end=e) for w, s, e in rec.words])
        rec.__dict__["_tokens"] = cached
    return cached


# --- Sentence units ---------------------------------------------------------------------------


@dataclass
class Unit:
    start: float
    end: float
    text: str
    words: int


def units(rec: Recording) -> list[Unit]:
    """The verbatim reading cut into sentences - and at every long silence,
    where the speaker often changes without a full stop."""
    tokens = _timed_tokens(rec)
    out, begin = [], 0
    for i, tok in enumerate(tokens):
        last = i == len(tokens) - 1
        stop = re.match(r"[.?!…]+", rec.text[tok.end_char:])
        gap = not last and (tokens[i + 1].start or 0) - (tok.end or 0) >= UNIT_GAP
        if last or stop or gap or i + 1 - begin >= UNIT_MAX_WORDS:
            end_char = tok.end_char + (stop.end() if stop else 0)
            out.append(Unit(tokens[begin].start or 0.0, tok.end or 0.0,
                            rec.text[tokens[begin].start_char:end_char].strip(), i + 1 - begin))
            begin = i + 1
    return out


# --- Alignment --------------------------------------------------------------------------------


class _UnitLabel(BaseModel):
    unit: int
    role: Literal["question", "answer", "other"]
    question: int = Field(description="answer units: the number of the question it answers; 0 otherwise.")


class _AskedQuestion(BaseModel):
    text: str = Field(description="The question as the examiner asked it, without fillers.")
    part: Literal["part1", "part2", "part3"]


class _Alignment(BaseModel):
    asked: list[_AskedQuestion] = Field(
        description="Only when NO questions are given: the examiner's questions in the order asked, "
        "numbered from 1 by their position here (a Part 2 cue card is one question, its rounding-off "
        "questions included); [] when questions are given.")
    labels: list[_UnitLabel]
    confidence: Literal["high", "low"]
    doubt: str = Field(description="'' when confident; else what is unclear, one line.")


ALIGN_SYSTEM = """You split the recording of an IELTS Speaking test into the candidate's answers. \
The recording is given as numbered sentence units with their times. It may be a mock test with \
an examiner's voice, or the candidate alone (who may read each question out before answering).

Label EVERY unit:
- role "question": an examiner's question, follow-up or instruction ("Why?", "Can you give an \
example?", "You have one minute to prepare", "Thank you"), or the candidate reading a question out. \
question = 0.
- role "answer": the candidate's own words answering a question. question = the number of the \
question it answers.
- role "other": anything that is not part of the test answers - the greeting, the name and ID \
check, small talk, the candidate talking to someone else. question = 0.
Rules:
- An answer to a follow-up ("Why?", "How about your friends?") belongs to the question being \
discussed. In Part 2 the long turn answers the cue card; the examiner's one or two rounding-off \
questions after it, and their short answers, belong to the cue card too.
- Match questions by meaning, not exact words: the examiner may word a listed question \
differently.
- An answer to a question that is NOT in the list (when a list is given) is role "answer" with \
question 0.
- Never assign an answer to a question it doesn't answer to fill a gap: a question with no \
answer simply has none.
- confidence "low" when you are unsure where any answer starts or ends, who is speaking, or which \
question an answer belongs to."""


def clock(seconds: float) -> str:
    """65.4 seconds as "1:05"."""
    seconds = max(0.0, seconds)
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


async def align(found: list[Unit], questions: list[str], parts: list[str], part: str) -> _Alignment | None:
    """None when the model call fails (the student is then shown the split
    as unknown and may send the times)."""
    if questions:
        listed = "\n".join(f"Q{i} [{p.replace('part', 'Part ')}]: {q}"
                           for i, (q, p) in enumerate(zip(questions, parts), 1))
        head = f"QUESTIONS (in the order they were asked):\n{listed}"
    else:
        scope = "a full test (Part 1, Part 2 and Part 3)" if part == "full" else part.replace("part", "Part ")
        head = (f"NO QUESTIONS ARE GIVEN: this is {scope}. List the examiner's test questions in `asked` "
                "(not the greeting or the ID check) and number the answers by that list.")
    lines = "\n".join(f"U{i} [{clock(u.start)}-{clock(u.end)}] {u.text}" for i, u in enumerate(found, 1))
    try:
        response = await parse_flex(
            model=settings.openai_model_sample,
            messages=[{"role": "system", "content": ALIGN_SYSTEM},
                      {"role": "user", "content": f"{head}\n\nRECORDING:\n{lines}"}],
            response_format=_Alignment,
            extra_body={"reasoning_effort": "medium"},
        )
        usage.record("split recording", response)
        return response.choices[0].message.parsed
    except Exception:
        logger.exception("Splitting the recording failed")
        return None


def spans_of(found: list[Unit], labels: list[_UnitLabel], count: int) -> list[list[tuple[float, float]]]:
    """Each question's answer as time spans: its answer units, neighbouring
    ones joined, a little padding that never reaches another unit."""
    by_unit = {x.unit: x for x in labels}
    out: list[list[tuple[float, float]]] = [[] for _ in range(count)]
    run: tuple[int, int, int] | None = None  # (question, first unit, last unit), 0-based units

    def close(r):
        q, a, b = r
        lo = found[a - 1].end if a > 0 else 0.0
        hi = found[b + 1].start if b + 1 < len(found) else found[b].end + PAD
        out[q - 1].append((round(max(lo, found[a].start - PAD), 2), round(min(hi, found[b].end + PAD), 2)))

    for i in range(len(found)):
        label = by_unit.get(i + 1)
        q = label.question if label and label.role == "answer" and 1 <= label.question <= count else 0
        if run and q == run[0]:
            run = (q, run[1], i)
            continue
        if run:
            close(run)
        run = (q, i, i) if q else None
    if run:
        close(run)
    return out


def answer_audio(rec: Recording, spans: list[tuple[float, float]]) -> AnswerAudio:
    """One question's transcript from the readings of the whole recording:
    its words in their spans, times as in cut(wav, spans), each word's
    confidence from the plain reading of the same spans."""
    tokens = _timed_tokens(rec)

    def inside(start, end) -> bool:
        mid = ((start or 0) + (end or 0)) / 2
        return any(s <= mid <= e for s, e in spans)

    to_clip = clip_time(spans)
    duration = sum(e - s for s, e in spans) + GAP * max(0, len(spans) - 1)
    chosen = [i for i, t in enumerate(tokens) if inside(t.start, t.end)]
    if not chosen:
        return AnswerAudio(text="", tokens=[], duration=duration, no_speech=True)
    # runs of neighbouring words, each copied with the punctuation that ends it
    pieces, times, run = [], [], [chosen[0]]
    for i in chosen[1:] + [None]:
        if i is not None and i == run[-1] + 1:
            run.append(i)
            continue
        first, last = tokens[run[0]], tokens[run[-1]]
        stop = re.match(r"[.?!…,;:]+", rec.text[last.end_char:])
        pieces.append(rec.text[first.start_char:last.end_char + (stop.end() if stop else 0)].strip())
        times += [(to_clip(tokens[k].start or 0), to_clip(tokens[k].end or 0)) for k in run]
        run = [i] if i is not None else []
    text = " ".join(pieces)
    words = _tokens(text)
    if len(words) == len(times):
        for w, (s, e) in zip(words, times):
            w.start, w.end = s, e
    else:  # never expected: the same words were re-read - time them by spelling instead
        _attach_times(words, [SimpleNamespace(word=tokens[k].text, start=to_clip(tokens[k].start or 0),
                                              end=to_clip(tokens[k].end or 0)) for k in chosen])
    plain = " ".join(w for w, s, e in rec.plain_words if inside(s, e))
    segments = [SimpleNamespace(start=to_clip(s), end=to_clip(e), avg_logprob=lp)
                for s, e, lp in rec.segments if inside(s, e)]
    _set_confidence(words, plain, segments)
    return AnswerAudio(text=text, tokens=words, duration=duration)


def problems(spans: list[list[tuple[float, float]]], result: _Alignment | None, rec: Recording,
             parts: list[str]) -> list[str]:
    """Why the student should see the split before grading; [] when it is clear."""
    if result is None:
        return ["the split could not be made"]
    out = []
    if result.confidence == "low":
        out.append(result.doubt.strip() or "the model was unsure")
    missing = [i for i, s in enumerate(spans, 1) if not s]
    if missing:
        out.append("no answer found for " + ", ".join(f"Q{i}" for i in missing))
    starts = [(s[0][0], i) for i, s in enumerate(spans) if s]
    if [i for _, i in sorted(starts)] != [i for _, i in starts]:
        out.append("the answers are not in the order of the questions")
    least = limits()["min_answer_words"]
    for i, (s, p) in enumerate(zip(spans, parts), 1):
        if s and answer_audio(rec, s).word_count < least.get(p, 0):
            out.append(f"the answer to Q{i} is very short")
    return out


# --- Showing and correcting the split ---------------------------------------------------------


def format_split(spans: list[list[tuple[float, float]]], questions: list[str], missing_word: str) -> str:
    lines = []
    for i, (s, q) in enumerate(zip(spans, questions), 1):
        times = ", ".join(f"{clock(a)}–{clock(b)}" for a, b in s) if s else missing_word
        short = q if len(q) <= 70 else q[:67].rstrip() + "…"
        lines.append(f"Q{i}: {times} — {short}")
    return "\n".join(lines)


_LINE = re.compile(r"^\s*Q?\s*(\d{1,2})\s*[:.)\-]?\s*(.*)$", re.IGNORECASE)
_NO_ANSWER = re.compile(r"\s*(?:[-–—]|yo'?q\b|нет\b|none\b|no answer|javob topilmadi|ответ не найден)",
                        re.IGNORECASE)
_RANGE = re.compile(r"(\d{1,2}):(\d{2})(?:[.,]\d+)?\s*[–—-]\s*(\d{1,2}):(\d{2})(?:[.,]\d+)?")


def parse_split(text: str, count: int, duration: float,
                current: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]] | None:
    """The student's corrected times ("Q2: 0:45–1:30, 1:35–1:50"; "Q3: -" for
    no answer). Questions not mentioned keep their current spans; None when
    no line can be read."""
    out = [list(s) for s in current]
    read = False
    for line in text.splitlines():
        m = _LINE.match(line)
        if not m or not 1 <= int(m.group(1)) <= count:
            continue
        rest = m.group(2)
        ranges = [(int(a) * 60 + int(b), int(c) * 60 + int(d)) for a, b, c, d in _RANGE.findall(rest)]
        ranges = [(float(s), float(min(e, duration + 1))) for s, e in ranges if e > s and s < duration + 1]
        if ranges:
            out[int(m.group(1)) - 1] = sorted(ranges)
            read = True
        elif _NO_ANSWER.match(rest) and not re.search(r"\d:\d", rest):
            out[int(m.group(1)) - 1] = []
            read = True
    return out if read else None
