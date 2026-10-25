#!/bin/bash
# Take a Kodi screenshot and copy it locally (plus a 1280px preview).  Usage: tools/shot.sh name [delay]
# Only ever touches the file this run created: pre-existing screenshots in the user's folder are left alone.
set -euo pipefail
HOST="${KODI_SSH:-root@192.168.0.20}"
OUT="${SHOT_DIR:-$(dirname "$0")/../shots}"
DIR=/storage/screenshots
mkdir -p "$OUT"
sleep "${2:-0}"
before=$(ssh "$HOST" "ls $DIR 2>/dev/null" | sort)
"$(dirname "$0")/rpc.sh" '{"jsonrpc":"2.0","id":1,"method":"Input.ExecuteAction","params":{"action":"screenshot"}}' >/dev/null
new=""
for _ in $(seq 1 20); do
  sleep 0.5
  after=$(ssh "$HOST" "ls $DIR 2>/dev/null" | sort)
  new=$(comm -13 <(echo "$before") <(echo "$after") | head -1)
  [[ -n "$new" ]] && break
done
if [[ -z "$new" ]]; then echo "no new screenshot appeared" >&2; exit 1; fi
# wait until the PNG is fully written (size stable)
prev=-1
for _ in $(seq 1 20); do
  size=$(ssh "$HOST" "wc -c < '$DIR/$new'" | tr -d ' ')
  [[ "$size" == "$prev" && "$size" -gt 0 ]] && break
  prev=$size; sleep 0.3
done
scp -q "$HOST:$DIR/$new" "$OUT/$1_full.png"
ssh "$HOST" "rm -f '$DIR/$new'"
sips -Z 1280 "$OUT/$1_full.png" --out "$OUT/$1.png" >/dev/null
echo "$OUT/$1.png"
