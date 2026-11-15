"""One search box for the panel (the 2026-11-15 audit: a user, a check or a payment could only be found by
scrolling its list): a name, an @username, a Telegram ID, or a number - the check or payment with that
number. Admins only.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_, select

from db.database import get_session
from db.models import PaymentRequest, SpeakingSubmission, User, WritingSubmission
from webapp import insights, panel

router = APIRouter()
templates = None  # set by webapp/main.py

LIMIT = 30


@router.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: str = ""):
    if (r := panel.guard(request)) is not None:
        return r
    if panel.is_teacher(request):
        return RedirectResponse("/checks", status_code=303)
    q = q.strip()[:100]
    found = {"users": [], "checks": [], "payments": []}
    if q:
        number = q.lstrip("#").strip()
        number = int(number) if number.isdigit() and len(number) <= 15 else None
        async with get_session() as session:
            text = q.lstrip("@")
            conditions = [User.full_name.ilike(f"%{text}%"), User.username.ilike(f"%{text}%")]
            if number is not None:
                conditions.append(User.id == number)
            users = (await session.execute(select(User).where(or_(*conditions))
                                           .order_by(User.created_at.desc()).limit(LIMIT))).scalars().all()
            found["users"] = users
            if number is not None and number < 10 ** 9:
                for kind, model in (("w", WritingSubmission), ("s", SpeakingSubmission)):
                    sub = (await session.execute(select(model).options(*insights._light(model))
                                                 .where(model.id == number))).scalar_one_or_none()
                    if sub is not None:
                        row = insights._check_row(sub, kind)
                        row["user"] = await session.get(User, sub.user_id)
                        found["checks"].append(row)
                payment = await session.get(PaymentRequest, number)
                if payment is not None:
                    found["payments"].append({"p": payment, "user": await session.get(User, payment.user_id),
                                              "when": insights.fmt(payment.created_at)})
    total = sum(len(v) for v in found.values())
    return templates.TemplateResponse(request, "search.html", {"active": "search", "q": q, "found": found,
                                                               "total": total})
