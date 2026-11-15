#!/usr/bin/env bash
# deploy/backup/offsite.sh end to end, on the server host (gpg, jq, python3) - never the real Telegram or the
# real data: a throwaway app dir, a fake backup, and a fake Bot API that keeps what it is sent. Then the parts
# are joined and decrypted with the passphrase, and must match what went in.
#
#   bash tests/offsite_test.sh /opt/writespeak_dev/calib/app/deploy/backup/offsite.sh
set -uo pipefail
SCRIPT=${1:?usage: offsite_test.sh <path to offsite.sh>}
T=$(mktemp -d /tmp/offsite-test.XXXXXX)
trap 'kill $SRV 2>/dev/null; rm -rf "$T"' EXIT
fails=0
check() { if eval "$2"; then echo "ok   $1"; else echo "FAIL $1"; fails=$((fails + 1)); fi; }

PASS="test-passphrase-$RANDOM-$RANDOM"
APP=$T/app
B=$T/backups/daily/20261116-0050
mkdir -p "$APP/data/storage/u1/uploads" "$APP/data/storage/u1/reports" "$APP/data/books" "$B" "$T/got"
printf 'TELEGRAM_BOT_TOKEN=123:TEST\nBACKUP_PASSPHRASE=%s\nADMIN_TELEGRAM_IDS=\n' "$PASS" > "$APP/.env"
echo '{"chat_id": -1001, "topics": {"backup": 77, "errors": 5}}' > "$APP/data/crm.json"
echo "book" > "$APP/data/books/b.txt"
head -c 300000 /dev/urandom > "$APP/data/storage/u1/uploads/new.ogg"            # new today
head -c 1000 /dev/urandom > "$APP/data/storage/u1/reports/old.pdf"
touch -d "3 days ago" "$APP/data/storage/u1/reports/old.pdf"                      # not in the daily
head -c 2500000 /dev/urandom > "$B/db.dump"                                     # > the 1 MB part size: split
for f in redis.rdb ledger.txt env; do echo "$f" > "$B/$f"; done
tar -C "$APP/data" -czf "$B/storage.tar.gz" storage
(cd "$B" && sha256sum db.dump storage.tar.gz redis.rdb ledger.txt env > SHA256SUMS)

# the fake Bot API: saves every sendDocument file, answers ok (or 400 when told to)
cat > "$T/api.py" <<'PY'
import email, email.policy, json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
out = sys.argv[1]
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if os.path.exists(os.path.join(out, "FAIL")):
            self.send_response(400); self.end_headers(); self.wfile.write(b'{"ok":false,"description":"Bad Request: test"}'); return
        if self.path.endswith("/sendDocument"):
            msg = email.message_from_bytes(b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body,
                                           policy=email.policy.default)
            fields, name, data = {}, None, None
            for part in msg.iter_parts():
                n = part.get_param("name", header="content-disposition")
                if part.get_filename():
                    name, data = part.get_filename(), part.get_payload(decode=True)
                else:
                    fields[n] = part.get_payload(decode=True).decode("utf-8")  # curl sends UTF-8
            open(os.path.join(out, name), "wb").write(data)
            with open(os.path.join(out, "log.jsonl"), "a") as f:
                f.write(json.dumps({"file": name, **fields}) + "\n")
        self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":true,"result":{}}')
HTTPServer(("127.0.0.1", int(sys.argv[2])), H).serve_forever()
PY
PORT=$((20000 + RANDOM % 20000))
python3 "$T/api.py" "$T/got" "$PORT" & SRV=$!
sleep 1

run() { OFFSITE_APP=$APP OFFSITE_TG_API=http://127.0.0.1:$PORT OFFSITE_PART_MB=1 OFFSITE_TMP=$T bash "$SCRIPT" "$@"; }

# 1. first run: never sent a full copy -> kunlik + fayllar, the big ones in parts
run "$B" "3 users, 1 writing" > "$T/run1.log" 2>&1; rc=$?
check "first run: exit 0" '[ $rc = 0 ]'
check "...sent to the backup topic of the group, silently" \
  '[ "$(jq -r .chat_id "$T/got/log.jsonl" | sort -u)" = "-1001" ] && [ "$(jq -r .message_thread_id "$T/got/log.jsonl" | sort -u)" = 77 ] && [ "$(jq -r .disable_notification "$T/got/log.jsonl" | sort -u)" = true ]'
check "...the daily archive in parts (bigger than the part size)" 'ls "$T/got" | grep -q "20261116-0050-kunlik.tar.gpg.part01"'
check "...and the full files archive (first run)" 'ls "$T/got" | grep -q "20261116-0050-fayllar.tar.gpg"'
check "...the caption: date, what, i/n, and the restore line on the first part" \
  'jq -r .caption "$T/got/log.jsonl" | grep -q "1/3 · jami" && jq -r .caption "$T/got/log.jsonl" | grep -q "3 users, 1 writing"'
cat "$T"/got/bandzilla-20261116-0050-kunlik.tar.gpg.part* > "$T/k.gpg"
mkdir -p "$T/k"
gpg --batch --quiet --pinentry-mode loopback --passphrase "$PASS" -d "$T/k.gpg" 2>/dev/null | tar -x -C "$T/k"
check "joined + decrypted with the passphrase: the dump is byte for byte the same" 'cmp -s "$T/k/db.dump" "$B/db.dump"'
check "...with redis, the ledger, .env, SHA256SUMS" '[ -s "$T/k/redis.rdb" ] && [ -s "$T/k/env" ] && (cd "$T/k" && sha256sum -c SHA256SUMS --ignore-missing >/dev/null 2>&1)'
check "...the small data (books, crm.json)" 'tar -tzf "$T/k/data-small.tar.gz" | grep -q "books/b.txt" && tar -tzf "$T/k/data-small.tar.gz" | grep -q crm.json'
check "...today's new files, not the old ones" 'tar -tzf "$T/k/storage-new.tar.gz" | grep -q "uploads/new.ogg" && ! tar -tzf "$T/k/storage-new.tar.gz" | grep -q old.pdf'
check "a wrong passphrase opens nothing" '! gpg --batch --quiet --pinentry-mode loopback --passphrase wrong -d "$T/k.gpg" >/dev/null 2>&1'
cat "$T"/got/bandzilla-20261116-0050-fayllar.tar.gpg* > "$T/f.gpg"
mkdir -p "$T/f"
gpg --batch --quiet --pinentry-mode loopback --passphrase "$PASS" -d "$T/f.gpg" 2>/dev/null | tar -x -C "$T/f"
check "the full files archive decrypts to the same storage.tar.gz" 'cmp -s "$T/f/storage.tar.gz" "$B/storage.tar.gz"'
check "status file: ok, full_at set, readable by the panel" \
  '[ "$(jq -r .ok "$APP/data/offsite_status.json")" = true ] && [ -n "$(jq -r .full_at "$APP/data/offsite_status.json")" ] && [ "$(stat -c %a "$APP/data/offsite_status.json")" = 644 ]'
check "nothing left behind in the temp dir" '[ -z "$(ls -A "$T" | grep "^.offsite")" ]'

# 2. the next night (not a Sunday, full sent today): the daily only
rm -f "$T"/got/*
if [ "$(date +%u)" != 7 ]; then
  run "$B" > "$T/run2.log" 2>&1
  check "next run: the daily only (a full copy went less than a week ago)" \
    'ls "$T/got" | grep -q kunlik && ! ls "$T/got" | grep -q fayllar'
fi

# 3. Telegram refusing: exit 1, status not ok, full_at kept
rm -f "$T"/got/*
touch "$T/got/FAIL"
full_before=$(jq -r .full_at "$APP/data/offsite_status.json")
run "$B" > "$T/run3.log" 2>&1; rc=$?
rm -f "$T/got/FAIL"
check "Telegram refusing: exit 1, the error in the status, full_at kept" \
  '[ $rc = 1 ] && [ "$(jq -r .ok "$APP/data/offsite_status.json")" = false ] && jq -r .error "$APP/data/offsite_status.json" | grep -q "qabul qilmadi" && [ "$(jq -r .full_at "$APP/data/offsite_status.json")" = "$full_before" ]'

# 4. no topic / no passphrase: refused with a clear reason, nothing sent
echo '{"chat_id": -1001, "topics": {"errors": 5}}' > "$APP/data/crm.json"
run "$B" > "$T/run4.log" 2>&1; rc=$?
check "no 💾 Zaxira topic yet: exit 1, says to open it and send /crm_setup" \
  '[ $rc = 1 ] && grep -q "crm_setup" "$T/run4.log" && [ -z "$(ls "$T/got" | grep gpg)" ]'
echo '{"chat_id": -1001, "topics": {"backup": 77}}' > "$APP/data/crm.json"
sed -i 's/^BACKUP_PASSPHRASE=.*/BACKUP_PASSPHRASE=/' "$APP/.env"
run "$B" > "$T/run5.log" 2>&1; rc=$?
check "no passphrase: exit 1, nothing sent unencrypted" '[ $rc = 1 ] && grep -q BACKUP_PASSPHRASE "$T/run5.log" && [ -z "$(ls "$T/got" | grep -v log)" ]'

if [ $fails = 0 ]; then echo; echo "all passed"; else echo; echo "$fails failed"; cat "$T"/run*.log; fi
exit $fails
