"""Speaking (Part 1 / 2 / 3) grading against the IELTS Speaking Band Descriptors.

Two-step design, confirmed against a live key on 2026-08-28:

1. `_listen_to_audio` sends the raw audio to `settings.openai_model_audio`
   (an audio-capable model) and asks for a free-text description of HOW the
   student speaks - pronunciation, pacing, hesitation - straight from the
   actual sound, not just the transcript.
2. `_grade_with_audio_notes` feeds those notes + the plain Whisper transcript
   into `settings.openai_model_grading` (a text model) with structured
   `response_format`, which produces the final SpeakingGradingResult.

Why two steps instead of one audio call with response_format directly: the
model this project originally targeted for step 1+2 combined,
`gpt-4o-audio-preview`, has been fully retired from the API (confirmed via a
live 404 "model_not_found" test) - and its intended successor,
`gpt-audio-1.5`, does NOT support structured `response_format` at all
(confirmed via a live 400 "not supported with this model" test). Splitting
the audio-understanding step from the structured-output step works around
that gap using each model for what it actually supports, and is more
resilient to either model's feature set shifting again - audio APIs are
still the fastest-moving part of OpenAI's surface, so re-verify against a
live key (see the test pattern used to discover the above) after any model
name change here.

If even the audio-listening step fails, `grade_speaking` falls back further
to grading from the plain transcript alone (weakest signal, but keeps the
Speaking module working end-to-end rather than hard-failing).
"""
from __future__ import annotations

import base64
import logging

from bot.config import settings
from services.ai.openai_client import client
from services.ai.schemas import SpeakingGradingResult

logger = logging.getLogger(__name__)

BAND_DESCRIPTORS = """
Band 9 — Speaks fluently with only rare, content-related hesitation; wide range of natural, \
precise, idiomatic vocabulary; full range of structures used naturally and accurately; \
pronunciation is effortless to understand throughout, with full flexibility.

Band 8 — Fluent with only occasional repetition/self-correction, hesitation is content-related \
not language-related; wide vocabulary used flexibly and precisely; wide range of structures, the \
majority error-free; easy to understand throughout, minor slips in pronunciation only.

Band 7 — Speaks at length without noticeable effort, some hesitation/repetition tied to searching \
for language; vocabulary allows some flexibility and precision, some paraphrase; a range of \
complex structures with some flexibility, frequent error-free sentences; generally easy to \
understand, some mispronunciation of individual words/sounds that doesn't affect communication.

Band 6 — Willing to speak at length but coherence may be lost with hesitation/repetition; enough \
vocabulary for the topic, some inappropriate word choice; mix of simple and complex structures but \
limited flexibility; generally understood despite some mispronunciation, though the listener may \
need to make an effort at times.

Band 5 — Usually maintains flow but with noticeable, sometimes lengthy, repetition and hesitation; \
limited vocabulary, often relies on simpler language and paraphrase; basic sentence forms with \
limited use of complex structures, frequent errors; mispronunciation causes some difficulty for the \
listener at times, only partly intelligible.

Band 4 — Frequent, sometimes long, pauses, noticeably slow speech, often self-corrects; limited \
vocabulary insufficient for less familiar topics; only basic structures, frequent breakdowns; \
noticeable mispronunciation that requires significant listener effort.
""".strip()

PART_INSTRUCTIONS = {
    "part1": (
        "This is Speaking PART 1: short, everyday questions about familiar topics (home, work, "
        "study, hobbies). Answers should be fairly brief but complete, natural, and directly "
        "responsive."
    ),
    "part2": (
        "This is Speaking PART 2 (the 'long turn'): the student speaks for 1-2 minutes on a cue "
        "card topic, covering all bullet points given. Grade for sustained, organized speech, not "
        "just short answers."
    ),
    "part3": (
        "This is Speaking PART 3: abstract, discussion-style follow-up questions related to the "
        "Part 2 topic. Answers should show the ability to develop, justify, and discuss ideas in "
        "depth, not just describe."
    ),
}

# Revision Brief v2, Section 1 (2026-08-30): calibrate toward the more
# generous end of a band range rather than a literal examiner reading, since
# every WriteSpeak user is an ESL learner, not a native speaker being
# benchmarked against native-level command of English.
ESL_CALIBRATION = """
ESL-CALIBRATED SCORING: When scoring the 4 criteria, weigh communicative effectiveness above \
prescriptive correctness. A single grammar slip, an awkward but understandable collocation, a \
minor first-language-interference pattern (e.g. article omission, preposition choice, word order \
carried over from Uzbek or Russian), or an isolated mispronunciation that doesn't block \
understanding should not by itself pull the band down - only deduct when such issues are frequent \
or severe enough to genuinely interfere with the listener's understanding or the response's \
fluency. Where a response sits between two bands, default to the HIGHER one unless the errors are \
systematic. This is intentionally more generous than a literal reading of the descriptors - it's \
a supportive estimate calibrated for ESL learners, not a literal examiner reading, and the \
report's disclaimer says so.
""".strip()

AUDIO_LISTEN_PROMPT = """You are an IELTS examiner's ear. Listen to this recording carefully and \
describe, in plain text (not JSON), exactly what you hear about HOW the student speaks - not what \
they say. Cover:
- Pronunciation: clarity of individual sounds, word stress, sentence stress, intonation patterns, \
any sounds that are consistently mispronounced.
- Fluency: pace, natural rhythm, length and placement of pauses, filler sounds (um/uh), \
self-corrections, and whether hesitation is language-related or just natural thinking pauses.

Be specific and concrete (e.g. "final consonants often dropped", "long pause before every complex \
sentence") - another examiner who cannot hear the audio will score Pronunciation and Fluency from \
your description alone, so vague praise or vague criticism is useless to them. 4-8 sentences."""


def _system_prompt(part: str, has_audio_notes: bool) -> str:
    audio_note = (
        "\n\nYou are given the transcribed TEXT of what the student said, plus a colleague "
        "examiner's LISTENING NOTES describing how it actually sounded (pronunciation, pacing, "
        "hesitation) - use the transcript for content/grammar/vocabulary and the listening notes "
        "specifically for Pronunciation and the delivery side of Fluency & Coherence."
        if has_audio_notes
        else "\n\nNOTE: No audio or listening notes are available this time (fallback path) - "
        "you only have the transcript. Score Pronunciation conservatively based solely on "
        "disfluency markers visible in the text (fillers, repetitions, false starts, incomplete "
        "words), and briefly note in the feedback summary that pronunciation could not be "
        "directly assessed this time."
    )
    return f"""You are a certified, meticulous IELTS Speaking examiner AND a warm, encouraging \
ESL tutor. Grade according to the IELTS Speaking Band Descriptors below AND the ESL-calibrated \
scoring note that follows them - the goal is an honest, diagnostic score a student can actually \
trust and act on: not a maximally strict literal reading, and not an inflated, feel-good one \
either.

{PART_INSTRUCTIONS.get(part, PART_INSTRUCTIONS["part1"])}

IELTS SPEAKING BAND DESCRIPTORS (summary):
{BAND_DESCRIPTORS}

{ESL_CALIBRATION}

Your job, in order:
1. Read the question and the student's answer (transcript + listening notes, see below).
2. Score Fluency & Coherence, Lexical Resource, Grammatical Range & Accuracy, and Pronunciation \
each from 0-9 in 0.5 increments per the ESL-calibrated approach above, then set the overall band \
to their average rounded to the nearest 0.5.
3. Annotate the answer sentence by sentence, working from the transcript: GREEN for genuinely \
strong/advanced/natural language, YELLOW for acceptable-but-improvable language OR an isolated, \
minor, understandable slip (say exactly how to improve it), RED only for an error frequent or \
severe enough to actually interfere with meaning or fluency (give the correction). Use the \
`sentence` field to quote what the student actually said, in order.
4. Extract 6-10 useful, topic-specific words/phrases (band 7+ level) for this exact topic, each \
with a meaning and an example sentence.
5. Write an engaging, encouraging feedback summary in a warm human tone, 150-300 words — never a \
robotic error list. Acknowledge what's working before addressing weaknesses, end on an \
actionable, motivating note, and include one short sentence noting this is a supportive estimate \
calibrated for ESL learners, not a literal examiner reading of the descriptors. Mention \
fluency/pace/hesitation observations, not just vocabulary and grammar.
6. Produce a short topic label (3-6 words) for this answer.{audio_note}"""


async def grade_speaking(
    part: str, question_text: str, mp3_audio_bytes: bytes, transcript_text: str
) -> SpeakingGradingResult:
    """Grades the spoken answer. Tries to actually listen to the audio first
    (so Pronunciation reflects real sound, not just word choice); falls back
    to grading from the transcript alone if the audio-listening step fails
    for any reason - see the module docstring."""
    try:
        audio_notes = await _listen_to_audio(part, question_text, mp3_audio_bytes)
    except Exception:
        logger.exception(
            "Audio-listening step failed (model=%s) - grading from transcript only. "
            "Pronunciation scoring will be less accurate until this is investigated.",
            settings.openai_model_audio,
        )
        return await _grade_from_transcript(part, question_text, transcript_text)

    return await _grade_with_audio_notes(part, question_text, transcript_text, audio_notes)


async def _listen_to_audio(part: str, question_text: str, mp3_audio_bytes: bytes) -> str:
    b64_audio = base64.b64encode(mp3_audio_bytes).decode("utf-8")
    response = await client.chat.completions.create(
        model=settings.openai_model_audio,
        modalities=["text"],
        messages=[
            {"role": "system", "content": AUDIO_LISTEN_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"SPEAKING QUESTION (Part {part[-1]}):\n{question_text}",
                    },
                    {
                        "type": "input_audio",
                        "input_audio": {"data": b64_audio, "format": "mp3"},
                    },
                ],
            },
        ],
        # No `temperature` override - gpt-5-family models (and, untested,
        # possibly gpt-audio-1.5 too) only accept the default; see
        # services/ai/ocr.py's comment.
    )
    notes = response.choices[0].message.content
    if not notes:
        raise RuntimeError("Audio-listening model returned no content")
    return notes


async def _grade_with_audio_notes(
    part: str, question_text: str, transcript_text: str, audio_notes: str
) -> SpeakingGradingResult:
    response = await client.beta.chat.completions.parse(
        model=settings.openai_model_grading,
        messages=[
            {"role": "system", "content": _system_prompt(part, has_audio_notes=True)},
            {
                "role": "user",
                "content": f"SPEAKING QUESTION (Part {part[-1]}):\n{question_text}\n\n"
                f"STUDENT'S TRANSCRIBED ANSWER (what was said):\n{transcript_text}\n\n"
                f"EXAMINER'S LISTENING NOTES (how it was said):\n{audio_notes}",
            },
        ],
        response_format=SpeakingGradingResult,
        # No `temperature` override - see services/ai/ocr.py's comment.
    )
    result = response.choices[0].message.parsed
    if result is None:
        raise RuntimeError("Speaking grading model returned no parsable result")
    return result


async def _grade_from_transcript(
    part: str, question_text: str, transcript_text: str
) -> SpeakingGradingResult:
    response = await client.beta.chat.completions.parse(
        model=settings.openai_model_grading,
        messages=[
            {"role": "system", "content": _system_prompt(part, has_audio_notes=False)},
            {
                "role": "user",
                "content": f"SPEAKING QUESTION (Part {part[-1]}):\n{question_text}\n\n"
                f"STUDENT'S TRANSCRIBED ANSWER:\n{transcript_text}",
            },
        ],
        response_format=SpeakingGradingResult,
        # No `temperature` override - see services/ai/ocr.py's comment.
    )
    result = response.choices[0].message.parsed
    if result is None:
        raise RuntimeError("Speaking grading model returned no parsable result (transcript fallback)")
    return result
