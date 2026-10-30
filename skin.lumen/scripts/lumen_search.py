"""Lumen search: the search page's library paths for the text being searched.

Run by the skin (Timers.xml): RunScript(special://skin/scripts/lumen_search.py,typed|search). It reads the text
itself, from Kodi's keyboard over the search page (typed) or from the confirmed search (search), so the text never
goes through a builtin's parameters. The paths go into Home window properties lumen.sq.*; the skin uses them only
while lumen.sq.text and lumen.sq.kids match what is on screen. A skin can't URL-encode, so a text with "&", "+" or
"%" can only be searched from here.

Every word must start a word of the title or the original title (after a space, ', ’ or -), in any order. The
database ignores case and accents.
"""
import json
import sys
import urllib.parse

KIDS_TAG = "Cinematheque Kids"
SEPS = (" ", "'", "’", "-")
MAX_WORDS = 6


def word_rule(word):
    rules = []
    for field in ("title", "originaltitle"):
        rules.append({"field": field, "operator": "startswith", "value": [word]})
        rules += [{"field": field, "operator": "contains", "value": [sep + word]} for sep in SEPS]
    return {"or": rules}


def people(text):
    return [{"field": field, "operator": "contains", "value": [text]} for field in ("actor", "director")]


def path(kind, rules, kids):
    if kids:
        rules = {"and": [{"field": "tag", "operator": "is", "value": [KIDS_TAG]}, rules]}
    xsp = {"type": kind, "rules": rules, "order": {"method": "title", "direction": "ascending"}}
    return f"videodb://{kind}/titles/?xsp=" + urllib.parse.quote(json.dumps(xsp, ensure_ascii=False, separators=(",", ":")), safe="")


def paths(text, kids):
    """The rows' paths for text: movies, shows and (not in kids mode) people; empty for a text without words."""
    words = text.split()[:MAX_WORDS]
    if not words:
        return {"movies": "", "shows": "", "people": ""}
    titles = {"and": [word_rule(w) for w in words]}
    phrase = " ".join(text.split())
    return {"movies": path("movies", titles, kids),
            "shows": path("tvshows", {"or": [titles] + people(phrase)}, kids),
            "people": "" if kids else path("movies", {"or": people(phrase)}, False)}


def main():
    import xbmc
    import xbmcgui
    typed = len(sys.argv) > 1 and sys.argv[1] == "typed"
    text = xbmc.getInfoLabel("Control.GetLabel(312).index(1)" if typed else "Skin.String(lumen.search)")
    kids = xbmc.getCondVisibility("Skin.HasSetting(CinemathequeKids)")
    home = xbmcgui.Window(10000)
    for row, value in paths(text, kids).items():
        home.setProperty(f"lumen.sq.{row}", value)
    home.setProperty("lumen.sq.kids", "1" if kids else "")
    home.setProperty("lumen.sq.text", text)  # last: the skin takes the paths once this matches the screen


if __name__ == "__main__":
    main()
