"""One Speaking check from recordings to report data, without Telegram or
the database (the handler, bot/handlers/speaking.py, adds those):

    voice analysis (services/ai/voice.py)
      -> pronunciation assessment (Azure) or, without it, listening notes
      -> grading (services/ai/speaking_grader.py, speaking_marking.py)
    and, at the same time, the model answers (services/ai/speaking_answers.py).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from bot.utils.speaking_questions import duplicate_of, parse_cue_card
from services.ai import pronunciation
from services.ai.speaking_answers import build_model_answers, targets_for
from services.ai.speaking_grader import LISTEN_MIN_WORDS, Evidence, SessionItem, grade_speaking, listening_notes
from services.ai.speaking_marking import SpeakingReport
from services.ai.voice import AnswerAudio, VoiceMetrics, analyse_answers, measure, measure_pitch, to_wav16k

logger = logging.getLogger(__name__)


@dataclass
class SpeakingCheck:
    report: SpeakingReport
    answers: list[dict]  # model answers
    items: list[SessionItem]
    metrics: VoiceMetrics


def build_items(questions: list[dict], audios: list[AnswerAudio]) -> list[SessionItem]:
    """`questions`: [{"part": "part1", "text": ...}] in the order asked, one
    per recorded answer."""
    dup = duplicate_of([q["text"] if q["part"] != "part2" else f"\x00{i}" for i, q in enumerate(questions)])
    items = []
    for i, (q, audio) in enumerate(zip(questions, audios)):
        cue = parse_cue_card(q["text"]) if q["part"] == "part2" else None
        items.append(SessionItem(
            qid=f"Q{i + 1}", part=q["part"], question=cue["topic"] if cue else q["text"], audio=audio,
            cue_card=cue, duplicate_of=f"Q{dup[i] + 1}" if dup[i] is not None and q["part"] != "part2" else None,
        ))
    return items


async def run_check(part: str, questions: list[dict], clips: list[tuple[bytes, str]], seed: int = 0) -> SpeakingCheck:
    """`clips`: (audio bytes, filename), one per question in `questions`."""
    wavs = list(await asyncio.gather(*(to_wav16k(audio, name) for audio, name in clips)))
    # Without a pronunciation service the audio model listens while Whisper transcribes.
    notes_task = None if pronunciation.configured() else asyncio.create_task(listening_notes(wavs))
    try:
        audios = await analyse_answers(wavs, [q["text"] for q in questions])
    except BaseException:
        if notes_task:
            notes_task.cancel()
        raise
    items = build_items(questions, audios)
    metrics = measure(audios)
    pitch = await asyncio.to_thread(measure_pitch, [w for w, a in zip(wavs, audios) if not a.no_speech])
    # The model answers build on what the student said; written while grading runs.
    answers_task = asyncio.create_task(build_model_answers(targets_for(items), seed=seed))
    try:
        pron, notes, practise = None, None, None
        if notes_task is None:
            pron = await pronunciation.assess_answers(
                [(wav, a.tokens, a.duration) for wav, a in zip(wavs, audios) if not a.no_speech]
            )
            practise = await pronunciation.words_to_practise(pron) if pron is not None else None
        if pron is None:
            notes = await notes_task if notes_task else await listening_notes(wavs)
            if sum(a.word_count for a in audios) < LISTEN_MIN_WORDS:
                notes = None  # too little speech to judge
        report = await grade_speaking(part, items, Evidence(metrics, pron, notes, pitch), practise)
    except BaseException:
        answers_task.cancel()
        raise
    try:
        answers = await answers_task
    except Exception:
        logger.exception("Speaking model answers failed - one more try")
        try:
            answers = await build_model_answers(targets_for(items), seed=seed)
        except Exception:
            logger.exception("Speaking model answers failed again - the report goes out without them")
            answers = []
    return SpeakingCheck(report=report, answers=answers, items=items, metrics=metrics)
