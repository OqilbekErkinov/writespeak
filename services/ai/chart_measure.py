"""Task 1 charts measured from the picture (2026-10-29, the academic lead:
"measure it as exactly as it can be measured").

The two AI reads (services/ai/visual_reading.py) say what a chart shows -
its series, its points and about where each figure sits - but they judge
heights by eye: #61's Washington was read 120 and 240 where the line stands
at 125 and 245, its 1940 points one gridline apart, and one read took the
0-255 axis for 0-225. Here code measures, like a ruler:

  1. local OCR (RapidOCR, in the image already for the Practice importer)
     finds every printed label and where it is; the value axis is the
     column (or row) of numbers whose positions fit one straight line - a
     misread label ("90" read as "06") falls out of the fit. A picture
     taken a little crooked is turned straight by its labels first;
  2. the time points or categories are found by their printed labels, the
     series by the colour of their legend swatches;
  3. a line chart is a chain of straight segments: each series is traced
     from point to point along the drawn segments (the reads' figures only
     say where to look), and the two segments meeting at a point give its
     height; a bar is followed up from its base to its top;
  4. the height becomes a figure through the axis.

Nothing here calls OpenAI. A figure is returned only when it was clear -
one line or bar, the series told apart from the others - and read_visual
decides how it combines with the reads (visual_reading.apply_measured).
"""
from __future__ import annotations

import io
import logging
import math
import re
import threading
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from itertools import combinations

import numpy as np
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

OCR_SIDE = 1600  # a smaller picture is enlarged first: small axis labels read badly
INK = 32  # a pixel this far (largest channel difference) from the background is drawn
GRIDLINE_SHARE = 0.55  # a row drawn across this share of the plot, at a label, is a gridline
MIN_LABELS = 3  # printed numbers that must fit the value axis
COLOUR_GAP = 60  # series swatches at least this far apart (RGB) are told apart by colour
MIN_TILT = 0.15  # degrees: a picture turned less than this is left as it is
MAX_TILT = 6.0
TRACE_MARGIN = 2.0  # how much better (log score) the traced line must be than any other through a point
HIDDEN_COST = 2.0  # a point where the line is not seen: as unlikely as a run 1.2 steps from the reads' figure
NEUTRAL_LINK = 3.0 * math.log(0.3)  # a link to or from a hidden point: a third of a segment drawn

_ocr = None
_ocr_lock = threading.Lock()


def _get_ocr():
    global _ocr
    if _ocr is None:
        from rapidocr import RapidOCR

        logging.getLogger("RapidOCR").setLevel(logging.ERROR)
        _ocr = RapidOCR()
    return _ocr


# --- The printed labels -------------------------------------------------------------------------------

_NUMBER = re.compile(r"[$£€¥]?\s*(-?\d{1,3}(?:,\d{3})+|-?\d+(?:\.\d+)?)\s*(?:%|[kKmM]|bn)?")


def number(text: str) -> float | None:
    """A printed tick label as a number ("$40", "1,000", "25%", "2.5"); None for words."""
    m = _NUMBER.fullmatch(text.strip())
    return float(m.group(1).replace(",", "")) if m else None


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


@dataclass
class Token:
    """One line of text the OCR found, in picture pixels."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    value: float | None = None

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def h(self) -> float:
        return self.y1 - self.y0


def read_tokens(img: np.ndarray) -> list[Token]:
    with _ocr_lock:
        result = _get_ocr()(np.ascontiguousarray(img[..., ::-1]))  # RapidOCR takes OpenCV's BGR order
    tokens = []
    for box, text, score in zip(result.boxes if result.txts else [], result.txts or [], result.scores or []):
        if float(score) < 0.5 or not text.strip():
            continue
        box = np.asarray(box, dtype=float)
        tokens.append(Token(text.strip(), box[:, 0].min(), box[:, 1].min(), box[:, 0].max(), box[:, 1].max(),
                            number(text)))
    return tokens


# --- The value axis --------------------------------------------------------------------------------------


@dataclass
class Axis:
    """A value axis: value = a * y + c (values growing upwards)."""

    a: float
    c: float
    labels: list[Token]
    step: float
    resid: float

    def value(self, y: float) -> float:
        return self.a * y + self.c

    def pos(self, value: float) -> float:
        return (value - self.c) / self.a

    @property
    def step_px(self) -> float:
        return abs(self.step / self.a)

    @property
    def low(self) -> float:
        return min(t.value for t in self.labels)

    @property
    def high(self) -> float:
        return max(t.value for t in self.labels)

    @property
    def x0(self) -> float:
        return min(t.x0 for t in self.labels)

    @property
    def x1(self) -> float:
        return max(t.x1 for t in self.labels)


def _step(values: list[float]) -> float | None:
    """The tick step of sorted distinct label values, when every gap is a
    whole number of steps (a label the OCR missed leaves a double gap)."""
    gaps = np.diff(values)
    if len(gaps) == 0 or gaps.min() <= 0:
        return None
    step = float(gaps.min())
    ratios = gaps / step
    return step if np.all(np.abs(ratios - np.round(ratios)) < 0.05) else None


def _fit(tokens: list[Token]) -> Axis | None:
    """The straight line (value against y) through the most labels, pairs tried
    in turn, refitted on them; None without MIN_LABELS evenly stepped labels."""
    height = float(np.median([t.h for t in tokens]))
    tol = max(2.0, 0.3 * height)
    best: list[Token] = []
    for ti, tj in combinations(tokens, 2):
        dp, dv = tj.cy - ti.cy, tj.value - ti.value
        if abs(dp) < 3 or dv == 0 or dv / dp >= 0:  # values grow upwards
            continue
        a = dv / dp
        c = ti.value - a * ti.cy
        inliers = [t for t in tokens if abs((t.value - c) / a - t.cy) <= tol]
        if len({t.value for t in inliers}) == len(inliers) and len(inliers) > len(best):
            best = inliers
    if len(best) < MIN_LABELS:
        return None
    best.sort(key=lambda t: t.cy)
    a, c = np.polyfit([t.cy for t in best], [t.value for t in best], 1)
    step = _step(sorted(t.value for t in best))
    if step is None:
        return None
    resid = max(abs((t.value - c) / a - t.cy) for t in best)
    return Axis(float(a), float(c), best, step, float(resid))


def _is_years(axis: Axis) -> bool:
    return all(1800 <= t.value <= 2100 and t.value == int(t.value) for t in axis.labels)


def find_axes(tokens: list[Token]) -> list[Axis]:
    """Every vertical value axis: numbers in one column (right edges, left
    edges or centres in line) whose heights fit a straight line. Two charts
    one above the other share a column: once an axis is taken, the rest of
    the column is searched again."""
    nums = [t for t in tokens if t.value is not None]
    groups: list[list[Token]] = []
    for key in ((lambda t: t.x1), (lambda t: t.x0), (lambda t: t.cx)):
        for t in nums:
            tol = max(4.0, 0.8 * t.h)
            group = [u for u in nums if abs(key(u) - key(t)) <= tol]
            if len(group) >= MIN_LABELS and not any(set(map(id, group)) <= set(map(id, g)) for g in groups):
                groups.append(group)
    axes: list[Axis] = []
    for group in sorted(groups, key=len, reverse=True):
        rest = list(group)
        while len(rest) >= MIN_LABELS:
            axis = _fit(rest)
            if axis is None:
                break
            taken = set(map(id, axis.labels))
            rest = [t for t in rest if id(t) not in taken]
            if _is_years(axis):
                continue
            if any(len(taken & set(map(id, b.labels))) * 2 >= len(taken) for b in axes):
                continue  # the same axis, found from another edge
            axes.append(axis)
    return axes


# --- The picture -----------------------------------------------------------------------------------------


def load(data: bytes, rotation: int = 0) -> np.ndarray:
    """The picture upright (EXIF, then `rotation` degrees clockwise), enlarged
    to OCR_SIDE when smaller, as an RGB array."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    if rotation % 360:
        img = img.rotate(-rotation, expand=True, fillcolor=(255, 255, 255))
    if max(img.size) < OCR_SIDE:
        scale = OCR_SIDE / max(img.size)
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    return np.asarray(img)


@dataclass
class Picture:
    img: np.ndarray
    tokens: list[Token]
    tilt: float = 0.0  # degrees the picture was turned back


def _tilt(tokens: list[Token]) -> float | None:
    """How many degrees the picture is turned clockwise, from its printed
    labels: a value axis's right-aligned column and the rows of labels."""
    estimates: list[tuple[float, float]] = []  # (degrees, weight)
    for axis in find_axes(tokens):
        if len(axis.labels) >= 4:
            slope = np.polyfit([t.cy for t in axis.labels], [t.x1 for t in axis.labels], 1)[0]
            estimates.append((-math.degrees(math.atan(slope)), len(axis.labels)))
    used: set[int] = set()
    for t in sorted(tokens, key=lambda t: t.cx):
        if id(t) in used:
            continue
        row = [u for u in tokens if abs(u.cy - t.cy) <= 0.6 * t.h and abs(u.h - t.h) <= 0.35 * t.h]
        row = [u for u in row if id(u) not in used]
        if len(row) >= 4 and max(u.cx for u in row) - min(u.cx for u in row) > 20 * t.h:
            used |= set(map(id, row))
            slope = np.polyfit([u.cx for u in row], [u.cy for u in row], 1)[0]
            estimates.append((math.degrees(math.atan(slope)), len(row)))
    if not estimates:
        return None
    degrees = np.array([e[0] for e in estimates])
    weights = np.array([e[1] for e in estimates])
    order = np.argsort(degrees)
    cum = np.cumsum(weights[order])
    return float(degrees[order][np.searchsorted(cum, cum[-1] / 2)])


def _turned(tokens: list[Token], degrees: float, w: int, h: int) -> list[Token]:
    """Token boxes after the picture is turned `degrees` anticlockwise about its centre."""
    phi = math.radians(degrees)
    cos, sin = math.cos(phi), math.sin(phi)
    cx0, cy0 = w / 2, h / 2
    out = []
    for t in tokens:
        xs, ys = [], []
        for x, y in ((t.x0, t.y0), (t.x1, t.y0), (t.x0, t.y1), (t.x1, t.y1)):
            xs.append(cx0 + (x - cx0) * cos + (y - cy0) * sin)
            ys.append(cy0 - (x - cx0) * sin + (y - cy0) * cos)
        # the box of a turned box is a little taller: keep the text's own height
        mid_y, half = (min(ys) + max(ys)) / 2, t.h / 2
        out.append(Token(t.text, min(xs), mid_y - half, max(xs), mid_y + half, t.value))
    return out


def prepare(data: bytes, rotation: int = 0) -> Picture:
    """The picture, turned straight, and its printed labels - the slow part
    (OCR, a second or two), started while the AI reads run."""
    img = load(data, rotation)
    tokens = read_tokens(img)
    tilt = _tilt(tokens)
    if tilt is not None and MIN_TILT <= abs(tilt) <= MAX_TILT:
        h, w, _ = img.shape
        img = np.asarray(Image.fromarray(img).rotate(tilt, resample=Image.BICUBIC, fillcolor=(255, 255, 255)))
        return Picture(img, _turned(tokens, tilt, w, h), tilt)
    return Picture(img, tokens)


def _background(area: np.ndarray) -> np.ndarray:
    """The paper's colour: of the colours covering a tenth of the area or more,
    the lightest - not the median, which a shaded area filling half the plot
    turns blue (v1-p008)."""
    flat = area.reshape(-1, 3).astype(int)
    keys = (flat // 16) @ np.array([256, 16, 1])
    values, counts = np.unique(keys, return_counts=True)
    common = values[counts >= 0.1 * len(keys)]
    if len(common) == 0:
        return np.median(flat, axis=0)
    lightest = max(common, key=lambda k: (k // 256) + (k // 16) % 16 + k % 16)
    return np.median(flat[keys == lightest], axis=0)


def _colour_dist(pixels: np.ndarray, colour: np.ndarray) -> np.ndarray:
    return np.sqrt(((pixels.astype(float) - colour) ** 2).sum(axis=-1))


@dataclass
class Plot:
    """One chart panel: its value axis, the area it draws in and what is drawn there."""

    axis: Axis
    x0: int
    x1: int
    y0: int
    y1: int
    background: np.ndarray
    ink: np.ndarray  # drawn pixels of the area (gridlines, axes, text and legend left out)
    gridrows: set[int] = field(default_factory=set)


def _legend_boxes(tokens: list[Token], labels: list[str]) -> list[tuple[float, float, float, float]]:
    """Where each legend entry is drawn: its word and the swatch before it."""
    boxes = []
    for label in labels:
        for t in _matches(label, tokens):
            boxes.append((t.x0 - 4.0 * t.h, t.y0 - 0.3 * t.h, t.x1 + 0.3 * t.h, t.y1 + 0.3 * t.h))
    return boxes


def _peaks(profile: np.ndarray, near: list[float], offset: int, reach: float) -> dict[int, float]:
    """For each expected row in `near`, the strongest thin peak of `profile`
    within `reach` of it, when it stands clearly above its surroundings."""
    out = {}
    for k, y in enumerate(near):
        lo, hi = int(max(0, y - reach - offset)), int(min(len(profile), y + reach + 1 - offset))
        if hi - lo < 5:
            continue
        window = profile[lo:hi]
        j = int(np.argmax(window))
        around = np.r_[window[max(0, j - 8):max(0, j - 2)], window[j + 3:j + 9]]
        if len(around) < 4:
            continue
        rise = window[j] - float(np.median(around))
        if rise > max(1.5, 4 * float(np.median(np.abs(around - np.median(around))))):
            # the peak's middle, weighted, for a gridline drawn two rows thick
            rows = np.arange(max(0, j - 1), min(len(window), j + 2))
            weights = np.clip(window[rows] - np.median(around), 0, None)
            out[k] = offset + lo + float((rows * weights).sum() / max(weights.sum(), 1e-9))
    return out


def _on_gridlines(img: np.ndarray, axis: Axis, x0: int, x1: int) -> Axis:
    """The axis set on the gridlines (or the tick marks) drawn at its labels:
    a label's printed middle sits a pixel or two off its gridline - #61's
    figures came out 1-2 high. Gridlines too faint to count as drawn still
    show as thin peaks of the rows' mean darkness. The axis as it was when
    fewer than three labels have one."""
    values = list(np.arange(axis.low, axis.high + axis.step / 2, axis.step))
    rows = [axis.pos(v) for v in values]
    step = axis.step_px
    top, bottom = int(max(0, min(rows) - 0.5 * step)), int(min(img.shape[0], max(rows) + 0.5 * step))
    best: dict[int, float] = {}
    for a, b in ((x0 + 0.05 * (x1 - x0), x1 - 0.05 * (x1 - x0)), (axis.x1 + 1, x0 + 0.02 * (x1 - x0))):
        if b - a < 4:
            continue
        strip = img[top:bottom, int(a):int(b)].astype(float).mean(axis=-1)
        profile = np.median(strip, axis=1).max() - strip.mean(axis=1)  # darker rows rise
        found = _peaks(profile, rows, top, 0.3 * step)
        if len(found) > len(best):
            best = found
    if len(best) < 3:
        return axis
    ks = sorted(best)
    a, c = np.polyfit([best[k] for k in ks], [values[k] for k in ks], 1)
    resid = max(abs((values[k] - c) / a - best[k]) for k in ks)
    if resid > max(1.5, 0.05 * step) or abs(a - axis.a) > 0.03 * abs(axis.a):
        return axis  # not the same scale: some peaks are something else
    return Axis(float(a), float(c), axis.labels, axis.step, float(resid))


def _plot(img: np.ndarray, tokens: list[Token], axis: Axis, left: float, right: float,
          legend: list[str]) -> Plot | None:
    """The drawing area of a value axis between `left` and `right` (the side
    of its labels where the drawing is), with what is drawn there: gridlines
    at the labels, the axis lines, printed text and the legend left out."""
    h, w, _ = img.shape
    x0, x1 = int(max(0, left)), int(min(w, right))
    axis = _on_gridlines(img, axis, x0, x1)
    step = axis.step_px
    y0 = int(max(0, axis.pos(axis.high) - 0.7 * step))
    y1 = int(min(h, axis.pos(axis.low) + 0.25 * step))
    if x1 - x0 < 20 or y1 - y0 < 20:
        return None
    area = img[y0:y1, x0:x1].astype(int)
    background = _background(area)
    ink = np.abs(area - background).max(axis=-1) > INK
    label_rows = [axis.pos(v) for v in np.arange(axis.low, axis.high + axis.step / 2, axis.step)]
    gridrows = set()
    share = ink.mean(axis=1)
    far = 4 + int(round(img.shape[1] / OCR_SIDE))
    for r in np.where(share > GRIDLINE_SHARE)[0]:
        # a thin line across the plot at a label - not a filled area (v1-p008)
        thin = all(share[q] < 0.5 * share[r] for q in (r - far, r + far) if 0 <= q < len(share))
        if thin and min(abs(y0 + r - lr) for lr in label_rows) <= max(3.0, 0.08 * step):
            gridrows.add(int(y0 + r))
    # a gridline and its softened edges (blends of it and the background, a
    # few rows either side on a blurred picture) go; a line crossing it keeps
    # its own, deeper colour
    edge = 2 + int(round(img.shape[1] / OCR_SIDE))
    cleared = set()
    for r in sorted(gridrows):
        grid = np.median(area[r - y0][ink[r - y0]], axis=0)
        for rr in range(r - edge, r + edge + 1):
            if 0 <= rr - y0 < ink.shape[0]:
                ink[rr - y0] &= ~_blend_of(area[rr - y0], background, grid, deeper=1.0)
                cleared.add(rr)
    gridrows = cleared  # a line's runs are joined across them
    down = ink.mean(axis=0)
    for c in np.where(down > GRIDLINE_SHARE)[0]:
        if not all(down[q] < 0.5 * down[c] for q in (c - far, c + far) if 0 <= q < len(down)):
            continue  # a filled area, not an axis line
        axis_colour = np.median(area[:, c][ink[:, c]], axis=0)
        for cc in range(c - edge, c + edge + 1):
            if 0 <= cc < ink.shape[1]:
                ink[:, cc] &= ~_blend_of(area[:, cc], background, axis_colour, deeper=1.0)
    # printed words inside the plot go; one or two characters are not taken
    # for words - the OCR reads hollow markers as "e", "o" or "Q" (v2-p060)
    words = [t for t in tokens if len(t.text.strip()) > 2 or (t.value is not None and len(t.text.strip()) == 2)]
    pad_boxes = [(t.x0 - 0.25 * t.h, t.y0 - 0.25 * t.h, t.x1 + 0.25 * t.h, t.y1 + 0.25 * t.h) for t in words]
    for bx0, by0, bx1, by1 in pad_boxes + _legend_boxes(tokens, legend):
        ink[max(0, int(by0) - y0):max(0, int(by1) - y0), max(0, int(bx0) - x0):max(0, int(bx1) - x0)] = False
    return Plot(axis, x0, x1, y0, y1, background, ink, gridrows)


def _blend_of(pixels: np.ndarray, background: np.ndarray, colour: np.ndarray, tol: float = 30.0,
              deeper: float = 2.0) -> np.ndarray:
    """Pixels that are `colour`, the background, or a mix of the two (a thin
    line's softened edge) - or, up to `deeper` times as far from the
    background, a deeper shade of it (a word's median colour is lighter than
    its strokes' core)."""
    d = colour - background
    norm = float(d @ d)
    if norm < 1.0:
        return _colour_dist(pixels, background) < tol
    t = np.clip(((pixels.astype(float) - background) @ d) / norm, 0.0, deeper)
    return _colour_dist(pixels, background + t[..., None] * d) < tol


def _runs(column: np.ndarray, offset: int, bridge: set[int]) -> list[tuple[float, float]]:
    """(top, bottom) of each drawn run in a column, as picture rows; runs split
    only by a removed gridline row are joined."""
    rows = np.where(column)[0]
    if len(rows) == 0:
        return []
    runs, start, prev = [], rows[0], rows[0]
    for r in rows[1:]:
        if r - prev > 1 and not all((offset + k) in bridge for k in range(prev + 1, r)):
            runs.append((offset + start, offset + prev))
            start = r
        prev = r
    runs.append((offset + start, offset + prev))
    return runs


# --- Points, categories and series -------------------------------------------------------------------


def _matches(label: str, tokens: list[Token]) -> list[Token]:
    """The tokens reading `label` - written across: an axis title standing on
    end may repeat a legend label (v1-p008), and its box is no word's."""
    want = _norm(label)
    if not want:
        return []
    return [t for t in tokens if t.x1 - t.x0 > t.h and (_norm(t.text) == want or (
        len(want) > 3 and SequenceMatcher(None, want, _norm(t.text)).ratio() >= 0.85))]


def _find_label(label: str, tokens: list[Token]) -> float | None:
    """The x of a printed category or time label among `tokens`: the token
    itself, a word inside a longer token (placed by its characters), or a
    close OCR misreading."""
    want = _norm(label)
    if not want:
        return None
    for t in tokens:
        if _norm(t.text) == want:
            return t.cx
    for t in tokens:
        low = t.text.lower()
        i = low.find(label.lower())
        if i >= 0 and len(label) < len(t.text):
            mid = (i + len(label) / 2) / len(t.text)
            return t.x0 + mid * (t.x1 - t.x0)
    scored = [(SequenceMatcher(None, want, _norm(t.text)).ratio(), t) for t in tokens]
    scored = [s for s in scored if s[0] >= 0.8]
    if scored:
        return max(scored, key=lambda s: s[0])[1].cx
    return None


def point_positions(points: list[str], tokens: list[Token], ordered: bool) -> dict[str, float] | None:
    """Each point's x from the labels below the plot; points without a label
    of their own are placed between labelled ones (by year, or by place when
    the points are evenly spaced categories). None when fewer than two are
    found, or (`ordered`: time points) they run out of order."""
    found = {p: x for p in points if (x := _find_label(p, tokens)) is not None}
    if len(found) < 2:
        return None
    order = [found[p] for p in points if p in found]
    if ordered and any(b <= a for a, b in zip(order, order[1:])):
        return None
    if len(found) == len(points) or not ordered:
        return found
    years = [number(p) for p in points]
    if all(y is not None for y in years):
        xs = [years[i] for i, p in enumerate(points) if p in found]
        a, c = np.polyfit(xs, [found[p] for p in points if p in found], 1)
        return {p: float(a * y + c) for p, y in zip(points, years)}
    idx = [i for i, p in enumerate(points) if p in found]
    a, c = np.polyfit(idx, [found[points[i]] for i in idx], 1)
    if max(abs(a * i + c - found[points[i]]) for i in idx) > 0.15 * abs(a):
        return None  # the labelled ones are not evenly spaced: no guessing the others
    return {p: float(a * i + c) for i, p in enumerate(points)}


def swatch_colours(img: np.ndarray, labels: list[str], tokens: list[Token]) -> dict[str, np.ndarray]:
    """Each legend label's swatch colour: the core colour drawn in the clear
    space just left of its word - or, when the OCR took the swatch for a
    character of the word ("�Washington") and nothing stands clear of it,
    what is drawn at the word's start in another colour than its letters."""
    h, w, _ = img.shape
    out = {}
    for label in labels:
        for t in _matches(label, tokens):
            y0, y1 = int(max(0, t.cy - 0.45 * t.h)), int(min(h, t.cy + 0.45 * t.h))
            before = [u.x1 + 1 for u in tokens if u is not t and abs(u.cy - t.cy) < 0.6 * t.h and u.x1 <= t.x0 + 2]
            left = int(max([0.0, t.x0 - 3.5 * t.h] + before))
            background = np.median(img[max(0, int(t.y0) - 2):int(t.y1) + 2, int(t.x0):int(t.x1)].reshape(-1, 3),
                                   axis=0)
            clear = img[y0:y1, left:int(max(left, t.x0 - 0.15 * t.h))].reshape(-1, 3).astype(int)
            diff = np.abs(clear - background).max(axis=1) if clear.size else np.zeros(0)
            if (diff > INK).sum() >= 6:
                drawn, strength = clear[diff > INK], diff[diff > INK]
                out[label] = np.median(drawn[strength >= np.percentile(strength, 70)], axis=0)  # not soft edges
                break
            word = img[y0:y1, int(t.x1 - 0.6 * (t.x1 - t.x0)):int(t.x1)].reshape(-1, 3).astype(int)
            letters = word[np.abs(word - background).max(axis=1) > 2 * INK]
            start = img[y0:y1, int(t.x0):int(min(t.x1, t.x0 + 2.5 * t.h))].reshape(-1, 3).astype(int)
            if len(letters) < 6 or start.size == 0:
                continue
            ink = np.median(letters, axis=0)
            diff = np.abs(start - background).max(axis=1)
            other = start[(diff > INK) & ~_blend_of(start, background, ink, 40.0)]  # not the letters or their edges
            if len(other) >= 6:
                strength = np.abs(other - background).max(axis=1)
                out[label] = np.median(other[strength >= np.percentile(strength, 70)], axis=0)
                break
    return out


def _distinct_from_others(colours: dict[str, np.ndarray], label: str, labels: list[str]) -> bool:
    if label not in colours or any(o not in colours for o in labels):  # an unknown swatch might share the colour
        return False
    return all(float(np.linalg.norm(colours[label] - colours[o])) >= COLOUR_GAP for o in labels if o != label)


# --- Lines ---------------------------------------------------------------------------------------------


def _line_mask(img: np.ndarray, plot: Plot, colour: np.ndarray, others: list[np.ndarray]) -> np.ndarray:
    """The drawn pixels nearer this series' colour than any other's."""
    area = img[plot.y0:plot.y1, plot.x0:plot.x1]
    mine = _colour_dist(area, colour)
    nearest_other = np.min([_colour_dist(area, o) for o in others], axis=0) if others else np.full(mine.shape, 1e9)
    return plot.ink & (mine < np.minimum(nearest_other, 110))


def _candidates(mask: np.ndarray, plot: Plot, x: float, img: np.ndarray,
                join: float = 0.0) -> list[tuple[float, np.ndarray]]:
    """The drawn runs at x: (middle row, the run's strongest colour). Runs
    less than `join` apart are one: a hollow marker's rim crosses the column
    twice (v2-p060) - only for one series' own colour, where nothing else is
    drawn."""
    col = int(round(x)) - plot.x0
    if not 0 <= col < mask.shape[1]:
        return []
    c0, c1 = max(0, col - 3), col + 4  # a dotted line may have a gap at x itself
    window = mask[:, c0:c1].any(axis=1)
    runs: list[tuple[float, float]] = []
    for t, b in _runs(window, plot.y0, plot.gridrows):
        if runs and t - runs[-1][1] <= join:
            runs[-1] = (runs[-1][0], b)
        else:
            runs.append((t, b))
    out = []
    for t, b in runs:
        patch = img[int(t):int(b) + 1, plot.x0 + c0:plot.x0 + c1].reshape(-1, 3).astype(int)
        strongest = patch[np.abs(patch - plot.background).max(axis=1).argmax()]
        out.append(((t + b) / 2, strongest))
    return out


def _coverage(mask: np.ndarray, plot: Plot, xa: float, ya: float, xb: float, yb: float, thick: float) -> float:
    """The share of the straight segment between two points that is drawn
    (its ends, where a marker or the other segments sit, left out)."""
    span = xb - xa
    if span < 6:
        return 0.0
    margin = min(0.15 * span, 12.0)
    xs = np.arange(xa + margin, xb - margin, 1.0)
    if len(xs) < 3:
        return 0.0
    hits = 0
    slope = (yb - ya) / span
    tol = thick + abs(slope) * 0.5 + 1.0
    for x in xs:
        y = ya + slope * (x - xa)
        c, r0, r1 = int(round(x)) - plot.x0, int(y - tol) - plot.y0, int(y + tol) + 1 - plot.y0
        if 0 <= c < mask.shape[1] and mask[max(0, r0):max(0, r1), c].any():
            hits += 1
    return hits / len(xs)


def _segment_fit(mask: np.ndarray, plot: Plot, xa: float, ya: float, xb: float, yb: float,
                 thick: float) -> tuple[float, float] | None:
    """The straight segment between two traced points, refitted on its drawn
    pixels (their ends left out): (slope, intercept) of y against x."""
    span = xb - xa
    margin = min(0.2 * span, 14.0)
    slope = (yb - ya) / span
    tol = thick + abs(slope) * 0.5 + 1.5
    pts = []
    for x in np.arange(xa + margin, xb - margin, 1.0):
        y = ya + slope * (x - xa)
        c = int(round(x)) - plot.x0
        if not 0 <= c < mask.shape[1]:
            continue
        near = [(t + b) / 2 for t, b in _runs(mask[:, c], plot.y0, plot.gridrows) if abs((t + b) / 2 - y) <= tol]
        if near:
            pts.append((int(round(x)), min(near, key=lambda m: abs(m - y))))
    if len(pts) < 4:
        return None
    xs, ys = zip(*pts)
    return tuple(np.polyfit(xs, ys, 1))


def trace(mask: np.ndarray, plot: Plot, xs: list[float], guesses: list[float | None], thick: float,
          img: np.ndarray, own: np.ndarray | None = None, others: list[np.ndarray] = (),
          join: float = 0.0, rivals: list[list[float]] | None = None) -> list[float | None]:
    """One series' height at each point: the chain of drawn straight segments
    through the points (dynamic programming over the runs at each point -
    a run near the reads' figure, in a colour nearer the series' swatch
    (`own`) than the other series' (`others`), and a drawn segment to the
    next point score well), then each point refitted from the segments
    meeting there. A point whose best chain is not clearly better than one
    through another run there gets None."""
    step = plot.axis.step_px
    # how far the reads' figure may be off: half a step or so - on a coarse
    # scale ($0k, $250k, $500k) a share of the whole axis instead
    span = abs(plot.axis.pos(plot.axis.high) - plot.axis.pos(plot.axis.low))
    sigma, window, same = min(0.6 * step, 0.06 * span), min(1.6 * step, 0.18 * span), min(0.1 * step, 0.02 * span)
    n = len(xs)
    # each point's runs near the reads' figure, and None: the line is hidden
    # there (under the legend, a gap in a dashed line) - a run is taken only
    # when the chain through it beats that
    cands: list[list[float | None]] = []
    cost: list[list[float]] = []
    for i, (x, g) in enumerate(zip(xs, guesses)):
        ys, costs = [], []
        for y, colour in _candidates(mask, plot, x, img, join):
            if g is not None and abs(y - g) > window:
                continue
            if g is not None and rivals and any(abs(y - r) < abs(y - g) for r in rivals[i]):
                continue  # nearer another series' figure: that series' line (v1-p052: dotted England on grey Germany)
            c = 0.0 if g is None else 0.5 * ((y - g) / sigma) ** 2
            if own is not None and len(others):
                mine = float(np.linalg.norm(colour - own))
                nearest = min(float(np.linalg.norm(colour - o)) for o in others)
                c += max(0.0, mine - nearest - 20.0) / 15.0  # the run looks like another series' line
            ys.append(y)
            costs.append(c)
        cands.append([*ys, None])
        cost.append([*costs, HIDDEN_COST])
    if not any(len(c) > 1 for c in cands):
        return [None] * n

    def link(i: int, ya: float | None, yb: float | None) -> float:
        if ya is None or yb is None:
            return NEUTRAL_LINK
        return 3.0 * math.log(_coverage(mask, plot, xs[i], ya, xs[i + 1], yb, thick) + 0.05)

    links = [[[link(i, ya, yb) for yb in cands[i + 1]] for ya in cands[i]] for i in range(n - 1)]
    fwd = [[-c for c in cost[0]]]
    for i in range(n - 1):
        fwd.append([-cost[i + 1][k] + max(fwd[i][j] + links[i][j][k] for j in range(len(cands[i])))
                    for k in range(len(cands[i + 1]))])
    bwd = [[0.0] * len(cands[-1])]
    for i in range(n - 2, -1, -1):
        nxt = bwd[0]
        bwd.insert(0, [max(nxt[k] - cost[i + 1][k] + links[i][j][k] for k in range(len(cands[i + 1])))
                       for j in range(len(cands[i]))])
    best: dict[int, float] = {}
    for i in range(n):
        totals = [f + b for f, b in zip(fwd[i], bwd[i])]
        order = list(np.argsort(totals)[::-1])
        top = cands[i][order[0]]
        if top is None:
            continue
        rivals = [k for k in order[1:] if cands[i][k] is None or abs(cands[i][k] - top) > max(3.0, same)]
        if rivals and totals[order[0]] - totals[rivals[0]] < TRACE_MARGIN:
            continue  # another run here, or none at all, makes almost as good a line
        best[i] = top
    out: list[float | None] = [None] * n
    for i, y_mid in best.items():
        fits = []
        for j in (i - 1, i + 1):
            if j not in best:
                continue
            a, b = (j, i) if j < i else (i, j)
            if _coverage(mask, plot, xs[a], best[a], xs[b], best[b], thick) < 0.35:
                continue
            fit = _segment_fit(mask, plot, xs[a], best[a], xs[b], best[b], thick)
            if fit is not None:
                x = xs[i]
                if i in (0, n - 1):  # the line's own end, when it stops short of its label's middle
                    x = _line_end(mask, plot, fit, xs[i], 1 if i == 0 else -1, abs(xs[j] - xs[i]), thick)
                fits.append(fit[0] * x + fit[1])
        if len(fits) == 2 and abs(fits[0] - fits[1]) > 0.2 * step:
            fits = []  # the segments don't meet here: keep the run's middle
        out[i] = float(np.mean(fits)) if fits else y_mid
    return out


def _line_end(mask: np.ndarray, plot: Plot, fit: tuple[float, float], x: float, inward: int, span: float,
              thick: float) -> float:
    """Where an end segment's last point is, near its label's middle x: the
    segment's outermost drawn column, less the rounded cap a line is drawn
    with (half its thickness along it). A label is placed by its OCR box, a
    pixel or two off - on a steep segment that alone made #61's 2000 points
    1-2 high."""
    slope, icpt = fit
    tol = thick + abs(slope) * 0.5 + 1.5

    def drawn(c: int) -> bool:
        col = c - plot.x0
        return 0 <= col < mask.shape[1] and any(abs((t + b) / 2 - (slope * c + icpt)) <= tol
                                                for t, b in _runs(mask[:, col], plot.y0, plot.gridrows))

    reach = int(0.15 * span)
    c = int(round(x))
    if drawn(c):
        while drawn(c - inward) and abs(c - inward - x) <= reach:
            c -= inward
    else:
        for k in range(1, reach + 1):
            if drawn(c + inward * k):
                c += inward * k
                break
        else:
            return x
    return c + inward * thick / math.sqrt(1 + slope * slope)


def _area_edge(mask: np.ndarray, plot: Plot) -> tuple[np.ndarray, bool]:
    """A filled area's top edge (v1-p008: people leaving Ireland drawn as a
    shaded area) - its figures are where the fill ends, and the edge can be
    traced like a line - and True; a line's own mask as it is, and False."""
    span = abs(plot.axis.pos(plot.axis.high) - plot.axis.pos(plot.axis.low))
    cols = [c for c in range(0, mask.shape[1], 4) if mask[:, c].any()]
    if not cols:
        return mask, False
    tall = 0
    for c in cols:
        rows = np.where(mask[:, c])[0]
        breaks = np.where(np.diff(rows) > 1)[0]
        lengths = np.diff(np.r_[-1, breaks, len(rows) - 1])
        tall += lengths.max() > 0.25 * span
    if tall < 0.5 * len(cols):
        return mask, False
    top = mask & ~np.vstack([np.zeros((1, mask.shape[1]), bool), mask[:-1]])
    return top | np.vstack([top[1:], np.zeros((1, mask.shape[1]), bool)]), True  # two rows thick


def _thickness(mask: np.ndarray) -> float:
    """A typical drawn line's half thickness, from the runs of the mask."""
    lengths = []
    for c in range(0, mask.shape[1], max(1, mask.shape[1] // 60)):
        rows = np.where(mask[:, c])[0]
        if len(rows):
            breaks = np.where(np.diff(rows) > 1)[0]
            starts = np.r_[rows[0], rows[breaks + 1]]
            ends = np.r_[rows[breaks], rows[-1]]
            lengths += list(ends - starts + 1)
    return max(1.5, float(np.median(lengths)) / 2) if lengths else 2.0


# --- Bars ----------------------------------------------------------------------------------------------


@dataclass
class Bar:
    x0: int
    x1: int
    colour: np.ndarray
    top: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2


def find_bars(img: np.ndarray, plot: Plot, min_width: float) -> list[Bar]:
    """Every bar standing on the base (0 when the axis shows it, else its
    lowest label): runs of one colour along a row just above the base, each
    followed up while its colour lasts."""
    axis = plot.axis
    base = axis.pos(0.0) if axis.low <= 0 <= axis.high else axis.pos(axis.low)
    scan = int(round(base - max(2.0, 0.12 * axis.step_px)))
    if not plot.y0 + 2 <= scan < plot.y1:
        return []
    row = img[scan, plot.x0:plot.x1].astype(int)
    drawn = np.abs(row - plot.background).max(axis=1) > INK
    runs, start = [], None
    for i in range(len(row) + 1):
        same = i < len(row) and drawn[i] and (start is None or _colour_dist(row[i], row[start:i].mean(axis=0)) < 45)
        if same and start is None:
            start = i
        elif not same and start is not None:
            if i - start >= min_width:
                runs.append((start, i - 1))
            start = i if i < len(row) and drawn[i] else None
    out = []
    for s, e in runs:
        inner = slice(plot.x0 + s + (e - s) // 5, plot.x0 + e - (e - s) // 5 + 1)
        colour = np.median(img[scan - 2:scan + 1, inner].reshape(-1, 3), axis=0)
        top, misses = scan, 0
        for y in range(scan - 1, plot.y0 - 1, -1):
            share = (_colour_dist(img[y, inner], colour) < 50).mean()
            if share >= 0.5:
                top, misses = y, 0
            else:
                misses += 1
                if misses >= 2:
                    break
        # an outline drawn round the bar: its top edge counts
        for y in range(top - 1, max(plot.y0, top - 4) - 1, -1):
            if (np.abs(img[y, inner].astype(int) - plot.background).max(axis=1) > 2 * INK).mean() >= 0.6:
                top = y
            else:
                break
        out.append(Bar(plot.x0 + s, plot.x0 + e, colour, float(top)))
    return out


# --- One block -------------------------------------------------------------------------------------------


@dataclass
class Measured:
    value: float
    how: str
    x: float = 0.0  # where it was measured, in the (straightened) picture's pixels
    y: float = 0.0


def _values(block: dict) -> list[dict]:
    return list(block.get("values") or block.get("data") or [])


def _below(tokens: list[Token], plot: Plot) -> list[Token]:
    """The labels under a plot's base, within its width."""
    base = plot.axis.pos(plot.axis.low)
    height = float(np.median([t.h for t in plot.axis.labels]))
    return [t for t in tokens if base - 0.3 * plot.axis.step_px < t.cy < base + 6 * height
            and plot.x0 - height < t.cx < plot.x1 + height]


def measure_line(img: np.ndarray, tokens: list[Token], plot: Plot, block: dict,
                 colours: dict[str, np.ndarray]) -> dict[tuple[str, str], Measured]:
    data = _values(block)
    series = [e["label"] for e in block.get("legend") or []] or list(dict.fromkeys(v["series"] for v in data))
    points = list(block.get("time_points") or []) or list(dict.fromkeys(v["point"] for v in data))
    xs = point_positions(points, _below(tokens, plot), ordered=True)
    if xs is None:
        return {}
    points = [p for p in points if p in xs]
    thick = _thickness(plot.ink)
    out = {}
    for s in series:
        mine = {v["point"]: v for v in data if v["series"] == s}
        if not any(not v.get("printed") and v.get("value") is not None for v in mine.values()):
            continue
        by_colour = _distinct_from_others(colours, s, series)
        mask = _line_mask(img, plot, colours[s], [colours[o] for o in series if o != s]) if by_colour else plot.ink
        area = False
        if by_colour:
            mask, area = _area_edge(mask, plot)
        guesses = [plot.axis.pos(float(mine[p]["value"])) if p in mine and mine[p].get("value") is not None else None
                   for p in points]
        known = [colours[o] for o in series if o != s and o in colours]
        # without its own colour a run is told by the reads' figures: one nearer another series' is not this one
        rivals = None if by_colour else [
            [plot.axis.pos(float(v["value"])) for v in data
             if v["series"] != s and v["point"] == p and v.get("value") is not None] for p in points]
        heights = trace(mask, plot, [xs[p] for p in points], guesses, _thickness(mask) if by_colour else thick,
                        img, colours.get(s), known,
                        join=min(0.35 * plot.axis.step_px, 24.0 * img.shape[1] / OCR_SIDE) if by_colour and not area
                        else 0.0, rivals=rivals)
        for p, y in zip(points, heights):
            if y is not None and p in mine and not mine[p].get("printed"):
                out[(s, p)] = Measured(plot.axis.value(y), "line, by colour" if by_colour else "line, traced",
                                       xs[p], y)
    return _no_shared_runs(out, data, plot)


def _no_shared_runs(out: dict[tuple[str, str], Measured], data: list[dict],
                    plot: Plot) -> dict[tuple[str, str], Measured]:
    """Two series measured on the same run at a point, though the reads put
    them apart, can't both be right: neither is kept."""
    read = {(v["series"], v["point"]): v.get("value") for v in data}
    drop = set()
    for (a, p), (b, q) in combinations(list(out), 2):
        if p != q or abs(out[(a, p)].y - out[(b, q)].y) > 2.0:
            continue
        ra, rb = read.get((a, p)), read.get((b, q))
        if ra is not None and rb is not None and abs(ra - rb) > plot.axis.step / 2:
            drop |= {(a, p), (b, q)}
    return {k: m for k, m in out.items() if k not in drop}


def measure_bars(img: np.ndarray, tokens: list[Token], plot: Plot, block: dict,
                 colours: dict[str, np.ndarray]) -> dict[tuple[str, str], Measured]:
    data = _values(block)
    legend = [e["label"] for e in block.get("legend") or []]
    height = float(np.median([t.h for t in plot.axis.labels]))
    bars = find_bars(img, plot, max(4.0, 0.4 * height))
    if not bars:
        return {}
    if legend:
        groups, series = list(dict.fromkeys(v["point"] for v in data)), legend
    else:  # one bar per category: the category is the series, the point ''
        groups, series = list(dict.fromkeys(v["series"] for v in data)), [""]
    xs = point_positions(groups, _below(tokens, plot), ordered=False)
    if xs is None:
        return {}
    centres = sorted(xs.values())
    spacing = min((b - a for a, b in zip(centres, centres[1:])), default=1e9)
    by_colour = legend and all(_distinct_from_others(colours, s, series) for s in series)
    printed = {(v["series"], v["point"]) for v in data if v.get("printed")}
    out = {}
    for g, gx in xs.items():
        mine = sorted((b for b in bars if abs(b.cx - gx) < 0.5 * spacing), key=lambda b: b.cx)
        if not mine:
            continue
        if not legend:
            if len(mine) == 1 and (g, "") not in printed:
                out[(g, "")] = Measured(plot.axis.value(mine[0].top), "bar", mine[0].cx, mine[0].top)
            continue
        if by_colour:
            for b in mine:
                d = sorted((float(np.linalg.norm(b.colour - colours[s])), s) for s in series)
                (near, s), second = d[0], d[1][0]
                # clearly one series' colour (a swatch's outline makes its colour a little off)
                if near < 0.5 * second and (s, g) not in out and (s, g) not in printed:
                    out[(s, g)] = Measured(plot.axis.value(b.top), "bar, by colour", b.cx, b.top)
        elif len(mine) == len(series):
            for s, b in zip(series, mine):
                if (s, g) not in printed:
                    out[(s, g)] = Measured(plot.axis.value(b.top), "bar, by place", b.cx, b.top)
    return out


# --- The whole picture -----------------------------------------------------------------------------------

MEASURED_KINDS = ("line", "bar")


def measurable(blocks: list[dict]) -> list[int]:
    return [i for i, b in enumerate(blocks) if b.get("visual_type") in MEASURED_KINDS
            and any(not v.get("printed") and v.get("value") is not None for v in _values(b))]


def _rotated(pic: Picture) -> Picture:
    """The picture turned a quarter left: horizontal bars stand up, the value
    labels under them become a column beside them."""
    h, w, _ = pic.img.shape
    turned = [Token(t.text, t.y0, w - t.x1, t.y1, w - t.x0, t.value) for t in pic.tokens]
    return Picture(np.ascontiguousarray(np.rot90(pic.img, 1)), turned, pic.tilt)


def _ink_share(img: np.ndarray, x0: float, x1: float, y0: float, y1: float) -> float:
    h, w, _ = img.shape
    area = img[int(max(0, y0)):int(min(h, y1)), int(max(0, x0)):int(min(w, x1))].astype(int)
    if area.size == 0:
        return 0.0
    background = _background(area)
    return float((np.abs(area - background).max(axis=-1) > INK).mean())


def _point_labels(block: dict) -> list[str]:
    data = _values(block)
    labels = list(block.get("time_points") or []) + [v["point"] for v in data]
    if not block.get("legend"):
        labels += [v["series"] for v in data]
    return [x for x in dict.fromkeys(labels) if x]


def _plots(pic: Picture, axes: list[Axis], blocks: list[dict]) -> list[Plot | None]:
    """Each axis's drawing area, up to the next axis's labels on that side:
    on the side of its labels where the points' or categories' labels stand
    under it (sub86: the axis title beside the labels is darker than the
    thin lines) - or, without them, the side with more drawn."""
    w = pic.img.shape[1]
    out = []
    for axis, block in zip(axes, blocks):
        legend = [e["label"] for e in block.get("legend") or []]
        top, bottom = axis.pos(axis.high), axis.pos(axis.low)
        same_band = [b for b in axes if b is not axis and abs(b.pos(b.high) - top) < 3 * axis.step_px]
        right_end = min((b.x0 for b in same_band if b.x0 > axis.x1), default=w) - 4
        left_end = max((b.x1 for b in same_band if b.x1 < axis.x0), default=0) + 4
        height = float(np.median([t.h for t in axis.labels]))
        under = [t for t in pic.tokens if bottom - 0.3 * axis.step_px < t.cy < bottom + 6 * height
                 and any(_norm(t.text) == _norm(lab) for lab in _point_labels(block))]
        right = sum(axis.x1 < t.cx < right_end for t in under)
        left = sum(left_end < t.cx < axis.x0 for t in under)
        if right == left:
            width = 0.35 * (right_end - left_end)
            right = _ink_share(pic.img, axis.x1 + 2, axis.x1 + 2 + width, top, bottom)
            left = _ink_share(pic.img, axis.x0 - 2 - width, axis.x0 - 2, top, bottom)
        if right >= left:
            out.append(_plot(pic.img, pic.tokens, axis, axis.x1 + 2, right_end, legend))
        else:
            out.append(_plot(pic.img, pic.tokens, axis, left_end, axis.x0 - 2, legend))
    return out


@dataclass
class BlockResult:
    """One block's measured figures and the printed value axis they were read on."""

    figures: dict[tuple[str, str], Measured]
    low: float
    high: float
    step: float


def measure(pic: Picture, blocks: list[dict]) -> dict[int, BlockResult]:
    """{block index: its measured figures, {(series, point): Measured}, with
    its axis} for the line and bar blocks of `blocks` (sheet or read blocks
    as dicts), in the picture's units. One value axis per measured block,
    matched top to bottom, left to right; horizontal bars are measured on
    the picture turned a quarter. A picture whose axes can't be matched that
    way is left to the reads."""
    wanted = measurable(blocks)
    if not wanted:
        return {}
    tries = [pic]
    if any(blocks[i]["visual_type"] == "bar" for i in wanted):
        tries.append(_rotated(pic))
    # the legend swatches on the picture as it is (turned, the words stand on end)
    colours = {i: swatch_colours(pic.img, [e["label"] for e in blocks[i].get("legend") or []], pic.tokens)
               for i in wanted}
    best: dict[int, BlockResult] = {}
    for p in tries:
        axes = find_axes(p.tokens)
        if len(axes) < len(wanted):
            continue
        if len(axes) > len(wanted):  # stray number columns (a table): the axes with most labels
            axes = sorted(axes, key=lambda a: len(a.labels), reverse=True)[:len(wanted)]
        band = max(1.0, p.img.shape[0] / 3)
        axes.sort(key=lambda a: (round(a.pos(a.high) / band), a.x1))
        out = {}
        for i, axis, plot in zip(wanted, axes, _plots(p, axes, [blocks[i] for i in wanted])):
            if plot is None:
                continue
            block = blocks[i]
            found = (measure_line if block["visual_type"] == "line" else measure_bars)(
                p.img, p.tokens, plot, block, colours[i])
            if found:
                out[i] = BlockResult(found, axis.low, axis.high, axis.step)
                logger.info("Measured %d figures of block %d (%s, axis %g-%g step %g, %d labels, fit ±%.1f px%s)",
                            len(found), i, block["visual_type"], axis.low, axis.high, axis.step, len(axis.labels),
                            axis.resid, f", turned {p.tilt:.1f}°" if p.tilt else "")
        if sum(len(r.figures) for r in out.values()) > sum(len(r.figures) for r in best.values()):
            best = out
    return best
