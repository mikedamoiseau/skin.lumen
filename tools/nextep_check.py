#!/usr/bin/env python3
"""Next episode card checks (Custom_1118_LumenNext.xml, the lookup in VideoFullScreen.xml, Timers.xml) against a Kodi box.

    python3 tools/nextep_check.py NAME [NAME ...]   # run the named checks
    python3 tools/nextep_check.py all               # every check, then cleanup

Each check plays library episodes near their end and lets some end by themselves, so it uses episodes nobody has
started (no play count, no last played date, no resume point) and puts every one it touched back that way, Kodi's
own way (VideoLibrary.SetEpisodeDetails: play count 0 and an empty last played date clear both). Playback events
come from Kodi's JSON-RPC notifications on TCP 9090 (Player.OnStop carries end = true after a natural end). Every
play is printed at the end (box, times, episode ids, kids mode) for whoever keeps the household's play history.
"""
import json
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kids_check  # noqa: E402
from kids_check import tagged_shows  # noqa: E402
from kodi_gui import HOST, cond, lab, reset_home, rpc, set_skin, shot, wait_for  # noqa: E402

CHECKS = {}
CARD, INFO = 1118, 1119  # the card (modal: Play now, Cancel) and the app's next item (modeless)
LINE, COUNTDOWN, PLAY_NOW, CANCEL = 103, 104, 110, 111
HOLD, HOLD_LABEL, SECS = "cinemathequeholdnext", "cinemathequenextlabel", "lumen.nextcard"
EVENTS = []  # (time, method, data) from the box
BEFORE = {}  # episodeid -> state before this run touched it
PLAYS = []  # (start, end, episodeid, kids)


def check(name):
    def register(fn):
        CHECKS[name] = fn
        return fn
    return register


# ---------- the box's notifications ----------

def listen():
    s = socket.create_connection((HOST.split(":")[0], 9090))
    buf, dec = "", json.JSONDecoder()
    while True:
        data = s.recv(65536)
        if not data:
            return
        buf += data.decode("utf-8", "replace")
        while buf.strip():
            buf = buf.lstrip()
            try:
                obj, n = dec.raw_decode(buf)
            except ValueError:
                break
            buf = buf[n:]
            if obj.get("method", "").startswith("Player."):
                EVENTS.append((time.time(), obj["method"], obj.get("params", {}).get("data") or {}))


def event(method, since, timeout):
    """The first `method` notification at or after `since`, waiting up to timeout seconds."""
    def hit():
        return next((e for e in EVENTS if e[1] == method and e[0] >= since), None)
    wait_for(lambda: hit() is not None, timeout, 0.2)
    return hit()


# ---------- episodes ----------

def state(eid):
    e = rpc("VideoLibrary.GetEpisodeDetails", {"episodeid": eid, "properties": ["playcount", "lastplayed", "resume"]})["result"]["episodedetails"]
    return {"playcount": e["playcount"], "lastplayed": e["lastplayed"], "resume": e["resume"]["position"]}


def fresh(e):
    return not e["playcount"] and not e["lastplayed"] and not e["resume"]["position"]


def episodes(show):
    r = rpc("VideoLibrary.GetEpisodes", {"tvshowid": show, "properties": ["season", "episode", "title", "playcount", "lastplayed", "resume"]})["result"]
    return sorted(r.get("episodes", []), key=lambda e: (e["season"], e["episode"], e["episodeid"]))


def next_main(eps, e):
    """Lumen's rule (the app's too): the next main episode by number; specials and watched state don't matter."""
    return next((x for x in eps if x["season"] > 0 and (x["season"], x["episode"]) > (e["season"], e["episode"])), None)


def line(e):
    return f"S{e['season']} E{e['episode']}  ·  {e['title']}"  # Lumen's episode line


def pick(shows, last=False, cross=False):
    """(episode, next one or None, all episodes) from the first of shows nobody has started, with both unwatched."""
    for show in shows:
        eps = episodes(show)
        main = [e for e in eps if e["season"] > 0]
        if not main or not all(fresh(e) for e in eps):
            continue
        if last:
            return main[-1], None, eps
        for e in main:
            n = next_main(eps, e)
            if n and (not cross or n["season"] != e["season"]):
                return e, n, eps
    return None, None, None


def library_shows(kids):
    tagged = tagged_shows()
    r = rpc("VideoLibrary.GetTVShows", {"properties": ["dateadded"]})["result"]["tvshows"]
    return [s["tvshowid"] for s in sorted(r, key=lambda s: s["dateadded"]) if (s["tvshowid"] in tagged) == kids]


def touch(*eids):
    for eid in eids:
        if eid not in BEFORE:
            BEFORE[eid] = state(eid)


def restore(r, *eids):
    for eid in eids:
        b = BEFORE[eid]
        rpc("VideoLibrary.SetEpisodeDetails", {"episodeid": eid, "playcount": b["playcount"], "lastplayed": b["lastplayed"],
                                               "resume": {"position": b["resume"], "total": 0}})
        r.ok(f"episode {eid} back as it was", state(eid) == b, state(eid))


# ---------- playing ----------

def remaining():
    v = lab("Player.TimeRemaining(secs)")
    return int(v) if v.isdigit() else -1


def play_near_end(eid, secs_left):
    """Start episode eid from the beginning (no resume) and jump to secs_left before its end; True once playing there."""
    touch(eid)
    t0 = time.time()
    rpc("Player.Open", {"item": {"episodeid": eid}, "options": {"resume": False}})
    if not event("Player.OnAVStart", t0, 40):
        return False
    PLAYS.append([t0, None, eid, cond("Skin.HasSetting(CinemathequeKids)")])
    time.sleep(1)
    total = rpc("Player.GetProperties", {"playerid": 1, "properties": ["totaltime"]})["result"]["totaltime"]
    t = total["hours"] * 3600 + total["minutes"] * 60 + total["seconds"] - secs_left
    rpc("Player.Seek", {"playerid": 1, "value": {"time": {"hours": t // 3600, "minutes": t // 60 % 60, "seconds": t % 60, "milliseconds": 0}}})
    return wait_for(lambda: 0 < remaining() <= secs_left + 2 and cond("Player.Playing") and cond("Window.IsActive(fullscreenvideo)"), 20)


def ended(since, timeout):
    """The OnStop notification after since (its data), noting the play's end."""
    e = event("Player.OnStop", since, timeout)
    for p in PLAYS:
        if p[1] is None:
            p[1] = e[0] if e else time.time()
    return e and e[2]


def nothing_plays(r, since, wait=8):
    time.sleep(wait)
    on = [e for e in EVENTS if e[1] == "Player.OnPlay" and e[0] >= since]
    r.ok(f"nothing starts within {wait} s", not on and not rpc("Player.GetActivePlayers")["result"], on)


def stop_all():
    if rpc("Player.GetActivePlayers")["result"]:
        t = time.time()
        rpc("Player.Stop", {"playerid": 1})
        ended(t, 15)
        time.sleep(2)


def card_at(secs):
    """Wait until secs or less are left, then up to 3 s for the card."""
    wait_for(lambda: 0 <= remaining() <= secs, secs + 60, 0.3)
    return wait_for(lambda: cond(f"Window.IsVisible({CARD})"), 3, 0.2)


def first_pair():
    """The first two episodes of the longest-unstarted show without the kids tag."""
    return pick(library_shows(False))


# ---------- checks ----------

@check("card_cancel")
def c_card_cancel(r):
    e, n, _ = first_pair()
    if not e:
        return r.skip("no unstarted show with two episodes")
    try:
        r.ok("episode plays 40 s before its end", play_near_end(e["episodeid"], 40))
        r.ok("no card with more than 30 s left", not cond(f"Window.IsVisible({CARD})") and remaining() > 31, remaining())
        r.ok("card shows once 30 s are left", card_at(29))
        shot("nextep-card")
        r.ok("card names the next episode", lab(f"Control.GetLabel({LINE})") == line(n), (lab(f"Control.GetLabel({LINE})"), line(n)))
        r.ok("Play now has focus", cond(f"Control.HasFocus({PLAY_NOW})"))
        rpc("Input.Back")
        r.ok("Back hides the card", wait_for(lambda: not cond(f"Window.IsVisible({CARD})"), 3))
        time.sleep(3)
        r.ok("card stays hidden", not cond(f"Window.IsVisible({CARD})") and cond("Player.Playing"))
        t = time.time()
        d = ended(t, 40)
        r.ok("episode ends by itself", bool(d and d.get("end")), d)
        nothing_plays(r, t)
    finally:
        stop_all()
        restore(r, e["episodeid"])


@check("natural_end")
def c_natural_end(r):
    e, n, _ = first_pair()
    if not e:
        return r.skip("no unstarted show with two episodes")
    touch(n["episodeid"])
    try:
        rpc("VideoLibrary.SetEpisodeDetails", {"episodeid": n["episodeid"], "resume": {"position": 600, "total": 0}})
        r.ok("episode plays 25 s before its end", play_near_end(e["episodeid"], 25))
        r.ok("card shows", card_at(25))
        t = time.time()
        d = ended(t, 40)
        r.ok("episode ends by itself (end = true)", bool(d and d.get("end")), d)
        stop = event("Player.OnStop", t, 1)
        p = event("Player.OnPlay", stop[0] if stop else t, 6)
        item = (p and p[2].get("item")) or {}
        r.ok("next episode starts within 5 s of the end", bool(stop and p) and p[0] - stop[0] <= 5 and item.get("type") == "episode"
             and item.get("id") == n["episodeid"], p and stop and (round(p[0] - stop[0], 1), item))
        if p:
            PLAYS.append([p[0], None, n["episodeid"], cond("Skin.HasSetting(CinemathequeKids)")])
            event("Player.OnAVStart", p[0], 30)
            time.sleep(2)
            pos = lab("Player.Time(secs)")
            r.ok("it resumes Kodi's point (600 s)", pos.isdigit() and 595 <= int(pos) <= 640, pos)
            r.ok("full screen", cond("Window.IsActive(fullscreenvideo)"))
            t = time.time()
            rpc("Input.ExecuteAction", {"action": "stop"})
            d = ended(t, 15)
            r.ok("stopped by hand (end = false)", d is not None and not d.get("end"), d)
            nothing_plays(r, t)
    finally:
        stop_all()
        restore(r, e["episodeid"], n["episodeid"])


@check("play_now")
def c_play_now(r):
    e, n, _ = first_pair()
    if not e:
        return r.skip("no unstarted show with two episodes")
    touch(n["episodeid"])
    try:
        r.ok("episode plays 28 s before its end", play_near_end(e["episodeid"], 28))
        r.ok("card shows", card_at(28))
        if not r.ok("Play now has focus", cond(f"Control.HasFocus({PLAY_NOW})")):
            return
        t = time.time()
        rpc("Input.Select")
        d = ended(t, 8)
        r.ok("Play now ends the episode (end = true)", bool(d and d.get("end")), d)
        p = event("Player.OnPlay", t, 10)
        item = (p and p[2].get("item")) or {}
        r.ok("next episode starts", item.get("id") == n["episodeid"], item)
        if p:
            PLAYS.append([p[0], None, n["episodeid"], cond("Skin.HasSetting(CinemathequeKids)")])
            event("Player.OnAVStart", p[0], 30)
            time.sleep(2)
    finally:
        stop_all()
        restore(r, e["episodeid"], n["episodeid"])


@check("manual_stop")
def c_manual_stop(r):
    """Stop with the remote's Stop while the card shows: nothing starts. Also a season change: S1's last -> S2 E1."""
    e, n, _ = pick(library_shows(False)[::-1], cross=True)
    if not e:
        return r.skip("no unstarted show with a second season")
    try:
        r.ok("episode plays 35 s before its end", play_near_end(e["episodeid"], 35))
        r.ok("card shows", card_at(29))
        r.ok("card names next season's first episode", lab(f"Control.GetLabel({LINE})") == line(n), (lab(f"Control.GetLabel({LINE})"), line(n)))
        wait_for(lambda: 0 <= remaining() <= 20, 15, 0.3)
        t = time.time()
        rpc("Input.ExecuteAction", {"action": "stop"})
        d = ended(t, 10)
        r.ok("Stop stops it with the card up (end = false)", d is not None and not d.get("end"), d)
        nothing_plays(r, t)
    finally:
        stop_all()
        restore(r, e["episodeid"])


@check("pause")
def c_pause(r):
    """The card takes the remote, but Play/Pause still reaches the player."""
    e, n, _ = first_pair()
    if not e:
        return r.skip("no unstarted show with two episodes")
    try:
        r.ok("episode plays 32 s before its end", play_near_end(e["episodeid"], 32))
        r.ok("card shows", card_at(29))
        rpc("Input.ExecuteAction", {"action": "playpause"})
        r.ok("Play/Pause pauses under the card", wait_for(lambda: cond("Player.Paused"), 3))
        r.ok("card stays while paused", cond(f"Window.IsVisible({CARD})"))
        rpc("Input.ExecuteAction", {"action": "playpause"})
        r.ok("and plays again", wait_for(lambda: cond("Player.Playing"), 3))
    finally:
        stop_all()
        restore(r, e["episodeid"])


@check("playlist")
def c_playlist(r):
    """Kodi's own playlist goes on to the next item: no card, and only Kodi starts it (no double play)."""
    e, n, _ = first_pair()
    if not e:
        return r.skip("no unstarted show with two episodes")
    touch(e["episodeid"], n["episodeid"])
    try:
        rpc("Playlist.Clear", {"playlistid": 1})
        rpc("Playlist.Add", {"playlistid": 1, "item": [{"episodeid": e["episodeid"]}, {"episodeid": n["episodeid"]}]})
        t0 = time.time()
        rpc("Player.Open", {"item": {"playlistid": 1, "position": 0}})
        if not r.ok("playlist starts", event("Player.OnAVStart", t0, 40) is not None):
            return
        PLAYS.append([t0, None, e["episodeid"], cond("Skin.HasSetting(CinemathequeKids)")])
        time.sleep(1)
        total = rpc("Player.GetProperties", {"playerid": 1, "properties": ["totaltime"]})["result"]["totaltime"]
        t = total["hours"] * 3600 + total["minutes"] * 60 + total["seconds"] - 25
        rpc("Player.Seek", {"playerid": 1, "value": {"time": {"hours": t // 3600, "minutes": t // 60 % 60, "seconds": t % 60, "milliseconds": 0}}})
        r.ok("no card while Kodi's playlist has a next item", not card_at(20))
        t = time.time()
        d = ended(t, 40)
        r.ok("episode ends by itself", bool(d and d.get("end")), d)
        time.sleep(8)
        on = [x[2].get("item", {}).get("id") for x in EVENTS if x[1] == "Player.OnPlay" and x[0] >= t]
        r.ok("exactly one start: Kodi's next item", on == [n["episodeid"]], on)
        if on:
            PLAYS.append([t, None, n["episodeid"], cond("Skin.HasSetting(CinemathequeKids)")])
    finally:
        stop_all()
        rpc("Playlist.Clear", {"playlistid": 1})
        restore(r, e["episodeid"], n["episodeid"])


@check("seek_back")
def c_seek_back(r):
    """In the last 2 seconds (armed), seeking back 30 s disarms: a later stop by hand starts nothing."""
    e, n, _ = first_pair()
    if not e:
        return r.skip("no unstarted show with two episodes")
    try:
        r.ok("episode plays 8 s before its end", play_near_end(e["episodeid"], 8))
        r.ok("card shows", wait_for(lambda: cond(f"Window.IsVisible({CARD})"), 3))
        armed = wait_for(lambda: 0 < remaining() <= 2, 10, 0.1)
        rpc("Player.Seek", {"playerid": 1, "value": {"seconds": -30}})
        if not r.ok("caught the last 2 seconds and seeked back", armed and wait_for(lambda: remaining() > 20, 5), remaining()):
            return
        time.sleep(2)
        t = time.time()
        rpc("Input.ExecuteAction", {"action": "stop"})
        d = ended(t, 10)
        r.ok("stopped by hand (end = false)", d is not None and not d.get("end"), d)
        nothing_plays(r, t)
    finally:
        stop_all()
        restore(r, e["episodeid"])


@check("hold")
def c_hold(r):
    """The app holds the next step (its Up next, stop after this, bedtime): no card, no auto-play; its own label shows."""
    e, n, _ = first_pair()
    if not e:
        return r.skip("no unstarted show with two episodes")
    was = (cond(f"Skin.HasSetting({HOLD})"), lab(f"Skin.String({HOLD_LABEL})"))
    try:
        r.ok("episode plays 45 s before its end", play_near_end(e["episodeid"], 45))
        time.sleep(5)  # the app sends its own values on every OnPlay; ours go after it
        set_skin(HOLD, True)
        set_skin(HOLD_LABEL, "Next: Lumen check, S9 E9")
        wait_for(lambda: 0 <= remaining() <= 28, 30, 0.3)
        time.sleep(2)
        r.ok("hold still set (the app didn't send again)", cond(f"Skin.HasSetting({HOLD})"))
        r.ok("no card", not cond(f"Window.IsVisible({CARD})"))
        r.ok("the app's next item shows", cond(f"Window.IsVisible({INFO})"))
        shot("nextep-hold")
        set_skin(HOLD_LABEL, "")
        r.ok("nothing shows without a label", wait_for(lambda: not cond(f"Window.IsVisible({INFO})"), 3))
        t = time.time()
        d = ended(t, 40)
        r.ok("episode ends by itself", bool(d and d.get("end")), d)
        nothing_plays(r, t)
    finally:
        stop_all()
        set_skin(HOLD, was[0])
        set_skin(HOLD_LABEL, was[1])
        restore(r, e["episodeid"])


@check("off")
def c_off(r):
    e, n, _ = first_pair()
    if not e:
        return r.skip("no unstarted show with two episodes")
    was = lab(f"Skin.String({SECS})")
    try:
        set_skin(SECS, "off")
        r.ok("episode plays 32 s before its end", play_near_end(e["episodeid"], 32))
        r.ok("no card with the setting off", not card_at(25))
        t = time.time()
        d = ended(t, 40)
        r.ok("episode ends by itself", bool(d and d.get("end")), d)
        nothing_plays(r, t)
    finally:
        stop_all()
        set_skin(SECS, was)
        restore(r, e["episodeid"])


@check("seconds")
def c_seconds(r):
    e, n, _ = first_pair()
    if not e:
        return r.skip("no unstarted show with two episodes")
    was = lab(f"Skin.String({SECS})")
    try:
        for secs in ("60", "90"):
            set_skin(SECS, secs)
            r.ok(f"[{secs}] episode plays {int(secs) + 8} s before its end", play_near_end(e["episodeid"], int(secs) + 8))
            r.ok(f"[{secs}] no card yet", not cond(f"Window.IsVisible({CARD})") and remaining() > int(secs) + 1, remaining())
            r.ok(f"[{secs}] card once {secs} s are left", card_at(int(secs) - 1))
            stop_all()
    finally:
        stop_all()
        set_skin(SECS, was)
        restore(r, e["episodeid"])


@check("last_episode")
def c_last_episode(r):
    e, _, _ = pick(library_shows(False), last=True)
    if not e:
        return r.skip("no unstarted show")
    try:
        r.ok("last episode plays 32 s before its end", play_near_end(e["episodeid"], 32))
        r.ok("no card after a show's last episode", not card_at(25))
        t = time.time()
        d = ended(t, 40)
        r.ok("episode ends by itself", bool(d and d.get("end")), d)
        nothing_plays(r, t)
    finally:
        stop_all()
        restore(r, e["episodeid"])


@check("kids")
def c_kids(r):
    """Kids mode: a kids show gets its card; another show playing in kids mode (started from a phone) gets none."""
    k, kn, _ = pick(library_shows(True))
    e, _, _ = first_pair()
    if not k or not e:
        return r.skip("no unstarted kids show with two episodes, or no unstarted show")
    try:
        set_skin("cinemathequekids", True)
        r.ok("kids episode plays 35 s before its end", play_near_end(k["episodeid"], 35))
        r.ok("card shows for a kids show", card_at(29))
        r.ok("card names its next episode", lab(f"Control.GetLabel({LINE})") == line(kn), (lab(f"Control.GetLabel({LINE})"), line(kn)))
        stop_all()
        r.ok("other episode plays 35 s before its end", play_near_end(e["episodeid"], 35))
        r.ok("no card for a show without the kids tag", not card_at(25))
        stop_all()
    finally:
        stop_all()
        set_skin("cinemathequekids", False)
        restore(r, k["episodeid"], e["episodeid"])


@check("setting_ui")
def c_setting_ui(r):
    """Configure skin › Next episode card goes 30 s → 60 s → 90 s → Off → 30 s."""
    was = lab(f"Skin.String({SECS})")
    try:
        set_skin(SECS, "")
        seen = []
        for want in ("60", "90", "off", ""):
            if not r.ok("press Next episode card", kids_check.settings_button(904)):
                return
            wait_for(lambda: lab(f"Skin.String({SECS})") == want, 3)
            seen.append(lab(f"Skin.String({SECS})"))
        r.ok("values cycle", seen == ["60", "90", "off", ""], seen)
        shot("nextep-setting")  # the value is the button's label2, which JSON-RPC can't read
    finally:
        set_skin(SECS, was)
        reset_home()


@check("restore")
def c_restore(r):
    for eid, b in BEFORE.items():
        r.ok(f"episode {eid} as before the run", state(eid) == b, state(eid))


@check("cleanup")
def c_cleanup(r):
    stop_all()
    r.ok("kids mode off", not cond("Skin.HasSetting(CinemathequeKids)"))
    r.ok("back on Home", reset_home() == "Home")


def main(names):
    threading.Thread(target=listen, daemon=True).start()
    time.sleep(1)
    if rpc("Player.GetActivePlayers")["result"]:
        print("FAIL setup: something is playing on the box; not touching it")
        return 1
    if names == ["all"]:
        names = [n for n in CHECKS if n not in ("restore", "cleanup")] + ["restore", "cleanup"]
    try:
        return kids_check.main(names, CHECKS)
    finally:
        stop_all()
        for eid, b in BEFORE.items():  # a crashed check still leaves its episodes as they were
            if state(eid) != b:
                rpc("VideoLibrary.SetEpisodeDetails", {"episodeid": eid, "playcount": b["playcount"], "lastplayed": b["lastplayed"],
                                                       "resume": {"position": b["resume"], "total": 0}})
        print(f"PLAYS on {HOST} (start-end, episode, kids):")
        for s, e, eid, kids in PLAYS:
            print(f"  {time.strftime('%H:%M:%S', time.localtime(s))}-{time.strftime('%H:%M:%S', time.localtime(e or time.time()))} episode {eid} kids={'on' if kids else 'off'}")
        print("TOUCHED (restored to):", json.dumps(BEFORE))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
