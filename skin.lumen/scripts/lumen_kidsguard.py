"""Lumen kids guard: in kids mode, only the kids' titles play.

Run by the skin (Timers.xml, lumenkidsguard) for each video started while kids mode is on, and for the one playing when
it comes on: RunScript(special://skin/scripts/lumen_kidsguard.py). It lets through a film tagged "Cinematheque Kids",
an episode of a show tagged so, and a file outside the library that the Cinémathèque app lists for kids; anything else
stops. The app writes that list (one full Kodi path per line, # starting a comment) because the tag can't reach a file
the library doesn't have, and sends every TV where it is (Skin.String(cinemathequekidsfiles)). A video let through goes
into Home window property lumen.kidsguard, so the timer leaves it alone. Live TV has its own rule (lumenkidslivetv).
"""
import json
import unicodedata

KIDS_TAG = "Cinematheque Kids"


def kids_files(text):
    """The paths in the app's list. The app writes them composed (NFC); Kodi returns a name as the NAS stores it."""
    return {unicodedata.normalize("NFC", line.strip()) for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")}


def allowed(item, show_tags, listed):
    """Whether item (Player.GetItem with tag and file) may play in kids mode; show_tags are its show's tags."""
    kind = item.get("type")
    if kind == "channel":
        return True  # live TV: lumenkidslivetv
    if kind == "movie":
        return KIDS_TAG in item.get("tag", [])
    if kind == "episode":
        return KIDS_TAG in show_tags
    return unicodedata.normalize("NFC", item.get("file", "")) in listed


def main():
    import xbmc
    import xbmcgui
    import xbmcvfs

    def rpc(method, params):
        return json.loads(xbmc.executeJSONRPC(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))).get("result")

    if not xbmc.getCondVisibility("Skin.HasSetting(CinemathequeKids)"):
        return
    playing = xbmc.getInfoLabel("Player.FilenameAndPath")
    video = [p for p in rpc("Player.GetActivePlayers", {}) or [] if p["type"] == "video"]
    if not video:
        return  # stopped meanwhile
    item = (rpc("Player.GetItem", {"playerid": video[0]["playerid"], "properties": ["tag", "tvshowid", "file"]}) or {}).get("item") or {}
    show_tags, listed = [], set()
    if item.get("type") == "episode" and item.get("tvshowid", -1) > 0:
        show = rpc("VideoLibrary.GetTVShowDetails", {"tvshowid": item["tvshowid"], "properties": ["tag"]}) or {}
        show_tags = show.get("tvshowdetails", {}).get("tag", [])
    elif item.get("type") not in ("movie", "channel"):
        path = xbmc.getInfoLabel("Skin.String(cinemathequekidsfiles)")
        if path:
            f = xbmcvfs.File(path)
            listed = kids_files(bytes(f.readBytes()).decode("utf-8-sig", "replace"))
            f.close()
    if allowed(item, show_tags, listed):
        xbmcgui.Window(10000).setProperty("lumen.kidsguard", playing)
    else:
        xbmc.executebuiltin("PlayerControl(Stop)")


if __name__ == "__main__":
    main()
