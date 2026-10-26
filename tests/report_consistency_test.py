"""The Writing report agrees with itself (the last prompt's package B,
2026-10-26) - offline, no OpenAI, Telegram or database:

    python tests/report_consistency_test.py

#41's report kept a priority and a checklist line built on "a renewed
interest" after the filter dropped that "mistake" (it is correct English);
#41-#43 bolded only the first phrase "What you did well" quoted; a model
answer aimed at 8.0 but scored 7.0 still said "Aimed at 8.0"; a model answer's
thesis read "the benefits are far outweighed by the advantages".
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import weasyprint  # noqa: F401
except ImportError:  # only the HTML is checked here; the PDF is task1_images_test's
    sys.modules["weasyprint"] = types.SimpleNamespace(HTML=None)

from services.ai import essay_marking, sample_bank, schemas  # noqa: E402
from services.pdf import report_builder  # noqa: E402

fails: list[str] = []


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


ESSAY = ("Many people believe that the internet has changed our lives. Others' views differ greatly. People who "
         "lives in cities enjoys faster services. There has been a renewed interest in reading.")


def seg(kind, text, mistake_id=None, fix=None):
    return schemas.EssaySegment(type=kind, text=text, fix=fix, mistake_id=mistake_id)


def priority(kind, title, wrong, right, ids, criteria=("GRA",)):
    return schemas.Priority(type=kind, title=title, rule="A rule.", example_wrong=wrong, example_right=right,
                            criteria=list(criteria), mistake_ids=ids)


def feedback(priorities, checklist, quoted, strength_text):
    mistake = schemas.Mistake
    return schemas.WritingFeedback(
        mistakes=[
            mistake(id=1, wrong="who lives", correct="who live", why="Plural subject.", criterion="GRA",
                    unclear=False, if_you_meant=None),
            mistake(id=2, wrong="enjoys", correct="enjoy", why="Plural subject.", criterion="GRA", unclear=False,
                    if_you_meant=None),
            mistake(id=3, wrong="a renewed interest", correct="renewed interest", why="No article.",
                    criterion="GRA", unclear=False, if_you_meant=None),
        ],
        essay=[[
            seg("text", "Many people believe that the internet has changed our lives. Others' views differ greatly. "
                        "People "),
            seg("error", "who lives", 1, "who live"),
            seg("text", " in cities "),
            seg("error", "enjoys", 2, "enjoy"),
            seg("text", " faster services. There has been "),
            seg("error", "a renewed interest", 3, "renewed interest"),
            seg("text", " in reading."),
        ]],
        criterion_notes=schemas.CriterionNotes(TR_or_TA="a", CC="b", LR="c", GRA="d"),
        strength=schemas.Strength(text=strength_text, quoted_phrases=quoted),
        priorities=priorities,
        vocabulary=[schemas.VocabEntry(term=f"t{i}", meaning="m", example="e") for i in range(10)],
        checklist=[schemas.ChecklistItem(text=text, ref=ref) for text, ref in checklist],
    )


def main() -> int:
    # B2-B3: a priority on a dropped mistake goes; the rest are renumbered and
    # the checklist follows them.
    fb = feedback(
        [priority("fix", "Articles", "a renewed interest", "renewed interest", [3]),
         priority("fix", "Plural verbs", "who lives", "who live", [1, 2]),
         priority("tip", "Develop each idea", "", "For example, online banking saves hours.", [], ["TR"])],
        [("I check my articles.", "Priority 01"), ("My verbs agree.", "Priority 02"),
         ("I develop every idea.", "Priority 03"), ("Renewed interest takes no article.", "Mistake 3"),
         ("Plural subjects take plural verbs.", "Mistake 1"), ("I reread my answer.", "Mistake 9")],
        ["Others' views", "greatly"], "You use “Others’ views” and “greatly” well.")
    trimmed, _ = essay_marking.drop_priorities(fb, {3}, ["who lives", "enjoys"])
    check("drop_priorities: the priority on the dropped mistake goes",
          [p.title for p in trimmed.priorities] == ["Plural verbs", "Develop each idea"], trimmed.priorities)
    check("drop_priorities: the checklist follows the new numbers",
          [(c.text, c.ref) for c in trimmed.checklist][:2]
          == [("My verbs agree.", "Priority 01"), ("I develop every idea.", "Priority 02")], trimmed.checklist)
    mixed = feedback([priority("fix", "Articles", "a renewed interest", "renewed interest", [1, 3])], [], [], "x")
    check("drop_priorities: an example outside every kept mistake goes",
          not essay_marking.drop_priorities(mixed, {3}, ["who lives", "enjoys"])[0].priorities)
    untouched = feedback([priority("fix", "Plural verbs", "who lives", "who live", [1, 2])], [], [], "x")
    check("drop_priorities: nothing dropped, nothing changed",
          essay_marking.drop_priorities(untouched, {3}, ["who lives", "enjoys"])[0] is untouched)

    report = essay_marking.build_report(
        "task2", "Some people think the internet has improved our lives. Discuss.", ESSAY, fb,
        bands={"TR_or_TA": 6, "CC": 6, "LR": 6, "GRA": 6}, dropped={3})
    check("report: two priorities, numbered 01 and 02",
          [(p["n"], p["title"]) for p in report.priorities] == [("01", "Plural verbs"), ("02", "Develop each idea")],
          report.priorities)
    tip = report.priorities[1]
    check("report: the tip shows a rule and a good example only",
          tip["tip"] and tip["ex_wrong"] == "" and tip["ids"] == [] and tip["ex_right"].startswith("For example"), tip)
    check("report: a fix lists its kept mistakes", report.priorities[0]["ids"] == [1, 2], report.priorities[0])
    check("report: checklist lines on dropped items go, the rest re-pointed",
          [c["ref"] for c in report.checklist] == ["Priority 01", "Priority 02", "Mistake 1", ""], report.checklist)
    check("report: the dropped mistake is not listed",
          [m["wrong"] for m in report.mistakes] == ["who lives", "enjoys"], report.mistakes)

    # B4: every quoted phrase - one word too, either apostrophe - is green and bold.
    green = "".join(report.essay_paragraphs)
    check("report: the quoted phrase with an apostrophe is green",
          '<span class="good">Others\' views</span>' in green, green)
    check("report: the one-word quoted phrase is green", '<span class="good">greatly</span>' in green, green)
    check("strength: both quoted phrases bold (curly apostrophe in the praise)",
          report.strength_html.count("<b>") == 2 and "Others’ views" in report.strength_html,
          report.strength_html)
    check("strength: a one-word phrase is not found inside another word",
          not essay_marking._inside("art", "start here"))

    # B1: fewer than 3 priorities is not a problem; 0 is allowed.
    none = feedback([], [("My verbs agree.", "Mistake 1")] * 5, ["greatly"], "You write \"greatly\".")
    check("assess: no priorities is not a problem",
          not any("priorit" in p for p in essay_marking.assess(none, ESSAY)), essay_marking.assess(none, ESSAY))

    # B6: a thesis that weighs a side against itself.
    contra = sample_bank.self_contradictions
    check("thesis: 'benefits ... outweighed by the advantages' is caught",
          len(contra("In my view, the benefits are far outweighed by the advantages.")) == 1)
    check("thesis: 'drawbacks ... outweighed by the risks' is caught",
          len(contra("The drawbacks are outweighed by the risks it brings.")) == 1)
    check("thesis: 'advantages outweigh its drawbacks' is fine",
          not contra("I believe the advantages of remote work outweigh its drawbacks."))
    check("thesis: two groups' benefits compared is fine",
          not contra("The benefits for employers outweigh the benefits for workers."))

    # B5 and the template: the badge, one priority, the footer kept with the answer.
    pages = []

    class FakeHTML:
        def __init__(self, string, base_url):
            pages.append(string)

        def render(self):
            return types.SimpleNamespace(write_pdf=lambda: b"%PDF")

    report_builder.HTML = FakeHTML
    report_builder._page_of = lambda document, anchor: 1
    progress = {"first": True, "note": "First."}

    def page(score, band, report_=report):
        answer = sample_bank.ModelAnswer(question="Q", paragraphs=["One.", "Two."], band=band, score=score)
        pages.clear()
        report_builder.build_writing_report_pdf(report_, answer, progress, "Test", 1)
        return pages[0]

    low = page(7.0, 8.0)
    check("badge: scored below its aim shows its own score", ">Scored<" in low and "<b>7.0</b>" in low
          and "Aimed at" not in low)
    high = page(8.5, 8.0)
    check("badge: reaching its aim shows 'Aimed at'", ">Aimed at<" in high and "<b>8.0</b>" in high)
    check("badge: no score yet shows 'Aimed at'", ">Aimed at<" in page(None, 8.0))
    check("template: the model answer's notes are kept together", 'class="sample-foot"' in high)
    check("template: a tip is labelled", "Next-band tip" in high)
    one = essay_marking.build_report("task2", "Q. Discuss.", ESSAY, untouched,
                                     bands={"TR_or_TA": 6, "CC": 6, "LR": 6, "GRA": 6})
    check("template: one priority reads 'Your priority'", "Your priority for Band" in page(8.0, 8.0, one))
    short = report_builder.build_progress(report, {"too_short": True})
    check("progress: earlier answers too short to compare with are not 'your first essay'",
          short["first"] and "too short" in short["note"] and "first" not in short["note"], short)
    check("progress: no earlier answer at all is still 'your first essay'",
          "first essay" in report_builder.build_progress(report, None)["note"])
    css = (report_builder.TEMPLATE_DIR / "report_styles.css").read_text(encoding="utf-8")
    check("css: the footer never starts a page alone", ".sample-foot { page-break-before: avoid" in css)

    print(f"\nFAILURES: {', '.join(fails) or 'none'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
