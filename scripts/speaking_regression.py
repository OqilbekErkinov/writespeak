"""Speaking regression: real recordings with a known official band go through
the bot's grading again (the academic lead, 2026-10-27: a real Band 9.0
candidate was given 6.5). Each case in data/calibration/speaking/<id>/:

    recording.<ogg|mp3|m4a|wav>   the recording as the student sent it
    recording.json                single_recording.Recording (both Whisper readings) - no Whisper again
    case.json                     {"part", "questions", "split": [[[start, end], ...], ...], "band"}

and its expectations in config/speaking_marking.json ("regression"). A case
that is also an anchor is graded WITHOUT itself as an anchor (leave one
out), so the test shows what the rules do, not the anchor read back.

    python scripts/speaking_regression.py [--only real9-p3-travel] [--budget 0.4]

Also writes data/calibration/speaking_anchors.json from the cases with an
official band (--anchors-only to do just that, no API calls).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from calibration_common import CAL, Budget, setup_logging

sys.stdout.reconfigure(encoding="utf-8")
CASES = CAL / "speaking"


def load_case(case_dir: Path) -> dict:
    case = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    case["id"] = case_dir.name
    case["audio"] = next(p for p in case_dir.iterdir() if p.stem == "recording" and p.suffix != ".json")
    return case


def write_anchors(cases: list[dict]) -> None:
    """The anchors' transcripts, as the scoring prompt shows them."""
    from services.ai import single_recording as one

    out = {}
    for case in cases:
        if not case.get("band"):
            continue
        rec = one.Recording.from_json(json.loads((CASES / case["id"] / "recording.json").read_text(encoding="utf-8")))
        answers = []
        for question, spans in zip(case["questions"], case["split"]):
            if spans:
                answers.append({"question": question,
                                "transcript": one.answer_audio(rec, [tuple(s) for s in spans]).text})
        out[case["id"]] = {"part": case["part"], "band": case["band"], "label": f"Band {case['band']:.1f} (official)",
                           "answers": answers}
    path = CAL / "speaking_anchors.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Wrote {path}: {', '.join(out) or 'none'}")


async def grade(case: dict) -> dict:
    from services.ai import single_recording as one
    from services.ai.speaking_check import run_check
    from services.ai.speaking_marking import build_progress
    from services.ai.voice import to_wav16k
    from services.pdf.report_builder import build_speaking_report_pdf

    rec = one.Recording.from_json(json.loads((CASES / case["id"] / "recording.json").read_text(encoding="utf-8")))
    joined = one.join_wavs([await to_wav16k(case["audio"].read_bytes(), case["audio"].name)])
    split = [[tuple(s) for s in q] for q in case["split"]]
    answered = [i for i, s in enumerate(split) if s]
    check = await run_check(
        case["part"], [{"part": case["part"], "text": case["questions"][i]} for i in answered],
        [(one.cut(joined, split[i]), f"q{i + 1}.wav") for i in answered], seed=1,
        transcribed=[one.answer_audio(rec, split[i]) for i in answered],
    )
    report = check.report
    report.notices = [one.NOTICE_ONE_RECORDING]
    progress = build_progress(case["part"], report.overall_band, check.metrics, None, report.priorities, None)
    pdf = build_speaking_report_pdf(report, check.answers, progress, "Regression", False)
    out = CASES / case["id"] / f"report-{time.strftime('%Y%m%d-%H%M')}.pdf"
    out.write_bytes(pdf)
    return {"report": report, "answers": check.answers, "pdf": str(out)}


async def main(args) -> None:
    from services.ai import speaking_grader, usage
    from services.ai.scoring import whole_band
    from services.ai.speaking_answers import repeated_phrases

    setup_logging()
    cases = [load_case(d) for d in sorted(CASES.iterdir()) if (d / "case.json").exists()] if CASES.exists() else []
    write_anchors(cases)
    if args.anchors_only:
        return
    expect = {k: v for k, v in (speaking_grader.speaking_config().get("regression") or {}).items()
              if not k.startswith("_") and k != "review"}
    cases = [c for c in cases if c["id"] in expect and (not args.only or c["id"] in args.only.split(","))]
    budget = Budget(args.budget)
    passed = []
    for case in cases:
        # never graded against itself - unless asked (--with-anchor: the anchor read back)
        speaking_grader.EXCLUDED_ANCHORS = set() if args.with_anchor else {case["id"]}
        with usage.track(f"speaking regression {case['id']}") as calls:
            got = await grade(case)
        cost = budget.add(calls)
        r = got["report"]
        bands = {c["key"]: c["band"] for c in r.criteria}
        mode = "WITH itself as an anchor" if args.with_anchor else "without itself as an anchor"
        print(f"\n=== {case['id']} ({case['part']}, official {case.get('band')}, {mode}): overall {r.overall_band} "
              f"({', '.join(f'{k} {v:g}' for k, v in bands.items())})  ${cost:.3f}")
        for c in r.criteria:
            print(f"   {c['name']}: {c['note']}")
        print("   mistakes: " + ("; ".join(f"[{m['criterion']}] {m['wrong']} -> {m['correct']}" for m in r.mistakes)
                                  or "none"))
        print("   priorities: " + ("; ".join(p["title"] for p in r.priorities) or "none"))
        print(f"   model answers aimed at: {sorted({a['band'] for a in got['answers']})}")
        repeats = repeated_phrases([(a["label"], a["question"], " ".join(a["paragraphs"])) for a in got["answers"]],
                                   [v.term for v in r.vocabulary])
        checks = []
        e = expect[case["id"]]
        if "overall_min" in e:
            checks.append((f"overall at least {e['overall_min']:g}", r.overall_band >= e["overall_min"],
                           f"{r.overall_band}"))
        checks.append(("no phrase repeated across the model answers", not repeats, f"{repeats}"))
        if r.pron_estimated:
            p_note = next(c["note"] for c in r.criteria if c["key"] == "P")
            mean = whole_band((bands["FC"] + bands["LR"] + bands["GRA"]) / 3)
            checks.append(("Pronunciation not measured: the mean of the other three, said so",
                           bands["P"] == mean and p_note == speaking_grader.P_NOT_MEASURED, f"P {bands['P']}, {p_note}"))
            checks.append(("no pronunciation priority without a measurement",
                           not any("Pronunciation" in t for p in r.priorities for t, _ in p["tags"]),
                           f"{[p['title'] for p in r.priorities]}"))
        for name, ok, detail in checks:
            print(f"   {'PASS' if ok else 'FAIL'}  {name}  [{detail}]")
            passed.append(ok)
        print(f"   PDF: {got['pdf']}")
    print(f"\n{sum(passed)}/{len(passed)} checks passed; spent ${budget.spent:.2f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="case ids, comma-separated")
    ap.add_argument("--budget", type=float, default=0.4)
    ap.add_argument("--anchors-only", action="store_true", help="only write speaking_anchors.json")
    ap.add_argument("--with-anchor", action="store_true",
                    help="grade a case with itself as an anchor (shows the anchor works; not a fair test)")
    asyncio.run(main(ap.parse_args()))
