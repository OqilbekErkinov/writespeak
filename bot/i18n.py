"""Minimal i18n for the bot's three UI languages (uz/ru/en) - the AI
grading output itself is unaffected (see services/ai/*, which always grades
and comments in English, matching the exam language).

`User.language` (db/models.py) stores the choice, picked once at first
/start (bot/handlers/start.py) and changeable from "Hisobim"/"Профиль"
(bot/handlers/account.py). bot/middlewares/access_control.py injects the
current user's `lang` into every handler's data, so any handler can just
declare a `lang: str` parameter to receive it - aiogram wires it in
automatically, the same way it wires in `message`/`state`.

The English texts live in bot/i18n_en.py (merged into _T below), so
adding a key means adding its English there too (scripts/check_i18n.py).

Exam terminology (Writing/Speaking/Task 1/Task 2/Part 1-3) is left in
English in uz and ru too, matching how IELTS materials are conventionally
written/discussed in Uzbekistan regardless of the surrounding language.
"""
from __future__ import annotations

from bot.i18n_en import EN

DEFAULT_LANG = "uz"
LANGS = ("uz", "ru", "en")

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
        "uz": "Salom, {name}! Men Bandzilla 🦖 — IELTS Writing va Speaking'dagi xatolaringizni yeyman.\n"
              "Nimani tekshiramiz? Quyidagi menyudan tanlang 👇",
        "ru": "Привет, {name}! Я Bandzilla 🦖 — съедаю ошибки в вашем IELTS Writing и Speaking.\n"
              "Что проверим? Выберите в меню ниже 👇",
    },
    "start.sample_caption": {
        "uz": "📄 Mana shunday hisobot olasiz: ball, har bir xatoning izohi, sizga moslab yozilgan "
        "model javob va Band 7+ so'zlar — har bir Writing yoki Speaking tekshiruvi uchun.",
        "ru": "📄 Именно такой подробный отчёт вы получите за каждую проверку Writing или "
        "Speaking!",
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
        "uz": "⚠️ <b>Coin yetarli emas.</b> Bu tekshiruv — <b>{price} coin</b>, balansingizda — <b>{balance} coin</b>.\nCoin paketini tanlang (katta paketda arzonroq):",
        "ru": "⚠️ <b>Недостаточно coin.</b> Эта проверка — <b>{price} coin</b>, на балансе — <b>{balance} coin</b>.\nВыберите пакет coin (в большом дешевле):",
    },
    "quota.paywall_generic": {
        "uz": "⚠️ <b>Coin yetarli emas.</b> Coin paketini tanlang (katta paketda arzonroq):",
        "ru": "⚠️ <b>Недостаточно coin.</b> Выберите пакет coin (в большом дешевле):",
    },
    "quota.price_list": {
        "uz": "🟡 <b>Tekshiruv narxlari:</b>\n✍️ Writing Task 1 — <b>{w1} coin</b> · Task 2 — <b>{w2} coin</b>\n🎙 Speaking Part 1 — <b>{s1} coin</b> · Part 2 — <b>{s2} coin</b> · Part 3 — <b>{s3} coin</b>\n🎯 To'liq Speaking (Part 1+2+3) — <b>{full} coin</b>\n<i>1 coin = {coin} so'm, katta paketlarda arzonroq. Coinning muddati tugamaydi.</i>",
        "ru": "🟡 <b>Цены проверок:</b>\n✍️ Writing Task 1 — <b>{w1} coin</b> · Task 2 — <b>{w2} coin</b>\n🎙 Speaking Part 1 — <b>{s1} coin</b> · Part 2 — <b>{s2} coin</b> · Part 3 — <b>{s3} coin</b>\n🎯 Полный Speaking (Part 1+2+3) — <b>{full} coin</b>\n<i>1 coin = {coin} сум, в больших пакетах дешевле. Coin не сгорают.</i>",
    },
    "quota.price_note": {
        "uz": "🟡 Bu tekshiruv — <b>{price} coin</b>. Hisobot tayyor bo'lganda yechiladi, balansingizda <b>{left} coin</b> qoladi.",
        "ru": "🟡 Эта проверка — <b>{price} coin</b>. Спишется, когда отчёт будет готов; на балансе останется <b>{left} coin</b>.",
    },
    # Shown during a test window (settings.checks_only_for) to everyone else;
    # they get quota.back when it ends (bot/utils/checks_back.py).
    "quota.paused": {
        "uz": "🛠 <b>Bandzilla yangilanmoqda</b>\n\nHozir hisobotlarni yanada aniq va foydali qilish ustida "
        "ishlayapmiz, shuning uchun tekshiruvlar qisqa vaqtga to'xtatildi. Tez orada hammasi yana ishga "
        "tushadi.\n\n✅ Coinlaringiz joyida — hech narsa yechilmaydi va yo'qolmaydi.\n🔔 Tekshiruvlar qayta "
        "ochilishi bilan sizga shu yerda xabar beramiz.\n\nBu orada <b>📝 Practice</b> bo'limidan savol "
        "tanlab, javobingizni tayyorlab qo'ying — qaytishimiz bilan tekshirasiz.\n\nSabringiz uchun rahmat! 🦖",
        "ru": "🛠 <b>Bandzilla обновляется</b>\n\nМы делаем отчёты ещё точнее и полезнее, поэтому проверки "
        "ненадолго приостановлены. Совсем скоро всё снова заработает.\n\n✅ Ваши coin на месте — ничего не "
        "списывается и не пропадает.\n🔔 Как только проверки откроются, мы сразу напишем вам здесь.\n\n"
        "А пока выберите вопрос в разделе <b>📝 Practice</b> и подготовьте ответ — проверите его, как "
        "только мы вернёмся.\n\nСпасибо за терпение! 🦖",
    },
    "quota.back": {
        "uz": "🎉 <b>Bandzilla yana ishlayapti!</b>\n\nTekshiruvlar qayta ochildi — Writing yoki Speaking "
        "javobingizni yuboring, hisobot 2–4 daqiqada keladi.\n\nKutganingiz uchun rahmat! 🦖",
        "ru": "🎉 <b>Bandzilla снова работает!</b>\n\nПроверки снова открыты — отправьте ответ по Writing или "
        "Speaking, отчёт придёт через 2–4 минуты.\n\nСпасибо, что дождались! 🦖",
    },
    # --- Payments (bot/handlers/payments.py, bot/keyboards/payment_kb.py) ---
    "pay.pack_button": {
        "uz": "🟡 {coins} coin — {price} so'm",
        "ru": "🟡 {coins} coin — {price} сум",
    },
    "pay.pack_button_discount": {
        "uz": "🟡 {coins} coin — {price} so'm (−{off}%)",
        "ru": "🟡 {coins} coin — {price} сум (−{off}%)",
    },
    "pay.center_packs_header": {
        "uz": "🏫 O'quv markazlar va o'qituvchilar uchun:",
        "ru": "🏫 Для учебных центров и преподавателей:",
    },
    "pay.choose_pack": {
        "uz": "Bu paket endi sotilmaydi. Iltimos, coin paketini tanlang:",
        "ru": "Этот пакет больше не продаётся. Выберите пакет coin:",
    },
    "pay.subscription_retired": {
        "uz": "Cheksiz obuna endi sotilmaydi — uning o'rniga coin paketlari bor (katta paketda coin arzonroq):",
        "ru": "Безлимитная подписка больше не продаётся — вместо неё есть пакеты coin (в большом пакете дешевле):",
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
    "pay.approved_coins": {
        "uz": "🎉 To'lovingiz tasdiqlandi! Hisobingizga <b>{coins} coin</b> qo'shildi. Balans: <b>{balance} coin</b>.",
        "ru": "🎉 Ваша оплата подтверждена! На счёт добавлено <b>{coins} coin</b>. Баланс: <b>{balance} coin</b>.",
    },
    "pay.referral_bonus_buyer": {
        "uz": "🎁 Taklif bonusi: sizga yana <b>{coins} coin</b> qo'shildi!",
        "ru": "🎁 Бонус за приглашение: вам добавлено ещё <b>{coins} coin</b>!",
    },
    "pay.referral_bonus_referrer": {
        "uz": "🎁 Siz taklif qilgan do'stingiz birinchi marta coin sotib oldi — sizga <b>{coins} coin</b> bonus! Balans: <b>{balance} coin</b>.",
        "ru": "🎁 Приглашённый вами друг впервые купил coin — вам бонус <b>{coins} coin</b>! Баланс: <b>{balance} coin</b>.",
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
    "writing.send_chart": {
        "uz": "✅ Savol qabul qilindi.\n\n📊 Endi shu Task 1 ning <b>grafik yoki diagramma rasmini</b> yuboring.\n\n"
        "Task 1 da ball rasmdagi ma'lumotga qarab qo'yiladi: Bandzilla javobingizdagi har bir raqam, "
        "taqqoslash va umumiy xulosani shu rasm bilan solishtiradi. Rasmsiz faqat matn tekshiriladi.",
        "ru": "✅ Вопрос принят.\n\n📊 Теперь отправьте <b>картинку графика или диаграммы</b> к этому Task 1.\n\n"
        "В Task 1 балл ставится по данным на картинке: Bandzilla сверит с ней каждую цифру, сравнение и "
        "общий вывод вашего ответа. Без картинки проверяется только текст.",
    },
    "writing.chart_photo_only": {
        "uz": "📊 Bu qadamda grafik rasmini yuboring — rasm yoki rasm-fayl. Rasm bo'lmasa, "
        "«➡️ Rasmsiz davom etish»ni bosing.",
        "ru": "📊 На этом шаге отправьте картинку графика — фото или файл-картинку. Если картинки нет, "
        "нажмите «➡️ Продолжить без картинки».",
    },
    "writing.btn_no_chart": {"uz": "➡️ Rasmsiz davom etish", "ru": "➡️ Продолжить без картинки"},
    "writing.chart_resend": {
        "uz": "📊 Grafik rasmidagi ma'lumotni o'qib bo'lmadi — rasm juda xira, kesilgan, grafik emas yoki fayli "
        "buzilgan.\n\nJavobingiz saqlangan, coin yechilmadi. Grafikning aniqroq rasmini yuboring (skrinshot yoki "
        "JPG/PNG fayl) — tekshiruvni darhol boshlayman.",
        "ru": "📊 Не удалось прочитать данные на картинке графика — она слишком размытая, обрезана, это не график "
        "или файл повреждён.\n\nВаш ответ сохранён, coin не списаны. Отправьте более чёткую картинку графика "
        "(скриншот или файл JPG/PNG) — и я сразу начну проверку.",
    },
    "writing.chart_partly_unclear": {
        "uz": "⚠️ Grafikning ayrim qiymatlari aniq o'qilmadi — ular bo'yicha raqamlaringiz xato deb hisoblanmadi.",
        "ru": "⚠️ Некоторые значения на графике не читаются чётко — ваши цифры по ним не считались ошибкой.",
    },
    "writing.chart_not_checked": {
        "uz": "⚠️ Grafik rasmi yuborilmadi — raqamlaringiz tekshirilmadi va Task Achievement ular uchun "
        "pasaytirilmadi.",
        "ru": "⚠️ Картинка графика не отправлена — ваши цифры не проверялись, и Task Achievement за них не снижался.",
    },
    "writing.grading_in_progress": {
        "uz": "🦷 Tahlil qilyapman… tishlarim o'tkir! Bu 2–4 daqiqa oladi.",
        "ru": "🦷 Разбираю… зубы острые! Это займёт 2–4 минуты.",
    },
    "writing.grading_failed": {
        "uz": "Voy, nimadir xato ketdi — tekshiruv tugamadi, coin yechilmadi. Birozdan so'ng qayta "
        "urinib ko'ring.",
        "ru": "Ой, что-то пошло не так — проверка не завершилась, coin не списаны. Попробуйте ещё раз чуть позже.",
    },
    "writing.done_caption": {
        "uz": "Gotcha! {mistakes} ta xato topdim 🦖\nNatijangiz: <b>Band {band}</b>",
        "ru": "Gotcha! Нашёл ошибок: {mistakes} 🦖\nВаш результат: <b>Band {band}</b>",
    },
    "writing.done_caption_clean": {
        "uz": "Xato topmadim — zo'r ish! 🦖\nNatijangiz: <b>Band {band}</b>",
        "ru": "Ошибок не нашёл — отличная работа! 🦖\nВаш результат: <b>Band {band}</b>",
    },
    "writing.resubmitted": {
        "uz": "♻️ Bu — #{number} inshodagi aynan o'sha matn, shuning uchun saqlangan hisobotingizni qayta "
        "yubordim (coin yechilmadi). Ball: <b>{band}</b>",
        "ru": "♻️ Это тот же текст, что и в эссе #{number}, поэтому я отправил сохранённый отчёт (coin "
        "не списаны). Балл: <b>{band}</b>",
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
    # Full test (Part 1 + 2 + 3 in one report)
    "speaking.btn_full_test": {"uz": "🎯 Full test (Part 1–3)", "ru": "🎯 Full test (Part 1–3)"},
    "speaking.full_intro": {
        "uz": "🎯 <b>Speaking Full test</b>\n\nImtihondagidek: Part 1 (qisqa savollar), Part 2 (cue card, "
        "1–2 daqiqa gapirasiz) va Part 3 (muhokama). Har bir savolga ovozli xabar bilan javob bering — "
        "oxirida bitta umumiy hisobot va <b>Speaking band</b> chiqadi.",
        "ru": "🎯 <b>Speaking Full test</b>\n\nКак на экзамене: Part 1 (короткие вопросы), Part 2 (cue card, "
        "говорите 1–2 минуты) и Part 3 (дискуссия). Отвечайте на каждый вопрос голосовым — в конце "
        "будет один общий отчёт и <b>Speaking band</b>.",
    },
    "speaking.full_no_questions": {
        "uz": "Hozircha Full test uchun savollar bazasi tayyor emas. Birozdan so'ng urinib ko'ring.",
        "ru": "База вопросов для Full test пока не готова. Попробуйте чуть позже.",
    },
    "speaking.part_start": {
        "uz": "— <b>{part}</b> —",
        "ru": "— <b>{part}</b> —",
    },
    "speaking.grading_in_progress": {
        "uz": "🎧 Yozuvingizni tinglab, tahlil qilyapman… tishlarim o'tkir 🦷 Bu 2–5 daqiqa oladi.",
        "ru": "🎧 Слушаю запись и разбираю… зубы острые 🦷 Это займёт 2–5 минут.",
    },
    "speaking.resubmitted": {
        "uz": "♻️ Bu — {number}-sessiyadagi aynan o'sha yozuv, shuning uchun saqlangan hisobotingizni "
        "qayta yubordim (coin yechilmadi). Ball: <b>{band}</b>",
        "ru": "♻️ Это та же самая запись, что и в сессии №{number}, поэтому я отправил сохранённый отчёт "
        "(coin не списаны). Балл: <b>{band}</b>",
    },
    "speaking.listen_prompt": {
        "uz": "🔊 Model javoblarni ovozli tinglash uchun bosing:",
        "ru": "🔊 Нажмите, чтобы прослушать образцовые ответы:",
    },
    "speaking.listen_gone": {
        "uz": "Bu model javob topilmadi.",
        "ru": "Этот образцовый ответ не найден.",
    },
    "speaking.listen_failed": {
        "uz": "Ovozni tayyorlab bo'lmadi, birozdan so'ng qayta bosib ko'ring.",
        "ru": "Не удалось подготовить аудио, попробуйте нажать ещё раз чуть позже.",
    },
    "speaking.model_voice_caption": {
        "uz": "Model answer · {label} · aimed at Band {band} (AI example)",
        "ru": "Model answer · {label} · aimed at Band {band} (AI example)",
    },
    # One recording with all the answers (bot/handlers/speaking.py, the last prompt's D)
    "speaking.mode_choose": {
        "uz": "✅ Savollar qabul qilindi: <b>{n} ta</b>.\n\nQanday javob berasiz?\n🎙 <b>Savolma-savol</b> — har bir "
        "savolga alohida ovozli xabar.\n🎧 <b>Bitta audio</b> — barcha javoblaringiz bitta yozuvda (masalan, mock test "
        "yoki o'zingiz yozib olgan audio).",
        "ru": "✅ Вопросы приняты: <b>{n}</b>.\n\nКак будете отвечать?\n🎙 <b>По вопросам</b> — отдельное голосовое "
        "на каждый вопрос.\n🎧 <b>Одно аудио</b> — все ответы в одной записи (например, mock test или ваша "
        "собственная запись).",
    },
    "speaking.btn_mode_each": {"uz": "🎙 Savolma-savol", "ru": "🎙 По вопросам"},
    "speaking.btn_mode_one": {"uz": "🎧 Bitta audio (barcha javoblar)", "ru": "🎧 Одно аудио (все ответы)"},
    "speaking.btn_no_questions": {
        "uz": "🎧 Savollar yozuvning o'zida (mock test)",
        "ru": "🎧 Вопросы есть в самой записи (mock test)",
    },
    "speaking.one_intro": {
        "uz": "🎧 <b>Bitta audio</b>\n\n{questions}\n\nBarcha javoblaringizni bitta yozuv qilib yuboring: ovozli "
        "xabar, audio fayl (mp3, m4a, ogg, wav), video-xabar yoki video. Bir nechta bo'lak bo'lsa ham bo'ladi — "
        "kelgan tartibida qo'shiladi. Hammasini yuborgach, «✅ Yakunlash va baholash»ni bosing.",
        "ru": "🎧 <b>Одно аудио</b>\n\n{questions}\n\nОтправьте все ответы одной записью: голосовое, аудиофайл "
        "(mp3, m4a, ogg, wav), видео-сообщение или видео. Можно несколькими частями — они соединятся в порядке "
        "получения. Когда отправите всё, нажмите «✅ Завершить и оценить».",
    },
    "speaking.one_intro_noq": {
        "uz": "🎧 <b>Savollar yozuvning o'zida</b>\n\nImtihonchining savollari ham eshitiladigan yozuvni yuboring "
        "(masalan, mock test): ovozli xabar, audio fayl (mp3, m4a, ogg, wav), video-xabar yoki video. Savollarni "
        "yozuvdan o'zim ajrataman, imtihonchining ovozi baholanmaydi. Bir nechta bo'lak bo'lsa ham bo'ladi. "
        "Hammasini yuborgach, «✅ Yakunlash va baholash»ni bosing.",
        "ru": "🎧 <b>Вопросы есть в самой записи</b>\n\nОтправьте запись, где слышны и вопросы экзаменатора "
        "(например, mock test): голосовое, аудиофайл (mp3, m4a, ogg, wav), видео-сообщение или видео. Вопросы я "
        "найду в записи сам, голос экзаменатора не оценивается. Можно несколькими частями. Когда отправите всё, "
        "нажмите «✅ Завершить и оценить».",
    },
    "speaking.one_piece_ok": {
        "uz": "✅ {n}-bo'lak qabul qilindi ({length}). Jami: <b>{total}</b>.\nYana bo'lak yuborishingiz yoki baholashni "
        "boshlashingiz mumkin.",
        "ru": "✅ Часть {n} принята ({length}). Всего: <b>{total}</b>.\nМожно отправить ещё часть или начать оценку.",
    },
    "speaking.one_too_long": {
        "uz": "⚠️ Yozuv juda uzun: {part} uchun eng ko'pi bilan {max} daqiqa. Bu bo'lak qo'shilmadi (jami {total} "
        "bo'lardi).",
        "ru": "⚠️ Запись слишком длинная: для {part} не больше {max} минут. Эта часть не добавлена (всего было бы "
        "{total}).",
    },
    "speaking.one_too_big": {
        "uz": "⚠️ Fayl 20 MB dan katta — Telegram botlarga bundan kattasini bermaydi. Yozuvni bo'laklarga bo'lib yoki "
        "siqib (masalan, ovozli xabar yoki mp3 qilib) yuboring.",
        "ru": "⚠️ Файл больше 20 МБ — Telegram не отдаёт ботам файлы крупнее. Отправьте запись частями или в сжатом "
        "виде (например, голосовым или в mp3).",
    },
    "speaking.one_too_short": {
        "uz": "⚠️ Yozuv {n} ta savol uchun juda qisqa ({total}). Yana bo'lak qo'shishingiz yoki shundayligicha "
        "baholashingiz mumkin.",
        "ru": "⚠️ Запись слишком короткая для {n} вопросов ({total}). Можно добавить ещё часть или оценить как есть.",
    },
    "speaking.btn_grade_anyway": {"uz": "✅ Baribir baholash", "ru": "✅ Всё равно оценить"},
    "speaking.one_splitting": {
        "uz": "🎧 Yozuvni tinglab, javoblarni savollarga ajratyapman… Bu 1–2 daqiqa oladi.",
        "ru": "🎧 Слушаю запись и разделяю ответы по вопросам… Это займёт 1–2 минуты.",
    },
    "speaking.one_no_speech": {
        "uz": "🤔 Yozuvda nutq topilmadi — coin yechilmadi. Boshqa yozuv yuboring.",
        "ru": "🤔 В записи не найдено речи — coin не списаны. Отправьте другую запись.",
    },
    "speaking.one_not_english": {
        "uz": "🤔 Yozuvda inglizcha nutq topilmadi — coin yechilmadi. Speaking javoblari ingliz tilida bo'lishi kerak. "
        "Boshqa yozuv yuboring.",
        "ru": "🤔 В записи не найдено английской речи — coin не списаны. Ответы Speaking должны быть на английском. "
        "Отправьте другую запись.",
    },
    "speaking.one_no_answers": {
        "uz": "🤔 Yozuvdan birorta savolga javob topilmadi — coin yechilmadi. Boshqa yozuv yuboring.",
        "ru": "🤔 В записи не найдено ответа ни на один вопрос — coin не списаны. Отправьте другую запись.",
    },
    "speaking.one_need_questions": {
        "uz": "Yozuvdan savollarni ajratib bo'lmadi. Iltimos, savollarni yuboring (matn, rasm yoki fayl) — yozuvingiz "
        "saqlanib turibdi.",
        "ru": "Не удалось найти вопросы в записи. Пожалуйста, отправьте вопросы (текстом, фото или файлом) — ваша "
        "запись сохранена.",
    },
    "speaking.one_no_questions_full": {
        "uz": "Yozuvdan Full test savollarini ajratib bo'lmadi — coin yechilmadi. Full testni savolma-savol "
        "topshiring yoki Part 1, 2, 3 ni alohida tekshiring.",
        "ru": "Не удалось найти вопросы Full test в записи — coin не списаны. Пройдите Full test по вопросам или "
        "проверьте Part 1, 2, 3 отдельно.",
    },
    "speaking.one_split_check": {
        "uz": "Javoblarni shunday ajratdim — tekshirib bering:\n\n{split}\n\nTo'g'rimi?",
        "ru": "Я разделил ответы так — проверьте, пожалуйста:\n\n{split}\n\nВсё верно?",
    },
    "speaking.one_missing": {"uz": "javob topilmadi", "ru": "ответ не найден"},
    "speaking.btn_split_ok": {"uz": "✅ To'g'ri", "ru": "✅ Верно"},
    "speaking.btn_split_fix": {"uz": "✏️ Qayta ajratish", "ru": "✏️ Разделить заново"},
    "speaking.one_split_fix": {
        "uz": "To'g'ri vaqtlarni shu ko'rinishda yuboring — har bir savol alohida qatorda (yuqoridagi xabarni "
        "nusxalab, tahrirlab yuborsangiz ham bo'ladi):\n\n<code>Q1: 0:00–0:41\nQ2: 0:45–1:30, 1:35–1:50\nQ3: -</code>"
        "\n\n«-» — bu savolga javob yo'q. Yozmagan savollaringiz o'zgarmaydi.",
        "ru": "Отправьте верное время в таком виде — каждый вопрос отдельной строкой (можно скопировать сообщение "
        "выше и исправить):\n\n<code>Q1: 0:00–0:41\nQ2: 0:45–1:30, 1:35–1:50\nQ3: -</code>\n\n«-» — ответа на этот "
        "вопрос нет. Вопросы, которые вы не укажете, не изменятся.",
    },
    "speaking.one_split_bad": {
        "uz": "Vaqtlarni tushunmadim. Iltimos, shu ko'rinishda yuboring: <code>Q1: 0:00–0:41</code>",
        "ru": "Не понял время. Пожалуйста, отправьте в таком виде: <code>Q1: 0:00–0:41</code>",
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
    "input.bad_image": {
        "uz": "🖼 Bu rasmni ochib bo'lmadi: fayl buzilgan yoki bu formatni qabul qilmayman (masalan, HEIC). "
        "Iltimos, oddiy rasm qilib yoki JPG/PNG fayl sifatida yuboring.",
        "ru": "🖼 Не удалось открыть эту картинку: файл повреждён или такой формат я не принимаю (например, "
        "HEIC). Пожалуйста, отправьте её как обычное фото или файлом JPG/PNG.",
    },
    "input.image_too_small": {
        "uz": "🖼 Rasm juda kichik — undagi yozuv va raqamlar o'qilmaydi. Iltimos, kattaroq va aniqroq rasm yuboring.",
        "ru": "🖼 Картинка слишком маленькая — надписи и цифры на ней не читаются. Пожалуйста, отправьте "
        "картинку побольше и почётче.",
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
        "uz": "<b>{label}</b>\n✅ Bajarilgan: {done}/{total}",
        "ru": "<b>{label}</b>\n✅ Выполнено: {done}/{total}",
    },
    "practice.filter_line": {
        "uz": "🔎 Turi: {kind} — {count} ta savol",
        "ru": "🔎 Тип: {kind} — вопросов: {count}",
    },
    "practice.legend_hot": {
        "uz": "🔥 imtihonda ko'p tushgan",
        "ru": "🔥 часто попадается на экзамене",
    },
    "practice.legend_new": {
        "uz": "🆕 so'nggi 2 haftada tushgan",
        "ru": "🆕 был на экзамене за последние 2 недели",
    },
    "practice.legend_season": {
        "uz": "🆕 yangi mavsum savollari",
        "ru": "🆕 вопросы нового сезона",
    },
    "practice.pick_hint": {
        "uz": "👇 Savol raqamini tanlang",
        "ru": "👇 Выберите номер вопроса",
    },
    "practice.btn_random": {"uz": "🎲 Tasodifiy savol", "ru": "🎲 Случайный вопрос"},
    "practice.btn_filter": {"uz": "🔎 Turi bo'yicha", "ru": "🔎 По типу"},
    "practice.choose_kind": {
        "uz": "<b>{label}</b>\n\nQaysi turdagi savollarni ko'rsatay?",
        "ru": "<b>{label}</b>\n\nВопросы какого типа показать?",
    },
    "practice.kind_all": {"uz": "📚 Barchasi", "ru": "📚 Все"},
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
        "uz": "🎉 Bu bo'limdagi barcha savollarni bajardingiz! Xohlagan savolni qayta ishlashingiz mumkin.",
        "ru": "🎉 Вы выполнили все вопросы этого раздела! Любой из них можно пройти ещё раз.",
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
    "account.coin_balance": {
        "uz": "🟡 Balans: <b>{coins} coin</b> (muddatsiz)",
        "ru": "🟡 Баланс: <b>{coins} coin</b> (бессрочно)",
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
    "account.referral": {
        "uz": "🔗 Do'stlaringizni taklif qiling — do'stingiz birinchi marta coin sotib olganda <b>ikkalangizga {coins} coin</b> bonus beriladi ({count} ta taklif qilingan):\n{link}",
        "ru": "🔗 Приглашайте друзей — когда друг впервые купит coin, <b>вы оба получите по {coins} coin</b> (приглашено: {count}):\n{link}",
    },
    "account.language_button": {
        "uz": "🌐 Tilni tanlang / Выберите язык / Choose language:",
        "ru": "🌐 Tilni tanlang / Выберите язык / Choose language:",
    },
    "start.welcome_coins": {
        "uz": "🎁 <b>Sizga {coins} coin sovg'a!</b>\n\nCoin — Bandzilla'dagi to'lov birligi: har bir tekshiruv "
              "o'z narxida coindan yechiladi. {coins} coin ikkita Writing Task 1 ga yoki bitta Writing Task 2 "
              "yoki bitta Speaking tekshiruviga yetadi.\n\nNarxlar va hisobni to'ldirish — <b>👤 Hisobim</b> bo'limida.",
        "ru": "🎁 <b>Вам подарок — {coins} coin!</b>\n\nCoin — валюта Bandzilla: каждая проверка списывает свою "
              "цену в coin. {coins} coin хватит на две проверки Writing Task 1 или на одну Writing Task 2 либо "
              "одну часть Speaking.\n\nЦены и пополнение — в разделе <b>👤 Профиль</b>.",
    },
    "start.real_task": {
        "uz": "⚠️ <b>Muhim maslahat</b>\n\nSovg'a coinlarni shunchaki sinab ko'rish uchun tasodifiy matn yoki bir-ikki "
              "gapga sarflab qo'ymang — Bandzilla ularni ham tekshiradi va coin yechiladi.\n\n✅ Haqiqiy IELTS "
              "savolini oling: Writing Task 1, Task 2 yoki Speaking.\n✅ Javobni imtihondagidek to'liq yozing yoki "
              "gapiring.\n✅ Savol yo'qmi? <b>📝 Practice</b> bo'limida tayyor savollar bor.\n\nShunda hisobot "
              "haqiqiy xatolaringizni topadi va ballingizni o'stiradi 🦖",
        "ru": "⚠️ <b>Важный совет</b>\n\nНе тратьте подарочные coin на проверку «просто посмотреть» — случайный "
              "текст или пару фраз: Bandzilla проверит и их, и coin спишутся.\n\n✅ Возьмите настоящее задание "
              "IELTS: Writing Task 1, Task 2 или Speaking.\n✅ Ответьте полностью, как на экзамене.\n✅ Нет "
              "задания? Готовые вопросы есть в разделе <b>📝 Practice</b>.\n\nТогда отчёт найдёт ваши настоящие "
              "ошибки и поможет поднять балл 🦖",
    },
    # --- Hisobim hub (bot/handlers/account.py, bot/keyboards/account_kb.py) ---
    "acc.title": {"uz": "👤 <b>Hisobim</b> · {name}", "ru": "👤 <b>Профиль</b> · {name}"},
    "acc.checks_line": {
        "uz": "📝 Tekshiruvlar: <b>{total}</b> ta · Writing {w} · Speaking {s}",
        "ru": "📝 Проверок: <b>{total}</b> · Writing {w} · Speaking {s}",
    },
    "acc.choose": {"uz": "Kerakli bo'limni tanlang 👇", "ru": "Выберите раздел 👇"},
    "acc.btn_topup": {"uz": "💳 Hisobni to'ldirish", "ru": "💳 Пополнить баланс"},
    "acc.btn_history": {"uz": "📜 Tarix", "ru": "📜 История"},
    "acc.btn_stats": {"uz": "📊 Statistika", "ru": "📊 Статистика"},
    "acc.btn_lang": {"uz": "🌐 Til / Язык / Language", "ru": "🌐 Til / Язык / Language"},
    "acc.btn_support": {"uz": "💬 Admin bilan aloqa", "ru": "💬 Связь с админом"},
    "acc.btn_back_main": {"uz": "⬅️ Asosiy menyu", "ru": "⬅️ Главное меню"},
    "acc.btn_back": {"uz": "⬅️ Hisobim", "ru": "⬅️ Профиль"},
    "acc.btn_back_topup": {"uz": "⬅️ Orqaga", "ru": "⬅️ Назад"},
    "acc.btn_for_students": {"uz": "🎓 O'quvchilar uchun", "ru": "🎓 Для учеников"},
    "acc.btn_for_centers": {"uz": "🏫 O'qituvchi va o'quv markazlari uchun", "ru": "🏫 Для преподавателей и учебных центров"},
    "acc.btn_become_teacher": {"uz": "🙋 O'qituvchi bo'lish so'rovini yuborish", "ru": "🙋 Отправить заявку преподавателя"},
    "acc.topup": {
        "uz": "💳 <b>Hisobni to'ldirish</b>\n\nBandzilla'da har bir tekshiruv <b>coin</b> bilan to'lanadi. Coin — "
              "botning ichki puli: bir marta sotib olasiz va xohlagan vaqtda ishlatasiz.\n\n{prices}\n\n"
              "✅ Coin faqat hisobot tayyor bo'lganda yechiladi — tekshiruv xato bilan tugasa, coin joyida qoladi.\n\n"
              "<b>Qanday to'ldiriladi:</b>\n1️⃣ Quyida kim uchun ekanini tanlang va paketni bosing.\n"
              "2️⃣ Bot ko'rsatgan kartaga pulni o'tkazing.\n3️⃣ Chekning rasmini shu chatga yuboring — admin "
              "tekshirgach, coin hisobingizga tushadi.\n\nKim uchun to'ldiryapsiz? 👇",
        "ru": "💳 <b>Пополнение баланса</b>\n\nВ Bandzilla каждая проверка оплачивается <b>coin</b>. Coin — "
              "внутренние деньги бота: покупаете один раз и тратите когда угодно.\n\n{prices}\n\n"
              "✅ Coin списываются только когда отчёт готов — если проверка сорвалась, coin остаются у вас.\n\n"
              "<b>Как пополнить:</b>\n1️⃣ Ниже выберите, для кого пакет, и нажмите на него.\n"
              "2️⃣ Переведите деньги на карту, которую покажет бот.\n3️⃣ Отправьте фото чека в этот чат — после "
              "проверки админом coin поступят на баланс.\n\nДля кого пополняете? 👇",
    },
    "acc.packs_students": {
        "uz": "🎓 <b>O'quvchilar uchun paketlar</b>\n\nO'z tekshiruvlaringiz uchun. Katta paketda har bir coin "
              "arzonroq — chegirma tugmada yozilgan.\n\nPaketni tanlang 👇",
        "ru": "🎓 <b>Пакеты для учеников</b>\n\nДля ваших собственных проверок. В большом пакете каждый coin "
              "дешевле — скидка указана на кнопке.\n\nВыберите пакет 👇",
    },
    "acc.packs_centers": {
        "uz": "🏫 <b>O'qituvchi va o'quv markazlari uchun</b>\n\nGuruhingizdagi o'quvchilarning tekshiruvlarini "
              "to'lash va ularga coin sovg'a qilish uchun. Katta hajm — eng arzon coin.\n\nPaketni tanlang 👇",
        "ru": "🏫 <b>Для преподавателей и учебных центров</b>\n\nЧтобы оплачивать проверки учеников вашей группы "
              "и дарить им coin. Большой объём — самый дешёвый coin.\n\nВыберите пакет 👇",
    },
    "acc.centers_only": {
        "uz": "🏫 <b>O'qituvchi va o'quv markazlari uchun</b>\n\nBu paketlar o'qituvchi hisoblari uchun: ular bilan "
              "guruhingizdagi o'quvchilarning tekshiruvlarini to'laysiz va ularga coin sovg'a qilasiz.\n\n"
              "O'qituvchimisiz? So'rov yuboring — admin tasdiqlagach, bu paketlar shu yerda ochiladi.",
        "ru": "🏫 <b>Для преподавателей и учебных центров</b>\n\nЭти пакеты — для аккаунтов преподавателей: с ними "
              "вы оплачиваете проверки учеников своей группы и дарите им coin.\n\nВы преподаватель? Отправьте "
              "заявку — после одобрения админом пакеты откроются здесь.",
    },
    "acc.history": {
        "uz": "📜 <b>Tarix</b> · {start}–{end} / {total}\n\nHisobotni qayta yuklab olish uchun ishni bosing 👇",
        "ru": "📜 <b>История</b> · {start}–{end} из {total}\n\nНажмите на работу, чтобы снова скачать отчёт 👇",
    },
    "acc.stats_wait": {"uz": "📊 Statistika tayyorlanmoqda…", "ru": "📊 Готовлю статистику…"},
    "acc.stats_caption": {
        "uz": "📊 <b>Statistika</b>\nSo'nggi ball: <b>{last}</b> · {trend}",
        "ru": "📊 <b>Статистика</b>\nПоследний балл: <b>{last}</b> · {trend}",
    },
    # --- Coin gifts (bot/handlers/account.py, payments.py, start.py, language.py) ---
    "acc.btn_gift": {"uz": "🎁 Sovg'a qilish", "ru": "🎁 Подарить"},
    "gift.intro": {
        "uz": "🎁 <b>Coin sovg'a qiling</b>\n\nDo'stingiz yoki yaqiningizga IELTS tayyorgarligi uchun coin sovg'a "
        "qiling — u Writing va Speaking javoblarini tekshirtirib, batafsil hisobot oladi.\n\n<b>Qanday ishlaydi:</b>\n"
        "1️⃣ Quyida paketni tanlang va to'lov qiling — xuddi o'zingizga olgandek.\n2️⃣ Admin tasdiqlagach, sizga "
        "maxsus sovg'a havolasi keladi.\n3️⃣ Havolani do'stingizga yuboring — u bosishi bilan coinlar uning "
        "hisobiga tushadi.\n\nPaketni tanlang 👇",
        "ru": "🎁 <b>Подарите coin</b>\n\nПодарите другу или близкому coin для подготовки к IELTS — он сможет "
        "проверять ответы по Writing и Speaking и получать подробные отчёты.\n\n<b>Как это работает:</b>\n"
        "1️⃣ Выберите пакет ниже и оплатите — как для себя.\n2️⃣ После подтверждения админом вы получите особую "
        "подарочную ссылку.\n3️⃣ Отправьте её другу — как только он её откроет, coin поступят на его баланс.\n\n"
        "Выберите пакет 👇",
    },
    "gift.pending_head": {
        "uz": "🎁 <b>Hali olinmagan sovg'alaringiz</b> — havolani do'stingizga yuboring:",
        "ru": "🎁 <b>Ещё не полученные подарки</b> — отправьте ссылку другу:",
    },
    "gift.pending_line": {"uz": "• {coins} coin — {link}", "ru": "• {coins} coin — {link}"},
    "gift.ready": {
        "uz": "🎁 <b>Sovg'angiz tayyor!</b>\n\nTo'lov tasdiqlandi — <b>{coins} coin</b> do'stingizni kutmoqda. "
        "Quyidagi havolani unga yuboring: bosishi bilan coinlar uning hisobiga tushadi.\n\n{link}\n\nHavola bir "
        "marta ishlaydi. Uni «👤 Hisobim» → «🎁 Sovg'a qilish» bo'limida ham topasiz.",
        "ru": "🎁 <b>Ваш подарок готов!</b>\n\nОплата подтверждена — <b>{coins} coin</b> ждут вашего друга. "
        "Отправьте ему ссылку ниже: как только он её откроет, coin поступят на его баланс.\n\n{link}\n\nСсылка "
        "срабатывает один раз. Её можно найти и в «👤 Профиль» → «🎁 Подарить».",
    },
    "gift.btn_share": {"uz": "📤 Do'stga yuborish", "ru": "📤 Отправить другу"},
    "gift.share_text": {
        "uz": "🎁 Sizga Bandzilla'dan {coins} coin sovg'a! IELTS Writing va Speaking javoblaringizni tekshirtirib, "
        "batafsil hisobot oling.",
        "ru": "🎁 Вам подарок от Bandzilla — {coins} coin! Проверьте свои ответы по IELTS Writing и Speaking и "
        "получите подробный отчёт.",
    },
    "gift.received": {
        "uz": "🎁 <b>Sizga {coins} coin sovg'a qilindi!</b>\n\n{name} sizga IELTS tayyorgarligingiz uchun sovg'a "
        "yubordi. Coinlar hisobingizga tushdi — balans: <b>{balance} coin</b>.\n\nWriting yoki Speaking "
        "javobingizni yuboring va batafsil hisobot oling 🦖",
        "ru": "🎁 <b>Вам подарили {coins} coin!</b>\n\n{name} отправил(а) вам подарок для подготовки к IELTS. "
        "Coin уже на вашем балансе — <b>{balance} coin</b>.\n\nОтправьте ответ по Writing или Speaking и получите "
        "подробный отчёт 🦖",
    },
    "gift.claimed_buyer": {
        "uz": "🎉 {name} sovg'angizni oldi — <b>{coins} coin</b> uning hisobiga tushdi. Rahmat!",
        "ru": "🎉 {name} получил(а) ваш подарок — <b>{coins} coin</b> на его балансе. Спасибо!",
    },
    "gift.own": {
        "uz": "🎁 Bu sizning sovg'angiz havolasi. Uni do'stingizga yuboring — coinlar unga tushadi.",
        "ru": "🎁 Это ссылка на ваш подарок. Отправьте её другу — coin получит он.",
    },
    "gift.taken": {"uz": "🎁 Bu sovg'a allaqachon olingan.", "ru": "🎁 Этот подарок уже получен."},
    "gift.missing": {
        "uz": "🎁 Sovg'a topilmadi — havola noto'g'ri ko'chirilgan bo'lishi mumkin.",
        "ru": "🎁 Подарок не найден — возможно, ссылка скопирована не полностью.",
    },
    # --- Coins changed by hand in the admin panel (webapp/main.py) ---
    "coins.added": {
        "uz": "🟡 Hisobingizga <b>{coins} coin</b> qo'shildi. Balans: <b>{balance} coin</b>.",
        "ru": "🟡 На ваш баланс добавлено <b>{coins} coin</b>. Баланс: <b>{balance} coin</b>.",
    },
    "coins.removed": {
        "uz": "🟡 Hisobingizdan <b>{coins} coin</b> ayirildi. Balans: <b>{balance} coin</b>.",
        "ru": "🟡 С вашего баланса списано <b>{coins} coin</b>. Баланс: <b>{balance} coin</b>.",
    },
    "coins.note": {"uz": "💬 {note}", "ru": "💬 {note}"},
    # --- Groups / B2B (bot/handlers/group.py, bot/handlers/start.py) ---
    "group.no_group": {
        "uz": "🏫 <b>Guruh</b>\n\nSiz hali hech qanday guruhda emassiz.\n\n"
        "• <b>Talaba bo'lsangiz:</b> o'qituvchingiz bergan taklif havolasini bosing.\n"
        "• <b>O'qituvchi bo'lsangiz:</b> o'z guruhingizni oching — talabalaringizning "
        "natijalari va hisobotlarini kuzatasiz, ularga coin ulashasiz.",
        "ru": "🏫 <b>Группа</b>\n\nВы пока не состоите ни в одной группе.\n\n"
        "• <b>Если вы ученик:</b> откройте ссылку-приглашение от преподавателя.\n"
        "• <b>Если вы преподаватель:</b> создайте свою группу — будете видеть результаты и "
        "отчёты учеников и сможете дарить им coin.",
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
    "group.btn_stats": {"uz": "📊 Statistika", "ru": "📊 Статистика"},
    "group.btn_students": {"uz": "👥 Talabalar", "ru": "👥 Ученики"},
    "group.btn_gift_all": {"uz": "🎁 Hammaga coin ulashish", "ru": "🎁 Подарить coin всем"},
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
        "🕒 Oxirgi faollik: {last} · 🔥 ketma-ket {streak} kun",
        "ru": "👤 <b>{name}</b>\n📅 В группе с: {joined}\n\n"
        "✍️ Writing: {writing} · средний {writing_avg}\n"
        "🎙 Speaking: {speaking} · средний {speaking_avg}\n"
        "📉 Самый слабый критерий: {weakest}\n"
        "🕒 Последняя активность: {last} · 🔥 дней подряд: {streak}",
    },
    "group.no_works_yet": {
        "uz": "Guruhga qo'shilganidan beri hali ish topshirmagan.",
        "ru": "С момента вступления в группу ещё не сдал ни одной работы.",
    },
    "group.report_unavailable": {
        "uz": "Bu hisobot mavjud emas.",
        "ru": "Этот отчёт недоступен.",
    },
    "group.btn_gift_one": {"uz": "🎁 Coin berish", "ru": "🎁 Подарить coin"},
    "group.btn_kick": {"uz": "🚪 Guruhdan chiqarish", "ru": "🚪 Исключить"},
    "group.gift_one": {
        "uz": "🎁 <b>{name}</b>ga nechta coin berasiz?\nBalansingiz: {balance} coin",
        "ru": "🎁 Сколько coin подарить: <b>{name}</b>?\nВаш баланс: {balance} coin",
    },
    "group.gift_all": {
        "uz": "🎁 Har bir talabaga nechta coin berasiz?\nTalabalar: {count} · balansingiz: {balance} coin",
        "ru": "🎁 Сколько coin подарить каждому ученику?\nУчеников: {count} · ваш баланс: {balance} coin",
    },
    "group.gift_no_balance": {
        "uz": "Balansingizda coin yo'q. Talabalarga ulashish uchun avval coin sotib oling:",
        "ru": "На балансе нет coin. Чтобы дарить их ученикам, сначала купите coin:",
    },
    "group.gift_all_not_enough": {
        "uz": "{count} ta talabaga ulashish uchun balansingiz yetmaydi ({balance} coin). Coin sotib oling:",
        "ru": "Чтобы подарить {count} ученикам, не хватает баланса ({balance} coin). Купите coin:",
    },
    "group.gift_failed": {
        "uz": "Coin berilmadi: balans yetarli emas yoki talaba guruhda emas.",
        "ru": "Coin не переданы: недостаточно баланса или ученик не в группе.",
    },
    "group.gift_done_one": {
        "uz": "✅ <b>{name}</b>ga {amount} coin berildi!",
        "ru": "✅ Подарено {amount} coin: <b>{name}</b>!",
    },
    "group.gift_done_all": {
        "uz": "✅ {count} ta talabaga {amount} coindan berildi (jami {total} coin).",
        "ru": "✅ {count} ученикам подарено по {amount} coin (всего {total}).",
    },
    "group.gift_notify_student": {
        "uz": "🎁 O'qituvchingiz sizga {amount} coin sovg'a qildi!",
        "ru": "🎁 Ваш преподаватель подарил вам {amount} coin!",
    },
    "group.kick_confirm": {
        "uz": "🚪 <b>{name}</b>ni guruhdan chiqarasizmi?\n\nUning ishlari va coinlari o'zida "
        "qoladi, siz endi uning natijalarini ko'rmaysiz.",
        "ru": "🚪 Исключить <b>{name}</b> из группы?\n\nЕго работы и coin останутся у него, "
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
        "chiqariladi (ularning ishlari va coinlari o'zlarida qoladi). Bu amalni qaytarib "
        "bo'lmaydi.",
        "ru": "🗑 Удалить группу \"<b>{name}</b>\"?\n\nВсе ученики будут исключены (их работы "
        "и coin останутся у них). Это действие нельзя отменить.",
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
    # --- Groups: teacher home, one group, centers (bot/handlers/group.py) ---
    "group.teacher_home_title": {"uz": "🏫 <b>Guruhlaringiz</b>", "ru": "🏫 <b>Ваши группы</b>"},
    "group.teacher_home_hint": {
        "uz": "Guruhni tanlang yoki yangisini oching:",
        "ru": "Выберите группу или создайте новую:",
    },
    "group.teacher_no_groups": {
        "uz": "Sizda hali guruh yo'q. Birinchi guruhingizni oching:",
        "ru": "У вас пока нет групп. Создайте первую:",
    },
    "group.group_button": {"uz": "{name} · {count} talaba", "ru": "{name} · {count} уч."},
    "group.btn_new_group": {"uz": "➕ Yangi guruh", "ru": "➕ Новая группа"},
    "group.btn_center": {"uz": "🏢 Markaz", "ru": "🏢 Центр"},
    "group.btn_cabinet": {"uz": "🖥 Web-kabinet", "ru": "🖥 Веб-кабинет"},
    "group.btn_my_groups_student": {"uz": "🎓 Men a'zo bo'lgan guruhlar", "ru": "🎓 Группы, где я ученик"},
    "group.center_line": {"uz": "🏢 Markaz: <b>{name}</b>", "ru": "🏢 Центр: <b>{name}</b>"},
    "group.teacher_line": {"uz": "👨‍🏫 O'qituvchi: {name}", "ru": "👨‍🏫 Преподаватель: {name}"},
    "group.group_title": {"uz": "🏫 <b>{name}</b>\n👥 Talabalar: {count}", "ru": "🏫 <b>{name}</b>\n👥 Учеников: {count}"},
    "group.sponsor_on_line": {
        "uz": "💳 Tekshiruvlarni guruh to'laydi · balans: {balance} coin · kunlik limit: {limit}",
        "ru": "💳 Проверки оплачивает группа · баланс: {balance} coin · лимит в день: {limit}",
    },
    "group.sponsor_off_line": {
        "uz": "💳 Talabalar tekshiruvlarni o'zlari to'laydi",
        "ru": "💳 Ученики оплачивают проверки сами",
    },
    "group.invite_line": {
        "uz": "\n🔗 <b>Taklif havolasi</b> (bosib nusxa oling):\n<code>{link}</code>",
        "ru": "\n🔗 <b>Ссылка-приглашение</b> (нажмите, чтобы скопировать):\n<code>{link}</code>",
    },
    "group.btn_assignments": {"uz": "📝 Vazifalar", "ru": "📝 Задания"},
    "group.btn_top": {"uz": "🏆 Reyting", "ru": "🏆 Рейтинг"},
    "group.btn_top_named": {"uz": "🏆 {name}", "ru": "🏆 {name}"},
    "group.btn_announce": {"uz": "📢 E'lon", "ru": "📢 Объявление"},
    "group.btn_qr": {"uz": "🔳 QR-kod", "ru": "🔳 QR-код"},
    "group.btn_excel": {"uz": "📥 Excel", "ru": "📥 Excel"},
    "group.btn_group_pdf": {"uz": "📄 PDF hisobot", "ru": "📄 PDF-отчёт"},
    "group.btn_settings": {"uz": "⚙️ Sozlamalar", "ru": "⚙️ Настройки"},
    "group.btn_my_assignments": {"uz": "📝 Vazifalarim", "ru": "📝 Мои задания"},
    "group.btn_parent_report": {"uz": "👨‍👩‍👧 Ota-ona uchun oylik hisobot", "ru": "👨‍👩‍👧 Отчёт за месяц для родителей"},
    "group.btn_report_pdf": {"uz": "📄 Hisobot (PDF)", "ru": "📄 Отчёт (PDF)"},
    "group.btn_comment": {"uz": "💬 Izoh yozish", "ru": "💬 Написать комментарий"},
    "group.btn_keep_ai_band": {"uz": "Ballni o'zgartirmaslik", "ru": "Балл не менять"},
    "group.btn_send": {"uz": "✅ Yuborish", "ru": "✅ Отправить"},
    "group.btn_sponsor_on": {"uz": "💳 Tekshiruvlarni men to'layman: yoqish", "ru": "💳 Я оплачиваю проверки: включить"},
    "group.btn_sponsor_off": {"uz": "💳 Tekshiruvlarni men to'layman: o'chirish", "ru": "💳 Я оплачиваю проверки: выключить"},
    "group.btn_board_on": {"uz": "🏆 Reytingni talabalarga ko'rsatish", "ru": "🏆 Показывать рейтинг ученикам"},
    "group.btn_board_off": {"uz": "🏆 Reytingni talabalardan yashirish", "ru": "🏆 Скрыть рейтинг от учеников"},
    "group.btn_open_cabinet": {"uz": "🖥 Kabinetni ochish", "ru": "🖥 Открыть кабинет"},
    "group.on": {"uz": "✅ yoqilgan", "ru": "✅ включено"},
    "group.off": {"uz": "❌ o'chirilgan", "ru": "❌ выключено"},
    "group.limit_none": {"uz": "cheksiz", "ru": "без лимита"},
    "group.settings": {
        "uz": "⚙️ <b>{name}</b> — sozlamalar\n\n"
        "💳 <b>Tekshiruvlarni men to'layman:</b> {sponsor}\n"
        "Yoqilsa, talabalarning tekshiruvlari (narxi coinda) sizning "
        "balansingizdan yechiladi.\n"
        "📏 <b>Kunlik limit</b> (har bir talabaga): {limit}. Vazifalar limitga kirmaydi.\n\n"
        "🏆 <b>Reyting talabalarga ko'rinadi:</b> {board}",
        "ru": "⚙️ <b>{name}</b> — настройки\n\n"
        "💳 <b>Я оплачиваю проверки:</b> {sponsor}\n"
        "Если включено, проверки учеников (цена в coin) списываются с вашего "
        "баланса.\n"
        "📏 <b>Лимит в день</b> (на ученика): {limit}. Задания в лимит не входят.\n\n"
        "🏆 <b>Рейтинг виден ученикам:</b> {board}",
    },
    "group.center_owner_pays": {
        "uz": "ℹ️ Bu markaz guruhi: coin va to'lov sozlamalarini markaz rahbari boshqaradi.",
        "ru": "ℹ️ Это группа центра: coin и оплату настраивает руководитель центра.",
    },
    "group.preparing": {"uz": "⏳ Tayyorlanyapti…", "ru": "⏳ Готовлю…"},
    "group.parent_report_caption": {
        "uz": "👨‍👩‍👧 <b>{name}</b> — oxirgi 30 kunlik hisobot. Ota-onasiga yuborishingiz mumkin.",
        "ru": "👨‍👩‍👧 <b>{name}</b> — отчёт за последние 30 дней. Можно отправить родителям.",
    },
    "group.works_hint": {
        "uz": "📄 Oxirgi ishlari — hisobot va izoh uchun bosing:",
        "ru": "📄 Последние работы — нажмите для отчёта и комментария:",
    },
    "group.work_card": {
        "uz": "📄 <b>{label}</b> · {date}\nAI bahosi: <b>{band}</b>\nMezonlar: {criteria}",
        "ru": "📄 <b>{label}</b> · {date}\nОценка ИИ: <b>{band}</b>\nКритерии: {criteria}",
    },
    "group.work_comment": {"uz": "\n💬 {date}{band}: {text}", "ru": "\n💬 {date}{band}: {text}"},
    "group.ask_comment": {
        "uz": "💬 Talabaga izohingizni yozing — u sizning ismingiz bilan yuboriladi:",
        "ru": "💬 Напишите комментарий ученику — он придёт от вашего имени:",
    },
    "group.ask_comment_band": {
        "uz": "O'zingiz ham ball qo'yasizmi? Tanlang yoki AI bahosini qoldiring:",
        "ru": "Поставите свой балл? Выберите или оставьте оценку ИИ:",
    },
    "group.comment_saved": {
        "uz": "✅ Izoh saqlandi va talabaga yuborildi.",
        "ru": "✅ Комментарий сохранён и отправлен ученику.",
    },
    "group.comment_notify": {
        "uz": "💬 <b>O'qituvchingiz izohi</b> ({teacher})\n{label} · {date}\n\n{text}{band_line}",
        "ru": "💬 <b>Комментарий преподавателя</b> ({teacher})\n{label} · {date}\n\n{text}{band_line}",
    },
    "group.comment_band_line": {"uz": "\n\nO'qituvchi bahosi: <b>{band}</b>", "ru": "\n\nОценка преподавателя: <b>{band}</b>"},
    "group.board": {
        "uz": "🏆 <b>{name}</b> — reyting\n\n<b>Bu hafta eng faol:</b>\n{week}\n\n"
        "<b>🔥 Ketma-ket faol kunlar:</b>\n{streaks}\n\n<b>🚀 Eng ko'p o'sgan</b> (14 kun):\n{improved}",
        "ru": "🏆 <b>{name}</b> — рейтинг\n\n<b>Самые активные на этой неделе:</b>\n{week}\n\n"
        "<b>🔥 Дней подряд:</b>\n{streaks}\n\n<b>🚀 Самый большой рост</b> (14 дней):\n{improved}",
    },
    "group.board_checks": {"uz": "{n} ta tekshiruv", "ru": "проверок: {n}"},
    "group.board_days": {"uz": "{n} kun", "ru": "дней: {n}"},
    "group.board_you": {"uz": "siz", "ru": "вы"},
    "group.board_hidden": {
        "uz": "O'qituvchi reytingni yashirgan.",
        "ru": "Преподаватель скрыл рейтинг.",
    },
    "group.ask_announcement": {
        "uz": "📢 \"<b>{name}</b>\" guruhiga e'lon: xabarni yuboring (matn, rasm, fayl yoki ovozli xabar):",
        "ru": "📢 Объявление для группы \"<b>{name}</b>\": отправьте сообщение (текст, фото, файл или голосовое):",
    },
    "group.announce_confirm": {
        "uz": "Shu xabar {count} ta talabaga yuborilsinmi?",
        "ru": "Отправить это сообщение {count} ученикам?",
    },
    "group.announce_sending": {"uz": "📤 {count} ta talabaga yuborilyapti…", "ru": "📤 Отправляю {count} ученикам…"},
    "group.announce_header": {
        "uz": "📢 <b>{group}</b> — o'qituvchingiz {teacher} e'loni:",
        "ru": "📢 <b>{group}</b> — объявление преподавателя {teacher}:",
    },
    "group.announce_done": {
        "uz": "✅ E'lon {sent} ta talabaga yuborildi. {failed} ta talabaga yetib bormadi (botni bloklagan).",
        "ru": "✅ Объявление отправлено {sent} ученикам. Не доставлено: {failed} (заблокировали бота).",
    },
    "group.qr_caption": {
        "uz": "🔳 <b>{name}</b> — taklif QR-kodi.\nDarsda ekranga yoki doskaga qo'ying: talabalar telefon "
        "kamerasi bilan skanerlab, guruhga qo'shiladi.\n\n{link}",
        "ru": "🔳 <b>{name}</b> — QR-код приглашения.\nПокажите его на уроке: ученики сканируют камерой "
        "телефона и вступают в группу.\n\n{link}",
    },
    "group.center_home": {
        "uz": "🏢 <b>{name}</b> — markaz\n\n👨‍🏫 O'qituvchilar: {teachers}\n🏫 Guruhlar: {groups}\n"
        "👥 Talabalar: {students}\n💳 Markaz balansi: {balance} coin\n"
        "<i>Markaz guruhlarining tekshiruvlari va sovg'alari shu balansdan.</i>\n\n"
        "{teacher_lines}\n\n🔗 <b>O'qituvchi taklif havolasi</b> (bosib nusxa oling):\n<code>{link}</code>\n\n"
        "O'qituvchini markazdan chiqarish uchun pastdagi tugmani bosing.",
        "ru": "🏢 <b>{name}</b> — центр\n\n👨‍🏫 Преподавателей: {teachers}\n🏫 Групп: {groups}\n"
        "👥 Учеников: {students}\n💳 Баланс центра: {balance} coin\n"
        "<i>Проверки и подарки групп центра — с этого баланса.</i>\n\n"
        "{teacher_lines}\n\n🔗 <b>Ссылка для преподавателей</b> (нажмите, чтобы скопировать):\n<code>{link}</code>\n\n"
        "Чтобы исключить преподавателя, нажмите кнопку ниже.",
    },
    "group.center_teacher_row": {"uz": "• {name} — {groups} ta guruh", "ru": "• {name} — групп: {groups}"},
    "group.center_teacher_view": {
        "uz": "🏢 Siz <b>{name}</b> markazining o'qituvchisisiz (rahbar: {owner}).\n\nGuruhlaringizning "
        "coin va to'lovlarini markaz rahbari boshqaradi.",
        "ru": "🏢 Вы преподаватель центра <b>{name}</b> (руководитель: {owner}).\n\nCoin и оплату "
        "ваших групп настраивает руководитель центра.",
    },
    "group.center_kick_confirm": {
        "uz": "❌ <b>{name}</b>ni markazdan chiqarasizmi? Uning guruhlari o'zida qoladi, lekin endi markaz "
        "tarkibida bo'lmaydi.",
        "ru": "❌ Исключить <b>{name}</b> из центра? Его группы останутся у него, но выйдут из центра.",
    },
    "group.center_removed_notify": {
        "uz": "ℹ️ Siz \"{name}\" markazidan chiqarildingiz. Guruhlaringiz o'zingizda qoladi.",
        "ru": "ℹ️ Вы исключены из центра \"{name}\". Ваши группы остаются у вас.",
    },
    "group.center_new_teacher": {
        "uz": "👋 Markazingizga yangi o'qituvchi qo'shildi: <b>{name}</b>.",
        "ru": "👋 В ваш центр вступил новый преподаватель: <b>{name}</b>.",
    },
    "group.center_join_joined": {
        "uz": "✅ Siz \"<b>{name}</b>\" markazining o'qituvchisi bo'ldingiz! Endi 🏫 Guruh → ➕ Yangi guruh "
        "orqali guruh ochishingiz mumkin.",
        "ru": "✅ Вы стали преподавателем центра \"<b>{name}</b>\"! Создайте группу: 🏫 Группа → ➕ Новая группа.",
    },
    "group.center_join_already": {
        "uz": "ℹ️ Siz allaqachon \"<b>{name}</b>\" markazidasiz.",
        "ru": "ℹ️ Вы уже в центре \"<b>{name}</b>\".",
    },
    "group.center_join_owner": {
        "uz": "ℹ️ Bu sizning markazingizning o'qituvchi taklif havolasi — uni o'qituvchilarga yuboring.",
        "ru": "ℹ️ Это ссылка вашего центра для преподавателей — отправьте её им.",
    },
    "group.center_join_other": {
        "uz": "⚠️ Siz boshqa markaz tarkibidasiz. Avval u markaz rahbaridan sizni chiqarishini so'rang.",
        "ru": "⚠️ Вы уже в другом центре. Попросите его руководителя сначала исключить вас.",
    },
    "group.center_join_expired": {
        "uz": "⚠️ Bu markaz havolasi noto'g'ri yoki eskirgan.",
        "ru": "⚠️ Эта ссылка центра неверна или устарела.",
    },
    "group.cabinet_text": {
        "uz": "🖥 <b>Web-kabinet</b> — guruhlaringiz, talabalar jadvali, vazifalar natijalari va hisobotlar "
        "katta ekranda.\n\nHavola 10 daqiqa amal qiladi va faqat siz uchun.",
        "ru": "🖥 <b>Веб-кабинет</b> — ваши группы, таблица учеников, результаты заданий и отчёты на "
        "большом экране.\n\nСсылка действует 10 минут и только для вас.",
    },
    "quota.sponsored_note": {
        "uz": "🏫 Bu tekshiruvni o'qituvchingiz yoki o'quv markazingiz to'laydi.",
        "ru": "🏫 Эту проверку оплачивает ваш преподаватель или учебный центр.",
    },
    # --- Weekly digest (bot/utils/group_scheduler.py) ---
    "gr.digest": {
        "uz": "📬 <b>{name}</b> — haftalik hisobot ({start}–{end})\n\n"
        "👥 Faol talabalar: {active}/{members}\n"
        "📝 Tekshiruvlar: {total} (Writing {writing}, Speaking {speaking})\n"
        "📈 O'rtacha ball: {avg}{trend}\n"
        "🏆 Eng faol: {top}\n"
        "🚀 Eng ko'p o'sgan: {improved}\n"
        "📉 Zaif mezon: {weakest}\n"
        "😴 Bu hafta ishlamaganlar: {inactive}",
        "ru": "📬 <b>{name}</b> — отчёт за неделю ({start}–{end})\n\n"
        "👥 Активных учеников: {active}/{members}\n"
        "📝 Проверок: {total} (Writing {writing}, Speaking {speaking})\n"
        "📈 Средний балл: {avg}{trend}\n"
        "🏆 Самые активные: {top}\n"
        "🚀 Самый большой рост: {improved}\n"
        "📉 Слабый критерий: {weakest}\n"
        "😴 Не занимались на неделе: {inactive}",
    },
    "gr.digest_assignment": {
        "uz": "📝 {title}: {complete}/{members} bajardi",
        "ru": "📝 {title}: выполнили {complete}/{members}",
    },
    "gr.digest_balance": {
        "uz": "💳 To'lov balansi: {balance} coin",
        "ru": "💳 Баланс для оплаты: {balance} coin",
    },
    # --- Assignments (bot/handlers/group_assignments.py) ---
    "as.list": {"uz": "📝 <b>{name}</b> — vazifalar\n\nNatijani ko'rish uchun vazifani tanlang:", "ru": "📝 <b>{name}</b> — задания\n\nВыберите задание, чтобы увидеть результаты:"},
    "as.list_empty": {"uz": "📝 <b>{name}</b> — hali vazifa yo'q.\n\nBirinchi uy vazifasi yoki mock testni bering:", "ru": "📝 <b>{name}</b> — заданий пока нет.\n\nДайте первое домашнее задание или mock-тест:"},
    "as.closed": {"uz": "yopilgan", "ru": "закрыто"},
    "as.btn_new": {"uz": "➕ Yangi vazifa", "ru": "➕ Новое задание"},
    "as.btn_mock": {"uz": "🎯 Mock test (Writing 1+2, Speaking 1–3)", "ru": "🎯 Mock-тест (Writing 1+2, Speaking 1–3)"},
    "as.choose_type": {"uz": "Qanday vazifa berasiz?", "ru": "Какое задание дадите?"},
    "as.choose_source": {"uz": "<b>{label}</b> — savolni qayerdan olamiz?", "ru": "<b>{label}</b> — откуда взять вопрос?"},
    "as.btn_bank": {"uz": "📚 Savollar bankidan", "ru": "📚 Из банка вопросов"},
    "as.btn_own": {"uz": "✍️ O'z savolim", "ru": "✍️ Свой вопрос"},
    "as.bank_empty": {"uz": "Bankda bu turdagi savol topilmadi. O'z savolingizni yozing.", "ru": "В банке нет вопросов этого типа. Напишите свой."},
    "as.picked": {"uz": "Tanlangan savol(lar):", "ru": "Выбранные вопросы:"},
    "as.btn_use": {"uz": "✅ Shu savol", "ru": "✅ Этот вопрос"},
    "as.btn_another": {"uz": "🔄 Boshqasi", "ru": "🔄 Другой"},
    "as.ask_own": {"uz": "✍️ Savol matnini yuboring:", "ru": "✍️ Отправьте текст вопроса:"},
    "as.ask_own_task1": {
        "uz": "✍️ Grafik/diagramma rasmini yuboring va <b>rasm ostiga savol matnini</b> yozing (yoki faqat matn):",
        "ru": "✍️ Отправьте фото графика/диаграммы и <b>текст вопроса в подписи</b> (или только текст):",
    },
    "as.ask_own_speaking": {
        "uz": "✍️ Savollarni yuboring — har birini yangi qatorda:",
        "ru": "✍️ Отправьте вопросы — каждый с новой строки:",
    },
    "as.own_needs_text": {"uz": "Savol matni kerak — rasm ostiga yoki alohida xabar qilib yozing.", "ru": "Нужен текст вопроса — в подписи к фото или отдельным сообщением."},
    "as.own_preview": {"uz": "Vazifa savoli:", "ru": "Вопрос задания:"},
    "as.choose_due": {"uz": "⏰ Muddat qachongacha?", "ru": "⏰ Срок сдачи?"},
    "as.due_today": {"uz": "Bugun", "ru": "Сегодня"},
    "as.due_tomorrow": {"uz": "Ertaga", "ru": "Завтра"},
    "as.due_3": {"uz": "3 kun", "ru": "3 дня"},
    "as.due_7": {"uz": "1 hafta", "ru": "1 неделя"},
    "as.created": {"uz": "✅ Vazifa berildi va {sent} ta talabaga yuborildi. Muddat: {due}.", "ru": "✅ Задание создано и отправлено {sent} ученикам. Срок: {due}."},
    "as.new_notify": {
        "uz": "📝 <b>Yangi vazifa</b> — {group}\n{title}\n⏰ Muddat: {due}",
        "ru": "📝 <b>Новое задание</b> — {group}\n{title}\n⏰ Срок: {due}",
    },
    "as.new_mock_notify": {
        "uz": "🎯 <b>Mock test</b> — {group}\nWriting Task 1 + 2 va Speaking Part 1–3, ketma-ket.\n⏰ Muddat: {due}",
        "ru": "🎯 <b>Mock-тест</b> — {group}\nWriting Task 1 + 2 и Speaking Part 1–3, по порядку.\n⏰ Срок: {due}",
    },
    "as.btn_do": {"uz": "✍️ Bajarish", "ru": "✍️ Выполнить"},
    "as.btn_next_part": {"uz": "➡️ Keyingi qism", "ru": "➡️ Следующая часть"},
    "as.results": {
        "uz": "📝 <b>{title}</b>\n⏰ Muddat: {due}\n✅ To'liq bajargan: {complete}/{members}\n",
        "ru": "📝 <b>{title}</b>\n⏰ Срок: {due}\n✅ Выполнили полностью: {complete}/{members}\n",
    },
    "as.mock_legend": {
        "uz": "\n<i>Tartib: W1 · W2 · S1 · S2 · S3 → Writing+Speaking umumiy bali.</i>",
        "ru": "\n<i>Порядок: W1 · W2 · S1 · S2 · S3 → общий балл Writing+Speaking.</i>",
    },
    "as.more_in_excel": {"uz": "… yana {n} ta talaba — to'liq jadval Excel'da.", "ru": "… ещё {n} учеников — полная таблица в Excel."},
    "as.btn_remind": {"uz": "🔔 Bajarmaganlarga eslatish", "ru": "🔔 Напомнить невыполнившим"},
    "as.btn_delete": {"uz": "🗑 Vazifani o'chirish", "ru": "🗑 Удалить задание"},
    "as.reminded": {"uz": "🔔 {n} ta talabaga eslatma yuborildi.", "ru": "🔔 Напоминание отправлено {n} ученикам."},
    "as.reminder": {
        "uz": "🔔 <b>Eslatma</b>: \"{title}\" vazifasi ({group}) hali bajarilmagan.\n⏰ Muddat: {due}",
        "ru": "🔔 <b>Напоминание</b>: задание \"{title}\" ({group}) ещё не выполнено.\n⏰ Срок: {due}",
    },
    "as.delete_confirm": {
        "uz": "🗑 \"<b>{title}</b>\" vazifasini o'chirasizmi? Talabalarning ishlari o'zlarida qoladi.",
        "ru": "🗑 Удалить задание \"<b>{title}</b>\"? Работы учеников останутся у них.",
    },
    "as.my_title": {"uz": "📝 <b>Vazifalarim</b>", "ru": "📝 <b>Мои задания</b>"},
    "as.my_empty": {"uz": "Hozircha vazifa yo'q.", "ru": "Пока заданий нет."},
    "as.due": {"uz": "muddat", "ru": "срок"},
    "as.status_done": {"uz": "✅ bajarildi ({bands})", "ru": "✅ выполнено ({bands})"},
    "as.status_open": {"uz": "⏳ {done}/{total}", "ru": "⏳ {done}/{total}"},
    "as.status_late": {"uz": "⚠️ muddati o'tgan ({done}/{total})", "ru": "⚠️ срок прошёл ({done}/{total})"},
    "as.not_available": {"uz": "Bu vazifa endi mavjud emas.", "ru": "Это задание больше недоступно."},
    "as.part_header": {"uz": "🎯 <b>{title}</b> — {n}/{total}-qism", "ru": "🎯 <b>{title}</b> — часть {n}/{total}"},
    "as.homework_header": {"uz": "📝 <b>Vazifa:</b> {title}", "ru": "📝 <b>Задание:</b> {title}"},
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
    # --- The previous bot, after the move to the new one (bot/moved.py) ---
    "moved.text": {
        "uz": "🚀 <b>Biz yangi botga ko'chdik: @{bot}</b>\n\nHisobingiz, balansingiz, obunangiz va barcha "
              "tekshiruvlaringiz saqlangan. Quyidagi tugmani bosing, yangi botda <b>Start</b> ni bosing va "
              "davom eting.",
        "ru": "🚀 <b>Мы переехали в новый бот: @{bot}</b>\n\nВаш аккаунт, баланс, подписка и все проверки "
              "сохранены. Нажмите кнопку ниже, затем <b>Start</b> в новом боте - и продолжайте.",
    },
    "moved.button": {"uz": "➡️ @{bot} ga o'tish", "ru": "➡️ Перейти в @{bot}"},
    # --- Admin bilan aloqa (bot/handlers/support.py, bot/handlers/crm.py) ---
    "support.ask": {
        "uz": "💬 Savolingiz, taklifingiz yoki muammoingizni yozing - matn, rasm, fayl yoki ovozli xabar "
              "bo'lishi mumkin. Admin javobi shu chatga keladi.",
        "ru": "💬 Напишите ваш вопрос, предложение или проблему - текстом, фото, файлом или голосовым "
              "сообщением. Ответ администратора придёт в этот чат.",
    },
    "support.sent": {
        "uz": "✅ Adminga yuborildi, javob shu yerga keladi. Yana biror narsa qo'shmoqchi bo'lsangiz, yozavering.",
        "ru": "✅ Отправлено администратору, ответ придёт сюда. Если хотите что-то добавить - просто напишите.",
    },
    "support.unavailable": {
        "uz": "Bu bo'lim hozircha ishlamayapti, birozdan so'ng urinib ko'ring.",
        "ru": "Этот раздел пока недоступен, попробуйте чуть позже.",
    },
    "support.too_many": {
        "uz": "Juda ko'p xabar yuborildi - bir soatdan so'ng yana yozing.",
        "ru": "Слишком много сообщений - напишите снова через час.",
    },
    "support.answer": {"uz": "💬 <b>Admin javobi:</b>", "ru": "💬 <b>Ответ администратора:</b>"},
    "support.btn_reply": {"uz": "✍️ Javob yozish", "ru": "✍️ Ответить"},
    # --- Share (bot/handlers/share.py) ---
    "share.not_found": {"uz": "Bu natija topilmadi.", "ru": "Результат не найден."},
    "share.caption": {
        "uz": "Natijangizni do'stlaringizga ulashing! 🦖📤",
        "ru": "Поделитесь результатом с друзьями! 🦖📤",
    },
    "share.button": {"uz": "📤 Ulashish uchun rasm", "ru": "📤 Изображение для публикации"},
}

for _key, _text in EN.items():
    if _key in _T:
        _T[_key]["en"] = _text


def t(key: str, lang: str | None = None, **kwargs: object) -> str:
    """Looks up `key` for `lang` ("uz"/"ru"/"en"), falling back to Uzbek (then to
    whatever's available) if the language or the key itself is missing -
    a translation gap degrades gracefully instead of crashing or leaking a
    raw key into the chat."""
    entry = _T.get(key)
    if entry is None:
        return key
    text = entry.get(lang or DEFAULT_LANG) or entry.get(DEFAULT_LANG) or next(iter(entry.values()))
    return text.format(**kwargs) if kwargs else text
