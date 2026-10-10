"""Turns the Feedback Coach's JSON into a validated, render-ready Writing report.

The model returns the essay split into segments (text / good / error). It is
never trusted to reproduce the essay: the report always renders the
student's ORIGINAL text, and only the marked segments (good / error) are
located in it, in order. A model copy that adds a full stop, repeats a word
or "fixes" a typo can therefore never reach the PDF - at worst one mark is
not shown (its mistake is still listed in Batafsil tahlil). Requiring the
model's full copy to match character for character failed on real essays
(acceptance test, 2026-10-09: two attempts out of two on the reference
essay), so the caller only retries when fewer than MIN_ANCHORED_SHARE of the
marks can be found, and shows the plain essay if a retry doesn't help.

Also enforced here (not left to the prompt): mistakes are numbered in essay
order, mistakes whose correction changes nothing are dropped, at most 5
"good" highlights, inline fixes only when 6 words or fewer, focus items only
point at mistakes that exist, and the overall band / next target are computed
with the same rule the rubric states (mean of the criteria, nearest 0.5).
"""
from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass, field

from services.ai.schemas import EssaySegment, Mistake, VocabEntry, WritingFeedback

INLINE_FIX_MAX_WORDS = 6
MAX_GOOD_HIGHLIGHTS = 5

CRITERION_NAMES = {
    "TR_or_TA": {"task1": "Task Achievement", "task2": "Task Response"},
    "CC": "Coherence & Cohesion",
    "LR": "Lexical Resource",
    "GRA": "Grammatical Range & Accuracy",
}
# Tag label + CSS class shown next to each mistake / focus item.
CRITERION_TAGS = {
    "LR": ("Lexical", "lr"),
    "GRA": ("Grammar", "gra"),
    "CC": ("Coherence", "cc"),
    "TR_or_TA": ("Task", "tr"),
}
# db criteria_scores keys (kept stable for history/statistics).
DB_KEYS = {"TR_or_TA": "TA", "CC": "CC", "LR": "LR", "GRA": "GRA"}

_CANON = str.maketrans({"’": "'", "‘": "'", "`": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})


def canon(text: str) -> str:
    """Whitespace- and quote/dash-normalised form used only for COMPARING texts."""
    return re.sub(r"\s+", " ", text.translate(_CANON)).strip()


# --- Scores ----------------------------------------------------------------------


def snap_band(value: float) -> float:
    return max(0.0, min(9.0, round(float(value) * 2) / 2))


def overall_band(criteria: list[float]) -> float:
    """Mean of the four criteria rounded to the nearest 0.5 (.25 and .75 round
    up) - the rule the grading rubric states."""
    mean = sum(criteria) / len(criteria)
    return math.floor(mean * 2 + 0.5) / 2


def target_band(overall: float) -> float:
    return min(9.0, overall + 0.5)


# --- Segment alignment -------------------------------------------------------------


MIN_ANCHORED_SHARE = 0.8  # below this the caller asks the model once more
MIN_USABLE_SHARE = 0.5  # below this the essay is shown without marks


@dataclass
class Span:
    start: int
    end: int
    type: str  # "good" | "error" (plain text isn't stored)
    fix: str | None = None
    mistake_id: int | None = None


@dataclass
class Alignment:
    spans: list[Span]
    marked: int  # good/error segments the model returned
    anchored: int  # how many of them were found in the original, in order

    @property
    def share(self) -> float:
        return self.anchored / self.marked if self.marked else 1.0


def _canon_index(original: str) -> tuple[str, list[int]]:
    """canon(original) plus, for each of its characters, the index of the
    original character it came from."""
    chars: list[str] = []
    index: list[int] = []
    previous_space = False
    for i, ch in enumerate(original):
        c = ch.translate(_CANON)
        if c.isspace():
            if previous_space:
                continue
            c, previous_space = " ", True
        else:
            previous_space = False
        chars.append(c)
        index.append(i)
    return "".join(chars), index


def _find_word_aligned(text: str, target: str, pos: int) -> int:
    """First occurrence of `target` at or after `pos` that doesn't start or
    end inside a word (so an error segment "a" never lands inside "an")."""
    k = text.find(target, pos)
    while k != -1:
        before_ok = k == 0 or not (target[0].isalnum() and text[k - 1].isalnum())
        end = k + len(target)
        after_ok = end >= len(text) or not (target[-1].isalnum() and text[end].isalnum())
        if before_ok and after_ok:
            return k
        k = text.find(target, k + 1)
    return -1


def align_segments(original: str, essay: list[list[EssaySegment]]) -> Alignment:
    """Locates the model's good/error segments in the original essay, in
    order. Plain text segments are ignored - the original is what's shown."""
    text, index = _canon_index(original)
    pos, spans, marked = 0, [], 0
    for seg in (s for paragraph in essay for s in paragraph):
        if seg.type not in ("good", "error"):
            continue
        target = canon(seg.text)
        if not target:
            continue
        marked += 1
        k = _find_word_aligned(text, target, pos)
        if k == -1:
            continue
        is_error = seg.type == "error"
        spans.append(
            Span(
                start=index[k],
                end=index[k + len(target) - 1] + 1,
                type=seg.type,
                fix=seg.fix if is_error else None,
                mistake_id=seg.mistake_id if is_error else None,
            )
        )
        pos = k + len(target)
    return Alignment(spans, marked, len(spans))


# --- Essay rendering ------------------------------------------------------------------


def _paragraph_ranges(text: str) -> list[tuple[int, int]]:
    """Each line of the original is a paragraph (students often separate
    paragraphs with a single newline in Telegram)."""
    ranges = []
    for m in re.finditer(r"[^\n]+", text):
        if m.group().strip():
            ranges.append((m.start(), m.end()))
    return ranges


def _inline_fix_html(wrong: str, fix: str) -> str:
    """'interest to' + 'interest in' -> interest <s>to</s> <ins>in</ins>: only
    the words that actually change are struck through / inserted."""
    w, f = wrong.split(), fix.split()
    pre = 0
    while pre < min(len(w), len(f)) and canon(w[pre]) == canon(f[pre]):
        pre += 1
    suf = 0
    while suf < min(len(w), len(f)) - pre and canon(w[-1 - suf]) == canon(f[-1 - suf]):
        suf += 1
    head, old = w[:pre], w[pre : len(w) - suf]
    new, tail = f[pre : len(f) - suf], w[len(w) - suf :]
    parts = []
    if head:
        parts.append(html.escape(" ".join(head)))
    if old:
        parts.append(f"<s>{html.escape(' '.join(old))}</s>")
    if new:
        parts.append(f"<ins>{html.escape(' '.join(new))}</ins>")
    if tail:
        parts.append(html.escape(" ".join(tail)))
    return " ".join(parts)


def render_essay(original: str, spans: list[Span] | None) -> list[str]:
    """One HTML string per paragraph. Text always comes from `original`."""
    paragraphs = []
    for p_start, p_end in _paragraph_ranges(original):
        out, pos = [], p_start
        for span in spans or []:
            start, end = max(span.start, p_start), min(span.end, p_end)
            if start >= end or start < pos:
                continue
            out.append(html.escape(original[pos:start]))
            piece = original[start:end]
            whole = start == span.start and end == span.end
            if span.type == "good":
                out.append(f'<span class="good">{html.escape(piece)}</span>')
            elif whole and span.fix is not None and len(span.fix.split()) <= INLINE_FIX_MAX_WORDS:
                out.append(f'<span class="err">{_inline_fix_html(piece, span.fix)}</span>')
            else:
                out.append(f'<span class="err">{html.escape(piece)}</span>')
            pos = end
        out.append(html.escape(original[pos:p_end]))
        paragraphs.append("".join(out))
    return paragraphs


# --- Report assembly ---------------------------------------------------------------------


@dataclass
class WritingReport:
    task_type: str  # "task1" | "task2"
    overall_band: float
    target_band: float
    criteria: list[dict]  # key, name, band, note, cls
    strength: str
    focus: list[dict]  # title, rule, tag, tag_cls, ids
    essay_paragraphs: list[str]  # rendered HTML
    essay_marked: bool
    mistakes: list[dict]  # n, wrong, correct, why, criterion, tag, tag_cls, unclear, likely_meaning
    vocabulary: list[VocabEntry]
    topic: str
    raw: WritingFeedback | None = field(default=None, repr=False)

    def criteria_scores(self) -> dict[str, float]:
        return {DB_KEYS[c["key"]]: c["band"] for c in self.criteria}

    def mistakes_json(self) -> list[dict]:
        """Stored in writing_submissions.annotations - the progress tracker
        compares these per-criterion counts with the next essay."""
        return [
            {k: m[k] for k in ("n", "wrong", "correct", "why", "criterion", "unclear", "likely_meaning")}
            for m in self.mistakes
        ]

    def mistake_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for m in self.mistakes:
            counts[m["criterion"]] = counts.get(m["criterion"], 0) + 1
        return counts


def _mistake_position(m: Mistake, original: str, segment_pos: dict[int, int]) -> float:
    if m.id in segment_pos:
        return segment_pos[m.id]
    idx = canon(original).lower().find(canon(m.wrong).lower())
    return idx if idx >= 0 else math.inf


def build_report(task_type: str, original: str, fb: WritingFeedback, spans: list[Span] | None) -> WritingReport:
    # Where each mistake first appears in the essay (from the anchored marks).
    segment_pos: dict[int, int] = {}
    for span in spans or []:
        if span.mistake_id is not None:
            segment_pos.setdefault(span.mistake_id, span.start)

    kept = [
        m for m in fb.mistakes
        if m.unclear or canon(m.wrong).lower() != canon(m.correct).lower()
    ]
    kept.sort(key=lambda m: _mistake_position(m, original, segment_pos))
    renumber = {m.id: n for n, m in enumerate(kept, 1)}

    mistakes = []
    for m in kept:
        tag, tag_cls = CRITERION_TAGS[m.criterion]
        mistakes.append(
            {
                "n": renumber[m.id],
                "wrong": m.wrong,
                "correct": m.correct,
                "why": m.why,
                "criterion": m.criterion,
                "tag": tag,
                "tag_cls": tag_cls,
                "unclear": bool(m.unclear and m.likely_meaning),
                "likely_meaning": m.likely_meaning if m.unclear else None,
            }
        )

    focus = []
    for item in fb.focus[:3]:
        tag, tag_cls = CRITERION_TAGS[item.criterion]
        ids = sorted({renumber[i] for i in item.mistake_ids if i in renumber})
        focus.append({"title": item.title, "rule": item.rule, "tag": tag, "tag_cls": tag_cls, "ids": ids})

    # At most MAX_GOOD_HIGHLIGHTS green phrases; extras render as plain text.
    if spans is not None:
        good_seen = 0
        trimmed = []
        for span in spans:
            if span.type == "good":
                good_seen += 1
                if good_seen > MAX_GOOD_HIGHLIGHTS:
                    continue
            trimmed.append(span)
        spans = trimmed

    bands = {key: snap_band(getattr(fb.scores, key)) for key in ("TR_or_TA", "CC", "LR", "GRA")}
    overall = overall_band(list(bands.values()))
    criteria = []
    for key, band in bands.items():
        name = CRITERION_NAMES[key][task_type] if isinstance(CRITERION_NAMES[key], dict) else CRITERION_NAMES[key]
        cls = "strong" if band > overall else "ok" if band == overall else "weak"
        criteria.append(
            {"key": key, "name": name, "band": band, "note": getattr(fb.criterion_notes, key), "cls": cls}
        )

    return WritingReport(
        task_type=task_type,
        overall_band=overall,
        target_band=target_band(overall),
        criteria=criteria,
        strength=fb.strength,
        focus=focus,
        essay_paragraphs=render_essay(original, spans),
        essay_marked=spans is not None,
        mistakes=mistakes,
        vocabulary=fb.vocabulary[:12],
        topic=fb.topic,
        raw=fb,
    )
