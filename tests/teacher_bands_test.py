"""Teachers' bands on checks (bot/utils/teacher_bands.py, the user 2026-11-14): a "teacher" login in the web
panel sees only the checks page - no bot band, no PDF, no users or payments - and enters its own bands; the
admins see them against the bot's; the weekly report sets them side by side; teacher-marked Speaking checks
become the scoring runs' anchors. Offline - a throwaway Postgres and Redis (tests run by test_part2.sh):

    python tests/teacher_bands_test.py
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from bot.config import settings  # noqa: E402
from bot.utils import crm_events, teacher_bands  # noqa: E402
from db.database import get_session  # noqa: E402
from db.models import SpeakingSubmission, User, WebAdmin, WritingSubmission  # noqa: E402
from services.ai import speaking_grader  # noqa: E402

fails: list[str] = []


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


def units() -> None:
    marks = teacher_bands.record("s", {"FC": "7", "LR": "8", "GRA": "8", "P": "6"}, "dilnoza", " clear ")
    check("a teacher's marks: the bands, the overall rounded down (7.25 -> 7.0), who and when",
          marks["bands"] == {"FC": 7, "LR": 8, "GRA": 8, "P": 6} and marks["overall"] == 7.0
          and marks["by"] == "dilnoza" and marks["note"] == "clear" and marks["at"], marks)
    check("...6,5 with a comma is 6.5", teacher_bands.record("w", {"TA": "6,5", "CC": "7", "LR": "7", "GRA": "7"},
                                                             "x")["bands"]["TA"] == 6.5)
    check("...all empty: the marks taken off", teacher_bands.record("w", {}, "x") is None)
    for bad in ({"TA": "7", "CC": "7", "LR": "7"}, {"TA": "10", "CC": "7", "LR": "7", "GRA": "7"},
                {"TA": "6.3", "CC": "7", "LR": "7", "GRA": "7"}, {"TA": "a", "CC": "7", "LR": "7", "GRA": "7"}):
        try:
            teacher_bands.record("w", bad, "x")
            check(f"...refused: {bad}", False)
        except ValueError:
            check(f"...refused: {bad}", True)
    diff = teacher_bands.difference("s", 7.0, {"FC": 8, "LR": 7, "GRA": 9, "Pronunciation": 5},
                                    {"bands": {"FC": 7, "LR": 8, "GRA": 8, "P": 6}, "overall": 7.0})
    check("the bot against the teacher: overall and each criterion (Pronunciation as P)",
          diff == {"overall": 0.0, "criteria": {"FC": 1, "LR": -1, "GRA": 1, "P": -1}}, diff)
    rows = [{"kind": "s", "id": 79, "overall": 7.0, "criteria_scores": {"FC": 7, "LR": 7, "GRA": 7, "Pronunciation": 8},
             "teacher": {"bands": {"FC": 8, "LR": 9, "GRA": 8, "P": 9}, "overall": 8.5}},
            {"kind": "w", "id": 171, "overall": 8.5, "criteria_scores": {"TA": 8, "CC": 8, "LR": 9, "GRA": 9},
             "teacher": {"bands": {"TA": 9, "CC": 9, "LR": 9, "GRA": 9}, "overall": 9.0}}]
    s = teacher_bands.summary(rows)
    check("the summary: mean -1.0, one check a band or more apart, per criterion",
          s["n"] == 2 and s["mean"] == -1.0 and s["within_half"] == 1 and len(s["far"]) == 1
          and s["criteria"]["S LR"] == -2 and s["criteria"]["W TA"] == -1, s)

    # anchors: the file's teacher checks, a later mark over the file's, more from the database by band
    file = {"real9-p3-travel": {"part": "part3", "band": 9.0, "label": "Band 9.0 (official)",
                                "answers": [{"question": "Q", "transcript": "official"}]},
            "teachers-s79": {"part": "part3", "band": 8.5, "label": "Teachers' band 8.5", "answers": [
                {"question": "Q", "transcript": "file version"}], "bands": {"FC": 8, "LR": 9, "GRA": 8, "P": 9}}}

    def db(sid, band):
        return f"teachers-s{sid}", {"part": "part1", "band": band, "label": f"Teachers' band {band:g}",
                                    "answers": [{"question": "Q", "transcript": f"db {sid}"}],
                                    "bands": {"FC": 6, "LR": 6, "GRA": 6, "P": 6}}

    items = dict([db(79, 8.0), *[db(100 + i, 5.0 + (i % 4)) for i in range(30)]])
    real = (speaking_grader._load_json, speaking_grader.speaking_config, dict(speaking_grader._teacher_anchors),
            speaking_grader.EXCLUDED_ANCHORS)
    try:
        speaking_grader._load_json = lambda path: file
        speaking_grader.speaking_config = lambda: {"anchors": {"ids": list(file)}}
        speaking_grader._teacher_anchors.update(items=items)
        speaking_grader.EXCLUDED_ANCHORS = {"teachers-s105"}
        text = speaking_grader.anchors_text()
    finally:
        speaking_grader._load_json, speaking_grader.speaking_config = real[0], real[1]
        speaking_grader._teacher_anchors.clear()
        speaking_grader._teacher_anchors.update(real[2])
        speaking_grader.EXCLUDED_ANCHORS = real[3]
    shown = text.count("Teachers' band")
    check("anchors: the teachers' at most 20, the official one too", shown == speaking_grader.TEACHER_ANCHORS_MAX
          and "Band 9.0 (official)" in text, shown)
    check("...a teacher's later mark replaces the file's", "db 79" in text and "file version" not in text)
    check("...every band shown (5, 6, 7, 8), an excluded check never", all(f"Teachers' band {b} " in text
                                                                            for b in ("5", "6", "7", "8"))
          and "db 105" not in text)


async def web_and_db() -> None:
    from webapp import panel
    from webapp.main import app

    async with get_session() as session:
        session.add(User(id=777000111, full_name="Test Student"))
        await session.flush()
        w = WritingSubmission(user_id=777000111, task_type="task2", prompt_text="Some people think...",
                              prompt_source="text", answer_text="I agree that " * 40, answer_source="text",
                              overall_band=7.5, criteria_scores={"TA": 8, "CC": 7, "LR": 8, "GRA": 7})
        s = SpeakingSubmission(user_id=777000111, part="part3", question_text="Why?", audio_file_path="/nope.mp3",
                               overall_band=7.0, criteria_scores={"FC": 7, "LR": 7, "GRA": 7, "Pronunciation": 8},
                               report_data={"questions": ["Why is it hard?"], "transcripts": ["It depends on people."]})
        session.add_all([w, s])
        session.add(WebAdmin(username="ustoz1", name="Ustoz", password_hash=panel.hash_password("pw-ustoz"),
                             role="teacher"))
        await session.commit()
        wid, sid = w.id, s.id

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as teacher:
        r = await teacher.post("/login", data={"username": "ustoz1", "password": "pw-ustoz"})
        check("a teacher signs in", r.status_code == 303, r.status_code)
        for path in ("/", "/users", "/payments", "/settings", f"/reports/w/{wid}.pdf"):
            r = await teacher.get(path)
            check(f"...{path} is closed to a teacher: back to the checks page",
                  r.status_code == 303 and r.headers.get("location") == "/checks", (r.status_code, r.headers))
        r = await teacher.get("/checks")
        page = r.text
        check("...the checks page opens, marked blind: no bot band, no PDF, no costs",
              r.status_code == 200 and "Baholash" in page and f"/checks/w/{wid}/answer" in page
              and f"/audio/s/{sid}" in page and ".pdf" not in page and "AI xarajat" not in page
              and "band-chip hi" not in page, r.status_code)
        r = await teacher.get(f"/checks/w/{wid}/answer")
        check("...the essay and its question, without the bot's band", r.status_code == 200
              and "Some people think" in r.text and "TA 8" not in r.text and "band-chip hi" not in r.text,
              r.status_code)
        r = await teacher.post(f"/checks/w/{wid}/teacher", data={"TA": "7", "CC": "7", "LR": "7", "GRA": "6.5",
                                                                  "back": "/checks?kind=w"})
        check("...its bands saved", r.status_code == 303 and "done=saved" in r.headers.get("location", ""), r.headers)
        r = await teacher.post(f"/checks/s/{sid}/teacher", data={"FC": "8", "LR": "9", "GRA": "8", "P": "9"})
        r2 = await teacher.post(f"/checks/s/{sid}/teacher", data={"FC": "8", "LR": "12", "GRA": "8", "P": "9"})
        check("...a band past 9 refused, the saved one kept", "done=bad" in r2.headers.get("location", ""), r2.headers)

    async with get_session() as session:
        w = await session.get(WritingSubmission, wid)
        s = await session.get(SpeakingSubmission, sid)
        check("the marks on the checks", w.teacher_bands["bands"] == {"TA": 7, "CC": 7, "LR": 7, "GRA": 6.5}
              and w.teacher_bands["overall"] == 6.5 and w.teacher_bands["by"] == "ustoz1"
              and s.teacher_bands["overall"] == 8.5, (w.teacher_bands, s.teacher_bands))

    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as owner:
        await owner.post("/login", data={"username": settings.web_admin_username,
                                         "password": settings.web_admin_password})
        r = await owner.get("/checks?tb=1")
        check("the admins: only the marked checks, the bot against the teacher", r.status_code == 200
              and "Ustoz baholagan 2 ta" in r.text and "bot +1.0" in r.text and "bot -1.5" in r.text, r.status_code)

    report = await crm_events.teacher_report(7)
    check("the weekly report: 2 checks, the mean, the one 1.5 apart", "2</b> ta tekshiruvni" in report
          and "S#" in report and "-1.5" in report, report)

    speaking_grader._teacher_anchors.update(at=-1e9, items={})
    await speaking_grader.refresh_teacher_anchors()
    got = speaking_grader._teacher_anchors["items"].get(f"teachers-s{sid}")
    check("a teacher-marked Speaking check becomes an anchor", got is not None and got["band"] == 8.5
          and got["bands"] == {"FC": 8, "LR": 9, "GRA": 8, "P": 9}
          and got["answers"] == [{"question": "Why is it hard?", "transcript": "It depends on people."}], got)

    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as teacher:
        await teacher.post("/login", data={"username": "ustoz1", "password": "pw-ustoz"})
        r = await teacher.post(f"/checks/w/{wid}/teacher", data={"TA": "", "CC": "", "LR": "", "GRA": ""})
    async with get_session() as session:
        w = await session.get(WritingSubmission, wid)
        check("...all empty: the marks taken off", "done=cleared" in r.headers.get("location", "")
              and not w.teacher_bands, w.teacher_bands)
    check("...and the report no longer counts it", "1</b> ta tekshiruvni" in await crm_events.teacher_report(7))

    async with get_session() as session:
        for obj in (await session.get(WritingSubmission, wid), await session.get(SpeakingSubmission, sid)):
            await session.delete(obj)
        await session.commit()


if __name__ == "__main__":
    units()
    asyncio.run(web_and_db())
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    sys.exit(1 if fails else 0)
