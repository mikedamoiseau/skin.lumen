"""Generate skin.lumen/xml/Home.xml.

Home rows dock at a fixed line (tvOS style): the focused row always sits at DOCK_Y and rows
above it slide up and fade out. Kodi has no "scroll to control" primitive, so the docking is
done with summed conditional slide animations, which is tedious to write by hand — hence
this generator. Edit ROWS below, then run: tools/.venv/bin/python tools/build_home.py
"""
import os

ROWS = [
    # id, kind, header, path, sortby, sortorder, onclick (None = Kodi default action)
    (5001, "landscape", "Continue Watching", "special://skin/playlists/inprogress_movies.xsp", "none", "ascending", None),
    (5002, "landscape", "Up Next", "videodb://inprogresstvshows/", "lastplayed", "descending", None),
    (5003, "poster", "Recently Added Movies", "videodb://recentlyaddedmovies/", "none", "ascending", "Action(Info)"),
    (5004, "landscape", "New Episodes", "special://skin/playlists/recent_unwatched_episodes.xsp", "none", "ascending", "Action(Info)"),
    (5005, "poster", "Discover", "special://skin/playlists/lumen_discover_movies.xsp", "none", "ascending", "Action(Info)"),
    (5006, "poster", "Top Rated", "special://skin/playlists/lumen_toprated_movies.xsp", "none", "ascending", "Action(Info)"),
    (5007, "poster", "TV Shows", "videodb://tvshows/titles/", "dateadded", "descending", None),
    (5008, "landscape", "Collections", "videodb://movies/sets/", "random", "ascending", None),
]
# Kids mode (skin bool CinemathequeKids): its own rows, shown instead of the ones above.
# Only titles the Cinémathèque app tagged "Cinematheque Kids" (skin.lumen/playlists/kids/).
KIDS_CONTINUE = ("multipath://special%3a%2f%2fskin%2fplaylists%2fkids%2finprogress_movies.xsp/"
                 "special%3a%2f%2fskin%2fplaylists%2fkids%2finprogress_tvshows.xsp/")
KIDS_ROWS = [
    (5011, "landscape", "Continue Watching", KIDS_CONTINUE, "lastplayed", "descending", None),
    (5012, "landscape", "New Episodes", "special://skin/playlists/kids/new_episodes.xsp", "dateadded", "descending", "Action(Info)"),
    (5013, "poster", "Recently Added", "special://skin/playlists/kids/recent_movies.xsp", "dateadded", "descending", "Action(Info)"),
    (5014, "poster", "TV Shows", "special://skin/playlists/kids/tvshows.xsp", "dateadded", "descending", None),
    (5015, "poster", "Discover", "special://skin/playlists/kids/discover.xsp", "none", "ascending", "Action(Info)"),
]
# Rows of the user's own (Configure skin > Home rows > Add a row): a smart playlist each, after the rows above, normal
# mode only. Skin strings lumen.rowN.path / lumen.rowN.name; OK on a film opens its detail page, anything else Kodi's way.
CUSTOM_ROWS = [
    (5020 + n, "poster", f"$VAR[LumenCustomRowName{n}]", f"$INFO[Skin.String(lumen.row{n}.path)]", "none", "ascending",
     ("String.IsEqual(ListItem.DBType,movie)", "Action(Info)"))
    for n in (1, 2, 3)
]
CUSTOM_IDS = [r[0] for r in CUSTOM_ROWS]
# smart playlists come back sorted by label, so rows that need an order say so in sortby
# each mode's rows are visible only in that mode. A hidden list still loads its path whenever Home opens
# (GUIBaseContainer::AllocResources), so kids rows read their path from a variable that is empty outside
# kids mode; the kids button re-opens Home (window 1114) so they load right away.
MODES = [("!$EXP[LumenKids]", ROWS + CUSTOM_ROWS), ("$EXP[LumenKids]", KIDS_ROWS)]
KIDS_PATH = lambda rid: f"LumenKidsPath{rid}"
# a switched-off row reads its path from a variable that is then empty, so Home doesn't load it for nothing
ROW_PATH = lambda rid: f"LumenRowPath{rid}"
row_path = lambda rows, rid, path: (f"$VAR[{KIDS_PATH(rid)}]" if rows is KIDS_ROWS else
                                    f"$VAR[{ROW_PATH(rid)}]" if any(rid == r[0] for r in ROWS) else path)
assert len(ROWS) <= 8, "settings switches 911-918: 920 and up are taken"


def row_visible(mode, rid):
    """Built-in normal rows can be switched off (skin bool lumen_hide_<id>); an added row shows once it has a playlist."""
    if rid in CUSTOM_IDS:
        return f"{mode} + !String.IsEmpty(Skin.String(lumen.row{CUSTOM_IDS.index(rid) + 1}.path))"
    if any(rid == r[0] for r in ROWS):
        return f"{mode} + !Skin.HasSetting(lumen_hide_{rid})"
    return mode
DOCK_Y = 600
ROW_H = 500  # must match the row group height in Includes_Lumen.xml
GROUP = lambda rid: rid + 1000  # 5001 -> 6001
HERO = lambda rid: rid + 2000  # 5001 -> 7001
SLIDE = 'time="420" tween="cubic" easing="out"'

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skin.lumen", "xml", "Home.xml")


def focus_any(ids):
    return " | ".join(f"Control.HasFocus({i})" for i in ids)


def settings_include():
    """Configure skin > Home rows (SkinSettings.xml): a switch per built-in row, then the added rows. One "Add a row"
    button shows, for the first free slot. Add picks a playlist (Skin.SetFile); lumenrowname in Timers.xml then asks
    for the name, only if one was picked."""
    S = ['\t<include name="LumenHomeRowSettings">']
    S.append('\t\t<control type="label" id="910">')
    S.append("\t\t\t<height>90</height>")
    S.append("\t\t\t<aligny>center</aligny>")
    S.append("\t\t\t<textoffsetx>40</textoffsetx>")
    S.append("\t\t\t<font>lm_caption_bold</font>")
    S.append("\t\t\t<textcolor>text_mute</textcolor>")
    S.append("\t\t\t<label>Home rows</label>")
    S.append("\t\t</control>")
    for i, (rid, kind, header, *_) in enumerate(ROWS):
        S.append(f'\t\t<control type="radiobutton" id="{911 + i}">')
        S.append(f"\t\t\t<label>{header}</label>")
        S.append("\t\t\t<include>DefaultSettingButton</include>")
        S.append(f"\t\t\t<selected>!Skin.HasSetting(lumen_hide_{rid})</selected>")
        S.append(f"\t\t\t<onclick>Skin.ToggleSetting(lumen_hide_{rid})</onclick>")
        S.append("\t\t</control>")
    for n in (1, 2, 3):
        filled = lambda k: f"!String.IsEmpty(Skin.String(lumen.row{k}.path))"
        S.append(f'\t\t<control type="button" id="{930 + n}">')
        S.append(f"\t\t\t<label>$VAR[LumenCustomRowName{n}]</label>")
        S.append("\t\t\t<label2>Rename</label2>")
        S.append("\t\t\t<include>DefaultSettingButton</include>")
        S.append(f"\t\t\t<onclick>Skin.SetString(lumen.row{n}.name)</onclick>")
        S.append(f"\t\t\t<visible>{filled(n)}</visible>")
        S.append("\t\t</control>")
        S.append(f'\t\t<control type="button" id="{940 + n}">')
        S.append("\t\t\t<label>Remove this row</label>")
        S.append("\t\t\t<include>DefaultSettingButton</include>")
        S.append(f"\t\t\t<onclick>Skin.Reset(lumen.row{n}.path)</onclick>")
        S.append(f"\t\t\t<onclick>Skin.Reset(lumen.row{n}.name)</onclick>")
        S.append("\t\t\t<onclick>SetFocus(920)</onclick>")
        S.append(f"\t\t\t<visible>{filled(n)}</visible>")
        S.append("\t\t</control>")
    for n in (1, 2, 3):
        S.append(f'\t\t<control type="button" id="{920 + n}">')
        S.append("\t\t\t<label>Add a row…</label>")
        S.append("\t\t\t<include>DefaultSettingButton</include>")
        S.append(f"\t\t\t<onclick>Skin.SetFile(lumen.row{n}.path,.xsp,special://videoplaylists/)</onclick>")
        S.append(f"\t\t\t<onclick>SetProperty(lumen.rowask,{n},home)</onclick>")
        before = "".join(f" + !String.IsEmpty(Skin.String(lumen.row{k}.path))" for k in range(1, n))
        S.append(f"\t\t\t<visible>String.IsEmpty(Skin.String(lumen.row{n}.path)){before}</visible>")
        S.append("\t\t</control>")
    S.append('\t\t<control type="button" id="920">')
    S.append("\t\t\t<label>Make a smart playlist…</label>")
    S.append("\t\t\t<include>DefaultSettingButton</include>")
    S.append("\t\t\t<onclick>ActivateWindow(videos,special://videoplaylists/,return)</onclick>")
    S.append("\t\t</control>")
    S.append("\t</include>")
    return S


def build():
    L = []
    w = L.append
    w('<?xml version="1.0" encoding="UTF-8"?>')
    w("<!-- GENERATED by tools/build_home.py — edit the generator, not this file. -->")
    w("<window>")
    w("\t<defaultcontrol always=\"true\">9100</defaultcontrol>")
    w("\t<backgroundcolor>background</backgroundcolor>")
    w("\t<onload>ClearProperty(lumen.row,home)</onload>")
    # no row to focus: the tab bar takes it, or the remote would have nothing to move
    w("\t<onload condition=\"$EXP[LumenNoHomeRows]\">SetFocus(9000)</onload>")
    w("\t<controls>")
    # live TV groups (Kodi lists them under pvr://channels/tv/): Timers.xml (lumentvgroups) keeps the all-channels and
    # Kids groups' paths in Home properties, for every place that opens live TV
    w('\t\t<include content="LumenNextLookup">')
    w('\t\t\t<param name="id" value="9310" />')
    w('\t\t\t<param name="path" value="pvr://channels/tv/" />')
    w('\t\t\t<param name="visible" value="PVR.HasTVChannels" />')
    w("\t\t</include>")
    # shown when no row is: Timers.xml (lumenhomeempty) reads it, as Kodi doesn't expand $EXP in Timers.xml
    w('\t\t<control type="image" id="6017">')
    w("\t\t\t<width>1</width>")
    w("\t\t\t<height>1</height>")
    w("\t\t\t<texture />")
    w("\t\t\t<visible>$EXP[LumenHomeRowsGone]</visible>")
    w("\t\t</control>")
    w('\t\t<control type="group">')
    w("\t\t<visible>!$EXP[infodialog_active]</visible>")
    w('\t\t<animation effect="fade" time="300">VisibleChange</animation>')
    w('\t\t<include content="LumenBackdrop"><param name="image" value="$VAR[LumenHomeFanart]" /></include>')

    # heroes — one per row, only the one for the last focused row of the current mode is visible
    for mode, rows in MODES:
        for rid, *_ in rows:
            w('\t\t<include content="LumenHero">')
            w(f'\t\t\t<param name="id" value="{HERO(rid)}" />')
            w(f'\t\t\t<param name="item" value="Container({rid}).ListItem" />')
            w(f'\t\t\t<param name="visible" value="String.IsEqual(Window(home).Property(lumen.row),{rid}) + {mode}" />')
            w("\t\t</include>")

    # rows
    w('\t\t<control type="group" id="9101">')
    w(f"\t\t\t<top>{DOCK_Y}</top>")
    w('\t\t\t<include content="LumenRiseIn"><param name="delay" value="120" /></include>')
    # A one-row viewport: the grouplist's own minimal scrolling then docks the focused row at
    # DOCK_Y, hidden (empty) rows collapse, and Kodi culls rows outside the viewport.
    w('\t\t\t<control type="grouplist" id="9100">')
    w("\t\t\t\t<left>0</left>")
    w("\t\t\t\t<top>0</top>")
    w("\t\t\t\t<width>1920</width>")
    w(f"\t\t\t\t<height>{ROW_H}</height>")
    w("\t\t\t\t<orientation>vertical</orientation>")
    w("\t\t\t\t<itemgap>0</itemgap>")
    w('\t\t\t\t<scrolltime tween="cubic" easing="out">420</scrolltime>')
    w("\t\t\t\t<onup>9000</onup>")
    for mode, rows in MODES:
        ids = [r[0] for r in rows]
        for i, (rid, kind, header, path, sortby, sortorder, onclick) in enumerate(rows):
            later = ids[i + 1:]
            inc = "LumenPosterRow" if kind == "poster" else "LumenLandscapeRow"
            w(f'\t\t\t\t<include content="{inc}">')
            w(f'\t\t\t\t\t<param name="id" value="{rid}" />')
            w(f'\t\t\t\t\t<param name="visible" value="{row_visible(mode, rid)}" />')
            w(f'\t\t\t\t\t<param name="group" value="{GROUP(rid)}" />')
            w(f'\t\t\t\t\t<param name="header" value="{header}" />')
            w(f'\t\t\t\t\t<param name="path" value="{row_path(rows, rid, path)}" />')
            w(f'\t\t\t\t\t<param name="sortby" value="{sortby}" />')
            w(f'\t\t\t\t\t<param name="sortorder" value="{sortorder}" />')
            w(f'\t\t\t\t\t<param name="fadeout" value="{focus_any(later) if later else "false"}" />')
            # explicit chain: Kodi follows it through hidden (empty) rows
            w(f'\t\t\t\t\t<param name="onup" value="{ids[i - 1] if i else 9000}" />')
            w(f'\t\t\t\t\t<param name="ondown" value="{ids[i + 1] if later else "noop"}" />')
            if onclick:
                cond, action = onclick if isinstance(onclick, tuple) else ("true", onclick)
                w(f'\t\t\t\t\t<param name="onclick_condition" value="{cond}" />')
                w(f'\t\t\t\t\t<param name="onclick" value="{action}" />')
            w("\t\t\t\t</include>")
    w("\t\t\t</control>")
    # kids mode with nothing tagged yet: say where the list comes from
    w('\t\t\t<control type="label" id="6019">')
    w("\t\t\t\t<left>lm_margin</left>")
    w("\t\t\t\t<top>44</top>")
    w("\t\t\t\t<width>1500</width>")
    w("\t\t\t\t<height>60</height>")
    w("\t\t\t\t<font>lm_body</font>")
    w("\t\t\t\t<textcolor>text_dim</textcolor>")
    w("\t\t\t\t<label>No titles for kids yet. A parent can choose them in the Cinémathèque app.</label>")
    w("\t\t\t\t<visible>$EXP[LumenKids] + !Integer.IsGreater(Container(5013).NumItems,0) + !Integer.IsGreater(Container(5014).NumItems,0)"
      " + !Container(5013).IsUpdating + !Container(5014).IsUpdating</visible>")
    w("\t\t\t</control>")
    # every row switched off: say where they come back from (the tab bar keeps focus, see onload)
    w('\t\t\t<control type="label" id="6018">')
    w("\t\t\t\t<left>lm_margin</left>")
    w("\t\t\t\t<top>44</top>")
    w("\t\t\t\t<width>1500</width>")
    w("\t\t\t\t<height>60</height>")
    w("\t\t\t\t<font>lm_body</font>")
    w("\t\t\t\t<textcolor>text_dim</textcolor>")
    w("\t\t\t\t<label>All Home rows are off. Turn them back on in Settings › Interface › Skin › Configure skin.</label>")
    w("\t\t\t\t<visible>$EXP[LumenNoHomeRows]</visible>")
    w("\t\t\t</control>")
    w("\t\t</control>")

    # tab bar hides once the user scrolls past the first visible row
    scrolled = []
    for _, rows in MODES:
        ids = [r[0] for r in rows]
        for i, rid in enumerate(ids):
            before = [GROUP(j) for j in ids[:i]]
            if before:
                scrolled.append(f"[Control.HasFocus({rid}) + [{' | '.join(f'Control.IsVisible({g})' for g in before)}]]")
    w('\t\t<control type="group">')
    w(f'\t\t\t<animation effect="slide" end="0,-150" {SLIDE} condition="{" | ".join(scrolled)}">Conditional</animation>')
    w(f'\t\t\t<animation effect="fade" start="100" end="0" time="300" condition="{" | ".join(scrolled)}">Conditional</animation>')
    w('\t\t\t<include content="LumenTabBar"><param name="tex10" value="lumen/pill.png" /></include>')
    w("\t\t\t<include>LumenClock</include>")
    w("\t\t</control>")
    w("\t\t</control>")
    w("\t</controls>")
    w("</window>")

    # backdrop variable lives in its own include file so the generator owns it too
    V = ['<?xml version="1.0" encoding="UTF-8"?>', "<!-- GENERATED by tools/build_home.py -->", "<includes>"]
    V.append('\t<variable name="LumenHomeFanart">')
    for mode, rows in MODES:
        for rid, *_ in rows:
            c = f"Container({rid}).ListItem"
            on = f"String.IsEqual(Window(home).Property(lumen.row),{rid}) + {mode}"
            V.append(f'\t\t<value condition="{on} + !String.IsEmpty({c}.Art(fanart))">$INFO[{c}.Art(fanart)]</value>')
            V.append(f'\t\t<value condition="{on}">$INFO[{c}.Art(tvshow.fanart)]</value>')
    V.append("\t</variable>")
    V.append('\t<expression name="LumenNoHomeRows">!$EXP[LumenKids] + '
             + " + ".join([f"Skin.HasSetting(lumen_hide_{r[0]})" for r in ROWS]
                          + [f"String.IsEmpty(Skin.String(lumen.row{n}.path))" for n in (1, 2, 3)]) + "</expression>")
    for n in (1, 2, 3):
        V.append(f'\t<variable name="LumenCustomRowName{n}">')
        V.append(f'\t\t<value condition="!String.IsEmpty(Skin.String(lumen.row{n}.name))">$INFO[Skin.String(lumen.row{n}.name)]</value>')
        V.append(f"\t\t<value>My row {n}</value>")
        V.append("\t</variable>")
    V.extend(settings_include())
    for rid, kind, header, path, *_ in ROWS:
        V.append(f'\t<variable name="{ROW_PATH(rid)}">')
        V.append(f'\t\t<value condition="!Skin.HasSetting(lumen_hide_{rid})">{path}</value>')
        V.append("\t</variable>")
    # no row group shown at all (every row off, or the ones on are empty): Home's control 6017, for Timers.xml
    groups = [GROUP(r[0]) for _, rows in MODES for r in rows]
    V.append('\t<expression name="LumenHomeRowsGone">' + " + ".join(f"!Control.IsVisible({g})" for g in groups) + "</expression>")
    for rid, kind, header, path, *_ in KIDS_ROWS:
        V.append(f'\t<variable name="{KIDS_PATH(rid)}">')
        V.append(f'\t\t<value condition="$EXP[LumenKids]">{path}</value>')
        V.append("\t</variable>")
    V.append("</includes>")

    with open(out, "w") as f:
        f.write("\n".join(L) + "\n")
    with open(os.path.join(os.path.dirname(out), "Includes_LumenHome.xml"), "w") as f:
        f.write("\n".join(V) + "\n")
    print("wrote", os.path.normpath(out))


if __name__ == "__main__":
    build()
