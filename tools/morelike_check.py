#!/usr/bin/env python3
"""More-like-this rows and studio-logo checks against a Kodi box over JSON-RPC.

    python3 tools/morelike_check.py NAME [NAME ...]   # run the named checks
    python3 tools/morelike_check.py all               # every check, then cleanup

Each check prints PASS/FAIL/SKIP lines; the exit status is 1 if anything failed.
Ground truth comes from the library (VideoLibrary.* filters) and from the logo pack's own file, never from the skin.
"""
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import actor_check  # noqa: E402
import kids_check  # noqa: E402
from actor_check import close_detail, open_detail  # noqa: E402
from kids_check import list_items  # noqa: E402
from kodi_gui import ADB_SERIAL, SSH, cond, lab, reset_home, rpc, shot, wait_for  # noqa: E402

CHECKS = {}
LOGO = 6170  # the studio logo at the end of the detail page's info line
PACK = "resource.images.studios.white"


def check(name):
    def register(fn):
        CHECKS[name] = fn
        return fn
    return register


# ---------- ground truth ----------

def pack_logos():
    """The logo names in the box's studio pack, lowercase without ".png". JSON-RPC can't list resource:// files,
    so they come from the names in the pack's Textures.xbt (Kodi matches them case-insensitively)."""
    rel = f"addons/{PACK}/resources/Textures.xbt"
    cmd = (["ssh", SSH, f"head -c 2000000 /storage/.kodi/{rel}"] if SSH else
           ["adb", "-s", ADB_SERIAL, "exec-out", f"head -c 2000000 /sdcard/Android/data/org.xbmc.kodi/files/.kodi/{rel}"])
    data = subprocess.run(cmd, capture_output=True, timeout=60).stdout
    return {m.decode("utf-8", "replace")[:-4] for m in re.findall(rb"[^\x00]{1,255}?\.png(?=\x00)", data)}


def pack_enabled():
    r = rpc("Addons.GetAddonDetails", {"addonid": PACK, "properties": ["enabled"]}).get("result")
    return bool(r and r["addon"]["enabled"])


def logo_film(logos):
    """The lowest-id film with exactly one studio, which has a logo in the pack: (movieid, studio)."""
    for m in actor_check._movies(["studio"]):
        if len(m["studio"]) == 1 and m["studio"][0].lower() in logos:
            return m["movieid"], m["studio"][0]
    return None


def logo_episode(logos):
    """An episode of the lowest-id show whose network has a logo in the pack: (episodeid, network)."""
    shows = rpc("VideoLibrary.GetTVShows", {"properties": ["studio"]})["result"].get("tvshows", [])
    for s in sorted(shows, key=lambda s: s["tvshowid"]):
        if len(s["studio"]) == 1 and s["studio"][0].lower() in logos:
            eps = rpc("VideoLibrary.GetEpisodes", {"tvshowid": s["tvshowid"], "limits": {"start": 0, "end": 1}})["result"]
            if eps.get("episodes"):
                return eps["episodes"][0]["episodeid"], s["studio"][0]
    return None


# ---------- studio logo ----------

@check("logo")
def c_logo(r):
    if not r.ok(f"{PACK} installed and enabled", pack_enabled()):
        return
    logos = pack_logos()
    r.ok("the pack's logo names read from the box", len(logos) > 1000, len(logos))
    film = logo_film(logos)
    if r.ok("a film whose studio has a logo", film is not None):
        movieid, studio = film
        if r.ok(f"{studio}: film page opens", open_detail("movie", movieid)):
            r.ok("its logo shows at the end of the info line",
                 wait_for(lambda: cond(f"Control.IsVisible({LOGO})"), 3) and lab("ListItem.Studio") == studio,
                 lab("ListItem.Studio"))
            r.ok("screenshot", bool(shot("morelike_logo_film")))
        close_detail()
    ep = logo_episode(logos)
    if r.ok("an episode of a show whose network has a logo", ep is not None):
        episodeid, network = ep
        if r.ok(f"{network}: episode page opens", open_detail("episode", episodeid)):
            r.ok("the show's network logo shows",
                 wait_for(lambda: cond(f"Control.IsVisible({LOGO})"), 3) and lab("ListItem.Studio") == network,
                 lab("ListItem.Studio"))
        close_detail()
    reset_home()


@check("logo_scope")
def c_logo_scope(r):
    """The info line is shared with the Home hero and the grids: only the detail page gets the logo."""
    r.ok("on Home", reset_home() == "Home")
    time.sleep(2)
    r.ok("no logo in the Home hero", not cond(f"Control.IsVisible({LOGO})"))


# ---------- rows: ground truth ----------

SET_ROW, DIRECTOR_ROW, GENRE_ROW = 5171, 5172, 5173
ROWS = (SET_ROW, DIRECTOR_ROW, GENRE_ROW)
HEADERS = {SET_ROW: 6171, DIRECTOR_ROW: 6172, GENRE_ROW: 6173}
DIRECTORS_LOOKUP = 5161
LIMITS = {SET_ROW: 25, DIRECTOR_ROW: 20, GENRE_ROW: 20}
FIELDS = ["title", "year", "rating", "genre", "tag"]


def _not_self(items, title, year):
    """The rows leave out the page's own title by title and year (a remake stays in)."""
    return [i for i in items if not (i["title"] == title and i["year"] == year)]


def _kids_only(items, kind):
    tagged = kids_check.tagged_movies() if kind == "movie" else kids_check.tagged_shows()
    return [i for i in items if i["id"] in tagged]


def _films(flt):
    return [dict(m, id=m["movieid"]) for m in actor_check._movies(FIELDS, flt)]


def _shows(flt):
    r = rpc("VideoLibrary.GetTVShows", {"filter": flt, "properties": FIELDS}, timeout=60)["result"]
    return [dict(s, id=s["tvshowid"]) for s in r.get("tvshows", [])]


def truth(kind, d, kids=False):
    """What each row should hold for the title d (VideoLibrary details): {row: (items, sort key, descending)}."""
    out = {}
    pick = (lambda items: _kids_only(items, kind)) if kids else (lambda items: items)
    if kind == "movie" and d.get("setid"):
        films = rpc("VideoLibrary.GetMovieSetDetails", {"setid": d["setid"], "movies": {"properties": FIELDS}})["result"]["setdetails"]["movies"]
        out[SET_ROW] = (pick(_not_self([dict(m, id=m["movieid"]) for m in films], d["title"], d["year"])), "year", False)
    if kind == "movie" and d["director"]:
        out[DIRECTOR_ROW] = (pick(_not_self(_films({"or": [actor_check._is("director", n) for n in d["director"][:3]]}), d["title"], d["year"])), "year", True)
    if kind in ("movie", "tvshow") and d["genre"]:
        get = _films if kind == "movie" else _shows
        out[GENRE_ROW] = (pick(_not_self(get(actor_check._is("genre", d["genre"][0])), d["title"], d["year"])), "rating", True)
    return out


def details(kind, dbid):
    if kind == "movie":
        return rpc("VideoLibrary.GetMovieDetails", {"movieid": dbid, "properties": ["title", "year", "setid", "set", "director", "genre"]})["result"]["moviedetails"]
    return rpc("VideoLibrary.GetTVShowDetails", {"tvshowid": dbid, "properties": ["title", "year", "genre"]})["result"]["tvshowdetails"]


def rows_loaded():
    busy = [f"Container({i}).IsUpdating" for i in ROWS + (DIRECTORS_LOOKUP,)]
    return wait_for(lambda: not any(cond(b) for b in busy), 15, 0.3) and time.sleep(0.5) is None and not any(cond(b) for b in busy)


def check_row(r, row, want, what):
    """The row holds the right titles: all of them in order, or for a limited row the first N by its sort key."""
    items, key, desc = want
    got = [i["DBID"] for i in list_items(row, ["DBID"])]
    byid = {str(i["id"]): i for i in items}
    limit = LIMITS[row]
    ranked = sorted(items, key=lambda i: i[key], reverse=desc)
    r.ok(f"{what}: {min(limit, len(items))} titles", len(got) == min(limit, len(items)), (len(got), len(items)))
    r.ok(f"{what}: only titles that belong", all(g in byid for g in got), [g for g in got if g not in byid][:5])
    keys = [byid[g][key] for g in got if g in byid]
    r.ok(f"{what}: ordered by {key}{' (high first)' if desc else ''}", keys == sorted(keys, reverse=desc), keys[:8])
    if len(items) > limit and keys:
        cut = ranked[limit - 1][key]
        r.ok(f"{what}: the first {limit} by {key}", (min(keys) >= cut) if desc else (max(keys) <= cut), (keys[-1], cut))


def unique(films):
    """Films whose title and year no other film shares (a film added twice can't be picked out of a listing)."""
    seen = {}
    for m in films:
        seen[(m["title"], m["year"])] = seen.get((m["title"], m["year"]), 0) + 1
    return [m for m in films if seen[(m["title"], m["year"])] == 1]


def film_with_rows():
    """The lowest-id film in a collection of 3+, with one director who made 2+ other films here."""
    films = actor_check._movies(["setid", "director", "title", "year"])
    sets, made = {}, {}
    for m in films:
        if m["setid"]:
            sets[m["setid"]] = sets.get(m["setid"], 0) + 1
        for n in m["director"]:
            made[n] = made.get(n, 0) + 1
    for m in unique(films):
        if sets.get(m["setid"], 0) >= 3 and len(m["director"]) == 1 and made[m["director"][0]] >= 3:
            return m["movieid"]
    return None


def check_title_rows(r, kind, dbid, kids=False):
    d = details(kind, dbid)
    want = truth(kind, d, kids)
    if not r.ok(f"{d['title']} ({d['year']}): page opens", open_detail(kind, dbid)):
        return close_detail()
    r.ok("rows loaded", rows_loaded())
    for row in ROWS:
        if row in want and want[row][0]:
            check_row(r, row, want[row], {SET_ROW: "collection row", DIRECTOR_ROW: "director row", GENRE_ROW: "genre row"}[row])
        else:
            r.ok(f"row {row} empty", lab(f"Container({row}).NumItems") in ("", "0"), lab(f"Container({row}).NumItems"))
    return d, want


# ---------- rows ----------

@check("rows_film")
def c_rows_film(r):
    dbid = film_with_rows()
    if not r.ok("a film in a collection of 3+ by a director with 2+ other films", dbid is not None):
        return
    d, want = check_title_rows(r, "movie", dbid) or (None, None)
    if d:
        r.ok("headers", [lab(f"Control.GetLabel({HEADERS[i]})") for i in ROWS] ==
             [d["set"], f"Directed by {d['director'][0]}", f"More {d['genre'][0]}"],
             [lab(f"Control.GetLabel({HEADERS[i]})") for i in ROWS])
    close_detail()
    reset_home()


@check("rows_codirected")
def c_rows_codirected(r):
    f = actor_check.codirected_film()
    if not f:
        r.skip("no co-directed film")
        return
    check_title_rows(r, "movie", f["movieid"])
    r.ok("header names every director", lab(f"Control.GetLabel({HEADERS[DIRECTOR_ROW]})") == "Directed by " + " / ".join(f["director"]),
         lab(f"Control.GetLabel({HEADERS[DIRECTOR_ROW]})"))
    close_detail()
    reset_home()


@check("rows_show")
def c_rows_show(r):
    """A show page: no collection or director row, the genre row lists shows."""
    shows = rpc("VideoLibrary.GetTVShows", {"properties": ["genre"]})["result"]["tvshows"]
    s = next((s for s in sorted(shows, key=lambda s: s["tvshowid"]) if s["genre"]
              and len(_shows(actor_check._is("genre", s["genre"][0]))) >= 3), None)
    if not r.ok("a show sharing its first genre with 2+ shows", s is not None):
        return
    check_title_rows(r, "tvshow", s["tvshowid"])
    close_detail()
    reset_home()


@check("rows_episode")
def c_rows_episode(r):
    ep = rpc("VideoLibrary.GetEpisodes", {"limits": {"start": 0, "end": 1}})["result"]["episodes"][0]
    if not r.ok("episode page opens", open_detail("episode", ep["episodeid"])):
        return close_detail()
    rows_loaded()
    r.ok("no rows on an episode page", all(lab(f"Container({i}).NumItems") in ("", "0") for i in ROWS),
         [lab(f"Container({i}).NumItems") for i in ROWS])
    close_detail()
    reset_home()


@check("rows_odd")
def c_rows_odd(r):
    """A title the rows can't put in their paths ("&", "+"): the rows still list the right titles (the page's own
    title may then show in its own rows)."""
    for ch in ("&", "+"):
        m = next(iter(unique(actor_check._movies(["genre", "title", "year"], {"field": "title", "operator": "contains", "value": ch}))), None)
        if not m or not m["genre"]:
            r.skip(f"no film with {ch!r} in its title")
            continue
        d = details("movie", m["movieid"])
        if not r.ok(f"{d['title']}: page opens", open_detail("movie", m["movieid"])):
            close_detail()
            continue
        rows_loaded()
        genre = {str(f["id"]) for f in _films(actor_check._is("genre", d["genre"][0]))}
        got = [i["DBID"] for i in list_items(GENRE_ROW, ["DBID"])]
        r.ok(f"{ch!r}: the genre row lists {d['genre'][0]} films", got and all(g in genre for g in got), len(got))
        close_detail()
    reset_home()


@check("rows_kids")
def c_rows_kids(r):
    """Kids mode: every row lists tagged titles only, and all of them."""
    tagged = kids_check.tagged_movies()
    films = [m for m in unique(actor_check._movies(["setid", "title", "year"])) if m["movieid"] in tagged and m["setid"]]
    sets = {}
    for m in films:
        sets[m["setid"]] = sets.get(m["setid"], 0) + 1
    m = next((m for m in films if sets[m["setid"]] >= 2), None)
    if not r.ok("a kids film in a collection with another kids film", m is not None):
        return
    try:
        kids_check.kids(True)
        _, want = check_title_rows(r, "movie", m["movieid"], kids=True) or (None, None)
        for row in ROWS:
            got = {i["DBID"] for i in list_items(row, ["DBID"])}
            r.ok(f"row {row}: tagged titles only", all(int(g) in tagged for g in got), [g for g in got if int(g) not in tagged][:5])
    finally:
        close_detail()
        kids_check.kids(False)
        reset_home()


# ---------- walking the rows, OK ----------

CAST = actor_check.CAST


def focused():
    return lab("System.CurrentControlId")


def press(key, target, timeout=3):
    rpc("Input." + key)
    return wait_for(lambda: focused() == str(target), timeout)


def film_without_set():
    """The lowest-id film with a cast, no collection, and a director with 2+ other films."""
    films = actor_check._movies(["setid", "director", "title", "year"])
    made = {}
    for m in films:
        for n in m["director"]:
            made[n] = made.get(n, 0) + 1
    for m in unique(films):
        if not m["setid"] and len(m["director"]) == 1 and made[m["director"][0]] >= 3 and actor_check._film(m["movieid"])["cast"]:
            return m["movieid"]
    return None


@check("nav")
def c_nav(r):
    dbid = film_with_rows()
    if not r.ok("a film with all three rows", dbid is not None) or not r.ok("its page opens", open_detail("movie", dbid)):
        return close_detail()
    rows_loaded()
    r.ok("Down: the cast row", press("Down", CAST), focused())
    for row in ROWS:
        r.ok(f"Down: row {row}", press("Down", row), focused())
    rpc("Input.Down")
    time.sleep(0.6)
    r.ok("Down on the last row stays", focused() == str(GENRE_ROW), focused())
    for row in (DIRECTOR_ROW, SET_ROW, CAST):
        r.ok(f"Up: {row}", press("Up", row), focused())
    press("Down", SET_ROW)
    rpc("Input.Back")
    r.ok("Back on a row: Play", wait_for(lambda: focused() == "8", 3) and cond("Window.IsVisible(movieinformation)"), focused())
    close_detail()
    dbid = film_without_set()
    if r.ok("a film without a collection", dbid is not None) and r.ok("its page opens", open_detail("movie", dbid)):
        rows_loaded()
        press("Down", CAST)
        r.ok("Down from the cast skips the empty collection row", press("Down", DIRECTOR_ROW), focused())
        r.ok("Up skips it too", press("Up", CAST), focused())
    close_detail()
    films = actor_check._movies(["cast", "director", "genre", "title", "year"])
    m = next((m for m in unique(films) if m["director"] and not m["cast"] and m["genre"]), None)
    if m and r.ok("a film with directors, a genre and no cast: page opens", open_detail("movie", m["movieid"])):
        rows_loaded()
        r.ok("Down: its directors", press("Down", actor_check.DIRECTORS), focused())
        first = next((i for i in ROWS if lab(f"Container({i}).NumItems") not in ("", "0")), None)
        r.ok("Down: the first row with titles", first and press("Down", first), (focused(), first))
        close_detail()
    reset_home()


@check("swap")
def c_swap(r):
    """OK on a film in a row: the page becomes that film's, focus on Play, its rows reloaded; Back closes the page."""
    dbid = film_with_rows()
    if not r.ok("its page opens", open_detail("movie", dbid)):
        return close_detail()
    rows_loaded()
    path = lab("Container.FolderPath")
    for hop in (1, 2):
        before = lab("ListItem.DBID")
        if not r.ok(f"hop {hop}: on the collection row", press("Down", CAST) and press("Down", SET_ROW), focused()):
            break
        target = lab(f"Container({SET_ROW}).ListItem.DBID")
        rpc("Input.Select")
        r.ok(f"hop {hop}: the page shows that film", wait_for(lambda: lab("ListItem.DBID") == target, 6) and cond("Window.IsVisible(movieinformation)"),
             (lab("ListItem.DBID"), target))
        r.ok(f"hop {hop}: focus on Play", wait_for(lambda: focused() == "8", 3), focused())
        r.ok(f"hop {hop}: its cast row filled", wait_for(lambda: lab("Container(50).NumItems") not in ("", "0"), 5) or not actor_check._film(int(target))["cast"],
             lab("Container(50).NumItems"))
        r.ok(f"hop {hop}: rows reloaded", rows_loaded())
        got = [i["DBID"] for i in list_items(SET_ROW, ["DBID"])]
        r.ok(f"hop {hop}: its collection row lists the film we came from, not itself", before in got and target not in got, got)
    rpc("Input.Back")
    r.ok("Back closes the page", wait_for(lambda: not cond("Window.IsVisible(movieinformation)") and not cond("Window.IsVisible(1116)"), 5))
    r.ok("back on the listing it was opened from", lab("Container.FolderPath") == path, lab("Container.FolderPath")[:80])
    close_detail()
    reset_home()


@check("open_fail")
def c_open_fail(r):
    """The helper that opens a film picked in a row (window 1116) gives up when the film doesn't load, instead of
    leaving its backdrop on screen. Needs builtins, so LibreELEC only (kodi-send over SSH)."""
    if not SSH:
        return r.skip("needs kodi-send (LibreELEC)")
    reset_home()
    gone = max(m["movieid"] for m in actor_check._movies([])) + 100000
    for action in (f"SetProperty(lumen.openid,{gone},home)", "ActivateWindow(1116)"):
        subprocess.run(["ssh", SSH, f'kodi-send --action="{action}"'], capture_output=True, timeout=30)
    r.ok("the helper opens for a film that isn't in the library", wait_for(lambda: cond("Window.IsVisible(1116)"), 5))
    r.ok("it closes by itself", wait_for(lambda: not cond("Window.IsVisible(1116)"), 20))
    r.ok("and clears the film it was given", lab("Window(home).Property(lumen.openid)") == "",
         lab("Window(home).Property(lumen.openid)"))
    reset_home()


@check("show_ok")
def c_show_ok(r):
    """OK on a show in a row opens its show page; Back returns to where the detail page was opened from."""
    shows = rpc("VideoLibrary.GetTVShows", {"properties": ["genre"]})["result"]["tvshows"]
    s = next((s for s in sorted(shows, key=lambda s: s["tvshowid"]) if s["genre"]
              and len(_shows(actor_check._is("genre", s["genre"][0]))) >= 2), None)
    if not r.ok("a show sharing its first genre", s is not None) or not r.ok("its page opens", open_detail("tvshow", s["tvshowid"])):
        return close_detail()
    rows_loaded()
    path = lab("Container.FolderPath")
    for _ in range(4):
        if focused() == str(GENRE_ROW):
            break
        rpc("Input.Down")
        time.sleep(0.5)
    if not r.ok("on the genre row", focused() == str(GENRE_ROW), focused()):
        return close_detail()
    target = lab(f"Container({GENRE_ROW}).ListItem.DBID")
    rpc("Input.Select")
    r.ok("its show page opens", wait_for(lambda: not cond("Window.IsVisible(movieinformation)")
                                          and lab("Container.FolderPath").startswith(f"videodb://tvshows/titles/{target}/"), 8),
         lab("Container.FolderPath")[:80])
    rpc("Input.Back")
    r.ok("Back: where the page was opened from", wait_for(lambda: lab("Container.FolderPath") == path, 6), lab("Container.FolderPath")[:80])
    close_detail()
    reset_home()


@check("stale")
def c_stale(r):
    """A row whose title had none, then one that has some (and back): never the other page's titles."""
    a, b = film_without_set(), film_with_rows()
    for first, second in ((a, b), (b, a)):
        open_detail("movie", first)
        rows_loaded()
        close_detail()
        if not r.ok(f"{second}: page opens", open_detail("movie", second)):
            close_detail()
            continue
        rows_loaded()
        d = details("movie", second)
        want = {str(i["id"]) for i in truth("movie", d).get(SET_ROW, ([], "", False))[0]}
        got = {i["DBID"] for i in list_items(SET_ROW, ["DBID"])}
        r.ok(f"{d['title']}: its own collection row", got == want, (len(got), len(want)))
        close_detail()
    reset_home()


@check("screens")
def c_screens(r):
    """Screenshots with each row focused, for the eye."""
    dbid = film_with_rows()
    if not r.ok("its page opens", open_detail("movie", dbid)):
        return close_detail()
    rows_loaded()
    time.sleep(1)
    r.ok("page", bool(shot("morelike_page")))
    press("Down", CAST)
    for row in ROWS:
        press("Down", row)
        time.sleep(1)
        r.ok(f"row {row}", bool(shot(f"morelike_row_{row}")))
    close_detail()
    reset_home()


@check("kids_nav")
def c_kids_nav(r):
    """Kids mode: no cast row, Down from the buttons reaches the rows; OK on a film opens its page, on a show its show page."""
    tagged = kids_check.tagged_movies()
    films = [m for m in unique(actor_check._movies(["setid", "title", "year"])) if m["movieid"] in tagged and m["setid"]]
    sets = {}
    for m in films:
        sets[m["setid"]] = sets.get(m["setid"], 0) + 1
    m = next((m for m in films if sets[m["setid"]] >= 2), None)
    kshows = kids_check.tagged_shows()
    shows = [s for s in rpc("VideoLibrary.GetTVShows", {"properties": ["genre"]})["result"]["tvshows"] if s["tvshowid"] in kshows and s["genre"]]
    show = next((s for s in sorted(shows, key=lambda s: s["tvshowid"])
                 if len([x for x in _shows(actor_check._is("genre", s["genre"][0])) if x["id"] in kshows]) >= 2), None)
    try:
        kids_check.kids(True)
        if r.ok("a kids film in a collection: page opens", m is not None and open_detail("movie", m["movieid"])):
            rows_loaded()
            r.ok("no cast row", not cond("Control.IsVisible(6050)"))
            r.ok("Down from the buttons: the collection row", press("Down", SET_ROW), focused())
            target = lab(f"Container({SET_ROW}).ListItem.DBID")
            rpc("Input.Select")
            r.ok("OK: that film's page", wait_for(lambda: lab("ListItem.DBID") == target and cond("Window.IsVisible(movieinformation)")
                                                    and not cond("Window.IsVisible(1116)"), 8), lab("ListItem.DBID"))
            r.ok("a kids film", int(target) in tagged)
        close_detail()
        if show and r.ok("a kids show sharing its genre: page opens", open_detail("tvshow", show["tvshowid"])):
            rows_loaded()
            r.ok("no buttons on a kids show page: focus starts on the genre row", wait_for(lambda: focused() == str(GENRE_ROW), 3), focused())
            target = lab(f"Container({GENRE_ROW}).ListItem.DBID")
            r.ok("a kids show", int(target) in kshows)
            rpc("Input.Select")
            r.ok("OK: its show page", wait_for(lambda: lab("Container.FolderPath").startswith(f"videodb://tvshows/titles/{target}/"), 8),
                 lab("Container.FolderPath")[:60])
        elif not show:
            r.skip("no kids show sharing its first genre with another")
    finally:
        close_detail()
        kids_check.kids(False)
        reset_home()


@check("kids_stale")
def c_kids_stale(r):
    """Kids mode right after a grown-up film's page: Down and OK while the new page's rows still hold that film's
    collection (a list keeps its items until its new ones load) never open an untagged film."""
    tagged = kids_check.tagged_movies()
    films = unique(actor_check._movies(["setid", "title", "year"]))
    members = {}
    for m in films:
        if m["setid"]:
            members.setdefault(m["setid"], []).append(m["movieid"])
    grown = next((m for m in films if len(members.get(m["setid"], [])) >= 2
                  and not any(i in tagged for i in members[m["setid"]])), None)
    kid = next((m for m in films if m["movieid"] in tagged), None)
    if not r.ok("a grown-up film in a collection and a kids film", grown and kid):
        return
    try:
        if not r.ok("the grown-up film's page opens, its collection row loads", open_detail("movie", grown["movieid"]) and rows_loaded()):
            return
        rpc("Input.Back")
        wait_for(lambda: not cond("Window.IsVisible(movieinformation)"), 5)
        kids_check.kids(True)
        path = actor_check._xsp_path("videodb://movies/titles/", "movies",
                                     [actor_check._is("title", [kid["title"]]), actor_check._is("year", [str(kid["year"])])])
        if not r.ok("the kids film is listed", actor_check.open_path(path)
                    and (lab("ListItem.DBID") == str(kid["movieid"]) or actor_check.goto_dbid(kid["movieid"]))):
            return
        rpc("Input.Info")
        wait_for(lambda: cond("Window.IsVisible(movieinformation)"), 5, 0.02)
        stale = cond(f"Container({SET_ROW}).IsUpdating")
        rpc("Input.Down")
        rpc("Input.Select")
        time.sleep(3)
        shown = lab("ListItem.DBID") if cond("Window.IsVisible(movieinformation)") else ""
        r.ok("Down and OK at once: still on a kids film", shown.isdigit() and int(shown) in tagged,
             f"page shows {shown or 'nothing'}, the row was {'still' if stale else 'no longer'} loading")
    finally:
        close_detail()
        kids_check.kids(False)
        reset_home()


@check("rows_speed")
def c_rows_speed(r):
    """Time Info → detail page shown with its cast and rows loaded (compare with actor_check detail_speed, which waits
    for the cast only)."""
    dbid = film_with_rows()
    if not open_detail("movie", dbid):
        r.ok("detail page opens", False)
        return close_detail()
    rpc("Input.Back")
    wait_for(lambda: not cond("Window.IsVisible(movieinformation)"), 5)
    busy = [f"Container({i}).IsUpdating" for i in (50, actor_check.DIRECTORS, DIRECTORS_LOOKUP) + ROWS]
    times = []
    for _ in range(5):
        time.sleep(1.5)
        t0 = time.time()
        rpc("Input.Info")
        shown = wait_for(lambda: cond("Window.IsVisible(movieinformation)") and not any(cond(b) for b in busy), 15, 0.02)
        times.append(round(time.time() - t0, 3))
        rpc("Input.Back")
        wait_for(lambda: not cond("Window.IsVisible(movieinformation)"), 5)
        if not shown:
            break
    times.sort()
    r.ok("page and rows load every time", len(times) == 5 and times[-1] < 15, times)
    print(f"INFO rows_speed: median {times[len(times) // 2]} s, all {times}", flush=True)
    close_detail()
    reset_home()


@check("log_clean")
def c_log_clean(r):
    """The pages this feature touches load without skin errors or failed fetches (actor_check.check_log)."""
    def visit(r):
        dbid = film_with_rows()
        r.ok("a film page with rows opens", open_detail("movie", dbid) and rows_loaded())
        press("Down", CAST)
        press("Down", SET_ROW)
        target = lab(f"Container({SET_ROW}).ListItem.DBID")
        rpc("Input.Select")
        r.ok("a film from a row opens", wait_for(lambda: lab("ListItem.DBID") == target and not cond("Window.IsVisible(1116)"), 8))
        rows_loaded()
        close_detail()
        r.ok("an episode page opens", open_detail("episode", rpc("VideoLibrary.GetEpisodes", {"limits": {"start": 0, "end": 1}})["result"]["episodes"][0]["episodeid"]))
        close_detail()
    actor_check.check_log(r, visit)


CHECKS["cleanup"] = kids_check.CHECKS["cleanup"]


if __name__ == "__main__":
    sys.exit(kids_check.main(sys.argv[1:], CHECKS))
