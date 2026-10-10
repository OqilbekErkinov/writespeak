"""The "Namuna" (sample) section of Writing reports.

Step A - exact match: if the book library (book_chunks, loaded from
data/books/_review by scripts/ingest_books.py) holds the same question for the
same task type, that book sample is shown exactly as printed, with its source.

Step B - style-based (the default): a NEW answer to the student's own question
is written in the style of ONE library writer, never a blend of several:
  * the library sample whose question is closest to the student's (same task
    type; the same Task 2 essay type / Task 1 chart type gets a small bonus)
    decides the writer; exact ties rotate;
  * the references are that sample plus up to two more by the same writer;
  * each writer's style profile is derived once per task type from their own
    samples and cached in data/books/style_profiles/<writer>-<task>.json
    (rebuilt automatically when their samples change);
  * the sample is written by a separate model call given the profile, the
    references and the student's question, at the writer's band (their
    declared IELTS Writing score, stored as the band of their book samples);
  * it must not copy book sentences: any shared run of 8+ words triggers a
    rewrite. The report labels it honestly as written in the author's style,
    never as the author's own text.

If the library is empty, a plain Band 8 sample is written instead - the
section is never empty.
"""
from __future__ import annotations

import base64
import difflib
import json
import logging
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from sqlalchemy import select

from bot.config import settings
from db.database import get_session
from db.models import BookChunk
from services.ai.openai_client import client
from services.ai.rag_book_search import embed_text
from services.ai.schemas import GeneratedSample, StyleProfile

logger = logging.getLogger(__name__)

EXACT_MATCH_RATIO = 0.9
WORD_RANGE = {"task2": (260, 300), "task1": (170, 200)}
COPY_NGRAM = 8  # a shared run of this many words counts as a copied sentence
REFERENCE_SAMPLES = 3
SAME_KIND_BONUS = 0.05
# Recap 2023 does not say which team member wrote each answer, so its samples are
# shown for exact question matches (citing the book) but never imitated as a style.
STYLE_EXCLUDED_AUTHORS = {"Diyorbek's IELTS team"}
PROFILE_SAMPLES = 8
GENERATION_ATTEMPTS = 3
DEFAULT_BAND = 8.0

_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*")


@dataclass
class LibrarySample:
    id: int
    author: str
    book_title: str
    page: int | None
    task_type: str  # "writing_task1" | "writing_task2"
    band: float | None
    question: str
    essay: str
    kind: str  # Task 2 essay type / Task 1 chart type
    embedding: list[float] = field(repr=False, default_factory=list)


@dataclass
class SampleSection:
    kind: str  # "book" | "style" | "generic"
    question: str
    paragraphs: list[str]
    author: str | None = None
    book_title: str | None = None
    page: int | None = None
    source_id: int | None = None  # book_chunks.id for an exact-match book sample

    @property
    def word_count(self) -> int:
        return sum(count_words(p) for p in self.paragraphs)


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def paragraphs_of(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n|\n", text.strip()) if p.strip()]


# --- Question parsing / classification ---------------------------------------------

def split_question(content: str) -> tuple[str, str]:
    """Library chunks start with the question, then a blank line, then the essay."""
    parts = re.split(r"\n\s*\n", content.strip(), maxsplit=1)
    if len(parts) == 2 and len(parts[0].split()) <= 90:
        return parts[0].strip(), parts[1].strip()
    return "", content.strip()


def task2_kind(question: str) -> str:
    q = question.lower()
    if re.search(r"advantages?\b.*\bdisadvantages?|outweigh|benefits?\b.*\b(drawbacks?|disadvantages?)", q):
        return "advantages_disadvantages"
    if re.search(r"discuss both|both (these |of these )?(views|opinions|sides)", q):
        return "discussion"
    if re.search(
        r"(problems?|causes?|reasons?|why)\b.*\b(solutions?|solve|tackle|overcome|measures|done|"
        r"what (can|could|should)|how (can|could|should|to))\b",
        q,
    ):
        return "problem_solution"
    if re.search(r"to what extent|do you agree|agree or disagree", q):
        return "opinion"
    if q.count("?") >= 2 or re.search(r"positive or (a )?negative", q):
        return "two_part"
    return "opinion"


def task1_kind(question: str) -> str:
    q = question.lower()
    for kind, pattern in (
        ("map", r"\bmaps?\b"),
        ("process", r"process|diagram|how .* (is|are) (made|produced)"),
        ("table", r"\btables?\b"),
        ("pie", r"\bpie\b"),
        ("line", r"line graph|line chart"),
        ("bar", r"bar chart|bar graph"),
    ):
        if re.search(pattern, q):
            return kind
    return "chart"


def kind_of(question: str, task_type: str) -> str:
    return task1_kind(question) if task_type.endswith("task1") else task2_kind(question)


_BOILERPLATE = (
    r"you should spend about \d+ minutes on this task",
    r"write at least \d+ words",
    r"write about the following topic",
    r"give reasons for your answer and include any relevant examples from your own knowledge or experience",
    r"summari[sz]e the information by selecting and reporting the main features,? and make comparisons where relevant",
    r"\btask [12]\b",
)


def normalize_question(question: str) -> str:
    q = question.lower()
    for pattern in _BOILERPLATE:
        q = re.sub(pattern, " ", q)
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", q).split())


# --- Library ------------------------------------------------------------------------------

async def load_library() -> list[LibrarySample]:
    async with get_session() as session:
        rows = (
            await session.execute(
                select(BookChunk).where(BookChunk.task_type.in_(["writing_task1", "writing_task2"]))
            )
        ).scalars().all()
    samples = []
    for row in rows:
        task_type = row.task_type.value if hasattr(row.task_type, "value") else row.task_type
        question, essay = split_question(row.content_text)
        samples.append(
            LibrarySample(
                id=row.id,
                author=row.author,
                book_title=row.book_title,
                page=row.page_number,
                task_type=task_type,
                band=row.band_level,
                question=question,
                essay=essay,
                kind=kind_of(question, task_type),
                embedding=list(row.embedding) if row.embedding is not None else [],
            )
        )
    return samples


def find_exact(question: str, task_type: str, library: list[LibrarySample], seed: int) -> LibrarySample | None:
    target = normalize_question(question)
    if len(target.split()) < 5:
        return None
    scored = []
    for s in library:
        if s.task_type != f"writing_{task_type}" or not s.question:
            continue
        ratio = difflib.SequenceMatcher(None, target, normalize_question(s.question)).ratio()
        if ratio >= EXACT_MATCH_RATIO:
            scored.append((round(ratio, 3), s))
    if not scored:
        return None
    best = max(r for r, _ in scored)
    tied = sorted((s for r, s in scored if r == best), key=lambda s: (s.author, s.id))
    return tied[seed % len(tied)]  # several authors answered the same question: rotate


def _cosine(a, b) -> float:
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a @ b) / denom if denom else 0.0


async def choose_writer(
    question: str, task_type: str, library: list[LibrarySample], seed: int
) -> tuple[str, list[LibrarySample]]:
    """Returns (writer, references): the library sample closest to the student's
    question decides the writer; every reference is by that one writer, closest first."""
    stylable = [s for s in library if s.author not in STYLE_EXCLUDED_AUTHORS and s.embedding]
    pool = [s for s in stylable if s.task_type == f"writing_{task_type}"] or stylable
    if not pool:
        raise LookupError("No library samples to take a style from")
    kind = kind_of(question, task_type)
    q_emb = await embed_text(question)
    scores = {
        s.id: round(_cosine(q_emb, s.embedding) + (SAME_KIND_BONUS if s.kind == kind else 0.0), 3)
        for s in pool
    }
    best_score = max(scores.values())
    tied = sorted((s for s in pool if scores[s.id] == best_score), key=lambda s: (s.author, s.id))
    best = tied[seed % len(tied)]
    others = sorted(
        (s for s in pool if s.author == best.author and s.id != best.id),
        key=lambda s: (s.kind != kind, -scores[s.id]),
    )
    return best.author, [best] + others[: REFERENCE_SAMPLES - 1]


def author_band(samples: list[LibrarySample]) -> float:
    bands = [s.band for s in samples if s.band]
    return round(statistics.median(bands) * 2) / 2 if bands else DEFAULT_BAND


# --- Style profiles --------------------------------------------------------------------------

def _profile_path(author: str, task_type: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", author.lower()).strip("-") or "author"
    return settings.books_dir / "style_profiles" / f"{slug}-{task_type}.json"


async def get_style_profile(author: str, task_type: str, author_samples: list[LibrarySample]) -> StyleProfile:
    """`author_samples`: the writer's samples of this task type (Task 1 reports and
    Task 2 essays are written differently, so each gets its own profile)."""
    path = _profile_path(author, task_type)
    source_ids = sorted(s.id for s in author_samples)
    if path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached.get("source_ids") == source_ids:
                return StyleProfile(**cached["profile"])
        except Exception:
            logger.warning("Unreadable style profile %s - rebuilding", path, exc_info=True)

    chosen = sorted(author_samples, key=lambda s: s.id)[:PROFILE_SAMPLES]
    samples_text = "\n\n".join(
        f"--- Sample {i} ---\nQuestion: {s.question}\n\n{s.essay}" for i, s in enumerate(chosen, 1)
    )
    response = await client.beta.chat.completions.parse(
        model=settings.openai_model_grading,
        messages=[
            {
                "role": "system",
                "content": "You are an expert in IELTS writing style analysis. From the "
                f"{'Task 1 reports' if task_type == 'task1' else 'Task 2 essays'} below, all "
                "written by the same author, describe THIS author's writing "
                "style precisely enough that another writer could imitate it on a new question: "
                "how they paraphrase the question and state the thesis; their body-paragraph "
                "pattern and how they use examples; their typical linkers; sentence length and "
                "complexity; vocabulary register; conclusion pattern; typical word count; and 5-10 "
                "short functional signature phrases (frames like 'While this can be attributed "
                "to', never topic content).",
            },
            {"role": "user", "content": f"AUTHOR: {author}\n\n{samples_text}"},
        ],
        response_format=StyleProfile,
    )
    profile = response.choices[0].message.parsed
    if profile is None:
        raise RuntimeError("Style profile call returned no parsable result")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"author": author, "source_ids": source_ids, "profile": profile.model_dump()},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return profile


# --- Writing the sample ----------------------------------------------------------------------------

def _ngrams(text: str, n: int) -> set[tuple[str, ...]]:
    tokens = [t.lower() for t in _WORD_RE.findall(text)]
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def copied_runs(text: str, references: list[str], allowed: str = "") -> int:
    """Word runs of COPY_NGRAM shared with `references`; runs that also occur in
    `allowed` (the student's own question) don't count."""
    ref_grams: set[tuple[str, ...]] = set()
    for ref in references:
        ref_grams |= _ngrams(ref, COPY_NGRAM)
    return len((_ngrams(text, COPY_NGRAM) & ref_grams) - _ngrams(allowed, COPY_NGRAM))


def _task_rules(task_type: str) -> str:
    if task_type == "task1":
        return (
            "This is Academic Writing Task 1: paraphrase the question, give a clear overview of "
            "the main trends/differences/stages, select and compare key features with accurate "
            "figures, use ONLY data given in the question/visual, and give no personal opinion."
        )
    return (
        "This is Writing Task 2: answer every part of the question with a clear, consistent "
        "position, well-developed and supported body paragraphs, and a conclusion."
    )


async def _write_sample(
    question: str,
    task_type: str,
    prompt_images: list[tuple[bytes, str]] | None,
    band: float,
    author: str | None = None,
    profile: StyleProfile | None = None,
    references: list[LibrarySample] | None = None,
    copy_sources: list[str] | None = None,
) -> list[str]:
    """`copy_sources`: texts the sample must not copy from (default: the references)."""
    lo, hi = WORD_RANGE[task_type]
    style = ""
    if author and profile:
        refs = "\n\n".join(
            f"--- Reference {i} (a different question) ---\nQuestion: {s.question}\n\n{s.essay}"
            for i, s in enumerate(references or [], 1)
        )
        paragraphs = statistics.median_low(len(paragraphs_of(s.essay)) for s in references or []) if references else 4
        style = (
            f"Write in the style of {author} - one writer only: every reference below is theirs.\n\n"
            "STYLE PROFILE:\n"
            f"{json.dumps(profile.model_dump(), ensure_ascii=False, indent=1)}\n\n"
            f"{author.upper()}'S OWN ANSWERS - style references only:\n{refs}\n\n"
            "Follow this author's introduction, body-paragraph pattern, linkers, sentence style, "
            f"register and conclusion pattern, in {paragraphs} paragraphs like theirs. Match the "
            "vocabulary level and sentence complexity of the references - don't write in a more "
            "academic or sophisticated way than they do. Do NOT copy sentences from the "
            "references; reusing short functional phrases like the signature phrases is fine."
        )
    system = (
        "You write original IELTS Writing model answers for students to learn from.\n\n"
        f"{_task_rules(task_type)}\nWrite at IELTS band {band:.1f} - not above it. Length: {lo}-{hi} "
        "words. Support ideas the way IELTS essays do: with general, realistic examples and "
        "explanations (e.g. 'in many cities', 'a student who...'). Never cite named programmes, "
        "studies, statistics, dates or other specific facts that could be wrong. Return only the "
        "answer's paragraphs - no title, labels or notes.\n\n" + style
    )
    text = f"QUESTION:\n{question}"
    user_content: str | list = text
    if prompt_images:
        user_content = [{"type": "text", "text": text}] + [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}}
            for data, mime in prompt_images
        ]

    reference_texts = copy_sources if copy_sources is not None else [s.essay for s in references or []]
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user_content}]
    best: list[str] | None = None
    best_problems = None
    for attempt in range(GENERATION_ATTEMPTS):
        response = await client.beta.chat.completions.parse(
            model=settings.openai_model_grading, messages=messages, response_format=GeneratedSample
        )
        parsed = response.choices[0].message.parsed
        if parsed is None or not parsed.paragraphs:
            continue
        paragraphs = [p.strip() for p in parsed.paragraphs if p.strip()]
        full = "\n\n".join(paragraphs)
        words, copies = count_words(full), copied_runs(full, reference_texts, allowed=question)
        problems = []
        if not (lo - 10 <= words <= hi + 10):
            problems.append(f"it has {words} words; it must be {lo}-{hi} words")
        if copies:
            problems.append(f"{copies} word sequence(s) are copied from this author's book samples; rewrite those in your own words")
        if best is None or len(problems) < len(best_problems):
            best, best_problems = paragraphs, problems
        if not problems:
            break
        logger.info("Sample attempt %d rejected: %s", attempt + 1, "; ".join(problems))
        messages = messages[:2] + [
            {"role": "assistant", "content": full},
            {"role": "user", "content": "Revise it: " + "; ".join(problems) + "."},
        ]
    if best is None:
        raise RuntimeError("Sample generation returned nothing usable")
    return best


async def build_sample_section(
    task_type: str,
    question: str,
    prompt_images: list[tuple[bytes, str]] | None = None,
    seed: int = 0,
) -> SampleSection:
    """`seed` rotates between equally good authors (e.g. the student's essay count)."""
    try:
        library = await load_library()
    except Exception:
        logger.exception("Could not load the sample library")
        library = []

    exact = find_exact(question, task_type, library, seed)
    if exact is not None:
        return SampleSection(
            kind="book",
            question=exact.question,
            paragraphs=paragraphs_of(exact.essay),
            author=exact.author,
            book_title=exact.book_title,
            page=exact.page,
            source_id=exact.id,
        )

    if library:
        try:
            author, references = await choose_writer(question, task_type, library, seed)
            ref_task = references[0].task_type
            author_samples = [s for s in library if s.author == author and s.task_type == ref_task]
            profile = await get_style_profile(author, ref_task.removeprefix("writing_"), author_samples)
            paragraphs = await _write_sample(
                question, task_type, prompt_images, author_band(author_samples), author, profile, references,
                copy_sources=[s.essay for s in author_samples],
            )
            return SampleSection(kind="style", question=question, paragraphs=paragraphs, author=author)
        except Exception:
            logger.exception("Style-based sample failed - falling back to a plain Band 8 sample")

    paragraphs = await _write_sample(question, task_type, prompt_images, DEFAULT_BAND)
    return SampleSection(kind="generic", question=question, paragraphs=paragraphs)
