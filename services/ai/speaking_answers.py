"""Speaking model answers (Speaking report prompt, Part 3, 2026-10-13).

The library: data/sample_library/speaking_index.json, built from the
speaking books (one entry per book answer: id, author - the real author,
not the book - book, part 1/2/3, topic, question, band as stated in the
book, word_count, text), plus styles/<author>_speaking.json, each author's
spoken style from their own answers only. As of 2026-10-13 there are no
speaking books yet (the owner will send them): until the index exists every
answer is newly written at DEFAULT_BAND with no author style.

For each question of a session (repeated questions share one answer,
labelled "Q3 · Q4"):

  1. exact match - a book question of the same part that is the same
     question after normalisation - and settings.allow_book_answers_verbatim:
     the book's answer as written, its badge the band stated in the book;
  2. otherwise a new answer in the style of ONE author, the same author for
     the whole report: authors with answers of this part, then the closest
     topic, then more answers; that author's style profile and 2-3 of their
     own answers as references; badge = that author's band;
  3. spoken style, the part's length (Part 1 40-60 words, Part 2 250-300,
     Part 3 70-100), answer -> reason -> example, no invented facts;
  4. one quality-check call for the whole report (answers the exact question
     in its type, one consistent position, examples that fit, no stock
     opener shared with another answer) - failing answers are written again
     once; then our scorer reads the set, and if it is more than 0.5 below
     the badge the set is written once more.

What was used (exact match or generated, author, sample ids, band) is
logged, never shown in the report.
"""
from __future__ import annotations

import asyncio
import difflib
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
from pydantic import BaseModel, Field

from bot.config import settings
from bot.utils.speaking_questions import same_question
from services.ai import usage
from services.ai.essay_marking import count_words
from services.ai.openai_client import client
from services.ai.sample_bank import _cosine, _slug, embed_texts, forbidden_names, names_in, write_json
from services.ai.schemas import StyleProfile

logger = logging.getLogger(__name__)

LENGTHS = {"part1": (40, 60), "part2": (250, 300), "part3": (70, 100)}
WORDS_PER_MINUTE = 140
DEFAULT_BAND = 7.5
SELF_CHECK_MARGIN = 0.5
REFERENCES = 3
ANSWER_MAX_TOKENS = 6000
EXACT_RATIO = 0.9
PART_NOTES = {
    "part1": "IELTS Speaking Part 1: a short, natural answer about everyday life - answer the question "
    "directly first (a yes/no question gets a clear yes/no-style answer), then a reason and a short "
    "personal example.",
    "part2": "IELTS Speaking Part 2: a 2-minute talk on the cue card, covering EVERY point on the card in "
    "order, with a short opening line and a natural close that answers the card's last point.",
    "part3": "IELTS Speaking Part 3: a discussion answer - a clear position, then reasons, an example or a "
    "comparison, and a brief conclusion. A \"why\" question gets reasons; a \"how\" question gets ways; "
    "a comparison question compares.",
}


@dataclass
class Target:
    label: str  # "Q1", "Q3 · Q4"
    qids: list[str]
    part: str  # part1 / part2 / part3
    question: str  # the question (Part 2: the whole cue card)
    student_answer: str = ""  # what the student said - the answer keeps their ideas


@dataclass
class BookAnswer:
    id: str
    author: str
    book: str
    part: int
    topic: str
    question: str
    band: float
    word_count: int
    text: str
    embedding: np.ndarray | None = field(default=None, repr=False)


# --- Library --------------------------------------------------------------------------------------


def index_path():
    return settings.sample_library_dir / "speaking_index.json"


def embeddings_path():
    return settings.sample_library_dir / "speaking_embeddings.json"


def style_path(author: str):
    return settings.sample_library_dir / "styles" / f"{_slug(author)}_speaking.json"


_cache: dict = {}


def load_library() -> list[BookAnswer]:
    path = index_path()
    if not path.exists():
        return []
    mtime = path.stat().st_mtime
    if _cache.get("mtime") == mtime:
        return _cache["answers"]
    entries = json.loads(path.read_text(encoding="utf-8"))
    vectors = json.loads(embeddings_path().read_text(encoding="utf-8")) if embeddings_path().exists() else {}
    answers = []
    for e in entries:
        v = vectors.get(e["id"])
        answers.append(BookAnswer(
            id=e["id"], author=e["author"], book=e["book"], part=int(e["part"]), topic=e.get("topic", ""),
            question=e["question"], band=float(e["band"]), word_count=int(e.get("word_count") or count_words(e["text"])),
            text=e["text"], embedding=np.asarray(v["vector"], dtype=float) if v else None,
        ))
    _cache.update(mtime=mtime, answers=answers)
    return answers


def _norm(q: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", q.lower()).split())


def exact_match(target: Target, library: list[BookAnswer]) -> BookAnswer | None:
    """A book answer to the same question of the same part."""
    part = int(target.part[-1])
    first_line = target.question.strip().splitlines()[0]
    best, best_ratio = None, 0.0
    for a in library:
        if a.part != part:
            continue
        book_q = a.question.strip().splitlines()[0]
        ratio = difflib.SequenceMatcher(None, _norm(first_line), _norm(book_q)).ratio()
        if ratio >= EXACT_RATIO or (part != 2 and same_question(first_line, book_q)):
            if ratio > best_ratio:
                best, best_ratio = a, ratio
    return best


def author_band(answers: list[BookAnswer]) -> float:
    counts = Counter(a.band for a in answers if a.band)
    if not counts:
        return DEFAULT_BAND
    top = max(counts.values())
    return max(b for b, c in counts.items() if c == top)


def choose_author(targets: list[Target], q_vecs: list[np.ndarray], library: list[BookAnswer], seed: int = 0) -> dict | None:
    """The single author for this report: answers of these parts, then the
    closest topic (mean best similarity of the session's questions to their
    questions), then more answers; level authors take turns per student."""
    parts = {int(t.part[-1]) for t in targets}
    rows = []
    for author in sorted({a.author for a in library}):
        mine = [a for a in library if a.author == author and a.embedding is not None]
        same_part = [a for a in mine if a.part in parts]
        if not same_part:
            continue
        sims = [max(_cosine(v, a.embedding) for a in same_part) for v in q_vecs] if q_vecs else [0.0]
        rows.append({"author": author, "parts": len({a.part for a in same_part} & parts),
                     "topic": round(float(np.mean(sims)), 3), "samples": len(mine), "_mine": mine})
    if not rows:
        return None
    top = max((r["parts"], r["topic"], r["samples"]) for r in rows)
    level = [r for r in rows if (r["parts"], r["topic"], r["samples"]) == top]
    best = level[seed % len(level)]
    return {"author": best["author"], "answers": best["_mine"], "band": author_band(best["_mine"]),
            "candidates": [{k: v for k, v in r.items() if not k.startswith("_")} for r in
                           sorted(rows, key=lambda r: (r["parts"], r["topic"], r["samples"]), reverse=True)[:6]]}


async def get_style_profile(author: str, answers: list[BookAnswer]) -> StyleProfile:
    path = style_path(author)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    source_ids = sorted(a.id for a in answers)
    if data.get("source_ids") == source_ids and data.get("profile"):
        return StyleProfile(**data["profile"])
    chosen = sorted(answers, key=lambda a: (a.part, a.id))[:9]
    text = "\n\n".join(f"--- Part {a.part} answer ---\nQuestion: {a.question}\n\n{a.text}" for a in chosen)
    response = await client.beta.chat.completions.parse(
        model=settings.openai_model_sample,
        messages=[
            {"role": "system", "content": "You are an expert in IELTS speaking style. From the spoken model "
             "answers below, all by the same speaker, describe their spoken style precisely enough that "
             "another speaker could imitate it on new questions: how they open an answer (introduction), "
             "how they build it (body_paragraphs: answer, reason, example...), how they give examples, "
             "their typical linkers and discourse markers, sentence length, vocabulary register and tone, "
             "how they close an answer (conclusion), and 5-10 short functional signature phrases (frames, "
             "never topic content). Don't name the speaker or any book."},
            {"role": "user", "content": text},
        ],
        response_format=StyleProfile,
    )
    usage.record("style_profile", response)
    profile = response.choices[0].message.parsed
    if profile is None:
        raise RuntimeError("Speaking style profile returned nothing")
    write_json(path, {"author": author, "source_ids": source_ids, "band": author_band(answers),
                      "profile": profile.model_dump()})
    return profile


# --- Writing answers --------------------------------------------------------------------------------


class _Answer(BaseModel):
    answer: str = Field(description="The spoken answer, as plain text (Part 2: paragraphs separated by blank lines).")


class _Check(BaseModel):
    label: str
    ok: bool
    problem: str = Field(description="Empty when ok; else what to fix, one line.")


class _Checks(BaseModel):
    checks: list[_Check]


def _opener(text: str) -> str:
    return " ".join(re.findall(r"[a-z']+", text.lower())[:3])


def _system(part: str, band: float, profile: StyleProfile | None, references: list[BookAnswer]) -> str:
    lo, hi = LENGTHS[part]
    style = ""
    if profile is not None:
        clean = json.dumps(profile.model_dump(), ensure_ascii=False, indent=1)
        for name in forbidden_names() + sorted({r.author for r in references} | {r.book for r in references}):
            clean = re.sub(re.escape(name), "the speaker", clean, flags=re.IGNORECASE)
        refs = "\n\n".join(f"--- Their answer {i} (a different question) ---\nQuestion: {r.question}\n\n{r.text}"
                           for i, r in enumerate(references, 1))
        style = (
            "\n\nSpeak in the style of ONE speaker, described by the style profile below and shown in their "
            f"own answers to other questions.\n\nSTYLE PROFILE:\n{clean}\n\nTHEIR OWN ANSWERS - style "
            f"references only:\n{refs}\n\nFollow their openings, answer structure, linkers, sentence style "
            "and register. Do NOT copy their sentences; short functional phrases are fine."
        )
    return (
        "You write original IELTS Speaking model answers that students read and listen to.\n\n"
        f"{PART_NOTES[part]}\nSpeak at IELTS band {band:.1f} - natural and fluent, not above that level, "
        f"not written English. Length: {lo}-{hi} words (count before answering).\n"
        "Spoken style: contractions (I'd, it's, don't), natural openers that vary (\"To be honest, ...\", "
        "\"I'd say ...\", \"Well, ...\", or just the direct answer; \"Definitely\"/\"Absolutely\"/\"Not "
        "really\" only answer a yes/no question), answer first, then a reason, then a short example. Keep "
        "ONE consistent position from start to finish; every example must fit the point it supports. "
        "Answer exactly the question asked - never change its type.\n"
        "When the student's own answer is given, build on it: keep their ideas, facts about themselves and "
        "position where they make sense, and show how to say them at this band (you may add a reason or an "
        "example). If their answer is unclear or contradicts itself, choose the clearest reading.\n"
        "No invented real-world facts: no statistics, studies, dates, named organisations or claims about "
        "real places presented as true; personal details are fine. Never mention any teacher, speaker or "
        "book." + style
    )


async def _write_one(target: Target, band: float, profile: StyleProfile | None, references: list[BookAnswer],
                     note: str = "") -> str:
    lo, hi = LENGTHS[target.part]
    system = _system(target.part, band, profile, references) + (f"\n\n{note}" if note else "")
    user = f"QUESTION:\n{target.question}"
    if target.student_answer.strip():
        user += f"\n\nTHE STUDENT'S OWN ANSWER (speech transcript):\n{target.student_answer.strip()}"
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    best, best_off = "", 10**6
    for attempt in range(3):
        response = await client.beta.chat.completions.parse(
            model=settings.openai_model_sample, messages=messages, response_format=_Answer,
            max_completion_tokens=ANSWER_MAX_TOKENS, extra_body={"reasoning_effort": "low"},
        )
        usage.record("model_answer", response)
        parsed = response.choices[0].message.parsed
        text = (parsed.answer.strip() if parsed else "")
        if not text:
            continue
        words = count_words(text)
        off = 0 if lo <= words <= hi else min(abs(words - lo), abs(words - hi))
        leaked = names_in(text)
        if leaked:
            off += 1000
        if off < best_off:
            best, best_off = text, off
        if off == 0:
            break
        problems = ([f"it has {words} words; it must be {lo}-{hi}"] if not lo <= words <= hi else []) + \
                   ([f"remove the names {', '.join(leaked)}"] if leaked else [])
        messages = messages[:2] + [{"role": "assistant", "content": text},
                                   {"role": "user", "content": "Revise it: " + "; ".join(problems) + "."}]
    if not best:
        raise RuntimeError(f"No model answer for {target.label}")
    return best


async def _quality_check(pairs: list[tuple[Target, str]]) -> dict[str, str]:
    """{label: problem} for answers that fail the report's quality check."""
    text = "\n\n".join(f"[{t.label}] QUESTION: {t.question}\nANSWER: {a}" for t, a in pairs)
    system = (
        "You check IELTS Speaking model answers before students see them. For each answer, ok=false if ANY of "
        "these fails, with the problem in one line:\n"
        "1. It answers exactly the question asked, with no change of question type: a yes/no question gets a "
        "clear yes/no-style answer first; a \"why\" question gives reasons; a Part 2 talk covers every cue-card "
        "point.\n"
        "2. Its position is consistent from start to finish - it never says one thing and then the opposite.\n"
        "3. Every example fits the point it supports.\n"
        "4. It doesn't open with the same stock phrase as another answer in this set.\n"
        "5. It states no invented statistics or real-world facts as true."
    )
    response = await client.beta.chat.completions.parse(
        model=settings.openai_model_grading,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": text}],
        response_format=_Checks, max_completion_tokens=6000,
        extra_body={"reasoning_effort": settings.openai_grading_reasoning_effort},
    )
    usage.record("answer_check", response)
    parsed = response.choices[0].message.parsed
    return {c.label: c.problem for c in (parsed.checks if parsed else []) if not c.ok}


def _shared_openers(pairs: list[tuple[Target, str]]) -> set[str]:
    """Answers that open with the same first three words as an earlier one."""
    seen, repeats = set(), set()
    for t, a in pairs:
        if _opener(a) in seen:
            repeats.add(t.label)
        seen.add(_opener(a))
    return repeats


async def build_model_answers(targets: list[Target], seed: int = 0) -> list[dict]:
    """[{"label", "qids", "question", "paragraphs", "words", "seconds", "band"}],
    one per target, in order."""
    from services.ai.speaking_grader import score_answers_text  # the heavier module

    log: dict = {"answers": {}}
    library = []
    try:
        library = load_library()
    except Exception:
        logger.exception("Could not load the speaking library")

    exact: dict[str, BookAnswer] = {}
    if library and settings.allow_book_answers_verbatim:
        for t in targets:
            match = exact_match(t, library)
            if match is not None:
                exact[t.label] = match
    to_write = [t for t in targets if t.label not in exact]

    band, profile, references = DEFAULT_BAND, None, []
    if to_write and library:
        try:
            vecs = [np.asarray(v, dtype=float) for v in await embed_texts([t.question for t in to_write])]
            choice = choose_author(to_write, vecs, library, seed)
            if choice is not None:
                band = choice["band"]
                parts = {int(t.part[-1]) for t in to_write}
                pool = [a for a in choice["answers"] if a.part in parts
                        and not any(exact_match(t, [a]) for t in to_write)]
                sims = {a.id: max(_cosine(v, a.embedding) for v in vecs) for a in pool if a.embedding is not None}
                references = sorted(pool, key=lambda a: -sims.get(a.id, 0))[:REFERENCES]
                profile = await get_style_profile(choice["author"], choice["answers"])
                log.update(author=choice["author"], band=band, reference_ids=[r.id for r in references],
                           candidates=choice["candidates"])
        except Exception:
            logger.exception("Choosing a speaking author failed - answers are written without a style")
            band, profile, references = DEFAULT_BAND, None, []
    if not library:
        log["library"] = "none - generated without an author style"

    texts: dict[str, str] = {t.label: exact[t.label].text.strip() for t in targets if t.label in exact}
    for t in targets:
        if t.label in exact:
            a = exact[t.label]
            log["answers"][t.label] = {"source": "book", "author": a.author, "sample_id": a.id, "band": a.band}

    if to_write:
        written = await asyncio.gather(*(_write_one(t, band, profile, references) for t in to_write),
                                       return_exceptions=True)
        for t, w in zip(to_write, written):
            if isinstance(w, BaseException):
                logger.error("Model answer %s failed: %r", t.label, w)
            else:
                texts[t.label] = w
        generated = [t for t in to_write if t.label in texts]
        # Quality check (one call), then the self-check score.
        try:
            pairs = [(t, texts[t.label]) for t in targets if t.label in texts]
            problems = await _quality_check(pairs)
            for label in _shared_openers(pairs):
                if label not in exact:
                    problems.setdefault(label, "opens with the same phrase as another answer - use a different opener")
            redo = [t for t in generated if t.label in problems]
            if redo:
                log["quality_fixes"] = {t.label: problems[t.label] for t in redo}
                again = await asyncio.gather(*(
                    _write_one(t, band, profile, references, note=f"A reviewer rejected a previous version: "
                               f"{problems[t.label]}. Avoid these openers: "
                               + ", ".join(sorted({_opener(texts[x.label]) for x in targets if x.label in texts})))
                    for t in redo), return_exceptions=True)
                for t, w in zip(redo, again):
                    if not isinstance(w, BaseException):
                        texts[t.label] = w
        except Exception:
            logger.exception("Model answer quality check failed - answers kept as written")
        try:
            by_part: dict[str, list[Target]] = {}
            for t in generated:
                by_part.setdefault(t.part, []).append(t)
            log["self_check"] = {}
            for part, group in by_part.items():
                score = await score_answers_text(part, [(t.question, texts[t.label]) for t in group])
                log["self_check"][part] = [score]
                if score < band - SELF_CHECK_MARGIN:
                    note = (f"An examiner scored a previous version {score:.1f}. These answers must read as clear "
                            f"Band {band:.1f} speech: develop each answer with a reason and an example, with "
                            "precise, natural vocabulary.")
                    again = await asyncio.gather(*(_write_one(t, band, profile, references, note) for t in group),
                                                 return_exceptions=True)
                    candidate = {t.label: w for t, w in zip(group, again) if not isinstance(w, BaseException)}
                    if len(candidate) == len(group):
                        second = await score_answers_text(part, [(t.question, candidate[t.label]) for t in group])
                        log["self_check"][part].append(second)
                        if second >= score:
                            texts.update(candidate)
        except Exception:
            logger.exception("Model answer self-check failed - answers kept as written")
        for t in generated:
            log["answers"][t.label] = {"source": "generated", "author": log.get("author"), "band": band}

    out = []
    for t in targets:
        if t.label not in texts:
            continue
        text = texts[t.label]
        words = count_words(text)
        a = exact.get(t.label)
        out.append({
            "label": t.label, "qids": t.qids, "part": t.part,
            "question": t.question.strip().splitlines()[0] if t.part == "part2" else t.question,
            "paragraphs": [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()],
            "words": words, "seconds": round(words / WORDS_PER_MINUTE * 60),
            "band": a.band if a else band,
        })
        log["answers"].setdefault(t.label, {})["words"] = words
    logger.info("Speaking model answers: %s", json.dumps(log, ensure_ascii=False))
    return out


def targets_for(items) -> list[Target]:
    """One target per question; a repeated question joins the one it repeats
    ("Q3 · Q4", asked with the later wording)."""
    groups: dict[str, Target] = {}
    order: list[str] = []
    for item in items:
        key = item.duplicate_of or item.qid
        question = (item.cue_card["topic"] + "\n" + "\n".join(item.cue_card["points"])) if item.cue_card else item.question
        said = item.audio.text if not item.audio.no_speech else ""
        if key in groups:
            g = groups[key]
            g.qids.append(item.qid)
            g.label = " · ".join(g.qids)
            g.question = question
            g.student_answer = f"{g.student_answer}\n{said}".strip()
        else:
            groups[key] = Target(label=item.qid, qids=[item.qid], part=item.part, question=question, student_answer=said)
            order.append(key)
    return [groups[k] for k in order]
