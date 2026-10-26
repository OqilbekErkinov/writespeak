from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.i18n import t
from bot.keyboards.nav_kb import back_button

CB_PART1 = "speaking:part1"
CB_PART2 = "speaking:part2"
CB_PART3 = "speaking:part3"
CB_FULL_TEST = "speaking:full"  # Part 1 + 2 + 3 from the question bank, one report
CB_SPEAKING_MENU = "speaking:menu"  # back to the Part 1/2/3 picker
CB_FINISH_NOW = "speaking:finish"  # grade the answers collected so far
CB_LISTEN_PREFIX = "speaking:listen:"  # + "<submission id>:<model answer index>"
# One recording with all the answers (the last prompt's package D)
CB_MODE_EACH = "speaking:mode:each"  # question by question, as before
CB_MODE_ONE = "speaking:mode:one"  # one recording answering the questions shown
CB_NO_QUESTIONS = "speaking:noq"  # one recording whose questions are in it (the examiner's voice)
CB_REC_FINISH = "speaking:rec:finish"
CB_REC_ANYWAY = "speaking:rec:anyway"  # grade a recording that seems too short
CB_SPLIT_OK = "speaking:split:ok"
CB_SPLIT_FIX = "speaking:split:fix"

PART_CALLBACKS = {CB_PART1: "part1", CB_PART2: "part2", CB_PART3: "part3"}


def speaking_part_kb(lang: str = "uz") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Part 1", callback_data=CB_PART1),
                InlineKeyboardButton(text="Part 2", callback_data=CB_PART2),
                InlineKeyboardButton(text="Part 3", callback_data=CB_PART3),
            ],
            [InlineKeyboardButton(text=t("speaking.btn_full_test", lang), callback_data=CB_FULL_TEST)],
            [back_button(lang)],
        ]
    )


def listen_kb(submission_id: int, labels: list[str]) -> InlineKeyboardMarkup:
    """One "🔊 Q1" button per model answer, three to a row."""
    buttons = [
        InlineKeyboardButton(text=f"🔊 {label}", callback_data=f"{CB_LISTEN_PREFIX}{submission_id}:{i}")
        for i, label in enumerate(labels)
    ]
    return InlineKeyboardMarkup(inline_keyboard=[buttons[i:i + 3] for i in range(0, len(buttons), 3)])


def send_question_kb(lang: str, back_callback: str) -> InlineKeyboardMarkup:
    """Under "send the question/s": or a recording that has the questions in it."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("speaking.btn_no_questions", lang), callback_data=CB_NO_QUESTIONS)],
        [back_button(lang, back_callback)],
    ])


def mode_kb(lang: str, back_callback: str, own_recording: bool) -> InlineKeyboardMarkup:
    """Question by question or one recording; `own_recording` (a full test):
    a recording with its own questions, instead of the bank's."""
    rows = [
        [InlineKeyboardButton(text=t("speaking.btn_mode_each", lang), callback_data=CB_MODE_EACH)],
        [InlineKeyboardButton(text=t("speaking.btn_mode_one", lang), callback_data=CB_MODE_ONE)],
    ]
    if own_recording:
        rows.append([InlineKeyboardButton(text=t("speaking.btn_no_questions", lang), callback_data=CB_NO_QUESTIONS)])
    rows.append([back_button(lang, back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def recording_kb(lang: str, back_callback: str, anyway: bool = False) -> InlineKeyboardMarkup:
    finish = (InlineKeyboardButton(text=t("speaking.btn_grade_anyway", lang), callback_data=CB_REC_ANYWAY) if anyway
              else InlineKeyboardButton(text=t("speaking.btn_finish_now", lang), callback_data=CB_REC_FINISH))
    return InlineKeyboardMarkup(inline_keyboard=[[finish], [back_button(lang, back_callback)]])


def split_kb(lang: str, back_callback: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("speaking.btn_split_ok", lang), callback_data=CB_SPLIT_OK),
         InlineKeyboardButton(text=t("speaking.btn_split_fix", lang), callback_data=CB_SPLIT_FIX)],
        [back_button(lang, back_callback)],
    ])


def question_kb(lang: str, back_callback: str, can_finish: bool) -> InlineKeyboardMarkup:
    rows = []
    if can_finish:
        rows.append(
            [InlineKeyboardButton(text=t("speaking.btn_finish_now", lang), callback_data=CB_FINISH_NOW)]
        )
    rows.append([back_button(lang, back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
