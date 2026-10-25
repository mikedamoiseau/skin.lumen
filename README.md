# Lumen

**A cinematic, artwork-first skin for Kodi 21 Omega, inspired by Infuse on Apple TV, and fast on a Raspberry Pi.**

![Lumen home screen](docs/screenshots/home.jpg)

Most Kodi skins look like settings menus that happen to show posters. Lumen puts
the artwork first: full-screen backdrops, clearlogos instead of plain titles,
posters that lift when focused, frosted-glass dialogs and a player overlay that
stays out of the way. It is built on Estuary, Kodi's default skin, so every
window still works. The screens you use every day were redesigned from scratch,
and the rest pick up Lumen's colours, fonts and textures.

It was designed and tuned on a Raspberry Pi 4, so it stays smooth on modest
hardware (see [Performance](#performance)).

## Screenshots

| | |
|---|---|
| ![Movie detail page](docs/screenshots/detail.jpg) | ![Movies grid](docs/screenshots/movies.jpg) |
| **Detail page**: logo, metadata, tech info, cast | **Movies**: poster grid with live metadata |
| ![Seasons](docs/screenshots/seasons.jpg) | ![Episodes](docs/screenshots/episodes.jpg) |
| **Seasons**: showcase row over the show's backdrop | **Episodes**: thumbnail row with runtime and air date |
| ![Settings](docs/screenshots/settings.jpg) | ![Dialog](docs/screenshots/dialog.jpg) |
| **Settings**: glass tiles | **Dialogs**: rounded glass sheets, white focus |

## Features

- **Home** with a tab bar and rows that dock into place as you move down:
  Continue Watching, Up Next, Recently Added Movies, New Episodes, Discover,
  Top Rated, TV Shows and Collections. The hero above the rows shows the focused
  item's logo, year, genre, runtime, rating, quality badges and plot. Empty rows
  hide themselves.
- **Library views**: Posters (movies, shows, collections), Showcase (seasons,
  movies in a collection), Episodes (landscape thumbnails), Landscape grid and a
  List view with preview.
- **Detail page**: clearlogo, tagline, plot, Play/Resume with progress, Trailer,
  Versions, Extras, Collection, video/audio/subtitle details and a cast row with
  round portraits. For collections the row lists the movies in the set.
- **Player**: thin seek bar with elapsed/remaining time, "Ends at", resolution,
  HDR, audio codec and channel badges, plus round transport buttons in the OSD.
- **Everything else**: settings, select/confirm/progress/keyboard dialogs,
  notifications, a background-task pill for library scans, Favourites and the
  skin settings page all share the same glass look.
- **Typography**: [Inter](https://rsms.me/inter/) throughout.

## Requirements

- Kodi 21 Omega (tested on 21.3)
- 16:9 display; the skin is laid out at 1920×1080
- A scraped video library with artwork. Clearlogos and landscape art (for
  example from fanart.tv) make it look its best.

## Install

1. Download `skin.lumen-<version>.zip` from the
   [latest release](https://github.com/mikedamoiseau/skin.lumen/releases/latest).
2. In Kodi: *Settings → Add-ons → Install from zip file* and pick the zip.
   You may need to allow *Unknown sources* first (*Settings → System → Add-ons*).
3. *Settings → Interface → Skin* → choose **Lumen**.

To build the zip yourself from a checkout:

```sh
git clone https://github.com/mikedamoiseau/skin.lumen.git
cd skin.lumen
git archive --format=zip -o skin.lumen.zip HEAD skin.lumen
```

### Recommended Kodi settings

Lumen is designed around these three settings. Kodi's defaults work, but feel less like Infuse.

| Setting | Value | Effect |
|---|---|---|
| Media → Videos → Default select action | **Show information** | Selecting a movie opens its detail page instead of starting playback |
| Media → Videos → Select first unwatched TV show season/episode | **On first entry** | Opening a show lands on the next episode to watch |
| Media → General → Show "parent folder" items | **Off** | No ".." tile in front of every grid |

### Skin settings

*Settings → Interface → Skin → Configure skin*

- **Animated backdrops** (off by default) slowly zoom the backdrop on Home,
  library and detail pages. It looks great, but the whole screen has to redraw
  every frame. On a Raspberry Pi 4 that roughly halves the menu frame rate, so
  it ships off.

## Performance

Measured on a Raspberry Pi 4 (LibreELEC 12.2, Kodi 21.3, 1080p50):

| Screen | Lumen | Estuary (same box) |
|---|---|---|
| Home, idle | no GPU work at all (nothing redraws) | – |
| Home, scrolling a row | ~39 fps | – |
| Movie grid, scrolling | ~29–35 fps | ~27–29 fps (InfoWall) |

Kodi redraws the whole screen whenever anything on it moves, so on a Pi the GPU
is the limit. Lumen keeps it light in three ways:

- Home only lays out the row you can see. The other rows are culled.
- Nothing animates continuously unless you turn on animated backdrops.
- There is no real-time blur. The "glass" is pre-rendered translucent textures.

## Development

```
skin.lumen/                  the skin; this folder is what gets installed
  xml/Includes_Lumen.xml     Lumen components: cards, hero, meta line, badges, buttons, player parts
  xml/Views_Lumen.xml        library views 500 Posters, 501 Showcase, 502 Episodes, 503 Landscape, 50 List
  xml/Home.xml               generated by tools/build_home.py, edit the script
  xml/Includes_LumenHome.xml generated by tools/build_home.py
  media/lumen/               generated by tools/gen_textures.py
  playlists/lumen_*.xsp      smart playlists behind the Discover and Top Rated rows
tools/
  gen_textures.py            draws every Lumen texture, the Estuary texture overrides, icon and fanart
  build_home.py              Home rows (the ROWS list) and the row-docking animations
  deploy.sh                  lint all XML, copy the skin to a Kodi box over SSH, reload the skin
  shot.sh NAME               take a screenshot on the box and copy it locally
  rpc.sh                     JSON-RPC helper
  fps.sh, navfps.sh, gpu.sh  frame-rate and GPU load sampling (gpu.sh is Raspberry Pi specific)
  log.sh                     recent warnings and errors from kodi.log
```

Regenerate textures and Home after changing a generator:

```sh
python3 -m venv tools/.venv && tools/.venv/bin/pip install -r tools/requirements.txt
tools/.venv/bin/python tools/gen_textures.py
python3 tools/build_home.py
```

The helper scripts need SSH access to the Kodi box (LibreELEC: root with a key)
and JSON-RPC enabled (*Settings → Services → Control → Allow remote control via HTTP*).
Point them at your box with environment variables:

```sh
export KODI_SSH=root@<kodi-ip>        # deploy.sh, shot.sh, log.sh, gpu.sh
export KODI_HOST=<kodi-ip>:8080       # rpc.sh, fps.sh, navfps.sh
tools/deploy.sh                       # refuses while something is playing; --force to override
```

`deploy.sh` replaces `/storage/.kodi/addons/skin.lumen` on the box (the LibreELEC
path) and runs `ReloadSkin()`.

## Known limitations

- Music, Pictures, Live TV/PVR and Games keep Estuary's layouts with Lumen's styling.
- Labels added by Lumen ("Collections", "Up Next", "Back", …) are English only.
- Kodi doesn't expose Dolby Atmos, so TrueHD and DD+ are shown instead.
- Add-on folders show no backdrop on purpose, because add-on fanart is usually a promo screenshot.
- Only 1080p/16:9 layouts. Kodi scales them for 4K output.

## Credits & license

- Built on [Estuary](https://github.com/xbmc/xbmc/tree/master/addons/skin.estuary) by phil65 and Team Kodi.
- [Inter](https://rsms.me/inter/) by Rasmus Andersson.
- Inspired by [Infuse](https://firecore.com/infuse). Lumen is not affiliated
  with Firecore or Apple.

Code is licensed under the [GNU GPL v2.0](LICENSE), like Estuary. Artwork
derived from Estuary is under CC BY-SA 4.0, and the fonts are under the SIL Open
Font License 1.1. See [`skin.lumen/LICENSE.txt`](skin.lumen/LICENSE.txt).
The movie and TV artwork in the screenshots comes from TMDB and fanart.tv and
belongs to its respective owners.
