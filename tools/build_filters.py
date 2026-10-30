"""Generate skin.lumen/xml/Includes_LumenFilters.xml: the Genre / Decade picker (Custom_1117) and the path it opens.

The picker only changes Home window properties; closing it opens the grid at $VAR[LumenFilterPath] (Custom_1117's
onunload). That path is the film or show list plus a smart playlist in the URL (?xsp=, URL-encoded JSON): the kids
tag in kids mode, the decade's years, and the genres ticked. The skin can't encode text, so every piece is written
out here. Run: python3 tools/build_filters.py

State (Home window properties):
  lumen.fg.<key>  the genre's piece of the JSON list, encoded (,"Comedy"), while it's ticked
  lumen.fx.<key>  "x" while it's ticked: lumen.fgcount strings them together to count
  lumen.fgenres   every ticked genre's piece, in table order; lumen.fdecade the decade's label
  lumen.fdirty    set by a change, so closing the picker opens the new path
  lumen.fpath     the path last opened (Views_Lumen.xml: the chips apply only while the grid is there)
"""
import json
import os
import re
import urllib.parse

# label, first year, last year ("Before 1950" starts at 1: films without a year have 0)
DECADES = [(f"{y}s", y, y + 9) for y in range(2020, 1949, -10)] + [("Before 1950", 1, 1949)]
# TMDB's genres for films and for shows, in English (what Kodi's scrapers write). A genre outside this list can't be
# ticked: its name would go into the URL as written, and an "&" splits Kodi's URL options.
GENRES = sorted({"Action", "Adventure", "Animation", "Comedy", "Crime", "Documentary", "Drama", "Family", "Fantasy",
                 "History", "Horror", "Music", "Mystery", "Romance", "Science Fiction", "TV Movie", "Thriller", "War",
                 "Western", "Action & Adventure", "Kids", "News", "Reality", "Sci-Fi & Fantasy", "Soap", "Talk",
                 "War & Politics"})
KIDS_TAG = "Cinematheque Kids"
SHOWS = "Container.Content(tvshows)"
KIDS = "$EXP[LumenKids]"
HOME = "Window(home).Property"
# films in kids mode: Kodi groups a library film list into collections, which kids grids don't show. It skips that for
# a video version's list (GUIWindowVideoBase::GetGroupedItems), and version 0 filters nothing (CVideoDatabase::GetFilter)
BASES = {("movies", False): "videodb://movies/titles/", ("movies", True): "videodb://movies/videoversions/0/",
         ("tvshows", False): "videodb://tvshows/titles/", ("tvshows", True): "videodb://tvshows/titles/"}
GENRES_MARK = "@GENRES@"


def key(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


def prop(name):
    return f"{HOME}({name})"


def info(name):
    return f"$INFO[{prop(name)}]"


def enc(text):
    return urllib.parse.quote(text, safe="")


def rules(kids, decade, genres):
    out = []
    if kids:
        out.append({"field": "tag", "operator": "is", "value": [KIDS_TAG]})
    if decade:
        out.append({"field": "year", "operator": "between", "value": [str(decade[1]), str(decade[2])]})
    if genres:
        # "" first: every ticked genre adds ,"Name"; "" matches no genre
        out.append({"field": "genre", "operator": "is", "value": [""]})
    return out


def xsp(kind, rule_list, group=None):
    d = {"rules": {"and": rule_list}, "type": kind}
    if group:
        d["group"] = {"type": group, "mixed": False}
    text = json.dumps(d, separators=(",", ":")).replace('[""]', f'[""{GENRES_MARK}]')
    return "?xsp=" + enc(text).replace(enc(GENRES_MARK), info("lumen.fgenres"))


def on(kind, kids):
    return " + ".join([SHOWS if kind == "tvshows" else f"!{SHOWS}", KIDS if kids else f"!{KIDS}"])


def decade_is(d):
    return f"String.IsEqual({prop('lumen.fdecade')},{d[0]})" if d else f"String.IsEmpty({prop('lumen.fdecade')})"


def value(condition, text, indent="\t\t"):
    c = f' condition="{condition}"' if condition else ""
    return f"{indent}<value{c}>{text}</value>"


def path_variable():
    """The grid's path for what the properties say: an empty rule set when nothing applies (Kodi's ActivateWindow
    skips a path the current one starts with, CGUIMediaWindow::IsSameStartFolder, so titles/?xsp=… → titles/ would do
    nothing)."""
    L = ['\t<variable name="LumenFilterPath">']
    for kind in ("movies", "tvshows"):
        for kids in (False, True):
            for d in [None] + DECADES:
                for genres in (True, False):
                    g = f"!String.IsEmpty({prop('lumen.fgenres')})" if genres else f"String.IsEmpty({prop('lumen.fgenres')})"
                    cond = f"{on(kind, kids)} + {decade_is(d)} + {g}"
                    L.append(value(cond, BASES[(kind, kids)] + xsp(kind, rules(kids, d, genres))))
    return L + ["\t</variable>"]


def genre_source():
    """The picker's genres: only those with titles under the kids tag and the decade (a smart playlist grouped by
    genre filters Kodi's genre list)."""
    L = ['\t<variable name="LumenGenreSource">']
    for kind in ("movies", "tvshows"):
        for kids in (False, True):
            for d in [None] + DECADES:
                r = rules(kids, d, False)
                base = f"videodb://{kind}/genres/"
                L.append(value(f"{on(kind, kids)} + {decade_is(d)}", base + (xsp(kind, r, "genres") if r else "")))
    return L + ["\t</variable>"]


def click(action, condition=None, indent="\t\t"):
    c = f' condition="{condition}"' if condition else ""
    return f"{indent}<onclick{c}>{action}</onclick>"


def recount(condition=None, indent="\t\t"):
    """lumen.fgenres and lumen.fgcount from the ticks."""
    return [click(f"SetProperty(lumen.fgenres,{''.join(info('lumen.fg.' + key(n)) for n in GENRES)},home)", condition, indent),
            click(f"SetProperty(lumen.fgcount,{''.join(info('lumen.fx.' + key(n)) for n in GENRES)},home)", condition, indent)]


def is_genre(name):
    return f"String.IsEqual(ListItem.Label,{name})"


def known():
    return " | ".join(is_genre(n) for n in GENRES)


def genre_actions():
    """List 11: OK ticks or unticks the genre; the picker stays open. Kodi runs a list's actions only if one of them
    holds without the row (GUIBaseContainer.cpp, HasActionsMeetingCondition: ListItem is then the grid's item), so the
    first has no condition."""
    L = [click("SetProperty(lumen.fdirty,1,home)")]
    for n in GENRES:
        k = key(n)
        off = f"String.IsEmpty({prop('lumen.fx.' + k)})"
        L += [click(f"SetProperty(lumen.fg.{k},{enc(',' + json.dumps(n))},home)", f"{is_genre(n)} + {off}"),
              click(f"SetProperty(lumen.fx.{k},x,home)", f"{is_genre(n)} + {off}"),
              click(f"ClearProperty(lumen.fg.{k},home)", f"{is_genre(n)} + !{off}"),
              click(f"ClearProperty(lumen.fx.{k},home)", f"{is_genre(n)} + !{off}")]
    return L + recount("$EXP[LumenGenreKnown]")


def clear_genres(tag, condition=None, indent="\t\t"):
    c = f' condition="{condition}"' if condition else ""
    out = []
    for n in GENRES:
        out += [f"{indent}<{tag}{c}>ClearProperty(lumen.fg.{key(n)},home)</{tag}>",
                f"{indent}<{tag}{c}>ClearProperty(lumen.fx.{key(n)},home)</{tag}>"]
    return out + [f"{indent}<{tag}{c}>ClearProperty(lumen.fgenres,home)</{tag}>",
                  f"{indent}<{tag}{c}>ClearProperty(lumen.fgcount,home)</{tag}>"]


def all_genres_actions():
    """Button 10: no genre, then close (closing opens the path)."""
    return clear_genres("onclick") + [click("SetProperty(lumen.fdirty,1,home)"), click("Dialog.Close(1117)")]


def reset_onload():
    """Opened over a grid the chips don't apply to (Movies opened again from Home, say): start from nothing."""
    cond = "!$EXP[LumenFilterActive]"
    return clear_genres("onload", cond, "\t") + [f'\t<onload condition="{cond}">ClearProperty(lumen.fdecade,home)</onload>']


def decade_items():
    """List 12's items: All decades, then each decade; a pick closes the picker."""
    I = "\t\t\t"
    items = []
    for d in [None] + DECADES:
        label = d[0] if d else "All decades"
        L = ["\t\t<item>", f"{I}<label>{label}</label>"]
        if not d:
            L.append(f'{I}<property name="all">1</property>')
        L += [click(f"SetProperty(lumen.fdecade,{label},home)" if d else "ClearProperty(lumen.fdecade,home)", indent=I),
              click("SetProperty(lumen.fdirty,1,home)", indent=I), click("Dialog.Close(1117)", indent=I), "\t\t</item>"]
        items += L
    return items


def ticked():
    """A genre row's tick."""
    return " | ".join(f"[{is_genre(n)} + !String.IsEmpty({prop('lumen.fx.' + key(n))})]" for n in GENRES)


def chip_label():
    """One genre: its name; several: the first (A to Z) and how many more."""
    L = ['\t<variable name="LumenGenreFirst">']
    L += [value(f"!String.IsEmpty({prop('lumen.fx.' + key(n))})", n) for n in GENRES]
    L += ["\t</variable>", '\t<variable name="LumenGenreChip">']
    L.append(value(f"$EXP[LumenGenreOn] + String.IsEqual({prop('lumen.fgcount')},x)", "$VAR[LumenGenreFirst]"))
    for i in range(2, len(GENRES) + 1):
        L.append(value(f"$EXP[LumenGenreOn] + String.IsEqual({prop('lumen.fgcount')},{'x' * i})", f"$VAR[LumenGenreFirst] +{i - 1}"))
    return L + [value(None, "Genre"), "\t</variable>"]


def main():
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skin.lumen", "xml", "Includes_LumenFilters.xml")
    X = ['<?xml version="1.0" encoding="UTF-8"?>',
         "<!-- GENERATED by tools/build_filters.py — edit the generator, not this file. -->",
         "<includes>",
         *path_variable(), *genre_source(), *chip_label(),
         f'\t<expression name="LumenGenreKnown">{known()}</expression>',
         f'\t<expression name="LumenGenreTicked">{ticked()}</expression>',
         "\t<include name=\"LumenGenrePickActions\">", *genre_actions(), "\t</include>",
         "\t<include name=\"LumenAllGenresActions\">", *all_genres_actions(), "\t</include>",
         "\t<include name=\"LumenDecadeItems\">", *decade_items(), "\t</include>",
         "\t<include name=\"LumenFilterResetOnload\">", *reset_onload(), "\t</include>",
         "</includes>", ""]
    with open(out, "w") as f:
        f.write("\n".join(X).replace("&", "&amp;").replace("&amp;amp;", "&amp;"))
    print(f"wrote {os.path.normpath(out)}")


if __name__ == "__main__":
    main()
