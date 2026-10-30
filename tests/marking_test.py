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
    errors = [NS(criterion="LR", severity="error", unclear=False, wrong=f"word {i}", correct=f"term {i}")
              for i in range(4)]  # 4 in 360 words: past the Band 8 density, within Band 7's
    out, raised = writing_grader.floors("task2", essay, bands, [slip] + errors, strong)
    check("floors: clear, less common vocabulary - Lexical Resource at least 7 (#65's LR 5)", out["LR"] == 7, raised)
    check("...only slips in Grammar and a wide range - Grammar at least 8 (#67)", out["GRA"] == 8, raised)
    one, raised = writing_grader.floors("task2", essay, bands, [slip, error], strong)
    check("...one error in 360 words is within the Band 8 density: Lexical Resource at least 8", one["LR"] == 8,
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
            gra = schemas.SpeakingJudgement(strengths=[], band=8, note="Minor slips.",
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
    check("an 8 with no quoted reason, checklist answered and clear, is a 9", run(judged(8))["CC"] == 9)
    check("...but an 8 whose checklist was never run stays 8 ('didn't look' is no 9)",
          run(judged(8, looked=False))["CC"] == 8)
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
    essay67 = " ".join(["word"] * 374)
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
          not writing_grader.evidenced(['Minor register lapse: "had the upper hand"'], text74, [], []))
    check("...a quote of a checked error is", writing_grader.evidenced(['"13% men and 9% women" lacks a preposition'
                                                                        .replace("lacks", "has no")], text74, [],
                                                                       ["at only 13% men and 9% women"]))
    check("...a named repetition still is", writing_grader.evidenced(['"predominated" is repeated'], text74, [], []))
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


if __name__ == "__main__":
    writing()
    round2()
    speaking()
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    sys.exit(1 if fails else 0)
