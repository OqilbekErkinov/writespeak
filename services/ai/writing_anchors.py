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

Both hold book text, so they live on the data volume (gitignored) and are
copied to the server by hand. Without them the scorer works from the
marking guide alone (logged once). They sit in the scoring prompt's fixed
part, so OpenAI's prompt cache bills them at a tenth of the price.
"""
from __future__ import annotations

import json
import logging

from bot.config import settings
from services.ai import marking_config

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


def anchors_text(task_type: str) -> str:
    """The anchors as the scoring prompt shows them ("" without the file)."""
    items = anchors(task_type)
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
        out.append(f"--- Anchor {k}: Band {a['band_label']} ---")
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
