#!/usr/bin/env bash
# deploy/watchdog/watchdog.sh's 2026-11-16 checks, on the server host (jq, python3, openssl) - never the real
# Telegram, the real backups or the real state: a throwaway app dir, backups dir and state dir, a fake Bot API.
#
#   bash tests/watchdog_test.sh /opt/writespeak_dev/calib/app/deploy/watchdog/watchdog.sh
set -uo pipefail
SCRIPT=${1:?usage: watchdog_test.sh <path to watchdog.sh>}
T=$(mktemp -d /tmp/watchdog-test.XXXXXX)
trap 'kill $SRV $HUNG 2>/dev/null; rm -rf "$T"' EXIT
HUNG=""
fails=0
check() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }

APP=$T/app
mkdir -p "$APP/data" "$T/backups/daily/20261116-0050" "$T/state" "$T/got"
printf 'TELEGRAM_BOT_TOKEN=123:TEST\nADMIN_TELEGRAM_IDS=111\n' > "$APP/.env"
now_iso() { date -Iseconds; }
good_backup() { echo "{\"at\": \"$(now_iso)\", \"ok\": true, \"ts\": \"20261116-0050\", \"db\": \"7M\", \"files\": \"160M\", \"error\": \"\"}" > "$APP/data/backup_status.json"; }
good_offsite() { echo "{\"at\": \"$(now_iso)\", \"ok\": true, \"error\": \"\", \"parts\": 1}" > "$APP/data/offsite_status.json"; }
good_backup; good_offsite

cat > "$T/api.py" <<'PY'
import json, os, sys, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
out = sys.argv[1]
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        if os.path.exists(os.path.join(out, "FAIL")):
            self.send_response(400); self.end_headers(); self.wfile.write(b'{"ok":false,"description":"test"}'); return
        fields = {k: v[0] for k, v in urllib.parse.parse_qs(body).items()}
        with open(os.path.join(out, "sent.jsonl"), "a") as f:
            f.write(json.dumps(fields, ensure_ascii=False) + "\n")
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":true,"result":{}}')
HTTPServer(("127.0.0.1", int(sys.argv[2])), H).serve_forever()
PY
PORT=$((20000 + RANDOM % 20000))
python3 "$T/api.py" "$T/got" "$PORT" & SRV=$!
sleep 1

run() {
  WATCHDOG_APP=$APP WATCHDOG_STATE=$T/state WATCHDOG_BACKUPS=$T/backups WATCHDOG_TG_API=http://127.0.0.1:$PORT \
  WATCHDOG_LOCK=$T/lock WATCHDOG_CONTAINERS="" WATCHDOG_TLS_HOSTS="${TLS:-}" WATCHDOG_SKIP_DOCKER=1 \
  WATCHDOG_HUNG_HOURS="${HUNG_H:-3}" bash "$SCRIPT" >> "$T/run.log" 2>&1
}
sent() { [ -f "$T/got/sent.jsonl" ] && wc -l < "$T/got/sent.jsonl" || echo 0; }
problems() { jq -r '.problems | join(",")' "$APP/data/ops_status.json"; }

# 1. all well: nothing sent, ops_status fresh and empty
run
check "all well: nothing sent" '[ "$(sent)" = 0 ]'
check "...ops_status.json written: fresh, no problems, readable by the panel" \
  '[ -z "$(problems)" ] && [ $(( $(date +%s) - $(jq -r .at "$APP/data/ops_status.json") )) -lt 60 ] && [ "$(stat -c %a "$APP/data/ops_status.json")" = 644 ]'

# 2. the nightly backup failed
echo "{\"at\": \"$(now_iso)\", \"ok\": false, \"error\": \"qator 97\"}" > "$APP/data/backup_status.json"
run
check "a failed backup: told once, with its reason" '[ "$(sent)" = 1 ] && grep -q "qator 97" "$T/got/sent.jsonl"'
check "...in ops_status" '[ "$(problems)" = backup_failed ]'
run
check "...the next run: not told again" '[ "$(sent)" = 1 ]'
good_backup
run
check "...fixed: 'tiklandi' once, the problem gone" '[ "$(sent)" = 2 ] && tail -1 "$T/got/sent.jsonl" | grep -q tiklandi && [ -z "$(problems)" ]'

# 3. no backup for over 26 hours / none at all
touch -d "30 hours ago" "$T/backups/daily/20261116-0050"
run
check "the newest backup 30 hours old: told" 'grep -q "30 soat oldin" "$T/got/sent.jsonl" && [ "$(problems)" = backup_old ]'
touch "$T/backups/daily/20261116-0050"; run
mv "$T/backups/daily/20261116-0050" "$T/backups/daily/20261116-0050.partial"
run
check "...no finished backup at all (only a .partial): told" 'grep -q "birorta ham kunlik zaxira" "$T/got/sent.jsonl"'
mv "$T/backups/daily/20261116-0050.partial" "$T/backups/daily/20261116-0050"; run

# 4. the copy in the admins' group: failed / stale / never sent
echo '{"at": "2026-11-16T00:50:00+01:00", "ok": false, "error": "Telegram faylni qabul qilmadi"}' > "$APP/data/offsite_status.json"
run
check "the group copy failed: told with its reason" 'grep -q "qabul qilmadi" "$T/got/sent.jsonl" && [ "$(problems)" = offsite_failed ]'
echo "{\"at\": \"$(date -Iseconds -d '40 hours ago')\", \"ok\": true}" > "$APP/data/offsite_status.json"
run
check "...ok but 40 hours old: told" 'grep -q "40 soat oldin" "$T/got/sent.jsonl" && [ "$(problems)" = offsite_old ]'
rm -f "$APP/data/offsite_status.json"
run
check "...never sent: told" 'grep -q "bir marta ham yuborilmagan" "$T/got/sent.jsonl"'
good_offsite; run

# 5. Telegram refusing: not counted as told, so the next run tries again
before=$(sent)
touch "$T/got/FAIL"
echo "{\"at\": \"$(now_iso)\", \"ok\": false, \"error\": \"qator 140\"}" > "$APP/data/backup_status.json"
run
rm -f "$T/got/FAIL"
check "Telegram refused the alert: no state kept (it would have been silent for 6 hours before)" \
  '[ ! -f "$T/state/active.backup_failed" ] && grep -q "SEND FAILED" "$T/run.log"'
run
check "...the next run: told" '[ "$(sent)" = $((before + 1)) ] && grep -q "qator 140" "$T/got/sent.jsonl"'
good_backup; run

# 6. a backup run that never ends
HUNG_H=0
bash -c 'exec -a "/opt/x/deploy/backup/backup.sh" sleep 60' & HUNG=$!
sleep 2
run
check "a backup.sh running past its limit: told it hangs" 'grep -q "tugamayapti" "$T/got/sent.jsonl" && [ "$(problems)" = backup_hung ]'
kill $HUNG 2>/dev/null; wait $HUNG 2>/dev/null; HUNG=""
run
HUNG_H=3

# 7. TLS: a certificate ending soon (the hourly result kept between runs), a real one fine
printf 'soon.example|5\n' > "$T/state/tls.last"
TLS="soon.example" run
check "a certificate ending in 5 days: told" 'grep -q "5 kundan keyin" "$T/got/sent.jsonl" && [ "$(problems)" = tls:soon.example ]'
rm -f "$T/state/tls.last"
TLS="bandzilla.uz" run
check "...the live bandzilla.uz certificate: fine (read over the network)" \
  'grep -q "^bandzilla.uz|[0-9]" "$T/state/tls.last" && [ -z "$(problems)" ]'

# 8. the watchdog's own lock: a second run at once does nothing
exec 8>"$T/lock"; flock -n 8
at_before=$(jq -r .at "$APP/data/ops_status.json"); sleep 1
run
check "a run while another holds the lock: nothing done" '[ "$(jq -r .at "$APP/data/ops_status.json")" = "$at_before" ]'
flock -u 8

if [ $fails = 0 ]; then echo; echo "all passed"; else echo; echo "$fails failed"; cat "$T/run.log"; fi
exit $fails
