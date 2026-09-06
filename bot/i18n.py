"""Minimal i18n for the bot's two supported UI languages (uz/ru) - the AI
grading output itself is unaffected (see services/ai/*, which always grades
and comments in English, matching the exam language).

`User.language` (db/models.py) stores the choice, picked once at first
/start (bot/handlers/start.py) and changeable from "Hisobim"/"Профиль"
(bot/handlers/account.py). bot/middlewares/access_control.py injects the
current user's `lang` into every handler's data, so any handler can just
declare a `lang: str` parameter to receive it - aiogram wires it in
automatically, the same way it wires in `message`/`state`.

Exam terminology (Writing/Speaking/Task 1/Task 2/Part 1-3) is left in
English in both languages, matching how IELTS materials are conventionally
written/discussed in Uzbekistan regardless of the surrounding language.
"""
from __future__ import annotations

DEFAULT_LANG = "uz"

_T: dict[str, dict[str, str]] = {
    # --- Language picker (bot/handlers/start.py) ---
    "lang.prompt": {
        "uz": "🌐 Tilni tanlang:",
        "ru": "🌐 Выберите язык:",
    },
    "lang.saved": {
        "uz": "Til o'zbekcha qilib o'rnatildi.",
        "ru": "Установлен русский язык.",
    },
    # --- start.py ---
    "start.admin_welcome": {
        "uz": "Salom, Admin {name}! 👋\nTo'lov so'rovlari shu yerga (Tasdiqlash/Rad etish "
        "tugmalari bilan) tushadi.",
        "ru": "Здравствуйте, Админ {name}! 👋\nЗаявки на оплату будут приходить сюда (с "
        "кнопками Подтвердить/Отклонить).",
    },
    "start.welcome": {
        "uz": "Xush kelibsiz, {name}! 👋\nQuyidagi menyudan bo'limni tanlang:",
        "ru": "Добро пожаловать, {name}! 👋\nВыберите раздел в меню ниже:",
    },
    "start.sample_caption": {
        "uz": "📄 Mana shunday batafsil hisobot olasiz — har bir Writing yoki Speaking "
        "tekshiruvi uchun! Sizga kuniga {free_limit} ta tekshiruv bepul.",
        "ru": "📄 Именно такой подробный отчёт вы получите за каждую проверку Writing или "
        "Speaking! Вам доступно {free_limit} бесплатных проверок в день.",
    },
    # --- main_menu.py ---
    "menu.shown": {
        "uz": "Asosiy menyu:",
        "ru": "Главное меню:",
    },
    # --- Quota / paywall (bot/utils/quota.py) ---
    "quota.paywall": {
        "uz": "⚠️ Bugungi {free_limit} ta bepul tekshiruv imkoniyatingiz tugadi.\n\nDavom "
        "etish uchun har bir tekshiruv (Writing yoki Speaking) — <b>{price} so'm</b>.\n"
        "Xohlagan miqdorni tanlang:",
        "ru": "⚠️ Ваши {free_limit} бесплатных проверки на сегодня закончились.\n\nЧтобы "
        "продолжить, каждая проверка (Writing или Speaking) стоит <b>{price} сум</b>.\n"
        "Выберите нужное количество:",
    },
    # --- Payments (bot/handlers/payments.py, bot/keyboards/payment_kb.py) ---
    "pay.bundle_button": {
        "uz": "{n} ta tekshiruv — {price} so'm",
        "ru": "{n} проверок — {price} сум",
    },
    "pay.subscription_button": {
        "uz": "♾️ Oylik cheksiz — {price} so'm",
        "ru": "♾️ Безлимит на месяц — {price} сум",
    },
    "pay.instructions": {
        "uz": "💳 <b>{amount} so'm</b>ni quyidagi kartaga o'tkazing:\n\n<code>{card}</code>\n"
        "{holder}\n\nO'tkazgandan so'ng, chekning skrinshotini shu yerga <b>rasm</b> qilib "
        "yuboring.",
        "ru": "💳 Переведите <b>{amount} сум</b> на карту:\n\n<code>{card}</code>\n{holder}\n\n"
        "После перевода отправьте сюда скриншот чека <b>картинкой</b>.",
    },
    "pay.choose_amount_first": {
        "uz": "Iltimos, avval 💳 to'lov miqdorini tanlang.",
        "ru": "Пожалуйста, сначала выберите 💳 сумму оплаты.",
    },
    "pay.error_retry": {
        "uz": "Xatolik yuz berdi, iltimos qaytadan urinib ko'ring.",
        "ru": "Произошла ошибка, попробуйте ещё раз.",
    },
    "pay.receipt_accepted": {
        "uz": "✅ Chek qabul qilindi, admin tomonidan tekshirilmoqda. Tez orada javob olasiz.",
        "ru": "✅ Чек принят, администратор проверит его. Скоро получите ответ.",
    },
    "pay.send_photo_only": {
        "uz": "Iltimos, chekning skrinshotini rasm shaklida yuboring.",
        "ru": "Пожалуйста, отправьте скриншот чека именно картинкой.",
    },
    "pay.approved_credits": {
        "uz": "🎉 To'lovingiz tasdiqlandi! Hisobingizga {credits} ta tekshiruv qo'shildi.",
        "ru": "🎉 Ваша оплата подтверждена! На ваш счёт добавлено {credits} проверок.",
    },
    "pay.approved_subscription": {
        "uz": "🎉 To'lovingiz tasdiqlandi! {months} oylik cheksiz obuna faollashtirildi.",
        "ru": "🎉 Ваша оплата подтверждена! Активирована безлимитная подписка на {months} мес.",
    },
    "pay.rejected": {
        "uz": "⛔ To'lovingiz tasdiqlanmadi. Agar bu xato bo'lsa, chekni qayta tekshirib "
        "qaytadan yuboring yoki admin bilan bog'laning.",
        "ru": "⛔ Оплата не подтверждена. Если это ошибка, проверьте чек и отправьте заново "
        "или свяжитесь с администратором.",
    },
    # --- Writing (bot/handlers/writing.py) ---
    "writing.choose_type": {
        "uz": "Qaysi turini tekshirmoqchisiz?",
        "ru": "Какой тип хотите проверить?",
    },
    "writing.send_prompt": {
        "uz": "✏️ Avval savol (prompt) matnini yuboring — matn, rasm, PDF yoki DOCX shaklida.",
        "ru": "✏️ Сначала отправьте текст задания (prompt) — текстом, фото, PDF или DOCX.",
    },
    "writing.could_not_read": {
        "uz": "Afsuski, matnni o'qib bo'lmadi. Iltimos, yana urinib ko'ring yoki matn "
        "shaklida yuboring.",
        "ru": "К сожалению, не удалось прочитать текст. Попробуйте ещё раз или отправьте "
        "текстом.",
    },
    "writing.confirm_preview": {
        "uz": "📄 Men shunday o'qidim:\n\n<i>{preview}</i>\n\nTo'g'rimi?",
        "ru": "📄 Вот что я прочитал:\n\n<i>{preview}</i>\n\nВсё верно?",
    },
    "writing.send_correct_text": {
        "uz": "Iltimos, to'g'ri matnni yozib yuboring:",
        "ru": "Пожалуйста, отправьте правильный текст:",
    },
    "writing.send_answer": {
        "uz": "✅ Qabul qilindi.\n\n✏️ Endi javobingizni yuboring (kamida {min_words} so'z) — "
        "matn, rasm, PDF yoki DOCX shaklida.",
        "ru": "✅ Принято.\n\n✏️ Теперь отправьте свой ответ (минимум {min_words} слов) — "
        "текстом, фото, PDF или DOCX.",
    },
    "writing.grading_in_progress": {
        "uz": "⏳ Tahlil qilinmoqda, biroz kuting...",
        "ru": "⏳ Идёт анализ, немного подождите...",
    },
    "writing.grading_failed": {
        "uz": "❌ Kechirasiz, tahlil qilishda xatolik yuz berdi. Birozdan so'ng qayta "
        "urinib ko'ring.",
        "ru": "❌ Извините, при анализе произошла ошибка. Попробуйте ещё раз чуть позже.",
    },
    "writing.done_caption": {
        "uz": "✅ Tayyor! Umumiy ball: <b>{band}</b>",
        "ru": "✅ Готово! Общий балл: <b>{band}</b>",
    },
    # --- Speaking (bot/handlers/speaking.py) ---
    "speaking.choose_part": {
        "uz": "Qaysi qismini tekshirmoqchisiz?",
        "ru": "Какую часть хотите проверить?",
    },
    "speaking.send_question": {
        "uz": "✏️ Savol matnini yuboring — matn, rasm, PDF yoki DOCX shaklida.",
        "ru": "✏️ Отправьте текст вопроса — текстом, фото, PDF или DOCX.",
    },
    "speaking.send_answer": {
        "uz": "✅ Qabul qilindi.\n\n🎙 Endi javobingizni ovozli xabar, video-xabar yoki "
        "audio fayl shaklida yuboring.",
        "ru": "✅ Принято.\n\n🎙 Теперь отправьте свой ответ голосовым, видео-сообщением "
        "или аудиофайлом.",
    },
    "speaking.audio_only": {
        "uz": "Iltimos, ovozli xabar, video-xabar yoki audio fayl yuboring.",
        "ru": "Пожалуйста, отправьте голосовое, видео-сообщение или аудиофайл.",
    },
    # --- Input extraction (bot/utils/input_extraction.py) ---
    "input.pdf_docx_only": {
        "uz": "Faqat .pdf yoki .docx fayllar qabul qilinadi (yoki matn/rasm yuboring).",
        "ru": "Принимаются только файлы .pdf или .docx (или отправьте текст/фото).",
    },
    "input.send_supported_format": {
        "uz": "Iltimos, matn, rasm, PDF yoki DOCX ko'rinishida yuboring.",
        "ru": "Пожалуйста, отправьте текстом, фото, PDF или DOCX.",
    },
    # --- Practice (bot/handlers/practice.py) ---
    "practice.choose_module": {
        "uz": "Qaysi bo'limda mashq qilmoqchisiz?",
        "ru": "В каком разделе хотите потренироваться?",
    },
    "practice.module_header": {
        "uz": "{label}\n\nBajarilgan: {done}/{total}\nSavolni tanlang:",
        "ru": "{label}\n\nВыполнено: {done}/{total}\nВыберите вопрос:",
    },
    "practice.question_not_found": {
        "uz": "Savol topilmadi, iltimos qaytadan tanlang.",
        "ru": "Вопрос не найден, выберите ещё раз.",
    },
    "practice.writing_question": {
        "uz": "📋 Savol:\n\n{question}\n\n✏️ Javobingizni yuboring (kamida {min_words} "
        "so'z) — matn, rasm, PDF yoki DOCX shaklida.",
        "ru": "📋 Вопрос:\n\n{question}\n\n✏️ Отправьте свой ответ (минимум {min_words} "
        "слов) — текстом, фото, PDF или DOCX.",
    },
    "practice.speaking_question": {
        "uz": "📋 Savol:\n\n{question}\n\n🎙 Javobingizni ovozli xabar, video-xabar yoki "
        "audio fayl shaklida yuboring.",
        "ru": "📋 Вопрос:\n\n{question}\n\n🎙 Отправьте ответ голосовым, видео-сообщением "
        "или аудиофайлом.",
    },
    # --- My Works (bot/handlers/my_works.py) ---
    "mywork.title": {
        "uz": "📂 Mening ishlarim",
        "ru": "📂 Мои работы",
    },
    "mywork.empty": {
        "uz": "Hali hech qanday ish topilmadi. Writing yoki Speaking bo'limidan boshlang!",
        "ru": "Работ пока нет. Начните с раздела Writing или Speaking!",
    },
    "mywork.history_header": {
        "uz": "📜 Oxirgi ishlaringiz (yuklab olish uchun bosing):",
        "ru": "📜 Ваши последние работы (нажмите, чтобы скачать):",
    },
    "mywork.file_not_found": {
        "uz": "Fayl topilmadi.",
        "ru": "Файл не найден.",
    },
    "mywork.file_missing_on_server": {
        "uz": "Afsuski, fayl serverda topilmadi.",
        "ru": "К сожалению, файл не найден на сервере.",
    },
    "mywork.need_more_data": {
        "uz": "📊 Statistikani ko'rsatish uchun kamida 2 ta baholangan ish kerak. Davom eting!",
        "ru": "📊 Для статистики нужно как минимум 2 оценённые работы. Продолжайте!",
    },
    "mywork.chart_ylabel": {"uz": "Band Score", "ru": "Балл (Band)"},
    "mywork.chart_title": {"uz": "Sizning progress'ingiz", "ru": "Ваш прогресс"},
    "mywork.trend_stable": {"uz": "➖ Barqaror", "ru": "➖ Стабильно"},
    "mywork.trend_up": {"uz": "📈 O'sish tendentsiyasi", "ru": "📈 Положительная динамика"},
    "mywork.trend_down": {"uz": "📉 Pasayish tendentsiyasi", "ru": "📉 Отрицательная динамика"},
    "mywork.stats_caption": {
        "uz": "So'nggi ball: <b>{score}</b>\nTendentsiya: {trend}",
        "ru": "Последний балл: <b>{score}</b>\nДинамика: {trend}",
    },
    # --- Account / Hisobim (bot/handlers/account.py) ---
    "account.title": {"uz": "👤 Hisobim", "ru": "👤 Профиль"},
    "account.subscription_active": {
        "uz": "♾️ Obuna faol — yana {days} kun",
        "ru": "♾️ Подписка активна — ещё {days} дн.",
    },
    "account.free_remaining": {
        "uz": "🆓 Bugungi bepul tekshiruvlar: <b>{remaining}/{total}</b> qoldi",
        "ru": "🆓 Бесплатных проверок сегодня: осталось <b>{remaining}/{total}</b>",
    },
    "account.credit_balance": {
        "uz": "💳 Kredit balansi: <b>{credits} ta</b> tekshiruv (muddatsiz)",
        "ru": "💳 Баланс: <b>{credits}</b> проверок (бессрочно)",
    },
    "account.vocab_progress": {
        "uz": "📚 Lug'atim: {learned}/{total} so'z o'rganildi",
        "ru": "📚 Мой словарь: изучено {learned}/{total} слов",
    },
    "account.weakest": {
        "uz": "📉 So'nggi ishlaringizda eng zaif tomoningiz: <b>{label}</b> (o'rtacha "
        "{avg:.1f}).\n💡 {tip}",
        "ru": "📉 Ваша самая слабая сторона в последних работах: <b>{label}</b> (в среднем "
        "{avg:.1f}).\n💡 {tip}",
    },
    "account.price_line": {
        "uz": "Har bir qo'shimcha tekshiruv — {price} so'm.",
        "ru": "Каждая дополнительная проверка — {price} сум.",
    },
    "account.referral": {
        "uz": "🔗 Do'stlaringizni taklif qiling — har bir do'stingiz birinchi tekshiruvni "
        "tugatganda sizga <b>1 ta bepul tekshiruv</b> beriladi ({count} ta taklif "
        "qilingan):\n{link}",
        "ru": "🔗 Приглашайте друзей — когда друг завершит первую проверку, вы получите "
        "<b>1 бесплатную проверку</b> (приглашено: {count}):\n{link}",
    },
    "account.language_button": {"uz": "🌐 Til / Язык", "ru": "🌐 Til / Язык"},
    # --- Groups / B2B (bot/handlers/group.py) ---
    "group.title": {"uz": "🏫 Guruh", "ru": "🏫 Группа"},
    "group.no_group_prompt": {
        "uz": "Siz hali hech qanday guruhga a'zo emassiz.\n\nAgar o'qituvchi bo'lsangiz, o'z "
        "guruhingizni ochib, talabalaringizni taklif qilishingiz va ularning natijalarini "
        "kuzatishingiz mumkin.",
        "ru": "Вы пока не состоите ни в одной группе.\n\nЕсли вы преподаватель, вы можете "
        "создать свою группу, пригласить учеников и отслеживать их результаты.",
    },
    "group.btn_create": {"uz": "➕ Guruh yaratish", "ru": "➕ Создать группу"},
    "group.ask_name": {
        "uz": "Guruh nomini kiriting (masalan: \"IELTS kechki guruh\"):",
        "ru": "Введите название группы (например: \"IELTS вечерняя группа\"):",
    },
    "group.created": {
        "uz": "✅ \"{name}\" guruhi yaratildi!\n\nTalabalaringizni taklif qilish uchun shu "
        "havolani ulashing:\n{link}",
        "ru": "✅ Группа \"{name}\" создана!\n\nОтправьте эту ссылку ученикам, чтобы они "
        "присоединились:\n{link}",
    },
    "group.teacher_view": {
        "uz": "🏫 <b>{name}</b>\n👥 A'zolar: {count}\n\nTaklif havolasi:\n{link}",
        "ru": "🏫 <b>{name}</b>\n👥 Участников: {count}\n\nСсылка-приглашение:\n{link}",
    },
    "group.btn_stats": {"uz": "📊 Statistika", "ru": "📊 Статистика"},
    "group.btn_gift": {"uz": "🎁 Kredit ulashish", "ru": "🎁 Подарить кредиты"},
    "group.stats_header": {
        "uz": "🏫 <b>{name}</b> — statistika\n",
        "ru": "🏫 <b>{name}</b> — статистика\n",
    },
    "group.stats_empty": {
        "uz": "Guruhingizda hali a'zo yo'q. Yuqoridagi havolani talabalaringizga yuboring.",
        "ru": "В группе пока нет участников. Отправьте ссылку выше своим ученикам.",
    },
    "group.stats_row": {
        "uz": "{name} — {total} ta ish, o'rtacha {avg:.1f}, oxirgi faollik: {last_active}",
        "ru": "{name} — {total} работ, средний балл {avg:.1f}, последняя активность: {last_active}",
    },
    "group.stats_row_no_work": {
        "uz": "{name} — hali ish topshirmagan",
        "ru": "{name} — пока не сдал ни одной работы",
    },
    "group.member_view": {
        "uz": "Siz \"{group_name}\" guruhidasiz (o'qituvchi: {teacher_name}).",
        "ru": "Вы состоите в группе \"{group_name}\" (преподаватель: {teacher_name}).",
    },
    "group.gift_no_students": {
        "uz": "Guruhingizda hali a'zo yo'q.",
        "ru": "В вашей группе пока нет участников.",
    },
    "group.gift_choose_student": {
        "uz": "Kimga kredit sovg'a qilmoqchisiz? (Balansingiz: {balance} ta)",
        "ru": "Кому подарить кредиты? (Ваш баланс: {balance})",
    },
    "group.gift_choose_amount": {
        "uz": "{name}ga nechta kredit berasiz?",
        "ru": "Сколько кредитов подарить пользователю {name}?",
    },
    "group.gift_insufficient": {
        "uz": "Balansingizda yetarli kredit yo'q.",
        "ru": "На вашем балансе недостаточно кредитов.",
    },
    "group.gift_success": {
        "uz": "✅ {name}ga {amount} ta kredit berildi!",
        "ru": "✅ Пользователю {name} подарено {amount} кредитов!",
    },
    "group.gift_notify_student": {
        "uz": "🎁 O'qituvchingiz sizga {amount} ta bepul tekshiruv sovg'a qildi!",
        "ru": "🎁 Ваш преподаватель подарил вам {amount} бесплатных проверок!",
    },
    "group.not_found": {"uz": "Guruh topilmadi.", "ru": "Группа не найдена."},
    "group.joined_notice": {
        "uz": "✅ Siz \"{name}\" guruhiga qo'shildingiz!",
        "ru": "✅ Вы присоединились к группе \"{name}\"!",
    },
    # --- Vocabulary (bot/handlers/vocabulary.py) ---
    "vocab.title": {"uz": "📚 Lug'atim", "ru": "📚 Мой словарь"},
    "vocab.empty": {
        "uz": "Hali hech qanday so'z yo'q — Writing yoki Speaking tekshiruvidan so'ng "
        "foydali so'zlar shu yerga avtomatik qo'shiladi.",
        "ru": "Пока слов нет — после проверки Writing или Speaking полезные слова будут "
        "добавляться сюда автоматически.",
    },
    "vocab.summary": {
        "uz": "📚 <b>Lug'atim</b>\n\nJami: {total} ta so'z, o'rganilgan: {learned} ta.",
        "ru": "📚 <b>Мой словарь</b>\n\nВсего: {total} слов, изучено: {learned}.",
    },
    "vocab.all_learned": {
        "uz": "🎉 Barcha so'zlarni o'rgangansiz!",
        "ru": "🎉 Вы изучили все слова!",
    },
    "vocab.ready_to_review": {
        "uz": "Mashq qilish uchun tayyor: {due} ta.",
        "ru": "Готово к повторению: {due}.",
    },
    "vocab.none_left": {
        "uz": "Hozircha mashq qilish uchun so'z qolmadi. 🎉",
        "ru": "Пока нечего повторять. 🎉",
    },
    "vocab.card_progress": {"uz": "({index}/{total})", "ru": "({index}/{total})"},
    "vocab.finished": {
        "uz": "✅ Mashq tugadi! {reviewed} ta so'zni ko'rib chiqdingiz, shundan "
        "{learned} tasini \"bildim\" deb belgiladingiz.",
        "ru": "✅ Повторение завершено! Вы просмотрели {reviewed} слов, из них {learned} "
        "отметили как \"знаю\".",
    },
    "vocab.btn_practice": {"uz": "🔄 Mashq qilish", "ru": "🔄 Повторить"},
    "vocab.btn_reveal": {"uz": "👁 Ma'nosini ko'rish", "ru": "👁 Показать значение"},
    "vocab.btn_know": {"uz": "✅ Bildim", "ru": "✅ Знаю"},
    "vocab.btn_again": {"uz": "🔁 Keyinroq", "ru": "🔁 Позже"},
    # --- Share (bot/handlers/share.py) ---
    "share.not_found": {"uz": "Bu natija topilmadi.", "ru": "Результат не найден."},
    "share.caption": {
        "uz": "Do'stlaringizga ulashing! 📤",
        "ru": "Поделитесь с друзьями! 📤",
    },
    "share.button": {"uz": "📤 Ulashish uchun rasm", "ru": "📤 Изображение для публикации"},
}


def t(key: str, lang: str | None = None, **kwargs: object) -> str:
    """Looks up `key` for `lang` ("uz"/"ru"), falling back to Uzbek (then to
    whatever's available) if the language or the key itself is missing -
    a translation gap degrades gracefully instead of crashing or leaking a
    raw key into the chat."""
    entry = _T.get(key)
    if entry is None:
        return key
    text = entry.get(lang or DEFAULT_LANG) or entry.get(DEFAULT_LANG) or next(iter(entry.values()))
    return text.format(**kwargs) if kwargs else text
