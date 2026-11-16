#!/usr/bin/env bash
# WriteSpeak daily backup: PostgreSQL dump + uploaded files + .env.
# Scheduled by /etc/cron.d/writespeak-backup (repo copy: deploy/backup/writespeak-backup.cron),
# log: /var/log/writespeak-backup.log. Restore steps: deploy/backup/RESTORE.md.
#
# Each run writes /opt/writespeak_backups/daily/<YYYYmmdd-HHMM>/ with:
#   db.dump        pg_dump custom format (compressed; restore with pg_restore)
#   storage.tar.gz data/storage + data/books + data/sample_library + data/calibration + data/crm.json
#                  (uploads, PDF reports, practice images, sample books, the model-answer library,
#                  the teachers' Speaking anchors, the admins' group settings)
#   redis.rdb      Redis snapshot: the OpenAI spend ledger (crm:spend:*), the balance counters,
#                  the test window, the admins' group queue (restore: RESTORE.md)
#   ledger.txt     the spend ledger as plain text, day by day (readable without Redis)
#   env            copy of /opt/writespeak/.env (bot token, API keys)
#   SHA256SUMS     checksums of the files above
# The directory is built as <name>.partial and renamed only once complete and
# verified, so a half-written backup is never picked up (e.g. by the PC pull).
# Every dump is test-restored into a throwaway Postgres container before it
# counts as good. Sunday runs are also kept in weekly/, 1st-of-month runs in
# monthly/ (hardlinks, no extra space). Retention is by count: 14 / 8 / 12.
# Any failure sends a Telegram message to ADMIN_TELEGRAM_IDS, and so does a disk over DISK_ALERT % full.
# The result is also written to data/backup_status.json (2026-11-16): the watchdog alerts on a failed, missing
# or hung night even when the message above did not get through, and the daily report shows it.
# Then a copy goes off the server, encrypted, to the admins' Telegram group (deploy/backup/offsite.sh).
#
# 2026-11-15: the test restore waited for the throwaway Postgres on its unix socket, which answers during
# the image's first-start init and then goes away for the restart - createdb hit that gap on ~1 night in 4
# ("No such file or directory" / "the database system is starting up"). It now waits on TCP, which only the
# final server listens on, and retries. Its anonymous data volume is removed with it (`rm -v`): 1045 of them
# had piled up (~29 GB).
#
# Usage: backup.sh            normal run
#        backup.sh --test-alert   only send a test Telegram alert
set -Eeuo pipefail

APP=/opt/writespeak
DEST=/opt/writespeak_backups
DB_CONTAINER=writespeak-db-1
REDIS_CONTAINER=writespeak-redis-1
PG_IMAGE=pgvector/pgvector:pg16
DISK_ALERT=85
KEEP_UPLOADS_DAYS=30
KEEP_DAILY=14
KEEP_WEEKLY=8
KEEP_MONTHLY=12
TS=$(date +%Y%m%d-%H%M)
WORK="$DEST/daily/$TS.partial"
TEST_CONTAINER="writespeak-restore-test-$$"

umask 077

env_value() { # reads KEY=value from .env without sourcing it (values may contain spaces)
  grep -m1 "^$1=" "$APP/.env" | cut -d= -f2- | tr -d '\r'
}

alert() {
  local token ids id
  token=$(env_value TELEGRAM_BOT_TOKEN || true)
  ids=$(env_value ADMIN_TELEGRAM_IDS || true)
  [ -n "$token" ] && [ -n "$ids" ] || return 0
  for id in ${ids//,/ }; do
    curl -s -m 20 -o /dev/null "https://api.telegram.org/bot${token}/sendMessage" \
      --data-urlencode "chat_id=${id}" --data-urlencode "text=$1" || true
  done
}

cleanup() {
  # -v: the image's anonymous data volume goes too (without it one was left behind every night)
  docker rm -f -v "$TEST_CONTAINER" >/dev/null 2>&1 || true
}

# The night's result for the watchdog and the daily report (2026-11-16): data/backup_status.json.
write_status() { # ok error [db files restored]
  local f="$APP/data/backup_status.json"
  jq -n --arg at "$(date -Iseconds)" --argjson ok "$1" --arg error "$2" --arg ts "$TS" --arg db "${3:-}"     --arg files "${4:-}" --arg restored "${5:-}"     '{at: $at, ok: $ok, ts: $ts, db: $db, files: $files, restored: $restored, error: $error}' > "$f.tmp" 2>/dev/null     && mv "$f.tmp" "$f" && chmod 644 "$f" || true
}

on_error() {
  local line=$1
  echo "$(date '+%F %T') FAILED at line $line"
  rm -rf "$WORK"
  cleanup
  write_status false "qator $line" || true
  alert "⚠️ WriteSpeak: kunlik zaxira MUVAFFAQIYATSIZ ($(hostname), $TS, qator $line). Log: /var/log/writespeak-backup.log"
}

if [ "${1:-}" = "--test-alert" ]; then
  alert "✅ WriteSpeak zaxira tizimi: test xabari. Zaxira xato bersa, shu yerga ogohlantirish keladi."
  echo "test alert sent"
  exit 0
fi

# One run at a time.
exec 9>/var/lock/writespeak-backup.lock
flock -n 9 || { echo "$(date '+%F %T') another backup is running, skipping"; exit 0; }

trap 'on_error $LINENO' ERR
trap cleanup EXIT

mkdir -p "$DEST/daily" "$DEST/weekly" "$DEST/monthly"
chmod 700 "$DEST"
rm -rf "$DEST"/daily/*.partial
mkdir -p "$WORK"

# 1. Database (custom format: compressed, restorable table by table).
docker exec "$DB_CONTAINER" sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -Z 6' > "$WORK/db.dump"

# 2. Test-restore it into a throwaway container and compare row counts with the live DB.
docker run -d --rm --name "$TEST_CONTAINER" -e POSTGRES_PASSWORD=restore-test "$PG_IMAGE" >/dev/null
# Ready = answering on TCP: the image's init server listens on the unix socket only, then restarts.
ready=""
for _ in $(seq 1 120); do
  if docker exec "$TEST_CONTAINER" pg_isready -h 127.0.0.1 -U postgres -q 2>/dev/null; then ready=1; break; fi
  sleep 1
done
[ -n "$ready" ] || { echo "test Postgres never became ready"; false; }
created=""
for _ in 1 2 3 4 5; do
  if docker exec "$TEST_CONTAINER" createdb -h 127.0.0.1 -U postgres restored 2>/dev/null; then created=1; break; fi
  sleep 3
done
[ -n "$created" ] || docker exec "$TEST_CONTAINER" createdb -h 127.0.0.1 -U postgres restored  # the real error, logged
docker exec -i "$TEST_CONTAINER" pg_restore -h 127.0.0.1 -U postgres -d restored --no-owner --no-privileges --exit-on-error < "$WORK/db.dump"
COUNT_SQL="select (select count(*) from users) || ' users, ' || (select count(*) from writing_submissions) || ' writing, ' || (select count(*) from speaking_submissions) || ' speaking, rev ' || (select version_num from alembic_version)"
restored=$(docker exec "$TEST_CONTAINER" psql -h 127.0.0.1 -U postgres -d restored -tAc "$COUNT_SQL")
live=$(docker exec "$DB_CONTAINER" sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -tAc \"$COUNT_SQL\"")
if [ "$restored" != "$live" ]; then
  echo "restore check mismatch: restored='$restored' live='$live'"
  false
fi
cleanup

# 3. Redis: a consistent snapshot (redis-cli --rdb asks the server for one) and the ledger as text.
docker exec "$REDIS_CONTAINER" sh -c 'redis-cli --rdb /tmp/backup.rdb >/dev/null 2>&1 && cat /tmp/backup.rdb; rc=$?; rm -f /tmp/backup.rdb; exit $rc' \
  > "$WORK/redis.rdb"
[ -s "$WORK/redis.rdb" ] || { echo "Redis snapshot is empty"; false; }
docker exec "$REDIS_CONTAINER" sh -c \
  'for k in $(redis-cli --scan --pattern "crm:spend:*" | sort); do echo "[$k]"; redis-cli hgetall "$k"; done' \
  > "$WORK/ledger.txt"

# 4. Files and config (data/models is left out: the importer downloads its model again by itself).
dirs="storage books"
for extra in sample_library calibration crm.json; do
  if [ -e "$APP/data/$extra" ]; then dirs="$dirs $extra"; fi
done
tar -C "$APP/data" -czf "$WORK/storage.tar.gz" $dirs
cp "$APP/.env" "$WORK/env"
(cd "$WORK" && sha256sum db.dump storage.tar.gz redis.rdb ledger.txt env > SHA256SUMS)

FINAL="$DEST/daily/$TS"
mv "$WORK" "$FINAL"

# 5. Weekly / monthly copies (hardlinks).
if [ "$(date +%u)" = 7 ]; then cp -al "$FINAL" "$DEST/weekly/$TS"; fi
if [ "$(date +%d)" = 01 ]; then cp -al "$FINAL" "$DEST/monthly/$TS"; fi

# 6. Retention by count (a missed day never deletes more than intended).
prune() {
  local dir=$1 keep=$2
  find "$dir" -mindepth 1 -maxdepth 1 -type d -name '20*' ! -name '*.partial' -printf '%f\n' \
    | sort | head -n -"$keep" | while read -r old; do rm -rf "${dir:?}/$old"; done
}
prune "$DEST/daily" "$KEEP_DAILY"
prune "$DEST/weekly" "$KEEP_WEEKLY"
prune "$DEST/monthly" "$KEEP_MONTHLY"

echo "$(date '+%F %T') OK $TS db=$(du -h "$FINAL/db.dump" | cut -f1) files=$(du -h "$FINAL/storage.tar.gz" | cut -f1) redis=$(du -h "$FINAL/redis.rdb" | cut -f1) restore-check: $restored"
write_status true "" "$(du -h "$FINAL/db.dump" | cut -f1)" "$(du -h "$FINAL/storage.tar.gz" | cut -f1)" "$restored"

# 7. Retention (the user, 2026-11-15: "1 month"): the students' own uploads - their photos, voices,
#    recordings, documents - go after KEEP_UPLOADS_DAYS, once tonight's backup holds them. The PDF reports
#    (reports/), the Practice and assignment pictures (practice/) and the chart keys (chart_keys/) stay.
#    A failure here never fails the backup.
removed=$(find "$APP/data/storage" -mindepth 3 -path "*/uploads/*" -type f -mtime +"$KEEP_UPLOADS_DAYS" -print -delete \
  2>/dev/null | wc -l) || removed="?"
echo "$(date '+%F %T') retention: $removed upload(s) older than $KEEP_UPLOADS_DAYS days removed"

# 8. The disk: a full one stops Postgres and Redis, and the bot with them.
used=$(df --output=pcent / | tail -1 | tr -dc '0-9')
if [ -n "$used" ] && [ "$used" -ge "$DISK_ALERT" ]; then
  echo "$(date '+%F %T') disk ${used}% full"
  alert "⚠️ Bandzilla serveri: disk ${used}% to'lgan ($(hostname)). Bo'sh joy tugasa baza va bot to'xtaydi — eski fayllar/zaxiralarni tozalash kerak."
fi

# 9. Off the server (the user, 2026-11-15): the backup, encrypted, to the admins' group's "💾 Zaxira" topic -
#    a dead or wiped server no longer takes its backups with it. offsite.sh messages the admins itself when it
#    fails; the backup above stays good either way.
"$APP/deploy/backup/offsite.sh" "$FINAL" "$restored" || true
