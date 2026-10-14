"""Voice analysis for the Speaking report (Speaking report prompt, Part 2):
what the student said, verbatim, and how - pace, pauses and fillers,
measured from the recording rather than guessed from the words.

Transcription. Every answer is its own Telegram voice message, so the
bot's question/answer boundaries already split a session per question.
Each clip is converted to 16 kHz mono WAV and transcribed by Whisper
(whisper-1) twice, in parallel:

  * verbatim: with a prompt full of fillers ("Umm, well, like, uh..."), which
    keeps the "um"s, "uh"s and repeats Whisper otherwise deletes, plus word
    timestamps - this is the transcript the report shows;
  * plain: no prompt - a second, independent reading.

Whisper's API gives no per-word probability. A word of the verbatim
transcript that the plain reading doesn't confirm counts as low confidence
(LOW_CONFIDENCE, under the report's 0.6 limit) - that is exactly where the
two disagree on hard-to-hear words ("it's all my time" / "it's not my
time"), and no mistake is ever marked there. On 2026-10-13 this beat
faster-whisper small.en ("a sexy little person" for "a cybersecurity
person") and gpt-4o(-mini)-transcribe (dropped fillers, cut answers short,
"I can say I am" for "I can't say I am") on the reference session.

Pauses come from the audio itself (ffmpeg silencedetect, threshold set
relative to the clip's own loudness): Whisper's word timestamps stretch
words over short silences.

Fillers are found in code: hesitation sounds always count; words that are
only sometimes fillers ("like", "so", "well", "kind of"...) are decided by
rules where the grammar is clear ("I like", "as well", "kind of person")
and by one cheap model call for the rest ("like a dentist" is a
comparison, "lost like a part" is a filler).
"""
from __future__ import annotations

import asyncio
import difflib
import io
import json
import logging
import re
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from bot.config import settings
from services.ai import usage
from services.ai.openai_client import client

logger = logging.getLogger(__name__)

FILLER_PROMPT = "Umm, well, like, I mean, uh... so, you know, er, yeah. Hmm, I'd say, uh, it's kind of, um, hard."
LOW_CONFIDENCE = 0.4
HIGH_CONFIDENCE = 0.9
CONFIDENCE_LIMIT = 0.6  # no mistake is marked on a word below this
LONG_PAUSE = 1.0  # seconds
NATURAL_WPM = (110, 160)

TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*%?")
HESITATIONS = {"um", "umm", "uh", "uhm", "uhh", "er", "erm", "hmm", "mm", "mmm", "ah", "eh"}
# The fillers counted on the voice page (spec Part 2.2), hesitation sounds
# folded into "um"/"uh"/"er".
MAYBE_FILLERS_1 = {"like", "so", "well", "yeah", "basically", "actually", "literally", "okay", "ok"}
MAYBE_FILLERS_2 = {("you", "know"), ("i", "mean"), ("kind", "of"), ("sort", "of")}
_HESITATION_NAME = {"umm": "um", "uhm": "um", "uhh": "uh", "erm": "er", "mmm": "mm"}


# --- Transcripts ---------------------------------------------------------------------------


@dataclass
class Token:
    text: str
    start_char: int
    end_char: int
    start: float | None = None  # seconds into the clip
    end: float | None = None
    conf: float = HIGH_CONFIDENCE

    @property
    def norm(self) -> str:
        return re.sub(r"[^a-z0-9]", "", self.text.lower())


@dataclass
class Filler:
    start_char: int
    end_char: int
    name: str  # "like", "um", "you know", ...


@dataclass
class Pause:
    start: float
    end: float
    mid_sentence: bool
    before: str  # the word after the pause ("" at the end)

    @property
    def seconds(self) -> float:
        return self.end - self.start


@dataclass
class AnswerAudio:
    text: str  # the verbatim transcript, shown in the report
    tokens: list[Token]
    duration: float  # the clip's length
    no_speech: bool = False
    fillers: list[Filler] = field(default_factory=list)
    pauses: list[Pause] = field(default_factory=list)

    @property
    def speaking_seconds(self) -> float:
        timed = [t for t in self.tokens if t.start is not None and t.end is not None]
        if not timed:
            return 0.0
        return max(0.0, timed[-1].end - timed[0].start)

    @property
    def word_count(self) -> int:
        return sum(t.norm not in HESITATIONS for t in self.tokens)

    def low_confidence_spans(self) -> list[tuple[int, int]]:
        return [(t.start_char, t.end_char) for t in self.tokens if t.conf < CONFIDENCE_LIMIT]


async def to_wav16k(audio_bytes: bytes, filename: str) -> bytes:
    """Any voice/audio/video Telegram sends -> 16 kHz mono 16-bit WAV."""
    suffix = Path(filename).suffix.lower() or ".ogg"
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"input{suffix}"
        dst = Path(tmp) / "output.wav"
        src.write_bytes(audio_bytes)
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "pcm_s16le", str(dst),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        if proc.returncode != 0 or not dst.exists():
            raise RuntimeError("ffmpeg could not convert the audio to WAV")
        return dst.read_bytes()


def wav_seconds(wav: bytes) -> float:
    import wave

    with wave.open(io.BytesIO(wav)) as w:
        return w.getnframes() / float(w.getframerate() or 16000)


async def _whisper(wav: bytes, prompt: str | None, timestamps: bool):
    kwargs = dict(model=settings.openai_model_transcribe, file=("answer.wav", io.BytesIO(wav)),
                  response_format="verbose_json", language="en")
    if timestamps:
        kwargs["timestamp_granularities"] = ["word", "segment"]
    if prompt:
        kwargs["prompt"] = prompt
    response = await client.audio.transcriptions.create(**kwargs)
    usage.record_minutes(
        "transcribe" if prompt else "transcribe_check", settings.openai_model_transcribe,
        float(getattr(response, "duration", 0) or 0),
    )
    return response


def _tokens(text: str) -> list[Token]:
    return [Token(m.group(), m.start(), m.end()) for m in TOKEN_RE.finditer(text)]


def _norm(word: str) -> str:
    return re.sub(r"[^a-z0-9]", "", word.lower())


def _attach_times(tokens: list[Token], words: list) -> None:
    """Gives each token of the punctuated text the timing of the matching
    Whisper word (aligned by spelling); unmatched tokens get their
    neighbours' times."""
    a = [t.norm for t in tokens]
    b = [_norm(getattr(w, "word", "")) for w in words]
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            w = words[block.b + k]
            tokens[block.a + k].start, tokens[block.a + k].end = float(w.start), float(w.end)
    # fill gaps from neighbours
    last_end = 0.0
    for i, t in enumerate(tokens):
        if t.start is None:
            nxt = next((u.start for u in tokens[i + 1 :] if u.start is not None), None)
            t.start = last_end
            t.end = nxt if nxt is not None and nxt >= last_end else last_end
        last_end = t.end or last_end


# Words whose disagreement between the two readings is grammar, not hearing:
# the plain reading tends to "correct" the student ("I visit" -> "I visited",
# "a heart" -> "the heart"), so these never make a word uncertain.
_GRAMMAR_WORDS = {
    "a", "an", "the", "in", "on", "at", "to", "of", "for", "with", "by", "from", "is", "are", "was", "were",
    "am", "be", "been", "do", "does", "did", "have", "has", "had", "it", "its", "this", "that", "i", "im",
}


def _misheard(a: list[str], b: list[str]) -> bool:
    """Do two stretches that the readings disagree on sound like different
    words ("all" / "not"), rather than one being a grammar fix of the other?"""
    if not a or not b:
        return False  # one reading dropped or added words: fillers, repeats, articles
    joined_a, joined_b = "".join(a), "".join(b)
    if joined_a.isdigit() or joined_b.isdigit():
        return False  # "ten" / "10"
    if all(w in _GRAMMAR_WORDS for w in a + b):
        return False
    if joined_a.startswith(joined_b) or joined_b.startswith(joined_a):
        return False  # "visit" / "visited", "im" / "i am"
    return difflib.SequenceMatcher(None, joined_a, joined_b).ratio() < 0.6


def _set_confidence(tokens: list[Token], check_text: str, segments: list) -> None:
    """LOW_CONFIDENCE for words the two readings heard differently - a
    different word, not a grammar fix (see _misheard; checked on 2026-10-14:
    an earlier "any disagreement" rule hid the student's real grammar
    mistakes) - and for words in a segment Whisper itself scores very low.
    Whisper's no_speech_prob is not used: with the filler prompt it reported
    0.9 for whole stretches of clear speech."""
    check = [_norm(m.group()) for m in TOKEN_RE.finditer(check_text)]
    norms = [t.norm for t in tokens]
    matcher = difflib.SequenceMatcher(None, norms, check, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "replace" and _misheard(norms[i1:i2], check[j1:j2]):
            for t in tokens[i1:i2]:
                if t.norm not in HESITATIONS:
                    t.conf = LOW_CONFIDENCE
    for seg in segments or []:
        if (getattr(seg, "avg_logprob", 0) or 0) < -1.0:
            for t in tokens:
                if t.start is not None and seg.start - 0.05 <= t.start < seg.end + 0.05:
                    t.conf = min(t.conf, LOW_CONFIDENCE)


async def transcribe_answer(wav: bytes) -> AnswerAudio:
    duration = wav_seconds(wav)
    verbatim, plain = await asyncio.gather(
        _whisper(wav, FILLER_PROMPT, timestamps=True),
        _whisper(wav, None, timestamps=False),
    )
    text = (verbatim.text or "").strip()
    tokens = _tokens(text)
    segments = getattr(verbatim, "segments", None) or []
    no_speech = not tokens or (
        len(tokens) <= 3 and all((getattr(s, "no_speech_prob", 0) or 0) > 0.5 for s in segments)
    )
    if no_speech:
        return AnswerAudio(text="", tokens=[], duration=duration, no_speech=True)
    _attach_times(tokens, getattr(verbatim, "words", None) or [])
    _set_confidence(tokens, plain.text or "", segments)
    return AnswerAudio(text=text, tokens=tokens, duration=duration)


# --- Pauses --------------------------------------------------------------------------------------


FRAME = 0.05  # seconds per loudness frame
SPEECH_DROP_DB = 12.0  # a frame this far under the speaking level counts as silence


def find_silences(wav: bytes, min_seconds: float = LONG_PAUSE) -> list[tuple[float, float]]:
    """Silent stretches of at least `min_seconds`. "Silent" is judged against
    the clip's own speaking level (its 90th-percentile loudness), not a fixed
    dB value: phones record at very different levels and noise floors, and a
    breath between words is quiet but not silent (measured on the reference
    session 2026-10-13: speech -20..-25 dB, pauses -45..-60 dB). One loud
    frame inside a pause (a click) doesn't end it."""
    import wave

    import numpy as np

    with wave.open(io.BytesIO(wav)) as w:
        rate = w.getframerate()
        samples = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64)
    size = int(rate * FRAME)
    if size == 0 or len(samples) < size * 4:
        return []
    frames = samples[: len(samples) // size * size].reshape(-1, size)
    rms = np.sqrt((frames ** 2).mean(axis=1)) + 1e-9
    db = 20 * np.log10(rms / 32768.0)
    audible = db[db > -90]  # Telegram pads clips with digital silence
    if audible.size == 0:
        return []
    threshold = max(-50.0, min(-25.0, float(np.percentile(audible, 90)) - SPEECH_DROP_DB))
    quiet = db < threshold
    # a single loud frame between quiet ones doesn't break a pause
    for i in range(1, len(quiet) - 1):
        if not quiet[i] and quiet[i - 1] and quiet[i + 1]:
            quiet[i] = True
    silences, start = [], None
    for i, q in enumerate(list(quiet) + [False]):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if (i - start) * FRAME >= min_seconds:
                silences.append((start * FRAME, i * FRAME))
            start = None
    return silences


def place_pauses(answer: AnswerAudio, silences: list[tuple[float, float]]) -> list[Pause]:
    """Silences between the first and the last word, each marked as mid-
    sentence unless the word before it ends a sentence."""
    timed = [t for t in answer.tokens if t.start is not None]
    if len(timed) < 2:
        return []
    first, last = timed[0].start, timed[-1].end
    pauses = []
    for s, e in silences:
        s, e = max(s, first), min(e, last)
        if e - s < LONG_PAUSE:
            continue
        before = [t for t in timed if t.start < s + 0.15]
        after = [t for t in timed if t.start >= s + 0.15]
        if not before or not after:
            continue
        prev = before[-1]
        tail = answer.text[prev.end_char : prev.end_char + 3]
        pauses.append(Pause(s, e, mid_sentence=not re.match(r"\s*[.?!…]", tail), before=after[0].text))
    return pauses


# --- Intonation ------------------------------------------------------------------------------------
# Measured here, with no outside service: the voice's pitch (F0) in every
# 10 ms frame, by autocorrelation, in semitones around the speaker's own
# median pitch. How far it moves (the 10th-90th percentile span) says how
# flat or lively the intonation is; the longest stretch of continuous speech
# is drawn on the report as "your voice".

PITCH_RANGE_HZ = (75.0, 400.0)
PITCH_FRAME = 0.04
PITCH_HOP = 0.01


@dataclass
class Pitch:
    span: float  # semitones between the 10th and 90th percentile of the voiced frames
    contour: list[float]  # the longest voiced stretch, semitones, ~48 points
    voiced_seconds: float


def _pitch_track(wav: bytes) -> list[float | None]:
    """F0 in semitones relative to 100 Hz per 10 ms frame (None = unvoiced)."""
    import wave

    import numpy as np

    with wave.open(io.BytesIO(wav)) as w:
        rate = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64)
    size, hop = int(rate * PITCH_FRAME), int(rate * PITCH_HOP)
    if len(x) < size * 2:
        return []
    count = 1 + (len(x) - size) // hop
    frames = np.lib.stride_tricks.as_strided(x, shape=(count, size), strides=(x.strides[0] * hop, x.strides[0]))
    frames = (frames - frames.mean(axis=1, keepdims=True)) * np.hanning(size)
    spec = np.fft.rfft(frames, n=2 * size, axis=1)
    ac = np.fft.irfft(np.abs(spec) ** 2, axis=1)[:, :size]
    energy = ac[:, 0] + 1e-9
    lo, hi = int(rate / PITCH_RANGE_HZ[1]), int(rate / PITCH_RANGE_HZ[0])
    lags = np.argmax(ac[:, lo:hi], axis=1) + lo
    strength = ac[np.arange(count), lags] / energy
    db = 10 * np.log10(energy / size + 1e-9) - 20 * np.log10(32768.0)
    loud = db > np.percentile(db, 90) - 25
    voiced = (strength > 0.45) & loud
    semis = 12 * np.log2((rate / lags) / 100.0)
    return [float(s) if v else None for s, v in zip(semis, voiced)]


def measure_pitch(wavs: list[bytes]) -> Pitch | None:
    """The session's intonation. None when too little voiced speech was found."""
    import numpy as np

    tracks = [_pitch_track(w) for w in wavs]
    values = np.array([v for t in tracks for v in t if v is not None])
    if values.size < 150:  # under ~1.5 s of voiced speech
        return None
    median = float(np.median(values))
    rel = values - median
    rel = rel[np.abs(rel) < 12]  # octave errors
    span = float(np.percentile(rel, 90) - np.percentile(rel, 10))
    # the longest stretch with few unvoiced gaps, smoothed, for the sketch
    best: list[float] = []
    for t in tracks:
        run: list[float] = []
        gap = 0
        for v in t + [None] * 30:
            if v is not None and abs(v - median) < 12:
                run.append(v - median)
                gap = 0
            else:
                gap += 1
                if gap > 25:  # a quarter of a second of silence ends the stretch
                    if len(run) > len(best):
                        best = run
                    run = []
    if len(best) < 40:
        return None
    k = 7
    smooth = np.convolve(np.array(best), np.ones(k) / k, mode="valid")
    points = np.interp(np.linspace(0, len(smooth) - 1, 48), np.arange(len(smooth)), smooth)
    return Pitch(span=round(span, 1), contour=[round(float(p), 2) for p in points],
                 voiced_seconds=round(values.size * PITCH_HOP, 1))


# --- Fillers ---------------------------------------------------------------------------------------


@dataclass
class _Candidate:
    index: int
    start_char: int
    end_char: int
    name: str
    certain: bool | None  # True filler / False not / None ask the model


_VERB_LIKE_BEFORE = {
    "i", "you", "we", "they", "he", "she", "would", "d", "dont", "didnt", "doesnt", "do", "does", "did",
    "to", "really", "also", "not", "just", "still", "always", "never", "people", "who", "wouldnt", "might",
    "may", "will", "can", "could", "should", "kids", "children", "most", "many", "some",
}
_NOT_FILLER_AFTER_WELL = {"known", "being", "paid", "educated", "off", "done", "organised", "organized"}
_DETERMINERS = {"a", "an", "the", "this", "that", "my", "your", "his", "her", "our", "their", "these", "those"}


def _candidates(answer: AnswerAudio) -> list[_Candidate]:
    tokens, text = answer.tokens, answer.text
    out: list[_Candidate] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        w = t.norm
        prev = tokens[i - 1].norm if i else ""
        nxt = tokens[i + 1].norm if i + 1 < len(tokens) else ""
        comma_after = bool(re.match(r"[,…]|\.\.\.", text[t.end_char : t.end_char + 3]))
        sentence_start = i == 0 or bool(re.search(r"[.?!…,]\s*$", text[: t.start_char]))
        pair = (w, nxt)
        if w in HESITATIONS:
            out.append(_Candidate(i, t.start_char, t.end_char, _HESITATION_NAME.get(w, w), True))
        elif pair in MAYBE_FILLERS_2:
            end = tokens[i + 1].end_char
            name = f"{w} {nxt}"
            certain: bool | None = None
            if w in ("kind", "sort") and prev in _DETERMINERS | {"what", "which", "any", "every", "same"}:
                certain = False  # "the kind of person"
            elif w in ("kind", "sort") and re.match(r"\s*[,.?!…]", text[end : end + 2]):
                certain = False  # "Well, kind of, nope" - the answer itself ("partly")
            out.append(_Candidate(i, t.start_char, end, name, certain))
            i += 2
            continue
        elif w in MAYBE_FILLERS_1:
            certain = None
            if w == "like":
                if prev in _VERB_LIKE_BEFORE or nxt in ("to", "it", "them", "this", "that", "him", "her", "me", "us"):
                    certain = False  # "I like it", "would like to"
                elif comma_after and (sentence_start or prev in {"", "and", "but", "so", "just"}):
                    certain = True  # "Like, I can't say..."
            elif w == "well":
                if prev in {"as", "very", "quite", "pretty", "really", "so"} or nxt in _NOT_FILLER_AFTER_WELL:
                    certain = False
                elif sentence_start and comma_after:
                    certain = True
            elif w == "so":
                if nxt in {"much", "many", "that", "far", "long", "i", "we"} and not comma_after and not sentence_start:
                    certain = False
                elif sentence_start and comma_after:
                    certain = True
            elif w in ("yeah", "okay", "ok"):
                certain = None if i == 0 else True
            out.append(_Candidate(i, t.start_char, t.end_char, "okay" if w == "ok" else w, certain))
        i += 1
    return out


class _FillerCall(BaseModel):
    filler_ids: list[int]


async def _ask_model(answers: list[AnswerAudio], cands: list[list[_Candidate]], questions: list[str]) -> set[tuple[int, int]]:
    """(answer index, candidate index) pairs the model calls fillers."""
    lines, keys, n = [], {}, 0
    for a_idx, (answer, cs) in enumerate(zip(answers, cands)):
        ask = [c for c in cs if c.certain is None]
        if not ask:
            continue
        marked, pos = [], 0
        for c in ask:
            n += 1
            keys[n] = (a_idx, cs.index(c))
            marked.append(answer.text[pos : c.start_char] + f"[[{n}: {answer.text[c.start_char:c.end_char]}]]")
            pos = c.end_char
        marked.append(answer.text[pos:])
        lines.append(f"QUESTION: {questions[a_idx]}\nANSWER: {''.join(marked)}")
    if not keys:
        return set()
    system = (
        "You label filler words in transcripts of spoken IELTS answers. Each marked item [[n: word]] is "
        "a word that is sometimes a filler. It is a FILLER when it adds no meaning - a hesitation or "
        "discourse habit you could delete without changing the sentence (\"Like, I can't say\", \"lost "
        "like a part of\", \"It's, you know, hard\", \"So, my dream job\", \"and yeah, that's it\"). It is "
        "NOT a filler when it carries meaning: a comparison or example (\"like a dentist\", \"sports like "
        "football\"), a verb (\"I like it\"), a real answer (\"Yeah, I do\" answering yes; \"kind of\" or "
        "\"sort of\" given as the answer itself, meaning \"partly\" - \"Well, kind of, nope\", \"Kind "
        "of.\" - is NOT a filler), a result (\"so I left\"), a degree (\"so tired\", \"kind of tired\" "
        "meaning somewhat). When unsure, it is not a filler. Return the ids of the items that ARE "
        "fillers."
    )
    try:
        response = await client.beta.chat.completions.parse(
            model=settings.openai_model_sample,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": "\n\n".join(lines)}],
            response_format=_FillerCall,
            extra_body={"reasoning_effort": "low"},
        )
        usage.record("fillers", response)
        parsed = response.choices[0].message.parsed
        ids = set(parsed.filler_ids) if parsed else set()
    except Exception:
        logger.exception("Filler classification failed - using the rules only")
        return set()
    return {keys[i] for i in ids if i in keys}


async def classify_fillers(answers: list[AnswerAudio], questions: list[str]) -> None:
    """Fills each answer's `fillers`."""
    cands = [_candidates(a) for a in answers]
    yes = await _ask_model(answers, cands, questions)
    for a_idx, (answer, cs) in enumerate(zip(answers, cands)):
        answer.fillers = [
            Filler(c.start_char, c.end_char, c.name)
            for c_idx, c in enumerate(cs)
            if c.certain is True or (c.certain is None and (a_idx, c_idx) in yes)
        ]


# --- Metrics -----------------------------------------------------------------------------------------


@dataclass
class VoiceMetrics:
    speaking_seconds: float
    words: int
    wpm: int
    fillers: int
    filler_types: list[tuple[str, int]]  # most frequent first
    long_pauses: int
    mid_sentence_pauses: int
    longest_pause: Pause | None
    longest_pause_answer: int | None  # index of the answer it is in

    @property
    def fillers_per_minute(self) -> float:
        return self.fillers / (self.speaking_seconds / 60) if self.speaking_seconds else 0.0

    @property
    def pauses_per_minute(self) -> float:
        return self.long_pauses / (self.speaking_seconds / 60) if self.speaking_seconds else 0.0

    def as_json(self) -> dict:
        return {
            "speaking_seconds": round(self.speaking_seconds, 1), "words": self.words, "wpm": self.wpm,
            "fillers": self.fillers, "filler_types": self.filler_types,
            "fillers_per_minute": round(self.fillers_per_minute, 2),
            "long_pauses": self.long_pauses, "mid_sentence_pauses": self.mid_sentence_pauses,
            "pauses_per_minute": round(self.pauses_per_minute, 2),
            "longest_pause": round(self.longest_pause.seconds, 1) if self.longest_pause else None,
        }


def measure(answers: list[AnswerAudio]) -> VoiceMetrics:
    seconds = sum(a.speaking_seconds for a in answers)
    words = sum(a.word_count for a in answers)
    counts = Counter(f.name for a in answers for f in a.fillers)
    pauses = [(i, p) for i, a in enumerate(answers) for p in a.pauses]
    longest = max(pauses, key=lambda x: x[1].seconds, default=None)
    return VoiceMetrics(
        speaking_seconds=seconds,
        words=words,
        wpm=round(words / (seconds / 60)) if seconds else 0,
        fillers=sum(counts.values()),
        filler_types=counts.most_common(),
        long_pauses=len(pauses),
        mid_sentence_pauses=sum(p.mid_sentence for _, p in pauses),
        longest_pause=longest[1] if longest else None,
        longest_pause_answer=longest[0] if longest else None,
    )


async def analyse_answers(wavs: list[bytes], questions: list[str]) -> list[AnswerAudio]:
    """Transcribed answers with pauses and fillers, for 16 kHz WAVs
    (to_wav16k), one per question."""
    answers = list(await asyncio.gather(*(transcribe_answer(w) for w in wavs)))
    for answer, wav in zip(answers, wavs):
        answer.pauses = place_pauses(answer, find_silences(wav))
    await classify_fillers(answers, questions)
    logger.info(
        "Voice: %s",
        json.dumps([{"words": a.word_count, "secs": round(a.speaking_seconds, 1), "fillers": [f.name for f in a.fillers],
                     "pauses": [round(p.seconds, 1) for p in a.pauses],
                     "low_conf": [t.text for t in a.tokens if t.conf < CONFIDENCE_LIMIT]} for a in answers]),
    )
    return answers
