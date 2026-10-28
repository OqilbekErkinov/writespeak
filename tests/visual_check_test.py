"""The Task 1 data check (services/ai/visual_check.py) on the cases the A8
run of 2026-10-27 found - offline, no OpenAI:

    python tests/visual_check_test.py

On the books' Band 8-9 reports the check called correct figures "not on the
sheet": a table label starting with a measure word ("Years taken to reach
the top salary") was "named" by any "15 years"; the category of the figure
before ("Luxembourg ... $112,000") held for the next one too; "Country A",
"Country B" and "Country C" were all named by "country"; a cost printed as
text ("$556 million") had no number; a map's "two-thirds of the site" had
nothing to be compared with. A wrong figure for a named category must still
be found.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.ai.visual_check import (  # noqa: E402
    data_visual,
    figures,
    match_figure,
    protected_mistake,
    unchecked_figures,
    visual_kind,
)

fails: list[str] = []


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


def table(rows: dict[str, dict[str, float | str]], kind: str = "table") -> dict:
    values = []
    for series, cells in rows.items():
        for point, value in cells.items():
            text = value if isinstance(value, str) else ""
            values.append({"series": series, "point": point, "value": None if text else value, "text": text,
                           "precision": 0.0, "status": "verified"})
    return {"readability": "clear", "blocks": [{"visual_type": kind, "title": "", "values": values}]}


def matches(text: str, reading: dict, question: str = "") -> dict[str, str]:
    return {f["text"]: match_figure(f, reading, question) for f in figures(text)}


def main() -> int:
    salaries = table({
        "Starting (US $)": {"Australia": 34600, "Korea": 30500, "Luxembourg": 80000, "Japan": 28000},
        "After 15 years (US $)": {"Australia": 48000, "Korea": 52600, "Luxembourg": 112000, "Japan": 49000},
        "Max (US $)": {"Australia": 48000, "Korea": 84500, "Luxembourg": 139000, "Japan": 62400},
        "Years taken to reach the top salary (years)": {"Australia": 9, "Korea": 37, "Luxembourg": 30, "Japan": 34},
    })
    got = matches("Having worked for 15 years, teachers in Luxembourg would generate an income of $112,000, which "
                  "would then rise to a whopping $139,000 after another 15 years.", salaries)
    check("a measure word ('years') does not name 'Years taken to reach the top salary'",
          got.get("$112,000") == "raw", got)
    check("the category of the figure before does not hold for the next one", got.get("$139,000") == "raw", got)
    got = matches("Korean teachers would obtain an income of $84.500 after 37 years of experience, and in Australia "
                  "only after 9 years they would reach the maximum.", salaries)
    check("'$84.500' (a full stop for thousands) and the years after it",
          got.get("$84.500") == "raw" and got.get("37") == "raw" and got.get("9") == "raw", got)

    fees = table({"Arts": {"Country A": 8000, "Country B": 18000, "Country C": 19000},
                  "Computing": {"Country A": 11000, "Country B": 27000, "Country C": 24000}})
    got = matches("Country C charges the highest amount for these two fields, with each costing $19,000.", fees)
    check("'Country C' named in full, and 'costing' is not 'Computing'", got.get("$19,000") == "raw", got)
    got = matches("Country C charges the most for Computing, at $27,000.", fees)
    check("...while a wrong figure for Country C's Computing is still found", got.get("$27,000") == "miss", got)

    pay = table({"doctors": {"France": 70, "Italy": 60, "The US": 120},
                 "other workers": {"France": 35, "Italy": 20, "The US": 45}}, "bar")
    got = matches("Doctors in the US earned about $150,000.", pay)
    check("'The US' (short words only) is named: a wrong US figure is found", got.get("about $150,000") == "miss", got)
    got = matches("Doctors in the US earned about $120,000, twice as much as doctors in France.", pay)
    check("...and the right one, and a ratio word, agree",
          got.get("about $120,000") == "raw" and got.get("twice") == "derived", got)

    states = table({"Aged under 18": {"California": 17, "Utal": 28, "Florida": 16},
                    "Population below poverty line": {"California": 16, "Utal": 9, "Florida": 12}})
    got = matches("28% of Utah's population is under 18, a much higher percentage than in California (17%). In "
                  "comparison, Utah and Florida have lower poverty rates, at 9% and 12%.", states)
    check("a label misread by a letter ('Utal') is still Utah; 'population' does not name the poverty row",
          got.get("17%") == "raw" and got.get("9%") == "raw" and got.get("12%") == "raw", got)

    tunnels = table({"Cost (US$)": {"Tunnel A": "$556 million", "Tunnel B": "$1.1 billion"},
                     "Length (kilometres)": {"Tunnel A": 2.2, "Tunnel B": 3.6}})
    got = matches("The total cost of this tunnel was $556 million. Tunnel B cost $1.1 billion, almost twice as "
                  "much as Tunnel A.", tunnels)
    check("a cost printed as text counts as its figure; 'almost twice' is worked out from it",
          got.get("$556 million") in ("raw", "label") and got.get("$1.1 billion") in ("raw", "label")
          and got.get("almost twice") == "derived",
          got)

    site = {"readability": "clear", "blocks": [{"visual_type": "map", "title": "", "values": [],
                                                "map_features": [{"name": "playing fields", "location": "south",
                                                                  "states": []}]}]}
    got = matches("Playing fields occupied nearly two-thirds of the site.", site)
    check("a map's 'two-thirds' has nothing to be compared with: never a mistake",
          got.get("nearly two-thirds") == "raw-uncertain", got)

    spending = {"readability": "clear", "blocks": [
        {"visual_type": "bar", "title": "1998", "values": [
            {"series": s, "point": "", "value": v, "precision": 0.0, "status": "verified"}
            for s, v in (("Eating out", 6), ("Holiday", 8), ("Go to the cinema", 3))]}]}
    got = matches("There was a small difference of only 2 percentage points between eating out and holidays.", spending)
    check("'the' in 'Go to the cinema' names nothing", got.get("2") == "derived", got)

    rulings()
    islamov_93_95()
    print(f"\nFAILURES: {', '.join(fails) or 'none'}")
    return 1 if fails else 0


def islamov_93_95() -> None:
    """The three reports of 2026-10-28 (after the deploy): #94, a Band 9 line
    graph report, got Task Achievement 6 for "missing Columbia as the biggest
    rise" - the key features ranked only the series read for sure; #95's
    "In 2011 ... the three countries rose to 6%" passed as Brazil's 2005 figure
    (the year was out of reach), then as 18 - 13 and as "six years"; #93's
    "4,300 mg among 12- 19- year-olds" was tied to 6-11, and a focused re-read
    that agreed with the sheet and the student made the cell uncertain - the
    model answer then wrote "the exact male value is not given"."""
    import asyncio

    from services.ai import visual_reading
    from services.ai.sample_bank import data_problems
    from services.ai.schemas import FocusedRead, VisualSheet
    from services.ai.visual_check import data_check
    from services.ai.visual_features import key_features

    def sheet(kind, units, points, rows, notes=None, extra=None):
        notes = notes or {}
        values = [{"series": s, "point": p, "value": v, "text": "", "printed": prec == 0, "precision": prec,
                   "status": "uncertain" if (s, p) in notes else "verified", "note": notes.get((s, p), "")}
                  for s, cells in rows.items() for p, (v, prec) in cells.items()]
        block = {"visual_type": kind, "title": "", "units": units, "time_points": points, "axis_labels": [],
                 "legend": [{"label": s, "colour": ""} for s in rows], "values": values, "map_features": [],
                 "overall": "", "process": None, "bars_drawn": []}
        return {"blocks": [block], "readability": "clear", "readability_note": "", "unreadable": [], "checks": [],
                "features": {"expected_overview": [], "key_comparisons": [], "suggested_grouping": ""}, "reads": 2,
                **(extra or {})}

    years = ["1940", "1970", "2000"]
    oregon = sheet("line", "thousands of people", years, {
        "Columbia": dict(zip(years, [(23.5, 12.5), (32, 12.5), (77.5, 12.5)])),
        "Yamhill": dict(zip(years, [(29, 16.5), (38.5, 12.5), (85, 17.5)])),
        "Washington": dict(zip(years, [(75, 12.5), (122.5, 12.5), (232.5, 20)]))},
        notes={("Yamhill", "1940"): "the reads disagree: 25 vs 33", ("Yamhill", "2000"): "the reads disagree: 80 vs 90",
               ("Washington", "2000"): "the reads disagree: 225 vs 240"})
    feats = key_features(VisualSheet.model_validate(oregon))
    check("#94: the biggest rise is ranked over every line, uncertain ends included (Washington, not Columbia)",
          any("Washington" in c and "biggest rise" in c for c in feats.key_comparisons)
          and not any("Columbia" in c and "biggest" in c for c in feats.key_comparisons), feats.key_comparisons)
    check("...and the overview says all three rose", any("All of them rose" in o for o in feats.expected_overview),
          feats.expected_overview)

    t = ["2005", "2007", "2009", "2011", "2013", "2015"]
    leisure = sheet("line", "%", t, {
        "United States": dict(zip(t, [(v, 2.5) for v in (20, 22, 24, 26, 28, 30)])),
        "Japan": dict(zip(t, [(v, 2.5) for v in (12, 14, 16, 18, 20, 22)])),
        "Brazil": dict(zip(t, [(v, 2.5) for v in (8, 9, 11, 13, 15, 17)]))})
    q95 = "The line graph shows the average household spending on leisure activities in three countries from 2005 to 2015."
    got = matches("In 2011, average household spending leisure activities in each three countries United States, "
                  "Brazil and Japan rose to 6%.", leisure, q95)
    check("#95: 'In 2011, ... the three countries rose to 6%' is not found (2011 named at the sentence start; "
          "a level is no difference; 6% is no count of six years)", got.get("6%") == "miss", got)
    got = matches("In 2011, the gap between the United States and Japan reached 8%, and Brazil rose by 2 points by 2013.",
                  leisure, q95)
    check("...while a gap and a rise named as such are still worked out", got.get("8%") == "derived"
          and got.get("2") == "derived", got)
    got = matches("In 2005, Brazil spent about 8%, and the United States and Japan were higher, at about 20% and 12% "
                  "respectively.", leisure, q95)
    check("...and figures of the year a sentence opens with agree", set(got.values()) <= {"raw", "label"}, got)
    houses = sheet("line", "$k", ["2006", "2014"], {"Country A": {"2006": (450, 60), "2014": (640, 60)}})
    got = matches("This was followed by a decline in 2006, after which prices picked up again, rising to around "
                  "$600,000 and remaining stable by 2014.", houses)
    check("...but not when the time moves on ('a decline in 2006, after which ... by 2014', v1-p064)",
          got.get("around $600,000") == "raw", got)
    feats = key_features(VisualSheet.model_validate(leisure))
    check("...and no 'biggest rise' for a tie (the US and Japan both +10)",
          not any("biggest rise" in c for c in feats.key_comparisons), feats.key_comparisons)

    groups = ["Under 6", "6-11", "12-19", "20-39", "40-60", "Over 60"]
    salt = sheet("bar", "Milligrams", [], {
        "Male": dict(zip(groups, [(v, 0) for v in (2200, 3600, 4300, 4500, 4000, 3700)])),
        "Female": dict(zip(groups, [(v, 0) for v in (1300, 3000, 3000, 3100, 2900, 2500)])),
        "Recommended salt intake (dashed red line)": {g: (2200, 0) for g in groups}},
        notes={("Recommended salt intake (dashed red line)", g): "seen by one read only" for g in groups})
    answer93 = ("Regarding males, salt consumption stood at 2,200 among children under 6. This figure then rose "
                "considerably to 3,600 mg for those aged 6-11 and increased further to 4,300 mg among 12- 19- year-olds. "
                "It reached a peak of 4,500 mg in the 20- 39 age group, before falling to 4,000 mg among 40-60- year- olds "
                "and 3,700 mg among those over 60.")
    q93 = "The chart shows the information about salt intake in the US in 2000."
    got = matches(answer93, salt, q93)
    check("#93: '4,300 mg among 12- 19- year-olds' (a range typed with spaces) agrees", got.get("4,300") == "raw", got)
    feats = key_features(VisualSheet.model_validate(salt))
    check("...a line only one read saw (the recommended level) is no category in the comparisons",
          not any("Recommended" in c for c in feats.key_comparisons + feats.expected_overview)
          and any("Male is higher than Female" in o for o in feats.expected_overview), feats)

    async def agreeing(question, images, sentence, figure):
        return FocusedRead(series="Male", point="Over 60", found=True, missing=False, value=3700, how="printed")
    real = visual_reading.read_one
    visual_reading.read_one = agreeing
    try:
        checked = asyncio.run(visual_reading.check_report_figures(answer93, salt, q93, [(b"x", "image/png")]))
    finally:
        visual_reading.read_one = real
    cell = next(v for v in checked["blocks"][0]["values"] if v["series"] == "Male" and v["point"] == "Over 60")
    check("...a focused re-read that agrees with the sheet and the student: the figure is correct and the cell "
          "stays verified", checked["agreed_figures"] == ["3,700"] and cell["status"] == "verified"
          and not unchecked_figures(answer93, checked, q93), (checked["agreed_figures"], cell))
    check("...and the DATA CHECK lists no figure as not found", "Not found" not in data_check(answer93, checked, q93),
          data_check(answer93, checked, q93))

    problems = data_problems("For Over-60-year-olds females consumed 2,500 mg while the male bar exceeds this level "
                             "(exact male value is not shown). Washington sustained its lead in the verified years.",
                             q93, salt)
    check("model answer: talk about the data ('is not shown', 'verified years') goes back for a rewrite",
          any("never write about the data" in p for p in problems), problems)


def rulings() -> None:
    """The academic lead's Task 1 rulings of 2026-10-27, on #53-#59."""
    from services.ai import visual_reading
    from services.ai.schemas import DataValue, GroupBars, LegendEntry, VisualBlock, VisualExtraction

    # A missing bar is never filled from its neighbour: a group with fewer bars drawn is uncertain.
    def bars(values, drawn):
        data = [DataValue(series=y, point=g, value=v, text="", printed=False, precision=2) for (y, g), v in values.items()]
        block = VisualBlock(visual_type="bar", title="Internet use", units="%", time_points=[], axis_labels=["0", "20", "40"],
                            legend=[LegendEntry(label=y, colour=c) for y, c in (("2003", "dark grey"), ("2004", "medium grey"),
                                                                                ("2005", "light grey"), ("2006", "white"))],
                            data=data, map_features=[], overall="", process=None, bars_drawn=drawn)
        return VisualExtraction(visuals=[block], unreadable=[], readability="clear", readability_note="",
                                chart_box=[0, 0, 1, 1], rotation=0)
    values = {(y, "65+"): v for y, v in (("2003", 15), ("2004", 15), ("2005", 15), ("2006", 20))}
    values.update({(y, "16-24"): v for y, v in (("2003", 80), ("2004", 80), ("2005", 100), ("2006", 90))})
    drawn = [GroupBars(point="65+", bars=3), GroupBars(point="16-24", bars=4)]
    sheet = visual_reading.combine(bars(values, drawn), bars(values, drawn)).model_dump()
    status = {(v["series"], v["point"]): v["status"] for v in sheet["blocks"][0]["values"]}
    check("a group with 3 of 4 bars drawn is uncertain (#54's 65+), the full group stays verified",
          status[("2003", "65+")] == "uncertain" and status[("2005", "16-24")] == "verified", status)
    check("...so the student's '0%' for it is never a mistake",
          "0%" in unchecked_figures("The oldest individuals started at 0%.", sheet, "") or
          match_figure(figures("65+ started at 0% in 2003")[1], sheet, "") != "miss")

    # Look-alike legend patterns: those slices are uncertain (#56).
    def pie(home, sports):
        data = [DataValue(series=s, point="", value=v, text="", printed=True, precision=0)
                for s, v in (("Quality of resources", 21), ("Suitable degree courses", 35), ("Close to parental home", home),
                             ("Quality teaching", 15), ("Good sports and social activities", sports))]
        block = VisualBlock(visual_type="pie", title="1987", units="%", time_points=[], axis_labels=[],
                            legend=[LegendEntry(label="Quality of resources", colour="light grey"),
                                    LegendEntry(label="Suitable degree courses", colour="diagonal hatching"),
                                    LegendEntry(label="Close to parental home", colour="chequered grey"),
                                    LegendEntry(label="Quality teaching", colour="solid black"),
                                    LegendEntry(label="Good sports and social activities", colour="dotted grey")],
                            data=data, map_features=[], overall="", process=None, bars_drawn=[])
        return VisualExtraction(visuals=[block], unreadable=[], readability="clear", readability_note="",
                                chart_box=[0, 0, 1, 1], rotation=0)
    pies = visual_reading.combine(pie(19, 10), pie(19, 10)).model_dump()
    status = {v["series"]: v["status"] for v in pies["blocks"][0]["values"]}
    check("look-alike patterns (chequered grey / dotted grey): both slices uncertain, the rest verified",
          status["Close to parental home"] == "uncertain" and status["Good sports and social activities"] == "uncertain"
          and status["Quality teaching"] == "verified", status)
    check("...and a Task 'mistake' about them is never kept (a paraphrase counts: 'proximity to home')",
          protected_mistake("Proximity to home influenced only 10% of students in 1987",
                            "Proximity to home accounted for 19% in 1987", "The sentence swaps them.", pies, "", set()))
    check("...while one about a verified slice can still be kept",
          not protected_mistake("Quality teaching was 25%", "Quality teaching was 15%", "The chart shows 15%.", pies, "",
                                set()))

    # Line graphs: half a gridline step (#53: 240 vs 230, 170 vs 150 on a 50-unit grid are fair readings).
    def line(v1980):
        data = [DataValue(series="Wood Pulp", point=p, value=v, text="", printed=False, precision=12.5)
                for p, v in (("1980", v1980), ("1990", 150), ("2000", 160))]
        block = VisualBlock(visual_type="line", title="UK forest industry", units="million tonnes", time_points=[],
                            axis_labels=["0", "50", "100", "150", "200", "250"], legend=[], data=data, map_features=[],
                            overall="", process=None, bars_drawn=[])
        return VisualExtraction(visuals=[block], unreadable=[], readability="clear", readability_note="",
                                chart_box=[0, 0, 1, 1], rotation=0)
    lines = visual_reading.combine(line(150), line(150)).model_dump()
    got = {f["text"]: match_figure(f, lines, "") for f in figures("Wood pulp started at about 170 million tonnes in 1980.")}
    check("a line point is ±half a gridline step: 'about 170' for a 1980 point read at 150 is fair",
          got.get("about 170 million") in ("raw",) and lines["blocks"][0]["values"][0]["precision"] == 25, got)

    # The deliberate wrong figure is caught by the matcher (then read again on its own before it is called wrong).
    internet = {"blocks": [{"visual_type": "bar", "title": "", "units": "%", "axis_labels": ["0", "20", "40", "60", "80", "100", "120"],
                            "values": [{"series": y, "point": "16-24", "value": v, "precision": 5.0, "status": "verified"}
                                       for y, v in (("2003", 80), ("2004", 80), ("2005", 100), ("2006", 90))]}]}
    got = {f["text"]: match_figure(f, internet, "") for f in figures("Young adults aged 16-24 reached 100% in 2004.")}
    check("'16-24 reached 100% in 2004' is not found on the sheet (100% belongs to 2005; an axis tick is no label)",
          got.get("100%") == "miss", got)
    got = {f["text"]: match_figure(f, internet, "") for f in figures("16-24 increased to a chart-high of 100% in 2005.")}
    check("...while the true '100% in 2005' agrees", got.get("100%") == "raw", got)

    # No comparisons needed for processes, cycles and maps.
    check("a process / cycle / map is no visual of figures; a chart is",
          not data_visual("The diagram shows the life cycle of flowering plants.")
          and not data_visual("The diagram shows how bamboo fabric is made.")
          and data_visual("The chart below shows the percentage of adults who used the Internet.")
          and not data_visual("", {"blocks": [{"visual_type": "process"}]}))
    check("visual kinds for the Band 9 anchors",
          visual_kind("", {"blocks": [{"visual_type": "process", "process": {"kind": "cyclical"}}]}) == "cycle"
          and visual_kind("", {"blocks": [{"visual_type": "bar"}, {"visual_type": "table"}]}) == "mixed"
          and visual_kind("The graph below shows the cost of water in five cities.", {"blocks": [{"visual_type": "table"}]}) == "table"
          and visual_kind("The diagram shows how bamboo fabric is made.") == "process")


if __name__ == "__main__":
    sys.exit(main())
