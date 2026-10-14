"""Pronunciation assessment with Azure AI Speech (Speaking report prompt,
Part 2.3; approved by the owner on 2026-10-13).

Each answer's audio is assessed against our own verbatim transcript as the
reference text, through the REST short-audio endpoint (no SDK): clips over
CHUNK_SECONDS are cut at the longest pause between words, since the endpoint
takes 30 seconds of audio at most. The result gives

  * accuracy, fluency, completeness and prosody scores (0-100), averaged
    over the clips by duration - evidence for the Pronunciation criterion
    and the Intonation box;
  * every word's accuracy score and error type - the "Words to practise"
    table takes the lowest of them.

Without AZURE_SPEECH_KEY/AZURE_SPEECH_REGION `assess_answers` returns None;
the report then hides the two pronunciation boxes and marks the
Pronunciation band "(estimated)".
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import wave
from dataclasses import dataclass, field

import httpx

from bot.config import settings

logger = logging.getLogger(__name__)

CHUNK_SECONDS = 28.0
TIMEOUT = 40
CONCURRENCY = 4
HESITATIONS = {"um", "umm", "uh", "uhm", "er", "erm", "hmm", "mm", "ah", "eh"}


@dataclass
class WordScore:
    word: str
    accuracy: float
    error_type: str
    phonemes: list[tuple[str, float]] = field(default_factory=list)  # (IPA phoneme, accuracy)


@dataclass
class Assessment:
    seconds: float
    accuracy: float
    fluency: float
    completeness: float
    prosody: float | None
    pron: float
    words: list[WordScore]


def configured() -> bool:
    return bool(settings.azure_speech_key and settings.azure_speech_region)


def _endpoint() -> str:
    return (
        f"https://{settings.azure_speech_region}.stt.speech.microsoft.com/speech/recognition/"
        "conversation/cognitiveservices/v1?language=en-US&format=detailed"
    )


def _wav_slice(wav: bytes, start: float, end: float) -> bytes:
    with wave.open(io.BytesIO(wav)) as src:
        rate, width, channels = src.getframerate(), src.getsampwidth(), src.getnchannels()
        src.setpos(int(max(0.0, start) * rate))
        frames = src.readframes(int(max(0.0, end - start) * rate))
    out = io.BytesIO()
    with wave.open(out, "wb") as dst:
        dst.setnchannels(channels)
        dst.setsampwidth(width)
        dst.setframerate(rate)
        dst.writeframes(frames)
    return out.getvalue()


def _chunks(words: list, duration: float) -> list[tuple[float, float, list]]:
    """(start, end, words) pieces of at most CHUNK_SECONDS, cut at the longest
    gap between words inside each window. `words` have .text/.start/.end."""
    if duration <= CHUNK_SECONDS or len(words) < 2:
        return [(0.0, duration, words)]
    pieces, i, start = [], 0, 0.0
    while i < len(words):
        window_end = start + CHUNK_SECONDS
        j = i
        while j < len(words) and words[j].end <= window_end:
            j += 1
        if j >= len(words):
            pieces.append((start, duration, words[i:]))
            break
        if j - i < 2:  # one very long word - just cut at the window
            j = i + 1
            cut = window_end
        else:
            k = max(range(i + 1, j), key=lambda k: words[k].start - words[k - 1].end)
            cut = (words[k - 1].end + words[k].start) / 2
            j = k
        pieces.append((start, cut, words[i:j]))
        start, i = cut, j
    return pieces


def _num(d: dict, key: str) -> float | None:
    """Score fields sit directly in the REST result, or under
    PronunciationAssessment in the SDK's JSON - read either."""
    value = d.get(key)
    if value is None:
        value = (d.get("PronunciationAssessment") or {}).get(key)
    return float(value) if value is not None else None


def _text(d: dict, key: str) -> str:
    return d.get(key) or (d.get("PronunciationAssessment") or {}).get(key) or ""


async def _assess_chunk(http: httpx.AsyncClient, wav: bytes, reference: str) -> dict | None:
    params = {
        "ReferenceText": reference,
        "GradingSystem": "HundredMark",
        "Granularity": "Phoneme",
        "Dimension": "Comprehensive",
        "EnableMiscue": False,
        "EnableProsodyAssessment": True,
        "PhonemeAlphabet": "IPA",
    }
    headers = {
        "Ocp-Apim-Subscription-Key": settings.azure_speech_key,
        "Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000",
        "Accept": "application/json",
        "Pronunciation-Assessment": base64.b64encode(json.dumps(params).encode()).decode(),
    }
    response = await http.post(_endpoint(), headers=headers, content=wav)
    response.raise_for_status()
    data = response.json()
    if data.get("RecognitionStatus") != "Success" or not data.get("NBest"):
        logger.warning("Azure assessment: %s", data.get("RecognitionStatus"))
        return None
    return data["NBest"][0]


async def _assess_clip(http, sem, wav: bytes, words: list, duration: float) -> Assessment | None:
    results = []
    for start, end, piece in _chunks(words, duration):
        reference = " ".join(w.text for w in piece if w.text.strip(".,!?;:").lower() not in HESITATIONS)
        if not reference.strip():
            continue
        async with sem:
            best = await _assess_chunk(http, _wav_slice(wav, start, end), reference)
        if best is not None:
            results.append((end - start, best))
    if not results:
        return None
    seconds = sum(s for s, _ in results)

    def avg(key: str) -> float | None:
        pairs = [(s, _num(b, key)) for s, b in results if _num(b, key) is not None]
        if not pairs:
            return None
        return sum(s * v for s, v in pairs) / sum(s for s, _ in pairs)

    word_scores = []
    for _, best in results:
        for w in best.get("Words") or []:
            accuracy = _num(w, "AccuracyScore")
            if accuracy is None:
                continue
            phonemes = [(p.get("Phoneme", ""), _num(p, "AccuracyScore") or 0.0) for p in w.get("Phonemes") or []]
            word_scores.append(WordScore(w.get("Word", ""), accuracy, _text(w, "ErrorType") or "None", phonemes))
    return Assessment(
        seconds=seconds,
        accuracy=avg("AccuracyScore") or 0.0,
        fluency=avg("FluencyScore") or 0.0,
        completeness=avg("CompletenessScore") or 0.0,
        prosody=avg("ProsodyScore"),
        pron=avg("PronScore") or 0.0,
        words=word_scores,
    )


def combine(clips: list[Assessment]) -> Assessment:
    """One session-level assessment: scores weighted by assessed seconds."""
    seconds = sum(c.seconds for c in clips) or 1.0

    def avg(get) -> float | None:
        pairs = [(c.seconds, get(c)) for c in clips if get(c) is not None]
        return sum(s * v for s, v in pairs) / sum(s for s, _ in pairs) if pairs else None

    return Assessment(
        seconds=seconds,
        accuracy=avg(lambda c: c.accuracy) or 0.0,
        fluency=avg(lambda c: c.fluency) or 0.0,
        completeness=avg(lambda c: c.completeness) or 0.0,
        prosody=avg(lambda c: c.prosody),
        pron=avg(lambda c: c.pron) or 0.0,
        words=[w for c in clips for w in c.words],
    )


async def assess_answers(answers: list[tuple[bytes, list, float]]) -> Assessment | None:
    """`answers`: (16 kHz mono WAV, transcript words, duration) per answer.
    None when Azure isn't configured or nothing could be assessed - the
    report then falls back to an estimated Pronunciation band."""
    if not configured():
        return None
    sem = asyncio.Semaphore(CONCURRENCY)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as http:
            clips = await asyncio.gather(
                *(_assess_clip(http, sem, wav, words, duration) for wav, words, duration in answers),
                return_exceptions=True,
            )
    except Exception:
        logger.exception("Azure pronunciation assessment failed")
        return None
    good = []
    for clip in clips:
        if isinstance(clip, BaseException):
            logger.warning("Azure assessment of one answer failed: %r", clip)
        elif clip is not None:
            good.append(clip)
    if not good:
        return None
    result = combine(good)
    logger.info(
        "Azure pronunciation: accuracy %.0f fluency %.0f prosody %s completeness %.0f over %.0fs",
        result.accuracy, result.fluency,
        "-" if result.prosody is None else f"{result.prosody:.0f}", result.completeness, result.seconds,
    )
    return result


# --- "Words to practise" ---------------------------------------------------------------------------

PRACTISE_MAX = 6
PRACTISE_MIN = 3
PRACTISE_BELOW = 90  # only words scored under this are worth practising


def lowest_words(assessment: Assessment) -> list[WordScore]:
    """The lowest-scoring real words, one entry per word (its lowest score)."""
    best: dict[str, WordScore] = {}
    for w in assessment.words:
        word = w.word.strip(".,!?;:'\"").lower()
        if len(word) < 3 or not word.isalpha() or word in HESITATIONS or w.error_type in ("Omission", "Insertion"):
            continue
        if word not in best or w.accuracy < best[word].accuracy:
            best[word] = w
    ranked = sorted(best.values(), key=lambda w: w.accuracy)
    low = [w for w in ranked if w.accuracy < PRACTISE_BELOW]
    return (low if len(low) >= PRACTISE_MIN else ranked[:PRACTISE_MIN] if low else [])[:PRACTISE_MAX]


async def words_to_practise(assessment: Assessment | None) -> list[dict] | None:
    """Rows for the report's table: word, IPA, a tip about the weakest sound
    or the stress, and the score. One cheap model call writes the IPA and tips."""
    from pydantic import BaseModel

    from services.ai import usage
    from services.ai.openai_client import client

    if assessment is None:
        return None
    words = lowest_words(assessment)
    if not words:
        return []

    class Tip(BaseModel):
        word: str
        ipa: str
        tip: str

    class Tips(BaseModel):
        words: list[Tip]

    listed = "\n".join(
        f"- {w.word.lower()}: weakest sounds "
        + (", ".join(f"/{p}/ {s:.0f}" for p, s in sorted(w.phonemes, key=lambda x: x[1])[:3]) or "unknown")
        for w in words
    )
    rows = {w.word.lower(): w for w in words}
    tips: dict[str, Tip] = {}
    try:
        response = await client.beta.chat.completions.parse(
            model=settings.openai_model_sample,
            messages=[
                {"role": "system", "content": "For each English word below (an ESL learner mispronounced it; "
                 "the weakest sounds a speech scorer found are given), return its British IPA with stress "
                 "marks and syllable dots, like /æmˈbɪʃ.əs/, and ONE short practical tip (12 words at most) "
                 "about the weak sound or the word stress, e.g. \"Stress the 2nd syllable: am-BI-tious.\" or "
                 "\"Don't drop the final /z/.\""},
                {"role": "user", "content": listed},
            ],
            response_format=Tips,
            extra_body={"reasoning_effort": "low"},
        )
        usage.record("practise_words", response)
        parsed = response.choices[0].message.parsed
        tips = {t.word.strip().lower(): t for t in (parsed.words if parsed else [])}
    except Exception:
        logger.exception("Describing the words to practise failed")
    out = []
    for key, w in rows.items():
        tip = tips.get(key)
        if tip is None:
            continue  # never show a word without its IPA and tip
        score = round(w.accuracy)
        out.append({"word": key, "ipa": tip.ipa.strip(), "tip": tip.tip.strip(), "score": score,
                    "cls": "lo" if score < 65 else "mid"})
    return out
