"""A Task 1 report's figures checked against the visual's data sheet by code
(Task 1 prompt A5, data accuracy; 2026-10-23) - so the scorer, the feedback
and the mistake filter judge data accuracy from one deterministic check
instead of each reading the chart again. On the books' Band 8-9 reports the
scorer called figures "inaccurate" or "invented" that this check finds
within the sheet's precision (a year between two labelled years, "around
$40,000" for a bar read as 40 ±2).

A figure matches when it equals a sheet figure within its precision (more
leeway after "about", "nearly", "over"...), a difference, sum, ratio or
percentage change of two figures in one visual, a total, or a number in a
label (an age group, a year on the axis, the number of steps). The same
code checks the books in scripts/task1_extraction_test.py.
"""
from __future__ import annotations

import difflib
import re
from collections import defaultdict

FIG_RE = re.compile(
    r"(?P<approx>\b(?:about|around|approximately|roughly|nearly|almost|just\s+(?:over|under|below|above)|over|"
    r"under|more\s+than|less\s+than|below|above|close\s+to|some|exactly|precisely)\s+)?"
    r"(?<![\w.])(?P<cur>[$£€])?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\d])"
    r"(?P<suffix>\s?(?:%|per\s?cent\b|percent\b)|\s?(?:million|billion|thousand|bn)\b|(?:m|k)\b)?",
    re.IGNORECASE)
SCALES = {"million": 1e6, "billion": 1e9, "bn": 1e9, "thousand": 1e3, "m": 1e6, "k": 1e3}
_SCALE_TRIES = (1, 1e3, 1e6, 1e9, 1e-3)

# Ratio words (2026-10-26: #48's correct "just one-third of this" was flagged
# as wrong): compared with the ratio of two figures on the sheet.
FRACTION_RE = re.compile(
    r"(?P<approx>\b(?:about|around|approximately|roughly|nearly|almost|just\s+(?:over|under|below|above)|over|"
    r"under|more\s+than|less\s+than|some|exactly|precisely)\s+)?"
    r"\b(?P<word>(?:a|one)[\s-]half|half|(?:a|one)[\s-]third|two[\s-]thirds|(?:a|one)[\s-]quarter|"
    r"three[\s-]quarters|twice|doubled?|tripled?|(?:three|four|five)\s+times)\b",
    re.IGNORECASE)
_RATIOS = {"half": 0.5, "third": 1 / 3, "thirds": 2 / 3, "quarter": 0.25, "quarters": 0.75, "twice": 2.0,
           "double": 2.0, "doubled": 2.0, "triple": 3.0, "tripled": 3.0, "three": 3.0, "four": 4.0, "five": 5.0}


def _ratio(word: str) -> float:
    w = re.sub(r"[\s-]+", " ", word.lower()).split()
    if w[0] == "two":
        return 2 / 3
    if w[0] == "three" and w[-1] == "quarters":
        return 0.75
    if w[-1] == "times":
        return _RATIOS[w[0]]
    return _RATIOS[w[-1]]


def figures(text: str) -> list[dict]:
    """Every figure in a text: value, approximate or not, a year or not, a
    percentage or not. The words where its category is named start after the
    figure before it: in "Luxembourg would earn $112,000, rising to $139,000
    after another 15 years" the second figure is not Luxembourg's 15-year
    salary (the 2026-10-27 A8 run on the books' Band 8-9 reports)."""
    def label_like(m: re.Match) -> bool:
        """A year, or a number of a range ("16-24", "25-44", "65+"): it names a
        category, so the figure after it still belongs to it."""
        raw = m.group(0).strip()
        if re.fullmatch(r"(1[89]|20)\d\d", raw) or re.fullmatch(r"\d{1,3}", raw) and text[m.end():m.end() + 1] == "+":
            return True
        return bool(re.match(r"\s?[-–]\d", text[m.end():m.end() + 2]) or re.search(r"\d[-–]$", text[max(0, m.start() - 2):m.start()]))

    ends = sorted(m.end() for r in (FIG_RE, FRACTION_RE) for m in r.finditer(text)
                  if r is FRACTION_RE or not label_like(m))

    def since_last(start: int, reach: int) -> int:
        before = [e for e in ends if e <= start]
        return max(start - reach, before[-1] if before else 0)

    out = []
    for m in FIG_RE.finditer(text):
        raw = m.group("num")
        value = float(raw.replace(",", ""))
        suffix = (m.group("suffix") or "").strip().lower()
        if re.match(r"(st|nd|rd|th)\b", text[m.end("num"):m.end("num") + 2], re.IGNORECASE):
            continue  # an ordinal ("the 21st century")
        if suffix in SCALES:
            value *= SCALES[suffix]
        year = not suffix and not m.group("cur") and raw.isdigit() and 1800 <= value <= 2100 and len(raw) == 4
        # where its category is named: the words before it back to the start of
        # its clause, and the few after it ("5,192 international students")
        clause = re.split(r"[.;:!?]\s|,\s(?:while|whereas|but|and)\s", text[since_last(m.start(), 90):m.start()])[-1]
        # ...and the words just after it: three at most - "after 9 years they would reach the maximum"
        # names the years, not the maximum salary - and a year as the fourth ("by the year 2000");
        # a range typed with spaces is one word ("4,300 mg among 12- 19- year-olds", #93)
        words = re.split(r"[,.;:()]", re.sub(r"(\d)\s*([-–])\s+(?=\d)", r"\1\2", text[m.end():m.end() + 40]))[0].split()
        after = " ".join(words[:4] if len(words) > 3 and re.fullmatch(r"(1[89]|20)\d\d", words[3]) else words[:3])
        # the sentence back to its start, but not past the figure before: a year
        # it opens with is the figure's ("In 2011, ... the three countries rose to 6%", #95)
        sentence_start = max((x.end() for x in re.finditer(r"[.!?]\s+|\n", text[:m.start()])), default=0)
        lead = text[max(sentence_start, since_last(m.start(), m.start())):m.start()]
        rest = re.split(r"[.!?](?:\s|$)|\n", text[m.end():])[0]
        if _YEAR_IN.search(rest) or _TIME_SHIFT.search(_YEAR_IN.split(lead)[-1]):
            # not when another year follows ("... rising to $600,000 and remaining stable by 2014")
            # or the time moves on after it ("a decline in 2006, after which prices rose to ...", v1-p064)
            lead = ""
        out.append({"text": m.group(0).strip(), "value": value, "approx": bool(m.group("approx")), "year": year,
                    "pct": "%" in suffix or "cent" in suffix, "before": f"{clause} {after}",
                    "lead": lead, "after": after, "prev": " ".join(clause.split()[-6:]), "clause": clause,
                    # a year or an age group names the figure only from right next to it: "in 1992, after
                    # which ... reaching a low of 5% by the year 2000" is 2000's figure
                    "tight": " ".join(clause.split()[-6:] + after.split()),
                    "context": text[max(0, m.start() - 40):m.end() + 25].replace("\n", " "),
                    "sentence": _sentence(text, m.start())})
    for m in FRACTION_RE.finditer(text):
        clause = re.split(r"[.;:!?]\s", text[since_last(m.start(), 120):m.start()])[-1]
        after = re.split(r"[.;:()]", text[m.end():m.end() + 60])[0]
        out.append({"text": m.group(0).strip(), "value": _ratio(m.group("word")), "approx": bool(m.group("approx")),
                    "year": False, "pct": False, "ratio": True, "before": f"{clause} {after}",
                    "context": text[max(0, m.start() - 40):m.end() + 25].replace("\n", " "),
                    "sentence": _sentence(text, m.start())})
    return out


def _sentence(text: str, at: int) -> str:
    """The sentence around position `at`."""
    start = max((m.end() for m in re.finditer(r"[.!?]\s+|\n", text[:at])), default=0)
    end = re.search(r"[.!?](?=\s|$)|\n", text[at:])
    return text[start:at + (end.end() if end else len(text) - at)].strip()


def _key(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", text.lower()) + " "


_STOP = {"the", "and", "for", "with", "per", "from", "into", "than", "that", "this", "its", "their", "who", "are",
         "was", "were", "has", "have", "all", "any", "not"}
# A label's first word that says what is measured, not which category ("Years
# taken to reach the top salary", "Number of visitors"): the next word names it.
_GENERIC = {"after", "before", "number", "numbers", "percentage", "percent", "proportion", "amount", "total",
            "average", "year", "years", "rate", "share", "level", "population", "people", "value", "time", "aged"}


def _stem(word: str) -> str:
    return word[:-1] if word.endswith("s") and len(word) > 4 else word


def _said(stem: str, near: str) -> bool:
    """The word in `near` - or, for a word of 4+ letters, the same word with a
    letter misread on the label ("Utal" for "Utah"; two letters in a word of
    8+: "Nowspapor"), never a different word ("costing" is not "computing")."""
    if f" {stem}" in near:
        return True
    if len(stem) < 4:
        return False
    allowed = 1 if len(stem) < 8 else 2
    return any(len(t) == len(stem) and sum(a != b for a, b in zip(t, stem)) <= allowed
               for t in (_stem(w) for w in near.split()))


def _named(label: str, near: str, tight: str | None = None) -> bool:
    """Is the category `label` named in `near` (already _key'ed)? Its first
    word is enough ("Health Care" in "health care volunteers"; "Environmental"
    in "environmental organisations") - its first word that is not a
    stopword or a measure ("the" named "Go to the cinema" in every sentence;
    "years" named "Years taken to reach the top salary"); a label of measures
    only needs them all."""
    if not _words(label) and re.search(r"\d", label):  # a year or an age group: "2004", "16-24"
        return all(f" {n} " in (tight or near) for n in re.findall(r"\d+", label))
    key = _key_word(label)
    if key is not None:
        return _said(_stem(key), near)
    words = _words(label)
    if not words:  # short words only ("The US", "UK"): each as a whole word
        short = [w for w in re.sub(r"[^a-z0-9]+", " ", label.lower()).split() if w not in _STOP and not w.isdigit()]
        return bool(short) and all(f" {w} " in near for w in short)
    # measures only ("After 15 years"): every word, and its numbers
    return all(_said(_stem(w), near) for w in words) and all(f" {n} " in near for n in re.findall(r"\d+", label))


def _words(label: str) -> list[str]:
    return [w for w in re.sub(r"[^a-z0-9]+", " ", label.lower()).split()
            if len(w) > 2 and not w.isdigit() and w not in _STOP]


def _key_word(label: str) -> str | None:
    return next((w for w in _words(label) if w not in _GENERIC), None)


def _named_among(labels: set[str], near: str, tight: str | None = None) -> set[str]:
    """The labels named in `near`. Labels that share their naming word
    ("Country A", "Country B") are named only in full ("country c")."""
    keys = {label: _key_word(label) for label in labels}
    counts = defaultdict(int)
    for k in keys.values():
        counts[k] += 1
    return {label for label, k in keys.items()
            if (_key(label) in near if k is not None and counts[k] > 1 else _named(label, near, tight))}


_VALUE_TICK = re.compile(r"[$£€]?\s*(?!(?:1[89]|20)\d\d\b)\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*%?\s*[kKmM]?")
_YEAR = re.compile(r"(1[89]|20)\d\d")
_YEAR_IN = re.compile(r"\b(?:1[89]|20)\d\d\b")
_TIME_SHIFT = re.compile(r"\b(?:after|afterwards|then|later|subsequently|thereafter|following|until|till|before|since|"
                         r"next|eventually|finally|by the end)\b", re.IGNORECASE)
# A figure given as a level ("rose to", "stood at", "reached", "was") - unless a
# difference is named before it ("the gap reached") or after it ("6% higher").
_LEVEL = re.compile(r"\b(?:to|at|reach(?:ed|ing|es)?|hit(?:s|ting)?|stood|was|were|is|are|from)(?:\s+(?:about|around|"
                    r"approximately|roughly|nearly|almost|just|over|under|some|only|exactly|precisely|merely))*\s*$",
                    re.IGNORECASE)
_DIFF_BEFORE = re.compile(r"\b(?:gap|difference|margin|increase|rise|growth|drop|fall|decline|decrease|jump|gain|loss|"
                          r"change|lead|surplus|deficit)s?\b(?:\s+(?:in|between|for|among)\b[^,;.]{0,50}?)?"
                          r"(?:\s+(?:of|was|were|is|are|stood at|reached|hit|amounted to|came to|totall?ed|widened to|"
                          r"narrowed to))?(?:\s+(?:about|around|approximately|roughly|nearly|almost|just|over|under|some|"
                          r"only))*\s*$", re.IGNORECASE)  # the difference the figure is: "the gap between X and Y reached"
_DIFF_AFTER = re.compile(r"\s*(?:more|less|fewer|higher|lower|greater|smaller|bigger|larger|points?|percentage points?|"
                         r"times|above|below|ahead|behind)\b", re.IGNORECASE)
_SUM = re.compile(r"\b(?:together|combined|total|altogether|in all|sum)\b", re.IGNORECASE)


def _numbers_in(text: str) -> set[float]:
    return {float(x.replace(",", "")) for x in re.findall(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?", text or "")}


def _values(block: dict) -> list[dict]:
    """A visual's figures; a cell printed as text with one figure in it
    ("$556 million", a table's cost) counts as that figure - the 2026-10-27
    A8 run read v2-p068's costs as text, and "$556 million" and "almost
    double the cost" were missed."""
    out = []
    for v in block.get("values", []):
        if v.get("value") is None and v.get("text"):
            found = [f for f in figures(v["text"]) if not f.get("ratio") and not f["year"]]
            if len(found) == 1:
                v = {**v, "value": found[0]["value"], "precision": v.get("precision") or 0.0}
        if v.get("value") is not None:
            out.append(v)
    return out


def _in_labels(f: float, labels: set[float]) -> bool:
    return any(abs(f - x * s) <= 1e-9 * max(1.0, abs(f)) for x in labels for s in (1, 1e3, 1e6, 1e9))


def _naming(fig: dict, vals: list[dict]) -> tuple[set[str], set[str]]:
    """The categories and points named just before a figure."""
    near = _key(fig.get("before", fig.get("context", "")))
    tight = _key(fig.get("tight", fig.get("before", "")))
    series_named = _named_among({v["series"] for v in vals}, near, tight)
    points_named = _named_among({v["point"] for v in vals}, near, tight)
    if not points_named and fig.get("lead"):
        # a year the sentence opens with, no figure in between: "In 2011, ... rose to 6%" is 2011's
        years = {v["point"] for v in vals if _YEAR.fullmatch(v["point"].strip())}
        points_named = _named_among(years, _key(fig["lead"]), _key(fig["lead"]))
    return series_named, points_named


def named_cells(fig: dict, reading: dict | None) -> list[dict]:
    """The cells a figure names by both category and point ("In 2011, ...
    the United States, Brazil and Japan rose to 6%": the three 2011 cells)."""
    out = []
    for b in (reading or {}).get("blocks", []):
        vals = _values(b)
        series_named, points_named = _naming(fig, vals)
        out += [v for v in vals if v["series"] in series_named and v["point"] in points_named]
    return out


def match_figure(fig: dict, reading: dict | None, question: str) -> str:
    """'raw' | 'raw-uncertain' | 'derived' | 'label' | 'miss' - `reading` is
    a VisualSheet as a dict (model_dump)."""
    blocks = reading.get("blocks", []) if reading else []
    label_text = " ".join([question] + [
        # an axis's value ticks (0, 20 ... 100) are no labels: "reached 100% in 2004" is a figure to check
        " ".join([b.get("title", ""), " ".join(b.get("time_points", [])),
                  " ".join(x for x in b.get("axis_labels", []) if not _VALUE_TICK.fullmatch(x.strip())),
                  " ".join(e["label"] for e in b.get("legend", []))]
                 + [f"{v['series']} {v['point']} {v.get('text', '')}" for v in b.get("values", [])]
                 + [f"{f['name']} {f['location']} " + " ".join(s["time"] + " " + s["detail"] for s in f["states"])
                    for f in b.get("map_features", [])]
                 + ([" ".join(b["process"]["steps"])] if b.get("process") else []))
        for b in blocks])
    labels = _numbers_in(label_text)
    counts = set()
    for b in blocks:
        if b.get("process"):
            counts |= {len(b["process"]["steps"]), len(b["process"]["stages"])}
        counts |= {len({v["series"] for v in b.get("values", [])}), len({v["point"] for v in b.get("values", [])})}
        counts.add(len(b.get("map_features", [])))
    if fig.get("ratio"):
        return _match_ratio(fig, blocks)
    f = fig["value"]
    years = [y for y in labels if 1800 <= y <= 2100 and y == int(y)]
    if fig["year"]:
        # a year on the axis, or between two years it shows (a line graph's axis runs on)
        return "label" if f in labels or (len(years) >= 2 and min(years) <= f <= max(years)) else "miss"
    if _in_labels(f, labels) and not fig["pct"]:
        return "label"

    def tol(v: float, prec: float) -> float:
        base = max(prec, 0.5, 0.01 * abs(v))
        return max(base, 0.1 * abs(v), 1.0) if fig["approx"] else base

    # A figure is compared with the categories named just before it ("Sport rose
    # from 15% to 25%"): a wrong figure that happens to be another category's
    # is not "found". With no category named nearby, any figure counts.
    # A level is no difference: "rose to 6%", "stood at 6%" give a figure, not the gap
    # between two others (#95's "6%" passed as 18 - 13); "a gap of", "rose by",
    # "6% higher than" do - and a change is worked out from its category's own figures.
    change = is_difference(fig)
    level = not change and _LEVEL.search(fig.get("prev", ""))
    uncertain_hit = unreadable = False
    pools = []  # per visual: (the figures this one may be, whether a category was named)
    for b in blocks:
        vals = _values(b)
        series_named, points_named = _naming(fig, vals)
        both = [v for v in vals if v["series"] in series_named and v["point"] in points_named]
        if both:  # "doctors in the US": that cell only (a sum of two other cells is no match)
            named = both
        elif series_named:  # a category named: its figures only (at any point)
            named = [v for v in vals if v["series"] in series_named]
        else:
            named = [v for v in vals if v["point"] in points_named]
        # the cell it names can't be checked at all (a missing bar, look-alike legend patterns -
        # not just two reads apart): never a mistake (#54's "65+ started at 0%")
        unreadable |= bool(both) and all(_structural(v) for v in both)
        wide = [v for v in vals if v["series"] in series_named or v["point"] in points_named]
        pools.append((wide if change and wide else named or vals, bool(named)))
        for v in named or vals:
            for s in _SCALE_TRIES:
                if abs(f - v["value"] * s) <= tol(v["value"], v["precision"]) * s:
                    if v["status"] == "verified":
                        return "raw"
                    uncertain_hit = True
    if uncertain_hit or unreadable:
        return "raw-uncertain"
    summed = _SUM.search(f"{fig.get('prev', '')} {fig.get('after', '')}")
    for vals, named in pools:  # differences, sums, ratios and changes of two figures in one visual
        for i, a in enumerate(vals):
            for c in vals[i + 1:]:
                if not (a["series"] == c["series"] or a["point"] == c["point"]):
                    continue
                x, y, prec = a["value"], c["value"], a["precision"] + c["precision"]
                if level:  # a level: only a sum two figures make together ("together reached 31%")
                    cands = [x + y] if summed else []
                else:
                    cands = [abs(x - y), x + y]
                    if min(x, y) > 0:
                        cands += [max(x, y) / min(x, y), abs(x - y) / min(x, y) * 100, abs(x - y) / max(x, y) * 100]
                for cand in cands:
                    for s in _SCALE_TRIES:
                        if abs(f - cand * s) <= max(tol(cand, prec), 0.15 * abs(cand) if fig["approx"] else 0) * s:
                            return "derived"
        if named:
            continue
        totals = defaultdict(float)
        for v in vals:
            totals[v["point"]] += v["value"]
        if any(abs(f - t * s) <= max(1.0, 0.02 * t) * s for t in totals.values() for s in _SCALE_TRIES):
            return "derived"
    if not fig["pct"] and (f in counts or f in labels):  # "six age groups" - "6%" is no count (#95: six years)
        return "label"
    return "miss"


def is_difference(fig: dict) -> bool:
    """Does the report give this figure as a difference or a change ("a gap
    of", "rose by", "6% higher than") rather than as a level?"""
    return bool(_DIFF_BEFORE.search(fig.get("clause", fig.get("prev", ""))) or _DIFF_AFTER.match(fig.get("after", ""))
                or re.search(r"\bby$", fig.get("prev", "").strip(), re.IGNORECASE))


def _structural(v: dict) -> bool:
    """An uncertain figure no precision can bound - a code check's (a missing
    bar, look-alike legend patterns), not the two reads' disagreement."""
    return v.get("status") == "uncertain" and not str(v.get("note", "")).startswith(("the reads disagree", "confirmed"))


def _match_ratio(fig: dict, blocks: list[dict]) -> str:
    """'derived' when two figures of one visual (the same point or the same
    series; the categories named near the word when it names any) stand in
    that ratio, within what their precisions allow; 'raw-uncertain' when the
    visual has no figures to compare; else 'miss'."""
    near = _key(fig.get("before", ""))
    slack = 0.12 if fig["approx"] else 0.05
    f = fig["value"]
    if not any(len([v for v in _values(b) if v["status"] == "verified"]) >= 2 for b in blocks):
        # a map's or a process's "two-thirds of the site": no figures to compare - never a mistake
        return "raw-uncertain"
    for b in blocks:
        vals = [v for v in _values(b) if v["value"] and v["status"] == "verified"]
        names = _named_among({v["series"] for v in vals}, near) | _named_among({v["point"] for v in vals}, near)
        named = [v for v in vals if v["series"] in names or v["point"] in names]
        pool = vals if not named else [v for v in vals if v in named or v["point"] in {n["point"] for n in named}
                                       or v["series"] in {n["series"] for n in named}]
        for a in pool:
            for c in pool:
                if a is c or not (a["series"] == c["series"] or a["point"] == c["point"]) or c["value"] <= 0:
                    continue
                lo = max(a["value"] - a["precision"], 0) / (c["value"] + c["precision"])
                hi = (a["value"] + a["precision"]) / max(c["value"] - c["precision"], 1e-9)
                if lo * (1 - slack) <= f <= hi * (1 + slack):
                    return "derived"
    return "miss"


def find_cell(reading: dict | None, series: str, point: str) -> dict | None:
    """The sheet's figure for a series and point named the way the visual
    labels them (a letter or two misread allowed); None when the sheet has
    no such figure."""
    def same(a: str, b: str) -> bool:
        x, y = re.sub(r"[^a-z0-9]+", "", (a or "").lower()), re.sub(r"[^a-z0-9]+", "", (b or "").lower())
        return x == y or (bool(x) and bool(y) and difflib.SequenceMatcher(None, x, y).ratio() >= 0.8)

    for b in (reading or {}).get("blocks", []):
        for v in b.get("values", []):
            if same(v.get("series"), series) and same(v.get("point"), point):
                return v
            if same(v.get("series"), point) and same(v.get("point"), series):  # named the other way round
                return v
    return None


def figure_agrees(fig: dict, value: float, precision: float) -> bool:
    """Is the student's figure this value, within its precision (and the
    leeway "about" gives)?"""
    base = max(precision, 0.5, 0.01 * abs(value))
    tol = max(base, 0.1 * abs(value), 1.0) if fig["approx"] else base
    return any(abs(fig["value"] - value * s) <= tol * s for s in _SCALE_TRIES)


def uncertain_labels(reading: dict | None) -> set[str]:
    """The categories (series and points) with a figure the sheet can't
    confirm - a Task mistake about them is never kept."""
    out = set()
    for b in (reading or {}).get("blocks", []):
        for v in b.get("values", []):
            if v.get("status") == "uncertain":
                out |= {x for x in (v.get("series"), v.get("point")) if x}
    return out


def unchecked_figures(answer: str, reading: dict | None, question: str) -> list[str]:
    """The report's figures the sheet can't check: near an UNCERTAIN figure,
    or set aside by the focused re-reads (reading["accepted_figures"]) -
    each of them is never a mistake, and the report says so."""
    if not reading:
        return []
    accepted = set(reading.get("accepted_figures") or [])
    agreed = set(reading.get("agreed_figures") or [])
    return [f["text"] for f in figures(answer) if f["text"] not in agreed and (
        f["text"] in accepted or _names_unread(f, reading) or match_figure(f, reading, question) == "raw-uncertain")]


def _names_unread(fig: dict, reading: dict) -> bool:
    """Does the figure name a series and point the sheet couldn't read (a
    cropped bar, a cell with no value)? Its figure is then not checked, even
    when it happens to equal some difference of other figures."""
    near, tight = _key(fig.get("before", "")), _key(fig.get("tight", fig.get("before", "")))
    for b in reading.get("blocks", []):
        vals = b.get("values", [])
        series = _named_among({v["series"] for v in vals}, near, tight)
        points = _named_among({v["point"] for v in vals}, near, tight)
        if any(v.get("value") is None and not v.get("text") and v["series"] in series and v["point"] in points
               for v in vals):
            return True
    return False


def _loosely(label: str, text: str) -> bool:
    """A category mentioned in any of its own words - a paraphrase counts
    ("proximity to home" for "Close to parental home")."""
    if not label:
        return True
    if not _words(label) and re.search(r"\d", label):
        return all(f" {n} " in text for n in re.findall(r"\d+", label))
    words = [w for w in _words(label) if w not in _GENERIC and len(w) >= 4]
    return any(_said(_stem(w), text) for w in words) if words else _named(label, text)


def protected_mistake(wrong: str, correct: str, why: str, reading: dict | None, question: str,
                      unchecked: set[str]) -> bool:
    """A Task (data) mistake code never keeps (the academic lead, 2026-10-27):
    it is about a figure the sheet can't check (`unchecked`: near an
    UNCERTAIN figure or set aside by a focused re-read); it "corrects" figures
    the sheet confirms; or it is about a category with an UNCERTAIN figure
    (#56: two look-alike legend patterns). A figure two readings agree is
    wrong ("confirmed_wrong") is never protected."""
    if not reading:
        return False
    figs = figures(wrong)
    if any(f["text"] in set(reading.get("confirmed_wrong") or []) for f in figs):
        # matched again out of its sentence, "rose to 6%" passes as another cell's 6 (#62)
        return False
    if any(f["text"] in unchecked for f in figs):
        return True
    agreed = set(reading.get("agreed_figures") or [])  # the sheet, a focused re-read and the student agree
    statuses = ["raw" if f["text"] in agreed else match_figure(f, reading, question) for f in figs]
    if figs and all(s in ("raw", "derived", "label") for s in statuses):
        if {f["value"] for f in figures(correct)} - {f["value"] for f in figs}:
            return True
    text = _key(f"{wrong} {correct} {why}")
    for b in reading.get("blocks", []):
        for v in b.get("values", []):
            if v.get("status") == "uncertain" and _loosely(v.get("series", ""), text) \
                    and _loosely(v.get("point", ""), text):
                return True
    return False


_NOT_DATA = re.compile(r"\b(process|life cycle|cycle of|how .{0,40} (?:is|are) (?:made|produced|manufactured|built|"
                       r"recycled|formed|generated|created)|stages?|flow ?chart|maps?|plans?|layout)\b", re.IGNORECASE)
_DATA = re.compile(r"\b(chart|graph|table|percentage|proportion|figures?|number of|amount of)\b", re.IGNORECASE)


def visual_kind(question: str, reading: dict | None = None) -> str | None:
    """line, bar, table, pie, process, cycle, map or mixed - from the sheet,
    else from the question's wording; None when it can't be told. Picks the
    Band 9 anchor of the same kind (services/ai/writing_anchors.py)."""
    blocks = (reading or {}).get("blocks", [])
    kinds = {b.get("visual_type") for b in blocks} - {"other", None}
    if len(kinds) > 1:
        return "mixed"
    if kinds:
        kind = kinds.pop()
        if kind == "process":
            cyclical = any((b.get("process") or {}).get("kind") == "cyclical" for b in blocks)
            return "cycle" if cyclical or re.search(r"\bcycle\b", question, re.IGNORECASE) else "process"
        return kind
    q = question.lower()
    if re.search(r"\bcycle\b", q):
        return "cycle"
    if re.search(r"\b(process|flow ?chart|is made|are made|produced|manufactured)\b", q):
        return "process"
    if re.search(r"\b(maps?|plans?)\b", q):
        return "map"
    if "table" in q and re.search(r"\b(chart|graph)\b", q):
        return "mixed"
    for word, kind in (("pie", "pie"), ("table", "table"), ("line graph", "line"), ("bar", "bar")):
        if word in q:
            return kind
    return None


def data_visual(question: str, reading: dict | None = None) -> bool:
    """Is it a visual of figures (line, bar, pie, table, mixed) - which
    needs comparisons - rather than a process, cycle or map? From the sheet
    when there is one, else from the question's wording."""
    kinds = {b.get("visual_type") for b in (reading or {}).get("blocks", [])}
    if kinds:
        return bool(kinds & {"line", "bar", "pie", "table"})
    if _NOT_DATA.search(question) and not _DATA.search(question.split("Summari")[0]):
        return False
    return True


def data_check(answer: str, reading: dict | None, question: str) -> str:
    """The DATA CHECK section added to the data sheet for judging a report:
    which of its figures agree with the sheet ("" when it gives none)."""
    if not reading or reading.get("readability") == "unreadable":
        return ""
    figs = figures(answer)
    if not figs:
        return ("DATA CHECK (by software): the report gives no figures - judge whether its key features are "
                "illustrated with data.")
    accepted = set(reading.get("accepted_figures") or [])
    confirmed = set(reading.get("confirmed_wrong") or [])
    agreed = set(reading.get("agreed_figures") or [])
    for fig in figs:
        fig["match"] = "accepted" if fig["text"] in accepted else "raw" if fig["text"] in agreed \
            else match_figure(fig, reading, question)
    ok = [f for f in figs if f["match"] in ("raw", "derived", "label")]
    unsure = [f for f in figs if f["match"] in ("raw-uncertain", "accepted")]
    wrong = [f for f in figs if f["match"] == "miss" and f["text"] in confirmed]
    other = [f for f in figs if f["match"] == "miss" and f["text"] not in confirmed]
    lines = [f"DATA CHECK (by software, against the sheet within its precision): {len(ok)} of {len(figs)} figures in "
             "the report agree with the visual."]
    if wrong:
        lines.append("WRONG - the sheet and a second, focused reading of the visual agree, and the student's figure "
                     "differs by more than the reading precision (a TR mistake):")
        lines += [f"- «{f['text']}» in \"…{f['context'].strip()}…\"" for f in wrong[:12]]
    if other:
        lines.append("Not found on the sheet - check each against the sheet before calling it a mistake (it may "
                     "be a fair approximation or a figure the sheet doesn't list):")
        lines += [f"- «{f['text']}» in \"…{f['context'].strip()}…\"" for f in other[:12]]
    if unsure:
        lines.append("NOT CHECKED - near an UNCERTAIN figure, or a second reading disagreed: never a mistake, and "
                     "Task Achievement is never lowered for them: " + ", ".join(f"«{f['text']}»" for f in unsure[:10]))
    lines.append("Every other figure agrees with the visual: never mark it as a mistake or as invented.")
    return "\n".join(lines)
