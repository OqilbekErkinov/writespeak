"""The main menu (reply keyboard), shown to every user. Writing/Speaking/
Practice/My Works stay in English regardless of UI language (see
bot/i18n.py's module docstring) - only the vocab/account/group labels have a
Russian variant.
"""
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

BTN_WRITING = "✍️ Writing"
BTN_SPEAKING = "🎙 Speaking"
BTN_PRACTICE = "📝 Practice"
BTN_MY_WORKS = "📂 My Works"
# BTN_MOCK_TEST retired from the menu on 2026-08-29 per the user - the
# feature (bot/handlers/mock_test.py) is built and DB-backed, just not
# registered in bot/main.py for now. Re-add a row for it when it comes back.
BTN_VOCAB_UZ = "📚 Lug'atim"
BTN_VOCAB_RU = "📚 Мой словарь"
BTN_ACCOUNT_UZ = "👤 Hisobim"
BTN_ACCOUNT_RU = "👤 Профиль"
BTN_GROUP_UZ = "🏫 Guruh"
BTN_GROUP_RU = "🏫 Группа"

# Used to guard FSM free-text handlers so tapping a menu button mid-flow
# switches modules instead of being swallowed as prompt/answer text.
MENU_BUTTON_TEXTS = {
    BTN_WRITING,
    BTN_SPEAKING,
    BTN_PRACTICE,
    BTN_MY_WORKS,
    BTN_VOCAB_UZ,
    BTN_VOCAB_RU,
    BTN_ACCOUNT_UZ,
    BTN_ACCOUNT_RU,
    BTN_GROUP_UZ,
    BTN_GROUP_RU,
}


def main_menu_kb(lang: str = "uz") -> ReplyKeyboardMarkup:
    vocab_label = BTN_VOCAB_RU if lang == "ru" else BTN_VOCAB_UZ
    account_label = BTN_ACCOUNT_RU if lang == "ru" else BTN_ACCOUNT_UZ
    group_label = BTN_GROUP_RU if lang == "ru" else BTN_GROUP_UZ
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_WRITING), KeyboardButton(text=BTN_SPEAKING)],
            [KeyboardButton(text=BTN_PRACTICE), KeyboardButton(text=BTN_MY_WORKS)],
            [KeyboardButton(text=vocab_label), KeyboardButton(text=account_label)],
            [KeyboardButton(text=group_label)],
        ],
        resize_keyboard=True,
    )
