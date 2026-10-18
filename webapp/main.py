"""Bandzilla admin web panel - a read-mostly dashboard over the same
Postgres database the bot uses (db/ package, completely unchanged - this is
a second consumer of it, not a rewrite). Runs as a separate process from the
same Docker image as the bot (see docker-compose.yml's `admin_web` service):

    uvicorn webapp.main:app --host 0.0.0.0 --port 8000 --proxy-headers ...

Auth is a single shared login (WEB_ADMIN_USERNAME/PASSWORD in .env) - a
different surface for the same business owner, not a multi-tenant system.
In production it sits behind the host's nginx at https://admin.bandzilla.uz
(see bot/config.py's web panel comment), which passes the real client IP in
X-Forwarded-For for the small brute-force guard below.
"""
from __future__ import annotations

import html
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from bot.config import settings
from bot.i18n import t
from bot.utils import crm_events
from db import crud
from db.database import get_session
from db.models import PaymentRequest, PaymentStatus, PracticeModule, UserRole
from services.storage.file_storage import delete_file, save_practice_image

logger = logging.getLogger(__name__)
app = FastAPI(title="Bandzilla Admin")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.web_session_secret,
    https_only=settings.web_cookie_secure,
)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Teachers' web cabinet (/cabinet, its own sign-in from the bot) - see webapp/cabinet.py.
from webapp import cabinet  # noqa: E402

cabinet.templates = templates
app.include_router(cabinet.router)

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


# Receipt images by payment request id - a receipt never changes, so the
# Telegram download happens once per image (the newest RECEIPT_CACHE kept).
_receipts: dict[int, bytes] = {}
RECEIPT_CACHE = 60


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
async def payment_receipt(request: Request, request_id: int):
    if (redirect := _require_login(request)) is not None:
        return redirect
    image = _receipts.get(request_id)
    if image is None:
        async with get_session() as session:
            payment = await session.get(PaymentRequest, request_id)
        image = await _download_receipt(payment.receipt_file_id) if payment and payment.receipt_file_id else None
        if image is None:
            return Response(status_code=404)
        _receipts[request_id] = image
        while len(_receipts) > RECEIPT_CACHE:
            _receipts.pop(next(iter(_receipts)))
    return Response(image, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@app.post("/payments/{request_id}/{action}")
async def decide_payment_web(request: Request, request_id: int, action: str):
    if (redirect := _require_login(request)) is not None:
        return redirect
    if action in ("approve", "reject"):
        async with get_session() as session:
            decision = await crud.decide_payment_request(
                session, request_id, approve=(action == "approve"), admin_id=WEB_REVIEWER_ID,
                referral_coins=settings.referral_coins,
            )
        if decision.applied:
            await crm_events.payment_decided(request_id)
            # The buyer is told as in the bot (a gift's link included).
            from bot.handlers.payments import notify_decision

            await _with_bot(lambda bot: notify_decision(bot, decision))
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
}


@app.get("/users/{user_id}/coins", response_class=HTMLResponse)
async def user_coins_page(request: Request, user_id: int, note: str = "", done: str | None = None,
                          sent: str | None = None):
    if (redirect := _require_login(request)) is not None:
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
        "error": done in ("number", "range", "balance"),
    })


@app.post("/users/{user_id}/coins")
async def user_coins_submit(request: Request, user_id: int, delta: str = Form(...), note: str = Form(""),
                            notify: str | None = Form(None)):
    if (redirect := _require_login(request)) is not None:
        return redirect
    back = f"/users/{user_id}/coins"
    try:
        amount = int(delta.replace(" ", "").replace("+", ""))
    except ValueError:
        return RedirectResponse(f"{back}?done=number", status_code=303)
    if amount == 0 or abs(amount) > MAX_COIN_CHANGE:
        return RedirectResponse(f"{back}?done=range", status_code=303)
    note = note.strip()[:500]
    async with get_session() as session:
        adjustment = await crud.adjust_coins(session, user_id, amount, note)
        user = await crud.get_user(session, user_id)
    if adjustment is None:
        return RedirectResponse(f"{back}?done=balance", status_code=303)
    await crm_events.coins_adjusted(user_id, amount, adjustment.balance_after, note)
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
    if (redirect := _require_login(request)) is not None:
        return redirect
    name = " ".join(name.split())[:100]
    async with get_session() as session:
        created = name and await crud.get_user_center(session, user_id) is None and await crud.get_user(session, user_id)
        if created:
            await crud.create_center(session, user_id, name)
    if created:
        await crm_events.center_created(user_id, name)
    return RedirectResponse("/teachers?done=center", status_code=303)


@app.post("/teachers/{user_id}/revoke")
async def revoke_teacher(request: Request, user_id: int):
    if (redirect := _require_login(request)) is not None:
        return redirect
    async with get_session() as session:
        user = await crud.get_user(session, user_id)
        revoked = user is not None and user.role == UserRole.teacher
        if revoked:
            await crud.set_user_role(session, user_id, UserRole.student)
    if revoked:
        await crm_events.teacher_revoked(user_id)
    return RedirectResponse("/teachers?done=revoked", status_code=303)


@app.post("/centers/{center_id}/delete")
async def delete_center(request: Request, center_id: int):
    if (redirect := _require_login(request)) is not None:
        return redirect
    async with get_session() as session:
        await crud.delete_center(session, center_id)
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
