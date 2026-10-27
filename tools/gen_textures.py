"""Generate Lumen's texture set.

Run with tools/.venv/bin/python tools/gen_textures.py. Everything is drawn at 4x and
downsampled so edges are anti-aliased. Most textures are white so XML can tint them
with colordiffuse; rounded rects are meant to be used as 9-slice (border = radius).
"""
import math
import os

from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "skin.lumen", "media")
S = 4  # supersampling factor
WHITE = (255, 255, 255)


def out(rel, img):
    path = os.path.join(ROOT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, optimize=True)


def down(img, w, h):
    return img.resize((w, h), Image.LANCZOS)


def rr(w, h, r, fill=(255, 255, 255, 255), stroke=None, stroke_w=0, inset=0):
    """Rounded rect, optional inner stroke. Sizes in final pixels."""
    img = Image.new("RGBA", (w * S, h * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    box = [inset * S, inset * S, (w - inset) * S - 1, (h - inset) * S - 1]
    if fill is not None:
        d.rounded_rectangle(box, radius=r * S, fill=fill)
    if stroke is not None and stroke_w:
        d.rounded_rectangle(box, radius=r * S, outline=stroke, width=int(stroke_w * S))
    return down(img, w, h)


def shadow(size, inset, r, blur, alpha):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([inset, inset, size - inset - 1, size - inset - 1], radius=r, fill=alpha)
    mask = mask.filter(ImageFilter.GaussianBlur(blur))
    img.putalpha(mask)
    return img


def smooth(t):
    return t * t * (3 - 2 * t)


def vgrad(w, h, curve=lambda t: t):
    """Alpha goes 0 (top) -> 255 (bottom)."""
    img = Image.new("RGBA", (w, h))
    px = img.load()
    for y in range(h):
        a = int(round(255 * curve(y / (h - 1))))
        for x in range(w):
            px[x, y] = (255, 255, 255, a)
    return img


def hgrad(w, h, curve=lambda t: t):
    """Alpha goes 255 (left) -> 0 (right)."""
    img = Image.new("RGBA", (w, h))
    px = img.load()
    for x in range(w):
        a = int(round(255 * curve(1 - x / (w - 1))))
        for y in range(h):
            px[x, y] = (255, 255, 255, a)
    return img


def hero_scrim(w=480, h=270):
    """Black scrim: strong on the left third and the bottom, clear top-right. Stretched full-screen."""
    img = Image.new("RGBA", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            fx = x / (w - 1)
            fy = y / (h - 1)
            left = smooth(max(0.0, 1 - fx / 0.62)) * 0.92
            bottom = smooth(max(0.0, (fy - 0.38) / 0.62)) * 0.97
            top = smooth(max(0.0, 1 - fy / 0.22)) * 0.45
            a = 1 - (1 - left) * (1 - bottom) * (1 - top)
            px[x, y] = (0, 0, 0, int(255 * min(1.0, a)))
    return img


def vignette(w=480, h=270, strength=0.75):
    img = Image.new("RGBA", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            dx = (x / (w - 1) - 0.5) * 2
            dy = (y / (h - 1) - 0.5) * 2
            dist = min(1.0, math.sqrt(dx * dx * 0.7 + dy * dy) / 1.25)
            px[x, y] = (0, 0, 0, int(255 * strength * smooth(dist)))
    return img


def spinner(size=128, width=10):
    big = size * S
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    steps = 240
    pad = width * S
    for i in range(steps):
        t = i / steps
        start = -90 + t * 300
        a = int(255 * (t ** 1.6))
        d.arc([pad, pad, big - pad, big - pad], start, start + 300 / steps + 1.5, fill=(255, 255, 255, a), width=width * S)
    # round head
    ang = math.radians(-90 + 300)
    rr_ = (big - 2 * pad) / 2
    cx, cy = big / 2 + rr_ * math.cos(ang), big / 2 + rr_ * math.sin(ang)
    hw = width * S / 2
    d.ellipse([cx - hw, cy - hw, cx + hw, cy + hw], fill=(255, 255, 255, 255))
    return down(img, size, size)


def poly_icon(size, points, rounded=0):
    big = size * S
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = [(x * big, y * big) for x, y in points]
    d.polygon(pts, fill=(255, 255, 255, 255))
    if rounded:
        d.line(pts + [pts[0]], fill=(255, 255, 255, 255), width=int(rounded * S), joint="curve")
    return down(img, size, size)


def star(size=64):
    pts = []
    for i in range(10):
        ang = math.radians(-90 + i * 36)
        rad = 0.48 if i % 2 == 0 else 0.2
        pts.append((0.5 + rad * math.cos(ang), 0.53 + rad * math.sin(ang)))
    return poly_icon(size, pts, rounded=3)


def check(size=64, stroke=7):
    big = size * S
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = [(0.2 * big, 0.52 * big), (0.42 * big, 0.73 * big), (0.8 * big, 0.3 * big)]
    d.line(pts, fill=WHITE + (255,), width=stroke * S, joint="curve")
    for p in (pts[0], pts[2]):
        r = stroke * S / 2
        d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=WHITE + (255,))
    return down(img, size, size)


def chevron(size=64, stroke=6, direction="right"):
    big = size * S
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if direction == "right":
        pts = [(0.38, 0.2), (0.66, 0.5), (0.38, 0.8)]
    elif direction == "left":
        pts = [(0.62, 0.2), (0.34, 0.5), (0.62, 0.8)]
    elif direction == "down":
        pts = [(0.2, 0.38), (0.5, 0.66), (0.8, 0.38)]
    else:
        pts = [(0.2, 0.62), (0.5, 0.34), (0.8, 0.62)]
    pts = [(x * big, y * big) for x, y in pts]
    d.line(pts, fill=WHITE + (255,), width=stroke * S, joint="curve")
    for p in (pts[0], pts[2]):
        r = stroke * S / 2
        d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=WHITE + (255,))
    return down(img, size, size)


def ring(size=64, stroke=5):
    return rr(size, size, size // 2, fill=None, stroke=WHITE + (255,), stroke_w=stroke, inset=1)


def search_icon(size=64, stroke=6):
    big = size * S
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c, r = 0.42 * big, 0.25 * big
    d.ellipse([c - r, c - r, c + r, c + r], outline=WHITE + (255,), width=stroke * S)
    a = c + r * 0.72
    d.line([(a, a), (0.84 * big, 0.84 * big)], fill=WHITE + (255,), width=stroke * S)
    e = 0.84 * big
    rr_ = stroke * S / 2
    d.ellipse([e - rr_, e - rr_, e + rr_, e + rr_], fill=WHITE + (255,))
    return down(img, size, size)


def power_icon(size=64, stroke=6):
    big = size * S
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy, r = 0.5 * big, 0.54 * big, 0.3 * big
    gap = 38  # degrees either side of 12 o'clock
    d.arc([cx - r, cy - r, cx + r, cy + r], start=-90 + gap, end=270 - gap, fill=WHITE + (255,), width=stroke * S)
    cap = stroke * S / 2
    rm = r - cap  # arc centreline radius
    for ang in (-90 + gap, 270 - gap):
        x, y = cx + rm * math.cos(math.radians(ang)), cy + rm * math.sin(math.radians(ang))
        d.ellipse([x - cap, y - cap, x + cap, y + cap], fill=WHITE + (255,))
    top, bottom = 0.12 * big, 0.5 * big
    d.line([(cx, top), (cx, bottom)], fill=WHITE + (255,), width=stroke * S)
    for y in (top, bottom):
        d.ellipse([cx - cap, y - cap, cx + cap, y + cap], fill=WHITE + (255,))
    return down(img, size, size)


def moon_icon(size=64):
    """Filled crescent (sleep timer): a disc with an offset disc cut out."""
    big = size * S
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c, r = 0.5 * big, 0.36 * big
    d.ellipse([c - r, c - r, c + r, c + r], fill=WHITE + (255,))
    ox, oy, r2 = c + 0.2 * big, c - 0.16 * big, 0.3 * big
    d.ellipse([ox - r2, oy - r2, ox + r2, oy + r2], fill=(0, 0, 0, 0))
    return down(img, size, size)


def lock_icon(size=64, stroke=6):
    """Padlock (leave kids mode): shackle arc over a filled body with a keyhole cut out."""
    big = size * S
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, r = 0.5 * big, 0.19 * big
    d.arc([cx - r, 0.12 * big, cx + r, 0.12 * big + 2 * r], start=180, end=360, fill=WHITE + (255,), width=stroke * S)
    for x in (cx - r + stroke * S / 2, cx + r - stroke * S / 2):
        d.line([(x, 0.12 * big + r), (x, 0.5 * big)], fill=WHITE + (255,), width=stroke * S)
    d.rounded_rectangle([0.2 * big, 0.44 * big, 0.8 * big, 0.88 * big], radius=0.08 * big, fill=WHITE + (255,))
    k = 0.055 * big
    d.ellipse([cx - k, 0.6 * big - k, cx + k, 0.6 * big + k], fill=(0, 0, 0, 0))
    d.rectangle([cx - k / 2.4, 0.6 * big, cx + k / 2.4, 0.74 * big], fill=(0, 0, 0, 0))
    return down(img, size, size)


def kids_icon(size=64, stroke=5):
    """Smiling face (enter kids mode)."""
    big = size * S
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c, r = 0.5 * big, 0.38 * big
    d.ellipse([c - r, c - r, c + r, c + r], outline=WHITE + (255,), width=stroke * S)
    e = 0.05 * big
    for x in (0.37 * big, 0.63 * big):
        d.ellipse([x - e, 0.4 * big - e, x + e, 0.4 * big + e], fill=WHITE + (255,))
    m = 0.2 * big
    d.arc([c - m, 0.42 * big - m, c + m, 0.42 * big + m], start=25, end=155, fill=WHITE + (255,), width=stroke * S)
    return down(img, size, size)


def audio_icon(size=64, stroke=5):
    """Speaker with two sound waves (next audio language)."""
    big = size * S
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pts = [(0.12, 0.38), (0.28, 0.38), (0.5, 0.2), (0.5, 0.8), (0.28, 0.62), (0.12, 0.62)]
    d.polygon([(x * big, y * big) for x, y in pts], fill=WHITE + (255,))
    for r in (0.16, 0.3):
        d.arc([0.5 * big - r * big, 0.5 * big - r * big, 0.5 * big + r * big, 0.5 * big + r * big],
              start=-45, end=45, fill=WHITE + (255,), width=stroke * S)
    return down(img, size, size)



def person_icon(size=256, stroke=11):
    """Head and shoulders outline (actor page without a photo); drawn large, it shows at 200 px."""
    big = size * S
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c, r = 0.354 * big, 0.167 * big
    d.ellipse([0.5 * big - r, c - r, 0.5 * big + r, c + r], outline=WHITE + (255,), width=stroke * S)
    d.arc([0.167 * big, 0.604 * big, 0.833 * big, 1.146 * big], start=180, end=360, fill=WHITE + (255,), width=stroke * S)
    for x in (0.167, 0.833):  # round caps where the shoulders end
        e = stroke * S / 2
        d.ellipse([x * big - e, 0.875 * big - e, x * big + e, 0.875 * big + e], fill=WHITE + (255,))
    return down(img, size, size)


def toggle(on, w=120, h=76):
    """iOS-style switch, drawn inside Estuary's radio button box (120x76)."""
    tw, th = 84, 48
    big = Image.new("RGBA", (w * S, h * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    x0 = (w - tw) // 2 * S
    y0 = (h - th) // 2 * S
    track = (255, 159, 10, 255) if on else (255, 255, 255, 60)
    d.rounded_rectangle([x0, y0, x0 + tw * S, y0 + th * S], radius=th * S // 2, fill=track)
    k = th - 8
    kx = x0 + ((tw - k - 4) if on else 4) * S
    ky = y0 + 4 * S
    d.ellipse([kx, ky, kx + k * S, ky + k * S], fill=(255, 255, 255, 255))
    return down(big, w, h)


def gloss(w, h, r):
    """Glass highlight: soft white sheen fading from the top edge, clipped to a rounded rect."""
    g = vgrad(w, h, lambda t: 0.0)
    px = g.load()
    for y in range(h):
        t = y / (h - 1)
        a = int(255 * 0.22 * max(0.0, 1 - t / 0.55) ** 1.8)
        for x in range(w):
            px[x, y] = (255, 255, 255, a)
    m = rr(w, h, r).split()[3]
    g.putalpha(Image.composite(g.split()[3], Image.new("L", (w, h), 0), m))
    return g


def ambient(w=480, h=270):
    """Opaque ambient backdrop for non-media screens: near-black with two soft colour glows (+ dither)."""
    import random
    rnd = random.Random(7)
    img = Image.new("RGB", (w, h))
    px = img.load()
    base = (7, 9, 13)
    glows = [((0.18, 0.15), 0.75, (34, 58, 110), 0.55), ((0.92, 0.95), 0.7, (120, 64, 22), 0.32)]
    for y in range(h):
        for x in range(w):
            r, g, b = base
            for (cx, cy), rad, col, k in glows:
                dx, dy = x / w - cx, (y / h - cy) * 0.6
                f = max(0.0, 1 - math.sqrt(dx * dx + dy * dy) / rad) ** 2 * k
                r += col[0] * f
                g += col[1] * f
                b += col[2] * f
            n = rnd.uniform(-1.2, 1.2)
            px[x, y] = (int(min(255, r + n)), int(min(255, g + n)), int(min(255, b + n)))
    return img


def branding():
    """Skin picker icon (256px) and add-on fanart for skin.lumen/resources."""
    from PIL import ImageFont
    res = os.path.join(ROOT, "..", "resources")
    font_path = os.path.join(ROOT, "..", "fonts", "InterDisplay-Bold.ttf")
    accent = (255, 159, 10)

    n = 256 * S
    bg = ambient(256, 256).resize((n, n), Image.LANCZOS).convert("RGBA")
    glow = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([n * 0.60, n * 0.20, n * 0.80, n * 0.40], fill=accent + (255,))
    bg.alpha_composite(glow.filter(ImageFilter.GaussianBlur(n * 0.06)))
    bg.alpha_composite(glow)
    d = ImageDraw.Draw(bg)
    d.text((n * 0.47, n * 0.54), "L", font=ImageFont.truetype(font_path, int(n * 0.62)), fill=WHITE + (255,), anchor="mm")
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, n - 1, n - 1], radius=int(n * 0.22), fill=255)
    bg.putalpha(mask)
    down(bg, 256, 256).save(os.path.join(res, "icon.png"), optimize=True)

    fan = ambient(480, 270).resize((1280, 720), Image.LANCZOS)
    d = ImageDraw.Draw(fan)
    d.text((640, 360), "Lumen", font=ImageFont.truetype(font_path, 150), fill=(245, 245, 247), anchor="mm")
    d.ellipse([862, 286, 890, 314], fill=accent)
    fan.save(os.path.join(res, "fanart.jpg"), quality=90)


def main():
    L = "lumen/"
    # --- 9-slice rounded rects (white; tint in XML) ---
    for r in (6, 10, 14, 20, 28):
        size = r * 2 + 8
        out(f"{L}rr{r}.png", rr(size, size, r))
        out(f"{L}rr{r}_stroke.png", rr(size, size, r, fill=None, stroke=WHITE + (255,), stroke_w=2))
        out(f"{L}rr{r}_stroke1.png", rr(size, size, r, fill=None, stroke=WHITE + (255,), stroke_w=1.25))
    out(f"{L}pill.png", rr(128, 64, 32))
    out(f"{L}pill_stroke.png", rr(128, 64, 32, fill=None, stroke=WHITE + (255,), stroke_w=2))
    out(f"{L}pill_s.png", rr(40, 20, 10))  # small badges / progress bars
    out(f"{L}dot.png", rr(32, 32, 16))
    out(f"{L}white.png", Image.new("RGBA", (8, 8), WHITE + (255,)))

    # --- shadows (black, alpha baked) ---
    out(f"{L}shadow_card.png", shadow(160, 40, 14, 16, 230))  # border 52
    out(f"{L}shadow_soft.png", shadow(256, 72, 24, 30, 200))  # border 96

    # --- masks for rounded artwork (diffuse stretches to control size) ---
    out(f"{L}mask_poster.png", rr(240, 360, 12))
    out(f"{L}mask_landscape.png", rr(448, 252, 14))
    out(f"{L}mask_landscape_l.png", rr(640, 360, 18))
    out(f"{L}mask_square.png", rr(256, 256, 16))
    out(f"{L}mask_circle.png", rr(256, 256, 128))
    out(f"{L}mask_banner.png", rr(758, 140, 14))
    # portrait (2:3) photo, circular crop at the top: avoids aspect-scaling, which also crops diffuse masks
    pm = Image.new("RGBA", (200 * S, 300 * S), (0, 0, 0, 0))
    ImageDraw.Draw(pm).ellipse([0, 6 * S, 200 * S - 1, 206 * S - 1], fill=(255, 255, 255, 255))
    out(f"{L}mask_person.png", down(pm, 200, 300))

    out(f"{L}gloss_poster.png", gloss(240, 360, 12))
    out(f"{L}gloss_landscape.png", gloss(448, 252, 14))

    # --- gradients / scrims ---
    out(f"{L}grad_v.png", vgrad(4, 256, smooth))
    out(f"{L}grad_v_lin.png", vgrad(4, 256))
    out(f"{L}grad_h.png", hgrad(256, 4, smooth))
    out(f"{L}hero_scrim.png", hero_scrim())
    out(f"{L}vignette.png", vignette())
    out(f"{L}ambient.png", ambient())

    # --- motion / icons ---
    out(f"{L}spinner.png", spinner())
    out(f"{L}icons/play.png", poly_icon(64, [(0.28, 0.16), (0.84, 0.5), (0.28, 0.84)], rounded=6))
    big = Image.new("RGBA", (64 * S, 64 * S), (0, 0, 0, 0))
    dd = ImageDraw.Draw(big)
    dd.rounded_rectangle([0.22 * 64 * S, 0.16 * 64 * S, 0.42 * 64 * S, 0.84 * 64 * S], radius=3 * S, fill=WHITE + (255,))
    dd.rounded_rectangle([0.58 * 64 * S, 0.16 * 64 * S, 0.78 * 64 * S, 0.84 * 64 * S], radius=3 * S, fill=WHITE + (255,))
    out(f"{L}icons/pause.png", down(big, 64, 64))
    out(f"{L}icons/star.png", star())
    out(f"{L}icons/check.png", check())
    out(f"{L}icons/chevron_right.png", chevron(direction="right"))
    out(f"{L}icons/chevron_left.png", chevron(direction="left"))
    out(f"{L}icons/chevron_down.png", chevron(direction="down"))
    out(f"{L}icons/chevron_up.png", chevron(direction="up"))
    out(f"{L}icons/ring.png", ring())
    out(f"{L}icons/search.png", search_icon())
    out(f"{L}icons/power.png", power_icon())
    out(f"{L}icons/moon.png", moon_icon())
    out(f"{L}icons/lock.png", lock_icon())
    out(f"{L}icons/kids.png", kids_icon())
    out(f"{L}icons/person.png", person_icon())
    out(f"{L}icons/audio.png", audio_icon())

    # --- Estuary overrides: restyle every legacy window through shared textures ---
    out("buttons/button-fo.png", rr(80, 80, 16, fill=(255, 255, 255, 62), stroke=(255, 255, 255, 150), stroke_w=2, inset=2))
    out("buttons/button-nofo.png", rr(80, 80, 16, fill=(255, 255, 255, 0)))
    out("buttons/button-alt-nofo.png", rr(80, 80, 16, fill=(255, 255, 255, 16), inset=2))
    out("buttons/dialogbutton-fo.png", rr(80, 80, 18, fill=(255, 255, 255, 255), inset=20))  # Estuary keeps a 20px transparent margin
    out("buttons/dialogbutton-nofo.png", rr(80, 80, 18, fill=(255, 255, 255, 26), inset=20))
    out("buttons/roundbutton-fo.png", rr(120, 120, 60, fill=(255, 255, 255, 70), stroke=(255, 255, 255, 170), stroke_w=2, inset=4))
    out("buttons/thumbnail_focused.png", rr(20, 20, 8, fill=None, stroke=WHITE + (255,), stroke_w=3))
    out("buttons/radio-button-on.png", toggle(True))
    out("buttons/radio-button-off.png", toggle(False))
    out("buttons/slider-back.png", rr(150, 28, 6, fill=(255, 255, 255, 60), inset=8))
    out("buttons/slider-nib.png", rr(28, 28, 14))
    out("lists/focus.png", rr(470, 77, 14, fill=(255, 255, 255, 58), stroke=(255, 255, 255, 140), stroke_w=2, inset=2))
    out("lists/panel.png", Image.new("RGBA", (40, 40), (14, 17, 24, 255)))
    out("dialogs/dialog-bg.png", rr(80, 80, 18, fill=(22, 26, 34, 250), stroke=(255, 255, 255, 34), stroke_w=1.25, inset=2))
    out("dialogs/dialog-bg-nobo.png", Image.new("RGBA", (40, 40), (20, 24, 32, 255)))
    out("overlays/shadow.png", shadow(80, 20, 10, 8, 200))
    infobar = vgrad(16, 512, lambda t: smooth(1 - t) * 0.85)
    infobar.putalpha(infobar.split()[3])
    out("frame/InfoBar.png", Image.merge("RGBA", (Image.new("L", infobar.size, 0),) * 3 + (infobar.split()[3],)))
    out("progress/texturebg_white.png", rr(30, 30, 4))
    out("progress/texturebg_alt_white.png", rr(30, 30, 4))
    out("progress/texturebg_border_white.png", rr(30, 30, 4, fill=(255, 255, 255, 46)))
    out("spinner.png", spinner())
    out("dialogs/extendedprogress/loading.png", spinner(160, 12))
    out("dialogs/extendedprogress/loading-back.png", rr(160, 160, 80, fill=None, stroke=(255, 255, 255, 40), stroke_w=12, inset=12))
    branding()
    print("textures written to", os.path.normpath(ROOT))


if __name__ == "__main__":
    main()
