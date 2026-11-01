"""Lumen kids guard: in kids mode, only the kids' titles play.

Run by the skin (Timers.xml, lumenkidsguard) for each video started while kids mode is on, and for the one playing when
it comes on: RunScript(special://skin/scripts/lumen_kidsguard.py). It lets through a film tagged "Cinematheque Kids",
an episode of a show tagged so, and a file outside the library that the Cinémathèque app lists for kids; anything else
stops, and so does everything if the check itself fails. The app writes that list (one full Kodi path per line, #
starting a comment) because the tag can't reach a file the library doesn't have, and sends every TV where it is
(Skin.String(cinemathequekidsfiles)). A video let through goes into Home window property lumen.kidsguard, so the timer
leaves it alone until it stops (lumenkidsguardreset). Live TV has its own rule (lumenkidslivetv).
"""
import json
import traceback
import unicodedata

KIDS_TAG = "Cinematheque Kids"


def kids_files(text):
    """The paths in the app's list. The app writes them composed (NFC); Kodi returns a name as the NAS stores it."""
    return {unicodedata.normalize("NFC", line.strip()) for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")}


def tagged(tags):
    """Kodi matches tags ignoring case (the kids lists too)."""
    return KIDS_TAG.casefold() in (t.casefold() for t in tags)


def allowed(item, show_tags, listed):
    """Whether item (Player.GetItem with tag and file) may play in kids mode; show_tags are its show's tags, listed the
    app's list (a file on it plays even once a scan has added it to the library)."""
    kind = item.get("type")
    if kind == "channel":
        return True  # live TV: lumenkidslivetv
    if kind == "movie" and tagged(item.get("tag", [])) or kind == "episode" and tagged(show_tags):
        return True
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
    if not playing or not video:
        return  # Kodi hasn't announced the video yet, or it stopped meanwhile: the timer tries again
    item = (rpc("Player.GetItem", {"playerid": video[0]["playerid"], "properties": ["tag", "tvshowid", "file"]}) or {}).get("item") or {}
    show_tags, listed = [], set()
    if item.get("type") == "episode" and item.get("tvshowid", -1) > 0:
        show = rpc("VideoLibrary.GetTVShowDetails", {"tvshowid": item["tvshowid"], "properties": ["tag"]}) or {}
        show_tags = show.get("tvshowdetails", {}).get("tag", [])
    path = xbmc.getInfoLabel("Skin.String(cinemathequekidsfiles)")
    if path and not allowed(item, show_tags, listed):
        f = xbmcvfs.File(path)
        listed = kids_files(bytes(f.readBytes()).decode("utf-8-sig", "replace"))
        f.close()
    if not allowed(item, show_tags, listed):
        xbmc.log(f"lumen_kidsguard: kids mode, stopped {item.get('type')} {playing}", xbmc.LOGINFO)
        xbmc.executebuiltin("PlayerControl(Stop)")
    elif xbmc.getInfoLabel("Player.FilenameAndPath") == playing:  # another video now: the timer judges that one
        xbmcgui.Window(10000).setProperty("lumen.kidsguard", playing)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # a check that can't finish lets nothing through
        import xbmc
        xbmc.log("lumen_kidsguard: " + traceback.format_exc(), xbmc.LOGERROR)
        if xbmc.getCondVisibility("Skin.HasSetting(CinemathequeKids)"):
            xbmc.executebuiltin("PlayerControl(Stop)")
