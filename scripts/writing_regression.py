"""Regression tests for the calibrated Writing marking (marking guide B6,
2026-10-22): the owner's own reports #35-#38 (Islamov), pulled read-only
from the database into data/calibration/regression/islamov_35_38.json,
go through the whole check as the bot runs it - the Task 1 photo read for
its question and its chart, the scores, the feedback, the mistake filter,
the model answer and the PDF - and must meet:

  #35 Task 1  6.0; the TA note names the overview, and the 145 words are
              noted; "were quite passive" is a Lexical mistake, not Task;
              the question box shows no chart figures
  #36 Task 2  5.5-6.0; CC 5 at most (one paragraph); GRA not 5
  #37 Task 2  6.5 (6.5-7.0 accepted); LR 7 or more, the note crediting it
  #38 Task 2  7.0-7.5; GRA 7 or more; "differently than" and "living
              healthier" not listed as mistakes
plus both Band 6.5 anchors (Volume 2 pp.72 and 26) within ±0.5.

    python scripts/writing_regression.py [--only 35,36] [--no-pdf] [--budget 3]

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


def _mistake(report, needle: str):
    return [m for m in report.mistakes if needle.lower() in m["wrong"].lower()]


def checks_for(n: int, report, question: str) -> list[tuple[str, bool, str]]:
    b = {c["key"]: c["band"] for c in report.criteria}
    notes = {c["key"]: c["note"] for c in report.criteria}
    out = []
    if n == 35:
        out.append(("overall 6.0", report.overall_band == 6.0, f"{report.overall_band}"))
        out.append(("TA note names the overview", "overview" in notes["TR_or_TA"].lower(), notes["TR_or_TA"]))
        out.append(("the 145 words are noted", "145" in notes["TR_or_TA"] or any("145" in x for x in report.notices),
                    f"note: {notes['TR_or_TA']} | notices: {report.notices}"))
        passive = _mistake(report, "quite passive")
        out.append(("'were quite passive' is LR, not Task", bool(passive) and all(m["criterion"] == "LR" for m in passive),
                    f"{[(m['wrong'], m['criterion']) for m in passive] or 'not listed'}"))
        from services.pdf.report_builder import question_text

        shown = question_text(question, True)
        out.append(("question box: no chart figures", not re.search(r"\d+\s*%", shown), shown.replace("\n", " / ")))
    elif n == 36:
        out.append(("overall 5.5-6.0", 5.5 <= report.overall_band <= 6.0, f"{report.overall_band}"))
        out.append(("CC 5 at most (one paragraph)", b["CC"] <= 5, f"CC {b['CC']}"))
        out.append(("GRA not 5", b["GRA"] >= 6, f"GRA {b['GRA']}"))
    elif n == 37:
        out.append(("overall 6.5 (6.5-7.0 accepted)", 6.5 <= report.overall_band <= 7.0, f"{report.overall_band}"))
        out.append(("LR 7 or more", b["LR"] >= 7, f"LR {b['LR']}: {notes['LR']}"))
    elif n == 38:
        out.append(("overall 7.0-7.5", 7.0 <= report.overall_band <= 7.5, f"{report.overall_band}"))
        out.append(("GRA 7 or more", b["GRA"] >= 7, f"GRA {b['GRA']}"))
        listed = _mistake(report, "differently than") + _mistake(report, "living healthier")
        out.append(("'differently than' / 'living healthier' not listed", not listed,
                    f"{[m['wrong'] for m in listed]}"))
    return out


async def run_case(rec: dict, pdf: bool) -> dict:
    from bot.handlers.writing import CHART_NOT_CHECKED, CHART_PARTLY_CHECKED
    from services.ai.ocr import extract_text_from_image
    from services.ai.sample_bank import build_model_answer, needs_from_feedback
    from services.ai.visual_reading import read_visual, sheet_text
    from services.ai.writing_grader import grade_writing
    from services.pdf.report_builder import build_writing_report_pdf
    from services.storage.file_storage import prepare_vision_image

    n, task = rec["essay_number"], rec["task_type"]
    question, images, sheet, visual, notices = rec["prompt_text"], [], "", None, []
    photo = REG / f"islamov_{n}_chart.jpg"
    if task == "task1" and photo.exists():
        content, mime, _ = prepare_vision_image(photo.read_bytes())
        images = [(content, mime)]
        question = await extract_text_from_image(content, mime_type=mime, mode="writing_task1_question")
        visual = await read_visual(question, images)
        sheet = sheet_text(visual)
        if visual is not None and visual.readability == "partly_unclear":
            notices.append(CHART_PARTLY_CHECKED)
    elif task == "task1":
        notices.append(CHART_NOT_CHECKED)
    holder = {}

    def start_answer(bands, overall, feedback):
        holder["needs"] = needs_from_feedback(bands, overall, feedback, rec["answer_text"])
        holder["task"] = asyncio.create_task(build_model_answer(task, question, images or None, seed=n,
                                                                needs=holder["needs"], sheet=sheet))

    from services.ai.visual_check import data_check

    judged = f"{sheet}\n\n{data_check(rec['answer_text'], visual.model_dump(), question)}".strip() if sheet else ""
    report = await grade_writing(task, question, rec["answer_text"], images or None, on_feedback=start_answer,
                                 sheet=judged, notices=notices)
    answer = await holder["task"] if "task" in holder else None
    path = None
    if pdf:
        progress = {"first": True, "note": "Regression run - progress is not compared."}
        data = await asyncio.to_thread(build_writing_report_pdf, report, answer, progress, "Islamov", n, images or None)
        path = REG / "out" / f"{n}-{time.strftime('%Y%m%d-%H%M')}.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return {"report": report, "answer": answer, "question": question, "sheet": sheet, "pdf": str(path) if path else None,
            "visual": visual.model_dump() if visual is not None else None}


async def main(args) -> None:
    from services.ai import usage
    from services.ai.scoring import overall_band
    from services.ai.writing_grader import decide_scores

    setup_logging()
    records = json.loads((REG / "islamov_35_38.json").read_text(encoding="utf-8"))
    if args.only:
        records = [r for r in records if str(r["essay_number"]) in args.only.split(",")]
    budget = Budget(args.budget)
    results, passed = [], []

    async def case(rec):
        with usage.track(f"regression #{rec['essay_number']}") as calls:
            got = await run_case(rec, not args.no_pdf)
        cost = budget.add(calls)
        r = got["report"]
        checks = checks_for(rec["essay_number"], r, got["question"])
        return rec, got, cost, checks

    for rec, got, cost, checks in await asyncio.gather(*(case(rec) for rec in records)):
        r = got["report"]
        before = rec["criteria_scores"]
        now = " ".join(f"{c['key'].replace('TR_or_TA', 'TA')} {c['band']:.0f}" for c in r.criteria)
        print(f"\n=== #{rec['essay_number']} ({rec['task_type']})  before {rec['overall_band']:.1f} "
              f"(TA {before['TA']:.0f} CC {before['CC']:.0f} LR {before['LR']:.0f} GRA {before['GRA']:.0f})  ->  now "
              f"{r.overall_band:.1f} ({now})  ${cost:.3f}")
        for c in r.criteria:
            print(f"   {c['name']}: {c['note']}")
        if r.notices:
            print(f"   notices: {r.notices}")
        print("   mistakes: " + "; ".join(f"[{m['criterion']}] {m['wrong']} -> {m['correct']}" for m in r.mistakes))
        if got["answer"] is not None:
            print(f"   model answer: {got['answer'].word_count} words, {got['answer'].log.get('quality')}")
        for name, ok, detail in checks:
            print(f"   {'PASS' if ok else 'FAIL'}  {name}  [{detail}]")
            passed.append(ok)
        if got["pdf"]:
            print(f"   PDF: {got['pdf']}")
        results.append({"essay_number": rec["essay_number"], "overall": r.overall_band,
                        "criteria": {c["key"]: c["band"] for c in r.criteria},
                        "notes": {c["key"]: c["note"] for c in r.criteria}, "notices": r.notices,
                        "mistakes": r.mistakes, "question": got["question"], "checks": checks, "cost": cost,
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
            mid = (s["band_low"] + s["band_high"]) / 2
            ok = s["band_low"] - 0.5 <= ours <= s["band_high"] + 0.5
            passed.append(ok)
            print(f"\n=== anchor {sample_id} (Band {s['band_low']}-{s['band_high']}): ours {ours:.1f} "
                  f"({scores.bands})  {'PASS' if ok else 'FAIL'} (within ±0.5)")
            results.append({"anchor": sample_id, "ours": ours, "bands": scores.bands, "notes": scores.notes,
                            "book_mid": mid, "pass": ok})

    print(f"\n{sum(passed)}/{len(passed)} checks passed; spent ${budget.spent:.2f}")
    out = CAL / "results" / f"b6-{time.strftime('%Y%m%d-%H%M')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"Saved {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="essay numbers, e.g. 35,38")
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--budget", type=float, default=3.0)
    asyncio.run(main(ap.parse_args()))
