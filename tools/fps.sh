#!/bin/bash
# Sample System.FPS once per second.  Usage: tools/fps.sh [samples=10]
for i in $(seq 1 "${1:-10}"); do
  "$(dirname "$0")/rpc.sh" '{"jsonrpc":"2.0","id":1,"method":"XBMC.GetInfoLabels","params":{"labels":["System.FPS"]}}' | grep -o '"System.FPS":"[0-9.]*"' | cut -d'"' -f4 | tr '\n' ' '
  sleep 1
done | awk '{s=0; for(i=1;i<=NF;i++){s+=$i; printf "%s ", $i}; printf "| avg %.1f\n", s/NF}'
