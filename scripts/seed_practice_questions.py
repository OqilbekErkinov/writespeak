"""Seeds `practice_questions` from the JSON files in data/practice_questions/
(Bosqich 7). Idempotent: replaces each module's question list on every run,
so editing/adding to the JSON files and re-running is safe.

Usage: python scripts/seed_practice_questions.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete  # noqa: E402

from db.database import get_session  # noqa: E402
from db.models import PracticeModule, PracticeQuestion  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "practice_questions"

FILE_TO_MODULE = {
    "writing_task1.json": PracticeModule.writing_task1,
    "writing_task2.json": PracticeModule.writing_task2,
    "speaking_part1.json": PracticeModule.speaking_part1,
    "speaking_part2.json": PracticeModule.speaking_part2,
    "speaking_part3.json": PracticeModule.speaking_part3,
}


async def seed() -> None:
    async with get_session() as session:
        for filename, module in FILE_TO_MODULE.items():
            path = DATA_DIR / filename
            if not path.exists():
                print(f"  skip {filename} (not found)")
                continue

            items = json.loads(path.read_text(encoding="utf-8"))
            await session.execute(delete(PracticeQuestion).where(PracticeQuestion.module == module))
            session.add_all(
                [
                    PracticeQuestion(
                        module=module,
                        question_text=item["question_text"],
                        topic=item.get("topic"),
                        order_index=i,
                    )
                    for i, item in enumerate(items)
                ]
            )
            print(f"  {filename}: {len(items)} question(s) -> {module.value}")

        await session.commit()

    print("Practice questions seeded.")


if __name__ == "__main__":
    asyncio.run(seed())
