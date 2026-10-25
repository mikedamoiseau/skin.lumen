#!/bin/bash
# Show skin-related warnings/errors from kodi.log (last N lines, default 400)
ssh "${KODI_SSH:-root@192.168.0.20}" "tail -n ${1:-400} /storage/.kodi/temp/kodi.log" | grep -iE 'error|warning' | grep -viE 'python|addon.*repository|CCurlFile|JSONRPC|Previous line repeats' | tail -40
