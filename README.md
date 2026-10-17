# Bandzilla

(Avvalgi nomi WriteSpeak - 2026-10-17 dan Bandzilla, bot @bandzilla_bot.)

Ochiq (istalgan foydalanuvchi kirishi mumkin) Telegram bot: Writing (Task 1/2) va Speaking
(Part 1/2/3) ishlarini rasmiy IELTS Band Descriptors asosida baholaydi, rangli tahlil bilan
chiroyli PDF hisobot beradi, o'quv kitoblaridagi bitta muallif uslubida yangi model javob yozadi va
o'quvchi progressini kuzatadi. Har bir foydalanuvchiga ro'yxatdan o'tgach bir martalik 2 ta bepul tekshiruv, undan
ko'pi pullik (karta orqali, admin tasdiqlaydi) — qarang: [Narxlash va to'lov](#narxlash-va-tolov).

To'liq arxitektura va bosqichma-bosqich reja: `PROJECT MASTERPIECE` spec asosida qurilgan (admin
bilan kelishilgan reja faylida saqlangan).

## Texnologik stack

- Python 3.11, aiogram 3 (Telegram bot), PostgreSQL 16 + pgvector (DB + semantik qidiruv), Redis
  (FSM holatini saqlash), OpenAI (GPT-4o — matn/vision/audio baholash, Whisper — transkripsiya,
  text-embedding-3-small — RAG), WeasyPrint + Jinja2 (PDF hisobot), Docker Compose (deploy).

## Loyiha tuzilishi

```
bot/            Telegram handler'lar, FSM state'lar, klaviaturalar, middleware
services/ai/    OCR, hujjat parsing, transkripsiya, baholash (writing/speaking), RAG qidiruv
services/pdf/   Jinja2 shablon + WeasyPrint PDF generator
services/storage/  Fayllarni diskda saqlash
db/             SQLAlchemy modellari, CRUD, Alembic migratsiyalar
services/practice_import/  Practice savollarini Telegram kanallardan import qilish (importer servisi)
data/books/     O'zingizning IELTS namuna kitoblaringiz (PDF) shu yerga qo'yiladi
data/sample_library/  Model answer kutubxonasi: index.json, embeddings.json, styles/ (gitignore)
scripts/        import_practice_questions.py, ingest_books.py, build_sample_index.py
landing/        bandzilla.uz landing sahifasi (statik, nginx beradi; writespeak.uz u yerga yo'naltiradi)
branding/       Logo, Telegram bot avatari va bot tavsif rasmi (BotFather uchun)
deploy/nginx/   Serverdagi nginx sozlamasi nusxasi
```

## O'rnatish (lokal, Docker bilan)

1. `.env.example` faylini `.env` ga nusxalang va to'ldiring:
   - `TELEGRAM_BOT_TOKEN` — @BotFather'dan olinadi
   - `ADMIN_TELEGRAM_IDS` — sizning Telegram ID'ingiz (bir nechta bo'lsa vergul bilan) — to'lov
     so'rovlari shularga keladi
   - `OPENAI_API_KEY` — platform.openai.com
   - `PAYMENT_CARD_NUMBER`, `PAYMENT_CARD_HOLDER` — to'lov uchun ko'rsatiladigan karta
2. Konteynerlarni ishga tushiring:
   ```
   docker compose up -d --build
   ```
   Bu avtomatik ravishda Postgres+pgvector va Redis'ni ko'taradi, migratsiyalarni
   (`alembic upgrade head`) qo'llaydi va botni ishga tushiradi.
3. Practice savollari Telegram kanallardan **avtomatik** olinadi — `importer` servisi har 3
   soatda yangilarini qo'shadi (`services/practice_import/`):
   - **Writing** — [@cdireport](https://t.me/cdireport): imtihondan keyin yuborilgan Task 1 +
     Task 2. AI (gpt-5-mini) postdan savollarni ajratadi, reklama/boshqa postlarni tashlaydi;
     Task 1 grafigidagi kanal nomi OCR (RapidOCR) bilan topilib, LaMa bilan o'chiriladi;
     avval tushgan savol qayta qo'shilmaydi, "yana tushdi" deb belgilanadi. Eng yangilari tepada.
   - **Speaking** — [@IELTS_Speaking_Assistant_App](https://t.me/IELTS_Speaking_Assistant_App):
     kanalning "PART 1 QUESTIONS" / "PART 2&3 QUESTIONS" indeks postlaridagi barcha mavzular
     ko'chiriladi (AI'siz); kanalda o'zgargan mavzu yangilanadi, olib tashlangani o'chadi.

   Birinchi marta (yoki qo'lda) ishga tushirish:
   ```
   docker compose exec importer python scripts/import_practice_questions.py --replace-old
   ```
   `--replace-old` kanaldan kelmagan savollarni o'chiradi; `--only writing|speaking`,
   `--since YYYY-MM-DD`, `--limit N` ham bor. Birinchi Writing importi 2026-04-01 dan beri
   postlarni o'qiydi. Savollarni admin panelning **Savollar** sahifasida
   (`https://admin.bandzilla.uz/questions`) tahrirlash/o'chirish mumkin. Speaking formati: Part
   1/3 da **har bir qator bitta savol**, bot ularni birma-bir so'raydi va oxirida hammasini bitta
   tekshiruv sifatida baholaydi. Part 2 da butun matn bitta cue card bo'ladi.
4. IELTS namuna kitoblaringizni `data/books/*.pdf` ga qo'ying, so'ng:
   ```
   docker compose exec bot python scripts/ingest_books.py extract
   ```
   `data/books/_review/*.json` fayllarini ochib, HAR bir chunk uchun quyidagilarni
   tekshiring/to'g'irlang:
   - **`author`** — kim yozgan (hech qachon GPT tomonidan taxmin qilinmaydi; noto'g'ri
     muallif ko'rsatish namunani ko'rsatmaslikdan battar, shuning uchun `load` bosqichi
     `author` bo'sh chunk'larni ogohlantirish bilan o'tkazib yuboradi). Bitta kitobda bir
     nechta muallif bo'lsa (masalan, bir xil savolga ikki xil o'qituvchi javob yozgan bo'lsa),
     chunker "Written by <Ism>" qatorini chunk chegarasi sifatida avtomatik aniqlaydi — shunda
     ikki muallifning ishi bitta chunk'ga aralashib ketmaydi. Baribir qo'lda tekshiring.
   - `task_type` (`writing_task1` / `writing_task2` / `speaking`) va `band_level` — bo'sh
     qoldirilsa, `load` bosqichida GPT taxmin qiladi (bu — hech qanday hallyutsinatsiya xavfi
     yo'q, oddiy klassifikatsiya, muallif kabi jiddiy emas).

   So'ng Writing hisobotidagi **Model answer** kutubxonasini quring (yangi kitob qo'shilganda yoki
   review fayl o'zgarganda qayta ishga tushiring — o'zgarmagan namunalar uchun API qayta
   chaqirilmaydi):
   ```
   docker compose exec bot python scripts/build_sample_index.py --styles
   ```
   U `data/sample_library/index.json`ga har bir namunani (muallif, kitob, task turi, subtype,
   mavzu, savol, kitobdagi band, so'z soni, matn), `embeddings.json`ga savollarning
   embeddinglarini, `styles/<muallif>.json`ga har bir muallifning Task 1 va Task 2 uslub
   profilini yozadi. Muallifi yoki bandi yo'q namunalar ro'yxat qilib ko'rsatiladi va kiritilmaydi;
   per-insho muallifi yozilmagan kitob (IELTS Writing Recap 2023) skriptdagi `BOOK_DEFAULTS`da
   bitta "jamoa" muallifi, band 8.5 sifatida berilgan. (`ingest_books.py load` Writing uchun endi
   kerak emas — u faqat `book_chunks` jadvalini to'ldiradi.)

   > Tekshirilgan review fayllar (6 kitob, 302 namuna) faqat serverda va kompyuterda saqlanadi
   > (gitignore) — mualliflik huquqi bilan himoyalangan matnlar repoga qo'yilmaydi.
5. Botga Telegram'da `/start` yozing — `.env`dagi ID(lar) avtomatik admin hisoblanadi, qolgan
   har bir kishi darhol to'liq foydalana boshlaydi (tasdiqlash kutilmaydi).

## Qayta ishga tushirish / yangilash

```
docker compose up -d --build
```
(Alembic migratsiyalari har safar avtomatik qo'llanadi — schema o'zgarishlari xavfsiz.)

## Lug'atim (shaxsiy lug'at)

Har bir tekshiruv hisobotidagi foydali so'zlar o'quvchining lug'atiga o'zi qo'shiladi (bir xil so'z
ikki marta saqlanmaydi); o'quvchi o'zi ham so'z qo'shishi mumkin — inglizcha yoki o'zbekcha/ruscha
yozsa, AI ma'nosi, misol va tarjimasini topadi (kuniga 30 tagacha). Bo'lim (`bot/handlers/vocabulary.py`):

- **🔄 Takrorlash** — Leitner usulidagi oraliqli takrorlash: to'g'ri eslangan so'z 1 → 3 → 7 → 14 kundan
  keyin qaytadi, 5 marta ketma-ket eslansa o'zlashtirilgan hisoblanadi; unutilgani boshidan boshlanadi.
- **🎯 Test** — ko'p tanlovli savollar: bo'sh joyni to'ldirish, ma'nosi bo'yicha so'zni topish, tarjima.
- **📋 So'zlarim** — barcha so'zlar sahifalab, holati bilan; o'chirish va qaytadan o'rganish.
- Tarjimalar (o'quvchi tiliga) faqat kerak bo'lganda, arzon modelda bir marta olinadi va saqlanadi.
- **Kunlik eslatma** — `VOCAB_REMINDER_HOUR` da (Toshkent vaqti, standart 20:00, `-1` = o'chiq)
  kamida 5 ta so'zi takrorlashga tushgan o'quvchiga bitta xabar; o'quvchi menyudan o'chira oladi.

## Narxlash va to'lov

Botdagi to'lov birligi — **coin** (`bot/utils/coins.py`, 2026-10-17 dan; avval "1 kredit = 1
tekshiruv = 5 000 so'm" edi). Har bir tekshiruv o'z tannarxiga qarab o'z narxida coindan yechiladi:

| Tekshiruv | Coin |
|---|---|
| Writing Task 1 / Task 2 | 4 / 5 |
| Speaking Part 1 / Part 2 / Part 3 | 6 / 5 / 8 |
| To'liq Speaking (Part 1+2+3, bitta hisobot) | 16 |

- Ro'yxatdan o'tganda **`WELCOME_COINS`** (standart: 8) coin sovg'a.
- Coin faqat hisobot saqlanganda yechiladi (xato bo'lgan tekshiruv bepul), muddati tugamaydi.
- Paketlar: 10 / 25 / 50 / 100 / 200 coin — 10 000 / 23 000 / 45 000 / 88 000 / 170 000 so'm.
  O'qituvchi va markazlarga qo'shimcha: 500 / 1 000 / 2 500 / 5 000 coin — 400 000 / 780 000 /
  1 875 000 / 3 600 000 so'm (talabalar tekshiruvini guruh to'laganda shu balansdan yechiladi).
- Referal: taklif qilingan do'st **birinchi marta coin sotib olganda** ikkalasiga
  **`REFERRAL_COINS`** (standart: 5) coin.
- Cheksiz oylik obuna endi sotilmaydi; faol obunalar muddati tugaguncha ishlaydi.

Sotib olish:

1. Coin yetmaganda (yoki "👤 Hisobim"da) bot paketlarni ko'rsatadi.
2. Foydalanuvchi `PAYMENT_CARD_NUMBER`ga o'tkazma qiladi va chek skrinshotini botga yuboradi.
3. Barcha adminlarga (`ADMIN_TELEGRAM_IDS`) skrinshot + Tasdiqlash/Rad etish tugmalari keladi
   (yoki web paneldagi "To'lovlar" sahifasida tasdiqlanadi).
4. Admin tasdiqlasa, coin foydalanuvchi hisobiga qo'shiladi va u darhol tekshiruv topshira oladi.

Bu — qo'lda tasdiqlanadigan, hech qanday tashqi integratsiya talab qilmaydigan boshlang'ich
usul (`bot/handlers/payments.py`). Payme/Click orqali avtomatik tasdiqlashga o'tish uchun
biznes/YATT sifatida ro'yxatdan o'tish, ular bilan shartnoma va bot uchun webhook qabul
qiladigan ochiq domen+SSL kerak bo'ladi.

## Zaxira nusxa

Har kuni 03:50 (Toshkent) da avtomatik: baza, `data/storage` (fayllar, PDF hisobotlar,
savol rasmlari) va `.env`. Har bir zaxira vaqtinchalik konteynerga tiklab tekshiriladi,
xato bo'lsa adminlarga Telegram xabari keladi. Serverda 14 kunlik + 8 haftalik + 12 oylik
nusxa saqlanadi, kompyuterga esa Task Scheduler har kuni ko'chiradi (`D:\Backups\WriteSpeak`).

- Server skripti va cron: `deploy/backup/backup.sh`, `deploy/backup/writespeak-backup.cron`
- Kompyuterga ko'chirish: `deploy/backup/pull-backups.ps1`
- Tiklash yo'riqnomasi: [`deploy/backup/RESTORE.md`](deploy/backup/RESTORE.md)

Baza porti (5433) faqat serverning o'zida ochiq. Kompyuterdan ulanish uchun SSH-tunnel:
`ssh -L 5433:127.0.0.1:5433 bridgin`, so'ng `localhost:5433`.

## Cheklovlar / ma'lum kamchiliklar

- Ball — AI tomonidan Band Descriptors asosida chiqarilgan **taxminiy** baho, rasmiy IELTS
  natijasi emas (bu hisobotlarda ham ko'rsatilgan).
- Speaking'dagi "Pronunciation" bahosi audio-input model orqali baholanadi, lekin rasmiy imtihon
  darajasidagi fonetik tahlil emas.
- Model answer sifati `data/books/_review` fayllarining to'g'riligiga bog'liq — yangi kitob
  qo'shganda review faylini albatta tekshiring.
- Har bir model answer bitta muallif uslubida yangidan yoziladi (`services/ai/sample_bank.py`):
  savol turi bo'yicha filtr, keyin eng yaqin mavzu, teng bo'lsa namunasi ko'proq muallif. Muallif
  va kitob nomi hisobotda hech qachon ko'rsatilmaydi — tanlov faqat bot logida
  (`Model answer: {...}` qatori).
