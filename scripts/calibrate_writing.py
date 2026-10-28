"""The Writing scorer calibrated against the books (marking guide B4,
2026-10-22) - run before deploying and whenever a scoring prompt changes.

Every book sample with a printed band that is not an anchor (the test set)
is scored exactly as a student's work is: Task 1 visuals read by the
production code, the median of the scoring runs, the caps. Our overall band
is compared with the book's (a range such as 7.0-7.5 counts as correct when
ours falls inside it). Targets: mean difference within ±0.25, at least 80%
within ±0.5, none more than 1.0 off; guardrail B7: no Band 8-9 sample below
7.5. When the scores are off, fix the reasoning (the guide, the anchors, the
mistake filter) - never add a flat amount to the result.

    python scripts/calibrate_writing.py --label after --full [--only t1|t2] [--ids ...] [--budget 20]

--full grades each sample as the bot does (the mistakes first, then the
bands from the checked list); without it only the scoring runs are made,
as the bot graded until 2026-10-23.

It also runs against the previous scorer (an older checkout, for the
"before" table): it uses decide_scores when the code has it, else
score_writing_runs. Results: data/calibration/results/b4-<label>-<time>.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time

from calibration_common import (
    CAL,
    Budget,
    BudgetExceeded,
    band_label,
    chart_images,
    diff_to_range,
    load_samples,
    question_of,
    setup_logging,
    visual_for,
)

sys.stdout.reconfigure(encoding="utf-8")

# The anchors of 2026-10-22, for older code without config/writing_marking.json
# (the "before" run); the current anchors and test-set overrides come from it.
OLD_ANCHORS = {"v2-p072", "v2-p068", "v2-p084", "v2-p026", "v2-p034", "v2-p078"}
KEYS = ("TR_or_TA", "CC", "LR", "GRA")
NAMES = {"TR_or_TA": "TA/TR", "CC": "CC", "LR": "LR", "GRA": "GRA"}


def anchors_and_overrides() -> tuple[set[str], dict]:
    try:
        from services.ai import marking_config
    except ImportError:
        return set(OLD_ANCHORS), {}
    return marking_config.anchor_ids(), marking_config.calibration().get("overrides", {})


def test_set(samples: list[dict], ids: list[str] | None = None) -> list[dict]:
    """The B4 test set: book samples with a printed band, never an anchor,
    with the academic lead's overrides (remove / expect another band).
    Fails when an asked-for sample is an anchor: a sample the scorer sees in
    its own prompt would only measure memory."""
    anchors, overrides = anchors_and_overrides()
    if ids:
        overlap = sorted(set(ids) & anchors)
        if overlap:
            raise SystemExit(f"B4 samples overlap the anchors: {', '.join(overlap)} - pick other samples")
    out = []
    for s in samples:
        if s["band_low"] is None or s["id"] in anchors or (ids and s["id"] not in ids):
            continue
        rule = overrides.get(s["id"]) or {}
        if rule.get("action") == "remove":
            continue
        if rule.get("action") == "expect":
            low, high = rule["band"] if isinstance(rule["band"], list) else (rule["band"], rule["band"])
            s = {**s, "band_low": float(low), "band_high": float(high), "band_label": f"{low}-{high} (override)"}
        out.append(s)
    return out


def culprit(bands: dict[str, float], low: float, high: float, diff: float) -> str:
    """The criterion furthest from the book's band in the direction of the difference."""
    if diff == 0:
        return ""
    mid = (low + high) / 2
    key = min(KEYS, key=lambda k: bands[k] - mid) if diff < 0 else max(KEYS, key=lambda k: bands[k] - mid)
    return NAMES[key]


def metrics(rows: list[dict]) -> dict:
    if not rows:
        return {}
    diffs = [r["diff"] for r in rows]
    return {
        "samples": len(rows),
        "mean_diff": round(statistics.mean(diffs), 3),
        "within_0_5": round(sum(abs(d) <= 0.5 for d in diffs) / len(diffs), 3),
        "over_1_0": sum(abs(d) > 1.0 for d in diffs),
        "band8_below_7_5": sum(r["book_low"] >= 8.0 and r["ours"] < 7.5 for r in rows),
        "exact": round(sum(d == 0 for d in diffs) / len(diffs), 3),
    }


def verdict(m: dict) -> str:
    ok = [-0.25 <= m["mean_diff"] <= 0.25, m["within_0_5"] >= 0.8, m["over_1_0"] == 0, m["band8_below_7_5"] == 0]
    return "PASS" if all(ok) else "FAIL"


async def main(args) -> None:
    from services.ai import usage, writing_grader
    from services.ai.scoring import overall_band

    setup_logging()
    samples = test_set(load_samples(), args.ids.split(",") if args.ids else None)
    if args.only:
        samples = [s for s in samples if s["task"] == args.only.upper()]
    detailed = hasattr(writing_grader, "decide_scores")
    if args.scorer_effort:  # the low-vs-medium experiment (the last prompt's A2)
        from bot.config import settings

        settings.openai_scoring_reasoning_effort = args.scorer_effort
        print(f"Scoring runs at reasoning effort {args.scorer_effort}")
    budget = Budget(args.budget)
    semaphore = asyncio.Semaphore(args.concurrency)
    rows: list[dict] = []

    failures: list[str] = []

    async def one(s: dict) -> None:
        async with semaphore:
            budget.check()
            if len(failures) >= 3:  # e.g. the account is out of credit: stop paying for nothing
                raise BudgetExceeded(f"{len(failures)} samples failed: {failures[-1]}")
            task = "task1" if s["task"] == "T1" else "task2"
            images = chart_images(s)
            started = time.monotonic()
            with usage.track(f"calibration b4 {s['id']}") as calls:
                try:
                    sheet, reading = "", None
                    if images:
                        reading, sheet, _ = await visual_for(s, cache_dir=args.visuals)
                        if sheet and detailed:  # as the bot judges a report: the sheet and its data check
                            from services.ai.visual_reading import judge_ready

                            reading, _, sheet, _ = await judge_ready(s["text"], reading, question_of(s), images)
                    if args.full:
                        # the whole check as the bot runs it (mistakes first), without the model answer
                        report = await writing_grader.grade_writing(task, question_of(s), s["text"], images or None,
                                                                    sheet=sheet, visual=reading if sheet else None)
                        bands = {c["key"]: c["band"] for c in report.criteria}
                        notes = {c["key"]: c["note"] for c in report.criteria}
                        capped, runs = [], []
                    elif detailed:
                        scores = await writing_grader.decide_scores(task, question_of(s), s["text"], images, sheet)
                        bands, notes, capped, runs = scores.bands, scores.notes, scores.capped, scores.runs
                    else:
                        bands = await writing_grader.score_writing_runs(task, question_of(s), s["text"], images, sheet)
                        notes, capped, runs = {}, [], []
                except Exception as e:
                    budget.add(calls)
                    failures.append(f"{s['id']}: {e!r}"[:300])
                    print(f"{s['id']} FAILED: {e!r}"[:300], flush=True)
                    return
            cost = budget.add(calls)
            ours = overall_band([bands[k] for k in KEYS])
            diff = diff_to_range(ours, s["band_low"], s["band_high"])
            row = {"id": s["id"], "task": s["task"], "book": band_label(s), "book_low": s["band_low"],
                   "book_high": s["band_high"], "ours": ours, "bands": {k: bands[k] for k in KEYS}, "diff": diff,
                   "culprit": culprit(bands, s["band_low"], s["band_high"], diff), "notes": notes,
                   "capped": capped, "runs": runs, "words": s["word_count"], "cost": round(cost, 4),
                   "seconds": round(time.monotonic() - started)}
            rows.append(row)
            print(f"{s['id']} {s['task']} book {row['book']:9} ours {ours:.1f} "
                  f"({' '.join(f'{NAMES[k]} {bands[k]:.0f}' for k in KEYS)})  diff {diff:+.1f}  ${cost:.3f}", flush=True)

    try:
        await asyncio.gather(*(one(s) for s in samples))
    except BudgetExceeded as e:
        print(f"STOPPED: {e}")
    rows.sort(key=lambda r: (r["task"], r["id"]))

    print(f"\n### {args.label}\n")
    print("| Sample | Book band | Ours | TA/TR | CC | LR | GRA | Diff | Main cause |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        b = r["bands"]
        print(f"| {r['id']} ({r['task']}) | {r['book']} | {r['ours']:.1f} | {b['TR_or_TA']:.0f} | {b['CC']:.0f} | "
              f"{b['LR']:.0f} | {b['GRA']:.0f} | {r['diff']:+.1f} | {r['culprit']} |")
    summary = {"all": metrics(rows), "T1": metrics([r for r in rows if r["task"] == "T1"]),
               "T2": metrics([r for r in rows if r["task"] == "T2"])}
    for part, m in summary.items():
        if m:
            print(f"{part}: {m['samples']} samples, mean diff {m['mean_diff']:+.2f}, within ±0.5 {m['within_0_5']:.0%}, "
                  f"in range {m['exact']:.0%}, >1.0 off {m['over_1_0']}, Band 8-9 below 7.5: {m['band8_below_7_5']}"
                  f"  -> {verdict(m)}")
    for k in KEYS:
        devs = [r["bands"][k] - (r["book_low"] + r["book_high"]) / 2 for r in rows]
        if devs:
            print(f"  {NAMES[k]}: ours minus the book band, on average {statistics.mean(devs):+.2f}")
    out = CAL / "results" / f"b4-{args.label}-{time.strftime('%Y%m%d-%H%M')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"label": args.label, "summary": summary, "spent": round(budget.spent, 3),
                               "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Saved {out}  (spent ${budget.spent:.2f})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", default="run")
    ap.add_argument("--only", choices=["t1", "t2"])
    ap.add_argument("--ids", help="only these sample ids, comma-separated")
    ap.add_argument("--visuals", default="visuals", help="the chart readings' cache folder in data/calibration/")
    ap.add_argument("--full", action="store_true",
                    help="the whole check (feedback, mistake filter, scores from the checked mistakes) - as the bot "
                         "grades since 2026-10-23; about $0.10 a sample instead of $0.02")
    ap.add_argument("--scorer-effort", choices=["low", "medium", "high"],
                    help="the scoring runs' reasoning effort for this run (default: the setting)")
    ap.add_argument("--budget", type=float, default=20.0, help="stop after this many dollars")
    ap.add_argument("--concurrency", type=int, default=4)
    asyncio.run(main(ap.parse_args()))
