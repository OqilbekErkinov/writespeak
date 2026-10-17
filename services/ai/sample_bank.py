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
  3. a NEW answer is always written from that writer's style profile and
     2-3 of their samples of this task type (never a sample of the same
     question) - written for THIS student's results (candidate-specific
     samples, 2026-10-16): it starts once the feedback is final
     (writing_grader's on_feedback), is aimed above the student
     (scoring.writing_aim_band: Band 9 from 8.0 up, Band 8 below - the user's
     rule, 2026-10-17; shown as "Aimed at" and "AI example") and demonstrates
     their priorities (Task
     Response: every part answered, ideas developed; Coherence: paragraphing,
     topic sentences, referencing; Lexical: precise collocations, not rare
     synonyms; Grammar: a useful variety of accurate structures; Task 1: the
     overview, key features, grouped comparisons, no unsupported detail),
     with 1-3 "what this shows" notes found in it by code. The writer lends
     the style; the level is the student's aim;
  4. it must be 300-340 words (Task 1: 170-200), copy no 8-word run from any
     book, reuse little of the student's own wording (REUSE_LIMIT) and name
     no writer or book; then our own scorer checks it (written once more if
     more than 0.5 below the aimed band), and a quality check (the question's
     exact type, one consistent position, examples that fit their points, no
     stock opener, no invented facts) - written once more if it fails.
Without a library an answer is written with no style - the page is never
empty. Where it came from (ai_generated; the writer and sample ids, the aimed
band, the priorities it shows) is logged and saved with the submission.
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
from pydantic import BaseModel, Field

from bot.config import settings
from services.ai import usage
from services.ai.essay_marking import WORD_RE, count_words
from services.ai.openai_client import client
from services.ai.schemas import GeneratedSample, StyleProfile, Task1Label, Task2Label
from services.ai.scoring import writing_aim_band

logger = logging.getLogger(__name__)

TASK_TYPES = {"task1": "task1_academic", "task2": "task2"}  # bot task -> library task
# Task 2 since 2026-10-17: 300-340 (aim 320) - a fuller answer shows the
# student more clearly what each part of the question needs (the user's call).
WORD_RANGE = {"task2": (300, 340), "task1_academic": (170, 200)}
COPY_NGRAM = 8  # a shared run of this many words counts as a copied sentence
SAME_QUESTION_RATIO = 0.9
REFERENCES = 3
MIN_REFERENCES = 2
PROFILE_SAMPLES = 8
GENERATION_ATTEMPTS = 3
SELF_CHECK_MARGIN = 0.5
DEFAULT_BAND = 8.0  # without the student's results
REUSE_LIMIT = 0.25  # share of the answer's 5-word runs that may come from the student's essay
MAX_SHOWS = 3
ANSWER_MAX_TOKENS = 8000  # a ~320-word answer needs ~2-4k with reasoning; stops a runaway
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
    band: float  # the band it was aimed at (approximate)
    shows: list[dict] = field(default_factory=list)  # {"n", "title", "phrase", "note"}
    source: str = "ai_generated"  # ai_generated | published_reference | human_reviewed
    log: dict = field(default_factory=dict)  # writer, sample ids, checks - internal only

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
        {"type": "image_url",
         "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}", "detail": "high"}}
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


# --- The student's results ----------------------------------------------------------------------

_CRITERION_NAMES = {"TR": "Task", "CC": "Coherence", "LR": "Vocabulary", "GRA": "Grammar"}


@dataclass
class Needs:
    """What the report found - the model answer is written to teach it."""

    overall: float
    bands: dict[str, float]  # TR_or_TA, CC, LR, GRA
    priorities: list[dict]  # {"n": "01", "title", "rule", "criteria": [...]}
    issues: list[str] = field(default_factory=list)  # task- and logic-level problems
    strengths: list[str] = field(default_factory=list)
    essay: str = ""  # the student's own text, for comparison

    @property
    def aim(self) -> float:
        return writing_aim_band(self.overall)


def needs_from_feedback(bands: dict[str, float], overall: float, fb, essay: str) -> Needs:
    """Needs from the feedback call's own output, so nothing is assessed
    twice. Only task- and logic-level mistakes go in as issues (TR, CC,
    unclear meaning); word and grammar slips reach the answer through the
    priorities, so the sample teaches patterns instead of correcting lines."""
    priorities = [
        {"n": f"{i:02d}", "title": p.title.strip(), "rule": re.sub(r"\*+", "", p.rule).strip(),
         "criteria": list(p.criteria)}
        for i, p in enumerate(fb.priorities[:3], 1)
    ]
    issues = [f"«{m.wrong}» - {m.why}" for m in fb.mistakes if m.criterion in ("TR", "CC") or m.unclear][:5]
    return Needs(overall=overall, bands=bands, priorities=priorities, issues=issues,
                 strengths=[q.strip() for q in fb.strength.quoted_phrases][:3], essay=essay)


def _needs_text(needs: Needs, task_type: str) -> str:
    first = "Task Achievement" if task_type == "task1_academic" else "Task Response"
    lines = [
        "THE STUDENT'S RESULTS (from the examiner's report - teach to these):",
        f"- Bands: {first} {needs.bands.get('TR_or_TA', 0):.0f}, Coherence & Cohesion {needs.bands.get('CC', 0):.0f}, "
        f"Lexical Resource {needs.bands.get('LR', 0):.0f}, Grammatical Range & Accuracy {needs.bands.get('GRA', 0):.0f}; "
        f"overall {needs.overall:.1f}. Your answer is aimed at about Band {needs.aim:.1f}.",
        "- Their priorities - what your answer must SHOW them how to do:",
    ]
    for p in needs.priorities:
        kind = ", ".join(_CRITERION_NAMES.get(c, c) for c in p["criteria"])
        lines.append(f"  {p['n']} {p['title']} ({kind}): {p['rule']}")
    if needs.issues:
        lines.append("- Task and logic problems the examiner found (don't repeat them):")
        lines += [f"  {i}" for i in needs.issues]
    if needs.strengths:
        lines.append("- What they already do well: " + ", ".join(f"«{s}»" for s in needs.strengths))
    return "\n".join(lines)


def _grams5(text: str, n: int = 5) -> set[tuple[str, ...]]:
    words = [w.lower().replace("’", "'") for w in WORD_RE.findall(text)]
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def reuse_share(answer: str, essay: str, question: str = "") -> float:
    """How much of the answer is the student's own wording: the share of its
    5-word runs found in their essay (runs from the question don't count)."""
    mine = _grams5(answer) - _grams5(question)
    if not mine or not essay.strip():
        return 0.0
    return len(mine & _grams5(essay)) / len(mine)


def _shows(parsed, text: str, needs: Needs | None) -> list[dict]:
    """The "what this shows" notes whose phrase really is in the answer and
    whose priority exists."""
    if needs is None:
        return []
    titles = {p["n"]: p["title"] for p in needs.priorities}
    low = re.sub(r"\s+", " ", text).lower()
    out = []
    for item in getattr(parsed, "shows", None) or []:
        n = re.sub(r"\D", "", item.priority).zfill(2)[-2:]
        phrase = item.phrase.strip().strip(".,;:!?\"'“”")
        if n in titles and len(phrase.split()) >= 3 and re.sub(r"\s+", " ", phrase).lower() in low \
                and all(o["phrase"].lower() != phrase.lower() for o in out):
            out.append({"n": n, "title": titles[n], "phrase": phrase, "note": item.note.strip()})
    return out[:MAX_SHOWS]


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
        "position, well-developed and supported body paragraphs, and a conclusion. Follow the question's "
        "exact type: an advantages-and-disadvantages question discusses both (it is not a \"do the "
        "advantages outweigh\" essay unless that is asked), a discussion question presents both views, an "
        "opinion question argues one position. Keep ONE position from the introduction to the conclusion - "
        "the conclusion restates what the body argued. Every example must support its own point. Don't "
        "open with a stock phrase such as \"With the advent of\" or \"In this day and age\"."
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
            "like theirs. Never write in a more academic or sophisticated way than they do; where the "
            "aimed band is below their level, keep their structure but use plainer words and simpler "
            "sentences. Do NOT copy sentences from their answers; reusing short functional phrases is "
            f"fine. LENGTH: {lo}-{hi} words, whatever the length of their answers."
        )
    facts = (
        " Use only the data in the question and the visual." if task_type == "task1_academic" else
        " Support ideas with general knowledge or clearly hypothetical examples (\"if posters were "
        "confined to transport hubs, ...\", \"a student who ...\")."
    )
    task1 = task_type == "task1_academic"
    teach = (
        "Task Achievement: a clear overview of the main trends or stages, the key features selected and "
        "compared, no detail the visual doesn't show. Coherence: information grouped logically, one focus "
        "per paragraph. Vocabulary: accurate trend and comparison language. Grammar: comparatives, "
        "a range of tenses, the passive for a process."
        if task1 else
        "Task Response: every part of the question answered, each main idea explained and supported, not "
        "just stated. Coherence: one clear topic sentence per body paragraph, ideas that follow logically, "
        "linking and referencing that guide the reader. Vocabulary: precise, natural words and collocations "
        "for this topic - never rare synonyms for their own sake. Grammar: a useful variety of accurate "
        "structures."
    )
    return (
        "You write IELTS Writing model answers that a student reads right after their own feedback report - "
        "each one written to TEACH this student what their report says they need.\n\n"
        f"{_task_rules(task_type)}\nWrite at about IELTS band {band:.1f} - not above it, natural, no "
        f"memorised templates. Length: {lo}-{hi} words (aim for about {(lo + hi) // 2}; count before "
        "answering). Answer every part of the question.\n"
        "TEACH, DON'T REWRITE: when the student's results are given, write a separate, complete answer to "
        "the same question that demonstrates their priorities while staying a strong answer overall. "
        f"{teach} The student's own text is given for comparison: you may share a sound idea, but never "
        "their sentences, and never keep a weak argument or a misreading of the question.\n"
        "shows: when priorities are given, 1-3 places in YOUR answer that demonstrate them (the first "
        "priority first): the priority number, a phrase copied exactly from your answer, and what the "
        "student should notice there.\n"
        "No invented real-world facts: no statistics, studies, surveys, named programmes, "
        "laws, dates, and no claims about real countries, cities or organisations that a reader could "
        f"take as fact.{facts} Never mention any writer, teacher or book. The paragraphs are only the "
        "answer - no title, labels or notes." + style
    )


async def _write(
    question: str,
    task_type: str,
    prompt_images: list[tuple[bytes, str]] | None,
    band: float,
    profile: StyleProfile | None,
    references: list[LibrarySample],
    note: str = "",
    needs: Needs | None = None,
) -> tuple[list[str], list[str], list[dict]]:
    """(paragraphs, problems left, "what this shows" notes) - the best of up
    to GENERATION_ATTEMPTS."""
    lo, hi = WORD_RANGE[task_type]
    system = _system(task_type, band, profile, references) + (f"\n\n{note}" if note else "")
    text = f"QUESTION:\n{question}"
    if needs is not None:
        text += "\n\n" + _needs_text(needs, task_type)
        if needs.essay.strip():
            text += f"\n\nTHE STUDENT'S OWN ANSWER (for comparison only):\n{needs.essay.strip()}"
    user = [{"type": "text", "text": text}, *_image_parts(prompt_images)] if prompt_images else text
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    best: list[str] | None = None
    best_problems: list[str] = []
    best_shows: list[dict] = []
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
        reused = reuse_share(full, needs.essay, question) if needs is not None else 0.0
        problems = []
        if not lo <= words <= hi:
            problems.append(f"it has {words} words; it must be {lo}-{hi} words")
        if copies:
            problems.append(f"{copies} word sequence(s) are copied from book samples; rewrite those in your own words")
        if names:
            problems.append(f"remove the names {', '.join(names)}")
        if reused > REUSE_LIMIT:
            problems.append("it repeats too much of the student's own wording - write a separate answer in new sentences")
        if best is None or len(problems) < len(best_problems):
            best, best_problems, best_shows = paragraphs, problems, _shows(parsed, full, needs)
        if not problems:
            break
        logger.info("Model answer attempt %d rejected: %s", attempt + 1, "; ".join(problems))
        messages = messages[:2] + [
            {"role": "assistant", "content": full},
            {"role": "user", "content": "Revise it: " + "; ".join(problems) + "."},
        ]
    if best is None:
        raise RuntimeError("Model answer generation returned nothing usable")
    return best, best_problems, best_shows


class _QualityCheck(BaseModel):
    ok: bool
    problem: str = Field(description="Empty when ok; else what to fix, one line.")


async def quality_problem(
    task_type: str, question: str, paragraphs: list[str], subtype: str,
    prompt_images: list[tuple[bytes, str]] | None = None,
) -> str | None:
    """The report's quality check of a model answer (one call): None if it
    passes, else what is wrong. Report #15's "Band 8.5" answer argued that the
    harms win and concluded that the benefits win; both #14's and #15's
    turned an advantages/disadvantages question into an "outweigh" essay and
    opened with "With the advent of"."""
    from services.ai.writing_grader import _parse, _with_images  # the heavier module

    lib_task = TASK_TYPES[task_type]
    checks = (
        "1. It does exactly what the question asks, in the question's type"
        + (f" (labelled: {subtype.replace('_', ' ')})" if lib_task == "task2" else "")
        + " - every part answered, nothing turned into a different question.\n"
        "2. Its position is the same from start to finish: the conclusion restates what the body argued.\n"
        "3. Every example fits the point it supports.\n"
        "4. It doesn't open with a stock phrase (\"With the advent of\", \"In this day and age\", "
        "\"Nowadays, ...\").\n"
        "5. It presents no invented statistics or real-world facts as true."
        + ("\n6. Every figure matches the visual." if lib_task == "task1_academic" else "")
    )
    messages = [
        {"role": "system", "content": "You check an IELTS Writing model answer before students see it. "
         f"ok=false if ANY check fails, with the problem in one line:\n{checks}"},
        {"role": "user", "content": _with_images(
            f"QUESTION:\n{question}\n\nMODEL ANSWER:\n" + "\n\n".join(paragraphs), prompt_images)},
    ]
    response = await _parse("answer_check", 6000, messages, _QualityCheck)
    parsed = response.choices[0].message.parsed
    if parsed is None or parsed.ok:
        return None
    return parsed.problem.strip() or "failed the quality check"


async def build_model_answer(
    task_type: str,
    question: str,
    prompt_images: list[tuple[bytes, str]] | None = None,
    seed: int = 0,
    needs: Needs | None = None,
) -> ModelAnswer:
    """`task_type`: the bot's "task1" / "task2". `seed` (e.g. the student's id
    plus essay count) takes turns between writers level on every count.
    With `needs` (the report's findings) the answer is aimed at needs.aim and
    teaches its priorities."""
    # Imported here: writing_grader is the heavier module and only this step needs it.
    from services.ai.writing_grader import score_writing

    lib_task = TASK_TYPES[task_type]
    log: dict = {"task_type": lib_task}
    try:
        library = load_library()
    except Exception:
        logger.exception("Could not load the sample library")
        library = []

    aim = needs.aim if needs is not None else DEFAULT_BAND
    band, profile, references = aim, None, []
    if library:
        try:
            (subtype, topic), (q_vec,) = await asyncio.gather(
                label_question(question, lib_task, prompt_images=prompt_images), embed_texts([question])
            )
            choice = choose_writer(question, lib_task, subtype, np.asarray(q_vec, dtype=float), library, seed)
            own = [s for s in library if s.author == choice.author and s.task_type == lib_task]
            profile = await get_style_profile(choice.author, lib_task, own)
            references = choice.references  # the writer lends the style; the level stays the student's aim
            log.update(topic=topic, **choice.log)
        except Exception:
            logger.exception("Choosing a writer failed - the model answer is written without a style")
            profile, references = None, []
    else:
        log["chosen"] = None

    log["aimed_at"] = band
    if needs is not None:
        log["priorities"] = [p["title"] for p in needs.priorities]
    paragraphs, problems, shows = await _write(question, lib_task, prompt_images, band, profile, references,
                                               needs=needs)
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
            second, second_problems, second_shows = await _write(
                question, lib_task, prompt_images, band, profile, references, note, needs)
            second_score = await score_writing(task_type, question, "\n\n".join(second), prompt_images)
            log["self_check"].append(second_score)
            if second_score >= score:
                paragraphs, log["problems"], shows = second, second_problems, second_shows
    except Exception:
        logger.exception("Model answer self-check failed - keeping the answer as written")
    try:
        subtype = log.get("subtype") or regex_subtype(question, lib_task)
        problem = await quality_problem(task_type, question, paragraphs, subtype, prompt_images)
        log["quality"] = problem or "ok"
        if problem:
            note = f"A reviewer rejected a previous version: {problem}. Write it again and fix exactly that."
            again, again_problems, again_shows = await _write(
                question, lib_task, prompt_images, band, profile, references, note, needs)
            again_problem = await quality_problem(task_type, question, again, subtype, prompt_images)
            log["quality_retry"] = again_problem or "ok"
            if again_problem is None:
                paragraphs, log["problems"], shows = again, again_problems, again_shows
    except Exception:
        logger.exception("Model answer quality check failed - keeping the answer as written")
    log["words"] = sum(count_words(p) for p in paragraphs)
    log["shows"] = [x["n"] for x in shows]
    if needs is not None:
        log["reuse"] = round(reuse_share("\n\n".join(paragraphs), needs.essay, question), 2)
    log["source"] = "ai_generated"
    logger.info("Model answer: %s", json.dumps(log, ensure_ascii=False))
    return ModelAnswer(question=question, paragraphs=paragraphs, band=band, shows=shows, log=log)
