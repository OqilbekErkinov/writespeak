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


def figures(text: str) -> list[dict]:
    """Every figure in a text: value, approximate or not, a year or not, a percentage or not."""
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
        clause = re.split(r"[.;:!?]\s|,\s(?:while|whereas|but|and)\s", text[max(0, m.start() - 90):m.start()])[-1]
        after = re.split(r"[,.;:()]|\d", text[m.end():m.end() + 35])[0]
        out.append({"text": m.group(0).strip(), "value": value, "approx": bool(m.group("approx")), "year": year,
                    "pct": "%" in suffix or "cent" in suffix, "before": f"{clause} {after}",
                    "context": text[max(0, m.start() - 40):m.end() + 25].replace("\n", " ")})
    return out


def _key(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", text.lower()) + " "


def _named(label: str, near: str) -> bool:
    """Is the category `label` named in `near` (already _key'ed)? Its first
    word is enough ("Health Care" in "health care volunteers"; "Environmental"
    in "environmental organisations")."""
    words = [w for w in re.sub(r"[^a-z0-9]+", " ", label.lower()).split() if len(w) > 2 and not w.isdigit()]
    if not words:
        return False
    stem = words[0][:-1] if words[0].endswith("s") and len(words[0]) > 4 else words[0]
    return f" {stem}" in near


def _numbers_in(text: str) -> set[float]:
    return {float(x.replace(",", "")) for x in re.findall(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?", text or "")}


def match_figure(fig: dict, reading: dict | None, question: str) -> str:
    """'raw' | 'raw-uncertain' | 'derived' | 'label' | 'miss' - `reading` is
    a VisualSheet as a dict (model_dump)."""
    blocks = reading.get("blocks", []) if reading else []
    label_text = " ".join([question] + [
        " ".join([b.get("title", ""), " ".join(b.get("time_points", [])), " ".join(e["label"] for e in b.get("legend", []))]
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
    f = fig["value"]
    years = [y for y in labels if 1800 <= y <= 2100 and y == int(y)]
    if fig["year"]:
        # a year on the axis, or between two years it shows (a line graph's axis runs on)
        return "label" if f in labels or (len(years) >= 2 and min(years) <= f <= max(years)) else "miss"
    if f in labels and not fig["pct"]:
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
        vals = [v for v in b.get("values", []) if v.get("value") is not None]
        named = [v for v in vals if _named(v["series"], near) or _named(v["point"], near)]
        series_named = {v["series"] for v in named if _named(v["series"], near)}
        if series_named:  # a category named: its figures only (at any point)
            named = [v for v in vals if v["series"] in series_named]
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
