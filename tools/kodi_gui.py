#!/usr/bin/env python3
"""Small Kodi GUI driver over JSON-RPC, used by the test checks (Android TV box by default).

    python3 tools/kodi_gui.py state            # window, focused control, path
    python3 tools/kodi_gui.py install ZIPNAME  # install a zip from the box's Download folder
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

HOST = os.environ.get("KH", "192.168.0.32:8080")
ADB_SERIAL = os.environ.get("ADB_SERIAL", "192.168.0.32:5555")
SSH = os.environ.get("KODI_SSH", "")  # set for a LibreELEC box: restarts and screenshots go over SSH, not ADB
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def rpc(method, params=None, timeout=30):
    req = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        req["params"] = params
    r = urllib.request.Request(f"http://{HOST}/jsonrpc", json.dumps(req).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=timeout))


def labs(names):
    return rpc("XBMC.GetInfoLabels", {"labels": list(names)})["result"]


def lab(name):
    return labs([name])[name]


def conds(cs):
    return rpc("XBMC.GetInfoBooleans", {"booleans": list(cs)})["result"]


def cond(c):
    return conds([c])[c]


def keepalive():
    """Reset Android's screensaver timer (KEYCODE_WAKEUP is not delivered to Kodi)."""
    if SSH:  # no Android underneath, but Kodi's screensaver swallows the JSON-RPC input that wakes it,
        # and an add-on screensaver (the Pi's Bing) keeps its own window on top until Back
        if cond("System.ScreenSaverActive"):
            rpc("Input.ExecuteAction", {"action": "noop"})
            wait_for(lambda: not cond("System.ScreenSaverActive"), 5)
        if "/addons/screensaver." in lab("Window.Property(xmlfile)"):
            rpc("Input.Back")
            wait_for(lambda: "/addons/screensaver." not in lab("Window.Property(xmlfile)"), 5)
        return
    subprocess.run(["adb", "-s", ADB_SERIAL, "shell", "input", "keyevent", "KEYCODE_WAKEUP"], capture_output=True, timeout=15)


def wait_for(fn, timeout=20, step=0.3):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if fn():
            return True
        time.sleep(step)
    return bool(fn())


def reset_home():
    """Close dialogs (keyboard, menus) and go to Home."""
    for _ in range(5):
        if not any(conds(["System.HasActiveModalDialog", "Window.IsVisible(virtualkeyboard)", "Window.IsVisible(numericinput)"]).values()):
            break
        rpc("Input.Back")
        time.sleep(1)
    rpc("GUI.ActivateWindow", {"window": "home"})
    wait_for(lambda: lab("System.CurrentWindow") == "Home", 10)
    return lab("System.CurrentWindow")


def set_skin(setting, value):
    r = rpc("Settings.SetSkinSettingValue", {"setting": setting, "value": value})
    if "error" in r:
        raise RuntimeError(f"Settings.SetSkinSettingValue({setting}): {r['error']}")


def focus_id(target, keys, maxsteps=20):
    """Press keys (cycling) until System.CurrentControlId == target."""
    for i in range(maxsteps):
        if lab("System.CurrentControlId") == str(target):
            return True
        rpc("Input." + keys[i % len(keys)])
        time.sleep(0.4)
    return lab("System.CurrentControlId") == str(target)


def send_number(text):
    """Type into Kodi's numeric dialog the way phone remotes do, and confirm."""
    if not wait_for(lambda: cond("Window.IsVisible(numericinput)"), 8):
        return False
    rpc("Input.SendText", {"text": text, "done": True})
    return wait_for(lambda: not cond("Window.IsVisible(numericinput)"), 8)


def restart_kodi(timeout=150):
    """Kill Kodi the hard way (like a TV losing power), start it again, wait for Home.
    On LibreELEC it's a clean restart, so settings changed in memory but not yet saved aren't lost."""
    if SSH:
        subprocess.run(["ssh", SSH, "systemctl restart kodi"], capture_output=True, timeout=60)
        time.sleep(5)
    else:
        adb = ["adb", "-s", ADB_SERIAL, "shell"]
        subprocess.run(adb + ["am", "force-stop", "org.xbmc.kodi"], capture_output=True, timeout=30)
        time.sleep(3)
        subprocess.run(adb + ["monkey", "-p", "org.xbmc.kodi", "-c", "android.intent.category.LAUNCHER", "1"], capture_output=True, timeout=30)

    def up():
        try:
            return lab("System.CurrentWindow") == "Home"
        except Exception:
            return False
    return wait_for(up, timeout, 3)


def shot(name):
    if SSH:  # Kodi's own screenshot, copied off the box and removed there (tools/shot.sh)
        out = subprocess.run([os.path.join(ROOT, "tools", "shot.sh"), name], capture_output=True, text=True, timeout=60,
                             env={**os.environ, "KODI_HOST": HOST}, check=True)
        return out.stdout.strip()
    os.makedirs(os.path.join(ROOT, "shots"), exist_ok=True)
    path = os.path.join(ROOT, "shots", f"{name}.png")
    with open(path, "wb") as f:
        subprocess.run(["adb", "-s", ADB_SERIAL, "exec-out", "screencap", "-p"], stdout=f, timeout=30, check=True)
    return path


def install_zip(zipname):
    """Add-on browser > Install from zip file > External storage > Download > zipname."""
    def pick(label, maxsteps=40):
        time.sleep(1)
        for _ in range(maxsteps):
            if lab("System.CurrentControl") == label:
                rpc("Input.Select")
                time.sleep(2)
                return True
            rpc("Input.Down")
            time.sleep(0.45)  # the focused label lags faster key presses
        return False
    keepalive()
    reset_home()
    rpc("GUI.ActivateWindow", {"window": "addonbrowser"})
    time.sleep(3)
    if not pick("Install from zip file"):
        return False
    wait_for(lambda: lab("System.CurrentWindow") == "File browser", 8)
    for step in ("[External storage]", "[Download]", zipname):
        if not pick(step):
            print("install: not found:", step, file=sys.stderr)
            return False
    time.sleep(4)
    return True


if __name__ == "__main__":
    cmd, *args = sys.argv[1:]
    if cmd == "state":
        for k, v in labs(["System.CurrentWindow", "System.CurrentControlId", "System.CurrentControl", "Container.FolderPath"] + args).items():
            print(f"  {k} = {v}")
    elif cmd == "install":
        sys.exit(0 if install_zip(args[0]) else 1)
