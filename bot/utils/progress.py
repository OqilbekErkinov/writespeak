"""The waiting message, alive (the user, 2026-11-14: "an animation like other bots have - but ours, on brand,
creative and beautiful - so students don't get bored while they wait").

While a check is graded its "analysing…" message becomes a small live card, edited every TICK seconds:

    🦖 Bandzilla tekshiryapti…
    🟩🟩🟩🟩🟩🟨⬜⬜⬜⬜  47%
    ✅ Javobingiz o'qildi
    ⏳ Xatolarni ovlayapman 🦷
    ▫️ Ballar · ▫️ Namuna javob · ▫️ PDF hisobot
    💡 an IELTS tip, a new one every few ticks
    🧠 a one-tap vocabulary question, with its own score

The stages are real: each OpenAI call the check records (services/ai/usage.listen) moves the card to its
stage; between calls the bar creeps on towards the stage's end, never to 100% before the report. Nothing here
can break a check - every edit's failure is swallowed, and the card stops itself after MAX_SECONDS.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import random
import time
from dataclasses import dataclass, field

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from services.ai import usage

logger = logging.getLogger(__name__)

ENABLED = True
TICK = 4.0  # seconds between edits - well under Telegram's limits for one chat
FIRST = 1.5  # the first frame, soon after the plain "analysing" text
MAX_SECONDS = 20 * 60
QUIZ_AFTER = 12.0  # seconds before the first question - the card first shows what it is doing
QUIZ_NEXT = 2  # ticks a question's result stays before the next question
CREEP = 35.0  # seconds: how fast the bar approaches its stage's end between calls
CELLS = 10
CB_PREFIX = "pq:"


@dataclass
class Stage:
    key: str
    purposes: frozenset[str]
    end: float  # the bar's share when this stage is done
    doing: dict[str, str]  # per language
    done: dict[str, str]


def _st(key, purposes, end, doing_uz, done_uz, doing_ru, done_ru, doing_en, done_en) -> Stage:
    return Stage(key, frozenset(purposes), end, {"uz": doing_uz, "ru": doing_ru, "en": doing_en},
                 {"uz": done_uz, "ru": done_ru, "en": done_en})


_MISTAKES = ("Xatolarni ovlayapman 🦷", "Xatolar topildi", "Охочусь за ошибками 🦷", "Ошибки найдены",
             "Hunting for mistakes 🦷", "Mistakes found")
_VERIFY = ("Har bir xatoni qayta tekshiryapman", "Xatolar tekshirildi", "Перепроверяю каждую ошибку",
           "Ошибки проверены", "Double-checking every mistake", "Mistakes checked")
_SCORE = ("Band ballarini hisoblayapman", "Ballar tayyor", "Считаю баллы", "Баллы готовы",
          "Working out your band scores", "Band scores ready")
_PDF = ("PDF hisobotni yig'yapman", "Hisobot tayyor", "Собираю PDF-отчёт", "Отчёт готов",
        "Putting your PDF report together", "Report ready")

STAGES = {
    "writing": [
        _st("read", {"ocr", "chart", "chart_settle", "chart_check", "embedding", "style_profile"}, 0.18,
            "Javobingizni o'qiyapman", "Javobingiz o'qildi", "Читаю ваш ответ", "Ответ прочитан",
            "Reading your answer", "Answer read"),
        _st("mistakes", {"feedback", "missed_errors"}, 0.45, *_MISTAKES),
        _st("verify", {"verify", "verify_second"}, 0.62, *_VERIFY),
        _st("score", {"score"}, 0.76, *_SCORE),
        _st("sample", {"model_answer", "answer_check", "self_check"}, 0.93,
            "Siz uchun namuna javob yozyapman", "Namuna javob tayyor", "Пишу для вас образец ответа",
            "Образец готов", "Writing a model answer for you", "Model answer ready"),
        _st("pdf", {"vocab_uz", "label", "vocab_translate", "vocab_describe"}, 1.0, *_PDF),
    ],
    "speaking": [
        _st("listen", {"transcribe", "transcribe_check", "fillers", "split recording"}, 0.25,
            "Yozuvingizni tinglayapman 🎧", "Yozuv tinglandi", "Слушаю вашу запись 🎧", "Запись прослушана",
            "Listening to your recording 🎧", "Recording heard"),
        _st("mistakes", {"practise_words", "feedback", "missed_errors"}, 0.52, *_MISTAKES),
        _st("verify", {"verify", "verify_second"}, 0.66, *_VERIFY),
        _st("score", {"score"}, 0.78, *_SCORE),
        _st("sample", {"model_answer", "answer_check", "self_check"}, 0.94,
            "Namuna javoblar yozyapman", "Namuna javoblar tayyor", "Пишу образцы ответов", "Образцы готовы",
            "Writing model answers", "Model answers ready"),
        _st("pdf", {"vocab_uz", "vocab_translate", "vocab_describe"}, 1.0, *_PDF),
    ],
}

TITLE = {"uz": "Bandzilla tekshiryapti", "ru": "Bandzilla проверяет", "en": "Bandzilla is checking"}
USUALLY = {
    "writing": {"uz": "odatda 2–4 daqiqa", "ru": "обычно 2–4 минуты", "en": "usually 2–4 minutes"},
    "writing_task1": {"uz": "odatda 3–6 daqiqa", "ru": "обычно 3–6 минут", "en": "usually 3–6 minutes"},
    "speaking": {"uz": "odatda 2–5 daqiqa", "ru": "обычно 2–5 минут", "en": "usually 2–5 minutes"},
}
TIP_HEAD = {"uz": "Bilasizmi?", "ru": "Знаете ли вы?", "en": "Did you know?"}
QUIZ_HEAD = {"uz": "Kutguncha mini-savol", "ru": "Мини-вопрос, пока ждёте", "en": "A quick one while you wait"}
QUIZ_ASK = {"uz": "«{word}» ga eng yaqin so'z qaysi?", "ru": "Какое слово ближе всего к «{word}»?",
            "en": "Which word is closest to “{word}”?"}
QUIZ_RIGHT = {"uz": "To'g'ri!", "ru": "Верно!", "en": "Right!"}
QUIZ_WRONG = {"uz": "Deyarli!", "ru": "Почти!", "en": "Almost!"}
QUIZ_SCORE = {"uz": "to'g'ri", "ru": "верно", "en": "right"}

TIPS = {
    "writing": {
        "uz": ["Task 2 kirishida savolni o'z so'zlaringiz bilan qayta yozing va pozitsiyangizni darhol ayting.",
               "Har bir body paragraf bitta asosiy g'oyaga bag'ishlanadi: avval g'oya, keyin izoh va misol.",
               "Task 1 da overview — eng muhim gap. Unda raqam emas, umumiy tendensiya bo'lsin.",
               "Bir xil so'zni qayta-qayta ishlatmang: 'increase' o'rniga 'rise', 'grow', 'climb'.",
               "Task 2 da kamida 250 so'z yozing, lekin so'z soni emas, g'oyaning rivoji ball beradi.",
               "'Moreover', 'Furthermore' ni har gapda ishlatish CC ballini oshirmaydi — tabiiy bog'lang.",
               "Xulosada yangi g'oya qo'shmang: body'da isbotlagan pozitsiyangizni qisqa takrorlang.",
               "Task 1 da diagrammadagi raqamlarni aniq yozing: 'about 40%' — taxminiy, '40%' — aniq.",
               "Murakkab gaplar yaxshi, lekin xatosiz oddiy gap xatoli murakkab gapdan yaxshiroq.",
               "Imloni tekshiring: bitta so'zni ikki xil yozish (colour / color) — tanlang va birini qoldiring."],
        "ru": ["Во вступлении Task 2 перефразируйте вопрос и сразу назовите свою позицию.",
               "Один абзац — одна главная мысль: сначала идея, потом пояснение и пример.",
               "В Task 1 главное предложение — overview: в нём общая тенденция, а не цифры.",
               "Не повторяйте одно слово: вместо 'increase' — 'rise', 'grow', 'climb'.",
               "Пишите в Task 2 не меньше 250 слов, но баллы даёт развитие идей, а не их количество.",
               "'Moreover' в каждом предложении не повышает CC — связывайте мысли естественно.",
               "В заключении не добавляйте новых идей: коротко повторите доказанную позицию.",
               "Цифры из графика пишите точно: 'about 40%' — примерно, '40%' — точно.",
               "Сложные предложения — хорошо, но простое без ошибки лучше сложного с ошибкой.",
               "Выберите одно написание (colour / color) и держитесь его во всём тексте."],
        "en": ["In a Task 2 introduction, paraphrase the question and state your position straight away.",
               "One body paragraph, one main idea: the idea first, then the explanation and an example.",
               "In Task 1 the overview is the key sentence: the big trend, not the numbers.",
               "Vary your words: instead of 'increase' every time, try 'rise', 'grow', 'climb'.",
               "Write at least 250 words in Task 2 - but developed ideas earn the band, not the count.",
               "'Moreover' in every sentence won't lift Coherence - link your ideas naturally.",
               "No new ideas in the conclusion: restate the position your body paragraphs proved.",
               "Quote the chart's figures accurately: 'about 40%' is an estimate, '40%' is exact.",
               "Complex sentences are good - but an accurate simple one beats a wrong complex one.",
               "Pick one spelling (colour or color) and keep to it all the way through."],
    },
    "speaking": {
        "uz": ["Part 1 da javobni bir gap bilan tugatmang: sabab yoki misol qo'shib, 2–4 gap gapiring.",
               "Part 2 da 1 daqiqalik tayyorgarlikda faqat kalit so'zlarni yozing, to'liq gaplarni emas.",
               "Part 3 da fikringizni sabab, misol yoki solishtirish bilan rivojlantiring.",
               "'Um', 'uh' tabiiy — ularning o'zi ball tushirmaydi. Gap o'rtasida uzoq to'xtash tushiradi.",
               "Bilmagan so'zingizni boshqacha tushuntiring (paraphrase) — bu LR uchun plyus.",
               "Intonatsiya: muhim so'zni ovozingiz bilan ajrating, monoton gapirmang.",
               "O'zingizni tuzatsangiz ('he go… he goes') — bu xato emas, yaxshi belgi.",
               "Idiomalarni faqat o'rniga tushganda ishlating: tabiiy bo'lmasa, ball bermaydi.",
               "Part 2 da 2 daqiqa gapirishga harakat qiling — vaqt qolsa, misol yoki his-tuyg'u qo'shing.",
               "Tez gapirish shart emas: aniq va ravon gapirish muhimroq."],
        "ru": ["В Part 1 не заканчивайте одной фразой: добавьте причину или пример, 2–4 предложения.",
               "За минуту подготовки в Part 2 пишите только ключевые слова, а не предложения.",
               "В Part 3 развивайте мысль: причина, пример или сравнение.",
               "'Um', 'uh' естественны и сами по себе баллы не снижают. Снижают долгие паузы посреди фразы.",
               "Не знаете слово — объясните иначе (paraphrase): это плюс к LR.",
               "Интонация: выделяйте голосом важные слова, не говорите монотонно.",
               "Исправили себя ('he go… he goes') — это не ошибка, а хороший знак.",
               "Идиомы — только к месту: неестественная идиома баллов не даёт.",
               "В Part 2 старайтесь говорить 2 минуты: осталось время — добавьте пример или чувства.",
               "Говорить быстро не обязательно: важнее чётко и плавно."],
        "en": ["Don't stop a Part 1 answer after one sentence: add a reason or an example, 2-4 sentences.",
               "In the Part 2 minute, note key words only - not full sentences.",
               "In Part 3, develop each view with a reason, an example or a comparison.",
               "'Um' and 'uh' are natural and cost nothing on their own; long pauses mid-sentence do.",
               "Don't know a word? Explain it another way - paraphrase is a plus for Lexical Resource.",
               "Intonation: stress the important words, don't speak on one note.",
               "Correcting yourself ('he go... he goes') is no mistake - it's a good sign.",
               "Use idioms only where they fit: a forced idiom earns nothing.",
               "Aim to speak for the full 2 minutes in Part 2 - add an example or a feeling if time is left.",
               "You don't need to speak fast: clear and smooth matters more."],
    },
}

# word, its closest meaning, two that are not - IELTS vocabulary, the same in every language
QUIZ = [
    ("significant", "important", ("tiny", "quiet")), ("decline", "decrease", ("arrive", "decorate")),
    ("crucial", "essential", ("optional", "cruel")), ("abundant", "plentiful", ("scarce", "absent")),
    ("obsolete", "outdated", ("obvious", "modern")), ("enhance", "improve", ("weaken", "enclose")),
    ("diminish", "reduce", ("expand", "dine")), ("adequate", "sufficient", ("excessive", "adjacent")),
    ("prominent", "noticeable", ("hidden", "promising")), ("deteriorate", "worsen", ("recover", "decorate")),
    ("feasible", "possible", ("pleasant", "impossible")), ("reluctant", "unwilling", ("eager", "relevant")),
    ("substantial", "considerable", ("slight", "substitute")), ("vital", "essential", ("visible", "minor")),
    ("fluctuate", "vary", ("flow", "settle")), ("acquire", "obtain", ("require", "lose")),
    ("mitigate", "ease", ("worsen", "migrate")), ("profound", "deep", ("shallow", "proud")),
    ("scarce", "rare", ("common", "scared")), ("advocate", "support", ("oppose", "advise")),
    ("comprehensive", "complete", ("partial", "comprehensible")), ("detrimental", "harmful", ("helpful", "determined")),
    ("ubiquitous", "everywhere", ("unique", "rare")), ("tranquil", "calm", ("noisy", "transparent")),
    ("meticulous", "careful", ("careless", "meteoric")), ("plummet", "fall sharply", ("rise slowly", "stay level")),
    ("surge", "sharp rise", ("slow fall", "pause")), ("inevitable", "unavoidable", ("unlikely", "invisible")),
    ("versatile", "adaptable", ("rigid", "verbal")), ("prosperous", "wealthy", ("poor", "prospective")),
    ("hinder", "obstruct", ("help", "hint")), ("consecutive", "successive", ("random", "conservative")),
    ("predominantly", "mainly", ("rarely", "previously")), ("approximately", "roughly", ("exactly", "approvingly")),
    ("contemporary", "modern", ("ancient", "temporary")), ("diverse", "varied", ("uniform", "divided")),
    ("frugal", "thrifty", ("wasteful", "fragile")), ("candid", "frank", ("secretive", "candied")),
    ("lucrative", "profitable", ("unpaid", "lucid")), ("negligible", "tiny", ("huge", "negligent")),
]


def _choices(index: int) -> list[str]:
    word, right, wrong = QUIZ[index]
    options = [right, *wrong]
    random.Random(index * 7919).shuffle(options)
    return options


@dataclass
class Waiting:
    msg: Message
    lang: str
    kind: str  # "writing", "writing_task1" or "speaking"
    started: float = field(default_factory=time.monotonic)
    stage: int = 0
    stage_at: float = field(default_factory=time.monotonic)
    shown: float = 0.0
    frame: int = 0
    quiz: list[int] = field(default_factory=list)  # the questions asked, in order
    answered: dict[int, str] = field(default_factory=dict)  # question -> the option tapped
    right: int = 0
    result_ticks: int = 0
    task: asyncio.Task | None = None
    poke: asyncio.Event = field(default_factory=asyncio.Event)
    token: object = None

    @property
    def stages(self) -> list[Stage]:
        return STAGES["speaking" if self.kind == "speaking" else "writing"]

    def on_call(self, purpose: str) -> None:
        """A call the check recorded: its stage, if later than the one shown, becomes the current one."""
        for i, s in enumerate(self.stages):
            if purpose in s.purposes and i > self.stage:
                self.stage, self.stage_at = i, time.monotonic()
                break

    def share(self) -> float:
        start = self.stages[self.stage - 1].end if self.stage else 0.0
        end = self.stages[self.stage].end
        t = time.monotonic() - self.stage_at
        value = start + (end - start) * (1 - math.exp(-t / CREEP)) * 0.92
        self.shown = min(0.97, max(self.shown, value))  # never back, never 100% before the report
        return self.shown

    # --- the card ------------------------------------------------------------------------------------

    def text(self) -> str:
        lang = self.lang if self.lang in TITLE else "uz"
        p = self.share()
        filled = min(CELLS - 1, int(p * CELLS))
        pulse = "🟨" if self.frame % 2 == 0 else "🟧"
        bar = "🟩" * filled + pulse + "⬜" * (CELLS - filled - 1)
        dino = "🦖" if self.frame % 2 == 0 else "🦕"
        dots = "." * (self.frame % 3 + 1)
        lines = [f"{dino} <b>{TITLE[lang]}</b>{dots}", "", f"{bar}  <b>{round(p * 100)}%</b>", ""]
        for i, s in enumerate(self.stages):
            if i < self.stage:
                lines.append(f"✅ {s.done[lang]}")
            elif i == self.stage:
                lines.append(f"{'⏳' if self.frame % 2 == 0 else '⌛'} <b>{s.doing[lang]}…</b>")
            else:
                lines.append(f"▫️ <i>{s.doing[lang]}</i>")
        elapsed = int(time.monotonic() - self.started)
        lines += ["", f"⏱ {elapsed // 60}:{elapsed % 60:02d} · {USUALLY[self.kind][lang]}"]
        quiz = self._quiz_lines(lang)
        if quiz:
            lines += ["", *quiz]
        else:
            tips = TIPS["speaking" if self.kind == "speaking" else "writing"][lang]
            seed = int(hashlib.md5(str(self.msg.message_id).encode()).hexdigest()[:6], 16)
            tip = tips[(seed + self.frame // 3) % len(tips)]
            lines += ["", f"💡 <b>{TIP_HEAD[lang]}</b> <i>{tip}</i>"]
        return "\n".join(lines)

    def _quiz_lines(self, lang: str) -> list[str]:
        if not self.quiz:
            return []
        q = self.quiz[-1]
        word, right, _ = QUIZ[q]
        head = f"🧠 <b>{QUIZ_HEAD[lang]}</b>"
        if self.answered:
            head += f" · {self.right}/{len(self.answered)} {QUIZ_SCORE[lang]}"
        lines = [head, QUIZ_ASK[lang].format(word=word)]
        if q in self.answered:
            ok = self.answered[q] == right
            lines.append(f"{'✅' if ok else '💡'} {QUIZ_RIGHT[lang] if ok else QUIZ_WRONG[lang]} "
                         f"<b>{word}</b> ≈ <b>{right}</b>")
        return lines

    def keyboard(self) -> InlineKeyboardMarkup | None:
        if not self.quiz or self.quiz[-1] in self.answered:
            return None
        q = self.quiz[-1]
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=o, callback_data=f"{CB_PREFIX}{q}:{i}") for i, o in enumerate(_choices(q))]])

    def _next_question(self) -> None:
        asked = set(self.quiz)
        left = [i for i in range(len(QUIZ)) if i not in asked]
        if left:
            seed = int(hashlib.md5(f"{self.msg.chat.id}:{len(self.quiz)}".encode()).hexdigest()[:6], 16)
            self.quiz.append(left[seed % len(left)])

    def answer(self, question: int, option: int) -> bool | None:
        """A tap on an option: True right, False wrong, None when it is no longer the open question."""
        if not self.quiz or self.quiz[-1] != question or question in self.answered:
            return None
        chosen = _choices(question)[option]
        self.answered[question] = chosen
        ok = chosen == QUIZ[question][1]
        self.right += ok
        self.result_ticks = 0
        self.poke.set()
        return ok

    # --- the loop --------------------------------------------------------------------------------------

    async def _run(self) -> None:
        delay = FIRST
        while time.monotonic() - self.started < MAX_SECONDS:
            try:
                await asyncio.wait_for(self.poke.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            self.poke.clear()
            delay = TICK
            self.frame += 1
            elapsed = time.monotonic() - self.started
            if not self.quiz and elapsed >= QUIZ_AFTER:
                self._next_question()
            elif self.quiz and self.quiz[-1] in self.answered:
                self.result_ticks += 1
                if self.result_ticks > QUIZ_NEXT:
                    self._next_question()
            try:
                await self.msg.edit_text(self.text(), reply_markup=self.keyboard())
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except TelegramBadRequest as e:
                if "not modified" not in str(e):
                    logger.info("Waiting card stopped: %s", e)
                    return  # the message was deleted or changed by the check itself
            except Exception as e:  # noqa: BLE001
                logger.info("Waiting card edit failed: %r", e)


_active: dict[tuple[int, int], Waiting] = {}


def begin(msg: Message, lang: str, kind: str) -> Waiting | None:
    """The live card on `msg` (its plain "analysing…" text until the first frame), its stages following the
    calls recorded in this context; None when switched off. The same message again: the card it has."""
    if not ENABLED:
        return None
    key = (msg.chat.id, msg.message_id)
    if key in _active:
        return _active[key]
    w = Waiting(msg=msg, lang=lang or "uz", kind=kind)
    w.token = usage.listen(w.on_call)
    w.task = asyncio.create_task(w._run())
    _active[key] = w
    return w


async def end(w: Waiting | None) -> None:
    """Stops the card before the check edits or deletes its message (the report, a failure, a question)."""
    if w is None:
        return
    _active.pop((w.msg.chat.id, w.msg.message_id), None)
    if w.token is not None:
        try:
            usage._listener.reset(w.token)
        except ValueError:  # set in another context: just stop listening here
            usage.listen(None)
        w.token = None
    if w.task is not None and not w.task.done():
        w.task.cancel()
        try:
            await w.task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


def find(chat_id: int, message_id: int) -> Waiting | None:
    return _active.get((chat_id, message_id))
