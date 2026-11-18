"""2026-11-18: the panel's dashboard went down ("Internal Server Error") over one Task 1 chart title the vision model
wrote as "Caf\\u0000e9" - Postgres keeps a NUL in a json column but fails every ->> on that row. db/models.py's
JSON now cleans NULs on the way in. Offline, a throwaway Postgres (run_test.sh).
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

fails: list[str] = []


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


async def main() -> int:
    from sqlalchemy import text, update

    from db.database import get_session
    from db.models import SourceType, TaskType, User, UserStatus, WritingSubmission, no_nul

    check("no_nul: a broken escape becomes its character, any other NUL goes, keys and lists too",
          no_nul({"t\x00": ["Caf\x00e9", "a\x00b", 3, None], "n": {"x": "ok"}})
          == {"t": ["Café", "ab", 3, None], "n": {"x": "ok"}})
    check("...text without a NUL is left as it is", no_nul("Café 40% — ok") == "Café 40% — ok")

    uid = 9_600_001
    async with get_session() as s:
        s.add(User(id=uid, full_name="Nul", coin_balance=8, language="uz", status=UserStatus.approved))
        await s.commit()
        sub = WritingSubmission(user_id=uid, task_type=TaskType.task1, prompt_text="Q", prompt_source=SourceType.text,
                                answer_text="A", answer_source=SourceType.text, overall_band=6.0,
                                criteria_scores={"TA": 6.0}, annotations=[{"note": "x\x00"}],
                                report_data={"visual": {"blocks": [{"title": "Caf\x00e9 before vs Caf\x00e9 today"}]},
                                             "cost": {"usd": 0.12}})
        s.add(sub)
        await s.commit()
        await s.refresh(sub)
        title = (await s.execute(text("select report_data->'visual'->'blocks'->0->>'title' from writing_submissions "
                                      "where id = :i"), {"i": sub.id})).scalar()
        check("saved through the ORM: Postgres reads the chart title, 'Café', no NUL", title == "Café before vs Café today",
              title)
        await s.execute(update(WritingSubmission).where(WritingSubmission.id == sub.id)
                        .values(report_data={"visual": {"blocks": [{"title": "Plan\x00"}]}}))
        await s.commit()
        title = (await s.execute(text("select report_data->'visual'->'blocks'->0->>'title' from writing_submissions "
                                      "where id = :i"), {"i": sub.id})).scalar()
        check("...and through a Core update", title == "Plan", title)
        back = (await s.execute(text("select report_data::text from writing_submissions where id = :i"),
                                {"i": sub.id})).scalar()
        check("...nothing of \\u0000 kept", "\\u0000" not in back, back)

    from webapp import insights

    try:
        async with get_session() as s:
            ok = isinstance(await insights.dashboard(s), dict)
    except Exception as e:  # noqa: BLE001
        ok = repr(e)
    check("the panel's dashboard opens", ok is True, ok)
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    from bot.utils.crm import redis

    await redis().aclose()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
