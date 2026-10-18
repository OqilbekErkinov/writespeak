/* Bandzilla landing: language switch (UZ in the HTML, RU and EN below), the sample
   report's notes, the hero chat replay, scroll reveals and the mobile CTA.
   No dependencies. */
(function () {
  "use strict";

  var RU = {
    "a11y.skip": "Перейти к содержанию",
    "nav.label": "Разделы",
    "nav.report": "Отчёт",
    "nav.speaking": "Speaking",
    "nav.how": "Как это работает",
    "nav.pricing": "Цены",
    "nav.faq": "Вопросы",
    "nav.cta": "Открыть бота",
    "nav.gift": "8 coin",
    "nav.menu": "Меню",
    "nav.teachers": "Преподавателям",

    "hero.cap": "IELTS Writing и Speaking · Telegram-бот",
    "hero.title": "Увидьте свои ошибки.<br><span class=\"t-lime\">Поднимите свой балл.</span>",
    "hero.lead": "Отправьте эссе или голосовой ответ. За 2–4 минуты Bandzilla найдёт каждую ошибку, исправит её и объяснит, почему это ошибка. Балл — по 4 критериям, которыми пользуются экзаменаторы IELTS.",
    "hero.cta": "Проверить бесплатно",
    "hero.cta2": "Посмотреть пример отчёта",
    "hero.note": "@bandzilla_bot · откроется в Telegram",
    "hero.t1": "8 coin в подарок — первые проверки бесплатно",
    "hero.t2": "Карта не нужна",
    "hero.t3": "Coin списываются, только когда отчёт готов",
    "hero.sample": "Пример диалога",

    "chat.status": "бот",
    "chat.wait": "🦷 Разбираю… зубы острые! Это займёт 2–4 минуты.",
    "chat.pdf": "PDF-отчёт · 6 страниц",
    "chat.caption": "<b>Gotcha! Нашёл ошибок: 8 🦖</b><br>Ваш результат: <b>Band 6.5</b>",
    "chat.again": "🔁 Проверить ещё",
    "chat.share": "📤 Поделиться",
    "chat.input": "Сообщение",

    "pain.cap": "Знакомо?",
    "pain.title": "Самое сложное в подготовке — <span class=\"pen\">увидеть свои ошибки</span>",
    "pain.1t": "Эссе написано. А кто проверит?",
    "pain.1d": "Ответ от преподавателя идёт днями. За это время вы пишете новое эссе — и повторяете те же ошибки.",
    "pain.2t": "Не с кем практиковать Speaking",
    "pain.2d": "Говорите перед зеркалом. Но кто оценит беглость, произношение и грамматику?",
    "pain.3t": "Балл есть, причины нет",
    "pain.3d": "Цифра «6.0» не говорит, что исправлять. Пока не знаете, какие предложения и слова снижают балл, он стоит на месте.",
    "pain.bridge": "<b>Не нужно ждать преподавателя.</b> Bandzilla проверит ваш ответ за 2–4 минуты — днём и ночью, прямо в Telegram.",
    "pain.cta": "Попробовать сейчас",

    "rep.cap": "Пример отчёта",
    "rep.title": "Не просто балл — разбор <mark class=\"hl\">каждой ошибки</mark>",
    "rep.sub": "После каждой проверки приходит цветной PDF-отчёт. Ниже — его фрагмент: нажмите на выделенные слова и прочитайте комментарий 👇",
    "rep.band": "Общий Band",
    "rep.target": "Следующая цель · 7.0",
    "tone.good": "Сильная фраза",
    "tone.pen": "Gotcha: ошибка",
    "rep.notelang": "Отчёт — на английском, как и сам экзамен.",
    "rep.vocab": "Слова по этой теме",
    "rep.inside": "Что в отчёте",
    "rep.i1": "<b>Общий Band и 4 критерия</b> — балл по каждому и объяснение, почему именно такой",
    "rep.i2": "<b>Пометки прямо в вашем тексте</b> — ошибки и сильные фразы видны с первого взгляда",
    "rep.i3": "<b>Каждая ошибка — с исправлением</b> — почему это ошибка и как правильно",
    "rep.i4": "<b>3 главных приоритета</b> — сначала ошибки, которые съедают больше всего баллов",
    "rep.i5": "<b>Модельный ответ, написанный для вас</b> — на ваш вопрос, в стиле авторов с высоким баллом, на уровне Band 8 или 9",
    "rep.i6": "<b>10–12 слов и выражений уровня Band 7+</b> — по теме, со значением и примером",
    "rep.i7": "<b>Прогресс и чек-лист</b> — сравнение с прошлыми результатами и напоминания на следующий раз",
    "rep.disc": "<b>Как выставляется балл?</b> По IELTS Band Descriptors, которыми пользуются экзаменаторы: 4 критерия, шкала 0–9. Каждый критерий оценивается трижды независимо, и засчитывается срединная оценка.",
    "rep.cta": "Проверить своё эссе",
    "rep.pages": "Страницы настоящего отчёта",
    "rep.dlW": "Скачать весь пример Writing (PDF, 6 страниц)",
    "rep.dlS": "Скачать весь пример Speaking (PDF, 7 страниц)",
    "rep.s1": "Обложка: общий балл, 4 критерия и 3 приоритета",
    "rep.s2": "Ваши ответы текстом — ошибки и слова-паразиты отмечены",
    "rep.s3": "Ваш голос измерен: темп, паузы, интонация",
    "rep.s4": "Модельный ответ на каждый вопрос — его можно и послушать",
    "rep.s1alt": "Обложка отчёта Speaking: общий Band 6.0, баллы по 4 критериям и 3 приоритета",
    "rep.s2alt": "Страница отчёта Speaking: текст ответов с ошибками, исправлениями и словами-паразитами",
    "rep.s3alt": "Страница отчёта Speaking: темп речи, длинные паузы, слова-паразиты и интонация",
    "rep.s4alt": "Страница отчёта Speaking: модельный ответ на каждый вопрос, его голос можно послушать в Telegram",
    "sp.sample": "Посмотреть пример отчёта Speaking",
    "rep.p1": "Обложка: общий балл, 4 критерия и 3 приоритета",
    "rep.p2": "Каждая ошибка — с исправлением и объяснением",
    "rep.p3": "Модельный ответ, написанный для вас",
    "rep.p1alt": "Обложка PDF-отчёта Bandzilla: общий Band 6.5, баллы по 4 критериям и 3 приоритета",
    "rep.p2alt": "Страница отчёта: каждая ошибка, её исправление и объяснение",
    "rep.p3alt": "Страница отчёта: модельный ответ на тот же вопрос уровня Band 8 или 9",

    "sp.title": "Как на <mark class=\"hl\">настоящем экзамене</mark> — вопрос за вопросом",
    "sp.sub": "Bandzilla задаёт вопросы по одному, вы отвечаете голосовыми. В конце все ответы оцениваются вместе — и приходит один отчёт.",
    "sp.1t": "Part 1, 2, 3 или полный тест",
    "sp.1d": "Отправьте свой вопрос или возьмите готовый в Practice. В полном тесте все три части — в одном отчёте.",
    "sp.2t": "Cue card для Part 2",
    "sp.2d": "1 минута на подготовку, затем 1–2 минуты речи — как на экзамене.",
    "sp.3t": "Голос измеряется",
    "sp.3d": "Произношение, темп, длинные паузы и слова-паразиты измеряются по записи вашего голоса, а не по тексту.",
    "sp.4t": "Балл по 4 критериям",
    "sp.4d": "Fluency &amp; Coherence, Lexical Resource, Grammatical Range &amp; Accuracy и Pronunciation — по каждому отдельно.",
    "sp.q1": "❓ <b>Вопрос 1/4</b>",
    "sp.q2": "❓ <b>Вопрос 2/4</b>",
    "sp.q3": "❓ <b>Вопрос 3/4</b>",
    "sp.finish": "✅ Завершить и оценить",

    "how.cap": "Как это работает",
    "how.title": "3 шага — и отчёт у вас",
    "how.1t": "Откройте бота",
    "how.1d": "Откройте @bandzilla_bot в Telegram и нажмите «Start». Выберите язык — никаких анкет. Сразу получите 8 coin в подарок.",
    "how.2t": "Отправьте вопрос и ответ",
    "how.2d": "Выберите Writing или Speaking. Отправьте вопрос и ответ так, как удобно: текстом, фото, файлом или голосом.",
    "how.3t": "Получите отчёт",
    "how.3d": "Через 2–4 минуты придёт PDF с баллом, разбором каждой ошибки, модельным ответом и новыми словами. Результатом можно поделиться с друзьями.",
    "how.formats": "Отправляйте в любом формате:",
    "fmt.text": "Текст",
    "fmt.photo": "Фото",
    "fmt.hand": "Рукопись",
    "fmt.voice": "Голосовое",
    "fmt.video": "Видеосообщение",

    "ft.cap": "Возможности",
    "ft.title": "Вся подготовка — <span class=\"t-lime\">в одном боте</span>",
    "ft.1t": "Practice: 700+ вопросов",
    "ft.1d": "Вопросы Writing и Speaking с недавних экзаменов, база регулярно пополняется. Выполненные отмечаются ✅, а «Следующий вопрос» не даёт остановиться.",
    "ft.2t": "Графики Task 1",
    "ft.2d": "Отправьте и фото графика или диаграммы — Bandzilla сверит каждую цифру в вашем ответе с данными на картинке.",
    "ft.3t": "Прогресс на виду",
    "ft.3d": "В «Профиле» — архив всех отчётов и динамика балла на яркой карточке статистики.",
    "ft.4t": "Личный словарь",
    "ft.4d": "Полезные слова из отчётов сами попадают в «Мой словарь» — с переводом. Учите их интервальным повторением, тестами и с ежедневным напоминанием.",
    "ft.5t": "Делитесь результатом",
    "ft.5d": "В один клик — красивая карточка с вашим баллом для Instagram или Telegram.",
    "ft.6t": "24/7, на узбекском, русском и английском",
    "ft.6d": "Работает и ночью, и накануне экзамена. Бот говорит на узбекском, русском и английском.",
    "ft.rcnote": "По 4 критериям · пример",
    "ft.rcfoot": "Попробуйте и вы: @bandzilla_bot",

    "pr.cap": "Цены",
    "pr.title": "Начать — <mark class=\"hl\">бесплатно</mark>",
    "pr.sub": "При первом входе в бот — 8 coin в подарок. Дальше каждая проверка оплачивается в coin — без подписки, coin не сгорают.",
    "pr.ctitle": "Цена одной проверки",
    "pr.cnote": "1 coin = 1 000 сум. Coin списываются, только когда отчёт готов.",
    "pr.full": "Полный тест (Part 1+2+3)",
    "pr.1n": "Подарок",
    "pr.1d": "Чтобы попробовать бота",
    "pr.1p": "0 сум",
    "pr.1f1": "Например, два Writing Task 1, один Task 2 или одна часть Speaking",
    "pr.1f2": "Writing и Speaking",
    "pr.1f3": "Полный PDF-отчёт",
    "pr.1f4": "Practice, словарь и прогресс",
    "pr.1b": "Начать бесплатно",
    "pr.2n": "Пакеты coin",
    "pr.2d": "Берите сколько нужно — в большом пакете дешевле",
    "pr.percoin": "сум / coin",
    "pr.2cap": "Цены пакетов coin",
    "u.som": "сум",
    "pr.2foot": "Coin бессрочные · оплата картой",
    "pr.2b": "Купить в боте",
    "pr.badge": "Для преподавателей и центров",
    "pr.3n": "Пакеты для центров",
    "pr.3d": "Проверки учеников оплачиваете вы",
    "pr.3p": "<span>от</span><b>720</b><span>сум / coin</span>",
    "pr.3cap": "Цены пакетов для центров",
    "pr.3f1": "Группы, задания и mock-тесты",
    "pr.3f2": "Подарки coin ученикам, дневной лимит, недельная сводка",
    "pr.3b": "Купить в боте",
    "pr.ref": "<b>Пригласите друга.</b> Когда он впервые купит coin, вы оба получите по 5 coin. Ваша ссылка — в разделе «👤 Профиль» в боте.",
    "pr.pay": "Оплата переводом на карту: переводите, отправляете боту фото чека, после подтверждения coin зачисляются на счёт.",

    "te.cap": "Для преподавателей и учебных центров",
    "te.title": "Вся ваша группа — в одном боте",
    "te.text": "Отправьте запрос как преподаватель — после подтверждения сможете создать группу. Ученики вступают по ссылке-приглашению, а вы видите у каждого количество работ, средний балл и последнюю активность.",
    "te.1": "Вступление по ссылке-приглашению",
    "te.2": "Статистика и отчёты каждого ученика",
    "te.3": "Задания и mock-тесты — с напоминанием до дедлайна",
    "te.4": "Дарите ученикам coin или оплачивайте их проверки сами",
    "te.5": "Каждый понедельник — недельная сводка",
    "te.cta": "Создать группу",
    "te.dgroup": "🏫 IELTS вечерняя группа",
    "te.dsample": "пример",
    "te.dstudent": "Ученик",
    "te.dworks": "Работ",
    "te.davg": "Средний",

    "faq.cap": "Вопросы",
    "faq.title": "Частые вопросы",
    "faq.more": "Не нашли ответ? Напишите в бот — поможем.",
    "faq.cta": "Написать в бот",
    "faq.1q": "Насколько можно доверять баллу Bandzilla?",
    "faq.1a": "Bandzilla оценивает по официальным IELTS Band Descriptors, которыми пользуются экзаменаторы: те же 4 критерия, та же шкала 0–9. Каждый критерий оценивается трижды независимо, и засчитывается срединная оценка — поэтому результат стабилен. И главное — за каждым баллом стоит причина: видно, какое предложение и какое слово его снизили. Официальный сертификат IELTS выдаёт только экзаменационный центр.",
    "faq.2q": "Можно отправить эссе, написанное от руки?",
    "faq.2a": "Да. Сфотографируйте и отправьте — если страниц несколько, альбомом. Bandzilla распознает текст и покажет его перед проверкой: если какое-то слово прочитано неверно, просто исправьте.",
    "faq.3q": "Сколько ждать результат?",
    "faq.3a": "Обычно 2–4 минуты. Bandzilla сообщает, что идёт разбор, и сразу присылает PDF-отчёт, когда он готов.",
    "faq.4q": "Как сдавать Speaking?",
    "faq.4a": "Выберите Part или полный тест, отправьте вопросы (или возьмите готовые из Practice) и отвечайте на каждый голосовым сообщением. Видеосообщения и аудиофайлы тоже принимаются.",
    "faq.5q": "Сколько coin стоит одна проверка?",
    "faq.5a": "Writing Task 1 — 4 coin, Task 2 — 5 coin. Speaking Part 1 — 6, Part 2 — 5, Part 3 — 8, полный тест (Part 1+2+3 в одном отчёте) — 16 coin. Coin списываются, только когда отчёт готов.",
    "faq.6q": "Что, если подарочные coin закончились?",
    "faq.6a": "Купите пакет coin: 10 coin — 10 000 сум, 25 — 23 000, 50 — 45 000, 100 — 88 000, 200 — 170 000 сум. Coin бессрочные. Оплата переводом на карту: переводите, отправляете боту фото чека, после подтверждения coin зачисляются на счёт.",
    "faq.7q": "Мои отчёты сохраняются?",
    "faq.7a": "Да. В «👤 Профиль» → «История» можно заново скачать любой отчёт, а в «Статистике» — увидеть, как растёт ваш балл.",
    "faq.8q": "На каком языке бот?",
    "faq.8a": "Интерфейс на узбекском, русском и английском — выбираете при первом входе, потом можно сменить в «Профиле». Эссе и ответы, конечно, на английском; PDF-отчёт тоже на английском.",

    "fin.title": "Проверьте следующее эссе <span class=\"ink-hl\">сегодня</span>",
    "fin.sub": "Первые проверки — за подарочные coin, бесплатно. Откройте бота и начните за минуту.",
    "fin.cta": "Открыть бота — бесплатно",
    "fin.qr": "Вы за компьютером? Отсканируйте камерой телефона",

    "foot.legal": "IELTS — зарегистрированный товарный знак British Council, IDP: IELTS Australia и Cambridge University Press &amp; Assessment. Bandzilla — независимый сервис и с ними не связан. Официальный результат IELTS выдаёт только экзаменационный центр."
  };

  var EN = {
    "a11y.skip": "Skip to content",
    "nav.label": "Sections",
    "nav.report": "Report",
    "nav.speaking": "Speaking",
    "nav.how": "How it works",
    "nav.pricing": "Pricing",
    "nav.faq": "FAQ",
    "nav.cta": "Open the bot",
    "nav.gift": "8 coins",
    "nav.menu": "Menu",
    "nav.teachers": "For teachers",

    "hero.cap": "IELTS Writing and Speaking · Telegram bot",
    "hero.title": "See your mistakes.<br><span class=\"t-lime\">Raise your band.</span>",
    "hero.lead": "Send your essay or a voice answer. In 2–4 minutes Bandzilla finds every mistake, fixes it and explains why it’s wrong. Your band is scored on the 4 criteria IELTS examiners use.",
    "hero.cta": "Try it free",
    "hero.cta2": "See a sample report",
    "hero.note": "@bandzilla_bot · opens in Telegram",
    "hero.t1": "8 coins as a gift — your first checks are free",
    "hero.t2": "No card needed",
    "hero.t3": "Coins are charged only when the report is ready",
    "hero.sample": "Sample chat",

    "chat.status": "bot",
    "chat.wait": "🦷 Chewing it over… my teeth are sharp! This takes 2–4 minutes.",
    "chat.pdf": "PDF report · 6 pages",
    "chat.caption": "<b>Gotcha! I found 8 mistakes 🦖</b><br>Your result: <b>Band 6.5</b>",
    "chat.again": "🔁 Check another",
    "chat.share": "📤 Share",
    "chat.input": "Message",

    "pain.cap": "Sound familiar?",
    "pain.title": "The hardest part of preparing is <span class=\"pen\">seeing your own mistakes</span>",
    "pain.1t": "The essay is written. Who will check it?",
    "pain.1d": "A teacher’s feedback takes days. Meanwhile you write a new essay — and repeat the same mistakes.",
    "pain.2t": "No one to practise Speaking with",
    "pain.2d": "You talk to the mirror. But who will judge your fluency, pronunciation and grammar?",
    "pain.3t": "A band, but no reason",
    "pain.3d": "A “6.0” doesn’t tell you what to fix. Until you know which sentences and words pull your band down, it won’t move.",
    "pain.bridge": "<b>No more waiting for a teacher.</b> Bandzilla checks your answer in 2–4 minutes — day or night, right in Telegram.",
    "pain.cta": "Try it now",

    "rep.cap": "Sample report",
    "rep.title": "Not just a band — a breakdown of <mark class=\"hl\">every mistake</mark>",
    "rep.sub": "After every check you get a full-colour PDF report. Below is a part of one: tap the highlighted words and read the note 👇",
    "rep.band": "Overall Band",
    "rep.target": "Next target · 7.0",
    "tone.good": "Strong phrase",
    "tone.pen": "Gotcha: mistake",
    "rep.notelang": "The report is in English — the language of the exam.",
    "rep.vocab": "Words for this topic",
    "rep.inside": "What’s in the report",
    "rep.i1": "<b>Overall Band and 4 criteria</b> — a band for each and why it is exactly that",
    "rep.i2": "<b>Marks right in your text</b> — mistakes and strong phrases visible at a glance",
    "rep.i3": "<b>Every mistake corrected</b> — why it’s wrong and how to say it right",
    "rep.i4": "<b>3 top priorities</b> — the mistakes that cost you the most come first",
    "rep.i5": "<b>A model answer written for you</b> — to your question, in the style of high-scoring writers, at Band 8 or 9",
    "rep.i6": "<b>10–12 Band 7+ words and phrases</b> — on your topic, with meaning and an example",
    "rep.i7": "<b>Progress and a checklist</b> — compared with your past results, with reminders for next time",
    "rep.disc": "<b>How is the band given?</b> By the IELTS Band Descriptors examiners use: 4 criteria on a 0–9 scale. Each criterion is scored three times independently and the middle score counts.",
    "rep.cta": "Check my essay",
    "rep.pages": "Pages from a real report",
    "rep.dlW": "Download the full Writing sample (PDF, 6 pages)",
    "rep.dlS": "Download the full Speaking sample (PDF, 7 pages)",
    "rep.s1": "Cover: overall band, 4 criteria and 3 priorities",
    "rep.s2": "Your answers as text — mistakes and filler words marked",
    "rep.s3": "Your voice measured: pace, pauses, intonation",
    "rep.s4": "A model answer to every question — you can listen to it too",
    "rep.s1alt": "Speaking report cover: overall Band 6.0, bands for the 4 criteria and 3 priorities",
    "rep.s2alt": "Speaking report page: the text of the answers with mistakes, corrections and filler words marked",
    "rep.s3alt": "Speaking report page: speech rate, long pauses, filler words and intonation",
    "rep.s4alt": "Speaking report page: a model answer to every question, with audio you can play in Telegram",
    "sp.sample": "See a sample Speaking report",
    "rep.p1": "Cover: overall band, 4 criteria and 3 priorities",
    "rep.p2": "Every mistake — with a correction and an explanation",
    "rep.p3": "A model answer written for you",
    "rep.p1alt": "Bandzilla PDF report cover: overall Band 6.5, bands for the 4 criteria and 3 priorities",
    "rep.p2alt": "Report page: every mistake, its correction and an explanation",
    "rep.p3alt": "Report page: a model answer to the same question at Band 8 or 9",

    "sp.title": "Just like <mark class=\"hl\">the real exam</mark> — one question at a time",
    "sp.sub": "Bandzilla asks the questions one by one and you answer with voice messages. At the end all your answers are scored together — and one report arrives.",
    "sp.1t": "Part 1, 2, 3 or the full test",
    "sp.1d": "Send your own question or take a ready one from Practice. In the full test all three parts go into one report.",
    "sp.2t": "Cue card for Part 2",
    "sp.2d": "1 minute to prepare, then 1–2 minutes of speaking — as in the exam.",
    "sp.3t": "Your voice is measured",
    "sp.3d": "Pronunciation, pace, long pauses and filler words are measured from the recording of your voice, not from text.",
    "sp.4t": "A band on 4 criteria",
    "sp.4d": "Fluency &amp; Coherence, Lexical Resource, Grammatical Range &amp; Accuracy and Pronunciation — each scored separately.",
    "sp.q1": "❓ <b>Question 1/4</b>",
    "sp.q2": "❓ <b>Question 2/4</b>",
    "sp.q3": "❓ <b>Question 3/4</b>",
    "sp.finish": "✅ Finish and grade",

    "how.cap": "How it works",
    "how.title": "3 steps — and the report is yours",
    "how.1t": "Open the bot",
    "how.1d": "Open @bandzilla_bot in Telegram and tap “Start”. Choose your language — no forms to fill in. You get 8 coins as a gift right away.",
    "how.2t": "Send the question and your answer",
    "how.2d": "Choose Writing or Speaking. Send the question and your answer however suits you: text, a photo, a file or your voice.",
    "how.3t": "Get your report",
    "how.3d": "In 2–4 minutes a PDF arrives with your band, every mistake explained, a model answer and new words. You can share your result with friends too.",
    "how.formats": "Send it in any format:",
    "fmt.text": "Text",
    "fmt.photo": "Photo",
    "fmt.hand": "Handwriting",
    "fmt.voice": "Voice message",
    "fmt.video": "Video message",

    "ft.cap": "Features",
    "ft.title": "All your preparation — <span class=\"t-lime\">in one bot</span>",
    "ft.1t": "Practice: 700+ questions",
    "ft.1d": "Writing and Speaking questions from recent exams; the bank is updated regularly. The ones you’ve done get a ✅, and “Next question” keeps you going.",
    "ft.2t": "Task 1 charts",
    "ft.2d": "Send the chart or diagram image too — Bandzilla checks every number in your answer against the data in the picture.",
    "ft.3t": "Progress in sight",
    "ft.3d": "“My account” keeps an archive of all your reports and your band trend on a bright statistics card.",
    "ft.4t": "Your own vocabulary",
    "ft.4d": "Useful words from your reports land in “My vocabulary” by themselves. Learn them with spaced repetition, tests and a daily reminder.",
    "ft.5t": "Share your result",
    "ft.5d": "One tap gives you a smart card with your band for Instagram or Telegram.",
    "ft.6t": "24/7, in Uzbek, Russian and English",
    "ft.6d": "Works at night and on the eve of the exam. The bot speaks Uzbek, Russian and English.",
    "ft.rcnote": "On 4 criteria · sample",
    "ft.rcfoot": "Try it yourself: @bandzilla_bot",

    "pr.cap": "Pricing",
    "pr.title": "Start for <mark class=\"hl\">free</mark>",
    "pr.sub": "8 coins as a gift when you first open the bot. After that each check is paid in coins — no subscription, and coins never expire.",
    "pr.ctitle": "Price of one check",
    "pr.cnote": "1 coin = 1,000 UZS. Coins are charged only when the report is ready.",
    "pr.full": "Full test (Part 1+2+3)",
    "pr.1n": "Gift",
    "pr.1d": "To try the bot",
    "pr.1p": "0 UZS",
    "pr.1f1": "For example, two Writing Task 1, one Task 2 or one Speaking part",
    "pr.1f2": "Writing and Speaking",
    "pr.1f3": "The full PDF report",
    "pr.1f4": "Practice, vocabulary and progress",
    "pr.1b": "Start free",
    "pr.2n": "Coin packs",
    "pr.2d": "Take as many as you need — bigger packs are cheaper",
    "pr.percoin": "UZS / coin",
    "pr.2cap": "Coin pack prices",
    "u.som": "UZS",
    "pr.2foot": "Coins never expire · pay by card",
    "pr.2b": "Buy in the bot",
    "pr.badge": "For teachers and centers",
    "pr.3n": "Center packs",
    "pr.3d": "You pay for your students’ checks",
    "pr.3p": "<span>from</span><b>720</b><span>UZS / coin</span>",
    "pr.3cap": "Center pack prices",
    "pr.3f1": "Groups, assignments and mock tests",
    "pr.3f2": "Coin gifts to students, a daily limit, a weekly report",
    "pr.3b": "Buy in the bot",
    "pr.ref": "<b>Invite a friend.</b> When they buy coins for the first time, you both get 5 coins. Your link is in “👤 My account” in the bot.",
    "pr.pay": "Payment by card transfer: you transfer the money, send the bot a photo of the receipt, and once it’s confirmed the coins land on your balance.",

    "te.cap": "For teachers and learning centers",
    "te.title": "Your whole group — in one bot",
    "te.text": "Send a request as a teacher — once it’s approved, you can create a group. Students join with an invite link, and for each one you see the number of works, the average band and their latest activity.",
    "te.1": "Joining with an invite link",
    "te.2": "Every student’s statistics and reports",
    "te.3": "Assignments and mock tests — with a reminder before the deadline",
    "te.4": "Gift coins to students or pay for their checks yourself",
    "te.5": "A weekly report every Monday",
    "te.cta": "Create a group",
    "te.dgroup": "🏫 IELTS evening group",
    "te.dsample": "sample",
    "te.dstudent": "Student",
    "te.dworks": "Works",
    "te.davg": "Average",

    "faq.cap": "FAQ",
    "faq.title": "Frequently asked questions",
    "faq.more": "Didn’t find your answer? Write to the bot — we’ll help.",
    "faq.cta": "Write to the bot",
    "faq.1q": "How far can I trust Bandzilla’s band?",
    "faq.1a": "Bandzilla scores by the official IELTS Band Descriptors that examiners use: the same 4 criteria, the same 0–9 scale. Each criterion is scored three times independently and the middle score counts — so the result is stable. Most importantly, every band comes with a reason: you see which sentence and which word pulled it down. Only a test centre issues an official IELTS certificate.",
    "faq.2q": "Can I send a handwritten essay?",
    "faq.2a": "Yes. Take a photo and send it — several pages as an album. Bandzilla reads the text and shows it to you before checking: if it misread a word, just correct it.",
    "faq.3q": "How long does the result take?",
    "faq.3a": "Usually 2–4 minutes. Bandzilla lets you know it’s working and sends the PDF report as soon as it’s ready.",
    "faq.4q": "How do I do Speaking?",
    "faq.4a": "Choose a Part or the full test, send the questions (or take ready ones from Practice) and answer each one with a voice message. Video messages and audio files work too.",
    "faq.5q": "How many coins does one check cost?",
    "faq.5a": "Writing Task 1 — 4 coins, Task 2 — 5 coins. Speaking Part 1 — 6, Part 2 — 5, Part 3 — 8, the full test (Part 1+2+3 in one report) — 16 coins. Coins are charged only when the report is ready.",
    "faq.6q": "What if my gift coins run out?",
    "faq.6a": "Buy a coin pack: 10 coins — 10,000 UZS, 25 — 23,000, 50 — 45,000, 100 — 88,000, 200 — 170,000 UZS. Coins never expire. Payment by card transfer: you transfer the money, send the bot a photo of the receipt, and once it’s confirmed the coins land on your balance.",
    "faq.7q": "Are my reports saved?",
    "faq.7a": "Yes. In “👤 My account” → “History” you can download any report again, and “Statistics” shows how your band is growing.",
    "faq.8q": "What language is the bot in?",
    "faq.8a": "The interface is in Uzbek, Russian and English — you choose when you first open the bot and can change it later in “My account”. Essays and answers are in English, of course; so is the PDF report.",

    "fin.title": "Check your next essay <span class=\"ink-hl\">today</span>",
    "fin.sub": "Your first checks are on the gift coins — free. Open the bot and start in a minute.",
    "fin.cta": "Open the bot — free",
    "fin.qr": "On a computer? Scan with your phone camera",

    "foot.legal": "IELTS is a registered trademark of the British Council, IDP: IELTS Australia and Cambridge University Press &amp; Assessment. Bandzilla is an independent service and is not affiliated with them. Only a test centre issues an official IELTS result."
  };

  var META = {
    uz: {
      title: "Bandzilla — IELTS Writing va Speaking tekshiruvi Telegram’da",
      desc: "Essay yoki ovozli javobingizni @bandzilla_bot’ga yuboring: 2–4 daqiqada 4 mezon bo‘yicha ball, har bir xato izohi, model javob va Band 7+ so‘zlar bilan PDF hisobot. Botga birinchi kirganda 8 coin sovg‘a."
    },
    ru: {
      title: "Bandzilla — проверка IELTS Writing и Speaking в Telegram",
      desc: "Отправьте эссе или голосовой ответ в @bandzilla_bot: за 2–4 минуты — PDF-отчёт с баллом по 4 критериям, разбором каждой ошибки, модельным ответом и словами Band 7+. 8 coin в подарок при первом входе."
    },
    en: {
      title: "Bandzilla — IELTS Writing and Speaking checks in Telegram",
      desc: "Send your essay or voice answer to @bandzilla_bot: in 2–4 minutes you get a PDF report with your band on the 4 criteria, every mistake explained, a model answer and Band 7+ words. 8 coins as a gift when you first open the bot."
    }
  };

  // The sample report's notes. They stay in English, like the real PDF.
  var NOTES = {
    c1: { kind: "good", tag: "Lexical", text: "Precise topic vocabulary, used naturally — exactly what Lexical Resource rewards." },
    c2: { kind: "good", tag: "Task", text: "A clear roadmap: the examiner knows where the essay is going." },
    c3: { kind: "good", tag: "Lexical", text: "A strong, natural chain of collocations for this topic." },
    c4: { kind: "pen", tag: "Grammar", from: "the rapid pace of technological change mean", to: "the rapid pace of technological change means", text: "The subject is “the pace” (singular), so the verb needs -s." },
    c5: { kind: "pen", tag: "Grammar", from: "which the older people especially find", to: "which older people especially find", text: "No “the” when you mean older people in general." },
    c6: { kind: "good", tag: "Coherence", text: "A natural linker between paragraphs — a plus for Coherence & Cohesion." },
    c7: { kind: "pen", tag: "Lexical", from: "can now be completed in second", to: "can now be completed in seconds", text: "“In seconds” is a fixed phrase meaning very quickly; it is always plural." }
  };
  var HAND = { good: { uz: "Zo‘r!", ru: "Отлично!", en: "Nice!" }, pen: { uz: "Gotcha!", ru: "Gotcha!", en: "Gotcha!" } };
  var DICTS = { ru: RU, en: EN };

  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var lang = "uz";
  var activeMark = null;
  var langHooks = [];  // run after every language switch (texts change width)

  // Remember the Uzbek originals so switching back needs no second dictionary.
  var textEls = $$("[data-i18n]");
  textEls.forEach(function (el) { el.setAttribute("data-uz", el.innerHTML); });
  var attrEls = $$("[data-i18n-attr]");
  attrEls.forEach(function (el) {
    el.getAttribute("data-i18n-attr").split(";").forEach(function (pair) {
      var attr = pair.split(":")[0];
      el.setAttribute("data-uz-" + attr, el.getAttribute(attr) || "");
    });
  });

  function storeGet() {
    try { return localStorage.getItem("bz-lang") || localStorage.getItem("ws-lang"); } catch (e) { return null; }
  }
  function storeSet(v) {
    try { localStorage.setItem("bz-lang", v); } catch (e) { /* private mode, blocked storage */ }
  }

  function applyLang(next) {
    lang = DICTS[next] ? next : "uz";
    var dict = DICTS[lang] || {};
    document.documentElement.lang = lang;
    textEls.forEach(function (el) {
      var key = el.getAttribute("data-i18n");
      el.innerHTML = dict[key] !== undefined ? dict[key] : el.getAttribute("data-uz");
    });
    attrEls.forEach(function (el) {
      el.getAttribute("data-i18n-attr").split(";").forEach(function (pair) {
        var parts = pair.split(":"), attr = parts[0], key = parts[1];
        el.setAttribute(attr, dict[key] !== undefined ? dict[key] : el.getAttribute("data-uz-" + attr));
      });
    });
    document.title = META[lang].title;
    var d = document.querySelector('meta[name="description"]');
    if (d) d.setAttribute("content", META[lang].desc);
    langOpts.forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-lang") === lang));
    });
    if (langCur) langCur.textContent = lang.toUpperCase();
    if (activeMark) selectMark(activeMark);
    langHooks.forEach(function (fn) { fn(); });
  }

  // --- Language menu: the globe chip opens a card with the three languages ---
  var langEl = document.getElementById("lang");
  var langBtn = document.getElementById("langBtn");
  var langCur = document.getElementById("langCur");
  var langOpts = $$(".lang-opt");
  function openLang(focusCurrent) {
    langEl.classList.add("open");
    langBtn.setAttribute("aria-expanded", "true");
    if (focusCurrent) (langEl.querySelector('.lang-opt[aria-pressed="true"]') || langOpts[0]).focus();
  }
  function closeLang() {
    langEl.classList.remove("open");
    langBtn.setAttribute("aria-expanded", "false");
  }
  langBtn.addEventListener("click", function () {
    if (langEl.classList.contains("open")) closeLang(); else openLang(false);
  });
  langBtn.addEventListener("keydown", function (e) {
    if (e.key === "ArrowDown") { e.preventDefault(); openLang(true); }
  });
  langOpts.forEach(function (b, i) {
    b.addEventListener("click", function () {
      var v = b.getAttribute("data-lang");
      var changed = v !== lang;
      storeSet(v);
      applyLang(v);
      closeLang();
      langBtn.focus();
      if (changed && !reduceMotion) {
        langCur.classList.remove("bump");
        void langCur.offsetWidth;  // restart the animation
        langCur.classList.add("bump");
      }
    });
    b.addEventListener("keydown", function (e) {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      e.preventDefault();
      langOpts[(i + (e.key === "ArrowDown" ? 1 : langOpts.length - 1)) % langOpts.length].focus();
    });
  });
  document.addEventListener("click", function (e) { if (!langEl.contains(e.target)) closeLang(); });
  langEl.addEventListener("focusout", function (e) {
    if (e.relatedTarget && !langEl.contains(e.relatedTarget)) closeLang();
  });

  // --- Sample report: click (or hover) a marked phrase to read its note ---
  var marks = $$("#essay mark");
  var note = document.getElementById("note");
  var noteHand = document.getElementById("noteHand");
  var noteQuote = document.getElementById("noteQuote");
  var noteFix = document.getElementById("noteFix");
  var noteText = document.getElementById("noteText");

  function el(tag, text) { var e = document.createElement(tag); e.textContent = text; return e; }

  function selectMark(m) {
    if (!note) return;
    activeMark = m;
    marks.forEach(function (x) { x.classList.toggle("active", x === m); });
    var n = NOTES[m.getAttribute("data-c")];
    if (!n) return;
    note.setAttribute("data-kind", n.kind);
    noteHand.textContent = HAND[n.kind][lang];
    var quote = m.textContent.replace(/\d+$/, "").trim();
    noteQuote.textContent = "“" + quote + "”";
    noteFix.textContent = "";
    if (n.from) {
      noteFix.appendChild(el("s", n.from));
      noteFix.appendChild(document.createTextNode(" → "));
      noteFix.appendChild(el("ins", n.to));
    }
    noteText.textContent = n.text + " ";
    noteText.appendChild(el("span", n.tag)).className = "note-tag";
  }

  var finePointer = window.matchMedia && window.matchMedia("(hover: hover) and (pointer: fine)").matches;
  marks.forEach(function (m) {
    m.addEventListener("click", function () { selectMark(m); });
    m.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectMark(m); }
    });
    if (finePointer) m.addEventListener("mouseenter", function () { selectMark(m); });
  });

  // --- Sample report pages: Writing | Speaking tabs (without JS both show) ---
  var tabs = $$(".pages-tabs [role=tab]");
  function openTab(which) {
    tabs.forEach(function (t) {
      var on = t.id === "tab-" + which;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
    });
  }
  if (tabs.length) {
    openTab("w");
    tabs.forEach(function (t) {
      t.addEventListener("click", function () { openTab(t.id.slice(4)); });
      t.addEventListener("keydown", function (e) {
        if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
          var next = t.id === "tab-w" ? "s" : "w";
          openTab(next);
          document.getElementById("tab-" + next).focus();
        }
      });
    });
    $$("[data-tab]").forEach(function (a) {
      a.addEventListener("click", function () { openTab(a.getAttribute("data-tab")); });
    });
  }

  // --- Hero chat: essay in -> "chewing" -> Gotcha! sticker, the mistakes in
  // the essay get eaten (struck out, fixed) -> the PDF with the bot's caption ---
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var chat = document.getElementById("heroChat");
  var typing = document.getElementById("heroTyping");
  var timers = [];
  function step(name) { return chat.querySelector('[data-step="' + name + '"]'); }
  function at(ms, fn) { timers.push(setTimeout(fn, ms)); }
  function show(name) { step(name).classList.add("show"); }
  function hide(name) { step(name).classList.remove("show"); }

  function playChat() {
    timers.forEach(clearTimeout);
    timers = [];
    $$(".msg", chat).forEach(function (m) { m.classList.remove("show"); });
    step("essay").classList.remove("marked");
    typing.classList.remove("show");
    at(400, function () { show("essay"); });
    at(1300, function () { typing.classList.add("show"); });
    at(2300, function () { typing.classList.remove("show"); show("wait"); });
    at(3300, function () { typing.classList.add("show"); });
    at(5000, function () { typing.classList.remove("show"); hide("wait"); show("sticker"); });
    at(5700, function () { step("essay").classList.add("marked"); });
    at(8800, function () { show("pdf"); });
    at(17500, playChat);
  }

  if (chat && typing) {
    if (reduceMotion) {
      // no motion: the marked essay and the report (both fit the screen)
      ["essay", "pdf"].forEach(show);
      step("essay").classList.add("marked");
    } else if ("IntersectionObserver" in window) {
      var playing = false;
      new IntersectionObserver(function (entries) {
        entries.forEach(function (e) {
          if (e.isIntersecting && !playing) { playing = true; playChat(); }
          else if (!e.isIntersecting && playing) { playing = false; timers.forEach(clearTimeout); }
        });
      }).observe(chat);
    } else {
      playChat();
    }
  }

  // --- Voice-message waveforms (deterministic: same bars on every load) ---
  $$("[data-wave]").forEach(function (w) {
    var seed = parseInt(w.getAttribute("data-wave"), 10);
    for (var i = 0; i < 24; i++) {
      var bar = document.createElement("i");
      bar.style.height = 5 + Math.round(16 * Math.abs(Math.sin(seed * 0.37 + i * 0.83) * Math.cos(i * 0.29))) + "px";
      w.appendChild(bar);
    }
  });

  // --- Scroll reveals ---
  var revealEls = $$(".reveal");
  if ("IntersectionObserver" in window && !reduceMotion) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
      });
    }, { rootMargin: "0px 0px -40px 0px", threshold: 0 });
    revealEls.forEach(function (r) { io.observe(r); });
  } else {
    revealEls.forEach(function (r) { r.classList.add("in"); });
  }

  // --- Header: the teeth fill with Lime as the page is read ---
  var header = document.getElementById("header");
  var ticking = false;
  function paintProgress() {
    ticking = false;
    var max = document.documentElement.scrollHeight - window.innerHeight;
    var p = max > 0 ? Math.min(1, Math.max(0, window.scrollY / max)) : 0;
    header.style.setProperty("--p", p.toFixed(4));
  }
  function onScroll() {
    if (!ticking) { ticking = true; window.requestAnimationFrame(paintProgress); }
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onScroll);
  paintProgress();

  // --- Header: the section on screen gets the sliding Lime pill (desktop) / Lime text (menu) ---
  var nav = document.getElementById("nav");
  var navPill = document.getElementById("navPill");
  var sectionLinks = $$('.nav a[href^="#"], .mnav-list a[href^="#"]');
  var current = "";
  function placePill() {
    var a = nav.querySelector("a.on");
    if (!a || !nav.offsetWidth) { navPill.classList.remove("on"); return; }
    navPill.style.setProperty("--x", a.offsetLeft + "px");
    navPill.style.setProperty("--w", a.offsetWidth + "px");
    navPill.classList.add("on");
  }
  function setCurrent(id) {
    if (id === current) return;
    current = id;
    sectionLinks.forEach(function (a) {
      var on = a.getAttribute("href") === "#" + id;
      a.classList.toggle("on", on);
      if (on) a.setAttribute("aria-current", "true"); else a.removeAttribute("aria-current");
    });
    placePill();
  }
  if ("IntersectionObserver" in window) {
    var spy = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) setCurrent(e.target.id); });
    }, { rootMargin: "-45% 0px -54% 0px" });
    $$("main section[id]").forEach(function (s) { spy.observe(s); });
  }
  window.addEventListener("resize", placePill);
  langHooks.push(placePill);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(placePill);

  // --- Mobile menu ---
  var root = document.documentElement;
  var burger = document.getElementById("burger");
  var mnav = document.getElementById("mnav");
  function setMenu(open) {
    root.classList.toggle("menu-open", open);
    burger.setAttribute("aria-expanded", String(open));
    if (open) closeLang();
  }
  burger.addEventListener("click", function () { setMenu(!root.classList.contains("menu-open")); });
  $$("a", mnav).forEach(function (a) { a.addEventListener("click", function () { setMenu(false); }); });
  var wide = window.matchMedia("(min-width: 1080px)");
  var onWide = function (m) { if (m.matches) setMenu(false); };
  if (wide.addEventListener) wide.addEventListener("change", onWide); else if (wide.addListener) wide.addListener(onWide);
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    if (langEl.classList.contains("open")) { closeLang(); langBtn.focus(); }
    else if (root.classList.contains("menu-open")) { setMenu(false); burger.focus(); }
  });

  // --- Mobile sticky CTA (hidden over the hero CTA and the final CTA) ---
  var sticky = document.getElementById("stickyCta");
  var heroCta = document.getElementById("heroCta");
  var finalSec = document.getElementById("final");
  var heroVisible = true, finalVisible = false;
  function updateSticky() { sticky.classList.toggle("on", !heroVisible && !finalVisible); }
  if ("IntersectionObserver" in window && sticky && heroCta && finalSec) {
    new IntersectionObserver(function (es) { heroVisible = es[0].isIntersecting; updateSticky(); }).observe(heroCta);
    new IntersectionObserver(function (es) { finalVisible = es[0].isIntersecting; updateSticky(); }).observe(finalSec);
  }

  var year = document.getElementById("year");
  if (year) year.textContent = String(new Date().getFullYear());

  // --- Language: ?lang=uz|ru|en (e.g. from ads), else the saved choice, else the browser's
  // (Russian browsers get RU, everyone else UZ; English only when chosen) ---
  var fromUrl = "";
  try { fromUrl = (new URLSearchParams(location.search).get("lang") || "").toLowerCase(); } catch (e) { /* old browser */ }
  var saved = fromUrl === "uz" || DICTS[fromUrl] ? fromUrl : storeGet();
  var browserRu = (navigator.language || "").toLowerCase().indexOf("ru") === 0;
  var startMark = document.querySelector('#essay mark[data-c="c7"]');
  if (startMark) activeMark = startMark;
  applyLang(saved || (browserRu ? "ru" : "uz"));
})();
