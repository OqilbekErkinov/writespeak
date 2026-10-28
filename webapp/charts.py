"""The web panel's /charts page: the answer key of every Task 1 chart the bot
has met (services/ai/chart_keys.py). An admin opens a chart, corrects its
table against the picture and marks it checked; from then on every student
who sends that chart is judged by the checked table.

Wired into webapp/main.py like webapp/panel.py (`templates` is set there).
Every check and deletion goes to the audit log.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy import case, func, select

from db.database import get_session
from db.models import ChartKey
from services.ai import chart_keys
from services.storage.file_storage import delete_file
from webapp import panel

logger = logging.getLogger(__name__)
router = APIRouter()
templates = None  # set by webapp/main.py

PAGE = 60
STATUS_LABELS = {"auto": "Tekshirilmagan", "verified": "Tekshirilgan", "all": "Hammasi"}
KIND_LABELS = {"bar": "Bar", "line": "Line", "pie": "Pie", "table": "Jadval", "map": "Xarita", "process": "Jarayon",
               "cycle": "Sikl", "mixed": "Aralash"}


def _order():
    """The unchecked first, the most used first: those are judged most often."""
    return case((ChartKey.status == "auto", 0), else_=1), ChartKey.uses.desc(), ChartKey.id.desc()


@router.get("/charts", response_class=HTMLResponse)
async def charts_page(request: Request, status: str = "auto", kind: str = "", page: int = 1):
    if (redirect := panel.guard(request)) is not None:
        return redirect
    status = status if status in STATUS_LABELS else "auto"
    page = max(page, 1)
    query = select(ChartKey)
    if status != "all":
        query = query.where(ChartKey.status == status)
    if kind:
        query = query.where(ChartKey.kind == kind)
    async with get_session() as session:
        keys = (await session.execute(query.order_by(*_order()).offset((page - 1) * PAGE).limit(PAGE + 1))).scalars().all()
        counts = dict((await session.execute(select(ChartKey.status, func.count()).group_by(ChartKey.status))).all())
    return templates.TemplateResponse(request, "charts.html", {
        "active": "charts", "keys": keys[:PAGE], "more": len(keys) > PAGE, "page": page, "status": status, "kind": kind,
        "status_labels": STATUS_LABELS, "kind_labels": KIND_LABELS,
        "counts": {"auto": counts.get("auto", 0), "verified": counts.get("verified", 0),
                   "all": sum(counts.values())},
        "flash": request.session.pop("flash", None),
    })


def _blocks(sheet: dict) -> list[dict]:
    """Each block of the sheet as the edit form shows it."""
    out = []
    for block in sheet.get("blocks", []):
        b = {"type": block.get("visual_type"), "title": block.get("title", ""), "units": block.get("units", ""),
             "overall": block.get("overall", ""), "axis": ", ".join(block.get("axis_labels") or [])}
        if block.get("process"):
            proc = block["process"]
            b["form"] = "process"
            b["steps"] = "\n".join(proc.get("steps", []))
            b["stages"] = "\n".join(
                f"{s['name']}: {', '.join(str(n) for n in s.get('steps', []))}" for s in proc.get("stages", []))
        elif block.get("map_features"):
            b["form"] = "map"
            times = [s["time"] for s in block["map_features"][0].get("states", [])] or block.get("time_points") or []
            b["times"] = times
            b["features"] = [{"name": f["name"], "location": f.get("location", ""),
                              "states": [(s.get("status", "present"), s.get("detail", "")) for s in f.get("states", [])]}
                             for f in block["map_features"]]
        else:
            b["form"] = "grid"
            series, points, cells = chart_keys.grid(block)
            b["points"] = points
            b["rows"] = []
            for s in series:
                row = []
                for p in points:
                    v = cells.get((s, p))
                    if v is None:
                        row.append({"text": "", "unsure": False, "note": ""})
                        continue
                    value = v.get("value")
                    text = v.get("text") or ("" if value is None else f"{value:g}")
                    note = v.get("note") or ""
                    if v.get("precision"):
                        note = f"±{v['precision']:g}" + (f" · {note}" if note else "")
                    row.append({"text": text, "unsure": v.get("status") == "uncertain", "note": note})
                b["rows"].append({"label": s, "cells": row})
        out.append(b)
    return out


@router.get("/charts/{key_id}", response_class=HTMLResponse)
async def chart_page(request: Request, key_id: int):
    if (redirect := panel.guard(request)) is not None:
        return redirect
    async with get_session() as session:
        key = await session.get(ChartKey, key_id)
        if key is None:
            return RedirectResponse("/charts", status_code=303)
        waiting = (await session.execute(select(func.count()).select_from(ChartKey).where(ChartKey.status == "auto"))
                   ).scalar() or 0
    sheet = key.sheet or {}
    return templates.TemplateResponse(request, "chart_edit.html", {
        "active": "charts", "key": key, "blocks": _blocks(sheet), "kind_labels": KIND_LABELS, "waiting": waiting,
        "read_note": sheet.get("readability_note") or "", "read_checks": sheet.get("checks") or [],
        "unreadable": sheet.get("unreadable") or [], "flash": request.session.pop("flash", None),
    })


async def _next_unchecked(after: int) -> int | None:
    async with get_session() as session:
        return (await session.execute(select(ChartKey.id).where(ChartKey.status == "auto", ChartKey.id != after)
                                      .order_by(*_order()).limit(1))).scalar()


@router.post("/charts/{key_id}")
async def chart_save(request: Request, key_id: int):
    if (redirect := panel.guard(request)) is not None:
        return redirect
    form = {k: str(v) for k, v in (await request.form()).items()}
    action = form.pop("action", "save")
    async with get_session() as session:
        key = await session.get(ChartKey, key_id)
    if key is None:
        return RedirectResponse("/charts", status_code=303)
    admin = panel.admin_name(request)
    if action == "unverify":
        async with get_session() as session:
            key = await session.get(ChartKey, key_id)
            key.status, key.verified_by, key.verified_at = "auto", None, None
            await session.commit()
        await panel.audit(request, "chart_key_unverify", details=f"#{key_id}")
        request.session["flash"] = "Tasdiq bekor qilindi: bu grafik endi baholashda ishlatilmaydi."
        return RedirectResponse(f"/charts/{key_id}", status_code=303)
    try:
        sheet = chart_keys.edited_sheet(key.sheet, form)
        verified = action in ("verify", "verify_next")
        await chart_keys.save(key_id, sheet, admin, verified=verified)
    except Exception as e:  # a sheet the schema rejects: nothing is saved
        logger.exception("Chart key #%s not saved", key_id)
        request.session["flash"] = f"Saqlanmadi: {e}"
        return RedirectResponse(f"/charts/{key_id}", status_code=303)
    await panel.audit(request, "chart_key_verify" if verified else "chart_key_edit", details=f"#{key_id}")
    if action == "verify_next" and (nxt := await _next_unchecked(key_id)) is not None:
        request.session["flash"] = f"#{key_id} tasdiqlandi. Keyingi tekshirilmagan grafik:"
        return RedirectResponse(f"/charts/{nxt}", status_code=303)
    request.session["flash"] = (f"#{key_id} tasdiqlandi: shu grafikni yuborgan talabalar endi shu jadval bo'yicha "
                                "baholanadi." if verified else "Saqlandi.")
    return RedirectResponse(f"/charts/{key_id}", status_code=303)


@router.post("/charts/{key_id}/delete")
async def chart_delete(request: Request, key_id: int):
    if (redirect := panel.guard(request)) is not None:
        return redirect
    async with get_session() as session:
        key = await session.get(ChartKey, key_id)
        if key is None:
            return RedirectResponse("/charts", status_code=303)
        image = key.image_path
        await session.delete(key)
        await session.commit()
    delete_file(image)
    await panel.audit(request, "chart_key_delete", details=f"#{key_id}")
    request.session["flash"] = f"#{key_id} o'chirildi."
    return RedirectResponse("/charts", status_code=303)


@router.get("/chart-image/{key_id}")
async def chart_image(request: Request, key_id: int):
    if (redirect := panel.guard(request)) is not None:
        return redirect
    async with get_session() as session:
        key = await session.get(ChartKey, key_id)
    if key is None or not key.image_path or not Path(key.image_path).exists():
        return Response(status_code=404)
    return FileResponse(key.image_path)
