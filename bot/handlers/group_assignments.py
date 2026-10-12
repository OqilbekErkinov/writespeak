"""Group assignments: homework (one Writing/Speaking question) and group mock
tests (Writing Task 1 + 2, Speaking Part 1-3), see db.models.Assignment.

Teacher (from a group's screen, bot/handlers/group.py): picks the type, takes
a question from the Practice bank or writes/sends their own (a Task 1 chart
as a photo with the question as its caption), picks a deadline; every
student then gets a "Bajarish" button. The results screen shows each
student's band per part (a mock's Writing+Speaking estimate too) and can
remind those who haven't finished; reminders also go out automatically a day
before the deadline (bot/utils/group_scheduler.py).

Student: "📝 Vazifalarim" lists their open assignments; "Bajarish" runs the
normal Writing/Speaking check with the question already filled in, linked to
the assignment part (writing.py/speaking.py save `assignment_part_id`), and
a mock moves on to its next part. If the group pays for checks, assignment
checks are always covered (never limited by the daily limit).
"""
from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from bot.handlers.speaking import start_question_set
from bot.i18n import t
from bot.keyboards import group_kb as kb
from bot.keyboards.main_menu_kb import MENU_BUTTON_TEXTS
from bot.states.group_states import AssignmentStates
from bot.states.writing_states import WritingStates
from bot.utils.messages import remove_message, show_screen
from bot.utils.quota import check_quota, free_check_note, send_paywall
from db import crud
from db.database import get_session
from db.models import PracticeModule
from services.storage.file_storage import read_file, save_practice_image

logger = logging.getLogger(__name__)
router = Router(name="group_assignments")

TYPES = {
    "w1": [PracticeModule.writing_task1],
    "w2": [PracticeModule.writing_task2],
    "s1": [PracticeModule.speaking_part1],
    "s2": [PracticeModule.speaking_part2],
    "s3": [PracticeModule.speaking_part3],
    "mock": list(crud.MOCK_PARTS),
}
MODULE_LABELS = {
    "writing_task1": "Writing Task 1",
    "writing_task2": "Writing Task 2",
    "speaking_part1": "Speaking Part 1",
    "speaking_part2": "Speaking Part 2",
    "speaking_part3": "Speaking Part 3",
}
CAPTION_LIMIT = 1024
RESULT_ROWS = 40
SEND_PAUSE = 0.05
_TASHKENT = timedelta(hours=5)


def _esc(text: str | None) -> str:
    return html.escape(text or "?", quote=False)


def _band(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}"


def _when(value: datetime) -> str:
    return (value + _TASHKENT).strftime("%d.%m %H:%M")


def _short(text: str, n: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


async def _managed_group(callback: CallbackQuery, group_id: int, lang: str):
    async with get_session() as session:
        group = await crud.get_group(session, group_id)
        if await crud.can_manage_group(session, callback.from_user.id, group):
            return group
    await callback.answer(t("group.report_unavailable", lang), show_alert=True)
    return None


# --- Teacher: list, create ----------------------------------------------------------------------------


async def _list_screen(callback: CallbackQuery, group_id: int, lang: str) -> None:
    async with get_session() as session:
        group = await crud.get_group(session, group_id)
        items = await crud.list_group_assignments(session, group_id)
    now = datetime.now(timezone.utc)
    rows = []
    for item in items:
        a = item["assignment"]
        icon = "🎯" if a.kind == "mock" else "📝"
        state = "" if a.due_at > now else " · " + t("as.closed", lang)
        rows.append((a.id, f"{icon} {_short(a.title, 34)} · {item['complete']}/{item['members']}{state}"))
    text = t("as.list", lang, name=_esc(group.name)) if items else t("as.list_empty", lang, name=_esc(group.name))
    await show_screen(callback, text, kb.assignments_kb(group_id, rows, lang))


@router.callback_query(F.data.startswith(kb.CB_ASSIGNMENTS_PREFIX))
async def list_assignments(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    group_id = int(callback.data.removeprefix(kb.CB_ASSIGNMENTS_PREFIX))
    if await _managed_group(callback, group_id, lang) is None:
        return
    await callback.answer()
    await state.clear()
    await _list_screen(callback, group_id, lang)


@router.callback_query(F.data.startswith(kb.CB_NEW_ASSIGNMENT_PREFIX))
async def new_assignment(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    group_id = int(callback.data.removeprefix(kb.CB_NEW_ASSIGNMENT_PREFIX))
    if await _managed_group(callback, group_id, lang) is None:
        return
    await callback.answer()
    await state.clear()
    await state.update_data(as_group_id=group_id)
    await show_screen(callback, t("as.choose_type", lang), kb.assignment_type_kb(group_id, lang))


async def _pick_parts(modules: list[PracticeModule], exclude: list[int]) -> list[dict] | None:
    parts = []
    async with get_session() as session:
        for module in modules:
            question = await crud.pick_practice_question(session, module, exclude)
            if question is None:
                question = await crud.pick_practice_question(session, module)
            if question is None:
                return None
            parts.append({
                "module": module.value,
                "prompt_text": question.question_text,
                "image_path": question.image_path,
                "practice_question_id": question.id,
            })
    return parts


def _preview(parts: list[dict], lang: str) -> str:
    lines = []
    for i, part in enumerate(parts, start=1):
        image = " 📊" if part.get("image_path") else ""
        prefix = f"{i}. " if len(parts) > 1 else ""
        lines.append(f"{prefix}<b>{MODULE_LABELS[part['module']]}</b>{image}\n{_esc(_short(part['prompt_text'], 400))}")
    return "\n\n".join(lines)


async def _show_pick(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    modules = [PracticeModule(m) for m in data["as_modules"]]
    parts = await _pick_parts(modules, data.get("as_seen", []))
    if parts is None:
        await show_screen(callback, t("as.bank_empty", lang), kb.source_kb(data["as_group_id"], lang))
        return
    seen = data.get("as_seen", []) + [p["practice_question_id"] for p in parts]
    await state.update_data(as_parts=parts, as_seen=seen[-200:])
    await show_screen(callback, t("as.picked", lang) + "\n\n" + _preview(parts, lang), kb.pick_kb(data["as_group_id"], lang))


@router.callback_query(F.data.startswith(kb.CB_ASSIGNMENT_TYPE_PREFIX))
async def choose_type(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    code = callback.data.removeprefix(kb.CB_ASSIGNMENT_TYPE_PREFIX)
    data = await state.get_data()
    if code not in TYPES or "as_group_id" not in data:
        return
    await state.update_data(as_kind="mock" if code == "mock" else "homework", as_modules=[m.value for m in TYPES[code]], as_seen=[])
    if code == "mock":
        await _show_pick(callback, state, lang)
        return
    await show_screen(callback, t("as.choose_source", lang, label=MODULE_LABELS[TYPES[code][0].value]), kb.source_kb(data["as_group_id"], lang))


@router.callback_query(F.data.in_({kb.CB_SOURCE_BANK, kb.CB_ANOTHER}))
async def from_bank(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    if "as_modules" not in await state.get_data():
        return
    await _show_pick(callback, state, lang)


@router.callback_query(F.data == kb.CB_SOURCE_OWN)
async def own_question(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    data = await state.get_data()
    if "as_modules" not in data:
        return
    module = data["as_modules"][0]
    await state.set_state(AssignmentStates.awaiting_prompt)
    key = "as.ask_own_task1" if module == "writing_task1" else ("as.ask_own_speaking" if module.startswith("speaking") else "as.ask_own")
    await show_screen(callback, t(key, lang), kb.back_kb(lang, f"{kb.CB_NEW_ASSIGNMENT_PREFIX}{data['as_group_id']}"))


@router.message(AssignmentStates.awaiting_prompt, ~F.text.in_(MENU_BUTTON_TEXTS))
async def receive_own_question(message: Message, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    text = (message.text or message.caption or "").strip()
    image_path = None
    if message.photo:
        file = await message.bot.download(message.photo[-1].file_id)
        try:
            image_path = save_practice_image(file.read())
        except ValueError:
            image_path = None
    if not text:
        await message.answer(t("as.own_needs_text", lang))
        return
    parts = [{"module": data["as_modules"][0], "prompt_text": text[:4000], "image_path": image_path, "practice_question_id": None}]
    await state.set_state(None)
    await state.update_data(as_parts=parts)
    await message.answer(
        t("as.own_preview", lang) + "\n\n" + _preview(parts, lang),
        reply_markup=kb.confirm_kb(t("as.btn_use", lang), kb.CB_USE, f"{kb.CB_NEW_ASSIGNMENT_PREFIX}{data['as_group_id']}", lang),
    )


@router.callback_query(F.data == kb.CB_USE)
async def choose_due(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    data = await state.get_data()
    if not data.get("as_parts"):
        return
    await show_screen(callback, t("as.choose_due", lang), kb.due_kb(data["as_group_id"], lang))


def _due_at(days: int) -> datetime:
    """23:59 Tashkent, `days` days from today, in UTC."""
    today = (datetime.now(timezone.utc) + _TASHKENT).date() + timedelta(days=days)
    return datetime(today.year, today.month, today.day, 23, 59, tzinfo=timezone.utc) - _TASHKENT


@router.callback_query(F.data.startswith(kb.CB_DUE_PREFIX))
async def create(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    if not data.get("as_parts") or "as_group_id" not in data:
        await callback.answer()
        return
    group = await _managed_group(callback, data["as_group_id"], lang)
    if group is None:
        return
    await callback.answer()
    await state.clear()
    parts = data["as_parts"]
    if data["as_kind"] == "mock":
        title = "Mock test"
    else:
        title = f"{MODULE_LABELS[parts[0]['module']]}: {_short(parts[0]['prompt_text'], 50)}"
    due_at = _due_at(int(callback.data.removeprefix(kb.CB_DUE_PREFIX)))
    async with get_session() as session:
        assignment = await crud.create_assignment(
            session, group_id=group.id, kind=data["as_kind"], title=title, due_at=due_at,
            created_by=callback.from_user.id, parts=parts,
        )
        first = (await crud.get_assignment_parts(session, assignment.id))[0]
        members = await crud.get_group_members(session, group.id)
    sent = 0
    for user, _ in members:
        student_lang = user.language or "uz"
        key = "as.new_mock_notify" if assignment.kind == "mock" else "as.new_notify"
        try:
            await callback.bot.send_message(
                user.id,
                t(key, student_lang, group=_esc(group.name), title=_esc(title), due=_when(due_at)),
                reply_markup=kb.do_part_kb(first.id, student_lang),
            )
            sent += 1
        except Exception:
            logger.info("Assignment notice to %s failed", user.id)
    await callback.message.answer(t("as.created", lang, sent=sent, due=_when(due_at)))
    await _results_screen(callback, assignment.id, lang)


# --- Teacher: results, reminders, deleting -------------------------------------------------------------


async def _results_screen(callback: CallbackQuery, assignment_id: int, lang: str) -> None:
    async with get_session() as session:
        assignment = await crud.get_assignment(session, assignment_id)
        if assignment is None:
            return
        results = await crud.get_assignment_results(session, assignment)
    parts = results["parts"]
    lines = [t(
        "as.results", lang, title=_esc(assignment.title), due=_when(assignment.due_at),
        complete=results["complete"], members=len(results["rows"]),
    )]
    rows = sorted(results["rows"], key=lambda r: (-r["done"], r["user"].full_name))
    for row in rows[:RESULT_ROWS]:
        name = _esc(row["user"].full_name)
        if assignment.kind == "mock":
            bands = " · ".join(_band(b[0]) if b else "—" for b in row["bands"])
            overall = row["mock"]["overall"] if row["mock"] else None
            tail = f" → <b>{_band(overall)}</b>" if overall is not None else ""
            mark = "✅" if row["done"] == len(parts) else ("⏳" if row["done"] else "❌")
            lines.append(f"{mark} {name}: {bands}{tail}")
        else:
            band = row["bands"][0]
            lines.append(f"✅ {name} — {_band(band[0])}" if band else f"❌ {name}")
    if len(rows) > RESULT_ROWS:
        lines.append(t("as.more_in_excel", lang, n=len(rows) - RESULT_ROWS))
    if assignment.kind == "mock":
        lines.append(t("as.mock_legend", lang))
    open_ = assignment.due_at > datetime.now(timezone.utc)
    await show_screen(callback, "\n".join(lines), kb.assignment_view_kb(assignment.id, assignment.group_id, open_, lang))


async def _assignment_for_manager(callback: CallbackQuery, assignment_id: int, lang: str):
    async with get_session() as session:
        assignment = await crud.get_assignment(session, assignment_id)
        group = await crud.get_group(session, assignment.group_id) if assignment else None
        allowed = await crud.can_manage_group(session, callback.from_user.id, group)
    if not allowed:
        await callback.answer(t("group.report_unavailable", lang), show_alert=True)
        return None, None
    return assignment, group


@router.callback_query(F.data.startswith(kb.CB_VIEW_PREFIX))
async def view_assignment(callback: CallbackQuery, lang: str) -> None:
    assignment, _ = await _assignment_for_manager(callback, int(callback.data.removeprefix(kb.CB_VIEW_PREFIX)), lang)
    if assignment is None:
        return
    await callback.answer()
    await _results_screen(callback, assignment.id, lang)


@router.callback_query(F.data.startswith(kb.CB_REMIND_PREFIX))
async def remind_now(callback: CallbackQuery, lang: str) -> None:
    assignment, group = await _assignment_for_manager(callback, int(callback.data.removeprefix(kb.CB_REMIND_PREFIX)), lang)
    if assignment is None:
        return
    async with get_session() as session:
        results = await crud.get_assignment_results(session, assignment)
    pending = [r["user"] for r in results["rows"] if r["done"] < len(results["parts"])]
    sent = 0
    for user in pending:
        student_lang = user.language or "uz"
        try:
            await callback.bot.send_message(
                user.id,
                t("as.reminder", student_lang, title=_esc(assignment.title), group=_esc(group.name), due=_when(assignment.due_at)),
                reply_markup=kb.my_assignments_kb(student_lang),
            )
            sent += 1
        except Exception:
            logger.info("Assignment reminder to %s failed", user.id)
    await callback.answer(t("as.reminded", lang, n=sent), show_alert=True)


@router.callback_query(F.data.startswith(kb.CB_ASSIGNMENT_DELETE_DO_PREFIX))
async def do_delete_assignment(callback: CallbackQuery, lang: str) -> None:
    assignment, group = await _assignment_for_manager(callback, int(callback.data.removeprefix(kb.CB_ASSIGNMENT_DELETE_DO_PREFIX)), lang)
    if assignment is None:
        return
    await callback.answer()
    async with get_session() as session:
        await crud.delete_assignment(session, assignment.id)
    await _list_screen(callback, group.id, lang)


@router.callback_query(F.data.startswith(kb.CB_ASSIGNMENT_DELETE_PREFIX))
async def confirm_delete_assignment(callback: CallbackQuery, lang: str) -> None:
    assignment, _ = await _assignment_for_manager(callback, int(callback.data.removeprefix(kb.CB_ASSIGNMENT_DELETE_PREFIX)), lang)
    if assignment is None:
        return
    await callback.answer()
    await show_screen(
        callback, t("as.delete_confirm", lang, title=_esc(assignment.title)),
        kb.confirm_kb(t("group.btn_delete_yes", lang), f"{kb.CB_ASSIGNMENT_DELETE_DO_PREFIX}{assignment.id}", f"{kb.CB_VIEW_PREFIX}{assignment.id}", lang),
    )


# --- Student: my assignments, doing a part --------------------------------------------------------------


@router.callback_query(F.data == kb.CB_MY_ASSIGNMENTS)
async def my_assignments(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    await callback.answer()
    await state.clear()
    async with get_session() as session:
        items = await crud.get_student_assignments(session, callback.from_user.id)
    now = datetime.now(timezone.utc)
    lines, buttons = [t("as.my_title", lang)], []
    for item in items:
        a, parts, done = item["assignment"], item["parts"], item["done"]
        icon = "🎯" if a.kind == "mock" else "📝"
        if len(done) == len(parts):
            bands = ", ".join(_band(done[p.id]) for p in parts)
            status = t("as.status_done", lang, bands=bands)
        else:
            status = t("as.status_open" if a.due_at > now else "as.status_late", lang, done=len(done), total=len(parts))
            first_open = next(p for p in parts if p.id not in done)
            buttons.append((first_open.id, f"✍️ {_short(a.title, 40)}"))
        lines.append(f"{icon} <b>{_esc(_short(a.title, 70))}</b>\n   {_esc(item['group'].name)} · {t('as.due', lang)} {_when(a.due_at)} · {status}")
    if not items:
        lines.append(t("as.my_empty", lang))
    await show_screen(callback, "\n\n".join(lines), kb.student_assignments_kb(buttons, lang))


@router.callback_query(F.data.startswith(kb.CB_DO_PART_PREFIX))
async def do_part(callback: CallbackQuery, state: FSMContext, lang: str) -> None:
    part_id = int(callback.data.removeprefix(kb.CB_DO_PART_PREFIX))
    async with get_session() as session:
        found = await crud.get_part(session, part_id)
        membership = await crud.get_membership(session, found[1].group_id, callback.from_user.id) if found else None
        parts = await crud.get_assignment_parts(session, found[1].id) if found else []
        bands = await crud.get_part_bands(session, [p.id for p in parts], [callback.from_user.id])
    if found is None or membership is None:
        await callback.answer(t("as.not_available", lang), show_alert=True)
        return
    part, assignment = found
    quota = await check_quota(callback.from_user.id, assignment_group_id=assignment.group_id)
    if not quota.allowed:
        await callback.answer()
        await send_paywall(callback.message, lang)
        return
    await callback.answer()
    await state.clear()
    later = [p for p in parts if p.position > part.position and (callback.from_user.id, p.id) not in bands]
    position = parts.index(part) + 1
    header = t("as.part_header", lang, title=_esc(assignment.title), n=position, total=len(parts)) if len(parts) > 1 \
        else t("as.homework_header", lang, title=_esc(assignment.title))
    common = {
        "assignment_part_id": part.id,
        "next_part_id": later[0].id if later else None,
        "spend_credit": quota.spend_credit,
        "payer_id": quota.payer_id,
    }
    message = callback.message
    await remove_message(message)
    note = free_check_note(quota, lang)
    if part.module.startswith("writing"):
        task_type = part.module.removeprefix("writing_")
        image = part.image_path if part.image_path and Path(part.image_path).exists() else None
        await state.update_data(
            task_type=task_type, prompt_text=part.prompt_text, prompt_source="text",
            prompt_image_paths=[image] if image else [], **common,
        )
        await state.set_state(WritingStates.awaiting_answer)
        text = note + header + "\n\n" + t(
            "practice.writing_question", lang,
            question=_esc(part.prompt_text), min_words="150" if task_type == "task1" else "250",
        )
        markup = kb.back_kb(lang, kb.CB_MY_ASSIGNMENTS)
        if image is None:
            await message.answer(text, reply_markup=markup)
        elif len(text) <= CAPTION_LIMIT:
            await message.answer_photo(BufferedInputFile(read_file(image), filename=Path(image).name), caption=text, reply_markup=markup)
        else:
            await message.answer_photo(BufferedInputFile(read_file(image), filename=Path(image).name))
            await message.answer(text, reply_markup=markup)
        return
    await state.update_data(part=part.module.removeprefix("speaking_"), **common)
    await start_question_set(message, state, part.prompt_text, lang, intro=note + header)
