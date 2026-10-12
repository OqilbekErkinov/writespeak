"""Builds the model-answer library, data/sample_library/ (final report prompt, Part 2).

    docker compose exec bot python scripts/build_sample_index.py [--styles]

Reads the verified review files in data/books/_review/ (one per book - see
scripts/ingest_books.py and the README for how a book gets there) and writes
  index.json       one entry per Writing sample: id, author, book, task_type
                   (task2 / task1_academic), subtype, topic, question, band
                   (as stated in the book), word_count, text, plus a digest
                   of its content;
  embeddings.json  an embedding of each sample's question.
Labels and embeddings of unchanged samples are reused, so re-running after
adding a book only pays for the new samples. --styles also builds every
writer's style profiles now, instead of on first use (styles/<writer>.json).
Re-run whenever a book is added or a review file changes.

Every sample needs its writer and its band. A book that states neither per
essay is listed in BOOK_DEFAULTS (decided with the owner); any other sample
without them is reported and left out - never guessed.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.config import settings  # noqa: E402
from services.ai.essay_marking import count_words  # noqa: E402
from services.ai.sample_bank import (  # noqa: E402
    LibrarySample,
    _slug,
    embed_texts,
    embeddings_path,
    get_style_profile,
    index_path,
    label_question,
    load_library,
    split_question,
    write_json,
)

REVIEW_DIR = settings.books_dir / "_review"
TASK_TYPES = {"writing_task1": "task1_academic", "writing_task2": "task2"}
BOOK_DEFAULTS = {
    # Lists its contributors but not who wrote which essay, and states no band
    # per essay. The owner (2026-10-12): use these like the other samples, as
    # one writer unit; band 8.5 = the Writing score most of its contributors,
    # including the main one, declare.
    "IELTS Writing Recap 2023": {"author": "Diyorbek's IELTS team", "band": 8.5},
}
LABEL_CONCURRENCY = 8
EMBED_BATCH = 100


def _digest(*parts: str) -> str:
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def _read_books() -> tuple[list[dict], list[str]]:
    entries, problems = [], []
    for review in sorted(REVIEW_DIR.glob("*.json")):
        data = json.loads(review.read_text(encoding="utf-8"))
        book = data["book_title"]
        defaults = BOOK_DEFAULTS.get(book, {})
        for i, chunk in enumerate(data["chunks"], 1):
            task_type = TASK_TYPES.get(chunk.get("task_type") or "")
            if task_type is None:
                continue
            question, text = split_question(chunk["content_text"])
            author = chunk.get("author") or defaults.get("author")
            band = chunk.get("band_level") or defaults.get("band")
            where = f"{book}, chunk {i} (page {chunk.get('page_number')})"
            if not question:
                problems.append(f"{where}: no question before the answer")
                continue
            if not author or not band:
                problems.append(f"{where}: no {'author' if not author else 'band'}")
                continue
            entries.append(
                {
                    "id": f"{_slug(book)}-{i:03d}",
                    "author": author,
                    "book": book,
                    "task_type": task_type,
                    "subtype": None,
                    "topic": None,
                    "question": question,
                    "band": float(band),
                    "word_count": count_words(text),
                    "text": text,
                    "digest": _digest(task_type, question, text),
                }
            )
    return entries, problems


async def build(styles: bool) -> None:
    entries, problems = _read_books()
    if not entries:
        print(f"No Writing samples found in {REVIEW_DIR}.")
        return
    old = {}
    if index_path().exists():
        old = {e["id"]: e for e in json.loads(index_path().read_text(encoding="utf-8"))}
    old_vectors = json.loads(embeddings_path().read_text(encoding="utf-8")) if embeddings_path().exists() else {}

    # Labels (subtype + topic): reused when the sample hasn't changed.
    to_label = []
    for e in entries:
        prev = old.get(e["id"])
        if prev and prev.get("digest") == e["digest"]:
            e["subtype"], e["topic"] = prev["subtype"], prev["topic"]
        else:
            to_label.append(e)
    semaphore = asyncio.Semaphore(LABEL_CONCURRENCY)

    async def label(e: dict) -> None:
        async with semaphore:
            e["subtype"], e["topic"] = await label_question(e["question"], e["task_type"], answer_start=e["text"])

    print(f"Labelling {len(to_label)} new or changed sample(s) ...")
    await asyncio.gather(*(label(e) for e in to_label))

    # Question embeddings: reused when the question hasn't changed.
    vectors = {}
    to_embed = []
    for e in entries:
        key = _digest(e["question"])
        prev = old_vectors.get(e["id"])
        if prev and prev.get("digest") == key:
            vectors[e["id"]] = prev
        else:
            to_embed.append((e, key))
    print(f"Embedding {len(to_embed)} question(s) ...")
    for start in range(0, len(to_embed), EMBED_BATCH):
        batch = to_embed[start : start + EMBED_BATCH]
        for (e, key), vector in zip(batch, await embed_texts([e["question"] for e, _ in batch])):
            vectors[e["id"]] = {"digest": key, "vector": [round(x, 6) for x in vector]}

    write_json(embeddings_path(), vectors, indent=None)
    write_json(index_path(), entries)
    print(f"Wrote {len(entries)} samples to {index_path()}")

    table = defaultdict(Counter)
    for e in entries:
        table[(e["task_type"], e["author"])][e["subtype"]] += 1
    for (task_type, author), subtypes in sorted(table.items()):
        print(f"  {task_type:15} {author:28} {sum(subtypes.values()):3}  {dict(subtypes)}")
    if problems:
        print(f"\n{len(problems)} sample(s) left out:")
        for p in problems:
            print("  -", p)

    if styles:
        library = load_library()
        groups: dict[tuple[str, str], list[LibrarySample]] = defaultdict(list)
        for s in library:
            groups[(s.author, s.task_type)].append(s)

        async def profile(author: str, task_type: str, samples: list[LibrarySample]) -> None:
            async with semaphore:
                await get_style_profile(author, task_type, samples)
            print(f"  style profile ready: {author} / {task_type}")

        await asyncio.gather(*(profile(a, t, s) for (a, t), s in sorted(groups.items())))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--styles", action="store_true", help="also build every writer's style profiles now")
    asyncio.run(build(parser.parse_args().styles))


if __name__ == "__main__":
    main()
