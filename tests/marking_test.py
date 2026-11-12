"""Judging, not counting (the academic lead, 2026-10-29) - offline, no OpenAI:

    python tests/marking_test.py

Writing: what the filter found acceptable is never a reason for a band; the
Lexical Resource / Grammar floors (never Task or Coherence, never a short
answer); the model answer's code checks (an unfinished sentence, "In my
view" where no opinion is asked, stock and repeated openers, mixed
spelling); the report's "Upgrade ideas" and its top band.
Speaking: self-corrections found by code; the whole grading with a fake
OpenAI - the 9.0 speaker's self-corrections and native-like forms are no
mistakes, an alternative becomes an upgrade idea, a priority built on them
goes, Grammar is 9 and the report is at the top band.
"""
import asyncio
import re
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.ai import essay_marking, marking_config, sample_bank, speaking_grader, writing_grader  # noqa: E402
from services.ai import schemas  # noqa: E402
from services.ai.speaking_marking import self_correction  # noqa: E402

fails: list[str] = []


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


def writing() -> None:
    essay = " ".join(["Many shops are available throughout the day and night in big cities."] * 30)
    check("a weakness quoting only what was acceptable as written is no reason (-> 9)",
          not writing_grader.evidenced(['"shops are available" is less natural'], essay, ["shops are available"]))
    check("...a quoted real error still is", writing_grader.evidenced(['"in big cities" lacks an article'], essay, []))

    slip = NS(criterion="GRA", severity="slip", unclear=False, wrong="a few", correct="few")
    error = NS(criterion="LR", severity="error", unclear=False, wrong="make the case worse", correct="worsen it")
    strong = ["scorching hot", "the emergence of logistics companies", "more tolerable"]
    bands = {"TR_or_TA": 7.0, "CC": 7.0, "LR": 5.0, "GRA": 7.0}
    essay += " " + ". ".join(strong) + "."
    errors = [NS(criterion="LR", severity="error", unclear=False, wrong=f"word {i}", correct=f"term {i}")
              for i in range(3)]  # 3 in ~370 words: past the Band 8 density, within the Lexical layer's 1 per 100
    out, raised = writing_grader.floors("task2", essay, bands, [slip] + errors, strong)
    check("floors: clear, less common vocabulary - Lexical Resource at least 7 (#65's LR 5)", out["LR"] == 7, raised)
    check("...only slips in Grammar and a wide range - Grammar at least 8 (#67)", out["GRA"] == 8, raised)
    one, raised = writing_grader.floors("task2", essay, bands, [slip, error], strong)
    check("...one error in 360 words: the Lexical layer, 7 (the Band 8 density floor is Grammar's alone)", one["LR"] == 7,
          raised)
    check("...never Task Response or Coherence", out["TR_or_TA"] == 7 and out["CC"] == 7)
    short, _ = writing_grader.floors("task2", "Too short an essay. " * 20, bands, [slip], strong)
    check("...never an answer under 250 words", short == bands, short)
    narrow, _ = writing_grader.floors("task2", essay, bands, [slip], strong[:1])
    check("...never without a wide range", narrow == bands, narrow)
    check("slip weight 0.25 (config)", marking_config.density()["slip_weight"] == 0.25)

    evidence = writing_grader.mistake_evidence(essay, [slip, error], strong,
                                               [{"wrong": "sleep regimes", "correct": "sleep patterns"}])
    check("the scoring runs see what was acceptable, apart from the mistakes",
          "ACCEPTABLE AS WRITTEN" in evidence and "sleep regimes" in evidence, evidence)

    q_causes = "What are the causes of this problem and what measures could be taken to solve it?"
    q_opinion = "Do you agree or disagree?"
    body = ["Cities are growing fast, and housing costs are rising.", "In my view, the main cause is migration.",
            "In conclusion."]
    problems = sample_bank.answer_problems(body, q_causes, "task2")
    check("model answer: 'In conclusion.' alone is unfinished", any("finish every sentence" in p for p in problems),
          problems)
    check("...'In my view' where the question asks no opinion", any("asks for no opinion" in p for p in problems),
          problems)
    check("...but fine when it asks one", not any("asks for no opinion" in p for p in
                                                  sample_bank.answer_problems(body[:2], q_opinion, "task2")))
    check("...stock openers", any("stock phrases" in p for p in sample_bank.answer_problems(
        ["First and foremost, prices matter."], q_opinion, "task2")))
    check("...this student's recent openers", any("open these paragraphs differently" in p for p in
                                                  sample_bank.answer_problems(["Cities are growing fast now."],
                                                                              q_opinion, "task2",
                                                                              ["cities are growing fast"])))
    check("...mixed spelling (traveling / travellers)",
          sample_bank.mixed_spelling("Traveling broadens the mind of travellers.") == ["travellers", "Traveling"],
          sample_bank.mixed_spelling("Traveling broadens the mind of travellers."))
    check("...one spelling is fine", not sample_bank.mixed_spelling("Travelling broadens the minds of travellers; "
                                                                     "colours and organisation."))

    fb = schemas.WritingFeedback(
        mistakes=[schemas.Mistake(id=1, wrong="it should be noticed", correct="it should be noted",
                                  why="Set phrase.", criterion="LR", unclear=False, if_you_meant=None)],
        essay=[[schemas.EssaySegment(type="text", text="Some say it should be noticed that shops are available.",
                                     mistake_id=None, fix=None)]],
        criterion_notes=schemas.CriterionNotes(TR_or_TA="x", CC="x", LR="x", GRA="x"),
        strength=schemas.Strength(text="Good.", quoted_phrases=[]), priorities=[], vocabulary=[], checklist=[])
    report = essay_marking.build_report(
        "task2", "q", "Some say it should be noticed that shops are available.", fb,
        bands={"TR_or_TA": 9, "CC": 9, "LR": 9, "GRA": 9},
        upgrades=[{"wrong": "shops are available", "correct": "shops are open", "why": "Both fine.", "criterion": "LR"}])
    check("report: an alternative is an upgrade idea, never a mistake",
          [u["wrong"] for u in report.upgrades] == ["shops are available"]
          and all(m["wrong"] != "shops are available" for m in report.mistakes), (report.upgrades, report.mistakes))
    check("...at 9.0 the report is at the top band (no 'next target')", report.top_band)


def _audio(text: str):
    from services.ai.voice import AnswerAudio, Token

    tokens, t = [], 0.0
    for m in re.finditer(r"[A-Za-z']+", text):
        tokens.append(Token(text=m.group(0), start_char=m.start(), end_char=m.end(), start=t, end=t + 0.35))
        t += 0.4
    return AnswerAudio(text=text, tokens=tokens, duration=t + 0.5)


def speaking() -> None:
    check("self-correction: 'there are so many, there's so much to explore'", self_correction(
        "Well, there are so many, there's so much to explore.", "there are so many, there's so much to explore",
        "there's so much to explore"))
    check("...'daily lifestyles, routines'", self_correction("It breaks our daily lifestyles, routines, you know.",
                                                             "daily lifestyles", "daily routines"))
    check("...a real error is none ('people doesn't')", not self_correction("People doesn't like it.",
                                                                           "People doesn't", "People don't"))

    from services.ai.speaking_grader import Evidence, SessionItem
    from services.ai.voice import VoiceMetrics

    a1 = ("Well, travelling is arguably the best way to break away from routines. There are so many, there's so "
          "much to explore, and first-hand experience is just unimaginable. It's much more free than staying home. "
          "People from the Eastern countries travel a lot for this reason, I'd say, and stuff like that.")
    a2 = ("Older people may prefer comfortable trips, while younger travellers broaden their horizons on a budget, "
          "if not done so properly it can be tiring, but both groups benefit immensely from travelling.")
    items = [SessionItem(qid="Q1", part="part3", question="Why do people enjoy travelling?", audio=_audio(a1)),
             SessionItem(qid="Q2", part="part3", question="Do old and young people travel differently?",
                         audio=_audio(a2))]
    metrics = VoiceMetrics(speaking_seconds=60, words=90, wpm=130, fillers=2, filler_types=[("you know", 2)],
                           long_pauses=0, mid_sentence_pauses=0, longest_pause=None, longest_pause_answer=None)

    def mistake(i, q, wrong, correct, crit="GRA"):
        return schemas.SpeakingMistake(id=i, question=q, wrong=wrong, correct=correct, why="x", criterion=crit,
                                       unclear=False, if_you_meant=None)

    feedback = schemas.SpeakingFeedback(
        answers=[schemas.AnswerMarking(id="Q1", segments=[schemas.AnswerSegment(type="text", text=a1, fix=None,
                                                                                mistake_id=None)]),
                 schemas.AnswerMarking(id="Q2", segments=[schemas.AnswerSegment(type="text", text=a2, fix=None,
                                                                                mistake_id=None)])],
        mistakes=[mistake(1, "Q1", "There are so many, there's so much to explore", "There's so much to explore"),
                  mistake(2, "Q1", "unimaginable", "unparalleled", "LR"),
                  mistake(3, "Q1", "much more free", "much freer"),
                  mistake(4, "Q1", "the Eastern countries", "Eastern countries"),
                  mistake(5, "Q2", "if not done so properly", "if not done properly")],
        criterion_notes=schemas.SpeakingCriterionNotes(FC="x", LR="x", GRA="x", P="x"),
        strength=schemas.Strength(text="Natural, precise.", quoted_phrases=[]),
        priorities=[schemas.SpeakingPriority(title="Choose precise adjectives", rule="x",
                                             example_wrong="just unimaginable", example_right="unparalleled",
                                             criteria=["LR"], mistake_ids=[2], voice_ref=False),
                    schemas.SpeakingPriority(title="Use correct comparatives", rule="x", example_wrong="much more free",
                                             example_right="much freer", criteria=["GRA"], mistake_ids=[3],
                                             voice_ref=False),
                    schemas.SpeakingPriority(title="Match there is/are", rule="x",
                                             example_wrong="There are so many, there's so much",
                                             example_right="There is so much", criteria=["GRA"], mistake_ids=[1],
                                             voice_ref=False)],
        vocabulary_topic="travel", vocabulary=[schemas.VocabEntry(term=f"word {i}", meaning="m", example="e")
                                               for i in range(10)],
        checklist=[schemas.ChecklistItem(text="habit", ref="Priority 01") for _ in range(5)], cue_coverage=[])
    kinds = {"unimaginable": "alternative", "much more free": "not_a_mistake", "the Eastern countries": "slip",
             "if not done so properly": "slip"}
    seen: dict = {}

    async def fake_call(purpose, max_tokens, messages, response_format, model=None):
        name = response_format.__name__
        text = messages[-1]["content"] if isinstance(messages[-1]["content"], str) else ""
        if name == "SpeakingFeedback":
            parsed = feedback
        elif name == "SpeakingMissedMistakes":
            parsed = schemas.SpeakingMissedMistakes(mistakes=[])
        elif name == "SpeakingVerdicts":
            listed = re.findall(r"^(\d+)\. \[\w+\] «(.*?)»", text, flags=re.M)
            seen["verified"] = [w for _, w in listed]
            parsed = schemas.SpeakingVerdicts(verdicts=[schemas.SpeakingVerdict(id=int(i), kind=kinds.get(w, "error"),
                                                                                reason="r") for i, w in listed])
        elif name == "SpeakingScoring":
            seen["scoring"] = text
            judged = schemas.SpeakingJudgement(strengths=['"broaden their horizons"'], weaknesses=[], band=9, note="n")
            gra = schemas.SpeakingJudgement(strengths=['"Older people may prefer comfortable trips, while younger '
                                                       'travellers broaden"', '"both groups benefit immensely"'],
                                            band=8, note="Minor slips.",
                                            weaknesses=['"There are so many, there\'s so much" - agreement'])
            parsed = schemas.SpeakingScoring(FC=judged, LR=judged, GRA=gra, P=9)
        else:
            raise AssertionError(f"unexpected call {name}")
        return NS(choices=[NS(message=NS(parsed=parsed))])

    real = speaking_grader.parse_call
    speaking_grader.parse_call = fake_call
    try:
        report = asyncio.run(speaking_grader.grade_speaking("part3", items, Evidence(metrics, None, None)))
    finally:
        speaking_grader.parse_call = real
    bands = {c["key"]: c["band"] for c in report.criteria}
    wrongs = [m["wrong"] for m in report.mistakes]
    check("the self-correction never reaches the filter or the report",
          not any("so many" in w for w in seen.get("verified", [])) and not any("so many" in w for w in wrongs),
          (seen.get("verified"), wrongs))
    check("...the scoring runs are told it is a self-correction", "SELF-CORRECTIONS" in seen.get("scoring", ""))
    check("'much more free' (natural spoken form) is no mistake", "much more free" not in wrongs, wrongs)
    check("'unimaginable' is an upgrade idea, not a mistake",
          [u["wrong"] for u in report.upgrades] == ["unimaginable"] and "unimaginable" not in wrongs)
    check("the two slips stay, as slips", wrongs == ["the Eastern countries", "if not done so properly"], wrongs)
    check("Grammar 8 whose only reason quotes a self-correction is 9", bands["GRA"] == 9, bands)
    check("overall 9.0 - the top band, no next target", report.overall_band == 9.0 and report.top_band,
          report.overall_band)
    check("priorities built on what the check set aside are gone (#32's 02 and 03)",
          not any(p["title"] in ("Use correct comparatives", "Match there is/are", "Choose precise adjectives")
                  for p in report.priorities), [p["title"] for p in report.priorities])
    check("the intonation measurement never reaches the scoring", "Intonation" not in seen.get("scoring", ""))

    try:  # the PDF, where WeasyPrint is installed (the bot image)
        from services.pdf import report_builder
        from services.ai.speaking_marking import build_progress
    except Exception:
        print("(no WeasyPrint here - the speaking PDF is rendered in the bot image)")
        return
    rendered = []
    real_html = report_builder.HTML

    def keep(string, base_url=None):
        rendered.append(string)
        return real_html(string=string, base_url=base_url)
    report_builder.HTML = keep
    try:
        progress = build_progress("part3", report.overall_band, metrics, None, report.priorities, None)
        answers = [{"label": "Q1", "qids": ["Q1"], "part": "part3", "question": "Why?", "paragraphs": ["One answer."],
                    "words": 2, "seconds": 1, "band": 9.0, "source": "ai_generated", "shows": [], "alternative": True}]
        report_builder.build_speaking_report_pdf(report, answers, progress, "Test", False)
    finally:
        report_builder.HTML = real_html
    html = rendered[0] if rendered else ""
    check("speaking PDF: upgrade ideas, the top band, no next target, one answer said another way",
          "Upgrade ideas" in html and "The top band" in html and "Next target" not in html
          and "already at this level" in html, len(html))


def round2() -> None:
    """The academic lead's checks of the deployed round (2026-10-29)."""
    from services.ai.essay_marking import clean_pasted, fits

    text, removed = clean_pasted("In 1990 output fell. Over the Ilhom Alijanov | IELTS Task 1: 9.0 | Page 3 of 154 "
                                 "next decade, it rose.")
    check("a running footer inside a sentence goes (#106) - and the name with it",
          removed and text == "In 1990 output fell. Over the next decade, it rose.", text)
    check("checklist line that fits its priority stays", fits("Double-check ratios like one-third vs one-half.",
                                                             "Be precise in group comparisons adjust ratios"))
    check("...a filler line ('Keep units consistent') under another priority goes",
          not fits("Keep units consistent.", "Be precise in group comparisons adjust ratios for each exception"))
    check("model answer: 'Moreover' opening two paragraphs in a row goes back",
          any("two paragraphs in a row" in p for p in sample_bank.answer_problems(
              ["Sport unites nations.", "Moreover, it brings money.", "Moreover, it builds pride."],
              "Discuss both views and give your opinion.", "task2")))
    check("...and 'There is an opinion that' is a stock opener",
          any("stock phrases" in p for p in sample_bank.answer_problems(
              ["There is an opinion that sport matters."], "Discuss both views and give your opinion.", "task2")))

    # the scorer's checklist (#71: Coherence 9 though a linker followed nothing)
    essay = ("Sport brings nations together. Taking this into account, tourism rises. " * 40).strip()

    def judged(band, found=(), weak=(), looked=True):
        items = writing_grader.CHECKLISTS["CC"]
        checklist = [schemas.CheckFinding(item=i, found=bool(found) and k == 0, quote=found[0] if found and k == 0
                                          else "") for k, i in enumerate(items)] if looked else []
        return schemas.CriterionJudgement(checklist=checklist, strengths=[], weaknesses=list(weak), band=band,
                                          note="Linking could be sharper." if band == 9 else "n")

    def run(cc):
        task = [schemas.CheckFinding(item=i, found=False, quote="") for i in writing_grader.CHECKLISTS["TR_or_TA"]["task2"]]
        lr = [schemas.CheckFinding(item=i, found=False, quote="") for i in writing_grader.CHECKLISTS["LR"]]
        gra = [schemas.CheckFinding(item=i, found=False, quote="") for i in writing_grader.CHECKLISTS["GRA"]]
        nine = lambda c: schemas.CriterionJudgement(checklist=c, strengths=[], weaknesses=[], band=9, note="Fine.")  # noqa: E731
        scoring = schemas.WritingScoring(TR_or_TA=nine(task), CC=cc, LR=nine(lr), GRA=nine(gra))

        async def fake(purpose, max_tokens, messages, response_format, effort=None):
            return NS(choices=[NS(message=NS(parsed=scoring))])
        real = writing_grader._parse
        writing_grader._parse = fake
        try:
            return asyncio.run(writing_grader._score_once("task2", "Q?", essay, None))
        finally:
            writing_grader._parse = real

    check("a 9 whose checklist found a problem (a quoted illogical linker) is an 8",
          run(judged(9, found=("Taking this into account",)))["CC"] == 8)
    check("Coherence 8 is never lifted to 9 (the teachers, 2026-11-09: a 128-word report of simple sentences got 9)",
          run(judged(8))["CC"] == 8)
    check("...but an 8 whose checklist was never run stays 8 ('didn't look' is no 9)",
          run(judged(8, looked=False))["CC"] == 8)
    check("...a clear 8 is marked for coherence_nine (#128), one never looked at is not",
          run(judged(8))["_cc_clear"] and not run(judged(8, looked=False))["_cc_clear"])
    check("...nor one with a quoted weakness",
          not run(judged(8, weak=['"Taking this into account" follows nothing']))["_cc_clear"])
    check("a 6 whose reason came back unquoted is never lifted to 9 (#60, #62: Coherence 6 -> 9)",
          run(judged(6, weak=["Linking is basic and repetitive"]))["CC"] == 6)
    check("a quote shortened with '...' still counts as a quote",
          writing_grader.evidenced(['Linking repeats time markers: "Sport brings... tourism rises"'], essay))

    slip = lambda w, c: NS(wrong=w, correct=c, criterion="GRA", severity="slip")  # noqa: E731
    two = writing_grader.distinct_slips([slip("Czech Republic", "the Czech Republic"),
                                         slip("Italy, Czech Republic and Germany", "Italy, the Czech Republic and Germany"),
                                         slip("and other $25 000", "and others $25 000")])
    check("the same slip made twice counts once (#105: 'the Czech Republic' twice + 'others' = 2 slips)",
          [n for _, n in two] == [2, 1], two)

    err = lambda w, c, crit="GRA": NS(wrong=w, correct=c, criterion=crit, severity="error")  # noqa: E731
    items = [err("such society", "such a society"), err("the previous one", "the previous ones"),
             err("they want it", "they want them"), err("strategies This", "strategies. This"),
             err("people doesn't", "people don't"), err("this turn of events are", "this turn of events is"),
             err("disposed into the oceans", "disposed of in the oceans", "LR"),
             err("an useful tool", "a useful tool"), err("more cheaper", "cheaper")]
    writing_grader.small_slips(items)
    check("an article, a pronoun's number or a full stop is a slip by its correction (#66); a verb's agreement, "
          "a wrong particle, 'an useful' and 'more cheaper' stay errors (T9)", [m.severity for m in items]
          == ["slip", "slip", "slip", "slip", "error", "error", "error", "error", "error"],
          [m.severity for m in items])
    feel = err("contributing to feeling of worthlessness", "contributing to feelings of worthlessness")
    writing_grader.small_slips([feel])
    check("'feeling of' -> 'feelings of' is a noun's number: a slip (#67)", feel.severity == "slip", feel.severity)

    # #67: one agreement error and two slips in 374 words, a wide range - Grammar 7 is lifted to 8
    essay67 = " ".join(["word"] * 374) + " a b c"
    gra = [NS(wrong="this turn of events are", correct="this turn of events is", criterion="GRA", severity="error"),
           NS(wrong="feeling of loneliness", correct="a feeling of loneliness", criterion="GRA", severity="slip"),
           NS(wrong="feeling of worthlessness", correct="feelings of worthlessness", criterion="GRA", severity="slip")]
    bands, raised = writing_grader.floors("task2", essay67, {"TR_or_TA": 9, "CC": 9, "LR": 8, "GRA": 7}, gra,
                                          ["a", "b", "c"])
    check("within the Band 8 density with a wide range: Grammar at least 8 (#67)", bands["GRA"] == 8, raised)
    many = gra + [NS(wrong=f"err {i}", correct=f"fix {i}", criterion="GRA", severity="error") for i in range(4)]
    bands, raised = writing_grader.floors("task2", essay67, {"TR_or_TA": 7, "CC": 7, "LR": 7, "GRA": 7}, many,
                                          ["a", "b", "c"])
    check("...but not past it (5 errors in 374 words stay 7)", bands["GRA"] == 7, raised)
    grammar = lambda n: [NS(wrong=f"err {i}", correct=f"fix {i}", criterion="GRA", severity="error") for i in range(n)]
    text300 = " ".join(["word"] * 300)
    votes = {"TR_or_TA": 7.0, "CC": 7.0, "LR": 6.0, "GRA": 7.0}
    capped, why = writing_grader.grammar_ceiling(text300, votes, grammar(9))
    check("Grammar past its limit (9 errors in 300 words) stays 6 (a real Band 6 script)", capped["GRA"] == 6, why)
    check("...only Grammar", {k: v for k, v in capped.items() if k != "GRA"} == {"TR_or_TA": 7.0, "CC": 7.0, "LR": 6.0})
    same, why = writing_grader.grammar_ceiling(text300, votes, grammar(8))
    check("...8 errors in 300 words, under the limit, keep the 7 (a real Band 7 script is at 2.70)", same == votes and not why, why)
    para = "word " * 150 + "\n\n" + "word " * 150
    capped, _, why, _ = writing_grader.cap_bands("task2", para, {"TR_or_TA": 7.0, "CC": 9.0, "LR": 7.0, "GRA": 7.0}, {})
    check("Coherence 9 with Lexical and Grammar at 7 stays 8 (#71)", capped["CC"] == 8, why)
    capped, _, why, _ = writing_grader.cap_bands("task2", para, {"TR_or_TA": 9.0, "CC": 9.0, "LR": 7.0, "GRA": 9.0}, {})
    check("...one of them above 7 keeps the 9 (#65)", capped["CC"] == 9, why)
    slips = [NS(wrong=f"slip {i}", correct=f"fixed {i}", criterion="GRA", severity="slip") for i in range(12)]
    same, why = writing_grader.grammar_ceiling(text300, votes, slips)
    check("...slips alone never reach it: no band lost for small slips", same == votes and not why, why)
    same, why = writing_grader.grammar_ceiling(text300, votes, None)
    check("...without a checked list nothing changes", same == votes and not why, why)

    # #128: a Band 9 report (227 words, 3 paragraphs) came out Coherence 8 in both runs, no weakness quoted
    report128 = "\n\n".join(" ".join(["word"] * n) for n in (80, 70, 77))
    strong128 = {"TR_or_TA": 8.0, "CC": 8.0, "LR": 9.0, "GRA": 8.0}
    clear = [{"_cc_clear": True}, {"_cc_clear": True}]
    one_gra = [NS(wrong="the percentage lag", correct="the percentage lags", criterion="GRA", severity="error")]
    nine = lambda *a, **k: writing_grader.coherence_nine(*a, **k)[0]["CC"]  # noqa: E731
    check("a full, strong report with Coherence 8 and no weakness quoted is Coherence 9 (#128)",
          nine("task1", report128, strong128, clear, one_gra, True) == 9)
    check("...not with Grammar 7 (Task, Lexical and Grammar must all be 8+)",
          nine("task1", report128, {**strong128, "GRA": 7.0}, clear, one_gra, True) == 8)
    check("...not under the word minimum (#121: 126 words, Coherence 9 before 5e8092d)",
          nine("task1", "\n\n".join(" ".join(["word"] * 42) for _ in range(3)), strong128, clear, one_gra, True) == 8)
    check("...not in two paragraphs", nine("task1", "word " * 120 + "\n\n" + "word " * 107, strong128, clear,
                                           one_gra, True) == 8)
    check("...not with a Coherence error on the checked list",
          nine("task1", report128, strong128, clear, one_gra + [NS(wrong="Moreover", correct="However",
                                                                   criterion="CC", severity="error")], True) == 8)
    check("...not when most runs quoted a weakness",
          nine("task1", report128, strong128, [{"_cc_clear": True}, {"_cc_clear": False}], one_gra, True) == 8)
    check("...not without a wide range shown", nine("task1", report128, strong128, clear, one_gra, False) == 8)
    check("...a Task 2 essay needs 4 paragraphs",
          nine("task2", "\n\n".join(" ".join(["word"] * 90) for _ in range(3)), strong128, clear, one_gra, True) == 8
          and nine("task2", "\n\n".join(" ".join(["word"] * 70) for _ in range(4)), strong128, clear, one_gra,
                   True) == 9)
    check("...and a Coherence 7 is never lifted", nine("task1", report128, {**strong128, "CC": 7.0}, clear, one_gra,
                                                       True) == 7)

    # #128: notional agreement - "the overwhelming share of people ... prefer" is right as written
    from services.ai.mistake_rules import notional_agreement
    for w, c, right in (
            ("the overwhelming share of people from all countries prefer",
             "the overwhelming share of people from all countries prefers", True),
            ("the majority of students are", "the majority of students is", True),
            ("a large proportion of children have", "a large proportion of children has", True),
            ("the percentage of people from other nations, especially the Netherlands, lag",
             "the percentage of people from other nations, especially the Netherlands, lags", False),
            ("the number of cars are", "the number of cars is", False),
            ("a lot of people use car", "a lot of people use cars", False),
            ("the majority of the population prefer", "the majority of the population prefers", False),
            ("people doesn't", "people don't", False)):
        check(f"notional agreement {'accepted' if right else 'left to the filter'}: {w!r}",
              notional_agreement(w, c) == right)
    check("...and the filter's 'error' on it becomes not_a_mistake by code",
          writing_grader.final_kind("the overwhelming share of people from all countries prefer",
                                    "the overwhelming share of people from all countries prefers", "error",
                                    "GRA")[0] == "not_a_mistake")
    # the academic lead's 2026-10-30 reports #72-#74
    pct = err("at only 13% men and 9% women", "at only 13% for men and 9% for women")
    writing_grader.small_slips([pct])
    check("'13% men and 9% women' -> '13% for men...': a preposition left out after a figure is a slip (#74)",
          pct.severity == "slip", pct.severity)
    check("a Task correction that only adds to the student's words is an omission, no mistake (#73)",
          writing_grader.only_adds("marking the closure of the landfill.", "marking the closure of the landfill, "
                                   "while drainage at the base continues to the tank.")
          and not writing_grader.only_adds("bottled, labeled, and finally transported",
                                           "bottled and finally transported")
          and not writing_grader.only_adds("people", "people's"))
    text74 = "Males predominated, except for emailing and online shopping, where females had the upper hand. " \
             "Research lagged far behind at only 13% men and 9% women."
    check("Lexical: a quote of a phrase the filter never flagged is no reason (#74 'had the upper hand')",
          not writing_grader.language_reason("LR", ['Minor register lapse: "had the upper hand"'], text74, [], [], True))
    check("...a quoted range limitation is", writing_grader.language_reason(
        "LR", ['"predominated" is repeated'], text74, [], [], True))
    check("...and so is one with no quote (#121: 'Range of complex structures is limited overall' was lifted to 9)",
          writing_grader.language_reason("GRA", ["Range of complex structures is limited overall"], text74, [], [], True)
          and writing_grader.language_reason("GRA", ["Range could be wider; sentence patterns are mostly simple"],
                                             text74, [], [], True)
          and writing_grader.language_reason("LR", ["Everyday vocabulary throughout"], text74, [], [], True))
    from services.ai import mistake_rules
    plain = " ".join(["word"] * 200)
    check("few errors in simple language: the samples shown are picked around Band 7 at most, not 8",
          writing_grader.estimated_band(plain, [], wide=False) == 7.0 and writing_grader.estimated_band(plain, []) == 8.0)
    check("a verb or noun form is grammar, never a synonym swap (#123 'go' -> 'went', #124 'study' -> 'studies')",
          not mistake_rules.synonym_swap("the number go down", "the number went down")
          and not mistake_rules.synonym_swap("my friend study online", "my friend studies online")
          and not mistake_rules.synonym_swap("This number grow", "This number grew")
          and mistake_rules.synonym_swap("are provided with", "are equipped with"))
    check("...so the filter keeps it an error", mistake_rules.final_kind("the number go down", "the number went down",
                                                                       "error", "GRA")[0] == "error")
    agree = NS(wrong="people doesn’t use", correct="people don't use", criterion="GRA", severity="slip")
    writing_grader.small_slips([agree])
    check("a verb's agreement the filter called a slip is an error (T9: 'people doesn't')",
          agree.severity == "error", agree.severity)
    three = [err("such society", "such a society"), err("of throwaway society", "of a throwaway society"),
             err("in United States", "in the United States")]
    writing_grader.small_slips(three)
    check("...but a third article slip means articles aren't controlled: all stay errors",
          all(m.severity == "error" for m in three), [m.severity for m in three])
    check("a wording the academic lead accepted is found inside a longer flag (#66, #65)",
          writing_grader.lead_accepted("as people use their tools for a limited time") == "use their tools"
          and writing_grader.lead_accepted("the night shift’s popularization") == "the night shift's popularization"
          and writing_grader.lead_accepted("The automotive industry along with the smartphone market are both a "
                                           "testament to this") == "are both a testament")
    check("...and nothing else is", writing_grader.lead_accepted("disposed into the oceans") is None
          and writing_grader.lead_accepted("are") is None)
    check("Speaking: 'daily lifestyles' (self-corrected in the recording, not in the transcript) is no mistake",
          speaking_grader.lead_accepted("to their daily lifestyles") == "daily lifestyles"
          and speaking_grader.lead_accepted("if not done so properly") is None)

    fb = schemas.WritingFeedback(
        mistakes=[], essay=[[schemas.EssaySegment(type="text", text="x", mistake_id=None, fix=None)]],
        criterion_notes=schemas.CriterionNotes(TR_or_TA="x", CC="x", LR="x", GRA="x"),
        strength=schemas.Strength(text="x", quoted_phrases=[]),
        priorities=[schemas.Priority(type="tip", title="Be precise in group comparisons", rule="Adjust ratios.",
                                     example_wrong="", example_right="Germany was about a half.", criteria=["TR"],
                                     mistake_ids=[]),
                    schemas.Priority(type="tip", title="Name the extremes", rule="x", example_wrong="",
                                     example_right="The US paid doctors most.", criteria=["TR"], mistake_ids=[])],
        vocabulary=[], checklist=[schemas.ChecklistItem(text="Adjust ratios for exceptions.", ref="Priority 01"),
                                  schemas.ChecklistItem(text="Name the extremes first.", ref="Priority 02")])
    kept, notes = writing_grader.true_tips(fb, lambda t: "half" in t)
    check("a tip whose data claim the chart doesn't bear out goes, with its checklist line (#105)",
          [p.title for p in kept.priorities] == ["Name the extremes"]
          and [c.ref for c in kept.checklist] == ["Priority 01"], (kept.priorities, kept.checklist))
    q125 ="The bar chart shows the percentage of people in four age groups who used the internet in 2010 and 2020."
    internet = {"blocks": [{"visual_type": "bar", "title": "", "units": "%", "time_points": [], "axis_labels": [],
                            "legend": [], "map_features": [], "overall": "", "process": None, "bars_drawn": [],
                            "values": [{"series": y, "point": g, "value": v, "text": "", "printed": True,
                                        "precision": 0.0, "status": "verified", "note": ""}
                                       for g, row in (("16–24", (72, 93)), ("25–44", (58, 85)), ("45–64", (36, 67)),
                                                      ("65+", (14, 40)))
                                       for y, v in zip(("2010", "2020"), row)]}]}
    length = fb.model_copy(update={"priorities": [schemas.Priority(
        type="tip", title="Meet the 150-word minimum", rule="Your report has 126 words: add 24 more words.",
        example_wrong="", example_right="Write at least 150 words; say that all four groups rose.", criteria=["TR"],
        mistake_ids=[])], "checklist": []})
    real = lambda t: "; ".join(sample_bank.example_problems(t, q125, internet))  # noqa: E731
    check("a word count is no data claim: the 150-word tip stays (#125, 126 words, lost it for its '150')",
          real("Write at least 150 words.") != ""
          and [p.title for p in writing_grader.true_tips(length, real)[0].priorities] == ["Meet the 150-word minimum"])
    wrong = length.model_copy(update={"priorities": [length.priorities[0].model_copy(
        update={"example_right": "Write at least 150 words; the 65+ group reached 45% in 2020."})]})
    check("...a wrong figure in the same tip still sends it", not writing_grader.true_tips(wrong, real)[0].priorities)
    words205 = " ".join(["word"] * 100) + "\n\n" + " ".join(["word"] * 105)
    _, notes2, _, _ = writing_grader.cap_bands("task2", words205, {"TR_or_TA": 6.0, "CC": 6.0, "LR": 5.0, "GRA": 5.0},
                                               {"TR_or_TA": "Both views and a clear stance."})
    check("a short Task 2 essay's Task note gives the count and the minimum, cap or no cap (#127, 205 words)",
          notes2["TR_or_TA"] == "Both views and a clear stance (205 words: aim for 250+).", notes2)
    words126 = " ".join(["word"] * 60) + "\n\n" + " ".join(["word"] * 66)
    _, notes1, _, _ = writing_grader.cap_bands("task1", words126, {"TR_or_TA": 6.0, "CC": 7.0, "LR": 6.0, "GRA": 6.0},
                                               {"TR_or_TA": "Overview limited."}, False)
    check("...a short Task 1 report's too (#125, 126 words)", notes1["TR_or_TA"] == "Overview limited (126 words: aim for 150+).",
          notes1)
    _, notes3, _, _ = writing_grader.cap_bands("task2", " ".join(["word"] * 150) + "\n\n" + " ".join(["word"] * 150),
                                               {"TR_or_TA": 6.0, "CC": 6.0, "LR": 5.0, "GRA": 5.0}, {"TR_or_TA": "Fine."})
    check("...a long enough one keeps its note", notes3["TR_or_TA"] == "Fine.", notes3)
    bands4, notes4, capped4, notices4 = writing_grader.cap_bands(
        "task2", " ".join(["word"] * 190) + "\n\nend", {"TR_or_TA": 7.0, "CC": 6.0, "LR": 5.0, "GRA": 5.0},
        {"TR_or_TA": "Fine."})
    check("...and a 191-word one gets the count once", notes4["TR_or_TA"].count("191 words") == 1, notes4)
    check("the word count lowers no band, it only gets a reminder (the user's ruling, 2026-11-09)",
          bands4["TR_or_TA"] == 7 and not capped4 and any("aim for at least 250" in n for n in notices4),
          (bands4, capped4, notices4))
    bands1, _, capped1, _ = writing_grader.cap_bands("task1", " ".join(["word"] * 60) + " 40%",
                                                     {"TR_or_TA": 8.0, "CC": 7.0, "LR": 7.0, "GRA": 7.0}, {}, False)
    check("...a 61-word Task 1 report too (was: Task 5 at most)", bands1["TR_or_TA"] == 8 and not capped1, capped1)


def fair_marking() -> None:
    """The academic lead's fair-marking prompt (2026-10-31): F1-F5."""
    from services.ai import essay_marking, mistake_rules, result_cache

    def final(wrong, correct, kind="error", criterion="LR", basis="grammar"):
        return mistake_rules.final_kind(wrong, correct, kind, criterion, basis)[0]

    # F1 (by code; the classes the model gives come with the paid run)
    never = {"squeeze in more homes at each floor": "fit in more homes at each floor",
             "are rife with locals": "are teeming with locals"}
    for wrong, correct in never.items():
        check(f"F1 praised, never flagged: {wrong!r}", final(wrong, correct) == "not_a_mistake")
    check("F1 'double story' is American spelling, no mistake",
          final("double story houses", "double-storey houses") == "not_a_mistake")
    for wrong, correct in {"a stable house to return to": "a stable home to return to",
                           "the use of them": "their use",
                           "For families, they definitely can benefit": "Families can definitely benefit"}.items():
        check(f"F1 alternative: {wrong!r}", final(wrong, correct) == "alternative")
    check("a synonym swap with no grammar changed is an alternative",
          final("are provided with very big screens", "are equipped with very big screens") == "alternative")
    check("...unless the word means something else", final("used to be unique", "used to be unusual",
                                                           basis="meaning") == "error")
    check("'another phrase is more common' is always an alternative",
          final("financial funds", "funds", basis="style") == "alternative")
    check("only a comma added is no mistake (a real Band 7 script: 5 of its 13 Grammar errors)",
          final("First of all money", "First of all, money", criterion="GRA") == "not_a_mistake"
          and final("people doesn't, use", "people don't, use", criterion="GRA") == "error")
    check("British and American both right", mistake_rules.spelling_variant("labeled", "labelled")
          and mistake_rules.spelling_variant("the colour", "the color")
          and not mistake_rules.spelling_variant("four", "for") and not mistake_rules.spelling_variant("filled", "filed"))

    # F2: every phrase of the prompt's section 2
    section2 = {"shops are available": "shops are open", "positive tendency": "positive development",
                "sleep regimes": "sleep patterns", "at certain parts of the day": "at certain times of the day",
                "certain people's freedoms": "the freedom of some people",
                "the night shift's popularization": "the popularity of night shifts",
                "use their tools for a limited time": "use their devices for a limited time",
                "The automotive industry along with the smartphone market are both a testament":
                    "The automotive industry and the smartphone market are both a testament",
                "that particular device": "this device", "creates a pressure": "creates pressure",
                "known for its visual nature, where people": "a visual platform where people",
                "plant the seed of future leaders emerging out of this": "plant the seed of future leaders emerging "
                                                                         "from this",
                "seemed to have thrived": "seemed to thrive", "develop their economy": "develop their economies",
                "declines in outputs": "a decline in output"}
    for wrong, correct in section2.items():
        check(f"F2 alternative: {wrong!r}", final(wrong, correct) == "alternative", final(wrong, correct))
    check("F2 ...but 'the football' in that stretch stays wrong",
          final("the football seemed to have thrived", "football seemed to have thrived") == "error")
    for phrase in ("of paramount importance", "differing views", "polishing their skills", "a crucial part of",
                   "a chart-high of", "bore parallels to", "reach parity", "buck the trend"):
        check(f"F2 praised: {phrase!r}", final(phrase, phrase + " here") == "not_a_mistake" or mistake_rules.praised(phrase))

    # F3: the three planted errors stay errors, whatever the filter said
    planted = [NS(wrong="people doesn't use", correct="people don't use", criterion="GRA", severity="slip"),
               NS(wrong="an useful tool", correct="a useful tool", criterion="GRA", severity="slip"),
               NS(wrong="more cheaper", correct="cheaper", criterion="GRA", severity="slip")]
    writing_grader.small_slips(planted)
    check("F3 'people doesn't', 'an useful', 'more cheaper' are errors", [m.severity for m in planted] == ["error"] * 3)
    for m in planted:
        check(f"F3 no rule turns {m.wrong!r} into an alternative", final(m.wrong, m.correct, criterion="GRA") == "error")
    check("the lead's real mistakes stay at least a slip", final("it should be noticed that", "it should be noted that",
                                                                 kind="alternative") == "slip"
          and final("it should be noticed that", "it should be noted that", basis="style") == "error")

    # F4: a footer inside a line
    text, removed = essay_marking.clean_pasted(
        "In 1990 output fell. Over the Ilhom Alijanov | IELTS Task 1: 9.0 | Page 3 of 154 next decade, it rose.")
    check("F4 footer gone, the note is due", removed and "Alijanov" not in text and "Page 3" not in text
          and essay_marking.PASTE_NOTICE == "Page headers/footers from pasted text were ignored.")

    # F5: scan artefacts - only in a scanned text
    scanned = "Prices that have to be paid. to enter are not afford.able and. other facilities exist."
    for wrong, correct in {"prices that have to be paid. to enter": "prices that have to be paid to enter",
                           "are not afford.able": "are not affordable", "stilljuggling": "still juggling",
                           "fvloreover,": "Moreover,", "a(so": "also", "and. other facilities": "and other facilities",
                           "On the other hand.,": "On the other hand,"}.items():
        check(f"F5 scan artefact: {wrong!r}", mistake_rules.scan_artefact(wrong, correct, scanned))
    for wrong, correct in {"hugh": "huge", "increace": "increased", "seatting": "seating", "redisign": "redesign",
                           "modificated": "modified", "refered": "referred", "money Firstly": "money. Firstly",
                           "Moreover;": "Moreover,"}.items():
        check(f"F5 the candidate's own mistake stays: {wrong!r}", not mistake_rules.scan_artefact(wrong, correct, scanned))
    check("F5 ...and only a photo or a PDF counts as scanned", mistake_rules.SCANNED_SOURCES == {"photo", "pdf"})

    # F6 (the question; the handler is in task1_images_test)
    t2 = "Some people say that theatres are no longer important. Discuss both these views and give your own opinion."
    t1 = "The graph below gives information about jobs. Summarise the information by selecting and reporting the main features."
    check("F6 a Task 2 question in Task 1 is told", essay_marking.wrong_section("task1", t2) == "task2")
    check("...a Task 1 question in Task 2 too", essay_marking.wrong_section("task2", t1) == "task1")
    cut = essay_marking.starts_mid_word
    check("an answer that starts mid-word is noticed (#121 'e youngest people', #122 'sitors in all the years')",
          cut("e youngest people had the highest figures. There was a gap. In 2010, 72% used it.")
          and cut("sitors in all the years. Italy was second. Spain had the lowest figure."))
    check("...a whole answer, or one written all in lower case, is left alone",
          not cut("The youngest people had the highest figures. There was a gap. In 2010, 72% used it.")
          and not cut("the youngest people had the highest figures. there was a gap. in 2010, 72% used it.")
          and not cut("“The chart shows two groups. One rose. The other fell."))
    check("...the right section is left alone", essay_marking.wrong_section("task1", t1) is None
          and essay_marking.wrong_section("task2", t2) is None)
    check("...a chart question on advantages stays Task 1", essay_marking.wrong_section(
        "task1", "The chart below shows the advantages and disadvantages of each transport type.") is None)

    # One error doesn't cost a band
    essay = " ".join(["word"] * 250)
    gra = lambda *wrong: [NS(wrong=w, correct=w + "x", criterion="GRA", severity="error", unclear=False)  # noqa: E731
                          for w in wrong]
    slip = NS(wrong="other", correct="others", criterion="GRA", severity="slip", unclear=False)
    reason = writing_grader.language_reason
    check("a single slip never lowers a criterion", not reason("GRA", [], essay, [], [slip], False))
    check("one isolated error with a wide range doesn't drop 9 -> 8", not reason("GRA", [], essay, [], gra("a"), True))
    check("...it does when the range isn't wide", reason("GRA", [], essay, [], gra("a"), False))
    check("...or the meaning is unclear", reason(
        "GRA", [], essay, [], [NS(wrong="a", correct="b", criterion="GRA", severity="error", unclear=True)], True))
    check("two distinct errors are a reason", reason("GRA", [], essay, [], gra("a", "b"), True))
    check("a slip quoted as the reason is none", not reason("GRA", ['"other" is wrong'], "the other word " * 80, [],
                                                            [slip], True))
    check("slips past 'rare' are", reason("GRA", [], essay, [], [
        NS(wrong=f"w{i}", correct=f"c{i}", criterion="GRA", severity="slip", unclear=False) for i in range(5)], True))

    # Lexical 7 layer: the official cafe sample
    cafe = " ".join(["word"] * 172)
    spelling = [NS(wrong=w, correct=c, criterion="LR", severity=sev, unclear=False) for w, c, sev in (
        ("redisign", "redesign", "error"), ("midle", "middle", "slip"), ("seatting", "seating", "error"),
        ("modificated", "modified", "error"))]
    long_essay = "strong one here and strong two here and strong three here " + cafe
    bands = {"TR_or_TA": 6.0, "CC": 6.0, "LR": 6.0, "GRA": 5.0}
    out, raised = writing_grader.floors("task1", long_essay, bands, spelling,
                                        ["strong one here", "strong two here", "strong three here"])
    check("the cafe's 4 spelling errors in ~180 words stay Lexical 6", out["LR"] == 6, raised)
    out, raised = writing_grader.floors("task1", long_essay, bands, spelling[:1],
                                        ["strong one here", "strong two here", "strong three here"])
    check("...one in ~180 words gets the layer", out["LR"] >= 7, raised)
    shown = writing_grader.shown_strong("A rich phrase and a word fine.", ["rich phrase", "a word", "not in the text"],
                                        [NS(wrong="a word", correct="the word")])
    check("only the strong phrases the report shows count", shown == ["rich phrase"], shown)

    # Overview
    notes = {k: "n" for k in ("TR_or_TA", "CC", "LR", "GRA")}
    full = {"TR_or_TA": 8.0, "CC": 8.0, "LR": 8.0, "GRA": 8.0}
    capped = writing_grader.cap_bands("task1", " ".join(["word"] * 200) + "\n\nmore words here", full, notes, True, "none")
    check("no overview: Task Achievement 5 at most", capped[0]["TR_or_TA"] == 5, capped[2])
    # with a figure in it, so only the overview cap speaks (no figure at all is the "no data" limit, 5)
    capped = writing_grader.cap_bands("task1", " ".join(["word"] * 200) + " rose to 40%\n\nmore words here", full, notes,
                                      True, "generic")
    check("a generic overview: 6 at most", capped[0]["TR_or_TA"] == 6, capped[2])
    capped = writing_grader.cap_bands("task2", " ".join(["word"] * 300) + "\n\nmore words here", full, notes, True, "none")
    check("...Task 2 has no overview cap", capped[0]["TR_or_TA"] == 8, capped[2])

    # A model answer follows its own advice
    problems = sample_bank.answer_problems(["In 1960 the number of Manufacturing jobs was high."], "Q", "task1", (),
                                           ["Manufacturing"])
    check("a word the report corrects to lower case stays lower case in the model answer",
          any("lower case" in p for p in problems), problems)
    check("...at the start of a sentence it may be capital", not any("lower case" in p for p in sample_bank.answer_problems(
        ["Manufacturing jobs were the most common."], "Q", "task1", (), ["Manufacturing"])))

    # The official samples (the file itself lives on the data volume)
    from scripts.marks_check import gate_failed
    from services.ai import writing_anchors
    check("question kinds", [writing_anchors.question_type(q) for q in (
        "Do the advantages outweigh the disadvantages?", "Discuss both these views and give your own opinion.",
        "What are the causes of this?", "To what extent do you agree or disagree?")]
        == ["advantages", "both_views", "causes", "opinion"])
    check("...the closing 'Give reasons for your answer' isn't a question about reasons", writing_anchors.question_type(
        "To what extent do you agree? Give reasons for your answer and include relevant examples.") == "opinion")
    pool = {"task1": [{"id": f"x{b}", "band": b, "kind": k, "text": f"text {b} {k}", "question": "", "comment": "c"}
                      for b, k in ((5.0, "bar"), (6.0, "line"), (6.0, "bar"), (7.5, "line"), (5.5, "map"))]}
    real_load = writing_anchors._load
    writing_anchors._load = lambda name: pool if name == "cambridge_anchors.json" else real_load(name)
    try:
        picked = writing_anchors.similar_anchors("task1", "line", "Q", 6.0, "another answer")
        check("the anchors: at most 3, nearest band, the same kind first", len(picked) <= 3
              and "x6.0" == picked[0]["id"] if False else any(a["band"] == 6 and a["kind"] == "line" for a in picked), picked)
        text = writing_anchors.anchors_text("task1", "line", "another answer", "Q", 6.0)
        check("...shown with their band and the examiner's comment, no book named", "Examiner's comment" in text
              and "Cambridge" not in text, text[:200])
    finally:
        writing_anchors._load = real_load
    check("the gate: mean above +0.25 stops the run", gate_failed(
        [{"name": "a", "examiner": 7, "overall": 8}, {"name": "b", "examiner": 7, "overall": 7}]) is not None)
    check("...a low band risen stops it", "low-band" in (gate_failed(
        [{"name": "a", "examiner": 5.5, "overall": 6.5}, {"name": "b", "examiner": 8, "overall": 7}]) or ""))
    check("...so does a mean too strict", gate_failed(
        [{"name": "a", "examiner": 7, "overall": 6}, {"name": "b", "examiner": 7, "overall": 7}]) is not None)
    check("...the early gate is wider", gate_failed(
        [{"name": "a", "examiner": 7, "overall": 7.5}, {"name": "b", "examiner": 7, "overall": 7}], 0.5) is None)
    check("...on target goes on", gate_failed(
        [{"name": "a", "examiner": 6, "overall": 6.5}, {"name": "b", "examiner": 7, "overall": 6.5}]) is None)

    # Marking only: the whole path with faked calls - no vocabulary asked or checked, no report, no retry
    asked = []
    long_essay2 = " ".join(["Cities grow fast and many people move there."] * 30)
    judged = schemas.CriterionJudgement(checklist=[], strengths=[], weaknesses=[], band=6, note="n")

    async def fake_parse(purpose, max_tokens, messages, response_format, effort=None, model=None):
        asked.append((purpose, messages[0]["content"], messages[1]["content"]))
        if response_format is schemas.WritingFeedback:
            parsed = schemas.WritingFeedback(
                mistakes=[schemas.Mistake(id=1, wrong="Cities grow", correct="Cities are growing", why="tense",
                                          criterion="GRA", unclear=False, if_you_meant=None)],
                essay=[[schemas.EssaySegment(type="text", text=long_essay2, mistake_id=None, fix=None)]],
                criterion_notes=schemas.CriterionNotes(TR_or_TA="x", CC="x", LR="x", GRA="x"),
                strength=schemas.Strength(text="x", quoted_phrases=[]), priorities=[], vocabulary=[], checklist=[])
        elif response_format is schemas.MissedMistakes:
            parsed = schemas.MissedMistakes(mistakes=[])
        elif response_format is schemas.WritingVerdicts:
            parsed = schemas.WritingVerdicts(verdicts=[schemas.WritingVerdict(
                id=1, kind="error", criterion="GRA", reason="r", fixed_correct=None)], vocabulary=[])
        else:
            parsed = schemas.WritingScoring(TR_or_TA=judged, CC=judged, LR=judged, GRA=judged)
        return NS(choices=[NS(message=NS(parsed=parsed))])

    real_parse = writing_grader._parse
    writing_grader._parse = fake_parse
    try:
        marks = asyncio.run(writing_grader.grade_writing("task2", "Do you agree?", long_essay2, marks_only=True))
    finally:
        writing_grader._parse = real_parse
    purposes = [a[0] for a in asked]
    # TA 6, CC 5 (one block of text), LR 6, GRA 6: 5.75, rounded down to 5.5 (the user's rule, 2026-11-09)
    check("marks only: the bands and every flag come back", marks.overall == 5.5 and marks.flags[0]["class"] == "error",
          marks)
    check("...one feedback call, no retry, no vocabulary in the filter", purposes.count("feedback") == 1
          and "VOCABULARY TO CHECK" not in next(a[2] for a in asked if a[0] == "verify")
          and "empty lists" in next(a[1] for a in asked if a[0] == "feedback"), purposes)

    # Same text = same result
    a = result_cache.key("task1", "The graph shows jobs.", "An answer.\n\nIts second part.", True, "text")
    check("the cache key ignores the question's punctuation and the answer's spacing",
          a == result_cache.key("task1", "the graph shows jobs", "An answer. Its second part.", True, "text"))
    check("...a typed and a scanned text are different", a != result_cache.key(
        "task1", "The graph shows jobs.", "An answer. Its second part.", True, "photo"))

    async def round_trip():
        before = result_cache.ENABLED
        result_cache.ENABLED = True
        try:
            await result_cache.put(a, ({"LR": 6.0}, 6.0, None, NS(overall_band=6.0)))
            return await result_cache.get(a)
        finally:
            result_cache.ENABLED = before

    got = asyncio.run(round_trip())
    check("a finished check comes back as it was", got is None or got[0] == {"LR": 6.0}, got)


def rounding() -> None:
    """The user's rule (2026-11-09), Writing and Speaking alike: a mean of .25
    or .75 goes down to the half band below it."""
    from db.crud import mock_overall
    from services.ai.scoring import band_down, overall_band, writing_speaking_overall

    cases = {(6, 7, 7, 7): 6.5, (5, 5, 5, 6): 5.0, (6, 6, 7, 7): 6.5, (7, 7, 7, 7): 7.0, (8, 9, 9, 9): 8.5,
             (9, 9, 9, 9): 9.0, (6, 6, 6, 7): 6.0, (4, 5, 5, 5): 4.5}
    got = {c: overall_band(list(c)) for c in cases}
    check("overall band: 6.75 -> 6.5, 5.25 -> 5.0, 6.5 stays, 8.75 -> 8.5 (rounded down)", got == cases, got)
    check("#138's bands (TA 6, CC 7, LR 7, GRA 7) now give 6.5, the examiner's band",
          overall_band([6.0, 7.0, 7.0, 7.0]) == 6.5)
    check("band_down: a float mean just under a half band is not pushed below it",
          band_down((6.5 + 2 * 6.5) / 3) == 6.5 and band_down(20.5 / 3) == 6.5 and band_down(7.0) == 7.0)
    check("Writing + Speaking estimate keeps the IELTS overall rule (6.25 -> 6.5, 6.75 -> 7.0)",
          (writing_speaking_overall(6.25), writing_speaking_overall(6.75), writing_speaking_overall(6.5)) == (6.5, 7.0, 6.5))
    mock = mock_overall({"writing_task1": 6.0, "writing_task2": 6.5, "speaking_part1": 7.0, "speaking_part2": 6.5,
                         "speaking_part3": 6.5})
    check("mock: Writing (6 + 2 x 6.5) / 3 = 6.33 -> 6.0, Speaking 6.67 -> 6.5, overall 6.25 -> 6.5",
          mock == {"writing": 6.0, "speaking": 6.5, "overall": 6.5}, mock)


def examiner_rules() -> None:
    """2026-11-09: the books' lessons reach the finder and the filter; the filter votes; a map's places are
    compared between the two reads; each kind of visual has its examiners' needs."""
    from services.ai import visual_reading
    from services.ai.schemas import MapFeature, MapFeatureState, VisualBlock

    g = writing_grader
    system = g._system_prompt("task1", None, 0.0, 0.0, None)
    check("the finder, the missed-errors pass and the filter all get what real examiners count",
          g.EXAMINER_PRACTICE in system and g.EXAMINER_PRACTICE in g._missed_prompt("task2")
          and g.EXAMINER_PRACTICE in g.VERIFY_SYSTEM)
    check("...and it says a plain or overused correct word is no mistake",
          "never one mistake for each use" in g.EXAMINER_PRACTICE and "upgrade idea at most" in g.EXAMINER_PRACTICE)

    # voting
    err = ("error", "LR", "r1", None, "meaning", False)
    slip = ("slip", "GRA", "r2", None, "grammar", False)
    alt = ("alternative", "LR", "r3", None, "meaning", False)
    no = ("not_a_mistake", "LR", "r4", None, "meaning", False)
    check("vote: two of three say error -> error", g.vote([err, no, err])[0] == "error")
    check("...error, slip, no -> counted, but a slip (no majority for error)", g.vote([err, slip, no])[0] == "slip")
    check("...one of three -> not counted (an alternative stays an upgrade idea)", g.vote([err, alt, alt])[0] == "alternative")
    check("...a 1-1 tie is the student's benefit of the doubt", g.vote([err, no])[0] == "not_a_mistake")
    check("...two agreeing runs need no third", not g.disputed([err, err]) and g.disputed([err, no])
          and g.disputed([err, slip]) and g.disputed([err, None]) and not g.disputed([alt, no]))

    calls = []

    async def fake_verify(source, items, vocabulary=None, model=None):
        calls.append(([i[0] for i in items], bool(vocabulary)))
        run = len(calls)
        out = {}
        for i in items:
            if i[0] == 1:
                out[1] = err  # every run: an error
            elif i[0] == 2:
                out[2] = err if run == 1 else no  # split: the third run decides
            else:
                out[3] = no
        return out, ({0: (None, None)} if vocabulary else {})

    real = g._verify_call
    g._verify_call = fake_verify
    try:
        items = [(k, "essay", f"w{k}", f"c{k}", "why", "LR", "") for k in (1, 2, 3)]
        verdicts, words = asyncio.run(g._voted_verify("SRC", items, [(0, "t", "m", "e", "")]))
    finally:
        g._verify_call = real
    check("voted filter: two runs, the vocabulary in the first only, a third on the split item only",
          calls == [([1, 2, 3], True), ([1, 2, 3], False), ([2], False)], calls)
    check("...the unanimous error stays, the split one goes by the third run, the vocabulary comes back",
          verdicts[1][0] == "error" and verdicts[2][0] == "not_a_mistake" and verdicts[3][0] == "not_a_mistake"
          and words == {0: (None, None)}, verdicts)

    # #137: the scorer named an omission, but its wording had an adverb in between - Task 8 went to 9
    check("an omission with an adverb in between is a reason ('not explicitly highlighted', 'does not explicitly note')",
          g.evidenced(["Retention of the central core is implied but not explicitly highlighted as a key feature."], "x")
          and g.evidenced(["Does not explicitly note retained central facilities (pool, seating)"], "x"))
    check("...'not fully developed' with nothing behind it still is none", not g.evidenced(["not fully developed"], "x"))

    # what each kind of visual needs
    check("a map's needs: both layouts, where things are, a summary of the main changes",
          all(w in g.kind_needs("map") for w in ("earlier layout", "WHERE", "summary of the main changes")))
    check("...a process: stages in the overview; a bar chart: categories and change over time; unknown: nothing",
          "main stages" in g.kind_needs("cycle") and "CATEGORIES" in g.kind_needs("bar")
          and "OVER TIME" in g.kind_needs("bar") and g.kind_needs(None) == "")
    check("...the scorer sees them for its kind and runs the checklist item for them",
          g.kind_needs("map") in g._scoring_system("task1", "map")
          and any("WHAT THIS KIND OF VISUAL NEEDS" in i for i in g.CHECKLISTS["TR_or_TA"]["task1"]))

    # a map's places, compared between the two reads
    check("place conflict: inside vs outside the building (#138's leisure pool)",
          visual_reading.place_conflict("far west within the extended building",
                                        "north-west of site, outside main building") != "")
    check("...north vs south is one; the same side in other words is none",
          visual_reading.place_conflict("north side of the site", "bottom of the plan") != ""
          and visual_reading.place_conflict("immediately north of the entrance, centre-south of building",
                                            "south-central, main entrance") == ""
          and visual_reading.place_conflict("far east side, north of the new changing room",
                                            "far east side, top-right") == "")

    def feature(name, place, *states):
        return MapFeature(name=name, location=place,
                          states=[MapFeatureState(time=t, status=s, detail=d) for t, s, d in states])

    def block(*features):
        return VisualBlock(visual_type="map", title="", units="", time_points=["now", "plan"], axis_labels=[],
                           legend=[], data=[], map_features=list(features), overall="", process=None, bars_drawn=[])

    a = block(feature("Gym", "north side", ("now", "present", ""), ("plan", "changed", "larger")),
              feature("Leisure pool", "far west within the extended building", ("now", "absent", ""),
                      ("plan", "present", "new pool")),
              feature("Outdoor courts", "west of the building", ("now", "present", ""), ("plan", "absent", "")))
    b = block(feature("Gym", "north side", ("now", "present", ""), ("plan", "present", "")),
              feature("Leisure pool", "north-west of site, outside main building", ("now", "absent", ""),
                      ("plan", "present", "occupies the corner outside")),
              feature("Outdoor courts", "west of the building", ("now", "present", ""), ("plan", "present", "")))
    unreadable: list[str] = []
    kept = {f.name: f for f in visual_reading._merge_map(a, b, unreadable)}
    check("merge: a feature both reads see at both dates stays, its change UNCERTAIN (#137's gym was dropped)",
          "Gym" in kept and "UNCERTAIN" in kept["Gym"].states[1].detail and kept["Gym"].states[1].status == "present",
          kept.get("Gym"))
    check("...a feature placed inside by one read and outside by the other: its place UNCERTAIN, its details gone",
          "Leisure pool" in kept and kept["Leisure pool"].location.startswith("UNCERTAIN")
          and all(s.detail == "" for s in kept["Leisure pool"].states), kept.get("Leisure pool"))
    check("...present vs absent is still a disagreement: left out, and said so",
          "Outdoor courts" not in kept and any("Outdoor courts" in u for u in unreadable), unreadable)


def official_descriptors() -> None:
    """The official band descriptors (May 2023), sent by the user on 2026-11-09: in the prompts close to word
    for word, "must fully fit", the [LIMIT] features, and what code can apply of them."""
    g = writing_grader
    t1, t2 = g.band_descriptors("task1"), g.band_descriptors("task2")
    check("the scorer and the feedback get the official descriptors of their task",
          t1 in g._scoring_system("task1", "map") and t2 in g._scoring_system("task2")
          and t1 in g._system_prompt("task1", None, 0.0, 0.0, None))
    check("...Task 1: Task Achievement with 'no data' as a limit; Task 2: Task Response with 'incompletely addressed'",
          "[LIMIT] There may be no data to support the description" in t1 and "TASK RESPONSE" not in t1
          and "[LIMIT] The main parts of the prompt are incompletely addressed" in t2)
    check("...paragraphing 'inadequate or missing' limits Coherence in Task 2 only (the Task 1 descriptor has no such line)",
          "[LIMIT] Paragraphing may be inadequate or missing" in t2 and "inadequate or missing" not in t1)
    check("...simple sentences predominating limit Grammar at 4",
          "[LIMIT] Subordinate clauses are rare and simple sentences predominate" in t1)
    guide = g.marking_guide()
    check("the guide: 'must fully fit' replaces 'benefit of the doubt - give the higher band'",
          "FULLY fits" in guide and "Benefit of the doubt" not in guide)
    check("...and no longer says simple sentences are Grammar 5-6", "only simple sentences are 5-6" not in guide)

    # sentences with a grammar mistake: Grammar 8 needs most of them clean
    text = ("The figure rose sharply in 2010. Sales was low in 2011. The number of users grew steadily. "
            "Prices has fallen since then. Both companies expanded quickly. Its share were small.")
    gra = [NS(criterion="GRA", wrong=w, correct="x", severity="error", unclear=False)
           for w in ("Sales was low", "Prices has fallen", "Its share were small")]
    check("error-free sentences counted from the checked grammar mistakes (3 of 6)",
          g.error_free_sentences(text, gra) == (3, 6), g.error_free_sentences(text, gra))
    lowered, why = g.sentence_ceiling(text, {"TR_or_TA": 7.0, "CC": 7.0, "LR": 7.0, "GRA": 8.0}, gra)
    check("...half of them or fewer clean: Grammar 8 -> 7", lowered["GRA"] == 7 and why, why)
    kept, _ = g.sentence_ceiling(text, {"TR_or_TA": 7.0, "CC": 7.0, "LR": 7.0, "GRA": 8.0}, gra[:2])
    check("...four of six clean: Grammar 8 stays", kept["GRA"] == 8)
    check("...a Lexical mistake is not a grammar one",
          g.error_free_sentences(text, [NS(criterion="LR", wrong="Sales was low", correct="x")]) == (6, 6))

    bands = {"TR_or_TA": 8.0, "CC": 8.0, "LR": 8.0, "GRA": 8.0}
    tiny, _, why, _ = g.cap_bands("task2", "I agree with this idea because it is good for people.", bands, {})
    check("20 words or fewer: Band 1 on every criterion", set(tiny.values()) == {1.0}, why)
    question = ("Some people think that governments should spend more money on public transport than on roads. "
                "To what extent do you agree or disagree?")
    own = " ".join(["Cities need better buses and trains for everyone who lives in them."] * 19)  # 228 words
    essay = question + "\n\n" + own + "\n\n" + own[:60]
    capped, _, why, said = g.cap_bands("task2", essay, bands, {}, question=question)
    check("words copied from the question are not counted ('any copied rubric must be discounted'): about 263 "
          "words, under 250 of the writer's own - a reminder, no band lowered",
          capped == bands and any("words of your own" in n and "aim for at least 250" in n for n in said), (why, said))
    report = ("The graph shows how sales changed. Sales rose and then fell, while profits stayed higher than costs "
              "throughout the period. ") * 12
    capped, _, why, _ = g.cap_bands("task1", report, bands, {}, True)
    check("Task 1 on a visual of figures with no figure: 'no data to support the description' - Task 5 at most",
          capped["TR_or_TA"] == 5 and any("No figures" in w for w in why), why)
    check("...and one block of text: Coherence 7 at most (Task 1 Coherence 8 needs sufficient paragraphing)",
          capped["CC"] == 7)
    with_data = report.replace("Sales rose", "Sales rose to 40% in 2010", 1) + "\n\nOverall, sales led."
    capped, _, _, _ = g.cap_bands("task1", with_data, bands, {}, True)
    check("...a report with a figure and two paragraphs keeps its bands", capped == bands, capped)
    capped, _, _, _ = g.cap_bands("task1", report + "\n\nOverall, it changed.", bands, {}, False)
    check("...a map or a process needs no figures", capped["TR_or_TA"] == 8)

    # Task Achievement 8 -> 9 only with the word minimum, no Task mistake and a clear overview (#137)
    long_report = ("In 2010 sales rose to 40%, while costs fell to 20%, so the gap widened over the period. " * 12).strip()

    def task_run(overview="clear", checked=None, weak=()):
        def item(key):
            items = g.CHECKLISTS[key]["task1"] if key == "TR_or_TA" else g.CHECKLISTS[key]
            return schemas.CriterionJudgement(checklist=[schemas.CheckFinding(item=i, found=False, quote="")
                                                         for i in items], strengths=[],
                                              weaknesses=list(weak) if key == "TR_or_TA" else [], band=8, note="n")
        scoring = schemas.WritingScoring(TR_or_TA=item("TR_or_TA"), CC=item("CC"), LR=item("LR"), GRA=item("GRA"),
                                         overview=overview)

        async def fake(purpose, max_tokens, messages, response_format, effort=None):
            return NS(choices=[NS(message=NS(parsed=scoring))])
        real = g._parse
        g._parse = fake
        try:
            return asyncio.run(g._score_once("task1", "The graph shows sales.", long_report, None, checked=checked))
        finally:
            g._parse = real

    check("Task 8 with nothing wrong, a clear overview and enough words: lifted to 9", task_run(checked=[])["TR_or_TA"] == 9)
    check("...not with a generic overview", task_run("generic", [])["TR_or_TA"] == 8)
    check("...not with a Task mistake on the checked list",
          task_run(checked=[NS(criterion="TR", wrong="sales rose to 40%", correct="x", severity="error",
                               unclear=False, less_common=False)])["TR_or_TA"] == 8)
    check("...not when the run names a weakness, quoted or not (#153: 'depth could be further expanded' -> Task 9)",
          task_run(checked=[], weak=["Depth could be further expanded (e.g., consumer choice) but coverage is "
                                     "sufficient for the task."])["TR_or_TA"] == 8)


def paid_run_fixes() -> None:
    """After the paid run of 2026-11-09: a word choice off with its meaning clear weighs half; Task runs far
    apart get more runs; a map's sizes are not judged by "retained"."""
    from services.ai import scoring
    from services.ai.visual_reading import JUDGE_RULES

    g = writing_grader
    d = marking_config.density()
    clear = NS(criterion="LR", severity="error", less_common=g.CLEAR_CHOICE, unclear=False, wrong="a", correct="b")
    plain = NS(criterion="LR", severity="error", less_common=False, unclear=False, wrong="c", correct="d")
    check("a word choice off with its meaning clear weighs half (the descriptors: 'occasional inaccuracies in word "
          "choice and collocation' at Lexical 8)",
          g._weight(clear, d["slip_weight"], d["less_common_weight"]) == d["less_common_weight"]
          and g._weight(plain, d["slip_weight"], d["less_common_weight"]) == 1.0)
    essay = " ".join(["The city invested heavily in modern public transport for its residents."] * 30)
    evidence = g.mistake_evidence(essay, [NS(**{**vars(clear), "wrong": "invested heavily", "correct": "spent much"})],
                                  [], [])
    check("...the scorer sees it marked so", "word choice off, meaning clear" in evidence, evidence)
    strong = ["invested heavily", "modern public transport", "its residents"]
    four = [NS(criterion="LR", severity="error", less_common=g.CLEAR_CHOICE, unclear=False, wrong=f"w{i}",
               correct=f"c{i}") for i in range(4)]
    raised, why = g.floors("task2", essay, {"TR_or_TA": 7.0, "CC": 7.0, "LR": 6.0, "GRA": 7.0}, four, strong)
    check("...four of them in 360 words keep the clear-vocabulary floor (Lexical 7); four plain errors don't",
          raised["LR"] == 7 and g.floors("task2", essay, {"TR_or_TA": 7.0, "CC": 7.0, "LR": 6.0, "GRA": 7.0},
                                         [NS(**{**vars(m), "less_common": False}) for m in four], strong)[0]["LR"] == 6,
          why)

    def runs_for(values):
        it = iter(values)

        async def once():
            return {"TR_or_TA": float(next(it)), "CC": 7.0, "LR": 7.0, "GRA": 7.0}
        medians, good = asyncio.run(scoring.median_of_runs(once, g.SCORE_KEYS, steady=("TR_or_TA", 5)))
        return medians["TR_or_TA"], len(good)

    check("Task runs 6, 8, 7 (two bands apart): two more runs, the median of five", runs_for([6, 8, 7, 7, 7]) == (7, 5))
    check("...runs 6, 7, 7: no more", runs_for([6, 7, 7]) == (7, 3))
    check("...two agreeing runs: no third", runs_for([8, 8]) == (8, 2))
    check("a map's size claims: 'retained' says nothing about size - in the judge rules and the filter",
          "says nothing about size" in JUDGE_RULES and "says nothing" in g.VERIFY_SYSTEM)

    # ...and by code, the same every time (the prompt line alone didn't hold in the second paid run)
    from services.ai.mistake_rules import existential_list, final_kind
    from services.ai.visual_check import protected_mistake
    check("'there is' before a list starting with a singular noun is right (proximity agreement)",
          existential_list("there is going to be a sports shop and a cafe",
                           "there are going to be a sports shop and a cafe")
          and existential_list("There's a pen and two books", "There are a pen and two books")
          and final_kind("there was a bank and a school", "there were a bank and a school", "error", "GRA")[0]
          == "not_a_mistake")
    check("...'there is a lot of cars' (no list) and 'there is many shops' stay mistakes",
          not existential_list("there is a lot of cars", "there are a lot of cars")
          and not existential_list("there is many shops and cafes", "there are many shops and cafes"))
    map_reading = {"blocks": [{"visual_type": "map", "values": []}]}
    bar_reading = {"blocks": [{"visual_type": "bar", "values": []}]}
    check("a map: 'the gym is going to be larger' -> 'retained' is set aside by code",
          protected_mistake("the gym is going to be larger", "the gym is going to be retained", "", map_reading, "", set()))
    check("...not a place claim ('opposite the gym'), and not on a chart of figures",
          not protected_mistake("opposite the gym", "on the east side of the site", "", map_reading, "", set())
          and not protected_mistake("sales were larger", "sales remained the same", "", bar_reading, "", set()))


def speed_fixes() -> None:
    """2026-11-10, time and money without touching the marking: the model answer's false rejections, its two
    checks side by side; a student's report is judged exactly as before."""
    from services.ai.visual_check import figures, match_figure

    vals = [37, 40, 43, 37, 43, 48, 46, 43, 40, 44]
    line = {"blocks": [{"visual_type": "line", "units": "millions of passengers", "title": "Train passengers",
                        "values": [{"series": "Passengers (millions)", "point": str(2000 + i), "value": float(v),
                                    "text": "", "printed": False, "precision": 5.0, "status": "verified", "note": ""}
                                   for i, v in enumerate(vals)]}], "readability": "clear"}
    fell = next(f for f in figures("It fell by about 5 million in 2003.") if not f["year"])
    check("a change named by one year: worked out from the year before for the model answer's check only",
          match_figure(fell, line, "Train passengers") == "miss"
          and match_figure(fell, line, "Train passengers", neighbours=True) == "derived")
    check("...the model answer's data check no longer rejects it",
          not any("not on the DATA SHEET" in p for p in sample_bank.data_problems(
              "It fell by about 5 million in 2003.", "Train passengers", line)))
    process = {"blocks": [{"visual_type": "process", "values": [], "process": {
        "steps": ["collect", "sort", "crush", "wash", "melt", "shape"], "numbered": True,
        "stages": [{"name": "collection", "steps": [1, 2]}, {"name": "processing", "steps": [3, 4, 5, 6]}],
        "kind": "linear", "natural_or_manmade": "man-made"}}], "readability": "clear"}
    check("a process (no figures on its sheet): step numbers are no data to reject",
          not any("not on the DATA SHEET" in p for p in sample_bank.data_problems(
              "At stage 2 the bottles are sorted, and in stages 5 and 7 they are melted.", "How bottles are recycled",
              process)))
    check("...a chart's wrong figure still is", any("not on the DATA SHEET" in p for p in sample_bank.data_problems(
        "It peaked at about 300 million in 2005.", "Train passengers", line)))
    check("word slack: 168 and 208 words pass for a 170-200 answer, 150 does not",
          sample_bank.WORD_SLACK == 10 and 170 - 10 <= 168 and 208 <= 200 + 10 and not 170 - 10 <= 150)

    # the self-check score and the reviewer side by side, the same decisions as one after the other
    order: list[str] = []

    async def run_answer(score_first: float, review_first, review_second=None):
        writes = iter([["first answer"], ["second answer"], ["third answer"]])

        async def fake_write(*args, **kwargs):
            order.append("write")
            return next(writes), [], []

        async def fake_score(task_type, question, text, images=None, sheet=""):
            order.append(f"score:{text}")
            await asyncio.sleep(0.01)
            return score_first if text == "first answer" else 8.5

        async def fake_review(task_type, question, paragraphs, subtype, images=None, sheet=""):
            order.append(f"review:{paragraphs[0]}")
            await asyncio.sleep(0.01)
            return review_first if paragraphs[0] == "first answer" else review_second

        saved = (sample_bank._write, sample_bank.quality_problem, writing_grader.score_writing, sample_bank.load_library)
        sample_bank._write, sample_bank.quality_problem, writing_grader.score_writing = fake_write, fake_review, fake_score
        sample_bank.load_library = lambda: []
        try:
            return await sample_bank.build_model_answer("task2", "Discuss both views.")
        finally:
            sample_bank._write, sample_bank.quality_problem, writing_grader.score_writing, sample_bank.load_library = saved

    order.clear()
    answer = asyncio.run(run_answer(8.0, None))
    check("a good first answer: scored and reviewed side by side, kept, nothing written again",
          answer.paragraphs == ["first answer"] and order == ["write", "score:first answer", "review:first answer"]
          and answer.log.get("quality") == "ok", order)
    order.clear()
    answer = asyncio.run(run_answer(6.0, None, None))
    check("...scored under the floor: written again, the better one kept and reviewed itself (not the first's verdict)",
          answer.paragraphs == ["second answer"] and "review:second answer" in order, order)
    order.clear()
    answer = asyncio.run(run_answer(8.0, "an overview is missing", None))
    check("...the reviewer rejects it: written again and kept once the reviewer passes it",
          answer.paragraphs == ["second answer"] and answer.log.get("quality") == "an overview is missing"
          and answer.log.get("quality_retry") == "ok", (order, answer.log))


def uzbek_vocabulary() -> None:
    """2026-11-10 (the user): the vocabulary table's meanings in Uzbek too - one small call, never in the way."""
    from services.ai import vocab_uz

    items = [schemas.VocabEntry(term="allocate funds", meaning="decide to spend money on something",
                                example="The city allocated funds to buses."),
             schemas.VocabEntry(term="commuters", meaning="people who travel to work every day",
                                example="Commuters prefer fast trains.")]
    seen = []

    def fake_parse(reply):
        async def parse(**kwargs):
            seen.append(kwargs)
            if isinstance(reply, Exception):
                raise reply
            return NS(choices=[NS(message=NS(parsed=vocab_uz.UzMeanings(meanings=reply)))], usage=None)
        return parse

    # every argument the call uses must be one the installed SDK accepts (2026-11-10: reasoning_effort as an
    # argument raised a TypeError on every report - the fake below took any keyword)
    import inspect

    from openai.resources.beta.chat.completions import AsyncCompletions

    accepted = set(inspect.signature(AsyncCompletions.parse).parameters)

    def strict(parse):
        async def call(**kwargs):
            unknown = set(kwargs) - accepted
            if unknown:
                raise TypeError(f"unexpected keyword argument(s) {sorted(unknown)}")
            return await parse(**kwargs)
        return call

    real = vocab_uz.client
    try:
        vocab_uz.client = NS(beta=NS(chat=NS(completions=NS(parse=strict(fake_parse(
            ["mablag' ajratmoq", "ishga qatnovchilar"]))))))
        got = asyncio.run(vocab_uz.uzbek_meanings(items))
        check("Uzbek meanings: one per item, in order, from the cheap model",
              got == ["mablag' ajratmoq", "ishga qatnovchilar"] and seen[-1]["model"] == vocab_uz.settings.openai_model_sample
              and "allocate funds" in seen[-1]["messages"][1]["content"], (got, seen[-1].get("model")))
        vocab_uz.client = NS(beta=NS(chat=NS(completions=NS(parse=fake_parse(["faqat bittasi"])))))
        check("...a count that doesn't match: none shown, never a wrong pairing",
              asyncio.run(vocab_uz.uzbek_meanings(items)) == ["", ""])
        vocab_uz.client = NS(beta=NS(chat=NS(completions=NS(parse=fake_parse(RuntimeError("down"))))))
        check("...the call fails: the table stays in English, nothing raised",
              asyncio.run(vocab_uz.uzbek_meanings(items)) == ["", ""])
        check("...no vocabulary: no call", asyncio.run(vocab_uz.uzbek_meanings([])) == [])
    finally:
        vocab_uz.client = real


def essay_in_the_question() -> None:
    """#152 (2026-11-10): the student sent the essay with the question, so every word of the answer was
    'copied from the question' - '0 words of your own', Band 1 on every criterion. That is an input mix-up,
    not copying."""
    g = writing_grader
    question = "Many countries import food from other countries.\n\nDo the advantages outweigh the disadvantages?"
    body = " ".join(["Importing food gives people a wider choice and keeps prices stable all year round."] * 20)
    both = question + "\n\n" + body
    bands = {"TR_or_TA": 8.0, "CC": 8.0, "LR": 8.0, "GRA": 8.0}
    check("the question holding the whole essay: nothing counts as copied", essay_marking.rubric_spans(
        both, both, essay_marking.copy_min_words("task2")) == [])
    capped, _, why, _ = g.cap_bands("task2", both, bands, {}, question=both)
    check("...so no Band 1 for it (#152)", capped == bands, why)
    spans = essay_marking.rubric_spans(question + "\n\n" + body, question, essay_marking.copy_min_words("task2"))
    check("a real copy of the question at the start: still found and not counted",
          len(spans) == 1 and "Do the advantages outweigh" in (question + "\n\n" + body)[spans[0][0]:spans[0][1]], spans)


def short_speaking_samples() -> None:
    """2026-11-11 (the teachers): one-question Speaking checks gave 8.5-9 speakers 6.0-7.0 - the official 9.0
    recording's answers alone came out 8.0 (156 words), 6.5 (85) and 6.0 (53), with no mistake at all."""
    from services.ai import speaking_marking
    from services.ai.speaking_grader import Evidence, SessionItem
    from services.ai.voice import VoiceMetrics

    short_text = ("Modern lifestyles negatively affect sleeping patterns in several ways. People often use "
                  "smartphones late at night, which delays sleep. As a result, many people develop irregular "
                  "sleeping habits.")
    short = [SessionItem(qid="Q1", part="part3", question="How do lifestyles affect sleep?", audio=_audio(short_text))]
    long_text = " ".join([short_text] * 6)
    long = [SessionItem(qid="Q1", part="part3", question="How do lifestyles affect sleep?", audio=_audio(long_text))]
    words = short[0].audio.word_count
    note = speaking_grader.short_sample_note("part3", short)
    check("a one-answer check is a short sample: its scoring runs are told so",
          note.startswith("SHORT SAMPLE: one answer") and f"{words} words" in note and "Part 3" in note, note[:80])
    check("...brevity is no Lexical Resource or Grammar reason", "Never lower Lexical Resource or Grammar" in note)
    check("...Part 1: 2-4 sentences is a complete answer",
          "2-4 sentences" in speaking_grader.short_sample_note("part1", [SessionItem(
              qid="Q1", part="part1", question="Do you sleep well?", audio=_audio(short_text))]))
    check("...not for 150 words or more, nor a full test",
          speaking_grader.short_sample_note("part3", long) == "" and speaking_grader.short_sample_note("full", short) == "")

    error = NS(wrong="a family time", correct="family time", criterion="LR", severity="error", unclear=False)
    check("the short-sample scoring rules are OFF (2026-11-11: they lifted the teachers' 6.5 and 7.0 checks to "
          "8.0 and 9.0)", not speaking_grader.short_scoring_on())
    ev_off = speaking_grader.mistake_evidence(short, [error], [], [], [])
    check("...off: a short answer's density is over its own words, as before",
          "short sample" not in ev_off and f"= {100 / words:.1f} weighted" in ev_off, ev_off)
    real_config = speaking_grader.speaking_config
    speaking_grader.speaking_config = lambda: {**real_config(), "short_sample": {"words": 150, "scoring_rules": True}}
    try:
        ev_short = speaking_grader.mistake_evidence(short, [error], [], [], [])
        ev_long = speaking_grader.mistake_evidence(long, [error], [], [], [])
    finally:
        speaking_grader.speaking_config = real_config
    check("...on: one mistake in a short answer - density over 150 words, not 2+ per 100 words",
          "counted over 150 words" in ev_short and "= 0.7 weighted per 100 words" in ev_short, ev_short)
    check("...on: a long sample keeps its own word count", "short sample" not in ev_long
          and f"= {100 / long[0].audio.word_count:.1f} weighted" in ev_long, ev_long)

    bands = {"FC": 7.0, "LR": 5.0, "GRA": 5.0, "P": 6.0}
    strong = ["visit new places", "good food", "with my family", "new places"]
    floored, raised = speaking_grader.speaking_floors(short, bands, [], strong)
    check("short sample: no band floor - no mistake is not a wide range ('It is fun. We eat good food.' stays 5)",
          floored == bands and not raised, (floored, raised))
    check("...the note: the linking counts for Fluency; brief but complete is never marked down; a supported "
          "view is developed", "the linking" in note and "never marked down for being brief" in note
          and "supported by a reason" in note)

    # 9 needs what 9 is: an 8 with no quoted weakness becomes 9 only with 2 quoted strengths (LR / GRA)
    def nine_run(strengths):
        async def call(purpose, max_tokens, messages, response_format, model=None):
            eight = schemas.SpeakingJudgement(strengths=strengths, weaknesses=[], band=8, note="n")
            return NS(choices=[NS(message=NS(parsed=schemas.SpeakingScoring(FC=eight, LR=eight, GRA=eight, P=8)))])
        return call

    m9 = VoiceMetrics(speaking_seconds=20, words=words, wpm=130, fillers=0, filler_types=[], long_pauses=0,
                      mid_sentence_pauses=0, longest_pause=None, longest_pause_answer=None)
    real = speaking_grader.parse_call
    try:
        speaking_grader.parse_call = nine_run(['"negatively affect sleeping patterns"'])
        one_quote, _ = asyncio.run(speaking_grader.score_session("part3", short, Evidence(m9, None, None)))
        speaking_grader.parse_call = nine_run(['"negatively affect sleeping patterns"', '"which delays sleep"'])
        two_quotes, _ = asyncio.run(speaking_grader.score_session("part3", short, Evidence(m9, None, None)))
        speaking_grader.parse_call = nine_run(['"varied vocabulary"', '"good grammar"'])  # quotes not said
        unsaid, _ = asyncio.run(speaking_grader.score_session("part3", short, Evidence(m9, None, None)))
    finally:
        speaking_grader.parse_call = real
    check("8 with no quoted weakness and one quoted strength: Lexical Resource and Grammar stay 8, Fluency is 9",
          one_quote["LR"] == 8 and one_quote["GRA"] == 8 and one_quote["FC"] == 9, one_quote)
    check("...with two strengths quoted from the answer: 9", two_quotes["LR"] == 9 and two_quotes["GRA"] == 9,
          two_quotes)
    check("...'quotes' the speaker never said are no evidence", unsaid["LR"] == 8 and unsaid["GRA"] == 8, unsaid)

    from services.ai.mistake_rules import small_slips
    age = [NS(wrong="the age", correct="age", criterion="GRA", severity="error", unclear=False) for _ in range(2)]
    small_slips(age)
    ev_age = speaking_grader.mistake_evidence(short, age, [], [], [])
    check("Writing's slip rules in Speaking: an article-only correction is a slip, the same slip twice counts once",
          all(m.severity == "slip" for m in age) and "0 errors, 1 slips" in ev_age and "2 times" in ev_age, ev_age)
    check("the scoring prompt carries the band ladders (impact on the listener); Pronunciation's only when measured",
          "DECIDING EACH BAND" in speaking_grader._scoring_system("part1", False)
          and "PRONUNCIATION\n9: a full range" not in speaking_grader._scoring_system("part1", False)
          and "PRONUNCIATION\n9: a full range" in speaking_grader._scoring_system("part1", True))

    caps, scored = [], []

    async def fake_call(purpose, max_tokens, messages, response_format, model=None):
        name = response_format.__name__
        if name == "SpeakingFeedback":
            caps.append(max_tokens)
            return NS(choices=[NS(message=NS(parsed=None))])
        scored.append(messages[-1]["content"])
        judged = schemas.SpeakingJudgement(strengths=[], weaknesses=[], band=9, note="n")
        return NS(choices=[NS(message=NS(parsed=schemas.SpeakingScoring(FC=judged, LR=judged, GRA=judged, P=9)))])

    metrics = VoiceMetrics(speaking_seconds=20, words=words, wpm=130, fillers=0, filler_types=[], long_pauses=0,
                           mid_sentence_pauses=0, longest_pause=None, longest_pause_answer=None)
    real = speaking_grader.parse_call
    speaking_grader.parse_call = fake_call
    try:
        for items in (short, long):
            try:
                asyncio.run(speaking_grader.feedback_call("part3", items, Evidence(metrics, None, None)))
            except RuntimeError:
                pass  # the fake feedback parses to nothing
        asyncio.run(speaking_grader.score_session("part3", short, Evidence(metrics, None, None)))
        speaking_grader.speaking_config = lambda: {**real_config(), "short_sample": {"words": 150,
                                                                                     "scoring_rules": True}}
        asyncio.run(speaking_grader.score_session("part3", short, Evidence(metrics, None, None)))
    finally:
        speaking_grader.parse_call = real
        speaking_grader.speaking_config = real_config
    check("a short sample's feedback is capped at 12k output tokens (a runaway costs half), a long one at 24k",
          caps == [speaking_grader.FEEDBACK_MAX_TOKENS_SHORT, speaking_grader.FEEDBACK_MAX_TOKENS], caps)
    check("...the scoring message has no short-sample note while the rules are off, and has it when on",
          len(scored) >= 2 and "SHORT SAMPLE" not in scored[0] and "SHORT SAMPLE" in scored[-1],
          [("SHORT SAMPLE" in s) for s in scored])

    # the report: the same mistake said twice is explained once (#42 "the age"); no mistakes below 9 points on
    text = "The biggest one is probably the age. It really depends on the age most of the time."
    items = [SessionItem(qid="Q1", part="part3", question="What films do people like?", audio=_audio(text))]

    def fb(mistakes):
        return schemas.SpeakingFeedback(
            answers=[schemas.AnswerMarking(id="Q1", segments=[schemas.AnswerSegment(type="text", text=text, fix=None,
                                                                                    mistake_id=None)])],
            mistakes=mistakes, criterion_notes=schemas.SpeakingCriterionNotes(FC="x", LR="x", GRA="x", P="x"),
            strength=schemas.Strength(text="Clear.", quoted_phrases=[]), priorities=[], vocabulary_topic="films",
            vocabulary=[schemas.VocabEntry(term=f"w{i}", meaning="m", example="e") for i in range(10)],
            checklist=[], cue_coverage=[])

    age = [schemas.SpeakingMistake(id=i, question="Q1", wrong="the age", correct="age", why="No article.",
                                   criterion="GRA", unclear=False, if_you_meant=None) for i in (1, 2)]
    scores = {"FC": 8.0, "LR": 8.0, "GRA": 7.0, "P": 8.0}
    m2 = VoiceMetrics(speaking_seconds=8, words=16, wpm=120, fillers=0, filler_types=[], long_pauses=0,
                      mid_sentence_pauses=0, longest_pause=None, longest_pause_answer=None)
    report = speaking_marking.build_report("part3", items, fb(age), [], set(), set(), scores, m2, None, None)
    rows = [(m["n"], m["same_as"]) for m in report.mistakes]
    check("the same mistake said twice: the second is 'the same as' the first", rows == [(1, None), (2, 1)], rows)

    clean = speaking_marking.build_report("part3", items, fb([]), [], set(), set(), scores, m2, None, None)
    try:
        from services.pdf import report_builder
        from services.ai.speaking_marking import build_progress
    except Exception:
        print("(no WeasyPrint here - the speaking PDF is rendered in the bot image)")
        return
    rendered, real_html = [], report_builder.HTML
    report_builder.HTML = lambda string, base_url=None: (rendered.append(string), real_html(string=string,
                                                                                            base_url=base_url))[1]
    try:
        for r in (report, clean):
            progress = build_progress("part3", r.overall_band, m2, None, r.priorities, None)
            report_builder.build_speaking_report_pdf(r, [], progress, "Test", False)
    finally:
        report_builder.HTML = real_html
    check("speaking PDF: a repeated mistake says 'the same mistake as 1'",
          "The same mistake as 1, said again." in rendered[0], len(rendered))
    check("...no mistakes below 9: the next band comes from the priorities",
          "No mistakes that need fixing" in rendered[1] and "Your next band comes from the priorities" in rendered[1])

    check("a short sample's report says the band is approximate (the user, 2026-11-11)",
          speaking_grader.short_sample_size("part3", short) == words
          and speaking_grader.short_sample_size("part3", long) is None
          and speaking_grader.short_sample_size("full", short) is None
          and f"({words} words), so it is approximate" in speaking_grader.SHORT_SAMPLE_NOTICE.format(words=words))
    from bot.i18n import t
    check("...and so does the bot's message, in every language",
          all(f"{words} " in t("speaking.short_sample", lang, words=words) and "Part 3" in
              t("speaking.short_sample", lang, words=words) for lang in ("uz", "ru", "en")))
    from bot.keyboards.speaking_part_kb import CB_FULL_TEST, speaking_part_kb
    datas = [b.callback_data for row in speaking_part_kb("uz").inline_keyboard for b in row]
    check("the Speaking menu has no Full test button any more", CB_FULL_TEST not in datas, datas)
    from bot.utils.quota import price_list
    check("...nor a full-test price", "Part 1+2+3" not in price_list("uz") and "{full}" not in price_list("ru"))


def speaking_azure_and_slips() -> None:
    """2026-11-11: Pronunciation from Azure's scores, calibrated on the teachers' six checks; a Lexical /
    Grammar band whose only reasons are slips is at least 8 (#42's Grammar 7 for "the age" - the teachers: 9)."""
    from services.ai.pronunciation import Assessment
    from services.ai.speaking_grader import Evidence, SessionItem, measured_pronunciation
    from services.ai.voice import VoiceMetrics

    def az(accuracy, prosody):
        return Assessment(seconds=30, accuracy=accuracy, fluency=85, completeness=97, prosody=prosody, pron=85,
                          words=[])

    # the teachers' Pronunciation for #38-43 and Azure's (accuracy, prosody) on the same answers
    measured = [(az(93, 78), 6), (az(94, 79), 6), (az(93, 73), 5), (az(93, 80), 6), (az(99, 86), 8), (az(99, 88), 8)]
    got = [measured_pronunciation(a) for a, _ in measured]
    check("Azure's scores give the teachers' Pronunciation on all six checks", got == [b for _, b in measured], got)
    check("...without prosody, accuracy alone", measured_pronunciation(az(99, None)) == 9.0)

    text = "I think the biggest one is probably the age, and older people prefer the prevalent genres."
    items = [SessionItem(qid="Q1", part="part3", question="What films do people like?", audio=_audio(text))]
    metrics = VoiceMetrics(speaking_seconds=8, words=16, wpm=120, fillers=0, filler_types=[], long_pauses=0,
                           mid_sentence_pauses=0, longest_pause=None, longest_pause_answer=None)
    slip = NS(wrong="the age", correct="age", criterion="LR", severity="slip", unclear=False)
    error = NS(wrong="prefer the prevalent", correct="prefer prevalent", criterion="GRA", severity="error",
               unclear=False)

    def run(weaknesses, checked, pron=None):
        async def call(purpose, max_tokens, messages, response_format, model=None):
            seven = schemas.SpeakingJudgement(strengths=[], weaknesses=weaknesses, band=7, note="n")
            return NS(choices=[NS(message=NS(parsed=schemas.SpeakingScoring(FC=seven, LR=seven, GRA=seven, P=7)))])
        real = speaking_grader.parse_call
        speaking_grader.parse_call = call
        try:
            return asyncio.run(speaking_grader.score_session("part3", items, Evidence(metrics, pron, None),
                                                             checked=checked))[0]
        finally:
            speaking_grader.parse_call = real

    only_slip = run(['Article slip: "the age" instead of "age"'], [slip])
    check("Grammar / Lexis 7 whose only reason is a slip: 8 (Fluency untouched)",
          only_slip["GRA"] == 8 and only_slip["LR"] == 8 and only_slip["FC"] == 7, only_slip)
    with_error = run(['Article slip: "the age" instead of "age"'], [slip, error])
    check("...a checked Grammar error keeps Grammar 7", with_error["GRA"] == 7 and with_error["LR"] == 8, with_error)
    unquoted = run(['Limited range of complex structures'], [slip])
    check("...an unquoted range limitation stands", unquoted["GRA"] == 7 and unquoted["LR"] == 7, unquoted)
    more = run(['"the age" and "older people prefer" are weak'], [slip])
    check("...a quote of more than the slip stands", more["GRA"] == 7, more)
    measured_p = run([], [], pron=az(99, 86))
    check("measured Pronunciation is Azure's calibrated band, not the runs' 7", measured_p["P"] == 8, measured_p)
    # Azure down: the check goes on (estimated band), the admins' group hears of it (the user, 2026-11-11)
    import io
    import logging
    import wave

    import httpx

    from services.ai import pronunciation

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)
    clip = (buf.getvalue(), [NS(text="hello", start=0.1, end=0.4), NS(text="world", start=0.5, end=0.9)], 1.0)
    records: list = []
    catch = logging.Handler()
    catch.emit = records.append
    log = logging.getLogger("services.ai.pronunciation")
    log.addHandler(catch)
    real_client, real_conf, real_endpoint = httpx.AsyncClient, pronunciation.configured, pronunciation._endpoint

    def azure_answers(response):
        httpx.AsyncClient = lambda **kw: real_client(transport=httpx.MockTransport(lambda request: response), **kw)
        records.clear()
        return asyncio.run(pronunciation.assess_answers([clip]))

    try:
        pronunciation.configured = lambda: True
        pronunciation._endpoint = lambda: "https://eastus.stt.speech.microsoft.com/v1"
        dead = azure_answers(httpx.Response(401, json={"error": "denied"}))
        errors = [r for r in records if r.levelno >= logging.ERROR]
        check("Azure refusing the key: no assessment (the band is estimated), and an ERROR for the group",
              dead is None and len(errors) == 1 and errors[0].getMessage().startswith(
                  "Azure Pronunciation is not working (HTTP 401: the key is wrong"), [r.getMessage() for r in records])
        from bot.utils.crm import error_card
        card = error_card(errors[0], 0, None) if errors else ""
        check("...the group's card heads it with 🚨 Azure (Pronunciation) ishlamayapti", "🚨 <b>Azure (Pronunciation) "
              "ishlamayapti!</b>" in card and "HTTP 401" in card, card[:200])
        azure_answers(httpx.Response(429, json={"error": "quota"}))
        check("...a used-up monthly limit says so", any("HTTP 429: too many requests, or the free tier's monthly "
                                                         "limit" in r.getMessage() for r in records))
        nothing = azure_answers(httpx.Response(200, json={"RecognitionStatus": "NoMatch"}))
        check("...no speech recognised is no outage: no ERROR", nothing is None
              and not any(r.levelno >= logging.ERROR for r in records), [r.getMessage() for r in records])
    finally:
        httpx.AsyncClient, pronunciation.configured, pronunciation._endpoint = real_client, real_conf, real_endpoint
        log.removeHandler(catch)

    check("the scoring guide: fillers are never lexical repetition; one developed view is a developed Part 3 answer",
          "never count them as lexical repetition" in speaking_grader._scoring_system("part3", False)
          and "several separate ideas are not required" in speaking_grader._scoring_system("part3", False))


def writing_label_repetition() -> None:
    """2026-11-12, #164 (a real Band 9 table report, Lexical 8): repeating the visual's own terms ("route",
    "passengers" in a table of route kilometres and passengers) is no lack of range in Task 1."""
    g = writing_grader
    prompt = "The table below gives information about the underground railway systems in six cities."
    sheet = "Table: Date opened | Kilometres of route | Passengers per year (in millions). London, Paris, Tokyo."
    report = ("The table compares six underground systems. London has 394 kilometres of route and Tokyo serves "
              "the most passengers, while the newest system, in Los Angeles, carries far fewer passengers. ") * 4
    given = g.given_words(prompt + "\n" + sheet)
    check("the visual's terms are given words", {"route", "passenger", "system"} <= given)
    check("...a remark quoting only them is about the task's own terms",
          g.only_given('Frequent reuse of high-frequency items: "system(s)", "route", "passengers"', given)
          and not g.only_given('"rose", "fell"', given) and not g.only_given("frequent reuse of words", given))

    def run(task, lr_quote, lr_weak=()):
        items = {"TR_or_TA": g.CHECKLISTS["TR_or_TA"][task], "CC": g.CHECKLISTS["CC"], "LR": g.CHECKLISTS["LR"],
                 "GRA": g.CHECKLISTS["GRA"]}

        def judgement(k, band):
            checklist = [schemas.CheckFinding(item=i, found=(k == "LR" and i.startswith("repetition")),
                                              quote=lr_quote if (k == "LR" and i.startswith("repetition")) else "")
                         for i in items[k]]
            return schemas.CriterionJudgement(checklist=checklist, strengths=[], band=band, note="n",
                                              weaknesses=list(lr_weak) if k == "LR" else [])
        scoring = schemas.WritingScoring(TR_or_TA=judgement("TR_or_TA", 9), CC=judgement("CC", 9),
                                         LR=judgement("LR", 8), GRA=judgement("GRA", 9), overview="clear")

        async def fake(purpose, max_tokens, messages, response_format, effort=None, model=None):
            return NS(choices=[NS(message=NS(parsed=scoring))])
        real = g._parse
        g._parse = fake
        try:
            return asyncio.run(g._score_once(task, prompt, report, None, sheet if task == "task1" else "",
                                             checked=[], wide=True))
        finally:
            g._parse = real

    labels = run("task1", 'Frequent reuse of high-frequency items: "system(s)", "route", "passengers"')
    check("Task 1: Lexical 8 held only by repeating the table's own terms is a 9", labels["LR"] == 9, labels)
    words = run("task1", '"compares", "serves", "carries"')
    check("...repetition of the writer's own words still holds it at 8", words["LR"] == 8, words)
    weak = run("task1", "", ['Some reliance on common nouns like "system" and "route" - repetition'])
    check("...a weakness about the labels' repetition is no reason either", weak["LR"] == 9, weak)
    task2 = run("task2", '"system", "route", "passengers"')
    check("...never in Task 2 (the question's words copied instead of paraphrased is a real limitation)",
          task2["LR"] == 8, task2)


def checklist_and_band9_samples() -> None:
    """2026-11-12: every checklist item is a required field (a run returned 4 of Lexical's 5 items and #164's 8
    could not become 9); Band 9 model answers take Band 9 samples as their references."""
    import numpy as np

    from services.ai.sample_bank import LibrarySample, choose_writer

    g = writing_grader
    schema = g.scoring_schema("task1")
    lr_fields = list(schema.model_fields["LR"].annotation.model_fields["checklist"].annotation.model_fields)
    check("the scoring answer has one required field per checklist item",
          lr_fields == [f"item{i + 1}" for i in range(len(g.CHECKLISTS["LR"]))]
          and len(schema.model_fields["TR_or_TA"].annotation.model_fields["checklist"].annotation.model_fields)
          == len(g.CHECKLISTS["TR_or_TA"]["task1"]), lr_fields)
    check("...named WritingScoring, as the calls know it", schema.__name__ == "WritingScoring")
    from openai.lib._pydantic import to_strict_json_schema

    loose = []

    def walk(node, path="$"):
        if isinstance(node, dict):
            if "properties" in node and node.get("additionalProperties") is not False:
                loose.append(path)
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
    for task in ("task1", "task2"):
        walk(to_strict_json_schema(g.scoring_schema(task)), task)
    check("...every object in its strict schema says additionalProperties: false (OpenAI refused it otherwise, "
          "2026-11-12)", not loose, loose[:4])

    def crit(key):
        items = g._checklist_items(key, "task1")
        return {"checklist": {f"item{i + 1}": {"found": i == 1 and key == "LR", "quote": "route" if i == 1 and
                                               key == "LR" else ""} for i in range(len(items))},
                "strengths": [], "weaknesses": [], "band": 8, "note": "n"}
    parsed = schema.model_validate({k: crit(k) for k in ("TR_or_TA", "CC", "LR", "GRA")} | {"overview": "clear"})
    scoring = g._as_scoring(parsed, "task1")
    lr = scoring.LR.checklist
    check("...read back as the checklist list with the items' own words, every item answered",
          [c.item for c in lr] == g.CHECKLISTS["LR"] and lr[1].found and lr[1].quote == "route"
          and all(len(getattr(scoring, k).checklist) == len(g._checklist_items(k, "task1"))
                  for k in ("TR_or_TA", "CC", "LR", "GRA")), [c.item for c in lr])
    check("...an answer already in the old form passes through", g._as_scoring(scoring, "task1") is scoring)

    def sample(i, author, band, vec):
        return LibrarySample(id=f"s{i}", author=author, book="b", task_type="task2", subtype="opinion",
                             topic="t", question=f"Question {i} about something?", band=band, word_count=300,
                             text="x", embedding=np.asarray(vec, dtype=float))
    library = [sample(1, "Close", 8.5, [1, 0]), sample(2, "Close", 8.5, [0.9, 0.1]), sample(3, "Close", 8.5, [1, 0.1]),
               sample(4, "Nine", 9.0, [0.2, 1]), sample(5, "Nine", 9.0, [0.3, 1]), sample(6, "Nine", 8.0, [0.9, 0.2])]
    q = np.asarray([1, 0], dtype=float)
    plain = choose_writer("A new question?", "task2", "opinion", q, library)
    nine = choose_writer("A new question?", "task2", "opinion", q, library, aim=9.0)
    eight = choose_writer("A new question?", "task2", "opinion", q, library, aim=8.0)
    check("without an aim: the closest topic's writer", plain.author == "Close", plain.author)
    check("a Band 9 answer: the writer with Band 9 samples, those samples first",
          nine.author == "Nine" and [s.band for s in nine.references[:2]] == [9.0, 9.0], (nine.author,
                                                                                          [s.id for s in nine.references]))
    check("...a Band 8 answer: any writer at 8+, so the closest topic again", eight.author == "Close", eight.author)


def per_criterion_range() -> None:
    """2026-11-12: an unexplained Lexical / Grammar 8 becomes 9 when the run itself found no range item - not only
    when another call marked 3 strong phrases (#164: no reason at all, held at 8 because 2 were marked that time).
    A run that finds "a narrow range of structures" or "everyday words throughout" still holds it (the teachers'
    128-word report of simple sentences)."""
    g = writing_grader
    essay = ("Public transport should be free in large cities, because it cuts traffic and pollution. " * 30).strip()

    def run(found_items=(), per_criterion=True):
        def judgement(k, band):
            items = g._checklist_items(k, "task2")
            return schemas.CriterionJudgement(
                checklist=[schemas.CheckFinding(item=i, found=i in found_items,
                                                quote="Public transport should be free" if i in found_items else "")
                           for i in items], strengths=[], weaknesses=[], band=band, note="n")
        scoring = schemas.WritingScoring(TR_or_TA=judgement("TR_or_TA", 7), CC=judgement("CC", 8),
                                         LR=judgement("LR", 8), GRA=judgement("GRA", 8))

        async def fake(purpose, max_tokens, messages, response_format, effort=None, model=None):
            return NS(choices=[NS(message=NS(parsed=scoring))])
        real, real_flag = g._parse, g.PER_CRITERION_WIDE
        g._parse, g.PER_CRITERION_WIDE = fake, per_criterion
        try:
            return asyncio.run(g._score_once("task2", "Should public transport be free?", essay, None, checked=[],
                                             wide=False))
        finally:
            g._parse, g.PER_CRITERION_WIDE = real, real_flag

    clean = run()
    check("Lexical and Grammar 8 with no reason and no range item found: 9, though 2 strong phrases (not wide)",
          clean["LR"] == 9 and clean["GRA"] == 9 and clean["CC"] == 8, clean)
    narrow = run(found_items=(g.RANGE_ITEMS["GRA"], g.RANGE_ITEMS["LR"]))
    check("...a run that finds a narrow range of structures / everyday words keeps them at 8",
          narrow["LR"] == 8 and narrow["GRA"] == 8, narrow)
    old = run(per_criterion=False)
    check("...switched off: the strong-phrase count alone decides, as before", old["LR"] == 8 and old["GRA"] == 8, old)


def task2_and_speaking_20261112() -> None:
    """2026-11-12: #166 (the teachers' 6.5-7.0 Task 2 essay) had Task Response 8 in three runs whose own reasons
    were Band 7's; #67 (a wide, idiomatic Part 2 talk) had Lexical 7 for two word choices off with their meaning
    clear; Part 2 model answers last at least 1:30."""
    from services.ai import speaking_answers
    from services.ai.speaking_grader import SessionItem

    g = writing_grader
    for reason in ("Benefits of hard work are acknowledged but not developed",
                   'One claim is overstrong without support: "cancer"', "One support is over-generalised",
                   '"they often work" (a main idea stated but not extended or supported)'):
        check(f"Task Response: a Band 7 reason - {reason[:40]}", g.band7_development([reason]))
    for reason in ("Depth could be further expanded", "Some points could be probed further",
                   "The conclusion is brief"):
        check(f"...a mild remark is no Band 7 reason - {reason}", not g.band7_development([reason]))

    essay = ("Some people work harder than others because they need money, and hard work brings rewards. " * 30)

    def run(task, weak):
        def judgement(k, band):
            items = g._checklist_items(k, task)
            return schemas.CriterionJudgement(checklist=[schemas.CheckFinding(item=i, found=False, quote="")
                                                         for i in items], strengths=[], band=band, note="n",
                                              weaknesses=list(weak) if k == "TR_or_TA" else [])
        scoring = schemas.WritingScoring(TR_or_TA=judgement("TR_or_TA", 8), CC=judgement("CC", 8),
                                         LR=judgement("LR", 7), GRA=judgement("GRA", 8))

        async def fake(purpose, max_tokens, messages, response_format, effort=None, model=None):
            return NS(choices=[NS(message=NS(parsed=scoring))])
        real = g._parse
        g._parse = fake
        try:
            return asyncio.run(g._score_once(task, "Why do some people work harder?", essay, None, checked=[],
                                             wide=True))
        finally:
            g._parse = real

    check("Task 2: Task Response 8 whose own reason is Band 7's is 7 (#166)",
          run("task2", ["Benefits are acknowledged but not developed"])["TR_or_TA"] == 7)
    check("...a mild remark keeps the 8", run("task2", ["Depth could be further expanded"])["TR_or_TA"] == 8)
    check("...never Task 1 (it works well, the user: don't touch it)",
          run("task1", ["Benefits are acknowledged but not developed"])["TR_or_TA"] == 8)

    talk = "I am a history buff and the pyramids mesmerize me. " * 25
    items = [SessionItem(qid="Q1", part="part2", question="Describe a historical period", audio=_audio(talk))]
    clear = NS(wrong="relive that experience", correct="witness it firsthand", criterion="LR", severity="error",
               unclear=False, clear_choice=True)
    evidence = speaking_grader.mistake_evidence(items, [clear], [], [], [])
    check("Speaking: a word choice off with its meaning clear weighs half and says so (#67)",
          "(word choice off, meaning clear)" in evidence and "1 errors" in evidence, evidence)
    check("...the scoring guide: it is one of Band 8's occasional inaccuracies",
          "occasional inaccuracies\" Band 8 allows" in speaking_grader._scoring_system("part2", False))

    check("Part 2 model answers last at least 1:30 (215+ words at 140 a minute)",
          speaking_answers.PART2_MIN_WORDS >= 210
          and speaking_answers.LENGTH_BOUNDS["part2"][0] == speaking_answers.PART2_MIN_WORDS
          and round(speaking_answers.PART2_MIN_WORDS / speaking_answers.WORDS_PER_MINUTE * 60) >= 90)
    target = NS(part="part2", student_answer="I like history. " * 50)  # 150 words of their own
    lo, hi = speaking_answers.length_for(target)
    check("...even when the student's own talk was short", lo >= speaking_answers.PART2_MIN_WORDS, (lo, hi))


def task1_labels_and_close_reads() -> None:
    """2026-11-12, #169 (a Band 9 report marked 7.5): the chart's own unit "per 1000-population" was four grammar
    errors, and two figures read off the scale as the same (21 and 23 both snapped to "about 22.5") made "a slight
    increase in all six groups" a Task mistake."""
    from services.ai.visual_reading import JUDGE_RULES

    g = writing_grader
    visual = {"blocks": [{
        "title": "Attendances per 1000-population by age group, December 2016 vs December 2017",
        "units": "Attendances per 1000-population", "axis_labels": ["0", "20", "40"], "time_points": [],
        "legend": [{"label": "December 2016"}, {"label": "December 2017"}],
        "values": [{"series": "December 2016", "point": "5-15"}, {"series": "December 2017", "point": "75+"}]}]}
    labels = g.visual_label_text(visual)
    check("Task 1: the visual's labels are its title, unit, axes, legend and categories",
          all(x in labels for x in ("Attendances per 1000-population", "December 2016", "75+", "20")), labels)
    check("...the chart's own unit, copied as printed, is no wording error (#169)",
          g.copies_visual_label("per 1000-population", labels)
          and g.copies_visual_label("Per 1000-Population.", labels))
    check("...whole words only: one word short of the label is still an error",
          not g.copies_visual_label("per 1000-populatio", labels)
          and not g.copies_visual_label("attendance per 1000", labels))
    check("...a few letters are no label", not g.copies_visual_label("per", labels))
    check("...no visual, no labels", not g.copies_visual_label("per 1000-population", ""))
    rule = " ".join(JUDGE_RULES.split())
    check("the judge: two figures read off the scale as the same are too close to tell apart, not equal",
          "too close to tell apart, not equal" in rule and "PRINTS them equal" in rule
          and "is never a mistake" in rule, rule[-600:])


def model_answer_bands() -> None:
    """The user's rule (2026-11-12), Writing and Speaking alike: 5.0-6.5 -> a Band 7 model answer, 7.0-7.5 ->
    Band 8, 8.0-9.0 -> Band 9 only."""
    from services.ai.sample_bank import Needs as WritingNeeds
    from services.ai.scoring import aim_band, writing_aim_band
    from services.ai.speaking_answers import Needs as SpeakingNeeds

    want = {4.0: 7.0, 5.0: 7.0, 5.5: 7.0, 6.0: 7.0, 6.5: 7.0, 7.0: 8.0, 7.5: 8.0, 8.0: 9.0, 8.5: 9.0, 9.0: 9.0}
    got = {o: aim_band(o) for o in want}
    check("model answers: 5.0-6.5 -> 7.0, 7.0-7.5 -> 8.0, 8.0-9.0 -> 9.0", got == want, got)
    check("...Writing and Speaking alike", all(writing_aim_band(o) == b for o, b in want.items())
          and WritingNeeds(overall=6.0, bands={}, priorities=[]).aim == 7.0
          and SpeakingNeeds(overall=7.5, bands={}, priorities=[]).aim == 8.0)


if __name__ == "__main__":
    writing()
    round2()
    fair_marking()
    speaking()
    rounding()
    examiner_rules()
    official_descriptors()
    paid_run_fixes()
    speed_fixes()
    uzbek_vocabulary()
    essay_in_the_question()
    short_speaking_samples()
    speaking_azure_and_slips()
    model_answer_bands()
    writing_label_repetition()
    checklist_and_band9_samples()
    per_criterion_range()
    task2_and_speaking_20261112()
    task1_labels_and_close_reads()
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    sys.exit(1 if fails else 0)
