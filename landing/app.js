/* Bandzilla landing: language switch (UZ in the HTML, RU below), the sample
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

    "hero.cap": "IELTS Writing и Speaking · Telegram-бот",
    "hero.title": "Съедает ошибки.<br><span class=\"t-lime\">Растит балл.</span>",
    "hero.lead": "Отправьте эссе или голосовой ответ — Bandzilla найдёт каждую ошибку и объяснит, почему это ошибка. Через несколько минут вы получите цветной PDF-отчёт: примерный балл по 4 критериям, модельный ответ и слова уровня Band 7+.",
    "hero.cta": "Проверить бесплатно",
    "hero.cta2": "Пример отчёта",
    "hero.note": "@bandzilla_bot · откроется в Telegram",
    "hero.t1": "8 coin в подарок при регистрации",
    "hero.t2": "Карта не нужна",
    "hero.t3": "Можно фото рукописи",
    "hero.sample": "Пример диалога",

    "chat.status": "бот",
    "chat.wait": "🦷 Разбираю… зубы острые! Это займёт 2–4 минуты.",
    "chat.pdf": "PDF-отчёт · 6 страниц",
    "chat.caption": "<b>Gotcha! Нашёл ошибок: 8 🦖</b><br>Примерный Band: <b>6.5</b> — это не официальный результат IELTS.",
    "chat.again": "🔁 Проверить ещё",
    "chat.share": "📤 Поделиться",
    "chat.input": "Сообщение",

    "pain.cap": "Знакомо?",
    "pain.title": "Самое сложное в подготовке — <span class=\"pen\">увидеть свои ошибки</span>",
    "pain.1t": "Эссе написано. А кто проверит?",
    "pain.1d": "Ждёте преподавателя, стоите в очереди, а результат узнаёте через несколько дней. Всё это время практика стоит на месте.",
    "pain.2t": "Не с кем практиковать Speaking",
    "pain.2d": "Дома говорите сами с собой — но кто оценит беглость, произношение и грамматику?",
    "pain.3t": "Балл есть, причины нет",
    "pain.3d": "Одной цифры мало. Не зная, какие предложения и слова снижают балл, вы повторите те же ошибки снова.",
    "pain.bridge": "<b>Bandzilla — IELTS-друг у вас в кармане.</b> 24/7, прямо в Telegram, за несколько минут. Смеётся над ошибкой — не над вами.",
    "pain.cta": "Попробовать",

    "rep.cap": "Пример отчёта",
    "rep.title": "Не просто балл — разбор <mark class=\"hl\">каждой ошибки</mark>",
    "rep.sub": "После каждой проверки приходит цветной PDF-отчёт. Вот его небольшой пример — нажмите на выделенные слова, чтобы прочитать комментарий 👇",
    "rep.band": "Примерный Band",
    "rep.target": "Следующая цель · 7.0",
    "tone.good": "Сильная фраза",
    "tone.pen": "Gotcha: ошибка",
    "rep.notelang": "Отчёт — на английском, как и сам экзамен.",
    "rep.vocab": "Слова по этой теме",
    "rep.inside": "Что внутри отчёта",
    "rep.i1": "<b>Примерный Band и 4 критерия</b> — отдельный балл и короткий комментарий по каждому",
    "rep.i2": "<b>Пометки прямо в вашем тексте</b> — ошибки и сильные фразы видны сразу",
    "rep.i3": "<b>Разбор каждой ошибки</b> — почему это ошибка и как её исправить",
    "rep.i4": "<b>3 главных приоритета</b> — сначала то, что съедает больше всего баллов",
    "rep.i5": "<b>Модельный ответ, написанный для вас</b> — на ваш вопрос, примерно на один band выше",
    "rep.i6": "<b>10–12 слов и выражений уровня Band 7+</b> — по теме, со значением и примером",
    "rep.i7": "<b>Прогресс и чек-лист</b> — сравнение с прошлыми результатами и напоминания на следующий раз",
    "rep.disc": "Балл — примерная оценка по IELTS Band Descriptors, а не официальный результат IELTS.",
    "rep.cta": "Проверить своё эссе",
    "rep.pages": "Страницы настоящего отчёта",
    "rep.p1": "Обложка: примерный балл, 4 критерия и 3 приоритета",
    "rep.p2": "Каждая ошибка — с исправлением и объяснением",
    "rep.p3": "Модельный ответ, написанный для вас",
    "rep.p1alt": "Обложка PDF-отчёта Bandzilla: примерный Band 6.5, баллы по 4 критериям и 3 приоритета",
    "rep.p2alt": "Страница отчёта: каждая ошибка, её исправление и объяснение",
    "rep.p3alt": "Страница отчёта: модельный ответ на тот же вопрос, на один band выше",

    "sp.title": "Как на <mark class=\"hl\">настоящем экзамене</mark> — вопрос за вопросом",
    "sp.sub": "Bandzilla задаёт вопросы по одному, вы отвечаете голосовыми сообщениями. В конце все ответы оцениваются вместе — как одна проверка.",
    "sp.1t": "Part 1, 2, 3 или полный тест",
    "sp.1d": "Отправьте свой вопрос или возьмите готовый из раздела Practice. В полном тесте все три части — в одном отчёте.",
    "sp.2t": "Cue card для Part 2",
    "sp.2d": "1 минута на подготовку, затем 1–2 минуты речи — как на настоящем экзамене.",
    "sp.3t": "Голос измеряется",
    "sp.3d": "Произношение, темп, длинные паузы и слова-паразиты анализируются по самой записи, а не по тексту.",
    "sp.4t": "Балл по 4 критериям",
    "sp.4d": "Fluency &amp; Coherence, Lexical Resource, Grammatical Range &amp; Accuracy и Pronunciation — по каждому отдельно.",
    "sp.q1": "❓ <b>Вопрос 1/4</b>",
    "sp.q2": "❓ <b>Вопрос 2/4</b>",
    "sp.q3": "❓ <b>Вопрос 3/4</b>",
    "sp.finish": "✅ Завершить и оценить",

    "how.cap": "Как это работает",
    "how.title": "3 шага — и отчёт у вас",
    "how.1t": "Откройте бота",
    "how.1d": "Откройте @bandzilla_bot в Telegram и нажмите «Start». Выберите язык — и всё, никаких анкет.",
    "how.2t": "Отправьте вопрос и ответ",
    "how.2d": "Выберите Writing или Speaking. Отправьте вопрос и ответ в любом виде — текстом, фото, файлом или голосом.",
    "how.3t": "Получите отчёт",
    "how.3d": "Через несколько минут придёт PDF с баллом, комментариями, модельным ответом и полезными словами. Результатом можно поделиться с друзьями.",
    "how.formats": "Отправляйте в любом формате:",
    "fmt.text": "Текст",
    "fmt.photo": "Фото",
    "fmt.hand": "Рукопись",
    "fmt.voice": "Голосовое",
    "fmt.video": "Видеосообщение",

    "ft.cap": "Возможности",
    "ft.title": "Вся подготовка — <span class=\"t-lime\">в одном боте</span>",
    "ft.1t": "Раздел Practice",
    "ft.1d": "Реальные вопросы Writing и Speaking с экзаменов, база регулярно пополняется. Выполненные отмечаются ✅, а кнопка «Следующий вопрос» не даёт остановиться.",
    "ft.2t": "Графики Task 1",
    "ft.2d": "Отправьте фото графика или диаграммы — Bandzilla сверит цифры в вашем ответе с данными на картинке.",
    "ft.3t": "Прогресс на виду",
    "ft.3d": "В «My Works» — архив всех ваших отчётов и график изменения балла.",
    "ft.4t": "Личный словарь",
    "ft.4d": "Полезные слова из отчётов сами попадают в «Мой словарь» — с переводом. Учите их интервальным повторением, тестами и с ежедневным напоминанием.",
    "ft.5t": "Делитесь результатом",
    "ft.5d": "В один клик — красивая карточка с вашим баллом для Instagram или Telegram.",
    "ft.6t": "24/7, на узбекском и русском",
    "ft.6d": "Работает и ночью, и накануне экзамена. Интерфейс на узбекском и русском.",
    "ft.rcnote": "Примерная оценка · пример",
    "ft.rcfoot": "Попробуйте и вы: @bandzilla_bot",

    "pr.cap": "Цены",
    "pr.title": "Начать — <mark class=\"hl\">бесплатно</mark>",
    "pr.sub": "При регистрации — 8 coin в подарок. Дальше каждая проверка оплачивается в coin по своей цене — без подписки, coin бессрочные.",
    "pr.ctitle": "Цена одной проверки",
    "pr.cnote": "Coin списываются, только когда отчёт готов.",
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
    "pr.3f2": "Дневной лимит, подарки coin, отчёты",
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
    "faq.1q": "Это официальный балл IELTS?",
    "faq.1a": "Нет. Это примерная оценка, которую выставляет искусственный интеллект по критериям IELTS Band Descriptors. Она показывает, где вы сейчас и что улучшить, но не заменяет официальный результат экзамена и не гарантирует никакой балл. Bandzilla не связан с British Council, IDP или Cambridge.",
    "faq.2q": "Можно отправить эссе, написанное от руки?",
    "faq.2a": "Да. Сфотографируйте и отправьте — если страниц несколько, альбомом. Bandzilla распознает текст и покажет его перед проверкой: если какое-то слово прочитано неверно, просто исправьте.",
    "faq.3q": "Сколько ждать результат?",
    "faq.3a": "Обычно несколько минут. Bandzilla сообщает, что идёт анализ, и сразу присылает PDF-отчёт, когда он готов.",
    "faq.4q": "Как сдавать Speaking?",
    "faq.4a": "Выберите Part или полный тест, отправьте вопросы (или возьмите готовые из Practice) и отвечайте на каждый голосовым сообщением. Видеосообщения и аудиофайлы тоже принимаются.",
    "faq.5q": "Сколько coin стоит одна проверка?",
    "faq.5a": "Writing Task 1 — 4 coin, Task 2 — 5 coin. Speaking Part 1 — 6, Part 2 — 5, Part 3 — 8, полный тест (Part 1+2+3 в одном отчёте) — 16 coin. Coin списываются, только когда отчёт готов.",
    "faq.6q": "Что, если подарочные coin закончились?",
    "faq.6a": "Купите пакет coin: 10 coin — 10 000 сум, 25 — 23 000, 50 — 45 000, 100 — 88 000, 200 — 170 000 сум. Coin бессрочные. Оплата переводом на карту: переводите, отправляете боту фото чека, после подтверждения coin зачисляются на счёт.",
    "faq.7q": "Мои отчёты сохраняются?",
    "faq.7a": "Да. В разделе «My Works» можно заново скачать все отчёты и посмотреть на графике, как меняется ваш балл.",
    "faq.8q": "На каком языке бот?",
    "faq.8a": "Интерфейс на узбекском и русском — выбираете при первом входе, потом можно сменить в «Профиле». Эссе и ответы, конечно, на английском; PDF-отчёт тоже на английском.",

    "fin.title": "Проверьте следующее эссе <span class=\"ink-hl\">сегодня</span>",
    "fin.sub": "Первые проверки — бесплатно, за подарочные coin. Откройте бота и начните за минуту.",
    "fin.cta": "Открыть бота — бесплатно",
    "fin.qr": "Вы за компьютером? Отсканируйте камерой телефона",

    "foot.legal": "IELTS — зарегистрированный товарный знак British Council, IDP: IELTS Australia и Cambridge University Press &amp; Assessment. Bandzilla не связан с ними и не выставляет официальных оценок IELTS. Балл в отчёте — примерная оценка; никакой результат не гарантируется."
  };

  var META = {
    uz: {
      title: "Bandzilla — IELTS Writing va Speaking tekshiruvi Telegram’da",
      desc: "Essay yoki ovozli javobingizni @bandzilla_bot’ga yuboring: bir necha daqiqada 4 mezon bo‘yicha taxminiy ball, har bir xato izohi, model javob va Band 7+ so‘zlar bilan PDF hisobot. Ro‘yxatdan o‘tganda 8 coin sovg‘a."
    },
    ru: {
      title: "Bandzilla — проверка IELTS Writing и Speaking в Telegram",
      desc: "Отправьте эссе или голосовой ответ в @bandzilla_bot: через несколько минут — PDF-отчёт с примерным баллом по 4 критериям, разбором каждой ошибки, модельным ответом и словами Band 7+. 8 coin в подарок при регистрации."
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
  var HAND = { good: { uz: "Zo‘r!", ru: "Отлично!" }, pen: { uz: "Gotcha!", ru: "Gotcha!" } };

  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var lang = "uz";
  var activeMark = null;

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
    lang = next === "ru" ? "ru" : "uz";
    document.documentElement.lang = lang;
    textEls.forEach(function (el) {
      var key = el.getAttribute("data-i18n");
      el.innerHTML = lang === "ru" && RU[key] !== undefined ? RU[key] : el.getAttribute("data-uz");
    });
    attrEls.forEach(function (el) {
      el.getAttribute("data-i18n-attr").split(";").forEach(function (pair) {
        var parts = pair.split(":"), attr = parts[0], key = parts[1];
        el.setAttribute(attr, lang === "ru" && RU[key] !== undefined ? RU[key] : el.getAttribute("data-uz-" + attr));
      });
    });
    document.title = META[lang].title;
    var d = document.querySelector('meta[name="description"]');
    if (d) d.setAttribute("content", META[lang].desc);
    $$(".lang button").forEach(function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-lang") === lang));
    });
    if (activeMark) selectMark(activeMark);
  }

  $$(".lang button").forEach(function (b) {
    b.addEventListener("click", function () {
      var v = b.getAttribute("data-lang");
      storeSet(v);
      applyLang(v);
    });
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

  // --- Header line + mobile sticky CTA (hidden over the hero CTA and the final CTA) ---
  var header = document.getElementById("header");
  var sticky = document.getElementById("stickyCta");
  var heroCta = document.getElementById("heroCta");
  var finalSec = document.getElementById("final");
  var heroVisible = true, finalVisible = false;
  function updateSticky() { sticky.classList.toggle("on", !heroVisible && !finalVisible); }
  if ("IntersectionObserver" in window && sticky && heroCta && finalSec) {
    new IntersectionObserver(function (es) { heroVisible = es[0].isIntersecting; updateSticky(); }).observe(heroCta);
    new IntersectionObserver(function (es) { finalVisible = es[0].isIntersecting; updateSticky(); }).observe(finalSec);
  }
  function onScroll() { header.classList.toggle("scrolled", window.scrollY > 8); }
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  var year = document.getElementById("year");
  if (year) year.textContent = String(new Date().getFullYear());

  // --- Language: ?lang=ru|uz (e.g. from ads), else the saved choice, else the browser's ---
  var fromUrl = "";
  try { fromUrl = (new URLSearchParams(location.search).get("lang") || "").toLowerCase(); } catch (e) { /* old browser */ }
  var saved = fromUrl === "ru" || fromUrl === "uz" ? fromUrl : storeGet();
  var browserRu = (navigator.language || "").toLowerCase().indexOf("ru") === 0;
  var startMark = document.querySelector('#essay mark[data-c="c7"]');
  if (startMark) activeMark = startMark;
  applyLang(saved || (browserRu ? "ru" : "uz"));
})();
