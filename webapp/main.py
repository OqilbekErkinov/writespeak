"""WriteSpeak admin web panel - a read-mostly dashboard over the same
Postgres database the bot uses (db/ package, completely unchanged - this is
a second consumer of it, not a rewrite). Runs as a separate process from the
same Docker image as the bot (see docker-compose.yml's `admin_web` service):

    uvicorn webapp.main:app --host 0.0.0.0 --port 8000

Auth is a single shared login (WEB_ADMIN_USERNAME/PASSWORD in .env) - a
different surface for the same business owner, not a multi-tenant system.
No domain/HTTPS yet per the user's 2026-08-29 choice (plain http://<vps-ip>
:port) - see bot/config.py's comment on why the password matters more than
usual here, and the small brute-force guard below.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from bot.config import settings
from db import crud
from db.database import get_session
from db.models import PaymentStatus

app = FastAPI(title="WriteSpeak Admin")
app.add_middleware(SessionMiddleware, secret_key=settings.web_session_secret)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# --- tiny in-memory brute-force guard (no domain/reverse-proxy to do this yet) ---
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


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    ip = _client_ip(request)
    if _too_many_attempts(ip):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Juda ko'p urinish. 15 daqiqadan so'ng qayta urining."},
            status_code=429,
        )
    if username == settings.web_admin_username and password == settings.web_admin_password:
        request.session["logged_in"] = True
        return RedirectResponse("/", status_code=303)
    _record_failure(ip)
    return templates.TemplateResponse(
        request, "login.html", {"error": "Login yoki parol noto'g'ri."}, status_code=401
    )


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    if (redirect := _require_login(request)) is not None:
        return redirect
    async with get_session() as session:
        stats = await crud.get_admin_stats(session)
        timeseries = await crud.get_dashboard_timeseries(session, days=30)
        active_subs = await crud.count_active_subscriptions(session)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "active": "dashboard",
            "stats": stats,
            "active_subs": active_subs,
            # Pre-serialized in Python (not the `tojson` filter) so this doesn't
            # depend on whether Starlette's Jinja2Templates registers it.
            "timeseries_json": json.dumps(timeseries),
        },
    )


@app.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, q: str | None = None):
    if (redirect := _require_login(request)) is not None:
        return redirect
    async with get_session() as session:
        users = await crud.list_users(session, search=q)
    return templates.TemplateResponse(
        request,
        "users.html",
        {
            "active": "users",
            "users": users,
            "q": q or "",
            "now": datetime.now(timezone.utc),
        },
    )


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
        {"active": "payments", "requests": requests_, "status": status or "all"},
    )


@app.post("/payments/{request_id}/{action}")
async def decide_payment_web(request: Request, request_id: int, action: str):
    if (redirect := _require_login(request)) is not None:
        return redirect
    if action in ("approve", "reject"):
        async with get_session() as session:
            await crud.decide_payment_request(
                session, request_id, approve=(action == "approve"), admin_id=WEB_REVIEWER_ID
            )
    return RedirectResponse("/payments", status_code=303)


@app.get("/groups", response_class=HTMLResponse)
async def groups_page(request: Request):
    if (redirect := _require_login(request)) is not None:
        return redirect
    async with get_session() as session:
        groups = await crud.list_groups_with_counts(session)
    return templates.TemplateResponse(request, "groups.html", {"active": "groups", "groups": groups})
