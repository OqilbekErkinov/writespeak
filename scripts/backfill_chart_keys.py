"""Chart keys from the Task 1 checks made before keys existed (2026-10-28):

    docker compose exec bot python scripts/backfill_chart_keys.py [--dry-run]

Every past Task 1 check kept its chart's sheet (report_data["visual"]) and
its picture; each chart becomes an "auto" key for the panel's /charts page,
the same chart met by several checks counted as its uses - so the admins can
check the charts students already sent, at no reading cost (only each
question's embedding, a fraction of a cent). Run once, on an empty table:
it refuses when keys already exist (it would count their uses twice).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from db.database import get_session  # noqa: E402
from db.models import ChartKey, TaskType, WritingSubmission  # noqa: E402
from services.ai import chart_keys, usage  # noqa: E402
from services.ai.schemas import VisualSheet  # noqa: E402


async def main(dry_run: bool) -> None:
    async with get_session() as session:
        if (await session.execute(select(func.count()).select_from(ChartKey))).scalar() and not dry_run:
            print("Chart keys already exist - the backfill runs once, on an empty table.")
            return
        rows = (await session.execute(
            select(WritingSubmission).where(WritingSubmission.task_type == TaskType.task1)
            .order_by(WritingSubmission.id))).scalars().all()
    kept = skipped = 0
    with usage.track("chart keys backfill") as calls:
        for sub in rows:
            sheet = (sub.report_data or {}).get("visual")
            charts = (sub.original_file_paths or {}).get("chart") or []
            if not sheet or len(charts) != 1 or not Path(charts[0]).exists():
                skipped += 1
                continue
            visual = VisualSheet.model_validate(sheet)
            if visual.readability == "unreadable" or not visual.blocks:
                skipped += 1
                continue
            images = [(Path(charts[0]).read_bytes(), "image/png")]
            look = chart_keys.Lookup(fingerprint=chart_keys.fingerprint(images[0][0]),
                                     embedding=await chart_keys._embedding(sub.prompt_text))
            print(f"#{sub.id}: {', '.join(b.visual_type for b in visual.blocks)} - {sub.prompt_text[:70]!r}")
            if not dry_run:
                await chart_keys.remember(sub.prompt_text, images, visual, look, sub.practice_question_id, sub.id)
            kept += 1
    print(f"\n{kept} checks gave their chart, {skipped} had none; cost ${usage.summary(calls).get('total_usd', 0):.4f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="list the charts, keep nothing")
    asyncio.run(main(ap.parse_args().dry_run))
