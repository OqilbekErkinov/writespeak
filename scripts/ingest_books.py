"""IELTS sample-book ingestion pipeline (Bosqich 6).

Two-step, human-reviewed process — book layouts vary too much to trust a
fully automatic chunker for a "must not be a hallucination" feature:

  1) python scripts/ingest_books.py extract
     Parses every PDF in data/books/, heuristically splits it into candidate
     sample-answer chunks (OCR fallback for scanned pages), and writes one
     review JSON file per book to data/books/_review/<book>.json. Open these
     and fix task_type / band_level / author / chunk boundaries by hand — or
     leave task_type/band_level null to let step 2 auto-fill them with GPT.

     "author" is auto-detected from "Written by <Name>" attribution lines,
     which also act as a chunk boundary — important for books where two
     instructors each answer the same prompt, so their essays never get
     merged into one chunk. ALWAYS verify "author" by hand: it is never
     guessed by GPT (see `load` below), since misattributing a sample to the
     wrong instructor is worse than leaving it out.

  2) python scripts/ingest_books.py load
     Reads the (reviewed) JSON files, fills in any still-missing task_type /
     topic_tags / band_level via GPT, embeds each chunk, and (re)inserts them
     into the book_chunks table. Chunks with no "author" set are skipped
     (with a warning) rather than ingested. Re-running `load` replaces a
     book's existing chunks (matched by book_title), so re-ingestion after
     edits is safe. Writing chunks embed only their question (the first
     paragraph): the sample section matches the student's question against
     the library's questions (services/ai/sample_bank.py). With --prune, books
     that no longer have a review file are removed from the table.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import re
import sys
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdf2image import convert_from_bytes  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from pypdf import PdfReader  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from bot.config import settings  # noqa: E402
from db.database import get_session  # noqa: E402
from db.models import BookChunk  # noqa: E402
from services.ai.ocr import extract_text_from_image  # noqa: E402
from services.ai.openai_client import client  # noqa: E402
from services.ai.rag_book_search import embed_text  # noqa: E402

REVIEW_DIR = settings.books_dir / "_review"
MIN_CHARS_PER_PAGE_BEFORE_OCR_FALLBACK = 20
MIN_CHUNK_CHARS = 200

HEADING_RE = re.compile(
    r"^(sample answer|model answer|band\s*[0-9](\.[0-9])?|task\s*[12]\b|part\s*[123]\b)",
    re.IGNORECASE,
)
# Matches "Written by Christina Khafizova" / "Written by Dilshodbek Ravshanov (IELTS 9.0, WR 8.5)"
AUTHOR_RE = re.compile(
    r"written\s+by\s+([A-Z][A-Za-z.'\-]+(?:\s+[A-Z][A-Za-z.'\-]+){0,3})", re.IGNORECASE
)
# Writing-specific band (e.g. "WR 8.5") takes priority over the overall IELTS band
WR_BAND_RE = re.compile(r"\bWR\s*([0-9](?:\.[0-9])?)", re.IGNORECASE)
BAND_IN_TEXT_RE = re.compile(r"band\s*([0-9](?:\.[0-9])?)", re.IGNORECASE)


async def _extract_book_pages(pdf_path: Path) -> list[str]:
    pdf_bytes = pdf_path.read_bytes()
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages: list[str] = []
    scanned_indices: list[int] = []

    for i, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        if len(text) < MIN_CHARS_PER_PAGE_BEFORE_OCR_FALLBACK:
            pages.append("")
            scanned_indices.append(i)
        else:
            pages.append(text)

    if scanned_indices:
        print(f"  ({len(scanned_indices)} scanned page(s) - running OCR fallback)")
        images = convert_from_bytes(pdf_bytes, dpi=200)
        for i in scanned_indices:
            buf = io.BytesIO()
            images[i].save(buf, format="PNG")
            pages[i] = await extract_text_from_image(buf.getvalue(), mime_type="image/png")

    return pages


def _chunk_pages(pages: list[str]) -> list[dict]:
    """Starts a new chunk at heading-like lines (Sample Answer / Band X /
    Task 1|2 / Part 1|2|3) and — crucially — right after a "Written by
    <Name>" attribution line, which in multi-author collections marks the
    end of one instructor's essay. This keeps each chunk scoped to exactly
    one author so two writers' work on the same prompt never gets merged
    into a single "sample". Beyond that it's intentionally conservative —
    the review step is where real accuracy comes from."""
    chunks: list[dict] = []
    current_lines: list[str] = []
    current_start_page = 1

    def flush(author: str | None) -> None:
        text = "\n".join(current_lines).strip()
        if len(text) >= MIN_CHUNK_CHARS:
            band_match = WR_BAND_RE.search(text) or BAND_IN_TEXT_RE.search(text)
            chunks.append(
                {
                    "page_number": current_start_page,
                    "author": author,
                    "content_text": text,
                    "task_type": None,
                    "topic_tags": None,
                    "band_level": float(band_match.group(1)) if band_match else None,
                }
            )

    for page_no, page_text in enumerate(pages, start=1):
        for line in page_text.splitlines():
            stripped = line.strip()

            author_match = AUTHOR_RE.search(stripped)
            if author_match:
                current_lines.append(line)
                flush(author_match.group(1).strip())
                current_lines = []
                current_start_page = page_no
                continue

            if HEADING_RE.match(stripped) and current_lines:
                flush(None)
                current_lines = []
                current_start_page = page_no

            current_lines.append(line)

    flush(None)
    return chunks


async def cmd_extract() -> None:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    pdf_files = sorted(settings.books_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"No PDFs found in {settings.books_dir}")
        return

    for pdf_path in pdf_files:
        print(f"Extracting {pdf_path.name} ...")
        pages = await _extract_book_pages(pdf_path)
        chunks = _chunk_pages(pages)
        out_path = REVIEW_DIR / f"{pdf_path.stem}.json"
        out_path.write_text(
            json.dumps({"book_title": pdf_path.stem, "chunks": chunks}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  -> {len(chunks)} candidate chunk(s) written to {out_path}")

    print(
        f"\nReview the JSON files in {REVIEW_DIR}: verify \"author\" on every chunk (never "
        "guessed automatically), fix task_type (writing_task1/writing_task2/speaking), "
        "band_level, and chunk boundaries as needed, then run: "
        "python scripts/ingest_books.py load"
    )


class _Classification(BaseModel):
    task_type: Literal["writing_task1", "writing_task2", "speaking"]
    topic_tags: list[str]
    band_level: float


async def _classify_chunk(text: str) -> _Classification:
    """Fills in task_type/topic_tags/band_level for a chunk the reviewer left null."""
    response = await client.beta.chat.completions.parse(
        model=settings.openai_model_grading,
        messages=[
            {
                "role": "system",
                "content": (
                    "Classify this IELTS sample answer excerpt: is it a Writing Task 1 report, "
                    "Writing Task 2 essay, or a Speaking answer? Give 3-6 topic tags (e.g. "
                    "'environment', 'technology') and your best-estimate band level (4-9) if it "
                    "isn't explicitly labelled in the text."
                ),
            },
            {"role": "user", "content": text[:4000]},
        ],
        response_format=_Classification,
    )
    parsed = response.choices[0].message.parsed
    if parsed is None:
        raise RuntimeError("Book chunk classification returned no parsable result")
    return parsed


def _embedding_text(chunk: dict) -> str:
    if chunk["task_type"].startswith("writing_"):
        return re.split(r"\n\s*\n", chunk["content_text"].strip(), maxsplit=1)[0]
    return chunk["content_text"][:6000]


async def cmd_load(prune: bool = False) -> None:
    review_files = sorted(REVIEW_DIR.glob("*.json"))
    if not review_files:
        print(f"No review files found in {REVIEW_DIR}. Run `extract` first.")
        return

    for review_path in review_files:
        data = json.loads(review_path.read_text(encoding="utf-8"))
        book_title = data["book_title"]
        chunks = data["chunks"]
        print(f"Loading {book_title} ({len(chunks)} chunk(s)) ...")

        rows = []
        skipped = 0
        for chunk in chunks:
            # Authorship is never guessed by GPT - a wrong attribution is worse than a
            # missing sample, so unattributed chunks are simply left out of ingestion.
            if not chunk.get("author"):
                print(f"  ! skipping chunk on page {chunk.get('page_number')} - no author set")
                skipped += 1
                continue

            if not chunk.get("task_type") or not chunk.get("band_level"):
                classification = await _classify_chunk(chunk["content_text"])
                chunk["task_type"] = chunk.get("task_type") or classification.task_type
                chunk["topic_tags"] = chunk.get("topic_tags") or classification.topic_tags
                chunk["band_level"] = chunk.get("band_level") or classification.band_level

            embedding = await embed_text(_embedding_text(chunk))
            rows.append(
                BookChunk(
                    book_title=book_title,
                    author=chunk["author"],
                    page_number=chunk.get("page_number"),
                    task_type=chunk["task_type"],
                    topic_tags=chunk.get("topic_tags") or [],
                    band_level=chunk.get("band_level"),
                    content_text=chunk["content_text"],
                    embedding=embedding,
                )
            )

        async with get_session() as session:
            await session.execute(delete(BookChunk).where(BookChunk.book_title == book_title))
            session.add_all(rows)
            await session.commit()
        skip_note = f", {skipped} skipped (no author)" if skipped else ""
        print(f"  -> {len(rows)} chunk(s) ingested{skip_note}.")

    if prune:
        titles = [json.loads(p.read_text(encoding="utf-8"))["book_title"] for p in review_files]
        async with get_session() as session:
            result = await session.execute(delete(BookChunk).where(BookChunk.book_title.not_in(titles)))
            await session.commit()
        print(f"Pruned {result.rowcount} chunk(s) of books without a review file.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["extract", "load"])
    parser.add_argument("--prune", action="store_true", help="load: drop books that have no review file")
    args = parser.parse_args()

    if args.command == "extract":
        asyncio.run(cmd_extract())
    else:
        asyncio.run(cmd_load(prune=args.prune))


if __name__ == "__main__":
    main()
