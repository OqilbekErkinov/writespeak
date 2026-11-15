#!/usr/bin/env bash
# Bandzilla: tonight's backup, encrypted, to the admins' Telegram group (topic "💾 Zaxira") - a copy off the
# server, so a dead or wiped server never takes the backups with it (the user, 2026-11-15). backup.sh runs it
# after a backup passed its restore check; it can also be run by hand:
#
#   offsite.sh /opt/writespeak_backups/daily/<TS> [--full] ["restore-check line"]
#
# What goes:
#   every night  bandzilla-<TS>-kunlik.tar.gpg: the database (db.dump), Redis (redis.rdb), the spend ledger,
#                .env, the small data (books, sample_library, calibration, crm.json) and the files of
#                data/storage changed in the last 26 hours (new uploads and reports)
#   Sundays,     bandzilla-<TS>-fayllar.tar.gpg: all of storage.tar.gz (every upload, report, practice image)
#   or --full,
#   or never sent yet
# A restore = the newest kunlik + the newest fayllar + the kunlik files after it (deploy/backup/RESTORE.md).
#
# Encrypted with gpg (AES-256) and BACKUP_PASSPHRASE from .env; the owner sees the passphrase in the admin
# panel (Sozlamalar -> Zaxira) and keeps it somewhere safe - without it the copies cannot be opened. Each
# archive is decrypted again and compared before it is sent. Bots may send files up to 50 MB, so bigger ones
# go in 45 MB parts. The result is written to data/offsite_status.json (the panel shows it); a failure
# messages the admins, but never touches the backup on the server.
set -uo pipefail

# The OFFSITE_* overrides are for tests/offsite_test.sh only.
APP=${OFFSITE_APP:-/opt/writespeak}
TG_API=${OFFSITE_TG_API:-https://api.telegram.org}
PART_MB=${OFFSITE_PART_MB:-45}
TMP_BASE=${OFFSITE_TMP:-/opt/writespeak_backups}
DIR=${1:?usage: offsite.sh <backup dir> [--full] [summary]}
FULL=""
SUMMARY=""
shift
for a in "$@"; do
  if [ "$a" = "--full" ]; then FULL=1; else SUMMARY=$a; fi
done
TS=$(basename "$DIR")
STATUS="$APP/data/offsite_status.json"
FULL_EVERY_DAYS=7

umask 077

env_value() { grep -m1 "^$1=" "$APP/.env" | cut -d= -f2- | tr -d '\r'; }

TOKEN=$(env_value TELEGRAM_BOT_TOKEN || true)
PASS=$(env_value BACKUP_PASSPHRASE || true)

alert() {
  local ids id
  ids=$(env_value ADMIN_TELEGRAM_IDS || true)
  [ -n "$TOKEN" ] && [ -n "$ids" ] || return 0
  for id in ${ids//,/ }; do
    curl -s -m 20 -o /dev/null "${TG_API}/bot${TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${id}" --data-urlencode "text=$1" || true
  done
}

status() { # ok error parts bytes full
  local prev_full_at now
  now=$(date -Iseconds)
  prev_full_at=$(jq -r '.full_at // empty' "$STATUS" 2>/dev/null || true)
  [ "$5" = 1 ] && [ "$1" = true ] && prev_full_at=$now
  jq -n --arg at "$now" --arg ts "$TS" --argjson ok "$1" --arg error "$2" --argjson parts "$3" \
    --argjson bytes "$4" --arg full_at "$prev_full_at" --argjson full "$5" \
    '{at: $at, ts: $ts, ok: $ok, error: $error, parts: $parts, bytes: $bytes, full: ($full == 1), full_at: $full_at}' \
    > "$STATUS.tmp" && mv "$STATUS.tmp" "$STATUS"
  chmod 644 "$STATUS" 2>/dev/null || true
}

fail() {
  echo "$(date '+%F %T') offsite FAILED: $1"
  status false "$1" 0 0 0
  alert "⚠️ Bandzilla: zaxirani Telegram guruhga yuborib bo'lmadi ($TS): $1. Serverdagi zaxira joyida."
  exit 1
}

[ -d "$DIR" ] || fail "papka yo'q: $DIR"
[ -n "$TOKEN" ] || fail ".env da TELEGRAM_BOT_TOKEN yo'q"
[ -n "$PASS" ] || fail ".env da BACKUP_PASSPHRASE yo'q"
CHAT=$(jq -r '.chat_id // empty' "$APP/data/crm.json" 2>/dev/null || true)
THREAD=$(jq -r '.topics.backup // empty' "$APP/data/crm.json" 2>/dev/null || true)
if [ -z "$CHAT" ] || [ -z "$THREAD" ]; then
  fail "adminlar guruhida «💾 Zaxira» bo'limi ulanmagan (bo'lim oching va unda /crm_setup yuboring)"
fi

# A full copy of the files: on Sundays, when asked, or when the last one is older than a week (or never sent).
if [ -z "$FULL" ]; then
  last_full=$(jq -r '.full_at // empty' "$STATUS" 2>/dev/null || true)
  if [ "$(date +%u)" = 7 ] || [ -z "$last_full" ] \
     || [ $(( $(date +%s) - $(date -d "$last_full" +%s 2>/dev/null || echo 0) )) -gt $(( FULL_EVERY_DAYS * 86400 )) ]; then
    FULL=1
  fi
fi

WORK=$(mktemp -d "$TMP_BASE/.offsite.XXXXXX") || fail "vaqtinchalik papka ochilmadi"
trap 'rm -rf "$WORK"' EXIT

# --- the archives -------------------------------------------------------------------------------------
small=""
for extra in books sample_library calibration crm.json; do
  [ -e "$APP/data/$extra" ] && small="$small $extra"
done
# shellcheck disable=SC2086
tar -C "$APP/data" -czf "$WORK/data-small.tar.gz" $small || fail "kichik fayllar arxivlanmadi"
(cd "$APP/data" && find storage -type f -mmin -$(( 26 * 60 )) -print0 \
  | tar --null -T - -czf "$WORK/storage-new.tar.gz") || fail "yangi fayllar arxivlanmadi"
tar -C "$DIR" -cf "$WORK/kunlik.tar" db.dump redis.rdb ledger.txt env SHA256SUMS \
  && tar -C "$WORK" -rf "$WORK/kunlik.tar" data-small.tar.gz storage-new.tar.gz \
  || fail "kunlik arxiv yig'ilmadi"
rm -f "$WORK/data-small.tar.gz" "$WORK/storage-new.tar.gz"
archives="kunlik"
if [ -n "$FULL" ]; then
  tar -C "$DIR" -cf "$WORK/fayllar.tar" storage.tar.gz SHA256SUMS || fail "fayllar arxivi yig'ilmadi"
  archives="kunlik fayllar"
fi

# --- encrypt, check, send -----------------------------------------------------------------------------
send_file() { # file caption -> 0 when Telegram took it (3 tries)
  local try out
  for try in 1 2 3; do
    out=$(curl -s -m 900 "${TG_API}/bot${TOKEN}/sendDocument" \
      -F "chat_id=${CHAT}" -F "message_thread_id=${THREAD}" -F "disable_notification=true" \
      -F "document=@$1" -F "caption=$2" 2>&1)
    if printf '%s' "$out" | grep -q '"ok":true'; then return 0; fi
    echo "$(date '+%F %T') offsite: send try $try failed: $(printf '%s' "$out" | tr -d '\n' | cut -c1-200)"
    sleep $(( try * 15 ))
  done
  return 1
}

parts_sent=0
bytes_sent=0
for name in $archives; do
  plain="$WORK/$name.tar"
  enc="$WORK/bandzilla-$TS-$name.tar.gpg"
  gpg --batch --yes --quiet --pinentry-mode loopback --passphrase-fd 3 --symmetric --cipher-algo AES256 \
    --compress-algo none -o "$enc" "$plain" 3<<<"$PASS" || fail "$name: shifrlanmadi"
  want=$(sha256sum < "$plain" | cut -d' ' -f1)
  got=$(gpg --batch --quiet --pinentry-mode loopback --passphrase-fd 3 -d "$enc" 3<<<"$PASS" 2>/dev/null \
    | sha256sum | cut -d' ' -f1)
  [ "$want" = "$got" ] || fail "$name: shifrlangan nusxa tekshiruvdan o'tmadi"
  rm -f "$plain"
  size=$(stat -c %s "$enc")
  human=$(numfmt --to=iec --suffix=B "$size")
  if [ "$size" -gt $(( PART_MB * 1024 * 1024 )) ]; then
    split -b "${PART_MB}m" -d -a 2 "$enc" "$enc.part" || fail "$name: bo'laklarga bo'linmadi"
    rm -f "$enc"
    files=("$enc".part*)
  else
    files=("$enc")
  fi
  n=${#files[@]}
  i=0
  for f in "${files[@]}"; do
    i=$((i + 1))
    if [ "$name" = kunlik ]; then what="kunlik: baza + yangi fayllar"; else what="barcha fayllar (haftalik)"; fi
    caption="💾 #zaxira $TS · $what · $i/$n · jami $human"
    [ -n "$SUMMARY" ] && [ "$i" = 1 ] && caption="$caption
$SUMMARY"
    send_file "$f" "$caption" || fail "$name: Telegram faylni qabul qilmadi ($i/$n)"
    parts_sent=$((parts_sent + 1))
    bytes_sent=$((bytes_sent + $(stat -c %s "$f")))
    sleep 3
  done
done

status true "" "$parts_sent" "$bytes_sent" "${FULL:-0}"
echo "$(date '+%F %T') offsite OK $TS: $archives, $parts_sent file(s), $(numfmt --to=iec --suffix=B "$bytes_sent")"
