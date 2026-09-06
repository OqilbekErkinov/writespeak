from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

CB_PART1 = "speaking:part1"
CB_PART2 = "speaking:part2"
CB_PART3 = "speaking:part3"


def speaking_part_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Part 1", callback_data=CB_PART1),
                InlineKeyboardButton(text="Part 2", callback_data=CB_PART2),
                InlineKeyboardButton(text="Part 3", callback_data=CB_PART3),
            ]
        ]
    )
