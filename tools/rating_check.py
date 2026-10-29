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


def holds(condition, mpaa):
    """Evaluate a badge's visible condition for a rating: Kodi's ! + | [ ] over String.IsEmpty / IsEqual / StartsWith
    of $PARAM[item].Mpaa (case-insensitive, as in Kodi)."""
    text = condition.replace(f"{ITEM}.Mpaa", "M")
    pos = 0

    def peek():
        return text[pos] if pos < len(text) else ""

    def skip():
        nonlocal pos
        while peek() == " ":
            pos += 1

    def unary():
        nonlocal pos
        skip()
        if peek() == "!":
            pos += 1
            return not unary()
        if peek() == "[":
            pos += 1
            v = either()
            skip()
            assert peek() == "]", text[pos:]
            pos += 1
            return v
        end = text.index(")", pos) + 1
        atom, pos = text[pos:end], end
        name, args = atom[:atom.index("(")], atom[atom.index("(") + 1:-1].split(",", 1)
        assert args[0] == "M", atom
        if name == "String.IsEmpty":
            return mpaa == ""
        if name == "String.IsEqual":
            return mpaa.lower() == args[1].lower()
        if name == "String.StartsWith":
            return mpaa.lower().startswith(args[1].lower())
        raise ValueError(atom)

    def both():
        nonlocal pos
        v = unary()
        while True:
            skip()
            if peek() != "+":
                return v
            pos += 1
            v = unary() and v

    def either():
        nonlocal pos
        v = both()
        while True:
            skip()
            if peek() != "|":
                return v
            pos += 1
            v = both() or v

    return either()


def shown(b, mpaa):
    """The labels the badges show for a rating."""
    return [mpaa if label == f"$INFO[{ITEM}.Mpaa]" else label for label, v in b if holds(v, mpaa)]


US = ["G", "PG", "PG-13", "R", "NC-17", "TV-Y", "TV-Y7", "TV-G", "TV-PG", "TV-14", "TV-MA"]


@check("map")
def c_map(r):
    """US ratings show without "Rated "; "NR", "Not Rated", "Unrated" and no rating show nothing; anything else (the
    app's "FSK 12", a US rating Lumen doesn't know) shows as written. Every rating in the library: one badge at most."""
    b = badges()
    r.ok("the detail line has rating badges", b, len(b))
    cases = {"": [], "Rated NR": [], "NR": [], "Not Rated": [], "Unrated": [], "FSK 12": ["FSK 12"], "FSK 0": ["FSK 0"],
             "12": ["12"], "Rated X": ["Rated X"], "Rated TV-Y7-FV": ["Rated TV-Y7-FV"], "rated pg-13": ["PG-13"]}
    cases.update({f"Rated {u}": [u] for u in US})
    for mpaa, want in cases.items():
        r.ok(f"{mpaa or '(none)'}: {want or 'no badge'}", shown(b, mpaa) == want, shown(b, mpaa))
    lib = ratings()
    for mpaa, n in sorted(lib.items(), key=lambda t: -t[1]):
        got = shown(b, mpaa)
        r.ok(f"library {mpaa or '(none)'} ({n}): at most one badge", len(got) <= 1 and (got != []) == (mpaa not in ("", "Rated NR")), got)
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
