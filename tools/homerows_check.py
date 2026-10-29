#!/usr/bin/env python3
"""Home rows checks (Configure skin > Home rows: turn rows off, add rows from smart playlists) against a Kodi box.

    python3 tools/homerows_check.py NAME [NAME ...]   # run the named checks
    python3 tools/homerows_check.py all               # every check, then cleanup

Each check prints PASS/FAIL/SKIP lines; the exit status is 1 if anything failed.
Everything goes through the real settings page, as with a remote. Ground truth for a playlist row comes from the
library (Files.GetDirectory on the playlist), never from the skin. Every check turns the rows back on and removes the
rows it added; the test playlist it writes into the box's video playlists folder is deleted afterwards.
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kids_check  # noqa: E402
from kids_check import dir_items  # noqa: E402
from kodi_gui import SSH, cond, conds, focus_id, lab, labs, reset_home, restart_kodi, rpc, shot, wait_for  # noqa: E402

CHECKS = {}
ROWS = range(5001, 5009)  # the built-in normal-mode rows; their groups are row + 1000
TOGGLE = {r: 911 + i for i, r in enumerate(ROWS)}  # settings radiobutton per row
CUSTOM = (5021, 5022, 5023)
ADD, RENAME, REMOVE = (921, 922, 923), (931, 932, 933), (941, 942, 943)
EMPTY_MSG = 6018  # Home's message when every row is off
PLAYLIST_DIR = "/storage/.kodi/userdata/playlists/video/"
XSP_NAME = "lumen-check-row.xsp"
XSP = """<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<smartplaylist type="movies"><name>Lumen check</name><match>all</match>
<rule field="year" operator="greaterthan"><value>2015</value></rule><limit>12</limit>
<order direction="descending">year</order></smartplaylist>
"""


def check(name):
    def register(fn):
        CHECKS[name] = fn
        return fn
    return register


# ---------- the settings page ----------

def open_settings():
    rpc("GUI.ActivateWindow", {"window": "skinsettings"})
    return wait_for(lambda: cond("Window.IsActive(skinsettings)"), 8)


def settings_focus(cid):
    """Walk the settings list (Down, then Up) to control cid."""
    if lab("System.CurrentControlId") == str(cid):
        return True
    return focus_id(cid, ("Down",), 30) or focus_id(cid, ("Up",), 30)


def click(cid):
    if not settings_focus(cid):
        return False
    rpc("Input.Select")
    time.sleep(0.8)
    return True


def hidden(row):
    return cond(f"Skin.HasSetting(lumen_hide_{row})")


def home_groups():
    """Visible Home row groups (built-in and added), once the rows have loaded."""
    reset_home()
    groups = [r + 1000 for r in list(ROWS) + list(CUSTOM)]
    wait_for(lambda: not any(conds([f"Container({g - 1000}).IsUpdating" for g in groups]).values()), 20)
    time.sleep(2)
    vis = conds([f"Control.IsVisible({g})" for g in groups])
    return [g - 1000 for g in groups if vis[f"Control.IsVisible({g})"]]


def show_all(r):
    """Turn every built-in row back on and remove added rows, through the page."""
    if not open_settings():
        return r.ok("settings page opens", False)
    for row in ROWS:
        if hidden(row):
            click(TOGGLE[row])
    for rm in REMOVE:
        if cond(f"Control.IsVisible({rm})"):
            click(rm)
    reset_home()
    return r.ok("all rows back, none added", not any(hidden(x) for x in ROWS)
                and all(lab(f"Skin.String(lumen.row{n}.path)") == "" for n in (1, 2, 3)))


def free(r):
    """Nothing playing, and Home rows as shipped: the checks turn every row back on and remove added rows at the
    end, so a box whose household set up its own rows is left alone."""
    if rpc("Player.GetActivePlayers")["result"]:
        r.skip("something is playing on the box; not touching it")
        return False
    if any(hidden(x) for x in ROWS) or any(lab(f"Skin.String(lumen.row{n}.path)") for n in (1, 2, 3)):
        r.skip("this box has its own Home rows set up; not touching them")
        return False
    kids_check.kids(False)
    return True


# ---------- checks ----------

@check("toggle")
def c_toggle(r):
    """A row turned off in Configure skin leaves Home, the others stay, and the remote skips over it."""
    if not free(r):
        return
    before = home_groups()
    middle = before[1:-1]  # a row with rows above and below it
    row = 5006 if 5006 in middle else (middle[0] if middle else None)
    if not r.ok("Home shows at least three rows", row is not None, before):
        return
    try:
        r.ok("settings page opens", open_settings())
        r.ok("the row's switch is on", settings_focus(TOGGLE[row]) and cond(f"Control.IsVisible({TOGGLE[row]})"))
        shot("homerows-settings")
        click(TOGGLE[row])
        r.ok(f"switched off: lumen_hide_{row} set", hidden(row))
        after = home_groups()
        r.ok(f"Home: row {row} gone, the others stay", after == [x for x in before if x != row], after)
        prev, nxt = before[before.index(row) - 1], before[before.index(row) + 1]
        focus_id(prev, ("Down",), 12)
        rpc("Input.Down")
        r.ok(f"Down from row {prev} goes to row {nxt}", wait_for(lambda: lab("System.CurrentControlId") == str(nxt), 3), lab("System.CurrentControlId"))
        shot("homerows-off")
    finally:
        show_all(r)
    r.ok("Home: the row is back", home_groups() == before, home_groups())


@check("all_off")
def c_all_off(r):
    """Every row off: Home says where to turn them back on, and the remote can still reach the tab bar."""
    if not free(r):
        return
    try:
        r.ok("settings page opens", open_settings())
        for row in ROWS:
            if not hidden(row):
                click(TOGGLE[row])
        r.ok("every row switched off", all(hidden(x) for x in ROWS))
        r.ok("Home: no rows", home_groups() == [])
        r.ok("Home: message shown", wait_for(lambda: cond(f"Control.IsVisible({EMPTY_MSG})"), 3), lab(f"Control.GetLabel({EMPTY_MSG})"))
        shot("homerows-alloff")
        for k in ("Up", "Down", "Up"):
            rpc(f"Input.{k}"); time.sleep(0.5)
        r.ok("the tab bar has focus", int(lab("System.CurrentControlId") or 0) in range(10, 21), lab("System.CurrentControlId"))
    finally:
        show_all(r)


@check("custom")
def c_custom(r):
    """Add a row: pick a smart playlist, name it; it shows at the end of Home with the playlist's titles. Rename,
    remove. Cancelling the file browser adds nothing and asks for no name."""
    if not SSH:
        r.skip("needs SSH to put a test playlist on the box")
        return
    if not free(r):
        return
    subprocess.run(["ssh", SSH, f"cat > {PLAYLIST_DIR}{XSP_NAME}"], input=XSP.encode(), capture_output=True, timeout=30, check=True)
    try:
        want = [str(i["id"]) for i in dir_items(f"special://profile/playlists/video/{XSP_NAME}") if i.get("type") == "movie"]
        r.ok("the test playlist lists films", len(want) > 3, len(want))
        r.ok("settings page opens", open_settings())
        r.ok("one Add a row button", cond(f"Control.IsVisible({ADD[0]})") and not any(conds([f"Control.IsVisible({a})" for a in ADD[1:]]).values()))
        # cancel: no path, no name prompt
        click(ADD[0])
        r.ok("Add a row opens the file browser", wait_for(lambda: cond("Window.IsVisible(filebrowser)"), 5))
        for _ in range(6):  # Back climbs to the parent folder first, then closes the browser
            if not cond("Window.IsVisible(filebrowser)"):
                break
            rpc("Input.Back")
            time.sleep(0.8)
        r.ok("file browser cancelled", not cond("Window.IsVisible(filebrowser)"))
        r.ok("cancelled: no name asked", not wait_for(lambda: cond("Window.IsVisible(virtualkeyboard)"), 2.5))
        r.ok("cancelled: nothing added", lab("Skin.String(lumen.row1.path)") == "")
        # pick the playlist, name the row
        click(ADD[0])
        r.ok("file browser again", wait_for(lambda: cond("Window.IsVisible(filebrowser)"), 5))
        r.ok("the playlist is listed", focus_id_label(XSP_NAME), lab("System.CurrentControl"))
        rpc("Input.Select")
        r.ok("a name is asked", wait_for(lambda: cond("Window.IsVisible(virtualkeyboard)"), 6))
        rpc("Input.SendText", {"text": "Check row", "done": True})
        wait_for(lambda: not cond("Window.IsVisible(virtualkeyboard)"), 5)
        r.ok("row 1 set", lab("Skin.String(lumen.row1.path)").endswith(XSP_NAME) and lab("Skin.String(lumen.row1.name)") == "Check row",
             labs(["Skin.String(lumen.row1.path)", "Skin.String(lumen.row1.name)"]))
        r.ok("now Rename and Remove for it, and Add a row for the next", all(conds([f"Control.IsVisible({c})" for c in (RENAME[0], REMOVE[0], ADD[1])]).values())
             and not cond(f"Control.IsVisible({ADD[0]})"))
        shot("homerows-custom-settings")
        groups = home_groups()
        r.ok("Home: the row shows last", groups and groups[-1] == CUSTOM[0], groups)
        got = [lab(f"Container({CUSTOM[0]}).ListItemAbsolute({i}).DBID") for i in range(int(lab(f"Container({CUSTOM[0]}).NumItems") or 0))]
        r.ok("Home: the row lists the playlist's films, in its order", got == want, (got[:6], want[:6]))
        focus_id(CUSTOM[0], ("Down",), 15)
        r.ok("Home: the row can be reached", lab("System.CurrentControlId") == str(CUSTOM[0]), lab("System.CurrentControlId"))
        shot("homerows-custom")
        rpc("Input.Select")
        r.ok("OK on a film opens its detail page", wait_for(lambda: cond("Window.IsVisible(movieinformation)"), 8))
        reset_home()
        # rename
        open_settings()
        click(RENAME[0])
        r.ok("Rename asks for the name", wait_for(lambda: cond("Window.IsVisible(virtualkeyboard)"), 5))
        rpc("Input.SendText", {"text": "Renamed", "done": True})
        wait_for(lambda: not cond("Window.IsVisible(virtualkeyboard)"), 5)
        r.ok("renamed", lab("Skin.String(lumen.row1.name)") == "Renamed", lab("Skin.String(lumen.row1.name)"))
        # kids mode: added rows stay out
        kids_check.kids(True)
        reset_home()
        time.sleep(3)
        r.ok("kids mode: the added row is not on Home", not cond(f"Control.IsVisible({CUSTOM[0] + 1000})"))
        kids_check.kids(False)
        # remove
        open_settings()
        click(REMOVE[0])
        r.ok("removed", lab("Skin.String(lumen.row1.path)") == "" and lab("Skin.String(lumen.row1.name)") == "")
        r.ok("Home: the row is gone", CUSTOM[0] not in home_groups())
    finally:
        kids_check.kids(False)
        show_all(r)
        subprocess.run(["ssh", SSH, f"rm -f {PLAYLIST_DIR}{XSP_NAME}"], capture_output=True, timeout=30)


def focus_id_label(label, maxsteps=40):
    """In the file browser's list, walk to the item labelled label. Up from ".." wraps to the last item (Right would
    leave the list for the dialog's buttons)."""
    for _ in range(maxsteps):
        if lab("Container(450).ListItem.Label") == label:
            return True
        rpc("Input.Up")
        time.sleep(0.4)
    return False


@check("empty_rows")
def c_empty_rows(r):
    """Every built-in row off and the one row left on lists nothing: Home still gives the tab bar focus."""
    if not SSH:
        r.skip("needs SSH to put a test playlist on the box")
        return
    if not free(r):
        return
    nothing = XSP.replace('"greaterthan"><value>2015', '"greaterthan"><value>3000')
    subprocess.run(["ssh", SSH, f"cat > {PLAYLIST_DIR}{XSP_NAME}"], input=nothing.encode(), capture_output=True, timeout=30, check=True)
    try:
        r.ok("settings page opens", open_settings())
        for row in ROWS:
            if not hidden(row):
                click(TOGGLE[row])
        click(ADD[0])
        wait_for(lambda: cond("Window.IsVisible(filebrowser)"), 5)
        r.ok("the playlist is listed", focus_id_label(XSP_NAME), lab("Container(450).ListItem.Label"))
        rpc("Input.Select")
        if wait_for(lambda: cond("Window.IsVisible(virtualkeyboard)"), 6):
            rpc("Input.SendText", {"text": "Nothing", "done": True})
        r.ok("one row on, the empty one", all(hidden(x) for x in ROWS) and lab("Skin.String(lumen.row1.path)").endswith(XSP_NAME))
        reset_home()
        r.ok("Home: no row shown", wait_for(lambda: not any(conds([f"Control.IsVisible({g + 1000})" for g in list(ROWS) + list(CUSTOM)]).values()), 10))
        r.ok("Home: the tab bar gets focus", wait_for(lambda: int(lab("System.CurrentControlId") or 0) in range(10, 21), 6), lab("System.CurrentControlId"))
        rpc("Input.Right"); time.sleep(0.5)
        r.ok("the remote moves along the tab bar", int(lab("System.CurrentControlId") or 0) in range(10, 21), lab("System.CurrentControlId"))
    finally:
        show_all(r)
        subprocess.run(["ssh", SSH, f"rm -f {PLAYLIST_DIR}{XSP_NAME}"], capture_output=True, timeout=30)


@check("kids")
def c_kids(r):
    """Kids mode keeps its own rows whatever is switched off for normal mode."""
    if not free(r):
        return
    try:
        open_settings()
        click(TOGGLE[5003])
        r.ok("Recently Added Movies off", hidden(5003))
        kids_check.kids(True)
        reset_home()
        time.sleep(4)
        vis = conds([f"Control.IsVisible({g})" for g in range(6011, 6016)])
        r.ok("kids mode: kids rows shown", any(vis.values()), vis)
    finally:
        kids_check.kids(False)
        show_all(r)


@check("persist")
def c_persist(r):
    """A switched-off row stays off after Kodi restarts."""
    if not free(r):
        return
    try:
        open_settings()
        click(TOGGLE[5006])
        r.ok("Top Rated off", hidden(5006))
        reset_home()
        r.ok("Kodi restarts", restart_kodi())
        r.ok("still off after the restart", hidden(5006))
        r.ok("Home: still gone", 5006 not in home_groups())
    finally:
        show_all(r)


CHECKS["cleanup"] = kids_check.CHECKS["cleanup"]


if __name__ == "__main__":
    sys.exit(kids_check.main(sys.argv[1:], CHECKS))
