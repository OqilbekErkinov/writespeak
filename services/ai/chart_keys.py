"""The answer key of a Task 1 chart (the user, 2026-10-28): the bot reads a
chart once, an admin checks its table, and the same chart sent again is
judged by the checked table instead of being read anew - no reading
mistakes, the same answer always gets the same Task Achievement, and one
read instead of two.

- After every Task 1 check the chart's sheet is kept (chart_keys, status
  "auto") with its own copy of the picture; meeting the same chart again
  adds a use, so the web panel (/charts) lists the most used charts first.
- An admin corrects the sheet there and marks it verified (checked_sheet).
- A new check first looks for a verified key (sheet_for): the same picture
  (its difference hash) is used straight away; the same Practice question or
  the same question text only after one read of the student's picture agrees
  with the key - its labels, and its figures within reading error. Anything
  else is read as before (two reads), and kept as a new auto key (remember).

Students mostly send their own photos of a chart (the user), so a key is
recognised by what the picture shows, not only by its file.
"""
from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from PIL import Image, ImageChops, ImageOps
from sqlalchemy import select

from db.database import get_session
from db.models import ChartKey
from services.ai import visual_reading
from services.ai.schemas import VisualSheet
from services.ai.visual_check import visual_kind
from services.ai.visual_features import key_features

logger = logging.getLogger(__name__)

SAME_PICTURE = 6      # difference-hash bits apart: the same picture, re-saved or resized
SAME_TEXT = 0.12      # cosine distance between two questions' embeddings: the same question
LABELS_AGREE = 0.6    # share of the key's labels the read shows
FIGURES_AGREE = 0.7   # share of the shared figures that agree within reading error
WORDS_AGREE = 0.5     # a map or a process: share of the key's words (features, steps) the read has


# --- Recognising a chart ------------------------------------------------------------------------------


def fingerprint(data: bytes) -> str | None:
    """The picture's 64-bit difference hash (hex), its plain borders trimmed
    first - a re-saved, resized or recompressed copy stays a few bits away."""
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("L")
        background = Image.new("L", image.size, image.getpixel((0, 0)))
        bbox = ImageChops.difference(image, background).point(lambda p: 255 if p > 24 else 0).getbbox()
        if bbox and (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) > 0.3 * image.width * image.height:
            image = image.crop(bbox)
        px = list(image.resize((9, 8), Image.LANCZOS).getdata())
        bits = 0
        for row in range(8):
            for col in range(8):
                bits = bits << 1 | (px[row * 9 + col] > px[row * 9 + col + 1])
        return f"{bits:016x}"
    except Exception as e:  # never in the way of a check
        logger.warning("No fingerprint for a chart picture: %r", e)
        return None


def distance(a: str | None, b: str | None) -> int:
    """Bits apart (64 when either is missing)."""
    if not a or not b:
        return 64
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _norm(label) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(label).lower())


def _cells(blocks: list[dict]) -> dict[tuple[str, str], float]:
    """(series, point) -> figure, from a read (VisualBlock: "data") or a sheet
    (SheetBlock: "values")."""
    out = {}
    for b in blocks:
        for v in b.get("values") or b.get("data") or []:
            if v.get("value") is not None:
                out[(_norm(v["series"]), _norm(v["point"]))] = float(v["value"])
    return out


def _words(blocks: list[dict]) -> set[str]:
    """The words of a map's features and a process's steps."""
    text = []
    for b in blocks:
        for f in b.get("map_features") or []:
            text.append(f"{f.get('name', '')} {f.get('location', '')}")
        text += ((b.get("process") or {}).get("steps") or [])
    return {w for w in re.findall(r"[a-z]{3,}", " ".join(text).lower())}


def agrees(read_blocks: list[dict], key_blocks: list[dict]) -> tuple[bool, str]:
    """Is the read the key's chart? Most of the key's labels in it (series
    and points either way round), and its figures within reading error; for
    a map or a process, most of the key's words."""
    key, read = _cells(key_blocks), _cells(read_blocks)
    if key:
        key_labels = ({s for s, _ in key} | {p for _, p in key}) - {""}
        read_labels = ({s for s, _ in read} | {p for _, p in read}) - {""}
        labels = len(key_labels & read_labels) / len(key_labels) if key_labels else 1.0
        if labels < LABELS_AGREE:
            return False, f"labels {labels:.0%}"
        shared = [(a, read.get(k, read.get((k[1], k[0])))) for k, a in key.items()]
        shared = [(a, b) for a, b in shared if b is not None]
        if len(shared) < min(3, len(key)):
            return False, f"labels {labels:.0%}, {len(shared)} figures in common"
        scale = max(abs(a) for a in key.values()) or 1.0
        ok = sum(abs(a - b) <= max(0.08 * abs(a), 0.04 * scale, 0.5) for a, b in shared)
        why = f"labels {labels:.0%}, figures {ok}/{len(shared)}"
        return ok / len(shared) >= FIGURES_AGREE, why
    key_words, read_words = _words(key_blocks), _words(read_blocks)
    if not key_words:
        return False, "nothing to compare"
    share = len(key_words & read_words) / len(key_words)
    return share >= WORDS_AGREE, f"words {share:.0%}"


# --- A check: the key's sheet, or the chart read ---------------------------------------------------------


@dataclass
class Lookup:
    """What a check found out about its chart, for remember()."""

    fingerprint: str | None = None
    embedding: list[float] | None = None
    key_id: int = 0  # the verified key the check is judged by
    how: str = ""


async def _embedding(question: str) -> list[float] | None:
    from services.ai.rag_book_search import embed_text

    try:
        return await embed_text(question[:4000])
    except Exception as e:
        logger.warning("No embedding for a chart question: %r", e)
        return None


async def _candidates(session, status: str, look: Lookup,
                      practice_question_id: int | None) -> list[tuple[ChartKey, int]]:
    """Keys of `status` that may be this chart, the likeliest first: (key,
    bits apart) - the same Practice question, the same picture, or the same
    question text (that one only a candidate: its figures must agree too)."""
    rows = (await session.execute(
        select(ChartKey.id, ChartKey.fingerprint, ChartKey.practice_question_id).where(ChartKey.status == status)
    )).all()
    if not rows:
        return []
    ids = {r.id for r in rows if practice_question_id and r.practice_question_id == practice_question_id}
    ids |= {r.id for r in rows if distance(r.fingerprint, look.fingerprint) <= SAME_PICTURE}
    if look.embedding is not None:
        near = await session.execute(
            select(ChartKey.id).where(ChartKey.status == status, ChartKey.embedding.is_not(None),
                                      ChartKey.embedding.cosine_distance(look.embedding) <= SAME_TEXT)
            .order_by(ChartKey.embedding.cosine_distance(look.embedding)).limit(5))
        ids |= set(near.scalars())
    if not ids:
        return []
    keys = (await session.execute(select(ChartKey).where(ChartKey.id.in_(ids)))).scalars().all()
    found = [(k, distance(k.fingerprint, look.fingerprint)) for k in keys]
    return sorted(found, key=lambda x: (x[1], x[0].id))


def _keyed(key: ChartKey) -> VisualSheet:
    return VisualSheet.model_validate({**key.sheet, "chart_key": key.id})


async def sheet_for(question: str, images: list[tuple[bytes, str]],
                    practice_question_id: int | None = None) -> tuple[VisualSheet | None, Lookup]:
    """The chart's sheet for a check: a verified key's when it is this chart,
    else read (visual_reading.read_visual). Raises ChartImageRejected as
    read_visual does."""
    look = Lookup()
    if len(images) != 1:  # a question in several pictures: read as before
        return await visual_reading.read_visual(question, images), look
    look.fingerprint = fingerprint(images[0][0])
    look.embedding = await _embedding(question)
    try:
        async with get_session() as session:
            candidates = await _candidates(session, "verified", look, practice_question_id)
    except Exception:
        logger.exception("Chart keys could not be looked up - reading the chart")
        candidates = []
    for key, bits in candidates:
        if bits <= SAME_PICTURE:  # the same picture: nothing to read
            look.key_id, look.how = key.id, f"the same picture ({bits} bits apart)"
            logger.info("Chart key #%d used: %s", key.id, look.how)
            return _keyed(key), look
    if not candidates:
        return await visual_reading.read_visual(question, images), look
    try:
        first = await visual_reading.first_read(question, images)
    except visual_reading.ChartImageRejected:
        first = None
    if first is not None and first.readability != "unreadable":
        blocks = [b.model_dump() for b in first.visuals]
        for key, _ in candidates:
            ok, why = agrees(blocks, key.sheet.get("blocks", []))
            if ok:
                look.key_id, look.how = key.id, f"recognised ({why})"
                logger.info("Chart key #%d used: %s", key.id, look.how)
                return _keyed(key), look
            logger.info("Chart key #%d is not this chart: %s", key.id, why)
    return await visual_reading.read_visual(question, images, first=first), look


async def remember(question: str, images: list[tuple[bytes, str]], sheet: VisualSheet | None, look: Lookup,
                   practice_question_id: int | None, submission_id: int | None) -> None:
    """After a check: one more use of the key it was judged by, or of the
    auto key of the same chart; else the chart kept as a new auto key for an
    admin to check. Never fails the check."""
    from services.storage.file_storage import save_practice_image

    try:
        async with get_session() as session:
            key = await session.get(ChartKey, look.key_id) if look.key_id else None
            if key is None and sheet is not None and sheet.readability != "unreadable" and sheet.blocks \
                    and len(images) == 1:
                data = sheet.model_dump()
                for other, bits in await _candidates(session, "auto", look, practice_question_id):
                    same_question = practice_question_id and other.practice_question_id == practice_question_id
                    if bits <= SAME_PICTURE or same_question \
                            or agrees(data["blocks"], other.sheet.get("blocks", []))[0]:
                        key = other
                        break
                if key is None:
                    session.add(ChartKey(
                        status="auto", source="practice" if practice_question_id else "student",
                        question_text=question, image_path=save_practice_image(images[0][0], folder="chart_keys"),
                        fingerprint=look.fingerprint, embedding=look.embedding, sheet=data,
                        kind=visual_kind(question, data), practice_question_id=practice_question_id, uses=1,
                        last_submission_id=submission_id))
                    await session.commit()
                    return
            if key is not None:
                key.uses += 1
                key.last_submission_id = submission_id
                await session.commit()
    except Exception:
        logger.exception("The chart was not kept as a chart key - the check itself is done")


# --- The admin's check ---------------------------------------------------------------------------------------


def checked_sheet(sheet: dict, admin: str) -> dict:
    """The sheet as an admin checked it: every figure verified, nothing
    unreadable, the key features worked out again from the checked figures."""
    out = {**sheet, "readability": "clear", "readability_note": "", "unreadable": [], "checks": [], "reads": 2}
    out["blocks"] = [{**b, "values": [{**v, "status": "verified", "note": f"checked by {admin}"}
                                      for v in b.get("values", [])]} for b in sheet.get("blocks", [])]
    out.pop("chart_key", None)
    out["features"] = key_features(VisualSheet.model_validate(out)).model_dump()
    return out


def grid(block: dict) -> tuple[list[str], list[str], dict[tuple[str, str], dict]]:
    """A block's figures as a table for the panel: series (rows), points
    (columns) in their order on the sheet, and the cells."""
    series, points, cells = [], [], {}
    for v in block.get("values", []):
        if v["series"] not in series:
            series.append(v["series"])
        if v["point"] not in points:
            points.append(v["point"])
        cells[(v["series"], v["point"])] = v
    return series, points, cells


_NUMBER = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?|[-+]?\.\d+")


def _indices(form: dict[str, str], pattern: str) -> list[int]:
    """The numbers in the form's field names that match `pattern` (one group)."""
    found = (re.fullmatch(pattern, name) for name in form)
    return sorted(int(m.group(1)) for m in found if m)


def edited_sheet(sheet: dict, form: dict[str, str]) -> dict:
    """The sheet with an admin's edits from the panel's form (field names as
    webapp/templates/chart_edit.html writes them, b<block>.<field>):
    the labels, a figure in every cell (a word for a table cell of words;
    a column or row left empty is dropped), a map's features, a process's
    steps and stages. Each cell keeps its precision; a new one gets its
    block's usual precision."""
    out = {**sheet, "blocks": []}
    for i, block in enumerate(sheet.get("blocks", [])):
        b = dict(block)
        get = lambda name, default="": form.get(f"b{i}.{name}", default)  # noqa: E731
        b["title"], b["units"], b["overall"] = get("title", b.get("title", "")).strip(), \
            get("units", b.get("units", "")).strip(), get("overall", b.get("overall", "")).strip()
        if f"b{i}.axis" in form:
            # "0, 1,000, 2,000": labels apart by a comma and a space, a thousands comma kept
            axis = get("axis")
            b["axis_labels"] = [x.strip() for x in re.split(r",\s+|;" if re.search(r",\s", axis) else r"[,;]", axis)
                                if x.strip()]
        if f"b{i}.s0" in form or f"b{i}.p0" in form:
            series, points, cells = grid(block)
            precisions = sorted(v.get("precision") or 0.0 for v in block.get("values", []))
            usual = precisions[len(precisions) // 2] if precisions else 0.0
            rows, cols = _indices(form, rf"b{i}\.s(\d+)"), _indices(form, rf"b{i}\.p(\d+)")
            values = []
            for r in rows:
                for c in cols:
                    raw = form.get(f"b{i}.v{r}.{c}", "").strip()
                    if not raw:
                        continue
                    old = cells.get((series[r], points[c])) if r < len(series) and c < len(points) else None
                    number = _NUMBER.fullmatch(raw.replace(" ", "").rstrip("%"))
                    values.append({
                        "series": form.get(f"b{i}.s{r}", "").strip(), "point": form.get(f"b{i}.p{c}", "").strip(),
                        "value": float(number.group(0).replace(",", "")) if number else None,
                        "text": "" if number else raw,
                        "printed": old["printed"] if old else usual == 0,
                        "precision": (old.get("precision") or 0.0) if old else usual,
                        "status": "verified", "note": ""})
            b["values"] = values
            colours = {e.get("label"): e.get("colour", "") for e in block.get("legend", [])}
            if block.get("legend"):
                b["legend"] = [{"label": s, "colour": colours.get(s, "")}
                               for s in dict.fromkeys(v["series"] for v in values)]
        if block.get("map_features") is not None and f"b{i}.t0" in form:
            times = [form[f"b{i}.t{j}"].strip() for j in _indices(form, rf"b{i}\.t(\d+)")]
            features = []
            for k in _indices(form, rf"b{i}\.f(\d+)\.name"):
                name = form.get(f"b{i}.f{k}.name")
                if name is None or not name.strip():
                    continue
                features.append({
                    "name": name.strip(), "location": form.get(f"b{i}.f{k}.location", "").strip(),
                    "states": [{"time": t, "status": form.get(f"b{i}.f{k}.st{j}", "present"),
                                "detail": form.get(f"b{i}.f{k}.d{j}", "").strip()} for j, t in enumerate(times)]})
            b["map_features"] = features
            b["time_points"] = times
        if block.get("process") and f"b{i}.steps" in form:
            steps = [s.strip() for s in get("steps").splitlines() if s.strip()]
            stages = []
            for line in get("stages").splitlines():
                name, _, nums = line.partition(":")
                found = []
                for part in nums.split(","):
                    lo, _, hi = part.strip().partition("-")
                    if lo.strip().isdigit():
                        found += list(range(int(lo), int(hi) + 1)) if hi.strip().isdigit() else [int(lo)]
                if name.strip():
                    stages.append({"name": name.strip(), "steps": [n for n in found if 1 <= n <= len(steps)]})
            b["process"] = {**block["process"], "steps": steps, "stages": stages}
        out["blocks"].append(b)
    return out


async def save(key_id: int, sheet: dict, admin: str, verified: bool) -> bool:
    """An admin's edits kept; with `verified` the key is used for judging from
    now on. False when there is no such key."""
    async with get_session() as session:
        key = await session.get(ChartKey, key_id)
        if key is None:
            return False
        if verified:
            sheet = checked_sheet(sheet, admin)
            key.status, key.verified_by, key.verified_at = "verified", admin, datetime.now(timezone.utc)
        else:
            sheet = {**sheet, "features": key_features(VisualSheet.model_validate(sheet)).model_dump()}
        key.sheet = sheet
        key.kind = visual_kind(key.question_text, sheet)
        await session.commit()
        return True
