#!/usr/bin/env python3
"""Kids-mode checks against the Android TV box over JSON-RPC.

    python3 tools/kids_check.py NAME [NAME ...]   # run the named checks
    python3 tools/kids_check.py all               # every check except baseline, then cleanup

Each check prints PASS/FAIL/SKIP lines; the exit status is 1 if anything failed.
Ground truth comes from the library itself (VideoLibrary.* with a tag filter), never from the skin.
"""
import json
import os
import sys
import time
import traceback
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kodi_gui import ROOT, cond, conds, film_state, focus_id, keepalive, lab, labs, reset_home, restart_kodi, restore_films, rpc, send_number, set_skin, shot, wait_for  # noqa: E402

TAG = "Cinematheque Kids"
KIDS_SETTING = "cinemathequekids"  # Kodi stores skin bools lower-cased; JSON-RPC ids are case-sensitive
KIDS = "special://skin/playlists/kids/"
# kids Home "Continue watching": films and shows in progress in one list (same string in build_home.py)
KIDS_CONTINUE = ("multipath://special%3a%2f%2fskin%2fplaylists%2fkids%2finprogress_movies.xsp/"
                 "special%3a%2f%2fskin%2fplaylists%2fkids%2finprogress_tvshows.xsp/")
BASELINE = os.environ.get("KIDS_BASELINE", os.path.join(ROOT, "shots", "baseline.json"))  # one per box
NORMAL_GROUPS = range(6001, 6009)
NORMAL_ROWS = range(5001, 5009)
TABS = range(10, 19)

CHECKS = {}
PIN_BEFORE = ""  # the kids PIN when the run started (main); cleanup puts it back


def check(name):
    def register(fn):
        CHECKS[name] = fn
        return fn
    return register


class Report:
    def __init__(self, name):
        self.name, self.failed = name, False

    def ok(self, label, passed, detail=""):
        self.failed |= not passed
        print(f"{'PASS' if passed else 'FAIL'} {self.name}: {label}{' — ' + str(detail) if detail != '' else ''}", flush=True)
        return passed

    def skip(self, reason):
        print(f"SKIP {self.name}: {reason}", flush=True)


# ---------- ground truth ----------

def _tag_filter(tag):
    return {"field": "tag", "operator": "is", "value": tag}


def tagged_movies():
    r = rpc("VideoLibrary.GetMovies", {"filter": _tag_filter(TAG)}, timeout=60)["result"]
    return {m["movieid"] for m in r.get("movies", [])}


def tagged_shows():
    r = rpc("VideoLibrary.GetTVShows", {"filter": _tag_filter(TAG)}, timeout=60)["result"]
    return {s["tvshowid"] for s in r.get("tvshows", [])}


def keyword_kids_untagged():
    """Films carrying the scraper keyword "kids" but not the app's tag: they must never show."""
    r = rpc("VideoLibrary.GetMovies", {"filter": _tag_filter("kids")}, timeout=60)["result"]
    return {m["movieid"] for m in r.get("movies", [])} - tagged_movies()


def dir_items(path, props=()):
    """Files.GetDirectory items with an int "id" and a "type" (movie/tvshow/episode/...)."""
    r = rpc("Files.GetDirectory", {"directory": path, "media": "video", "properties": list(props)}, timeout=90)
    if "error" in r:
        raise RuntimeError(f"Files.GetDirectory({path}): {r['error']}")
    items = r["result"].get("files") or []
    for it in items:
        if not it.get("id"):
            tail = it.get("file", "").rstrip("/").split("?")[0].rsplit("/", 1)[-1]
            it["id"] = int(tail) if tail.isdigit() else None
    return items


def list_items(cid, fields):
    """Every item of container cid (None: the window's main container), as dicts of ListItem fields."""
    c = f"Container({cid})" if cid else "Container"
    n = int(lab(f"{c}.NumItems") or 0)
    names = [f"{c}.ListItemAbsolute({i}).{f}" for i in range(n) for f in fields]
    values = labs(names) if names else {}
    return [{f: values[f"{c}.ListItemAbsolute({i}).{f}"] for f in fields} for i in range(n)]


def settings_ok():
    """Changing Kodi settings on the box needs the user's OK; they give it with KIDS_OK_SETTINGS=1."""
    return os.environ.get("KIDS_OK_SETTINGS") == "1"


def wait_listing(path, timeout=40):
    """Wait until the media window shows path and has finished loading."""
    return wait_for(lambda: lab("Container.FolderPath") == path and not cond("Container.IsUpdating")
                    and int(lab("Container.NumItems") or 0) > 0, timeout, 0.5)


def tab_bar(target):
    """From Home, move up into the tab bar (Home remembers the last row) and onto control target."""
    for _ in range(10):
        if int(lab("System.CurrentControlId") or 0) in range(10, 21):
            break
        rpc("Input.Up")
        time.sleep(0.5)
    else:
        return False
    return focus_id(target, ("Right",) if target >= int(lab("System.CurrentControlId") or 0) else ("Left",), 15)


def home_state():
    reset_home()
    time.sleep(10)
    vis = conds([f"Control.IsVisible({g})" for g in NORMAL_GROUPS] + [f"Control.IsVisible({t})" for t in TABS])
    counts = labs([f"Container({r}).NumItems" for r in NORMAL_ROWS])
    return {
        "groups": [g for g in NORMAL_GROUPS if vis[f"Control.IsVisible({g})"]],
        "tabs": [t for t in TABS if vis[f"Control.IsVisible({t})"]],
        "counts": {str(r): counts[f"Container({r}).NumItems"] for r in NORMAL_ROWS},
    }


def load_baseline():
    with open(BASELINE) as f:
        return json.load(f)


# ---------- checks ----------

@check("baseline")
def c_baseline(r):
    if os.path.exists(BASELINE):
        r.ok("baseline not overwritten", False, f"{BASELINE} exists; it must come from the unchanged skin")
        return
    state = home_state()
    state["showparentdiritems"] = rpc("Settings.GetSettingValue", {"setting": "filelists.showparentdiritems"})["result"]["value"]
    os.makedirs(os.path.dirname(BASELINE), exist_ok=True)
    with open(BASELINE, "w") as f:
        json.dump(state, f, indent=1)
    r.ok("baseline recorded", bool(state["groups"]), state)


@check("playlists")
def c_playlists(r):
    movies, shows, keyword = tagged_movies(), tagged_shows(), keyword_kids_untagged()
    r.ok("ground truth has tagged titles", bool(movies) and bool(shows), f"{len(movies)} films, {len(shows)} shows")

    def ids(path, props=()):
        try:
            items = dir_items(path, props)
        except RuntimeError as e:
            r.ok(f"{path} lists", False, e)
            return None, []
        return {i["id"] for i in items}, items

    got, _ = ids(KIDS + "movies.xsp")
    if got is not None:
        r.ok("movies.xsp == tagged films", got == movies, f"{len(got)} vs {len(movies)}; extra {sorted(got - movies)[:5]}, missing {sorted(movies - got)[:5]}")
        r.ok("movies.xsp has no keyword-only 'kids' film", not (got & keyword), f"{len(keyword)} keyword-only films checked")
    got, _ = ids(KIDS + "tvshows.xsp")
    if got is not None:
        r.ok("tvshows.xsp == tagged shows", got == shows, f"{len(got)} vs {len(shows)}")
    got, items = ids(KIDS + "recent_movies.xsp")
    if got is not None:
        # Files.GetDirectory re-sorts by label, so compare the set; the order is checked on the Home row (home_kids)
        newest = rpc("VideoLibrary.GetMovies", {"filter": _tag_filter(TAG), "sort": {"method": "dateadded", "order": "descending"},
                                                "limits": {"start": 0, "end": 25}}, timeout=60)["result"]["movies"]
        r.ok("recent_movies.xsp == the 25 newest tagged films", got == {m["movieid"] for m in newest}, f"{len(items)} items")
    got, items = ids(KIDS + "discover.xsp")
    if got is not None:
        r.ok("discover.xsp: <=25 tagged", 0 < len(items) <= 25 and got <= movies, f"{len(items)} items")
    got, items = ids(KIDS + "new_episodes.xsp", ["tvshowid", "playcount"])
    if got is not None:
        r.ok("new_episodes.xsp: <=15 unwatched episodes of tagged shows",
             len(items) <= 15 and all(i["tvshowid"] in shows and i["playcount"] == 0 for i in items), f"{len(items)} items")
    m, _ = ids(KIDS + "inprogress_movies.xsp")
    if m is not None:
        r.ok("inprogress_movies.xsp ⊆ tagged films", m <= movies, f"{len(m)} items")
    s, _ = ids(KIDS + "inprogress_tvshows.xsp")
    if s is not None:
        r.ok("inprogress_tvshows.xsp ⊆ tagged shows", s <= shows, f"{len(s)} items")
    # KIDS_CONTINUE (multipath://) can't be listed over JSON-RPC (FileOperations.cpp:79 only allows known path types);
    # home_kids checks it where it is used, on Home row 5011


KIDS_ROWS = range(5011, 5016)
KIDS_GROUPS = range(6011, 6016)


def ensure_settings():
    """Kodi creates a skin setting the first time a skin action sets it, and JSON-RPC only sees the settings that
    existed when the skin loaded. Create lumen.pin and CinemathequeKids once through the real UI (Set kids PIN,
    then the kids button) and restart Kodi so JSON-RPC can change them."""
    known = lambda sid: "error" not in rpc("Settings.GetSkinSettingValue", {"setting": sid})
    if known("lumen.pin") and known(KIDS_SETTING):
        return
    if not known("lumen.pin"):
        settings_button(902)
        send_number("2468")
        wait_for(lambda: lab("Window(home).Property(lumen.pinprompt)") == "again", 5)
        send_number("2468")
        wait_for(lambda: not cond("Window.IsVisible(1113)"), 5)
    if not known(KIDS_SETTING):
        if lab("Skin.String(lumen.pin)") == "":
            set_skin("lumen.pin", "2468")
        kids_button()
        wait_for(lambda: cond("Skin.HasSetting(CinemathequeKids)"), 5)
    restart_kodi()
    set_skin(KIDS_SETTING, False)
    set_skin("lumen.pin", "")
    reset_home()


def kids(on):
    ensure_settings()
    set_skin(KIDS_SETTING, on)


def pin(value):
    ensure_settings()
    set_skin("lumen.pin", value)


@check("home_normal")
def c_home_normal(r):
    try:
        kids(False)
    except RuntimeError:
        pass  # a skin that doesn't know the setting is in normal mode anyway
    # a hidden list keeps what it loaded, so prove "never loaded" from a fresh start in normal mode
    r.ok("fresh Kodi in normal mode", restart_kodi())
    base, state = load_baseline(), home_state()
    r.ok("normal row groups as before", state["groups"] == base["groups"], f"{state['groups']} vs {base['groups']}")
    r.ok("tabs 10-18 as before", state["tabs"] == base["tabs"], f"{state['tabs']} vs {base['tabs']}")
    counts = labs([f"Container({i}).NumItems" for i in KIDS_ROWS])
    r.ok("kids lists never loaded", all(v in ("", "0") for v in counts.values()), counts)
    # a list that ran its query reports IsUpdating until it is shown (CDirectoryProvider::IsUpdating)
    busy = conds([f"Container({i}).IsUpdating" for i in KIDS_ROWS])
    r.ok("kids rows never queried", not any(busy.values()), [k for k, v in busy.items() if v])


@check("home_kids")
def c_home_kids(r):
    movies, shows = tagged_movies(), tagged_shows()
    kids(True)
    reset_home()
    time.sleep(15)
    vis = conds([f"Control.IsVisible({g})" for g in list(NORMAL_GROUPS) + list(KIDS_GROUPS) + [6019]])
    r.ok("normal rows hidden", not any(vis[f"Control.IsVisible({g})"] for g in NORMAL_GROUPS))
    r.ok("kids rows shown", any(vis[f"Control.IsVisible({g})"] for g in KIDS_GROUPS), [g for g in KIDS_GROUPS if vis[f"Control.IsVisible({g})"]])
    r.ok("empty-tag message hidden", not vis["Control.IsVisible(6019)"])
    cw = [(i["DBType"], int(i["DBID"])) for i in list_items(5011, ["DBType", "DBID"])]
    # ISO dates from the library (ListItem.LastPlayed is date-only, in the box's regional format)
    played = {("movie", i["id"]): i["lastplayed"] for i in dir_items(KIDS + "inprogress_movies.xsp", ["lastplayed"])}
    played.update({("tvshow", i["id"]): i["lastplayed"] for i in dir_items(KIDS + "inprogress_tvshows.xsp", ["lastplayed"])})
    r.ok("5011 continue: exactly the tagged films and shows in progress", set(cw) == set(played), f"{len(cw)} items vs {len(played)}")
    order = [played.get(k, "") for k in cw]
    r.ok("5011 continue: most recently played first", order == sorted(order, reverse=True), order[:4])
    eps = list_items(5012, ["TvShowDBID"])
    r.ok("5012 new episodes: tagged shows only", all(int(i["TvShowDBID"]) in shows for i in eps), f"{len(eps)} items")
    when = {i["id"]: i["dateadded"] for i in dir_items(KIDS + "recent_movies.xsp", ["dateadded"])}
    added = [when.get(int(i["DBID"]), "") for i in list_items(5013, ["DBID"])]
    r.ok("5013 recently added: newest first", added == sorted(added, reverse=True), added[:3])
    when = {i["id"]: i["dateadded"] for i in dir_items(KIDS + "new_episodes.xsp", ["dateadded"])}
    added = [when.get(int(i["DBID"]), "") for i in list_items(5012, ["DBID"])]
    r.ok("5012 new episodes: newest first", added == sorted(added, reverse=True), added[:3])
    for cid, pool in ((5013, movies), (5014, shows), (5015, movies)):
        items = list_items(cid, ["DBID"])
        r.ok(f"{cid}: tagged only", all(i["DBID"] and int(i["DBID"]) in pool for i in items), f"{len(items)} items")
    rpc("Input.Up"); time.sleep(0.6); rpc("Input.Down"); time.sleep(0.8)
    r.ok("Down from the tab bar lands on a kids row", lab("System.CurrentControlId") in [str(i) for i in KIDS_ROWS], lab("System.CurrentControlId"))
    r.ok("app's read: Skin.HasSetting(CinemathequeKids)", cond("Skin.HasSetting(CinemathequeKids)"))
    kids(False)
    r.ok("normal rows back after leaving", home_state()["groups"] == load_baseline()["groups"])


@check("home_switch")
def c_home_switch(r):
    kids(False)
    reset_home()
    time.sleep(8)
    r.ok("focus a normal row (5003)", focus_id(5003, ("Down",), 10), lab("System.CurrentControlId"))
    time.sleep(1)
    r.ok("its hero shows (check can see heroes)", cond("Control.IsVisible(7003)"))
    kids(True)
    time.sleep(3)
    shot("switch_kids")
    heroes = conds([f"Control.IsVisible({7000 + i - 5000})" for i in NORMAL_ROWS])
    r.ok("no normal-row hero in kids mode", not any(heroes.values()), [k for k, v in heroes.items() if v])
    kids(False)


@check("tabs")
def c_tabs(r):
    movies, shows = tagged_movies(), tagged_shows()
    kids(False)
    pin("")
    reset_home()
    time.sleep(3)
    r.ok("no PIN: no kids button", not cond("Control.IsVisible(20)"))
    pin("2468")
    time.sleep(1)
    vis = conds([f"Control.IsVisible({t})" for t in TABS] + ["Control.IsVisible(20)"])
    r.ok("PIN set: kids button shows", vis["Control.IsVisible(20)"])
    r.ok("tabs 10-18 as before", [t for t in TABS if vis[f"Control.IsVisible({t})"]] == load_baseline()["tabs"])
    r.ok("focus the kids button", tab_bar(20), lab("System.CurrentControlId"))
    rpc("Input.Select")
    time.sleep(1.5)
    r.ok("kids button enters kids mode", cond("Skin.HasSetting(CinemathequeKids)"))
    r.ok("Home re-opens on the kids rows", wait_for(lambda: lab("System.CurrentWindow") == "Home"
                                                    and lab("System.CurrentControlId") in [str(i) for i in KIDS_ROWS], 8),
         lab("System.CurrentControlId"))
    r.ok("kids rows load", wait_for(lambda: int(lab("Container(5013).NumItems") or 0) > 0 and int(lab("Container(5014).NumItems") or 0) > 0, 30),
         labs(["Container(5013).NumItems", "Container(5014).NumItems"]))
    vis = conds([f"Control.IsVisible({t})" for t in (10, 11, 12, 13, 14, 15, 16, 17, 18)])
    r.ok("kids tab bar: Home, Movies, TV Shows, Search", [t for t in TABS if vis[f"Control.IsVisible({t})"]] == [10, 11, 12, 16],
         [t for t in TABS if vis[f"Control.IsVisible({t})"]])
    for tab, path, pool in ((11, KIDS + "movies.xsp", movies), (12, KIDS + "tvshows.xsp", shows)):
        r.ok(f"focus tab {tab}", tab_bar(tab), lab("System.CurrentControlId"))
        rpc("Input.Select")
        r.ok(f"tab {tab} opens {path}", wait_listing(path), lab("Container.FolderPath"))
        items = list_items(None, ["DBID", "DBType"])
        r.ok(f"tab {tab}: every tagged title, nothing else", {int(i["DBID"]) for i in items if i["DBID"]} == pool and len(items) == len(pool),
             f"{len(items)} items vs {len(pool)}")
        r.ok(f"tab {tab}: no collections", not any(i["DBType"] == "set" for i in items))
        rpc("Input.Back")
        wait_for(lambda: lab("System.CurrentWindow") == "Home", 8)
        tab_bar(20)


@check("tabs_grouping")
def c_tabs_grouping(r):
    if not settings_ok():
        r.skip("awaiting the user's OK to turn on videolibrary.groupmoviesets on the box (KIDS_OK_SETTINGS=1)")
        return
    movies = tagged_movies()
    old = rpc("Settings.GetSettingValue", {"setting": "videolibrary.groupmoviesets"})["result"]["value"]
    try:
        rpc("Settings.SetSettingValue", {"setting": "videolibrary.groupmoviesets", "value": True})
        kids(True)
        rpc("GUI.ActivateWindow", {"window": "videos", "parameters": [KIDS + "movies.xsp", "return"]})
        r.ok("kids films listed", wait_listing(KIDS + "movies.xsp"))
        items = list_items(None, ["DBID", "DBType"])
        got = {int(i["DBID"]) for i in items if i["DBID"] and i["DBType"] == "movie"}
        r.ok("grouping on: every tagged film, one by one, and nothing else",
             got == movies and len(items) == len(movies) and not any(i["DBType"] == "set" for i in items), f"{len(items)} items vs {len(movies)}")
    finally:
        rpc("Settings.SetSettingValue", {"setting": "videolibrary.groupmoviesets", "value": old})
        kids(False)
        reset_home()


@check("tabs_parentdir")
def c_tabs_parentdir(r):
    if not settings_ok():
        r.skip("awaiting the user's OK to turn on filelists.showparentdiritems on the box (KIDS_OK_SETTINGS=1)")
        return
    old = rpc("Settings.GetSettingValue", {"setting": "filelists.showparentdiritems"})["result"]["value"]
    try:
        rpc("Settings.SetSettingValue", {"setting": "filelists.showparentdiritems", "value": True})
        kids(False)
        set_skin("lumen.pin", "2468")
        reset_home()
        time.sleep(3)
        r.ok("focus the kids button", tab_bar(20))
        rpc("Input.Select")
        r.ok("a message explains why", wait_for(lambda: cond("Window.IsVisible(notification)"), 3))
        r.ok("kids mode stays off", not cond("Skin.HasSetting(CinemathequeKids)"))
    finally:
        rpc("Settings.SetSettingValue", {"setting": "filelists.showparentdiritems", "value": old})
        set_skin("lumen.pin", "")


@check("parentdir_key")
def c_parentdir_key(r):
    """A Harmony remote's Prev sends ParentDir (customcontroller.Harmony.xml). Kids grids open with "return", so Kodi
    keeps no parent history (GUIMediaWindow): ParentDir lands on Home, never on the library above."""
    kids(True)
    xsp = {"rules": {"and": [{"field": "tag", "operator": "is", "value": [TAG]}]}, "type": "movies"}
    genre_grid = "videodb://movies/videoversions/0/?xsp=" + urllib.parse.quote(json.dumps(xsp, separators=(",", ":")), safe="")
    for label, params in (("kids films", [KIDS + "movies.xsp", "return"]), ("kids shows", [KIDS + "tvshows.xsp", "return"]),
                          ("a kids Genre/Decade grid", [genre_grid, "return", "replace"])):
        reset_home()
        rpc("GUI.ActivateWindow", {"window": "videos", "parameters": params})
        if not r.ok(f"{label} listed", wait_for(lambda: cond("Window.IsActive(videos)") and not cond("Container.IsUpdating")
                                                 and int(lab("Container.NumItems") or 0) > 0, 30)):
            continue
        rpc("Input.ExecuteAction", {"action": "parentdir"})
        r.ok(f"{label}: ParentDir goes Home", wait_for(lambda: lab("System.CurrentWindow") == "Home", 5),
             f"{lab('System.CurrentWindow')} {lab('Container.FolderPath')[:60]}")
    kids(False)
    reset_home()


@check("discover_random")
def c_discover_random(r):
    """Kids Discover shows 25 random kids films, in random order (a grouped smart playlist would come A to Z)."""
    import re
    key = lambda t: re.sub(r"^(the|a|an) ", "", t.lower())
    kids(True)
    reset_home()
    r.ok("kids Discover loads", wait_for(lambda: int(lab("Container(5015).NumItems") or 0) >= 10 and not cond("Container(5015).IsUpdating"), 30))
    labels = [i["Label"] for i in list_items(5015, ["Label"])]
    r.ok("kids Discover isn't A to Z", labels != sorted(labels, key=key), labels[:5])
    kids(False)
    reset_home()


def settings_button(bid):
    """Open Lumen's skin settings and press button bid."""
    reset_home()
    rpc("GUI.ActivateWindow", {"window": "skinsettings"})
    if not wait_for(lambda: cond("Window.IsActive(skinsettings)"), 8) or not focus_id(bid, ("Down",), 10):
        return False
    rpc("Input.Select")
    return True


def kids_button():
    """On Home, press the kids/lock button."""
    reset_home()
    time.sleep(2)
    if not tab_bar(20):
        return False
    rpc("Input.Select")
    return True


def pin_result(timeout=3):
    """Wait for the PIN result dialog and return its message key ('' if it never showed one)."""
    wait_for(lambda: cond("Window.IsVisible(1113)") and lab("Window(1113).Property(result)") != "", timeout, 0.1)
    return lab("Window(1113).Property(result)") if cond("Window.IsVisible(1113)") else ""


def pin_state():
    return labs(["Skin.String(lumen.pin)", "Skin.String(lumen.pinnew)", "Skin.String(lumen.pinentry)", "Window(home).Property(lumen.pinflow)",
                 "Window(home).Property(lumen.pinprompt)"])


@check("pin_set")
def c_pin_set(r):
    kids(False)
    pin("")
    r.ok("press Set kids PIN", settings_button(902))
    r.ok("first prompt", wait_for(lambda: cond("Window.IsVisible(numericinput)"), 5))
    r.ok("first prompt says choose", lab("Window(home).Property(lumen.pinprompt)") == "new")
    r.ok("typed digits hidden", not cond("Control.IsVisible(4)"))
    send_number("1111")
    r.ok("second prompt", wait_for(lambda: cond("Window.IsVisible(numericinput)") and lab("Window(home).Property(lumen.pinprompt)") == "again", 5))
    send_number("2222")
    r.ok("mismatch reported", pin_result() == "mismatch")
    r.ok("nothing saved", lab("Skin.String(lumen.pin)") == "")
    wait_for(lambda: not cond("Window.IsVisible(1113)"), 5)
    r.ok("press Set kids PIN again", settings_button(902))
    send_number("2468")
    wait_for(lambda: lab("Window(home).Property(lumen.pinprompt)") == "again", 5)
    send_number("2468")
    r.ok("saved reported", pin_result() == "saved")
    st = pin_state()
    r.ok("PIN stored", st["Skin.String(lumen.pin)"] == "2468")
    r.ok("scratch values cleared", not any(v for k, v in st.items() if k != "Skin.String(lumen.pin)"), st)
    wait_for(lambda: not cond("Window.IsVisible(1113)"), 5)
    r.ok("Set hidden, Remove shown once a PIN exists", not cond("Control.IsVisible(902)") and cond("Control.IsVisible(903)"))


@check("pin_unlock")
def c_pin_unlock(r):
    pin("2468")
    kids(True)
    r.ok("press the lock", kids_button())
    r.ok("prompt asks for the PIN", wait_for(lambda: lab("Window(home).Property(lumen.pinprompt)") == "current", 5))
    send_number("0000")
    r.ok("wrong PIN reported", pin_result() == "wrong")
    r.ok("kids mode stays on", cond("Skin.HasSetting(CinemathequeKids)"))
    r.ok("message goes away", wait_for(lambda: not cond("Window.IsVisible(1113)"), 4))
    r.ok("press the lock again", kids_button())
    send_number("2468")
    r.ok("right PIN leaves kids mode", wait_for(lambda: not cond("Skin.HasSetting(CinemathequeKids)"), 3))
    time.sleep(1)
    r.ok("no dialog left", not cond("Window.IsVisible(1113)") and not cond("System.HasActiveModalDialog"))
    r.ok("on Home, focus on the button", lab("System.CurrentWindow") == "Home" and lab("System.CurrentControlId") == "20",
         labs(["System.CurrentWindow", "System.CurrentControlId"]))
    r.ok("normal rows back", home_state()["groups"] == load_baseline()["groups"])
    r.ok("scratch values cleared", not any(v for k, v in pin_state().items() if k != "Skin.String(lumen.pin)"))


@check("pin_remove")
def c_pin_remove(r):
    kids(False)
    pin("2468")
    r.ok("press Remove kids PIN", settings_button(903))
    r.ok("prompt asks for the PIN", wait_for(lambda: lab("Window(home).Property(lumen.pinprompt)") == "current", 5))
    send_number("1357")
    r.ok("wrong PIN reported", pin_result() == "wrong")
    r.ok("PIN unchanged", lab("Skin.String(lumen.pin)") == "2468")
    wait_for(lambda: not cond("Window.IsVisible(1113)"), 5)
    r.ok("press Remove kids PIN again", settings_button(903))
    send_number("2468")
    r.ok("removed reported", pin_result() == "removed")
    r.ok("PIN gone", lab("Skin.String(lumen.pin)") == "")
    reset_home()
    time.sleep(2)
    r.ok("no kids button on Home", not cond("Control.IsVisible(20)"))


@check("pin_cancel")
def c_pin_cancel(r):
    kids(False)
    pin("")
    r.ok("press Set kids PIN", settings_button(902))
    wait_for(lambda: cond("Window.IsVisible(numericinput)"), 5)
    rpc("Input.Back")
    r.ok("second prompt still opens", wait_for(lambda: cond("Window.IsVisible(numericinput)") and lab("Window(home).Property(lumen.pinprompt)") == "again", 5))
    rpc("Input.Back")
    time.sleep(1)
    r.ok("no message", not cond("Window.IsVisible(1113)"))
    r.ok("nothing saved", lab("Skin.String(lumen.pin)") == "")
    pin("2468")
    kids(True)
    r.ok("press the lock", kids_button())
    wait_for(lambda: cond("Window.IsVisible(numericinput)"), 5)
    rpc("Input.Back")
    time.sleep(1)
    r.ok("cancelled unlock: kids mode stays on", cond("Skin.HasSetting(CinemathequeKids)"))
    r.ok("cancelled unlock: no message", not cond("Window.IsVisible(1113)"))
    r.ok("scratch values cleared", not any(v for k, v in pin_state().items() if k != "Skin.String(lumen.pin)"))


@check("pin_nopin")
def c_pin_nopin(r):
    pin("")
    kids(True)
    r.ok("lock shown without a PIN", kids_button())
    r.ok("no PIN prompt", not wait_for(lambda: cond("Window.IsVisible(numericinput)"), 1.5))
    r.ok("kids mode left", not cond("Skin.HasSetting(CinemathequeKids)"))


def open_listing(path):
    rpc("GUI.ActivateWindow", {"window": "videos", "parameters": [path, "return"]})
    return wait_listing(path)


def goto_dbid(dbid, timeout=60):
    """Focus the item with this DBID in the media window's grid, walking it with the arrow keys."""
    ids = [i["DBID"] for i in list_items(None, ["DBID"])]
    if ids.count(str(dbid)) != 1:
        return False
    target = ids.index(str(dbid)) + 1  # Container.CurrentItem is 1-based
    cur = lambda: int(lab("Container.CurrentItem") or 0)
    c0 = cur()
    rpc("Input.Down"); time.sleep(0.4)
    c1 = cur()
    if c1 == c0:
        rpc("Input.Up"); time.sleep(0.4)
        c1 = cur()
    cols = max(1, abs(c1 - c0))
    t0 = time.time()
    while cur() != target and time.time() - t0 < timeout:
        c = cur()
        row, trow = (c - 1) // cols, (target - 1) // cols
        rpc("Input." + ("Down" if row < trow else "Up" if row > trow else "Right" if c < target else "Left"))
        time.sleep(0.3)
    time.sleep(0.4)
    return lab("ListItem.DBID") == str(dbid)


@check("nav")
def c_nav(r):
    for on in (True, False):
        kids(on)
        r.ok(f"kids={on}: kids films listed", open_listing(KIDS + "movies.xsp"))
        view = lab("System.CurrentControlId")
        for _ in range(3):
            rpc("Input.Left"); time.sleep(0.5)
        menu = cond("ControlGroup(9000).HasFocus")
        if on:
            r.ok("kids: Left never opens the side menu", not menu and lab("System.CurrentControlId") == view, lab("System.CurrentControlId"))
            # the Menu action focuses the window's menu control by id, hidden group or not (GUIWindow.cpp)
            rpc("Input.ExecuteAction", {"action": "menu"})
            time.sleep(0.8)
            r.ok("kids: the Menu action never reaches the side menu", not cond("ControlGroup(9000).HasFocus"), lab("System.CurrentControlId"))
        else:
            r.ok("normal: Left opens the side menu", menu)
        reset_home()


@check("info")
def c_info(r):
    pool = rpc("VideoLibrary.GetMovies", {"filter": _tag_filter(TAG), "properties": ["trailer", "cast", "setid"]}, timeout=60)["result"]["movies"]
    cands = {m["movieid"] for m in pool if m["trailer"] and m["cast"]}
    if not cands:
        r.skip("no tagged film with both a trailer and cast")
        return
    in_set = {m["movieid"] for m in pool if m.get("setid")} & cands
    kids(True)
    r.ok("kids films listed", open_listing(KIDS + "movies.xsp"))
    order = [int(i["DBID"]) for i in list_items(None, ["DBID"])]
    dbid = min(in_set or cands, key=order.index)  # one in a collection shows the collection button too
    r.ok(f"focus film {dbid}", goto_dbid(dbid))
    hidden = {11: "trailer", 14: "versions", 15: "extras", 443: "collection", 10: "art", 6: "refresh", 6050: "cast"}
    seen = {}
    for on in (False, True):  # normal first: kids mode must hide what normal mode shows on this film
        kids(on)
        rpc("Input.Info")
        r.ok(f"kids={on}: detail page opens", wait_for(lambda: cond("Window.IsVisible(movieinformation)"), 8))
        time.sleep(2)
        if not on:
            wait_for(lambda: cond("Control.IsVisible(6050)"), 8)
            vis = conds([f"Control.IsVisible({c})" for c in hidden])
            seen = {c: name for c, name in hidden.items() if vis[f"Control.IsVisible({c})"]}
            r.ok("normal: trailer and cast row shown", 11 in seen and 6050 in seen, sorted(seen.values()))
            missing = sorted(set(hidden.values()) - set(seen.values()))
            if missing:
                r.skip(f"this film has no {', '.join(missing)} button even in normal mode: not checked in kids mode")
        else:
            vis = conds([f"Control.IsVisible({c})" for c in [8] + list(seen)])
            r.ok("kids: Play stays", vis["Control.IsVisible(8)"])
            r.ok(f"kids: {', '.join(seen.values())} hidden", not any(vis[f"Control.IsVisible({c})"] for c in seen),
                 [seen[c] for c in seen if vis[f"Control.IsVisible({c})"]])
        rpc("Input.Back")
        wait_for(lambda: not cond("Window.IsVisible(movieinformation)"), 5)
    reset_home()


@check("info_show")
def c_info_show(r):
    # Kodi's Browse button opens a show without "return", so Back would climb into every TV show
    for on in (True, False):
        kids(on)
        r.ok(f"kids={on}: kids shows listed", open_listing(KIDS + "tvshows.xsp"))
        rpc("Input.Info")
        r.ok(f"kids={on}: a show's detail page opens", wait_for(lambda: cond("Window.IsVisible(movieinformation)"), 8)
             and lab("ListItem.DBType") == "tvshow", lab("ListItem.DBType"))
        time.sleep(2)
        browse = cond("Control.IsVisible(8)")
        if not on:
            r.ok("normal: Browse shown", browse)
        else:
            r.ok("kids: no Browse on a show", not browse)
            if browse and focus_id(8, ("Up", "Left"), 6):
                rpc("Input.Select")
                wait_for(lambda: not cond("Window.IsVisible(movieinformation)") and not cond("Container.IsUpdating"), 10)
                time.sleep(1)
                rpc("Input.Back")
                time.sleep(2)
                where = lab("Container.FolderPath") if cond("Window.IsActive(videos)") else lab("System.CurrentWindow")
                r.ok("kids: Back after Browse stays with kids titles", where == "Home" or where.startswith(KIDS), where)
        reset_home()


@check("ctx")
def c_ctx(r):
    kids(True)
    r.ok("kids films listed", open_listing(KIDS + "movies.xsp"))
    rpc("Input.ContextMenu")
    time.sleep(1.5)
    r.ok("long-press menu closed in kids mode", not cond("Window.IsVisible(contextmenu)"))
    rpc("Input.Back") if cond("Window.IsVisible(contextmenu)") else None
    sel = rpc("Settings.GetSettingValue", {"setting": "myvideos.selectaction"})["result"]["value"]
    resumable = rpc("VideoLibrary.GetMovies", {"filter": {"and": [_tag_filter(TAG), {"field": "inprogress", "operator": "true", "value": ""}]}},
                    timeout=60)["result"].get("movies", [])
    if not resumable:
        r.skip("no half-watched tagged film to test the resume question")
    elif sel not in (1, 3):
        r.skip(f"myvideos.selectaction is {sel}: OK neither plays nor opens the detail page on this box")
    else:
        dbid = resumable[0]["movieid"]
        r.ok(f"focus half-watched film {dbid}", goto_dbid(dbid))
        rpc("Input.Select")
        if sel == 3:  # OK opens the detail page; its Play button asks
            r.ok("detail page opens", wait_for(lambda: cond("Window.IsVisible(movieinformation)"), 8))
            time.sleep(1.5)
            r.ok("Play focused", focus_id(8, ("Up", "Left"), 6))
            rpc("Input.Select")
        r.ok("resume question shows", wait_for(lambda: cond("Window.IsVisible(contextmenu)"), 4))
        r.ok("it offers Play from beginning", lab("Control.GetLabel(1002)") == "Play from beginning", lab("Control.GetLabel(1002)"))
        rpc("Input.Back")  # never start playback here: stopping early would erase the resume point
        time.sleep(1)
        r.ok("nothing playing", rpc("Player.GetActivePlayers")["result"] == [])
    reset_home()


def search_query():
    """A word that starts a word in a tagged film's title and in an untagged one's."""
    import re
    allm = rpc("VideoLibrary.GetMovies", {"properties": ["title"]}, timeout=90)["result"]["movies"]
    tagged = tagged_movies()
    words = lambda t: {w for w in re.findall(r"[a-z]+", t.lower())}
    kid_words = set().union(*(words(m["title"]) for m in allm if m["movieid"] in tagged))
    other = [words(m["title"]) for m in allm if m["movieid"] not in tagged]
    best = [(sum(any(w.startswith(q) for w in ws) for ws in other), q) for q in kid_words if len(q) >= 4]
    best = [b for b in best if b[0] > 0]
    return max(best)[1] if best else None


def run_search(q):
    """Home > Search tab > type q (checking the live preview) > confirm. Returns the preview's DBIDs."""
    reset_home()
    time.sleep(2)
    tab_bar(16)
    rpc("Input.Select")
    if not wait_for(lambda: cond("Window.IsVisible(virtualkeyboard)"), 8):
        return None
    rpc("Input.SendText", {"text": q, "done": False})
    # the preview is built for q (scripts/lumen_search.py, about a second after typing stops) in the current mode;
    # until then it is hidden, though the list may still hold an older search's titles
    mode = "1" if cond("Skin.HasSetting(CinemathequeKids)") else ""
    films = 9513 if mode else 9511  # the keyboard's live films: kids mode has its own list
    if not wait_for(lambda: labs(["Window(home).Property(lumen.sq.text)", "Window(home).Property(lumen.sq.kids)"])
                    == {"Window(home).Property(lumen.sq.text)": q, "Window(home).Property(lumen.sq.kids)": mode}, 10, 0.3):
        return None
    time.sleep(0.5)
    if not wait_for(lambda: not cond(f"Container({films}).IsUpdating") and int(lab(f"Container({films}).NumItems") or 0) > 0, 15, 0.5):
        return None
    preview = [int(i["DBID"]) for i in list_items(films, ["DBID"]) if i["DBID"]]
    rpc("Input.SendText", {"text": q, "done": True})
    wait_for(lambda: not cond("Window.IsVisible(virtualkeyboard)"), 8)
    time.sleep(1.5)
    wait_for(lambda: not any(conds([f"Container({c}).IsUpdating" for c in (5201, 5202, 5203)]).values()), 25, 0.5)
    return preview


@check("search")
def c_search(r):
    q = search_query()
    if not q:
        r.skip("no word shared by tagged and untagged titles")
        return
    movies, shows = tagged_movies(), tagged_shows()
    kids(True)
    preview = run_search(q)
    r.ok(f"kids: live preview for '{q}' shows tagged films, and only them", bool(preview) and set(preview) <= movies, preview)
    got = [int(i["DBID"]) for i in list_items(5201, ["DBID"]) if i["DBID"]]
    r.ok("kids: films row finds tagged films only", got and set(got) <= movies, f"{len(got)} films")
    got = [int(i["DBID"]) for i in list_items(5202, ["DBID"]) if i["DBID"]]
    r.ok("kids: shows row finds tagged shows only", set(got) <= shows, f"{len(got)} shows")
    r.ok("kids: no cast & crew row", not cond("Control.IsVisible(6203)") and not cond("Control.IsVisible(5203)"))
    kids(False)
    run_search(q)
    got = {int(i["DBID"]) for i in list_items(5201, ["DBID"]) if i["DBID"]}
    r.ok("normal: untagged films found too", bool(got - movies), f"{len(got)} films")
    people = int(lab("Container(5203).NumItems") or 0) > 0
    r.ok("normal: cast & crew row as before", cond("Control.IsVisible(6203)") == people)
    reset_home()


@check("osd")
def c_osd(r):
    pool = rpc("VideoLibrary.GetMovies", {"filter": _tag_filter(TAG), "properties": ["streamdetails", "resume"]}, timeout=60)["result"]["movies"]
    # never a half-watched film: a short play from the start would overwrite its resume point
    pool = [m for m in pool if not (m.get("resume") or {}).get("position")]
    if not pool:
        r.skip("no tagged film without a resume point")
        return
    score = lambda m: (len(m["streamdetails"].get("audio", [])) >= 2, len(m["streamdetails"].get("subtitle", [])) >= 1)
    film = max(pool, key=score)
    two_audio = score(film)[0]
    kids(True)
    films_before = {film["movieid"]: film_state(film["movieid"])}
    t0 = time.time()
    rpc("Player.Open", {"item": {"movieid": film["movieid"]}})
    try:
        r.ok(f"playing film {film['movieid']}", wait_for(lambda: cond("Player.HasVideo") and cond("Player.Playing"), 30))
        time.sleep(3)
        # Kodi saves a film's audio/subtitle choice in the shared library when playback stops: put it back after
        streams = rpc("Player.GetProperties", {"playerid": 1, "properties": ["currentaudiostream", "currentsubtitle", "subtitleenabled"]})["result"]

        def osd():
            if not cond("Window.IsVisible(videoosd)"):
                rpc("Input.ShowOSD")
                wait_for(lambda: cond("Window.IsVisible(videoosd)"), 5)
            return cond("Window.IsVisible(videoosd)")
        r.ok("player controls open", osd())
        vis = conds([f"Control.IsVisible({c})" for c in (70046, 70048, 70061, 70062)] + ["VideoPlayer.HasSubtitles"])
        r.ok("kids: no subtitle settings, no gear", not vis["Control.IsVisible(70046)"] and not vis["Control.IsVisible(70048)"])
        r.ok("kids: audio button", vis["Control.IsVisible(70062)"])
        r.ok("kids: subtitle button when the file has subtitles", vis["Control.IsVisible(70061)"] == vis["VideoPlayer.HasSubtitles"])
        osd()
        if focus_id(70062, ("Right",), 15):
            before = lab("VideoPlayer.AudioLanguage")
            rpc("Input.Select")
            time.sleep(2.5)
            after = lab("VideoPlayer.AudioLanguage")
            if two_audio:
                r.ok("audio button switches the audio language", after != before, f"{before} -> {after}")
            else:
                r.skip(f"film has one audio stream ({before})")
        else:
            r.ok("focus the audio button", False, lab("System.CurrentControlId"))
        if vis["VideoPlayer.HasSubtitles"]:
            osd()
            r.ok("focus the subtitle button", focus_id(70061, ("Left",), 15))
            rpc("Input.Select")
            time.sleep(1.5)
            r.ok("subtitle button never opens the subtitle settings", not cond("Window.IsVisible(osdsubtitlesettings)"))
        kids(False)
        osd()
        time.sleep(1)
        vis = conds([f"Control.IsVisible({c})" for c in (70046, 70048, 70061, 70062)])
        r.ok("normal: subtitle settings and gear back, kids buttons gone",
             vis["Control.IsVisible(70046)"] and vis["Control.IsVisible(70048)"] and not vis["Control.IsVisible(70061)"] and not vis["Control.IsVisible(70062)"], vis)
    finally:
        if "streams" in locals():
            rpc("Player.SetAudioStream", {"playerid": 1, "stream": streams["currentaudiostream"]["index"]})
            if streams["currentsubtitle"]:
                rpc("Player.SetSubtitle", {"playerid": 1, "subtitle": streams["currentsubtitle"]["index"]})
            rpc("Player.SetSubtitle", {"playerid": 1, "subtitle": "on" if streams["subtitleenabled"] else "off"})
            time.sleep(1)
            after = rpc("Player.GetProperties", {"playerid": 1, "properties": ["currentaudiostream", "subtitleenabled"]})["result"]
            r.ok("audio and subtitles put back", after["currentaudiostream"]["index"] == streams["currentaudiostream"]["index"]
                 and after["subtitleenabled"] == streams["subtitleenabled"])
        for p in rpc("Player.GetActivePlayers")["result"]:
            rpc("Player.Stop", {"playerid": p["playerid"]})
        # Player.Open made the film the whole video queue, and it stays there after the stop
        rpc("Playlist.Clear", {"playlistid": 1})
        print(f"INFO osd: test play of movie {film['movieid']} (kids mode) at {time.strftime('%H:%M:%S', time.localtime(t0))}, stopped at {time.strftime('%H:%M:%S')}", flush=True)
        r.ok("last played put back", restore_films(films_before), film_state(film["movieid"]))
    r.ok("stopped within 2 minutes", time.time() - t0 < 120, f"{time.time() - t0:.0f} s")
    r.ok("nothing playing", wait_for(lambda: rpc("Player.GetActivePlayers")["result"] == [], 10))
    reset_home()


def empty_grid_path():
    """A kids grid with nothing in it, the way a Movies tab looks before any film is tagged."""
    xsp = {"rules": {"and": [{"field": "tag", "operator": "is", "value": [TAG]}, {"field": "title", "operator": "is", "value": ["zzqxw lumen none"]}]},
           "type": "movies"}
    return "videodb://movies/titles/?xsp=" + urllib.parse.quote(json.dumps(xsp, separators=(",", ":")))


@check("empty_grid")
def c_empty_grid(r):
    # Kodi gives an empty list a ".." item even with parent folder items off (GUIMediaWindow.cpp:1992);
    # from a kids grid opened with "return" it leads up into the whole library
    path = empty_grid_path()
    for on in (True, False):
        kids(on)
        reset_home()
        rpc("GUI.ActivateWindow", {"window": "videos", "parameters": [path, "return"]})
        loaded = wait_for(lambda: cond("Window.IsActive(videos)") and not cond("Container.IsUpdating")
                          and lab("Container.NumAllItems") == "1", 20, 0.5)
        r.ok(f"kids={on}: empty grid open, only Kodi's '..' in it", loaded and lab("Container.NumItems") == "0",
             labs(["Container.NumItems", "Container.NumAllItems", "Container.FolderPath"]))
        start = lab("Container.FolderPath")
        rpc("Input.Select")
        time.sleep(2)
        moved = not cond("Window.IsActive(videos)") or lab("Container.FolderPath") != start
        if on:
            r.ok("kids: '..' leads nowhere", not moved, lab("Container.FolderPath"))
            r.ok("kids: the grid says there is nothing yet", cond("Control.IsVisible(6060)"))
            rpc("Input.Back")
            r.ok("kids: Back returns Home", wait_for(lambda: lab("System.CurrentWindow") == "Home", 5), lab("System.CurrentWindow"))
        else:
            r.ok("normal: '..' goes up as before", moved, lab("Container.FolderPath"))
            r.ok("normal: no kids message", not cond("Control.IsVisible(6060)"))
    reset_home()


def queue_films(n=2):
    """n tagged films without a resume point (a short play from the start would overwrite one)."""
    pool = rpc("VideoLibrary.GetMovies", {"filter": _tag_filter(TAG), "properties": ["resume"]}, timeout=60)["result"]["movies"]
    return sorted(m["movieid"] for m in pool if not (m.get("resume") or {}).get("position"))[:n]


def play_queue(films):
    rpc("Playlist.Add", {"playlistid": 1, "item": [{"movieid": m} for m in films]})
    rpc("Player.Open", {"item": {"playlistid": 1, "position": 0}})
    return wait_for(lambda: cond("Player.HasVideo") and cond("Player.Playing"), 30)


def open_osd():
    """Input.ShowOSD toggles: press it only while the player controls are closed."""
    for _ in range(3):
        if cond("Window.IsVisible(videoosd)"):
            return True
        rpc("Input.ShowOSD")
        if wait_for(lambda: cond("Window.IsVisible(videoosd)"), 3):
            return True
    return False


def stop_all():
    for p in rpc("Player.GetActivePlayers")["result"]:
        rpc("Player.Stop", {"playerid": p["playerid"]})
    return wait_for(lambda: rpc("Player.GetActivePlayers")["result"] == [], 10)


@check("queue")
def c_queue(r):
    # a parent's queue playing when kids mode starts would carry on into their next film
    queues = {pl: rpc("Playlist.GetItems", {"playlistid": pl})["result"].get("items") for pl in (0, 1)}
    if any(queues.values()) or rpc("Player.GetActivePlayers")["result"]:
        r.skip("something is queued or playing on the box; not touching it")
        return
    films = queue_films()
    if len(films) < 2:
        r.skip("fewer than 2 tagged films without a resume point")
        return
    kids(False)
    pin("2468")
    films_before = {m: film_state(m) for m in films}
    t0 = time.time()
    try:
        r.ok(f"parent's queue playing {films}", play_queue(films))
        time.sleep(3)
        reset_home()
        time.sleep(2)
        r.ok("focus the kids button", tab_bar(20), lab("System.CurrentControlId"))
        rpc("Input.Select")
        time.sleep(2)
        r.ok("kids button enters kids mode", cond("Skin.HasSetting(CinemathequeKids)"))
        r.ok("kids: the parent's video stops", wait_for(lambda: rpc("Player.GetActivePlayers")["result"] == [], 8))
        r.ok("kids: the video queue is empty", not rpc("Playlist.GetItems", {"playlistid": 1})["result"].get("items"))
        stop_all()
        rpc("Playlist.Clear", {"playlistid": 1})
        # kids mode set from a computer skips the button: the player still offers no queue
        for on in (True, False):
            kids(on)
            r.ok(f"kids={on}: queue playing", play_queue(films))
            time.sleep(2)
            r.ok(f"kids={on}: player controls open, 2 videos queued", open_osd() and lab("Playlist.Length(video)") == "2",
                 labs(["Playlist.Length(video)", "System.CurrentWindow", "Player.Playing"]))
            time.sleep(1)
            shown = cond("Control.IsVisible(70042)")
            r.ok(f"kids={on}: playlist button {'hidden' if on else 'shown'}", shown != on)
            stop_all()
            rpc("Playlist.Clear", {"playlistid": 1})
            time.sleep(3)  # the Pi's hardware decoder can stall on a film reopened a second after a stop
    finally:
        stop_all()
        rpc("Playlist.Clear", {"playlistid": 1})
        print(f"INFO queue: test plays of movie {films[0]} (queue {films}; normal, kids, normal) from {time.strftime('%H:%M:%S', time.localtime(t0))}, stopped at {time.strftime('%H:%M:%S')}", flush=True)
        r.ok("last played put back", restore_films(films_before), {m: film_state(m) for m in films_before})
    r.ok("stopped within 2 minutes", time.time() - t0 < 120, f"{time.time() - t0:.0f} s")
    r.ok("nothing playing, nothing queued", stop_all() and not rpc("Playlist.GetItems", {"playlistid": 1})["result"].get("items"))
    kids(False)
    pin("")
    reset_home()


BLOCKED = ["settings", "systemsettings", "skinsettings", "addonbrowser", "filemanager", "favouritesbrowser",
           "programs", "eventlog", "profiles", "systeminfo", "videoplaylist", "musicplaylist"]


@check("blocked")
def c_blocked(r):
    # each window must open in normal mode, or kids mode "sending it back" would prove nothing
    kids(False)
    opens = []
    for w in BLOCKED:
        reset_home()
        rpc("GUI.ActivateWindow", {"window": w})
        if wait_for(lambda: cond(f"Window.IsActive({w})"), 4):
            opens.append(w)
        else:
            r.skip(f"{w} doesn't open on this box in normal mode: nothing to block")
    r.ok("most blocked windows open in normal mode", len(opens) >= len(BLOCKED) - 2, f"{len(opens)}/{len(BLOCKED)}")
    kids(True)
    for w in opens:
        reset_home()
        rpc("GUI.ActivateWindow", {"window": w})
        time.sleep(0.5)
        r.ok(f"kids: {w} sends back to Home", wait_for(lambda: lab("System.CurrentWindow") == "Home" and not cond(f"Window.IsActive({w})"), 3),
             lab("System.CurrentWindow"))
    reset_home()
    rpc("GUI.ActivateWindow", {"window": "shutdownmenu"})
    time.sleep(0.5)
    r.ok("kids: power menu closes", wait_for(lambda: not cond("Window.IsVisible(shutdownmenu)"), 2))
    kids(False)
    reset_home()
    rpc("GUI.ActivateWindow", {"window": "settings"})
    r.ok("normal: settings open", wait_for(lambda: cond("Window.IsActive(settings)"), 4))
    reset_home()


@check("restart")
def c_restart(r):
    """Kids mode entered with the real button must survive Kodi being killed (a TV losing power)."""
    kids(False)
    pin("")
    r.ok("press Set kids PIN", settings_button(902))
    send_number("2468")
    wait_for(lambda: lab("Window(home).Property(lumen.pinprompt)") == "again", 5)
    send_number("2468")
    r.ok("PIN saved", pin_result() == "saved")
    wait_for(lambda: not cond("Window.IsVisible(1113)"), 5)
    r.ok("press the kids button", kids_button())
    r.ok("kids mode on", wait_for(lambda: cond("Skin.HasSetting(CinemathequeKids)"), 3))
    time.sleep(2)
    r.ok("Kodi back on Home", restart_kodi())
    keepalive()
    r.ok("kids mode still on after the kill", cond("Skin.HasSetting(CinemathequeKids)"))
    time.sleep(15)
    vis = conds([f"Control.IsVisible({g})" for g in list(NORMAL_GROUPS) + list(KIDS_GROUPS)])
    r.ok("kids rows, no normal rows", any(vis[f"Control.IsVisible({g})"] for g in KIDS_GROUPS)
         and not any(vis[f"Control.IsVisible({g})"] for g in NORMAL_GROUPS))
    r.ok("press the lock", kids_button())
    send_number("2468")
    r.ok("PIN leaves kids mode", wait_for(lambda: not cond("Skin.HasSetting(CinemathequeKids)"), 4))
    pin("")


@check("regress")
def c_regress(r):
    kids(False)
    pin("")
    r.ok("fresh Kodi in normal mode", restart_kodi())  # hidden lists keep what an earlier kids session loaded
    base, state = load_baseline(), home_state()
    r.ok("normal row groups as in 0.2.1", state["groups"] == base["groups"], f"{state['groups']} vs {base['groups']}")
    r.ok("tabs 10-18 as in 0.2.1", state["tabs"] == base["tabs"])
    diff = {k: (base["counts"][k], v) for k, v in state["counts"].items() if k != "5005" and v != base["counts"][k]}
    r.ok("row sizes as in 0.2.1 (random Discover excepted)", not diff, diff)
    counts = labs([f"Container({i}).NumItems" for i in KIDS_ROWS])
    r.ok("kids lists never loaded", all(v in ("", "0") for v in counts.values()), counts)
    # a list that ran its query reports IsUpdating until it is shown (CDirectoryProvider::IsUpdating)
    busy = conds([f"Container({i}).IsUpdating" for i in KIDS_ROWS])
    r.ok("kids rows never queried", not any(busy.values()), [k for k, v in busy.items() if v])
    r.ok("no kids button without a PIN", not cond("Control.IsVisible(20)"))
    r.ok("screenshot", bool(shot("normal_home")))


@check("cleanup")
def c_cleanup(r):
    for p in rpc("Player.GetActivePlayers")["result"]:
        rpc("Player.Stop", {"playerid": p["playerid"]})
    # lumen.search: the Search page would open on the checks' last search word. The PIN goes back to what it was
    # before the run: on a household box it's the family's
    for setting, value in ((KIDS_SETTING, False), ("lumen.pin", PIN_BEFORE), ("lumen.search", "")):
        try:
            set_skin(setting, value)
        except RuntimeError:
            pass  # the skin on the box doesn't know this setting yet
    r.ok("back on Home", reset_home() == "Home")
    r.ok("nothing playing", rpc("Player.GetActivePlayers")["result"] == [])
    r.ok("kids mode off", not cond("Skin.HasSetting(CinemathequeKids)"))
    r.ok("kids PIN as it was before the run", lab("Skin.String(lumen.pin)") == PIN_BEFORE)


def main(names, checks=CHECKS):
    global PIN_BEFORE
    PIN_BEFORE = lab("Skin.String(lumen.pin)")
    if names == ["all"]:
        names = [n for n in checks if n not in ("baseline", "cleanup")] + ["cleanup"]
    failed = False
    try:
        for name in names:
            if name not in checks:
                print(f"FAIL {name}: no such check (have: {', '.join(checks)})")
                failed = True
                continue
            keepalive()
            r = Report(name)
            try:
                checks[name](r)
            except Exception as e:  # a crashed check is a failed check
                r.ok("ran without error", False, f"{type(e).__name__}: {e}")
                traceback.print_exc(limit=2)
            failed |= r.failed
    finally:  # a run stopped early, or PIN checks run by name, still leave the household's PIN
        try:
            if lab("Skin.String(lumen.pin)") != PIN_BEFORE:
                set_skin("lumen.pin", PIN_BEFORE)
        except Exception:
            pass
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
