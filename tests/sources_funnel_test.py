"""2026-11-16, before opening the bot to everyone: where users come from (users.source, migration 0028 - set at
the first /start from its link, never overwritten; bot/utils/signup_source.py) and the panel's business pages
(webapp/analytics.py): 📣 Manbalar with its link builder, 📈 Voronka with the funnel, the weekly cohorts, the top
error causes and the stopped checks by reason. Exact numbers on a seeded dataset, the exclusions (admins,
teachers, test checks, test payments), who may see the revenue, and the queries' speed at 10k users.
Offline: a throwaway Postgres + Redis (run_test.sh), Telegram a fake.
"""
import asyncio
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


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


# The analytics are asked "as of" NOW: a Wednesday, 12:00 in Tashkent, before the real clock (so the panel's
# pages, which use the clock, see every seeded row too).
NOW = datetime(2026, 11, 11, 7, 0, tzinfo=timezone.utc)
H, D, M = timedelta(hours=1), timedelta(days=1), timedelta(minutes=1)
ADMIN = 111  # ADMIN_TELEGRAM_IDS in run_test.sh
U1, U2, U3, U4, U5, U6, U7, U8 = range(9_100_001, 9_100_009)
ADMIN2, TEACHER = 9_100_099, 9_100_098  # role admin without being in .env; a center's teacher
NAMES = {U1: "Insta Bir", U2: "Insta Ikki", U3: "Insta Uch", U4: "Direkt Tort", U5: "Direkt Besh", U6: "Eski Olti",
         U7: "Referal Yetti", U8: "Insta Sakkiz", ADMIN: "Admin", ADMIN2: "Admin Ikki", TEACHER: "Ustoz Teacher"}


def client():
    from webapp.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://testserver")


# --- 1. the migration -------------------------------------------------------------------------------------------


async def migration() -> None:
    from sqlalchemy import text

    from db.database import get_session

    async with get_session() as s:
        col = (await s.execute(text(
            "SELECT data_type, character_maximum_length, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'users' AND column_name = 'source'"))).one_or_none()
        index = (await s.execute(text(
            "SELECT indexdef FROM pg_indexes WHERE tablename = 'users' AND indexname = 'ix_users_source'"))).scalar()
        version = (await s.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    check("migration 0028: users.source is a nullable varchar(64)", col is not None and tuple(col) ==
          ("character varying", 64, "YES"), col)
    check("...with its index", index is not None and "(source)" in index, index)
    check("...and it is the head", version == "0028", version)


# --- 2. the payload rules --------------------------------------------------------------------------------------


def payload_rules() -> None:
    from bot.utils import gifts, signup_source as src
    from bot.utils.group_invite import center_invite_link, invite_link

    group_payload = invite_link(5).split("start=", 1)[1]
    center_payload = center_invite_link(7).split("start=", 1)[1]
    cases = {
        None: "direct", "": "direct", "   ": "direct",
        "src_instagram": "instagram", "src_Instagram_Bio": "instagram_bio", "src_tg_post_12": "tg_post_12",
        "src_" + "a" * 40: "a" * 40, "src_" + "a" * 41: "other", "src_": "other", "src_bad-name": "other",
        "src_direct": "other", "src_referral": "other", "src_unknown": "other", "src_a b": "other",
        "ref_123": "referral", "ref_abc": "referral",
        "gift_ABCdef123": "gift", "gift_": "other",
        group_payload: "group", "g5_0000000000": "group", "group_5": "group",
        center_payload: "center", "c7_0000000000": "center",
        "hello": "other", "pl_token": "other", "srcinstagram": "other",
    }
    bad = {k: (src.from_payload(k), v) for k, v in cases.items() if src.from_payload(k) != v}
    check("every /start payload gets its source", not bad, bad)
    check("the gift prefix is gifts.py's", src.GIFT_PREFIX == gifts.PAYLOAD_PREFIX)
    check("a src_ payload is none of the others: no gift code, no group or center invite",
          gifts.gift_code("src_instagram") is None
          and not __import__("bot.utils.group_invite", fromlist=["x"]).is_invite_payload("src_instagram")
          and not __import__("bot.utils.group_invite", fromlist=["x"]).is_center_payload("src_instagram"))
    names = {"Instagram Bio": "instagram_bio", "  TG kanal — post #3 ": "tg_kanal_post_3", "o'quv markazi": "o_quv_markazi",
             "__x__": "x", "Привет": None, "": None, "direct": None, "Referral": None, "unknown": None,
             "a" * 45: "a" * 40, "a" * 39 + " b": "a" * 39}
    got = {k: src.normalize(k) for k in names}
    check("the link builder's names", got == names, got)
    check("a built name is read back as that source", all(src.from_payload(src.PREFIX + n) == n for n in names.values() if n))
    check("the link", src.link("instagram_bio") == f"https://t.me/{settings.telegram_bot_username}?start=src_instagram_bio")
    check("labels: NULL is noma'lum, a link is its name", src.label(None).startswith("Noma'lum")
          and src.label("instagram") == "instagram" and src.label("referral").startswith("Do'st")
          and src.is_link("instagram") and not src.is_link("gift") and not src.is_link(None))


# --- 3. the seeded dataset -------------------------------------------------------------------------------------


async def seed() -> None:
    from db.database import get_session
    from db.models import (
        CheckHold, PaymentRequest, PaymentStatus, SpeakingPart, SpeakingSubmission, TaskType, User, UserRole, UserStatus,
        WritingSubmission,
    )

    users = [  # id, source, signed up (before NOW), role
        (U1, "insta", NOW - 2 * D, UserRole.student), (U2, "insta", NOW - 10 * D, UserRole.student),
        (U3, "insta", NOW - 40 * D, UserRole.student), (U4, "direct", NOW - D, UserRole.student),
        (U5, "direct", NOW - 20 * D, UserRole.student), (U6, None, NOW - 100 * D, UserRole.student),
        (U7, "referral", NOW - 5 * D, UserRole.student), (U8, "insta", NOW - 35 * D - 2 * H, UserRole.student),
        (ADMIN, "direct", NOW - 3 * D, UserRole.admin), (ADMIN2, "direct", NOW - 3 * D, UserRole.admin),
        (TEACHER, "insta", NOW - 3 * D, UserRole.teacher),
    ]
    joined = {uid: at for uid, _, at, _ in users}

    def writing(uid, after, test=False):
        return WritingSubmission(user_id=uid, task_type=TaskType.task2, prompt_text="P", prompt_source="text",
                                 answer_text="A", answer_source="text", overall_band=6.0, is_test=test,
                                 created_at=joined[uid] + after)

    def speaking(uid, after, test=False):
        return SpeakingSubmission(user_id=uid, part=SpeakingPart.part1, question_text="Q", audio_file_path="/tmp/x.ogg",
                                  overall_band=6.0, is_test=test, created_at=joined[uid] + after)

    def pay(uid, amount, reviewed_after, status=PaymentStatus.approved, test=False):
        return PaymentRequest(user_id=uid, coins=10, amount=amount, status=status, is_test=test,
                              created_at=joined[uid] + reviewed_after - H,
                              reviewed_at=joined[uid] + reviewed_after if status != PaymentStatus.pending else None)

    def hold(n, uid, state, reason, ago, coins=5):
        return CheckHold(id=f"{n:032x}", user_id=uid, chat_id=uid, module="w", kind="writing_task2", state=state,
                         reason=reason, coins=coins, payer_id=uid, created_at=NOW - ago)

    async with get_session() as s:
        for uid, source, at, role in users:
            s.add(User(id=uid, full_name=NAMES[uid], source=source, created_at=at, role=role, status=UserStatus.approved))
        await s.flush()
        s.add_all([
            writing(U1, H), pay(U1, 50000, D),
            speaking(U2, 3 * H), writing(U2, 10 * M, test=True), writing(U2, 8 * D),
            pay(U2, 20000, D, PaymentStatus.pending), pay(U2, 30000, 2 * D, PaymentStatus.rejected),
            speaking(U3, D, test=True), pay(U3, 10000, 2 * D, test=True),
            writing(U4, 30 * M), pay(U4, 30000, 2 * H),
            writing(U5, 8 * D), speaking(U5, 15 * D),
            writing(U6, D), pay(U6, 100000, 10 * D),
            pay(U7, 20000, D),
            writing(U8, 2 * D), writing(U8, 22 * D), speaking(U8, 29 * D), pay(U8, 40000, 31 * D),
            writing(ADMIN, H), pay(ADMIN, 999000, H), writing(ADMIN2, H), pay(ADMIN2, 777000, H),
            writing(TEACHER, H), pay(TEACHER, 500000, 2 * H),
            # the stopped checks
            hold(1, U1, "refunded", "TimeoutError: Request timed out.", D),
            hold(2, U2, "refunded", "TimeoutError: Read timed out after 120s", 10 * D),
            hold(3, U4, "interrupted", "the bot restarted", 2 * D, coins=15),
            hold(4, U5, "refunded", "chart unreadable: ValueError: bad image", 3 * D),
            hold(5, U8, "refunded", "chart unreadable: ValueError: no axes", 20 * D, coins=0),
            hold(6, U1, "charged", None, D),
            hold(7, ADMIN, "refunded", "TimeoutError: admin test", D),
            hold(8, ADMIN2, "refunded", "TimeoutError: admin test", D),
            hold(9, U2, "refunded", "TimeoutError: too old", 40 * D),
            hold(10, U7, "expired", "left running", 5 * D),
            hold(11, U3, "refunded", None, 6 * D),
            hold(12, TEACHER, "refunded", "RuntimeError: boom", 2 * D),
        ])
        await s.commit()


# --- 4. the numbers -------------------------------------------------------------------------------------------


async def numbers() -> None:
    from db import crud
    from db.database import get_session
    from webapp import analytics

    admins = settings.admin_ids
    async with get_session() as s:
        stats = {r["source"]: r for r in await crud.source_stats(s, admins, NOW)}
        funnel = await crud.funnel_stats(s, admins, NOW)
        cohorts = await crud.weekly_cohorts(s, admins, NOW)
        stops = await crud.stopped_check_groups(s, admins, NOW)
        left_out = await crud.analytics_left_out(s, admins)
        overview = await analytics.sources_overview(s, NOW, with_revenue=True)
        hidden = await analytics.sources_overview(s, NOW, with_revenue=False)
        page = await analytics.funnel_overview(s, NOW)

    def row(src):
        r = stats.get(src) or {}
        return tuple(r.get(k) for k in ("signups", "checked", "paid", "revenue", "d7", "d30"))

    expected = {"insta": (4, 3, 2, 90000, 1, 2), "direct": (2, 2, 1, 30000, 1, 2), "referral": (1, 0, 1, 20000, 1, 1),
                None: (1, 1, 1, 100000, 0, 0)}
    got = {k: row(k) for k in expected}
    check("Manbalar per source: signups, checked, paid, revenue, 7 and 30 days", got == expected, got)
    check("...nothing else counted (admins, the teacher, test rows left out)", set(stats) == set(expected), set(stats))
    check("left out: 2 admins (the .env one and a role admin), 1 teacher", left_out == {"admins": 2, "teachers": 1}, left_out)

    rows = [(r["key"], r["signups"], r["checked_pct"], r["paid_pct"], r["revenue"]) for r in overview["rows"]]
    check("the table: biggest first, noma'lum last, the per cents", rows == [
        ("insta", 4, 75, 50, 90000), ("direct", 2, 100, 50, 30000), ("referral", 1, 0, 100, 20000),
        ("unknown", 1, 100, 100, 100000)], rows)
    t = overview["total"]
    check("...and the total (5 of 8 is 63%, the half rounded up)", (t["signups"], t["checked"], t["checked_pct"], t["paid"],
          t["paid_pct"], t["revenue"], t["d7"], t["d30"]) == (8, 6, 75, 5, 63, 240000, 3, 5), t)
    check("a custom source carries its link, the bot's own ones none", overview["rows"][0]["link"] ==
          f"https://t.me/{settings.telegram_bot_username}?start=src_insta" and overview["rows"][1]["link"] is None)
    check("without perm finance no revenue leaves the server", all("revenue" not in r for r in hidden["rows"])
          and "revenue" not in hidden["total"])

    a, r = funnel["all"], funnel["recent"]
    check("funnel, all time: 8 signed up, 6 checked, 5 paid (4 of them after a check)",
          (a["signups"], a["checked"], a["paid"], a["paid_checked"]) == (8, 6, 5, 4), a)
    check("...median to the first check 13h30 (a test check never counts), to the first payment 1 day",
          a["to_check"] == timedelta(hours=13, minutes=30) and a["to_pay"] == D, (a["to_check"], a["to_pay"]))
    check("funnel, last 30 days: 5 signed up, 4 checked, 3 paid (2 after a check)",
          (r["signups"], r["checked"], r["paid"], r["paid_checked"]) == (5, 4, 3, 2), r)
    check("...medians 2 hours and 1 day", r["to_check"] == 2 * H and r["to_pay"] == D, (r["to_check"], r["to_pay"]))
    steps = [(s["n"], s["of_all"], s["of_prev"]) for s in page["all"]["steps"]]
    check("the funnel's steps: % of signups, and of those who checked, % who paid", steps ==
          [(8, 100, None), (6, 75, 75), (5, 63, 67)] and page["all"]["paid_unchecked"] == 1
          and page["all"]["to_check"] == "13 soat 30 daqiqa" and page["all"]["to_pay"] == "1 kun", (steps, page["all"]))

    fields = ("size", "checked7", "open7", "paid30", "open30", *(f"{x}{k}" for k in range(1, 5)
                                                                  for x in ("started", "active", "ended")))
    want = {
        7: dict(size=2, checked7=2, open7=2, paid30=2, open30=2),                       # U1, U4: this week
        6: dict(size=1, checked7=0, open7=1, paid30=1, open30=1),                       # U7
        5: dict(size=1, checked7=1, paid30=0, open30=1, started1=1, active1=1),         # U2
        4: dict(size=1, open30=1, started1=1, active1=1, ended1=1, started2=1, active2=1),  # U5
        2: dict(size=1, checked7=1, **{f"started{k}": 1 for k in range(1, 5)},           # U8
                **{f"ended{k}": 1 for k in range(1, 5)}, active3=1, active4=1),
        1: dict(size=1, **{f"started{k}": 1 for k in range(1, 5)}, **{f"ended{k}": 1 for k in range(1, 5)}),  # U3
    }
    bad = {}
    for c in cohorts:
        exp = {f: want.get(c["idx"], {}).get(f, 0) for f in fields}
        have = {f: c[f] for f in fields}
        if have != exp:
            bad[c["idx"]] = {f: (have[f], exp[f]) for f in fields if have[f] != exp[f]}
    from datetime import date

    check("8 cohorts, Mondays in Tashkent, oldest first", len(cohorts) == 8 and cohorts[0]["start"] == date(2026, 9, 21)
          and cohorts[-1]["start"] == date(2026, 11, 9), [c["start"] for c in cohorts])
    check("the cohorts' numbers (U6, 100 days ago, outside them)", not bad, bad)
    views = {i: v for i, v in enumerate(page["cohorts"][::-1])}
    check("a cohort's cells: per cent, n/of and 'not over yet'", views[5]["checked7"] == {"pct": 100, "n": 1, "of": 1,
          "partial": False} and views[5]["weeks"][0] == {"pct": 100, "n": 1, "of": 1, "partial": True}
          and views[5]["weeks"][1] is None and views[3]["checked7"] is None and views[7]["current"]
          and views[2]["paid30"] == {"pct": 0, "n": 0, "of": 1, "partial": False}
          and views[2]["weeks"][3] == {"pct": 100, "n": 1, "of": 1, "partial": False}
          and views[7]["label"] == "09.11 – 15.11", (views[5], views[2]))

    g = analytics.group_stops(stops)
    got = [(x["state"], x["cause"], x["label"], x["n7"], x["n30"], x["coins"]) for x in g["groups"]]
    check("stopped checks by reason: the details dropped, admins' runs and older than 30 days left out", got == [
        ("refunded", "chart unreadable: ValueError", "Grafik o'qilmadi: ValueError", 1, 2, 5),
        ("refunded", "TimeoutError", "TimeoutError", 1, 2, 10),
        ("interrupted", "the bot restarted", "Bot qayta ishga tushdi", 1, 1, 15),
        ("refunded", "RuntimeError", "RuntimeError", 1, 1, 5),
        ("refunded", "", "Sababi yozilmagan", 1, 1, 5),
        ("expired", "left running", "Tugallanmay qoldi (javob kelmadi)", 1, 1, 5),
    ] and (g["n7"], g["n30"], g["coins"]) == (6, 8, 45), (got, g["n7"], g["n30"], g["coins"]))
    check("...each with its latest reason as the sample", g["groups"][1]["sample"] == "TimeoutError: Request timed out."
          and g["groups"][0]["sample"] == "chart unreadable: ValueError: bad image")
    check("a reason's cause", [analytics.stop_cause(x) for x in (
        "openai.APITimeoutError: Request timed out.", "chart refused: The picture is not a chart", "APIStatusError: Error code: 429",
        "  ", None)] == ["openai.APITimeoutError", "chart refused", "APIStatusError", "", ""])
    check("a duration in words", [analytics.duration(x) for x in (None, 20 * timedelta(seconds=1), 45 * M, 2 * H,
          13 * H + 30 * M, 3 * D, 2 * D + 4 * H)] == ["—", "1 daqiqadan kam", "45 daqiqa", "2 soat", "13 soat 30 daqiqa",
                                                      "3 kun", "2 kun 4 soat"])


# --- 5. the errors ---------------------------------------------------------------------------------------------


def error_entries() -> list[str]:
    t = time.time()
    entries = [
        {"at": t - 3600, "level": "ERROR", "logger": "bot.handlers.writing", "line": 700, "process": "bot",
         "message": "Grading failed for user 123 <b>***</b>", "again": 0, "exc": "Traceback...\n  File x\nTimeoutError: slow\n"},
        {"at": t - 7200, "level": "ERROR", "logger": "bot.handlers.writing", "line": 700, "process": "bot",
         "message": "Grading failed for user 456", "again": 3, "exc": ""},
        {"at": t - 10800, "level": "WARNING", "logger": "services.ai.openai_client", "line": 88, "process": "bot",
         "message": "Flex fell back", "again": 0, "exc": ""},
        {"at": t - 14400, "level": "ERROR", "logger": "bot.handlers.writing", "line": 701, "process": "panel",
         "message": "other line", "again": 1, "exc": ""},
    ]
    return [json.dumps(e) for e in entries] + ["not json", "[1, 2]"]


def error_grouping() -> None:
    from webapp import analytics

    out = analytics.group_errors(error_entries())
    got = [(g["key"], g["count"], g["level"], g["processes"]) for g in out["groups"]]
    check("errors by logger:line, each with its 'again' repeats", got == [
        ("bot.handlers.writing:700", 5, "ERROR", ["bot"]), ("bot.handlers.writing:701", 2, "ERROR", ["panel"]),
        ("services.ai.openai_client:88", 1, "WARNING", ["bot"])], got)
    first = out["groups"][0]
    check("...the latest message as the sample, the exception's last line, when last", first["message"].startswith(
        "Grading failed for user 123") and first["exc"] == "TimeoutError: slow" and "soat oldin" in first["ago"], first)
    check("...4 readable entries in 3 places (a broken item skipped)", out["entries"] == 4 and out["places"] == 3, out)
    check("an empty log", analytics.group_errors([])["groups"] == [])


# --- 6. the pages and who may see what -------------------------------------------------------------------------


async def pages() -> None:
    from bot.utils import crm
    from db.database import get_session
    from db.models import WebAdmin
    from webapp import panel

    await crm.redis().delete(crm.ERRORS_LOG_KEY)
    # newest first, as log_error keeps them - dicts only, as it writes them (the panel's own badge counter,
    # webapp/panel.py errors_today, reads every item as a dict)
    await crm.redis().rpush(crm.ERRORS_LOG_KEY, *(e for e in error_entries() if e.startswith("{")))
    async with get_session() as s:
        for login, role, perms in (("viewer_e2", "admin", ""), ("money_e2", "admin", "finance"), ("ustoz_e2", "teacher", "")):
            s.add(WebAdmin(username=login, name=login, password_hash=panel.hash_password("pw-" + login), role=role,
                           perms=perms))
        await s.commit()

    async def sign_in(c, login=None):
        if login is None:
            await c.post("/login", data={"username": settings.web_admin_username, "password": settings.web_admin_password})
        else:
            await c.post("/login", data={"username": login, "password": "pw-" + login})

    bot_link = f"https://t.me/{settings.telegram_bot_username}?start=src_"
    async with client() as owner:
        await sign_in(owner)
        r = await owner.get("/sources")
        html = r.text
        check("the owner: /sources opens with the sidebar, its link active", r.status_code == 200 and 'class="side"' in html
              and 'class="si active" href="/sources"' in html and html.count('data-group="') == 5, r.status_code)
        check("...the table with the revenue column and its total", "Tushum, so'm" in html)
        check("...240 000 so'm (no admin's, teacher's or test money)", "240 000" in html and "999 000" not in html
              and "500 000" not in html and "777 000" not in html)
        check("...the sources by name, noma'lum, the copy button of a custom one", "<code>insta</code>" in html
              and "Noma&#39;lum (2026-11-16 gacha)" in html and f'data-copy="{bot_link}insta"' in html)
        check("...links to the users list by source", 'href="/users?s=src:insta"' in html and 'href="/users?s=src:unknown"' in html)
        check("...what is left out, said on the page", "Hisobga olinmagan" in html and "2 admin" in html and "1 o'qituvchi" in html)
        html = (await owner.get("/sources", params={"name": "Instagram Bio"})).text
        check("the link builder: a name made into the link", f"<code id=\"srcLink\">{bot_link}instagram_bio</code>" in html
              and f'data-copy="{bot_link}instagram_bio"' in html and 'id="srcCopy"' in html)
        html = (await owner.get("/sources", params={"name": "Direct"})).text
        check("...one of the bot's own names refused", 'id="srcErr">«direct» — botning o&#39;z manba nomi' in html
              and 'id="srcOut" hidden' in html)
        html = (await owner.get("/sources", params={"name": "Привет"})).text
        check("...nothing Latin left: refused", 'id="srcErr">Nomda kamida bitta lotin harfi' in html)
        html = (await owner.get("/sources")).text
        check("...nothing typed: no link, no error", 'id="srcOut" hidden' in html and 'id="srcErr" hidden' in html)
        check("...the page explains one link per channel", "alohida havola" in html and "Instagram bio" in html)

        r = await owner.get("/funnel")
        html = r.text
        check("/funnel opens, its link active", r.status_code == 200 and 'class="si active" href="/funnel"' in html, r.status_code)
        check("...the two funnels, the cohorts, the stops, the errors", "Hamma vaqt" in html and "So&#39;nggi 30 kun" in html
              and "Haftalik kohortalar" in html and "Grafik o&#39;qilmadi: ValueError" in html
              and "bot.handlers.writing:700" in html)
        check("...a stored message shown escaped, as it was kept", "Grading failed for user 123 &lt;b&gt;***&lt;/b&gt;" in html
              and "<b>***</b>" not in html)
        check("...what is left out, said on the page", "Hisobga olinmagan" in html and "test to'lovlar" in html)

        html = (await owner.get("/users", params={"s": "src:insta"})).text
        check("the users list filtered by a source (all its users, the teacher too)", NAMES[U1] in html and NAMES[U8] in html
              and NAMES[TEACHER] in html and NAMES[U4] not in html and "Manba: <b>insta</b>" in html)
        html = (await owner.get("/users", params={"s": "src:unknown"})).text
        check("...noma'lum: the users from before", NAMES[U6] in html and NAMES[U1] not in html)
        html = (await owner.get("/users")).text
        check("...and the source column", "<th>Manba</th>" in html and 'href="/users?s=src:referral"' in html)
        html = (await owner.get(f"/users/{U1}")).text
        check("the user's page shows the source", "Manba: <a href=\"/users?s=src:insta\"" in html)
        html = (await owner.get(f"/users/{U6}")).text
        check("...noma'lum for an old one", "Noma&#39;lum (2026-11-16 gacha)" in html)

    async with client() as viewer:
        await sign_in(viewer, "viewer_e2")
        r = await viewer.get("/sources")
        html = r.text
        check("an admin without finance: the page opens, no revenue on it", r.status_code == 200 and "Tushum, so" not in html
              and "240 000" not in html and "90 000" not in html and "Moliya» ruxsati" in html, r.status_code)
        r = await viewer.get("/funnel")
        check("...the funnel too", r.status_code == 200 and "Haftalik kohortalar" in r.text)
    async with client() as money:
        await sign_in(money, "money_e2")
        html = (await money.get("/sources")).text
        check("an admin with finance sees the revenue", "Tushum, so" in html and "240 000" in html)
    async with client() as teacher:
        await sign_in(teacher, "ustoz_e2")
        for path in ("/sources", "/funnel", "/sources?name=x"):
            r = await teacher.get(path, follow_redirects=False)
            check(f"a teacher's login: {path} closed", r.status_code in (302, 303) and r.headers.get("location") == "/checks",
                  (r.status_code, r.headers.get("location")))
        html = (await teacher.get("/checks")).text
        check("...and no Manbalar/Voronka in its sidebar", 'href="/sources"' not in html and 'href="/funnel"' not in html)
    async with client() as anonymous:
        r = await anonymous.get("/funnel", follow_redirects=False)
        check("signed out: to the login", r.status_code == 303 and r.headers.get("location") == "/login")


# --- 7. the first /start writes the source, a later one never changes it ------------------------------------


async def start_rules() -> None:
    from aiogram import Bot
    from aiogram.client.session.base import BaseSession
    from aiogram.types import Chat, Message, Update, User

    from bot.main import build_dispatcher
    from bot.utils import gifts
    from bot.utils.crm import redis
    from bot.utils.group_invite import center_invite_link, invite_link
    from db.database import get_session
    from db.models import Center, CenterTeacher, CoinGift, Group, GroupMembership, User as DbUser, UserRole, UserStatus
    from sqlalchemy import select

    now = datetime.now(timezone.utc)
    S = {k: 7_300_000 + i for i, k in enumerate(
        ("direct", "src", "ref", "gift", "group", "center", "other", "bad", "reserved", "long", "ref_bad", "legacy",
         "group_teacher", "center_owner", "src_upper"), start=1)}

    async with get_session() as s:
        s.add(DbUser(id=S["group_teacher"], full_name="Guruh Ustoz", role=UserRole.teacher, status=UserStatus.approved))
        s.add(DbUser(id=S["center_owner"], full_name="Markaz Egasi", role=UserRole.teacher, status=UserStatus.approved))
        await s.flush()
        group = Group(name="IELTS 7", teacher_id=S["group_teacher"])
        center = Center(name="Chilonzor", owner_id=S["center_owner"])
        s.add_all([group, center])
        await s.flush()
        group_id, center_id = group.id, center.id
        s.add(CoinGift(code="GIFTE2CODE", coins=10, buyer_id=S["group_teacher"]))
        await s.commit()

    class TgSession(BaseSession):
        def __init__(self):
            super().__init__()
            self.calls, self.n = [], 9000

        async def close(self):
            pass

        async def stream_content(self, *a, **k):
            yield b""

        async def make_request(self, bot, method, timeout=None):
            self.calls.append(method)
            self.n += 1
            if type(method).__name__ in ("AnswerCallbackQuery", "DeleteMessage", "SendChatAction"):
                return True
            return Message(message_id=self.n, date=now, chat=Chat(id=getattr(method, "chat_id", None) or 1, type="private"),
                           text=getattr(method, "text", None) or "x").as_(bot)

    session = TgSession()
    bot = Bot("8888548766:TEST", session=session)
    dp = build_dispatcher()
    counter = iter(range(800_000, 900_000))

    async def start(uid, payload=None):
        n = len(session.calls)
        text = "/start" + (f" {payload}" if payload is not None else "")
        msg = Message(message_id=next(counter), date=now, chat=Chat(id=uid, type="private"),
                      from_user=User(id=uid, is_bot=False, first_name=f"S{uid}"), text=text)
        await dp.feed_update(bot, Update(update_id=next(counter), message=msg))
        return [c.text for c in session.calls[n:] if type(c).__name__ == "SendMessage"]

    group_payload = invite_link(group_id).split("start=", 1)[1]
    center_payload = center_invite_link(center_id).split("start=", 1)[1]
    firsts = {
        "direct": (None, "direct"), "src": ("src_instagram_bio", "instagram_bio"), "src_upper": ("src_TG_Post", "tg_post"),
        "ref": (f"ref_{S['direct']}", "referral"), "gift": ("gift_GIFTE2CODE", "gift"), "group": (group_payload, "group"),
        "center": (center_payload, "center"), "other": ("hello", "other"), "bad": ("src_bad-name", "other"),
        "reserved": ("src_referral", "other"), "long": ("src_" + "x" * 41, "other"), "ref_bad": ("ref_nobody", "referral"),
        "legacy": ("group_5", "group"),
    }
    for who, (payload, _) in firsts.items():
        await start(S[who], payload)
    async with get_session() as s:
        rows = {u.id: u for u in (await s.execute(select(DbUser).where(DbUser.id.in_(list(S.values()))))).scalars()}
        member = (await s.execute(select(GroupMembership).where(GroupMembership.user_id == S["group"]))).scalar_one_or_none()
        teaching = (await s.execute(select(CenterTeacher).where(CenterTeacher.user_id == S["center"]))).scalar_one_or_none()
        src_member = (await s.execute(select(GroupMembership).where(GroupMembership.user_id == S["src"]))).scalar_one_or_none()
    got = {who: rows[S[who]].source if S[who] in rows else "(no row)" for who in firsts}
    want = {who: v for who, (_, v) in firsts.items()}
    check("the first /start writes each source", got == want, {k: (got[k], want[k]) for k in got if got[k] != want[k]})
    check("the other payloads still work: the referrer kept, the group joined, the center joined",
          rows[S["ref"]].referred_by == S["direct"] and member is not None and teaching is not None
          and rows[S["center"]].role == UserRole.teacher, (rows[S["ref"]].referred_by, member, teaching))
    check("...a gift: its buyer is the referrer, the code waits for the language pick",
          rows[S["gift"]].referred_by == S["group_teacher"]
          and await redis().get(gifts.PENDING_KEY.format(user_id=S["gift"])) == "GIFTE2CODE")
    check("a src_ link is only a source: no referrer, no group, no gift kept",
          rows[S["src"]].referred_by is None and src_member is None
          and await redis().get(gifts.PENDING_KEY.format(user_id=S["src"])) is None)
    check("...a ref_ to nobody: a referral link all the same, no referrer", rows[S["ref_bad"]].referred_by is None)

    # a later /start - another link, or a group invite for an existing user - never changes it
    out = await start(S["src"], f"ref_{S['direct']}")
    await start(S["direct"], "src_tiktok")
    await start(S["other"], group_payload)
    await start(S["gift"], None)
    async with get_session() as s:
        again = {u.id: u for u in (await s.execute(select(DbUser).where(DbUser.id.in_(list(S.values()))))).scalars()}
        joined_later = (await s.execute(select(GroupMembership).where(GroupMembership.user_id == S["other"]))).scalar_one_or_none()
    check("a returning user's /start keeps the first source", again[S["src"]].source == "instagram_bio"
          and again[S["direct"]].source == "direct" and again[S["other"]].source == "other" and again[S["gift"]].source == "gift",
          {k: again[S[k]].source for k in ("src", "direct", "other", "gift")})
    check("...nor makes a referral of them, and greets them as usual", again[S["src"]].referred_by is None and bool(out), out)
    check("...while a group invite still works for them", joined_later is not None)
    await bot.session.close()


# --- 8. fast at 10k users ---------------------------------------------------------------------------------------


async def speed() -> None:
    import random

    from sqlalchemy import insert

    from db import crud
    from db.database import get_session
    from db.models import PaymentRequest, PaymentStatus, SpeakingPart, SpeakingSubmission, TaskType, User, UserStatus, WritingSubmission

    rnd = random.Random(7)
    base = 9_500_000
    sources = ["direct", "referral", "insta", "tg_post", "group", None]
    users, writings, speakings, payments = [], [], [], []
    for i in range(10_000):
        joined = NOW - timedelta(minutes=rnd.randrange(0, 90 * 24 * 60))
        users.append({"id": base + i, "full_name": f"P{i}", "source": rnd.choice(sources), "created_at": joined,
                      "status": UserStatus.approved})
        for _ in range(rnd.choice((0, 0, 1, 2, 3))):
            writings.append({"user_id": base + i, "task_type": TaskType.task2, "prompt_text": "P", "prompt_source": "text",
                             "answer_text": "A", "answer_source": "text", "original_file_paths": {}, "criteria_scores": {},
                             "annotations": [], "vocabulary_json": [],
                             "created_at": joined + timedelta(hours=rnd.randrange(1, 900))})
        if rnd.random() < 0.3:
            speakings.append({"user_id": base + i, "part": SpeakingPart.part2, "question_text": "Q", "audio_file_path": "/x",
                              "criteria_scores": {}, "annotations": [], "vocabulary_json": [],
                              "created_at": joined + timedelta(hours=rnd.randrange(1, 900))})
        if rnd.random() < 0.08:
            payments.append({"user_id": base + i, "coins": 10, "amount": 10000, "status": PaymentStatus.approved,
                             "created_at": joined + D, "reviewed_at": joined + D + H})
    async with get_session() as s:
        for table, rows in ((User, users), (WritingSubmission, writings), (SpeakingSubmission, speakings),
                            (PaymentRequest, payments)):
            for k in range(0, len(rows), 2000):
                await s.execute(insert(table), rows[k:k + 2000])
        await s.commit()
    from sqlalchemy import text

    async def timed() -> tuple[dict, int]:
        timings, total = {}, 0
        async with get_session() as s:
            for name, call in (("sources", crud.source_stats), ("funnel", crud.funnel_stats),
                               ("cohorts", crud.weekly_cohorts), ("stops", crud.stopped_check_groups)):
                t0 = time.perf_counter()
                result = await call(s, settings.admin_ids, NOW)
                timings[name] = round((time.perf_counter() - t0) * 1000)
                if name == "sources":
                    total = sum(r["signups"] for r in result)
        return timings, total

    stale, total = await timed()  # right after the bulk insert: the planner's statistics still say "empty"
    async with get_session() as s:
        await s.execute(text("ANALYZE"))  # as autovacuum does on the live database
        await s.commit()
    analyzed, _ = await timed()
    print(f"   (10k users, {len(writings) + len(speakings)} checks, {len(payments)} payments: {stale} ms with stale "
          f"statistics, {analyzed} ms analyzed)")
    check("10k users: every query a fraction of a second, even before the statistics catch up",
          all(ms < 400 for ms in (*stale.values(), *analyzed.values())), (stale, analyzed))
    check("...and they are all counted", total == 10_000 + 8, total)


async def migration_roundtrip() -> None:
    """0028's downgrade and upgrade again (last: it drops the column the rest of the test reads)."""
    from sqlalchemy import text

    from db.database import engine, get_session

    await engine.dispose()  # no pooled connection in the way of the ALTER TABLE

    async def alembic(*args) -> int:
        proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "alembic", *args, cwd=str(Path(__file__).parents[1]),
                                                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        if proc.returncode:
            print(err.decode()[-1500:])
        return proc.returncode

    async def has_column() -> bool:
        async with get_session() as s:
            return bool((await s.execute(text("SELECT count(*) FROM information_schema.columns WHERE table_name = 'users' "
                                              "AND column_name = 'source'"))).scalar())

    down = await alembic("downgrade", "0027")
    gone = not await has_column()
    await engine.dispose()
    up = await alembic("upgrade", "head")
    check("migration 0028 downgrades (column and index gone) and upgrades again", down == 0 and gone and up == 0
          and await has_column(), (down, gone, up))


async def main() -> int:
    from bot.utils.crm import redis

    await redis().flushdb()
    await migration()
    payload_rules()
    await seed()
    await numbers()
    error_grouping()
    await pages()
    await start_rules()
    await speed()
    await migration_roundtrip()
    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    await redis().aclose()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
