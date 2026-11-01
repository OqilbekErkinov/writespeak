"""Marking only, for calibration: the marks and the bands of a list of answers,
with no model answer, vocabulary, PDF, Telegram or database - nothing a student
could see, and the least a check can cost.

    python scripts/marks_check.py CASES.json OUT_DIR --cap 3.5 [--gate 8:0.5,16:0.25]

CASES.json: a list of {"name", "kind": "task1"|"task2", "prompt_text",
"answer_text", "chart": path (Task 1), "examiner": band (a calibration essay),
"source": "text"|"photo"|"pdf"}. OUT_DIR/results.json grows after every case:
bands, the runs, every flag with its final class, the cost.

--cap: stops before the next case once the recorded cost reaches it.
--gate N:LIMIT,...: after the first N cases, stops when the mean difference
from the examiner is beyond +-LIMIT or an essay at Band 6 or below rose by more
than 0.5 (the academic lead, 2026-11-01). Two failures in a row stop it too.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["MARKING_CACHE_ENABLED"] = "0"  # every case is marked afresh


def gate_failed(results: list[dict], limit: float = 0.25) -> str | None:
    rows = [r for r in results if r.get("examiner") is not None and "overall" in r]
    if not rows:
        return None
    diffs = [r["overall"] - r["examiner"] for r in rows]
    mean = sum(diffs) / len(diffs)
    if abs(mean) > limit:
        return f"the mean difference from the examiner is {mean:+.2f}, beyond +-{limit:g}"
    risen = [r["name"] for r in rows if r["examiner"] <= 6 and r["overall"] - r["examiner"] > 0.5]
    return f"low-band essays rose: {', '.join(risen)}" if risen else None


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cases")
    parser.add_argument("out")
    parser.add_argument("--cap", type=float, required=True)
    parser.add_argument("--gate", default="")
    args = parser.parse_args()

    from services.ai import usage
    from services.ai.essay_marking import clean_pasted
    from services.ai.visual_reading import judge_ready, read_visual, sheet_text
    from services.ai.writing_grader import grade_writing

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    gates = {int(g.split(":")[0]): float(g.split(":")[1]) for g in args.gate.split(",") if g}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    spent = 0.0
    for n, case in enumerate(cases, 1):
        if spent >= args.cap:
            print(f"STOP: ${spent:.2f} spent, the cap is ${args.cap:.2f}; {len(cases) - n + 1} cases not run", flush=True)
            break
        print(f"== {n}/{len(cases)} {case['name']}", flush=True)
        kind = case["kind"]
        answer, _ = clean_pasted(case["answer_text"])
        images, visual_data, judged, figures = None, None, "", []
        result = {"name": case["name"], "kind": kind, "examiner": case.get("examiner"), "words": len(answer.split())}
        try:
            with usage.track(f"marks {case['name']}") as calls:
                if kind == "task1" and case.get("chart"):
                    data = Path(case["chart"]).read_bytes()
                    images = [(data, "image/png" if data[:4] == b"\x89PNG" else "image/jpeg")]
                    visual = await read_visual(case["prompt_text"], images)
                    if visual is not None and sheet_text(visual):
                        visual_data, _, judged, _ = await judge_ready(answer, visual.model_dump(), case["prompt_text"],
                                                                      images)
                marks = await grade_writing(task_type=kind, prompt_text=case["prompt_text"], answer_text=answer,
                                            prompt_images=images, sheet=judged, visual=visual_data,
                                            text_source=case.get("source", "text"), marks_only=True)
            summary = usage.summary(calls)
            result.update(overall=marks.overall, scores=marks.bands, runs=marks.runs, capped=marks.capped,
                          flags=marks.flags, strong=marks.strong, cost=summary["total_usd"],
                          cost_by_step=summary.get("by_purpose"))
            spent += summary["total_usd"]
            if visual_data:
                result["figures"] = [{"series": v["series"], "point": v["point"], "value": v["value"],
                                      "note": v.get("note", ""), "status": v.get("status")}
                                     for b in visual_data.get("blocks", []) for v in b.get("values", [])]
            print(f"   {marks.overall:g} {marks.bands} examiner {case.get('examiner')} ${summary['total_usd']:.3f}", flush=True)
        except Exception as e:  # one case failing doesn't lose the others
            result["error"] = repr(e)
            print("   FAILED:", repr(e), flush=True)
        results.append(result)
        (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        if n in gates and (why := gate_failed(results, gates[n])):
            print(f"STOP at the gate: {why}. The rest was not run.", flush=True)
            break
        if len(results) >= 2 and all("error" in r for r in results[-2:]):
            print("STOP: two failures in a row. The rest was not run.", flush=True)
            break
    print(f"DONE {len(results)} of {len(cases)} cases, ${spent:.2f}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
