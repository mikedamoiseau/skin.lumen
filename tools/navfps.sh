#!/bin/bash
# FPS while navigating: presses a key every 400ms and samples FPS. Usage: tools/navfps.sh [Right|Left|Down|Up] [presses=10]
key="${1:-Right}"; n="${2:-10}"
( for i in $(seq 1 "$n"); do "$(dirname "$0")/rpc.sh" "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"Input.$key\"}" >/dev/null; sleep 0.4; done ) &
"$(dirname "$0")/fps.sh" $(( n * 4 / 10 + 1 ))
wait
