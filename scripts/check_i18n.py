"""Checks the bot's UI texts (bot/i18n.py, bot/i18n_en.py) are complete.

    python scripts/check_i18n.py

Every key needs uz, ru and en, and every translation needs the same
{placeholders} and HTML tags as the Uzbek text - a missing placeholder
crashes t(), a missing tag breaks the message's HTML. Run it after adding
or changing a text; exits 1 on any problem.
"""
from __future__ import annotations

import re
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.i18n import LANGS, _T  # noqa: E402
from bot.i18n_en import EN  # noqa: E402

_TAG = re.compile(r"</?[a-z]+[^>]*>")


def _fields(text: str) -> list[str]:
    return sorted({name for _, name, _, _ in string.Formatter().parse(text) if name is not None})


def main() -> int:
    problems = [f"{key}: in bot/i18n_en.py but not in bot/i18n.py" for key in EN if key not in _T]
    for key, entry in _T.items():
        base = entry.get("uz", "")
        for lang in LANGS:
            text = entry.get(lang)
            if text is None:
                problems.append(f"{key}: no {lang}")
                continue
            if _fields(text) != _fields(base):
                problems.append(f"{key} [{lang}]: placeholders {_fields(text)} != uz {_fields(base)}")
            if sorted(_TAG.findall(text)) != sorted(_TAG.findall(base)):
                problems.append(f"{key} [{lang}]: HTML tags differ from uz")
    for line in problems:
        print(line)
    print(f"{len(_T)} keys, {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
