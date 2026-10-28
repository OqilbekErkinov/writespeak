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
    print(f"\nFAILURES: {', '.join(fails) or 'none'}")
    return 1 if fails else 0


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
