"""Seeds `practice_questions` from the JSON files in data/practice_questions/
(Bosqich 7).

The question bank is managed from the web admin panel (webapp/main.py,
"Savollar") once seeded, so by default this only fills modules that are
still EMPTY - re-running it never clobbers the admin's edits.

`--overwrite MODULE [MODULE ...]` (or `--overwrite all`) replaces those
modules' content with the JSON's, in place: row N keeps its id (so past
submissions stay linked), its text/topic are updated, rows beyond the
JSON's length are deleted and missing ones added. Uploaded images are kept.
Completion ✅ marks are cleared only for questions whose text changed.

Usage:
  python scripts/seed_practice_questions.py
  python scripts/seed_practice_questions.py --overwrite speaking_part1 speaking_part2 speaking_part3
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete  # noqa: E402

from db import crud  # noqa: E402
from db.database import get_session  # noqa: E402
from db.models import PracticeModule, PracticeProgress, PracticeQuestion  # noqa: E402
from services.storage.file_storage import delete_file  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "practice_questions"

FILE_TO_MODULE = {
    "writing_task1.json": PracticeModule.writing_task1,
    "writing_task2.json": PracticeModule.writing_task2,
    "speaking_part1.json": PracticeModule.speaking_part1,
    "speaking_part2.json": PracticeModule.speaking_part2,
    "speaking_part3.json": PracticeModule.speaking_part3,
}


async def seed(overwrite: set[PracticeModule]) -> None:
    for filename, module in FILE_TO_MODULE.items():
        path = DATA_DIR / filename
        if not path.exists():
            print(f"  skip {filename} (not found)")
            continue
        items = json.loads(path.read_text(encoding="utf-8"))

        async with get_session() as session:
            existing = await crud.get_practice_questions(session, module)
            if existing and module not in overwrite:
                print(f"  skip {module.value}: already has {len(existing)} question(s) (use --overwrite)")
                continue

            for i, item in enumerate(items):
                text, topic = item["question_text"], item.get("topic")
                if i < len(existing):
                    row = existing[i]
                    if row.question_text != text:
                        await session.execute(
                            delete(PracticeProgress).where(PracticeProgress.practice_question_id == row.id)
                        )
                    row.question_text, row.topic, row.order_index = text, topic, i
                else:
                    session.add(PracticeQuestion(module=module, question_text=text, topic=topic, order_index=i))
            await session.commit()

            for extra in existing[len(items):]:
                deleted = await crud.delete_practice_question(session, extra.id)
                delete_file(deleted.image_path if deleted else None)

        print(f"  {filename}: {len(items)} question(s) -> {module.value}")

    print("Practice questions seeded.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--overwrite",
        nargs="+",
        default=[],
        metavar="MODULE",
        help="modules to replace from JSON even if not empty: "
        + ", ".join(m.value for m in PracticeModule)
        + ", or 'all'",
    )
    args = parser.parse_args()
    if "all" in args.overwrite:
        overwrite = set(PracticeModule)
    else:
        try:
            overwrite = {PracticeModule(m) for m in args.overwrite}
        except ValueError as e:
            parser.error(str(e))
    asyncio.run(seed(overwrite))


if __name__ == "__main__":
    main()
