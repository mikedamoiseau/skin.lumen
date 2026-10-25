#!/bin/bash
# Push skin.lumen to the Kodi box and reload the skin.
# Refuses while something is playing unless --force is given.
set -euo pipefail
HOST="${KODI_SSH:-root@192.168.0.20}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "${1:-}" != "--force" ]]; then
  players=$("$ROOT/tools/rpc.sh" '{"jsonrpc":"2.0","id":1,"method":"Player.GetActivePlayers"}')
  if [[ "$players" != *'"result":[]'* ]]; then echo "Playback active — not deploying ($players)"; exit 1; fi
fi
for f in "$ROOT"/skin.lumen/xml/*.xml; do xmllint --noout "$f" || { echo "XML error in $f"; exit 1; }; done
COPYFILE_DISABLE=1 tar -C "$ROOT" --exclude='.DS_Store' -czf - skin.lumen | ssh "$HOST" 'rm -rf /storage/.kodi/addons/skin.lumen && tar -xzf - -C /storage/.kodi/addons'
ssh "$HOST" 'kodi-send --action="ReloadSkin()" >/dev/null'
echo "deployed"
