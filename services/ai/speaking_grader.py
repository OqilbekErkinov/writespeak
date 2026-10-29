"""Speaking (Part 1 / 2 / 3 / full test) grading against the IELTS Speaking
Band Descriptors (Speaking report prompt, 2026-10-13).

Mistakes first, then the bands (the academic lead, 2026-10-29: "stop
counting, start judging" - the 9.0 speaker's Grammar came out 8 for five
"mistakes" that were self-corrections and native-like forms):

  1. the feedback call returns SpeakingFeedback (JSON only): the mistakes,
     the marked transcript, priorities, vocabulary. If speaking_marking's
     `assess` finds a problem worth paying for, the call is made once more
     and the better answer kept;
  2. a second call finds mistakes the first one missed;
  3. self-corrections are taken out by code (speaking_marking.self_correction);
     a third call asks of every other flag one question - is it actually
     wrong in natural spoken English? - error, slip, alternative (acceptable
     as said: the report's "Upgrade ideas") or not a mistake; it also checks
     the vocabulary's meanings;
  4. scores: SCORE_RUNS independent scoring calls, whole bands, median per
     criterion (services/ai/scoring.py), judged from the checked errors and
     slips, their density and the range shown. A band below 9 needs a quoted
     reason. Fluency is scored from what was measured in the recording
     (services/ai/voice.py: pace, pauses); fillers, restarts and pauses
     between ideas are natural speech, never a fault (2026-10-27). Without a
     pronunciation measurement the Pronunciation band is the mean of the
     other three, "not measured"; the intonation measured from the pitch
     never reaches the scoring. The scoring prompt carries anchor recordings
     with their official band (data/calibration/speaking_anchors.json,
     config/speaking_marking.json);
  5. speaking_marking.build_report validates everything and assembles the report.

`score_answers_text` is the same examiner scoring a written-out answer -
the model answers' self-check (services/ai/speaking_answers.py).

All calls run gpt-5 at settings.openai_grading_reasoning_effort with an
output-token limit (see writing_grader.py for why).
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import openai

from bot.config import settings
from services.ai import usage
from services.ai.openai_client import parse_flex
from services.ai.pronunciation import Assessment
from services.ai import marking_config
from services.ai.schemas import SpeakingFeedback, SpeakingMissedMistakes, SpeakingScoring, SpeakingVerdicts
from services.ai.scoring import LENIENCY_NOTE, median_of_runs, whole_band
from services.ai.voice import CONFIDENCE_LIMIT, AnswerAudio, Pitch, VoiceMetrics

logger = logging.getLogger(__name__)

FEEDBACK_MAX_TOKENS = 24000
MISSED_MAX_TOKENS = 8000
VERIFY_MAX_TOKENS = 8000
SCORE_MAX_TOKENS = 4000
SCORE_KEYS = ("FC", "LR", "GRA", "P")
CONFIG = Path(__file__).resolve().parents[2] / "config" / "speaking_marking.json"
P_NOT_MEASURED = "Not measured this time: set to the average of your other three bands."
# Anchors left out of the scoring prompt: the regression test of a recording
# that is itself an anchor leaves it out (scripts/speaking_regression.py).
EXCLUDED_ANCHORS: set[str] = set()

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
    pron: Assessment | None  # None: Pronunciation not measured
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


def _marked(a: AnswerAudio, mark_uncertain: bool, mark_pauses: bool) -> str:
    """The transcript with (?) after words the recogniser may have misheard
    and [pause 1.6 s] where the student paused - so the scorer can tell a
    pause before a new idea from a search for a word."""
    inserts: list[tuple[int, str]] = []
    if mark_uncertain:
        inserts += [(t.end_char, "(?)") for t in a.tokens if t.conf < CONFIDENCE_LIMIT]
    if mark_pauses:
        for p in a.pauses:
            nxt = next((t for t in a.tokens if t.start is not None and t.start >= p.end - 0.2), None)
            if nxt is not None:
                inserts.append((nxt.start_char, f"[pause {p.seconds:.1f} s] "))
    out, pos = [], 0
    for at, mark in sorted(inserts, key=lambda x: (x[0], x[1].startswith("["))):
        out.append(a.text[pos:at] + mark)
        pos = at
    out.append(a.text[pos:])
    return "".join(out)


def answers_text(items: list[SessionItem], mark_uncertain: bool, mark_pauses: bool = False) -> str:
    """The questions and verbatim answers. With `mark_uncertain`, words the
    recogniser may have misheard are followed by (?); with `mark_pauses`
    (the scoring runs), pauses over a second are marked where they happened."""
    blocks = []
    for item in items:
        a = item.audio
        if a.no_speech or not a.text:
            answer = "(no speech recorded)"
        else:
            answer = _marked(a, mark_uncertain, mark_pauses)
        meta = f"[{_fmt_seconds(a.speaking_seconds)}, {a.word_count} words]"
        blocks.append(f"{_question_block(item)}\nANSWER {item.qid} {meta}:\n{answer}")
    return "\n\n".join(blocks)


def evidence_text(items: list[SessionItem], ev: Evidence, for_scoring: bool = False) -> str:
    """What was measured. The scoring runs never see the filler count: it is
    no evidence for a band (the academic lead, 2026-10-27)."""
    m = ev.metrics
    lines = [
        "MEASURED FROM THE RECORDING (software, not an opinion):",
        f"- Speaking time {_fmt_seconds(m.speaking_seconds)} over {len(items)} answer(s); pace {m.wpm} "
        "words a minute (natural speech is about 110-160).",
    ]
    if for_scoring:
        lines.append("- Pauses over 1 second are marked in the transcript where they happened.")
    else:
        lines.append(f"- Pauses over 1 second: {m.long_pauses} ({m.mid_sentence_pauses} in the middle of a sentence)"
                     + (f", the longest {m.longest_pause.seconds:.1f} s" if m.longest_pause else "")
                     + ". A pause between sentences or before a new idea is thinking about content; only a pause "
                     "while searching for a word or a grammatical form is a Fluency fault.")
        lines.append(f"- Fillers: {m.fillers} ({m.fillers_per_minute:.1f} a minute): "
                     + (", ".join(f"{name} x{n}" for name, n in m.filler_types) or "none")
                     + ". Fillers are natural in fluent speech - never a fault on their own.")
    if ev.pitch is not None and not for_scoring:  # the intonation comment never affects a band (2026-10-29)
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
    else:
        lines.append("PRONUNCIATION IS NOT MEASURED this time: software sets its band from the other three "
                     "criteria. Make no claim about the student's pronunciation.")
    return "\n".join(lines)


SPOKEN_FLUENCY = """HOW EXAMINERS HEAR SPEECH:
- Fillers and discourse markers (um, uh, you know, I mean, basically, so, well, like, right, actually) \
are part of natural, fluent speech at every band, Band 9 included. Never lower Fluency & Coherence \
for how many there are.
- A pause at the start of an answer, between sentences or before a new idea is thinking about \
content - never a fault. Only hesitation in the middle of a sentence while searching for a word or a \
grammatical form counts against Fluency.
- Restarts, repeated words and self-corrections ("they can, they can basically", "can make them, \
that can make them") are normal in speech: never grammar errors, and a Fluency matter only when they \
are frequent AND come from searching for language.
- Fluency & Coherence is how the ideas flow and develop: extended, relevant, well-linked answers \
with precise language are Band 8-9 however many fillers they contain.
- Slips native speakers make in spontaneous speech (an article, "more free", a sentence restarted \
half-way) are not systematic errors: they don't hold Grammar or Lexical Resource below 8 when the \
range is wide and the meaning precise.
- Pauses over 1 second are marked in the transcript where they happened, e.g. [pause 1.6 s]: judge \
each by what follows - a new idea or sentence (thinking about content, never a fault) or a search \
for a word or a structure (a Fluency fault)."""


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError:
        logger.warning("%s is not valid JSON - ignored", path)
        return None


def speaking_config() -> dict:
    return _load_json(CONFIG) or {}


def lead_accepted(wrong: str) -> str | None:
    """The spoken wording the academic lead ruled no mistake (config
    "accepted") that a flag is about."""
    from services.ai.essay_marking import canon

    low = canon(wrong).lower().strip(" .,")
    for phrase in speaking_config().get("accepted", {}).get("phrases", []):
        p = canon(phrase).lower().strip(" .,")
        if p and (p in low or (len(low) >= 12 and low in p)):
            return phrase
    return None


def anchors_text() -> str:
    """Recordings with their OFFICIAL IELTS band, as the scoring prompt shows
    them (verbatim transcripts, fillers and restarts included - they earned
    that band all the same); "" without any."""
    ids = [i for i in (speaking_config().get("anchors") or {}).get("ids", []) if i not in EXCLUDED_ANCHORS]
    found = _load_json(settings.calibration_dir / "speaking_anchors.json") or {}
    anchors = [(i, found[i]) for i in ids if i in found]
    if not anchors:
        return ""
    out = ["ANCHOR RECORDINGS - real candidates with their OFFICIAL IELTS Speaking band. Calibrate against "
           "them: put the student next to them, criterion by criterion. Their transcripts are verbatim speech "
           "recognition like the student's - fillers, restarts and repeats were really said, and they earned "
           "this band all the same."]
    for k, (_, a) in enumerate(anchors, 1):
        out.append(f"--- Anchor {k}: {a['label']}, {a['part'].replace('part', 'Part ')} ---")
        out += [f"Q: {x['question']}\nA: {x['transcript']}" for x in a["answers"]]
    return "\n\n".join(out)


def _scoring_system(part: str, has_pron: bool = True) -> str:
    pron = ("Pronunciation from the pronunciation evidence" if has_pron else
            "Pronunciation is not measured this time: return the same band as Lexical Resource (software sets it)")
    anchors = anchors_text()
    return f"""You are a certified IELTS Speaking examiner. Score the student's recorded answers \
against the IELTS Speaking Band Descriptors below.

{PART_NOTES[part]}

IELTS SPEAKING BAND DESCRIPTORS (summary):
{BAND_DESCRIPTORS}

{SPOKEN_FLUENCY}

{LENIENCY_NOTE}

{JUDGING}

The transcript is verbatim speech recognition: fillers (uh, um), repeats and false starts were \
really said; punctuation is the recogniser's guess, not the student's. Words followed by (?) may be \
misheard - don't count them as errors. Score Fluency & Coherence by the rules above, with the \
measured pace and pauses; {pron}. For Fluency & Coherence, Lexical Resource and Grammar give the \
strengths, the weaknesses (each with a short quote), the whole band and a 6-14-word note; \
Pronunciation as a whole band.""" + (f"\n\n{anchors}" if anchors else "")


JUDGING = """JUDGE, DON'T COUNT (the academic lead, 2026-10-29):
- The answers come with the CHECKED MISTAKES - errors and slips found by one examiner and checked by \
a second against natural spoken English. Self-corrections ("there are so many, there's so much"; \
"daily lifestyles... routines"), false starts, repeated words, fragments, fillers, natural spoken \
phrases ("and stuff like that", "you know", "I mean") and colloquial forms native speakers use widely \
("much more free") were taken out: they are never mistakes. What is ACCEPTABLE AS SAID is listed \
apart - never count it or quote it as a weakness.
- Weigh errors by their density, their effect on meaning and the range the speaker attempts: an error \
that comes from attempting less common language weighs less. A slip is very small - Band 9 Grammar \
allows mistakes characteristic of native-speaker speech.
- A wide range with a few slips is Band 8 or 9, never 7. Lexical Resource 6 or below only for a \
genuinely limited range or words that make the meaning unclear.
- Below 9 needs a reason: a criterion's weaknesses must quote the transcript (a real error, a search \
for language) or name what is missing. "Could be more varied", "minor slips" with no quote are no \
reasons - with no such weakness, give 9."""


def _feedback_system(part: str, has_pron: bool) -> str:
    """FEEDBACK_RULES (the same for every check, so OpenAI's prompt cache bills
    it at a tenth of the price when checks follow each other) and then what
    is particular to this student. The bands are decided after the list is
    checked (mistakes first, 2026-10-29)."""
    notes = [
        PART_NOTES[part],
        "THE BANDS ARE DECIDED AFTER YOUR LIST IS CHECKED: write criterion_notes as short observations - software "
        "replaces them with the examiner's.",
        "Priorities: from real, repeated patterns of mistakes; where real mistakes are few, from what separates "
        "these answers from Band 9 in the descriptors (extend answers with reasons and examples, less common "
        "vocabulary and collocations, more complex structures), with mistake_ids=[] - never from trivial slips, "
        "self-corrections, restarts or fillers.",
    ]
    if not has_pron:
        notes.append("Pronunciation is not measured: no PRON mistake, no pronunciation priority, and the P note "
                     "only says it was not measured.")
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
"but" or "so" are all fine, and so are restarts, repeated words and self-corrections ("they can, \
they can basically", "can make them, that can make them") - normal speech, never a mistake. Flag \
only what an IELTS examiner would penalise: grammar errors, wrong \
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
The FC note is about how the ideas flow and develop (length, development, linking, hesitation while \
searching for language) - never the number of fillers.
4. strength - text: 1-2 sentences (40 words at most) on what the student genuinely does well, \
quoting 1-2 of the phrases you marked "good", word for word, in double quotes; quoted_phrases: \
those phrases exactly as in the transcript. No generic praise.
5. priorities - exactly 3, the most important first, each about ONE specific pattern (never a \
whole category, never one trivial word): title (2-6 words), rule (one line, 15 words at most), \
example_wrong (2-8 words copied exactly from an answer) and example_right (improved), criteria \
(its tags). A priority about the measured voice - pace, or hesitation while searching for \
language (tag FLU), a measured pronunciation pattern (tag PRON) - has voice_ref=true and \
mistake_ids=[]. Never a priority about fillers (they are natural in fluent speech), and no \
pronunciation priority without pronunciation evidence. Any other priority has \
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
- Spoken English is not written English: contractions, short answers, informal phrases, natural \
fragments, restarts, repeated words and self-corrections are fine. Only clear errors an IELTS \
examiner would penalise. When in doubt, leave it out.
- question: the answer's id. wrong: copied exactly from that answer (without "(?)"), long enough \
to occur only once. correct: that part corrected, keeping the meaning; it must fit back into the \
sentence. why: one plain line, 25 words or fewer. criterion: GRA, LR or FLU. unclear=true with \
if_you_meant only when the meaning can't be determined.
- If nothing was missed, return an empty list."""


VERIFY_SYSTEM = """You are a senior IELTS Speaking examiner double-checking a colleague's list of \
mistakes in a student's recorded answers. Ask ONE question of every item: is this actually wrong in \
natural spoken English? Then give its kind (the academic lead's classes, 2026-10-29):
- error: wrong, or it makes the meaning unclear - "people doesn't", a wrong verb pattern, a \
collocation error, a wrong-meaning word, a vague or invented word a fluent speaker wouldn't use where \
a precise one is expected;
- slip: wrong, but a one-off small mistake the speaker clearly controls elsewhere - an article ("the \
Eastern countries"), a redundant word in a set phrase ("if not done so properly"), one plural or \
agreement slip;
- alternative: acceptable as said - another option is only more common, more idiomatic or more \
precise (a near-synonym, "inconceivable" where "unimaginable" would do). It is no mistake;
- not_a_mistake: correct as said; natural spoken English - contractions, short answers ("I can't say \
I am"), informal phrases ("kind of", "to be honest", "and stuff like that", "you know"), fragments, \
starting with "and"/"so", colloquial forms native speakers use widely ("much more free"); a restart, a \
repeated word or a SELF-CORRECTION ("there are so many, there's so much", "daily lifestyles, \
routines"); a style preference; a correction that changes what the student meant; anything about \
punctuation, capitals or spelling (the recogniser's, not the student's) or a filler alone (a filler \
inside a wrong phrase, "lost like a part of", is a real error); or you are not sure - the student gets \
the benefit of the doubt.

VOCABULARY (when listed): the words the report teaches, each with its meaning and an example. For \
each give its index; meaning = the standard dictionary meaning in plain words only when the given one \
is wrong or backwards, else null; example = a rewritten example only when it misuses the term, else null.

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


async def score_session(part: str, items: list[SessionItem], ev: Evidence, evidence: str = "",
                        acceptable: list[str] = ()) -> tuple[dict[str, float], dict[str, str]]:
    """(the median of SCORE_RUNS scoring runs, whole bands {"FC", "LR",
    "GRA", "P"}; each band's note, from a run that gave it). `evidence`: the
    checked mistakes (mistake_evidence); `acceptable`: what was found
    acceptable as said - quoting it is no reason for a band. An 8 whose
    weaknesses quote nothing real is a 9."""
    from services.ai.writing_grader import evidenced  # the same rule as Writing's

    spoken = " ".join(item.audio.text for item in items)
    messages = [
        {"role": "system", "content": _scoring_system(part, ev.pron is not None)},
        {"role": "user", "content": f"{answers_text(items, mark_uncertain=True, mark_pauses=True)}\n\n"
                                    f"{evidence_text(items, ev, for_scoring=True)}"
                                    + (f"\n\n{evidence}" if evidence else "")},
    ]

    async def once() -> dict:
        response = await parse_call("score", SCORE_MAX_TOKENS, messages, SpeakingScoring)
        parsed = response.choices[0].message.parsed
        if parsed is None:
            raise RuntimeError("Scoring returned nothing")
        out: dict = {"P": float(parsed.P), "_notes": {}}
        for k in ("FC", "LR", "GRA"):
            j = getattr(parsed, k)
            out[k] = whole_band(j.band)
            if out[k] == 8 and not evidenced(j.weaknesses, spoken, acceptable):  # only an 8, as in Writing
                logger.info("Speaking %s %g -> 9: no quoted weakness (%s)", k, out[k], j.weaknesses)
                out[k] = 9.0
            out["_notes"][k] = j.note.strip()
        return out

    bands, runs = await median_of_runs(once, SCORE_KEYS, label=f"speaking {part}")
    if ev.pron is None:  # not measured: never a band lowered without evidence
        bands["P"] = whole_band((bands["FC"] + bands["LR"] + bands["GRA"]) / 3)
    notes = {k: next((r["_notes"][k] for r in runs if whole_band(r[k]) == bands[k] and r["_notes"].get(k)), "")
             for k in ("FC", "LR", "GRA")}
    return bands, notes


def mistake_evidence(items: list[SessionItem], checked: list, alternatives: list[dict], corrected: list[str],
                     strong: list[str]) -> str:
    """The checked errors and slips per criterion with their density per 100
    spoken words (a slip counting slip_weight, as in Writing), what was
    acceptable as said, the self-corrections taken out, the strong phrases."""
    words = max(sum(item.audio.word_count for item in items), 1)
    weight = marking_config.density()["slip_weight"]
    lines = [f"CHECKED MISTAKES (errors and slips only; the complete list). The answers have {words} words; "
             f"a slip counts {weight:g}."]
    for key, name in (("GRA", "Grammar (GRA)"), ("LR", "Vocabulary (LR)"), ("FLU", "Fluency (FC)")):
        mine = [m for m in checked if m.criterion == key]
        if not mine:
            lines.append(f"- {name}: none.")
            continue
        slips = [m for m in mine if m.severity == "slip"]
        errors = [m for m in mine if m.severity != "slip"]
        density = (len(errors) + len(slips) * weight) * 100 / words
        listed = "; ".join([f"«{m.wrong}» -> «{m.correct}»" for m in errors[:10]]
                           + [f"«{m.wrong}» -> «{m.correct}» (slip)" for m in slips[:6]])
        lines.append(f"- {name}: {len(errors)} errors, {len(slips)} slips = {density:.1f} weighted per 100 words: "
                     f"{listed}")
    if alternatives:
        lines.append("ACCEPTABLE AS SAID (another option is only more common - NOT mistakes, never count or quote "
                     "them): " + "; ".join(f"«{a['wrong']}»" for a in alternatives[:10]))
    if corrected:
        lines.append("SELF-CORRECTIONS the speaker made (natural speech, never a mistake): "
                     + "; ".join(f"«{c}»" for c in corrected[:8]))
    if strong:
        lines.append("STRONG PHRASES the examiner marked (credit them): " + "; ".join(f"«{x}»" for x in strong[:6]))
    return "\n".join(lines)


def speaking_floors(items: list[SessionItem], bands: dict[str, float], checked: list,
                    strong: list[str]) -> tuple[dict[str, float], list[str]]:
    """Writing's band floors for Lexical Resource and Grammar (config/
    writing_marking.json "floors", the academic lead 2026-10-29), for answers
    of 150 words or more: a wide range and only slips - at least 8; Lexical
    Resource with a wide range, nothing unclear and errors under the Band 7
    density - at least 7. Never Fluency or Pronunciation."""
    f = marking_config.floors()
    words = sum(item.audio.word_count for item in items)
    if words < 150 or len(strong) < f["wide_range_phrases"]:
        return bands, []
    out, raised = dict(bands), []
    weight = marking_config.density()["slip_weight"]
    for key in ("LR", "GRA"):
        mine = [m for m in checked if m.criterion == key]
        errors = [m for m in mine if m.severity != "slip"]
        least = f["slips_only_min"] if not errors else 0
        if key == "LR" and not any(m.unclear for m in mine):
            if (len(errors) + (len(mine) - len(errors)) * weight) * 100 / words <= marking_config.density()["band7_max"]:
                least = max(least, f["clear_vocabulary_lr_min"])
        if out[key] < least:
            raised.append(f"{key} {out[key]:g} -> {least:g}")
            out[key] = float(least)
    return out, raised


async def score_answers_text(part: str, qa: list[tuple[str, str]]) -> float:
    """The overall band our examiner gives written-out answers (model answers'
    self-check): one run, transcript only, pronunciation left out."""
    from services.ai.scoring import overall_band, whole_band

    text = "\n\n".join(f"Q{i}: {q}\nANSWER Q{i}:\n{a}" for i, (q, a) in enumerate(qa, 1))
    system = (
        _scoring_system(part, has_pron=False)
        + "\n\nThese answers are written out, not recorded: judge them as if spoken fluently, and give "
        "Pronunciation the same band as Lexical Resource."
    )
    response = await parse_call(
        "self_check", SCORE_MAX_TOKENS, [{"role": "system", "content": system}, {"role": "user", "content": text}],
        SpeakingScoring,
    )
    s = response.choices[0].message.parsed
    if s is None:
        raise RuntimeError("Self-check scoring returned nothing")
    return overall_band([whole_band(s.FC.band), whole_band(s.LR.band), whole_band(s.GRA.band), whole_band(s.LR.band)])


def _uncertain_text(items: list[SessionItem]) -> str:
    lines = []
    for item in items:
        words = [t.text for t in item.audio.tokens if t.conf < CONFIDENCE_LIMIT]
        if words:
            lines.append(f"{item.qid}: " + ", ".join(f"\"{w}\"" for w in words))
    return "UNCERTAIN WORDS (may be misheard - never base a mistake on them):\n" + ("\n".join(lines) or "none")


async def feedback_call(part: str, items: list[SessionItem], ev: Evidence) -> SpeakingFeedback | None:
    """None when the answer ran past FEEDBACK_MAX_TOKENS."""
    text = f"{answers_text(items, mark_uncertain=False)}\n\n{_uncertain_text(items)}\n\n{evidence_text(items, ev)}"
    messages = [
        {"role": "system", "content": _feedback_system(part, ev.pron is not None)},
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


async def verify_call(source_text: str, items: list[tuple[int, str, str, str, str]],
                      vocabulary: list[tuple[int, str, str, str]] | None = None) -> tuple[dict, dict]:
    """`items`: (id, where, wrong, correct, why); `vocabulary`: (index, term,
    meaning, example). ({id: (kind, reason)} - items without a verdict are
    kept -, {index: (meaning or None, example or None)})."""
    if not items and not vocabulary:
        return {}, {}
    listed = "\n".join(f"{i}. [{where}] «{wrong}» -> «{correct}» ({why})"
                       for i, where, wrong, correct, why in items) or "(none)"
    text = f"{source_text}\n\nMISTAKES TO CHECK:\n{listed}"
    if vocabulary:
        text += "\n\nVOCABULARY TO CHECK:\n" + "\n".join(f"{i}. {term} - {meaning} - e.g. {example}"
                                                         for i, term, meaning, example in vocabulary)
    response = await parse_call(
        "verify", VERIFY_MAX_TOKENS,
        [{"role": "system", "content": VERIFY_SYSTEM}, {"role": "user", "content": text}],
        SpeakingVerdicts,
    )
    parsed = response.choices[0].message.parsed
    if not parsed:
        return {}, {}
    return ({v.id: (v.kind, v.reason) for v in parsed.verdicts},
            {v.index: ((v.meaning or "").strip() or None, (v.example or "").strip() or None)
             for v in parsed.vocabulary})


# --- The whole grading ------------------------------------------------------------------------------------


async def grade_speaking(part: str, items: list[SessionItem], ev: Evidence, practise: list[dict] | None = None,
                         on_feedback=None):
    """Feedback, missed-errors pass, self-corrections out, the filter, then the
    bands -> SpeakingReport. `on_feedback(scores, overall, feedback)` is
    called as soon as the bands are decided, so the candidate-specific model
    answers run alongside the report's assembly."""
    # Imported here: speaking_marking imports SessionItem from this module.
    from services.ai.scoring import overall_band
    from services.ai.speaking_marking import assess, build_report, self_correction

    feedback = await feedback_call(part, items, ev)
    problems = assess(feedback, items) if feedback else ["the answer ran past its token limit"]
    if problems:
        logger.warning("Speaking feedback needs another try: %s", "; ".join(problems))
        retry = await feedback_call(part, items, ev)
        if retry is not None:
            retry_problems = assess(retry, items)
            if feedback is None or len(retry_problems) < len(problems):
                feedback, problems = retry, retry_problems
        if feedback is None:
            raise RuntimeError("Speaking feedback ran past its token limit twice")
        if problems:
            logger.warning("Speaking feedback still has problems after the retry: %s", "; ".join(problems))

    source = answers_text(items, mark_uncertain=True)
    texts = {item.qid: item.audio.text for item in items}

    async def verify(checks, vocabulary=None) -> tuple[dict, dict]:
        try:
            return await verify_call(source, checks, vocabulary)
        except Exception:
            logger.exception("Speaking verification pass failed - keeping those mistakes")
            return {}, {}

    async def find_missed() -> list:
        try:
            return await missed_call(items, feedback.mistakes)
        except Exception:
            logger.exception("Speaking missed-errors pass failed - first pass only")
            return []

    # Only language corrections are checked: Fluency items (an unclear or
    # undeveloped answer), Pronunciation items and unclear meaning are
    # judgements about the answer, not corrections that could be "already right".
    def checkable(m) -> bool:
        return m.criterion in ("GRA", "LR") and not m.unclear

    def corrected(m) -> bool:
        """A self-correction, by code: the speaker said it right straight away (#32)."""
        return checkable(m) and self_correction(texts.get(m.question.strip().upper(), ""), m.wrong, m.correct)

    words = [(i, v.term, v.meaning, v.example) for i, v in enumerate(feedback.vocabulary)]
    (verdicts, fixes), missed = await asyncio.gather(
        verify([(m.id, m.question, m.wrong, m.correct, m.why) for m in feedback.mistakes
                if checkable(m) and not corrected(m)], words),
        find_missed(),
    )
    missed_verdicts, _ = await verify(
        [(k, x.question, x.wrong, x.correct, x.why) for k, x in enumerate(missed) if checkable(x) and not corrected(x)])
    if fixes:
        feedback = feedback.model_copy(update={"vocabulary": [
            v.model_copy(update={"meaning": fixes.get(i, (None, None))[0] or v.meaning,
                                 "example": fixes.get(i, (None, None))[1] or v.example})
            for i, v in enumerate(feedback.vocabulary)]})

    self_corrected, alternatives, checked = [], [], []
    dropped, dropped_missed = set(), set()
    for found, verdict_of, kept_ids, label in ((list(enumerate(feedback.mistakes)), verdicts, dropped, "mistake"),
                                               (list(enumerate(missed)), missed_verdicts, dropped_missed, "missed")):
        for k, m in found:
            key = m.id if label == "mistake" else k
            if corrected(m):
                kept_ids.add(key)
                self_corrected.append(m.wrong)
                logger.info("Speaking %s %s set aside: a self-correction (%r)", label, key, m.wrong)
                continue
            kind, reason = verdict_of.get(key, ("error", ""))
            if kind in ("error", "slip") and (ruled := lead_accepted(m.wrong)):
                kind, reason = "not_a_mistake", f"the academic lead accepted {ruled!r}"
            if kind not in ("error", "slip"):
                kept_ids.add(key)
                logger.info("Speaking %s %s set aside (%s): %s", label, key, kind, reason)
                if kind == "alternative":
                    alternatives.append({"q": m.question, "wrong": m.wrong, "correct": m.correct, "why": reason,
                                         "criterion": m.criterion})
                continue
            checked.append(SimpleNamespace(wrong=m.wrong, correct=m.correct, criterion=m.criterion, severity=kind,
                                           unclear=m.unclear))

    strong = [seg.text.strip() for answer in feedback.answers for seg in answer.segments if seg.type == "good"]
    scores, notes = await score_session(part, items, ev, mistake_evidence(items, checked, alternatives,
                                                                          self_corrected, strong),
                                        [a["wrong"] for a in alternatives] + self_corrected)
    scores, raised = speaking_floors(items, scores, checked, strong)
    if raised:
        logger.info("Band floors (speaking %s): %s", part, "; ".join(raised))
        if ev.pron is None:
            scores["P"] = whole_band((scores["FC"] + scores["LR"] + scores["GRA"]) / 3)
    overall = overall_band(list(scores.values()))
    if on_feedback is not None:
        try:
            on_feedback(scores, overall, feedback)
        except Exception:
            logger.exception("on_feedback failed")

    report = build_report(part, items, feedback, missed, dropped, dropped_missed, scores, ev.metrics, ev.pron,
                          practise, ev.pitch, criterion_notes=notes, upgrades=alternatives)
    if report.notes:
        logger.info("Speaking report validation: %s", "; ".join(report.notes))
    return report
