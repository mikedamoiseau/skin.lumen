#!/usr/bin/env python3
"""Actor-page checks against a Kodi box over JSON-RPC.

    python3 tools/actor_check.py NAME [NAME ...]   # run the named checks
    python3 tools/actor_check.py all               # every check, then cleanup

Each check prints PASS/FAIL/SKIP lines; the exit status is 1 if anything failed.
Ground truth comes from the library itself (VideoLibrary.* with actor/director filters), never from the skin.
"""
import json
import re
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kids_check  # noqa: E402
from kids_check import Report, goto_dbid, kids, list_items, open_listing, run_search  # noqa: E402,F401
from kodi_gui import cond, conds, focus_id, lab, labs, reset_home, rpc, wait_for  # noqa: E402,F401

CHECKS = {}


def check(name):
    def register(fn):
        CHECKS[name] = fn
        return fn
    return register


# ---------- ground truth ----------

def _is(field, value):
    return {"field": field, "operator": "is", "value": value}


def _xsp_path(base, kind, rules):
    xsp = {"rules": {"and": rules}, "type": kind}
    return base + "?xsp=" + urllib.parse.quote(json.dumps(xsp, separators=(",", ":")))


def _film(movieid):
    d = rpc("VideoLibrary.GetMovieDetails", {"movieid": movieid, "properties": ["title", "year", "cast", "director"]})["result"]["moviedetails"]
    cast = [{"name": c["name"], "role": c.get("role", ""), "thumbnail": c.get("thumbnail", "")} for c in d["cast"]]
    return {"movieid": movieid, "title": d["title"], "year": d["year"], "cast": cast, "director": d["director"]}


def _movies(props, flt=None):
    params = {"properties": props}
    if flt:
        params["filter"] = flt
    return sorted(rpc("VideoLibrary.GetMovies", params, timeout=120)["result"].get("movies", []), key=lambda m: m["movieid"])


def person_titles(name):
    """Everything of name's in the library: films they acted in or directed, shows they're in the main cast of."""
    films = _movies(["title", "year", "director"], {"or": [_is("actor", name), _is("director", name)]})
    shows = rpc("VideoLibrary.GetTVShows", {"filter": _is("actor", name), "properties": ["title", "year"]}, timeout=60)["result"].get("tvshows", [])
    out = [{"type": "movie", "id": m["movieid"], "title": m["title"], "year": m["year"], "directed": name in m["director"]} for m in films]
    out += [{"type": "tvshow", "id": s["tvshowid"], "title": s["title"], "year": s["year"], "directed": False} for s in shows]
    return sorted(out, key=lambda t: (-t["year"], t["title"]))


def cast_film():
    """The lowest-id film with one director (not in its cast) and 3+ cast members, all with a photo,
    whose second cast member has 2+ titles."""
    for m in _movies(["director"]):
        if len(m["director"]) != 1:
            continue
        f = _film(m["movieid"])
        names = [c["name"] for c in f["cast"]]
        if len(names) >= 3 and all(c["thumbnail"] for c in f["cast"]) and f["director"][0] not in names \
                and len(person_titles(names[1])) >= 2:
            return f
    return None


def codirected_film():
    for m in _movies(["director"]):
        if len(m["director"]) >= 2:
            f = _film(m["movieid"])
            if f["cast"]:
                return f
    return None


def set_film():
    """A collection with 2+ films: {setid, title}."""
    counts = {}
    for m in _movies(["setid"]):
        if m["setid"]:
            counts[m["setid"]] = counts.get(m["setid"], 0) + 1
    setid = min((s for s, n in counts.items() if n >= 2), default=None)
    if setid is None:
        return None
    d = rpc("VideoLibrary.GetMovieSetDetails", {"setid": setid, "properties": ["title"]})["result"]["setdetails"]
    return {"setid": setid, "title": d["title"]}


def odd_name():
    """A film plus a cast member whose name has an apostrophe, an ampersand or a non-ASCII letter."""
    for ch in ("'", "&", "é", "ø", "ü", "ñ", "ç", "á", "ö"):
        for m in _movies([], {"field": "actor", "operator": "contains", "value": ch})[:5]:
            f = _film(m["movieid"])
            name = next((c["name"] for c in f["cast"] if ch in c["name"]), None)
            if name:
                return f, name
    return None


def guest_episode():
    """An episode with a guest star who has no title of their own in the library: {episodeid, tvshowid, guest}."""
    # an episode's cast is its guests, then the show's cast (VideoDatabase.cpp GetDetailsForEpisode)
    eps = rpc("VideoLibrary.GetEpisodes", {"properties": ["tvshowid", "cast"], "limits": {"start": 0, "end": 500}}, timeout=120)["result"].get("episodes", [])
    show_cast = {}
    for e in eps:
        if e["tvshowid"] not in show_cast:
            d = rpc("VideoLibrary.GetTVShowDetails", {"tvshowid": e["tvshowid"], "properties": ["cast"]})["result"]["tvshowdetails"]
            show_cast[e["tvshowid"]] = {c["name"] for c in d["cast"]}
        for c in e["cast"]:
            if c["name"] not in show_cast[e["tvshowid"]] and not person_titles(c["name"]):
                return {"episodeid": e["episodeid"], "tvshowid": e["tvshowid"], "guest": c["name"]}
    return None


def mixed_person():
    """A film plus the cast index of someone who is in it and in a show's main cast: (film, index)."""
    shows = rpc("VideoLibrary.GetTVShows", {"properties": ["cast"]}, timeout=120)["result"].get("tvshows", [])
    for s in sorted(shows, key=lambda s: s["tvshowid"]):
        for c in s["cast"][:8]:
            films = _movies([], _is("actor", c["name"]))
            if films:
                f = _film(films[0]["movieid"])
                return f, [x["name"] for x in f["cast"]].index(c["name"])
    return None


def nophoto_film():
    """A film plus the cast index of someone in it without a photo: (film, index)."""
    for m in _movies([])[:300]:
        f = _film(m["movieid"])
        for i, c in enumerate(f["cast"]):
            if not c["thumbnail"]:
                return f, i
    return None


def any_show():
    return rpc("VideoLibrary.GetTVShows", {"limits": {"start": 0, "end": 1}})["result"]["tvshows"][0]["tvshowid"]


def open_path(path):
    """Open a library listing in Videos, without "return": with it, Kodi keeps showing the old listing when the new
    path differs from the window's start folder only in its options (GUIMediaWindow.cpp:530 ignores URL options)."""
    rpc("GUI.ActivateWindow", {"window": "videos", "parameters": [path]})
    return kids_check.wait_listing(path)


def open_detail(kind, dbid):
    """Open a title's detail page from a library listing that holds only that title (or its season)."""
    if kind == "movie":
        d = rpc("VideoLibrary.GetMovieDetails", {"movieid": dbid, "properties": ["title", "year"]})["result"]["moviedetails"]
        path = _xsp_path("videodb://movies/titles/", "movies", [_is("title", [d["title"]]), _is("year", [str(d["year"])])])
    elif kind == "tvshow":
        d = rpc("VideoLibrary.GetTVShowDetails", {"tvshowid": dbid, "properties": ["title"]})["result"]["tvshowdetails"]
        path = _xsp_path("videodb://tvshows/titles/", "tvshows", [_is("title", [d["title"]])])
    elif kind == "episode":
        d = rpc("VideoLibrary.GetEpisodeDetails", {"episodeid": dbid, "properties": ["tvshowid", "season"]})["result"]["episodedetails"]
        path = f"videodb://tvshows/titles/{d['tvshowid']}/{d['season']}/"
    else:
        path = "videodb://movies/sets/"
    if not open_path(path):
        return False
    # a season opens in Lumen's show view, where the arrows don't walk a grid: often the item is already selected
    if lab("ListItem.DBID") != str(dbid) and not goto_dbid(dbid):
        return False
    rpc("Input.Info")
    return wait_for(lambda: cond("Window.IsVisible(movieinformation)"), 8) and lab("ListItem.DBID") == str(dbid)


@check("truth")
def c_truth(r):
    f = cast_film()
    if not r.ok("a film with a director and a cast with photos", f is not None):
        return
    name = f["cast"][1]["name"]
    titles = person_titles(name)
    r.ok(f"{name}: at least 2 titles", len(titles) >= 2, len(titles))
    for t in titles:
        if t["type"] != "movie":
            continue
        d = rpc("VideoLibrary.GetMovieDetails", {"movieid": t["id"], "properties": ["cast", "director"]})["result"]["moviedetails"]
        r.ok(f"{t['title']} ({t['year']}) lists {name}", name in [c["name"] for c in d["cast"]] + d["director"])


# ---------- GUI ----------

CAST = 6051  # the cast row's focus stand-in over Kodi's list 50 (Kodi handles OK on list 50 itself)


def close_detail():
    for _ in range(5):
        if not any(conds(["Window.IsVisible(movieinformation)", "Window.IsVisible(1115)", "System.HasActiveModalDialog"]).values()):
            break
        rpc("Input.Back")
        time.sleep(1)


def watch_windows(names, seconds=3):
    """Poll for seconds; which of the windows were ever visible."""
    seen = dict.fromkeys(names, False)
    t0 = time.time()
    while time.time() - t0 < seconds:
        for k, v in conds([f"Window.IsVisible({n})" for n in names]).items():
            seen[k[len("Window.IsVisible("):-1]] |= v
        time.sleep(0.15)
    return seen


@check("entry")
def c_entry(r):
    f = cast_film()
    if not r.ok("a film with a director and a cast with photos", f is not None):
        return
    if not r.ok(f"{f['title']}: detail page opens", open_detail("movie", f["movieid"])):
        return close_detail()
    time.sleep(2)
    r.ok("Down reaches the cast row", focus_id(CAST, ("Down",), 4), lab("System.CurrentControlId"))
    row = list_items(50, ["Label", "Label2"])
    r.ok("cast in billing order, as today", [i["Label"] for i in row] == [c["name"] for c in f["cast"]], [i["Label"] for i in row][:4])
    r.ok("roles under the faces, as today", [i["Label2"] for i in row] == [c["role"] for c in f["cast"]], [i["Label2"] for i in row][:4])
    name = f["cast"][1]["name"]
    rpc("Input.Right")
    r.ok("Right moves along the row", wait_for(lambda: lab("Container(50).ListItem.Label") == name, 3), lab("Container(50).ListItem.Label"))
    rpc("Input.Select")
    seen = watch_windows(["1115", "selectdialog", "progressdialog"])
    r.ok("OK opens the actor page", seen["1115"])
    r.ok("no Searching… box, no Search results list", not seen["selectdialog"] and not seen["progressdialog"], seen)
    r.ok("the page is told who", lab("Window(home).Property(lumen.person)") == name, lab("Window(home).Property(lumen.person)"))
    rpc("Input.Back")
    r.ok("Back closes the actor page", wait_for(lambda: not cond("Window.IsVisible(1115)"), 5))
    time.sleep(0.5)
    r.ok("back on the same detail page", cond("Window.IsVisible(movieinformation)") and lab("ListItem.DBID") == str(f["movieid"]), lab("ListItem.DBID"))
    r.ok("on the same cast member", cond(f"Control.HasFocus({CAST})") and lab("Container(50).ListItem.Label") == name,
         (lab("System.CurrentControlId"), lab("Container(50).ListItem.Label")))
    close_detail()
    reset_home()


@check("set_row")
def c_set_row(r):
    s = set_film()
    if not s:
        r.skip("no collection with 2+ films")
        return
    if not r.ok(f"{s['title']}: collection page opens", open_detail("set", s["setid"])):
        return close_detail()
    time.sleep(2)
    r.ok("Down reaches the films row (Kodi's list 50)", focus_id(50, ("Down",), 4), lab("System.CurrentControlId"))
    rpc("Input.Select")
    r.ok("OK on a film opens no actor page", not watch_windows(["1115"])["1115"])
    r.ok("the page shows that film, as today", wait_for(lambda: lab("ListItem.DBType") == "movie", 5), lab("ListItem.DBType"))
    close_detail()
    reset_home()


def open_person(movieid, index):
    """From a film's detail page, open the actor page of cast member index (0-based)."""
    if not open_detail("movie", movieid):
        return False
    time.sleep(1.5)
    if not focus_id(CAST, ("Down",), 4):
        return False
    for _ in range(index):  # always rightwards: on a film with directors, Left from the first face goes to them
        rpc("Input.Right")
        time.sleep(0.25)
    if not wait_for(lambda: lab("Container(50).CurrentItem") == str(index + 1), 3):
        return False
    rpc("Input.Select")
    return wait_for(lambda: cond("Window.IsVisible(1115)"), 5)


def page_grid():
    """The actor page's grid once loaded: [(DBType, DBID, Year)]."""
    wait_for(lambda: not cond("Container(5150).IsUpdating"), 20, 0.4)
    time.sleep(0.5)
    return [(i["DBType"], int(i["DBID"] or 0), int(i["Year"] or 0)) for i in list_items(5150, ["DBType", "DBID", "Year"])]


def count_copy(n):
    return "No titles of theirs in your library" if n == 0 else "1 title in your library" if n == 1 else f"{n} titles in your library"


def check_page(r, f, index):
    name = f["cast"][index]["name"]
    if not r.ok(f"{name}: actor page opens from {f['title']}", open_person(f["movieid"], index)):
        return close_detail()
    titles = person_titles(name)
    grid = page_grid()
    want = sorted((t["type"], t["id"]) for t in titles)
    r.ok(f"{name}: grid = their titles in the library", sorted(g[:2] for g in grid) == want, f"{len(grid)} vs {len(want)}: {grid[:6]}")
    years = [g[2] for g in grid]
    r.ok(f"{name}: newest first", years == sorted(years, reverse=True), years)
    r.ok(f"{name}: name", lab("Control.GetLabel(6150)") == name, lab("Control.GetLabel(6150)"))
    kind = "Actor and director" if any(t["directed"] for t in titles) else "Actor"
    r.ok(f"{name}: {kind}", lab("Control.GetLabel(6151)") == kind, lab("Control.GetLabel(6151)"))
    r.ok(f"{name}: count", lab("Control.GetLabel(6152)") == count_copy(len(titles)), lab("Control.GetLabel(6152)"))
    r.ok(f"{name}: photo, no silhouette", cond("Control.IsVisible(6153)") and not cond("Control.IsVisible(6154)"))
    close_detail()
    return grid


@check("page")
def c_page(r):
    f = cast_film()
    if r.ok("a film with a director and a cast with photos", f is not None):
        check_page(r, f, 1)
    m = mixed_person()
    if m and r.ok("someone in a film and in a show's main cast", True, m[0]["cast"][m[1]]["name"]):
        grid = check_page(r, *m) or []
        # one list holds both: films and shows come from one multipath, sorted together
        r.ok("their films and shows in one grid", {"movie", "tvshow"} <= {g[0] for g in grid}, sorted({g[0] for g in grid}))
    else:
        r.skip("nobody in both a film and a show's main cast")
    reset_home()


@check("page_nophoto")
def c_page_nophoto(r):
    p = nophoto_film()
    if not p:
        r.skip("every cast member in the first 300 films has a photo")
        return
    f, index = p
    name = f["cast"][index]["name"]
    if r.ok(f"{name}: actor page opens from {f['title']}", open_person(f["movieid"], index)):
        time.sleep(1)
        r.ok("silhouette, no photo", cond("Control.IsVisible(6154)") and not cond("Control.IsVisible(6153)"))
        r.ok("name", lab("Control.GetLabel(6150)") == name, lab("Control.GetLabel(6150)"))
    close_detail()
    reset_home()


@check("names")
def c_names(r):
    p = odd_name()
    if not p:
        r.skip("no cast name with ', & or a non-ASCII letter")
        return
    f, name = p
    index = [c["name"] for c in f["cast"]].index(name)
    if not r.ok(f"{name}: actor page opens from {f['title']}", open_person(f["movieid"], index)):
        return close_detail()
    grid = sorted(g[:2] for g in page_grid())
    want = sorted((t["type"], t["id"]) for t in person_titles(name))
    r.ok(f"{name}: name shown as Kodi spells it", lab("Control.GetLabel(6150)") == name, lab("Control.GetLabel(6150)"))
    if "&" in name:  # "&" splits URL options: their titles or nothing, never anyone else's
        r.ok(f"{name}: grid is theirs or empty", grid == want or (grid == [] and lab("Control.GetLabel(6152)") == count_copy(0)), grid[:6])
    else:
        r.ok(f"{name}: grid = their titles in the library", grid == want, f"{grid[:6]} vs {want[:6]}")
    close_detail()
    reset_home()


DIRECTORS = 5060  # the directors at the front of a film's cast row


def acts(name):
    """Whether name is in any film's cast or any show's main cast."""
    films = _movies([], _is("actor", name))
    shows = rpc("VideoLibrary.GetTVShows", {"filter": _is("actor", name), "limits": {"start": 0, "end": 1}})["result"].get("tvshows", [])
    return bool(films or shows)


def no_directors(r, what):
    # empty, not just hidden: the cast row's place depends on how many directors the list holds
    wait_for(lambda: lab(f"Container({DIRECTORS}).NumItems") in ("", "0"), 3)
    r.ok(f"{what}: no director card", lab(f"Container({DIRECTORS}).NumItems") in ("", "0"), lab(f"Container({DIRECTORS}).NumItems"))


@check("director")
def c_director(r):
    f = cast_film()
    if not r.ok(f"{f['title']}: detail page opens", open_detail("movie", f["movieid"])):
        return close_detail()
    time.sleep(1.5)
    d = f["director"][0]
    r.ok("Down reaches the cast row", focus_id(CAST, ("Down",), 4), lab("System.CurrentControlId"))
    r.ok("Left from the first cast member reaches the director", focus_id(DIRECTORS, ("Left",), 3), lab("System.CurrentControlId"))
    r.ok("the director leads the row", lab(f"Container({DIRECTORS}).ListItem.Label") == d, lab(f"Container({DIRECTORS}).ListItem.Label"))
    rpc("Input.Right")
    r.ok("Right from the last director reaches the first cast member",
         wait_for(lambda: cond(f"Control.HasFocus({CAST})"), 3) and lab("Container(50).CurrentItem") == "1",
         (lab("System.CurrentControlId"), lab("Container(50).CurrentItem")))
    rpc("Input.Left")
    wait_for(lambda: cond(f"Control.HasFocus({DIRECTORS})"), 3)
    rpc("Input.Select")
    r.ok("OK opens the director's page", wait_for(lambda: cond("Window.IsVisible(1115)"), 5))
    r.ok("the page is told who", lab("Window(home).Property(lumen.person)") == d, lab("Window(home).Property(lumen.person)"))
    grid = sorted(g[:2] for g in page_grid())
    r.ok(f"{d}: grid = their titles in the library", grid == sorted((t["type"], t["id"]) for t in person_titles(d)), grid[:6])
    kind = "Actor and director" if acts(d) else "Director"
    r.ok(f"{d}: {kind}", lab("Control.GetLabel(6151)") == kind, lab("Control.GetLabel(6151)"))
    close_detail()
    show = any_show()
    if r.ok("a show's detail page opens", open_detail("tvshow", show)):
        time.sleep(2)
        no_directors(r, "show page")
    close_detail()
    reset_home()


@check("codirected")
def c_codirected(r):
    f = codirected_film()
    if not f:
        r.skip("no film with two directors and a cast")
        return
    if r.ok(f"{f['title']}: detail page opens", open_detail("movie", f["movieid"])):
        wait_for(lambda: not cond(f"Container({DIRECTORS}).IsUpdating"), 8)
        time.sleep(1)
        got = sorted(i["Label"] for i in list_items(DIRECTORS, ["Label"]))
        r.ok("every director at the front", got == sorted(f["director"]), f"{got} vs {sorted(f['director'])}")
    close_detail()
    reset_home()


@check("episode")
def c_episode(r):
    e = guest_episode()
    if not e:
        r.skip("no episode with a guest who has no title of their own")
        return
    if not r.ok(f"episode {e['episodeid']}: detail page opens", open_detail("episode", e["episodeid"])):
        return close_detail()
    time.sleep(2)
    no_directors(r, "episode page")
    r.ok("Down reaches the cast row", focus_id(CAST, ("Down",), 4), lab("System.CurrentControlId"))
    names = [i["Label"] for i in list_items(50, ["Label"])]
    if not r.ok(f"guest {e['guest']} in the row", e["guest"] in names, names[:6]):
        return close_detail()
    for _ in range(names.index(e["guest"])):
        rpc("Input.Right")
        time.sleep(0.25)
    r.ok("on the guest", wait_for(lambda: lab("Container(50).ListItem.Label") == e["guest"], 3), lab("Container(50).ListItem.Label"))
    rpc("Input.Select")
    if r.ok("OK opens their page", wait_for(lambda: cond("Window.IsVisible(1115)"), 5)):
        grid = page_grid()
        r.ok("nothing of theirs: an empty grid", grid == [], grid[:4])
        r.ok("and says so", lab("Control.GetLabel(6152)") == count_copy(0), lab("Control.GetLabel(6152)"))
    close_detail()
    reset_home()


@check("detail_speed")
def c_detail_speed(r):
    """Time Info → detail page shown with its director row loaded; compare by hand with the build before."""
    f = cast_film()
    if not open_detail("movie", f["movieid"]):
        r.ok("detail page opens", False)
        return close_detail()
    rpc("Input.Back")
    wait_for(lambda: not cond("Window.IsVisible(movieinformation)"), 5)
    times = []
    for _ in range(5):
        time.sleep(1.5)
        t0 = time.time()
        rpc("Input.Info")
        shown = wait_for(lambda: cond("Window.IsVisible(movieinformation)") and not cond(f"Container({DIRECTORS}).IsUpdating")
                         and not cond("Container(50).IsUpdating"), 10, 0.02)
        times.append(round(time.time() - t0, 3))
        rpc("Input.Back")
        wait_for(lambda: not cond("Window.IsVisible(movieinformation)"), 5)
        if not shown:
            break
    times.sort()
    r.ok("detail page opens every time", len(times) == 5 and times[-1] < 10, times)
    print(f"INFO detail_speed: median {times[len(times) // 2]} s, all {times}", flush=True)
    close_detail()
    reset_home()


def back_once():
    rpc("Input.Back")
    time.sleep(1.2)


def on_detail(dbid):
    """The detail page of dbid is on top (a title opened from the actor page sits over it)."""
    return wait_for(lambda: cond("Window.IsTopMost(movieinformation)") and lab("ListItem.DBID") == str(dbid), 6)


def open_poster(grid, dbid):
    """On the actor page, select the poster of dbid and press OK."""
    i = [g[1] for g in grid].index(dbid)
    for _ in range(i):
        rpc("Input.Right")
        time.sleep(0.3)
    rpc("Input.Select")
    return i


@check("back")
def c_back(r):
    f = cast_film()
    name = f["cast"][1]["name"]
    if not r.ok(f"{name}: actor page opens from {f['title']}", open_person(f["movieid"], 1)):
        return close_detail()
    grid = page_grid()
    r.ok("a fresh actor page starts at the newest title", lab("Container(5150).CurrentItem") == "1", lab("Container(5150).CurrentItem"))
    # the first poster that isn't the film we came from, so its page can't be mistaken for the film's
    other = next((g for g in grid if g[1] != f["movieid"]), None)
    if not r.ok("a title other than the film", other is not None, grid):
        return close_detail()
    i = open_poster(grid, other[1])
    r.ok(f"OK on poster {i + 1} opens that title's page", on_detail(other[1]), (lab("ListItem.DBID"), lab("ListItem.DBType")))
    back_once()
    r.ok("Back: the actor page again", wait_for(lambda: cond("Window.IsVisible(1115)"), 5) and lab("Control.GetLabel(6150)") == name, lab("Control.GetLabel(6150)"))
    r.ok("on the same poster", cond("Control.HasFocus(5150)") and lab("Container(5150).CurrentItem") == str(i + 1), lab("Container(5150).CurrentItem"))
    back_once()
    r.ok("Back: the film's page again", on_detail(f["movieid"]), (lab("ListItem.DBID"), lab("System.CurrentWindow")))
    back_once()
    r.ok("Back: the library", wait_for(lambda: cond("Window.IsActive(videos)") and not cond("Window.IsVisible(movieinformation)"), 5),
         lab("System.CurrentWindow"))
    # longer chain: film → person → title → someone in its cast → Back lands on the first film, never past it
    if not r.ok("again: actor page", open_person(f["movieid"], 1)):
        return close_detail()
    grid = page_grid()
    open_poster(grid, other[1])
    if r.ok("their other title's page", on_detail(other[1])) and focus_id(CAST, ("Down",), 4):
        second = lab("Container(50).ListItem.Label")
        rpc("Input.Select")
        r.ok(f"its cast opens {second}'s page", wait_for(lambda: cond("Window.IsVisible(1115)") and not cond("Window.IsVisible(movieinformation)")
                                                        and lab("Control.GetLabel(6150)") == second, 6), lab("Control.GetLabel(6150)"))
        back_once()
        r.ok("Back: the first film's page", on_detail(f["movieid"]), (lab("ListItem.DBID"), lab("System.CurrentWindow")))
        back_once()
        r.ok("Back: the library", wait_for(lambda: cond("Window.IsActive(videos)") and not cond("Window.IsVisible(movieinformation)"), 5))
    close_detail()
    reset_home()


@check("play")
def c_play(r):
    """Play on a title opened from the actor page goes full screen (Kodi refuses while a modal dialog is open).
    A test play: a film without a resume point, stopped within seconds."""
    if not r.ok("nothing playing", rpc("Player.GetActivePlayers")["result"] == []):
        return
    f = cast_film()
    name = f["cast"][1]["name"]
    films = [t for t in person_titles(name) if t["type"] == "movie" and t["id"] != f["movieid"]]
    fresh = [t for t in films if rpc("VideoLibrary.GetMovieDetails", {"movieid": t["id"], "properties": ["resume"]})
             ["result"]["moviedetails"]["resume"]["position"] == 0]
    if not fresh:
        r.skip(f"every other film of {name} has a resume point")
        return
    b = fresh[0]
    if not r.ok(f"{name}: actor page opens", open_person(f["movieid"], 1)):
        return close_detail()
    open_poster(page_grid(), b["id"])
    if not r.ok(f"{b['title']}: its page opens", on_detail(b["id"])):
        return close_detail()
    r.ok("on Play", focus_id(8, ("Up",), 3) and lab("Control.GetLabel(8)") == "Play", lab("Control.GetLabel(8)"))
    t0 = time.strftime("%H:%M:%S")
    rpc("Input.Select")
    # the NAS can take most of a minute to open a file: wait it out, so a late start never runs on unattended
    playing = wait_for(lambda: cond("Player.HasVideo"), 90, 0.3)
    full = playing and wait_for(lambda: cond("Window.IsActive(fullscreenvideo)") and not cond("Window.IsVisible(1115)"), 6, 0.2)
    if not playing:  # still opening: Kodi's stop cancels that too
        rpc("Input.ExecuteAction", {"action": "stop"})
    for p in rpc("Player.GetActivePlayers")["result"]:
        rpc("Player.Stop", {"playerid": p["playerid"]})
    print(f"INFO play: test play of movie {b['id']} ({b['title']}) at {t0}, stopped at {time.strftime('%H:%M:%S')}", flush=True)
    r.ok("it plays", playing)
    r.ok("full screen, actor page gone", full, (lab("System.CurrentWindow"), cond("Window.IsVisible(1115)")))
    wait_for(lambda: rpc("Player.GetActivePlayers")["result"] == [], 10)
    time.sleep(2)
    r.ok("after Stop: no detail page reopens on its own", not cond("Window.IsVisible(movieinformation)") and not cond("Window.IsVisible(1115)"),
         lab("System.CurrentWindow"))
    resume = rpc("VideoLibrary.GetMovieDetails", {"movieid": b["id"], "properties": ["resume"]})["result"]["moviedetails"]["resume"]
    r.ok("no resume point left behind", resume["position"] == 0, resume)
    close_detail()
    reset_home()


@check("browse")
def c_browse(r):
    """Browse on a show opened from the actor page lands on its seasons, with the actor page gone."""
    m = mixed_person()
    if not m:
        r.skip("nobody in both a film and a show's main cast")
        return
    f, index = m
    name = f["cast"][index]["name"]
    show = next(t for t in person_titles(name) if t["type"] == "tvshow")
    if not r.ok(f"{name}: actor page opens", open_person(f["movieid"], index)):
        return close_detail()
    open_poster(page_grid(), show["id"])
    if not r.ok(f"{show['title']}: its page opens", on_detail(show["id"])):
        return close_detail()
    r.ok("on Browse", focus_id(8, ("Up",), 3), lab("Control.GetLabel(8)"))
    rpc("Input.Select")
    path = f"videodb://tvshows/titles/{show['id']}/"  # Kodi goes on to .../-2/?tvshowid= for a one-season show
    landed = wait_for(lambda: cond("Window.IsActive(videos)") and lab("Container.FolderPath").startswith(path)
                      and not cond("Window.IsVisible(1115)") and not cond("Window.IsVisible(movieinformation)"), 8)
    r.ok("its seasons, nothing over them", landed, (lab("Container.FolderPath"), cond("Window.IsVisible(1115)"), cond("Window.IsVisible(movieinformation)")))
    time.sleep(2)
    r.ok("and no detail page pops up", not cond("Window.IsVisible(movieinformation)"), lab("System.CurrentWindow"))
    close_detail()
    reset_home()


def kodi_log():
    """Kodi's log on the box (LibreELEC over SSH, Android over adb)."""
    import subprocess
    from kodi_gui import ADB_SERIAL, SSH
    cmd = (["ssh", SSH, "cat /storage/.kodi/temp/kodi.log"] if SSH else
           ["adb", "-s", ADB_SERIAL, "shell", "cat /sdcard/Android/data/org.xbmc.kodi/files/.kodi/temp/kodi.log"])
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout.splitlines()


@check("log_clean")
def c_log_clean(r):
    """The pages this feature touches load without skin errors (a bad condition fails quietly on screen).
    Skin errors come from Kodi's GUI thread (the one that loaded the skin): counted since the skin loaded, as a window
    is parsed once; on an SSH box this check reloads the skin first, so Kodi's start-up (Bluetooth, add-on repos)
    doesn't count. Fetches run on worker threads, next to the NAS, add-ons and the player: a failed fetch counts
    only while this check opens the pages, and not for the NAS (smb://) nor for an empty path — after a play or a
    library update Kodi refetches a list even when its path is empty (DirectoryProvider.cpp:261), which the show
    page's Play-next lookups (0.2) do once a show page has been visited."""
    def errors(lines):
        return [l.split("<general>:")[-1].strip() for l in lines
                if " error <general>:" in l and "/addons/screensaver." not in l
                # a page with nothing on it: its onload SetFocus finds an empty grid
                and "Control 5150 in window 11115 has been asked to focus, but it can't" not in l
                # an add-on's own window (Python windows are 13000-13099), e.g. a screensaver's
                and not re.search(r"in window 130\d\d has been asked to focus", l)]
    reset_home()
    # reload only from Home with nothing open or playing (a reload under open dialogs crashed Kodi)
    if (rpc("Player.GetActivePlayers")["result"] == [] and not cond("System.HasActiveModalDialog")
            and kodi_send("ReloadSkin()")):
        time.sleep(5)
        wait_for(lambda: lab("System.CurrentWindow") == "Home", 20)
    before = len(kodi_log())
    f = cast_film()
    r.ok("actor page opens", open_person(f["movieid"], 1))
    page_grid()
    close_detail()
    reset_home()
    lines = kodi_log()
    start = max((i for i, l in enumerate(lines) if "load skin from:" in l), default=0)
    gui = next((w for w in lines[start].split() if w.startswith("T:")), "") if lines else ""
    bad = errors(l for l in lines[start:] if f" {gui} " in l)
    r.ok("no skin errors in Kodi's log since the skin loaded", gui and not bad, bad[:4])
    bad = [e for e in errors(lines[max(before, start):]) if "smb://" not in e and e != "GetDirectory - Error getting"]
    r.ok("and no failed fetch while the pages opened", not bad, bad[:4])


def kodi_send(action):
    """Run a builtin on an SSH box (JSON-RPC can't open a custom window); False where there's no kodi-send."""
    import subprocess
    from kodi_gui import SSH
    if not SSH:
        return False
    return subprocess.run(["ssh", SSH, f'kodi-send --action="{action}" >/dev/null'], capture_output=True, timeout=30).returncode == 0


def gone_home():
    return lab("System.CurrentWindow") == "Home" and not any(
        conds(["Window.IsVisible(1115)", "Window.IsVisible(movieinformation)", "System.HasActiveModalDialog"]).values())


@check("kids_guard")
def c_kids_guard(r):
    """No actor page in kids mode: one open when kids mode comes on closes and Kodi goes Home; one opened in kids
    mode (nothing on screen leads there) does the same."""
    f = cast_film()
    try:
        if r.ok("actor page opens", open_person(f["movieid"], 1)):
            kids(True)
            r.ok("kids mode comes on: actor page and detail page close, Home", wait_for(gone_home, 5),
                 (lab("System.CurrentWindow"), cond("Window.IsVisible(1115)")))
        close_detail()
        reset_home()
        # the kids timer clears lumen.personreopen before it closes the page: still set afterwards = never opened
        if kodi_send(f"SetProperty(lumen.person,{f['cast'][1]['name']},home)") and kodi_send("SetProperty(lumen.personreopen,1,home)"):
            r.ok("kids mode: ActivateWindow(1115) sent", kodi_send("ActivateWindow(1115)"))
            time.sleep(1)
            r.ok("kids mode: an actor page opened anyway closes, Home", wait_for(gone_home, 5),
                 (lab("System.CurrentWindow"), cond("Window.IsVisible(1115)")))
            r.ok("and it had opened (the kids timer ran)", lab("Window(home).Property(lumen.personreopen)") == "")
        else:
            r.skip("opening window 1115 directly needs kodi-send (an SSH box)")
    finally:
        close_detail()
        kids(False)
        reset_home()


@check("kids_hidden")
def c_kids_hidden(r):
    """Kids mode: from a tagged film's page, Down never reaches the directors (5060), the cast (6051, 50); Search
    shows no Cast & crew row."""
    pool = rpc("VideoLibrary.GetMovies", {"filter": kids_check._tag_filter(kids_check.TAG), "properties": ["cast", "director", "title"]},
               timeout=60)["result"]["movies"]
    pool = [m for m in pool if m["cast"] and m["director"]]
    if not pool:
        r.skip("no tagged film with cast and a director")
        return
    m = pool[0]
    try:
        kids(True)
        r.ok("kids films listed", open_listing(kids_check.KIDS + "movies.xsp"))
        r.ok(f"focus {m['title']}", goto_dbid(m["movieid"]))
        rpc("Input.Info")
        r.ok("detail page opens", wait_for(lambda: cond("Window.IsVisible(movieinformation)"), 8))
        time.sleep(2)
        seen = set()
        for _ in range(4):
            rpc("Input.Down")
            time.sleep(0.4)
            seen.add(lab("System.CurrentControlId"))
        r.ok("Down never lands on the people rows", not seen & {"5060", "6051", "50"}, sorted(seen))
        r.ok("cast group hidden", not cond("Control.IsVisible(6050)"))
        close_detail()
        word = max(m["title"].split(), key=len)
        run_search(word)
        r.ok(f"search \"{word}\": no Cast & crew row", not cond("Control.IsVisible(5203)") and not cond("Control.IsVisible(6203)"),
             lab("Container(5203).NumItems"))
    finally:
        close_detail()
        kids(False)
        reset_home()


def nocast_film():
    """The lowest-id film with a director and no cast (documentaries)."""
    films = _movies(["cast", "director", "title"])
    return next((m for m in sorted(films, key=lambda m: m["movieid"]) if m["director"] and not m["cast"]), None)


@check("director_nocast")
def c_director_nocast(r):
    """A film with a director but no cast still shows the director card, and Down lands on it."""
    m = nocast_film()
    if not m:
        r.skip("no film with a director and no cast")
        return
    if not r.ok(f"{m['title']}: detail page opens", open_detail("movie", m["movieid"])):
        return close_detail()
    time.sleep(1.5)
    # never press OK unless on the card: the page opens on Play
    if not r.ok("Down lands on the director", focus_id(5060, ("Down",), 3), lab("System.CurrentControlId")):
        return close_detail()
    r.ok("the card is theirs", lab("Container(5060).ListItem.Label") == m["director"][0], lab("Container(5060).ListItem.Label"))
    rpc("Input.Select")
    r.ok("OK opens their page", wait_for(lambda: cond("Window.IsVisible(1115)"), 5)
         and lab("Window(home).Property(lumen.person)") == m["director"][0])
    close_detail()


@check("swap_odd")
def c_swap_odd(r):
    """A name with "&" or "/" shows the empty page, never the previous person's titles: when the open page switches
    person (from a title's cast, opened over the page) and when the page opens fresh after someone else's."""
    f = cast_film()
    name = f["cast"][1]["name"]
    if not r.ok(f"{name}: actor page opens", open_person(f["movieid"], 1)):
        return close_detail()
    r.ok(f"{name}: their titles", len(page_grid()) == len(person_titles(name)))
    for odd in ("Tom & Jerry Testperson", "AC/DC Testperson"):
        if not kodi_send(f"SetProperty(lumen.person,{odd},home)"):
            r.skip("switching the person needs kodi-send (an SSH box)")
            return close_detail()
        wait_for(lambda: lab("Control.GetLabel(6150)") == odd, 5)
        time.sleep(2)
        r.ok(f"switched to {odd!r}: nothing listed", page_grid() == [], len(page_grid()))
        r.ok(f"{odd!r}: says no titles", wait_for(lambda: lab("Control.GetLabel(6152)") == count_copy(0), 5), lab("Control.GetLabel(6152)"))
    close_detail()
    r.ok(f"{name}: page opens again", open_person(f["movieid"], 1) and len(page_grid()) == len(person_titles(name)))
    close_detail()
    reset_home()
    kodi_send("SetProperty(lumen.person,Tom & Jerry Testperson,home)")
    kodi_send("SetProperty(lumen.personthumb,,home)")
    kodi_send("ActivateWindow(1115)")
    if r.ok("fresh page for 'Tom & Jerry Testperson' opens", wait_for(lambda: cond("Window.IsVisible(1115)"), 5)):
        time.sleep(2)
        r.ok("fresh page: nothing listed", page_grid() == [], len(page_grid()))
    close_detail()
    reset_home()


@check("lookup_limit")
def c_lookup_limit(r):
    """The page's two "what they do" lookups fetch one title each, not a whole filmography."""
    f = cast_film()
    name = f["cast"][1]["name"]
    if not r.ok(f"{name}: actor page opens", open_person(f["movieid"], 1)):
        return close_detail()
    page_grid()
    wait_for(lambda: not cond("Container(5151).IsUpdating") and not cond("Container(5152).IsUpdating"), 10)
    n = {c: int(lab(f"Container({c}).NumItems") or 0) for c in (5151, 5152)}
    r.ok("each lookup holds at most one title", max(n.values()) <= 1, n)
    titles = person_titles(name)
    kind = ("Actor and director" if any(t["directed"] for t in titles) and acts(name) else
            "Director" if any(t["directed"] for t in titles) else "Actor")
    r.ok(f"still says {kind!r}", lab("Control.GetLabel(6151)") == kind, lab("Control.GetLabel(6151)"))
    close_detail()


@check("bgvideo_play")
def c_bgvideo_play(r):
    """A film plays in the background as the actor page opens, then stops; Play on a poster (the remote's Play key,
    or the poster's menu) then still goes full screen. Play on a title's page is handled by its Play button, so this
    is the timer's case. Test plays: two films without resume points, stopped within seconds."""
    if not r.ok("nothing playing", rpc("Player.GetActivePlayers")["result"] == []):
        return
    f = cast_film()
    name = f["cast"][1]["name"]
    fresh = lambda i: rpc("VideoLibrary.GetMovieDetails", {"movieid": i, "properties": ["resume"]})["result"]["moviedetails"]["resume"]["position"] == 0
    b = next((t for t in person_titles(name) if t["type"] == "movie" and t["id"] != f["movieid"] and fresh(t["id"])), None)
    if not b or not fresh(f["movieid"]):
        r.skip("needs two films of theirs without a resume point")
        return
    t0 = time.strftime("%H:%M:%S")
    try:
        rpc("Player.Open", {"item": {"movieid": f["movieid"]}})
        r.ok(f"background film {f['movieid']} plays", wait_for(lambda: cond("Player.HasVideo"), 90, 0.3))
        time.sleep(2)
        if not r.ok(f"{name}: actor page opens over it", open_person(f["movieid"], 1)):
            return
        for p in rpc("Player.GetActivePlayers")["result"]:
            rpc("Player.Stop", {"playerid": p["playerid"]})
        r.ok("background film stopped, actor page still open", wait_for(lambda: not cond("Player.HasVideo"), 10)
             and cond("Window.IsVisible(1115)"))
        grid = page_grid()
        for _ in range([g[1] for g in grid].index(b["id"])):
            rpc("Input.Right")
            time.sleep(0.3)
        if not r.ok(f"on {b['title']}'s poster", lab("Container(5150).ListItem.DBID") == str(b["id"]) and cond("Control.HasFocus(5150)"),
                    lab("Container(5150).ListItem.DBID")):
            return
        rpc("Input.ExecuteAction", {"action": "play"})
        playing_b = wait_for(lambda: lab("VideoPlayer.DBID") == str(b["id"]), 90, 0.3)
        r.ok(f"{b['title']} plays", playing_b, lab("VideoPlayer.DBID"))
        r.ok("full screen, actor page gone", playing_b and wait_for(
            lambda: cond("Window.IsActive(fullscreenvideo)") and not cond("Window.IsVisible(1115)"), 6, 0.2),
            (lab("System.CurrentWindow"), cond("Window.IsVisible(1115)")))
    finally:
        rpc("Input.ExecuteAction", {"action": "stop"})
        for p in rpc("Player.GetActivePlayers")["result"]:
            rpc("Player.Stop", {"playerid": p["playerid"]})
        print(f"INFO bgvideo_play: test plays of movies {f['movieid']} and {b['id']} from {t0}, stopped at {time.strftime('%H:%M:%S')}", flush=True)
        wait_for(lambda: rpc("Player.GetActivePlayers")["result"] == [], 10)
        close_detail()
        reset_home()
    r.ok("no resume points left behind", fresh(f["movieid"]) and fresh(b["id"]))


@check("back_search")
def c_back_search(r):
    """From a film found with Search: its cast → actor page → a poster → Back, Back returns to the film's page, Back
    again to Search."""
    f = cast_film()
    name = f["cast"][1]["name"]
    word = max(f["title"].split(), key=len).strip("'")
    run_search(word)
    ids = [int(i["DBID"] or 0) for i in list_items(5201, ["DBID"])]
    if not r.ok(f"search {word!r} finds {f['title']}", f["movieid"] in ids, ids[:8]):
        return reset_home()
    r.ok("films row focused", focus_id(5201, ("Down",), 4), lab("System.CurrentControlId"))
    for _ in range(ids.index(f["movieid"])):
        rpc("Input.Right")
        time.sleep(0.3)
    rpc("Input.Info")
    if not r.ok("its detail page opens", wait_for(lambda: cond("Window.IsVisible(movieinformation)") and lab("ListItem.DBID") == str(f["movieid"]), 8)):
        return close_detail()
    time.sleep(1.5)
    if not r.ok("cast row", focus_id(CAST, ("Down",), 4)):
        return close_detail()
    for _ in range(1):
        rpc("Input.Right")
        time.sleep(0.25)
    rpc("Input.Select")
    if not r.ok(f"{name}: actor page opens", wait_for(lambda: cond("Window.IsVisible(1115)"), 5)):
        return close_detail()
    grid = page_grid()
    other = next((g[1] for g in grid if g[1] != f["movieid"]), None)
    if not r.ok("a poster other than the film", other):
        return close_detail()
    open_poster(grid, other)
    r.ok("its page opens", on_detail(other))
    back_once()
    r.ok("Back: the actor page", wait_for(lambda: cond("Window.IsTopMost(1115)"), 5))
    back_once()
    r.ok("Back: the film's page again", on_detail(f["movieid"]))
    back_once()
    r.ok("Back: Search", wait_for(lambda: not cond("System.HasActiveModalDialog") and cond("Window.IsActive(1111)"), 5),
         lab("System.CurrentWindow"))
    close_detail()
    reset_home()


CHECKS["cleanup"] = kids_check.CHECKS["cleanup"]


if __name__ == "__main__":
    sys.exit(kids_check.main(sys.argv[1:], CHECKS))
