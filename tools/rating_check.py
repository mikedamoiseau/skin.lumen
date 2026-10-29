#!/usr/bin/env python3
"""Age rating badge checks (the rating beside the year and runtime) against the skin's XML and a Kodi box.

    python3 tools/rating_check.py NAME [NAME ...]   # run the named checks
    python3 tools/rating_check.py all               # every check, then cleanup

Each check prints PASS/FAIL/SKIP lines; the exit status is 1 if anything failed.
Kodi has no infolabel for a badge without an id, so `map` reads the badges from the skin's detail line and holds them
against every rating in the library; `detail` opens rated films and leaves screenshots to look at.
"""
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kids_check  # noqa: E402
from actor_check import close_detail, open_detail  # noqa: E402
from kodi_gui import lab, reset_home, rpc, shot  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECKS = {}
ITEM = "$PARAM[item]"


def check(name):
    def register(fn):
        CHECKS[name] = fn
        return fn
    return register


def badges():
    """[(label, visible)] of the badges in LumenMetaLine that read the rating, in order."""
    tree = ET.parse(os.path.join(ROOT, "skin.lumen", "xml", "Includes_Lumen.xml"))
    line = next(i for i in tree.getroot().iter("include") if i.get("name") == "LumenMetaLine")
    out = []
    for inc in line.iter("include"):
        if inc.get("content") != "LumenBadge":
            continue
        p = {x.get("name"): x.get("value") for x in inc.iter("param")}
        if "Mpaa" in p.get("visible", "") or "Mpaa" in p.get("label", ""):
            out.append((p.get("label"), p.get("visible")))
    return out


def ratings():
    """{rating: count} over every film and show in the library."""
    out = {}
    for method, key in (("VideoLibrary.GetMovies", "movies"), ("VideoLibrary.GetTVShows", "tvshows")):
        for m in rpc(method, {"properties": ["mpaa"]}, timeout=120)["result"].get(key, []):
            out[m["mpaa"]] = out.get(m["mpaa"], 0) + 1
    return out


@check("map")
def c_map(r):
    """Every US rating in the library has a badge without "Rated "; "NR" and empty show none; anything else (the app's
    "FSK 12") shows as written."""
    b = badges()
    r.ok("the detail line has rating badges", b, b)
    generic = [v for label, v in b if label == f"$INFO[{ITEM}.Mpaa]"]
    r.ok("one badge shows the rating as written", len(generic) == 1, generic)
    if generic:
        r.ok("  but not empty ones", f"!String.IsEmpty({ITEM}.Mpaa)" in generic[0], generic[0])
        r.ok("  nor US ones (they start with Rated)", f"!String.StartsWith({ITEM}.Mpaa,rated)" in generic[0], generic[0])
    us = {v: label for label, v in b if label != f"$INFO[{ITEM}.Mpaa]"}
    lib = ratings()
    for rating, n in sorted(lib.items(), key=lambda t: -t[1]):
        if not rating.lower().startswith("rated "):
            continue
        short = rating[6:]
        want = f"String.IsEqual({ITEM}.Mpaa,{rating})"
        if short == "NR":
            r.ok(f"{rating} ({n}): no badge", want not in us, us.get(want))
        else:
            r.ok(f"{rating} ({n}): badge {short}", us.get(want) == short, us.get(want))
    print(f"INFO map: ratings in the library: {sorted(lib.items(), key=lambda t: -t[1])}", flush=True)


@check("detail")
def c_detail(r):
    """Detail pages of a film rated in the US way, one rated another way (if the app wrote any yet), and one without:
    screenshots to look at."""
    kids_check.kids(False)
    films = rpc("VideoLibrary.GetMovies", {"properties": ["mpaa"]}, timeout=120)["result"]["movies"]
    picks = [next((f for f in films if f["mpaa"] == "Rated PG-13"), None),
             next((f for f in films if f["mpaa"] and not f["mpaa"].lower().startswith("rated")), None),
             next((f for f in films if not f["mpaa"]), None)]
    for name, f in zip(("us", "other", "none"), picks):
        if not f:
            r.skip(f"no film rated {name}")
            continue
        reset_home()
        if r.ok(f"{f['label']} ({f['mpaa'] or 'no rating'}): detail page opens", open_detail("movie", f["movieid"])):
            r.ok("  Kodi has its rating", lab("ListItem.Mpaa") == f["mpaa"], lab("ListItem.Mpaa"))
            print(f"INFO detail: screenshot {shot('rating-' + name)}", flush=True)
            close_detail()
    reset_home()


CHECKS["cleanup"] = kids_check.CHECKS["cleanup"]


if __name__ == "__main__":
    sys.exit(kids_check.main(sys.argv[1:], CHECKS))
