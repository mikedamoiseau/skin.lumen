"""search_regress.py Q1 [Q2 ...]: open the Search tab, run each query through the keyboard, report row counts."""
import sys, time; sys.path.insert(0, sys.path[0])
from kodi_gui import *
B = lambda *b: rpc("XBMC.GetInfoBooleans", {"booleans": list(b)})["result"]
def wait_kbd(on, t=10):
    t0 = time.time()
    while time.time() - t0 < t:
        if B("Window.IsVisible(virtualkeyboard)")["Window.IsVisible(virtualkeyboard)"] == on: return True
        time.sleep(0.2)
    return False
keepalive()
print("window:", lab("System.CurrentWindow"))
for _ in range(10):  # never press keys outside Home
    if lab("System.CurrentWindow") == "Home": break
    rpc("GUI.ActivateWindow", {"window": "home"}); time.sleep(2)
else:
    sys.exit("not on Home; not pressing keys")
for _ in range(6):
    if lab("System.CurrentControlId") in [str(i) for i in range(10, 18)]: break
    rpc("Input.Up"); time.sleep(0.5)
for _ in range(10):
    c = lab("System.CurrentControlId")
    if c == "16": break
    rpc("Input.Right" if int(c or 0) < 16 else "Input.Left"); time.sleep(0.4)
rpc("Input.Select")
for i, q in enumerate(sys.argv[1:]):
    if i:  # click the search field
        keepalive()
        if lab("System.CurrentWindow") == "Home": sys.exit("left the search page")
        for _ in range(4):
            if lab("System.CurrentControlId") == "100": break
            rpc("Input.Up"); time.sleep(0.4)
        rpc("Input.Select")
    print(f"[{q}] keyboard:", wait_kbd(True))
    if q == "<cancel>":
        rpc("Input.Back")
    else:
        rpc("Input.SendText", {"text": q, "done": True})
    t0 = time.time(); wait_kbd(False)
    L = ["Container(5201).NumItems", "Container(5202).NumItems", "Container(5203).NumItems"]
    time.sleep(0.5)
    while time.time() - t0 < 25:
        r = rpc("XBMC.GetInfoLabels", {"labels": L + ["System.CurrentWindow", "System.CurrentControlId", "Skin.String(lumen.search)"]})["result"]
        u = B("Container(5201).IsUpdating", "Container(5202).IsUpdating", "Container(5203).IsUpdating")
        if not any(u.values()) and time.time() - t0 > 1.5: break
        time.sleep(0.3)
    print(f"[{q}] {time.time()-t0:.1f}s", {k.split('(')[1].split(')')[0] if 'Container' in k else k: v for k, v in r.items()})
