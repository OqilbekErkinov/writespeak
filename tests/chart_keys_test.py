"""Chart keys (services/ai/chart_keys.py, webapp/charts.py) - offline, no
OpenAI; needs the throwaway Postgres + Redis of test_part2.sh:

    TEST=tests/chart_keys_test.py ./test_part2.sh

A chart the bot meets is kept as an "auto" key; meeting it again adds a use;
an admin's checked sheet is used instead of reading the chart - straight
away for the same picture, after one agreeing read for a photo of it - and
never for another chart with the same question text. The panel lists the
charts and saves an admin's edits.
"""
import asyncio
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

fails: list[str] = []


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


def chart_png(heights: list[int], photo: bool = False) -> bytes:
    """A bar chart picture; `photo`: the same chart as a phone photo of it
    (a border, slightly blurred, smaller, JPEG)."""
    image = Image.new("RGB", (800, 500), "white")
    d = ImageDraw.Draw(image)
    d.line((60, 450, 760, 450), fill="black", width=3)
    d.line((60, 40, 60, 450), fill="black", width=3)
    for i, h in enumerate(heights):
        x = 100 + i * 110
        d.rectangle((x, 450 - h, x + 60, 450), fill=(30, 90, 200))
    if photo:
        framed = Image.new("RGB", (900, 600), (230, 230, 225))
        framed.paste(image.filter(ImageFilter.GaussianBlur(1.2)), (50, 50))
        image = framed.resize((690, 460))
    buf = io.BytesIO()
    image.save(buf, format="JPEG" if photo else "PNG", quality=85)
    return buf.getvalue()


def extraction(values: dict[str, float], kind: str = "bar"):
    from services.ai.schemas import DataValue, LegendEntry, VisualBlock, VisualExtraction

    data = [DataValue(series="Male", point=p, value=v, text="", printed=True, precision=0) for p, v in values.items()]
    block = VisualBlock(visual_type=kind, title="Salt intake", units="mg", time_points=[], axis_labels=[],
                        legend=[LegendEntry(label="Male", colour="blue")], data=data, map_features=[], overall="",
                        process=None, bars_drawn=[])
    return VisualExtraction(visuals=[block], unreadable=[], readability="clear", readability_note="",
                            chart_box=[0, 0, 1, 1], rotation=0)


SALT = {"Under 6": 2200, "6-11": 3600, "12-19": 4300, "20-39": 4500, "40-60": 4000, "Over 60": 3700}
QUESTION = "The chart shows the information about salt intake in the US in 2000."


def pure() -> None:
    from services.ai import chart_keys
    from services.ai.visual_reading import combine

    a, b = chart_keys.fingerprint(chart_png([220, 360, 430, 300])), chart_keys.fingerprint(chart_png([220, 360, 430, 300]))
    resaved = Image.open(io.BytesIO(chart_png([220, 360, 430, 300])))
    buf = io.BytesIO()
    resaved.resize((640, 400)).save(buf, format="JPEG", quality=70)
    other = chart_keys.fingerprint(chart_png([400, 100, 150, 380]))
    check("the same picture, re-saved smaller as a JPEG, stays a few bits away",
          chart_keys.distance(a, b) == 0 and chart_keys.distance(a, chart_keys.fingerprint(buf.getvalue())) <= chart_keys.SAME_PICTURE,
          chart_keys.distance(a, chart_keys.fingerprint(buf.getvalue())))
    check("...another chart is far", chart_keys.distance(a, other) > chart_keys.SAME_PICTURE, chart_keys.distance(a, other))
    check("no picture, no fingerprint", chart_keys.fingerprint(b"not an image") is None)

    key = combine(extraction(SALT), extraction(SALT)).model_dump()["blocks"]
    read = [extraction({**SALT, "12-19": 4250, "Over 60": 3000}).visuals[0].model_dump()]
    ok, why = chart_keys.agrees(read, key)
    check("a read of the same chart (one figure misread) agrees with its key", ok, why)
    swapped = extraction({**SALT}).visuals[0].model_dump()
    swapped["data"] = [{**v, "series": v["point"], "point": v["series"]} for v in swapped["data"]]
    check("...also with series and points the other way round", chart_keys.agrees([swapped], key)[0])
    ok, why = chart_keys.agrees([extraction({p: v * 0.7 for p, v in SALT.items()}).visuals[0].model_dump()], key)
    check("a chart with the same labels but other figures does not", not ok, why)
    ok, why = chart_keys.agrees([extraction({"Cars": 10, "Buses": 20, "Trains": 30}).visuals[0].model_dump()], key)
    check("...nor one with other labels", not ok, why)

    sheet = combine(extraction(SALT), extraction(SALT)).model_dump()
    series, points, _ = chart_keys.grid(sheet["blocks"][0])
    form = {"b0.title": "Salt intake in the US", "b0.units": "milligrams", "b0.axis": "0, 1,000, 2,000", "b0.overall": "",
            "b0.s0": "Male", "b0.s1": "Female"}
    for c, p in enumerate(points):
        form[f"b0.p{c}"] = p
        form[f"b0.v0.{c}"] = f"{SALT[p]:,}"
        form[f"b0.v1.{c}"] = ""
    form["b0.v0.2"] = "4,350"   # corrected
    form["b0.v0.5"] = ""        # emptied: dropped
    form["b0.v1.0"] = "1300"    # a new row
    form["b0.p6"], form["b0.v0.6"] = "Note", "n/a"  # a new column with a word
    edited = chart_keys.edited_sheet(sheet, form)
    cells = {(v["series"], v["point"]): v for v in edited["blocks"][0]["values"]}
    check("an admin's edits: a corrected figure, an emptied cell dropped, a new row and a word cell",
          cells[("Male", "12-19")]["value"] == 4350 and ("Male", "Over 60") not in cells
          and cells[("Female", "Under 6")]["value"] == 1300 and cells[("Male", "Note")]["text"] == "n/a"
          and edited["blocks"][0]["units"] == "milligrams" and edited["blocks"][0]["axis_labels"] == ["0", "1,000", "2,000"],
          cells)
    checked = chart_keys.checked_sheet(edited, "islamov")
    check("...and checked: every figure verified, the sheet clear, the key features worked out again",
          all(v["status"] == "verified" for v in checked["blocks"][0]["values"]) and checked["readability"] == "clear"
          and any("Male" in x for x in checked["features"]["expected_overview"] + checked["features"]["key_comparisons"]),
          checked["features"])

    process = {"blocks": [{"visual_type": "process", "title": "", "units": "", "time_points": [], "axis_labels": [],
                           "legend": [], "values": [], "map_features": [], "overall": "", "bars_drawn": [],
                           "process": {"steps": ["a", "b"], "numbered": False, "stages": [], "kind": "linear",
                                       "natural_or_manmade": "man-made"}}]}
    edited = chart_keys.edited_sheet(process, {"b0.steps": "plant\nharvest\n\nweave", "b0.stages": "Growing: 1-2\nMaking: 3",
                                               "b0.title": "", "b0.units": "", "b0.overall": ""})
    proc = edited["blocks"][0]["process"]
    check("a process's steps and stages from the form", proc["steps"] == ["plant", "harvest", "weave"]
          and proc["stages"] == [{"name": "Growing", "steps": [1, 2]}, {"name": "Making", "steps": [3]}], proc)


async def flow() -> None:
    from sqlalchemy import select

    from db.database import get_session
    from db.models import ChartKey
    from services.ai import chart_keys, visual_reading

    calls = {"read": 0, "first": 0}
    seen = {"values": SALT}

    async def fake_read(question, images, first=None):
        calls["read"] += 1
        return visual_reading.combine(first or extraction(seen["values"]), extraction(seen["values"]))

    async def fake_first(question, images):
        calls["first"] += 1
        return extraction(seen["values"])

    async def fake_embedding(question):
        return [1.0] + [0.0] * 1535 if "salt" in question else [0.0, 1.0] + [0.0] * 1534

    visual_reading.read_visual, visual_reading.first_read = fake_read, fake_first
    chart_keys._embedding = fake_embedding
    picture = chart_png([220, 360, 430, 450, 400, 370])
    images = [(picture, "image/png")]

    sheet, look = await chart_keys.sheet_for(QUESTION, images)
    check("no keys yet: the chart is read", calls["read"] == 1 and sheet is not None and not look.key_id, calls)
    await chart_keys.remember(QUESTION, images, sheet, look, None, 101)
    async with get_session() as session:
        keys = (await session.execute(select(ChartKey))).scalars().all()
    check("...and kept as an auto key with its own copy of the picture",
          len(keys) == 1 and keys[0].status == "auto" and Path(keys[0].image_path).exists() and keys[0].uses == 1
          and keys[0].kind == "bar", [(k.status, k.uses, k.kind) for k in keys])

    sheet, look = await chart_keys.sheet_for(QUESTION, images)
    await chart_keys.remember(QUESTION, images, sheet, look, None, 102)
    async with get_session() as session:
        keys = (await session.execute(select(ChartKey))).scalars().all()
    check("the same chart again: read (the key isn't checked yet), one more use, no second key",
          calls["read"] == 2 and len(keys) == 1 and keys[0].uses == 2, [(k.id, k.uses) for k in keys])
    key_id = keys[0].id

    fixed = {**keys[0].sheet}
    fixed["blocks"] = [{**fixed["blocks"][0], "values": [{**v, "value": 4350.0} if v["point"] == "12-19" else v
                                                         for v in fixed["blocks"][0]["values"]]}]
    await chart_keys.save(key_id, fixed, "islamov", verified=True)

    reads_before = dict(calls)
    sheet, look = await chart_keys.sheet_for(QUESTION, images)
    figure = next(v.value for v in sheet.blocks[0].values if v.point == "12-19")
    check("checked: the same picture is judged by the checked sheet, nothing read",
          look.key_id == key_id and sheet.chart_key == key_id and figure == 4350 and calls == reads_before
          and all(v.status == "verified" for v in sheet.blocks[0].values), (look, calls))
    await chart_keys.remember(QUESTION, images, sheet, look, None, 103)

    photo = [(chart_png([220, 360, 430, 450, 400, 370], photo=True), "image/jpeg")]
    sheet, look = await chart_keys.sheet_for(QUESTION, photo)
    check("a phone photo of it (another picture, the same question): one read agrees, then the checked sheet",
          look.key_id == key_id and calls["first"] == reads_before["first"] + 1 and calls["read"] == reads_before["read"],
          (look, calls))
    await chart_keys.remember(QUESTION, photo, sheet, look, None, 104)

    seen["values"] = {p: v * 0.6 for p, v in SALT.items()}
    other = [(chart_png([130, 220, 260, 270, 240, 220]), "image/png")]
    sheet, look = await chart_keys.sheet_for(QUESTION, other)
    check("another chart with the same question text: its read disagrees, so it is read in full - never the key",
          not look.key_id and sheet.chart_key == 0 and calls["read"] == reads_before["read"] + 1, (look, calls))
    await chart_keys.remember(QUESTION, other, sheet, look, None, 105)
    async with get_session() as session:
        keys = {k.id: k for k in (await session.execute(select(ChartKey))).scalars().all()}
    check("...and kept as a new auto key; the checked key counts its uses",
          len(keys) == 2 and keys[key_id].uses == 4 and keys[key_id].status == "verified"
          and keys[key_id].last_submission_id == 104, [(k.id, k.status, k.uses) for k in keys.values()])

    # Judging by a checked key: a focused re-read only tells which figure is meant.
    async def misreading(question, imgs, sentence, figure):
        from services.ai.schemas import FocusedRead
        return FocusedRead(series="Male", point="12-19", found=True, missing=False, value=4000, how="misread")
    real = visual_reading.read_one
    visual_reading.read_one = misreading
    try:
        keyed = (await chart_keys.sheet_for(QUESTION, images))[0].model_dump()
        # the matcher ties the figure to 40-60 (4000) - the re-read tells it means 12-19
        right = await visual_reading.check_report_figures("In the 40-60 group males consumed 4,350 mg.",
                                                          keyed, QUESTION, images)
        wrong = await visual_reading.check_report_figures("In the 40-60 group males consumed 4,600 mg.",
                                                          keyed, QUESTION, images)
    finally:
        visual_reading.read_one = real
    cell = next(v for v in wrong["blocks"][0]["values"] if v["point"] == "12-19")
    check("a checked key's figure is the truth: the student's right figure agrees though the re-read misread it",
          right["agreed_figures"] == ["4,350"], right.get("agreed_figures"))
    check("...a wrong one is wrong, and the key's cell stays verified",
          wrong["confirmed_wrong"] == ["4,600"] and cell["status"] == "verified", (wrong.get("confirmed_wrong"), cell))

    await panel_pages(key_id)


async def panel_pages(key_id: int) -> None:
    import httpx

    from webapp.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://panel") as web:
        r = await web.post("/login", data={"username": "admin", "password": "change-me"})
        check("panel: signed in", r.status_code == 303, r.status_code)
        r = await web.get("/charts?status=all")
        check("panel: the charts list opens and shows both", r.status_code == 200 and f"#{key_id}" in r.text
              and "Tekshirilgan" in r.text, r.status_code)
        r = await web.get(f"/charts/{key_id}")
        check("panel: a chart opens with its table", r.status_code == 200 and 'name="b0.v0.0"' in r.text
              and "4350" in r.text, r.status_code)
        form = {"action": "save", "b0.title": "Salt", "b0.units": "mg", "b0.axis": "", "b0.overall": "", "b0.s0": "Male"}
        page = r.text
        import re
        for name, value in re.findall(r'name="(b0\.(?:p\d+|v0\.\d+))" value="([^"]*)"', page):
            form[name] = value
        form["b0.v0.0"] = "2250"
        r = await web.post(f"/charts/{key_id}", data=form)
        r2 = await web.get(f"/charts/{key_id}")
        check("panel: an edit is saved", r.status_code == 303 and 'value="2250"' in r2.text, r.status_code)
        r = await web.post(f"/charts/{key_id}", data={**form, "action": "unverify"})
        r2 = await web.get("/charts?status=auto")
        check("panel: a check can be taken back", r.status_code == 303 and f"#{key_id}" in r2.text)


def main() -> int:
    pure()
    asyncio.run(flow())
    print(f"\nFAILURES: {', '.join(fails) or 'none'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
