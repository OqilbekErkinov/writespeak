# WriteSpeak — zaxiradan tiklash

Zaxiralar har kuni 03:50 (Toshkent) da `deploy/backup/backup.sh` tomonidan olinadi:

| Joy | Nima saqlanadi |
|---|---|
| Server: `/opt/writespeak_backups/daily/` | oxirgi 14 kun |
| Server: `/opt/writespeak_backups/weekly/` | oxirgi 8 yakshanba |
| Server: `/opt/writespeak_backups/monthly/` | oxirgi 12 oyning 1-sanasi |
| Kompyuter: `D:\Backups\WriteSpeak\daily\` | oxirgi 30 kun |
| Kompyuter: `D:\Backups\WriteSpeak\monthly\` | har oyning birinchi nusxasi, doimiy |

Har bir papkada: `db.dump` (baza), `storage.tar.gz` (yuklangan fayllar, PDF
hisobotlar, savol rasmlari), `env` (`.env` nusxasi — bot token va kalitlar),
`SHA256SUMS`. Log: serverda `/var/log/writespeak-backup.log`, kompyuterda
`D:\Backups\WriteSpeak\pull.log`. Zaxira xato bersa, adminlarga Telegram
xabari keladi.

## 1. Bazani shu serverda tiklash

```bash
cd /opt/writespeak
B=/opt/writespeak_backups/daily/20261009-0050      # kerakli sana
(cd $B && sha256sum -c SHA256SUMS)                  # fayllar butunmi

docker compose stop bot admin_web                   # yozuvlarni to'xtatish
docker exec writespeak-db-1 sh -c 'dropdb -U "$POSTGRES_USER" --force "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
docker exec -i writespeak-db-1 sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --exit-on-error' < $B/db.dump
docker compose start bot admin_web
```

Bitta jadvalni tiklash (masalan, tasodifan o'chirilgan savollar): avval
`pg_restore -l $B/db.dump` bilan ro'yxatni ko'ring, keyin `pg_restore -t <jadval>`
bilan alohida bazaga tiklab, kerakli qatorlarni ko'chiring.

## 2. Fayllarni tiklash

```bash
tar -xzf $B/storage.tar.gz -C /opt/writespeak/data   # data/storage va data/books
```

## 3. `.env` ni tiklash

```bash
cp $B/env /opt/writespeak/.env && chmod 600 /opt/writespeak/.env
```

## 4. Yangi serverga noldan ko'chirish

1. Docker, nginx, certbot o'rnating; `git clone https://github.com/OqilbekErkinov/writespeak /opt/writespeak`.
2. Zaxira papkasini serverga yuklang (kompyuterdan: `scp -r D:\Backups\WriteSpeak\daily\<sana> root@<yangi-ip>:/root/restore`).
3. `cp /root/restore/env /opt/writespeak/.env`, `tar -xzf /root/restore/storage.tar.gz -C /opt/writespeak/data`.
4. `docker compose up -d db`, so'ng 1-bo'limdagi `dropdb/createdb/pg_restore` buyruqlari.
5. `docker compose up -d --build`.
6. nginx: `deploy/nginx/writespeak.uz` ni `/etc/nginx/sites-available/` ga qo'ying, sites-enabled'ga link qiling,
   DNS'ni yangi IP'ga o'zgartiring va `certbot --nginx --cert-name writespeak.uz -d writespeak.uz -d www.writespeak.uz -d admin.writespeak.uz`.
7. Kunlik zaxirani qayta yoqing: `cp deploy/backup/writespeak-backup.cron /etc/cron.d/writespeak-backup`.

## Zaxira ishlayotganini tekshirish

```bash
tail -5 /var/log/writespeak-backup.log      # har kuni "OK ... restore-check: N users ..." qatori
ls /opt/writespeak_backups/daily/
```

Har bir zaxira olinganidan keyin vaqtinchalik konteynerga tiklab ko'riladi va
foydalanuvchilar/ishlar soni jonli baza bilan solishtiriladi — `OK` faqat shu
tekshiruv o'tgandagina yoziladi.
