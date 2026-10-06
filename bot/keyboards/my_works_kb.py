from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.keyboards.nav_kb import back_button

CB_MENU = "mywork:menu"
CB_HISTORY = "mywork:history"
CB_STATS = "mywork:stats"
CB_DOWNLOAD_PREFIX = "mywork:dl:"


def my_works_menu_kb(lang: str = "uz") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📜 Tarix / Fayl Vault", callback_data=CB_HISTORY)],
            [InlineKeyboardButton(text="📊 Statistika", callback_data=CB_STATS)],
            [back_button(lang)],
        ]
    )


def history_list_kb(items: list[dict], lang: str = "uz") -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"📥 {item['label']} · {item['date']:%d.%m.%Y} · Band {item['band']}",
                callback_data=f"{CB_DOWNLOAD_PREFIX}{item['type']}:{item['id']}",
            )
        ]
        for item in items
    ]
    rows.append([back_button(lang, CB_MENU)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
