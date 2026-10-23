"""Shared by the Writing calibration scripts (task1_extraction_test.py,
build_writing_anchors.py, calibrate_writing.py, writing_regression.py):
the book samples, their chart pictures, the cached chart readings and a
spending limit. Everything lives in settings.calibration_dir
(data/calibration/, gitignored - book text and a student's essays).
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.config import settings  # noqa: E402
from services.ai import usage  # noqa: E402

# (getattr: the "before" calibration runs this against older code without the setting)
CAL = getattr(settings, "calibration_dir", None) or Path(settings.books_path).parent / "calibration"
logger = logging.getLogger("calibration")


class BudgetExceeded(Exception):
    pass


class Budget:
    """The OpenAI spend of one script run, stopped at `limit` dollars."""

    def __init__(self, limit: float):
        self.limit, self.spent = limit, 0.0

    def add(self, calls) -> float:
        cost = usage.summary(calls)["total_usd"] if calls else 0.0
        self.spent += cost
        return cost

    def check(self) -> None:
        if self.spent > self.limit:
            raise BudgetExceeded(f"spent ${self.spent:.2f}, over the limit of ${self.limit:.2f}")


def load_samples() -> list[dict]:
    return json.loads((CAL / "book_samples.json").read_text(encoding="utf-8"))


def chart_images(sample: dict) -> list[tuple[bytes, str]]:
    if not sample.get("chart"):
        return []
    return [((CAL / sample["chart"]).read_bytes(), "image/png")]


def question_of(sample: dict) -> str:
    """The sample's question; Volume 2 p.16 prints only its two maps."""
    if sample["question"]:
        return sample["question"]
    return ("The maps show a museum in 1998 and 2008. Summarise the information by selecting and reporting the "
            "main features, and make comparisons where relevant.")


def reading_version() -> str:
    """Changes whenever the chart-reading prompts or models change, so cached readings are redone."""
    from services.ai import visual_reading as vr

    from services.ai import schemas

    parts = [getattr(vr, name, "") for name in ("READ_A", "READ_B", "READ_SYSTEM", "_RULES")]
    parts += [settings.openai_model_vision, settings.openai_chart_reasoning_effort]
    for name in ("VisualExtraction", "VisualReading"):
        if hasattr(schemas, name):
            parts.append(json.dumps(getattr(schemas, name).model_json_schema(), sort_keys=True))
    return hashlib.sha1("\n".join(map(str, parts)).encode()).hexdigest()[:12]


async def visual_for(sample: dict, cache_dir: str = "visuals", fresh: bool = False):
    """(reading or None, its sheet text, from_cache) for a Task 1 sample -
    read with the production code, cached per reading version."""
    from services.ai.visual_reading import read_visual, sheet_text

    folder = CAL / cache_dir
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sample['id']}.json"
    version = reading_version()
    if path.exists() and not fresh:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached.get("version") == version:
            reading = cached.get("reading")
            try:  # the key features again, with the current engine (no new read)
                from services.ai.schemas import VisualSheet
                from services.ai.visual_features import key_features

                sheet = VisualSheet.model_validate(reading)
                if sheet.readability != "unreadable":
                    sheet.features = key_features(sheet)
                return sheet.model_dump(), sheet_text(sheet), True
            except (ImportError, ValueError, TypeError):  # older code: the sheet as it was saved
                return reading, cached.get("sheet", ""), True
    reading = await read_visual(question_of(sample), chart_images(sample))
    text = sheet_text(reading) if reading is not None else ""
    dumped = reading.model_dump() if reading is not None else None
    path.write_text(json.dumps({"version": version, "reading": dumped, "sheet": text}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return dumped, text, False


def band_label(sample: dict) -> str:
    low, high = sample["band_low"], sample["band_high"]
    return f"{low:.1f}" if low == high else f"{low:.1f}-{high:.1f}"


def diff_to_range(ours: float, low: float, high: float) -> float:
    """0 inside the book's range, else the distance to its nearest end (signed)."""
    if ours < low:
        return ours - low
    if ours > high:
        return ours - high
    return 0.0


def setup_logging() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    for name in ("services.ai.visual_reading", "services.ai.writing_grader", "services.ai.scoring", "calibration"):
        logging.getLogger(name).setLevel(logging.INFO)
