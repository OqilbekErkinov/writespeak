"""Turns the Feedback Coach's JSON into a validated, render-ready Writing report
(final report design, 2026-10-12).

The report always shows the student's ORIGINAL text. The model returns its
copy of the essay split into segments (text / good / error); `essay_matches`
checks that the copy joins back to the original (whitespace-normalised).
Either way only the marked segments are located in the original and drawn
on it, so a model copy that adds a full stop or "fixes" a typo can never
reach the PDF; the caller asks the model once more only when too few of its
marks can be placed. If even then they can't, the essay is shown without
them. (Showing the plain essay whenever the copy differs at all was tried on
2026-10-09 and failed on two attempts out of two on the reference essay.)

Enforced here, not left to the prompt:
  * text copied from the question (8+ words in a row) is found by code, shown
    grey and listed as a Task mistake (find_copied_spans);
  * strong phrases never sit on a mistake or on copied text, at most 5;
  * every mistake is marked in the essay - located by its `wrong` text when
    the model didn't mark it - or dropped, and mistakes are numbered in essay
    order; the second, missed-errors pass is merged the same way;
  * a correction that would double a word when put back into its sentence
    ("argue that that") is repaired, or not shown inline;
  * 3 priorities whose 2-5 mistake numbers carry the priority's tags, 10-12
    vocabulary items, 5-6 checklist lines;
  * every phrase quoted in "What you did well" is one of the green strong
    phrases, and the cover's strong-phrase count is the number of them;
  * mistakes the verification pass rejected are dropped, and those it put
    under another criterion are moved there (one problem, one criterion -
    "were quite passive" is Lexical, not Task);
  * the overall band (IELTS rounding) from the scoring runs' whole bands,
    the target, criterion chips and stats.
`assess` lists the problems worth one more model call.
"""
from __future__ import annotations

import difflib
import html
import math
import re
from dataclasses import dataclass, field

from services.ai.schemas import EssaySegment, MissedMistake, VocabEntry, WritingFeedback
from services.ai.scoring import overall_band, target_band, whole_band

INLINE_FIX_MAX_WORDS = 6
MAX_GOOD_HIGHLIGHTS = 5
COPY_MIN_WORDS = 8
# Task 1: the visual's subject repeated by name ("three counties in the state of Oregon", "average household
# spending on leisure activities in three countries") is no copy - a copied sentence is (2026-10-28, #61/#62).
COPY_MIN_WORDS_TASK1 = 10
UPGRADE_MAX = 3  # "Upgrade ideas": acceptable as written, another option only more common (2026-10-29)
PRIORITY_COUNT = 3
PRIORITY_IDS = (2, 5)
VOCAB_RANGE = (10, 12)
CHECKLIST_RANGE = (5, 6)

SCORE_KEYS = ("TR_or_TA", "CC", "LR", "GRA")
CRITERION_NAMES = {
    "TR_or_TA": {"task1": "Task Achievement", "task2": "Task Response"},
    "CC": "Coherence & Cohesion",
    "LR": "Lexical Resource",
    "GRA": "Grammatical Range & Accuracy",
}
# Mistake / priority tag: label + CSS class.
TAGS = {"LR": ("Lexical", "lr"), "GRA": ("Grammar", "gra"), "CC": ("Coherence", "cc"), "TR": ("Task", "task")}
# db criteria_scores keys (kept stable for history/statistics).
DB_KEYS = {"TR_or_TA": "TA", "CC": "CC", "LR": "LR", "GRA": "GRA"}

COPY_WHY = (
    "This is copied word for word from the question. Examiners do not count copied words, so "
    "paraphrase the question in your own words."
)

_CANON = str.maketrans({"’": "'", "‘": "'", "`": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})
WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*")
# Counting words the way an examiner does: "$40,000", "3.5" and "15-20" are one word each.
COUNT_RE = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+|[.,]\d+)*")
_QUOTES = "\"“”‘’'"


def canon(text: str) -> str:
    """Whitespace- and quote/dash-normalised form used only for COMPARING texts."""
    return re.sub(r"\s+", " ", text.translate(_CANON)).strip()


PASTE_NOTICE = "Page headers/footers from pasted text were ignored."
_PAGE_MARK = re.compile(r"\bpage\s+\d+\s*(?:of|/)\s*\d+\b", re.IGNORECASE)
# a running footer inside a line: "Ilhom Alijanov | IELTS Task 1: 9.0 | Page 3 of 154" (#106 had it
# mid-sentence: it was marked as a grammar mistake and the name reached the report)
_FOOTER = re.compile(r"\s*(?:[A-Z][\w'’.-]*\s+){0,3}[A-Z][\w'’.-]*\s*\|[^|\n]{0,60}\|\s*(?i:page)\s+\d+\s*"
                     r"(?:(?i:of)|/)\s*\d+\s*")
_PAGE_INLINE = re.compile(r"\s*\|\s*(?i:page)\s+\d+\s*(?:(?i:of)|/)\s*\d+\s*\|?\s*|\s*\|?\s*(?i:page)\s+\d+\s*"
                          r"(?:(?i:of)|/)\s*\d+\s*\|\s*")
_SENTENCE_END = re.compile(r"[.!?…:;\"”’')\]]\s*$")


def clean_pasted(text: str) -> tuple[str, bool]:
    """(the text, whether page headers/footers were left out) - text pasted
    from a PDF (the academic lead, 2026-10-27: #53-#59 came in with every
    printed line a "paragraph" and "Ilhom Alijanov | IELTS Task 1: 9.0 | Page
    3 of 154" in the middle of a sentence): a line that ends mid-sentence
    joins the next one, a word hyphenated at a line end is joined up, blank
    lines collapse, and a "Page N of M" line, a running footer inside a line
    ("Name | IELTS ... | Page N of M") and a short line that comes back word
    for word all go. Text typed with one paragraph per line (Telegram) comes
    out the same."""
    kept, removed = [], False
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # a short line that comes back word for word (a running header with the author's name)
    seen: dict[str, int] = {}
    for line in lines:
        key = line.strip()
        if key and len(key.split()) <= 8 and not _SENTENCE_END.search(key):
            seen[key] = seen.get(key, 0) + 1
    for line in lines:
        if _PAGE_MARK.search(line) and len(line.split()) <= 14 or seen.get(line.strip(), 0) >= 2:
            removed = True
            continue
        clean = _PAGE_INLINE.sub(" ", _FOOTER.sub(" ", line))
        if clean != line:
            removed = True
            line = re.sub(r"[ \t]{2,}", " ", clean)
        kept.append(line.strip())
    paragraphs, current = [], ""
    for line in kept:
        if not line:
            if current and _SENTENCE_END.search(current):
                paragraphs.append(current)
                current = ""
            continue
        if not current:
            current = line
        elif current.endswith("-") and line[:1].islower():
            current += line
        elif _SENTENCE_END.search(current):
            paragraphs.append(current)
            current = line
        else:
            current = f"{current} {line}"
    if current:
        paragraphs.append(current)
    return "\n".join(paragraphs), removed


def count_words(text: str) -> int:
    return len(COUNT_RE.findall(text))


def paragraph_count(text: str) -> int:
    return len(_paragraph_ranges(text))


# --- Copied from the question --------------------------------------------------------------


def _word_key(word: str) -> str:
    return word.lower().replace("’", "'")


def copy_min_words(task_type: str) -> int:
    return COPY_MIN_WORDS_TASK1 if task_type == "task1" else COPY_MIN_WORDS


def find_copied_spans(essay: str, question: str, min_words: int = COPY_MIN_WORDS) -> list[tuple[int, int]]:
    """Character ranges of the essay that repeat `min_words` or more consecutive
    words of the question (case and punctuation ignored)."""
    words = [(_word_key(m.group()), m.start(), m.end()) for m in WORD_RE.finditer(essay)]
    q = [_word_key(w) for w in WORD_RE.findall(question)]
    grams = {tuple(q[i : i + min_words]) for i in range(len(q) - min_words + 1)}
    copied = [False] * len(words)
    for i in range(len(words) - min_words + 1):
        if tuple(w for w, _, _ in words[i : i + min_words]) in grams:
            copied[i : i + min_words] = [True] * min_words
    spans = []
    i = 0
    while i < len(words):
        if not copied[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(words) and copied[j + 1]:
            j += 1
        start, end = words[i][1], words[j][2]
        if essay[end : end + 1] in (".", "!", "?"):  # the copied sentence's full stop
            end += 1
        spans.append((start, end))
        i = j + 1
    return spans


# --- Locating text in the original ------------------------------------------------------------


MIN_ANCHORED_SHARE = 0.8  # below this the caller asks the model once more
MIN_USABLE_SHARE = 0.5  # below this the essay is shown without the model's marks


@dataclass
class Span:
    start: int
    end: int
    type: str  # "good" | "error"
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
    order. A plain text segment only moves the search on when it matches
    right where the previous segment ended, so a short mark like "the" lands
    on the right "the" wherever the model's copy is exact, and an altered
    copy can't push the search past later marks."""
    text, index = _canon_index(original)
    pos, spans, marked = 0, [], 0
    for seg in (s for paragraph in essay for s in paragraph):
        target = canon(seg.text)
        if not target:
            continue
        if seg.type not in ("good", "error"):
            k = text.find(target, pos)
            if k != -1 and k - pos <= 2:
                pos = k + len(target)
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


def locate(original: str, needle: str, near: int = 0) -> tuple[int, int] | None:
    """Where `needle` occurs in `original` (whitespace/quote-insensitive, then
    case-insensitive), the occurrence closest to `near`."""
    target = canon(needle)
    if not target:
        return None
    text, index = _canon_index(original)
    for hay, tgt in ((text, target), (text.lower(), target.lower())):
        if len(hay) != len(text):
            continue
        hits = []
        k = _find_word_aligned(hay, tgt, 0)
        while k != -1:
            hits.append(k)
            k = _find_word_aligned(hay, tgt, k + 1)
        if hits:
            k = min(hits, key=lambda h: abs(index[h] - near))
            return index[k], index[k + len(tgt) - 1] + 1
    return None


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text.translate(_CANON))


def essay_matches(original: str, essay: list[list[EssaySegment]]) -> bool:
    """Do the model's segments join back into the original (whitespace-normalised)?"""
    return _squash("".join(seg.text for paragraph in essay for seg in paragraph)) == _squash(original)


# --- Checks shared by `assess` and `build_report` --------------------------------------------


def _shared_run(a: str, b: str, n: int = 5) -> bool:
    """Do two texts share a run of n words (or is the shorter one inside the other)?"""
    wa, wb = [_word_key(w) for w in WORD_RE.findall(a)], [_word_key(w) for w in WORD_RE.findall(b)]
    if not wa or not wb:
        return False
    short, long_ = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    if len(short) < n:
        return " ".join(short) in " ".join(long_)
    grams = {tuple(long_[i : i + n]) for i in range(len(long_) - n + 1)}
    return any(tuple(short[i : i + n]) in grams for i in range(len(short) - n + 1))


def _copy_mistake_index(copied_text: str, mistakes: list) -> int | None:
    """Index of the Task mistake the model wrote for a copied span, if any."""
    for i, m in enumerate(mistakes):
        if m.criterion == "TR" and _shared_run(m.wrong, copied_text):
            return i
    return None


def _double_words(text: str) -> set[str]:
    return {m.group(1).lower() for m in re.finditer(r"\b([A-Za-z']+)\s+\1\b", text, re.IGNORECASE)}


def creates_double(original: str, start: int, end: int, replacement: str) -> bool:
    """Would putting `replacement` in place of original[start:end] create a
    doubled word ("that that", "the the") that wasn't there before?"""
    before, after = original[max(0, start - 60) : start], original[end : end + 60]
    new = canon(before + replacement + after)
    old = canon(before + original[start:end] + after)
    return bool(_double_words(new) - _double_words(old))


def assess(fb: WritingFeedback, original: str) -> list[str]:
    """Problems worth paying for one more model call: the marks can't be
    placed on the essay, or a section of the report would be short. Smaller
    faults (an essay copy that differs slightly, a priority with one mistake,
    praise without a quote, copied text without its Task mistake) are
    repaired in build_report and only noted - each retry costs as much as the
    first call (owner's decision, 2026-10-13)."""
    problems = []
    if not essay_matches(original, fb.essay):
        share = align_segments(original, fb.essay).share
        if share < MIN_ANCHORED_SHARE:
            problems.append(f"only {share:.0%} of the marks can be placed on the essay")
    if len(fb.vocabulary) < VOCAB_RANGE[0]:
        problems.append(f"{len(fb.vocabulary)} vocabulary items")
    if len(fb.checklist) < CHECKLIST_RANGE[0]:
        problems.append(f"{len(fb.checklist)} checklist lines")
    return problems


# --- Formatting helpers ---------------------------------------------------------------------------


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def inline_format(text: str) -> str:
    """Escapes model text, then turns **bold** and *italic* into tags - the
    only markup the model may use."""
    s = _esc(text)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    return re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", s)


def _inside(phrase: str, text: str) -> bool:
    """Whether a (canon, lower-case) phrase is in a text as whole words - a
    one-word phrase like "art" is not inside "start"."""
    return bool(phrase) and re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def phrases_in_goods(quoted: list[str], goods: list[str]) -> list[str]:
    """The strength's quoted phrases (single words too) that are green
    highlights or inside one."""
    out = []
    for phrase in quoted:
        core = canon(phrase).strip(_QUOTES + " .,").lower()
        if any(_inside(core, canon(g).lower()) for g in goods):
            out.append(phrase.strip().strip(_QUOTES + " .,"))
    return out


def _flexible(phrase: str) -> str:
    """A regex for an (HTML-escaped) phrase that matches it with any kind of
    apostrophe or dash and any run of spaces - #41-#43's second quoted phrase
    ("others' views") was never bolded."""
    def char(ch: str) -> str:
        if ch in "'’‘":
            return "['’‘]"
        return "[-–—]" if ch in "-–—" else re.escape(ch)

    return r"\s+".join("".join(char(ch) for ch in word) for word in phrase.split())


def strength_html(text: str, phrases: list[str], goods: list[str]) -> str:
    """The praise with the validated phrases in bold quotes. Any other
    quotation loses its quote marks - "What you did well" only ever quotes
    the green strong phrases (validation rule 8); if it quotes none of
    them, the first two are named."""
    s = _esc(text.strip())
    shown = 0
    for phrase in phrases:
        # a full stop or comma the model put inside the closing quote goes after it
        pattern = re.compile(f"[{_QUOTES}]?({_flexible(_esc(phrase))})([.,;:!?]?)[{_QUOTES}]?", re.IGNORECASE)
        s, hits = pattern.subn(lambda m: f"<b>“{m.group(1)}”</b>{m.group(2)}", s, count=1)
        shown += hits
    green = [canon(g).lower() for g in goods]

    def other_quote(m: re.Match) -> str:
        # a quotation that is part of a green phrase stays a quotation
        nonlocal shown
        inner = m.group(1)
        if inner.strip() and any(_inside(canon(html.unescape(inner)).lower(), g) for g in green):
            shown += 1
            return f"<b>“{inner}”</b>"
        return inner

    s = re.sub(r"(?<!<b>)[“\"]([^“”\"<]+)[”\"]", other_quote, s)
    if not shown and goods:
        s += " Phrases like " + " and ".join(f"<b>“{_esc(g)}”</b>" for g in goods[:2]) + " show this."
    return s


def _changed(old: str, new: str) -> str:
    """One changed place of an inline fix, wrapped in the amber mark."""
    if old and new and len(old.split()) == 1 == len(new.split()):
        if new.startswith(old) and not any(c.isalnum() for c in new[len(old):]):
            return f'{_esc(old)}<span class="err"><ins>{_esc(new[len(old):])}</ins></span>'
        if old.startswith(new) and not any(c.isalnum() for c in old[len(new):]):
            return f'{_esc(new)}<span class="err"><s>{_esc(old[len(new):])}</s></span>'
    parts = []
    if old:
        parts.append(f"<s>{_esc(old)}</s>")
    if new:
        parts.append(f"<ins>{_esc(new)}</ins>")
    return f'<span class="err">{" ".join(parts)}</span>'


def _punctuation_only(old: list[str], new: list[str]) -> bool:
    strip = lambda words: [re.sub(r"[^\w'-]", "", x) for x in words]
    return len(old) == len(new) == 1 and strip(old) == strip(new)


def fix_is_inline(wrong: str, fix: str) -> bool:
    """Short fixes, and fixes that only add or remove punctuation, are shown
    inside the essay; longer problems are only underlined."""
    if len(fix.split()) <= INLINE_FIX_MAX_WORDS:
        return True
    w, f = wrong.split(), fix.split()
    matcher = difflib.SequenceMatcher(None, [canon(x) for x in w], [canon(x) for x in f], autojunk=False)
    return all(
        op == "equal" or _punctuation_only(w[i1:i2], f[j1:j2]) for op, i1, i2, j1, j2 in matcher.get_opcodes()
    )


def inline_fix_html(wrong: str, fix: str) -> str:
    """The amber mark with the fix shown inline: one change marks just that
    place ('leading car' -> leading <ins>to</ins> car), several commas are
    each inserted where they go, anything else is struck through as a whole
    and replaced ('make customers informed' -> 'inform customers')."""
    w, f = wrong.split(), fix.split()
    matcher = difflib.SequenceMatcher(None, [canon(x) for x in w], [canon(x) for x in f], autojunk=False)
    ops = matcher.get_opcodes()
    changes = [op for op in ops if op[0] != "equal"]
    if not changes:
        return f'<span class="err">{_esc(wrong)}</span>'
    if len(changes) > 1 and not all(_punctuation_only(w[i1:i2], f[j1:j2]) for _, i1, i2, j1, j2 in changes):
        pre = 0
        while pre < min(len(w), len(f)) and canon(w[pre]) == canon(f[pre]):
            pre += 1
        suf = 0
        while suf < min(len(w), len(f)) - pre and canon(w[-1 - suf]) == canon(f[-1 - suf]):
            suf += 1
        ops = [("equal", 0, pre, 0, pre), ("replace", pre, len(w) - suf, pre, len(f) - suf),
               ("equal", len(w) - suf, len(w), len(f) - suf, len(f))]
    out = []
    for op, i1, i2, j1, j2 in ops:
        if op == "equal":
            if i2 > i1:
                out.append(_esc(" ".join(w[i1:i2])))
        else:
            out.append(_changed(" ".join(w[i1:i2]), " ".join(f[j1:j2])))
    return " ".join(out)


# --- Report assembly ------------------------------------------------------------------------------


@dataclass(eq=False)
class _Mistake:
    wrong: str
    correct: str
    why: str
    criterion: str
    unclear: bool
    if_you_meant: str | None
    model_id: int | None = None
    copy: bool = False
    pos: float = math.inf
    n: int = 0


@dataclass(eq=False)
class _Mark:
    start: int
    end: int
    kind: str  # "good" | "error" | "copy"
    fix: str | None = None
    mistake: _Mistake | None = None


def _overlaps(start: int, end: int, marks: list[_Mark]) -> bool:
    return any(start < k.end and k.start < end for k in marks)


def _paragraph_ranges(text: str) -> list[tuple[int, int]]:
    """Each line of the original is a paragraph (students often separate
    paragraphs with a single newline in Telegram)."""
    return [(m.start(), m.end()) for m in re.finditer(r"[^\n]+", text) if m.group().strip()]


def render_essay(original: str, marks: list[_Mark]) -> list[str]:
    """One HTML string per paragraph. Text always comes from `original`."""
    paragraphs = []
    for p_start, p_end in _paragraph_ranges(original):
        out, pos = [], p_start
        for k in marks:
            start, end = max(k.start, p_start), min(k.end, p_end)
            if start >= end or start < pos:
                continue
            out.append(_esc(original[pos:start]))
            piece = original[start:end]
            if k.kind == "good":
                out.append(f'<span class="good">{_esc(piece)}</span>')
            elif k.kind == "copy":
                out.append(f'<span class="copy">{_esc(piece)}</span>')
            elif (start, end) == (k.start, k.end) and k.fix is not None:
                out.append(inline_fix_html(piece, k.fix))
            else:
                out.append(f'<span class="err">{_esc(piece)}</span>')
            pos = end
        out.append(_esc(original[pos:p_end]))
        paragraphs.append("".join(out))
    return paragraphs


@dataclass
class WritingReport:
    task_type: str  # "task1" | "task2"
    question: str
    overall_band: float
    target_band: float
    criteria: list[dict]  # key, name, band, note, chip, cls
    stats: dict  # words, paragraphs, fixes, strong
    strength_html: str
    priorities: list[dict]  # n, title, rule_html, ex_wrong, ex_right, tags, ids
    essay_paragraphs: list[str]  # rendered HTML
    essay_marked: bool
    has_copy: bool
    mistakes: list[dict]  # n, wrong, correct, why, why_html, criterion, tag, tag_cls, unclear, if_you_meant
    vocabulary: list[VocabEntry]
    checklist: list[dict]  # html, ref
    word_count: int
    notes: list[str] = field(default_factory=list)  # what validation repaired (logged)
    notices: list[str] = field(default_factory=list)  # shown to the student (e.g. the chart wasn't checked)
    upgrades: list[dict] = field(default_factory=list)  # wrong, correct, why_html, tag, tag_cls - never mistakes

    @property
    def top_band(self) -> bool:
        """At Band 9 there is no next target: the priorities are optional polish."""
        return self.overall_band >= 9.0

    def criteria_scores(self) -> dict[str, float]:
        return {DB_KEYS[c["key"]]: c["band"] for c in self.criteria}

    def mistakes_json(self) -> list[dict]:
        """Stored in writing_submissions.annotations - the progress page
        compares their per-criterion counts with the next essay."""
        keys = ("n", "wrong", "correct", "why", "criterion", "unclear", "if_you_meant")
        return [{k: m[k] for k in keys} for m in self.mistakes]

    def mistake_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for m in self.mistakes:
            counts[m["criterion"]] = counts.get(m["criterion"], 0) + 1
        return counts


def _from_model(m, criterion: str | None = None) -> _Mistake:
    return _Mistake(
        wrong=m.wrong.strip(),
        correct=m.correct.strip(),
        why=m.why.strip(),
        criterion=criterion or m.criterion,
        unclear=bool(m.unclear and m.if_you_meant),
        if_you_meant=m.if_you_meant.strip() if m.unclear and m.if_you_meant else None,
        model_id=getattr(m, "id", None),
    )


def _is_noop(m) -> bool:
    return not m.unclear and canon(m.wrong).lower() == canon(m.correct).lower()


def _repair_doubles(original: str, m: _Mistake, near: int, notes: list[str]) -> None:
    """If `correct` put in place of `wrong` doubles a word at either edge
    ("argue that" + "that street advertising..."), the doubled word is moved
    into `wrong` so the pair reads correctly."""
    loc = locate(original, m.wrong, near)
    if loc is None or not m.correct:
        return
    start, end = loc
    if not creates_double(original, start, end, m.correct):
        return
    first, last = m.correct.split()[0], m.correct.split()[-1]
    before = re.search(r"([A-Za-z']+)(\s+)$", original[:start])
    after = re.match(r"(\s+)([A-Za-z']+)", original[end:])
    if before and before.group(1).lower() == first.lower():
        start = before.start(1)
    elif after and after.group(2).lower() == last.lower():
        end = end + after.end(2)
    if creates_double(original, start, end, m.correct):
        notes.append(f"correction still doubles a word: {m.wrong!r} -> {m.correct!r}")
        return
    m.wrong = original[start:end]


def _ref(ref: str, by_model_id: dict[int, _Mistake], priorities: int, gone: set[int]) -> str | None:
    """A checklist line's reference after the renumbering (mistakes in essay
    order); None when it points to a mistake that was dropped - the line goes
    then (#41's checklist repeated a dropped "mistake": "renewed interest
    takes no article"). A reference to nothing at all is just left off."""
    p = re.search(r"priority\D*(\d+)", ref, re.IGNORECASE)
    if p and 1 <= int(p.group(1)) <= priorities:
        return f"Priority {int(p.group(1)):02d}"
    m = re.search(r"mistake\D*(\d+)", ref, re.IGNORECASE)
    if m and int(m.group(1)) in by_model_id:
        return f"Mistake {by_model_id[int(m.group(1))].n}"
    if m and int(m.group(1)) in gone:
        return None
    return ""


def _usable(p, gone: set[int], kept: list[str]) -> bool:
    if p.type == "tip" or not p.mistake_ids:
        return True
    if all(x in gone for x in p.mistake_ids):
        return False
    example = canon(p.example_wrong.strip(" .…" + _QUOTES)).lower()
    return not example or any(example in w or w in example for w in kept)


def drop_priorities(fb: WritingFeedback, gone: set[int], kept_wrongs: list[str]) -> tuple[WritingFeedback, list[str]]:
    """The feedback with only the priorities still true after the mistake
    filter (the last prompt's B2-B3): a priority built on mistakes goes when
    all of them were dropped, or when its example is not inside a mistake that
    was kept (#41's Priority 01 was "a renewed interest" - correct English). A
    next-band tip, or a priority with no mistakes of its own, stays. The rest
    are numbered 01, 02...; checklist lines follow their priority's new number,
    and a line about a dropped priority goes. `gone`: ids of mistakes not kept."""
    kept = [canon(w).lower() for w in kept_wrongs if w.strip()]
    priorities, numbers, notes = [], {}, []
    for i, p in enumerate(fb.priorities[:PRIORITY_COUNT], 1):
        if _usable(p, gone, kept):
            numbers[i] = len(priorities) + 1
            priorities.append(p)
        else:
            notes.append(f"priority {i} dropped - its mistakes were not kept: {p.title!r}")
    if len(priorities) == len(fb.priorities):
        return fb, notes
    checklist = []
    for item in fb.checklist:
        ref = re.search(r"priority\D*(\d+)", item.ref, re.IGNORECASE)
        if ref and int(ref.group(1)) in numbers:
            item = item.model_copy(update={"ref": f"Priority {numbers[int(ref.group(1))]:02d}"})
        elif ref and int(ref.group(1)) <= len(fb.priorities):
            notes.append(f"checklist line dropped with its priority: {item.text!r}")
            continue
        checklist.append(item)
    return fb.model_copy(update={"priorities": priorities, "checklist": checklist}), notes


def without_priorities(fb: WritingFeedback, drop: set[int]) -> WritingFeedback:
    """The feedback without the priorities numbered in `drop` (1-based); the
    rest numbered again, their checklist lines following, the dropped ones'
    lines gone."""
    numbers, kept = {}, []
    for i, p in enumerate(fb.priorities, 1):
        if i not in drop:
            numbers[i] = len(kept) + 1
            kept.append(p)
    checklist = []
    for item in fb.checklist:
        ref = re.search(r"priority\D*(\d+)", item.ref, re.IGNORECASE)
        if ref and int(ref.group(1)) in drop:
            continue
        if ref and int(ref.group(1)) in numbers:
            item = item.model_copy(update={"ref": f"Priority {numbers[int(ref.group(1))]:02d}"})
        checklist.append(item)
    return fb.model_copy(update={"priorities": kept, "checklist": checklist})


_CONTENT = re.compile(r"[a-z]{4,}")
_FILLER_WORDS = {"your", "with", "that", "this", "each", "every", "when", "make", "sure", "before", "write",
                 "writing", "always", "check", "next", "time", "report", "essay", "answer", "clear", "clearly",
                 "keep", "use", "using", "from", "into", "than", "them", "they", "their", "have"}


def fits(line: str, about: str) -> bool:
    """Does a checklist line share a real word with what it refers to (a
    priority, a mistake)? "Keep units consistent" under a priority about
    group comparisons does not (2026-10-29)."""
    words = {w for w in _CONTENT.findall(line.lower()) if w not in _FILLER_WORDS}
    target = {w for w in _CONTENT.findall(about.lower()) if w not in _FILLER_WORDS}
    stems = {w[:5] for w in target}
    return bool(words & target) or any(w[:5] in stems for w in words)


TASK_MIN_WORDS = {"task1": 150, "task2": 250}


def task_first(fb: WritingFeedback, bands: dict[str, float], words: int, task_type: str) -> tuple[WritingFeedback, list[str]]:
    """A Task problem that holds the band down comes first (the academic
    lead, 2026-10-28: #60, a 124-word report leaving out half the chart, had
    "Hyphenate age ranges" as priority 01). Under the task's word minimum, or
    with Task Achievement / Response as the lowest band, the Task priorities
    lead; under the minimum with none, a tip about the length comes first.
    Checklist lines follow their priority's new number."""
    if task_type != "task1":  # the ruling was on Task 1 reports
        return fb, []
    priorities = list(fb.priorities[:PRIORITY_COUNT])
    minimum = TASK_MIN_WORDS.get(task_type, 0)
    short = words < minimum
    lowest = bands["TR_or_TA"] <= min(bands[k] for k in SCORE_KEYS if k != "TR_or_TA")
    if not (short or lowest):
        return fb, []
    order = sorted(range(len(priorities)), key=lambda i: (0 if "TR" in priorities[i].criteria else 1, i))
    new = [priorities[i] for i in order]
    notes = []
    if short and not any("TR" in p.criteria for p in new):
        from services.ai.schemas import Priority

        new.insert(0, Priority(
            type="tip", title=f"Write at least {minimum} words",
            rule=f"Your answer has {words} words: cover every part of the task in {minimum} words or more.",
            example_wrong="", criteria=["TR"], mistake_ids=[],
            example_right="Describe every group the visual shows, with figures, in one paragraph each."
            if task_type == "task1" else "Develop each main idea with an explanation and an example."))
        notes.append(f"a length tip added first ({words} words)")
        order.insert(0, -1)
    new, order = new[:PRIORITY_COUNT], order[:PRIORITY_COUNT]
    if [p.title for p in new] == [p.title for p in priorities]:
        return fb, notes
    numbers = {old + 1: n for n, old in enumerate(order, 1) if old >= 0}  # old number -> new number
    checklist = []
    for item in fb.checklist:
        ref = re.search(r"priority\D*(\d+)", item.ref, re.IGNORECASE)
        if ref is None:
            checklist.append(item)
        elif int(ref.group(1)) in numbers:
            checklist.append(item.model_copy(update={"ref": f"Priority {numbers[int(ref.group(1))]:02d}"}))
        else:
            notes.append(f"checklist line dropped with its priority: {item.text!r}")
    notes.append("Task priorities first: " + ", ".join(repr(p.title) for p in new))
    return fb.model_copy(update={"priorities": new, "checklist": checklist}), notes


def build_report(
    task_type: str,
    question: str,
    original: str,
    fb: WritingFeedback,
    missed: list[MissedMistake] | tuple = (),
    *,
    bands: dict[str, float],
    dropped: set[int] = frozenset(),
    retag: dict[int, str] | None = None,
    criterion_notes: dict[str, str] | None = None,
    notices: list[str] | None = None,
    upgrades: list[dict] | None = None,
) -> WritingReport:
    """`bands`: the scoring runs' whole criterion bands; `dropped`: ids of
    mistakes the verification pass rejected; `retag`: {id: criterion} for
    mistakes it moved to the criterion they belong to (one problem, one
    criterion); `criterion_notes`: what decided each band (the scoring runs),
    used on the cover instead of the feedback's own notes; `notices`: lines
    shown to the student; `upgrades`: what the filter found acceptable as
    written ({wrong, correct, why, criterion}) - the report's "Upgrade
    ideas", at most UPGRADE_MAX, in the essay's order, never a mistake."""
    notes: list[str] = []
    retag = retag or {}

    mistakes: list[_Mistake] = []
    seen_ids = set()
    for m in fb.mistakes:
        if m.id in dropped:
            notes.append(f"dropped mistake {m.id} (not definitely wrong): {m.wrong!r}")
            continue
        if _is_noop(m) or m.id in seen_ids:
            notes.append(f"dropped mistake {m.id} (no change or repeated id)")
            continue
        seen_ids.add(m.id)
        if retag.get(m.id, m.criterion) != m.criterion:
            notes.append(f"mistake {m.id} moved from {m.criterion} to {retag[m.id]}: {m.wrong!r}")
        mistakes.append(_from_model(m, retag.get(m.id)))

    # Text copied from the question: grey, with its own Task mistake.
    marks: list[_Mark] = []
    for start, end in find_copied_spans(original, question, copy_min_words(task_type)):
        text = original[start:end]
        free = [m for m in mistakes if not m.copy]
        i = _copy_mistake_index(text, free)
        if i is None:
            notes.append("copied text had no Task mistake - added one")
            m = _Mistake(text, "", COPY_WHY, "TR", False, None)
            mistakes.append(m)
        else:
            m = free[i]
            m.wrong = text
        m.copy = True
        marks.append(_Mark(start, end, "copy", mistake=m))

    alignment = align_segments(original, fb.essay)
    marked = alignment.share >= MIN_USABLE_SHARE
    by_model_id = {m.model_id: m for m in mistakes if m.model_id is not None}

    if marked:
        for span in (s for s in alignment.spans if s.type == "error"):
            m = by_model_id.get(span.mistake_id)
            if m is None or m.copy or _overlaps(span.start, span.end, marks):
                continue
            marks.append(_Mark(span.start, span.end, "error", fix=span.fix, mistake=m))
        # Mistakes the model listed but didn't mark: find their text, or drop
        # them. Text already under another mark counts as marked.
        for m in list(mistakes):
            if m.copy or any(k.mistake is m for k in marks):
                continue
            loc = locate(original, m.wrong)
            if loc is None:
                notes.append(f"dropped unmarkable mistake {m.wrong!r}")
                mistakes.remove(m)
            elif _overlaps(*loc, marks):
                m.pos = loc[0]
            else:
                marks.append(_Mark(*loc, "error", fix=m.correct, mistake=m))
    else:
        notes.append(f"essay shown without marks ({alignment.anchored}/{alignment.marked} found)")

    # The second, missed-errors pass.
    for x in missed:
        if _is_noop(x) or any(_shared_run(x.wrong, m.wrong, 3) for m in mistakes):
            continue
        loc = locate(original, x.wrong)
        if loc is None or _overlaps(*loc, marks):
            continue
        m = _from_model(x)
        mistakes.append(m)
        if marked:
            marks.append(_Mark(*loc, "error", fix=m.correct, mistake=m))
        notes.append(f"second pass added {x.wrong!r} -> {x.correct!r}")

    # Strong phrases: never on a mistake or copied text. Rule 8 first: every
    # phrase quoted in "What you did well" that is in the essay and free of
    # mistakes is green (#41-#43's second quoted phrase was not); then the
    # model's other strong phrases, up to MAX_GOOD_HIGHLIGHTS in all.
    if marked:
        wrongs = [canon(m.wrong).lower() for m in mistakes]
        good = []
        for phrase in fb.strength.quoted_phrases:
            core = phrase.strip().strip(_QUOTES + " .,")
            if not core:
                continue
            loc = locate(original, core)
            if loc is None:
                notes.append(f"quoted strength {core!r} is not in the essay")
                continue
            if _overlaps(*loc, marks + good) or any(w and w in canon(core).lower() for w in wrongs):
                continue
            good.append(_Mark(*loc, "good"))
        for span in (s for s in alignment.spans if s.type == "good"):
            text = canon(original[span.start : span.end]).lower()
            if _overlaps(span.start, span.end, marks + good) or any(w and w in text for w in wrongs):
                continue
            if len(good) < MAX_GOOD_HIGHLIGHTS:
                good.append(_Mark(span.start, span.end, "good"))
        marks += good
    marks.sort(key=lambda k: k.start)

    # Inline fixes: 6 words or fewer (or punctuation only), never doubling a word.
    for k in marks:
        if k.kind == "error" and k.fix is not None:
            piece = original[k.start : k.end]
            if not fix_is_inline(piece, k.fix) or creates_double(original, k.start, k.end, k.fix):
                k.fix = None

    # Number the mistakes in essay order.
    for k in marks:
        if k.mistake is not None and k.mistake.pos == math.inf:
            k.mistake.pos = k.start
    for m in mistakes:
        if m.pos == math.inf:
            loc = locate(original, m.wrong)
            m.pos = loc[0] if loc else math.inf
    mistakes.sort(key=lambda m: (m.pos, m.model_id or 10**6))
    for n, m in enumerate(mistakes, 1):
        m.n = n
        if not m.copy:
            _repair_doubles(original, m, 0 if m.pos == math.inf else int(m.pos), notes)
    by_model_id = {m.model_id: m for m in mistakes if m.model_id is not None}

    # Priorities on mistakes that were not kept go (grade_writing already took
    # out those the filter dropped; this catches the unmarkable ones).
    gone = {m.id for m in fb.mistakes} - set(by_model_id)
    fb, dropped_notes = drop_priorities(fb, gone, [m.wrong for m in mistakes])
    notes += dropped_notes
    fb, first_notes = task_first(fb, {k: whole_band(bands[k]) for k in SCORE_KEYS}, count_words(original), task_type)
    notes += first_notes
    priorities = []
    for i, p in enumerate(fb.priorities[:PRIORITY_COUNT], 1):
        tags = [c for c in dict.fromkeys(p.criteria) if c in TAGS]
        tip = p.type == "tip"
        chosen = {x: by_model_id[x] for x in p.mistake_ids if x in by_model_id and by_model_id[x].criterion in tags}
        ids = [] if tip else sorted(chosen.values(), key=lambda m: m.n)[: PRIORITY_IDS[1]]
        used = [c for c in tags if any(m.criterion == c for m in ids)] or tags
        if p.mistake_ids and not tip and len(ids) < PRIORITY_IDS[0]:
            notes.append(f"priority {i} has {len(ids)} matching mistakes")
        if len(used) == 1 and len(ids) >= 3 and len(ids) == sum(m.criterion == used[0] for m in mistakes):
            notes.append(f"priority {i} lists every {used[0]} mistake")
        if not tip and p.example_wrong.strip() and locate(original, p.example_wrong.strip(" .…")) is None:
            notes.append(f"priority {i} example is not from the essay: {p.example_wrong!r}")
        priorities.append(
            {
                "n": f"{i:02d}",
                "title": p.title.strip(),
                "rule_html": inline_format(p.rule.strip()),
                # a next-band tip shows its rule and a good example only
                "ex_wrong": "" if tip else p.example_wrong.strip(),
                "ex_right": p.example_right.strip(),
                "tags": [TAGS[c] for c in used],
                "ids": [m.n for m in ids],
                "tip": tip,
            }
        )

    checklist = []
    for item in fb.checklist[: CHECKLIST_RANGE[1]]:
        ref = _ref(item.ref, by_model_id, len(priorities), gone | set(dropped))
        if ref is None:
            notes.append(f"checklist line dropped with its mistake ({item.ref}): {item.text!r}")
            continue
        number = re.search(r"(\d+)", ref)
        about = ""
        if ref.startswith("Priority") and number and int(number.group(1)) <= len(fb.priorities):
            p = fb.priorities[int(number.group(1)) - 1]
            about = f"{p.title} {p.rule} {p.example_wrong} {p.example_right}"
        elif ref.startswith("Mistake") and number:
            m = next((x for x in mistakes if x.n == int(number.group(1))), None)
            about = f"{m.wrong} {m.correct} {m.why}" if m else ""
        if about and not fits(item.text, about):
            notes.append(f"checklist line dropped - it doesn't match {ref}: {item.text!r}")
            continue
        checklist.append({"html": inline_format(item.text.strip()), "ref": ref})

    bands = {key: whole_band(bands[key]) for key in SCORE_KEYS}
    overall = overall_band(list(bands.values()))
    top = max(bands.values())
    single_top = list(bands.values()).count(top) == 1
    criteria = []
    for key, band in bands.items():
        if band > overall:
            chip, cls = ("Strongest" if band == top and single_top else "Strong"), "top"
        elif band == overall:
            chip, cls = "On track", "ok"
        else:
            chip, cls = "Focus", "focus"
        name = CRITERION_NAMES[key][task_type] if isinstance(CRITERION_NAMES[key], dict) else CRITERION_NAMES[key]
        note = (criterion_notes or {}).get(key) or getattr(fb.criterion_notes, key).strip()
        criteria.append({"key": key, "name": name, "band": band, "note": note, "chip": chip, "cls": cls})

    goods = [original[k.start:k.end] for k in marks if k.kind == "good"]
    phrases = phrases_in_goods(fb.strength.quoted_phrases, goods)
    if not phrases:
        notes.append("strength quotes no green phrase")
    mistake_rows = []
    for m in mistakes:
        tag, tag_cls = TAGS[m.criterion]
        mistake_rows.append(
            {
                "n": m.n, "wrong": m.wrong, "correct": m.correct, "why": m.why, "why_html": inline_format(m.why),
                "criterion": m.criterion, "tag": tag, "tag_cls": tag_cls,
                "unclear": m.unclear, "if_you_meant": m.if_you_meant,
            }
        )

    upgrade_rows, seen = [], set()
    placed = sorted(((locate(original, u["wrong"].strip(" .…")) or (len(original), 0))[0], k, u)
                    for k, u in enumerate(upgrades or []) if u.get("wrong", "").strip() and u.get("correct", "").strip())
    for _, _, u in placed:
        key = canon(u["wrong"]).lower().strip(" .,")
        if key in seen or any(_shared_run(u["wrong"], m.wrong) for m in mistakes):
            continue
        seen.add(key)
        tag, tag_cls = TAGS.get(u.get("criterion"), TAGS["LR"])
        upgrade_rows.append({"wrong": u["wrong"].strip(), "correct": u["correct"].strip(),
                             "why_html": inline_format((u.get("why") or "").strip()), "tag": tag, "tag_cls": tag_cls})
        if len(upgrade_rows) == UPGRADE_MAX:
            break

    return WritingReport(
        task_type=task_type,
        question=question,
        overall_band=overall,
        target_band=target_band(overall),
        criteria=criteria,
        stats={
            "words": count_words(original),
            "paragraphs": len(_paragraph_ranges(original)),
            "fixes": len(mistakes),
            "strong": sum(k.kind == "good" for k in marks),
        },
        strength_html=strength_html(fb.strength.text, phrases, goods),
        priorities=priorities,
        essay_paragraphs=render_essay(original, marks),
        essay_marked=marked,
        has_copy=any(k.kind == "copy" for k in marks),
        mistakes=mistake_rows,
        vocabulary=fb.vocabulary[: VOCAB_RANGE[1]],
        checklist=checklist,
        word_count=count_words(original),
        notes=notes,
        notices=list(dict.fromkeys(notices or [])),
        upgrades=upgrade_rows,
    )
