"""The feedback PDF's file name wherever a student gets it - right after the
check, a resubmission, History: "Bandzilla - Writing Task 1 Feedback.pdf",
"Bandzilla - Speaking Full test Feedback.pdf" (the user's wording,
2026-10-17; it was "IELTS_Feedback.pdf" for every report)."""
from __future__ import annotations

from db.models import SPEAKING_LABELS

WRITING_LABELS = {"task1": "Writing Task 1", "task2": "Writing Task 2"}


def report_filename(module: str, task_or_part) -> str:
    """`module`: "writing" or "speaking"; `task_or_part`: task1/task2 or
    part1/part2/part3/full - the enum or its value."""
    value = getattr(task_or_part, "value", task_or_part)
    label = (WRITING_LABELS if module == "writing" else SPEAKING_LABELS).get(value)
    return f"Bandzilla - {label or module.capitalize()} Feedback.pdf"


CAPTION_LIMIT = 1024  # Telegram's document caption limit


def with_ai_note(caption: str, lang: str | None) -> str:  # noqa: ARG001 - kept for its callers
    """A report's caption as it is. From 2026-11-10 it carried "marked by AI, may differ from an official IELTS
    result by about ±0.5"; the owner took that note off everywhere on 2026-11-18 (the bot, the PDFs, the site) -
    only the "Bandzilla is independent, official results come from a test centre" line stays."""
    return caption
