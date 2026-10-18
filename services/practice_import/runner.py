"""The `importer` service (docker-compose.yml): imports new channel questions
every INTERVAL_HOURS, in its own container so the CPU-heavy watermark removal
never slows the bot down.

    python -m services.practice_import.runner

scripts/import_practice_questions.py runs the same steps once, by hand.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy import select

from bot.utils import crm, crm_events
from db import crud
from db.database import get_session
from db.models import PracticeQuestion
from services.practice_import import speaking, writing
from services.storage.file_storage import delete_file

logger = logging.getLogger(__name__)

INTERVAL_HOURS = 3
FIRST_RUN_DELAY = 60  # seconds - lets the bot container apply migrations first


async def run_once(*, do_writing: bool = True, do_speaking: bool = True, since: datetime | None = None,
                   limit: int | None = None) -> dict[str, dict]:
    """{"speaking": counts, "writing": counts} of what was imported."""
    result: dict[str, dict] = {}
    if do_speaking:
        try:
            result["speaking"] = await speaking.sync()
        except Exception:
            logger.exception("Speaking import failed")
    if do_writing:
        try:
            counts = await writing.import_new_posts(since=since, limit=limit)
            logger.info("Writing import: %s", counts)
            result["writing"] = counts
        except Exception:
            logger.exception("Writing import failed")
    return result


async def remove_manual_questions() -> int:
    """Deletes every practice question not imported from a channel (the old
    seeded bank and admin-written ones), with their images and ✅ marks."""
    async with get_session() as session:
        ids = list((await session.execute(
            select(PracticeQuestion.id).where(PracticeQuestion.source.is_(None))
        )).scalars().all())
        for question_id in ids:
            question = await crud.delete_practice_question(session, question_id)
            delete_file(question.image_path if question else None)
    logger.info("Removed %d question(s) that were not imported from a channel", len(ids))
    return len(ids)


async def main() -> None:
    crm.install_error_forwarding("importer")  # errors to the admins' group and the panel (bot/utils/crm.py)
    await asyncio.sleep(FIRST_RUN_DELAY)
    while True:
        result = await run_once()
        await crm_events.import_done(result)  # the run's OpenAI spend, to the daily totals
        await asyncio.sleep(INTERVAL_HOURS * 3600)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    asyncio.run(main())
