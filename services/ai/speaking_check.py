"""One Speaking check from recordings to report data, without Telegram or
the database (the handler, bot/handlers/speaking.py, adds those):

    voice analysis (services/ai/voice.py)
      -> pronunciation assessment (Azure) - without it Pronunciation is not
         measured and takes the mean of the other three bands
      -> grading (services/ai/speaking_grader.py, speaking_marking.py)
    and, as soon as the feedback is final, the model answers written for the
    student's results (services/ai/speaking_answers.py), alongside the
    grading's last two passes.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from bot.utils.speaking_questions import duplicate_of, parse_cue_card
from services.ai import pronunciation
from services.ai.speaking_answers import build_model_answers, needs_from_feedback, targets_for
from services.ai.speaking_grader import Evidence, SessionItem, grade_speaking
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


async def run_check(
    part: str, questions: list[dict], clips: list[tuple[bytes, str]], seed: int = 0,
    transcribed: list[AnswerAudio] | None = None,
) -> SpeakingCheck:
    """`clips`: (audio bytes, filename), one per question in `questions`;
    `transcribed`: their transcripts when already made (one recording, split
    by services/ai/single_recording.py)."""
    wavs = list(await asyncio.gather(*(to_wav16k(audio, name) for audio, name in clips)))
    audios = await analyse_answers(wavs, [q["text"] for q in questions], transcribed)
    items = build_items(questions, audios)
    metrics = measure(audios)
    pitch = await asyncio.to_thread(measure_pitch, [w for w, a in zip(wavs, audios) if not a.no_speech])
    answers_task: asyncio.Task | None = None
    needs = None

    def start_answers(scores, overall, feedback) -> None:
        # The model answers are written for this student's results.
        nonlocal answers_task, needs
        needs = needs_from_feedback(items, scores, overall, feedback, metrics)
        answers_task = asyncio.create_task(build_model_answers(targets_for(items), needs, seed=seed))

    try:
        pron, practise = None, None
        if pronunciation.configured():
            pron = await pronunciation.assess_answers(
                [(wav, a.tokens, a.duration) for wav, a in zip(wavs, audios) if not a.no_speech]
            )
            practise = await pronunciation.words_to_practise(pron) if pron is not None else None
        report = await grade_speaking(part, items, Evidence(metrics, pron, pitch), practise,
                                      on_feedback=start_answers)
    except BaseException:
        if answers_task is not None:
            answers_task.cancel()
        raise
    try:
        if answers_task is None:
            raise RuntimeError("the feedback never reached the model answers")
        answers = await answers_task
    except Exception:
        logger.exception("Speaking model answers failed - one more try")
        try:
            answers = await build_model_answers(targets_for(items), needs, seed=seed)
        except Exception:
            logger.exception("Speaking model answers failed again - the report goes out without them")
            answers = []
    return SpeakingCheck(report=report, answers=answers, items=items, metrics=metrics)
