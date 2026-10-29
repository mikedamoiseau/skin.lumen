#!/usr/bin/env python3
"""Quick-filter chip checks (the All / Unwatched and sort chips above the film and show grids) against a Kodi box.

    python3 tools/filters_check.py NAME [NAME ...]   # run the named checks
    python3 tools/filters_check.py all               # every check, then cleanup

Each check prints PASS/FAIL/SKIP lines; the exit status is 1 if anything failed.
Ground truth comes from the library (VideoLibrary.*), never from the skin. Every check puts the grid back the way it
found it: Kodi remembers a grid's sort and the watched filter.
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kids_check  # noqa: E402
from kids_check import KIDS, wait_listing  # noqa: E402
from kodi_gui import SSH, cond, conds, focus_id, lab, labs, reset_home, rpc, shot, wait_for  # noqa: E402

CHECKS = {}
BAR = 6210  # the chip row
ALL, UNWATCHED, TITLE, ADDED, YEAR, RATING = range(6211, 6217)
# chip -> (Kodi sort id, direction it starts in)
SORTS = {TITLE: (29, "ascending"), ADDED: (40, "descending"), YEAR: (16, "descending"), RATING: (17, "descending")}
VIEWS = (500, 503, 50)
MOVIES, SHOWS = "videodb://movies/titles/", "videodb://tvshows/titles/"
# a playlist of watched films only, so Unwatched empties it (written on the box for the empty-grid check, then removed)
TMP_DIR = "/storage/.kodi/temp/lumen-filters-check/"
TMP_XSP = TMP_DIR + "watched.xsp"
WATCHED_XSP = """<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<smartplaylist type="movies"><name>Watched</name><match>all</match>
<rule field="playcount" operator="greaterthan"><value>0</value></rule><order direction="ascending">sorttitle</order></smartplaylist>
"""
TMP_XSP2 = TMP_DIR + "unwatched.xsp"  # emptied by the side menu's Watched mode
UNWATCHED_XSP = WATCHED_XSP.replace("Watched", "Unwatched").replace('"greaterthan"><value>0', '"is"><value>0')


def check(name):
    def register(fn):
        CHECKS[name] = fn
        return fn
    return register


# ---------- the grid ----------

def open_grid(path):
    """Open a grid. GUI.ActivateWindow with "return" can leave the window on the listing it showed before (seen after
    a smart playlist); Container.Update then moves it."""
    rpc("GUI.ActivateWindow", {"window": "videos", "parameters": [path, "return"]})
    if wait_for(lambda: lab("Container.FolderPath") == path, 10, 0.5):
        return wait_listing(path)
    if SSH:
        subprocess.run(["ssh", SSH, f'kodi-send --action="Container.Update({path})"'], capture_output=True, timeout=30)
    return wait_listing(path)


def sort_state():
    """(sort id, direction) of the grid, for the four sorts the chips offer; None for any other sort."""
    c = conds([f"Container.SortMethod({sid})" for sid, _ in SORTS.values()] + ["Container.SortDirection(ascending)"])
    for sid, _ in SORTS.values():
        if c[f"Container.SortMethod({sid})"]:
            return sid, "ascending" if c["Container.SortDirection(ascending)"] else "descending"
    return None


def watch_mode():
    """The window's watched filter as Kodi labels it on the side menu's button: "All videos", "Unwatched" or "Watched"."""
    return lab("Control.GetLabel(10)")


def settled():
    return wait_for(lambda: not cond("Container.IsUpdating"), 15)


def to_bar():
    """Up from the grid's top row reaches the chip row (the grid reopens on the last film picked, so go to the top)."""
    if int(lab("System.CurrentControlId") or 0) in range(ALL, RATING + 1):
        return True
    if int(lab("System.CurrentControlId") or 0) in VIEWS:
        rpc("Input.ExecuteAction", {"action": "firstpage"})
        time.sleep(0.6)
    rpc("Input.Up")
    return wait_for(lambda: int(lab("System.CurrentControlId") or 0) in range(ALL, RATING + 1), 3)


def press(chip):
    """Focus a chip by walking the row, then OK."""
    if not to_bar():
        return False
    cur = int(lab("System.CurrentControlId") or 0)
    if not focus_id(chip, ("Right",) if chip >= cur else ("Left",), 8):
        return False
    rpc("Input.Select")
    time.sleep(0.8)
    return settled()


def restore(state, mode):
    """Put back the grid's sort and watched filter. Only the chips' four sorts can be restored this way."""
    if mode == "All videos":
        press(ALL)
    elif mode == "Unwatched":
        press(UNWATCHED)
    if state and sort_state() != state:
        chip = next(c for c, (sid, _) in SORTS.items() if sid == state[0])
        press(chip)
        if sort_state() != state:
            press(chip)  # the chip flips an active sort
    return sort_state() == state and watch_mode() == mode


def first_items(n=40):
    names = [f"Container.ListItemAbsolute({i}).{f}" for i in range(n) for f in ("DBID", "DBType")]
    v = labs(names)
    return [(v[f"Container.ListItemAbsolute({i}).DBType"], v[f"Container.ListItemAbsolute({i}).DBID"]) for i in range(n)
            if v[f"Container.ListItemAbsolute({i}).DBID"]]


def library(kind):
    """{id: {dateadded, year, rating, playcount}} for every film or show."""
    method, key, idk = (("VideoLibrary.GetMovies", "movies", "movieid") if kind == "movie" else
                        ("VideoLibrary.GetTVShows", "tvshows", "tvshowid"))
    r = rpc(method, {"properties": ["dateadded", "year", "rating", "playcount"]}, timeout=120)["result"].get(key, [])
    return {str(m[idk]): m for m in r}


def ordered(values, direction):
    return all((a >= b) if direction == "descending" else (a <= b) for a, b in zip(values, values[1:]))


# ---------- checks ----------

def restorable(r):
    """The grid's sort and watched filter, if the chips can put them back afterwards; else skip (None)."""
    s = (sort_state(), watch_mode())
    if s[0] is None or s[1] not in ("All videos", "Unwatched"):
        r.skip(f"the grid has a sort or filter the chips can't put back ({lab('Container.SortMethod')}, {s[1]}); not touching it")
        return None
    return s


def start(r, path):
    """Normal mode, nothing playing, the grid open. Returns its (sort, watched filter) to restore, or None."""
    if rpc("Player.GetActivePlayers")["result"]:
        r.skip("something is playing on the box; not touching it")
        return None
    kids_check.kids(False)
    reset_home()
    if not r.ok(f"{path} opens", open_grid(path)):
        return None
    return restorable(r)


@check("bar")
def c_bar(r):
    """The chip row shows above the films grid instead of the sort caption; Up reaches it, Down goes back."""
    s = start(r, MOVIES)
    if not s:
        return
    try:
        view = int(lab("System.CurrentControlId") or 0)
        r.ok("a grid view has focus", view in VIEWS, view)
        r.ok("chip row shown", cond(f"Control.IsVisible({BAR})"))
        r.ok("Up from the first row reaches a chip", to_bar(), lab("System.CurrentControlId"))
        shot("filters-bar")
        rpc("Input.Down")
        r.ok("Down goes back to the grid", wait_for(lambda: lab("System.CurrentControlId") == str(view), 3), lab("System.CurrentControlId"))
        to_bar()
        rpc("Input.Back")
        r.ok("Back goes back to the grid", wait_for(lambda: lab("System.CurrentControlId") == str(view), 3), lab("System.CurrentControlId"))
        to_bar()
        for _ in range(8):
            rpc("Input.Left"); time.sleep(0.3)
        r.ok("Left past the first chip opens the side menu", cond("ControlGroup(9000).HasFocus"), lab("System.CurrentControlId"))
    finally:
        reset_home()


@check("sort")
def c_sort(r):
    """Each sort chip sorts the films its way (newest, highest first; titles A to Z), whatever direction Kodi last
    gave that sort; pressing the chip again turns the order round."""
    s = start(r, MOVIES)
    if not s:
        return
    lib = library("movie")
    field = {ADDED: "dateadded", YEAR: "year", RATING: "rating"}
    try:
        for chip in (ADDED, YEAR, RATING, TITLE, ADDED):  # Date added twice: the second time it starts from where it was left
            sid, want = SORTS[chip]
            r.ok(f"chip {chip} pressed", press(chip))
            r.ok(f"chip {chip}: sort {sid} {want}", sort_state() == (sid, want), sort_state())
            arrow = "\u2193" if want == "descending" else "\u2191"
            r.ok(f"chip {chip}: its label shows the direction", lab(f"Control.GetLabel({chip})").endswith(arrow), lab(f"Control.GetLabel({chip})"))
            if chip in field:
                vals = [lib[i][field[chip]] for t, i in first_items() if t == "movie" and i in lib]
                r.ok(f"chip {chip}: the first films follow the library's {field[chip]}", len(vals) > 5 and ordered(vals, want), vals[:6])
            if chip == ADDED:
                flip = "ascending" if want == "descending" else "descending"
                press(chip)
                r.ok("same chip again turns the order round", sort_state() == (sid, flip), sort_state())
                r.ok("and its arrow", lab(f"Control.GetLabel({chip})").endswith("\u2193" if flip == "descending" else "\u2191"), lab(f"Control.GetLabel({chip})"))
        shot("filters-sort")
    finally:
        r.ok("sort and filter put back", restore(*s), (s, sort_state(), watch_mode()))
        reset_home()


@check("watched")
def c_watched(r):
    """Unwatched lists only unwatched films and All brings the rest back, from either side menu state (Kodi's button
    also has a Watched mode)."""
    s = start(r, MOVIES)
    if not s:
        return
    lib = library("movie")
    try:
        press(ALL)
        total = int(lab("Container.NumItems") or 0)
        r.ok("All: the watched filter is off", watch_mode() == "All videos" and not cond("System.Setting(hidewatched)"), watch_mode())
        r.ok("Unwatched pressed", press(UNWATCHED))
        r.ok("Unwatched: Kodi hides watched films", cond("System.Setting(hidewatched)"), watch_mode())
        seen = [lib[i]["playcount"] for t, i in first_items(60) if t == "movie" and i in lib]
        r.ok("Unwatched: the films listed are unwatched", seen and not any(seen), seen[:10])
        if any(m["playcount"] for m in lib.values()):
            r.ok("Unwatched: fewer items", int(lab("Container.NumItems") or 0) < total, (lab("Container.NumItems"), total))
        shot("filters-unwatched")
        r.ok("All pressed", press(ALL))
        r.ok("All: every item back", int(lab("Container.NumItems") or 0) == total and watch_mode() == "All videos", lab("Container.NumItems"))
        # Kodi's own button (side menu) cycles All -> Unwatched -> Watched; from Watched, Unwatched still takes one press
        if SSH:
            for _ in range(2):
                subprocess.run(["ssh", SSH, 'kodi-send --action="SendClick(10)"'], capture_output=True, timeout=30)
                time.sleep(1); settled()
            r.ok("side menu button: Watched mode", watch_mode() == "Watched", watch_mode())
            shot("filters-watchedmode")  # neither chip lit
            press(UNWATCHED)
            r.ok("Unwatched from Watched: one press", watch_mode() == "Unwatched" and cond("System.Setting(hidewatched)"), watch_mode())
    finally:
        r.ok("sort and filter put back", restore(*s), (s, sort_state(), watch_mode()))
        reset_home()


@check("shows")
def c_shows(r):
    """The TV shows grid has the same row; Date added sorts the shows newest first."""
    s = start(r, SHOWS)
    if not s:
        return
    lib = library("tvshow")
    try:
        r.ok("chip row shown", cond(f"Control.IsVisible({BAR})"))
        r.ok("Date added pressed", press(ADDED))
        r.ok("sort 40 descending", sort_state() == (40, "descending"), sort_state())
        vals = [lib[i]["dateadded"] for t, i in first_items() if t == "tvshow" and i in lib]
        r.ok("the first shows follow the library's dateadded", len(vals) > 5 and ordered(vals, "descending"), vals[:6])
    finally:
        r.ok("sort and filter put back", restore(*s), (s, sort_state(), watch_mode()))
        reset_home()


@check("kids")
def c_kids(r):
    """Kids mode: the kids films grid has the row too; it sorts and filters kids films only, and Left never reaches the
    side menu."""
    if rpc("Player.GetActivePlayers")["result"]:
        r.skip("something is playing on the box; not touching it")
        return
    path = KIDS + "movies.xsp"
    kids_check.kids(True)
    try:
        reset_home()
        if not r.ok("kids films open", open_grid(path)):
            return
        s = restorable(r)
        if not s:
            return
        tagged = {str(i) for i in kids_check.tagged_movies()}
        try:
            r.ok("chip row shown", cond(f"Control.IsVisible({BAR})"))
            r.ok("Year pressed", press(YEAR))
            r.ok("sort 16 descending", sort_state() == (16, "descending"), sort_state())
            listed = {i for t, i in first_items() if t == "movie"}
            r.ok("only kids films listed", listed and listed <= tagged, sorted(listed - tagged)[:5])
            r.ok("Unwatched pressed", press(UNWATCHED))
            listed = {i for t, i in first_items() if t == "movie"}
            r.ok("Unwatched: still only kids films", (listed or int(lab("Container.NumItems") or 0) == 0) and listed <= tagged,
                 sorted(listed - tagged)[:5])
            to_bar()
            for _ in range(8):
                rpc("Input.Left"); time.sleep(0.3)
            r.ok("Left never opens the side menu", not cond("ControlGroup(9000).HasFocus"), lab("System.CurrentControlId"))
        finally:
            r.ok("sort and filter put back", restore(*s), (s, sort_state(), watch_mode()))
    finally:
        kids_check.kids(False)
        reset_home()


@check("empty")
def c_empty(r):
    """Unwatched on a grid where everything is watched: a message, nothing to select, and Up still reaches the chips
    to go back to All (normal and kids mode)."""
    if not SSH:
        r.skip("needs SSH to put a test playlist on the box")
        return
    if rpc("Player.GetActivePlayers")["result"]:
        r.skip("something is playing on the box; not touching it")
        return
    subprocess.run(["ssh", SSH, f"mkdir -p {TMP_DIR} && cat > {TMP_XSP}"], input=WATCHED_XSP.encode(), capture_output=True, timeout=30, check=True)
    subprocess.run(["ssh", SSH, f"cat > {TMP_XSP2}"], input=UNWATCHED_XSP.encode(), capture_output=True, timeout=30, check=True)
    try:
        for on in (False, True):
            kids_check.kids(on)
            reset_home()
            if not r.ok(f"kids={on}: watched films open", open_grid(TMP_XSP)):
                continue
            s = restorable(r)
            if not s:
                continue
            try:
                r.ok(f"kids={on}: Unwatched pressed", press(UNWATCHED))
                r.ok(f"kids={on}: grid empty", int(lab("Container.NumItems") or 0) == 0, lab("Container.NumItems"))
                r.ok(f"kids={on}: message shown", wait_for(lambda: cond("Control.IsVisible(6060)"), 3)
                     and "unwatched" in lab("Control.GetLabel(6060)").lower(), lab("Control.GetLabel(6060)"))
                r.ok(f"kids={on}: chip row still shown", cond(f"Control.IsVisible({BAR})"))
                shot(f"filters-empty-{'kids' if on else 'normal'}")
                rpc("Input.Down"); time.sleep(0.5)
                r.ok(f"kids={on}: Down lands on the empty-grid button", lab("System.CurrentControlId") == "6061", lab("System.CurrentControlId"))
                rpc("Input.Select"); time.sleep(1)
                r.ok(f"kids={on}: OK there does nothing", lab("Container.FolderPath") == TMP_XSP, lab("Container.FolderPath"))
                r.ok(f"kids={on}: Up reaches the chips", to_bar(), lab("System.CurrentControlId"))
                if not on:  # the side menu, reached from the chips, comes back to the empty-grid button
                    for _ in range(8):
                        rpc("Input.Left"); time.sleep(0.3)
                    r.ok("side menu open", cond("ControlGroup(9000).HasFocus"), lab("System.CurrentControlId"))
                    rpc("Input.Right"); time.sleep(0.6)
                    r.ok("Right from the side menu: the empty-grid button", lab("System.CurrentControlId") == "6061", lab("System.CurrentControlId"))
                    to_bar()
                    for _ in range(8):
                        rpc("Input.Left"); time.sleep(0.3)
                    rpc("Input.Back"); time.sleep(0.8)
                    r.ok("Back from the side menu: the empty-grid button, still in the grid",
                         lab("System.CurrentControlId") == "6061" and lab("Container.FolderPath") == TMP_XSP,
                         (lab("System.CurrentControlId"), lab("Container.FolderPath")))
            finally:
                r.ok(f"kids={on}: filter put back", restore(*s), (s, sort_state(), watch_mode()))
            # the side menu's Watched mode (a parent's, say) empties a grid of unwatched films: the chips stay, All works
            reset_home()
            if not r.ok(f"kids={on}: unwatched films open", open_grid(TMP_XSP2)):
                continue
            for _ in range(2):  # All -> Unwatched -> Watched
                subprocess.run(["ssh", SSH, 'kodi-send --action="SendClick(10)"'], capture_output=True, timeout=30)
                time.sleep(1); settled()
            try:
                r.ok(f"kids={on}: Watched mode", watch_mode() == "Watched", watch_mode())
                r.ok(f"kids={on}: Watched: grid empty", int(lab("Container.NumItems") or 0) == 0, lab("Container.NumItems"))
                r.ok(f"kids={on}: Watched: message", wait_for(lambda: cond("Control.IsVisible(6060)"), 3)
                     and lab("Control.GetLabel(6060)") == "Nothing watched here.", lab("Control.GetLabel(6060)"))
                r.ok(f"kids={on}: Watched: chip row still shown", cond(f"Control.IsVisible({BAR})"))
                r.ok(f"kids={on}: Watched: Up reaches the chips", to_bar(), lab("System.CurrentControlId"))
                press(ALL)
                r.ok(f"kids={on}: All brings the films back", watch_mode() == "All videos" and int(lab("Container.NumItems") or 0) > 0,
                     (watch_mode(), lab("Container.NumItems")))
            finally:
                if watch_mode() != "All videos":
                    press(ALL)
    finally:
        kids_check.kids(False)
        reset_home()
        subprocess.run(["ssh", SSH, f"rm -f {TMP_XSP} {TMP_XSP2} && rmdir {TMP_DIR}"], capture_output=True, timeout=30)


@check("elsewhere")
def c_elsewhere(r):
    """No chip row where the chips can't work: Collections, a collection's films, recently added."""
    if rpc("Player.GetActivePlayers")["result"]:
        r.skip("something is playing on the box; not touching it")
        return
    kids_check.kids(False)
    sets = rpc("VideoLibrary.GetMovieSets")["result"].get("sets", [])
    paths = ["videodb://movies/sets/", "videodb://recentlyaddedmovies/"] + ([f"videodb://movies/sets/{sets[0]['setid']}/"] if sets else [])
    for p in paths:
        reset_home()
        if r.ok(f"{p} opens", open_grid(p)):
            r.ok(f"{p}: no chip row", not cond(f"Control.IsVisible({BAR})"))
    reset_home()


CHECKS["cleanup"] = kids_check.CHECKS["cleanup"]


if __name__ == "__main__":
    sys.exit(kids_check.main(sys.argv[1:], CHECKS))
