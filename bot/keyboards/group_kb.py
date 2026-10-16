"""Keyboards for groups and learning centers (bot/handlers/group.py) and their
assignments (bot/handlers/group_assignments.py)."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.i18n import t
from bot.keyboards.nav_kb import CB_BACK_MAIN

# --- groups (gr:) ---
CB_HOME = "gr:home"
CB_CREATE = "gr:create"
CB_REQUEST = "gr:req"
CB_TEACHER_OK_PREFIX = "gr:t_ok:"  # + user id (admin decision)
CB_TEACHER_NO_PREFIX = "gr:t_no:"
CB_MEMBER_HOME = "gr:member"
CB_GROUP_PREFIX = "gr:g:"  # + group id
CB_STATS_PREFIX = "gr:stats:"  # + group id
CB_LIST_PREFIX = "gr:list:"  # + group id:page
CB_STUDENT_PREFIX = "gr:stu:"  # + group id:user id:page
CB_WORK_PREFIX = "gr:work:"  # + group id:user id:w|s:submission id
CB_PDF_PREFIX = "gr:pdf:"  # + w|s:submission id
CB_COMMENT_PREFIX = "gr:cmt:"  # + group id:user id:w|s:submission id
CB_COMMENT_BAND_PREFIX = "gr:cband:"  # + band | "x"
CB_PARENT_PREFIX = "gr:par:"  # + group id:user id
CB_GIFT_PREFIX = "gr:gift:"  # + group id:user id:page
CB_GIFT_DO_PREFIX = "gr:giftdo:"  # + group id:user id:amount
CB_GIFT_ALL_PREFIX = "gr:giftall:"  # + group id
CB_GIFT_ALL_DO_PREFIX = "gr:giftalldo:"  # + group id:amount
CB_KICK_PREFIX = "gr:kick:"  # + group id:user id:page
CB_KICK_DO_PREFIX = "gr:kickdo:"  # + group id:user id
CB_DELETE_PREFIX = "gr:del:"  # + group id
CB_DELETE_DO_PREFIX = "gr:deldo:"  # + group id
CB_LEAVE_PREFIX = "gr:leave:"  # + group id
CB_LEAVE_DO_PREFIX = "gr:leavedo:"  # + group id
CB_SETTINGS_PREFIX = "gr:set:"  # + group id
CB_SPONSOR_PREFIX = "gr:sp:"  # + group id (toggle)
CB_LIMIT_PREFIX = "gr:lim:"  # + group id:limit (0 = none)
CB_BOARD_TOGGLE_PREFIX = "gr:lb:"  # + group id
CB_TOP_PREFIX = "gr:top:"  # + group id
CB_ANNOUNCE_PREFIX = "gr:ann:"  # + group id
CB_ANNOUNCE_SEND = "gr:annok"
CB_QR_PREFIX = "gr:qr:"  # + group id
CB_EXCEL_PREFIX = "gr:xls:"  # + group id
CB_GROUP_PDF_PREFIX = "gr:gpdf:"  # + group id
CB_CABINET = "gr:web"
CB_CENTER = "gr:center"
CB_CENTER_KICK_PREFIX = "gr:ctk:"  # + user id
CB_CENTER_KICK_DO_PREFIX = "gr:ctkdo:"  # + user id

# --- assignments (as:) ---
CB_MY_ASSIGNMENTS = "as:my"
CB_DO_PART_PREFIX = "as:do:"  # + part id
CB_ASSIGNMENTS_PREFIX = "as:list:"  # + group id
CB_NEW_ASSIGNMENT_PREFIX = "as:new:"  # + group id
CB_ASSIGNMENT_TYPE_PREFIX = "as:type:"  # + w1|w2|s1|s2|s3|mock
CB_SOURCE_BANK = "as:bank"
CB_SOURCE_OWN = "as:own"
CB_ANOTHER = "as:another"
CB_USE = "as:use"
CB_DUE_PREFIX = "as:due:"  # + days
CB_VIEW_PREFIX = "as:view:"  # + assignment id
CB_REMIND_PREFIX = "as:remind:"  # + assignment id
CB_ASSIGNMENT_DELETE_PREFIX = "as:del:"  # + assignment id
CB_ASSIGNMENT_DELETE_DO_PREFIX = "as:deldo:"  # + assignment id

GIFT_AMOUNT_OPTIONS = (5, 10, 20, 50)  # coins
LIMIT_OPTIONS = (1, 2, 3, 5)
STUDENTS_PER_PAGE = 10
COMMENT_BANDS = (4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5, 8.0, 8.5, 9.0)
DUE_OPTIONS = (0, 1, 3, 7)  # days from today


def _button(text: str, callback_data: str | None = None, url: str | None = None) -> InlineKeyboardButton:
    if url:
        return InlineKeyboardButton(text=text, url=url)
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def _back_row(lang: str, callback_data: str = CB_BACK_MAIN) -> list[InlineKeyboardButton]:
    return [_button(t("nav.back", lang), callback_data)]


def _kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_kb(lang: str, callback_data: str) -> InlineKeyboardMarkup:
    return _kb([_back_row(lang, callback_data)])


def no_group_kb(lang: str) -> InlineKeyboardMarkup:
    return _kb([[_button(t("group.btn_create", lang), CB_CREATE)], _back_row(lang)])


def request_teacher_kb(lang: str) -> InlineKeyboardMarkup:
    return _kb([[_button(t("group.btn_request", lang), CB_REQUEST)], _back_row(lang, CB_HOME)])


def teacher_review_kb(user_id: int) -> InlineKeyboardMarkup:
    return _kb([[
        _button("✅ O'qituvchi qilish", f"{CB_TEACHER_OK_PREFIX}{user_id}"),
        _button("⛔ Rad etish", f"{CB_TEACHER_NO_PREFIX}{user_id}"),
    ]])


def teacher_home_kb(
    groups: list[tuple[int, str]], has_center: bool, is_member: bool, cabinet: bool, lang: str
) -> InlineKeyboardMarkup:
    """`groups`: (group id, button label)."""
    rows = [[_button(label, f"{CB_GROUP_PREFIX}{gid}")] for gid, label in groups]
    rows.append([_button(t("group.btn_new_group", lang), CB_CREATE)])
    extra = []
    if has_center:
        extra.append(_button(t("group.btn_center", lang), CB_CENTER))
    if cabinet:
        extra.append(_button(t("group.btn_cabinet", lang), CB_CABINET))
    if extra:
        rows.append(extra)
    if is_member:
        rows.append([_button(t("group.btn_my_groups_student", lang), CB_MEMBER_HOME)])
    rows.append(_back_row(lang))
    return _kb(rows)


def group_kb(gid: int, can_pay: bool, lang: str) -> InlineKeyboardMarkup:
    rows = [
        [_button(t("group.btn_stats", lang), f"{CB_STATS_PREFIX}{gid}"), _button(t("group.btn_students", lang), f"{CB_LIST_PREFIX}{gid}:0")],
        [_button(t("group.btn_assignments", lang), f"{CB_ASSIGNMENTS_PREFIX}{gid}"), _button(t("group.btn_top", lang), f"{CB_TOP_PREFIX}{gid}")],
        [_button(t("group.btn_announce", lang), f"{CB_ANNOUNCE_PREFIX}{gid}"), _button(t("group.btn_qr", lang), f"{CB_QR_PREFIX}{gid}")],
        [_button(t("group.btn_excel", lang), f"{CB_EXCEL_PREFIX}{gid}"), _button(t("group.btn_group_pdf", lang), f"{CB_GROUP_PDF_PREFIX}{gid}")],
    ]
    money = [_button(t("group.btn_settings", lang), f"{CB_SETTINGS_PREFIX}{gid}")]
    if can_pay:
        money.insert(0, _button(t("group.btn_gift_all", lang), f"{CB_GIFT_ALL_PREFIX}{gid}"))
    rows.append(money)
    rows.append([_button(t("group.btn_delete", lang), f"{CB_DELETE_PREFIX}{gid}")])
    rows.append(_back_row(lang, CB_HOME))
    return _kb(rows)


def member_home_kb(groups: list[tuple[int, str, bool]], can_create: bool, lang: str) -> InlineKeyboardMarkup:
    """`groups`: (group id, name, leaderboard visible)."""
    rows = [[_button(t("group.btn_my_assignments", lang), CB_MY_ASSIGNMENTS)]]
    for gid, name, board in groups:
        row = []
        if board:
            row.append(_button(t("group.btn_top_named", lang, name=name[:20]), f"{CB_TOP_PREFIX}{gid}"))
        row.append(_button(t("group.btn_leave", lang, name=name[:20]), f"{CB_LEAVE_PREFIX}{gid}"))
        rows.append(row)
    if can_create:
        rows.append([_button(t("group.btn_create", lang), CB_CREATE)])
    rows.append(_back_row(lang))
    return _kb(rows)


def students_kb(gid: int, rows: list[tuple[int, str]], page: int, pages: int, lang: str) -> InlineKeyboardMarkup:
    """`rows`: (student id, button label) for this page."""
    keyboard = [[_button(label, f"{CB_STUDENT_PREFIX}{gid}:{uid}:{page}")] for uid, label in rows]
    nav = []
    if page > 0:
        nav.append(_button("◀️", f"{CB_LIST_PREFIX}{gid}:{page - 1}"))
    if page < pages - 1:
        nav.append(_button("▶️", f"{CB_LIST_PREFIX}{gid}:{page + 1}"))
    if nav:
        keyboard.append(nav)
    keyboard.append(_back_row(lang, f"{CB_GROUP_PREFIX}{gid}"))
    return _kb(keyboard)


def student_card_kb(
    gid: int, uid: int, page: int, works: list[tuple[str, int, str]], can_pay: bool, lang: str
) -> InlineKeyboardMarkup:
    """`works`: (kind w|s, submission id, button label) for the recent works."""
    keyboard = [[_button(label, f"{CB_WORK_PREFIX}{gid}:{uid}:{kind}:{sid}")] for kind, sid, label in works]
    keyboard.append([_button(t("group.btn_parent_report", lang), f"{CB_PARENT_PREFIX}{gid}:{uid}")])
    actions = [_button(t("group.btn_kick", lang), f"{CB_KICK_PREFIX}{gid}:{uid}:{page}")]
    if can_pay:
        actions.insert(0, _button(t("group.btn_gift_one", lang), f"{CB_GIFT_PREFIX}{gid}:{uid}:{page}"))
    keyboard.append(actions)
    keyboard.append(_back_row(lang, f"{CB_LIST_PREFIX}{gid}:{page}"))
    return _kb(keyboard)


def work_kb(gid: int, uid: int, kind: str, sid: int, has_pdf: bool, lang: str) -> InlineKeyboardMarkup:
    rows = []
    if has_pdf:
        rows.append([_button(t("group.btn_report_pdf", lang), f"{CB_PDF_PREFIX}{kind}:{sid}")])
    rows.append([_button(t("group.btn_comment", lang), f"{CB_COMMENT_PREFIX}{gid}:{uid}:{kind}:{sid}")])
    rows.append(_back_row(lang, f"{CB_STUDENT_PREFIX}{gid}:{uid}:0"))
    return _kb(rows)


def comment_band_kb(lang: str) -> InlineKeyboardMarkup:
    bands = [_button(f"{b:.1f}", f"{CB_COMMENT_BAND_PREFIX}{b}") for b in COMMENT_BANDS]
    rows = [bands[i:i + 4] for i in range(0, len(bands), 4)]
    rows.append([_button(t("group.btn_keep_ai_band", lang), f"{CB_COMMENT_BAND_PREFIX}x")])
    return _kb(rows)


def amounts_kb(options: list[int], callback_prefix: str, back: str, lang: str) -> InlineKeyboardMarkup:
    keyboard = [[_button(str(a), f"{callback_prefix}{a}") for a in options]] if options else []
    keyboard.append(_back_row(lang, back))
    return _kb(keyboard)


def confirm_kb(yes_text: str, yes_callback: str, no_callback: str, lang: str) -> InlineKeyboardMarkup:
    return _kb([[_button(yes_text, yes_callback), _button(t("group.btn_cancel", lang), no_callback)]])


def settings_kb(gid: int, sponsor: bool, limit: int | None, board: bool, can_pay: bool, lang: str) -> InlineKeyboardMarkup:
    rows = []
    if can_pay:
        rows.append([_button(t("group.btn_sponsor_off" if sponsor else "group.btn_sponsor_on", lang), f"{CB_SPONSOR_PREFIX}{gid}")])
        if sponsor:
            rows.append(
                [_button(("✅ " if limit == n else "") + str(n), f"{CB_LIMIT_PREFIX}{gid}:{n}") for n in LIMIT_OPTIONS]
                + [_button(("✅ " if limit is None else "") + "∞", f"{CB_LIMIT_PREFIX}{gid}:0")]
            )
    rows.append([_button(t("group.btn_board_off" if board else "group.btn_board_on", lang), f"{CB_BOARD_TOGGLE_PREFIX}{gid}")])
    rows.append(_back_row(lang, f"{CB_GROUP_PREFIX}{gid}"))
    return _kb(rows)


def center_kb(teachers: list[tuple[int, str]], lang: str) -> InlineKeyboardMarkup:
    """`teachers`: (user id, name) of the center's teachers other than the owner."""
    rows = [[_button(f"❌ {name[:30]}", f"{CB_CENTER_KICK_PREFIX}{uid}")] for uid, name in teachers]
    rows.append(_back_row(lang, CB_HOME))
    return _kb(rows)


def cabinet_kb(url: str, lang: str) -> InlineKeyboardMarkup:
    return _kb([[_button(t("group.btn_open_cabinet", lang), url=url)], _back_row(lang, CB_HOME)])


# --- assignments ---


def my_assignments_kb(lang: str) -> InlineKeyboardMarkup:
    return _kb([[_button(t("group.btn_my_assignments", lang), CB_MY_ASSIGNMENTS)]])


def do_part_kb(part_id: int, lang: str, label_key: str = "as.btn_do") -> InlineKeyboardMarkup:
    return _kb([[_button(t(label_key, lang), f"{CB_DO_PART_PREFIX}{part_id}")]])


def assignment_after_kb(next_part_id: int | None, lang: str) -> InlineKeyboardMarkup:
    """After a check that answered an assignment part: the next part of a mock, or the list."""
    if next_part_id:
        return _kb([[_button(t("as.btn_next_part", lang), f"{CB_DO_PART_PREFIX}{next_part_id}")],
                    [_button(t("group.btn_my_assignments", lang), CB_MY_ASSIGNMENTS)]])
    return _kb([[_button(t("group.btn_my_assignments", lang), CB_MY_ASSIGNMENTS)], _back_row(lang)])


def student_assignments_kb(buttons: list[tuple[int, str]], lang: str) -> InlineKeyboardMarkup:
    """`buttons`: (part id to start, label) for unfinished assignments."""
    rows = [[_button(label, f"{CB_DO_PART_PREFIX}{pid}")] for pid, label in buttons]
    rows.append(_back_row(lang, CB_MEMBER_HOME))
    return _kb(rows)


def assignments_kb(gid: int, items: list[tuple[int, str]], lang: str) -> InlineKeyboardMarkup:
    rows = [[_button(t("as.btn_new", lang), f"{CB_NEW_ASSIGNMENT_PREFIX}{gid}")]]
    rows += [[_button(label, f"{CB_VIEW_PREFIX}{aid}")] for aid, label in items]
    rows.append(_back_row(lang, f"{CB_GROUP_PREFIX}{gid}"))
    return _kb(rows)


def assignment_type_kb(gid: int, lang: str) -> InlineKeyboardMarkup:
    return _kb([
        [_button("Writing Task 1", f"{CB_ASSIGNMENT_TYPE_PREFIX}w1"), _button("Writing Task 2", f"{CB_ASSIGNMENT_TYPE_PREFIX}w2")],
        [_button("Speaking 1", f"{CB_ASSIGNMENT_TYPE_PREFIX}s1"), _button("Speaking 2", f"{CB_ASSIGNMENT_TYPE_PREFIX}s2"),
         _button("Speaking 3", f"{CB_ASSIGNMENT_TYPE_PREFIX}s3")],
        [_button(t("as.btn_mock", lang), f"{CB_ASSIGNMENT_TYPE_PREFIX}mock")],
        _back_row(lang, f"{CB_ASSIGNMENTS_PREFIX}{gid}"),
    ])


def source_kb(gid: int, lang: str) -> InlineKeyboardMarkup:
    return _kb([
        [_button(t("as.btn_bank", lang), CB_SOURCE_BANK)],
        [_button(t("as.btn_own", lang), CB_SOURCE_OWN)],
        _back_row(lang, f"{CB_NEW_ASSIGNMENT_PREFIX}{gid}"),
    ])


def pick_kb(gid: int, lang: str) -> InlineKeyboardMarkup:
    return _kb([
        [_button(t("as.btn_use", lang), CB_USE), _button(t("as.btn_another", lang), CB_ANOTHER)],
        _back_row(lang, f"{CB_NEW_ASSIGNMENT_PREFIX}{gid}"),
    ])


def due_kb(gid: int, lang: str) -> InlineKeyboardMarkup:
    labels = {0: "as.due_today", 1: "as.due_tomorrow", 3: "as.due_3", 7: "as.due_7"}
    return _kb([
        [_button(t(labels[d], lang), f"{CB_DUE_PREFIX}{d}") for d in DUE_OPTIONS],
        _back_row(lang, f"{CB_ASSIGNMENTS_PREFIX}{gid}"),
    ])


def assignment_view_kb(aid: int, gid: int, open_: bool, lang: str) -> InlineKeyboardMarkup:
    rows = []
    if open_:
        rows.append([_button(t("as.btn_remind", lang), f"{CB_REMIND_PREFIX}{aid}")])
    rows.append([_button(t("as.btn_delete", lang), f"{CB_ASSIGNMENT_DELETE_PREFIX}{aid}")])
    rows.append(_back_row(lang, f"{CB_ASSIGNMENTS_PREFIX}{gid}"))
    return _kb(rows)
