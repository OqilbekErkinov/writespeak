"""services/ai/answer_relevance.py (the owner, 2026-11-18): an answer to another question is not marked - but only
when both readings say so; a real answer, a failure or one doubtful reading lets the check go on. Offline: a fake
OpenAI client.
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

fails: list[str] = []


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


Q197 = ("Nowadays more and more people have to compete with young people for the same jobs. What problems does this "
        "cause? What are some possible solutions?")
A197 = ("In contemporary society, legislation prohibiting employers from discriminating against job applicants based "
        "on age has become increasingly common. I firmly believe that this legal protection is a positive development.")


async def main() -> int:
    from services.ai import answer_relevance as ar

    sent: list[dict] = []
    verdicts: list = []

    class FakeCompletions:
        async def parse(self, **kw):
            sent.append(kw)
            v = verdicts.pop(0)
            if isinstance(v, Exception):
                raise v
            parsed = ar.Relevance(asks="problems and solutions of competing with the young for jobs",
                                  addresses="whether anti-age-discrimination laws are positive", verdict=v)
            return NS(choices=[NS(message=NS(parsed=parsed))], usage=None, model="gpt-5")

    ar.client = NS(beta=NS(chat=NS(completions=FakeCompletions())))
    ar.usage.record = lambda *a, **k: None

    verdicts[:] = ["other_question", "other_question"]
    why = await ar.off_question("task2", Q197, A197)
    check("both readings: another question -> refused, with what it asks and what the essay argues",
          why is not None and why.startswith("other_question") and "asks:" in why and "addresses:" in why, why)
    check("...two readings, the question and the essay in each, on the grading model",
          len(sent) == 2 and all(Q197 in m["messages"][1]["content"] and A197 in m["messages"][1]["content"]
                                 and m["model"] == ar.settings.openai_model_grading for m in sent))
    verdicts[:] = ["other_question", "answers"]
    check("one reading says it answers -> marked as usual", await ar.off_question("task2", Q197, A197) is None)
    verdicts[:] = ["unrelated", "other_question"]
    check("unrelated + another question -> refused", (await ar.off_question("task2", Q197, A197) or "")
          .startswith("unrelated"))
    verdicts[:] = [RuntimeError("down"), "other_question"]
    check("a reading fails -> counted as 'answers', marked as usual", await ar.off_question("task2", Q197, A197) is None)
    verdicts[:] = []
    check("no question or no answer -> nothing asked", await ar.off_question("task2", "", A197) is None
          and await ar.off_question("task2", Q197, "  ") is None)
    sent.clear()
    verdicts[:] = ["answers", "answers"]
    await ar.off_question("task1", "The chart shows sales.", "Sales rose.", sheet="Sales 2010: 5; 2020: 9")
    check("Task 1: the chart's sheet goes with the question", "Sales 2010: 5" in sent[0]["messages"][1]["content"]
          and "Task 1" in sent[0]["messages"][1]["content"])
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
