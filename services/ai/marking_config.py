"""The academic values of the Writing marking (Bandzilla last prompt,
2026-10-26): config/writing_marking.json, owned by the academic lead -
density thresholds, band floors, anchors, calibration overrides, progress minimums,
regression expectations. The code reads them from here and never hard-codes
them; a missing file or key falls back to DEFAULTS (logged once).
"""
from __future__ import annotations

import copy
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PATH = Path(__file__).resolve().parents[2] / "config" / "writing_marking.json"

DEFAULTS = {
    "density": {"slip_weight": 0.5, "band9_max": 0.5, "band8_max": 1.2, "band7_max": 2.5, "start_at_band8_max": 1.2},
    "anchors": {"task1": ["v2-p072", "v2-p068", "v2-p084"], "task2": ["v2-p026", "v2-p034", "v2-p078"]},
    "calibration": {"overrides": {}, "low_band_max": 6.0, "low_band_max_rise": 0.5, "max_cost_per_check": 0.15},
    "progress": {"min_words": {"task1": 100, "task2": 150}},
    "regression": {},
    "type_anchors": {"use": True},
    "floors": {"wide_range_phrases": 3, "slips_only_min": 8, "clear_vocabulary_lr_min": 7},
    "model_answer": {"banned_openers": []},
}

_cache: dict = {}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in extra.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load() -> dict:
    """The config, re-read when the file changes."""
    try:
        mtime = PATH.stat().st_mtime
    except FileNotFoundError:
        if not _cache.get("warned"):
            logger.warning("%s not found - the Writing marking uses its defaults", PATH)
            _cache["warned"] = True
        return copy.deepcopy(DEFAULTS)
    if _cache.get("mtime") != mtime:
        _cache.update(mtime=mtime, data=_merge(DEFAULTS, json.loads(PATH.read_text(encoding="utf-8"))))
    return _cache["data"]


def density() -> dict:
    return load()["density"]


def anchor_ids(task_type: str | None = None) -> list[str] | set[str]:
    anchors = load()["anchors"]
    if task_type:
        return list(anchors.get(task_type, []))
    return {i for key in ("task1", "task2") for i in anchors.get(key, [])}


def type_anchors_on() -> bool:
    """Whether Task 1 scoring shows the Band 9 report of the same kind of visual."""
    return bool(load()["type_anchors"].get("use", True))


def floors() -> dict:
    """The least Lexical Resource / Grammar band a long-enough answer gets for
    what it shows (the academic lead, 2026-10-29)."""
    return load()["floors"]


def banned_openers() -> list[str]:
    return list(load()["model_answer"].get("banned_openers", []))


def calibration() -> dict:
    return load()["calibration"]


def progress_min_words(task_type: str) -> int:
    return int(load()["progress"]["min_words"].get(task_type, 0))


def regression_expectations() -> dict:
    return {k: v for k, v in load()["regression"].items() if not k.startswith("_") and k != "review"}


def derived_cases() -> dict:
    """Regression cases made from another by changing its text ({"from",
    "replace": [old, new]}) - a figure changed on purpose must be caught."""
    return {k: v for k, v in regression_expectations().items() if isinstance(v, dict) and v.get("from")}
