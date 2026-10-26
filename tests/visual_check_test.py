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

from services.ai.visual_check import figures, match_figure  # noqa: E402

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

    print(f"\nFAILURES: {', '.join(fails) or 'none'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
