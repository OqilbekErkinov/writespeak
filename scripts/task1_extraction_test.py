"""Task 1 visual reading tested against the books (Task 1 prompt A8,
2026-10-22) - run once, and again whenever the reading prompts change:

  1. extraction accuracy: every book Task 1 visual is read by the production
     code (services/ai/visual_reading.py: two reads, the code checks); every
     figure the book's report mentions (numbers, percentages, years) is
     looked up in our data - within its precision, or as a difference, sum
     or ratio of two figures, or in a label. Target: 95% matched;
  2. overview understanding: the book report's overview sentence (and, for
     Volume 2, its commentary) against our computed expected overview -
     a gpt-5-mini judge lists its points and whether we cover each;
  3. the overview pattern library, data/calibration/task1_patterns.json:
     2-3 real overviews per visual type, for the scorer and the model answer.

    python scripts/task1_extraction_test.py [--ids v1-p004,v2-p072] [--fresh] [--budget 15]

Readings are cached in data/calibration/visuals/ (redone when the reading
prompts change, or with --fresh); results go to data/calibration/results/.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter, defaultdict
from typing import Literal

from pydantic import BaseModel

from calibration_common import CAL, Budget, BudgetExceeded, load_samples, setup_logging, visual_for
from services.ai.visual_check import figures, match_figure  # the production check

sys.stdout.reconfigure(encoding="utf-8")

OVERVIEW_RE = re.compile(r"^(overall|in general|generally|it is (clear|evident|noticeable|apparent)|at first glance|"
                         r"in summary|broadly|on the whole)", re.IGNORECASE)


def overview_sentence(text: str) -> str:
    sentences = [s.strip() for p in text.split("\n\n")[:2] for s in re.split(r"(?<=[.!?])\s+", p) if s.strip()]
    hit = next((s for s in sentences if OVERVIEW_RE.match(s)), None)
    if hit:
        i = sentences.index(hit)
        nxt = sentences[i + 1] if i + 1 < len(sentences) else ""
        # an overview often runs on into a second sentence ("Overall, ... . Notably, ...")
        return hit + (" " + nxt if nxt and re.match(r"^(notably|in addition|also|moreover|additionally|it is also|"
                                                     r"while|whereas|meanwhile)", nxt, re.IGNORECASE) else "")
    return sentences[1] if len(sentences) > 1 else (sentences[0] if sentences else "")


class OverviewPoint(BaseModel):
    point: str
    covered: Literal["yes", "partly", "no"]


class OverviewJudgement(BaseModel):
    book_points: list[OverviewPoint]
    commentary_points: list[OverviewPoint]
    missed: str


JUDGE = """You compare two descriptions of the same IELTS Task 1 visual. The BOOK OVERVIEW is from a \
Band 7-9 report; OUR EXPECTED OVERVIEW and KEY COMPARISONS were computed by software. List each main \
point the book overview makes (a trend, a contrast, an extreme, a stage) and say whether ours covers \
it (yes / partly / no). If an examiner's COMMENTARY is given, also list the points it says the overview \
or key features should contain (content only), and whether ours covers each. Judge CONTENT only - \
what is said about the data - never wording, style, paragraphing or grammar: ours is a list of facts, \
not a report. `missed`: one line on the content we miss, '' if nothing."""


async def judge_overview(sample: dict, reading: dict) -> OverviewJudgement | None:
    from bot.config import settings
    from services.ai import usage
    from services.ai.openai_client import parse_flex

    f = reading["features"]
    text = (f"BOOK OVERVIEW:\n{overview_sentence(sample['text'])}\n\nOUR EXPECTED OVERVIEW:\n"
            + "\n".join(f"- {x}" for x in f["expected_overview"]) + "\n\nOUR KEY COMPARISONS:\n"
            + "\n".join(f"- {x}" for x in f["key_comparisons"]))
    if sample.get("commentary"):
        text += f"\n\nCOMMENTARY:\n{sample['commentary'][:1500]}"
    response = await parse_flex(flex_timeout=90, model=settings.openai_model_sample, response_format=OverviewJudgement,
                                messages=[{"role": "system", "content": JUDGE}, {"role": "user", "content": text}])
    usage.record("overview_judge", response)
    return response.choices[0].message.parsed


def visual_kind(reading: dict) -> str:
    kinds = {b["visual_type"] for b in reading.get("blocks", [])}
    return kinds.pop() if len(kinds) == 1 else "mixed"


async def main(args) -> None:
    from services.ai import usage
    from services.ai.writing_anchors import anchor_ids

    setup_logging()
    samples = [s for s in load_samples() if s["task"] == "T1" and s.get("chart")]
    if args.ids:
        samples = [s for s in samples if s["id"] in args.ids.split(",")]
    budget = Budget(args.budget)
    semaphore = asyncio.Semaphore(args.concurrency)
    rows: list[dict] = []

    async def one(s: dict) -> None:
        async with semaphore:
            budget.check()
            started = time.monotonic()
            with usage.track(f"calibration a8 {s['id']}") as calls:
                reading, sheet, cached = await visual_for(s, fresh=args.fresh)
                judgement = None
                if reading and reading.get("features") and not args.no_judge:
                    try:
                        judgement = await judge_overview(s, reading)
                    except Exception as e:  # the extraction result still counts
                        print(f"  {s['id']}: overview judge failed: {e!r}")
            cost = budget.add(calls)
            figs = figures(s["text"])
            for fig in figs:
                fig["match"] = match_figure(fig, reading, s["question"])
            row = {
                "id": s["id"], "book_band": s["band_label"], "kind": visual_kind(reading) if reading else "-",
                "reads": reading.get("reads") if reading else 0,
                "readability": reading.get("readability") if reading else "failed",
                "values": sum(len(b["values"]) for b in reading["blocks"]) if reading else 0,
                "uncertain": sum(v["status"] == "uncertain" for b in reading["blocks"] for v in b["values"]) if reading else 0,
                "checks": reading.get("checks", []) if reading else [],
                "figures": len(figs), "match": Counter(f["match"] for f in figs),
                "misses": [f"{f['text']} «{f['context']}»" for f in figs if f["match"] == "miss"],
                "uncertain_hits": [f"{f['text']} «{f['context']}»" for f in figs if f["match"] == "raw-uncertain"],
                "overview": overview_sentence(s["text"]),
                "expected_overview": reading["features"]["expected_overview"] if reading else [],
                "judge": judgement.model_dump() if judgement else None,
                "cost": round(cost, 4), "cached": cached, "seconds": round(time.monotonic() - started),
            }
            rows.append(row)
            m = row["match"]
            print(f"{s['id']}  {row['kind']:7} figures {row['figures']:2}  raw {m['raw']:2}  derived {m['derived']:2}  "
                  f"label {m['label']:2}  uncertain {m['raw-uncertain']:2}  MISS {m['miss']:2}   "
                  f"${cost:.3f}{' (cached)' if cached else ''}", flush=True)

    try:
        await asyncio.gather(*(one(s) for s in samples))
    except BudgetExceeded as e:
        print(f"STOPPED: {e}")
    rows.sort(key=lambda r: r["id"])

    total = Counter()
    for r in rows:
        total.update(r["match"])
    n = sum(total.values())
    matched = n - total["miss"]
    strict = n - total["miss"] - total["raw-uncertain"]
    print("\n| Chart | Type | Book band | Figures | Matched | Uncertain | Missed | Reads | Checks |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        m = r["match"]
        ok = r["figures"] - m["miss"]
        print(f"| {r['id']} | {r['kind']} | {r['book_band']} | {r['figures']} | {ok} ({ok / r['figures']:.0%}) | "
              f"{m['raw-uncertain']} | {m['miss']} | {r['reads']} | {len(r['checks'])} |" if r["figures"] else
              f"| {r['id']} | {r['kind']} | {r['book_band']} | 0 | - | - | - | {r['reads']} | {len(r['checks'])} |")
    if n:
        print(f"\nFigures matched: {matched}/{n} = {matched / n:.1%} (verified figures only: {strict / n:.1%}); "
              f"target 95%.")
    for r in rows:
        for x in r["misses"]:
            print(f"  MISS {r['id']}: {x}")

    by_kind: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        for part in ("book_points", "commentary_points"):
            for p in (r["judge"] or {}).get(part, []):
                by_kind[r["kind"]][f"{part}:{p['covered']}"] += 1
    if by_kind:
        print("\nOverview points covered (book overview / commentary): yes · partly · no")
        for kind, c in sorted(by_kind.items()):
            print(f"  {kind:8} book {c['book_points:yes']}·{c['book_points:partly']}·{c['book_points:no']}   "
                  f"commentary {c['commentary_points:yes']}·{c['commentary_points:partly']}·{c['commentary_points:no']}")
        for r in rows:
            if r["judge"] and r["judge"].get("missed"):
                print(f"  {r['id']} ({r['kind']}): {r['judge']['missed']}")

    # The pattern library: 2-3 real overviews per visual type, from the
    # best-banded reports (never an anchor's).
    patterns: dict[str, list[tuple[float, str]]] = defaultdict(list)
    samples_by_id = {s["id"]: s for s in samples}
    for r in rows:
        s = samples_by_id[r["id"]]
        if r["id"] in anchor_ids() or r["kind"] == "-" or not r["overview"] or not s["band_low"]:
            continue
        if OVERVIEW_RE.match(r["overview"]) and 12 <= len(r["overview"].split()) <= 60:
            patterns[r["kind"]].append((-s["band_low"], len(r["overview"]), r["overview"], r["id"]))
    library = {k: [x[2] for x in sorted(v)[:3 if len(v) > 3 else 2 if len(v) >= 2 else 1]] for k, v in sorted(patterns.items())}
    used = {k: [x[3] for x in sorted(v)[:len(library[k])]] for k, v in patterns.items()}
    if not args.ids:
        (CAL / "task1_patterns.json").write_text(json.dumps(library, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nWrote {CAL / 'task1_patterns.json'}: " + ", ".join(f"{k} {len(v)}" for k, v in library.items()))
    out = CAL / "results" / f"a8-{time.strftime('%Y%m%d-%H%M')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": {"figures": n, "matched": matched, "strict": strict, "spent": budget.spent,
                                           "patterns_from": used}, "rows": rows}, ensure_ascii=False, indent=1,
                              default=dict), encoding="utf-8")
    print(f"Saved {out}  (spent ${budget.spent:.2f})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", help="only these sample ids, comma-separated")
    ap.add_argument("--fresh", action="store_true", help="read every chart again, ignoring the cache")
    ap.add_argument("--no-judge", action="store_true", help="skip the overview judge")
    ap.add_argument("--budget", type=float, default=15.0, help="stop after this many dollars")
    ap.add_argument("--concurrency", type=int, default=4)
    asyncio.run(main(ap.parse_args()))
