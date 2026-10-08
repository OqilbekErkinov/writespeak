#!/usr/bin/env bash
# WriteSpeak daily backup: PostgreSQL dump + uploaded files + .env.
# Scheduled by /etc/cron.d/writespeak-backup (repo copy: deploy/backup/writespeak-backup.cron),
# log: /var/log/writespeak-backup.log. Restore steps: deploy/backup/RESTORE.md.
#
# Each run writes /opt/writespeak_backups/daily/<YYYYmmdd-HHMM>/ with:
#   db.dump        pg_dump custom format (compressed; restore with pg_restore)
#   storage.tar.gz data/storage + data/books (uploads, PDF reports, practice images)
#   env            copy of /opt/writespeak/.env (bot token, API keys)
#   SHA256SUMS     checksums of the three files above
# The directory is built as <name>.partial and renamed only once complete and
# verified, so a half-written backup is never picked up (e.g. by the PC pull).
# Every dump is test-restored into a throwaway Postgres container before it
# counts as good. Sunday runs are also kept in weekly/, 1st-of-month runs in
# monthly/ (hardlinks, no extra space). Retention is by count: 14 / 8 / 12.
# Any failure sends a Telegram message to ADMIN_TELEGRAM_IDS.
#
# Usage: backup.sh            normal run
#        backup.sh --test-alert   only send a test Telegram alert
set -Eeuo pipefail

APP=/opt/writespeak
DEST=/opt/writespeak_backups
DB_CONTAINER=writespeak-db-1
PG_IMAGE=pgvector/pgvector:pg16
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
  docker rm -f "$TEST_CONTAINER" >/dev/null 2>&1 || true
}

on_error() {
  local line=$1
  echo "$(date '+%F %T') FAILED at line $line"
  rm -rf "$WORK"
  cleanup
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
for _ in $(seq 1 60); do
  docker exec "$TEST_CONTAINER" pg_isready -U postgres -q 2>/dev/null && break
  sleep 1
done
docker exec "$TEST_CONTAINER" createdb -U postgres restored
docker exec -i "$TEST_CONTAINER" pg_restore -U postgres -d restored --no-owner --no-privileges --exit-on-error < "$WORK/db.dump"
COUNT_SQL="select (select count(*) from users) || ' users, ' || (select count(*) from writing_submissions) || ' writing, ' || (select count(*) from speaking_submissions) || ' speaking, rev ' || (select version_num from alembic_version)"
restored=$(docker exec "$TEST_CONTAINER" psql -U postgres -d restored -tAc "$COUNT_SQL")
live=$(docker exec "$DB_CONTAINER" sh -c "psql -U \"\$POSTGRES_USER\" -d \"\$POSTGRES_DB\" -tAc \"$COUNT_SQL\"")
if [ "$restored" != "$live" ]; then
  echo "restore check mismatch: restored='$restored' live='$live'"
  false
fi
cleanup

# 3. Files and config.
tar -C "$APP/data" -czf "$WORK/storage.tar.gz" storage books
cp "$APP/.env" "$WORK/env"
(cd "$WORK" && sha256sum db.dump storage.tar.gz env > SHA256SUMS)

FINAL="$DEST/daily/$TS"
mv "$WORK" "$FINAL"

# 4. Weekly / monthly copies (hardlinks).
if [ "$(date +%u)" = 7 ]; then cp -al "$FINAL" "$DEST/weekly/$TS"; fi
if [ "$(date +%d)" = 01 ]; then cp -al "$FINAL" "$DEST/monthly/$TS"; fi

# 5. Retention by count (a missed day never deletes more than intended).
prune() {
  local dir=$1 keep=$2
  find "$dir" -mindepth 1 -maxdepth 1 -type d -name '20*' ! -name '*.partial' -printf '%f\n' \
    | sort | head -n -"$keep" | while read -r old; do rm -rf "${dir:?}/$old"; done
}
prune "$DEST/daily" "$KEEP_DAILY"
prune "$DEST/weekly" "$KEEP_WEEKLY"
prune "$DEST/monthly" "$KEEP_MONTHLY"

echo "$(date '+%F %T') OK $TS db=$(du -h "$FINAL/db.dump" | cut -f1) files=$(du -h "$FINAL/storage.tar.gz" | cut -f1) restore-check: $restored"
