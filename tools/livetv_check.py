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


def free(r):
    if rpc("Player.GetActivePlayers")["result"]:
        r.skip("something is playing on the box; not touching it")
        return False
    if not channels():
        r.skip("no live TV channels on this box")
        return False
    return True


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
