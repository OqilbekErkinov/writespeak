"""Speaking Part 1 / 2 / 3 practice questions from @IELTS_Speaking_Assistant_App.

The channel keeps two pinned index posts - "PART 1 QUESTIONS" and
"PART 2&3 QUESTIONS" - linking every topic post of the current question
seasons ("Always In Use", "May-December 2026", ...). Each topic post is plain
text:

    🔴PART 1 | TOPIC-4. TEACHERS
    📅 May-December 2026 📅
    1. Would you like to be a teacher?
    ...
    ➡️ @IELTS_Speaking_Assistant_App - Real IELTS speaking questions!

A Part 2 cue card ("⭐ Describe ...", "You should say:", "🔵 ..." points) is
followed directly by its Part 3 post. New topics are added by editing the
existing posts, and a "Soon..." post has no questions yet, so every run
re-reads all linked posts and mirrors them: new topics are added, changed
ones updated (an admin's own edit stays until the channel text changes),
topics gone from the index are removed. Newest season first.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from bot.utils.speaking_questions import dedupe
from db import crud
from db.database import get_session
from db.models import PracticeModule, PracticeQuestion
from services.practice_import import telegram_preview as tg

logger = logging.getLogger(__name__)

CHANNEL = "IELTS_Speaking_Assistant_App"
SOURCE = "speaking_assistant"
INDEX_PAGES = 15  # how far back to look for the index posts
# Fewer topics than this means the channel's layout changed - nothing is removed then.
MIN_TOPICS = {1: 20, 2: 20, 3: 10}

MODULES = {
    1: PracticeModule.speaking_part1,
    2: PracticeModule.speaking_part2,
    3: PracticeModule.speaking_part3,
}
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}

_HEADER = re.compile(r"PART\s*-?\s*([123])\s*\|\s*TOPIC\s*-?\s*(\d+)\s*[.:]?\s*(.*)", re.I)
_NUMBERED = re.compile(r"^\s*\d{1,2}\s*[.)]\s*(.+)$")
_SEASON = re.compile(r"📅\s*(.+?)\s*📅")
_SYMBOLS = re.compile(r"^[\W_]+", re.UNICODE)  # leading emoji / bullets
POINT_MARKS = "🔵•-–*●▪◦"  # a cue card's "You should say" points


@dataclass
class Topic:
    part: int
    number: int
    title: str
    season: str
    season_start: datetime | None
    question_text: str
    post_id: int

    @property
    def module(self) -> PracticeModule:
        return MODULES[self.part]

    @property
    def ref(self) -> str:
        return str(self.post_id)

    @property
    def hash(self) -> str:
        return hashlib.sha256(f"{self.title}\n{self.question_text}".encode("utf-8")).hexdigest()[:16]


def season_start(season: str) -> datetime | None:
    """"May-December 2026" -> 2026-05-01; "Always In Use" -> None."""
    month = re.search(r"(" + "|".join(MONTHS) + r")", season, re.I)
    year = re.search(r"(20\d\d)", season)
    if not month or not year:
        return None
    return datetime(int(year.group(1)), MONTHS[month.group(1).lower()], 1, tzinfo=timezone.utc)


def _clean_line(line: str) -> str:
    return _SYMBOLS.sub("", line).strip()


def parse_topic(post: tg.ChannelPost) -> Topic | None:
    """A topic post's questions, or None for "Soon..." / anything else."""
    lines = [line.strip() for line in post.text.splitlines()]
    header = next((_HEADER.search(line) for line in lines if _HEADER.search(line)), None)
    if header is None:
        return None
    part, number = int(header.group(1)), int(header.group(2))
    title = header.group(3).strip(" .")
    season_match = _SEASON.search(post.text)
    season = season_match.group(1).strip() if season_match else ""

    body = []
    for line in lines:
        if not line or _HEADER.search(line) or _SEASON.search(line):
            continue
        if "@IELTS_Speaking_Assistant_App" in line or line.startswith("➡"):
            break
        body.append(line)
    if not body or re.fullmatch(r"soon\W*", " ".join(body), re.I):
        return None

    if part == 2:
        card = []
        for line in body:
            text = _clean_line(line)
            if text:
                card.append(f"• {text}" if line[0] in POINT_MARKS else text)
        question_text = "\n".join(card)
        if not question_text.lower().startswith(("describe", "talk about")):
            return None
    else:
        questions = [m.group(1).strip() for m in map(_NUMBERED.match, body) if m]
        if not questions:
            questions = [_clean_line(line) for line in body if line.rstrip().endswith("?")]
        if not questions:
            return None
        # The channel sometimes asks one question twice in other words ("Do you
        # think you are an ambitious person?" / "Are you an ambitious person?").
        question_text = "\n".join(dedupe(questions))

    title = title.capitalize() if title.isupper() else title
    return Topic(part, number, title, season, season_start(season), question_text, post.id)


def _index_links(post: tg.ChannelPost) -> list[int]:
    """Post ids of the numbered topic links ("01", "02", ...) in an index post."""
    ids = []
    for url, label in post.links():
        match = re.search(r"/(\d+)(?:\?.*)?$", url)
        if match and re.fullmatch(r"\d{1,3}", label.strip()):
            ids.append(int(match.group(1)))
    return ids


async def find_index(http) -> tuple[list[int], list[int]]:
    """(Part 1 topic post ids, Part 2 topic post ids) from the latest index posts."""
    part1: list[int] = []
    part23: list[int] = []
    before = None
    for _ in range(INDEX_PAGES):
        page = await tg.fetch_page(http, CHANNEL, before)
        if not page:
            break
        for post in sorted(page, key=lambda p: p.id, reverse=True):
            upper = post.text.upper()
            if not part1 and "PART 1 QUESTIONS" in upper:
                part1 = _index_links(post)
            if not part23 and "PART 2&3 QUESTIONS" in upper.replace(" & ", "&"):
                part23 = _index_links(post)
        if part1 and part23:
            break
        before = min(p.id for p in page)
    return part1, part23


async def fetch_topics() -> list[Topic]:
    async with tg.new_client() as http:
        part1_ids, part2_ids = await find_index(http)
        wanted = set(part1_ids) | set(part2_ids) | {i + 1 for i in part2_ids}
        posts = await tg.fetch_ids(http, CHANNEL, wanted) if wanted else {}

    topics = []
    for post_id in part1_ids:
        topic = parse_topic(posts[post_id]) if post_id in posts else None
        if topic is not None and topic.part == 1:
            topics.append(topic)
    for post_id in part2_ids:
        cue = parse_topic(posts[post_id]) if post_id in posts else None
        if cue is not None and cue.part == 2:
            topics.append(cue)
        follow = parse_topic(posts[post_id + 1]) if post_id + 1 in posts else None
        if follow is not None and follow.part == 3:
            topics.append(follow)
    return topics


def _order_key(topic: Topic):
    start = topic.season_start or datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (-start.timestamp(), topic.number)


async def sync() -> dict[str, int]:
    """Mirrors the channel's current topics into the practice bank."""
    topics = await fetch_topics()
    counts = {"added": 0, "updated": 0, "removed": 0, "unchanged": 0}
    per_part = {part: sum(t.part == part for t in topics) for part in MODULES}
    complete = all(per_part[part] >= MIN_TOPICS[part] for part in MODULES)
    if not complete:
        logger.error("Speaking channel gave too few topics %s - nothing removed this time", per_part)

    async with get_session() as session:
        existing = {(q.module, q.source_ref): q for q in await crud.get_imported_questions(session, SOURCE)}
        seen = set()
        order = {module: 0 for module in MODULES.values()}
        for topic in sorted(topics, key=_order_key):
            key = (topic.module, topic.ref)
            seen.add(key)
            question = existing.get(key)
            if question is None:
                question = PracticeQuestion(module=topic.module, source=SOURCE, source_ref=topic.ref, times_seen=1)
                session.add(question)
                counts["added"] += 1
            elif question.source_hash != topic.hash:
                if question.question_text != topic.question_text:
                    await crud.clear_practice_progress(session, question.id)
                counts["updated"] += 1
            else:
                counts["unchanged"] += 1
            if question.source_hash != topic.hash:
                question.question_text = topic.question_text
                question.topic = topic.title[:255]
                question.source_hash = topic.hash
            question.seen_at = topic.season_start
            question.order_index = order[topic.module]
            order[topic.module] += 1
        await session.commit()

        if complete:
            for key, question in existing.items():
                if key not in seen:
                    await crud.delete_practice_question(session, question.id)
                    counts["removed"] += 1
    logger.info("Speaking sync: %s (topics per part %s)", counts, per_part)
    return counts


async def dedupe_existing() -> int:
    """Drops repeated questions from the Part 1/3 topics already in the bank
    (parse_topic does it for new ones). The stored hash is updated too, so
    the next sync sees the topic as unchanged and students' progress stays."""
    changed = 0
    async with get_session() as session:
        for question in await crud.get_imported_questions(session, SOURCE):
            if question.module == PracticeModule.speaking_part2:
                continue
            lines = [line for line in question.question_text.splitlines() if line.strip()]
            kept = dedupe(lines)
            if len(kept) == len(lines):
                continue
            logger.info("Topic %s (%s): dropped %s", question.id, question.topic,
                        [line for line in lines if line not in kept])
            question.question_text = "\n".join(kept)
            title = question.topic or ""
            question.source_hash = hashlib.sha256(f"{title}\n{question.question_text}".encode("utf-8")).hexdigest()[:16]
            changed += 1
        await session.commit()
    return changed
