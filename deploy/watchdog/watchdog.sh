#!/usr/bin/env bash
# Bandzilla watchdog (2026-11-15 audit): every alert the bot sends goes through the bot itself, so a dead,
# crash-looping or stuck bot could not report itself. This runs on the host every 5 minutes
# (deploy/watchdog/writespeak-watchdog.cron) and tells ADMIN_TELEGRAM_IDS directly (the Bot API with curl,
# like deploy/backup/backup.sh) when:
#   - a container is not running, or keeps restarting (its restart count went up);
#   - the bot's heartbeat (Redis "health:bot", written every minute by bot/utils/crm.py) has expired;
#   - the admin panel does not answer;
#   - the disk is over DISK_ALERT % full.
# Each problem is reported once when it starts, again every REMIND_HOURS while it lasts, and once more
# when it is over. State: /var/lib/writespeak-watchdog/. Log: /var/log/writespeak-watchdog.log.
#
# Usage: watchdog.sh            normal run
#        watchdog.sh --test     only send a test alert
set -uo pipefail

APP=/opt/writespeak
STATE_DIR=/var/lib/writespeak-watchdog
CONTAINERS="writespeak-bot-1 writespeak-admin_web-1 writespeak-importer-1 writespeak-db-1 writespeak-redis-1"
REDIS=writespeak-redis-1
PANEL_URL=http://127.0.0.1:8088/login
DISK_ALERT=90
REMIND_HOURS=6
# a bot (re)started this recently may not have written its heartbeat yet
GRACE_SECONDS=240

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"

env_value() { grep -m1 "^$1=" "$APP/.env" | cut -d= -f2- | tr -d '\r'; }

send() {
  local token ids id
  token=$(env_value TELEGRAM_BOT_TOKEN || true)
  ids=$(env_value ADMIN_TELEGRAM_IDS || true)
  [ -n "$token" ] && [ -n "$ids" ] || return 0
  for id in ${ids//,/ }; do
    curl -s -m 20 -o /dev/null "https://api.telegram.org/bot${token}/sendMessage" \
      --data-urlencode "chat_id=${id}" --data-urlencode "text=$1" || true
  done
}

if [ "${1:-}" = "--test" ]; then
  send "✅ Bandzilla watchdog: test xabari. Bot, panel, konteynerlar yoki disk bilan muammo bo'lsa, shu yerga yoziladi."
  echo "test alert sent"
  exit 0
fi

exec 9>/var/lock/writespeak-watchdog.lock
flock -n 9 || exit 0

now=$(date +%s)
problems=()   # "key|text"

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

used=$(df --output=pcent / | tail -1 | tr -dc '0-9')
if [ -n "$used" ] && [ "$used" -ge "$DISK_ALERT" ]; then
  problems+=("disk|🔴 Server diski ${used}% to'lgan — to'lsa baza va bot to'xtaydi")
fi

# Report what started, what lasts (every REMIND_HOURS), what is over.
declare -A active=()
for p in "${problems[@]}"; do
  key=${p%%|*}; text=${p#*|}
  active[$key]=1
  file="$STATE_DIR/active.${key//[^A-Za-z0-9_.-]/_}"
  if [ ! -f "$file" ]; then
    echo "$now" > "$file"
    echo "$(date '+%F %T') ALERT $key: $text"
    send "⚠️ Bandzilla watchdog ($(hostname))
$text"
  else
    since=$(head -1 "$file")
    reminded=$(sed -n 2p "$file")
    if [ $(( now - ${reminded:-$since} )) -ge $(( REMIND_HOURS * 3600 )) ]; then
      printf '%s\n%s\n' "$since" "$now" > "$file"
      echo "$(date '+%F %T') STILL $key"
      send "⚠️ Bandzilla watchdog: hali ham davom etmoqda ($(( (now - since) / 60 )) daqiqa)
$text"
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
    rm -f "$file"
    echo "$(date '+%F %T') OK $name"
    send "✅ Bandzilla watchdog: tiklandi — $name ($(( (now - since) / 60 )) daqiqadan keyin)"
  fi
done
exit 0
