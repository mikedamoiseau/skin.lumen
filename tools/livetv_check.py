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
from kodi_gui import HOST, SSH, cond, focus_id, lab, labs, reset_home, restart_kodi, rpc, shot, wait_for  # noqa: E402

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
    return wait_for(on, 45, 0.5)  # a stream can take 20–30 s to open (the relays are slow at times)


def stop():
    if rpc("Player.GetActivePlayers")["result"]:
        played(lab("VideoPlayer.ChannelName"), "stop")
    for p in rpc("Player.GetActivePlayers")["result"]:
        rpc("Player.Stop", {"playerid": p["playerid"]})
    return wait_for(lambda: rpc("Player.GetActivePlayers")["result"] == [], 10)


def live_channel(keep=False):
    """The first channel that plays in normal mode, or None. With keep it's left playing: opening a stream again
    right away can take longer than playing() waits (the relays are slow at times)."""
    for c in channels()[:5]:
        played(c["channel"], "channelid")
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


def played(name, how):
    """Every test play goes to kodi-app (box, time, channel): one line each, on stderr."""
    print(f"PLAY {time.strftime('%H:%M:%S')} {name} ({how})", file=sys.stderr, flush=True)


def play_in(label, c):
    played(c["channel"], label)
    rpc("Player.Open", {"item": {"file": channel_path(label, c)}})
    return playing(c["channel"])


def play_first(cands):
    """Plays the first of up to three channels whose stream opens, through All channels, and returns it (None when none
    did): some relays are too slow to open at times."""
    for c in cands[:3]:
        if play_in(all_label(), c):
            return c
        stop()
    return None


def guide_pairs():
    """Neighbouring channels (in the list's order) that both have a programme on now."""
    every = group_channels(all_label())
    return [(a, b) for a, b in zip(every, every[1:]) if a.get("broadcastnow") and b.get("broadcastnow")]


def play_pair(pairs):
    """Plays the first channel of the first pair whose stream opens; returns that pair, or None."""
    a = play_first([p[0] for p in pairs])
    return next((p for p in pairs if a and p[0]["channelid"] == a["channelid"]), None)


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
    played(name, action)
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
    pairs = guide_pairs()
    if not pairs:
        r.skip("no two neighbouring channels with guide data")
        return
    kids_check.kids(False)
    reset_home()
    try:
        pair = play_pair(pairs)
        if not r.ok(f"a channel with guide data next to another plays ({pair and pair[0]['channel']})", pair is not None):
            return
        a, b = pair
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
    every = group_channels(all_label())
    kids_check.kids(False)
    reset_home()
    try:
        c = play_first([x for x in every if x.get("broadcastnow")] or every)
        if not r.ok(f"a channel plays ({c and c['channel']})", c is not None):
            return
        time.sleep(6)
        played("(next)", "up")
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


# DialogPVRChannelsOSD.xml: the channel list (Kodi's id), the chip slots (one per group of pvr://channels/tv/, in
# Kodi's order; the empty ones, All channels and Kids hidden), the "Player controls" button, the card's programme lines
LIST = 11
CHIPS = range(71, 87)
PLAYER_CONTROLS = 90
CARD_PROGRAMME = 301


def open_list():
    """The remote's OK while a channel plays (the keymap's "OSD" action) opens the channel list, loaded."""
    rpc("Input.ExecuteAction", {"action": "osd"})
    return wait_for(lambda: cond("Window.IsActive(pvrosdchannels)") and not cond("Window.IsActive(videoosd)")
                    and cond(f"Integer.IsGreater(Container({LIST}).NumItems,0)"), 5)


def row():
    return labs([f"Container({LIST}).ListItem.Label", f"Container({LIST}).ListItem.ChannelNumberLabel",
                 f"Container({LIST}).ListItem.Title"])


def focused_row(name, timeout=3):
    return wait_for(lambda: cond(f"Control.HasFocus({LIST})") and lab(f"Container({LIST}).ListItem.Label") == name, timeout)


def chip_groups():
    """The groups the chips stand for: Kodi's order, the non-empty ones other than All channels and Kids."""
    labels = [g["label"] for g in rpc("PVR.GetChannelGroups", {"channeltype": "tv"})["result"]["channelgroups"]]
    return [g for g in labels if g not in (all_label(), "Kids") and group_channels(g)]


def shown_chips():
    return [(i, lab(f"Control.GetLabel({i})")) for i in CHIPS if cond(f"Control.IsVisible({i})")]


def to_player_controls():
    """From the list: ← to the chip row, then → to its end, "Player controls"; OK there."""
    rpc("Input.Left")
    if not focus_id(PLAYER_CONTROLS, ["Right"], len(CHIPS) + 2):
        return False
    rpc("Input.Select")
    return wait_for(lambda: cond("Window.IsActive(videoosd)") and not cond("Window.IsActive(pvrosdchannels)"), 5)


@check("osd_list")
def c_osd_list(r):
    """OK while a channel plays opens the channel list (not the player controls): every channel, the playing one
    focused and marked, the header "Channels"."""
    if not free(r):
        return
    every = group_channels(all_label())
    kids_check.kids(False)
    reset_home()
    try:
        c = play_first([x for x in every if x.get("broadcastnow")] or every)
        if not r.ok(f"a channel plays ({c and c['channel']})", c is not None):
            return
        r.ok("OK opens the channel list", open_list(), lab("System.CurrentWindow"))
        n = len(group_channels(all_label()))
        l = labs([f"Container({LIST}).NumItems", "Control.GetLabel(2)"])
        r.ok(f"every channel ({n})", l[f"Container({LIST}).NumItems"] == str(n), l)
        r.ok("header: Channels", l["Control.GetLabel(2)"] == "Channels", l)
        r.ok("the playing channel focused and marked", focused_row(c["channel"]) and cond(f"Container({LIST}).ListItem.IsPlaying"), row())
        r.ok("its programme on the card", cond(f"Control.IsVisible({CARD_PROGRAMME})"))
        time.sleep(1)
        shot("live-list")
        rpc("Input.Back")
        r.ok("Back: the picture again", wait_for(lambda: cond("Window.IsActive(fullscreenvideo)") and not cond("Window.IsActive(pvrosdchannels)")
                                                 and lab("VideoPlayer.ChannelName") == c["channel"], 3))
    finally:
        stop()
        reset_home()


@check("list_play")
def c_list_play(r):
    """↓ then OK in the list plays the next channel, through All channels, and the list closes."""
    if not free(r):
        return
    every = group_channels(all_label())
    kids_check.kids(False)
    reset_home()
    try:
        pair = play_pair(list(zip(every, every[1:])))
        if not r.ok(f"a channel plays ({pair and pair[0]['channel']})", pair is not None):
            return
        a, b = pair
        if not r.ok("the list opens", open_list()):
            return
        rpc("Input.Down")
        if not r.ok(f"↓ → {b['channel']}", focused_row(b["channel"]), row()):
            return
        played(b["channel"], "list OK")
        rpc("Input.Select")
        r.ok(f"OK plays {b['channel']}", playing(b["channel"]), lab("VideoPlayer.ChannelName"))
        r.ok("through All channels", lab("VideoPlayer.ChannelGroup") == all_label(), lab("VideoPlayer.ChannelGroup"))
    finally:
        stop()
        reset_home()


@check("list_noguide")
def c_list_noguide(r):
    """A channel without guide data: its row and the card show no programme (no title, no progress)."""
    if not free(r):
        return
    c = noguide_channel()
    if not c:
        r.skip("every channel has guide data")
        return
    kids_check.kids(False)
    reset_home()
    try:
        if not r.ok(f"{c['channel']} plays", play_in(all_label(), c)):
            return
        if not r.ok("the list opens", open_list()):
            return
        r.ok(f"focused on {c['channel']}", focused_row(c["channel"]), row())
        r.ok("no programme in the row", row()[f"Container({LIST}).ListItem.Title"] == "" and not cond(f"Container({LIST}).ListItem.HasEpg"), row())
        r.ok("no programme lines on the card", not cond(f"Control.IsVisible({CARD_PROGRAMME})"))
        time.sleep(1)
        shot("live-list-noguide")
    finally:
        stop()
        reset_home()


@check("list_chips")
def c_list_chips(r):
    """The chips are the non-empty groups other than All channels and Kids, in Kodi's order; OK on one focuses its
    group's first channel in the list."""
    if not free(r):
        return
    want = chip_groups()
    kids_check.kids(False)
    reset_home()
    try:
        c = play_first(group_channels(all_label()))
        if not r.ok(f"a channel plays ({c and c['channel']})", c is not None):
            return
        if not r.ok("the list opens", open_list()):
            return
        time.sleep(1)  # the groups' own lists load after the dialog
        shown = shown_chips()
        r.ok(f"chips: {want}", [label for _, label in shown] == want, shown)
        for i, label in shown:
            first = group_channels(label)[0]["channel"] if label in want else ""
            rpc("Input.Left")  # lands on the chip focused last (the dialog keeps it), so the way on is either side
            if not r.ok(f"← then ←/→ to the {label} chip", focus_id(i, ["Right"], len(CHIPS)) or focus_id(i, ["Left"], len(CHIPS) + 2),
                        lab("System.CurrentControlId")):
                return
            rpc("Input.Select")
            r.ok(f"{label}: OK focuses {first}", focused_row(first), row())
    finally:
        stop()
        reset_home()


@check("list_info")
def c_list_info(r):
    """→ on a row opens programme info for that row's channel; Back returns to the list."""
    if not free(r):
        return
    pairs = guide_pairs()
    if not pairs:
        r.skip("no two neighbouring channels with guide data")
        return
    kids_check.kids(False)
    reset_home()
    try:
        pair = play_pair(pairs)
        if not r.ok(f"a channel with guide data next to another plays ({pair and pair[0]['channel']})", pair is not None):
            return
        a, b = pair
        if not r.ok("the list opens", open_list()):
            return
        rpc("Input.Down")
        if not r.ok(f"↓ → {b['channel']}", focused_row(b["channel"]), row()):
            return
        rpc("Input.Right")
        r.ok("→ opens programme info", wait_for(lambda: cond("Window.IsActive(pvrguideinfo)"), 4), lab("System.CurrentWindow"))
        l = labs(["ListItem.ChannelName", "ListItem.Title"])
        r.ok(f"for {b['channel']}: {now_next(b)[0]}", l["ListItem.ChannelName"] == b["channel"] and l["ListItem.Title"] == now_next(b)[0], l)
        rpc("Input.Back")
        r.ok("Back: the list again", wait_for(lambda: cond("Window.IsActive(pvrosdchannels)") and not cond("Window.IsActive(pvrguideinfo)"), 3))
    finally:
        stop()
        reset_home()


@check("player_controls")
def c_player_controls(r):
    """"Player controls" in the list opens Kodi's player controls (sleep timer etc.); Back returns to the picture, and
    OK opens the list again."""
    if not free(r):
        return
    every = group_channels(all_label())
    kids_check.kids(False)
    reset_home()
    try:
        c = play_first([x for x in every if x.get("broadcastnow")] or every)
        if not r.ok(f"a channel plays ({c and c['channel']})", c is not None):
            return
        if not r.ok("the list opens", open_list()):
            return
        r.ok("Player controls opens them", to_player_controls(), lab("System.CurrentWindow"))
        rpc("Input.Back")
        # Kodi's OK toggles the OSD: while it fades out, OK would close it, not open the list
        r.ok("Back: the picture", wait_for(lambda: cond("Window.IsActive(fullscreenvideo)") and not cond("Window.IsVisible(videoosd)")
                                           and not cond("Window.IsActive(pvrosdchannels)") and cond("Player.Playing"), 4),
             lab("System.CurrentWindow"))
        r.ok("OK: the list again", open_list(), lab("System.CurrentWindow"))
    finally:
        stop()
        reset_home()


# programme info (DialogFullScreenInfo.xml live, DialogPVRInfo.xml; include LumenLiveProgramme): the programme part,
# the part for a channel without guide data, title, description, next, times; the live dialog's buttons
INFO_PROGRAMME, INFO_NOGUIDE = 1201, 1202
INFO_TITLE, INFO_PLOT, INFO_NEXT, INFO_TIMES, INFO_PROGRESS = 1211, 1212, 1213, 1214, 1215
INFO_CHANNELS, INFO_GUIDE, INFO_CONTROLS = 1231, 1232, 1233


def open_info():
    """The remote's Info while a channel plays: programme info for it."""
    rpc("Input.Info")
    return wait_for(lambda: cond("Window.IsActive(fullscreeninfo)"), 4)


def back_to_picture():
    """Back, then wait until every dialog over the picture has faded out: a key pressed while one fades goes to it."""
    rpc("Input.Back")
    return wait_for(lambda: cond("Window.IsActive(fullscreenvideo)") and not cond("System.HasActiveModalDialog")
                    and not any(cond(f"Window.IsVisible({w})") for w in ("fullscreeninfo", "videoosd", "pvrosdchannels", "pvrguideinfo")), 4)


@check("info")
def c_info(r):
    """Info while a channel plays: its programme now (title, times, description) and next, and the buttons."""
    if not free(r):
        return
    kids_check.kids(False)
    reset_home()
    try:
        c = play_first([x for x in group_channels(all_label()) if (x.get("broadcastnow") or {}).get("plot")])
        if not r.ok(f"a channel with a description on now plays ({c and c['channel']})", c is not None):
            return
        r.ok("Info opens programme info", open_info(), lab("System.CurrentWindow"))
        now = next(x for x in group_channels(all_label()) if x["channelid"] == c["channelid"])["broadcastnow"]
        nxt = now_next(c)[1]
        l = labs([f"Control.GetLabel({i})" for i in (INFO_TITLE, INFO_PLOT, INFO_NEXT, INFO_TIMES)])
        r.ok("the programme part shows", cond(f"Control.IsVisible({INFO_PROGRAMME})") and not cond(f"Control.IsVisible({INFO_NOGUIDE})"))
        r.ok(f"title: {now['title']}", l[f"Control.GetLabel({INFO_TITLE})"] == now["title"], l)
        r.ok("description", now["plot"][:40] in l[f"Control.GetLabel({INFO_PLOT})"], l)
        r.ok(f"next: {nxt}", nxt and nxt in l[f"Control.GetLabel({INFO_NEXT})"], l)
        r.ok("times, progress", l[f"Control.GetLabel({INFO_TIMES})"].count(":") >= 2 and cond(f"Control.IsVisible({INFO_PROGRESS})"), l)
        r.ok("Kodi's seek bar stays hidden", not cond("Window.IsVisible(seekbar)"))
        r.ok("buttons Channels, TV guide, Player controls; Channels focused",
             all(cond(f"Control.IsVisible({i})") for i in (INFO_CHANNELS, INFO_GUIDE, INFO_CONTROLS))
             and lab("System.CurrentControlId") == str(INFO_CHANNELS), lab("System.CurrentControlId"))
        time.sleep(1)
        shot("live-info")
        r.ok("Back: the picture", back_to_picture(), lab("System.CurrentWindow"))
    finally:
        stop()
        reset_home()


@check("info_noguide")
def c_info_noguide(r):
    """Info on a channel the guide has nothing for: number, name and logo, no programme lines."""
    if not free(r):
        return
    c = noguide_channel()
    if not c:
        r.skip("every channel has guide data")
        return
    kids_check.kids(False)
    reset_home()
    try:
        if not r.ok(f"{c['channel']} plays", play_in(all_label(), c)):
            return
        r.ok("Info opens programme info", open_info(), lab("System.CurrentWindow"))
        r.ok("no programme part, the channel's part instead", not cond(f"Control.IsVisible({INFO_PROGRAMME})")
             and cond(f"Control.IsVisible({INFO_NOGUIDE})"))
        time.sleep(1)
        shot("live-info-noguide")
        back_to_picture()
    finally:
        stop()
        reset_home()


@check("info_buttons")
def c_info_buttons(r):
    """The live info's buttons open their targets: Channels the channel list, TV guide the guide on every channel,
    Player controls Kodi's OSD. From the list, → then Switch on another channel plays it."""
    if not free(r):
        return
    pairs = guide_pairs()
    if not pairs:
        r.skip("no two neighbouring channels with guide data")
        return
    kids_check.kids(False)
    reset_home()
    try:
        pair = play_pair(pairs)
        if not r.ok(f"a channel with guide data next to another plays ({pair and pair[0]['channel']})", pair is not None):
            return
        a, b = pair
        for button, name, target in ((INFO_CHANNELS, "Channels", "pvrosdchannels"), (INFO_GUIDE, "TV guide", "tvguide"),
                                     (INFO_CONTROLS, "Player controls", "videoosd")):
            if not r.ok("Info opens programme info", open_info(), labs(["System.CurrentWindow", "System.CurrentControl", "System.HasActiveModalDialog"])):
                return
            if not r.ok(f"focus {name}", focus_id(button, ["Right"], 4), lab("System.CurrentControlId")):
                return
            rpc("Input.Select")
            r.ok(f"{name} opens {target}", wait_for(lambda: cond(f"Window.IsActive({target})") and not cond("Window.IsVisible(fullscreeninfo)"), 5),
                 lab("System.CurrentWindow"))
            if target == "tvguide":
                n = str(len(group_channels(all_label())))
                r.ok(f"the guide on every channel ({n})", wait_for(lambda: lab("Container.NumItems") == n, 5), lab("Container.NumItems"))
                rpc("Input.Back")  # from the grid: the chips first (MyPVRGuide.xml), then out
                r.ok("Back: the chips", wait_for(lambda: lab("System.CurrentControlId") in [str(i) for i in list(CHIPS) + [GUIDE_NOW]], 3),
                     lab("System.CurrentControlId"))
                rpc("Input.Back")
                r.ok("Back again: the picture", wait_for(lambda: cond("Window.IsActive(fullscreenvideo)"), 5), lab("System.CurrentWindow"))
                time.sleep(1)
            else:
                r.ok("Back: the picture", back_to_picture(), lab("System.CurrentWindow"))
        if not r.ok("the list opens", open_list()):
            return
        rpc("Input.Down")
        if not r.ok(f"↓ → {b['channel']}", focused_row(b["channel"]), row()):
            return
        rpc("Input.Right")
        if not r.ok("→ opens programme info", wait_for(lambda: cond("Window.IsActive(pvrguideinfo)"), 4)):
            return
        r.ok("Switch and Close there", cond("Control.IsVisible(5)") and cond("Control.IsVisible(7)") and cond(f"Control.IsVisible({INFO_PROGRAMME})"))
        r.ok("on now: LIVE and progress", cond(f"Control.IsVisible({INFO_PROGRESS})"))
        time.sleep(1)
        shot("live-guideinfo")
        if r.ok("Switch focused", lab("System.CurrentControlId") == "5", lab("System.CurrentControlId")):
            played(b["channel"], "guide info Switch")
            rpc("Input.Select")
            r.ok(f"Switch plays {b['channel']}", playing(b["channel"]), lab("VideoPlayer.ChannelName"))
    finally:
        stop()
        reset_home()


# Home's Live TV tab (Includes_Lumen.xml LumenTabBar) and the guide (MyPVRGuide.xml): its grid, the selected programme's
# title in the header, the Now chip (the group chips are the list's ids, CHIPS)
LIVE_TAB = 19
GUIDE, GUIDE_TITLE, GUIDE_SUB, GUIDE_NOW = 50, 1301, 1302, 87


def live_tab():
    """Home, up to the tab bar, along to Live TV, OK."""
    reset_home()
    for _ in range(5):
        if lab("System.CurrentControlId") in [str(i) for i in range(10, 21)]:
            break
        rpc("Input.Up")
        time.sleep(0.4)
    if not focus_id(LIVE_TAB, ["Right"], 12):
        return False
    rpc("Input.Select")
    return True


def open_guide_from_home():
    """Home's Live TV tab: the guide on every channel, loaded."""
    if not live_tab():
        return False
    n = str(len(group_channels(all_label())))
    return wait_for(lambda: cond("Window.IsActive(tvguide)") and lab("Container.NumItems") == n and lab("ListItem.ChannelName") != "", 10)


def on_now():
    """The selected guide entry is on now (ListItem.Progress is a number Kodi gives conditions, not labels)."""
    return cond("Integer.IsGreater(ListItem.Progress,0) + Integer.IsLess(ListItem.Progress,100)")


def selected():
    return {**labs(["ListItem.ChannelName", "ListItem.Title", f"Control.GetLabel({GUIDE_TITLE})"]), "on now": on_now()}


@check("home_tab")
def c_home_tab(r):
    """Home has a Live TV tab: it opens the guide on every channel."""
    if not free(r):
        return
    kids_check.kids(False)
    try:
        reset_home()
        r.ok("the Live TV tab shows", cond(f"Control.IsVisible({LIVE_TAB})") and lab(f"Control.GetLabel({LIVE_TAB})") == "Live TV",
             lab(f"Control.GetLabel({LIVE_TAB})"))
        r.ok("it opens the guide on every channel", open_guide_from_home(), labs(["System.CurrentWindow", "Container.NumItems"]))
    finally:
        reset_home()


@check("guide")
def c_guide(r):
    """The guide: every channel, the selected programme in the header as Kodi has it; the chips jump to their group's
    first channel, Now back to the programme on now."""
    if not free(r):
        return
    kids_check.kids(False)
    try:
        if not r.ok("the guide opens on every channel", open_guide_from_home(), labs(["System.CurrentWindow", "Container.NumItems"])):
            return
        time.sleep(1)
        s = selected()
        c = next((x for x in group_channels(all_label()) if x["channel"] == s["ListItem.ChannelName"]), None)
        now = ((c or {}).get("broadcastnow") or {}).get("title", "")
        r.ok(f"selected: {s['ListItem.ChannelName']}, {now}", s["ListItem.Title"] == now and s[f"Control.GetLabel({GUIDE_TITLE})"] == now, s)
        shot("live-guide")
        want = chip_groups()
        rpc("Input.Back")  # from the grid, Back goes up to the chips
        r.ok("Back: the chips", wait_for(lambda: lab("System.CurrentControlId") in [str(i) for i in list(CHIPS) + [GUIDE_NOW]], 3),
             lab("System.CurrentControlId"))
        shown = shown_chips()
        r.ok(f"chips: Now, {want}", cond(f"Control.IsVisible({GUIDE_NOW})") and [label for _, label in shown] == want, shown)
        for i, label in reversed(shown):  # the first row is selected already: last chip first, so every jump moves
            first = group_channels(label)[0]["channel"] if label in want else ""
            if not r.ok(f"to the {label} chip", focus_id(i, ["Right"], len(CHIPS)) or focus_id(i, ["Left"], len(CHIPS) + 2),
                        lab("System.CurrentControlId")):
                return
            rpc("Input.Select")
            r.ok(f"{label}: OK selects {first}", wait_for(lambda: cond(f"Control.HasFocus({GUIDE})") and lab("ListItem.ChannelName") == first, 5),
                 selected())
            rpc("Input.Back")
            wait_for(lambda: not cond(f"Control.HasFocus({GUIDE})"), 3)
        rpc("Input.Down")  # back in the grid: a later programme, then Now
        time.sleep(0.5)
        for _ in range(3):
            rpc("Input.Right")
            time.sleep(0.5)
        rpc("Input.Back")
        if r.ok("to the Now chip", focus_id(GUIDE_NOW, ["Left"], len(CHIPS) + 2), lab("System.CurrentControlId")):
            rpc("Input.Select")
            r.ok("Now: the programme on now", wait_for(lambda: cond(f"Control.HasFocus({GUIDE})") and on_now(), 4), selected())
    finally:
        reset_home()


@check("guide_ok")
def c_guide_ok(r):
    """OK in the guide on a programme on now plays its channel, through All channels; on a later one Kodi's smart select
    asks whether to switch to the channel or set a reminder (Yes/No dialog: 11 Yes, 10 No)."""
    if not free(r):
        return
    kids_check.kids(False)
    try:
        name = None
        for k in range(3):  # the first channel whose stream opens (some relays are too slow at times)
            if not r.ok("the guide opens", open_guide_from_home()):
                return
            for _ in range(k):
                rpc("Input.Down")
                time.sleep(0.4)
            name = lab("ListItem.ChannelName")
            played(name, "guide OK")
            rpc("Input.Select")
            if playing(name):
                break
            stop()
            name = None
        if not r.ok(f"OK on now plays it ({name})", name is not None):
            return
        r.ok("through All channels", lab("VideoPlayer.ChannelGroup") == all_label(), lab("VideoPlayer.ChannelGroup"))
        stop()
        if not r.ok("the guide opens", open_guide_from_home()):
            return
        for _ in range(3):
            rpc("Input.Right")
            time.sleep(0.5)
        r.ok("→ a later programme", not on_now() and not cond("Integer.IsGreater(ListItem.Progress,99)"), selected())
        rpc("Input.Select")
        asked = wait_for(lambda: cond("Window.IsActive(yesnodialog)"), 4)
        buttons = labs(["Control.GetLabel(11)", "Control.GetLabel(10)"])
        r.ok("OK asks: Switch or Set reminder", asked and [buttons["Control.GetLabel(11)"], buttons["Control.GetLabel(10)"]] == ["Switch", "Set reminder"],
             {"window": lab("System.CurrentWindow"), **buttons})
        shot("live-guide-later")
        r.ok("nothing plays", not rpc("Player.GetActivePlayers")["result"])
        rpc("Input.Back")
        r.ok("Back: the guide again, nothing plays", wait_for(lambda: not cond("Window.IsVisible(yesnodialog)"), 3)
             and cond("Window.IsActive(tvguide)") and not rpc("Player.GetActivePlayers")["result"], lab("System.CurrentWindow"))
    finally:
        stop()
        reset_home()


@check("guide_focus")
def c_guide_focus(r):
    """The guide opens on the first channel when nothing plays, on the playing channel when one does."""
    if not free(r):
        return
    every = group_channels(all_label())
    kids_check.kids(False)
    try:
        r.ok("nothing playing: the guide opens", open_guide_from_home())
        r.ok(f"on the first channel ({every[0]['channel']})", lab("ListItem.ChannelName") == every[0]["channel"], selected())
        reset_home()
        c = play_first([x for x in every[3:] if x.get("broadcastnow")])
        if not r.ok(f"a channel plays ({c and c['channel']})", c is not None):
            return
        if not r.ok("Info opens programme info", open_info()):
            return
        if not r.ok("focus TV guide", focus_id(INFO_GUIDE, ["Right"], 4)):
            return
        rpc("Input.Select")
        r.ok("TV guide opens it", wait_for(lambda: cond("Window.IsActive(tvguide)") and lab("Container.NumItems") == str(len(every)), 8))
        r.ok(f"on the playing channel ({c['channel']})", wait_for(lambda: lab("ListItem.ChannelName") == c["channel"], 3), selected())
    finally:
        stop()
        reset_home()


@check("guide_noguide")
def c_guide_noguide(r):
    """The guide on a channel without guide data: the header gives its number and name only (no title, no times),
    its logo stands in for the picture, and its row has no empty boxes (screenshot)."""
    if not free(r):
        return
    c = noguide_channel()
    if not c:
        r.skip("every channel has guide data")
        return
    every = group_channels(all_label())
    i = next(k for k, x in enumerate(every) if x["channelid"] == c["channelid"])
    kids_check.kids(False)
    try:
        if not r.ok("the guide opens", open_guide_from_home()):
            return
        for _ in range(i):
            rpc("Input.Down")
            time.sleep(0.15)
        if not r.ok(f"on {c['channel']}", wait_for(lambda: lab("ListItem.ChannelName") == c["channel"], 5), selected()):
            return
        time.sleep(1)
        l = labs([f"Control.GetLabel({GUIDE_TITLE})", f"Control.GetLabel({GUIDE_SUB})"])
        want = {f"Control.GetLabel({GUIDE_TITLE})": "", f"Control.GetLabel({GUIDE_SUB})": f"{c['channelnumber']}  ·  {c['channel']}"}
        r.ok("header: number and name only", l == want, l)
        shot("live-guide-noguide")
    finally:
        reset_home()


@check("guide_fps")
def c_guide_fps(r):
    """Scrolling down the guide on this box: an average of at least 28 fps (31.3 with the old Estuary layout, less 10 %)."""
    if not free(r):
        return
    kids_check.kids(False)
    try:
        if not r.ok("the guide opens", open_guide_from_home()):
            return
        time.sleep(3)
        out = subprocess.run([os.path.join(os.path.dirname(os.path.abspath(__file__)), "navfps.sh"), "Down", "20"],
                             capture_output=True, text=True, timeout=60, env={**os.environ, "KODI_HOST": HOST}).stdout
        avg = float(out.rsplit("avg", 1)[-1]) if "avg" in out else 0.0
        r.ok(f"Down ×20: {avg} fps ≥ 28", avg >= 28, out.strip())
    finally:
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
        r.ok("player controls open (OK, then Player controls in the list)", open_list() and to_player_controls()
             and wait_for(lambda: cond("Window.IsVisible(seekbar)"), 4))
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
        played(c["channel"], "channelid, kids")
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
    """Kids mode: the Live TV windows send back to Home, TV channels too unless it shows the Kids group (Home's Live TV
    tab opens it so); normal mode opens them."""
    if not free(r):
        return
    try:
        kids_check.kids(True)
        for w in WINDOWS:
            reset_home()
            # TV channels on every channel: Kodi would otherwise open the group it showed last, maybe Kids
            rpc("GUI.ActivateWindow", {"window": w, "parameters": [group_path(all_label())]} if w == "tvchannels" else {"window": w})
            time.sleep(0.5)
            r.ok(f"kids: {w} sends back to Home",
                 wait_for(lambda: cond("Window.IsActive(home)") and not cond(f"Window.IsActive({w})"), 3),
                 lab("System.CurrentWindow"))
        if group_channels("Kids"):
            reset_home()
            rpc("GUI.ActivateWindow", {"window": "tvchannels", "parameters": [group_path("Kids")]})
            time.sleep(1.5)
            r.ok("kids: TV channels on the Kids group stays", cond("Window.IsActive(tvchannels)"), lab("System.CurrentWindow"))
        kids_check.kids(False)
        reset_home()
        rpc("GUI.ActivateWindow", {"window": "tvchannels"})
        r.ok("normal: TV channels open", wait_for(lambda: cond("Window.IsActive(tvchannels)"), 5), lab("System.CurrentWindow"))
    finally:
        kids_check.kids(False)
        reset_home()


def kids_playing(label):
    """Kids mode on, a kids channel playing through that group: the first of up to three whose stream opens, or None."""
    for c in group_channels("Kids")[:3]:
        if play_in(label, c):
            return c
        stop()
    return None


def stops_soon(name, how, path):
    """Opens the path and watches: True when Kodi counted it as live TV and playback then ended within 3 s, and stayed
    ended 6 s more. A stream that never opened proves nothing (the relays fail at times): False, saying so."""
    played(name, how)
    rpc("Player.Open", {"item": {"file": path}})
    t0, seen = time.time(), None
    time.sleep(1)  # right after Player.Open the player may not exist yet
    while time.time() - t0 < 45:
        if cond("PVR.IsPlayingTV") and seen is None:
            seen = time.time()
        if not rpc("Player.GetActivePlayers")["result"] and not cond("Player.HasMedia"):
            break
        time.sleep(0.1)
    else:
        return False, "still playing after 45 s"
    if seen is None:
        return False, "it never counted as live TV: the stream didn't open, so nothing was stopped (run again)"
    took = time.time() - seen
    time.sleep(6)
    stayed = not rpc("Player.GetActivePlayers")["result"]
    return took <= 3 and stayed, f"{took:.1f} s after it counted as live TV, stayed stopped: {stayed}"


@check("kids_group_plays")
def c_kids_group_plays(r):
    """Kids mode: a kids channel played through the Kids group plays, and is still playing 10 s later."""
    if not free(r):
        return
    try:
        kids_check.kids(True)
        reset_home()
        c = kids_playing("Kids")
        if not r.ok(f"kids: {c and c['channel']} plays through Kids", c is not None):
            return
        time.sleep(10)
        l = labs(["VideoPlayer.ChannelName", "VideoPlayer.ChannelGroup"])
        r.ok("and still plays 10 s later, in Kids", cond("Player.Playing") and l == {"VideoPlayer.ChannelName": c["channel"], "VideoPlayer.ChannelGroup": "Kids"}, l)
    finally:
        stop()
        kids_check.kids(False)
        reset_home()


@check("kids_other_stops")
def c_kids_other_stops(r):
    """Kids mode: a channel that isn't a kids one, through All channels, stops within 3 s and stays stopped."""
    if not free(r):
        return
    kids = {c["channelid"] for c in group_channels("Kids")}
    c = live_channel()  # one whose stream opens, in normal mode
    if not r.ok("a channel that plays in normal mode", c is not None and c["channelid"] not in kids):
        return
    c = next(x for x in group_channels(all_label()) if x["channelid"] == c["channelid"])
    try:
        kids_check.kids(True)
        reset_home()
        ok, how = stops_soon(c["channel"], "All channels, kids", channel_path(all_label(), c))
        r.ok(f"kids: {c['channel']} stops", ok, how)
    finally:
        stop()
        kids_check.kids(False)
        reset_home()


@check("kids_kidschannel_via_all")
def c_kids_kidschannel_via_all(r):
    """Kids mode: a kids channel played through All channels stops too (only the Kids group keeps the channel keys on
    kids channels)."""
    if not free(r):
        return
    kids_check.kids(False)
    reset_home()
    c = None
    for x in group_channels("Kids")[:3]:  # one whose stream opens
        ok = play_in(all_label(), x)
        stop()
        if ok:
            c = x
            break
    if not r.ok(f"a kids channel that plays in normal mode ({c and c['channel']})", c is not None):
        return
    try:
        kids_check.kids(True)
        reset_home()
        ok, how = stops_soon(c["channel"], "All channels, kids", channel_path(all_label(), c))
        r.ok(f"kids: {c['channel']} through All stops", ok, how)
    finally:
        stop()
        kids_check.kids(False)
        reset_home()


@check("kids_zap_wrap")
def c_kids_zap_wrap(r):
    """Kids mode: the channel keys go round the Kids group (CH+ on its last channel gives its first, CH− back gives
    the last) and never leave it."""
    if not free(r):
        return
    kids = group_channels("Kids")
    if not r.ok("a Kids group with two channels or more", len(kids) > 1, len(kids)):
        return
    first, last = kids[0], kids[-1]
    try:
        kids_check.kids(True)
        reset_home()
        if not r.ok(f"{last['channel']} (last kids channel) plays", play_in("Kids", last)):
            return
        r.ok(f"CH+ → {first['channel']} (first)", zap_to("channelup", first["channel"]))
        r.ok("still Kids", lab("VideoPlayer.ChannelGroup") == "Kids", lab("VideoPlayer.ChannelGroup"))
        r.ok(f"CH− → {last['channel']} (last)", zap_to("channeldown", last["channel"]))
        time.sleep(4)
        r.ok("still playing, in Kids", rpc("Player.GetActivePlayers")["result"] != [] and lab("VideoPlayer.ChannelGroup") == "Kids",
             lab("VideoPlayer.ChannelGroup"))
    finally:
        stop()
        kids_check.kids(False)
        reset_home()


@check("kids_list")
def c_kids_list(r):
    """Kids mode: Home's Live TV tab opens TV channels on the Kids group (no chips); OK on a channel plays it through
    Kids; OK while it plays opens the channel list: "Kids channels", the Kids group, no chips, no Player controls."""
    if not free(r):
        return
    kids = group_channels("Kids")
    try:
        kids_check.kids(True)
        reset_home()
        r.ok("the Live TV tab shows", cond(f"Control.IsVisible({LIVE_TAB})"))
        c = None
        for k in range(3):  # down to the first channel whose stream opens
            if not r.ok("to the Live TV tab", live_tab(), lab("System.CurrentControlId")):
                return
            opened = wait_for(lambda: cond("Window.IsActive(tvchannels)") and lab("Container.NumItems") == str(len(kids)), 10)
            if k == 0:
                l = labs(["System.CurrentWindow", "Container.FolderPath", "Container.NumItems", "Window(home).Property(lumen.tv.kids)"])
                r.ok(f"TV channels on Kids ({len(kids)})", opened and l["Container.FolderPath"] == l["Window(home).Property(lumen.tv.kids)"], l)
                r.ok("no chips", not any(cond(f"Control.IsVisible({i})") for i in list(CHIPS) + [GUIDE_NOW]))
                shot("live-kids-channels")
            for _ in range(k):
                rpc("Input.Down")
                time.sleep(0.4)
            name = lab("ListItem.ChannelName")
            played(name, "kids list OK")
            rpc("Input.Select")
            if playing(name):
                c = name
                break
            stop()
        if not r.ok(f"OK plays it through Kids ({c})", c is not None and lab("VideoPlayer.ChannelGroup") == "Kids", lab("VideoPlayer.ChannelGroup")):
            return
        time.sleep(4)
        r.ok("and keeps playing", cond("Player.Playing") and lab("VideoPlayer.ChannelName") == c)
        if not r.ok("OK: the channel list", open_list()):
            return
        time.sleep(1)
        l = labs(["Control.GetLabel(2)", f"Container({LIST}).NumItems"])
        r.ok("Kids channels, the Kids group", l == {"Control.GetLabel(2)": "Kids channels", f"Container({LIST}).NumItems": str(len(kids))}, l)
        r.ok("no chips, no Player controls", not any(cond(f"Control.IsVisible({i})") for i in list(CHIPS) + [PLAYER_CONTROLS]))
        shot("live-kids-list")
        back_to_picture()
    finally:
        stop()
        kids_check.kids(False)
        reset_home()


@check("kids_no_guide")
def c_kids_no_guide(r):
    """Kids mode: the guide sends back to Home, on every channel and on Kids alike."""
    if not free(r):
        return
    try:
        kids_check.kids(True)
        for label in (all_label(), "Kids"):
            reset_home()
            rpc("GUI.ActivateWindow", {"window": "tvguide", "parameters": [group_path(label)]})
            time.sleep(0.5)
            r.ok(f"kids: the guide on {label} sends back to Home",
                 wait_for(lambda: cond("Window.IsActive(home)") and not cond("Window.IsActive(tvguide)"), 3), lab("System.CurrentWindow"))
    finally:
        kids_check.kids(False)
        reset_home()


@check("kids_info")
def c_kids_info(r):
    """Kids mode: Info on a kids channel opens its programme info with Channels only (no TV guide, no Player controls)."""
    if not free(r):
        return
    try:
        kids_check.kids(True)
        reset_home()
        c = kids_playing("Kids")
        if not r.ok(f"kids: {c and c['channel']} plays", c is not None):
            return
        time.sleep(2)
        if not r.ok("Info opens programme info", open_info()):
            return
        time.sleep(1)
        shown = {i: cond(f"Control.IsVisible({i})") for i in (INFO_CHANNELS, INFO_GUIDE, INFO_CONTROLS)}
        r.ok("Channels only", shown == {INFO_CHANNELS: True, INFO_GUIDE: False, INFO_CONTROLS: False}, shown)
        shot("live-kids-info")
        back_to_picture()
    finally:
        stop()
        kids_check.kids(False)
        reset_home()


@check("guide_after_kids")
def c_guide_after_kids(r):
    """After a kids session (Kodi then keeps Kids as its TV group), an adult gets every channel again: the guide from
    Home's tab, and the channel keys after a start from it. The same after a Kodi restart."""
    if not free(r):
        return
    every = group_channels(all_label())
    try:
        for when in ("after kids", "after kids and a restart"):
            kids_check.kids(True)
            reset_home()
            c = kids_playing("Kids")
            if not r.ok(f"a kids session: {c and c['channel']} through Kids", c is not None):
                return
            stop()
            kids_check.kids(False)
            if when.endswith("restart"):
                if not r.ok("Kodi restarts", restart_kodi()):
                    return
                # the tab shows once live TV has started (seconds after Home), never before every channel's path is known
                early, t0 = [], time.time()
                while time.time() - t0 < 60 and not cond(f"Control.IsVisible({LIVE_TAB})"):
                    time.sleep(0.1)
                shown = time.time() - t0
                if cond(f"Control.IsVisible({LIVE_TAB})") and lab("Window(home).Property(lumen.tv.all)") == "":
                    early.append(f"{shown:.1f} s")
                r.ok(f"the Live TV tab shows ({shown:.1f} s after Home), with the path it opens", shown < 60 and not early, early)
            if not r.ok(f"{when}: the guide on all {len(every)} channels", open_guide_from_home(),
                        labs(["System.CurrentWindow", "Container.NumItems"])):
                return
            # a start from the guide (the first channel whose stream opens), then CH+: the next of every channel
            name = None
            for k in range(3):
                if k and not open_guide_from_home():
                    break
                for _ in range(k):
                    rpc("Input.Down")
                    time.sleep(0.4)
                name = lab("ListItem.ChannelName")
                played(name, f"guide OK, {when}")
                rpc("Input.Select")
                if playing(name):
                    break
                stop()
                name = None
            if not r.ok(f"{when}: OK plays {name} through All channels", name is not None and lab("VideoPlayer.ChannelGroup") == all_label(),
                        lab("VideoPlayer.ChannelGroup")):
                return
            i = next(k for k, x in enumerate(every) if x["channel"] == name)
            nxt = every[(i + 1) % len(every)]["channel"]
            r.ok(f"{when}: CH+ → {nxt}, still every channel", zap_to("channelup", nxt) and lab("VideoPlayer.ChannelGroup") == all_label(),
                 labs(["VideoPlayer.ChannelName", "VideoPlayer.ChannelGroup"]))
            stop()
    finally:
        stop()
        kids_check.kids(False)
        reset_home()


CHECKS["cleanup"] = kids_check.CHECKS["cleanup"]


if __name__ == "__main__":
    sys.exit(kids_check.main(sys.argv[1:], CHECKS))
