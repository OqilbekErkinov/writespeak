"""Task 1 charts measured from the picture (services/ai/chart_measure.py) and
the measured figures set against the reads (visual_reading.apply_measured,
settle) - offline, no OpenAI (local OCR only):

    python tests/chart_measure_test.py

Charts are drawn here with known values - coloured and black-and-white
lines (dashed, with markers), grouped and horizontal bars, a picture turned
a little - and every measured figure must be within a fifth of a gridline
step of the drawn one. Then the rules (the academic lead, 2026-10-29): a
measured figure stands over the reads; a misread axis gives way to the
printed one; without a measurement, two of three readings within half a
gridline settle a figure; a focused re-read never overrules a measured one.
"""
import asyncio
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.ai import chart_measure, visual_reading  # noqa: E402
from services.ai.visual_check import protected_mistake  # noqa: E402
from services.ai.schemas import FocusedRead, SettledFigure, SettleReads, VisualSheet  # noqa: E402

fails: list[str] = []


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


def png(fig, turn: float = 0.0, quality: int = 80) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    img = Image.open(buf).convert("RGB")
    if turn:
        img = img.rotate(turn, expand=True, fillcolor=(255, 255, 255), resample=Image.BICUBIC)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=quality)
    return out.getvalue()


def line_chart(series: dict[str, list[float]], years: list[str], step: float, top: float, colour: bool,
               turn: float = 0.0) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 5), dpi=90)
    ax.grid(axis="y", color="#cccccc")
    ax.set_axisbelow(True)
    styles = ["-", "--", "-.", ":"]
    for k, (name, ys) in enumerate(series.items()):
        ax.plot(range(len(years)), ys, label=name, linewidth=2,
                color=f"C{k}" if colour else ["#000000", "#555555", "#888888"][min(k, 2)],
                linestyle="-" if colour else styles[k], marker=None if colour else "os^"[k % 3], markersize=5)
    ax.set_xticks(range(len(years)), years)
    ax.set_ylim(0, top)
    ax.set_yticks(np.arange(0, top + step / 2, step))
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=len(series), frameon=False)
    fig.tight_layout()
    return png(fig, turn)


def bar_chart(series: dict[str, list[float]], cats: list[str], step: float, top: float, colours: list[str],
              horizontal: bool = False) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 5), dpi=90)
    width = 0.8 / len(series)
    for k, (name, vals) in enumerate(series.items()):
        pos = np.arange(len(cats)) + (k - (len(series) - 1) / 2) * width
        (ax.barh if horizontal else ax.bar)(pos, vals, width, label=name, color=colours[k])
    if horizontal:
        ax.set_yticks(range(len(cats)), cats)
        ax.invert_yaxis()
        ax.set_xlim(0, top)
        ax.set_xticks(np.arange(0, top + step / 2, step))
    else:
        ax.set_xticks(range(len(cats)), cats)
        ax.set_ylim(0, top)
        ax.set_yticks(np.arange(0, top + step / 2, step))
    ax.legend(loc="upper right")
    fig.tight_layout()
    return png(fig)


def block(kind: str, series: dict[str, list[float]], points: list[str], step: float, slack: float) -> dict:
    """A read's block: the drawn figures, each off by `slack` steps as a read by eye would be."""
    rng = np.random.default_rng(7)
    data = [{"series": s, "point": p, "value": round(v + rng.uniform(-slack, slack) * step, 1), "printed": False}
            for s, vals in series.items() for p, v in zip(points, vals)]
    return {"visual_type": kind, "legend": [{"label": s, "colour": ""} for s in series],
            "time_points": points if kind == "line" else [], "axis_labels": [], "values": data}


def measured_ok(name: str, data: bytes, blk: dict, series: dict[str, list[float]], points: list[str],
                step: float, share: float) -> None:
    got = chart_measure.measure(chart_measure.prepare(data), [blk])
    figures = got[0].figures if 0 in got else {}
    truth = {(s, p): v for s, vals in series.items() for p, v in zip(points, vals)}
    errors = [abs(m.value - truth[k]) / step for k, m in figures.items()]
    check(f"{name}: {len(figures)}/{len(truth)} measured, each within 0.2 of a step "
          f"(worst {max(errors, default=0):.2f})",
          len(figures) >= share * len(truth) and errors and max(errors) <= 0.2,
          {k: (round(m.value, 1), truth[k]) for k, m in figures.items() if abs(m.value - truth[k]) > 0.2 * step})


def pictures() -> None:
    years = ["1990", "1995", "2000", "2005", "2010", "2015"]
    lines = {"Canada": [12, 18.5, 24, 31, 29.5, 36], "Japan": [30, 27, 22.5, 20, 14, 11],
             "Brazil": [5, 9, 15.5, 14, 21, 26.5]}
    measured_ok("coloured lines", line_chart(lines, years, 5, 40, True), block("line", lines, years, 5, 0.6),
                lines, years, 5, 0.9)
    measured_ok("black-and-white lines (solid, dashed, dash-dot; markers)", line_chart(lines, years, 5, 40, False),
                block("line", lines, years, 5, 0.6), lines, years, 5, 0.7)
    measured_ok("a picture turned 1 degree", line_chart(lines, years, 5, 40, True, turn=1.0),
                block("line", lines, years, 5, 0.6), lines, years, 5, 0.8)
    cats = ["Food", "Rent", "Transport", "Leisure"]
    bars = {"2010": [120, 340, 85, 210], "2020": [160, 310, 140, 255]}
    measured_ok("grouped coloured bars", bar_chart(bars, cats, 50, 400, ["#1f77b4", "#ff7f0e"]),
                block("bar", bars, cats, 50, 0.4), bars, cats, 50, 1.0)
    measured_ok("grouped grey bars (told apart by place)", bar_chart(bars, cats, 50, 400, ["#333333", "#aaaaaa"]),
                block("bar", bars, cats, 50, 0.4), bars, cats, 50, 1.0)
    measured_ok("horizontal bars", bar_chart(bars, cats, 50, 400, ["#2ca02c", "#d62728"], horizontal=True),
                block("bar", bars, cats, 50, 0.4), bars, cats, 50, 0.75)


def sheet(values: list[dict], axis: list[str], kind: str = "line") -> VisualSheet:
    full = [{"text": "", "printed": False, "precision": 7.5, "status": "verified", "note": "", **v} for v in values]
    return VisualSheet.model_validate({
        "blocks": [{"visual_type": kind, "title": "Oregon", "units": "thousands", "time_points": ["1940", "1970"],
                    "axis_labels": axis, "legend": [], "values": full, "map_features": [], "overall": "",
                    "process": None}],
        "readability": "partly_unclear", "readability_note": "some figures could not be confirmed", "unreadable": [],
        "checks": [], "features": {"expected_overview": [], "key_comparisons": [], "suggested_grouping": ""},
        "reads": 2})


def result(figures: dict, low=0.0, high=255.0, step=15.0):
    figures = {k: chart_measure.Measured(v, "test") for k, v in figures.items()}
    return chart_measure.BlockResult(figures, low, high, step)


def rules() -> None:
    axis = [str(v) for v in range(0, 256, 15)]
    s = sheet([{"series": "Washington", "point": "1970", "value": 120},
               {"series": "Washington", "point": "1940", "value": 67.5, "precision": 15, "status": "uncertain",
                "note": "the reads disagree: 60 vs 75"},
               {"series": "Columbia", "point": "1970", "value": 50},
               {"series": "Yamhill", "point": "2000", "value": 85, "precision": 15, "status": "uncertain",
                "note": "its legend colour is shared with another category"}], axis)
    out = visual_reading.apply_measured(s, {0: result({("Washington", "1970"): 125.2, ("Washington", "1940"): 74.6,
                                                       ("Columbia", "1970"): 35.3, ("Yamhill", "2000"): 90.4})})
    cells = {(v.series, v.point): v for v in out.blocks[0].values}
    w70, w40, c70, y00 = (cells[k] for k in (("Washington", "1970"), ("Washington", "1940"), ("Columbia", "1970"),
                                             ("Yamhill", "2000")))
    check("a measured figure stands over the reads (Washington 1970: read 120, measured 125.2 -> 125)",
          w70.value == 125 and w70.status == "verified" and w70.note == "measured from the picture (read 120)"
          and w70.precision == 7.5, w70)
    check("...over reads one gridline apart (Washington 1940: 60 vs 75, measured 74.6 -> 75)",
          w40.value == 75 and w40.status == "verified" and w40.note == "measured from the picture (read 60 / 75)", w40)
    check("...and over reads that agree a step away (Columbia 1970: read 50, measured 35.3 -> 35)",
          c70.value == 35 and c70.status == "verified", c70)
    far = visual_reading.apply_measured(
        sheet([{"series": "2006", "point": "16-24", "value": 95, "precision": 5}], [str(v) for v in range(0, 121, 20)],
              kind="bar"), {0: result({("2006", "16-24"): 17.5}, 0, 120, 20)})
    check("a measurement more than 1.5 steps from every read is not trusted: the third read decides "
          "(#107: a striped bar measured 18 for 90-100)",
          far.blocks[0].values[0].status == "uncertain"
          and far.blocks[0].values[0].note == "the reads disagree: 95 vs 17.5", far.blocks[0].values[0])
    check("a code check's doubt (a legend colour shared) is not answered by a measurement: still uncertain",
          y00.status == "uncertain" and y00.value == 85, y00)
    check("the sheet stays 'partly unclear' while a figure is uncertain", out.readability == "partly_unclear")

    misread = sheet([{"series": "Washington", "point": "2000", "value": 232.5}],
                    [str(v) for v in range(25, 226, 25)])
    out = visual_reading.apply_measured(misread, {0: result({("Washington", "2000"): 245.1})})
    cell = out.blocks[0].values[0]
    check("reads on a misread axis (0-225 by 25 for 0-255 by 15): the measured figure stands and the axis is set "
          "right", cell.value == 245 and cell.status == "verified" and out.blocks[0].axis_labels[-1] == "255"
          and out.readability == "clear" and any("the printed axis 15" in c for c in out.checks),
          (cell, out.blocks[0].axis_labels, out.checks))
    check("a measured figure is given to a fitting grain (an eighth of the step: 1 on a 15 axis, 2.5 on a 25 one, "
          "0.25 on a 2 one)", visual_reading._granularity(15) == 1 and visual_reading._granularity(25) == 2.5
          and visual_reading._granularity(2) == 0.25 and visual_reading._granularity(250) == 25)

    # the third read (no measurement): two of three within half a gridline -> their mean
    s2 = sheet([{"series": "Washington", "point": "1940", "value": 67.5, "precision": 15, "status": "uncertain",
                 "note": "the reads disagree: 60 vs 75"},
                {"series": "Yamhill", "point": "1940", "value": 30, "precision": 17.5, "status": "uncertain",
                 "note": "the reads disagree: 20 vs 40"},
                {"series": "Columbia", "point": "1940", "value": 25, "precision": 12.5, "status": "uncertain",
                 "note": "the reads disagree: 20 vs 30"}], axis)

    async def third_read(**kw):
        figures = [SettledFigure(series="Washington", point="1940", value=72, how=""),
                   SettledFigure(series="Yamhill", point="1940", value=55, how=""),
                   SettledFigure(series="Columbia", point="1940", value=26, how="")]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=SettleReads(figures=figures)))])
    real = visual_reading.parse_flex
    visual_reading.parse_flex = third_read
    try:
        settled = asyncio.run(visual_reading.settle("q", [(b"x", "image/png")], s2))
    finally:
        visual_reading.parse_flex = real
    cells = {(v.series, v.point): v for v in settled.blocks[0].values}
    check("third read: two of three within half a gridline -> verified at those two's mean "
          "(Washington 60 / 75 / 72 -> 73.5)",
          cells[("Washington", "1940")].status == "verified" and cells[("Washington", "1940")].value == 73.5,
          cells[("Washington", "1940")])
    check("...the closer two when the third read leans to one (Columbia 20 / 30 / 26 -> 28)",
          cells[("Columbia", "1940")].status == "verified" and cells[("Columbia", "1940")].value == 28,
          cells[("Columbia", "1940")])
    check("...no two within half a gridline -> still uncertain (Yamhill 20 / 40 / 55)",
          cells[("Yamhill", "1940")].status == "uncertain", cells[("Yamhill", "1940")])

    # a focused re-read never overrules a measured figure
    visual = sheet([{"series": "Washington", "point": "1970", "value": 125, "note": "measured from the picture"}],
                   axis).model_dump()

    async def re_read(question, images, sentence, figure):
        return FocusedRead(series="Washington", point="1970", found=True, missing=False, value=150, how="")
    real_one = visual_reading.read_one
    visual_reading.read_one = re_read
    try:
        checked = asyncio.run(visual_reading.check_report_figures(
            "In 1970, Washington had about 150,000 people.", visual, "population of three counties",
            [(b"x", "image/png")]))
    finally:
        visual_reading.read_one = real_one
    cell = checked["blocks"][0]["values"][0]
    check("a focused re-read siding with the student never overrules a measured figure: the figure is wrong",
          [x for x in checked["confirmed_wrong"] if "150,000" in x] and cell["status"] == "verified"
          and cell["value"] == 125,
          (checked["confirmed_wrong"], checked["accepted_figures"], cell))

    # one figure for three measured cells (#62: "In 2011, ... United States, Brazil and Japan rose to 6%")
    visual = sheet([{"series": s, "point": "2011", "value": v, "precision": 1.0, "note": "measured from the picture"}
                    for s, v in (("United States", 26), ("Brazil", 13), ("Japan", 18))]
                   + [{"series": "Brazil", "point": "2005", "value": 6, "precision": 1.0, "note": "measured from the picture"}],
                   axis).model_dump()

    async def no_cell(question, images, sentence, figure):
        return FocusedRead(series="", point="2011", found=False, missing=False, value=None, how="")
    visual_reading.read_one = no_cell
    try:
        checked = asyncio.run(visual_reading.check_report_figures(
            "In 2011, spending in each of the three countries, the United States, Brazil and Japan, rose to 6%.",
            visual, "household spending on leisure in three countries", [(b"x", "image/png")]))
        fair = asyncio.run(visual_reading.check_report_figures(
            "In 2011, spending in the United States, Brazil and Japan rose to 13%.",
            visual, "household spending on leisure in three countries", [(b"x", "image/png")]))
    finally:
        visual_reading.read_one = real_one
    check("a figure given for three measured cells that agrees with none is wrong, though the re-read finds no "
          "one cell (#62's 6%)", checked["confirmed_wrong"] == ["6%"], (checked["confirmed_wrong"],
                                                                     checked["accepted_figures"]))
    check("...one that agrees with one of them is not", "13%" not in fair["confirmed_wrong"],
          (fair["confirmed_wrong"], fair["accepted_figures"]))
    check("...and the examiner's Task mistake on it is kept, though 'rose to 6%' out of its sentence matches "
          "Brazil's 2005 figure", not protected_mistake("rose to 6%", "rose to 26%, 13% and 18% respectively",
                                                        "wrong figures", checked, "spending", set()))


if __name__ == "__main__":
    rules()
    pictures()
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    sys.exit(1 if fails else 0)
