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
    ends = sorted(m.end() for r in (FIG_RE, FRACTION_RE) for m in r.finditer(text))

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
        # names the years, not the maximum salary
        after = " ".join(re.split(r"[,.;:()]|\d", text[m.end():m.end() + 35])[0].split()[:3])
        out.append({"text": m.group(0).strip(), "value": value, "approx": bool(m.group("approx")), "year": year,
                    "pct": "%" in suffix or "cent" in suffix, "before": f"{clause} {after}",
                    "context": text[max(0, m.start() - 40):m.end() + 25].replace("\n", " ")})
    for m in FRACTION_RE.finditer(text):
        clause = re.split(r"[.;:!?]\s", text[since_last(m.start(), 120):m.start()])[-1]
        after = re.split(r"[.;:()]", text[m.end():m.end() + 60])[0]
        out.append({"text": m.group(0).strip(), "value": _ratio(m.group("word")), "approx": bool(m.group("approx")),
                    "year": False, "pct": False, "ratio": True, "before": f"{clause} {after}",
                    "context": text[max(0, m.start() - 40):m.end() + 25].replace("\n", " ")})
    return out


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


def _named(label: str, near: str) -> bool:
    """Is the category `label` named in `near` (already _key'ed)? Its first
    word is enough ("Health Care" in "health care volunteers"; "Environmental"
    in "environmental organisations") - its first word that is not a
    stopword or a measure ("the" named "Go to the cinema" in every sentence;
    "years" named "Years taken to reach the top salary"); a label of measures
    only needs them all."""
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


def _named_among(labels: set[str], near: str) -> set[str]:
    """The labels named in `near`. Labels that share their naming word
    ("Country A", "Country B") are named only in full ("country c")."""
    keys = {label: _key_word(label) for label in labels}
    counts = defaultdict(int)
    for k in keys.values():
        counts[k] += 1
    return {label for label, k in keys.items()
            if (_key(label) in near if k is not None and counts[k] > 1 else _named(label, near))}


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


def match_figure(fig: dict, reading: dict | None, question: str) -> str:
    """'raw' | 'raw-uncertain' | 'derived' | 'label' | 'miss' - `reading` is
    a VisualSheet as a dict (model_dump)."""
    blocks = reading.get("blocks", []) if reading else []
    label_text = " ".join([question] + [
        " ".join([b.get("title", ""), " ".join(b.get("time_points", [])), " ".join(b.get("axis_labels", [])),
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
    near = _key(fig.get("before", fig.get("context", "")))
    uncertain_hit = False
    pools = []  # per visual: (the figures this one may be, whether a category was named)
    for b in blocks:
        vals = _values(b)
        series_named = _named_among({v["series"] for v in vals}, near)
        points_named = _named_among({v["point"] for v in vals}, near)
        both = [v for v in vals if v["series"] in series_named and v["point"] in points_named]
        if both:  # "doctors in the US": that cell only (a sum of two other cells is no match)
            named = both
        elif series_named:  # a category named: its figures only (at any point)
            named = [v for v in vals if v["series"] in series_named]
        else:
            named = [v for v in vals if v["point"] in points_named]
        pools.append((named or vals, bool(named)))
        for v in named or vals:
            for s in _SCALE_TRIES:
                if abs(f - v["value"] * s) <= tol(v["value"], v["precision"]) * s:
                    if v["status"] == "verified":
                        return "raw"
                    uncertain_hit = True
    if uncertain_hit:
        return "raw-uncertain"
    for vals, named in pools:  # differences, sums, ratios and changes of two figures in one visual
        for i, a in enumerate(vals):
            for c in vals[i + 1:]:
                if not (a["series"] == c["series"] or a["point"] == c["point"]):
                    continue
                x, y, prec = a["value"], c["value"], a["precision"] + c["precision"]
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
    if f in counts or f in labels:
        return "label"
    return "miss"


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


def data_check(answer: str, reading: dict | None, question: str) -> str:
    """The DATA CHECK section added to the data sheet for judging a report:
    which of its figures agree with the sheet ("" when it gives none)."""
    if not reading or reading.get("readability") == "unreadable":
        return ""
    figs = figures(answer)
    if not figs:
        return ("DATA CHECK (by software): the report gives no figures - judge whether its key features are "
                "illustrated with data.")
    for fig in figs:
        fig["match"] = match_figure(fig, reading, question)
    ok = [f for f in figs if f["match"] in ("raw", "derived", "label")]
    unsure = [f for f in figs if f["match"] == "raw-uncertain"]
    wrong = [f for f in figs if f["match"] == "miss"]
    lines = [f"DATA CHECK (by software, against the sheet within its precision): {len(ok)} of {len(figs)} figures in "
             "the report agree with the visual."]
    if wrong:
        lines.append("Not found on the sheet - check each against the sheet before calling it a mistake (it may "
                     "be a fair approximation or a figure the sheet doesn't list):")
        lines += [f"- «{f['text']}» in \"…{f['context'].strip()}…\"" for f in wrong[:12]]
    if unsure:
        lines.append("Near an UNCERTAIN figure - never a mistake: " + ", ".join(f"«{f['text']}»" for f in unsure[:8]))
    lines.append("Every other figure agrees with the visual: never mark it as a mistake or as invented.")
    return "\n".join(lines)
