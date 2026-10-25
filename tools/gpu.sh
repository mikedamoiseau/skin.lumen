#!/bin/bash
# V3D render-queue load over N seconds (default 5) + GUI FPS. Usage: tools/gpu.sh [secs]
ssh "${KODI_SSH:-root@192.168.0.20}" "f=/sys/devices/platform/v3dbus/fec00000.v3d/gpu_stats; a=\$(grep render \$f); sleep ${1:-5}; b=\$(grep render \$f); echo \$a \$b" | awk '{dt=($6-$2)/1e9; jobs=$7-$3; rt=($8-$4)/1e6; printf "render jobs/s %.1f  gpu busy %.0f%%  ms/job %.2f  ", jobs/dt, rt/(dt*10), (jobs?rt/jobs:0)}'
"$(dirname "$0")/rpc.sh" '{"jsonrpc":"2.0","id":1,"method":"XBMC.GetInfoLabels","params":{"labels":["System.FPS"]}}' | grep -o '"System.FPS":"[0-9.]*"' | cut -d'"' -f4 | sed 's/^/fps /'
