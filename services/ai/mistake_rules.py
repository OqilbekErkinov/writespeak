"""What code decides about a flagged mistake, whatever the model's filter says
(the academic lead, 2026-10-29 and 2026-10-31).

An ERROR is ungrammatical, has the wrong meaning or confuses the reader;
"another phrase is more common" is an ALTERNATIVE, always. The rules here hold
for Writing and Speaking: the lead's lists (config/writing_marking.json
"accepted", "praised", "real_mistakes"), a synonym swap, British and American
spelling, a correction that only adds a detail, scan artefacts in a text that
came from a photo or a PDF, and a correction that shows a slip or an error by
what it changes.
"""
from __future__ import annotations

import difflib
import re
from types import SimpleNamespace

from services.ai import marking_config
from services.ai.essay_marking import canon


def _tokens(text: str) -> list[str]:
    return re.findall(r"[\w'$%]+", canon(text).lower())


def _edits(m) -> list[tuple]:
    """A correction's changes: (words taken out, words put in, the word after them)."""
    a, b = _tokens(m.wrong), _tokens(m.correct)
    return [(tuple(a[i1:i2]), tuple(b[j1:j2]), a[i2] if i2 < len(a) else "")
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
            if tag != "equal"]


def _change(m) -> tuple:
    """What a correction changes first ("Czech Republic" -> "the Czech
    Republic" and "Italy, Czech Republic" -> "Italy, the Czech Republic"
    make one change)."""
    return (_edits(m) or [((), (), "")])[0]


def only_adds(wrong: str, correct: str) -> bool:
    """Does the correction keep the student's words as they are and only add
    to them (two words or more)?"""
    a, b = _tokens(wrong), _tokens(correct)
    if not a or len(b) < len(a) + 2:
        return False
    return any(b[i:i + len(a)] == a for i in range(len(b) - len(a) + 1))


def only_adds_comma(wrong: str, correct: str) -> bool:
    """Does the correction only add commas ("First of all money" -> "First of
    all, money")? An examiner passes over that: a Band 7 script of a test book
    had 5 of them counted as Grammar errors and came out 6."""
    w, c = canon(wrong), canon(correct)
    return c.count(",") > w.count(",") and w.replace(",", "").split() == c.replace(",", "").split()


def _matches(phrases: list[str], wrong: str, correct: str | None = None) -> str | None:
    """The listed phrase a flag is about: it is in the flagged words, or the
    flagged words are a part of it - and, given the correction, the correction
    changes that phrase ("the football seemed to have thrived" -> "football
    seemed to have thrived" is about "the football", not the phrase)."""
    low = canon(wrong).lower().strip(" .,")
    for phrase in phrases:
        p = canon(phrase).lower().strip(" .,")
        if not p:
            continue
        if len(low) >= 12 and low in p:
            return phrase
        if p not in low:
            continue
        if correct is None:
            return phrase
        a, b, want = _tokens(wrong), _tokens(correct), _tokens(phrase)
        start = next((i for i in range(len(a) - len(want) + 1) if a[i:i + len(want)] == want), None)
        if start is None:
            return phrase
        end = start + len(want)
        ops = [op for op in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op[0] != "equal"]
        if not ops or any((i1 < end and i2 > start) if i1 != i2 else start < i1 < end for _, i1, i2, _, _ in ops):
            return phrase
    return None


def lead_accepted(wrong: str, correct: str | None = None) -> str | None:
    """The wording the academic lead accepted (config "accepted") that a flag
    is about: an upgrade idea at most, never a mistake."""
    return _matches(marking_config.accepted_phrases(), wrong, correct)


def praised(wrong: str, correct: str | None = None) -> str | None:
    """A phrase examiners praise in official samples or Band 9 books (config
    "praised"): never flagged, not even as an upgrade idea."""
    return _matches(marking_config.praised_phrases(), wrong, correct)


def real_mistake(wrong: str) -> str | None:
    """The lead's list of real mistakes (config "real_mistakes"): a slip at
    least, whatever the filter says."""
    return _matches(marking_config.real_mistakes(), wrong)


# British and American spelling: both right, so a correction that only moves between them is no correction.
_SPELLING = [
    (r"\b(colo|favo|labo|hono|neighbo|behavio|harbo|flavo|rumo|humo)ur", r"\1r"),
    (r"\b(travel|label|cancel|model|level|signal|fuel|dial|counsel|marvel|quarrel|equal|total)l(ed|ing|er|ers)\b", r"\1\2"),
    (r"\b(cen|thea|me|li|fib|calib|somb|meag)(?:tre|bre|gre)(s?)\b", r"\1\2"),
    (r"\b(cen|thea|me|li)ter(s?)\b", r"\1\2"), (r"\b(fib|calib|somb)er(s?)\b", r"\1\2"), (r"\bmeager\b", "meag"),
    (r"(\w{4,})is(e|ed|es|ing|ation|ations)\b", r"\1iz\2"), (r"(\w{3,})ys(e|ed|es|ing)\b", r"\1yz\2"),
    (r"\bstoreys\b", "stories"), (r"\bstorey\b", "story"), (r"\bprogrammes?\b", "program"), (r"\bgrey\b", "gray"),
    (r"\btyres?\b", "tire"), (r"\bcatalogues?\b", "catalog"), (r"\bdialogues?\b", "dialog"),
    (r"\bdefence\b", "defense"), (r"\boffence\b", "offense"), (r"\blicence\b", "license"),
    (r"\bcheques?\b", "check"), (r"\baluminium\b", "aluminum"), (r"\bageing\b", "aging"),
    (r"\bjudgement\b", "judgment"), (r"\blearnt\b", "learned"), (r"\bspelt\b", "spelled"), (r"\bburnt\b", "burned"),
]


def _one_spelling(word: str) -> str:
    for pattern, to in _SPELLING:
        word = re.sub(pattern, to, word)
    return word


def spelling_variant(wrong: str, correct: str) -> bool:
    """Does the correction only move between British and American spelling
    ("double story" -> "double-storey")?"""
    a, b = _tokens(wrong), _tokens(correct)
    return a != b and len(a) == len(b) and all(_one_spelling(x) == _one_spelling(y) for x, y in zip(a, b))


_FUNCTION_WORDS = {
    "a", "an", "the", "this", "that", "these", "those", "some", "any", "each", "every", "no", "both", "all", "much",
    "many", "more", "most", "less", "few", "little", "of", "in", "on", "at", "by", "for", "from", "to", "with", "about",
    "into", "onto", "over", "under", "between", "among", "through", "during", "before", "after", "against", "as",
    "than", "and", "or", "but", "so", "because", "although", "though", "while", "if", "when", "where", "which", "who",
    "whom", "whose", "what", "how", "why", "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them",
    "my", "your", "his", "its", "our", "their", "is", "are", "was", "were", "be", "been", "being", "am", "do", "does",
    "did", "have", "has", "had", "will", "would", "shall", "should", "can", "could", "may", "might", "must", "not",
}


# Irregular forms: "go" -> "went" is a verb form, grammar - never a synonym swap (#123, 2026-11-07)
_IRREGULAR = [set(forms.split("/")) for forms in (
    "become/became", "begin/began/begun", "break/broke/broken", "bring/brought", "build/built", "buy/bought",
    "catch/caught", "choose/chose/chosen", "come/came", "draw/drew/drawn", "drink/drank/drunk",
    "drive/drove/driven", "eat/ate/eaten", "fall/fell/fallen", "feel/felt", "fight/fought", "find/found",
    "fly/flew/flown", "forget/forgot/forgotten", "get/got/gotten", "give/gave/given", "go/goes/went/gone",
    "grow/grew/grown", "hear/heard", "hold/held", "keep/kept", "know/knew/known", "lead/led", "leave/left",
    "lose/lost", "make/made", "mean/meant", "meet/met", "pay/paid", "rise/rose/risen", "run/ran", "say/said",
    "see/saw/seen", "seek/sought", "sell/sold", "send/sent", "show/showed/shown", "shrink/shrank/shrunk",
    "sink/sank/sunk", "sit/sat", "speak/spoke/spoken", "spend/spent", "stand/stood", "take/took/taken",
    "teach/taught", "tell/told", "think/thought", "understand/understood", "win/won", "write/wrote/written",
    "child/children", "man/men", "woman/women", "person/people", "good/well/better/best", "bad/worse/worst")]


def _forms_of_one_word(a: str, b: str) -> bool:
    """Two forms of one word - "study"/"studies", "like"/"likes", "go"/"went":
    the correction changes grammar, not the word."""
    a, b = a.lower(), b.lower()
    if any(a in forms and b in forms for forms in _IRREGULAR):
        return True
    shared = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return shared >= 2 and shared >= min(4, min(len(a), len(b)) - 1)


def synonym_swap(wrong: str, correct: str) -> bool:
    """Does the correction only swap a content word or two for others, with no
    grammar changed ("are provided with" -> "are equipped with")? A spelling
    fix or another form of the same word ("noticed" -> "noted") is no swap."""
    edits = _edits(SimpleNamespace(wrong=wrong, correct=correct))
    if len(edits) != 1:
        return False
    out, put, _ = edits[0]
    if not out or not put or len(out) > 3 or len(put) > 3 or set(out + put) & _FUNCTION_WORDS:
        return False
    if any(_forms_of_one_word(a, b) for a, b in zip(out, put)):
        return False  # "the number go down", "my friend study online" were shown as no mistake (#123, #124)
    return difflib.SequenceMatcher(None, " ".join(out), " ".join(put), autojunk=False).ratio() < 0.7


# --- Scan artefacts: only in a text that came from a photo or a PDF ------------------------------------------

SCANNED_SOURCES = {"photo", "pdf"}
_STRAY_STOP = re.compile(r"\w\.(?:\w|,|\s+[a-z])")
_SCAN_LETTERS = (("fvl", "m"), ("rn", "m"), ("vv", "w"), ("cl", "d"))
_INSIDE_WORD = re.compile(r"[a-z][()\[\]|\\/!1]|[()\[\]|\\/!1][a-z]", re.IGNORECASE)


def _stray_stop(wrong: str, correct: str, text: str) -> bool:
    """A full stop where no sentence ends: "afford.able", "paid. to enter",
    "On the other hand.,", or at the end of the flag with the sentence going
    on in the text ("theatres and. other")."""
    w, c = canon(wrong), canon(correct)
    if "." not in w or re.sub(r"[.,;]", "", w).split() != re.sub(r"[.,;]", "", c).split():
        return False
    if _STRAY_STOP.search(w):
        return True
    if not w.rstrip().endswith("."):
        return False
    at = canon(text).find(w.rstrip())
    rest = canon(text)[at + len(w.rstrip()):].lstrip() if at >= 0 else ""
    return bool(rest) and rest[0].islower()


def _joined(wrong: str, correct: str) -> bool:
    """Two words run together: "stilljuggling" -> "still juggling"."""
    a, b = _tokens(wrong), _tokens(correct)
    if len(b) != len(a) + 1:
        return False
    return any(a[:i] == b[:i] and a[i] == b[i] + b[i + 1] and a[i + 1:] == b[i + 2:] for i in range(len(a)))


def _misread_letters(wrong: str, correct: str) -> bool:
    """A letter read as something no word holds, or as look-alike letters:
    "a(so", "it1s", "fvloreover" -> "Moreover"."""
    a, b = canon(wrong).lower().split(), canon(correct).lower().split()
    if len(a) != len(b):
        return False
    changed = [(x.strip(".,;:"), y.strip(".,;:")) for x, y in zip(a, b)]
    changed = [(x, y) for x, y in changed if x != y]  # punctuation alone is no misread letter
    return bool(changed) and all(
        (_INSIDE_WORD.search(x) and len(x) == len(y)) or any(x.replace(seen, read) == y for seen, read in _SCAN_LETTERS)
        for x, y in changed)


def scan_artefact(wrong: str, correct: str, text: str = "") -> bool:
    """Is the flag only a trace of scanning (never the candidate's own
    mistake)? Their real misspellings stay: "hugh", "seatting", "modificated"."""
    return _stray_stop(wrong, correct, text) or _joined(wrong, correct) or _misread_letters(wrong, correct)


# --- What a correction shows ----------------------------------------------------------------------------------

_ARTICLES = {"a", "an", "the"}
_NUMBER = {("it", "them"), ("one", "ones"), ("this", "these"), ("that", "those"), ("other", "others"),
           ("its", "their"), ("another", "other")}
_AGREEMENT = {("doesn't", "don't"), ("does", "do"), ("is", "are"), ("was", "were"), ("has", "have"),
              ("isn't", "aren't"), ("wasn't", "weren't"), ("hasn't", "haven't")}
ARTICLE_SLIPS_MAX = 2  # a third article left out or wrong: the writer doesn't control articles
_FIGURE = re.compile(r"\$?\d[\d,.]*%?")
_FIGURE_PREPOSITIONS = {"of", "for", "among"}


def small_kind(m) -> str | None:
    """'punctuation', 'article', 'number' or 'preposition' (one left out
    right after a figure) when a correction changes only that - the
    academic lead's slips ("a missing full stop", "a single article or plural
    slip"; 2026-10-29, #66's "such society", "the previous one", "they want
    it" were kept as errors). 'basic' for what is always an error: a verb's
    agreement ("people doesn't", "this turn of events are"), the article's
    form ("an useful"), a double comparative ("more cheaper")."""
    edits = _edits(m)
    if not edits:
        return "punctuation" if canon(m.wrong) != canon(m.correct) else None
    a, b = _tokens(m.wrong), _tokens(m.correct)
    ops = [op for op in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op[0] != "equal"]
    if ops and all(tag == "insert" and i1 > 0 and _FIGURE.fullmatch(a[i1 - 1]) and len(b[j1:j2]) == 1
                   and b[j1] in _FIGURE_PREPOSITIONS for tag, i1, _, j1, j2 in ops):
        return "preposition"  # "at only 13% men and 9% women": the meaning stays obvious (#74)
    if len(edits) != 1:
        return None
    out, put, after = edits[0]
    if set(out + put) == {"a", "an"}:
        return "basic"
    if out == ("more",) and not put and after.endswith("er"):
        return "basic"
    if len(out) <= 1 and len(put) <= 1 and set(out + put) <= _ARTICLES:
        return "article"
    if len(out) == len(put) == 1 and ((out[0], put[0]) in _NUMBER or (put[0], out[0]) in _NUMBER):
        return "number"
    if len(out) == len(put) == 1 and after == "of" and (put[0] in (out[0] + "s", out[0] + "es")
                                                       or out[0] in (put[0] + "s", put[0] + "es")):
        return "number"  # "feeling of worthlessness" -> "feelings of": a noun's number
    if len(out) == len(put) == 1 and ((out[0], put[0]) in _AGREEMENT or (put[0], out[0]) in _AGREEMENT):
        return "basic"
    return None


def small_slips(checked: list) -> list[str]:
    """Marks as slips the checked errors whose correction is only a full
    stop, an article or a pronoun's number - unless articles go wrong more
    than ARTICLE_SLIPS_MAX times (then the writer doesn't control them) -
    and as an error what is always one, when the filter called it a slip.
    Never a Task (data) mistake. Returns what it changed, for the log."""
    kinds = [small_kind(m) if m.criterion != "TR" else None for m in checked]
    articles = sum(k == "article" for k in kinds)
    changed = []
    for m, k in zip(checked, kinds):
        if k == "basic":  # "people doesn't": an error, whatever the filter said (T9, 2026-10-29)
            if m.severity == "slip":
                m.severity = "error"
                changed.append(f"{m.wrong!r} (basic language: an error)")
        elif k and m.severity == "error" and not (k == "article" and articles > ARTICLE_SLIPS_MAX):
            m.severity = "slip"
            changed.append(f"{m.wrong!r} ({k})")
    return changed


def distinct_slips(slips: list) -> list[tuple]:
    """(slip, how many times) - the same slip made again counts once (the
    academic lead, 2026-10-29: "the Czech Republic" left without "the" twice
    and "other" for "others" are 2 slips, not 3)."""
    seen: dict[tuple, list] = {}
    for m in slips:
        key = _change(m)
        if key == ((), (), ""):
            key = (canon(m.wrong).lower(), canon(m.correct).lower())
        seen.setdefault(key, [m, 0])[1] += 1
    return [(m, n) for m, n in seen.values()]


def final_kind(wrong: str, correct: str, kind: str, criterion: str, basis: str = "grammar") -> tuple[str, str]:
    """(kind, what decided it - "" when the filter's verdict stands). A data
    (TR) mistake is judged by the sheet, except a correction that only adds a
    detail: a sentence that is right as it stands is no mistake (#73)."""
    if criterion == "TR":
        if kind in ("error", "slip") and only_adds(wrong, correct):
            return "not_a_mistake", "its correction only adds to the student's words"
        return kind, ""
    if kind not in ("error", "slip"):
        if real_mistake(wrong):
            return "slip", "a real mistake on the academic lead's list"
        return kind, ""
    if ruled := praised(wrong, correct):
        return "not_a_mistake", f"a phrase examiners praise: {ruled!r}"
    if spelling_variant(wrong, correct):
        return "not_a_mistake", "British and American spelling are both right"
    if only_adds_comma(wrong, correct):
        return "not_a_mistake", "only a comma added: an examiner passes over it"
    if ruled := lead_accepted(wrong, correct):
        return "alternative", f"the academic lead accepted {ruled!r}"
    if real_mistake(wrong):
        return kind, ""
    if basis == "style":
        return "alternative", "the suggestion is only more common"
    if basis not in ("meaning", "clarity") and synonym_swap(wrong, correct):
        return "alternative", "a synonym swap with no grammar changed"
    return kind, ""
