"""Is a reported Writing question one we already have?

The same exam question comes back many times, reported in other words and
with another photo of the chart, so wording alone can't tell (the same
Task 1 lands 0.07-0.17 apart in embedding distance - as far as some
different questions). For the closest few questions:
1. near-identical wording (distance <= SAME_DISTANCE) - same question;
2. Task 1: the same picture (ORB feature matches after RANSAC) - same;
3. otherwise gpt-5-mini compares the wordings (same visual / same essay
   question?) - one cheap text-only call.

merge_existing() runs the same check over a module already imported and
merges the copies (scripts/import_practice_questions.py --merge-duplicates).
"""
from __future__ import annotations

import asyncio
import logging

import cv2
import numpy as np
from pydantic import BaseModel, Field

from bot.config import settings
from db import crud
from db.database import get_session
from db.models import PracticeModule, PracticeQuestion
from services.ai import usage
from services.ai.openai_client import client
from services.storage.file_storage import delete_file, read_file

logger = logging.getLogger(__name__)

NEAR_DISTANCE = 0.3  # closer than this gets a closer look
SAME_DISTANCE = 0.05  # this close is the same question without asking
CANDIDATES = 4
MATCH_INLIERS = 40  # ORB matches surviving RANSAC: the same picture (different charts stay under ~15)

_orb = cv2.ORB_create(nfeatures=3000)


def _grey(content: bytes) -> np.ndarray | None:
    image = cv2.imdecode(np.frombuffer(content, np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return None
    scale = 1000 / max(image.shape)
    return cv2.resize(image, (int(image.shape[1] * scale), int(image.shape[0] * scale)), interpolation=cv2.INTER_AREA)


def same_picture(a: bytes, b: bytes) -> bool:
    """Both images show the same chart (crops, sizes and headers may differ)."""
    grey_a, grey_b = _grey(a), _grey(b)
    if grey_a is None or grey_b is None:
        return False
    keys_a, desc_a = _orb.detectAndCompute(grey_a, None)
    keys_b, desc_b = _orb.detectAndCompute(grey_b, None)
    if desc_a is None or desc_b is None:
        return False
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(desc_a, desc_b, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.75 * p[1].distance]
    if len(good) < MATCH_INLIERS:
        return False
    src = np.float32([keys_a[m.queryIdx].pt for m in good])
    dst = np.float32([keys_b[m.trainIdx].pt for m in good])
    _, inliers = cv2.findHomography(src, dst, cv2.RANSAC, 6.0)
    return inliers is not None and int(inliers.sum()) >= MATCH_INLIERS


class SameQuestion(BaseModel):
    match: int | None = Field(description="Number of the earlier question that is the same exam question, or null")


JUDGE_PROMPT = {
    PracticeModule.writing_task1: (
        "Two IELTS Academic Writing Task 1 prompts are the same question when they describe the same visual: "
        "the same subject, the same places or categories and the same years or period. Wording differs between "
        "reports and one may leave details out. A different period, different places or a different subject "
        "make it a different question."
    ),
    PracticeModule.writing_task2: (
        "Two IELTS Writing Task 2 prompts are the same question when they raise the same issue and ask the same "
        "thing (e.g. both ask whether you agree that X), even if reported in different words. A different issue, "
        "or the same topic with a different claim or task, is a different question."
    ),
}


async def judge_same(module: PracticeModule, text: str, earlier: list[str]) -> int | None:
    """0-based index of the earlier question that is the same one, or None."""
    listing = "\n\n".join(f"{i}. {q}" for i, q in enumerate(earlier, 1))
    response = await client.beta.chat.completions.parse(
        model=settings.openai_model_sample,
        messages=[
            {"role": "system", "content": JUDGE_PROMPT[module]},
            {"role": "user", "content": f"New question:\n{text}\n\nEarlier questions:\n{listing}"},
        ],
        response_format=SameQuestion,
        # Sent as extra_body: openai 1.57 has no reasoning_effort argument yet.
        extra_body={"reasoning_effort": "low"},
    )
    usage.record("practice import dedupe", response)
    parsed = response.choices[0].message.parsed
    if parsed is None or parsed.match is None or not 1 <= parsed.match <= len(earlier):
        return None
    return parsed.match - 1


async def find_same(
    module: PracticeModule,
    text: str,
    image: bytes | None,
    candidates: list[tuple[PracticeQuestion, float]],
) -> tuple[PracticeQuestion, str] | None:
    """The candidate (closest first, all within NEAR_DISTANCE) that is the
    same question, with how it was recognised."""
    if not candidates:
        return None
    closest, distance = candidates[0]
    if distance <= SAME_DISTANCE:
        return closest, f"same wording ({distance:.3f})"
    if image is not None:
        for question, _ in candidates:
            if not question.image_path:
                continue
            try:
                stored = read_file(question.image_path)
            except FileNotFoundError:
                continue
            if await asyncio.to_thread(same_picture, image, stored):
                return question, "same picture"
    index = await judge_same(module, text, [q.question_text for q, _ in candidates])
    if index is not None:
        return candidates[index][0], "same question (model)"
    return None


def _distance(a, b) -> float:
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    return float(1 - a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))


async def merge_existing(module: PracticeModule, source: str) -> int:
    """Merges copies of the same question already in `module`; the oldest
    copy stays. Returns how many copies were merged away."""
    async with get_session() as session:
        questions = sorted(
            [q for q in await crud.get_imported_questions(session, source) if q.module == module and q.embedding is not None],
            key=lambda q: q.id,
        )
    kept: list[PracticeQuestion] = []
    merged = 0
    for question in questions:
        near = sorted(
            ((k, _distance(question.embedding, k.embedding)) for k in kept), key=lambda pair: pair[1]
        )
        near = [pair for pair in near if pair[1] <= NEAR_DISTANCE][:CANDIDATES]
        image = None
        if question.image_path:
            try:
                image = read_file(question.image_path)
            except FileNotFoundError:
                pass
        same = await find_same(module, question.question_text, image, near)
        if same is None:
            kept.append(question)
            continue
        keep, how = same
        async with get_session() as session:
            delete_file(await crud.merge_practice_questions(session, keep.id, question.id))
        if not keep.image_path and question.image_path:
            keep.image_path = question.image_path  # it moved over
        merged += 1
        logger.info("Merged #%s into #%s (%s)", question.id, keep.id, how)
    return merged
