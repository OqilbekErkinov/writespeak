"""Regression tests for the calibrated Writing marking (marking guide B6,
2026-10-22; the last prompt's A4, 2026-10-26): the owner's own reports,
pulled read-only from the database into data/calibration/regression/
(islamov_35_38.json, islamov_40_48.json, and each Task 1 photo as
islamov_<n>_chart.jpg), go through the whole check as the bot runs it - the
Task 1 photo read for its question and its chart, the feedback, the mistake
filter, the scores from the checked mistakes, the model answer and the PDF.

What each report must get is the academic lead's, in config/writing_marking.json
("regression"): an overall range or minimum, criterion minimums/maximums, and
phrases that must not be flagged as mistakes. On top of that, for every case:
an answer the lead expects at Band 6.0 or below must not come out more than
0.5 higher (the inflation guard), and a check must cost at most
max_cost_per_check. #35 keeps its own checks (the TA note, the 145 words,
"were quite passive" under LR, the question box), and both Band 6.5 anchors
(Volume 2 pp.72 and 26) must land within ±0.5.

    python scripts/writing_regression.py [--only 35,41] [--no-pdf] [--budget 3]

PDFs go to data/calibration/regression/out/, results to data/calibration/results/.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time

from calibration_common import CAL, Budget, chart_images, load_samples, question_of, setup_logging, visual_for

sys.stdout.reconfigure(encoding="utf-8")
REG = CAL / "regression"
KEY = {"TA": "TR_or_TA", "TR": "TR_or_TA", "CC": "CC", "LR": "LR", "GRA": "GRA"}


def load_records() -> list[dict]:
    out = []
    for name in ("islamov_35_38.json", "islamov_40_48.json"):
        path = REG / name
        if path.exists():
            out += json.loads(path.read_text(encoding="utf-8"))
    return out


def expected_checks(n: int, report, cost: float, expect: dict, config: dict) -> list[tuple[str, bool, str]]:
    """The academic lead's expectations for report #n, plus the inflation guard and the cost limit."""
    bands = {c["key"]: c["band"] for c in report.criteria}
    notes = {c["key"]: c["note"] for c in report.criteria}
    out = []
    if "overall" in expect:
        low, high = expect["overall"]
        out.append((f"overall {low:g}-{high:g}", low <= report.overall_band <= high, f"{report.overall_band}"))
        if high <= config["low_band_max"]:
            limit = high + config["low_band_max_rise"]
            out.append((f"inflation guard: at most {limit:g}", report.overall_band <= limit, f"{report.overall_band}"))
    if "overall_min" in expect:
        out.append((f"overall at least {expect['overall_min']:g}", report.overall_band >= expect["overall_min"],
                    f"{report.overall_band}"))
    for rule, limit in expect.items():
        m = re.fullmatch(r"(TA|TR|CC|LR|GRA)_(min|max)", rule)
        if not m:
            continue
        key, kind = KEY[m.group(1)], m.group(2)
        ok = bands[key] >= limit if kind == "min" else bands[key] <= limit
        out.append((f"{m.group(1)} {'at least' if kind == 'min' else 'at most'} {limit:g}", ok,
                    f"{m.group(1)} {bands[key]:g}: {notes[key]}"))
    for phrase in expect.get("not_flagged", []):
        hits = [mk["wrong"] for mk in report.mistakes if phrase.lower() in mk["wrong"].lower()]
        out.append((f"'{phrase}' not flagged", not hits, f"{hits}"))
    out.append((f"cost at most ${config['max_cost_per_check']:.2f}", cost <= config["max_cost_per_check"], f"${cost:.3f}"))
    return out


def report35_checks(report, question: str) -> list[tuple[str, bool, str]]:
    notes = {c["key"]: c["note"] for c in report.criteria}
    passive = [m for m in report.mistakes if "quite passive" in m["wrong"].lower()]
    from services.pdf.report_builder import question_text

    shown = question_text(question, True)
    return [
        ("TA note names the overview", "overview" in notes["TR_or_TA"].lower(), notes["TR_or_TA"]),
        ("the 145 words are noted", "145" in notes["TR_or_TA"] or any("145" in x for x in report.notices),
         f"note: {notes['TR_or_TA']} | notices: {report.notices}"),
        ("'were quite passive' is LR, not Task", bool(passive) and all(m["criterion"] == "LR" for m in passive),
         f"{[(m['wrong'], m['criterion']) for m in passive] or 'not listed'}"),
        ("question box: no chart figures", not re.search(r"\d+\s*%", shown), shown.replace("\n", " / ")),
    ]


async def run_case(rec: dict, pdf: bool) -> dict:
    from bot.handlers.writing import CHART_NOT_CHECKED, CHART_PARTLY_CHECKED
    from services.ai.ocr import extract_text_from_image
    from services.ai.sample_bank import build_model_answer, needs_from_feedback
    from services.ai.visual_check import data_check
    from services.ai.visual_reading import read_visual, sheet_text
    from services.ai.writing_grader import grade_writing
    from services.pdf.report_builder import build_writing_report_pdf
    from services.storage.file_storage import prepare_vision_image

    n, task = rec["essay_number"], rec["task_type"]
    question, images, sheet, judged, visual, notices = rec["prompt_text"], [], "", "", None, []
    photo = REG / f"islamov_{n}_chart.jpg"
    if task == "task1" and photo.exists():
        content, mime, _ = prepare_vision_image(photo.read_bytes())
        images = [(content, mime)]
        question = await extract_text_from_image(content, mime_type=mime, mode="writing_task1_question")
        visual = await read_visual(question, images)
        sheet = sheet_text(visual)
        if sheet:
            judged = f"{sheet}\n\n{data_check(rec['answer_text'], visual.model_dump(), question)}".strip()
        if visual is not None and visual.readability == "partly_unclear":
            notices.append(CHART_PARTLY_CHECKED)
    elif task == "task1":
        notices.append(CHART_NOT_CHECKED)
    visual_data = visual.model_dump() if visual is not None and sheet else None
    holder = {}

    def start_answer(bands, overall, feedback):
        holder["needs"] = needs_from_feedback(bands, overall, feedback, rec["answer_text"])
        holder["task"] = asyncio.create_task(build_model_answer(task, question, images or None, seed=n,
                                                                needs=holder["needs"], sheet=sheet, visual=visual_data))

    report = await grade_writing(task, question, rec["answer_text"], images or None, on_feedback=start_answer,
                                 sheet=judged, notices=notices, visual=visual_data)
    answer = await holder["task"] if "task" in holder else None
    path = None
    if pdf:
        progress = {"first": True, "note": "Regression run - progress is not compared."}
        data = await asyncio.to_thread(build_writing_report_pdf, report, answer, progress, "Islamov", n, images or None)
        path = REG / "out" / f"{n}-{time.strftime('%Y%m%d-%H%M')}.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return {"report": report, "answer": answer, "question": question, "pdf": str(path) if path else None,
            "visual": visual_data}


async def main(args) -> None:
    from services.ai import marking_config, usage
    from services.ai.scoring import overall_band
    from services.ai.writing_grader import decide_scores

    setup_logging()
    expectations = marking_config.regression_expectations()
    config = marking_config.calibration()
    records = [r for r in load_records() if str(r["essay_number"]) in expectations]
    if args.only:
        records = [r for r in records if str(r["essay_number"]) in args.only.split(",")]
    budget = Budget(args.budget)
    results, passed = [], []

    async def case(rec):
        with usage.track(f"regression #{rec['essay_number']}") as calls:
            got = await run_case(rec, not args.no_pdf)
        got["cost_by_step"] = usage.summary(calls)["by_purpose"]  # what to cut when a check passes the cost limit
        return rec, got, budget.add(calls)

    for rec, got, cost in await asyncio.gather(*(case(rec) for rec in records)):
        n, r = rec["essay_number"], got["report"]
        checks = expected_checks(n, r, cost, expectations[str(n)], config)
        if n == 35:
            checks += report35_checks(r, got["question"])
        before = rec["criteria_scores"]
        now = " ".join(f"{c['key'].replace('TR_or_TA', 'TA')} {c['band']:.0f}" for c in r.criteria)
        print(f"\n=== #{n} ({rec['task_type']})  before {rec['overall_band']:.1f} "
              f"(TA {before['TA']:.0f} CC {before['CC']:.0f} LR {before['LR']:.0f} GRA {before['GRA']:.0f})  ->  now "
              f"{r.overall_band:.1f} ({now})  ${cost:.3f}")
        print("   cost by step: " + ", ".join(f"{k} ${v:.3f}" for k, v in got["cost_by_step"].items()))
        for c in r.criteria:
            print(f"   {c['name']}: {c['note']}")
        if r.notices:
            print(f"   notices: {r.notices}")
        print("   mistakes: " + "; ".join(f"[{m['criterion']}] {m['wrong']} -> {m['correct']}" for m in r.mistakes))
        print("   priorities: " + "; ".join(f"{p['n']} {p['title']}" for p in r.priorities))
        if got["answer"] is not None:
            print(f"   model answer: {got['answer'].word_count} words, score {got['answer'].score}, "
                  f"{got['answer'].log.get('quality')}")
        for name, ok, detail in checks:
            print(f"   {'PASS' if ok else 'FAIL'}  {name}  [{detail}]")
            passed.append(ok)
        if got["pdf"]:
            print(f"   PDF: {got['pdf']}")
        results.append({"essay_number": n, "overall": r.overall_band, "before": rec["overall_band"],
                        "criteria": {c["key"]: c["band"] for c in r.criteria}, "before_criteria": before,
                        "notes": {c["key"]: c["note"] for c in r.criteria}, "notices": r.notices,
                        "mistakes": r.mistakes, "question": got["question"], "checks": checks, "cost": cost,
                        "cost_by_step": got["cost_by_step"],
                        "model_answer": got["answer"].paragraphs if got["answer"] else None, "pdf": got["pdf"],
                        "visual": got["visual"]})

    if not args.only:
        by_id = {s["id"]: s for s in load_samples()}
        for sample_id in ("v2-p072", "v2-p026"):
            s = by_id[sample_id]
            task = "task1" if s["task"] == "T1" else "task2"
            with usage.track(f"regression {sample_id}") as calls:
                sheet = ""
                if s["chart"]:
                    from services.ai.visual_check import data_check

                    reading, sheet, _ = await visual_for(s)
                    sheet = f"{sheet}\n\n{data_check(s['text'], reading, question_of(s))}".strip()
                scores = await decide_scores(task, question_of(s), s["text"], chart_images(s) or None, sheet)
            budget.add(calls)
            ours = overall_band(list(scores.bands.values()))
            ok = s["band_low"] - 0.5 <= ours <= s["band_high"] + 0.5
            passed.append(ok)
            print(f"\n=== anchor {sample_id} (Band {s['band_low']}-{s['band_high']}): ours {ours:.1f} "
                  f"({scores.bands})  {'PASS' if ok else 'FAIL'} (within ±0.5)")
            results.append({"anchor": sample_id, "ours": ours, "bands": scores.bands, "notes": scores.notes,
                            "pass": ok})

    print(f"\n{sum(passed)}/{len(passed)} checks passed; spent ${budget.spent:.2f}")
    out = CAL / "results" / f"b6-{time.strftime('%Y%m%d-%H%M')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"Saved {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="essay numbers, e.g. 35,41")
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--budget", type=float, default=3.0)
    asyncio.run(main(ap.parse_args()))
