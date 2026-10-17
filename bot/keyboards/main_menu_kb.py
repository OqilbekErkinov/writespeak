"""The main menu (reply keyboard), shown to every user. Writing/Speaking/
Practice/My Works stay in English regardless of UI language (see
bot/i18n.py's module docstring) - only the vocab/account/group labels have
Russian and English variants.
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
BTN_VOCAB_EN = "📚 My vocabulary"
BTN_ACCOUNT_UZ = "👤 Hisobim"
BTN_ACCOUNT_RU = "👤 Профиль"
BTN_ACCOUNT_EN = "👤 My account"
BTN_GROUP_UZ = "🏫 Guruh"
BTN_GROUP_RU = "🏫 Группа"
BTN_GROUP_EN = "🏫 Group"
BTN_SUPPORT_UZ = "💬 Admin bilan aloqa"  # bot/handlers/support.py
BTN_SUPPORT_RU = "💬 Связь с админом"
BTN_SUPPORT_EN = "💬 Contact admin"

# Used to guard FSM free-text handlers so tapping a menu button mid-flow
# switches modules instead of being swallowed as prompt/answer text.
MENU_BUTTON_TEXTS = {
    BTN_WRITING,
    BTN_SPEAKING,
    BTN_PRACTICE,
    BTN_MY_WORKS,
    BTN_VOCAB_UZ,
    BTN_VOCAB_RU,
    BTN_VOCAB_EN,
    BTN_ACCOUNT_UZ,
    BTN_ACCOUNT_RU,
    BTN_ACCOUNT_EN,
    BTN_GROUP_UZ,
    BTN_GROUP_RU,
    BTN_GROUP_EN,
    BTN_SUPPORT_UZ,
    BTN_SUPPORT_RU,
    BTN_SUPPORT_EN,
}


def main_menu_kb(lang: str = "uz") -> ReplyKeyboardMarkup:
    vocab_label = {"ru": BTN_VOCAB_RU, "en": BTN_VOCAB_EN}.get(lang, BTN_VOCAB_UZ)
    account_label = {"ru": BTN_ACCOUNT_RU, "en": BTN_ACCOUNT_EN}.get(lang, BTN_ACCOUNT_UZ)
    group_label = {"ru": BTN_GROUP_RU, "en": BTN_GROUP_EN}.get(lang, BTN_GROUP_UZ)
    # My Works and Admin bilan aloqa live in Hisobim since 2026-10-17
    # (bot/handlers/account.py); their old texts still work.
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_WRITING), KeyboardButton(text=BTN_SPEAKING)],
            [KeyboardButton(text=BTN_PRACTICE), KeyboardButton(text=vocab_label)],
            [KeyboardButton(text=account_label), KeyboardButton(text=group_label)],
        ],
        resize_keyboard=True,
        # Collapses after each tap so the section's own (inline) buttons get
        # the screen; still reachable via the keyboard icon, and every
        # section's "⬅️ Orqaga" re-opens it (bot/handlers/main_menu.py).
        one_time_keyboard=True,
    )
