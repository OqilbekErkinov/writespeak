"""Teachers' bands on checks (the user, 2026-11-14: "the teachers enter their marks themselves").

An IELTS teacher marks a check in the web panel (webapp/main.py, the checks page; a "teacher" login sees only
that page). The bands are kept on the check (teacher_bands), set against the bot's every week in the admins'
group (crm_events.teacher_report_if_due, /ustozlar), and teacher-marked Speaking checks become the scoring
runs' anchors (services/ai/speaking_grader.refresh_teacher_anchors).
"""
from __future__ import annotations

import statistics
from datetime import datetime, timezone

from services.ai.scoring import overall_band

# kind ("w" | "s") -> (key in teacher_bands, key in the bot's criteria_scores, label)
CRITERIA = {
    "w": (("TA", "TA", "Task"), ("CC", "CC", "Coherence"), ("LR", "LR", "Lexical"), ("GRA", "GRA", "Grammar")),
    "s": (("FC", "FC", "Fluency"), ("LR", "LR", "Lexical"), ("GRA", "GRA", "Grammar"),
          ("P", "Pronunciation", "Pronunciation")),
}


def parse_band(raw: str) -> float | None:
    """"7", "6.5", "6,5" -> the band; None when empty; ValueError for anything that is no IELTS band."""
    raw = (raw or "").strip().replace(",", ".")
    if not raw:
        return None
    value = float(raw)
    if not 0 <= value <= 9 or value * 2 != int(value * 2):
        raise ValueError(raw)
    return value


def record(kind: str, values: dict[str, str], by: str, note: str = "") -> dict | None:
    """The teacher_bands to keep, from the form's values ({"TA": "7", ...}); None when every band is empty (the
    marks taken off). ValueError when a band is missing or no IELTS band."""
    bands = {key: parse_band(values.get(key, "")) for key, _, _ in CRITERIA[kind]}
    if all(v is None for v in bands.values()):
        return None
    if any(v is None for v in bands.values()):
        raise ValueError("every criterion needs a band")
    return {"bands": bands, "overall": overall_band(list(bands.values())), "by": by,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "note": (note or "").strip()[:500]}


def bot_bands(kind: str, criteria_scores: dict | None) -> dict[str, float | None]:
    scores = criteria_scores or {}
    if kind == "w" and "TA" not in scores:
        scores = {**scores, "TA": scores.get("TR_or_TA", scores.get("TR"))}
    return {key: scores.get(bot_key) for key, bot_key, _ in CRITERIA[kind]}


def difference(kind: str, overall: float | None, criteria_scores: dict | None, teacher: dict | None) -> dict | None:
    """The bot minus the teacher: overall and per criterion (None where either is missing)."""
    if not teacher:
        return None
    mine = bot_bands(kind, criteria_scores)
    per = {k: (mine[k] - teacher["bands"][k]) if mine.get(k) is not None else None for k, _, _ in CRITERIA[kind]}
    return {"overall": (overall - teacher["overall"]) if overall is not None else None, "criteria": per}


def summary(rows: list[dict]) -> dict:
    """rows: {"kind", "id", "overall", "criteria_scores", "teacher"} -> how far the bot is from the teachers:
    the mean difference (bot minus teacher, negative = the bot lower), the mean absolute one, how many within
    half a band, per criterion, and the checks a band or more apart."""
    diffs = [(r, difference(r["kind"], r["overall"], r["criteria_scores"], r["teacher"])) for r in rows if r["teacher"]]
    overall = [d["overall"] for _, d in diffs if d and d["overall"] is not None]
    per: dict[str, list[float]] = {}
    for r, d in diffs:
        for k, v in (d or {}).get("criteria", {}).items():
            if v is not None:
                per.setdefault(f"{'W' if r['kind'] == 'w' else 'S'} {k}", []).append(v)
    far = [(r, d) for r, d in diffs if d and d["overall"] is not None and abs(d["overall"]) >= 1.0]
    return {
        "n": len(diffs),
        "mean": statistics.mean(overall) if overall else None,
        "mae": statistics.mean(abs(x) for x in overall) if overall else None,
        "within_half": sum(abs(x) <= 0.5 for x in overall),
        "criteria": {k: statistics.mean(v) for k, v in sorted(per.items())},
        "far": far,
    }
