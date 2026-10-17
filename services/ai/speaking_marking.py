"""Turns the Speaking feedback JSON, the scores and the voice analysis into a
validated, render-ready report (Speaking report prompt, 2026-10-13; the
Writing counterpart is services/ai/essay_marking.py, whose text-locating
helpers this reuses).

The report always shows the ORIGINAL verbatim transcripts; only the model's
marked segments are located in them. Enforced here, not left to the prompt
(section 1.12):

  * each answer's segments must join back to its transcript, or only the
    marks that can be placed are drawn (the caller retries the model once
    when too few can be);
  * no mistake on a low-confidence word, and none that is only a filler -
    fillers come from code (services/ai/voice.py) and are drawn grey;
  * strong phrases never sit on a mistake, at most 5 per session, and every
    phrase quoted in "What you did well" is one of them;
  * mistakes are numbered in answer order; dropped when the verification
    pass says they aren't definitely wrong;
  * 3 priorities, each with 2-5 mistakes of its tags or a voice reference;
    10-12 vocabulary items; 5-6 checklist lines;
  * repeated questions are labelled ("Same question as Q3");
  * bands, chips, stats, timings, the voice page and progress.
"""
from __future__ import annotations

import difflib
import html
import math
import re
from dataclasses import dataclass, field

from services.ai.essay_marking import (
    MIN_ANCHORED_SHARE,
    MIN_USABLE_SHARE,
    _QUOTES,
    _shared_run,
    align_segments,
    canon,
    creates_double,
    essay_matches,
    inline_format,
    locate,
    phrases_in_goods,
    strength_html,
)
from services.ai.pronunciation import Assessment
from services.ai.schemas import SpeakingFeedback
from services.ai.scoring import overall_band, target_band, whole_band
from services.ai.speaking_grader import SessionItem
from services.ai.voice import CONFIDENCE_LIMIT, NATURAL_WPM, TOKEN_RE, Pitch, VoiceMetrics

INLINE_FIX_MAX_WORDS = 6
MAX_GOOD = 5
PRIORITY_COUNT = 3
PRIORITY_IDS = (2, 5)
VOCAB_RANGE = (10, 12)
CHECKLIST_RANGE = (5, 6)
PART2_TARGET_SECONDS = 120
# Pitch movement (semitones, 10th-90th percentile) - measured 2026-10-14: the
# model answers' natural TTS voice ~9.6, students 2.8-7.5.
INTONATION_NATURAL = 8.0
INTONATION_FLAT = 5.0
VOICE_REF = "See: Your voice, measured"

CRITERIA = (
    ("FC", "Fluency & Coherence"),
    ("LR", "Lexical Resource"),
    ("GRA", "Grammatical Range & Accuracy"),
    ("P", "Pronunciation"),
)
# Mistake / priority tag: label + CSS class.
TAGS = {"LR": ("Lexical", "lr"), "GRA": ("Grammar", "gra"), "FLU": ("Fluency", "flu"), "PRON": ("Pronunciation", "pron")}
DB_KEYS = {"FC": "FC", "LR": "LR", "GRA": "GRA", "P": "Pronunciation"}
PART_NAMES = {"part1": "Part 1", "part2": "Part 2", "part3": "Part 3", "full": "Full test"}


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def fmt_seconds(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


# --- Rendering a transcript ----------------------------------------------------------------------


def _with_fillers(text: str, start: int, end: int, fillers: list[tuple[int, int]]) -> str:
    """text[start:end], escaped, with the filler words in it drawn grey."""
    out, pos = [], start
    for f_start, f_end in fillers:
        a, b = max(f_start, start), min(f_end, end)
        if a >= b or a < pos:
            continue
        out.append(_esc(text[pos:a]))
        out.append(f'<span class="filler">{_esc(text[a:b])}</span>')
        pos = b
    out.append(_esc(text[pos:end]))
    return "".join(out)


def _wnorm(word: str) -> str:
    return re.sub(r"[^a-z0-9']", "", word.lower().replace("’", "'"))


def inline_fix_html(text: str, start: int, end: int, fix: str, fillers: list[tuple[int, int]]) -> str:
    """An amber-underlined mistake with its fix shown inline: words to drop
    struck through, new words in green, fillers inside it left grey and
    unstruck ("I used to <s>be</s> like <s>sticking with</s> <ins>stick
    to</ins> dreams")."""
    words = [(m.start() + start, m.end() + start) for m in TOKEN_RE.finditer(text[start:end])]
    is_filler = [any(a < f_end and f_start < b for f_start, f_end in fillers) for a, b in words]
    core = [i for i, f in enumerate(is_filler) if not f]
    fix_words = fix.split()
    matcher = difflib.SequenceMatcher(
        None, [_wnorm(text[words[i][0]:words[i][1]]) for i in core], [_wnorm(w) for w in fix_words], autojunk=False
    )
    deleted: set[int] = set()
    insert_after: dict[int, str] = {}  # word index -> inserted text after it (-1 = before the first)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        for k in range(i1, i2):
            deleted.add(core[k])
        new = " ".join(fix_words[j1:j2])
        if new:
            anchor = core[i2 - 1] if i2 > i1 else (core[i1 - 1] if i1 > 0 else -1)
            insert_after[anchor] = (insert_after.get(anchor, "") + " " + new).strip()
    if not deleted and not insert_after:
        return f'<span class="err">{_with_fillers(text, start, end, fillers)}</span>'

    pieces: list[tuple[str, str]] = []  # (kind, html): kind in del/ins/keep/gap
    if -1 in insert_after:
        pieces += [("ins", f"<ins>{_esc(insert_after[-1])}</ins>"), ("gap", " ")]
    pos = start
    for i, (a, b) in enumerate(words):
        if a > pos:
            pieces.append(("gap", _esc(text[pos:a])))
        word = text[a:b]
        if is_filler[i]:
            pieces.append(("keep", f'<span class="filler">{_esc(word)}</span>'))
        elif i in deleted:
            pieces.append(("del", _esc(word)))
        else:
            pieces.append(("keep", _esc(word)))
        if i in insert_after:
            pieces += [("gap", " "), ("ins", f"<ins>{_esc(insert_after[i])}</ins>")]
        pos = b
    if pos < end:
        pieces.append(("gap", _esc(text[pos:end])))

    # consecutive struck words (and the spaces between them) become one <s>
    out: list[str] = []
    i = 0
    while i < len(pieces):
        kind, h = pieces[i]
        if kind != "del":
            out.append(h)
            i += 1
            continue
        group = [h]
        j = i + 1
        while j + 1 < len(pieces) and pieces[j][0] == "gap" and pieces[j][1].strip() == "" and pieces[j + 1][0] == "del":
            group += [pieces[j][1], pieces[j + 1][1]]
            j += 2
        out.append(f"<s>{''.join(group)}</s>")
        i = j
    return f'<span class="err">{"".join(out)}</span>'


@dataclass(eq=False)
class _Mistake:
    q: int  # index of the answer it is in
    wrong: str
    correct: str
    why: str
    criterion: str
    unclear: bool
    if_you_meant: str | None
    model_id: int | None = None
    pos: float = math.inf
    n: int = 0


@dataclass(eq=False)
class _Mark:
    start: int
    end: int
    kind: str  # "good" | "error"
    fix: str | None = None
    mistake: _Mistake | None = None


def _overlaps(start: int, end: int, marks: list[_Mark]) -> bool:
    return any(start < k.end and k.start < end for k in marks)


def render_answer(text: str, marks: list[_Mark], fillers: list[tuple[int, int]]) -> str:
    out, pos = [], 0
    for k in sorted(marks, key=lambda k: k.start):
        if k.start < pos:
            continue
        out.append(_with_fillers(text, pos, k.start, fillers))
        if k.kind == "good":
            out.append(f'<span class="good">{_with_fillers(text, k.start, k.end, fillers)}</span>')
        elif k.fix is not None:
            out.append(inline_fix_html(text, k.start, k.end, k.fix, fillers))
        else:
            out.append(f'<span class="err">{_with_fillers(text, k.start, k.end, fillers)}</span>')
        pos = k.end
    out.append(_with_fillers(text, pos, len(text), fillers))
    return "".join(out)


# --- Checks ---------------------------------------------------------------------------------------------


def _answer_index(items: list[SessionItem]) -> dict[str, int]:
    return {item.qid.upper(): i for i, item in enumerate(items)}


def _segments_for(fb: SpeakingFeedback, qid: str):
    for a in fb.answers:
        if a.id.strip().upper() == qid.upper():
            return a.segments
    return None


def assess(fb: SpeakingFeedback, items: list[SessionItem]) -> list[str]:
    """Problems worth one more feedback call (each retry costs as much as the
    first call): marks that can't be placed, a missing answer, a short section."""
    problems = []
    for item in items:
        if item.audio.no_speech:
            continue
        segments = _segments_for(fb, item.qid)
        if segments is None:
            problems.append(f"no segments for {item.qid}")
        elif not essay_matches(item.audio.text, [segments]):
            share = align_segments(item.audio.text, [segments]).share
            if share < MIN_ANCHORED_SHARE:
                problems.append(f"only {share:.0%} of {item.qid}'s marks can be placed")
    if len(fb.priorities) < PRIORITY_COUNT:
        problems.append(f"{len(fb.priorities)} priorities instead of {PRIORITY_COUNT}")
    if len(fb.vocabulary) < VOCAB_RANGE[0]:
        problems.append(f"{len(fb.vocabulary)} vocabulary items")
    if len(fb.checklist) < CHECKLIST_RANGE[0]:
        problems.append(f"{len(fb.checklist)} checklist lines")
    return problems


def _low_conf(item: SessionItem) -> list[tuple[int, int]]:
    return [(t.start_char, t.end_char) for t in item.audio.tokens if t.conf < CONFIDENCE_LIMIT]


def _filler_spans(item: SessionItem) -> list[tuple[int, int]]:
    return sorted((f.start_char, f.end_char) for f in item.audio.fillers)


def _only_fillers(item: SessionItem, start: int, end: int) -> bool:
    words = [t for t in item.audio.tokens if t.start_char < end and start < t.end_char]
    fillers = _filler_spans(item)
    return bool(words) and all(any(t.start_char < b and a < t.end_char for a, b in fillers) for t in words)


def _hits(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(start < b and a < end for a, b in spans)


def _ref(ref: str, by_model_id: dict[int, _Mistake], priorities: int) -> str:
    p = re.search(r"priority\D*(\d+)", ref, re.IGNORECASE)
    if p and 1 <= int(p.group(1)) <= priorities:
        return f"Priority {int(p.group(1)):02d}"
    m = re.search(r"mistake\D*(\d+)", ref, re.IGNORECASE)
    if m and int(m.group(1)) in by_model_id:
        return f"Mistake {by_model_id[int(m.group(1))].n}"
    if re.search(r"voice", ref, re.IGNORECASE):
        return "Your voice, measured"
    return ""


# --- Voice page --------------------------------------------------------------------------------------

_NATURAL = [(5, 40), (30, 12), (55, 12), (80, 32), (120, 52), (145, 22), (190, 18), (215, 44)]
_BASELINE = 34


def _curve(flatness: float) -> str:
    """The sketch's pitch line: the natural rise and fall, flattened toward
    the baseline by `flatness` (1 = natural, 0 = flat)."""
    p = [(x, round(_BASELINE + (y - _BASELINE) * flatness, 1)) for x, y in _NATURAL]
    return (f"M{p[0][0]} {p[0][1]} C {p[1][0]} {p[1][1]}, {p[2][0]} {p[2][1]}, {p[3][0]} {p[3][1]} "
            f"S {p[4][0]} {p[4][1]}, {p[5][0]} {p[5][1]} S {p[6][0]} {p[6][1]}, {p[7][0]} {p[7][1]}")


def _pace_note(wpm: int, fillers_per_minute: float) -> str:
    lo, hi = NATURAL_WPM
    if wpm < lo:
        return f"Slower than natural speech. Aim for {lo}–{hi} words a minute."
    if wpm > hi:
        return "Faster than natural speech. Slow down a little so every word is clear."
    note = "Inside the natural range."
    if fillers_per_minute >= 4:
        note += " Speed is not your problem; fillers are."
    return note


def _filler_note(m: VoiceMetrics, target: float) -> str:
    if not m.fillers:
        return "No fillers. Your answers flow cleanly."
    every = m.speaking_seconds / m.fillers
    first = f"About one every {max(1, round(every))} seconds." if every >= 1.5 else "More than one every two seconds."
    band = max(6, min(9, math.floor(target)))
    return f"{first} Band {band} speakers use them {'only occasionally' if band <= 7 else 'rarely'}."


def _contour_path(contour: list[float]) -> str:
    """The student's measured pitch (semitones around their own median) as
    the sketch's line: 48 points across the 220 x 60 box."""
    step = 210 / (len(contour) - 1)
    points = [(5 + i * step, max(4.0, min(54.0, _BASELINE - v * 2.2))) for i, v in enumerate(contour)]
    return "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in points)


def _intonation(pitch: Pitch) -> dict:
    if pitch.span >= INTONATION_NATURAL:
        tip = "Your pitch rises and falls naturally. Keep <b>stressing the key word</b> in each answer."
    elif pitch.span >= INTONATION_FLAT:
        tip = ("Your pitch varies a little but often stays level. Let your voice <b>rise on the key word</b> "
               "and <b>fall at the end</b> of each answer.")
    else:
        tip = ("Your pitch stays almost flat, which can sound bored or unsure. Let your voice <b>rise on the "
               "key word</b> and <b>fall at the end</b> of each answer.")
    return {"student": _contour_path(pitch.contour), "natural": _curve(1.0), "tip": tip}


def voice_page(metrics: VoiceMetrics, items: list[SessionItem], pron: Assessment | None,
               practise: list[dict] | None, target: float, pitch: Pitch | None = None) -> dict:
    m = metrics
    lo, hi = NATURAL_WPM
    marker = max(2.0, min(98.0, 35 + (m.wpm - lo) / (hi - lo) * 35))
    if m.long_pauses:
        where = items[m.longest_pause_answer].qid if m.longest_pause_answer is not None else ""
        note = f"Longest: {m.longest_pause.seconds:.1f} s, in your {where} answer." if where else ""
        mid = m.mid_sentence_pauses
        if mid:
            note += f" {'One' if mid == 1 else mid} of them {'happens' if mid == 1 else 'happen'} in the middle of a sentence."
        else:
            note += " All of them come between sentences."
    else:
        note = "No pauses over a second. Your speech keeps moving."
    top = max((n for _, n in m.filler_types), default=0)
    voice = {
        "pace": {"wpm": m.wpm, "marker": round(marker, 1), "note": _pace_note(m.wpm, m.fillers_per_minute)},
        "pauses": {"count": m.long_pauses, "note": note.strip()},
        "fillers": {"count": m.fillers, "seconds": round(m.speaking_seconds), "note": _filler_note(m, target)},
        "filler_bars": [
            {"name": name, "count": n, "pct": round(n / top * 100) if top else 0} for name, n in m.filler_types
        ],
        "practise": practise if pron is not None and practise else None,
        "intonation": None,
    }
    if pitch is not None:
        voice["intonation"] = _intonation(pitch)
    return voice


# --- Progress ------------------------------------------------------------------------------------------


def _chip(change: float, better_when_lower: bool, word_up: str, word_down: str, same: float = 0.0) -> dict:
    if abs(change) <= same:
        return {"chip": "= same", "cls": "eq"}
    better = change < 0 if better_when_lower else change > 0
    return {"chip": f"✓ {word_down if better_when_lower else word_up}", "cls": "up"} if better else \
        {"chip": f"↑ {word_up}" if better_when_lower else f"↓ {word_down}", "cls": "down"}


def build_progress(part: str, overall: float, metrics: VoiceMetrics, previous: dict | None,
                   priorities: list[dict], resubmission_of: int | None) -> dict:
    """The progress page: compared with the student's previous session of the
    same part ({"band", "metrics", "number"}), never with a resubmission of
    the same answers."""
    if resubmission_of is not None:
        return {"first": True, "resubmission": True, "lead": f"Same answers as session #{resubmission_of}.",
                "rest": "Progress is only shown for new answers. Record fresh answers next time to see how "
                "your band, fillers and pace change."}
    if previous is None:
        name = "full test" if part == "full" else f"{PART_NAMES[part]} session"
        return {"first": True, "lead": f"This is your first {name}.",
                "rest": "Your results are saved, and your next report will show how your band, fillers and "
                "pace have changed."}
    rows, gains = [], []
    before = float(previous["band"])
    rows.append({"label": "Band", "value": f"{before:.1f} → {overall:.1f}",
                 **_chip(overall - before, False, "higher", "lower")})
    if overall > before:
        gains.append(((overall - before) / 2, f"your band rose from {before:.1f} to {overall:.1f}"))
    pm = previous.get("metrics") or {}
    if pm.get("fillers_per_minute") is not None:
        old, new = float(pm["fillers_per_minute"]), round(metrics.fillers_per_minute, 1)
        rows.append({"label": "Fillers per minute", "value": f"{old:.1f} → {new:.1f}",
                     **_chip(new - old, True, "more", "fewer", same=0.2)})
        if new < old - 0.2:
            gains.append(((old - new) / max(old, 0.1), f"your fillers dropped from {old:.1f} to {new:.1f} a minute"))
    if pm.get("wpm"):
        old, new = int(pm["wpm"]), metrics.wpm
        mid = sum(NATURAL_WPM) / 2
        closer = abs(old - mid) - abs(new - mid)
        rows.append({"label": "Words per minute", "value": f"{old} → {new}",
                     **_chip(closer, False, "more natural", "less natural", same=4)})
        if closer > 4:
            gains.append((closer / mid, f"your pace moved from {old} to {new} words a minute"))
    if pm.get("pauses_per_minute") is not None:
        old, new = float(pm["pauses_per_minute"]), round(metrics.pauses_per_minute, 1)
        rows.append({"label": "Long pauses per minute", "value": f"{old:.1f} → {new:.1f}",
                     **_chip(new - old, True, "more", "fewer", same=0.2)})
        if new < old - 0.2:
            gains.append(((old - new) / max(old, 0.1), f"your long pauses fell from {old:.1f} to {new:.1f} a minute"))
    if gains:
        note = f"Great work: {max(gains)[1]}."
    elif overall == before:
        note = f"Your band held steady at {overall:.1f}."
    else:
        note = "Scores move up and down from one session to the next - keep practising."
    if priorities:
        note += f" Next time, focus on “{priorities[0]['title']}” (priority 01)."
    return {"first": False, "rows": rows, "note": note,
            "compared": f"Compared with your previous {PART_NAMES[part]} session"
            + (f" (#{previous['number']})" if previous.get("number") else "") + "."}


# --- Assembly ----------------------------------------------------------------------------------------------


@dataclass
class SpeakingReport:
    part: str
    task_chip: str
    meta_label: str
    band_label: str
    overall_band: float
    target_band: float
    criteria: list[dict]
    pron_estimated: bool
    stats: list[tuple[str, str]]  # (value, label)
    strength_html: str
    priorities: list[dict]
    cards: list[dict]
    marked: bool
    mistakes: list[dict]
    voice: dict
    vocabulary_topic: str
    vocabulary: list
    checklist: list[dict]
    disclaimer: str
    notes: list[str] = field(default_factory=list)

    def criteria_scores(self) -> dict[str, float]:
        return {DB_KEYS[c["key"]]: c["band"] for c in self.criteria}

    def mistakes_json(self) -> list[dict]:
        keys = ("n", "q", "wrong", "correct", "why", "criterion", "unclear", "if_you_meant")
        return [{k: m[k] for k in keys} for m in self.mistakes]


def _disclaimer(part: str) -> str:
    scored = ("Scored against the IELTS Speaking Band Descriptors: the four criteria and the 0–9 scale examiners "
              "use. Each criterion is scored three times and the middle score counts.")
    official = " Bandzilla is an independent service; official IELTS results come only from a test centre."
    if part == "full":
        return scored + official
    return f"{scored} This band covers {PART_NAMES[part]}; a full Speaking band takes Parts 1–3.{official}"


def _from_model(m, q: int, model_id: int | None) -> _Mistake:
    unclear = bool(m.unclear and m.if_you_meant)
    return _Mistake(q=q, wrong=m.wrong.strip(), correct=m.correct.strip(), why=m.why.strip(),
                    criterion=m.criterion, unclear=unclear,
                    if_you_meant=m.if_you_meant.strip() if unclear else None, model_id=model_id)


def build_report(
    part: str,
    items: list[SessionItem],
    fb: SpeakingFeedback,
    missed: list | tuple,
    dropped: set[int],
    dropped_missed: set[int],
    scores: dict[str, float],
    metrics: VoiceMetrics,
    pron: Assessment | None,
    practise: list[dict] | None,
    pitch: Pitch | None = None,
) -> SpeakingReport:
    """`dropped` / `dropped_missed`: ids of the first-pass mistakes and
    indexes of the missed-pass ones the verification pass rejected."""
    notes: list[str] = []
    index = _answer_index(items)

    def answer_of(qid: str) -> int | None:
        return index.get(qid.strip().upper())

    # The model's mistakes, minus rejected / no-op / repeated ones.
    mistakes: list[_Mistake] = []
    seen = set()
    for m in fb.mistakes:
        if m.id in seen or m.id in dropped:
            notes.append(f"dropped mistake {m.id} ({'rejected by the check' if m.id in dropped else 'repeated id'}): {m.wrong!r}")
            continue
        if not m.unclear and canon(m.wrong).lower() == canon(m.correct).lower():
            notes.append(f"dropped mistake {m.id} (no change)")
            continue
        seen.add(m.id)
        q = answer_of(m.question)
        if q is None:  # find it in whichever answer contains it
            q = next((i for i, it in enumerate(items) if locate(it.audio.text, m.wrong)), None)
        if q is None:
            notes.append(f"dropped mistake {m.id}: unknown answer {m.question!r}")
            continue
        mistakes.append(_from_model(m, q, m.id))

    marks: list[list[_Mark]] = [[] for _ in items]
    marked_flags = []
    alignments = []
    for i, item in enumerate(items):
        segments = _segments_for(fb, item.qid) or []
        alignment = align_segments(item.audio.text, [segments]) if item.audio.text else None
        alignments.append(alignment)
        marked_flags.append(alignment is not None and alignment.share >= MIN_USABLE_SHARE)
        if alignment is not None and not marked_flags[-1]:
            notes.append(f"{item.qid} shown without marks ({alignment.anchored}/{alignment.marked} found)")

    by_model_id = {m.model_id: m for m in mistakes if m.model_id is not None}

    def placeable(m: _Mistake, start: int, end: int) -> bool:
        item = items[m.q]
        if _hits(_low_conf(item), start, end):
            notes.append(f"dropped {m.wrong!r}: on an uncertain word")
            return False
        if _only_fillers(item, start, end):
            notes.append(f"dropped {m.wrong!r}: only fillers")
            return False
        return True

    # Error segments the model marked.
    for i, alignment in enumerate(alignments):
        if not marked_flags[i]:
            continue
        for span in (s for s in alignment.spans if s.type == "error"):
            m = by_model_id.get(span.mistake_id)
            if m is None or m.q != i or _overlaps(span.start, span.end, marks[i]):
                continue
            if not placeable(m, span.start, span.end):
                mistakes.remove(m)
                by_model_id.pop(m.model_id, None)
                continue
            marks[i].append(_Mark(span.start, span.end, "error", fix=span.fix, mistake=m))
    # Listed but not marked: find their text, or drop them.
    for m in list(mistakes):
        if any(k.mistake is m for k in marks[m.q]):
            continue
        loc = locate(items[m.q].audio.text, m.wrong)
        if loc is None:
            notes.append(f"dropped unplaceable mistake {m.wrong!r}")
            mistakes.remove(m)
            continue
        if not placeable(m, *loc):
            mistakes.remove(m)
            continue
        if _overlaps(*loc, marks[m.q]) or not marked_flags[m.q]:
            m.pos = loc[0]
        else:
            marks[m.q].append(_Mark(*loc, "error", fix=m.correct, mistake=m))

    # The missed-errors pass.
    for k, x in enumerate(missed):
        if k in dropped_missed:
            notes.append(f"missed-pass item rejected by the check: {x.wrong!r}")
            continue
        if not x.unclear and canon(x.wrong).lower() == canon(x.correct).lower():
            continue
        q = answer_of(x.question)
        if q is None or any(_shared_run(x.wrong, m.wrong, 3) for m in mistakes if m.q == q):
            continue
        loc = locate(items[q].audio.text, x.wrong)
        if loc is None or _overlaps(*loc, marks[q]):
            continue
        m = _from_model(x, q, None)
        if not placeable(m, *loc):
            continue
        mistakes.append(m)
        if marked_flags[q]:
            marks[q].append(_Mark(*loc, "error", fix=m.correct, mistake=m))
        else:
            m.pos = loc[0]
        notes.append(f"second pass added {x.wrong!r} -> {x.correct!r}")

    # Strong phrases: not on a mistake, a filler-only stretch or an uncertain word; 5 at most.
    goods_text: list[str] = []
    for i, alignment in enumerate(alignments):
        if not marked_flags[i]:
            continue
        item = items[i]
        wrongs = [canon(m.wrong).lower() for m in mistakes if m.q == i]
        for span in (s for s in alignment.spans if s.type == "good"):
            text = canon(item.audio.text[span.start:span.end]).lower()
            if (_overlaps(span.start, span.end, marks[i]) or any(w and w in text for w in wrongs)
                    or _hits(_low_conf(item), span.start, span.end) or _only_fillers(item, span.start, span.end)
                    or len(goods_text) >= MAX_GOOD):
                continue
            marks[i].append(_Mark(span.start, span.end, "good"))
            goods_text.append(item.audio.text[span.start:span.end])
    # Rule 8: every phrase quoted as a strength is a green highlight.
    for phrase in fb.strength.quoted_phrases:
        core = phrase.strip().strip(_QUOTES + " .,")
        if len(core.split()) < 2 or any(canon(core).lower() in canon(g).lower() for g in goods_text):
            continue
        for i, item in enumerate(items):
            loc = locate(item.audio.text, core) if marked_flags[i] else None
            if loc and not _overlaps(*loc, marks[i]) and len(goods_text) < MAX_GOOD \
                    and not _hits(_low_conf(item), *loc) and not any(
                        canon(m.wrong).lower() in canon(core).lower() for m in mistakes if m.q == i):
                marks[i].append(_Mark(*loc, "good"))
                goods_text.append(item.audio.text[loc[0]:loc[1]])
                notes.append(f"quoted strength {core!r} added as a strong phrase")
                break
    phrases = phrases_in_goods(fb.strength.quoted_phrases, goods_text)
    if not phrases:
        notes.append("strength quotes no green phrase")

    # Inline fixes: 6 words or fewer, never doubling a word. An unclear
    # stretch and a Fluency item (an answer that stops without a reason) are
    # only underlined - what to say instead is in the list, as in Appendix A.
    for i, item in enumerate(items):
        for k in marks[i]:
            if k.kind == "error" and k.fix is not None:
                piece = item.audio.text[k.start:k.end]
                if k.mistake.unclear or k.mistake.criterion == "FLU" or len(k.fix.split()) > INLINE_FIX_MAX_WORDS \
                        or creates_double(item.audio.text, k.start, k.end, k.fix) \
                        or canon(piece).lower() == canon(k.fix).lower():
                    k.fix = None
                else:
                    # the transcript's own end punctuation stays; don't add it twice
                    k.fix = re.sub(r"[.?!,;:…]+$", "", k.fix.strip()) if re.search(r"[.?!,;:…]\s*$", piece) else k.fix

    # Number the mistakes in answer order.
    for i in range(len(items)):
        for k in marks[i]:
            if k.mistake is not None and k.mistake.pos == math.inf:
                k.mistake.pos = k.start
    mistakes.sort(key=lambda m: (m.q, m.pos, m.model_id or 10**6))
    for n, m in enumerate(mistakes, 1):
        m.n = n
    by_model_id = {m.model_id: m for m in mistakes if m.model_id is not None}

    # Priorities.
    priorities = []
    for i, p in enumerate(fb.priorities[:PRIORITY_COUNT], 1):
        tags = [c for c in dict.fromkeys(p.criteria) if c in TAGS]
        if p.voice_ref:
            ids, ref = [], VOICE_REF
        else:
            chosen = [by_model_id[x] for x in p.mistake_ids if x in by_model_id and by_model_id[x].criterion in tags]
            ids = [m.n for m in sorted(chosen, key=lambda m: m.n)][: PRIORITY_IDS[1]]
            if len(ids) < PRIORITY_IDS[0]:
                notes.append(f"priority {i} has {len(ids)} matching mistakes")
            ref = ""
            used = [c for c in tags if any(m.criterion == c for m in chosen)]
            tags = used or tags
        priorities.append({
            "n": f"{i:02d}", "title": p.title.strip(), "rule_html": inline_format(p.rule.strip()),
            "ex_wrong": p.example_wrong.strip(), "ex_right": p.example_right.strip(),
            "tags": [TAGS[c] for c in tags], "ids": ids, "voice_ref": ref,
        })

    checklist = [
        {"html": inline_format(item.text.strip()), "ref": _ref(item.ref, by_model_id, len(priorities))}
        for item in fb.checklist[: CHECKLIST_RANGE[1]]
    ]

    # Bands and chips (ties for the top both get "Strongest").
    bands = {key: whole_band(scores[key]) for key, _ in CRITERIA}
    overall = overall_band(list(bands.values()))
    target = target_band(overall)
    top = max(bands.values())
    criteria = []
    for key, name in CRITERIA:
        band = bands[key]
        if band > overall:
            chip, cls = ("Strongest" if band == top else "Strong"), "top"
        elif band == overall:
            chip, cls = "On track", "ok"
        else:
            chip, cls = "Focus", "focus"
        note = getattr(fb.criterion_notes, key).strip()
        if key == "P" and pron is None:
            note = note.rstrip(".") + ". (Scored from the transcript: no audio measurement this time.)"
        criteria.append({"key": key, "name": name, "band": band, "note": note, "chip": chip, "cls": cls})

    # Question cards.
    cards = []
    for i, item in enumerate(items):
        a = item.audio
        card = {
            "qid": item.qid, "part": item.part, "question": item.question, "duplicate_of": item.duplicate_of,
            "meta": f"{fmt_seconds(a.speaking_seconds)} · {a.word_count} words" if not a.no_speech else "no speech",
            "html": render_answer(a.text, marks[i] if marked_flags[i] else [], _filler_spans(item)) if a.text else "",
            "no_speech": a.no_speech, "cue_card": item.cue_card, "talk": None,
        }
        if item.cue_card:
            covered = {canon(c.point).lower(): c.covered for c in fb.cue_coverage}
            points = []
            for point in item.cue_card["points"]:
                key = canon(point).lower()
                hit = covered.get(key)
                if hit is None:  # the model may shorten a point
                    hit = next((v for k, v in covered.items() if k in key or key in k), None)
                points.append({"point": point, "covered": bool(hit)})
            pct = min(100, round(a.speaking_seconds / PART2_TARGET_SECONDS * 100))
            card["talk"] = {"length": fmt_seconds(a.speaking_seconds), "target": fmt_seconds(PART2_TARGET_SECONDS),
                            "pct": pct, "points": points}
        cards.append(card)

    mistake_rows = []
    for m in mistakes:
        tag, tag_cls = TAGS.get(m.criterion, TAGS["GRA"])
        mistake_rows.append({
            "n": m.n, "q": items[m.q].qid, "wrong": m.wrong, "correct": m.correct, "why": m.why,
            "why_html": inline_format(m.why), "criterion": m.criterion, "tag": tag, "tag_cls": tag_cls,
            "unclear": m.unclear, "if_you_meant": m.if_you_meant,
        })

    answered = [it for it in items if not it.audio.no_speech]
    if part == "part2":
        count = ("1", "talk")
    else:
        count = (str(len(answered)), "answers" if len(answered) != 1 else "answer")
    stats = [
        (fmt_seconds(metrics.speaking_seconds), "speaking time"),
        count,
        (str(metrics.wpm), "words / min"),
        (str(metrics.fillers), "fillers"),
    ]
    name = PART_NAMES[part]
    return SpeakingReport(
        part=part,
        task_chip=f"IELTS Speaking · {name}",
        meta_label="Full mock test" if part == "full" else f"{name} practice",
        band_label="Speaking band" if part == "full" else f"{name} band",
        overall_band=overall,
        target_band=target,
        criteria=criteria,
        pron_estimated=pron is None,
        stats=stats,
        strength_html=strength_html(fb.strength.text, phrases, goods_text),
        priorities=priorities,
        cards=cards,
        marked=all(marked_flags[i] for i, it in enumerate(items) if it.audio.text),
        mistakes=mistake_rows,
        voice=voice_page(metrics, items, pron, practise, target, pitch),
        vocabulary_topic=fb.vocabulary_topic.strip().rstrip("."),
        vocabulary=fb.vocabulary[: VOCAB_RANGE[1]],
        checklist=checklist,
        disclaimer=_disclaimer(part),
        notes=notes,
    )
