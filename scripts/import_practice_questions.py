"""Imports Practice questions from the Telegram channels once, by hand (the
`importer` service does it every few hours - services/practice_import/).

Usage (inside the bot container):
  python scripts/import_practice_questions.py                      # both channels, new posts only
  python scripts/import_practice_questions.py --only speaking
  python scripts/import_practice_questions.py --since 2026-01-01   # re-read Writing posts from a date
  python scripts/import_practice_questions.py --limit 20           # first 20 unread Writing posts
  python scripts/import_practice_questions.py --replace-old        # delete non-imported questions first
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.practice_import.runner import remove_manual_questions, run_once  # noqa: E402


async def main(args: argparse.Namespace) -> None:
    if args.replace_old:
        await remove_manual_questions()
    since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc) if args.since else None
    await run_once(
        do_writing=args.only in (None, "writing"),
        do_speaking=args.only in (None, "speaking"),
        since=since,
        limit=args.limit,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", choices=["writing", "speaking"])
    parser.add_argument("--since", help="Writing: read posts from this date (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, help="Writing: read at most this many posts")
    parser.add_argument("--replace-old", action="store_true", help="delete questions not imported from a channel")
    asyncio.run(main(parser.parse_args()))
