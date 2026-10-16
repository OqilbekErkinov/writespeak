"""Calibrates our scorer against book samples with known bands (reliability
rules, Speaking report prompt section 1.9 and Part 4): each sample is scored
the way a student's work is (the median of 3 scoring runs, whole bands) and
compared with the band stated in its book.

Usage (inside the bot container):
  python scripts/calibrate_scoring.py --writing 20            # shows the plan and cost, calls nothing
  python scripts/calibrate_scoring.py --writing 20 --yes      # runs it (about $0.02 per sample)
  python scripts/calibrate_scoring.py --speaking 20 --yes     # needs data/sample_library/speaking_index.json

If the average difference is more than 0.5 band, adjust the scoring prompt
(services/ai/writing_grader.py's _scoring_system / speaking_grader.py's
_scoring_system) and run it again - and after every scoring-prompt change.
Results are saved to data/sample_library/calibration/<date>-<kind>.json.

Caveats: a book's band is often the WRITER's IELTS result, not a marking of
that one essay; Task 1 samples have no chart in the library, so only Task 2
is used; book speaking answers have no recording, so Speaking is scored on
the transcript alone with Pronunciation left out.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.config import settings  # noqa: E402
from services.ai import usage  # noqa: E402

COST_PER_SAMPLE = {"writing": 0.02, "speaking": 0.02}


def spread(samples: list, n: int, band_of) -> list:
    """Up to n samples, taken in turn from each stated band, so the table
    covers the bands the books have."""
    by_band: dict[float, list] = {}
    for s in sorted(samples, key=lambda s: s.id):
        by_band.setdefault(band_of(s), []).append(s)
    chosen = []
    while len(chosen) < n and any(by_band.values()):
        for band in sorted(by_band):
            if by_band[band] and len(chosen) < n:
                chosen.append(by_band[band].pop(0))
    return chosen


async def writing(n: int) -> list[dict]:
    from services.ai.sample_bank import load_library
    from services.ai.scoring import overall_band
    from services.ai.writing_grader import score_writing_runs

    library = [s for s in load_library() if s.task_type == "task2" and s.band and s.question]
    rows = []
    for s in spread(library, n, lambda s: s.band):
        bands = await score_writing_runs("task2", s.question, s.text)
        ours = overall_band(list(bands.values()))
        rows.append({"id": s.id, "book_band": s.band, "ours": ours, "bands": bands, "diff": ours - s.band})
        print(f"{s.id:45} book {s.band:.1f}  ours {ours:.1f}  ({bands})  diff {ours - s.band:+.1f}", flush=True)
    return rows


async def speaking(n: int) -> list[dict]:
    from services.ai.scoring import overall_band
    from services.ai.speaking_answers import load_library
    from services.ai.speaking_grader import score_answers_text

    library = [a for a in load_library() if a.band]
    if not library:
        print("No speaking library yet (data/sample_library/speaking_index.json) - nothing to calibrate.")
        return []
    rows = []
    for a in spread(library, n, lambda a: a.band):
        ours = await score_answers_text(f"part{a.part}", [(a.question, a.text)])
        rows.append({"id": a.id, "book_band": a.band, "ours": ours, "diff": ours - a.band})
        print(f"{a.id:45} book {a.band:.1f}  ours {ours:.1f}  diff {ours - a.band:+.1f}", flush=True)
    return rows


async def main(args) -> None:
    kind = "writing" if args.writing else "speaking"
    n = args.writing or args.speaking
    estimate = n * COST_PER_SAMPLE[kind]
    if not args.yes:
        print(f"Would score {n} {kind} book samples (3 scoring runs each) - about ${estimate:.2f} of OpenAI usage.")
        print("Nothing was called. Run again with --yes to do it.")
        return
    with usage.track(f"calibration {kind}"):
        rows = await (writing(n) if kind == "writing" else speaking(n))
    if not rows:
        return
    diffs = [r["diff"] for r in rows]
    summary = {"samples": len(rows), "mean_diff": round(statistics.mean(diffs), 2),
               "mean_abs_diff": round(statistics.mean(abs(d) for d in diffs), 2),
               "within_0.5": sum(abs(d) <= 0.5 for d in diffs)}
    print(json.dumps(summary))
    if summary["mean_abs_diff"] > 0.5:
        print("Average difference is over 0.5 band: adjust the scoring prompt and run this again.")
    out = settings.sample_library_dir / "calibration" / f"{date.today()}-{kind}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Saved {out}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--writing", type=int, metavar="N", help="score N Writing Task 2 book samples")
    group.add_argument("--speaking", type=int, metavar="N", help="score N speaking book answers")
    parser.add_argument("--yes", action="store_true", help="really call the API (costs credits)")
    asyncio.run(main(parser.parse_args()))
