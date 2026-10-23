"""Builds data/calibration/anchors.json - the Writing scorer's fixed anchor
examples (marking guide B3; the owner chose them on 2026-10-22, see
services/ai/writing_anchors.py) - from data/calibration/book_samples.json.
A Task 1 anchor carries its visual's data sheet, read by the production
code (cached by scripts/task1_extraction_test.py, read now if not).

    python scripts/build_writing_anchors.py

Re-run after the chart-reading prompts change. Copy anchors.json and
task1_patterns.json to the server's data/calibration/ (they hold book text,
so they are not in git).
"""
from __future__ import annotations

import asyncio
import json
import re
import sys

from calibration_common import CAL, load_samples, question_of, setup_logging, visual_for

sys.stdout.reconfigure(encoding="utf-8")


async def main() -> None:
    from services.ai.essay_marking import count_words
    from services.ai.writing_anchors import ANCHOR_IDS

    setup_logging()
    by_id = {s["id"]: s for s in load_samples()}
    out: dict[str, list[dict]] = {}
    for task, ids in ANCHOR_IDS.items():
        out[task] = []
        for sample_id in ids:
            s = by_id[sample_id]
            if s["band_low"] is None or not s["commentary"]:
                raise SystemExit(f"{sample_id}: no band or no commentary - not an anchor")
            sheet = ""
            if task == "task1":
                _, sheet, cached = await visual_for(s)
                print(f"{sample_id}: data sheet {'from the cache' if cached else 'read now'} ({len(sheet)} chars)")
            out[task].append({
                "id": sample_id,
                "band_label": re.sub(r"^band\s*:?\s*", "", s["band_label"], flags=re.IGNORECASE).strip(),
                "band_low": s["band_low"], "band_high": s["band_high"],
                "question": question_of(s), "sheet": sheet, "text": s["text"],
                "words": count_words(s["text"]), "commentary": s["commentary"],
            })
    path = CAL / "anchors.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for task, items in out.items():
        print(f"{task}: " + ", ".join(f"{a['id']} (Band {a['band_label']}, {a['words']} words)" for a in items))
    print(f"Wrote {path}")


if __name__ == "__main__":
    asyncio.run(main())
