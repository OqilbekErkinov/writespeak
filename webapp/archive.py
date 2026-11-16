"""The archive of every check (the owner, 2026-11-16: "a page where all the work is kept - inside Checks, two
pages, Writing and Speaking; for each check its question with its pictures, the answer, the audio, and the band
our bot gave - everything inside the check").

- /archive/writing, /archive/speaking         every check, newest first, 50 a page, with filters;
- /archive/writing/<id>, /archive/speaking/<id>  everything a check holds: the student, where the question came
  from, how it was paid and what it cost, the bot's bands (and a teacher's), the question with its pictures,
  the answer as sent, the recording, the transcripts, Azure's pronunciation scores, the numbered mistakes, the
  model answers, the vocabulary, the chart reading and the PDF - older and partial rows show what they have;
- a few read-only file routes (an upload, a recording with its own type, a model answer's voice from Telegram)
  and /archive/hold/<id>, which takes a stopped check (/checks/stopped) to what the student got afterwards.

Admins only, read-only: a teacher login marks blind and never sees the bot's bands, so it is sent back to its
checks page (here and by webapp/main.py's middleware). The list never loads a whole report: its page's ids
first (by the primary key), then only those rows' light columns (webapp/insights.py _light) and their users in
one query each - the same number of queries for any page.

Wired into webapp/main.py like webapp/panel.py (`templates` is set there).
"""
from __future__ import annotations

import logging
import math
import mimetypes
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from markupsafe import Markup, escape
from sqlalchemy import func, or_, select

from bot.config import settings
from bot.utils import teacher_bands as tb
from db.database import get_session
from db.models import (
    SPEAKING_LABELS,
    Assignment,
    AssignmentPart,
    Center,
    ChartKey,
    CheckHold,
    Group,
    MockTestPart,
    MockTestSession,
    PracticeQuestion,
    SpeakingPart,
    SpeakingSubmission,
    SubmissionComment,
    TaskType,
    User,
    WritingSubmission,
)
from webapp import insights
from webapp.checks_ops import _admin_only

logger = logging.getLogger(__name__)
router = APIRouter()
templates = None  # set by webapp/main.py

PAGE = 50
KEEP_UPLOADS_DAYS = 30  # deploy/backup/backup.sh: the students' photos and voices are deleted after a month


@dataclass(frozen=True)
class Kind:
    slug: str  # "writing" | "speaking"
    code: str  # "w" | "s" - as in /reports/<code>/<id>.pdf and teacher_bands
    model: type
    subs: dict  # the task / part filter: value -> label
    active: str  # the sidebar's open page


WRITING = Kind("writing", "w", WritingSubmission, {"task1": "Task 1", "task2": "Task 2"}, "archive_w")
SPEAKING = Kind("speaking", "s", SpeakingSubmission,
                {"part1": "Part 1", "part2": "Part 2", "part3": "Part 3", "full": "Full test"}, "archive_s")
KINDS = {"writing": WRITING, "speaking": SPEAKING}

# the criteria in the report's order: (short, the keys a row may hold them under - newest first)
CRITERIA = {
    "w": (("TA", ("TA", "TR", "TR_or_TA", "task_achievement_or_response", "task_achievement", "task_response")),
          ("CC", ("CC", "coherence_and_cohesion")), ("LR", ("LR", "lexical_resource")),
          ("GRA", ("GRA", "grammatical_range_and_accuracy"))),
    "s": (("FC", ("FC", "fluency_and_coherence", "fluency")), ("LR", ("LR", "lexical_resource")),
          ("GRA", ("GRA", "grammatical_range_and_accuracy")), ("P", ("Pronunciation", "P", "pronunciation"))),
}
CRITERION_NAMES = {"TA": "Task Achievement", "TR": "Task Response", "CC": "Coherence & Cohesion",
                   "LR": "Lexical Resource", "GRA": "Grammatical Range & Accuracy", "FC": "Fluency & Coherence",
                   "P": "Pronunciation"}
SOURCES = {"text": "matn", "photo": "rasm", "pdf": "PDF fayl", "docx": "Word fayl", "audio": "audio"}
CHANNELS = {"cdireport": "@cdireport", "speaking_assistant": "@IELTS_Speaking_Assistant_App"}
PAID_EXTRA = {"test-run": "test (live_check)"}
IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic"}
MEDIA = {".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg", ".m4a": "audio/mp4",
         ".mp4": "audio/mp4", ".aac": "audio/aac", ".wav": "audio/wav", ".flac": "audio/flac", ".webm": "audio/webm",
         ".amr": "audio/amr", ".wma": "audio/x-ms-wma", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif", ".heic": "image/heic",
         ".pdf": "application/pdf", ".txt": "text/plain; charset=utf-8",
         ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
INLINE = {".mp3", ".ogg", ".oga", ".opus", ".m4a", ".mp4", ".aac", ".wav", ".flac", ".webm", ".jpg", ".jpeg", ".png",
          ".webp", ".gif", ".pdf", ".txt"}


# --- tolerant readers: an old or partial row shows what it has, never a 500 --------------------------------------


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _num(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            x = float(value.strip().replace(",", "."))
        except ValueError:
            return None
        return x if math.isfinite(x) else None
    return None


def _str(value) -> str:
    return value.strip() if isinstance(value, str) else ("" if value is None else str(value))


def _file(path) -> Path | None:
    """The file at a path the row recorded, when it is still on the disk."""
    if not isinstance(path, str) or not path.strip():
        return None
    try:
        p = Path(path)
        return p if p.is_file() else None
    except (OSError, ValueError):
        return None


def _value(raw) -> str:
    return getattr(raw, "value", raw) or ""


def _clock(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "—"
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def _size(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{max(1, round(n / 1024))} KB"


def criteria(code: str, scores, task: str = "") -> list[dict]:
    """The four criteria in the report's order ({"key", "short", "name", "band"}) - from whichever key the row
    holds each under - then anything else it holds."""
    scores = _dict(scores)
    out, used = [], set()
    for key, aliases in CRITERIA[code]:
        band = None
        used.update(aliases)  # one criterion, whichever of its names the row used
        for alias in aliases:
            if alias in scores:
                band = _num(scores[alias])
                break
        short = "TR" if key == "TA" and task == "task2" else key
        out.append({"key": key, "short": short, "name": CRITERION_NAMES[short], "band": band})
    for k, v in scores.items():
        if k not in used and _num(v) is not None:
            out.append({"key": str(k), "short": str(k), "name": str(k), "band": _num(v)})
    return out


def words_of(text) -> int:
    return len(text.split()) if isinstance(text, str) else 0


def highlight(text: str, mistakes: list[dict]) -> Markup | None:
    """The text with each numbered mistake's `wrong` marked where it first appears (and doesn't overlap one
    already marked); None when none of them is found."""
    if not text:
        return None
    spans = []
    for m in mistakes:
        wrong = m.get("wrong") or ""
        if len(wrong) < 2:
            continue
        start = 0
        while (at := text.find(wrong, start)) >= 0:
            end = at + len(wrong)
            if not any(at < e and s < end for s, e, _ in spans):
                spans.append((at, end, m))
                break
            start = at + 1
    if not spans:
        return None
    spans.sort(key=lambda s: s[0])
    out, pos = [], 0
    for s, e, m in spans:
        out.append(escape(text[pos:s]))
        tip = f"{m.get('correct') or ''} — {m.get('why') or ''}".strip(" —")
        out.append(Markup('<mark class="a-mk" title="{}">{}<sup>{}</sup></mark>').format(tip, text[s:e], m.get("n") or ""))
        pos = e
    out.append(escape(text[pos:]))
    return Markup("").join(out)


def mistakes_of(annotations) -> list[dict]:
    """The numbered mistakes ({n, q, wrong, correct, why, criterion, unclear, if_you_meant}) - or an older row's
    sentence notes ({sentence, color, comment}), marked `old`."""
    out = []
    for i, m in enumerate(_list(annotations), 1):
        if not isinstance(m, dict):
            if isinstance(m, str) and m.strip():
                out.append({"n": i, "old": True, "wrong": m.strip(), "correct": "", "why": "", "color": ""})
            continue
        if "sentence" in m and "wrong" not in m:
            out.append({"n": i, "old": True, "wrong": _str(m.get("sentence")), "correct": "",
                        "why": _str(m.get("comment")), "color": _str(m.get("color"))})
            continue
        n = m.get("n")
        out.append({
            "n": n if isinstance(n, int) else i, "old": False, "q": _str(m.get("q")).upper(),
            "wrong": _str(m.get("wrong")), "correct": _str(m.get("correct")), "why": _str(m.get("why")),
            "crit": _str(m.get("criterion")), "unclear": bool(m.get("unclear")),
            "meant": _str(m.get("if_you_meant")),
        })
    return out


def vocabulary_of(items) -> list[dict]:
    out = []
    for v in _list(items):
        if isinstance(v, dict):
            term = _str(v.get("word_or_phrase") or v.get("term") or v.get("word"))
            if term:
                out.append({"term": term, "meaning": _str(v.get("meaning")),
                            "example": _str(v.get("example_sentence") or v.get("example"))})
        elif isinstance(v, str) and v.strip():
            out.append({"term": v.strip(), "meaning": "", "example": ""})
    return out


def cost_of(rd: dict) -> dict:
    cost = _dict(rd.get("cost"))
    total = _num(cost.get("total_usd"))
    if total is None:
        total = _num(cost.get("usd"))
    parts = [(str(k), _num(v)) for k, v in _dict(cost.get("by_purpose")).items() if _num(v) is not None]
    return {"total": total or 0.0, "parts": parts, "calls": cost.get("calls") if isinstance(cost.get("calls"), int)
            else None, "models": [str(m) for m in _list(cost.get("models"))]}


# --- the filters --------------------------------------------------------------------------------------------------


def _date(raw: str | None) -> date | None:
    try:
        return date.fromisoformat((raw or "").strip()[:10])
    except ValueError:
        return None


def _band(raw: str | None) -> float | None:
    v = _num(raw) if raw not in (None, "") else None
    return v if v is not None and 0 <= v <= 9 else None


def day_start(d: date) -> datetime:
    """Midnight in Tashkent on day `d`, as UTC."""
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc) - insights.TASHKENT


@dataclass
class Filters:
    q: str = ""
    start: date | None = None
    end: date | None = None
    sub: str = ""  # task1/task2 or part1/part2/part3/full
    bmin: float | None = None
    bmax: float | None = None
    test: bool = False

    @classmethod
    def parse(cls, kind: Kind, params) -> "Filters":
        f = cls(q=(params.get("q") or "").strip()[:100], start=_date(params.get("from")), end=_date(params.get("to")),
                sub=params.get("sub") if params.get("sub") in kind.subs else "", bmin=_band(params.get("bmin")),
                bmax=_band(params.get("bmax")), test=params.get("test") == "1")
        if f.start and f.end and f.start > f.end:
            f.start, f.end = f.end, f.start
        if f.bmin is not None and f.bmax is not None and f.bmin > f.bmax:
            f.bmin, f.bmax = f.bmax, f.bmin
        return f

    def pairs(self) -> list[tuple[str, str]]:
        out = [("q", self.q)] if self.q else []
        out += [("from", self.start.isoformat())] if self.start else []
        out += [("to", self.end.isoformat())] if self.end else []
        out += [("sub", self.sub)] if self.sub else []
        out += [("bmin", f"{self.bmin:g}")] if self.bmin is not None else []
        out += [("bmax", f"{self.bmax:g}")] if self.bmax is not None else []
        return out + ([("test", "1")] if self.test else [])

    def query(self, **extra) -> str:
        return urlencode(self.pairs() + [(k, str(v)) for k, v in extra.items() if v not in (None, "")])

    @property
    def any(self) -> bool:
        return bool(self.q or self.start or self.end or self.sub or self.bmin is not None or self.bmax is not None)

    def conditions(self, kind: Kind) -> list:
        m = kind.model
        out = []
        if self.q:
            number = self.q.lstrip("#").strip()
            if number.isdigit() and len(number) <= 15:
                n = int(number)
                by_id = m.id == n if n < 2 ** 31 else None  # the id column is a 32-bit integer
                if self.q.startswith("#"):
                    out.append(by_id if by_id is not None else m.id.is_(None))
                else:
                    out.append(or_(m.user_id == n, by_id) if by_id is not None else m.user_id == n)
            else:
                text = self.q.lstrip("@").strip()
                out.append(m.user_id.in_(select(User.id).where(or_(
                    User.full_name.icontains(text, autoescape=True), User.username.icontains(text, autoescape=True)))))
        if self.start:
            out.append(m.created_at >= day_start(self.start))
        if self.end:
            out.append(m.created_at < day_start(self.end + timedelta(days=1)))
        if self.sub:
            out.append(m.task_type == TaskType(self.sub) if kind is WRITING else m.part == SpeakingPart(self.sub))
        if self.bmin is not None:
            out.append(m.overall_band >= self.bmin)
        if self.bmax is not None:
            out.append(m.overall_band <= self.bmax)
        if not self.test:
            out.append(m.is_test.is_(False))
        return out


def _periods(f: Filters) -> list[dict]:
    """The quick date chips: today, 7 days, 30 days (Tashkent days), and their state."""
    today = (insights.now() + insights.TASHKENT).date()
    out = []
    for label, days in (("Bugun", 0), ("7 kun", 6), ("30 kun", 29)):
        start = today - timedelta(days=days)
        g = Filters(**{**f.__dict__, "start": start, "end": today})
        out.append({"label": label, "query": g.query(), "on": f.start == start and f.end == today})
    return out


# --- the lists ----------------------------------------------------------------------------------------------------


async def list_page(session, kind: Kind, f: Filters, page: int) -> dict:
    m = kind.model
    conds = f.conditions(kind)
    total, avg, coins = (await session.execute(
        select(func.count(), func.avg(m.overall_band), func.coalesce(func.sum(m.coins_spent), 0))
        .select_from(m).where(*conds))).one()
    pages = max(1, math.ceil(total / PAGE))
    page = min(max(page, 1), pages)
    # the page's ids by the primary key, then only those rows: never a report evaluated for a skipped row
    ids = (await session.execute(select(m.id).where(*conds).order_by(m.id.desc())
                                 .offset((page - 1) * PAGE).limit(PAGE))).scalars().all()
    rows = []
    if ids:
        if kind is WRITING:
            extra = (m.original_file_paths.label("files"), m.answer_text.label("text"),
                     func.left(m.prompt_text, 140).label("prompt"))
        else:
            extra = (m.report_data["metrics"].label("metrics"), m.report_data["one_recording"].label("one"),
                     func.left(m.question_text, 140).label("prompt"))
        found = (await session.execute(select(m, *extra).options(*insights._light(m)).where(m.id.in_(ids))
                                       .order_by(m.id.desc()))).all()
        user_ids = {r[0].user_id for r in found}
        users = {u.id: u for u in (await session.execute(select(User).where(User.id.in_(user_ids)))).scalars()}
        query = f.query()
        for r in found:
            rows.append(_list_row(kind, r, users.get(r[0].user_id), query))
    return {"rows": rows, "total": total, "page": page, "pages": pages, "avg": avg,
            "coins": int(coins or 0), "window": _window(page, pages)}


def _window(page: int, pages: int) -> list[int | None]:
    """The page numbers to show: the first, the last, two around this one; None for a gap."""
    keep = sorted({1, pages, *range(max(1, page - 2), min(pages, page + 2) + 1)})
    out: list[int | None] = []
    for n in keep:
        if out and n - out[-1] > 1:
            out.append(None)
        out.append(n)
    return out


def _list_row(kind: Kind, r, user: User | None, query: str) -> dict:
    """One row of a list - its own tolerant reading of the row (insights._check_row stops at a band kept as text)."""
    sub = r[0]
    task = _value(sub.task_type if kind is WRITING else sub.part)
    row = {"id": sub.id, "user_id": sub.user_id, "user": user, "when": insights.fmt(sub.created_at),
           "band": _num(sub.overall_band), "coins": sub.coins_spent or 0, "cost": _num(sub.cost_usd) or 0.0,
           "is_test": bool(sub.is_test), "practice": bool(sub.practice_question_id),
           "assignment": bool(sub.assignment_part_id), "teacher": bool(_dict(sub.teacher_bands).get("bands")),
           "audio": kind is SPEAKING and _file(sub.audio_file_path) is not None,
           "url": f"/archive/{kind.slug}/{sub.id}" + (f"?{query}" if query else ""),
           "crit": criteria(kind.code, sub.criteria_scores, task), "prompt": _str(r.prompt).replace("\n", " "),
           "pdf_ok": _file(sub.feedback_pdf_path) is not None, "sub": kind.subs.get(task, task or "—")}
    if kind is WRITING:
        files = _dict(r.files)
        charts = [p for p in _list(files.get("chart")) if _file(p)]
        uploads = [p for p in (files.get("prompt"), files.get("answer"))
                   if _file(p) and Path(p).suffix.lower() in IMAGE_TYPES]
        row.update(words=words_of(r.text), images=bool(charts or uploads))
    else:
        one, metrics = _dict(r.one), _dict(r.metrics)
        secs = _num(one.get("seconds"))
        secs = secs if secs is not None else _num(metrics.get("speaking_seconds"))
        row.update(secs=secs, duration=_clock(secs) if secs is not None else "—",
                   spoken=int(_num(metrics.get("words")) or 0))
    return row


def _page_int(raw: str | None) -> int:
    try:
        return int(raw or 1)
    except ValueError:
        return 1


async def _list_view(request: Request, kind: Kind):
    if (r := _admin_only(request)) is not None:
        return r
    f = Filters.parse(kind, request.query_params)
    async with get_session() as session:
        data = await list_page(session, kind, f, _page_int(request.query_params.get("page")))
    return templates.TemplateResponse(request, "archive_list.html", {
        "active": kind.active, "kind": kind, "f": f, "query": f.query(), "periods": _periods(f),
        "bands": [x / 2 for x in range(2, 19)], "hold": request.query_params.get("hold") == "1", **data,
    })


@router.get("/archive")
async def archive_home(request: Request):
    if (r := _admin_only(request)) is not None:
        return r
    return RedirectResponse("/archive/writing", status_code=303)


@router.get("/archive/writing", response_class=HTMLResponse)
async def writing_list(request: Request):
    return await _list_view(request, WRITING)


@router.get("/archive/speaking", response_class=HTMLResponse)
async def speaking_list(request: Request):
    return await _list_view(request, SPEAKING)


# --- one check ----------------------------------------------------------------------------------------------------


class _Sections:
    """Builds each part of a detail page on its own: one that fails on an unexpected row is left out (and
    logged), the rest of the page still shows."""

    def __init__(self, kind: Kind, sub_id: int):
        self.where = f"{kind.code.upper()}#{sub_id}"
        self.failed: list[str] = []

    async def run(self, name: str, build, default=None):
        try:
            result = build()
            if hasattr(result, "__await__"):
                result = await result
            return result
        except Exception:  # noqa: BLE001 - an unexpected row: the page still opens
            logger.exception("Archive %s: the %s section failed", self.where, name)
            self.failed.append(name)
            return default


async def _header(session, kind: Kind, sub, f: Filters, s: _Sections) -> dict:
    """What every detail page starts with: the student, where the question came from, how it was paid and what it
    cost, the teachers' marks and comments, the neighbours in the list."""
    rd = _dict(sub.report_data)
    task = _value(sub.task_type if kind is WRITING else sub.part)
    out = {"rd": rd, "task": task, "label": (insights.WRITING_LABELS.get(task, "Writing") if kind is WRITING
                                             else SPEAKING_LABELS.get(task, "Speaking")),
           "when": insights.fmt(sub.created_at),
           "age_days": (insights.now() - sub.created_at).days if sub.created_at else None,
           "is_test": bool(sub.is_test), "coins": sub.coins_spent or 0}
    days = out["age_days"]
    out["ago"] = insights.ago(sub.created_at) if days is None or days < 30 else f"{days} kun oldin"
    out["age"] = "bugun" if days == 0 else f"{days} kun oldin" if days is not None else ""
    out["user"] = await session.get(User, sub.user_id)
    out["payer"] = await session.get(User, sub.paid_by_id) if sub.paid_by_id else None
    out["criteria"] = criteria(kind.code, sub.criteria_scores, task)
    out["band"] = _num(sub.overall_band)

    def paid():
        pw = _dict(rd.get("paid_with"))
        mode = pw.get("mode")
        label = PAID_EXTRA.get(mode) or insights.PAID_NAMES.get(mode, mode) if mode else (
            "guruh to'ladi" if sub.paid_by_id else "—")
        return {"mode": _str(label), "coins": sub.coins_spent or 0}

    out["paid"] = await s.run("paid", paid, {"mode": "—", "coins": sub.coins_spent or 0})
    out["cost"] = await s.run("cost", lambda: cost_of(rd), {"total": 0.0, "parts": [], "calls": None, "models": []})
    out["seconds"] = _num(rd.get("seconds"))
    resub = rd.get("resubmission_of")
    out["resubmission_of"] = resub if isinstance(resub, int) else None

    async def source():
        src = {"practice": None, "assignment": None, "mock": None}
        if sub.practice_question_id:
            q = await session.get(PracticeQuestion, sub.practice_question_id)
            src["practice"] = {"id": sub.practice_question_id, "q": q, "module": _value(q.module) if q else "",
                               "channel": CHANNELS.get(q.source, q.source) if q and q.source else None,
                               "image": bool(q and _file(q.image_path))}
        if sub.assignment_part_id:
            part = await session.get(AssignmentPart, sub.assignment_part_id)
            a = await session.get(Assignment, part.assignment_id) if part else None
            g = await session.get(Group, a.group_id) if a else None
            src["assignment"] = {"part": part, "a": a, "group": g,
                                 "teacher": await session.get(User, g.teacher_id) if g else None,
                                 "center": await session.get(Center, g.center_id) if g and g.center_id else None}
        mock = (await session.execute(select(MockTestPart, MockTestSession).join(
            MockTestSession, MockTestSession.id == MockTestPart.session_id).where(
            MockTestPart.submission_id == sub.id,
            MockTestPart.submission_type == ("writing" if kind is WRITING else "speaking")).limit(1))).first()
        if mock is not None:
            src["mock"] = {"session": mock[1], "status": _value(mock[1].status)}
        return src

    out["source"] = await s.run("source", source, {"practice": None, "assignment": None, "mock": None})

    def teacher():
        marks = _dict(sub.teacher_bands)
        if not marks or not isinstance(marks.get("bands"), dict):
            return None
        bands = {k: _num(v) for k, v in marks["bands"].items()}
        overall = _num(marks.get("overall"))
        diff = None
        if overall is not None and all(bands.get(k) is not None for k, _, _ in tb.CRITERIA[kind.code]):
            mine = {("Pronunciation" if c["key"] == "P" else c["key"]): c["band"] for c in out["criteria"][:4]}
            diff = tb.difference(kind.code, out["band"], mine, {"bands": bands, "overall": overall})
        return {"bands": bands, "overall": overall, "by": _str(marks.get("by")),
                "role": _str(marks.get("role")), "approved": marks.get("approved", True), "note": _str(marks.get("note")),
                "at": _str(marks.get("at"))[:16].replace("T", " "), "diff": diff}

    out["teacher"] = await s.run("teacher", teacher)

    async def comments():
        rows = (await session.execute(select(SubmissionComment, User).join(
            User, User.id == SubmissionComment.teacher_id, isouter=True).where(
            SubmissionComment.kind == kind.code, SubmissionComment.submission_id == sub.id)
            .order_by(SubmissionComment.created_at))).all()
        return [{"c": c, "who": u, "when": insights.fmt(c.created_at)} for c, u in rows]

    out["comments"] = await s.run("comments", comments, [])

    async def neighbours():
        m = kind.model
        conds = f.conditions(kind)
        newer = (await session.execute(select(func.min(m.id)).where(m.id > sub.id, *conds))).scalar()
        older = (await session.execute(select(func.max(m.id)).where(m.id < sub.id, *conds))).scalar()
        return newer, older

    out["newer"], out["older"] = await s.run("neighbours", neighbours, (None, None))
    pdf = _file(sub.feedback_pdf_path)
    out["pdf"] = {"url": f"/reports/{kind.code}/{sub.id}.pdf", "size": _size(pdf.stat().st_size)} if pdf else None
    out["pdf_recorded"] = bool(sub.feedback_pdf_path)
    return out


async def writing_context(session, sub: WritingSubmission, f: Filters) -> dict:
    s = _Sections(WRITING, sub.id)
    ctx = await _header(session, WRITING, sub, f, s)
    rd, files = ctx["rd"], _dict(sub.original_file_paths)
    ctx["prompt_source"] = SOURCES.get(_value(sub.prompt_source), _value(sub.prompt_source))
    ctx["answer_source"] = SOURCES.get(_value(sub.answer_source), _value(sub.answer_source))
    ctx["words"] = words_of(sub.answer_text)
    ctx["mistakes"] = await s.run("mistakes", lambda: mistakes_of(sub.annotations), [])
    ctx["marked"] = await s.run("highlight", lambda: highlight(sub.answer_text or "", [
        m for m in ctx["mistakes"] if not m["old"]]))
    ctx["vocabulary"] = await s.run("vocabulary", lambda: vocabulary_of(sub.vocabulary_json), [])

    def pictures():
        gone = 0
        charts = []
        for n, p in enumerate(_list(files.get("chart"))):
            if _file(p):
                charts.append({"url": f"/checks/w/{sub.id}/chart/{n}", "n": n + 1})
            elif p:
                gone += 1
        uploads = []
        for which, title in (("prompt", "Savolning fayli"), ("answer", "Javobning fayli")):
            p = files.get(which)
            if not p:
                continue
            found = _file(p)
            if found is None:
                gone += 1
                continue
            ext = found.suffix.lower()
            uploads.append({"which": which, "title": title, "url": f"/archive/writing/{sub.id}/upload/{which}",
                            "image": ext in IMAGE_TYPES, "ext": ext.lstrip(".").upper() or "fayl",
                            "size": _size(found.stat().st_size)})
        practice = ctx["source"]["practice"]
        practice_image = None
        if practice and practice["image"]:
            path = practice["q"].image_path
            if path not in _list(files.get("chart")):
                practice_image = f"/questions/{practice['id']}/image"
        return {"charts": charts, "uploads": uploads, "gone": gone, "practice_image": practice_image}

    ctx["pictures"] = await s.run("pictures", pictures, {"charts": [], "uploads": [], "gone": 0,
                                                          "practice_image": None})

    async def chart_key():
        if ctx["task"] != "task1":
            return None
        key_id = rd.get("visual", {}).get("chart_key") if isinstance(rd.get("visual"), dict) else None
        conds = [ChartKey.last_submission_id == sub.id]
        if isinstance(key_id, int) and key_id > 0:
            conds.append(ChartKey.id == key_id)
        if sub.practice_question_id:
            conds.append(ChartKey.practice_question_id == sub.practice_question_id)
        keys = (await session.execute(select(ChartKey).where(or_(*conds)).order_by(ChartKey.id.desc()))).scalars().all()
        if not keys:
            return None
        key = next((k for k in keys if k.id == key_id), None) or next(
            (k for k in keys if k.last_submission_id == sub.id), keys[0])
        return {"key": key, "verified": key.status == "verified", "image": bool(_file(key.image_path)),
                "judged_by": isinstance(key_id, int) and key_id == key.id}

    ctx["chart_key"] = await s.run("chart key", chart_key)
    ctx["visual"] = await s.run("chart reading", lambda: visual_of(rd.get("visual")))
    ctx["model_answer"] = await s.run("model answer", lambda: _model_meta(rd.get("model_answer")))
    ctx["failed"] = s.failed
    return ctx


def _model_meta(raw) -> dict | None:
    m = _dict(raw)
    if not m:
        return None
    return {"source": _str(m.get("source")), "aimed_at": _num(m.get("aimed_at")), "provisional": m.get("provisional"),
            "writer": _str(m.get("writer")), "shows": [str(x) for x in _list(m.get("shows"))],
            "alternative": bool(m.get("alternative")), "refs": len(_list(m.get("reference_ids")))}


def visual_of(raw) -> dict | None:
    """A Task 1 chart's data sheet (services/ai/schemas.py VisualSheet) as the page shows it: per block a grid
    (series x points), a process's steps or a map's features."""
    sheet = _dict(raw)
    if not sheet:
        return None
    blocks = []
    for b in _list(sheet.get("blocks")):
        if not isinstance(b, dict):
            continue
        block = {"type": _str(b.get("visual_type")), "title": _str(b.get("title")), "units": _str(b.get("units")),
                 "overall": _str(b.get("overall")), "points": [], "rows": [], "steps": [], "features": []}
        series, cells = [], {}
        for v in _list(b.get("values")):
            if not isinstance(v, dict):
                continue
            s, point = _str(v.get("series")), _str(v.get("point"))
            if s not in series:
                series.append(s)
            if point not in block["points"]:
                block["points"].append(point)
            value = _num(v.get("value"))
            text = _str(v.get("text")) or ("" if value is None else f"{value:g}")
            note = _str(v.get("note"))
            if _num(v.get("precision")):
                note = f"±{_num(v.get('precision')):g}" + (f" · {note}" if note else "")
            cells[(s, point)] = {"text": text or "?", "unsure": v.get("status") == "uncertain", "note": note}
        block["rows"] = [{"label": s, "cells": [cells.get((s, p)) for p in block["points"]]} for s in series]
        process = _dict(b.get("process"))
        block["steps"] = [_str(x) for x in _list(process.get("steps"))]
        for feature in _list(b.get("map_features")):
            if isinstance(feature, dict):
                block["features"].append({"name": _str(feature.get("name")), "location": _str(feature.get("location")),
                                          "states": [f"{_str(x.get('time'))}: {_str(x.get('status'))}"
                                                     + (f" ({_str(x.get('detail'))})" if x.get("detail") else "")
                                                     for x in _list(feature.get("states")) if isinstance(x, dict)]})
        blocks.append(block)
    features = _dict(sheet.get("features"))
    return {"blocks": blocks, "readability": _str(sheet.get("readability")),
            "note": _str(sheet.get("readability_note")), "unreadable": [_str(x) for x in _list(sheet.get("unreadable"))],
            "checks": [_str(x) for x in _list(sheet.get("checks"))], "reads": sheet.get("reads"),
            "overview": [_str(x) for x in _list(features.get("expected_overview"))],
            "comparisons": [_str(x) for x in _list(features.get("key_comparisons"))],
            "grouping": _str(features.get("suggested_grouping"))}


_QA = re.compile(r"Q(\d+): (.*?)\nA: (.*?)(?=\n\nQ\d+: |\Z)", re.S)


async def speaking_context(session, sub: SpeakingSubmission, f: Filters) -> dict:
    s = _Sections(SPEAKING, sub.id)
    ctx = await _header(session, SPEAKING, sub, f, s)
    rd = ctx["rd"]
    ctx["mistakes"] = await s.run("mistakes", lambda: mistakes_of(sub.annotations), [])
    ctx["vocabulary"] = await s.run("vocabulary", lambda: vocabulary_of(sub.vocabulary_json), [])

    def answers():
        questions = [_str(q) for q in _list(rd.get("questions"))]
        transcripts = [_str(t) for t in _list(rd.get("transcripts"))]
        parts = [_str(p) for p in _list(rd.get("question_parts"))]
        if not questions:  # saved before report_data: "1. ...\n2. ..." and "Q1: ...\nA: ..." (bot/handlers/speaking.py)
            pairs = _QA.findall(sub.transcript_text or "")
            if pairs:
                questions = [q.strip() for _, q, _ in pairs]
                transcripts = [a.strip() for _, _, a in pairs]
            else:
                questions = [sub.question_text or ""]
                transcripts = [sub.transcript_text or ""]
        one = _dict(rd.get("one_recording"))
        spans = [x for x in _list(one.get("split")) if x]  # the answered questions' pieces, in order
        models = []
        for i, a in enumerate(_list(rd.get("model_answers"))):
            if isinstance(a, dict):
                models.append({"i": i, "label": _str(a.get("label")).upper(), "text": _str(a.get("text")),
                               "aimed_at": _num(a.get("aimed_at")), "source": _str(a.get("source")),
                               "words": a.get("words") if isinstance(a.get("words"), int) else words_of(a.get("text")),
                               "voice": bool(a.get("voice_file_id")),
                               "voice_url": f"/archive/speaking/{sub.id}/voice/{i}" if a.get("voice_file_id") else None})
        out, placed = [], set()
        for i, q in enumerate(questions):
            label = f"Q{i + 1}"
            said = transcripts[i] if i < len(transcripts) else ""
            mine = [m for m in ctx["mistakes"] if not m["old"] and m.get("q") == label]
            if len(questions) == 1:
                mine = [m for m in ctx["mistakes"] if not m["old"] and m.get("q") in ("", label)]
            placed.update(id(m) for m in mine)
            piece = []
            if i < len(spans):
                for span in _list(spans[i]):
                    if isinstance(span, list) and len(span) == 2 and _num(span[0]) is not None:
                        piece.append({"start": _num(span[0]), "end": _num(span[1]),
                                      "label": f"{_clock(_num(span[0]))}–{_clock(_num(span[1]))}"})
            part = parts[i] if i < len(parts) else ctx["task"]
            out.append({"label": label, "question": q, "part": SPEAKING.subs.get(part, part), "said": said,
                        "words": words_of(said), "mistakes": mine, "marked": highlight(said, mine),
                        "models": [m for m in models if m["label"] == label], "pieces": piece})
        others = [m for m in ctx["mistakes"] if id(m) not in placed]
        shown = {m["label"] for q in out for m in q["models"]}
        return {"qa": out, "other_mistakes": others, "other_models": [m for m in models if m["label"] not in shown],
                "one": {"pieces": one.get("pieces"), "seconds": _num(one.get("seconds")),
                        "missing": [_str(x) for x in _list(one.get("missing"))]} if one else None}

    ctx.update(await s.run("answers", answers, {"qa": [], "other_mistakes": ctx["mistakes"], "other_models": [],
                                                "one": None}))

    def recording():
        found = _file(sub.audio_file_path)
        if found is None:
            return {"ok": False, "recorded": bool(sub.audio_file_path)}
        return {"ok": True, "url": f"/archive/speaking/{sub.id}/audio", "size": _size(found.stat().st_size),
                "ext": found.suffix.lstrip(".").upper() or "audio"}

    ctx["audio"] = await s.run("recording", recording, {"ok": False, "recorded": bool(sub.audio_file_path)})

    def pronunciation():
        p = _dict(rd.get("pronunciation_scores"))
        rows = [(name, _num(p.get(key))) for key, name in (("accuracy", "Accuracy"), ("fluency", "Fluency"),
                                                            ("prosody", "Prosody"), ("completeness", "Completeness"))]
        if not any(v is not None for _, v in rows):
            return {"rows": [], "measured": rd.get("pronunciation_measured"), "seconds": None}
        return {"rows": rows, "measured": rd.get("pronunciation_measured"), "seconds": _num(p.get("seconds"))}

    ctx["pron"] = await s.run("pronunciation", pronunciation, {"rows": [], "measured": None, "seconds": None})

    def metrics():
        m = _dict(rd.get("metrics"))
        if not m:
            return None
        fillers = [f"{_str(x[0])} ×{x[1]}" for x in _list(m.get("filler_types"))
                   if isinstance(x, list) and len(x) == 2]
        secs = _num(m.get("speaking_seconds"))
        return {"rows": [(label, value) for label, value in (
            ("Gapirgan vaqti", _clock(secs) if secs is not None else None),
            ("So'zlar", m.get("words")), ("So'z / daqiqa", m.get("wpm")),
            ("To'ldiruvchi so'zlar", m.get("fillers")), ("…daqiqasiga", m.get("fillers_per_minute")),
            ("Uzun pauzalar", m.get("long_pauses")), ("Gap o'rtasidagi pauzalar", m.get("mid_sentence_pauses")),
            ("Eng uzun pauza", f"{_num(m.get('longest_pause')):g} s" if _num(m.get("longest_pause")) else None),
        ) if value not in (None, "")], "fillers": fillers}

    ctx["metrics"] = await s.run("voice metrics", metrics)
    ctx["number"] = rd.get("number") if isinstance(rd.get("number"), int) else None
    ctx["failed"] = s.failed
    return ctx


async def _detail(request: Request, kind: Kind, submission_id: int):
    if (r := _admin_only(request)) is not None:
        return r
    f = Filters.parse(kind, request.query_params)
    async with get_session() as session:
        sub = await session.get(kind.model, submission_id)
        if sub is None:
            return templates.TemplateResponse(request, "archive_missing.html", {
                "active": kind.active, "kind": kind, "id": submission_id, "query": f.query()}, status_code=404)
        build = writing_context if kind is WRITING else speaking_context
        try:
            ctx = await build(session, sub, f)
        except Exception:  # noqa: BLE001 - the header itself failed: the bare check, never a 500
            logger.exception("Archive %s#%s: the page could not be built", kind.code, submission_id)
            ctx = None
    base = {"active": kind.active, "kind": kind, "sub": sub, "f": f, "query": f.query(),
            "hold": request.query_params.get("hold") == "1"}
    if ctx is not None:
        try:
            return templates.TemplateResponse(request, f"archive_{kind.slug}.html", {**base, **ctx})
        except Exception:  # noqa: BLE001
            logger.exception("Archive %s#%s: the page did not render", kind.code, submission_id)
    return templates.TemplateResponse(request, "archive_missing.html", {
        **base, "id": submission_id, "bare": True, "criteria": criteria(kind.code, sub.criteria_scores),
        "band": _num(sub.overall_band), "when": insights.fmt(sub.created_at)})


@router.get("/archive/writing/{submission_id}", response_class=HTMLResponse)
async def writing_detail(request: Request, submission_id: int):
    return await _detail(request, WRITING, submission_id)


@router.get("/archive/speaking/{submission_id}", response_class=HTMLResponse)
async def speaking_detail(request: Request, submission_id: int):
    return await _detail(request, SPEAKING, submission_id)


# --- the files (read-only) ----------------------------------------------------------------------------------------


def _serve(path: Path, name: str) -> FileResponse:
    """A file with its own type - inline when the browser can show it - with Range requests (Starlette)."""
    ext = path.suffix.lower()
    media = MEDIA.get(ext) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media, filename=f"{name}{ext}",
                        content_disposition_type="inline" if ext in INLINE else "attachment",
                        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


@router.get("/archive/writing/{submission_id}/upload/{which}")
async def writing_upload(request: Request, submission_id: int, which: str):
    """The question's or the answer's own upload (a photo, a PDF, a Word file) - kept a month."""
    if (r := _admin_only(request)) is not None:
        return r
    if which not in ("prompt", "answer"):
        return Response(status_code=404)
    async with get_session() as session:
        sub = await session.get(WritingSubmission, submission_id)
    found = _file(_dict(sub.original_file_paths).get(which)) if sub is not None else None
    if found is None:
        return Response("Fayl topilmadi", status_code=404)
    return _serve(found, f"Bandzilla_W{submission_id}_{which}")


@router.get("/archive/speaking/{submission_id}/audio")
async def speaking_recording(request: Request, submission_id: int):
    """The recording with its real type (a single voice message is an .ogg) and Range requests, so the player
    can jump anywhere - /audio/s/<id> stays the teachers' route."""
    if (r := _admin_only(request)) is not None:
        return r
    async with get_session() as session:
        sub = await session.get(SpeakingSubmission, submission_id)
    found = _file(sub.audio_file_path) if sub is not None else None
    if found is None:
        return Response("Audio topilmadi", status_code=404)
    return _serve(found, f"Bandzilla_S{submission_id}")


_voices: dict[str, bytes] = {}
VOICE_CACHE = 40


async def _telegram_file(file_id: str) -> bytes | None:
    """A file the bot sent (a model answer's voice): only Telegram keeps it - by the bot's token, else the old
    bot's (MOVED_BOT_TOKEN, before 2026-10-17)."""
    from aiogram import Bot

    for token in filter(None, (settings.telegram_bot_token, settings.moved_bot_token)):
        bot = Bot(token)
        try:
            file = await bot.get_file(file_id)
            return (await bot.download_file(file.file_path)).read()
        except Exception:  # noqa: BLE001 - not this bot's file, or Telegram away
            continue
        finally:
            await bot.session.close()
    return None


@router.get("/archive/speaking/{submission_id}/voice/{index}")
async def model_answer_voice(request: Request, submission_id: int, index: int):
    """A model answer's voice (OpenAI TTS, made when the student first asked for it) - never made here: only
    the copy Telegram keeps, when there is one."""
    if (r := _admin_only(request)) is not None:
        return r
    async with get_session() as session:
        sub = await session.get(SpeakingSubmission, submission_id)
    answers = _list(_dict(sub.report_data if sub is not None else None).get("model_answers"))
    file_id = _dict(answers[index]).get("voice_file_id") if 0 <= index < len(answers) else None
    if not isinstance(file_id, str) or not file_id:
        return Response("Ovoz topilmadi", status_code=404)
    data = _voices.get(file_id)
    if data is None:
        data = await _telegram_file(file_id)
        if data is None:
            return Response("Ovoz topilmadi", status_code=404)
        _voices[file_id] = data
        while len(_voices) > VOICE_CACHE:
            _voices.pop(next(iter(_voices)))
    return Response(data, media_type="audio/ogg", headers={
        "Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f'inline; filename="Bandzilla_S{submission_id}_model_{index + 1}.ogg"'})


@router.get("/archive/hold/{hold_id}")
async def stopped_check(request: Request, hold_id: str):
    """A stopped check (/checks/stopped) in the archive: the report it saved, else the student's next check of
    the same kind after it (the one they got instead), else their checks."""
    if (r := _admin_only(request)) is not None:
        return r
    async with get_session() as session:
        hold = await session.get(CheckHold, hold_id[:32])
        if hold is None:
            return RedirectResponse("/checks/stopped", status_code=303)
        kind = WRITING if hold.module == "w" else SPEAKING
        target = hold.submission_id
        if target is None:
            target = (await session.execute(select(kind.model.id).where(
                kind.model.user_id == hold.user_id, kind.model.created_at >= hold.created_at)
                .order_by(kind.model.id).limit(1))).scalar()
    if target is None:
        return RedirectResponse(f"/archive/{kind.slug}?{urlencode({'q': hold.user_id, 'test': '1', 'hold': '1'})}",
                                status_code=303)
    return RedirectResponse(f"/archive/{kind.slug}/{target}" + ("" if hold.submission_id else "?test=1&hold=1"),
                            status_code=303)
