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
        "tekshiruvi uchun!",
        "ru": "📄 Именно такой подробный отчёт вы получите за каждую проверку Writing или "
        "Speaking!",
    },
    "start.free_checks": {
        "uz": "🎁 <b>Sizga {total} ta bepul tekshiruv berildi!</b>\n\n"
        "Bu imkoniyat <b>faqat bir marta</b> beriladi — har kuni yangilanmaydi. Ularni "
        "xohlaganingizcha ishlatishingiz mumkin:\n"
        "• 1 ta Writing + 1 ta Speaking\n"
        "• yoki 2 ta Writing\n"
        "• yoki 2 ta Speaking\n\n"
        "❗️ <b>Iltimos, ularni shunchaki sinab ko'rish uchun tasodifiy matn yoki bir-ikki gap "
        "yuborib sarflab qo'ymang.</b> Haqiqiy IELTS savolini oling (Writing Task 1, Task 2 "
        "yoki Speaking savoli) va javobingizni imtihondagidek to'liq yozing yoki gapiring — "
        "shundagina hisobot sizga haqiqiy foyda beradi.\n\n"
        "ℹ️ Tekshiruv faqat hisobot tayyor bo'lganda hisoblanadi. Bepul tekshiruvlar tugagach, "
        "har bir tekshiruv — {price} so'm.",
        "ru": "🎁 <b>Вам доступно {total} бесплатные проверки!</b>\n\n"
        "Они даются <b>только один раз</b> — каждый день не обновляются. Распределите их как "
        "удобно:\n"
        "• 1 Writing + 1 Speaking\n"
        "• или 2 Writing\n"
        "• или 2 Speaking\n\n"
        "❗️ <b>Пожалуйста, не тратьте их на проверку «просто посмотреть» — случайный текст или "
        "пару фраз.</b> Возьмите настоящее задание IELTS (Writing Task 1, Task 2 или вопрос "
        "Speaking) и ответьте полностью, как на экзамене — только тогда отчёт принесёт "
        "реальную пользу.\n\n"
        "ℹ️ Проверка засчитывается, только когда отчёт готов. Когда бесплатные проверки "
        "закончатся, каждая проверка стоит {price} сум.",
    },
    # --- main_menu.py ---
    "menu.shown": {
        "uz": "Asosiy menyu:",
        "ru": "Главное меню:",
    },
    # --- Navigation (bot/keyboards/nav_kb.py) ---
    "nav.back": {"uz": "⬅️ Orqaga", "ru": "⬅️ Назад"},
    "nav.main_menu": {"uz": "🏠 Asosiy menyu", "ru": "🏠 Главное меню"},
    "nav.next_question": {"uz": "➡️ Keyingi savol", "ru": "➡️ Следующий вопрос"},
    "nav.question_list": {"uz": "📋 Savollar ro'yxati", "ru": "📋 Список вопросов"},
    "nav.check_again": {"uz": "🔁 Yana tekshirish", "ru": "🔁 Проверить ещё"},
    "nav.what_next": {"uz": "Davom etamizmi? 👇", "ru": "Продолжим? 👇"},
    # --- Quota / paywall (bot/utils/quota.py) ---
    "quota.paywall": {
        "uz": "⚠️ {free_limit} ta bepul tekshiruvingiz ishlatib bo'lingan (ular bir martalik "
        "edi).\n\nDavom etish uchun har bir tekshiruv (Writing yoki Speaking) — "
        "<b>{price} so'm</b>.\nXohlagan miqdorni tanlang:",
        "ru": "⚠️ Ваши {free_limit} бесплатные проверки уже использованы (они даются один "
        "раз).\n\nЧтобы продолжить, каждая проверка (Writing или Speaking) стоит "
        "<b>{price} сум</b>.\nВыберите нужное количество:",
    },
    "quota.free_note": {
        "uz": "🆓 <b>Bepul tekshiruv</b> — sizda {left}/{total} ta qoldi, bu ulardan biri. "
        "Bepul tekshiruvlar faqat bir marta beriladi, shuning uchun haqiqiy IELTS savoli va "
        "to'liq javobingizni yuboring.",
        "ru": "🆓 <b>Бесплатная проверка</b> — у вас осталось {left}/{total}, эта — одна из "
        "них. Бесплатные проверки даются только один раз, поэтому отправьте настоящее задание "
        "IELTS и полный ответ.",
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
    # Exact wording requested by the business owner (2026-10-06), same in
    # both UI languages - no format list, since any format is accepted.
    "writing.send_prompt": {
        "uz": "✏️ Please, send the question/s in any format",
        "ru": "✏️ Please, send the question/s in any format",
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
    "confirm.ok": {"uz": "✅ To'g'ri", "ru": "✅ Верно"},
    "confirm.edit": {"uz": "✏️ Tahrirlash", "ru": "✏️ Исправить"},
    "writing.send_correct_text": {
        "uz": "Iltimos, to'g'ri matnni yozib yuboring:",
        "ru": "Пожалуйста, отправьте правильный текст:",
    },
    "writing.send_answer": {
        "uz": "✅ Qabul qilindi.\n\n✏️ Endi javobingizni yuboring (kamida {min_words} so'z).",
        "ru": "✅ Принято.\n\n✏️ Теперь отправьте свой ответ (минимум {min_words} слов).",
    },
    "writing.grading_in_progress": {
        "uz": "⏳ Tahlil qilinmoqda — bu 1-2 daqiqa davom etadi, biroz kuting...",
        "ru": "⏳ Идёт анализ — это займёт 1-2 минуты, немного подождите...",
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
        "uz": "✏️ Please, send the question/s in any format",
        "ru": "✏️ Please, send the question/s in any format",
    },
    # One question of a multi-question set (Part 1/3), asked one at a time
    # like a real examiner - see bot/handlers/speaking.py's _ask_current_question.
    "speaking.question_n": {
        "uz": "❓ <b>Savol {n}/{total}</b>\n\n{question}\n\n🎙 Javobingizni ovozli xabar qilib "
        "yuboring.",
        "ru": "❓ <b>Вопрос {n}/{total}</b>\n\n{question}\n\n🎙 Отправьте ответ голосовым "
        "сообщением.",
    },
    "speaking.question_single": {
        "uz": "❓ <b>Savol</b>\n\n{question}\n\n🎙 Javobingizni ovozli xabar qilib yuboring.",
        "ru": "❓ <b>Вопрос</b>\n\n{question}\n\n🎙 Отправьте ответ голосовым сообщением.",
    },
    "speaking.cue_card": {
        "uz": "🗂 <b>Cue card</b>\n\n{question}\n\n⏱ 1 daqiqa tayyorlaning, so'ng 1-2 daqiqa "
        "gapiring.\n🎙 Javobingizni ovozli xabar qilib yuboring.",
        "ru": "🗂 <b>Cue card</b>\n\n{question}\n\n⏱ 1 минута на подготовку, затем говорите "
        "1-2 минуты.\n🎙 Отправьте ответ голосовым сообщением.",
    },
    "speaking.btn_finish_now": {
        "uz": "✅ Yakunlash va baholash",
        "ru": "✅ Завершить и оценить",
    },
    "speaking.audio_only": {
        "uz": "🎙 Iltimos, javobingizni ovozli xabar, video-xabar yoki audio fayl qilib yuboring.",
        "ru": "🎙 Пожалуйста, отправьте ответ голосовым, видео-сообщением или аудиофайлом.",
    },
    # --- Input extraction (bot/utils/input_extraction.py) ---
    "input.reading": {
        "uz": "⏳ O'qilmoqda...",
        "ru": "⏳ Читаю...",
    },
    "input.unsupported_file": {
        "uz": "Bu fayl turini o'qib bo'lmadi. Iltimos, boshqa shaklda yuboring (masalan, matn "
        "yoki rasm qilib).",
        "ru": "Не удалось прочитать файл такого типа. Пожалуйста, отправьте в другом виде "
        "(например, текстом или картинкой).",
    },
    "input.file_too_big": {
        "uz": "Fayl juda katta (Telegram botlar uchun chegara — 20 MB). Iltimos, kichikroq "
        "fayl yoki rasm yuboring.",
        "ru": "Файл слишком большой (лимит Telegram для ботов — 20 МБ). Пожалуйста, отправьте "
        "файл поменьше или картинку.",
    },
    "input.send_supported_format": {
        "uz": "Bu xabardan matn olib bo'lmadi. Iltimos, boshqa shaklda yuboring.",
        "ru": "Не удалось извлечь текст из этого сообщения. Пожалуйста, отправьте в другом виде.",
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
        "uz": "📋 Savol:\n\n{question}\n\n✏️ Javobingizni yuboring (kamida {min_words} so'z).",
        "ru": "📋 Вопрос:\n\n{question}\n\n✏️ Отправьте свой ответ (минимум {min_words} слов).",
    },
    "practice.speaking_topic": {
        "uz": "<b>{label}</b> — {topic}",
        "ru": "<b>{label}</b> — {topic}",
    },
    "practice.no_questions": {
        "uz": "Bu bo'limda hozircha savollar yo'q.",
        "ru": "В этом разделе пока нет вопросов.",
    },
    "practice.all_done": {
        "uz": "🎉 Bu bo'limdagi oxirgi savol edi! Ro'yxatdan boshqa savolni tanlashingiz mumkin.",
        "ru": "🎉 Это был последний вопрос в разделе! Можете выбрать другой вопрос из списка.",
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
        "uz": "🆓 Bepul tekshiruvlar (bir martalik): <b>{remaining}/{total}</b> qoldi",
        "ru": "🆓 Бесплатные проверки (разовые): осталось <b>{remaining}/{total}</b>",
    },
    "account.credit_balance": {
        "uz": "💳 Kredit balansi: <b>{credits} ta</b> tekshiruv (muddatsiz)",
        "ru": "💳 Баланс: <b>{credits}</b> проверок (бессрочно)",
    },
    "account.vocab_progress": {
        "uz": "📚 Lug'atim: {learned}/{total} so'z o'zlashtirildi",
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
    # --- Groups / B2B (bot/handlers/group.py, bot/handlers/start.py) ---
    "group.no_group": {
        "uz": "🏫 <b>Guruh</b>\n\nSiz hali hech qanday guruhda emassiz.\n\n"
        "• <b>Talaba bo'lsangiz:</b> o'qituvchingiz bergan taklif havolasini bosing.\n"
        "• <b>O'qituvchi bo'lsangiz:</b> o'z guruhingizni oching — talabalaringizning "
        "natijalari va hisobotlarini kuzatasiz, ularga kredit ulashasiz.",
        "ru": "🏫 <b>Группа</b>\n\nВы пока не состоите ни в одной группе.\n\n"
        "• <b>Если вы ученик:</b> откройте ссылку-приглашение от преподавателя.\n"
        "• <b>Если вы преподаватель:</b> создайте свою группу — будете видеть результаты и "
        "отчёты учеников и сможете дарить им кредиты.",
    },
    "group.btn_create": {"uz": "➕ Guruh yaratish", "ru": "➕ Создать группу"},
    "group.teachers_only": {
        "uz": "🏫 Guruh ochish <b>o'qituvchilar</b> uchun.\n\nO'qituvchi ekaningizni "
        "tasdiqlash uchun so'rov yuboring — admin tasdiqlagach, sizga xabar keladi va guruh "
        "ochishingiz mumkin bo'ladi.",
        "ru": "🏫 Создавать группы могут <b>преподаватели</b>.\n\nОтправьте запрос — после "
        "подтверждения администратором вы получите сообщение и сможете создать группу.",
    },
    "group.btn_request": {"uz": "📨 So'rov yuborish", "ru": "📨 Отправить запрос"},
    "group.request_sent": {
        "uz": "✅ So'rovingiz adminga yuborildi. Tasdiqlangach, sizga xabar keladi.",
        "ru": "✅ Запрос отправлен администратору. После подтверждения вы получите сообщение.",
    },
    "group.teacher_approved": {
        "uz": "🎉 Siz <b>o'qituvchi</b> sifatida tasdiqlandingiz!\n\nEndi 🏫 Guruh → ➕ Guruh "
        "yaratish orqali guruhingizni ochishingiz mumkin.",
        "ru": "🎉 Вы подтверждены как <b>преподаватель</b>!\n\nТеперь создайте группу: "
        "🏫 Группа → ➕ Создать группу.",
    },
    "group.teacher_rejected": {
        "uz": "Afsuski, o'qituvchi bo'lish so'rovingiz tasdiqlanmadi. Savollar bo'lsa, admin "
        "bilan bog'laning.",
        "ru": "К сожалению, ваш запрос преподавателя не подтверждён. Если есть вопросы, "
        "свяжитесь с администратором.",
    },
    "group.ask_name": {
        "uz": "Guruh nomini yozing (masalan: \"IELTS kechki guruh\"):",
        "ru": "Напишите название группы (например: \"IELTS вечерняя группа\"):",
    },
    "group.created": {"uz": "✅ Guruh yaratildi!", "ru": "✅ Группа создана!"},
    "group.teacher_home": {
        "uz": "🏫 <b>{name}</b>\n👥 Talabalar: {count}\n💳 Kredit balansingiz: {balance} ta\n\n"
        "🔗 <b>Taklif havolasi</b> (bosib nusxa oling va talabalaringizga yuboring):\n"
        "<code>{link}</code>\n\n"
        "ℹ️ Talabaning faqat guruhga qo'shilganidan keyingi ishlarini ko'rasiz.",
        "ru": "🏫 <b>{name}</b>\n👥 Учеников: {count}\n💳 Ваш баланс: {balance} кредитов\n\n"
        "🔗 <b>Ссылка-приглашение</b> (нажмите, чтобы скопировать, и отправьте ученикам):\n"
        "<code>{link}</code>\n\n"
        "ℹ️ Вы видите работы ученика только с момента его вступления в группу.",
    },
    "group.btn_stats": {"uz": "📊 Statistika", "ru": "📊 Статистика"},
    "group.btn_students": {"uz": "👥 Talabalar", "ru": "👥 Ученики"},
    "group.btn_gift_all": {"uz": "🎁 Hammaga kredit ulashish", "ru": "🎁 Подарить кредиты всем"},
    "group.btn_delete": {"uz": "🗑 Guruhni o'chirish", "ru": "🗑 Удалить группу"},
    "group.btn_cancel": {"uz": "Bekor qilish", "ru": "Отмена"},
    "group.member_home_title": {"uz": "🏫 <b>Guruhlaringiz</b>\n", "ru": "🏫 <b>Ваши группы</b>\n"},
    "group.member_home_row": {
        "uz": "• <b>{name}</b> — o'qituvchi: {teacher}",
        "ru": "• <b>{name}</b> — преподаватель: {teacher}",
    },
    "group.member_home_note": {
        "uz": "\nℹ️ O'qituvchingiz guruhga qo'shilganingizdan keyingi natijalaringiz va "
        "hisobotlaringizni ko'radi.",
        "ru": "\nℹ️ Преподаватель видит ваши результаты и отчёты с момента вступления в группу.",
    },
    "group.btn_leave": {"uz": "🚪 Chiqish: {name}", "ru": "🚪 Выйти: {name}"},
    "group.no_students": {
        "uz": "Guruhingizda hali talaba yo'q. Taklif havolasini talabalaringizga yuboring.",
        "ru": "В группе пока нет учеников. Отправьте им ссылку-приглашение.",
    },
    "group.stats": {
        "uz": "📊 <b>{name}</b> — umumiy statistika\n"
        "<i>Talabalar guruhga qo'shilgandan keyingi ishlar</i>\n\n"
        "👥 Talabalar: {members} · so'nggi 7 kunda faol: {active}\n"
        "📝 Tekshiruvlar: {total} (Writing {writing}, Speaking {speaking})\n"
        "📈 O'rtacha ball: Writing {writing_avg} · Speaking {speaking_avg}\n"
        "📉 Guruhning eng zaif mezoni: {weakest}\n"
        "🏆 Eng faol: {most_active}\n"
        "😴 Hali ish topshirmagan: {inactive} ta",
        "ru": "📊 <b>{name}</b> — общая статистика\n"
        "<i>Работы с момента вступления учеников в группу</i>\n\n"
        "👥 Учеников: {members} · активны за 7 дней: {active}\n"
        "📝 Проверок: {total} (Writing {writing}, Speaking {speaking})\n"
        "📈 Средний балл: Writing {writing_avg} · Speaking {speaking_avg}\n"
        "📉 Самый слабый критерий группы: {weakest}\n"
        "🏆 Самые активные: {most_active}\n"
        "😴 Ещё не сдали работ: {inactive}",
    },
    "group.students_title": {
        "uz": "👥 <b>Talabalar</b> ({count}) — {page}/{pages}-sahifa\n\nTalabani tanlang:",
        "ru": "👥 <b>Ученики</b> ({count}) — страница {page}/{pages}\n\nВыберите ученика:",
    },
    "group.student_row": {
        "uz": "{name} · {total} ta ish · {avg}",
        "ru": "{name} · работ: {total} · {avg}",
    },
    "group.student_card": {
        "uz": "👤 <b>{name}</b>\n📅 Guruhga qo'shilgan: {joined}\n\n"
        "✍️ Writing: {writing} ta · o'rtacha {writing_avg}\n"
        "🎙 Speaking: {speaking} ta · o'rtacha {speaking_avg}\n"
        "📉 Eng zaif mezon: {weakest}\n"
        "🕒 Oxirgi faollik: {last}",
        "ru": "👤 <b>{name}</b>\n📅 В группе с: {joined}\n\n"
        "✍️ Writing: {writing} · средний {writing_avg}\n"
        "🎙 Speaking: {speaking} · средний {speaking_avg}\n"
        "📉 Самый слабый критерий: {weakest}\n"
        "🕒 Последняя активность: {last}",
    },
    "group.reports_hint": {
        "uz": "📄 Oxirgi ishlari — hisobotini olish uchun bosing:",
        "ru": "📄 Последние работы — нажмите, чтобы получить отчёт:",
    },
    "group.no_works_yet": {
        "uz": "Guruhga qo'shilganidan beri hali ish topshirmagan.",
        "ru": "С момента вступления в группу ещё не сдал ни одной работы.",
    },
    "group.report_unavailable": {
        "uz": "Bu hisobot mavjud emas.",
        "ru": "Этот отчёт недоступен.",
    },
    "group.btn_gift_one": {"uz": "🎁 Kredit berish", "ru": "🎁 Подарить кредиты"},
    "group.btn_kick": {"uz": "🚪 Guruhdan chiqarish", "ru": "🚪 Исключить"},
    "group.gift_one": {
        "uz": "🎁 <b>{name}</b>ga nechta kredit berasiz?\nBalansingiz: {balance} ta",
        "ru": "🎁 Сколько кредитов подарить: <b>{name}</b>?\nВаш баланс: {balance}",
    },
    "group.gift_all": {
        "uz": "🎁 Har bir talabaga nechta kredit berasiz?\nTalabalar: {count} · balansingiz: {balance} ta",
        "ru": "🎁 Сколько кредитов подарить каждому ученику?\nУчеников: {count} · ваш баланс: {balance}",
    },
    "group.gift_no_balance": {
        "uz": "Balansingizda kredit yo'q. Talabalarga ulashish uchun avval kredit sotib oling:",
        "ru": "На балансе нет кредитов. Чтобы дарить их ученикам, сначала купите кредиты:",
    },
    "group.gift_all_not_enough": {
        "uz": "Hammaga kamida 1 tadan berish uchun {count} ta kredit kerak, balansingizda "
        "{balance} ta. Kredit sotib oling:",
        "ru": "Чтобы дать всем хотя бы по 1, нужно {count} кредитов, у вас {balance}. Купите кредиты:",
    },
    "group.gift_failed": {
        "uz": "Kredit berilmadi: balans yetarli emas yoki talaba guruhda emas.",
        "ru": "Кредиты не переданы: недостаточно баланса или ученик не в группе.",
    },
    "group.gift_done_one": {
        "uz": "✅ <b>{name}</b>ga {amount} ta kredit berildi!",
        "ru": "✅ Подарено {amount} кредитов: <b>{name}</b>!",
    },
    "group.gift_done_all": {
        "uz": "✅ {count} ta talabaga {amount} tadan kredit berildi (jami {total} ta).",
        "ru": "✅ {count} ученикам подарено по {amount} кредитов (всего {total}).",
    },
    "group.gift_notify_student": {
        "uz": "🎁 O'qituvchingiz sizga {amount} ta tekshiruv sovg'a qildi!",
        "ru": "🎁 Ваш преподаватель подарил вам {amount} проверок!",
    },
    "group.kick_confirm": {
        "uz": "🚪 <b>{name}</b>ni guruhdan chiqarasizmi?\n\nUning ishlari va kreditlari o'zida "
        "qoladi, siz endi uning natijalarini ko'rmaysiz.",
        "ru": "🚪 Исключить <b>{name}</b> из группы?\n\nЕго работы и кредиты останутся у него, "
        "но вы больше не будете видеть его результаты.",
    },
    "group.btn_kick_yes": {"uz": "Ha, chiqarish", "ru": "Да, исключить"},
    "group.kick_done": {
        "uz": "✅ <b>{name}</b> guruhdan chiqarildi.",
        "ru": "✅ <b>{name}</b> исключён из группы.",
    },
    "group.kicked_notify": {
        "uz": "ℹ️ Siz \"{group}\" guruhidan chiqarildingiz.",
        "ru": "ℹ️ Вы исключены из группы \"{group}\".",
    },
    "group.delete_confirm": {
        "uz": "🗑 \"<b>{name}</b>\" guruhini o'chirasizmi?\n\nBarcha talabalar guruhdan "
        "chiqariladi (ularning ishlari va kreditlari o'zlarida qoladi). Bu amalni qaytarib "
        "bo'lmaydi.",
        "ru": "🗑 Удалить группу \"<b>{name}</b>\"?\n\nВсе ученики будут исключены (их работы "
        "и кредиты останутся у них). Это действие нельзя отменить.",
    },
    "group.btn_delete_yes": {"uz": "Ha, o'chirish", "ru": "Да, удалить"},
    "group.deleted": {"uz": "✅ Guruh o'chirildi.", "ru": "✅ Группа удалена."},
    "group.leave_confirm": {
        "uz": "🚪 \"<b>{name}</b>\" guruhidan chiqasizmi? O'qituvchingiz endi natijalaringizni "
        "ko'rmaydi.",
        "ru": "🚪 Выйти из группы \"<b>{name}</b>\"? Преподаватель больше не будет видеть ваши "
        "результаты.",
    },
    "group.btn_leave_yes": {"uz": "Ha, chiqish", "ru": "Да, выйти"},
    "group.left": {"uz": "✅ Guruhdan chiqdingiz.", "ru": "✅ Вы вышли из группы."},
    "group.left_notify_teacher": {
        "uz": "ℹ️ <b>{name}</b> guruhingizdan chiqdi.",
        "ru": "ℹ️ <b>{name}</b> вышел(а) из вашей группы.",
    },
    "group.joined_notice": {
        "uz": "✅ Siz \"<b>{name}</b>\" guruhiga qo'shildingiz (o'qituvchi: {teacher}).\n\n"
        "ℹ️ O'qituvchingiz bundan keyingi natijalaringiz va hisobotlaringizni ko'ra oladi.",
        "ru": "✅ Вы вступили в группу \"<b>{name}</b>\" (преподаватель: {teacher}).\n\n"
        "ℹ️ Преподаватель будет видеть ваши результаты и отчёты с этого момента.",
    },
    "group.join_own_link": {
        "uz": "ℹ️ Bu sizning guruhingizning taklif havolasi — uni talabalaringizga yuboring.",
        "ru": "ℹ️ Это ссылка-приглашение вашей группы — отправьте её ученикам.",
    },
    "group.join_expired": {
        "uz": "⚠️ Bu taklif havolasi eskirgan yoki noto'g'ri. O'qituvchingizdan yangi havolani so'rang.",
        "ru": "⚠️ Эта ссылка-приглашение устарела или неверна. Попросите у преподавателя новую.",
    },
    "group.new_member_notify": {
        "uz": "👋 Guruhingizga yangi talaba qo'shildi: <b>{name}</b> (jami: {count}).",
        "ru": "👋 В вашу группу вступил новый ученик: <b>{name}</b> (всего: {count}).",
    },
    # --- Vocabulary (bot/handlers/vocabulary.py) ---
    "vocab.title": {"uz": "📚 Lug'atim", "ru": "📚 Мой словарь"},
    "vocab.empty": {
        "uz": "📚 <b>Lug'atim</b>\n\nHali so'z yo'q. Har bir Writing yoki Speaking tekshiruvidan "
        "so'ng hisobotdagi foydali so'zlar shu yerga o'zi qo'shiladi.\n\nO'zingiz ham so'z "
        "qo'shishingiz mumkin 👇",
        "ru": "📚 <b>Мой словарь</b>\n\nПока слов нет. После каждой проверки Writing или Speaking "
        "полезные слова из отчёта попадают сюда сами.\n\nМожно добавить и свои слова 👇",
    },
    "vocab.summary": {
        "uz": "📚 <b>Lug'atim</b> — {total} ta so'z\n\n🆕 Yangi: {new}\n🔄 O'rganilmoqda: "
        "{learning}\n✅ O'zlashtirilgan: {mastered}",
        "ru": "📚 <b>Мой словарь</b> — {total} слов\n\n🆕 Новые: {new}\n🔄 Изучаются: "
        "{learning}\n✅ Выучены: {mastered}",
    },
    "vocab.due_now": {
        "uz": "⏰ Bugun takrorlash kerak: <b>{due} ta</b>",
        "ru": "⏰ На повторение сегодня: <b>{due}</b>",
    },
    "vocab.next_due": {
        "uz": "✨ Hozircha takrorlanadigan so'z yo'q. Keyingi takrorlash: <b>{when}</b>",
        "ru": "✨ Пока повторять нечего. Следующее повторение: <b>{when}</b>",
    },
    "vocab.all_mastered": {
        "uz": "🎉 Barcha so'zlarni o'zlashtirgansiz! Yangi tekshiruvlar yangi so'zlar olib keladi.",
        "ru": "🎉 Вы выучили все слова! Новые проверки принесут новые слова.",
    },
    "vocab.how_it_works": {
        "uz": "<i>💡 So'zlar oraliq bilan takrorlanadi: 1 kun → 3 kun → 1 hafta → 2 hafta. "
        "Ketma-ket 5 marta eslasangiz — so'z o'zlashtirilgan bo'ladi. Unutsangiz, boshidan "
        "boshlanadi.</i>",
        "ru": "<i>💡 Слова повторяются с интервалами: 1 день → 3 дня → неделя → 2 недели. "
        "Вспомнили 5 раз подряд — слово выучено. Забыли — всё начинается сначала.</i>",
    },
    "vocab.today": {"uz": "bugun", "ru": "сегодня"},
    "vocab.tomorrow": {"uz": "ertaga", "ru": "завтра"},
    "vocab.preparing": {"uz": "⏳ Tayyorlanmoqda...", "ru": "⏳ Готовлю..."},
    "vocab.nothing_due": {
        "uz": "Hozircha takrorlanadigan so'z yo'q 🎉 Test bilan mashq qilishingiz mumkin.",
        "ru": "Пока повторять нечего 🎉 Можно потренироваться в тесте.",
    },
    "vocab.session_expired": {
        "uz": "Bu mashg'ulot tugagan — Lug'atim qayta ochildi.",
        "ru": "Это занятие уже завершено — словарь открыт заново.",
    },
    "vocab.remind_on": {"uz": "🔔 Kunlik eslatma yoqildi", "ru": "🔔 Ежедневные напоминания включены"},
    "vocab.remind_off": {"uz": "🔕 Kunlik eslatma o'chirildi", "ru": "🔕 Ежедневные напоминания выключены"},
    # Review (flip cards)
    "vocab.card_front": {
        "uz": "🔄 Takrorlash · {index}/{total}\n\n📖 <b>{word}</b>\n\n<i>Ma'nosini eslang, so'ng "
        "o'zingizni tekshiring.</i>",
        "ru": "🔄 Повторение · {index}/{total}\n\n📖 <b>{word}</b>\n\n<i>Вспомните значение, затем "
        "проверьте себя.</i>",
    },
    "vocab.review_header": {"uz": "🔄 Takrorlash · {index}/{total}", "ru": "🔄 Повторение · {index}/{total}"},
    "vocab.review_done": {
        "uz": "✅ <b>Takrorlash tugadi!</b>\n\nSo'zlar: {words} ta\n✅ Esladingiz: {known}\n❌ Qayta "
        "o'rganasiz: {missed}",
        "ru": "✅ <b>Повторение завершено!</b>\n\nСлов: {words}\n✅ Вспомнили: {known}\n❌ Повторим "
        "ещё: {missed}",
    },
    "vocab.review_mastered": {
        "uz": "🏆 {count} ta so'z to'liq o'zlashtirildi!",
        "ru": "🏆 Полностью выучено слов: {count}!",
    },
    "vocab.more_due": {
        "uz": "⏰ Yana {due} ta so'z takrorlashni kutmoqda.",
        "ru": "⏰ Ещё {due} слов ждут повторения.",
    },
    # Test (multiple choice)
    "vocab.test_need_words": {
        "uz": "Test uchun lug'atda kamida {min} ta so'z bo'lishi kerak.",
        "ru": "Для теста в словаре нужно хотя бы {min} слова.",
    },
    "vocab.test_header": {"uz": "🎯 Test · {index}/{total}", "ru": "🎯 Тест · {index}/{total}"},
    "vocab.q_meaning": {
        "uz": "Qaysi so'z shu ma'noni beradi?\n\n💬 <i>{meaning}</i>",
        "ru": "Какое слово имеет это значение?\n\n💬 <i>{meaning}</i>",
    },
    "vocab.q_gap": {
        "uz": "Bo'sh joyga qaysi so'z mos keladi?\n\n✏️ <i>{sentence}</i>",
        "ru": "Какое слово подходит на место пропуска?\n\n✏️ <i>{sentence}</i>",
    },
    "vocab.q_translate": {
        "uz": "📖 <b>{word}</b> — qaysi tarjima to'g'ri?",
        "ru": "📖 <b>{word}</b> — какой перевод верный?",
    },
    "vocab.correct": {"uz": "✅ <b>To'g'ri!</b>", "ru": "✅ <b>Верно!</b>"},
    "vocab.wrong": {
        "uz": "❌ <b>Noto'g'ri.</b> To'g'ri javob: <b>{right}</b>",
        "ru": "❌ <b>Неверно.</b> Правильный ответ: <b>{right}</b>",
    },
    "vocab.test_done": {
        "uz": "🏁 <b>Test tugadi!</b>\n\nNatija: <b>{correct}/{total}</b> ({percent}%)",
        "ru": "🏁 <b>Тест завершён!</b>\n\nРезультат: <b>{correct}/{total}</b> ({percent}%)",
    },
    "vocab.test_great": {"uz": "🔥 Ajoyib! So'zlarni yaxshi bilasiz.", "ru": "🔥 Отлично! Вы хорошо знаете слова."},
    "vocab.test_good": {
        "uz": "👍 Yaxshi natija! Xato qilingan so'zlar takrorlashga qaytarildi.",
        "ru": "👍 Хороший результат! Слова с ошибками вернулись на повторение.",
    },
    "vocab.test_low": {
        "uz": "💪 Xato qilingan so'zlar takrorlashga qaytarildi — ularni yana ko'rasiz va "
        "albatta eslab qolasiz.",
        "ru": "💪 Слова с ошибками вернулись на повторение — вы увидите их снова и обязательно "
        "запомните.",
    },
    # Word list
    "vocab.list_header": {
        "uz": "📋 <b>So'zlarim</b> — {total} ta · {page}/{pages}-sahifa\n\n🆕 yangi · 🔄 "
        "o'rganilmoqda · ✅ o'zlashtirilgan\n\nBatafsil ko'rish uchun so'zni bosing.",
        "ru": "📋 <b>Мои слова</b> — {total} · стр. {page}/{pages}\n\n🆕 новое · 🔄 изучается · "
        "✅ выучено\n\nНажмите на слово, чтобы открыть его.",
    },
    "vocab.word_source": {"uz": "📍 Manba: {source} · {date}", "ru": "📍 Источник: {source} · {date}"},
    "vocab.source_writing": {"uz": "Writing hisoboti", "ru": "отчёт Writing"},
    "vocab.source_speaking": {"uz": "Speaking hisoboti", "ru": "отчёт Speaking"},
    "vocab.source_manual": {"uz": "o'zingiz qo'shgansiz", "ru": "добавлено вами"},
    "vocab.status_new": {"uz": "🆕 Yangi — hali takrorlanmagan", "ru": "🆕 Новое — ещё не повторялось"},
    "vocab.status_learning": {
        "uz": "🔄 O'rganilmoqda: {box}/{max} bosqich · keyingi takrorlash: {when}",
        "ru": "🔄 Изучается: этап {box}/{max} · следующее повторение: {when}",
    },
    "vocab.status_mastered": {"uz": "✅ O'zlashtirilgan", "ru": "✅ Выучено"},
    "vocab.delete_confirm": {
        "uz": "🗑 <b>{word}</b> lug'atdan o'chirilsinmi?",
        "ru": "🗑 Удалить <b>{word}</b> из словаря?",
    },
    "vocab.deleted": {"uz": "O'chirildi", "ru": "Удалено"},
    "vocab.relearned": {
        "uz": "So'z qaytadan takrorlash navbatiga qo'shildi",
        "ru": "Слово снова в очереди на повторение",
    },
    # Adding words by hand
    "vocab.add_prompt": {
        "uz": "➕ <b>So'z qo'shish</b>\n\nInglizcha so'z yoki iborani yuboring — ma'nosi, misol "
        "va tarjimasini o'zim topaman. O'zbekcha yoki ruscha yozsangiz, inglizcha muqobilini "
        "qo'shaman.\n\nBir nechta so'z: har birini yangi qatorda yoki vergul bilan (bir martada "
        "{max} tagacha).",
        "ru": "➕ <b>Добавить слово</b>\n\nОтправьте английское слово или выражение — значение, "
        "пример и перевод я найду сам. Если напишете по-русски или по-узбекски, добавлю "
        "английский аналог.\n\nНесколько слов: каждое с новой строки или через запятую (до {max} "
        "за раз).",
    },
    "vocab.add_working": {"uz": "⏳ So'zlar tayyorlanmoqda...", "ru": "⏳ Готовлю слова..."},
    "vocab.add_added": {"uz": "✅ Lug'atga qo'shildi:", "ru": "✅ Добавлено в словарь:"},
    "vocab.add_exists": {
        "uz": "📌 Lug'atingizda allaqachon bor: {words}",
        "ru": "📌 Уже есть в вашем словаре: {words}",
    },
    "vocab.add_invalid": {"uz": "⚠️ Tushunmadim: {words}", "ru": "⚠️ Не удалось распознать: {words}"},
    "vocab.add_truncated": {
        "uz": "ℹ️ Bir martada {max} tagacha so'z — qolganlarini keyingi xabarda yuboring.",
        "ru": "ℹ️ За раз — до {max} слов, остальные отправьте следующим сообщением.",
    },
    "vocab.add_limit": {
        "uz": "ℹ️ Kuniga {limit} tagacha so'z qo'shish mumkin — bugungi limit tugadi. Ertaga "
        "davom eting!",
        "ru": "ℹ️ В день можно добавить до {limit} слов — на сегодня лимит исчерпан. Продолжим "
        "завтра!",
    },
    "vocab.add_more": {
        "uz": "Yana so'z yuborishingiz mumkin.",
        "ru": "Можете отправить ещё слова.",
    },
    "vocab.add_failed": {
        "uz": "⚠️ Hozir so'zlarni tayyorlab bo'lmadi. Birozdan so'ng qayta yuboring.",
        "ru": "⚠️ Сейчас не получилось подготовить слова. Отправьте их ещё раз чуть позже.",
    },
    "vocab.add_text_only": {
        "uz": "Iltimos, so'zni matn ko'rinishida yuboring.",
        "ru": "Пожалуйста, отправьте слово текстом.",
    },
    # Reminder (bot/utils/vocab_reminders.py) and after a check
    "vocab.reminder": {
        "uz": "📚 <b>Takrorlash vaqti!</b>\n\nLug'atingizda {due} ta so'z sizni kutmoqda. 5 daqiqa "
        "ajrating — so'zlar xotirada mustahkam qoladi va keyingi Writing/Speaking'da o'zi "
        "esga keladi 💪",
        "ru": "📚 <b>Время повторить слова!</b>\n\nВ вашем словаре {due} слов ждут повторения. "
        "Уделите 5 минут — слова закрепятся в памяти и сами придут на ум в следующем "
        "Writing/Speaking 💪",
    },
    "vocab.added_after_check": {
        "uz": "📚 Lug'atimga {count} ta yangi so'z qo'shildi",
        "ru": "📚 В «Мой словарь» добавлено новых слов: {count}",
    },
    # Buttons
    "vocab.btn_review": {"uz": "🔄 Takrorlash ({due})", "ru": "🔄 Повторить ({due})"},
    "vocab.btn_test": {"uz": "🎯 Test", "ru": "🎯 Тест"},
    "vocab.btn_list": {"uz": "📋 So'zlarim", "ru": "📋 Мои слова"},
    "vocab.btn_add": {"uz": "➕ So'z qo'shish", "ru": "➕ Добавить слово"},
    "vocab.btn_remind_on": {"uz": "🔔 Eslatma: yoqilgan", "ru": "🔔 Напоминания: вкл."},
    "vocab.btn_remind_off": {"uz": "🔕 Eslatma: o'chiq", "ru": "🔕 Напоминания: выкл."},
    "vocab.btn_remind_stop": {"uz": "🔕 Eslatmani o'chirish", "ru": "🔕 Отключить напоминания"},
    "vocab.btn_reveal": {"uz": "👁 Ma'nosini ko'rish", "ru": "👁 Показать значение"},
    "vocab.btn_know": {"uz": "✅ Bildim", "ru": "✅ Знаю"},
    "vocab.btn_forgot": {"uz": "❌ Bilmadim", "ru": "❌ Не помню"},
    "vocab.btn_stop": {"uz": "⏹ Tugatish", "ru": "⏹ Завершить"},
    "vocab.btn_next": {"uz": "➡️ Keyingisi", "ru": "➡️ Дальше"},
    "vocab.btn_result": {"uz": "🏁 Natija", "ru": "🏁 Результат"},
    "vocab.btn_back_menu": {"uz": "⬅️ Lug'atim", "ru": "⬅️ Мой словарь"},
    "vocab.btn_back_list": {"uz": "⬅️ Ro'yxatga", "ru": "⬅️ К списку"},
    "vocab.btn_delete": {"uz": "🗑 O'chirish", "ru": "🗑 Удалить"},
    "vocab.btn_delete_yes": {"uz": "✅ Ha, o'chirish", "ru": "✅ Да, удалить"},
    "vocab.btn_cancel": {"uz": "❌ Bekor qilish", "ru": "❌ Отмена"},
    "vocab.btn_relearn": {"uz": "↩️ Qaytadan o'rganish", "ru": "↩️ Учить заново"},
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
