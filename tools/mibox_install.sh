#!/bin/sh
# Build skin.lumen from the working tree and install it on the Android TV box through Kodi's
# add-on browser. Kodi caches zip listings by path, so every build gets a unique name.
# Removes only the test zips this script pushed.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SERIAL="${ADB_SERIAL:-192.168.0.32:5555}"
TMP="${TMPDIR:-/tmp}"
cd "$ROOT"
for f in skin.lumen/xml/*.xml; do xmllint --noout "$f"; done
NAME="skin.lumen-test-$(date +%H%M%S).zip"
rm -f "$TMP"/skin.lumen-test-*.zip
zip -qr -X "$TMP/$NAME" skin.lumen -x '*.DS_Store'
adb -s "$SERIAL" shell 'rm -f /sdcard/Download/skin.lumen-test-*.zip'
adb -s "$SERIAL" push "$TMP/$NAME" /sdcard/Download/ >/dev/null
python3 "$ROOT/tools/kodi_gui.py" install "$NAME"
# the installed tree must match the working tree file for file
D=/sdcard/Android/data/org.xbmc.kodi/files/.kodi/addons/skin.lumen
adb -s "$SERIAL" shell "cd $D && find . -type f | sort | xargs md5sum" > "$TMP/lumen-remote.md5"
python3 - "$TMP/lumen-remote.md5" <<'EOF'
import hashlib, os, sys
remote = dict(reversed(l.split(None, 1)) for l in open(sys.argv[1]).read().split("\n") if l.strip())
remote = {k.strip(): v for k, v in remote.items()}
local = {}
for d, _, fs in os.walk("skin.lumen"):
    for f in fs:
        if f == ".DS_Store":
            continue
        p = os.path.join(d, f)
        local["./" + os.path.relpath(p, "skin.lumen")] = hashlib.md5(open(p, "rb").read()).hexdigest()
bad = sorted(k for k in local if remote.get(k) != local[k])
if bad:
    sys.exit(f"installed: MISMATCH in {len(bad)} files, e.g. {bad[:3]}")
print(f"installed: OK ({len(local)} files match)")
EOF
