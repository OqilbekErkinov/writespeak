"""Parses the two calibration books - Jurabek Sanokulov's "IELTS Writing
Collection" Volume 1 (Band 8-9, no commentary) and Volume 2 (Band 6.5-8.5,
examiner-style commentary after each sample) - into
data/calibration/book_samples.json, with each Task 1 visual rendered to
data/calibration/charts/<id>.png.

One entry per sample: id, book, page (the PDF page where the band is
printed), task (T1/T2), question, chart (T1), text, word_count (counted;
stated_words as printed), band_low/band_high ("7.0-7.5" -> 7.0/7.5, "8.0+"
-> 8.0/8.5), commentary (Volume 2). The books are copyrighted: the output
stays in the gitignored data/calibration/, never in the repo.

    python scripts/parse_calibration_books.py --v1 "Final edition 1.pdf" --v2 "1.pdf"
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from services.ai.essay_marking import count_words  # noqa: E402

OUT = ROOT / "data" / "calibration"
BAND_RE = re.compile(r"^\s*Band\s*:?\s*(\d(?:\.\d)?)\s*(?:[-–—]\s*(\d(?:\.\d)?))?\s*(\+)?\s*$", re.IGNORECASE)
WORDS_RE = re.compile(r"^\s*(?:(\d{2,3})\s+words|word count\s*:?\s*(\d{2,3}))\s*\.?\s*$", re.IGNORECASE)
WRITE_RE = re.compile(r"write at least (150|250) words", re.IGNORECASE)
# A Task 1 instruction - not "the conclusion summarizes the essay" in a commentary (Volume 2 p.35).
SUMMARISE_RE = re.compile(r"summari[sz]e (the )?information", re.IGNORECASE)
HEADER_RE = re.compile(r"support us on telegram|@ieltswithsanokulov|sanokulov\.uz|^\s*notes:?\s*$|^\s*S\s*$", re.IGNORECASE)
CHART_ZOOM = 2.5  # ~180 dpi: small chart labels stay readable
# Volume 1 essays already in the model-answer library from Sanokulov's other
# books (Essay Collection, Task 2 Essays) - checked against index.json, 2026-10-22.
REVIEW_SKIP = {62, 78, 82}


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace(" ", " ")
    return re.sub(r"[ \t]+", " ", text).strip()


def _blocks(page) -> list[dict]:
    """Text blocks, each split further where a paragraph ends inside it (a
    short line ending a sentence - Volume 2 p.22 prints a whole essay as one
    block)."""
    out = []
    for block in page.get_text("dict")["blocks"]:
        rows = []
        for line in block.get("lines", []):
            text = _clean("".join(span["text"] for span in line["spans"]))
            if text and not HEADER_RE.search(text):
                rows.append((line["bbox"], text))
        if not rows:
            continue
        right = max(b[2] for b, _ in rows)
        width = right - min(b[0] for b, _ in rows)
        current: list = []
        for i, (bbox, text) in enumerate(rows):
            current.append((bbox, text))
            short = bbox[2] < right - 0.12 * width
            if i == len(rows) - 1 or (short and re.search(r"[.!?\"”’)\]]\s*$", text)):
                out.append({"y0": current[0][0][1], "y1": current[-1][0][3], "lines": [t for _, t in current]})
                current = []
    return sorted(out, key=lambda b: b["y0"])


def _join(lines: list[str]) -> str:
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def _band(match: re.Match) -> tuple[float, float]:
    low = float(match.group(1))
    if match.group(3):  # "8.0+"
        return low, low + 0.5
    return low, float(match.group(2)) if match.group(2) else low


def _find_band(blocks: list[dict]):
    """(block index, line index, match) of the band line, or None."""
    for i, b in enumerate(blocks):
        for j, line in enumerate(b["lines"]):
            m = BAND_RE.match(line)
            if m:
                return i, j, m
    return None


def parse_page(doc, pno: int, book: str) -> dict | None:
    page = doc[pno]
    blocks = _blocks(page)
    found = _find_band(blocks)
    if found is None:
        # Volume 2 pp.40 and 76: a sample whose band is not printed - kept for
        # the chart tests, never for calibration (band None).
        if not any(WRITE_RE.search(x) for b in blocks for x in b["lines"]):
            return None
        found = (len(blocks) - 1, len(blocks[-1]["lines"]), None)
    bi, bj, bm = found
    rects = [r for xref, *_ in page.get_images(full=True) for r in page.get_image_rects(xref)
             if r.width > 100 and r.height > 60]
    img = None
    for r in rects:
        img = r if img is None else img | r
    # The question: everything up to the "Write at least N words" line - or,
    # where a page leaves that line out, its first block.
    qi = next((i for i, b in enumerate(blocks) if any(WRITE_RE.search(x) for x in b["lines"])), None)
    if qi is None:
        qi = next((i for i, b in enumerate(blocks[:4]) if any(SUMMARISE_RE.search(x) for x in b["lines"])), 0)
    q_lines = [x for b in blocks[: qi + 1] for x in b["lines"]]
    q_text = " ".join(q_lines)
    write = WRITE_RE.search(q_text)
    note = ""
    if write or SUMMARISE_RE.search(q_text):
        task = "T1" if (write and write.group(1) == "150") or not write else "T2"
        question = _join([x for x in q_lines if not WRITE_RE.search(x)])
        q_bottom = blocks[qi]["y1"]
    elif img is not None and img.y0 < blocks[0]["y0"]:
        # A Task 1 page that prints only the visual (Volume 2 p.16's maps):
        # the question is not printed.
        task, question, q_bottom, qi, note = "T1", "", img.y0 - 4, -1, "question not printed"
    else:
        return None  # a "band 8.0" inside a commentary, not a sample

    chart_rect, answer_from = None, q_bottom
    if task == "T1":
        if img is None:
            raise ValueError(f"{book} p{pno + 1}: Task 1 without an embedded chart image")
        chart_rect = pymupdf.Rect(page.rect.x0 + 30, q_bottom + 2, page.rect.x1 - 30, img.y1 + 4)
        answer_from = img.y1 - 5

    # The answer: the blocks after the question (and the chart), before "NNN words".
    stated, paragraphs = None, []
    for b in blocks[qi + 1 : bi + 1]:
        if b["y0"] < answer_from:
            continue
        lines = b["lines"] if b is not blocks[bi] else b["lines"][:bj]
        keep = []
        for x in lines:
            m = WORDS_RE.match(x)
            if m:
                stated = int(m.group(1) or m.group(2))
                continue
            if x.lower().rstrip(":") == "sample":
                continue
            keep.append(x)
        if keep:
            paragraphs.append(_join(keep))

    commentary = ""
    if book == "v2":
        after = blocks[bi]["lines"][bj + 1 :] + [x for b in blocks[bi + 1 :] for x in b["lines"]]
        if pno + 1 < doc.page_count:
            nxt = _blocks(doc[pno + 1])
            if not any(WRITE_RE.search(x) or SUMMARISE_RE.search(x) for b in nxt for x in b["lines"]):
                after += [x for b in nxt for x in b["lines"]]
        commentary = "\n".join(_paragraphs(after))

    low, high = _band(bm) if bm else (None, None)
    if bm is None:
        note = "band not printed"
    sample_id = f"{book}-p{pno + 1:03d}"
    chart = None
    if chart_rect is not None:
        (OUT / "charts").mkdir(parents=True, exist_ok=True)
        chart = f"charts/{sample_id}.png"
        page.get_pixmap(matrix=pymupdf.Matrix(CHART_ZOOM, CHART_ZOOM), clip=chart_rect).save(OUT / chart)
    text = "\n\n".join(paragraphs)
    return {
        "id": sample_id, "book": book, "page": pno + 1, "task": task, "question": question, "chart": chart,
        "text": text, "word_count": count_words(text), "stated_words": stated,
        "band_low": low, "band_high": high, "band_label": bm.group(0).strip() if bm else "", "commentary": commentary,
        "note": note,
    }


def _paragraphs(lines: list[str]) -> list[str]:
    """PDF lines rejoined into paragraphs: a line ending a sentence ends one."""
    out, cur = [], []
    for x in lines:
        cur.append(x)
        if re.search(r"[.!?\]]\s*$", x):
            out.append(_join(cur))
            cur = []
    if cur:
        out.append(_join(cur))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--v1", required=True, help="Volume 1 PDF (Final edition 1.pdf)")
    ap.add_argument("--v2", required=True, help="Volume 2 PDF (1.pdf)")
    ap.add_argument("--review", action="store_true", help="also write Volume 1 as a model-answer library review file")
    args = ap.parse_args()
    samples = []
    for book, path in (("v1", args.v1), ("v2", args.v2)):
        doc = pymupdf.open(path)
        for pno in range(doc.page_count):
            s = parse_page(doc, pno, book)
            if s is not None:
                samples.append(s)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "book_samples.json").write_text(json.dumps(samples, ensure_ascii=False, indent=1), encoding="utf-8")
    for book in ("v1", "v2"):
        mine = [s for s in samples if s["book"] == book]
        print(f"{book}: {len(mine)} samples ({sum(s['task'] == 'T1' for s in mine)} T1, "
              f"{sum(s['task'] == 'T2' for s in mine)} T2)")
        print("  " + " · ".join(f"p{s['page']} {s['task']} {s['band_label'].removeprefix('Band ').strip() or '?'}"
                                for s in mine))
    odd = [s for s in samples if s["stated_words"] and abs(s["stated_words"] - s["word_count"]) > 8]
    for s in odd:
        print(f"  word count differs: {s['id']} stated {s['stated_words']}, counted {s['word_count']}")
    print(f"written {OUT / 'book_samples.json'}")
    if args.review:
        # Volume 1 (Band 8-9) joins the model-answer library as Sanokulov's
        # samples (the owner, 2026-10-22); Volume 2 stays out - it is the
        # calibration test set. Then: scripts/build_sample_index.py --styles.
        review = ROOT / "data" / "books" / "_review" / "IELTS Writing Collection Volume 1.json"
        review.parent.mkdir(parents=True, exist_ok=True)
        chunks = [{"page_number": str(s["page"]), "author": "Jurabek Sanokulov",
                   "content_text": f"{s['question']}\n\n{s['text']}",
                   "task_type": "writing_task1" if s["task"] == "T1" else "writing_task2",
                   "band_level": str(s["band_low"])}
                  for s in samples if s["book"] == "v1" and s["question"] and s["page"] not in REVIEW_SKIP]
        review.write_text(json.dumps({"book_title": "IELTS Writing Collection Volume 1", "chunks": chunks},
                                     ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"written {review} ({len(chunks)} samples)")


if __name__ == "__main__":
    main()
