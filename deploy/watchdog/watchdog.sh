#!/usr/bin/env bash
# Bandzilla watchdog (2026-11-15 audit): every alert the bot sends goes through the bot itself, so a dead,
# crash-looping or stuck bot could not report itself. This runs on the host every 5 minutes
# (deploy/watchdog/writespeak-watchdog.cron) and tells ADMIN_TELEGRAM_IDS directly (the Bot API with curl,
# like deploy/backup/backup.sh) when:
#   - a container is not running, or keeps restarting (its restart count went up);
#   - the bot's heartbeat (Redis "health:bot", written every minute by bot/utils/crm.py) has expired;
#   - the admin panel does not answer;
#   - the disk is over DISK_ALERT % full;
# and since 2026-11-16 (the owner: "nothing should need watching by hand"):
#   - the nightly backup failed (data/backup_status.json, written by backup.sh), is older than BACKUP_MAX_HOURS
#     or has been running for over BACKUP_HUNG_HOURS (a hung run made every later night skip silently);
#   - the encrypted copy in the admins' group (data/offsite_status.json, offsite.sh) failed or is older than
#     OFFSITE_MAX_HOURS;
#   - a site's TLS certificate ends within TLS_WARN_DAYS (checked once an hour).
# Each problem is reported once when it starts, again every REMIND_HOURS while it lasts, and once more
# when it is over - and only counted as told when Telegram said "ok" (a failed send is tried again on the next
# run). Every run writes data/ops_status.json; https://admin.bandzilla.uz/healthz/ops reports it, so an outside
# monitor (UptimeRobot) e-mails the owner even when Telegram, or this script's cron, is the thing that broke.
# State: /var/lib/writespeak-watchdog/. Log: /var/log/writespeak-watchdog.log.
#
# Usage: watchdog.sh            normal run
#        watchdog.sh --test     only send a test alert
# The WATCHDOG_* overrides are for tests/watchdog_test.sh only.
set -uo pipefail

APP=${WATCHDOG_APP:-/opt/writespeak}
STATE_DIR=${WATCHDOG_STATE:-/var/lib/writespeak-watchdog}
BACKUPS=${WATCHDOG_BACKUPS:-/opt/writespeak_backups}
TG_API=${WATCHDOG_TG_API:-https://api.telegram.org}
LOCK=${WATCHDOG_LOCK:-/var/lock/writespeak-watchdog.lock}
CONTAINERS=${WATCHDOG_CONTAINERS-"writespeak-bot-1 writespeak-admin_web-1 writespeak-importer-1 writespeak-moved_bot-1 writespeak-db-1 writespeak-redis-1"}
TLS_HOSTS=${WATCHDOG_TLS_HOSTS-"bandzilla.uz admin.bandzilla.uz writespeak.uz"}
REDIS=writespeak-redis-1
PANEL_URL=${WATCHDOG_PANEL_URL:-http://127.0.0.1:8088/login}
DISK_ALERT=90
REMIND_HOURS=6
BACKUP_MAX_HOURS=26
BACKUP_HUNG_HOURS=${WATCHDOG_HUNG_HOURS:-3}
OFFSITE_MAX_HOURS=30
TLS_WARN_DAYS=14
# a bot (re)started this recently may not have written its heartbeat yet
GRACE_SECONDS=240

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"

env_value() { grep -m1 "^$1=" "$APP/.env" | cut -d= -f2- | tr -d '\r'; }

send() { # text -> 0 when Telegram took it for at least one admin
  local token ids id out ok=1
  token=$(env_value TELEGRAM_BOT_TOKEN || true)
  ids=$(env_value ADMIN_TELEGRAM_IDS || true)
  [ -n "$token" ] && [ -n "$ids" ] || return 1
  for id in ${ids//,/ }; do
    out=$(curl -s -m 20 "${TG_API}/bot${token}/sendMessage" \
      --data-urlencode "chat_id=${id}" --data-urlencode "text=$1" 2>/dev/null || true)
    if printf '%s' "$out" | grep -q '"ok":true'; then ok=0; fi
  done
  [ $ok = 0 ] || echo "$(date '+%F %T') SEND FAILED: $(printf '%s' "$out" | tr -d '\n' | cut -c1-160)"
  return $ok
}

if [ "${1:-}" = "--test" ]; then
  send "✅ Bandzilla watchdog: test xabari. Bot, panel, konteynerlar, zaxira yoki disk bilan muammo bo'lsa, shu yerga yoziladi."
  echo "test alert sent"
  exit 0
fi

exec 9>"$LOCK"
flock -n 9 || exit 0

now=$(date +%s)
problems=()   # "key|text"

age_hours() { # ISO time -> whole hours since it (999 when unreadable)
  local t
  t=$(date -d "$1" +%s 2>/dev/null) || { echo 999; return; }
  echo $(( (now - t) / 3600 ))
}

if [ -z "${WATCHDOG_SKIP_DOCKER:-}" ]; then
  for c in $CONTAINERS; do
    running=$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null || echo "missing")
    if [ "$running" != "true" ]; then
      problems+=("down:$c|🔴 $c ishlamayapti (holati: $running)")
      continue
    fi
    restarts=$(docker inspect -f '{{.RestartCount}}' "$c" 2>/dev/null || echo 0)
    last=$(cat "$STATE_DIR/restarts.$c" 2>/dev/null || echo "$restarts")
    echo "$restarts" > "$STATE_DIR/restarts.$c"
    if [ "$restarts" -gt "$last" ]; then
      problems+=("restart:$c|🟠 $c qayta-qayta o'chib-yonmoqda (oxirgi 5 daqiqada $((restarts - last)) marta) — loglarini ko'ring")
    fi
  done

  # The bot's heartbeat - only for a bot that has been up longer than GRACE_SECONDS.
  started=$(docker inspect -f '{{.State.StartedAt}}' writespeak-bot-1 2>/dev/null || echo "")
  up_for=0
  if [ -n "$started" ]; then up_for=$(( now - $(date -d "$started" +%s 2>/dev/null || echo "$now") )); fi
  if [ "$up_for" -gt "$GRACE_SECONDS" ]; then
    beat=$(docker exec "$REDIS" redis-cli get health:bot 2>/dev/null | tr -dc '0-9')
    if [ -z "$beat" ]; then
      problems+=("heartbeat|🔴 Bot javob bermayapti: 5 daqiqadan beri tiriklik belgisi yo'q (osilib qolgan yoki Redis'ga yozolmayapti)")
    fi
  fi

  code=$(curl -s -m 15 -o /dev/null -w '%{http_code}' "$PANEL_URL" || echo 000)
  if [ "$code" != "200" ]; then
    problems+=("panel|🟠 Admin panel javob bermayapti (HTTP $code)")
  fi
fi

used=$(df --output=pcent / | tail -1 | tr -dc '0-9')
if [ -n "$used" ] && [ "$used" -ge "$DISK_ALERT" ]; then
  problems+=("disk|🔴 Server diski ${used}% to'lgan — to'lsa baza va bot to'xtaydi")
fi

# --- the nightly backup (2026-11-16) -------------------------------------------------------------------
status_file="$APP/data/backup_status.json"
if [ -f "$status_file" ]; then
  b_ok=$(jq -r '.ok | tostring' "$status_file" 2>/dev/null || true)
  b_err=$(jq -r '.error // empty' "$status_file" 2>/dev/null || true)
  [ -n "$b_err" ] || b_err="sababi logda"
  if [ "$b_ok" = "false" ]; then
    problems+=("backup_failed|🔴 Tungi zaxira xato bilan tugadi: $b_err — /var/log/writespeak-backup.log")
  fi
fi
newest=$(find "$BACKUPS/daily" -mindepth 1 -maxdepth 1 -type d -name '20*' ! -name '*.partial' -printf '%T@\n' 2>/dev/null \
  | sort -n | tail -1 | cut -d. -f1)
if [ -z "$newest" ]; then
  problems+=("backup_old|🔴 Serverda birorta ham kunlik zaxira topilmadi ($BACKUPS/daily)")
elif [ $(( (now - newest) / 3600 )) -ge "$BACKUP_MAX_HOURS" ]; then
  problems+=("backup_old|🔴 Oxirgi kunlik zaxira $(( (now - newest) / 3600 )) soat oldin olingan — tungi zaxira ishlamagan")
fi
# a run that never ends holds its lock, and every later night exits "another backup is running"
hung=$(ps -eo etimes=,args= 2>/dev/null | awk -v max=$(( BACKUP_HUNG_HOURS * 3600 )) \
  '$0 ~ /deploy\/backup\/backup\.sh/ && $0 !~ /awk/ && $1 > max {print int($1 / 3600); exit}')
if [ -n "$hung" ]; then
  problems+=("backup_hung|🟠 Zaxira skripti ${hung} soatdan beri tugamayapti (osilib qolgan) — keyingi kechalar o'tkazib yuboriladi")
fi

# --- the encrypted copy in the admins' group ------------------------------------------------------------
off_file="$APP/data/offsite_status.json"
if [ -f "$off_file" ]; then
  o_ok=$(jq -r '.ok | tostring' "$off_file" 2>/dev/null || true)
  o_at=$(jq -r '.at // empty' "$off_file" 2>/dev/null || true)
  o_err=$(jq -r '.error // empty' "$off_file" 2>/dev/null || true)
  [ -n "$o_err" ] || o_err="sababi noma'lum"
  if [ "$o_ok" != "true" ]; then
    problems+=("offsite_failed|🔴 Zaxiraning Telegram guruhdagi nusxasi yuborilmadi: $o_err")
  elif [ "$(age_hours "$o_at")" -ge "$OFFSITE_MAX_HOURS" ]; then
    problems+=("offsite_old|🟠 Zaxiraning guruhdagi oxirgi nusxasi $(age_hours "$o_at") soat oldin yuborilgan")
  fi
else
  problems+=("offsite_failed|🟠 Zaxiraning Telegram guruhdagi nusxasi hali bir marta ham yuborilmagan")
fi

# --- TLS certificates, once an hour (the result kept for the runs in between) ---------------------------
tls_state="$STATE_DIR/tls.last"
if [ -n "$TLS_HOSTS" ]; then
  if [ ! -f "$tls_state" ] || [ $(( now - $(stat -c %Y "$tls_state") )) -ge 3600 ]; then
    : > "$tls_state.new"
    for h in $TLS_HOSTS; do
      end=$(echo | timeout 20 openssl s_client -servername "$h" -connect "$h:443" 2>/dev/null \
        | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
      if [ -z "$end" ]; then
        echo "$h|unreadable" >> "$tls_state.new"
      else
        echo "$h|$(( ($(date -d "$end" +%s) - now) / 86400 ))" >> "$tls_state.new"
      fi
    done
    mv "$tls_state.new" "$tls_state"
  fi
  while IFS='|' read -r h days; do
    [ -n "$h" ] || continue
    if [ "$days" = unreadable ]; then
      problems+=("tls:$h|🟠 $h sertifikatini o'qib bo'lmadi (sayt javob bermayaptimi?)")
    elif [ "$days" -lt "$TLS_WARN_DAYS" ]; then
      problems+=("tls:$h|🔴 $h sertifikati $days kundan keyin tugaydi — certbot yangilamayapti")
    fi
  done < "$tls_state"
fi

# --- tell: what started, what lasts (every REMIND_HOURS), what is over -----------------------------------
declare -A active=()
for p in "${problems[@]}"; do
  key=${p%%|*}; text=${p#*|}
  active[$key]=1
  file="$STATE_DIR/active.${key//[^A-Za-z0-9_.-]/_}"
  if [ ! -f "$file" ]; then
    echo "$(date '+%F %T') ALERT $key: $text"
    # counted as told only when Telegram took it - else the next run (5 minutes) tries again
    send "⚠️ Bandzilla watchdog ($(hostname))
$text" && echo "$now" > "$file"
  else
    since=$(head -1 "$file")
    reminded=$(sed -n 2p "$file")
    if [ $(( now - ${reminded:-$since} )) -ge $(( REMIND_HOURS * 3600 )) ]; then
      echo "$(date '+%F %T') STILL $key"
      send "⚠️ Bandzilla watchdog: hali ham davom etmoqda ($(( (now - since) / 60 )) daqiqa)
$text" && printf '%s\n%s\n' "$since" "$now" > "$file"
    fi
  fi
done
for file in "$STATE_DIR"/active.*; do
  [ -e "$file" ] || continue
  name=${file##*/active.}
  found=""
  for key in "${!active[@]}"; do
    [ "${key//[^A-Za-z0-9_.-]/_}" = "$name" ] && found=1
  done
  if [ -z "$found" ]; then
    since=$(head -1 "$file")
    echo "$(date '+%F %T') OK $name"
    send "✅ Bandzilla watchdog: tiklandi — $name ($(( (now - since) / 60 )) daqiqadan keyin)" && rm -f "$file"
  fi
done

# --- the state for /healthz/ops (webapp/main.py) - written last, so a run that died midway goes stale -----
keys=()
for p in "${problems[@]}"; do keys+=("${p%%|*}"); done
ops="$APP/data/ops_status.json"
if command -v jq >/dev/null 2>&1; then
  printf '%s\n' "${keys[@]}" | jq -R . | jq -s --argjson at "$now" '{at: $at, problems: map(select(. != ""))}' \
    > "$ops.tmp" 2>/dev/null && mv "$ops.tmp" "$ops" && chmod 644 "$ops"
fi
exit 0
