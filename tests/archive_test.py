"""2026-11-16, the owner's request: an archive of every check in the admin panel (webapp/archive.py) - two lists,
Writing and Speaking, with filters, and a page per check with everything inside it. Seeded checks of every shape
(a full one, an old one saved before report_data, a partial one, a malformed one, a test one, 2000 bulk ones),
real files under FILE_STORAGE_PATH (a chart, an answer photo, a PDF, a recording) and some gone after the month.
Offline: a throwaway Postgres + Redis (run_test.sh), Telegram a fake. With an argument, the pages are also saved
there as HTML (to look at in a browser):

    python tests/archive_test.py [out_dir]
"""
import asyncio
import io
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from bot.config import settings  # noqa: E402

settings.web_2fa = False  # signs in with the password alone: the two-step sign-in is tests/panel_access_test.py

fails: list[str] = []
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else None


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


NOW = datetime.now(timezone.utc)
D = timedelta(days=1)
U1, U2, U3, U9 = 9_200_001, 9_200_002, 9_200_003, 9_200_009  # Aziza, Bobur, the teacher, the bulk student
STORE = settings.storage_dir
ESSAY = ("In 2010, the number of tourists visiting the city rised sharply, reaching about 40 million.\n\n"
         "By contrast, the figure for the countryside remained stable at roughly 12 million over the period. "
         "Overall, it is clear that urban tourism growed much faster than rural tourism.")
IDS: dict[str, int] = {}


def png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (12, 8), (91, 242, 166)).save(buf, format="PNG")
    return buf.getvalue()


def put(rel: str, data: bytes) -> str:
    path = STORE / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


VISUAL = {
    "blocks": [{"visual_type": "bar", "title": "Tourists by destination", "units": "millions", "time_points": ["2010", "2020"],
                "axis_labels": [], "legend": [], "map_features": [], "process": None, "bars_drawn": [],
                "overall": "City tourism grew, the countryside stayed flat",
                "values": [{"series": "City", "point": "2010", "value": 40, "text": "", "printed": True, "precision": 0,
                            "status": "verified"},
                           {"series": "City", "point": "2020", "value": 55, "text": "", "printed": False, "precision": 2,
                            "status": "uncertain", "note": "between gridlines"},
                           {"series": "Countryside", "point": "2010", "value": 12, "text": "", "printed": True,
                            "precision": 0, "status": "verified"}]}],
    "readability": "clear", "readability_note": "", "unreadable": [], "checks": ["sum of shares checked"],
    "features": {"expected_overview": ["city up, countryside flat"], "key_comparisons": ["city vs countryside in 2020"],
                 "suggested_grouping": "by destination"},
    "reads": 2, "chart_key": 0,
}


async def seed() -> None:
    from sqlalchemy import insert

    from db.database import get_session
    from db.models import (Assignment, AssignmentPart, ChartKey, CheckHold, Group, MockTestPart, MockTestSession,
                           PracticeModule, PracticeQuestion, SourceType, SpeakingPart, SpeakingSubmission,
                           SubmissionComment, TaskType, User, UserRole, WritingSubmission)

    chart = put(f"{U1}/uploads/chart.png", png())
    photo = put(f"{U1}/uploads/answer.jpg", b"\xff\xd8\xff\xe0" + b"0" * 200)
    pdf = put(f"{U1}/reports/w_full.pdf", b"%PDF-1.4\n% archive test\n")
    s_pdf = put(f"{U1}/reports/s_full.pdf", b"%PDF-1.4\n% archive test speaking\n")
    practice_img = put("practice/q1.png", png())
    key_img = put("chart_keys/k1.png", png())
    recording = put(f"{U1}/uploads/rec.ogg", b"OggS" + bytes(range(256)) * 40)
    gone = str(STORE / f"{U1}/uploads/deleted-after-a-month.jpg")

    async with get_session() as s:
        s.add_all([User(id=U1, full_name="Aziza Karimova", username="aziza"),
                   User(id=U2, full_name="Bobur Sobirov", username="bobur"),
                   User(id=U3, full_name="Dilnoza Ustoz", username="dilnoza", role=UserRole.teacher),
                   User(id=U9, full_name="Bulk Student")])
        await s.flush()
        pq = PracticeQuestion(module=PracticeModule.writing_task1, question_text="The chart shows tourists.",
                              topic="Tourism", image_path=practice_img, source="cdireport")
        group = Group(name="IELTS 7+", teacher_id=U3)
        s.add_all([pq, group])
        await s.flush()
        a = Assignment(group_id=group.id, kind="homework", title="Week 3 essay", due_at=NOW + D, created_by=U3)
        s.add(a)
        await s.flush()
        part = AssignmentPart(assignment_id=a.id, position=1, module="writing_task2", prompt_text="Some say...")
        s.add(part)
        await s.flush()

        def w(**k):
            base = dict(user_id=U1, task_type=TaskType.task2, prompt_text="Some people think...", answer_text=ESSAY,
                        prompt_source=SourceType.text, answer_source=SourceType.text, original_file_paths={},
                        criteria_scores={"TA": 6, "CC": 6, "LR": 6, "GRA": 6}, annotations=[], vocabulary_json=[])
            return WritingSubmission(**{**base, **k})

        # an old check: no report_data, the sentence notes of before 2026-10, its PDF gone
        old = w(task_type=TaskType.task2, overall_band=6.0, created_at=NOW - 60 * D,
                criteria_scores={"TR_or_TA": 6, "CC": 6, "LR": 5.5, "GRA": 5.5},
                annotations=[{"sentence": "growed much faster", "color": "red", "comment": "grew - irregular verb"}],
                feedback_pdf_path=str(STORE / f"{U1}/reports/lost.pdf"))
        # a Task 1 whose photos are gone (past the month), a partial report_data
        gone_row = w(task_type=TaskType.task1, overall_band=5.0, created_at=NOW - 45 * D, answer_source=SourceType.photo,
                     original_file_paths={"prompt": gone, "answer": gone, "chart": [gone]},
                     report_data={"cost": {"usd": 0.05}})
        s.add_all([old, gone_row])
        await s.flush()
        IDS.update(w_old=old.id, w_gone=gone_row.id)

        # 2000 checks of the bulk student: the list's speed and queries
        rows = [dict(user_id=U9, task_type=TaskType.task2 if i % 3 else TaskType.task1, prompt_text=f"Bulk question {i}",
                     answer_text="word " * 250, prompt_source=SourceType.text, answer_source=SourceType.text,
                     original_file_paths={}, overall_band=4.0 + (i % 7) * 0.5,
                     criteria_scores={"TA": 6, "CC": 6, "LR": 6, "GRA": 6}, annotations=[{"n": 1, "wrong": "x"}] * 8,
                     vocabulary_json=[], coins_spent=5, is_test=False, created_at=NOW - 29 * D + i * timedelta(minutes=19),
                     report_data={"cost": {"total_usd": 0.12}, "visual": VISUAL, "paid_with": {"mode": "coins"},
                                  "model_answer": {"openers": ["x" * 400] * 6}})
                for i in range(2000)]
        await s.execute(insert(WritingSubmission), rows)

        assign = w(user_id=U2, overall_band=7.0, created_at=NOW - D, assignment_part_id=part.id, coins_spent=5,
                   paid_by_id=U3, report_data={"paid_with": {"mode": "sponsor", "coins": 5, "payer_id": U3}})
        # malformed shapes everywhere: the page shows what it can
        weird = w(user_id=U2, overall_band=6.5, created_at=NOW - D + timedelta(hours=1),
                  criteria_scores={"TA": 6.5, "CC": None, "extra": 4},
                  annotations=[{"n": "x", "wrong": None}, "a bare note", 7], vocabulary_json=["resilient", {"term": "t"}],
                  original_file_paths={"chart": "not-a-list", "prompt": 5},
                  report_data={"visual": "garbage", "model_answer": [1, 2], "cost": "x", "paid_with": 5, "seconds": "9"})
        full = w(task_type=TaskType.task1, prompt_text="The chart below shows the number of tourists, 2010-2020.",
                 answer_source=SourceType.photo, overall_band=6.5, created_at=NOW - timedelta(minutes=5),
                 criteria_scores={"TA": 6, "CC": 6.5, "LR": 7, "GRA": 6}, coins_spent=5, practice_question_id=pq.id,
                 original_file_paths={"prompt": None, "answer": photo, "chart": [chart, gone]},
                 annotations=[{"n": 1, "wrong": "rised", "correct": "rose", "why": "rise is irregular", "criterion": "GRA",
                               "unclear": False, "if_you_meant": None},
                              {"n": 2, "wrong": "growed", "correct": "grew", "why": "irregular past", "criterion": "GRA",
                               "unclear": False, "if_you_meant": None}],
                 vocabulary_json=[{"word_or_phrase": "rise sharply", "meaning": "go up fast",
                                   "example_sentence": "Prices rose sharply."}],
                 feedback_pdf_path=pdf,
                 teacher_bands={"bands": {"TA": 6, "CC": 6, "LR": 6.5, "GRA": 6}, "overall": 6.0, "by": "ustoz1",
                                "role": "teacher", "approved": False, "at": "2026-11-15T10:00:00", "note": "fair"},
                 report_data={"model_answer": {"source": "ai_generated", "aimed_at": 7.0, "provisional": True,
                                               "shows": ["01"], "writer": "simon", "reference_ids": [3, 4],
                                               "alternative": False, "openers": []},
                              "resubmission_of": None, "visual": VISUAL,
                              "cost": {"total_usd": 0.1834, "by_purpose": {"scoring": 0.09, "feedback": 0.07},
                                       "calls": 9, "models": ["gpt-5"]},
                              "seconds": 74, "paid_with": {"mode": "coins", "coins": 5}, "check_token": "abc123"})
        s.add_all([assign, weird, full])
        await s.flush()
        test = w(overall_band=9.0, created_at=NOW, is_test=True, prompt_text="A test run")
        s.add(test)
        await s.flush()
        IDS.update(w_assign=assign.id, w_weird=weird.id, w_full=full.id, w_test=test.id, pq=pq.id)
        key = ChartKey(status="verified", source="practice", question_text=pq.question_text, image_path=key_img,
                       sheet=VISUAL, last_submission_id=full.id)
        s.add(key)
        s.add(SubmissionComment(kind="w", submission_id=assign.id, teacher_id=U3, text="Good structure", band=6.5))

        s_old = SpeakingSubmission(user_id=U1, part=SpeakingPart.part2, question_text="1. Describe a trip\n2. Why?",
                                   transcript_text="Q1: Describe a trip\nA: I went to Samarkand last year.\n\n"
                                                   "Q2: Why?\nA: Because it is beautiful.",
                                   audio_file_path=str(STORE / f"{U1}/uploads/old-voice.ogg"), overall_band=5.5,
                                   criteria_scores={"FC": 5.5, "LR": 5.5, "GRA": 5, "Pronunciation": 6},
                                   annotations=[{"sentence": "it is beautiful", "color": "green", "comment": "fine"}],
                                   vocabulary_json=[], created_at=NOW - 50 * D)
        s.add(s_old)
        await s.flush()
        questions = ["Do you work or study?", "What do you like about your hometown?", "Do you like reading?"]
        said = ["I am study at university in Tashkent.", "My hometown is very peaceful and green.",
                "Yes, I like reading books very much."]
        s_full = SpeakingSubmission(
            user_id=U1, part=SpeakingPart.part1, question_text="\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1)),
            transcript_text="...", audio_file_path=recording, overall_band=6.5,
            criteria_scores={"FC": 6.5, "LR": 6, "GRA": 6, "Pronunciation": 7}, feedback_pdf_path=s_pdf, coins_spent=5,
            annotations=[{"n": 1, "q": "Q1", "wrong": "I am study", "correct": "I study", "why": "no 'am'",
                          "criterion": "GRA", "unclear": False, "if_you_meant": None},
                         {"n": 2, "q": "Q3", "wrong": "very much", "correct": "a lot", "why": "more natural",
                          "criterion": "LR", "unclear": False, "if_you_meant": None}],
            vocabulary_json=[{"word_or_phrase": "bustling", "meaning": "busy", "example_sentence": "A bustling city."}],
            created_at=NOW - timedelta(minutes=3),
            report_data={"number": 4, "metrics": {"speaking_seconds": 88.2, "words": 120, "wpm": 82, "fillers": 3,
                                                  "filler_types": [["um", 2], ["uh", 1]], "fillers_per_minute": 2.04,
                                                  "long_pauses": 2, "mid_sentence_pauses": 1, "pauses_per_minute": 1.3,
                                                  "longest_pause": 2.4},
                         "questions": questions, "question_parts": ["part1"] * 3, "transcripts": said,
                         "pronunciation_measured": True,
                         "pronunciation_scores": {"accuracy": 82.6, "fluency": 74.1, "prosody": 68.0,
                                                  "completeness": 96.0, "seconds": 61.2},
                         "model_answers": [{"label": "Q1", "source": "ai_generated", "aimed_at": 7.0, "provisional": True,
                                            "words": 40, "shows": [], "text": "I'm currently studying economics.",
                                            "voice_file_id": "AwACAgIAAxkBAAI"},
                                           {"label": "Q2", "source": "ai_generated", "aimed_at": 7.0, "words": 38,
                                            "text": "My hometown is a quiet place.", "voice_file_id": None}],
                         "one_recording": {"pieces": 1, "seconds": 95.4, "split": [[[0.5, 30.2]], [[31.0, 60.0]],
                                                                                   [[61.0, 95.0]]],
                                           "missing": [], "questions_from_recording": False},
                         "cost": {"total_usd": 0.2412, "by_purpose": {"transcribe": 0.03}},
                         "paid_with": {"mode": "coins", "coins": 5}, "seconds": 51})
        s.add(s_full)
        await s.flush()
        s_test = SpeakingSubmission(user_id=U2, part=SpeakingPart.part3, question_text="Q", audio_file_path="",
                                    overall_band=8.0, criteria_scores={}, is_test=True, created_at=NOW)
        s.add(s_test)
        await s.flush()
        IDS.update(s_old=s_old.id, s_full=s_full.id, s_test=s_test.id, key=key.id)
        mock = MockTestSession(user_id=U1)
        s.add(mock)
        await s.flush()
        s.add(MockTestPart(session_id=mock.id, part_type="speaking_part1", order_index=3, submission_id=s_full.id,
                           submission_type="speaking", band=6.5))
        # stopped checks: one that saved a report, one the student redid, one with nothing after it
        s.add_all([CheckHold(id="hold_saved", user_id=U1, chat_id=U1, module="s", kind="speaking_part1", state="refunded",
                             reason="delivery", submission_id=s_full.id, created_at=NOW - timedelta(hours=2)),
                   CheckHold(id="hold_redo", user_id=U1, chat_id=U1, module="w", kind="writing_task1", state="refunded",
                             reason="OpenAI timeout", created_at=NOW - timedelta(hours=1)),
                   CheckHold(id="hold_none", user_id=U3, chat_id=U3, module="s", kind="speaking_part3",
                             state="interrupted", reason="restart", created_at=NOW - timedelta(minutes=1))])
        await s.commit()


def client():
    from webapp.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver")


class Queries:
    """Counts the SQL statements the app sends while it is on."""

    def __init__(self):
        from sqlalchemy import event

        from db.database import engine

        self.n, self.on = 0, False
        event.listen(engine.sync_engine, "before_cursor_execute", self._count)

    def _count(self, *a, **k):
        if self.on:
            self.n += 1

    async def run(self, coro):
        self.n, self.on = 0, True
        try:
            return await coro
        finally:
            self.on = False


async def lists(owner: httpx.AsyncClient) -> None:
    w_full, w_test = IDS["w_full"], IDS["w_test"]
    r = await owner.get("/archive/writing")
    html = r.text
    check("the Writing list opens, in the sidebar's checks group", r.status_code == 200 and 'data-group="checks" open' in html
          and 'href="/archive/writing" class="active"' in html and 'href="/archive/speaking"' in html, r.status_code)
    check("newest first, 50 a page", html.count('class="row-link"') == 50 and html.index(f"#{w_full}<")
          < html.index(f"#{IDS['w_weird']}<"), html.count('class="row-link"'))
    total = 2000 + 5  # old, gone, assign, weird, full - not the test one
    check("the count leaves the test check out", f'<b class="num">{total}</b>' in html, "")
    check("a test check is hidden by default", f"/archive/writing/{w_test}" not in html)
    html_t = (await owner.get("/archive/writing?test=1")).text
    check("…and shown with «test ham»", f"/archive/writing/{w_test}?test=1" in html_t and f'<b class="num">{total + 1}</b>' in html_t
          and "pill pill-warn" in html_t)
    check("a row: student link, band, the 4 criteria pills, words, coins, cost, icons",
          f'href="/users/{U1}"' in html and "@aziza" in html and '<i>TA</i>6' in html and '<i>LR</i>7' in html
          and "🖼" in html and "📄" in html and "$0.183" in html and "👩‍🏫" in html, "")
    check("a row shows the question's first words", "The chart below shows the number of tourists" in html)

    async def rows(q: str) -> list[int]:
        import re
        page = (await owner.get(f"/archive/writing?{q}")).text
        return [int(x) for x in re.findall(r'id="a-(\d+)"', page)]

    found = await rows("q=Aziza")
    check("search by name", set(found) == {IDS["w_old"], IDS["w_gone"], w_full}, found)
    check("search by @username", set(await rows("q=%40bobur")) == {IDS["w_assign"], IDS["w_weird"]})
    check("search by Telegram id", set(await rows(f"q={U2}")) == {IDS["w_assign"], IDS["w_weird"]})
    check("search by #check id", await rows(f"q=%23{IDS['w_old']}") == [IDS["w_old"]])
    check("a bare number finds the check id too", IDS["w_gone"] in await rows(f"q={IDS['w_gone']}"))
    check("a huge number is no error", (await owner.get("/archive/writing?q=%2399999999999")).status_code == 200)
    day = (NOW - 60 * D + timedelta(hours=5)).date().isoformat()
    check("from/to: one Tashkent day", await rows(f"from={day}&to={day}") == [IDS["w_old"]])
    t1 = await rows("sub=task1&q=Aziza")
    check("task filter", set(t1) == {IDS["w_gone"], w_full}, t1)
    hi = await rows("bmin=8")
    check("band range: none of the real checks is 8+", hi == [], hi)
    mid = await rows("bmin=6.5&bmax=7&q=bobur")
    check("band range 6.5-7", set(mid) == {IDS["w_assign"], IDS["w_weird"]}, mid)
    check("reversed bounds are swapped", set(await rows("bmin=7&bmax=6.5&q=bobur")) == set(mid))
    check("junk filters are ignored, never a 422/500", (await owner.get(
        "/archive/writing?from=yesterday&bmin=abc&page=x&sub=task9")).status_code == 200)

    p2 = await rows("page=2")
    p1 = await rows("")
    check("page 2: the next 50, older", len(p2) == 50 and max(p2) < min(p1), (len(p2), max(p2 or [0]), min(p1 or [0])))
    last = (await owner.get("/archive/writing?page=9999")).text
    check("a page past the end shows the last one", 'aria-current="page">41<' in last and last.count('class="row-link"') == 5,
          last.count('class="row-link"'))
    filtered = (await owner.get("/archive/writing?q=Aziza")).text
    check("filters ride along into the rows' links", f"/archive/writing/{w_full}?q=Aziza" in filtered)

    sp = (await owner.get("/archive/speaking")).text
    check("the Speaking list: duration, words, audio icon, test hidden", f"/archive/speaking/{IDS['s_full']}" in sp
          and "1:35" in sp and "120 so'z" in sp and "🎧" in sp and f"/archive/speaking/{IDS['s_test']}" not in sp
          and '<i>P</i>7' in sp and 'href="/archive/speaking" class="active"' in sp)
    check("part filter", f"/archive/speaking/{IDS['s_old']}" in (await owner.get("/archive/speaking?sub=part2")).text
          and f"/archive/speaking/{IDS['s_full']}" not in (await owner.get("/archive/speaking?sub=part2")).text)


async def speed(owner: httpx.AsyncClient) -> None:
    """2000+ checks: a page costs the same few queries whatever it holds (no per-row query), and is quick."""
    counter = Queries()
    await owner.get("/archive/writing")  # warm
    t = time.perf_counter()
    r = await counter.run(owner.get("/archive/writing"))
    first = time.perf_counter() - t
    many = counter.n
    await counter.run(owner.get("/archive/writing?q=Aziza"))
    few = counter.n
    t = time.perf_counter()
    await owner.get("/archive/writing?page=41")
    deep = time.perf_counter() - t
    await counter.run(owner.get("/archive/speaking"))
    speaking = counter.n
    print(f"     queries: 50 rows {many}, 3 rows {few}, speaking {speaking}; page 1 {first * 1000:.0f} ms, "
          f"page 41 {deep * 1000:.0f} ms")
    check("no N+1: 50 rows take the same queries as 3", r.status_code == 200 and many == few and many <= 14, (many, few))
    check("the speaking list too", speaking == many, speaking)
    check("a page is quick at 2000 checks (first and last)", first < 1.5 and deep < 1.5, (round(first, 2), round(deep, 2)))


async def writing_pages(owner: httpx.AsyncClient) -> None:
    w_full = IDS["w_full"]
    r = await owner.get(f"/archive/writing/{w_full}")
    html = r.text
    check("the Writing detail opens", r.status_code == 200 and 'data-group="checks" open' in html, r.status_code)
    check("head: the student, the date, the band, the 4 criteria by name", "Aziza Karimova" in html and f'href="/users/{U1}"'
          in html and "<b>6.5</b><span>bot bahosi</span>" in html and "Task Achievement" in html
          and "Coherence &amp; Cohesion" in html and "Toshkent vaqti" in html)
    check("head: the source (Practice, its channel), coins, cost, waited",
          f"/questions?module=writing_task1#q{IDS['pq']}" in html and "@cdireport" in html and "5 coin" in html
          and "$0.1834" in html and "1:14" in html)
    check("head: the teacher's bands and how far the bot is", "ustoz baholagan" in html and "ustoz1" in html
          and "bot +0.5" in html and "tasdiq kutmoqda" in html)
    check("the question and the Task 1 picture (a gone one counted)", "The chart below shows the number of tourists" in html
          and f'src="/checks/w/{w_full}/chart/0"' in html and f"/checks/w/{w_full}/chart/1" not in html
          and "1 ta fayl o'chirilgan" in html)
    check("the practice question's own picture", f'src="/questions/{IDS["pq"]}/image"' in html)
    check("the chart key, checked", f'href="/charts/{IDS["key"]}"' in html and "tekshirilgan" in html)
    check("the chart reading: its table, an unsure figure, the overview", "Tourists by destination" in html
          and "Countryside" in html and 'class="num unsure"' in html and "city up, countryside flat" in html
          and "±2" in html)
    check("the answer exactly as sent, its word count", "rised sharply, reaching about 40 million.\n\nBy contrast" in html
          and f"{len(ESSAY.split())} so'z" in html)
    check("the answer with its mistakes marked (a toggle)", 'class="a-mk"' in html and "rised<sup>1</sup>" in html)
    check("the numbered mistakes: original, correction, why", "<s>rised</s> → <b>rose</b>" in html and "rise is irregular" in html)
    check("the vocabulary", "rise sharply" in html and "Prices rose sharply." in html)
    check("the model answer's facts", "Mo'ljallangan band" in html and "7.0" in html and "simon" in html)
    check("the student's upload", f"/archive/writing/{w_full}/upload/answer" in html)
    check("the PDF: open, download, view here", f'href="/reports/w/{w_full}.pdf"' in html and "a-embed" in html)
    check("prev/next skip the test check by default", f"/archive/writing/{IDS['w_weird']}" in html and "Eskiroq" in html
          and f"/archive/writing/{IDS['w_test']}" not in html)
    with_test = (await owner.get(f"/archive/writing/{w_full}?test=1")).text
    check("…and follow the list's filters", f"/archive/writing/{IDS['w_test']}?test=1" in with_test)
    filt = (await owner.get(f"/archive/writing/{w_full}?q=Aziza")).text
    check("prev/next within a search", f"/archive/writing/{IDS['w_gone']}?q=Aziza" in filt
          and f"/archive/writing/{IDS['w_weird']}" not in filt)

    up = await owner.get(f"/archive/writing/{w_full}/upload/answer")
    check("an upload is served with its type", up.status_code == 200 and up.headers["content-type"] == "image/jpeg"
          and up.headers.get("x-content-type-options") == "nosniff", (up.status_code, up.headers.get("content-type")))
    check("no other upload slot", (await owner.get(f"/archive/writing/{w_full}/upload/chart")).status_code == 404
          and (await owner.get(f"/archive/writing/{w_full}/upload/prompt")).status_code == 404)

    old = await owner.get(f"/archive/writing/{IDS['w_old']}")
    h = old.text
    check("an old check (no report_data) opens", old.status_code == 200 and "Some people think" in h
          and "rised sharply" in h, old.status_code)
    check("…its Task 2 TR band from TR_or_TA, the old notes, the PDF missing", "Task Response" in h
          and "Eski formatdagi" in h and "growed much faster" in h and "PDF fayli serverda topilmadi" in h
          and "report_data" in h)
    gone = (await owner.get(f"/archive/writing/{IDS['w_gone']}")).text
    check("a Task 1 past the month: the pictures gone, said so", "Task 1 rasmi serverda yo'q" in gone and "30 kun" in gone
          and "45 kun oldin" in gone and "upload/answer" not in gone)
    weird = await owner.get(f"/archive/writing/{IDS['w_weird']}")
    check("malformed shapes: the page still opens", weird.status_code == 200 and "Bobur Sobirov" in weird.text
          and "resilient" in weird.text and "a bare note" in weird.text, weird.status_code)
    assign = (await owner.get(f"/archive/writing/{IDS['w_assign']}")).text
    check("an assignment: its group, teacher, the payer, the teacher's comment", "Week 3 essay" in assign
          and "IELTS 7+" in assign and f'href="/users/{U3}"' in assign and "guruh to&#39;ladi" in assign
          and "Good structure" in assign and "to'ladi: <a" in assign)
    from webapp import archive

    crit = archive.criteria("w", {"TA": "6.5", "CC": "x", "LR": None, "GRA": 7, "TR_or_TA": 5}, "task2")
    check("bands kept as text or junk are read, never an error", [(c["short"], c["band"]) for c in crit]
          == [("TR", 6.5), ("CC", None), ("LR", None), ("GRA", 7.0)], crit)
    check("the archive's own reading of shapes", archive.mistakes_of("junk") == [] and archive.vocabulary_of({"a": 1}) == []
          and archive.visual_of([1]) is None and archive.cost_of({"cost": {"usd": "0.5"}})["total"] == 0.5)
    missing = await owner.get("/archive/writing/99999999")
    check("an unknown check: a 404 page, not an error", missing.status_code == 404 and "topilmadi" in missing.text)

    real = archive.writing_context

    async def broken(*a, **k):
        raise RuntimeError("boom")

    archive.writing_context = broken
    try:
        bare = await owner.get(f"/archive/writing/{w_full}")
    finally:
        archive.writing_context = real
    check("a page that cannot be built: the bare check, never a 500", bare.status_code == 200
          and "asosiy ma'lumotlari" in bare.text and "rised sharply" in bare.text, bare.status_code)


async def speaking_pages(owner: httpx.AsyncClient) -> None:
    from webapp import archive

    s_full = IDS["s_full"]
    r = await owner.get(f"/archive/speaking/{s_full}")
    html = r.text
    check("the Speaking detail opens", r.status_code == 200 and "Speaking Part 1" in html, r.status_code)
    check("the recording's player", f'<audio id="aPlayer" controls preload="metadata" src="/archive/speaking/{s_full}/audio"'
          in html and "1:35" in html)
    check("each question, its transcript, its piece of the recording", all(q in html for q in (
        "Do you work or study?", "What do you like about your hometown?", "I am study at university in Tashkent.",
        "Yes, I like reading books very much.")) and 'data-start="0.5" data-end="30.2"' in html and "▶ 0:31–1:00" in html)
    check("a question's own mistakes, marked in its transcript", "<s>I am study</s> → <b>I study</b>" in html
          and "I am study<sup>1</sup>" in html and "very much<sup>2</sup>" in html)
    check("Azure's pronunciation scores", all(x in html for x in ("Accuracy", "Fluency", "Prosody", "Completeness"))
          and "<b>83</b>" in html and "<b>68</b>" in html and "61.2 s" in html)
    check("the model answers, a voice where Telegram has one", "I&#39;m currently studying economics." in html
          and f'src="/archive/speaking/{s_full}/voice/0"' in html and "Ovozi yaratilmagan" in html)
    check("the voice metrics, fillers", "So&#39;z / daqiqa" in html and "um ×2" in html)
    check("the mock test, the session number, the PDF", "Mock test #" in html and "4-sessiya" in html
          and f'href="/reports/s/{s_full}.pdf"' in html)
    check("the vocabulary", "bustling" in html)

    audio = await owner.get(f"/archive/speaking/{s_full}/audio")
    check("the recording with its real type", audio.status_code == 200 and audio.headers["content-type"] == "audio/ogg"
          and len(audio.content) == 4 + 256 * 40, (audio.status_code, audio.headers.get("content-type")))
    part = await owner.get(f"/archive/speaking/{s_full}/audio", headers={"Range": "bytes=0-9"})
    check("…and Range requests (the player jumps)", part.status_code == 206 and part.content == b"OggS" + bytes(range(6))
          and part.headers.get("content-range", "").startswith("bytes 0-9/"), part.status_code)

    async def fake(file_id):
        return b"OggS-voice" if file_id == "AwACAgIAAxkBAAI" else None

    real, archive._telegram_file = archive._telegram_file, fake
    try:
        voice = await owner.get(f"/archive/speaking/{s_full}/voice/0")
        none = await owner.get(f"/archive/speaking/{s_full}/voice/1")
        past = await owner.get(f"/archive/speaking/{s_full}/voice/7")
    finally:
        archive._telegram_file = real
    check("a model answer's voice from Telegram's copy", voice.status_code == 200 and voice.content == b"OggS-voice"
          and voice.headers["content-type"] == "audio/ogg")
    check("no voice, no index: 404", none.status_code == 404 and past.status_code == 404)

    old = await owner.get(f"/archive/speaking/{IDS['s_old']}")
    h = old.text
    check("an old Speaking check: its questions from the stored text", old.status_code == 200 and "Describe a trip" in h
          and "I went to Samarkand last year." in h and "Because it is beautiful." in h, old.status_code)
    check("…the recording gone, said so; no player", "Yozuv serverda yo'q" in h and "30 kun" in h and 'id="aPlayer"' not in h
          and "50 kun oldin" in h)
    check("…no Azure scores", "Azure ballari saqlanmagan" in h)
    gone_audio = await owner.get(f"/archive/speaking/{IDS['s_old']}/audio")
    check("a gone recording: 404, no error", gone_audio.status_code == 404)


async def access_and_links(owner: httpx.AsyncClient) -> None:
    from db.database import get_session
    from db.models import WebAdmin
    from webapp import panel

    checks = (await owner.get("/checks")).text
    check("/checks links each row to its archive page (admins)", f'href="/archive/writing/{IDS["w_full"]}"' in checks
          and f'href="/archive/speaking/{IDS["s_full"]}"' in checks)
    stopped = (await owner.get("/checks/stopped")).text
    check("/checks/stopped links its rows", 'href="/archive/hold/hold_redo"' in stopped
          and 'href="/archive/hold/hold_none"' in stopped)
    r = await owner.get("/archive/hold/hold_saved", follow_redirects=False)
    check("a stopped check that saved a report: that report", r.headers.get("location") == f"/archive/speaking/{IDS['s_full']}")
    r = await owner.get("/archive/hold/hold_redo", follow_redirects=False)
    check("…else the student's next check of the kind", r.headers.get("location")
          == f"/archive/writing/{IDS['w_full']}?test=1&hold=1", r.headers.get("location"))
    note = (await owner.get(r.headers["location"])).text
    check("…said so on the page", "To'xtagan tekshiruvning o'zi hisobot saqlamagan" in note)
    r = await owner.get("/archive/hold/hold_none", follow_redirects=False)
    check("…else the student's checks", r.headers.get("location") == f"/archive/speaking?q={U3}&test=1&hold=1",
          r.headers.get("location"))
    r = await owner.get("/archive/hold/nope", follow_redirects=False)
    check("an unknown stopped check: back to the list", r.headers.get("location") == "/checks/stopped")
    profile = (await owner.get(f"/users/{U1}")).text
    check("the user page links its checks to the archive", f'href="/archive/writing/{IDS["w_full"]}"' in profile
          and f"/archive/writing?q={U1}" in profile)
    r = await owner.get("/archive", follow_redirects=False)
    check("/archive goes to the Writing list", r.headers.get("location") == "/archive/writing")

    async with client() as anon:
        r = await anon.get("/archive/writing", follow_redirects=False)
        check("signed out: to the login", r.status_code == 303 and r.headers["location"] == "/login")

    async with get_session() as session:
        session.add(WebAdmin(username="ustoz_arx", name="Ustoz", password_hash=panel.hash_password("pw-a"), role="teacher"))
        await session.commit()
    async with client() as teacher:
        await teacher.post("/login", data={"username": "ustoz_arx", "password": "pw-a"})
        for path in ("/archive/writing", "/archive/speaking", f"/archive/writing/{IDS['w_full']}",
                     f"/archive/speaking/{IDS['s_full']}", f"/archive/speaking/{IDS['s_full']}/audio",
                     f"/archive/writing/{IDS['w_full']}/upload/answer", f"/archive/speaking/{IDS['s_full']}/voice/0",
                     "/archive/hold/hold_redo", "/archive"):
            r = await teacher.get(path, follow_redirects=False)
            check(f"a teacher is sent back from {path} (blind to the bot's bands)", r.status_code == 303
                  and r.headers.get("location") == "/checks", (r.status_code, r.headers.get("location")))
        page = (await teacher.get("/checks")).text
        check("a teacher's checks page has no archive link", "/archive/" not in page and 'class="side"' in page)

    from webapp import archive

    class Req:  # the router's own guard too, not only the middleware's
        session = {"logged_in": True, "teacher": True}

    r = archive._admin_only(Req())
    check("the router refuses a teacher by itself", r is not None and r.headers["location"] == "/checks")


async def main() -> None:
    await seed()
    async with client() as owner:
        r = await owner.post("/login", data={"username": settings.web_admin_username,
                                             "password": settings.web_admin_password})
        check("the owner signs in", r.status_code == 303, r.status_code)
        await lists(owner)
        await speed(owner)
        await writing_pages(owner)
        await speaking_pages(owner)
        await access_and_links(owner)
    if OUT:
        OUT.mkdir(parents=True, exist_ok=True)
        async with client() as owner:
            await owner.post("/login", data={"username": settings.web_admin_username,
                                             "password": settings.web_admin_password})
            for name, path in (("w_list", "/archive/writing"), ("s_list", "/archive/speaking?test=1"),
                               ("w_full", f"/archive/writing/{IDS['w_full']}"), ("w_old", f"/archive/writing/{IDS['w_old']}"),
                               ("w_gone", f"/archive/writing/{IDS['w_gone']}"), ("w_weird", f"/archive/writing/{IDS['w_weird']}"),
                               ("w_assign", f"/archive/writing/{IDS['w_assign']}"),
                               ("s_full", f"/archive/speaking/{IDS['s_full']}"), ("s_old", f"/archive/speaking/{IDS['s_old']}")):
                (OUT / f"{name}.html").write_text((await owner.get(path)).text, encoding="utf-8")
    print(json.dumps(IDS))


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    sys.exit(1 if fails else 0)
