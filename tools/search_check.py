#!/usr/bin/env python3
"""Search page checks (Home > Search, Custom_1111_LumenSearch.xml and the keyboard's live results) against a Kodi box.

    python3 tools/search_check.py NAME [NAME ...]   # run the named checks
    python3 tools/search_check.py all               # every check, then cleanup

Each check prints PASS/FAIL/SKIP lines; the exit status is 1 if anything failed.
Ground truth comes from the library (VideoLibrary.*) with Lumen's rule written again here: every typed word starts a
word of the title or the original title (after a space, ', ’ or -), in any order; the database ignores case and
accents. Searches build their paths in skin.lumen/scripts/lumen_search.py, which leaves them in Home window
properties (lumen.sq.*).
"""
import os
import re
import subprocess
import sys
import time
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kids_check  # noqa: E402
from kids_check import list_items, tab_bar, tagged_movies, tagged_shows  # noqa: E402
from kodi_gui import SSH, cond, lab, labs, reset_home, rpc, set_skin, wait_for  # noqa: E402

CHECKS = {}
FILMS, SHOWS, PEOPLE = 5201, 5202, 5203  # the search page's rows
PREVIEW = 9511  # the keyboard's live films row
SEPS = (" ", "'", "’", "-")
SCRIPT = "/storage/.kodi/addons/skin.lumen/scripts/lumen_search.py"


def check(name):
    def register(fn):
        CHECKS[name] = fn
        return fn
    return register


# ---------- ground truth ----------

def fold(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s.casefold()) if not unicodedata.combining(c))


def word_hit(word, text):
    t, w = fold(text), fold(word)
    return t.startswith(w) or any(s + w in t for s in SEPS)


def title_hit(words, item):
    return all(word_hit(w, item["title"]) or word_hit(w, item.get("originaltitle", "")) for w in words)


_LIB = {}


def library(kind):
    if kind not in _LIB:
        if kind == "movies":
            r = rpc("VideoLibrary.GetMovies", {"properties": ["title", "originaltitle"]}, timeout=120)["result"]
            _LIB[kind] = [dict(m, id=m["movieid"]) for m in r.get("movies", [])]
        else:
            r = rpc("VideoLibrary.GetTVShows", {"properties": ["title", "originaltitle"]}, timeout=120)["result"]
            _LIB[kind] = [dict(s, id=s["tvshowid"]) for s in r.get("tvshows", [])]
    return _LIB[kind]


def truth(kind, text, pool=None):
    words = text.split()[:6]
    return {m["id"] for m in library(kind) if title_hit(words, m) and (pool is None or m["id"] in pool)}


# ---------- driving the page ----------

def sq():
    v = labs(["Window(home).Property(lumen.sq.text)", "Window(home).Property(lumen.sq.kids)"])
    return v["Window(home).Property(lumen.sq.text)"], v["Window(home).Property(lumen.sq.kids)"]


def open_keyboard():
    """Home > Search tab: the page opens with Kodi's keyboard over it."""
    reset_home()
    time.sleep(1.5)
    if not tab_bar(16):
        return False
    rpc("Input.Select")
    return wait_for(lambda: cond("Window.IsVisible(virtualkeyboard)") and cond("Window.IsVisible(1111)"), 8)


def rows_loaded(rows=(FILMS, SHOWS, PEOPLE), timeout=25):
    return wait_for(lambda: not any(cond(f"Container({c}).IsUpdating") for c in rows), timeout, 0.5)


def search(text, kids=False):
    """Type text, confirm, wait for the script's paths for it and the rows. Returns False if the page never got there."""
    set_skin("cinemathequekids", kids)
    if not open_keyboard():
        return False
    rpc("Input.SendText", {"text": text, "done": True})
    if not wait_for(lambda: not cond("Window.IsVisible(virtualkeyboard)"), 8):
        return False
    if not wait_for(lambda: sq() == (text, "1" if kids else ""), 10, 0.3):
        return False
    time.sleep(1)
    return rows_loaded()


def ids(cid):
    return {int(i["DBID"]) for i in list_items(cid, ["DBID"]) if i["DBID"]}


def shown(cid):
    """The row's titles on screen: none while the results (group 9199) are hidden or faded out (a row loading),
    whatever the lists still hold."""
    if not cond("Control.IsVisible(9199)") or any(cond(f"Container({c}).IsUpdating") for c in (FILMS, SHOWS, PEOPLE)):
        return set()
    return ids(cid)


def pick_amp():
    """A film title with " & " between two words: (query, source id)."""
    for m in library("movies"):
        g = re.search(r"(\w{3,}) & (\w{3,})", m["title"])
        if g:
            return f"{g.group(1)} & {g.group(2)}", m["id"]
    return None, None


def pick_two_words():
    for m in library("movies"):
        ws = re.findall(r"[A-Za-z]{4,}", m["title"])
        if len(ws) >= 2 and len(truth("movies", f"{ws[1]} {ws[0]}")) <= 50:
            return f"{ws[1]} {ws[0]}", m["id"]
    return None, None


def pick_inner_word():
    """A word that starts after - or ' in a film title (Spider-Man, L'Odyssée), and only there."""
    for m in library("movies"):
        for g in re.finditer(r"[-'’]([A-Za-z]{3,})", m["title"]):
            w = g.group(1)
            if not fold(m["title"]).startswith(fold(w)) and f" {fold(w)}" not in fold(m["title"]) and len(truth("movies", w)) <= 50:
                return w, m["id"]
    return None, None


# ---------- checks ----------

@check("amp")
def c_amp(r):
    q, src = pick_amp()
    if not q:
        r.skip("no film title with ' & ' in the library")
        return
    r.ok(f"search '{q}' reaches the page", search(q))
    want = truth("movies", q)
    got = ids(FILMS)
    r.ok(f"films row: every film matching '{q}', nothing else", got == want and src in got, f"{len(got)} vs {len(want)}")


@check("order")
def c_order(r):
    q, src = pick_two_words()
    if not q:
        r.skip("no two-word film title")
        return
    r.ok(f"search '{q}' (words reversed) reaches the page", search(q))
    want = truth("movies", q)
    got = ids(FILMS)
    r.ok("films row: every film with both words, any order", got == want and src in got, f"{len(got)} vs {len(want)}")


@check("wordstart")
def c_wordstart(r):
    q, src = pick_inner_word()
    if not q:
        r.skip("no film title with a word after - or '")
        return
    r.ok(f"search '{q}' reaches the page", search(q))
    got = ids(FILMS)
    r.ok(f"films row finds the title with '{q}' after - or '", src in got and got == truth("movies", q), f"{len(got)} films")


@check("odd_chars")
def c_odd_chars(r):
    for q in ('a"b', "x,y", "50%", "c+", "back\\slash"):
        ok = search(q)
        r.ok(f"'{q}': the page builds paths for exactly this text", ok and sq()[0] == q, repr(sq()[0]))
        r.ok(f"'{q}': still on the search page", cond("Window.IsActive(1111)"), lab("System.CurrentWindow"))


@check("typing")
def c_typing(r):
    word = next((w for w in ("spider", "dragon", "star", "love") if truth("movies", w)), None)
    if not word:
        r.skip("no test word with films")
        return
    set_skin("cinemathequekids", False)
    r.ok("keyboard over the search page", open_keyboard())
    seen = set()
    for i in range(2, len(word) + 1):
        rpc("Input.SendText", {"text": word[:i], "done": False})
        t0 = time.time()
        while time.time() - t0 < 0.25:
            seen.add(sq()[0])
            time.sleep(0.05)
    last = time.time()
    r.ok("no search while the text keeps changing", not (seen & {word[:i] for i in range(2, len(word))}), sorted(seen))
    early = sq()[0] == word
    r.ok("waits after the last key", wait_for(lambda: sq()[0] == word, 6, 0.1), sq())
    took = time.time() - last
    r.ok("searches about 1 s after the last key", not early and 0.8 <= took <= 4, f"{took:.1f} s")
    rows_loaded((PREVIEW,))
    got = ids(PREVIEW)
    r.ok("live films are the word's films", got and got <= truth("movies", word), f"{len(got)} films")
    reset_home()


@check("kids_stale")
def c_kids_stale(r):
    q = kids_check.search_query()
    if not q:
        r.skip("no word shared by tagged and untagged titles")
        return
    movies, shows = tagged_movies(), tagged_shows()
    r.ok(f"normal search '{q}'", search(q, kids=False))
    r.ok("normal: untagged films found, on screen", bool(shown(FILMS) - movies), f"{len(shown(FILMS))} shown")
    set_skin("cinemathequekids", True)
    leak = set()
    t0 = time.time()
    while time.time() - t0 < 6:
        leak |= (shown(FILMS) - movies) | (shown(SHOWS) - shows)
        time.sleep(0.2)
    r.ok("kids on: never an untagged title, even for a moment", not leak, sorted(leak)[:5])
    r.ok("kids on: the search is built again for kids", wait_for(lambda: sq() == (q, "1"), 10), sq())
    rows_loaded()
    got = ids(FILMS)
    r.ok("kids: tagged films only, and some", got and got <= movies, f"{len(got)} films")
    r.ok("kids: no cast & crew row", not cond("Control.IsVisible(5203)"))
    set_skin("cinemathequekids", False)
    reset_home()


@check("fail_closed")
def c_fail_closed(r):
    if not SSH:
        r.skip("needs SSH to the box (KODI_SSH) to hide the script")
        return
    q1, q = [w for w in ("dragon", "star", "love") if truth("movies", w)][:2]
    r.ok(f"search '{q1}' shows films", search(q1) and bool(shown(FILMS)))
    moved = subprocess.run(["ssh", SSH, f"mv {SCRIPT} {SCRIPT}.off"], capture_output=True, timeout=30).returncode == 0
    try:
        r.ok("script hidden", moved)
        set_skin("cinemathequekids", False)
        r.ok("keyboard over the search page", open_keyboard())
        rpc("Input.SendText", {"text": q, "done": True})
        time.sleep(6)
        r.ok("no path for a text the script never built", sq()[0] != q, sq())
        r.ok("no films shown", not shown(FILMS), len(shown(FILMS)))
    finally:
        if moved:
            subprocess.run(["ssh", SSH, f"mv {SCRIPT}.off {SCRIPT}"], capture_output=True, timeout=30)
        reset_home()


@check("hint")
def c_hint(r):
    for kids in (False, True):
        set_skin("lumen.search", "")
        set_skin("cinemathequekids", kids)
        r.ok(f"kids={kids}: keyboard over the search page", open_keyboard())
        rpc("Input.Back")
        wait_for(lambda: not cond("Window.IsVisible(virtualkeyboard)"), 5)
        hint = lab("Control.GetLabel(1190)")
        if kids:
            r.ok("kids: the hint offers titles only", hint and not re.search("actor|director", hint, re.I), hint)
        else:
            r.ok("normal: the hint offers actors and directors", bool(re.search("actor", hint, re.I)), hint)
    set_skin("cinemathequekids", False)
    reset_home()


@check("cleanup")
def c_cleanup(r):
    for setting, value in (("cinemathequekids", False), ("lumen.search", "")):
        set_skin(setting, value)
    r.ok("back on Home", reset_home() == "Home")
    r.ok("kids mode off", not cond("Skin.HasSetting(CinemathequeKids)"))


if __name__ == "__main__":
    sys.exit(kids_check.main(sys.argv[1:], CHECKS))
