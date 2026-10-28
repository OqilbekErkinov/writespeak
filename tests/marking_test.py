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
    out, raised = writing_grader.floors("task2", essay, bands, [slip, error], strong)
    check("floors: clear, less common vocabulary - Lexical Resource at least 7 (#65's LR 5)", out["LR"] == 7, raised)
    check("...only slips in Grammar and a wide range - Grammar at least 8 (#67)", out["GRA"] == 8, raised)
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


if __name__ == "__main__":
    writing()
    speaking()
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    sys.exit(1 if fails else 0)
