"""Writing Task 1 / Task 2 practice questions from @cdireport.

The channel posts what test-takers report after the exam, usually one post
per exam day: "Part 1: <Task 1>" + "Part 2: <Task 2>", with the Task 1
chart attached (mostly stamped "@cdireport"), plus ads, mock-test notices
and Reading/Listening/Speaking reports in between.

For each post not read before (practice_import_posts):
1. a cheap model reads the text and the photos and returns the Task 1 /
   Task 2 questions in standard IELTS wording, and which photos show the
   Task 1 visual (ads, chat screenshots, room photos are left out);
2. Task 1 photos get the channel mark removed (watermark.py) and are joined
   into one image; a Task 1 without any visual is skipped - it can't be
   practised;
3. a question reported before (embedding distance below
   DUPLICATE_DISTANCE) is not added again - the existing one is marked as
   seen again (seen_at, times_seen) and gets the image if it had none.

The first run reads posts from FIRST_RUN_SINCE on; later runs read the
posts newer than the last one read, and retry failed posts a few times.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import logging
import re
from datetime import datetime, timezone

from PIL import Image
from pydantic import BaseModel, Field

from bot.config import settings
from db import crud
from db.database import get_session
from db.models import PracticeModule, PracticeQuestion
from services.ai import usage
from services.ai.openai_client import client
from services.ai.rag_book_search import embed_text
from services.practice_import import telegram_preview as tg
from services.practice_import.watermark import remove_watermark
from services.storage.file_storage import save_practice_image

logger = logging.getLogger(__name__)

CHANNEL = "cdireport"
SOURCE = "cdireport"
FIRST_RUN_SINCE = datetime(2026, 4, 1, tzinfo=timezone.utc)
# Cosine distance below which two reported questions are the same question
# (paraphrased reports of one question land around 0.02-0.06).
DUPLICATE_DISTANCE = 0.07
MAX_ATTEMPTS = 3
MAX_IMAGES = 4  # photos sent to the model per post
PARSE_BATCH = 4  # posts read by the model at once

# Posts without photos are only worth a model call if they look like a Writing report.
_WRITING_HINT = re.compile(
    r"\b(part|task|t|p)\s*-?\s*[12]\b|writing|chart|graph|table|diagram|map|agree|discuss|advantage|opinion",
    re.I,
)


class Task1Question(BaseModel):
    question_text: str = Field(description="Full Task 1 prompt in standard IELTS wording")
    topic: str = Field(description="1-4 lowercase words")
    visual_images: list[int] = Field(description="1-based numbers of the photos that show the Task 1 visual")


class Task2Question(BaseModel):
    question_text: str = Field(description="The full Task 2 essay question")
    topic: str = Field(description="1-4 lowercase words")


class PostQuestions(BaseModel):
    exam_report: bool
    exam_date: str | None = Field(description="YYYY-MM-DD of the exam if the post states it")
    task1: Task1Question | None
    task2: Task2Question | None


SYSTEM_PROMPT = """You read posts from a Telegram channel where IELTS test-takers in Uzbekistan report the Academic Writing questions they got in the exam. Extract those questions so students can practise them.

Rules:
- exam_report is true only if the post reports real exam Writing questions. Ads, mock-test announcements, greetings, Reading/Listening/Speaking reports and questions to the admin are not: return exam_report=false, task1=null, task2=null.
- In this channel "Part 1" / "P1" / "Task 1" / "T1" is Writing Task 1 and "Part 2" / "Task 2" / "T2" is Writing Task 2. When a post also lists Listening or Reading parts (P1-P4 there are their sections), take only the Writing section's tasks. A post may give Task 1 only as a photo, with no text.
- task1 (Academic, a chart / graph / table / map / diagram / process): question_text is the complete prompt in standard IELTS wording, e.g. "The bar chart below shows ... Summarise the information by selecting and reporting the main features, and make comparisons where relevant." Use the wording printed on the photo when the photo shows the prompt; otherwise turn the post's description into that form. It must describe what the photos actually show (if the post says the exam version differed slightly, describe the photo). Do not add "Write at least 150 words". A General Training letter is not a task1: return null.
- visual_images lists the photos (numbered from 1 in the order given) that show the Task 1 visual itself. Leave out chat screenshots, photos of rooms or people, ads and Reading/Listening pages. Use [] if no photo shows it.
- task2: question_text is the essay question as reported, with obvious typos fixed, keeping its instruction line (e.g. "To what extent do you agree or disagree?") as given. Never invent a question that is not in the post.
- Leave out dates, hashtags, @mentions, links, channel footers and comments in Uzbek or Russian.
- topic: 1-4 lowercase English words naming the subject, e.g. "household recycling", "free university education".
- exam_date: the exam date if the post states one (e.g. "JUNE 19" -> that day in the post's year), else null."""


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


async def parse_post(post: tg.ChannelPost, images: list[bytes]) -> PostQuestions:
    posted = f"{post.date:%Y-%m-%d}" if post.date else "unknown"
    content: list[dict] = [
        {
            "type": "text",
            "text": f"Post date: {posted}\nPhotos attached: {len(images)}\n\nPost text:\n{post.text or '(no text)'}",
        }
    ]
    for data in images:
        b64 = base64.b64encode(data).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    response = await client.beta.chat.completions.parse(
        model=settings.openai_model_sample,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}],
        response_format=PostQuestions,
        # Sent as extra_body: openai 1.57 has no reasoning_effort argument yet.
        extra_body={"reasoning_effort": "low"},
    )
    usage.record("practice import parse", response)
    parsed = response.choices[0].message.parsed
    if parsed is None:
        raise RuntimeError(f"model gave no answer: {response.choices[0].message.refusal}")
    return parsed


def _stack(images: list[bytes]) -> bytes:
    """Several Task 1 photos (e.g. two maps) as one image, one under another."""
    if len(images) == 1:
        return images[0]
    pictures = [Image.open(io.BytesIO(data)).convert("RGB") for data in images]
    width = max(p.width for p in pictures)
    gap = 16
    canvas = Image.new("RGB", (width, sum(p.height for p in pictures) + gap * (len(pictures) - 1)), "white")
    y = 0
    for p in pictures:
        canvas.paste(p, ((width - p.width) // 2, y))
        y += p.height + gap
    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()


def _seen_at(post: tg.ChannelPost, exam_date: str | None) -> datetime:
    posted = post.date or datetime.now(timezone.utc)
    if exam_date:
        try:
            day = datetime.strptime(exam_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            # A reported exam is never after the post, and rarely weeks before it.
            if (posted - day).days in range(0, 31):
                return day
        except ValueError:
            pass
    return posted


async def _save_question(
    module: PracticeModule,
    text: str,
    topic: str,
    seen_at: datetime,
    ref: str,
    image: bytes | None,
) -> str:
    """Adds the question, or marks an earlier copy of it as seen again.
    Returns what happened, for the log."""
    embedding = await embed_text(text)
    async with get_session() as session:
        nearest = await crud.nearest_imported_question(session, module, SOURCE, embedding)
        if nearest is not None and nearest[1] <= DUPLICATE_DISTANCE:
            question = nearest[0]
            question.times_seen = (question.times_seen or 1) + 1
            if question.seen_at is None or seen_at > question.seen_at:
                question.seen_at = seen_at
            if image is not None and not question.image_path:
                question.image_path = save_practice_image(image)
            await session.commit()
            return f"seen again as #{question.id} ({nearest[1]:.3f})"
        if module == PracticeModule.writing_task1 and image is None:
            return "skipped: Task 1 without a chart"
        question = PracticeQuestion(
            module=module,
            question_text=text,
            topic=topic or None,
            image_path=save_practice_image(image) if image is not None else None,
            source=SOURCE,
            source_ref=ref,
            source_hash=_hash(text),
            seen_at=seen_at,
            times_seen=1,
            embedding=embedding,
            order_index=0,
        )
        session.add(question)
        await session.commit()
        return f"added #{question.id}"


async def _process(post: tg.ChannelPost, parsed: PostQuestions, images: list[bytes]) -> str:
    if not parsed.exam_report or (parsed.task1 is None and parsed.task2 is None):
        return "skipped: not a Writing report"
    seen_at = _seen_at(post, parsed.exam_date)
    results = []
    if parsed.task2 is not None and parsed.task2.question_text.strip():
        outcome = await _save_question(
            PracticeModule.writing_task2,
            parsed.task2.question_text.strip(),
            parsed.task2.topic.strip(),
            seen_at,
            f"{post.id}:t2",
            None,
        )
        results.append(f"T2 {outcome}")
    if parsed.task1 is not None and parsed.task1.question_text.strip():
        chosen = [images[i - 1] for i in parsed.task1.visual_images if 1 <= i <= len(images)]
        image = None
        if chosen:
            cleaned = []
            for data in chosen:
                clean, _ = await asyncio.to_thread(remove_watermark, data)
                cleaned.append(clean)
            image = await asyncio.to_thread(_stack, cleaned)
        outcome = await _save_question(
            PracticeModule.writing_task1,
            parsed.task1.question_text.strip(),
            parsed.task1.topic.strip(),
            seen_at,
            f"{post.id}:t1",
            image,
        )
        results.append(f"T1 {outcome}")
    return "; ".join(results) or "skipped: empty questions"


def _worth_reading(post: tg.ChannelPost) -> bool:
    return bool(post.image_urls) or bool(_WRITING_HINT.search(post.text or ""))


async def _read_post(http, post: tg.ChannelPost) -> tuple[PostQuestions, list[bytes]]:
    try:
        images = [await tg.download(http, url) for url in post.image_urls[:MAX_IMAGES]]
    except Exception:
        # Photo links can go stale during a long first import - read the post again.
        fresh = (await tg.fetch_ids(http, CHANNEL, {post.id})).get(post.id, post)
        images = [await tg.download(http, url) for url in fresh.image_urls[:MAX_IMAGES]]
    return await parse_post(post, images), images


async def import_new_posts(since: datetime | None = None, limit: int | None = None) -> dict[str, int]:
    """Reads the channel's new posts (and retries failed ones). Returns counts per status."""
    async with get_session() as session:
        known = await crud.get_import_posts(session, CHANNEL)
    async with tg.new_client() as http:
        if since is not None or not known:
            posts = await tg.fetch_since(http, CHANNEL, since=since or FIRST_RUN_SINCE)
        else:
            posts = await tg.fetch_since(http, CHANNEL, min_id=max(known))
        retry_ids = {pid for pid, row in known.items() if row.status == "failed" and row.attempts < MAX_ATTEMPTS}
        if retry_ids:
            posts += list((await tg.fetch_ids(http, CHANNEL, retry_ids)).values())
        posts = [p for p in posts if p.id not in known or p.id in retry_ids]
        posts.sort(key=lambda p: p.id)
        if limit is not None:
            posts = posts[:limit]
        logger.info("cdireport: %d post(s) to read", len(posts))

        counts: dict[str, int] = {}
        with usage.track(f"practice import {CHANNEL}"):
            for start in range(0, len(posts), PARSE_BATCH):
                batch = posts[start : start + PARSE_BATCH]
                readable = [p for p in batch if _worth_reading(p)]
                results = await asyncio.gather(*(_read_post(http, p) for p in readable), return_exceptions=True)
                read = dict(zip((p.id for p in readable), results))
                for post in batch:
                    status, note = "skipped", "no Writing content"
                    if post.id in read:
                        result = read[post.id]
                        try:
                            if isinstance(result, BaseException):
                                raise result
                            parsed, images = result
                            note = await _process(post, parsed, images)
                            status = "skipped" if note.startswith("skipped") else "done"
                        except Exception as e:
                            logger.exception("cdireport post %s failed", post.id)
                            status, note = "failed", f"{type(e).__name__}: {e}"[:500]
                    async with get_session() as session:
                        await crud.mark_import_post(session, CHANNEL, post.id, status, note)
                    counts[status] = counts.get(status, 0) + 1
                    logger.info("cdireport/%s: %s - %s", post.id, status, note)

    async with get_session() as session:
        for module in (PracticeModule.writing_task1, PracticeModule.writing_task2):
            await crud.order_practice_module_by_recency(session, module)
    return counts
