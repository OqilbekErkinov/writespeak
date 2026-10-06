from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.i18n import t
from bot.keyboards.nav_kb import back_button

CB_PART1 = "speaking:part1"
CB_PART2 = "speaking:part2"
CB_PART3 = "speaking:part3"
CB_SPEAKING_MENU = "speaking:menu"  # back to the Part 1/2/3 picker
CB_FINISH_NOW = "speaking:finish"  # grade the answers collected so far

PART_CALLBACKS = {CB_PART1: "part1", CB_PART2: "part2", CB_PART3: "part3"}


def speaking_part_kb(lang: str = "uz") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Part 1", callback_data=CB_PART1),
                InlineKeyboardButton(text="Part 2", callback_data=CB_PART2),
                InlineKeyboardButton(text="Part 3", callback_data=CB_PART3),
            ],
            [back_button(lang)],
        ]
    )


def question_kb(lang: str, back_callback: str, can_finish: bool) -> InlineKeyboardMarkup:
    rows = []
    if can_finish:
        rows.append(
            [InlineKeyboardButton(text=t("speaking.btn_finish_now", lang), callback_data=CB_FINISH_NOW)]
        )
    rows.append([back_button(lang, back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
