"""The "Model answer" page of Writing reports (final report prompt, Part 2,
2026-10-12).

The library lives in data/sample_library/ (scripts/build_sample_index.py
builds it from the verified review files in data/books/_review/):
  index.json       one entry per book sample: id, author, book, task_type
                   (task2 / task1_academic), subtype, topic, question, band
                   (as stated in the book), word_count, text. Samples are
                   attributed to their writer, not their book. IELTS Writing
                   Recap 2023 doesn't say who wrote which essay, so its
                   samples are one writer unit, "Diyorbek's IELTS team".
  embeddings.json  an embedding of each sample's question.
  styles/<writer>.json   the writer's style per task type, derived from
                   their own samples only; built on first use and rebuilt
                   when their samples change.

For each report:
  1. the student's question is labelled (subtype + topic) and embedded;
  2. ONE writer is chosen: writers with samples of this task type and at
     least one of this subtype, then the closest topic (their sample question
     most similar to the student's), ties -> more samples overall; writers
     still level (e.g. two who answered the same questions) take turns per
     student. The choice and the scores behind it are logged - never shown;
  3. a NEW answer is always written, in a call separate from the feedback,
     from that writer's style profile and 2-3 of their samples of this task
     type (never a sample of the same question), at the writer's book band;
  4. it must be 260-300 words (Task 1: 170-200), copy no 8-word run from any
     book and name no writer or book; then our own scorer checks it, and if
     it is more than 0.5 below the writer's band it is written once more.
Without a library a Band 8 answer is written with no style - the page is
never empty.
"""
from __future__ import annotations

import asyncio
import base64
import difflib
import json
import logging
import os
import re
import statistics
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import openai

from bot.config import settings
from services.ai import usage
from services.ai.essay_marking import WORD_RE, count_words
from services.ai.openai_client import client
from services.ai.schemas import GeneratedSample, StyleProfile, Task1Label, Task2Label

logger = logging.getLogger(__name__)

TASK_TYPES = {"task1": "task1_academic", "task2": "task2"}  # bot task -> library task
WORD_RANGE = {"task2": (260, 300), "task1_academic": (170, 200)}
COPY_NGRAM = 8  # a shared run of this many words counts as a copied sentence
SAME_QUESTION_RATIO = 0.9
REFERENCES = 3
MIN_REFERENCES = 2
PROFILE_SAMPLES = 8
GENERATION_ATTEMPTS = 3
SELF_CHECK_MARGIN = 0.5
DEFAULT_BAND = 8.0
ANSWER_MAX_TOKENS = 8000  # a ~300-word answer needs ~2-4k with reasoning; stops a runaway
INDEX_FIELDS = ("id", "author", "book", "task_type", "subtype", "topic", "question", "band", "word_count", "text")


@dataclass
class LibrarySample:
    id: str
    author: str
    book: str
    task_type: str  # "task2" | "task1_academic"
    subtype: str
    topic: str
    question: str
    band: float
    word_count: int
    text: str
    embedding: np.ndarray | None = field(default=None, repr=False)


@dataclass
class ModelAnswer:
    question: str
    paragraphs: list[str]
    band: float

    @property
    def word_count(self) -> int:
        return sum(count_words(p) for p in self.paragraphs)


def paragraphs_of(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n|\n", text.strip()) if p.strip()]


# --- Questions ------------------------------------------------------------------------------


def split_question(content: str) -> tuple[str, str]:
    """Review-file chunks start with the question, then a blank line, then the answer."""
    parts = re.split(r"\n\s*\n", content.strip(), maxsplit=1)
    if len(parts) == 2 and len(parts[0].split()) <= 90:
        return parts[0].strip(), parts[1].strip()
    return "", content.strip()


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


def same_question(a: str, b: str) -> bool:
    na, nb = normalize_question(a), normalize_question(b)
    if len(na.split()) < 5 or len(nb.split()) < 5:
        return False
    return difflib.SequenceMatcher(None, na, nb).ratio() >= SAME_QUESTION_RATIO


def regex_subtype(question: str, task_type: str) -> str:
    """Rough subtype from the wording - the fallback when labelling fails."""
    q = question.lower()
    if task_type == "task1_academic":
        for kind, pattern in (
            ("map", r"\bmaps?\b|\bplans?\b"),
            ("process", r"process|diagram|how .* (is|are) (made|produced)|life cycle|stages"),
            ("table", r"\btables?\b"),
            ("pie", r"\bpie\b"),
            ("line", r"line graph|line chart"),
            ("bar", r"bar chart|bar graph|column"),
        ):
            if re.search(pattern, q):
                return kind
        return "mixed"
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
    if q.count("?") >= 2:
        return "two_part"
    return "opinion"


_LABEL_RULES = {
    "task2": (
        "Label this IELTS Writing Task 2 question. subtype: opinion (agree/disagree, to what "
        "extent, is this a positive or negative development), discussion (discuss both views, "
        "usually with your own opinion), advantages_disadvantages (advantages and disadvantages, "
        "or do the advantages outweigh), problem_solution (problems or causes, and solutions or "
        "measures), two_part (two separate direct questions that fit none of the above)."
    ),
    "task1_academic": (
        "Label this IELTS Academic Writing Task 1 question. subtype: line (line graph), bar (bar "
        "chart), pie (pie charts), table, map (maps or plans of a place), process (a process, "
        "life cycle or how something works or is made), mixed (two or more different kinds of "
        "chart together)."
    ),
}


def _image_parts(prompt_images: list[tuple[bytes, str]] | None) -> list[dict]:
    return [
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}}
        for data, mime in prompt_images or []
    ]


async def label_question(
    question: str, task_type: str, answer_start: str = "", prompt_images: list[tuple[bytes, str]] | None = None
) -> tuple[str, str]:
    """(subtype, topic) of a question; `answer_start` (book samples) and the
    chart image (the student's Task 1) help tell chart types apart."""
    text = f"QUESTION:\n{question}"
    if answer_start:
        text += f"\n\nSTART OF A MODEL ANSWER:\n{answer_start[:600]}"
    content: str | list = text
    if prompt_images:
        content = [{"type": "text", "text": text}, *_image_parts(prompt_images)]
    try:
        response = await client.beta.chat.completions.parse(
            model=settings.openai_model_sample,
            messages=[
                {"role": "system", "content": _LABEL_RULES[task_type] + " topic: one or two lowercase words "
                 "naming the subject area, e.g. education, advertising, environment, transport, energy."},
                {"role": "user", "content": content},
            ],
            response_format=Task1Label if task_type == "task1_academic" else Task2Label,
        )
        usage.record("label", response)
        label = response.choices[0].message.parsed
        if label is not None:
            return label.subtype, label.topic.strip().lower()
    except Exception:
        logger.exception("Labelling a question failed - using the wording instead")
    return regex_subtype(question, task_type), "general"


async def embed_texts(texts: list[str]) -> list[list[float]]:
    response = await client.embeddings.create(model=settings.openai_embedding_model, input=texts)
    usage.record("embedding", response)
    return [d.embedding for d in response.data]


# --- Library -----------------------------------------------------------------------------------


def index_path() -> Path:
    return settings.sample_library_dir / "index.json"


def embeddings_path() -> Path:
    return settings.sample_library_dir / "embeddings.json"


def write_json(path: Path, data, indent: int | None = 1) -> None:
    """Written to a temp file first, so a reader never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)
    os.replace(tmp, path)


_cache: dict = {}


def load_library() -> list[LibrarySample]:
    """index.json with its embeddings; re-read when the index changes."""
    path = index_path()
    if not path.exists():
        return []
    mtime = path.stat().st_mtime
    if _cache.get("mtime") == mtime:
        return _cache["samples"]
    entries = json.loads(path.read_text(encoding="utf-8"))
    vectors = json.loads(embeddings_path().read_text(encoding="utf-8")) if embeddings_path().exists() else {}
    samples = []
    for e in entries:
        v = vectors.get(e["id"])
        samples.append(
            LibrarySample(
                **{k: e[k] for k in INDEX_FIELDS},
                embedding=np.asarray(v["vector"], dtype=float) if v else None,
            )
        )
    grams: set[tuple[str, ...]] = set()
    for s in samples:
        grams |= _ngrams(s.text, COPY_NGRAM)
    _cache.update(mtime=mtime, samples=samples, grams=grams, names=_names(samples))
    return samples


def _ngrams(text: str, n: int) -> set[tuple[str, ...]]:
    tokens = [t.lower().replace("’", "'") for t in WORD_RE.findall(text)]
    return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def _names(samples: list[LibrarySample]) -> list[str]:
    """Writer and book names that must never appear in a report: full names,
    surnames, book titles."""
    names: set[str] = set()
    for s in samples:
        names |= {s.author, s.book}
        names |= {w for w in re.findall(r"[A-Z][a-z'’]{4,}", s.author)[1:]}
        if "'s " in s.author:
            names.add(s.author.split("'s ")[0] + "'s")  # "Diyorbek's"
    return sorted(names, key=len, reverse=True)


def forbidden_names() -> list[str]:
    load_library()
    return _cache.get("names", [])


def names_in(text: str, names: list[str] | None = None) -> list[str]:
    names = forbidden_names() if names is None else names
    low = text.lower().replace("’", "'")
    return [n for n in names if re.search(rf"(?<![a-z]){re.escape(n.lower().replace('’', chr(39)))}(?![a-z])", low)]


def copied_runs(text: str, allowed: str = "") -> int:
    """8-word runs shared with any book sample; runs that also occur in
    `allowed` (the student's own question) don't count."""
    load_library()
    grams = _cache.get("grams", set())
    return len((_ngrams(text, COPY_NGRAM) & grams) - _ngrams(allowed, COPY_NGRAM))


# --- Choosing the writer --------------------------------------------------------------------------


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(a @ b) / denom if denom else 0.0


def writer_band(samples: list[LibrarySample]) -> float:
    """The band stated in the writer's book samples; the most common one if several."""
    counts = Counter(s.band for s in samples if s.band)
    if not counts:
        return DEFAULT_BAND
    top = max(counts.values())
    return max(b for b, c in counts.items() if c == top)


@dataclass
class Choice:
    author: str
    references: list[LibrarySample]
    band: float
    log: dict


def choose_writer(
    question: str, task_type: str, subtype: str, q_vec: np.ndarray, library: list[LibrarySample], seed: int = 0
) -> Choice:
    """The single best writer for this question - see the module docstring.
    `seed` picks between writers that are level on every count."""
    same_task = [s for s in library if s.task_type == task_type and s.embedding is not None]
    totals = Counter(s.author for s in library)
    rows = []
    for author in sorted({s.author for s in same_task}):
        mine = [s for s in same_task if s.author == author]
        usable = [s for s in mine if not same_question(question, s.question)]
        if len(usable) < MIN_REFERENCES:
            continue
        sims = {s.id: _cosine(q_vec, s.embedding) for s in mine}
        closest = max(mine, key=lambda s: sims[s.id])
        rows.append(
            {
                "author": author,
                "same_subtype": sum(s.subtype == subtype for s in mine),
                "topic": round(sims[closest.id], 3),
                "closest_sample": closest.id,
                "samples": totals[author],
                "_mine": mine,
                "_usable": usable,
                "_sims": sims,
            }
        )
    if not rows:
        raise LookupError(f"No writer has {MIN_REFERENCES}+ {task_type} samples")
    eligible = [r for r in rows if r["same_subtype"]] or rows
    top = max((r["topic"], r["samples"]) for r in eligible)
    level = [r for r in eligible if (r["topic"], r["samples"]) == top]
    best = level[seed % len(level)]
    sims = best["_sims"]
    references = sorted(best["_usable"], key=lambda s: (s.subtype != subtype, -sims[s.id]))[:REFERENCES]
    band = writer_band(best["_mine"])
    public = lambda r: {k: v for k, v in r.items() if not k.startswith("_")}
    ranked = sorted(rows, key=lambda r: (r["same_subtype"] > 0, r["topic"], r["samples"]), reverse=True)
    return Choice(
        author=best["author"],
        references=references,
        band=band,
        log={
            "subtype": subtype,
            "rule": "same subtype, then closest topic, then more samples"
            if eligible is not rows else "no writer has this subtype: closest topic, then more samples",
            "chosen": best["author"],
            "band": band,
            "reference_ids": [s.id for s in references],
            "same_question_excluded": sorted({s.id for s in best["_mine"]} - {s.id for s in best["_usable"]}),
            "candidates": [public(r) for r in ranked[:8]],
        },
    )


# --- Style profiles ---------------------------------------------------------------------------------


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower().replace("'", "")).strip("-") or "writer"


def style_path(author: str) -> Path:
    return settings.sample_library_dir / "styles" / f"{_slug(author)}.json"


def _spread(samples: list[LibrarySample], k: int) -> list[LibrarySample]:
    """Up to k samples, taken in turn from each subtype."""
    groups: dict[str, list[LibrarySample]] = {}
    for s in sorted(samples, key=lambda s: s.id):
        groups.setdefault(s.subtype, []).append(s)
    chosen: list[LibrarySample] = []
    while len(chosen) < k and any(groups.values()):
        for subtype in sorted(groups):
            if groups[subtype] and len(chosen) < k:
                chosen.append(groups[subtype].pop(0))
    return chosen


_profile_locks: dict[str, asyncio.Lock] = {}


async def get_style_profile(author: str, task_type: str, samples: list[LibrarySample]) -> StyleProfile:
    """`samples`: the writer's own samples of this task type - Task 1 reports
    and Task 2 essays are written differently, so each gets its own profile."""
    path = style_path(author)
    async with _profile_locks.setdefault(path.name, asyncio.Lock()):
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        source_ids = sorted(s.id for s in samples)
        entry = data.get(task_type)
        if entry and entry.get("source_ids") == source_ids:
            return StyleProfile(**entry["profile"])

        chosen = _spread(samples, PROFILE_SAMPLES)
        kind = "Academic Task 1 reports" if task_type == "task1_academic" else "Task 2 essays"
        samples_text = "\n\n".join(
            f"--- Sample {i} ---\nQuestion: {s.question}\n\n{s.text}" for i, s in enumerate(chosen, 1)
        )
        response = await client.beta.chat.completions.parse(
            model=settings.openai_model_sample,
            messages=[
                {
                    "role": "system",
                    "content": f"You are an expert in IELTS writing style analysis. From the {kind} "
                    "below, all by the same writer, describe this writer's style precisely enough "
                    "that another writer could imitate it on a new question: how they paraphrase the "
                    "question and state the thesis (Task 1: the overview); their body-paragraph "
                    "pattern; how they give examples (Task 1: how they select and report data); their "
                    "typical linkers; sentence length and complexity; vocabulary register; conclusion "
                    "pattern; and 5-10 short functional signature phrases (frames like 'While this can "
                    "be attributed to', never topic content). Don't name the writer or any book.",
                },
                {"role": "user", "content": samples_text},
            ],
            response_format=StyleProfile,
        )
        usage.record("style_profile", response)
        profile = response.choices[0].message.parsed
        if profile is None:
            raise RuntimeError("Style profile call returned no parsable result")
        data["author"] = author
        data[task_type] = {
            "source_ids": source_ids,
            "band": writer_band(samples),
            "typical_word_count": int(statistics.median(s.word_count for s in samples)),
            "profile": profile.model_dump(),
        }
        write_json(path, data)
        return profile


# --- Writing the answer -------------------------------------------------------------------------------


def _task_rules(task_type: str) -> str:
    if task_type == "task1_academic":
        return (
            "This is Academic Writing Task 1: paraphrase the question, give a clear overview of the "
            "main trends, differences or stages, then select and compare the key features with "
            "accurate figures. Use ONLY the data shown in the question and the visual, reported "
            "accurately, and give no personal opinion."
        )
    return (
        "This is Writing Task 2: answer every part of the question with a clear, consistent "
        "position, well-developed and supported body paragraphs, and a conclusion."
    )


def _system(task_type: str, band: float, profile: StyleProfile | None, references: list[LibrarySample]) -> str:
    lo, hi = WORD_RANGE[task_type]
    style = ""
    if profile is not None:
        refs = "\n\n".join(
            f"--- Their answer {i} (a different question) ---\nQuestion: {s.question}\n\n{s.text}"
            for i, s in enumerate(references, 1)
        )
        paragraphs = statistics.median_low(len(paragraphs_of(s.text)) for s in references) if references else 4
        clean = json.dumps(profile.model_dump(), ensure_ascii=False, indent=1)
        for name in forbidden_names():
            clean = re.sub(re.escape(name), "the writer", clean, flags=re.IGNORECASE)
        style = (
            "\n\nWrite in the style of ONE writer, described by the style profile below and shown in "
            "their own answers to other questions.\n\n"
            f"STYLE PROFILE:\n{clean}\n\n"
            f"THE WRITER'S OWN ANSWERS - style references only:\n{refs}\n\n"
            "Follow this writer's introduction, body-paragraph pattern, way of giving examples, "
            f"linkers, sentence style, register and conclusion pattern, in {paragraphs} paragraphs "
            "like theirs. Match the vocabulary level and sentence complexity of their answers - don't "
            "write in a more academic or sophisticated way than they do. Do NOT copy sentences from "
            f"their answers; reusing short functional phrases is fine. LENGTH: {lo}-{hi} words, "
            "whatever the length of their answers."
        )
    facts = (
        " Use only the data in the question and the visual." if task_type == "task1_academic" else
        " Support ideas with general knowledge or clearly hypothetical examples (\"if posters were "
        "confined to transport hubs, ...\", \"a student who ...\")."
    )
    return (
        "You write original IELTS Writing model answers for students to learn from.\n\n"
        f"{_task_rules(task_type)}\nWrite at IELTS band {band:.1f} - not above it. Length: {lo}-{hi} "
        f"words (aim for about {(lo + hi) // 2}; count before answering). Answer every part of the "
        "question.\nNo invented real-world facts: no statistics, studies, surveys, named programmes, "
        "laws, dates, and no claims about real countries, cities or organisations that a reader could "
        f"take as fact.{facts} Never mention any writer, teacher or book. Return only the answer's "
        "paragraphs - no title, labels or notes." + style
    )


async def _write(
    question: str,
    task_type: str,
    prompt_images: list[tuple[bytes, str]] | None,
    band: float,
    profile: StyleProfile | None,
    references: list[LibrarySample],
    note: str = "",
) -> tuple[list[str], list[str]]:
    """(paragraphs, problems left) - the best of up to GENERATION_ATTEMPTS."""
    lo, hi = WORD_RANGE[task_type]
    system = _system(task_type, band, profile, references) + (f"\n\n{note}" if note else "")
    text = f"QUESTION:\n{question}"
    user = [{"type": "text", "text": text}, *_image_parts(prompt_images)] if prompt_images else text
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    best: list[str] | None = None
    best_problems: list[str] = []
    for attempt in range(GENERATION_ATTEMPTS):
        try:
            response = await client.beta.chat.completions.parse(
                model=settings.openai_model_sample, messages=messages, response_format=GeneratedSample,
                max_completion_tokens=ANSWER_MAX_TOKENS,
            )
        except openai.LengthFinishReasonError as e:
            if getattr(e, "completion", None) is not None:
                usage.record("model_answer", e.completion)
            logger.warning("Model answer attempt %d ran past %d output tokens", attempt + 1, ANSWER_MAX_TOKENS)
            continue
        usage.record("model_answer", response)
        parsed = response.choices[0].message.parsed
        if parsed is None or not parsed.paragraphs:
            continue
        paragraphs = [p.strip() for p in parsed.paragraphs if p.strip()]
        full = "\n\n".join(paragraphs)
        words, copies, names = count_words(full), copied_runs(full, allowed=question), names_in(full)
        problems = []
        if not lo <= words <= hi:
            problems.append(f"it has {words} words; it must be {lo}-{hi} words")
        if copies:
            problems.append(f"{copies} word sequence(s) are copied from book samples; rewrite those in your own words")
        if names:
            problems.append(f"remove the names {', '.join(names)}")
        if best is None or len(problems) < len(best_problems):
            best, best_problems = paragraphs, problems
        if not problems:
            break
        logger.info("Model answer attempt %d rejected: %s", attempt + 1, "; ".join(problems))
        messages = messages[:2] + [
            {"role": "assistant", "content": full},
            {"role": "user", "content": "Revise it: " + "; ".join(problems) + "."},
        ]
    if best is None:
        raise RuntimeError("Model answer generation returned nothing usable")
    return best, best_problems


async def build_model_answer(
    task_type: str,
    question: str,
    prompt_images: list[tuple[bytes, str]] | None = None,
    seed: int = 0,
) -> ModelAnswer:
    """`task_type`: the bot's "task1" / "task2". `seed` (e.g. the student's id
    plus essay count) takes turns between writers level on every count."""
    # Imported here: writing_grader is the heavier module and only this step needs it.
    from services.ai.writing_grader import score_writing

    lib_task = TASK_TYPES[task_type]
    log: dict = {"task_type": lib_task}
    try:
        library = load_library()
    except Exception:
        logger.exception("Could not load the sample library")
        library = []

    band, profile, references = DEFAULT_BAND, None, []
    if library:
        try:
            (subtype, topic), (q_vec,) = await asyncio.gather(
                label_question(question, lib_task, prompt_images=prompt_images), embed_texts([question])
            )
            choice = choose_writer(question, lib_task, subtype, np.asarray(q_vec, dtype=float), library, seed)
            own = [s for s in library if s.author == choice.author and s.task_type == lib_task]
            profile = await get_style_profile(choice.author, lib_task, own)
            band, references = choice.band, choice.references
            log.update(topic=topic, **choice.log)
        except Exception:
            logger.exception("Choosing a writer failed - the model answer is written without a style")
            band, profile, references = DEFAULT_BAND, None, []
    else:
        log["chosen"] = None

    paragraphs, problems = await _write(question, lib_task, prompt_images, band, profile, references)
    log["problems"] = problems
    try:
        score = await score_writing(task_type, question, "\n\n".join(paragraphs), prompt_images)
        log["self_check"] = [score]
        if score < band - SELF_CHECK_MARGIN:
            note = (
                f"An examiner scored a previous attempt {score:.1f}. This answer must read as a clear "
                f"Band {band:.1f} answer: develop every idea fully, with precise vocabulary and "
                "accurate, varied grammar."
            )
            second, second_problems = await _write(question, lib_task, prompt_images, band, profile, references, note)
            second_score = await score_writing(task_type, question, "\n\n".join(second), prompt_images)
            log["self_check"].append(second_score)
            if second_score >= score:
                paragraphs, log["problems"] = second, second_problems
    except Exception:
        logger.exception("Model answer self-check failed - keeping the answer as written")
    log["words"] = sum(count_words(p) for p in paragraphs)
    logger.info("Model answer: %s", json.dumps(log, ensure_ascii=False))
    return ModelAnswer(question=question, paragraphs=paragraphs, band=band)
