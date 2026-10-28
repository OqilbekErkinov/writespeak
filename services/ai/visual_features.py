"""The key features of a Task 1 visual - what a Band 7-8 report would have to
mention - computed in code from the checked data sheet (Task 1 prompt A4,
2026-10-22), never by a model:

  charts and tables  the highest and lowest at each time point (and what led
                     throughout), the largest rise and fall (absolute and
                     relative), rank changes and overtakes, fluctuations and
                     peaks, what stayed stable, the overall direction and its
                     exceptions, equal values, gaps between two series,
                     combined totals;
  maps               what was added, removed or changed, and the overall
                     direction the reader saw;
  processes          the number of steps, the logical stages, the start and
                     end, linear or cyclical.
A visual the code can't analyse (a diagram of objects) falls back on the
reader's one-line `overall`.

Figures are quoted only when verified. An uncertain one (the two reads
disagreed, a check failed) is kept wide enough to cover both reads and only
used for a ranking it can't change ("15-24 was the highest throughout").
"""
from __future__ import annotations

import re

from services.ai.schemas import KeyFeatures, SheetBlock, VisualSheet

MAX_COMPARISONS = 9
_TOTAL = re.compile(r"\b(total|overall|all\s+\w+\s+combined|sum)\b", re.IGNORECASE)
_YEARISH = re.compile(r"^((1[5-9]|20)\d\d)s?$|^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", re.IGNORECASE)


def _n(v: float) -> str:
    if abs(v) >= 10000:
        return f"{v:,.0f}"
    return str(int(round(v))) if abs(v - round(v)) < 0.05 else f"{v:.1f}"


def _pct(units: str) -> bool:
    return "%" in units or "percent" in units.lower()


def _v(v: float, units: str) -> str:
    return f"{_n(v)}%" if _pct(units) else _n(v)


def _delta(d: float, units: str) -> str:
    sign = "+" if d > 0 else "−" if d < 0 else "±"
    return f"{sign}{_n(abs(d))} pts" if _pct(units) else f"{sign}{_n(abs(d))}"


def _and(items) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _order(labels: list[str]) -> list[str]:
    """Labels in their order of first appearance - numerically when all are numbers or years."""
    seen = list(dict.fromkeys(labels))
    nums = []
    for x in seen:
        m = re.match(r"^\s*(\d{1,4}(?:\.\d+)?)", x)
        if not m:
            return seen
        nums.append(float(m.group(1)))
    return [x for _, x in sorted(zip(nums, seen), key=lambda t: t[0])]


def _yearish(labels) -> bool:
    labels = [x.strip() for x in labels if x.strip()]
    return bool(labels) and all(_YEARISH.match(x) for x in labels)


def _ord(n: int) -> str:
    return {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth"}.get(n, f"{n}th")


INF = float("inf")


def _bounded(v) -> bool:
    """A figure whose error the sheet can bound: verified, or the two reads'
    disagreement (its value and ± cover both)."""
    return v.status == "verified" or (v.note or "").startswith(("the reads disagree", "confirmed"))


class _Grid:
    """series x point -> (value, precision). `cell`: verified figures only;
    `loose`: every figure, the uncertain ones with a precision wide enough to
    cover both reads - for rankings only."""

    def __init__(self, block: SheetBlock):
        numeric = [v for v in block.values if v.value is not None]
        rows = [(v.series, v.point, v) for v in numeric]
        # Years as the legend and categories on the axis (a bar chart of countries
        # in 1995 and 2005): turned round, so the points are the years.
        if _yearish({s for s, _, _ in rows}) and not _yearish({p for _, p, _ in rows}):
            rows = [(p, s, v) for s, p, v in rows]
        # A "Total" row or column is not a category: it is kept apart, for the totals line.
        self.totals = {(s, p): (v.value, v.precision) for s, p, v in rows
                       if v.status == "verified" and (_TOTAL.search(s) or _TOTAL.search(p))}
        rows = [(s, p, v) for s, p, v in rows if not (_TOTAL.search(s) or _TOTAL.search(p))]
        # A series only one read saw at all (#93's dashed "recommended intake" line) is no category to rank.
        alone = {s for s, _, _ in rows} - {s for s, _, v in rows if v.note != "seen by one read only"}
        rows = [(s, p, v) for s, p, v in rows if s not in alone]
        self.series = list(dict.fromkeys(s for s, _, _ in rows))
        self.points = _order([p for _, p, _ in rows])
        self.cell = {(s, p): (v.value, v.precision) for s, p, v in rows if v.status == "verified"}
        # Every figure for rankings: one the reads disagree on with the precision
        # that covers both; one no precision can bound (a look-alike legend, a
        # missing bar, seen by one read) as INF - it blocks every claim it could change.
        self.loose = {(s, p): (v.value, v.precision if _bounded(v) else INF) for s, p, v in rows}
        self.units = block.units
        self.uniform = bool(block.units) and not re.search(r"\band\b|axis|;|,|/", block.units, re.IGNORECASE)

    def get(self, s: str, p: str):
        return self.cell.get((s, p))

    def show(self, s: str, p: str) -> str:
        """A figure as the features print it: an uncertain one as "about"."""
        v = self.loose[(s, p)]
        return ("" if (s, p) in self.cell else "about ") + _v(v[0], self.units)

    @staticmethod
    def close(a, b) -> bool:
        """Two figures too close to tell apart."""
        return abs(a[0] - b[0]) <= max(a[1] + b[1], 0.5)

    @staticmethod
    def equal(a, b) -> bool:
        """Two figures a report may call equal: the same reading, give or take rounding."""
        top = max(abs(a[0]), abs(b[0]))
        return abs(a[0] - b[0]) <= max(0.01 * top, min(0.5, 0.05 * top))

    def top(self, p: str, lowest: bool = False) -> str | None:
        """The series clearly highest (or lowest) at point p, by every figure."""
        here = [(s, self.loose[(s, p)]) for s in self.series if (s, p) in self.loose]
        if len(here) < 2:
            return None
        here.sort(key=lambda x: x[1][0], reverse=not lowest)
        first, second = here[0], here[1]
        return first[0] if not self.close(first[1], second[1]) else None


def _clear_first(ranked: list, key, spread) -> bool:
    """Is the first of `ranked` (sorted best first by `key`) ahead of the
    second by more than both their precisions? An extreme is named only then:
    the biggest of the series read for sure is no "biggest" (2026-10-28, #94:
    "Columbia, the biggest rise" with Washington's uncertain +157 left out)."""
    return len(ranked) == 1 or abs(key(ranked[0]) - key(ranked[1])) > spread(ranked[0]) + spread(ranked[1])


def _time_like(block: SheetBlock, grid: _Grid) -> bool:
    if len(grid.points) < 2:
        return False
    if block.visual_type == "line" or _yearish(grid.points):
        return True
    return bool(block.time_points) and set(grid.points) <= set(block.time_points)


def _stable(grid: _Grid, first, last) -> bool:
    """Too small a change to call a rise or fall."""
    d = last[0] - first[0]
    if abs(d) <= (first[1] + last[1]) / 2:
        return True
    if _pct(grid.units):
        return abs(d) <= 1.5
    return first[0] != 0 and abs(d / first[0]) < 0.05


def _throughout(grid: _Grid, leaders: list[str | None], word: str) -> str:
    """'X was the highest throughout' when no other series ever led - also
    when one or two points are too close to call."""
    known = [t for t in leaders if t]
    if not known or len(set(known)) != 1 or len(known) < max(2, -(-len(leaders) * 2 // 3)):
        return ""
    unclear = [p for p, t in zip(grid.points, leaders) if not t]
    if not unclear:
        return f"{known[0]} was the {word} throughout."
    return f"{known[0]} was the {word} throughout, except {_and(unclear)} (too close to call)."


def _shape(grid: _Grid, s: str) -> str:
    """A line's turning point, when it has one clearly inside the period:
    'X fell from 17% (1988) to a low of 4% (2000), then rose to 6% (2008)'."""
    seq = [(p, grid.loose[(s, p)]) for p in grid.points if (s, p) in grid.loose]
    if len(seq) < 3:
        return ""
    (p0, a), (p1, b) = seq[0], seq[-1]
    for kind, pick in (("low", min), ("peak", max)):
        pt, v = pick(seq[1:-1], key=lambda x: x[1][0])
        dips = v[0] < min(a[0], b[0]) if kind == "low" else v[0] > max(a[0], b[0])
        margin = min(abs(v[0] - a[0]), abs(v[0] - b[0]))
        if dips and margin > v[1] + max(a[1], b[1]) + 0.5 and margin >= 0.1 * max(abs(a[0]), abs(b[0]), 1):
            first, then = ("fell", "rose") if kind == "low" else ("rose", "fell")
            return (f"{s} {first} from {grid.show(s, p0)} ({p0}) to a {kind} of {grid.show(s, pt)} ({pt}), then "
                    f"{then} to {grid.show(s, p1)} ({p1}).")
    return ""


def _time_features(block: SheetBlock, grid: _Grid, out: dict) -> None:
    u, p0, p1 = grid.units, grid.points[0], grid.points[-1]
    rows = []  # (series, first, last, change) - every series, an uncertain end with its wide precision
    for s in grid.series:
        a, b = grid.loose.get((s, p0)), grid.loose.get((s, p1))
        if a and b and INF not in (a[1], b[1]):
            rows.append((s, a, b, b[0] - a[0]))
    risers = [r for r in rows if not _stable(grid, r[1], r[2]) and r[3] > 0]
    fallers = [r for r in rows if not _stable(grid, r[1], r[2]) and r[3] < 0]
    stable = [r for r in rows if _stable(grid, r[1], r[2])]
    complete = len(rows) == len(grid.series)

    # The overview: the direction, then what led.
    if rows:
        parts = []
        if risers:
            parts.append(f"{_and(r[0] for r in risers)} rose")
        if fallers:
            parts.append(f"{_and(r[0] for r in fallers)} fell")
        line = ", while ".join(parts)
        if stable:
            line += f"{'; ' if line else ''}{_and(r[0] for r in stable)} stayed about the same"
        if risers and fallers:
            line += (" (most rose)" if len(risers) > len(fallers) else " (most fell)" if len(fallers) > len(risers)
                     else " (a mixed picture)")
        elif len(rows) > 1 and complete and not stable:
            line = f"All of them {'rose' if risers else 'fell'} ({_and(r[0] for r in rows)})"
        out["overview"].append(f"{line} between {p0} and {p1}.")
    tops = [grid.top(p) for p in grid.points]
    lows = [grid.top(p, lowest=True) for p in grid.points]
    lead = _throughout(grid, tops, "highest")
    if lead:
        out["overview"].append(lead)
    elif tops[0] and tops[-1] and tops[0] != tops[-1]:
        a, b = grid.get(tops[-1], p1), grid.get(tops[0], p0)
        figs = f" ({_v(a[0], u)} in {p1}; {_v(b[0], u)} for {tops[0]} in {p0})" if a and b else ""
        out["overview"].append(f"{tops[-1]} became the highest by {p1}, replacing {tops[0]}{figs}.")
    if len(grid.series) > 2:
        low = _throughout(grid, lows, "lowest")
        if low:
            out["overview"].append(low)
    # A line graph's shape: where each line turned (Volume 1 p.8: both peaked at
    # the start, fell until 2000, then rose again).
    if len(grid.points) >= 3:
        shapes = {s: _shape(grid, s) for s in grid.series}
        described = [x for x in shapes.values() if x]
        if described:
            target = out["overview"] if len(grid.series) <= 3 else out["comparisons"]
            target += described[:3]

    cmp = out["comparisons"]

    def ends(r) -> str:
        return f"{grid.show(r[0], p0)} → {grid.show(r[0], p1)}"

    def spread(r) -> float:
        return r[1][1] + r[2][1]

    def ratio(r, lo: bool = False, hi: bool = False) -> float:
        a = r[1][0] + (r[1][1] if lo else -r[1][1] if hi else 0)
        b = r[2][0] + (-r[2][1] if lo else r[2][1] if hi else 0)
        return b / a if a > 0 else 0

    # The biggest rise or fall is named only among all the series, and only
    # when it is clearly the biggest; with one riser it is "the only rise".
    if risers and complete:
        ranked = sorted(risers, key=lambda r: -r[3])
        r = ranked[0]
        if _clear_first(ranked, lambda x: x[3], spread):
            what = "the only rise" if len(ranked) == 1 and len(rows) > 1 else "the biggest rise"
            cmp.append(f"{r[0]}: {ends(r)} ({_delta(r[3], u)}), {what}.")
        by_ratio = sorted(risers, key=lambda x: -ratio(x))
        rel = by_ratio[0]
        clear = len(by_ratio) == 1 or ratio(rel, lo=True) > ratio(by_ratio[1], hi=True)
        if rel[0] != r[0] and clear and ratio(rel) >= 1.5:
            cmp.append(f"{rel[0]} grew the most in relative terms ({ends(rel)}, ×{ratio(rel):.1f}).")
    if fallers and complete:
        ranked = sorted(fallers, key=lambda r: r[3])
        r = ranked[0]
        if _clear_first(ranked, lambda x: x[3], spread):
            rank0 = sorted(rows, key=lambda x: -x[1][0]).index(r) + 1
            rank1 = sorted(rows, key=lambda x: -x[2][0]).index(r) + 1
            moved = f"; it went from {_ord(rank0)} to {_ord(rank1)} place" if rank1 > rank0 else ""
            what = "the only fall" if len(ranked) == 1 and len(rows) > 1 else "the biggest fall"
            cmp.append(f"{r[0]}: {ends(r)} ({_delta(r[3], u)}), {what}{moved}.")
    # Overtakes: the first point where a pair's order flips (any point on a line graph).
    flips = []
    for i, a in enumerate(grid.series):
        for b in grid.series[i + 1:]:
            order, seen = None, None
            for p in grid.points:
                x, y = grid.loose.get((a, p)), grid.loose.get((b, p))
                if not x or not y or grid.close(x, y):
                    continue
                now = x[0] > y[0]
                if order is not None and now != order:
                    winner, loser = (a, b) if now else (b, a)
                    seen = (p, winner, loser)
                    break
                order = now
            if seen:
                p, w, l = seen
                wv, lv = grid.get(w, p1), grid.get(l, p1)
                flips.append((max((wv or (0, 0))[0], (lv or (0, 0))[0]), p, w, l, wv, lv))
    for _, p, w, l, wv, lv in sorted(flips, key=lambda f: -f[0])[:3]:
        end = f"; {_v(wv[0], u)} vs {_v(lv[0], u)} in {p1}" if wv and lv else ""
        cmp.append(f"{w} overtook {l} by {p}{end}.")
    by_change: dict[float, list] = {}
    for r in risers + fallers:
        by_change.setdefault(round(r[3], 1), []).append(r)
    for change, group in by_change.items():
        if len(group) > 1:
            figures = ", ".join(ends(r) for r in group)
            cmp.append(f"{_and(r[0] for r in group)} both {'rose' if change > 0 else 'fell'} by "
                       f"{_delta(abs(change), u).lstrip('+')} ({figures}).")
    for r in stable:
        cmp.append(f"{r[0]} almost unchanged ({ends(r)}).")
    equal = []
    for p in (p0, p1):
        here = [(s, grid.get(s, p)) for s in grid.series if grid.get(s, p)]
        for i, (s1, a) in enumerate(here):
            for s2, b in here[i + 1:]:
                if grid.equal(a, b):
                    equal.append(f"In {p}, {s1} and {s2} were equal at {_v(a[0], u)}.")
    cmp += equal[:2]
    if len(grid.points) >= 3:
        for s in grid.series:
            seq = [(p, grid.loose[(s, p)]) for p in grid.points if (s, p) in grid.loose]
            if len(seq) < 3:
                continue
            peak = max(seq, key=lambda x: x[1][0])
            trough = min(seq, key=lambda x: x[1][0])
            inner = [x[0] for x in seq[1:-1]]
            if peak[0] in inner and peak[1][0] - max(seq[0][1][0], seq[-1][1][0]) > peak[1][1] * 2 + 0.5:
                cmp.append(f"{s} peaked at {grid.show(s, peak[0])} in {peak[0]}.")
            elif trough[0] in inner and min(seq[0][1][0], seq[-1][1][0]) - trough[1][0] > trough[1][1] * 2 + 0.5:
                cmp.append(f"{s} hit its lowest point, {grid.show(s, trough[0])}, in {trough[0]}.")
            moves = []
            for (_, a), (_, b) in zip(seq, seq[1:]):
                if not grid.close(a, b):
                    moves.append(b[0] > a[0])
            turns = sum(1 for x, y in zip(moves, moves[1:]) if x != y)
            if turns >= 2:
                cmp.append(f"{s} fluctuated ({' → '.join(_v(v[0], u) for _, v in seq)}).")
    if len(rows) == 2:
        a, b = rows
        g0, g1 = a[1][0] - b[1][0], a[2][0] - b[2][0]
        slack = max(0.5, spread(a) + spread(b))
        trend = "reversed" if g0 * g1 < 0 and min(abs(g0), abs(g1)) > slack / 2 else \
            "widened" if abs(g1) > abs(g0) + slack else \
            "narrowed" if abs(g1) < abs(g0) - slack else "stayed about the same"
        cmp.append(f"The gap between {a[0]} and {b[0]} {trend}: {_n(abs(g0))} in {p0}, {_n(abs(g1))} in {p1}.")
    shown = [v for (s, p), v in grid.totals.items() if p in (p0, p1) or s in (p0, p1)]
    if len(shown) == 2:
        cmp.append(f"The total: {_n(shown[0][0])} in {p0}, {_n(shown[1][0])} in {p1}.")
    elif grid.uniform and not _pct(u) and len(grid.series) >= 3:
        totals = []
        for p in (p0, p1):
            vals = [grid.get(s, p) for s in grid.series]
            if all(vals):
                totals.append((p, sum(v[0] for v in vals)))
        if len(totals) == 2:
            cmp.append(f"Combined: {_n(totals[0][1])} in {p0}, {_n(totals[1][1])} in {p1}.")
    if risers and fallers:
        out["grouping"].append("risers vs fallers")
    elif block.visual_type == "line" and len(grid.points) >= 3:
        out["grouping"].append("by trend: the main lines first, then the rest")
    else:
        out["grouping"].append("the largest categories first, then the rest")


def _static_features(block: SheetBlock, grid: _Grid, out: dict) -> None:
    u = grid.units
    if len(grid.series) >= 2 and len(grid.points) >= 2:
        if not grid.uniform:
            # Different units per row (a table of two tunnels: depth, length,
            # cost): each row compared across the items only.
            for s in grid.series:
                here = [(p, grid.get(s, p)) for p in grid.points if grid.get(s, p)]
                if len(here) >= 2:
                    ranked = sorted(here, key=lambda x: -x[1][0])
                    out["comparisons"].append(f"{s}: " + " vs ".join(f"{p} {_n(v[0])}" for p, v in ranked) + ".")
            out["grouping"].append("feature by feature, comparing the items side by side")
            return
        # Every figure counts for who is highest or lowest (an uncertain one with
        # its wide precision); an extreme is named only when it is clear.
        def spread(x) -> float:
            return x[1][1]

        for p in grid.points:
            here = sorted(((s, grid.loose[(s, p)]) for s in grid.series if (s, p) in grid.loose),
                          key=lambda x: -x[1][0])
            if len(here) >= 2:
                hi, lo = here[0], here[-1]
                both_sure = (hi[0], p) in grid.cell and (lo[0], p) in grid.cell
                if both_sure and grid.equal(hi[1], lo[1]):
                    out["comparisons"].append(f"{p}: {_and(s for s, _ in here)} equal ({_v(hi[1][0], u)}).")
                elif not grid.close(hi[1], lo[1]) and _clear_first(here, lambda x: x[1][0], spread) \
                        and _clear_first(here[::-1], lambda x: x[1][0], spread):
                    out["comparisons"].append(f"{p}: {hi[0]} {grid.show(hi[0], p)} vs {lo[0]} {grid.show(lo[0], p)}.")
        if len(grid.series) == 2:
            a, b = grid.series
            gaps = [(p, grid.loose[(a, p)][0] - grid.loose[(b, p)][0], grid.loose[(a, p)][1] + grid.loose[(b, p)][1])
                    for p in grid.points if (a, p) in grid.loose and (b, p) in grid.loose]
            gaps = [g for g in gaps if g[2] != INF]
            if gaps:
                more = [p for p, g, e in gaps if g > max(0.5, e)]
                less = [p for p, g, e in gaps if g < -max(0.5, e)]
                same = [p for p, g, e in gaps if abs(g) <= max(0.5, e)]
                lead, trail, rest = (a, b, less) if len(more) >= len(less) else (b, a, more)
                line = f"{lead} is higher than {trail} in {'every case' if not rest and not same else 'most cases'}"
                if rest or same:
                    line += f" (except {_and(rest + same)})"
                out["overview"].append(line + ".")
                big = max(gaps, key=lambda x: abs(x[1]))
                out["comparisons"].append(f"The widest gap: {big[0]} ({_n(abs(big[1]))}).")
        for s in grid.series:
            here = sorted(((p, grid.loose[(s, p)]) for p in grid.points if (s, p) in grid.loose),
                          key=lambda x: -x[1][0])
            if len(here) >= 2:
                hi, lo = here[0], here[-1]
                top, bottom = _clear_first(here, lambda x: x[1][0], spread), \
                    _clear_first(here[::-1], lambda x: x[1][0], spread)
                if not grid.close(hi[1], lo[1]) and (top or bottom):
                    parts = ([f"highest in {hi[0]} ({grid.show(s, hi[0])})"] if top else []) + \
                            ([f"lowest in {lo[0]} ({grid.show(s, lo[0])})"] if bottom else [])
                    out["overview"].append(f"{s}: {', '.join(parts)}.")
        if not _pct(u):
            totals = [(p, sum(grid.get(s, p)[0] for s in grid.series)) for p in grid.points
                      if all(grid.get(s, p) for s in grid.series)]
            if len(totals) >= 2:
                hi, lo = max(totals, key=lambda t: t[1]), min(totals, key=lambda t: t[1])
                out["comparisons"].append(f"Combined, {hi[0]} has the highest total ({_n(hi[1])}) and {lo[0]} the "
                                          f"lowest ({_n(lo[1])}).")
        out["grouping"].append("by category or by series, comparing them side by side")
        return
    # One set of figures: a ranking.
    items = [(s if len(grid.series) > 1 else p, grid.loose[(s, p)], grid.show(s, p))
             for s in grid.series for p in grid.points if (s, p) in grid.loose]
    unbounded = any(x[1][1] == INF for x in items)  # a slice that may be another's: no largest or smallest
    items = [x for x in items if x[1][1] != INF]
    if len(items) < 2:
        return
    ranked = sorted(items, key=lambda x: -x[1][0])

    def spread(x) -> float:
        return x[1][1]

    top, bottom = _clear_first(ranked, lambda x: x[1][0], spread) and not unbounded, \
        _clear_first(ranked[::-1], lambda x: x[1][0], spread) and not unbounded
    if top and bottom:
        out["overview"].append(f"{ranked[0][0]} is the largest ({ranked[0][2]}) and {ranked[-1][0]} the "
                               f"smallest ({ranked[-1][2]}).")
    elif top:
        out["overview"].append(f"{ranked[0][0]} is the largest ({ranked[0][2]}).")
    elif bottom:
        out["overview"].append(f"{ranked[-1][0]} is the smallest ({ranked[-1][2]}).")
    if block.visual_type == "pie" and len(ranked) >= 3 and ranked[0][1][0] + ranked[1][1][0] > 50 \
            and _clear_first(ranked[1:], lambda x: x[1][0], spread):
        out["overview"].append(f"{ranked[0][0]} and {ranked[1][0]} together make up over half "
                               f"({_v(ranked[0][1][0] + ranked[1][1][0], u)}).")
    out["comparisons"].append("Ranking: " + ", ".join(f"{s} {shown}" for s, _, shown in ranked) + ".")
    if top and bottom and ranked[-1][1][0] > 0 and ranked[0][1][0] / ranked[-1][1][0] >= 2:
        out["comparisons"].append(f"{ranked[0][0]} is about {ranked[0][1][0] / ranked[-1][1][0]:.0f} times "
                                  f"{ranked[-1][0]}.")
    sure = [(s, v) for s, v, shown in ranked if not shown.startswith("about ")]  # equal only by sure figures
    equal = [f"{s1} and {s2} are equal at {_v(a[0], u)}." for i, (s1, a) in enumerate(sure)
             for s2, b in sure[i + 1:] if grid.equal(a, b)]
    out["comparisons"] += equal[:2]
    out["grouping"].append("the largest items first, then the smaller ones")


def _map_features(block: SheetBlock, out: dict) -> None:
    added, removed, changed, kept = [], [], [], []
    for f in block.map_features:
        if not f.states:
            continue
        first, last = f.states[0].status, f.states[-1].status
        detail = next((s.detail for s in reversed(f.states) if s.detail), "")
        if first == "absent" and last != "absent":
            added.append((f.name, detail or f.location))
        elif first != "absent" and last == "absent":
            removed.append((f.name, detail or f.location))
        elif any(s.status == "changed" for s in f.states[1:]):
            changed.append((f.name, detail or f.location))
        else:
            kept.append(f.name)
    times = [s.time for s in block.map_features[0].states] if block.map_features else block.time_points
    span = f" between {times[0]} and {times[-1]}" if len(times) >= 2 else ""
    if block.overall:
        out["overview"].append(f"Overall{span}: {block.overall.rstrip('.')}.")
    counts = [f"{len(added)} added" if added else "", f"{len(removed)} removed" if removed else "",
              f"{len(changed)} changed or moved" if changed else ""]
    if any(counts):
        out["overview"].append("Changes: " + ", ".join(c for c in counts if c) + ".")
    for label, rows in (("Added", added), ("Removed", removed), ("Changed or moved", changed)):
        for name, detail in rows:
            out["comparisons"].append(f"{label}: {name}" + (f" ({detail})" if detail else "") + ".")
    if kept:
        out["comparisons"].append(f"Unchanged: {_and(kept[:6])}.")
    out["grouping"].append("one paragraph per map (the earlier layout, then the changes)")


def _process_features(block: SheetBlock, out: dict) -> None:
    proc = block.process
    if proc is None or not proc.steps:
        return
    n = len(proc.steps)
    stages = [s.name for s in proc.stages]
    # An unnumbered diagram's steps can fairly be counted 7 or 10 (Volume 2 p.72).
    count = f"{n} steps" if proc.numbered else f"about {n} steps"
    out["overview"].append(
        f"A {proc.kind}, {proc.natural_or_manmade} process of {count}"
        + (f" in {len(stages)} main stages: {' → '.join(stages)}" if stages else "")
        + f", from {proc.steps[0].rstrip('.')} to {proc.steps[-1].rstrip('.')}.")
    for s in proc.stages:
        if s.steps:
            first, last = min(s.steps), max(s.steps)
            span = f"step {first}" if first == last else f"steps {first}-{last}"
            out["comparisons"].append(f"{s.name}: {span}.")
    out["grouping"].append("by stage (the first stages in one paragraph, the rest in the next)")


def key_features(sheet: VisualSheet) -> KeyFeatures:
    out = {"overview": [], "comparisons": [], "grouping": []}
    many = len(sheet.blocks) > 1
    for block in sheet.blocks:
        part = {"overview": [], "comparisons": [], "grouping": []}
        if block.visual_type == "map":
            _map_features(block, part)
        elif block.visual_type == "process":
            _process_features(block, part)
        else:
            grid = _Grid(block)
            if grid.loose:
                (_time_features if _time_like(block, grid) else _static_features)(block, grid, part)
        if not part["overview"] and block.overall:
            part["overview"].append(block.overall.rstrip(".") + ".")
        name = f"{block.title or block.visual_type} - " if many else ""
        out["overview"] += [name + x for x in part["overview"]]
        out["comparisons"] += [name + x for x in part["comparisons"]]
        out["grouping"] += [name + x for x in part["grouping"]] if many else part["grouping"]
    return KeyFeatures(
        expected_overview=out["overview"][:8],
        key_comparisons=list(dict.fromkeys(out["comparisons"]))[:MAX_COMPARISONS * max(1, len(sheet.blocks))],
        suggested_grouping="; ".join(dict.fromkeys(out["grouping"])),
    )
