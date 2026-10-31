#!/usr/bin/env python3
"""Live TV (a PVR add-on such as IPTV Simple) checks against a Kodi box over JSON-RPC.

    python3 tools/livetv_check.py NAME [NAME ...]   # run the named checks
    python3 tools/livetv_check.py all               # every check, then cleanup

Each check prints PASS/FAIL/SKIP lines; the exit status is 1 if anything failed.
Kids mode has no live TV: nothing filters channels for children.
"""
import os
import subprocess
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kids_check  # noqa: E402
from kodi_gui import SSH, cond, lab, labs, reset_home, rpc, shot, wait_for  # noqa: E402

CHECKS = {}
# the Live TV windows (each serves TV and radio)
WINDOWS = ["tvchannels", "tvguide", "tvrecordings", "tvtimers", "tvsearch", "radiochannels"]


def check(name):
    def register(fn):
        CHECKS[name] = fn
        return fn
    return register


def channels():
    r = rpc("PVR.GetChannels", {"channelgroupid": "alltv", "properties": ["channel", "channelnumber"]})
    return sorted(r.get("result", {}).get("channels", []), key=lambda c: c["channelnumber"])


def playing(name):
    """That channel is on screen: full screen video with no dialog over it (Kodi shows a busy dialog while a stream
    opens), its name, a known codec. Not the clock: it jumps about while a stream opens and can stand still on a live one."""
    def on():
        l = labs(["VideoPlayer.VideoCodec", "VideoPlayer.ChannelName"])
        return (l["VideoPlayer.VideoCodec"] != "" and l["VideoPlayer.ChannelName"] == name and cond("Window.IsActive(fullscreenvideo)")
                and not cond("System.HasActiveModalDialog") and cond("Player.Playing"))
    return wait_for(on, 25, 0.5)


def stop():
    for p in rpc("Player.GetActivePlayers")["result"]:
        rpc("Player.Stop", {"playerid": p["playerid"]})
    return wait_for(lambda: rpc("Player.GetActivePlayers")["result"] == [], 10)


def live_channel(keep=False):
    """The first channel that plays in normal mode, or None. With keep it's left playing: opening a stream again
    right away can take longer than playing() waits (the relays are slow at times)."""
    for c in channels()[:5]:
        rpc("Player.Open", {"item": {"channelid": c["channelid"]}})
        ok = playing(c["channel"])
        if not (ok and keep):
            stop()
        if ok:
            return c
    return None


def client():
    return rpc("PVR.GetClients")["result"]["clients"][0]


def all_label():
    """Kodi's name for its all-channels group, in the box's language ("All channels" in English)."""
    return rpc("PVR.GetChannelGroupDetails", {"channelgroupid": "alltv"})["result"]["channelgroupdetails"]["label"]


def group_id(label):
    for g in rpc("PVR.GetChannelGroups", {"channeltype": "tv"})["result"]["channelgroups"]:
        if g["label"] == label:
            return g["channelgroupid"]
    return None


def group_channels(label):
    """The group's channels in number order, with their programme now and next (nothing for an unknown group)."""
    gid = "alltv" if label == all_label() else group_id(label)
    if gid is None:
        return []
    r = rpc("PVR.GetChannels", {"channelgroupid": gid,
                                "properties": ["channel", "channelnumber", "uniqueid", "broadcastnow", "broadcastnext"]})
    return sorted(r.get("result", {}).get("channels", []), key=lambda c: c["channelnumber"])


def group_path(label):
    """A channel group's path: Kodi's own all-channels group is local (-1), the others come from the PVR add-on."""
    owner = -1 if label == all_label() else client()["clientid"]
    return "pvr://channels/tv/%s@%d/" % (urllib.parse.quote(label), owner)


def channel_path(label, c):
    """Playing this path plays the channel through that group, so the remote's channel keys then stay in it."""
    cl = client()
    return group_path(label) + "%d@%s_%d.pvr" % (cl["instanceid"], cl["addonid"], c["uniqueid"])


def play_in(label, c):
    rpc("Player.Open", {"item": {"file": channel_path(label, c)}})
    return playing(c["channel"])


def guide_channel():
    """A channel with a programme on now (none when the guide hasn't loaded)."""
    return next((c for c in group_channels(all_label()) if c.get("broadcastnow")), None)


def noguide_channel():
    """A channel the guide has nothing for."""
    return next((c for c in group_channels(all_label()) if not c.get("broadcastnow")), None)


def kids_channel():
    return next(iter(group_channels("Kids")), None)


def free(r):
    if rpc("Player.GetActivePlayers")["result"]:
        r.skip("something is playing on the box; not touching it")
        return False
    if not channels():
        r.skip("no live TV channels on this box")
        return False
    return True


@check("groups")
def c_groups(r):
    """Home keeps the paths of the all-channels group and of the Kids group (empty while there is none) in its window
    properties: every Lumen entry point opens live TV through them, which keeps the remote's channel keys on that list."""
    reset_home()
    want_all = group_path(all_label())
    want_kids = group_path("Kids") if group_channels("Kids") else ""

    def props():
        return labs(["Window(home).Property(lumen.tv.all)", "Window(home).Property(lumen.tv.kids)"])
    ok = wait_for(lambda: props()["Window(home).Property(lumen.tv.all)"] == want_all, 10)
    p = props()
    r.ok("all channels: " + want_all, ok, p)
    kids = p["Window(home).Property(lumen.tv.kids)"]
    if want_kids:
        r.ok("kids: " + want_kids, kids == want_kids, p)
    else:  # no kids channels yet: an empty Kids group may or may not be listed; either way no channel plays through it
        r.ok("no kids channels: empty, or the empty Kids group's path", kids in ("", group_path("Kids")), p)


BANNER = 1120  # Custom_1120_LumenLiveBanner.xml


def zap_to(action, name, timeout=5):
    """Channel up/down (Kodi's own action, what the remote's keys send); True once that channel is the one shown."""
    rpc("Input.ExecuteAction", {"action": action})
    return wait_for(lambda: lab("VideoPlayer.ChannelName") == name, timeout, 0.2)


def now_next(c):
    """The channel's programme now and next as Kodi has them at this moment."""
    fresh = next(x for x in group_channels(all_label()) if x["channelid"] == c["channelid"])
    return (fresh.get("broadcastnow") or {}).get("title", ""), (fresh.get("broadcastnext") or {}).get("title", "")


@check("banner")
def c_banner(r):
    """Zapping shows the banner: the new channel's number and name, its programme now (with times) and next."""
    if not free(r):
        return
    every = group_channels(all_label())
    pair = next(((a, b) for a, b in zip(every, every[1:]) if a.get("broadcastnow") and b.get("broadcastnow")), None)
    if not pair:
        r.skip("no two neighbouring channels with guide data")
        return
    a, b = pair
    kids_check.kids(False)
    reset_home()
    try:
        if not r.ok(f"{a['channel']} plays", play_in(all_label(), a)):
            return
        time.sleep(6)  # the banner from the start has gone
        r.ok(f"CH+ → {b['channel']}", zap_to("channelup", b["channel"]))
        r.ok("the banner shows", wait_for(lambda: cond(f"Window.IsVisible({BANNER})"), 3))
        title, nxt = now_next(b)
        l = labs(["VideoPlayer.ChannelNumberLabel", "VideoPlayer.ChannelName", "VideoPlayer.Title", "VideoPlayer.StartTime",
                  "VideoPlayer.EndTime", "VideoPlayer.NextTitle"])
        r.ok("number, name, programme now and next", l["VideoPlayer.ChannelNumberLabel"] == str(b["channelnumber"])
             and l["VideoPlayer.ChannelName"] == b["channel"] and l["VideoPlayer.Title"] == title and l["VideoPlayer.NextTitle"] == nxt
             and ":" in l["VideoPlayer.StartTime"] and ":" in l["VideoPlayer.EndTime"], l)
        shot("live-banner")
        r.ok("and goes after a few seconds", wait_for(lambda: not cond(f"Window.IsVisible({BANNER})"), 15))
    finally:
        stop()
        reset_home()


@check("banner_noguide")
def c_banner_noguide(r):
    """A channel the guide has nothing for: the banner shows number, logo and name, and no programme lines (Kodi's
    programme labels are empty; the screenshot shows no empty boxes)."""
    if not free(r):
        return
    every = group_channels(all_label())
    i = next((i for i, c in enumerate(every) if i and not c.get("broadcastnow")), None)
    if i is None:
        r.skip("every channel has guide data")
        return
    a, b = every[i - 1], every[i]
    kids_check.kids(False)
    reset_home()
    try:
        if not r.ok(f"{a['channel']} plays", play_in(all_label(), a)):
            return
        time.sleep(6)
        r.ok(f"CH+ → {b['channel']}", zap_to("channelup", b["channel"]))
        r.ok("the banner shows", wait_for(lambda: cond(f"Window.IsVisible({BANNER})"), 3))
        l = labs(["VideoPlayer.ChannelNumberLabel", "VideoPlayer.Title", "VideoPlayer.NextTitle"])
        r.ok("its number, no programme", l["VideoPlayer.ChannelNumberLabel"] == str(b["channelnumber"])
             and l["VideoPlayer.Title"] == "" and l["VideoPlayer.NextTitle"] == "", l)
        shot("live-banner-noguide")
    finally:
        stop()
        reset_home()


@check("zap_wrap")
def c_zap_wrap(r):
    """The channel keys go round the whole list: CH+ on the last channel gives the first, CH− back gives the last."""
    if not free(r):
        return
    every = group_channels(all_label())
    first, last = every[0], every[-1]
    kids_check.kids(False)
    reset_home()
    try:
        if not r.ok(f"{last['channel']} (last) plays", play_in(all_label(), last)):
            return
        r.ok(f"CH+ → {first['channel']} (first)", zap_to("channelup", first["channel"]))
        r.ok("still all channels", lab("VideoPlayer.ChannelGroup") == all_label(), lab("VideoPlayer.ChannelGroup"))
        r.ok(f"CH− → {last['channel']} (last)", zap_to("channeldown", last["channel"]))
    finally:
        stop()
        reset_home()


@check("updown_switch")
def c_updown_switch(r):
    """↑/↓ change channel without OK (pvrplayback.confirmchannelswitch off): remotes without channel keys zap with them."""
    if not free(r):
        return
    c = guide_channel() or group_channels(all_label())[0]
    kids_check.kids(False)
    reset_home()
    try:
        if not r.ok(f"{c['channel']} plays", play_in(all_label(), c)):
            return
        time.sleep(6)
        rpc("Input.Up")
        r.ok("↑ shows another channel", wait_for(lambda: lab("VideoPlayer.ChannelName") != c["channel"], 3))
        other = lab("VideoPlayer.ChannelName")
        time.sleep(8)  # a preview waiting for OK would fall back to the old channel by now
        l = labs(["VideoPlayer.ChannelName"])
        r.ok(f"and stays on it without OK ({other})", l["VideoPlayer.ChannelName"] == other and not cond("Player.ChannelPreviewActive")
             and cond("Player.Playing"), l)
    finally:
        stop()
        reset_home()


@check("normal_channel")
def c_normal_channel(r):
    """Normal mode: a channel plays full screen as live TV."""
    if not free(r):
        return
    kids_check.kids(False)
    reset_home()
    try:
        c = live_channel(keep=True)
        if not r.ok("a channel plays", c is not None):
            return
        r.ok(f"{c['channel']}: as live TV, full screen", wait_for(lambda: cond("VideoPlayer.Content(livetv)") and cond("Window.IsActive(fullscreenvideo)"), 5),
             lab("System.CurrentWindow"))
    finally:
        r.ok("stops", stop())
        reset_home()


@check("live_overlay")
def c_live_overlay(r):
    """The player controls on a channel. They live in the seek bar dialog, which JSON-RPC can't read (labels come from
    the active window or the topmost modal dialog only, GUIInfoHelper.cpp:92), so this checks what they show and saves
    screenshots for the eye: the channel's name and number, "Live" for the time, no "Ends at", the sleep timer alone."""
    if not free(r):
        return
    kids_check.kids(False)
    reset_home()
    c = live_channel(keep=True)
    if not r.ok("a channel that plays", c is not None):
        return
    try:
        rpc("Input.ShowOSD")
        r.ok("player controls open", wait_for(lambda: cond("Window.IsActive(videoosd)") and cond("Window.IsVisible(seekbar)"), 4))
        time.sleep(1)
        l = labs(["VideoPlayer.ChannelName", "VideoPlayer.ChannelNumberLabel"])
        r.ok("live TV, with its channel's name and number", cond("VideoPlayer.Content(livetv)") and l["VideoPlayer.ChannelName"] == c["channel"]
             and l["VideoPlayer.ChannelNumberLabel"] == str(c["channelnumber"]), l)
        shot("livetv-overlay")
        if SSH:  # the sleep timer needs a builtin to set: kodi-send
            subprocess.run(["ssh", SSH, 'kodi-send --action="AlarmClock(lumensleep,PlayerControl(Stop),30,silent)"'], capture_output=True, timeout=30)
            r.ok("sleep timer on", wait_for(lambda: cond("System.HasAlarm(lumensleep)"), 4))
            time.sleep(1)
            shot("livetv-overlay-sleep")
            subprocess.run(["ssh", SSH, 'kodi-send --action="CancelAlarm(lumensleep,true)"'], capture_output=True, timeout=30)
            r.ok("sleep timer off again", wait_for(lambda: not cond("System.HasAlarm(lumensleep)"), 4))
    finally:
        stop()
        reset_home()


@check("kids_channel")
def c_kids_channel(r):
    """Kids mode: a channel started from outside (the phone app, a remote app) stops at once."""
    if not free(r):
        return
    c = live_channel()
    if not r.ok("a channel that plays in normal mode", c is not None):
        return
    try:
        kids_check.kids(True)
        reset_home()
        rpc("Player.Open", {"item": {"channelid": c["channelid"]}})
        t0 = time.time()
        # right after Player.Open the player may not exist yet: what counts is that it's gone a while later
        time.sleep(1)
        r.ok(f"kids: {c['channel']} stops", wait_for(lambda: rpc("Player.GetActivePlayers")["result"] == [], 15)
             and not cond("Player.HasMedia"), f"{time.time() - t0:.1f} s")
        time.sleep(6)
        r.ok("and stays stopped", rpc("Player.GetActivePlayers")["result"] == [] and not cond("Player.HasMedia"))
    finally:
        stop()
        kids_check.kids(False)
        reset_home()


@check("kids_switch")
def c_kids_switch(r):
    """A channel playing when kids mode comes on stops."""
    if not free(r):
        return
    c = live_channel(keep=True)
    if not r.ok("a channel that plays in normal mode", c is not None):
        return
    try:
        kids_check.kids(True)
        r.ok("kids mode on: it stops", wait_for(lambda: rpc("Player.GetActivePlayers")["result"] == [], 15))
    finally:
        stop()
        kids_check.kids(False)
        reset_home()


@check("kids_windows")
def c_kids_windows(r):
    """Kids mode: the Live TV windows send back to Home; normal mode opens them."""
    if not free(r):
        return
    try:
        kids_check.kids(True)
        for w in WINDOWS:
            reset_home()
            rpc("GUI.ActivateWindow", {"window": w})
            time.sleep(0.5)
            r.ok(f"kids: {w} sends back to Home",
                 wait_for(lambda: cond("Window.IsActive(home)") and not cond(f"Window.IsActive({w})"), 3),
                 lab("System.CurrentWindow"))
        kids_check.kids(False)
        reset_home()
        rpc("GUI.ActivateWindow", {"window": "tvchannels"})
        r.ok("normal: TV channels open", wait_for(lambda: cond("Window.IsActive(tvchannels)"), 5), lab("System.CurrentWindow"))
    finally:
        kids_check.kids(False)
        reset_home()


CHECKS["cleanup"] = kids_check.CHECKS["cleanup"]


if __name__ == "__main__":
    sys.exit(kids_check.main(sys.argv[1:], CHECKS))
