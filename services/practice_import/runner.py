"""The `importer` service (docker-compose.yml): imports new channel questions
every INTERVAL_HOURS, in its own container so the CPU-heavy watermark removal
never slows the bot down.

    python -m services.practice_import.runner

scripts/import_practice_questions.py runs the same steps once, by hand.

Each run's outcome is kept in Redis (crm_events.IMPORTER_STATUS_KEY, 2026-11-16) for the daily report, which
flags a failed run, or none for over 8 hours (the service down).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from bot.utils import balance, crm, crm_events
from db import crud
from db.database import get_session
from db.models import PracticeQuestion
from services.practice_import import speaking, writing
from services.storage.file_storage import delete_file

logger = logging.getLogger(__name__)

INTERVAL_HOURS = 3
FIRST_RUN_DELAY = 60  # seconds - lets the bot container apply migrations first
STATUS_KEEP = 30 * 24 * 3600


def _reason(e: BaseException) -> str:
    return f"{type(e).__name__}: {e}"[:300]


async def run_once(*, do_writing: bool = True, do_speaking: bool = True, since: datetime | None = None,
                   limit: int | None = None, errors: dict[str, str] | None = None) -> dict[str, dict]:
    """{"speaking": counts, "writing": counts} of what was imported. A part that failed is logged and left out;
    `errors` (when given) gets why, by part."""
    result: dict[str, dict] = {}
    if do_speaking:
        try:
            result["speaking"] = await speaking.sync()
        except Exception as e:
            logger.exception("Speaking import failed")
            if errors is not None:
                errors["speaking"] = _reason(e)
    if do_writing:
        try:
            counts = await writing.import_new_posts(since=since, limit=limit)
            logger.info("Writing import: %s", counts)
            result["writing"] = counts
        except Exception as e:
            logger.exception("Writing import failed")
            if errors is not None:
                errors["writing"] = _reason(e)
    return result


async def save_status(result: dict, errors: dict[str, str], skipped: str | None = None) -> None:
    """The run's outcome for the daily report (crm_events.daily_report): {"at", "ok", "writing": counts,
    "speaking": counts, "error"} (+ "skipped": "credit" for a run left out while the OpenAI credit is out).
    Never raises."""
    status = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ok": not errors,
              "writing": result.get("writing") or {}, "speaking": result.get("speaking") or {},
              "error": "; ".join(f"{part}: {why}" for part, why in errors.items())[:500]}
    if skipped:
        status["skipped"] = skipped
    try:
        await crm.redis().set(crm_events.IMPORTER_STATUS_KEY, json.dumps(status, ensure_ascii=False), ex=STATUS_KEEP)
    except Exception as e:  # noqa: BLE001
        logger.warning("Practice import status not saved: %r", e)


async def run_and_record() -> None:
    """One turn of the service: the import (not while the OpenAI credit is out - checks are paused then too),
    its spend to the day's totals, and its outcome kept. A failed run is logged and waits for the next one - it
    used to end the process, and the restart ran (and paid for) the import again at once (2026-11-15)."""
    if await balance.credit_paused():
        logger.info("Practice import skipped: the OpenAI credit is out")
        await save_status({}, {}, skipped="credit")
        return
    errors: dict[str, str] = {}
    result: dict[str, dict] = {}
    try:
        result = await run_once(errors=errors)
        await crm_events.import_done(result)  # the run's OpenAI spend, to the daily totals
    except Exception as e:  # noqa: BLE001
        logger.exception("Practice import run failed - the next one in %s h", INTERVAL_HOURS)
        errors["run"] = _reason(e)
    await save_status(result, errors)


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
        await run_and_record()
        await asyncio.sleep(INTERVAL_HOURS * 3600)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    asyncio.run(main())
