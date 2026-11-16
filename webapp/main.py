"""Bandzilla admin web panel - a read-mostly dashboard over the same
Postgres database the bot uses (db/ package, completely unchanged - this is
a second consumer of it, not a rewrite). Runs as a separate process from the
same Docker image as the bot (see docker-compose.yml's `admin_web` service):

    uvicorn webapp.main:app --host 0.0.0.0 --port 8000 --proxy-headers ...

Sign-in: the owner's login from .env (WEB_ADMIN_USERNAME/PASSWORD - always
works, the break-glass login) or a separate admin login the owner made on
/admins (since 2026-10-18; webapp/panel.py), and what each one did goes to
the audit log. Since 2026-11-16 the password is followed by a code from the
bot in the login's own Telegram, and each admin's actions are limited to
what the owner ticked (bot/utils/panel_access.py).
In production it sits behind the host's nginx at https://admin.bandzilla.uz
(see bot/config.py's web panel comment), which passes the real client IP in
X-Forwarded-For for the small brute-force guard below.
"""
from __future__ import annotations

import html
import json
import logging
import time
from urllib.parse import urlparse
from pathlib import Path

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from bot.config import settings
from bot.i18n import t
from bot.keyboards.payment_kb import REJECT_REASONS
from bot.utils import crm_events, support_log
from db import crud
from db.database import get_session
from db.models import PaymentRequest, PaymentStatus, PracticeModule, SpeakingSubmission, UserRole, WritingSubmission
from services.storage.file_storage import delete_file, save_practice_image

logger = logging.getLogger(__name__)
# No /docs, /redoc or /openapi.json (2026-11-15: they showed the whole route map to anyone).
app = FastAPI(title="Bandzilla Admin", docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def admin_session(request: Request, call_next):
    """Signs out a web admin who was disabled or got a new password, and
    counts the top bar's badges (unread support messages, payments waiting,
    ...). Added before SessionMiddleware, so it runs inside it and sees the
    session.

    A state-changing request (POST...) made by another site's page is refused (CSRF, the 2026-11-15
    audit: the cookie's SameSite=Lax let the sibling domain's pages through): the browser's Origin must be
    this host, and Sec-Fetch-Site must not say cross-site or same-site."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        fetch_site = request.headers.get("sec-fetch-site")
        if fetch_site in ("cross-site", "same-site") or (
                origin and origin != "null" and urlparse(origin).netloc != request.headers.get("host")):
            logger.warning("Cross-site %s %s refused (origin %s, %s)", request.method, request.url.path, origin,
                           fetch_site)
            return PlainTextResponse("Forbidden: cross-site request", status_code=403)
    if request.session.get("logged_in"):
        if not await panel.session_valid(request):
            request.session.clear()
        else:
            if panel.is_teacher(request) and not request.url.path.startswith(panel.TEACHER_PATHS):
                return RedirectResponse("/checks", status_code=303)  # a teacher's login: the checks page only
            if request.method == "GET" and not request.url.path.startswith(panel.NAV_SKIP):
                request.state.nav = await panel.nav_counts(request)
    return await call_next(request)


app.add_middleware(
    SessionMiddleware,
    secret_key=settings.web_session_secret,
    https_only=settings.web_cookie_secure,
)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Teachers' web cabinet (/cabinet, its own sign-in from the bot) - see webapp/cabinet.py.
from webapp import cabinet, insights, panel, sessions  # noqa: E402
from bot.utils import panel_access  # noqa: E402

templates.env.globals["can"] = panel.can  # {% if can(request, "payments") %}: a button only for who may press it

cabinet.templates = templates
app.include_router(cabinet.router)
# Test window, broadcast, support inbox, errors, Excel, blocking, admins + audit.
panel.templates = templates
app.include_router(panel.router)
# Task 1 chart keys: the checked data sheet of every chart the bot has met.
from webapp import charts  # noqa: E402

charts.templates = templates
app.include_router(charts.router)
# Finance: every income and expense, month by month (2026-11-14).
from webapp import finance  # noqa: E402

finance.templates = templates
app.include_router(finance.router)
# The checks behind the scenes: running now, stopped ones; resend a report, mark a test (2026-11-15).
from webapp import checks_ops  # noqa: E402

checks_ops.templates = templates
app.include_router(checks_ops.router)
# One search box: users, checks, payments (2026-11-15).
from webapp import search  # noqa: E402

search.templates = templates
app.include_router(search.router)


@app.on_event("startup")
async def _errors_to_the_panel() -> None:
    from bot.utils import crm

    crm.install_error_forwarding("panel")

# --- tiny in-memory brute-force guard (per client IP, as forwarded by nginx) ---
_failed_attempts: dict[str, list[float]] = {}
MAX_ATTEMPTS = 5
WINDOW_SECONDS = 900

# Sentinel for PaymentRequest.reviewed_by when a decision is made from the web
# panel instead of Telegram - real Telegram user ids are always positive.
WEB_REVIEWER_ID = -1


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _too_many_attempts(ip: str) -> bool:
    now = time.time()
    attempts = [t for t in _failed_attempts.get(ip, []) if now - t < WINDOW_SECONDS]
    _failed_attempts[ip] = attempts
    return len(attempts) >= MAX_ATTEMPTS


def _record_failure(ip: str) -> None:
    _failed_attempts.setdefault(ip, []).append(time.time())


def _require_login(request: Request) -> RedirectResponse | None:
    if not request.session.get("logged_in"):
        return RedirectResponse("/login", status_code=303)
    return None


@app.get("/healthz", include_in_schema=False)
async def healthz():
    """For an outside uptime monitor (UptimeRobot, 2026-11-15): the host's own watchdog cannot report a dead
    server. 200 "ok" while the bot's heartbeat is fresh, the admins' group gets its messages and the database
    answers; 503 naming what is down otherwise ("bot", "group", "redis", "db"). Public, so it says nothing
    more."""
    from sqlalchemy import text

    from bot.utils import crm

    down = []
    try:
        beat = await crm.redis().get(crm.HEARTBEAT_KEY)
        if not beat or time.time() - int(beat) > 3 * crm.HEARTBEAT_EVERY:
            down.append("bot")
        # the admins' group: every alert, payment and support message goes through it (2026-11-16) - its
        # sender beats after every turn that went well; stale = the group unreachable or the sender stuck
        sender = await crm.redis().get(crm.SENDER_BEAT_KEY)
        if sender and "bot" not in down and time.time() - int(sender) > crm.SENDER_STALE:
            down.append("group")
    except Exception:  # noqa: BLE001
        down.append("redis")
    try:
        async with get_session() as session:
            await session.execute(text("select 1"))
    except Exception:  # noqa: BLE001
        down.append("db")
    return PlainTextResponse("down: " + ", ".join(down) if down else "ok", status_code=503 if down else 200,
                             headers={"Cache-Control": "no-store"})


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    ip = _client_ip(request)
    if _too_many_attempts(ip) or await sessions.too_many_fails(ip, username.strip()):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Juda ko'p urinish. 15 daqiqadan so'ng qayta urining."},
            status_code=429,
        )
    found = await panel.authenticate(username.strip(), password)
    if found is not None:
        if panel_access.enabled() and not await panel_access.device_ok(
                found["admin"], request.cookies.get(panel_access.DEVICE_COOKIE)):
            # the password was right; the code from the bot finishes it (bot/utils/panel_access.py)
            pid = await panel_access.start(found, ip)
            request.session.clear()
            request.session["mfa_pid"] = pid
            if await panel_access.linked(found["admin"]) is None:
                return RedirectResponse("/login/link", status_code=303)
            sent = await _with_bot_result(lambda bot: panel_access.send_code(bot, pid))
            return RedirectResponse(f"/login/code?s={sent or 'failed'}", status_code=303)
        return await _signed_in(request, found, ip, "eslab qolingan qurilma" if panel_access.enabled() else "")
    _record_failure(ip)
    await sessions.failed(ip, username.strip())
    await panel.audit("—", "login_failed", details=f"{ip} · {username.strip()[:50]}")
    return templates.TemplateResponse(
        request, "login.html", {"error": "Login yoki parol noto'g'ri."}, status_code=401
    )


async def _signed_in(request: Request, found: dict, ip: str, how: str) -> RedirectResponse:
    # a fresh session, its id kept on the server; "mfa" only when the second step was on (a sign-in made while
    # WEB_2FA=false does not outlive the step being switched back on)
    await sessions.issue(request, {**found, "mfa": panel_access.enabled()})
    await panel.audit(found["admin"], "login", details=ip + (f" · {how}" if how else ""))
    return RedirectResponse("/", status_code=303)


async def _with_bot_result(send):
    """Like _with_bot, but returns what `send(bot)` returned (None when it raised)."""
    bot = Bot(settings.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        return await send(bot)
    except Exception as e:  # noqa: BLE001
        logger.warning("Panel bot call failed: %r", e)
        return None
    finally:
        await bot.session.close()


_CODE_NOTES = {
    "sent": "Kod Telegram'ga yuborildi.", "wait": "Kod yaqinda yuborilgan — biroz kuting yoki o'sha kodni kiriting.",
    "failed": "Kodni Telegram'ga yuborib bo'lmadi. Botni bloklamaganingizni tekshirib, qayta yuboring.",
}


async def _second_step_page(request: Request, step: str, error: str | None = None, note: str | None = None,
                             status_code: int = 200):
    data = await panel_access.pending(request.session.get("mfa_pid"))
    if data is None:
        request.session.pop("mfa_pid", None)
        return templates.TemplateResponse(request, "login.html", {
            "error": "Kirish vaqti tugadi — login va parolni qayta kiriting."}, status_code=401)
    login = data["found"]["admin"]
    row = await panel_access.linked(login)
    if step == "code" and row is None:
        step = "link"
    if step == "link" and row is not None:
        step = "code"
    return templates.TemplateResponse(request, "login.html", {
        "step": step, "error": error, "note": note, "login": login, "owner": bool(data["found"].get("owner")),
        "tg_name": row.telegram_name if row is not None else None, "bot": settings.telegram_bot_username,
        "link_url": await panel_access.link_url(request.session["mfa_pid"]) if step == "link" else None,
        "days": panel_access.DEVICE_DAYS,
    }, status_code=status_code)


@app.get("/login/code", response_class=HTMLResponse)
async def login_code_form(request: Request, s: str = ""):
    return await _second_step_page(request, "code", note=_CODE_NOTES.get(s))


@app.get("/login/link", response_class=HTMLResponse)
async def login_link_form(request: Request):
    return await _second_step_page(request, "link")


@app.post("/login/code/resend")
async def login_code_resend(request: Request):
    pid = request.session.get("mfa_pid")
    sent = await _with_bot_result(lambda bot: panel_access.send_code(bot, pid)) if pid else "gone"
    return RedirectResponse(f"/login/code?s={sent or 'failed'}", status_code=303)


@app.post("/login/code")
async def login_code_submit(request: Request, code: str = Form(""), remember: str | None = Form(None)):
    ip = _client_ip(request)
    pid = request.session.get("mfa_pid")
    data = await panel_access.pending(pid)
    login = data["found"]["admin"] if data else ""
    if data and await sessions.too_many_fails(ip, login):
        return await _second_step_page(request, "code", "Juda ko'p urinish. 15 daqiqadan so'ng qayta urining.",
                                       status_code=429)
    result, found = await panel_access.check(pid, code)
    if found is not None:
        response = await _signed_in(request, found, ip, "tiklash kodi" if result == "recovery" else "Telegram kodi")
        if remember:
            token = await panel_access.remember_device(found["admin"])
            response.set_cookie(panel_access.DEVICE_COOKIE, token, max_age=panel_access.DEVICE_DAYS * 86400,
                                httponly=True, secure=settings.web_cookie_secure, samesite="lax")
        return response
    if result == "gone" or result == "locked":
        request.session.pop("mfa_pid", None)
        if result == "locked":
            await sessions.failed(ip, login)
            await panel.audit(login, "login_code_failed", details=f"{ip} · {panel_access.MAX_TRIES} marta xato")
        return templates.TemplateResponse(request, "login.html", {"error": (
            "Kod ko'p marta xato kiritildi — login va parolni qayta kiriting." if result == "locked" else
            "Kirish vaqti tugadi — login va parolni qayta kiriting.")}, status_code=401)
    await sessions.failed(ip, login)
    if result == "expired":
        return await _second_step_page(request, "code", "Kod eskirgan — «Kodni qayta yuborish» ni bosing.",
                                       status_code=401)
    await panel.audit(login, "login_code_failed", details=ip)
    return await _second_step_page(request, "code", "Kod noto'g'ri.", status_code=401)


@app.get("/logout")
async def logout(request: Request):
    await sessions.end(request)  # the server forgets this sign-in too: a copied cookie stops working
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    if (redirect := _require_login(request)) is not None:
        return redirect
    async with get_session() as session:
        data = await insights.dashboard(session)
    charts = {k: data[k] for k in ("series", "bands", "mix", "langs")}
    charts["spark"] = [k["spark"] for k in data["kpi"]]
    return templates.TemplateResponse(request, "dashboard.html", {
        "active": "dashboard", "d": data,
        # Pre-serialized in Python (not the `tojson` filter) so this doesn't
        # depend on whether Starlette's Jinja2Templates registers it.
        "charts_json": json.dumps(charts),
    })


@app.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, q: str | None = None, s: str = "all"):
    if (redirect := _require_login(request)) is not None:
        return redirect
    async with get_session() as session:
        data = await insights.users_overview(session, q, s)
    return templates.TemplateResponse(request, "users.html", {
        "active": "users", "q": q or "", "segments": insights.SEGMENTS, **data,
    })


@app.get("/users/{user_id}", response_class=HTMLResponse)
async def user_detail(request: Request, user_id: int, sent: str | None = None, blocked: str | None = None):
    if (redirect := _require_login(request)) is not None:
        return redirect
    async with get_session() as session:
        profile = await insights.user_profile(session, user_id)
    if profile is None:
        return RedirectResponse("/users", status_code=303)
    flash = {"1": "Xabar yuborildi.", "0": "Xabar yuborilmadi: foydalanuvchi botni bloklagan bo'lishi mumkin.",
             "empty": "Xabar bo'sh."}.get(sent or "") or {
        "block": "Foydalanuvchi bloklandi: bot unga endi javob bermaydi.", "unblock": "Foydalanuvchi blokdan chiqarildi.",
        "admin": "Adminni bloklab bo'lmaydi."}.get(blocked or "")
    return templates.TemplateResponse(request, "user_detail.html", {
        "active": "users", **profile, "flash": flash, "flash_bad": sent in ("0", "empty") or blocked == "admin",
        "is_admin": user_id in settings.admin_ids, "blocked": profile["user"].status.value == "denied",
        "series_json": json.dumps(profile["series"]), "fmt": insights.fmt, "som": insights.som,
    })


@app.post("/users/{user_id}/message")
async def user_message(request: Request, user_id: int, text: str = Form(""), back: str = Form("")):
    """A message to the user in the bot, as the admins' support answer (they
    can reply with its button); a copy goes to the admins' group, and it is
    kept in the support inbox. Sent from the profile or from the inbox."""
    if (redirect := _require_login(request)) is not None:
        return redirect
    page = f"/support/{user_id}" if back == "support" else f"/users/{user_id}"
    text = text.strip()[:3500]
    if not text:
        return RedirectResponse(f"{page}?sent=empty", status_code=303)
    from bot.keyboards.support_kb import support_reply_kb
    from bot.utils import crm

    async with get_session() as session:
        user = await crud.get_user(session, user_id)
    lang = user.language if user is not None else None
    body = f"{t('support.answer', lang)}\n\n{html.escape(text, quote=False)}"
    sent = await _with_bot(lambda bot: bot.send_message(user_id, body, reply_markup=support_reply_kb(lang)))
    who = html.escape(user.full_name, quote=False) if user is not None else str(user_id)
    status = "yetkazildi" if sent else "yetkazilmadi"
    admin = panel.admin_name(request)
    await crm.post("support", f"✉️ <b>Web paneldan javob</b> ({html.escape(admin)}) → {who} (<code>{user_id}</code>) · "
                              f"{status}\n\n{html.escape(text, quote=False)}")
    if sent:  # saved as the answer, which marks the student's messages read (support_log.save)
        await support_log.save(user_id, "out", text=text, via="web", admin=admin)
    else:  # not delivered, but read (2026-11-16: a reply from the profile page left them unread)
        await support_log.mark_read(user_id)
    await panel.audit(request, "message", user_id, details=f"{status} · {text[:300]}")
    return RedirectResponse(f"{page}?sent={int(sent)}" + ("" if back == "support" else "#message"), status_code=303)


@app.get("/reports/{kind}/{submission_id}.pdf")
async def report_pdf_admin(request: Request, kind: str, submission_id: int):
    """Any check's PDF report, for the admins."""
    if (redirect := _require_login(request)) is not None:
        return redirect
    model = {"w": WritingSubmission, "s": SpeakingSubmission}.get(kind)
    if model is None:
        return Response(status_code=404)
    async with get_session() as session:
        sub = await session.get(model, submission_id)
    if sub is None or not sub.feedback_pdf_path or not Path(sub.feedback_pdf_path).exists():
        return Response(status_code=404)
    name = f"Bandzilla_{'W' if kind == 'w' else 'S'}{submission_id}_{sub.user_id}.pdf"
    return FileResponse(sub.feedback_pdf_path, media_type="application/pdf", filename=name,
                        content_disposition_type="inline")


@app.get("/checks", response_class=HTMLResponse)
async def checks_page(request: Request, kind: str = "all", period: str = "all", tb: str = "", done: str = ""):
    if (redirect := _require_login(request)) is not None:
        return redirect
    kind = kind if kind in ("all", "w", "s") else "all"
    period = period if period in ("all", "today", "7d", "30d") else "all"
    async with get_session() as session:
        data = await insights.all_checks(session, kind, period, marked=tb == "1")
    flash = {"saved": "Ustoz bahosi saqlandi.", "cleared": "Ustoz bahosi olib tashlandi.",
             "approved": "Ustoz bahosi anchor sifatida tasdiqlandi — Speaking baholashiga namuna bo'ladi.",
             "unapproved": "Ustoz bahosi anchor'lardan olindi.",
             "resent": "Hisobot o'quvchiga qayta yuborildi.",
             "notsent": "Hisobot yuborilmadi — o'quvchi botni bloklagan bo'lishi mumkin.",
             "nopdf": "Bu tekshiruvning PDF hisoboti topilmadi.",
             "marked_test": "Tekshiruv test deb belgilandi: o'quvchi progressiga va anchor'larga kirmaydi.",
             "marked_real": "Tekshiruv haqiqiy deb belgilandi.",
             "bad": "Har bir mezonga 0-9 oralig'ida band kiriting (masalan 6 yoki 6.5)."}.get(done)
    return templates.TemplateResponse(request, "checks.html", {
        "active": "checks", **data, "tb": tb == "1", "flash": flash, "flash_bad": done in ("bad", "notsent", "nopdf"),
        "teacher_view": panel.is_teacher(request), "owner": panel.is_owner(request)})


@app.post("/checks/{kind}/{submission_id}/teacher")
async def checks_teacher_bands(request: Request, kind: str, submission_id: int):
    """A teacher's (or an admin's) own bands for a check (bot/utils/teacher_bands.py); all empty = taken off."""
    if (redirect := _require_login(request)) is not None:
        return redirect
    from sqlalchemy import null

    from bot.utils import teacher_bands

    if kind not in ("w", "s"):
        return RedirectResponse("/checks", status_code=303)
    form = await request.form()
    back = str(form.get("back") or "/checks")
    back = back if back.startswith("/checks") else "/checks"
    sep = "&" if "?" in back else "?"
    try:
        marks = teacher_bands.record(kind, {k: str(form.get(k) or "") for k, _, _ in teacher_bands.CRITERIA[kind]},
                                     panel.admin_name(request), str(form.get("note") or ""),
                                     role="teacher" if panel.is_teacher(request) else "admin")
    except ValueError:
        return RedirectResponse(f"{back}{sep}done=bad#c-{kind}{submission_id}", status_code=303)
    model = WritingSubmission if kind == "w" else SpeakingSubmission
    async with get_session() as session:
        sub = await session.get(model, submission_id)
        if sub is None:
            return RedirectResponse(back, status_code=303)
        sub.teacher_bands = marks if marks is not None else null()
        await session.commit()
    await panel.audit(request, "teacher_bands", details=f"{'W' if kind == 'w' else 'S'}#{submission_id}: "
                      + (" ".join(f"{k} {v:g}" for k, v in marks["bands"].items()) if marks else "olib tashlandi"))
    return RedirectResponse(f"{back}{sep}done={'saved' if marks else 'cleared'}#c-{kind}{submission_id}",
                            status_code=303)


@app.post("/checks/{kind}/{submission_id}/teacher/approve")
async def checks_teacher_approve(request: Request, kind: str, submission_id: int):
    """The owner lets a teacher login's marks teach the Speaking scoring as anchors (or takes that back)."""
    if (redirect := panel.guard(request, owner=True)) is not None:
        return redirect
    if kind not in ("w", "s"):
        return RedirectResponse("/checks", status_code=303)
    form = await request.form()
    back = str(form.get("back") or "/checks")
    back = back if back.startswith("/checks") else "/checks"
    model = WritingSubmission if kind == "w" else SpeakingSubmission
    async with get_session() as session:
        sub = await session.get(model, submission_id)
        if sub is None or not sub.teacher_bands:
            return RedirectResponse(back, status_code=303)
        approved = not sub.teacher_bands.get("approved", True)
        sub.teacher_bands = {**sub.teacher_bands, "approved": approved}
        await session.commit()
    await panel.audit(request, "teacher_bands", details=f"{'W' if kind == 'w' else 'S'}#{submission_id}: "
                      + ("anchor sifatida tasdiqlandi" if approved else "anchor'dan olindi"))
    sep = "&" if "?" in back else "?"
    return RedirectResponse(f"{back}{sep}done={'approved' if approved else 'unapproved'}#c-{kind}{submission_id}",
                            status_code=303)


@app.get("/checks/w/{submission_id}/answer", response_class=HTMLResponse)
async def writing_answer(request: Request, submission_id: int):
    """A Writing check's question and essay as the student sent them - for the teacher marking it, without the
    bot's bands."""
    if (redirect := _require_login(request)) is not None:
        return redirect
    async with get_session() as session:
        sub = await session.get(WritingSubmission, submission_id)
    if sub is None:
        return Response("Topilmadi", status_code=404)
    charts = [i for i, p in enumerate((sub.original_file_paths or {}).get("chart") or []) if p and Path(p).exists()]
    return templates.TemplateResponse(request, "answer.html", {
        "active": "checks", "sub": sub, "task": "Task 1" if getattr(sub.task_type, "value", sub.task_type) == "task1"
        else "Task 2", "charts": charts, "words": len((sub.answer_text or "").split())})


@app.get("/checks/w/{submission_id}/chart/{n}")
async def writing_chart(request: Request, submission_id: int, n: int):
    if (redirect := _require_login(request)) is not None:
        return redirect
    async with get_session() as session:
        sub = await session.get(WritingSubmission, submission_id)
    paths = ((sub.original_file_paths or {}).get("chart") or []) if sub is not None else []
    if not 0 <= n < len(paths) or not paths[n] or not Path(paths[n]).exists():
        return Response(status_code=404)
    return FileResponse(paths[n])


@app.get("/audio/s/{submission_id}")
async def speaking_audio(request: Request, submission_id: int):
    """A Speaking check's recording, for the teacher marking it."""
    if (redirect := _require_login(request)) is not None:
        return redirect
    async with get_session() as session:
        sub = await session.get(SpeakingSubmission, submission_id)
    if sub is None or not sub.audio_file_path or not Path(sub.audio_file_path).exists():
        return Response("Audio topilmadi", status_code=404)
    return FileResponse(sub.audio_file_path, media_type="audio/mpeg", content_disposition_type="inline",
                        filename=f"Bandzilla_S{submission_id}.mp3")


@app.get("/payments", response_class=HTMLResponse)
async def payments_page(request: Request, status: str | None = None):
    if (redirect := _require_login(request)) is not None:
        return redirect
    valid_statuses = {s.value for s in PaymentStatus}
    status_enum = PaymentStatus(status) if status in valid_statuses else None
    async with get_session() as session:
        requests_ = await crud.list_payment_requests(session, status=status_enum)
    return templates.TemplateResponse(
        request,
        "payments.html",
        {"active": "payments", "requests": requests_, "status": status or "all", "reject_reasons": REJECT_REASONS},
    )


# Receipts by payment request id - a receipt never changes, so the
# Telegram download happens once per receipt (the newest RECEIPT_CACHE kept).
_receipts: dict[int, bytes] = {}
RECEIPT_CACHE = 250  # the payments page shows up to 200 receipts


def _receipt_type(data: bytes) -> tuple[str, str]:
    """(media type, file extension) of a downloaded receipt, read from its first bytes - Telegram's file says
    nothing reliable (2026-11-16: a receipt may be an image file or a PDF now; all were served as JPEG)."""
    if b"%PDF-" in data[:1024]:  # a PDF may have a few bytes before its header
        return "application/pdf", "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1", b"heim", b"heis"):
        return "image/heic", "heic"
    return "application/octet-stream", "bin"


async def _download_receipt(file_id: str) -> bytes | None:
    """A Telegram file_id works only with the bot that received the photo:
    receipts sent before the move to @bandzilla_bot (2026-10-17) belong to
    @writespeak_bot, whose token is MOVED_BOT_TOKEN."""
    for token in filter(None, (settings.telegram_bot_token, settings.moved_bot_token)):
        bot = Bot(token)
        try:
            file = await bot.get_file(file_id)
            return (await bot.download_file(file.file_path)).read()
        except Exception:
            continue
        finally:
            await bot.session.close()
    return None


@app.get("/payments/{request_id}/receipt")
async def payment_receipt(request: Request, request_id: int, info: int = 0):
    """The receipt as it is - a photo, an image file or a PDF - shown in the browser (inline), its type read
    from the bytes. `info=1`: only {"type": ...}, for the payments page to turn a receipt it can't show as a
    picture (a PDF) into a link."""
    if (redirect := _require_login(request)) is not None:
        return redirect
    data = _receipts.get(request_id)
    if data is None:
        async with get_session() as session:
            payment = await session.get(PaymentRequest, request_id)
        data = await _download_receipt(payment.receipt_file_id) if payment and payment.receipt_file_id else None
        if data is None:
            return Response(status_code=404)
        _receipts[request_id] = data
        while len(_receipts) > RECEIPT_CACHE:
            _receipts.pop(next(iter(_receipts)))
    media_type, ext = _receipt_type(data)
    if info:
        return JSONResponse({"type": media_type}, headers={"Cache-Control": "private, max-age=86400"})
    # an unknown kind of file is only downloaded, never shown; nosniff: the browser never guesses another type
    disposition = "attachment" if media_type == "application/octet-stream" else "inline"
    return Response(data, media_type=media_type, headers={
        "Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f'{disposition}; filename="chek-{request_id}.{ext}"',
    })


@app.post("/payments/{request_id}/{action}")
async def decide_payment_web(request: Request, request_id: int, action: str, reason: str = Form("")):
    """Approve, reject (with a reason - REJECT_REASONS, 2026-11-16), or mark an approved payment as a test."""
    if (redirect := panel.guard(request, perm="payments")) is not None:
        return redirect
    if action == "test":  # approved only to try the bot out: no revenue (or back to real money)
        async with get_session() as session:
            p = await session.get(PaymentRequest, request_id)
            if p is not None and p.status == PaymentStatus.approved:
                p.is_test = not p.is_test
                user_id, now_test = p.user_id, p.is_test
                await session.commit()
                await panel.audit(request, "payment_test" if now_test else "payment_real", user_id,
                                  details=f"#{request_id}")
        return RedirectResponse("/payments?status=approved", status_code=303)
    if action in ("approve", "reject"):
        approve = action == "approve"
        # a reason from an older page (none sent) or one not on the list: "Sababsiz"
        reject_reason = None if approve else (reason if reason in REJECT_REASONS else "none")
        async with get_session() as session:
            decision = await crud.decide_payment_request(
                session, request_id, approve=approve, admin_id=WEB_REVIEWER_ID,
                referral_coins=settings.referral_coins, reject_reason=reject_reason,
            )
        if decision.applied:
            who = panel.admin_name(request)
            await panel.audit(request, f"payment_{action}", decision.request.user_id if decision.request else None,
                              details=f"#{request_id}" + (f" · {REJECT_REASONS[reject_reason]}" if reject_reason else ""))
            await crm_events.payment_decided(request_id, by=who)  # the group's card names the panel login
            from bot.handlers.payments import clear_review_cards, decision_line, notify_decision

            line = decision_line(decision.request, f"{who} (web panel)")

            async def tell(bot) -> None:
                # the admins' copies of the card in the bot lose ✅/⛔ (2026-11-16), and the buyer is told
                # as in the bot (a gift's link, a rejection's reason and "send the receipt again")
                await clear_review_cards(bot, request_id, line)
                await notify_decision(bot, decision)

            await _with_bot(tell)
    return RedirectResponse("/payments", status_code=303)


async def _with_bot(send) -> bool:
    """Runs `send(bot)` with a short-lived Bot on the bot's token - the panel
    has no running bot, but tells users about payment decisions and coin
    changes. False when it failed."""
    bot = Bot(settings.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        await send(bot)
        return True
    except Exception as e:  # noqa: BLE001 - the user blocked the bot, ...
        logger.info("Panel message not sent: %r", e)
        return False
    finally:
        await bot.session.close()


# --- Coins by hand: a payment that differed from its pack, a present, a correction ---

MAX_COIN_CHANGE = 100_000
_COIN_FLASH = {
    "added": "Coin qo'shildi.", "removed": "Coin ayirildi.",
    "number": "Butun son kiriting: masalan 6 yoki -2.", "range": f"0 bo'lmagan, ±{MAX_COIN_CHANGE:,} gacha son kiriting.",
    "balance": "Balans manfiy bo'lib qoladi - buncha coin ayirib bo'lmaydi.",
    "cap": "Bir martada ko'pi bilan 50 coin — undan ko'pini faqat egasi qo'sha yoki ayira oladi.",
}


@app.get("/users/{user_id}/coins", response_class=HTMLResponse)
async def user_coins_page(request: Request, user_id: int, note: str = "", done: str | None = None,
                          sent: str | None = None):
    if (redirect := panel.guard(request, perm="coins")) is not None:
        return redirect
    async with get_session() as session:
        user = await crud.get_user(session, user_id)
        history = await crud.list_coin_adjustments(session, user_id) if user is not None else []
    if user is None:
        return RedirectResponse("/users", status_code=303)
    flash = _COIN_FLASH.get(done or "")
    if flash and done in ("added", "removed"):
        flash += " Foydalanuvchiga xabar yuborildi." if sent == "1" else (
            " Xabar yuborilmadi (botni bloklagan bo'lishi mumkin)." if sent == "0" else "")
    return templates.TemplateResponse(request, "user_coins.html", {
        "active": "users", "u": user, "history": history, "note": note, "flash": flash,
        "error": done in ("number", "range", "balance", "cap"),
    })


@app.post("/users/{user_id}/coins")
async def user_coins_submit(request: Request, user_id: int, delta: str = Form(...), note: str = Form(""),
                            notify: str | None = Form(None)):
    if (redirect := panel.guard(request, perm="coins")) is not None:
        return redirect
    back = f"/users/{user_id}/coins"
    try:
        amount = int(delta.replace(" ", "").replace("+", ""))
    except ValueError:
        return RedirectResponse(f"{back}?done=number", status_code=303)
    if amount == 0 or abs(amount) > MAX_COIN_CHANGE:
        return RedirectResponse(f"{back}?done=range", status_code=303)
    if abs(amount) > panel_access.COIN_CAP and not panel.is_owner(request):
        return RedirectResponse(f"{back}?done=cap", status_code=303)
    note = note.strip()[:500]
    async with get_session() as session:
        adjustment = await crud.adjust_coins(session, user_id, amount, note)
        user = await crud.get_user(session, user_id)
    if adjustment is None:
        return RedirectResponse(f"{back}?done=balance", status_code=303)
    await crm_events.coins_adjusted(user_id, amount, adjustment.balance_after, note)
    await panel.audit(request, "coins", user_id,
                      details=f"{amount:+d} → {adjustment.balance_after}" + (f" · {note}" if note else ""))
    done = "added" if amount > 0 else "removed"
    if not notify:
        return RedirectResponse(f"{back}?done={done}", status_code=303)
    lang = user.language if user is not None else None
    text = t("coins.added" if amount > 0 else "coins.removed", lang, coins=abs(amount),
             balance=adjustment.balance_after)
    if note:
        text += "\n\n" + t("coins.note", lang, note=html.escape(note, quote=False))
    sent = await _with_bot(lambda bot: bot.send_message(user_id, text))
    return RedirectResponse(f"{back}?done={done}&sent={int(sent)}", status_code=303)


@app.get("/groups", response_class=HTMLResponse)
async def groups_page(request: Request):
    if (redirect := _require_login(request)) is not None:
        return redirect
    async with get_session() as session:
        groups = await crud.list_groups_with_counts(session)
    return templates.TemplateResponse(request, "groups.html", {"active": "groups", "groups": groups})


# --- Teachers and learning centers ------------------------------------------------


@app.get("/teachers", response_class=HTMLResponse)
async def teachers_page(request: Request, done: str | None = None):
    if (redirect := _require_login(request)) is not None:
        return redirect
    async with get_session() as session:
        teachers = await crud.list_teachers_with_stats(session)
        centers = await crud.list_centers_with_stats(session)
    flash = {"center": "Markaz yaratildi.", "revoked": "O'qituvchilik huquqi olindi.", "deleted": "Markaz o'chirildi."}.get(done or "")
    return templates.TemplateResponse(
        request, "teachers.html", {"active": "teachers", "teachers": teachers, "centers": centers, "flash": flash}
    )


@app.post("/teachers/{user_id}/center")
async def make_center(request: Request, user_id: int, name: str = Form(...)):
    if (redirect := panel.guard(request, perm="users")) is not None:
        return redirect
    name = " ".join(name.split())[:100]
    async with get_session() as session:
        created = name and await crud.get_user_center(session, user_id) is None and await crud.get_user(session, user_id)
        if created:
            await crud.create_center(session, user_id, name)
    if created:
        await crm_events.center_created(user_id, name)
        await panel.audit(request, "center_created", user_id, details=name)
    return RedirectResponse("/teachers?done=center", status_code=303)


@app.post("/teachers/{user_id}/revoke")
async def revoke_teacher(request: Request, user_id: int):
    if (redirect := panel.guard(request, perm="users")) is not None:
        return redirect
    async with get_session() as session:
        user = await crud.get_user(session, user_id)
        revoked = user is not None and user.role == UserRole.teacher
        if revoked:
            await crud.set_user_role(session, user_id, UserRole.student)
    if revoked:
        await crm_events.teacher_revoked(user_id)
        await panel.audit(request, "teacher_revoked", user_id)
    return RedirectResponse("/teachers?done=revoked", status_code=303)


@app.post("/centers/{center_id}/delete")
async def delete_center(request: Request, center_id: int):
    if (redirect := panel.guard(request, perm="users")) is not None:
        return redirect
    async with get_session() as session:
        await crud.delete_center(session, center_id)
    await panel.audit(request, "center_deleted", details=f"#{center_id}")
    return RedirectResponse("/teachers?done=deleted", status_code=303)


# --- Practice question bank (what the bot's 📝 Practice section serves) ------

MODULE_LABELS = {
    PracticeModule.writing_task1: "Writing Task 1",
    PracticeModule.writing_task2: "Writing Task 2",
    PracticeModule.speaking_part1: "Speaking Part 1",
    PracticeModule.speaking_part2: "Speaking Part 2",
    PracticeModule.speaking_part3: "Speaking Part 3",
}
# How the bot uses each module's text (see bot/utils/speaking_questions.py).
MODULE_HINTS = {
    PracticeModule.writing_task1: "Savol matni + grafik/diagramma rasmi. Rasm talabaga savol bilan "
    "birga yuboriladi va baholashda ham hisobga olinadi.",
    PracticeModule.writing_task2: "Insho savolining to'liq matni.",
    PracticeModule.speaking_part1: "Har bir qatorga bitta savol yozing — bot ularni imtihondagidek "
    "birma-bir so'raydi, oxirida hammasini birga baholaydi.",
    PracticeModule.speaking_part2: "Butun matn bitta cue card bo'lib ko'rsatiladi: «Describe ...», "
    "«You should say:», • bandlar, «and explain ...».",
    PracticeModule.speaking_part3: "Har bir qatorga bitta savol yozing — bot ularni imtihondagidek "
    "birma-bir so'raydi, oxirida hammasini birga baholaydi.",
}
# Channels the importer (services/practice_import/) brings questions from.
SOURCE_LABELS = {"cdireport": "@cdireport", "speaking_assistant": "@IELTS_Speaking_Assistant_App"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024


def _parse_module(value: str | None) -> PracticeModule | None:
    try:
        return PracticeModule(value)
    except ValueError:
        return None


def _question_form(
    request: Request,
    *,
    module: PracticeModule,
    question=None,
    topic: str = "",
    question_text: str = "",
    error: str | None = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request,
        "question_form.html",
        {
            "active": "questions",
            "question": question,
            "module": module.value,
            "topic": topic,
            "question_text": question_text,
            "modules": {m.value: label for m, label in MODULE_LABELS.items()},
            # Pre-serialized, like dashboard()'s timeseries_json.
            "hints_json": json.dumps({m.value: hint for m, hint in MODULE_HINTS.items()}),
            "error": error,
        },
        status_code=status_code,
    )


async def _read_image(image: UploadFile | None) -> tuple[bytes | None, str | None]:
    """(content, error) - (None, None) when no file was chosen."""
    if image is None or not image.filename:
        return None, None
    content = await image.read()
    if len(content) > MAX_IMAGE_BYTES:
        return None, "Rasm juda katta (10 MB gacha)."
    return content, None


@app.get("/questions", response_class=HTMLResponse)
async def questions_page(request: Request, module: str | None = None):
    if (redirect := _require_login(request)) is not None:
        return redirect
    module_enum = _parse_module(module) or PracticeModule.writing_task1
    async with get_session() as session:
        questions = await crud.get_practice_questions(session, module_enum)
        counts = await crud.get_practice_question_counts(session)
    return templates.TemplateResponse(
        request,
        "questions.html",
        {
            "active": "questions",
            "module": module_enum.value,
            "module_label": MODULE_LABELS[module_enum],
            "hint": MODULE_HINTS[module_enum],
            "modules": {m.value: label for m, label in MODULE_LABELS.items()},
            "counts": {m.value: c for m, c in counts.items()},
            "questions": questions,
            "source_labels": SOURCE_LABELS,
            "flash": request.session.pop("flash", None),
        },
    )


@app.get("/questions/new", response_class=HTMLResponse)
async def question_new_form(request: Request, module: str | None = None):
    if (redirect := panel.guard(request, perm="content")) is not None:
        return redirect
    return _question_form(request, module=_parse_module(module) or PracticeModule.writing_task1)


@app.post("/questions/new")
async def question_create(
    request: Request,
    module: str = Form(...),
    topic: str = Form(""),
    question_text: str = Form(""),
    image: UploadFile | None = File(None),
):
    if (redirect := panel.guard(request, perm="content")) is not None:
        return redirect
    module_enum = _parse_module(module) or PracticeModule.writing_task1
    topic, question_text = topic.strip(), question_text.strip()

    def form_error(message: str):
        return _question_form(
            request, module=module_enum, topic=topic, question_text=question_text,
            error=message, status_code=400,
        )

    if not question_text:
        return form_error("Savol matni bo'sh bo'lmasligi kerak.")
    content, error = await _read_image(image)
    if error:
        return form_error(error)
    image_path = None
    if content is not None:
        try:
            image_path = save_practice_image(content)
        except ValueError:
            return form_error("Yuklangan fayl rasm emas.")

    async with get_session() as session:
        await crud.create_practice_question(
            session, module=module_enum, question_text=question_text, topic=topic or None,
            image_path=image_path,
        )
    request.session["flash"] = "Savol qo'shildi."
    return RedirectResponse(f"/questions?module={module_enum.value}", status_code=303)


@app.get("/questions/{question_id}/edit", response_class=HTMLResponse)
async def question_edit_form(request: Request, question_id: int):
    if (redirect := panel.guard(request, perm="content")) is not None:
        return redirect
    async with get_session() as session:
        question = await crud.get_practice_question(session, question_id)
    if question is None:
        return RedirectResponse("/questions", status_code=303)
    return _question_form(
        request, module=question.module, question=question, topic=question.topic or "",
        question_text=question.question_text,
    )


@app.post("/questions/{question_id}/edit")
async def question_update(
    request: Request,
    question_id: int,
    module: str = Form(...),
    topic: str = Form(""),
    question_text: str = Form(""),
    remove_image: str | None = Form(None),
    image: UploadFile | None = File(None),
):
    if (redirect := panel.guard(request, perm="content")) is not None:
        return redirect
    async with get_session() as session:
        question = await crud.get_practice_question(session, question_id)
    if question is None:
        return RedirectResponse("/questions", status_code=303)
    module_enum = _parse_module(module) or question.module
    topic, question_text = topic.strip(), question_text.strip()

    def form_error(message: str):
        return _question_form(
            request, module=module_enum, question=question, topic=topic,
            question_text=question_text, error=message, status_code=400,
        )

    if not question_text:
        return form_error("Savol matni bo'sh bo'lmasligi kerak.")
    content, error = await _read_image(image)
    if error:
        return form_error(error)

    old_image = question.image_path
    image_path = None if remove_image else old_image
    if content is not None:
        try:
            image_path = save_practice_image(content)
        except ValueError:
            return form_error("Yuklangan fayl rasm emas.")

    async with get_session() as session:
        await crud.update_practice_question(
            session, question_id, module=module_enum, question_text=question_text,
            topic=topic or None, image_path=image_path,
        )
    if old_image and old_image != image_path:
        delete_file(old_image)
    request.session["flash"] = "O'zgarishlar saqlandi."
    return RedirectResponse(f"/questions?module={module_enum.value}#q{question_id}", status_code=303)


@app.post("/questions/{question_id}/delete")
async def question_delete(request: Request, question_id: int):
    if (redirect := panel.guard(request, perm="content")) is not None:
        return redirect
    async with get_session() as session:
        question = await crud.delete_practice_question(session, question_id)
    if question is None:
        return RedirectResponse("/questions", status_code=303)
    delete_file(question.image_path)
    request.session["flash"] = "Savol o'chirildi."
    return RedirectResponse(f"/questions?module={question.module.value}", status_code=303)


@app.post("/questions/{question_id}/move/{direction}")
async def question_move(request: Request, question_id: int, direction: str):
    if (redirect := panel.guard(request, perm="content")) is not None:
        return redirect
    async with get_session() as session:
        if direction in ("up", "down"):
            await crud.move_practice_question(session, question_id, -1 if direction == "up" else 1)
        question = await crud.get_practice_question(session, question_id)
    module = question.module.value if question else PracticeModule.writing_task1.value
    return RedirectResponse(f"/questions?module={module}#q{question_id}", status_code=303)


@app.get("/questions/{question_id}/image")
async def question_image(request: Request, question_id: int):
    if (redirect := _require_login(request)) is not None:
        return redirect
    async with get_session() as session:
        question = await crud.get_practice_question(session, question_id)
    if question is None or not question.image_path or not Path(question.image_path).exists():
        return Response(status_code=404)
    return FileResponse(question.image_path)
