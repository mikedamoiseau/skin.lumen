"""shownext_regress.py [TVSHOWID ...]: compare Lumen's show-page 'Play next' label with an independent implementation of the app's rule."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kodi_gui import rpc, lab
def expected(tvshowid):
    eps = rpc("VideoLibrary.GetEpisodes", {"tvshowid": tvshowid, "properties": ["season", "episode", "playcount", "lastplayed", "resume", "file", "title"]})["result"].get("episodes", [])
    eps = sorted([e for e in eps if e["season"] > 0], key=lambda e: (e["season"], e["episode"], e["file"]))
    if not eps:
        return None, "no episodes"
    played = [e for e in eps if e["lastplayed"]]
    if played:
        a = max(played, key=lambda e: e["lastplayed"])
        if e_resumable(a) or a["playcount"] == 0:
            return a, ("Resume" if e_resumable(a) else "Play") + f" S{a['season']} E{a['episode']}"
        after = [e for e in eps if (e["season"], e["episode"]) > (a["season"], a["episode"]) and e["playcount"] == 0]
        if after:
            b = after[0]; return b, f"Play S{b['season']} E{b['episode']}"
        return eps[0], "Watch again"
    unw = [e for e in eps if e["playcount"] == 0]
    if unw:
        c = unw[0]; return c, f"Play S{c['season']} E{c['episode']}"
    return eps[0], "Watch again"
# a show without episodes: the button reads "No episodes" (Views_Lumen.xml) or is hidden
EMPTY = ("No episodes", "(hidden)")
def e_resumable(e):
    return (e.get("resume") or {}).get("position", 0) > 0
shows = rpc("VideoLibrary.GetTVShows", {"properties": ["title", "episode", "watchedepisodes"]})["result"]["tvshows"]
ids = [int(x) for x in sys.argv[1:]] or [s["tvshowid"] for s in shows]
ok = bad = 0
for s in shows:
    if s["tvshowid"] not in ids: continue
    ep, want = expected(s["tvshowid"])
    rpc("GUI.ActivateWindow", {"window": "videos", "parameters": [f"videodb://tvshows/titles/{s['tvshowid']}/"]})
    got = ""
    sid = str(s["tvshowid"])
    for _ in range(30):
        time.sleep(0.5)
        upd = rpc("XBMC.GetInfoBooleans", {"booleans": [f"Container({i}).IsUpdating" for i in (5110, 5111, 5112, 5113)] + ["Control.IsVisible(5101)"]})["result"]
        vis = upd.pop("Control.IsVisible(5101)")
        # radiobutton labels come back as "label ( )" / "label (*)"
        got = lab("Control.GetLabel(5101)").removesuffix(" ( )").removesuffix(" (*)") if vis else "(hidden)"
        fresh = lab("Container(5113).ListItem.TvShowDBID") == sid
        if not any(upd.values()) and (fresh or (want == "no episodes" and got in EMPTY)):
            break
    content = lab("Container.Content")
    match = (got == want) or (want == "no episodes" and got in EMPTY)
    ok += match; bad += (not match)
    print(f"{'OK ' if match else 'BAD'} {s['tvshowid']:>3} {s['title'][:34]:<34} {s['watchedepisodes']}/{s['episode']} [{content}] want={want!r} got={got!r}")
print("ok", ok, "bad", bad)
sys.exit(1 if bad else 0)
