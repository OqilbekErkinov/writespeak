"""The chart reading fixes of 2026-11-26 (24 Cambridge 13-19 Task 1 visuals read by Bandzilla and set against two
expert readings): offline, no OpenAI.

    python tests/chart_reading_test.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.ai import chart_measure as cm  # noqa: E402
from services.ai import visual_features  # noqa: E402
from services.ai import visual_reading as vr  # noqa: E402
from services.ai.schemas import (  # noqa: E402
    DataValue,
    LegendEntry,
    MapFeature,
    MapFeatureState,
    ProcessInfo,
    ProcessStage,
    SheetBlock,
    SheetValue,
    VisualBlock,
    VisualExtraction,
)

fails: list[str] = []


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


def block(kind="line", points=(), data=(), legend=(), axis=(), features=(), process=None) -> VisualBlock:
    return VisualBlock(visual_type=kind, title="t", units="%", time_points=list(points), axis_labels=list(axis),
                       legend=[LegendEntry(label=s, colour=c) for s, c in legend],
                       data=[DataValue(series=s, point=p, value=v, text="", printed=pr, precision=0.5)
                             for s, p, v, pr in data],
                       map_features=list(features), overall="", process=process, bars_drawn=[])


def read(*blocks, box=(0.1, 0.4, 0.9, 0.8)) -> VisualExtraction:
    return VisualExtraction(visuals=list(blocks), unreadable=[], readability="clear", readability_note="",
                            chart_box=list(box), rotation=0)


def months_and_votes() -> None:
    initials = list("JFMAMJJASOND")
    copper = [2, 1, 0.5, 0.5, -0.5, -0.5, 1, 1, 1, 0.5, 0.5, 1.5]
    b = vr._capped(block(points=initials, data=[("Copper", p, v, False) for p, v in zip(initials, copper)],
                         axis=[str(x) for x in range(-4, 8)]))
    check("J F M A M J J A S O N D: the twelve months, each figure its own month (Cambridge 18 test 4)",
          b.time_points == vr.MONTHS and [(v.point, v.value) for v in b.data] == list(zip(vr.MONTHS, copper)))
    full = vr._capped(block(points=["January", "February", "March"], data=[("A", "March", 3, False)]))
    check("months written in full or abbreviated are one month", full.time_points == ["Jan", "Feb", "Mar"]
          and full.data[0].point == "Mar")
    other = vr._capped(block(points=["Q1", "Q2", "Q1"], data=[("A", "Q1", 1, False), ("A", "Q2", 2, False),
                                                               ("A", "Q1", 3, False)]))
    check("other repeated labels get their order", other.time_points == ["Q1", "Q2", "Q1 (2)"]
          and [v.point for v in other.data] == ["Q1", "Q2", "Q1 (2)"])

    check("two of three within half a gridline: their mean", vr._vote(10, 20, 11, 5) == (10.5, False))
    check("...a read that gave no figure: the other and the third agree", vr._vote(None, 462.9, 462.9, 0.5)
          == (462.9, False))
    check("...no two agree: not settled", vr._vote(10, 30, 20, 5) is None)

    # a printed figure one read missed is kept, uncertain - not left empty (Cambridge 13 test 3, France 462.9)
    x = block("bar", data=[("Consumption", "France", None, False)])
    y = block("bar", data=[("Consumption", "France", 462.9, True)])
    merged = vr._merge_values(x, y)
    check("one read's printed figure, the other's none: kept, waiting for a third", merged[0].value == 462.9
          and merged[0].status == "uncertain" and vr._readings(merged[0].note) == [None, 462.9], merged[0])

    # the cropped read 3 replaced read 2: read 2 still decides what reads 1 and 3 disagree on (Cambridge 17 test 4)
    pts = ["2011", "2012"]
    r1 = read(block("line", pts, [("Openings", "2011", 8500, False), ("Openings", "2012", 3900, False)],
                    axis=["0", "2000", "4000", "6000", "8000"]))
    r3 = read(block("line", pts, [("Openings", "2011", 6700, False), ("Openings", "2012", 3900, False)],
                    axis=["0", "2000", "4000", "6000", "8000"]))
    r2 = read(block("line", pts, [("Openings", "2011", 8400, False), ("Openings", "2012", 3900, False)],
                    axis=["0", "2000", "4000", "6000", "8000"]))
    sheet = vr.third_opinion(vr.combine(r1, r3), r2, "read 2")
    cell = next(v for v in sheet.blocks[0].values if v.point == "2011")
    check("read 2 settles 2011 with read 1 (8500/8400), the cropped read's swap set aside",
          cell.status == "verified" and cell.value == 8450, cell)
    check("the reads' boxes together: a box that cut the labels off is widened",
          vr.union_box([[0.1, 0.36, 0.9, 0.76], [0.09, 0.49, 0.92, 0.86], None]) == [0.09, 0.36, 0.92, 0.86])


def maps() -> None:
    def f(name, loc, *states):
        return MapFeature(name=name, location=loc,
                          states=[MapFeatureState(time=t, status=s, detail="") for t, s in zip(("now", "future"), states)])

    x = block("map", features=[f("Outdoor courts", "west of the building", "present", "absent"),
                               f("Outdoor courts", "east of the building", "present", "absent"),
                               f("Northwestern rose garden", "north-west corner", "present", "present"),
                               f("Southwestern rose garden", "south-west corner", "present", "absent"),
                               f("Changing room (SE)", "south-east corner", "absent", "present")])
    y = block("map", features=[f("Rose garden", "south-west, on Eldon Street", "present", "absent"),
                               f("Outdoor courts", "east side", "present", "absent"),
                               f("Outdoor courts", "west side", "present", "absent"),
                               f("Rose garden", "north-west", "present", "present")])
    unreadable: list[str] = []
    kept = vr._merge_map(x, y, unreadable)
    names = [k.name for k in kept]
    check("features printed under one name are each kept, paired by place (Cambridge 13 test 4: two courts)",
          names.count("Outdoor courts") == 2 and not any(k.location.startswith("UNCERTAIN - the reads disagree")
                                                          for k in kept), [(k.name, k.location) for k in kept])
    check("...a name with or without its place ('Northwestern rose garden' / 'Rose garden') is one feature",
          "Northwestern rose garden" in names and "Southwestern rose garden" in names
          and not any("rose" in u.lower() for u in unreadable), unreadable)
    one = next(k for k in kept if k.name.startswith("Changing room"))
    check("...one seen by one read only is kept as UNCERTAIN, never dropped", one.location.startswith("UNCERTAIN"))

    check("lines with different markers are no look-alikes (Cambridge 18 test 1)",
          not vr._alike("Black dotted line with hollow diamond markers", "Black dashed line with × markers")
          and vr._alike("chequered grey", "dotted grey"))


def measuring() -> None:
    t = cm.Token("1970 1980 1990 2000", 100, 500, 500, 520)
    parts = cm.split_token(t)
    check("a row of numbers read as one: one token each, placed by its characters (Cambridge 18 test 1)",
          [p.text for p in parts] == ["1970", "1980", "1990", "2000"] and parts[0].cx < parts[1].cx < parts[3].cx
          and cm.split_token(cm.Token("Number of hours", 0, 0, 10, 10)) == [cm.Token("Number of hours", 0, 0, 10, 10)])
    xs = [100 + 50 * i for i in range(12)]
    row = [cm.Token(ch, x - 5, 900, x + 5, 915) for ch, x in zip("JFMAMJJASOND", xs)]
    markers = [cm.Token("X", 333, 400, 343, 412), cm.Token("X", 520, 600, 530, 612)]
    got = cm.point_positions(vr.MONTHS, row + markers, ordered=True)
    check("months printed by their initials found in order, markers read as 'X' left out (Cambridge 18 test 4)",
          got is not None and [round(got[m]) for m in vr.MONTHS] == xs, got)
    missing = [r for i, r in enumerate(row) if i != 6]  # July's "J" not read
    got = cm.point_positions(vr.MONTHS, missing, ordered=True)
    check("...one initial the OCR missed: placed by the spacing", got is not None and round(got["Jul"]) == xs[6], got)


def features() -> None:
    # Cambridge 19 test 4 as Bandzilla read it: Tap 450 vs 420 within the figures' precision (10 + 25)
    sheet_block = SheetBlock(
        visual_type="bar", title="Dance", units="students", time_points=[],
        axis_labels=["0", "100", "200", "300", "400", "500", "600", "700"],
        legend=[LegendEntry(label="Under 11", colour="black"), LegendEntry(label="11-16", colour="grey")],
        values=[SheetValue(series=s, point=p, value=v, text="", printed=False, precision=e, status="verified")
                for s, p, v, e in (("Under 11", "Ballet", 600, 10), ("11-16", "Ballet", 300, 10),
                                   ("Under 11", "Tap", 450, 10), ("11-16", "Tap", 420, 25),
                                   ("Under 11", "Modern", 300, 10), ("11-16", "Modern", 520, 25))],
        map_features=[], overall="", process=None)
    from services.ai.schemas import KeyFeatures, VisualSheet

    sheet = VisualSheet(blocks=[sheet_block], readability="clear", readability_note="", unreadable=[], checks=[],
                        features=KeyFeatures(expected_overview=[], key_comparisons=[], suggested_grouping=""), reads=2)
    overview = " ".join(visual_features.key_features(sheet).expected_overview)
    check("one case each way and a close one: no 'higher in most cases' (Cambridge 19 test 4)",
          "most cases" not in overview and "about the same in Tap" in overview, overview)
    proc = SheetBlock(visual_type="process", title="p", units="", time_points=[], axis_labels=[], legend=[],
                      values=[], map_features=[], overall="",
                      process=ProcessInfo(steps=[f"s{i}" for i in range(1, 9)], numbered=True,
                                          stages=[ProcessStage(name="Storage", steps=[1, 2, 7]),
                                                  ProcessStage(name="Day", steps=[3, 4, 5, 6])],
                                          kind="cyclical", natural_or_manmade="man-made"))
    sheet = sheet.model_copy(update={"blocks": [proc]})
    cmp = visual_features.key_features(sheet).key_comparisons
    check("a stage of steps 1, 2 and 7 is no 'steps 1-7' (Cambridge 14 test 3)",
          "Storage: steps 1, 2 and 7." in cmp and "Day: steps 3-6." in cmp, cmp)


if __name__ == "__main__":
    months_and_votes()
    maps()
    measuring()
    features()
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    sys.exit(1 if fails else 0)
