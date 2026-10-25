#!/bin/bash
# Usage: tools/rpc.sh '<json-rpc payload>'  — posts to the Kodi box
KODI_HOST="${KODI_HOST:-192.168.0.20:8080}"
curl -s -m 15 -H 'Content-Type: application/json' -d "$1" "http://$KODI_HOST/jsonrpc"; echo
