"""Speaking (Part 1 / 2 / 3 / full test) grading against the IELTS Speaking
Band Descriptors (Speaking report prompt, 2026-10-13).

  1. scores: SCORE_RUNS independent scoring calls, whole bands, median per
     criterion (services/ai/scoring.py). Fluency and Pronunciation are scored
     from what was measured in the recording (services/ai/voice.py: pace,
     pauses, fillers; services/ai/pronunciation.py: Azure's pronunciation
     assessment) - or, without Azure, from an audio model's listening notes,
     and the report then calls the Pronunciation band estimated;
  2. the feedback call returns SpeakingFeedback (JSON only), written for the
     scores already decided, so its notes and priorities match the numbers.
     If speaking_marking's `assess` finds a problem worth paying for, the
     call is made once more and the better answer kept;
  3. a second call finds mistakes the first one missed;
  4. a third call checks every mistake: is it definitely wrong, and does the
     correction keep the student's meaning? Anything that isn't is dropped;
  5. speaking_marking.build_report validates everything and assembles the report.

`score_answers_text` is the same examiner scoring a written-out answer -
the model answers' self-check (services/ai/speaking_answers.py).

All calls run gpt-5 at settings.openai_grading_reasoning_effort with an
output-token limit (see writing_grader.py for why).
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import wave
from dataclasses import dataclass

import openai

from bot.config import settings
from services.ai import usage
from services.ai.openai_client import client, parse_flex
from services.ai.pronunciation import Assessment
from services.ai.schemas import SpeakingFeedback, SpeakingMissedMistakes, SpeakingScores, Verdicts
from services.ai.scoring import LENIENCY_NOTE, median_of_runs
from services.ai.voice import CONFIDENCE_LIMIT, AnswerAudio, Pitch, VoiceMetrics

logger = logging.getLogger(__name__)

FEEDBACK_MAX_TOKENS = 24000
MISSED_MAX_TOKENS = 8000
VERIFY_MAX_TOKENS = 8000
SCORE_MAX_TOKENS = 4000
LISTEN_MIN_WORDS = 15  # too little speech for listening notes
SCORE_KEYS = ("FC", "LR", "GRA", "P")

# Condensed paraphrase of the public IELTS Speaking Band Descriptors.
BAND_DESCRIPTORS = """\
FLUENCY & COHERENCE
9: Fluent; any hesitation is about content, not language; fully coherent; topics developed fully.
8: Fluent with only very occasional repetition or self-correction; hesitation mostly about content; \
coherent, relevant development.
7: Keeps going and produces long turns without noticeable effort; some hesitation, repetition or \
self-correction, often mid-sentence while searching for language, but coherence is kept; flexible \
use of discourse markers and connectives.
6: Willing to produce long turns, but coherence is lost at times through hesitation, repetition or \
self-correction; a range of connectives, not always appropriately.
5: Usually keeps going, but relies on repetition, self-correction and/or slow speech; hesitation \
while searching for fairly basic words and grammar; overuses certain connectives; complex speech \
causes disfluency.
4: Can't keep going without noticeable pauses; slow speech, frequent repetition and self-correction; \
links simple sentences with repetitive connectives; some breakdowns in coherence.

LEXICAL RESOURCE
9: Total flexibility and precision; sustained accurate, idiomatic language.
8: Wide resource used flexibly to convey precise meaning; skilful use of less common and idiomatic \
items with occasional inaccuracies; effective paraphrase.
7: Resource used flexibly on a variety of topics; some less common and idiomatic items, awareness \
of style and collocation, with some inappropriacies; effective paraphrase.
6: Enough vocabulary to discuss topics at length; some inappropriate word choice but meaning is \
clear; generally paraphrases successfully.
5: Enough for familiar and unfamiliar topics but little flexibility; paraphrase attempted, not \
always successfully.
4: Enough for familiar topics; only basic meaning on unfamiliar ones; frequent errors in word \
choice; rarely paraphrases.

GRAMMATICAL RANGE & ACCURACY
9: Precise and accurate structures at all times, apart from native-speaker-like slips.
8: Wide range used flexibly; most sentences error-free; occasional non-systematic errors.
7: A range of structures used flexibly; error-free sentences are frequent; simple and complex \
sentences used effectively despite some errors; a few basic errors persist.
6: A mix of short and complex forms with limited flexibility; errors frequent in complex structures \
but rarely impede communication.
5: Basic sentence forms fairly well controlled; complex structures attempted but limited and nearly \
always contain errors.
4: Basic forms and some error-free short utterances; subordinate clauses rare; turns short, \
structures repetitive, errors frequent.

PRONUNCIATION
9: Full range of features; effortless to understand; accent has no effect on intelligibility.
8: Wide range of features; appropriate rhythm; flexible stress and intonation over long utterances \
with occasional lapses; easy to understand throughout.
7: All the positive features of band 6 and some, not all, of band 8.
6: A range of features with variable control; chunking generally appropriate but rhythm may suffer; \
some effective intonation and stress, not sustained; occasional mispronunciation causes only \
occasional lack of clarity; understood without much effort.
5: All the positive features of band 4 and some, not all, of band 6.
4: Limited range; frequent lapses in rhythm; limited control of intonation and stress; frequent \
mispronunciation causes lack of clarity; some effort needed to understand."""

PART_NOTES = {
    "part1": "This is Speaking PART 1: short questions about familiar topics. Good answers are direct "
    "and extended naturally with a reason or example (2-4 sentences); a one-line answer that stops "
    "early limits Fluency & Coherence.",
    "part2": "This is Speaking PART 2, the long turn: the student talks for 1-2 minutes about a cue "
    "card, covering its points. Score sustained, organised speech; a talk well under a minute limits "
    "Fluency & Coherence.",
    "part3": "This is Speaking PART 3: abstract discussion questions. Good answers develop, justify "
    "and compare ideas, not just describe personal experience.",
    "full": "This is a FULL Speaking test: Part 1 (short answers on familiar topics), Part 2 (a 1-2 "
    "minute long turn on a cue card) and Part 3 (abstract discussion). Score the whole performance.",
}


@dataclass
class SessionItem:
    qid: str  # "Q1"
    part: str  # the part this question belongs to: part1 / part2 / part3
    question: str
    audio: AnswerAudio
    cue_card: dict | None = None  # Part 2: {"topic", "points"}
    duplicate_of: str | None = None  # "Q3" when this repeats an earlier question


@dataclass
class Evidence:
    metrics: VoiceMetrics
    pron: Assessment | None
    listening_notes: str | None = None
    pitch: Pitch | None = None


# --- Prompt pieces ------------------------------------------------------------------------------------


def _fmt_seconds(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _question_block(item: SessionItem) -> str:
    head = f"{item.qid} ({item.part.replace('part', 'Part ')})"
    if item.duplicate_of:
        head += f" - the same question as {item.duplicate_of}, asked again"
    if item.cue_card:
        points = "\n".join(f"  - {p}" for p in item.cue_card["points"])
        return f"{head} CUE CARD: {item.cue_card['topic']}\n{points}"
    return f"{head}: {item.question}"


def answers_text(items: list[SessionItem], mark_uncertain: bool) -> str:
    """The questions and verbatim answers. With `mark_uncertain`, words the
    recogniser may have misheard are followed by (?)."""
    blocks = []
    for item in items:
        a = item.audio
        if a.no_speech or not a.text:
            answer = "(no speech recorded)"
        elif mark_uncertain:
            out, pos = [], 0
            for t in a.tokens:
                if t.conf < CONFIDENCE_LIMIT:
                    out.append(a.text[pos : t.end_char] + "(?)")
                    pos = t.end_char
            out.append(a.text[pos:])
            answer = "".join(out)
        else:
            answer = a.text
        meta = f"[{_fmt_seconds(a.speaking_seconds)}, {a.word_count} words]"
        blocks.append(f"{_question_block(item)}\nANSWER {item.qid} {meta}:\n{answer}")
    return "\n\n".join(blocks)


def evidence_text(items: list[SessionItem], ev: Evidence) -> str:
    m = ev.metrics
    lines = [
        "MEASURED FROM THE RECORDING (software, not an opinion):",
        f"- Speaking time {_fmt_seconds(m.speaking_seconds)} over {len(items)} answer(s); pace {m.wpm} "
        "words a minute (natural speech is about 110-160).",
        f"- Pauses over 1 second: {m.long_pauses} ({m.mid_sentence_pauses} in the middle of a sentence)"
        + (f", the longest {m.longest_pause.seconds:.1f} s" if m.longest_pause else "") + ".",
        f"- Fillers: {m.fillers} ({m.fillers_per_minute:.1f} a minute): "
        + (", ".join(f"{name} x{n}" for name, n in m.filler_types) or "none") + ".",
    ]
    if ev.pitch is not None:
        lines.append(
            f"- Intonation: the voice's pitch moves over {ev.pitch.span:.1f} semitones (10th-90th "
            "percentile; natural, lively speech is about 8-12, under 5 sounds flat)."
        )
    if ev.pron is not None:
        p = ev.pron
        low = sorted(
            (w for w in p.words if w.word.isalpha() and len(w.word) > 2), key=lambda w: w.accuracy
        )[:8]
        lines += [
            "PRONUNCIATION ASSESSMENT (Azure speech service, 0-100, measured against the transcript):",
            f"- accuracy {p.accuracy:.0f}, fluency {p.fluency:.0f}, completeness {p.completeness:.0f}"
            + (f", prosody (stress, intonation, rhythm) {p.prosody:.0f}" if p.prosody is not None else "") + ".",
            "- lowest-scoring words: " + (", ".join(f"{w.word} {w.accuracy:.0f}" for w in low) or "none") + ".",
            "As a rough guide only: accuracy and prosody in the 90s fit band 8-9, the 80s band 7, the 70s "
            "band 6, the 60s band 5, lower band 4 - weigh it together with the descriptors.",
        ]
    elif ev.listening_notes:
        lines += [
            "LISTENING NOTES (an examiner who heard the recording; no measurement is available - the "
            "Pronunciation band will be shown as an estimate):",
            ev.listening_notes.strip(),
        ]
    else:
        lines.append(
            "No pronunciation evidence is available: estimate Pronunciation from the transcript's "
            "disfluencies only, and stay close to the student's other bands."
        )
    return "\n".join(lines)


def _scoring_system(part: str) -> str:
    return f"""You are a certified IELTS Speaking examiner. Score the student's recorded answers \
against the IELTS Speaking Band Descriptors below.

{PART_NOTES[part]}

IELTS SPEAKING BAND DESCRIPTORS (summary):
{BAND_DESCRIPTORS}

{LENIENCY_NOTE}

The transcript is verbatim speech recognition: fillers (uh, um), repeats and false starts were \
really said; punctuation is the recogniser's guess, not the student's. Words followed by (?) may be \
misheard - don't count them as errors. Score Fluency & Coherence from the content AND the measured \
pace, pauses and fillers; Pronunciation from the pronunciation evidence. Return the four bands as \
whole numbers."""


def _feedback_system(part: str, scores: dict[str, float], overall: float, target: float, has_pron: bool) -> str:
    """FEEDBACK_RULES (the same for every check, so OpenAI's prompt cache bills
    it at a tenth of the price when checks follow each other) and then what
    is particular to this student."""
    notes = [
        PART_NOTES[part],
        f"THE SCORES ARE ALREADY DECIDED (separate scoring runs): Fluency & Coherence {scores['FC']:.0f}, "
        f"Lexical Resource {scores['LR']:.0f}, Grammatical Range & Accuracy {scores['GRA']:.0f}, Pronunciation "
        f"{scores['P']:.0f}; overall band {overall:.1f}; the student's next target is {target:.1f}. Everything "
        "you write must be consistent with these scores.",
        "Priorities: where real mistakes are few"
        + (" (the overall band is 7 or above)" if overall >= 7 else "")
        + ", take them from what separates this band from the next in the descriptors (extend answers with "
        "reasons and examples, less common vocabulary and collocations, more complex structures, livelier "
        "intonation), with mistake_ids=[] - never from trivial slips.",
    ]
    if not has_pron:
        notes.append("The P note (criterion_notes) is based on the listening notes.")
    if part in ("part1", "full"):
        notes.append('Vocabulary: include natural spoken Part 1 phrases ("I\'d say I\'m fairly ...", "to be '
                     'honest, ...").')
    return FEEDBACK_RULES + "\n\nTHIS STUDENT AND THIS TEST:\n" + "\n\n".join(notes)


FEEDBACK_RULES = f"""You are a certified, meticulous IELTS Speaking examiner AND a warm, encouraging ESL \
tutor writing a feedback report on a student's recorded answers. The part of the test, the decided \
scores and a few rules for this student are given at the end. Be supportive in tone, honest in content.

IELTS SPEAKING BAND DESCRIPTORS (summary):
{BAND_DESCRIPTORS}

INPUT: each answer's id (Q1, Q2...), the question, and the VERBATIM transcript from speech \
recognition. Fillers (uh, um, like...), repeats and false starts were really said and stay in the \
transcript. Punctuation and capitals are the recogniser's guess, not the student's - never correct \
punctuation, capitals or spelling. Words listed under UNCERTAIN WORDS may be misheard: never base a \
mistake on them. Fillers are counted by software and shown on a separate page: never list a filler \
on its own as a mistake (only when it is part of a wrong phrase, e.g. "lost like a part of" -> \
"lost part of").

SPOKEN ENGLISH IS NOT WRITTEN ENGLISH. Contractions, short answers ("Not really."), informal \
phrases ("to be honest", "kind of", "pretty much"), natural fragments and starting with "and", \
"but" or "so" are all fine. Flag only what an IELTS examiner would penalise: grammar errors, wrong \
or unnatural words and collocations, word forms, meaning that is unclear, an answer that doesn't \
answer the question or stops without developing. Never correct what is already correct: to "Are \
you the kind of person who sticks to dreams?" the answer "I can't say I am" is correct - don't \
change it. A correction keeps the student's meaning; a style preference is not a mistake.

Your output is rendered into the report by software, so follow the field rules exactly. Write in \
English, in plain words a B1-B2 learner understands.

1. mistakes - EVERY real mistake, in answer order, ids 1, 2, 3...:
   - question: the answer's id. wrong: ONLY the part that matters, copied exactly from that answer.
   - correct: the corrected part, keeping the meaning; it must fit back into the spoken sentence \
(put it in place of `wrong` and read the sentence: no doubled words).
   - why: one plain-English line, 25 words or fewer.
   - criterion: GRA (tense, verb pattern, article, agreement, word order, sentence structure); LR \
(word choice, collocation, word form, unnatural expression); FLU (an answer that is unclear, \
contradicts itself, doesn't answer the question, or ends without developing, e.g. "and yeah, \
that's my job" -> add a reason); PRON only for a word the pronunciation evidence lists as \
mispronounced.
   - unclear meaning: unclear=true; why says why the listener loses the point; if_you_meant = the \
most likely meaning; correct = a natural way to say that meaning. Otherwise unclear=false, \
if_you_meant=null.
   - Then do a SECOND pass over every answer whose only job is to find mistakes you missed - verb \
patterns (used to + base verb, would for imagined things), "after becoming 18" (turn + age), fixed \
phrases and prepositions, a verb missing its object ("help" -> "help people").
2. answers - for every answer id, its transcript copied EXACTLY as a list of segments:
   - "good": 2-5 genuinely strong phrases in the WHOLE session (not per answer) - language above \
the student's level worth keeping: a natural linking or discourse phrase, a precise or idiomatic \
expression, a well-built complex structure. Never an ordinary simple clause any learner would say, \
never text with a mistake, a filler or an uncertain word.
   - "error": the exact text of a mistake with its mistake_id; fix = the replacement if it is 6 \
words or fewer (empty string to delete it), else null. Every mistake needs an error segment.
   - "text": everything else. Joining an answer's segments must give back its transcript exactly - \
fillers, repeats and punctuation included. Don't number anything.
3. criterion_notes - FC, LR, GRA, P: one short line each (6-14 words) on what drives that score. \
The FC note may mention answer length, fillers or pauses.
4. strength - text: 1-2 sentences (40 words at most) on what the student genuinely does well, \
quoting 1-2 of the phrases you marked "good", word for word, in double quotes; quoted_phrases: \
those phrases exactly as in the transcript. No generic praise.
5. priorities - exactly 3, the most important first, each about ONE specific pattern (never a \
whole category, never one trivial word): title (2-6 words), rule (one line, 15 words at most), \
example_wrong (2-8 words copied exactly from an answer) and example_right (improved), criteria \
(its tags). A priority about the measured voice - fillers, pace or pauses (tag FLU), a \
pronunciation pattern (tag PRON) - has voice_ref=true and mistake_ids=[]; any other has \
voice_ref=false and mistake_ids = 2-5 ids from \
`mistakes` showing this pattern, each with one of the priority's criteria.
6. vocabulary_topic (3-6 words) and vocabulary - 10-12 words or phrases useful for speaking on \
this topic, about one band above the student: term, plain-English meaning, an English example \
sentence a student could say.
7. checklist - 5-6 short habits for "Before your next speaking practice", built ONLY from this \
report's priorities and mistakes and written as forward-looking habits ("Every answer has a direct \
answer, a reason and an example."), never "I fixed ..."; ref = "Priority 01".."Priority 03", \
"Mistake <id>", or "Your voice" for a voice habit.
8. cue_coverage - Part 2 only: every cue-card point and whether the talk covered it; [] otherwise.

Plain text only. In why, rule and checklist you may put a key word in **double asterisks** or \
*single asterisks*; no other markup."""


MISSED_SYSTEM = """You are a meticulous IELTS Speaking examiner. Another examiner has already \
listed the mistakes in a student's recorded answers (below). Your ONLY job is to find real mistakes \
they MISSED. Read every answer again, sentence by sentence. Typical misses in speech: verb patterns \
(used to + base verb, would for imagined things, want + to), turning an age ("after I turned 18"), \
prepositions in fixed phrases, a verb missing its object, countable/uncountable nouns, wrong word \
partners (collocations).

Rules:
- Don't repeat or overlap a listed mistake.
- The transcript is verbatim speech recognition: fillers and repeats are real; punctuation is not \
the student's - never flag punctuation, capitals or spelling, never flag a filler on its own, and \
never use a word marked (?) - it may be misheard.
- Spoken English is not written English: contractions, short answers, informal phrases and natural \
fragments are fine. Only clear errors an IELTS examiner would penalise. When in doubt, leave it out.
- question: the answer's id. wrong: copied exactly from that answer (without "(?)"), long enough \
to occur only once. correct: that part corrected, keeping the meaning; it must fit back into the \
sentence. why: one plain line, 25 words or fewer. criterion: GRA, LR or FLU. unclear=true with \
if_you_meant only when the meaning can't be determined.
- If nothing was missed, return an empty list."""


VERIFY_SYSTEM = """You are a senior IELTS Speaking examiner double-checking a colleague's list of \
mistakes in a student's recorded answers. For each item decide whether to keep it.

keep=false when:
- the original is ALREADY CORRECT, grammatical and natural in its context - read the whole \
sentence: a verb may correctly continue an earlier verb phrase, a negative or future form may be \
correct as it stands;
- it is only a STYLE PREFERENCE or a different-but-equally-good choice (a near-synonym, a slightly \
plainer word, "In my childhood" vs "When I was a child");
- the correction CHANGES WHAT THE STUDENT MEANT;
- it is natural spoken English: contractions, short answers ("I can't say I am" is a correct answer \
to "Are you the kind of person who...?"), informal phrases ("kind of", "to be honest"), fragments, \
starting with "and"/"so";
- it is about punctuation, capitals or spelling (the transcript's punctuation is the speech \
recogniser's, not the student's), or it is about a filler alone (a filler inside a wrong phrase, \
"lost like a part of" -> "lost part of", is a real error).

keep=true for every real error an examiner would penalise, including collocation errors, \
wrong-meaning words, verb-pattern errors, unclear meaning, and vague or unnatural words a fluent \
speaker wouldn't use where a precise one is expected (an invented job title, a word that doesn't \
fit the thing named). A "plainer" or "more precise" option is a style preference only when the \
original is already natural.

Judge each item on its own; give a short reason. Return a verdict for every id."""


# --- Calls --------------------------------------------------------------------------------------------


async def parse_call(purpose: str, max_tokens: int, messages: list[dict], response_format, model: str | None = None):
    """A gpt-5 call at the grading reasoning effort with an output-token
    limit; a cut-off answer raises openai.LengthFinishReasonError (its cost
    is logged all the same)."""
    try:
        response = await parse_flex(
            model=model or settings.openai_model_grading,
            messages=messages,
            response_format=response_format,
            max_completion_tokens=max_tokens,
            # gpt-5 accepts no temperature; openai 1.57 has no reasoning_effort argument yet.
            # parse_flex: the Flex tier at half price, the default tier as a fallback.
            extra_body={"reasoning_effort": settings.openai_grading_reasoning_effort},
        )
    except openai.LengthFinishReasonError as e:
        if getattr(e, "completion", None) is not None:
            usage.record(purpose, e.completion)
        raise
    usage.record(purpose, response)
    return response


async def score_session(part: str, items: list[SessionItem], ev: Evidence) -> dict[str, float]:
    """Median of SCORE_RUNS scoring runs, whole bands: {"FC", "LR", "GRA", "P"}."""
    messages = [
        {"role": "system", "content": _scoring_system(part)},
        {"role": "user", "content": f"{answers_text(items, mark_uncertain=True)}\n\n{evidence_text(items, ev)}"},
    ]

    async def once() -> dict[str, float]:
        response = await parse_call("score", SCORE_MAX_TOKENS, messages, SpeakingScores)
        parsed = response.choices[0].message.parsed
        if parsed is None:
            raise RuntimeError("Scoring returned nothing")
        return parsed.model_dump()

    bands, _ = await median_of_runs(once, SCORE_KEYS, label=f"speaking {part}")
    return bands


async def score_answers_text(part: str, qa: list[tuple[str, str]]) -> float:
    """The overall band our examiner gives written-out answers (model answers'
    self-check): one run, transcript only, pronunciation left out."""
    from services.ai.scoring import overall_band, whole_band

    text = "\n\n".join(f"Q{i}: {q}\nANSWER Q{i}:\n{a}" for i, (q, a) in enumerate(qa, 1))
    system = (
        _scoring_system(part)
        + "\n\nThese answers are written out, not recorded: judge them as if spoken fluently, and give "
        "Pronunciation the same band as Lexical Resource."
    )
    response = await parse_call(
        "self_check", SCORE_MAX_TOKENS, [{"role": "system", "content": system}, {"role": "user", "content": text}],
        SpeakingScores,
    )
    s = response.choices[0].message.parsed
    if s is None:
        raise RuntimeError("Self-check scoring returned nothing")
    return overall_band([whole_band(s.FC), whole_band(s.LR), whole_band(s.GRA), whole_band(s.LR)])


def _uncertain_text(items: list[SessionItem]) -> str:
    lines = []
    for item in items:
        words = [t.text for t in item.audio.tokens if t.conf < CONFIDENCE_LIMIT]
        if words:
            lines.append(f"{item.qid}: " + ", ".join(f"\"{w}\"" for w in words))
    return "UNCERTAIN WORDS (may be misheard - never base a mistake on them):\n" + ("\n".join(lines) or "none")


async def feedback_call(
    part: str, items: list[SessionItem], ev: Evidence, scores: dict[str, float], overall: float, target: float
) -> SpeakingFeedback | None:
    """None when the answer ran past FEEDBACK_MAX_TOKENS."""
    text = f"{answers_text(items, mark_uncertain=False)}\n\n{_uncertain_text(items)}\n\n{evidence_text(items, ev)}"
    messages = [
        {"role": "system", "content": _feedback_system(part, scores, overall, target, ev.pron is not None)},
        {"role": "user", "content": text},
    ]
    try:
        response = await parse_call("feedback", FEEDBACK_MAX_TOKENS, messages, SpeakingFeedback)
    except openai.LengthFinishReasonError:
        logger.warning("Speaking feedback ran past %d output tokens - cut off", FEEDBACK_MAX_TOKENS)
        return None
    result = response.choices[0].message.parsed
    if result is None:
        raise RuntimeError("Speaking feedback returned no parsable result")
    return result


async def missed_call(items: list[SessionItem], found) -> list:
    listed = "\n".join(f"{m.question} {m.id}. {m.wrong} -> {m.correct}" for m in found) or "(none)"
    text = f"{answers_text(items, mark_uncertain=True)}\n\nMISTAKES ALREADY LISTED:\n{listed}"
    response = await parse_call(
        "missed_errors", MISSED_MAX_TOKENS,
        [{"role": "system", "content": MISSED_SYSTEM}, {"role": "user", "content": text}],
        SpeakingMissedMistakes,
    )
    parsed = response.choices[0].message.parsed
    return parsed.mistakes if parsed is not None else []


async def verify_call(source_text: str, items: list[tuple[int, str, str, str, str]]) -> dict[int, tuple[bool, str]]:
    """`items`: (id, where, wrong, correct, why). {id: (keep, reason)};
    items without a verdict are kept."""
    if not items:
        return {}
    listed = "\n".join(f"{i}. [{where}] «{wrong}» -> «{correct}» ({why})" for i, where, wrong, correct, why in items)
    response = await parse_call(
        "verify", VERIFY_MAX_TOKENS,
        [{"role": "system", "content": VERIFY_SYSTEM},
         {"role": "user", "content": f"{source_text}\n\nMISTAKES TO CHECK:\n{listed}"}],
        Verdicts,
    )
    parsed = response.choices[0].message.parsed
    return {v.id: (v.keep, v.reason) for v in parsed.verdicts} if parsed else {}


# --- Listening notes (no pronunciation service) --------------------------------------------------------

LISTEN_PROMPT = """You are an IELTS examiner's ear. Listen to this recording carefully and describe, \
in plain text, exactly what you hear about HOW the student speaks - not what they say: clarity of \
individual sounds, word stress, sentence stress, intonation (flat or varied), rhythm and chunking, \
sounds that are consistently mispronounced. Be specific and concrete (e.g. "final consonants often \
dropped", "flat intonation, little rise and fall") - another examiner who cannot hear the audio will \
score Pronunciation from your description alone. 4-8 sentences."""


def join_wavs(wavs: list[bytes]) -> bytes:
    """Several 16 kHz mono WAVs, one after another, as one WAV."""
    out = io.BytesIO()
    with wave.open(out, "wb") as dst:
        for i, wav in enumerate(wavs):
            with wave.open(io.BytesIO(wav)) as src:
                if i == 0:
                    dst.setparams(src.getparams())
                dst.writeframes(src.readframes(src.getnframes()))
    return out.getvalue()


async def listening_notes(wavs: list[bytes]) -> str | None:
    """An audio model's description of the pronunciation - the evidence
    used when no pronunciation service is configured. None if it fails.
    (Too little speech - under LISTEN_MIN_WORDS - is checked by the caller.)"""
    try:
        response = await client.chat.completions.create(
            model=settings.openai_model_audio,
            modalities=["text"],
            messages=[
                {"role": "system", "content": LISTEN_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": "The student's answers, one after another."},
                    {"type": "input_audio", "input_audio": {
                        "data": base64.b64encode(join_wavs(wavs)).decode(), "format": "wav"}},
                ]},
            ],
        )
        usage.record("audio_listen", response)
        return response.choices[0].message.content or None
    except Exception:
        logger.exception("Listening notes failed (model=%s)", settings.openai_model_audio)
        return None


# --- The whole grading ------------------------------------------------------------------------------------


async def grade_speaking(part: str, items: list[SessionItem], ev: Evidence, practise: list[dict] | None = None,
                         on_feedback=None):
    """Scores, feedback, missed-errors pass, verification -> SpeakingReport.
    `on_feedback(scores, overall, feedback)` is called as soon as the
    feedback is final, so work that needs it (the candidate-specific model
    answers) runs alongside the last two passes."""
    # Imported here: speaking_marking imports SessionItem from this module.
    from services.ai.scoring import overall_band, target_band
    from services.ai.speaking_marking import assess, build_report

    scores = await score_session(part, items, ev)
    overall = overall_band(list(scores.values()))
    target = target_band(overall)

    feedback = await feedback_call(part, items, ev, scores, overall, target)
    problems = assess(feedback, items) if feedback else ["the answer ran past its token limit"]
    if problems:
        logger.warning("Speaking feedback needs another try: %s", "; ".join(problems))
        retry = await feedback_call(part, items, ev, scores, overall, target)
        if retry is not None:
            retry_problems = assess(retry, items)
            if feedback is None or len(retry_problems) < len(problems):
                feedback, problems = retry, retry_problems
        if feedback is None:
            raise RuntimeError("Speaking feedback ran past its token limit twice")
        if problems:
            logger.warning("Speaking feedback still has problems after the retry: %s", "; ".join(problems))
    if on_feedback is not None:
        try:
            on_feedback(scores, overall, feedback)
        except Exception:
            logger.exception("on_feedback failed")

    # The first pass's mistakes are verified while the missed-errors pass
    # runs; whatever that pass finds is verified after it (a short call).
    source = answers_text(items, mark_uncertain=True)

    async def verify(checks) -> dict[int, tuple[bool, str]]:
        try:
            return await verify_call(source, checks)
        except Exception:
            logger.exception("Speaking verification pass failed - keeping those mistakes")
            return {}

    async def find_missed() -> list:
        try:
            return await missed_call(items, feedback.mistakes)
        except Exception:
            logger.exception("Speaking missed-errors pass failed - first pass only")
            return []

    # Only language corrections are verified: Fluency items (an unclear or
    # undeveloped answer), Pronunciation items and unclear meaning are
    # judgements about the answer, not corrections that could be "already right".
    def checkable(m) -> bool:
        return m.criterion in ("GRA", "LR") and not m.unclear

    verdicts, missed = await asyncio.gather(
        verify([(m.id, m.question, m.wrong, m.correct, m.why) for m in feedback.mistakes if checkable(m)]),
        find_missed(),
    )
    missed_verdicts = await verify(
        [(k, x.question, x.wrong, x.correct, x.why) for k, x in enumerate(missed) if checkable(x)]
    )
    dropped = {i for i, (keep, _) in verdicts.items() if not keep}
    dropped_missed = {k for k, (keep, _) in missed_verdicts.items() if not keep}
    for label, found in (("mistake", verdicts), ("missed-pass item", missed_verdicts)):
        for i, (keep, reason) in found.items():
            if not keep:
                logger.info("Verification dropped %s %s: %s", label, i, reason)

    report = build_report(part, items, feedback, missed, dropped, dropped_missed, scores, ev.metrics, ev.pron,
                          practise, ev.pitch)
    if report.notes:
        logger.info("Speaking report validation: %s", "; ".join(report.notes))
    return report
