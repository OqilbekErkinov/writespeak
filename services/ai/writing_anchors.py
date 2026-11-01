"""Anchor examples and Task 1 overview patterns for the Writing scorer
(marking guide B3 and A8, 2026-10-22).

  data/calibration/anchors.json        the fixed anchors, the same in every
      scoring run, chosen by the academic lead in config/writing_marking.json
      (services/ai/marking_config.py): from Volume 2 of Sanokulov's IELTS
      Writing Collection with its commentary (2026-10-22: Task 1 pp.72 6.5,
      68 7.0-7.5, 84 8.0; Task 2 pp.26 6.5-7.0, 34 7.0-7.5, 78 8.0+), plus a
      Band 9 anchor per task from Volume 1 (2026-10-26: the scorer had no
      picture of a 9 and gave every criterion 8). Built by
      scripts/build_writing_anchors.py from book_samples.json and the
      anchors' verified chart sheets.
  data/calibration/task1_patterns.json 2-3 real overviews per visual type
      from the books' Band 7-9 reports, for the Task Achievement scorer
      and the model answer.

  data/calibration/cambridge_anchors.json  real candidates' answers with an
      examiner's band and comment ("task1", "task2": id, kind, band, question,
      text, comment; built by the academic lead's calibration, 2026-11-01).
      With it the scorer sees only the 2-3 most similar: the same kind of
      question, at the band the checked mistakes point to, one above and one
      below - never a fixed set, and never in a student's report.

They hold book text, so they live on the data volume (gitignored) and are
copied to the server by hand. Without them the scorer works from the
marking guide alone (logged once). They sit in the scoring prompt's fixed
part, so OpenAI's prompt cache bills them at a tenth of the price.
"""
from __future__ import annotations

import difflib
import json
import logging
import re

from bot.config import settings
from services.ai import marking_config
from services.ai.essay_marking import canon

logger = logging.getLogger(__name__)

_cache: dict[str, tuple[float, object]] = {}
_warned: set[str] = set()


def _load(name: str):
    path = settings.calibration_dir / name
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        if name not in _warned:
            logger.warning("%s not found - the Writing scorer works without it", path)
            _warned.add(name)
        return None
    cached = _cache.get(name)
    if cached and cached[0] == mtime:
        return cached[1]
    data = json.loads(path.read_text(encoding="utf-8"))
    _cache[name] = (mtime, data)
    return data


def anchors(task_type: str) -> list[dict]:
    data = _load("anchors.json") or {}
    return data.get(task_type, [])


def anchor_ids() -> set[str]:
    """Every anchor id (config/writing_marking.json) - never a B4 test sample."""
    return marking_config.anchor_ids()


def _same_text(a: str, b: str) -> bool:
    """Is `b` the anchor `a` itself (an anchor is never shown for its own report)?"""
    if not a or not b:
        return False
    x, y = canon(a).lower()[:2500], canon(b).lower()[:2500]
    return difflib.SequenceMatcher(None, x, y, autojunk=False).ratio() >= 0.9


def type_anchor(kind: str | None, exclude: str = "") -> dict | None:
    """The Band 9 report of the same kind of visual (data/calibration/
    type_anchors.json: one per kind - line, bar, table, pie, process, cycle,
    mixed - chosen by the academic lead on 2026-10-27), unless it is the
    report being scored."""
    if not kind or not marking_config.type_anchors_on():
        return None
    item = (_load("type_anchors.json") or {}).get(kind)
    return None if item is None or _same_text(item.get("text", ""), exclude) else item


_QUESTION_TYPES = (
    ("advantages", r"advantages?|disadvantages?|benefits? and drawbacks"),
    ("both_views", r"discuss both|both (?:these )?views"),
    ("causes", r"causes?|reasons?|why\b.*\?|problems?|solutions?|effects?|what can be done|what measures"),
    ("opinion", r"to what extent|do you agree|agree or disagree|your opinion|positive or negative"),
)


def question_type(question: str) -> str:
    """A Task 2 question's kind, the first that fits ("Give reasons for your answer" is in every question)."""
    question = re.sub(r"give reasons for your answer.*", "", question or "", flags=re.IGNORECASE | re.DOTALL)
    for name, pattern in _QUESTION_TYPES:
        if re.search(pattern, question, re.IGNORECASE):
            return name
    return "other"


def similar_anchors(task_type: str, kind: str | None, question: str, band: float, exclude: str = "") -> list[dict]:
    """The 2-3 official samples most like this answer: the nearest to `band`
    (where its checked mistakes point), then one above and one below, the
    same kind of visual or question first. A different kind counts as 0.75
    of a band away."""
    pool = (_load("cambridge_anchors.json") or {}).get(task_type) or []
    pool = [a for a in pool if not _same_text(a.get("text", ""), exclude)]
    wanted = kind if task_type == "task1" else question_type(question)
    chosen: list[dict] = []
    for target in (band, band + 1, band - 1):
        rest = [a for a in pool if a not in chosen]
        if not rest:
            break
        best = min(rest, key=lambda a: (abs(a["band"] - target) + (0 if a.get("kind") == wanted else 0.75), a["id"]))
        if abs(best["band"] - target) <= 1:
            chosen.append(best)
    return sorted(chosen, key=lambda a: a["band"])[:marking_config.similar_anchors_max()]


def _official_text(items: list[dict], task_type: str) -> str:
    piece = "report" if task_type == "task1" else "essay"
    out = [f"ANCHOR EXAMPLES - real candidates' {piece}s with the band an IELTS examiner gave them and the examiner's "
           "comment. They keep their mistakes: see how many a real examiner forgives at each band, and calibrate "
           f"against them, criterion by criterion. Never quote them or mention them."]
    for k, a in enumerate(items, 1):
        out.append(f"--- Anchor {k}: Band {a['band']:g} ({a.get('kind', '')}) ---\nQuestion: {a.get('question', '')}\n"
                   f"The {piece} ({len(a['text'].split())} words):\n{a['text']}\nExaminer's comment: {a['comment']}")
    return "\n\n".join(out)


def anchors_text(task_type: str, kind: str | None = None, exclude: str = "", question: str = "",
                 band: float | None = None) -> str:
    """The anchors as the scoring prompt shows them ("" without the file):
    with the official samples and an estimated `band`, the most similar of
    them (a Band 9 report of the same kind too, for an answer heading to 8 or
    9); otherwise the fixed set, plus for Task 1 the Band 9 report of the
    visual's `kind`. Never the answer being scored (`exclude`) itself."""
    if band is not None and (similar := similar_anchors(task_type, kind, question, band, exclude)):
        text = _official_text(similar, task_type)
        top = type_anchor(kind, exclude) if task_type == "task1" and band >= 8 else None
        return text if top is None else text + f"\n\n--- A Band 9 report on the same kind of visual ---\n{top['text']}"
    items = [a for a in anchors(task_type) if not _same_text(a.get("text", ""), exclude)]
    extra = type_anchor(kind, exclude) if task_type == "task1" else None
    if extra is not None:
        items = items + [extra]
    if not items:
        return ""
    piece = "report" if task_type == "task1" else "essay"
    low = min(a["band_low"] for a in items)
    high = max(a["band_high"] for a in items)
    out = [
        f"ANCHOR EXAMPLES - real {piece}s with the band an experienced examiner gave them, and the examiner's "
        "reasons where the book gives them. Calibrate against them: put the answer you score next to them, "
        f"criterion by criterion. They cover Band {low:g} to {high:g}; below {low:g}, use the guide."
    ]
    for k, a in enumerate(items, 1):
        same_kind = f" - a report on the same kind of visual ({a['kind']})" if a.get("kind") else ""
        out.append(f"--- Anchor {k}: Band {a['band_label']}{same_kind} ---")
        if a.get("question"):
            out.append(f"Question: {a['question']}")
        if a.get("sheet"):
            out.append(a["sheet"])
        out.append(f"The {piece} ({a['words']} words):\n{a['text']}")
        if a.get("commentary"):
            out.append(f"Examiner's commentary: {a['commentary']}")
        else:
            out.append(f"(No commentary: a Band {a['band_label']} {piece} from the book - what a {a['band_label']} "
                       "looks like: few or no errors, wide and natural language, every part fully developed.)")
    return "\n\n".join(out)


def patterns() -> dict[str, list[str]]:
    return _load("task1_patterns.json") or {}


def patterns_text() -> str:
    """Real overviews by visual type ("" without the file)."""
    data = patterns()
    if not data:
        return ""
    lines = ["WHAT A GOOD OVERVIEW LOOKS LIKE - real overviews from Band 7-9 reports, by visual type (they state the "
             "main trends, differences or stages, not single figures):"]
    for kind, examples in data.items():
        lines += [f"- {kind}: \"{x}\"" for x in examples]
    return "\n".join(lines)
