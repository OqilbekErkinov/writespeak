# WriteSpeak

Ochiq (istalgan foydalanuvchi kirishi mumkin) Telegram bot: Writing (Task 1/2) va Speaking
(Part 1/2/3) ishlarini rasmiy IELTS Band Descriptors asosida baholaydi, rangli tahlil bilan
chiroyli PDF hisobot beradi, o'quv kitoblaridan haqiqiy namuna topadi va o'quvchi progressini
kuzatadi. Har bir foydalanuvchiga kuniga 2 ta bepul tekshiruv, undan ko'pi pullik (karta orqali,
admin tasdiqlaydi) — qarang: [Narxlash va to'lov](#narxlash-va-tolov).

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
data/practice_questions/  Practice bo'limi uchun 50+50 yozma va Speaking savollar banki
data/books/     O'zingizning IELTS namuna kitoblaringiz (PDF) shu yerga qo'yiladi
scripts/        seed_practice_questions.py, ingest_books.py
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
3. Practice savollarini yuklang (bir martalik):
   ```
   docker compose exec bot python scripts/seed_practice_questions.py
   ```
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

   So'ng:
   ```
   docker compose exec bot python scripts/ingest_books.py load
   ```

   > **Eslatma:** Ushbu loyihaga allaqachon 2 ta kitob uchun tayyor review JSON qo'shilgan —
   > `data/books/_review/Task 2 Essay Collection.HardQuestionsAnswered.json` (Christina
   > Khafizova va Dilshodbek Ravshanov, har bir mavzuga ikkala muallifdan alohida-alohida
   > namuna, 20 ta chunk) va `data/books/_review/Task 2 Essays by Jurabek Sanokulov.json`
   > (30 ta chunk). Ularni ko'rib chiqib to'g'ridan-to'g'ri `load` buyrug'ini ishga
   > tushirishingiz mumkin — original PDF fayllarni `data/books/`ga qo'yish shart emas, chunki
   > matn allaqachon qo'lda tekshirilib chiqarilgan.
5. Botga Telegram'da `/start` yozing — `.env`dagi ID(lar) avtomatik admin hisoblanadi, qolgan
   har bir kishi darhol to'liq foydalana boshlaydi (tasdiqlash kutilmaydi).

## Qayta ishga tushirish / yangilash

```
docker compose up -d --build
```
(Alembic migratsiyalari har safar avtomatik qo'llanadi — schema o'zgarishlari xavfsiz.)

## Narxlash va to'lov

Har bir foydalanuvchiga so'nggi 24 soatda **`FREE_DAILY_LIMIT`** ta (standart: 2) bepul tekshiruv
beriladi — Writing va Speaking bitta umumiy hisobga kiradi (OpenAI xarajatini nazorat qilish
uchun). Shundan ko'pi uchun har bir tekshiruv **`PRICE_PER_CHECK`** so'm (standart: 5000) turadi:

1. Limit tugaganda bot foydalanuvchiga miqdor tanlash tugmalarini (1/3/5/10 ta) ko'rsatadi.
2. Foydalanuvchi `PAYMENT_CARD_NUMBER`ga o'tkazma qiladi va chek skrinshotini botga yuboradi.
3. Barcha adminlarga (`ADMIN_TELEGRAM_IDS`) skrinshot + Tasdiqlash/Rad etish tugmalari keladi.
4. Admin tasdiqlasa, kredit foydalanuvchi hisobiga qo'shiladi (muddatsiz — ishlatilmaguncha
   yo'qolmaydi) va u darhol yana tekshiruv topshira oladi.

Bu — qo'lda tasdiqlanadigan, hech qanday tashqi integratsiya talab qilmaydigan boshlang'ich
usul (`bot/handlers/payments.py`). Payme/Click orqali avtomatik tasdiqlashga o'tish uchun
biznes/YATT sifatida ro'yxatdan o'tish, ular bilan shartnoma va bot uchun webhook qabul
qiladigan ochiq domen+SSL kerak bo'ladi.

## Zaxira nusxa (production uchun tavsiya)

- `docker compose exec db pg_dump -U ielts ielts_bot > backup.sql` — kunlik cron orqali.
- `data/storage/` (foydalanuvchi fayllari va PDF hisobotlar) papkasini alohida rsync/backup qiling.

## Cheklovlar / ma'lum kamchiliklar

- Ball — AI tomonidan Band Descriptors asosida chiqarilgan **taxminiy** baho, rasmiy IELTS
  natijasi emas (bu hisobotlarda ham ko'rsatilgan).
- Speaking'dagi "Pronunciation" bahosi audio-input model orqali baholanadi, lekin rasmiy imtihon
  darajasidagi fonetik tahlil emas.
- Kitoblardan namuna qidirish sifat jihatdan `ingest_books.py`dagi chunking va sizning qo'lda
  ko'rib chiqishingizga bog'liq — yangi kitob qo'shganda review faylini albatta tekshiring.
- Har bir `book_chunks` yozuvi aynan bitta muallifning bitta ishiga tegishli (hech qachon ikki
  muallif matni bitta chunk'ga aralashtirilmaydi); RAG qidiruv shu mavzu bo'yicha eng mos
  keladigan bitta namunani tanlaydi va PDF hisobotda muallif ismi aniq ko'rsatiladi (`services
  /pdf/templates/feedback_report.html`dagi Sample bo'limi).
