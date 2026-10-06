"""WriteSpeak admin web panel - a read-mostly dashboard over the same
Postgres database the bot uses (db/ package, completely unchanged - this is
a second consumer of it, not a rewrite). Runs as a separate process from the
same Docker image as the bot (see docker-compose.yml's `admin_web` service):

    uvicorn webapp.main:app --host 0.0.0.0 --port 8000 --proxy-headers ...

Auth is a single shared login (WEB_ADMIN_USERNAME/PASSWORD in .env) - a
different surface for the same business owner, not a multi-tenant system.
In production it sits behind the host's nginx at https://admin.writespeak.uz
(see bot/config.py's web panel comment), which passes the real client IP in
X-Forwarded-For for the small brute-force guard below.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from bot.config import settings
from db import crud
from db.database import get_session
from db.models import PaymentStatus, PracticeModule
from services.storage.file_storage import delete_file, save_practice_image

app = FastAPI(title="WriteSpeak Admin")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.web_session_secret,
    https_only=settings.web_cookie_secure,
)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

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
            "flash": request.session.pop("flash", None),
        },
    )


@app.get("/questions/new", response_class=HTMLResponse)
async def question_new_form(request: Request, module: str | None = None):
    if (redirect := _require_login(request)) is not None:
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
    if (redirect := _require_login(request)) is not None:
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
    if (redirect := _require_login(request)) is not None:
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
    if (redirect := _require_login(request)) is not None:
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
    if (redirect := _require_login(request)) is not None:
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
    if (redirect := _require_login(request)) is not None:
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
