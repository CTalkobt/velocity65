#!/usr/bin/env python3
"""Generate the game's art, palette and lookup tables.

Usage: gen_assets.py <out_dir>

Writes:
  <out_dir>/gen_config.h   #defines shared by the C sources and the assembly
  <out_dir>/tables.c       const tables (palette, sky, road profiles, sprites)
  <out_dir>/art_chip.bin   the road profiles (unpacked to bank 1)
  <out_dir>/art_attic.bin  panoramas, sprites, banners and the bonnet (unpacked
  <out_dir>/art_attic2.bin   to attic RAM, in three parts)
  <out_dir>/art_attic3.bin
  <out_dir>/art_track.bin  the circuit (unpacked to attic RAM)
  <out_dir>/preview.png    everything on one sheet, to look at on the PC

All of the art is drawn here with PIL at 4x and scaled down, so edges are
smooth; then each group is reduced to its share of the 256 colours.
Everything tunable about the look of the game lives here.
"""
import math
import os
import random
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# --- screen and camera -----------------------------------------------------
W, H = 320, 192          # the rendered image (24 FCM cell rows; row 25 is text)
CW, CH = 40, 24          # character cells
COLSTRIDE = CH * 64      # bytes per cell column (column-major cells)
BUF = [0x40000, 0x50000]  # the two pixel buffers
HZ = 76                  # the horizon row
FOCAL = 192              # pixels per unit at distance 1 (fov ~80 deg)

# --- world -----------------------------------------------------------------
SEG = 256                # segment length (world units)
RW = 720                 # half the road width
CAMH_COCKPIT = 330       # eye height above the road
CAMH_CHASE = 380
CHASE_BACK = 900         # chase camera: this far behind the car
DRAW = 152               # segments drawn
NEAR = 64                # nearest distance projected

# --- road profile: -PSPAN..PSPAN half-widths, 256 texels per half-width at
# mip 0, half as many per mip level ------------------------------------------
PSPAN = 1.5
NMIP = 8
FOG = [0, 0, 0, 0.06, 0.16, 0.28, 0.42, 0.56]   # haze per mip (= distance)

# --- palette groups ----------------------------------------------------------
P_SKY, N_SKY = 16, 48
P_ROAD, N_ROAD = 64, 64
P_SPR, N_SPR = 128, 128
# the road's colours in two groups by distance (mip level), so a look can
# light or fog the near road apart from the far: the headlights' pool by
# night, the fog
NEAR_MIPS = 3
N_ROADN = 36
P_ROADF, N_ROADF = P_ROAD + N_ROADN, N_ROAD - N_ROADN

# --- far memory --------------------------------------------------------------
CHIP_BASE = 0x12000      # bank 1, free when BASIC has no variables
CHIP_END = 0x1C000        # (the two screens follow)
ATTIC_BASE = 0x8000000
ATTIC_SPLIT = 0x38000     # each part of the attic art (a file each)
ATTIC_PARTS = 5           # (the last ends below TRK_ADDR)
LOWTAB = 0xCA0            # bank 0 tables that never change (render.s45)

# --- sky and panoramas -------------------------------------------------------
PANO_W = 512
PANO_FAR_H = 56
PANO_FAR_Y = HZ + 6 - PANO_FAR_H     # screen row of its top
PANO_NEAR_H = 22
PANO_NEAR_Y = HZ + 5 - PANO_NEAR_H
SKY_TOP = (34, 78, 178)
SKY_MID = (92, 150, 224)
HAZE = (200, 220, 236)

SS = 4                   # supersampling of the drawings


def lerp(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def clamp8(v):
    return max(0, min(255, int(round(v))))


def nib(v):
    v = clamp8(v)
    return ((v << 4) | (v >> 4)) & 0xFF


def carray(ctype, name, values, per_line=16):
    lines = []
    for i in range(0, len(values), per_line):
        lines.append("    " + ", ".join(str(v) for v in values[i:i + per_line]) + ",")
    return "const %s %s[%d] = {\n%s\n};\n" % (ctype, name, len(values), "\n".join(lines))


# =============================================================================
# palette reduction
# =============================================================================

def build_palette(images, ncol, base):
    """images: RGBA images (alpha < 128 is transparent). Returns the palette
    (list of rgb) and a function mapping an image to indices (0: transparent)."""
    pix = []
    for im in images:
        for r, g, b, a in im.getdata():
            if a >= 128:
                pix.append((r, g, b))
    strip = Image.new("RGB", (len(pix), 1))
    strip.putdata(pix)
    q = strip.quantize(colors=ncol, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    pal = q.getpalette()[:3 * ncol]
    pal = [tuple(pal[i:i + 3]) for i in range(0, len(pal), 3)]
    while len(pal) < ncol:
        pal.append((0, 0, 0))
    cache = {}

    def nearest(c):
        if c in cache:
            return cache[c]
        best, bd = 0, 1 << 30
        for i, p in enumerate(pal):
            d = 3 * (p[0] - c[0]) ** 2 + 4 * (p[1] - c[1]) ** 2 + 2 * (p[2] - c[2]) ** 2
            if d < bd:
                best, bd = i, d
        cache[c] = base + best
        return base + best

    def index(im):
        out = []
        for r, g, b, a in im.getdata():
            out.append(nearest((r, g, b)) if a >= 128 else 0)
        return out

    return pal, index, nearest


# =============================================================================
# drawing helpers (on a supersampled RGBA canvas)
# =============================================================================

def canvas(w, h):
    return Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))


def down(im, w, h):
    im = im.resize((w, h), Image.Resampling.LANCZOS)
    # hard alpha: the DMA has one transparent value
    px = im.load()
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a >= 120:
                k = 255.0 / max(a, 1)
                px[x, y] = (clamp8(r * k), clamp8(g * k), clamp8(b * k), 255)
            else:
                px[x, y] = (0, 0, 0, 0)
    return im


def shade(c, f):
    return tuple(clamp8(v * f) for v in c)


def S(*v):
    return [x * SS for x in v]


def poly(d, pts, fill):
    d.polygon([(x * SS, y * SS) for x, y in pts], fill=fill)


def ellipse(d, x0, y0, x1, y1, fill):
    d.ellipse(S(x0, y0, x1, y1), fill=fill)


def rect(d, x0, y0, x1, y1, fill):
    d.rectangle(S(x0, y0, x1, y1), fill=fill)


def font(size):
    for f in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default(size=size)


# =============================================================================
# sprites
# =============================================================================

def draw_pine(w, h, seed):
    rnd = random.Random(seed)
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    cx = w / 2
    rect(d, cx - 2.2, h - 14, cx + 2.2, h, (92, 60, 34, 255))
    rect(d, cx - 2.2, h - 14, cx - 0.6, h, (120, 82, 48, 255))
    tiers = 5
    for i in range(tiers):
        t = i / (tiers - 1)
        top = 2 + t * (h - 30)
        bot = top + 22 + t * 6
        half = 6 + t * (w / 2 - 7)
        dark = (22 + rnd.randint(0, 6), 74 + rnd.randint(0, 10), 40, 255)
        lite = (48 + rnd.randint(0, 8), 120 + rnd.randint(0, 12), 58, 255)
        poly(d, [(cx, top), (cx + half, bot), (cx - half, bot)], dark)
        poly(d, [(cx, top), (cx - half, bot), (cx - half * 0.15, bot)], lite)
        poly(d, [(cx - half, bot), (cx + half, bot), (cx + half * 0.8, bot + 2), (cx - half * 0.8, bot + 2)],
             shade(dark[:3], 0.7) + (255,))
    return down(im, w, h)


def draw_palm(w, h, seed):
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    rnd = random.Random(seed)
    # a gently curved trunk
    pts = []
    for i in range(21):
        t = i / 20
        x = w / 2 + math.sin(t * 2.2) * 5 - 2
        y = h - t * (h - 18)
        pts.append((x, y))
    for i in range(20):
        (x0, y0), (x1, y1) = pts[i], pts[i + 1]
        r = 2.6 - i * 0.05
        c = (140, 104, 64, 255) if i % 2 else (116, 84, 50, 255)
        poly(d, [(x0 - r, y0), (x0 + r, y0), (x1 + r, y1), (x1 - r, y1)], c)
    tx, ty = pts[-1]
    # fronds
    for k in range(9):
        a = math.pi * (k / 8.0) + rnd.uniform(-0.15, 0.15)
        length = 20 + rnd.uniform(-2, 3)
        droop = 9 + rnd.uniform(0, 4)
        seg = []
        for j in range(9):
            t = j / 8
            x = tx - math.cos(a) * length * t
            y = ty - math.sin(a) * length * t * 0.6 + droop * t * t
            seg.append((x, y))
        for j in range(8):
            (x0, y0), (x1, y1) = seg[j], seg[j + 1]
            wd = 3.2 * (1 - j / 9) + 0.6
            c = (40, 130, 52, 255) if k % 2 else (30, 104, 44, 255)
            poly(d, [(x0, y0 - wd), (x1, y1 - wd * 0.8), (x1, y1 + wd * 0.8), (x0, y0 + wd)], c)
    ellipse(d, tx - 3, ty - 2, tx + 3, ty + 4, (98, 70, 40, 255))
    return down(im, w, h)


def draw_oak(w, h, seed):
    rnd = random.Random(seed)
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    cx = w / 2
    poly(d, [(cx - 3, h), (cx + 3, h), (cx + 2, h - 26), (cx - 2, h - 26)], (88, 62, 40, 255))
    poly(d, [(cx - 3, h), (cx - 1, h), (cx - 1, h - 26), (cx - 2, h - 26)], (112, 80, 52, 255))
    blobs = []
    for _ in range(26):
        a = rnd.uniform(0, math.tau)
        r = rnd.uniform(0, 1) ** 0.6
        x = cx + math.cos(a) * r * (w / 2 - 9)
        y = (h - 40) * 0.55 + math.sin(a) * r * (h - 44) * 0.42
        s = rnd.uniform(6, 10)
        blobs.append((y, x, s))
    blobs.sort()
    for y, x, s in blobs:
        lit = max(0.0, min(1.0, 0.5 - (x - cx) / w - (y - h * 0.3) / h))
        base = lerp((28, 82, 30), (96, 160, 58), lit)
        ellipse(d, x - s, y - s, x + s, y + s, tuple(clamp8(v) for v in base) + (255,))
        ellipse(d, x - s * 0.7, y - s * 0.8, x + s * 0.3, y + s * 0.1,
                tuple(clamp8(v * 1.15) for v in base) + (255,))
    return down(im, w, h)


def draw_bush(w, h, seed):
    rnd = random.Random(seed)
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    for i in range(14):
        x = rnd.uniform(7, w - 7)
        s = rnd.uniform(5, 8)
        y = h - s - rnd.uniform(0, 5)
        c = lerp((30, 90, 34), (90, 150, 54), rnd.random())
        ellipse(d, x - s, y - s, x + s, y + s, tuple(clamp8(v) for v in c) + (255,))
    for i in range(6):
        x, y = rnd.uniform(6, w - 6), rnd.uniform(h * 0.3, h - 6)
        ellipse(d, x - 1.2, y - 1.2, x + 1.2, y + 1.2, (220, 200, 60, 255) if i % 2 else (230, 90, 120, 255))
    return down(im, w, h)


def draw_rock(w, h, seed):
    rnd = random.Random(seed)
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    pts = []
    for k in range(11):
        a = math.pi + math.pi * k / 10
        r = rnd.uniform(0.8, 1.0)
        pts.append((w / 2 + math.cos(a) * (w / 2 - 1) * r, h - 1 + math.sin(a) * (h - 2) * r))
    poly(d, pts, (118, 110, 100, 255))
    poly(d, [pts[0]] + pts[1:6] + [(w * 0.45, h - 1)], (150, 142, 128, 255))
    poly(d, [(w * 0.3, h * 0.45), (w * 0.5, h * 0.25), (w * 0.62, h * 0.5), (w * 0.4, h * 0.6)], (176, 168, 152, 255))
    rect(d, 1, h - 3, w - 2, h - 1, (84, 78, 70, 255))
    return down(im, w, h)


def draw_sign(w, h, direction):
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    for px in (w * 0.25, w * 0.75):
        rect(d, px - 1.2, h * 0.5, px + 1.2, h, (180, 180, 186, 255))
    rect(d, 1, 2, w - 2, h * 0.62, (30, 30, 34, 255))
    rect(d, 2, 3, w - 3, h * 0.62 - 1, (250, 206, 30, 255))
    ch = h * 0.62
    for k in range(3):
        x = 7 + k * (w - 14) / 3
        s = 1 if direction > 0 else -1
        if s < 0:
            x = w - x
        poly(d, [(x, 6), (x + 7 * s, ch / 2 + 1.5), (x, ch - 5), (x + 4 * s, ch - 5),
                 (x + 11 * s, ch / 2 + 1.5), (x + 4 * s, 6)], (24, 24, 28, 255))
    return down(im, w, h)


def draw_lamp(w, h, side):
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    px = 4 if side > 0 else w - 5
    rect(d, px - 2.5, 10, px + 2.5, h, (150, 154, 162, 255))
    rect(d, px - 2.5, 10, px - 1, h, (200, 204, 212, 255))
    rect(d, px - 3, h - 6, px + 3, h, (110, 112, 120, 255))
    ax = w - 3 if side > 0 else 2
    poly(d, [(px, 9), (ax, 6), (ax, 8), (px, 12)], (150, 154, 162, 255))
    rect(d, min(ax, ax - 7 * side), 7, max(ax, ax - 7 * side), 10, (90, 92, 100, 255))
    rect(d, min(ax, ax - 7 * side) + 1, 10, max(ax, ax - 7 * side) - 1, 11.5, (255, 246, 190, 255))
    return down(im, w, h)


def draw_billboard(w, h, text, colours):
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    for px in (w * 0.22, w * 0.78):
        rect(d, px - 1.5, h * 0.5, px + 1.5, h, (70, 70, 76, 255))
    bh = h * 0.68
    rect(d, 0, 0, w - 1, bh, (40, 40, 44, 255))
    bg, fg, stripe = colours
    rect(d, 1.5, 1.5, w - 2.5, bh - 1.5, bg)
    rect(d, 1.5, bh - 7, w - 2.5, bh - 4, stripe)
    f = font(int((bh - 12) * SS))
    tw = d.textlength(text, font=f)
    while tw > (w - 8) * SS:
        f = font(int(f.size * 0.9))
        tw = d.textlength(text, font=f)
    d.text(((w * SS - tw) / 2 + SS, 2.5 * SS + SS), text, font=f, fill=(0, 0, 0, 120))
    d.text(((w * SS - tw) / 2, 2.5 * SS), text, font=f, fill=fg)
    return down(im, w, h)


def draw_barrier(w, h):
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    for px in (3, w / 2, w - 4):
        rect(d, px - 1.2, h * 0.4, px + 1.2, h, (120, 120, 126, 255))
    for k in range(8):
        x0 = k * w / 8
        c = (226, 30, 34, 255) if k % 2 == 0 else (244, 244, 244, 255)
        poly(d, [(x0, 1), (x0 + w / 8, 1), (x0 + w / 8, h * 0.55), (x0, h * 0.55)], c)
    rect(d, 0, h * 0.55, w - 1, h * 0.55 + 1, (90, 20, 20, 255))
    return down(im, w, h)


def draw_gantry(w, h):
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    rect(d, 0, 0, 6, h, (190, 192, 200, 255))
    rect(d, w - 7, 0, w - 1, h, (190, 192, 200, 255))
    rect(d, 0, 0, 2, h, (230, 232, 240, 255))
    rect(d, w - 7, 0, w - 5, h, (230, 232, 240, 255))
    bh = 30
    rect(d, 6, 2, w - 7, bh, (24, 24, 30, 255))
    for k in range((w - 14) // 6):
        for r in range(2):
            x0 = 8 + k * 6
            y0 = 4 + r * 3
            if (k + r) % 2 == 0:
                rect(d, x0, y0, x0 + 5.6, y0 + 2.8, (240, 240, 240, 255))
    f = font(16 * SS)
    text = "START  FINISH"
    tw = d.textlength(text, font=f)
    d.text(((w * SS - tw) / 2, 11 * SS), text, font=f, fill=(255, 210, 40, 255))
    for k in range((w - 14) // 6):
        x0 = 8 + k * 6
        if k % 2 == 0:
            rect(d, x0, bh - 4, x0 + 5.6, bh - 1.5, (240, 240, 240, 255))
    # lights
    for k in range(5):
        x = w / 2 - 24 + k * 12
        ellipse(d, x - 3, bh + 2, x + 3, bh + 8, (60, 60, 64, 255))
    return down(im, w, h)


def vgrad(d, x0, y0, x1, y1, top, bottom):
    """A rectangle shaded from top to bottom (on the supersampled canvas)."""
    for y in range(int(y0 * SS), int(y1 * SS)):
        t = (y - y0 * SS) / max(1, (y1 - y0) * SS)
        d.line([(x0 * SS, y), (x1 * SS, y)], fill=tuple(clamp8(v) for v in lerp(top, bottom, t)) + (255,))


def tyre(d, x0, y0, x1, y1, wheel, fat=False):
    """A tyre seen from behind: rubber, a lit shoulder, tread grooves that
    move between the two wheel frames."""
    rr = (x1 - x0) * 0.3
    d.rounded_rectangle(S(x0, y0, x1, y1), radius=rr * SS, fill=(24, 24, 26, 255))
    d.rounded_rectangle(S(x0 + 0.8, y0 + 0.6, x0 + 2.4, y1 - 0.6), radius=SS, fill=(62, 62, 66, 255))
    step = 3.4 if fat else 2.4                  # fat tyres: bigger tread blocks
    off = (step / 2) * wheel
    y = y0 + 1.2 + off
    while y < y1 - 1:
        d.line(S(x0 + 2.6, y, x1 - 1.2, y), fill=(74, 74, 80, 255), width=max(1, SS // 2 + 1))
        y += step


# where the tyres are: the same for every body, so any body takes any tyres
AXLE_H = 12


def tyre_x(w, view, fat):
    tw = w * (0.18 if fat else 0.14)
    return [(tx - view * 1.5, tx + tw - view * 1.5) for tx in (w * 0.03, w * 0.97 - tw)]


def draw_axle(w, h, view, wheel, fat):
    """A car's shadow and tyres, the bottom AXLE_H rows of a car (drawn
    before its body, which covers their tops)."""
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    ellipse(d, 1, h - 6, w - 2, h - 0.5, (16, 18, 20, 255))
    for xa, xb in tyre_x(w, view, fat):
        tyre(d, xa, 1, xb, h - 1, wheel, fat)
    return down(im, w, h)


# the rear wings: none, a lip (a ducktail), a high wing on stalks, a big
# wing with endplates. The opponents' cars have one baked into their body
# (AI_WING, per livery); the player's is a layer of its own drawn over the
# body (WING_H rows, its bottom row on the body's top: wing_row)
WING_NAMES = ["NONE", "LIP", "HIGH", "BIG"]
AI_WING = [0, 2, 1, 2, 1, 0, 3, 3]
WING_H = 18
BODY_TOP = {"GT": 0.42, "HATCH": 0.46, "ROADSTER": 0.48, "WEDGE": 0.50}


def wing_row(h, model):
    """The body's texture row the wing's bottom sits on, + 1."""
    return int(h * BODY_TOP[model]) + 2


def draw_wing(d, w, base, style, sh, dark, light):
    """A wing whose stalks end on row base, shifted sh * 0.3 when turned."""
    o = sh * 0.3
    stalk = (40, 40, 44, 255)
    if style == 1:                                  # a ducktail lip
        poly(d, [(w * 0.14 + o, base - 2.5), (w * 0.86 + o, base - 2.5), (w * 0.9 + o, base + 0.5),
                 (w * 0.1 + o, base + 0.5)], dark + (255,))
        rect(d, w * 0.15 + o, base - 2.5, w * 0.85 + o, base - 1.7, light + (255,))
    elif style == 2:                                # a high wing on two stalks
        for px in (w * 0.28, w * 0.72):
            rect(d, px - 0.8 + o, base - 9, px + 0.8 + o, base + 0.5, stalk)
        poly(d, [(w * 0.1 + o, base - 12), (w * 0.9 + o, base - 12), (w * 0.92 + o, base - 9),
                 (w * 0.08 + o, base - 9)], dark + (255,))
        rect(d, w * 0.11 + o, base - 12, w * 0.89 + o, base - 11.2, light + (255,))
    elif style == 3:                                # a big wing with endplates
        for px in (w * 0.24, w * 0.76):
            rect(d, px - 1.1 + o, base - 12, px + 1.1 + o, base + 0.5, stalk)
        poly(d, [(w * 0.05 + o, base - 15.5), (w * 0.95 + o, base - 15.5), (w * 0.95 + o, base - 11),
                 (w * 0.05 + o, base - 11)], dark + (255,))
        rect(d, w * 0.06 + o, base - 15.5, w * 0.94 + o, base - 14.6, light + (255,))
        rect(d, w * 0.06 + o, base - 11.8, w * 0.94 + o, base - 11, (200, 30, 30, 255))   # a gurney flap
        for ex in (w * 0.03, w * 0.95):                                                 # the endplates
            rect(d, ex + o, base - 17, ex + w * 0.025 + o, base - 9, stalk)


def draw_wing_layer(w, h, style, view):
    """The player's wing as a layer of its own: carbon, h rows, its stalks
    ending on the bottom row."""
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    draw_wing(d, w, h - 1, style, view * w * 0.06, (34, 34, 40), (120, 120, 132))
    return down(im, w, h)


def draw_car(w, h, model, body, view, wing=0):
    """A car's body from behind (w x h texels), without its tyres (an axle
    sprite: draw_axle). model: GT, HATCH, ROADSTER, WEDGE; view -1 / 0 / 1:
    seen a little from its left / straight / its right (the cabin shifts,
    the side shows)."""
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    light = tuple(clamp8(v * 1.25 + 26) for v in body)
    mid = shade(body, 0.82)
    dark = shade(body, 0.52)
    glass_t, glass_b = (36, 44, 60), (82, 104, 130)
    red, red_core = (170, 8, 18), (255, 90, 80)
    sh = view * w * 0.06                        # how far the top shifts when turned
    # the car's outline per model: body width (0..1 of w), body top, roof top
    spec = {"GT": (0.96, 0.42, 0.10), "HATCH": (0.88, 0.46, 0.04),
            "ROADSTER": (0.92, 0.48, 0.30), "WEDGE": (1.0, 0.50, 0.20)}[model]
    bw, btop, rtop = spec
    x0, x1 = w * (1 - bw) / 2 + 2, w - w * (1 - bw) / 2 - 3
    ytop, ybot = h * btop, h - 8
    # the flank, when turned (and the far tyre beyond it)
    if view:
        fx, fd = (x0, -1) if view > 0 else (x1, 1)
        poly(d, [(fx, ybot + 1), (fx, ytop + 3), (fx + fd * 5, ytop + 6), (fx + fd * 5, ybot - 1)], dark + (255,))
        xa, xb = sorted((fx + fd * 1.5, fx + fd * 4.5))
        rect(d, xa, (ytop + ybot) / 2, xb, (ytop + ybot) / 2 + 1, shade(body, 0.35) + (255,))
    # the body: shaded, with a crease of light along its shoulder
    if model == "WEDGE":
        poly(d, [(x0, ybot), (x0 + 3, ytop), (x1 - 3, ytop), (x1, ybot)], mid + (255,))
        vgrad(d, x0 + 3, ytop, x1 - 3, ybot, light, dark)
        poly(d, [(x0, ybot), (x0 + 3, ytop), (x0 + 3, ybot)], mid + (255,))
        poly(d, [(x1, ybot), (x1 - 3, ytop), (x1 - 3, ybot)], dark + (255,))
    else:
        d.rounded_rectangle(S(x0, ytop, x1, ybot), radius=3 * SS, fill=mid + (255,))
        vgrad(d, x0 + 2, ytop + 1, x1 - 2, ybot, light, dark)
    rect(d, x0 + 3, ytop + 2, x1 - 3, ytop + 2.8, tuple(clamp8(v * 1.4 + 40) for v in body) + (255,))
    # the cabin (and its glass) - or the roadster's open cockpit
    cx0, cx1 = x0 + w * 0.15 + sh, x1 - w * 0.15 + sh
    if model == "ROADSTER":
        for hx in (w * 0.36 + sh, w * 0.64 + sh):      # the humps behind the seats
            ellipse(d, hx - w * 0.1, ytop - 7, hx + w * 0.1, ytop + 4, mid + (255,))
        d.arc(S(w * 0.3 + sh, ytop - 13, w * 0.5 + sh, ytop + 5), 180, 360, fill=(170, 170, 176, 255), width=SS)
        ellipse(d, w * 0.34 + sh, ytop - 11, w * 0.46 + sh, ytop - 1, (240, 240, 244, 255))   # the helmet
        rect(d, w * 0.34 + sh, ytop - 7, w * 0.46 + sh, ytop - 5.6, (40, 40, 50, 255))
        rect(d, w * 0.25 + sh, ytop - 3.2, w * 0.75 + sh, ytop + 0.5, (30, 30, 34, 255))     # the cockpit's rim
    else:
        roof = h * rtop
        tilt = 4 if model in ("GT", "WEDGE") else 3.5      # how much the rear glass leans
        poly(d, [(cx0 - 2, ytop + 1), (cx1 + 2, ytop + 1), (cx1 - tilt + sh * 0.4, roof),
                 (cx0 + tilt + sh * 0.4, roof)], mid + (255,))
        gx0, gx1 = cx0 + 1.5, cx1 - 1.5
        for y in range(int(roof * SS) + 2 * SS, int((ytop - 0.5) * SS)):
            t = (y / SS - roof - 2) / max(1, ytop - roof - 2.5)
            inset = (1 - t) * (tilt - 0.5)
            d.line([((gx0 + inset + sh * 0.4 * (1 - t)) * SS, y), ((gx1 - inset + sh * 0.4 * (1 - t)) * SS, y)],
                   fill=tuple(clamp8(v) for v in lerp(glass_t, glass_b, t)) + (255,))
        poly(d, [(gx0 + w * 0.08, ytop - 1), (gx0 + w * 0.14, ytop - 1), (gx0 + w * 0.2, roof + 2.5),
                 (gx0 + w * 0.14, roof + 2.5)], (150, 176, 204, 255))            # a streak of sky
        if model == "HATCH":                              # the roof spoiler
            rect(d, cx0 + 1 + sh * 0.4, roof - 1.5, cx1 - 1 + sh * 0.4, roof + 1.2, dark + (255,))
    # the lights
    ly = ytop + (ybot - ytop) * 0.28
    if model == "GT":
        rect(d, x0 + 3, ly, x1 - 3, ly + 2.6, red + (255,))
        rect(d, x0 + 4, ly + 0.6, x1 - 4, ly + 1.8, red_core + (255,))
    elif model == "HATCH":
        # slim LED bars wrapping round the corners, a light strip between
        for side in (-1, 1):
            xa = x0 + 1 if side < 0 else x1 - 15
            poly(d, [(xa, ytop + 2), (xa + 14, ytop + 3), (xa + 14, ytop + 5.5), (xa, ytop + 5)], red + (255,))
            rect(d, xa + 1.5, ytop + 3.2, xa + 12.5, ytop + 4.4, red_core + (255,))
        rect(d, x0 + 15, ytop + 3.6, x1 - 15, ytop + 4.4, (120, 20, 26, 255))
    elif model == "ROADSTER":
        for lx in (x0 + 7, x0 + 15, x1 - 7, x1 - 15):
            ellipse(d, lx - 3, ly - 2, lx + 3, ly + 4, red + (255,))
            ellipse(d, lx - 1.6, ly - 0.6, lx + 1.6, ly + 2.6, red_core + (255,))
    else:                                                 # WEDGE: a V of LEDs
        for side in (-1, 1):
            xa, xb = (x0 + 4, w / 2 - 6) if side < 0 else (x1 - 4, w / 2 + 6)
            poly(d, [(xa, ly), (xb, ly + 3.5), (xb, ly + 5), (xa, ly + 1.6)], red_core + (255,))
    # the plate, the bumper / diffuser, the exhausts
    rect(d, w / 2 - 7, ybot - 9, w / 2 + 7, ybot - 4.5, (232, 232, 222, 255))
    rect(d, w / 2 - 5.5, ybot - 7.6, w / 2 + 5.5, ybot - 6, (60, 60, 70, 255))
    rect(d, x0 + 2, ybot - 3.5, x1 - 2, ybot + 0.5, (34, 34, 38, 255))
    if model == "WEDGE":
        for k in range(7):
            fx = w * 0.3 + k * w * 0.4 / 6
            rect(d, fx - 0.5, ybot - 3.5, fx + 0.5, ybot + 1.5, (70, 70, 76, 255))
    pipes = {"GT": (-4, 4), "HATCH": (-24,), "ROADSTER": (-26, -20, 20, 26), "WEDGE": (0,)}[model]
    for px in pipes:
        ellipse(d, w / 2 + px - 2.2, ybot - 2.6, w / 2 + px + 2.2, ybot + 0.6, (150, 150, 158, 255))
        ellipse(d, w / 2 + px - 1.2, ybot - 1.8, w / 2 + px + 1.2, ybot - 0.2, (20, 20, 20, 255))
    # a rear wing baked in (the opponents' cars)
    if wing:
        draw_wing(d, w, wing_row(h, model) - 1, wing, sh, dark, light)
    # a stripe over the top
    if model in ("GT", "HATCH"):
        rect(d, w / 2 - 2.5 + sh * 0.3, ytop + 0.5, w / 2 + 2.5 + sh * 0.3, ytop + 3, (244, 244, 244, 255))
    return down(im, w, h)

def draw_banner(w, h, text, size):
    im = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f = font(size * SS)
    tw = d.textlength(text, font=f)
    bbox = d.textbbox((0, 0), text, font=f)
    th = bbox[3] - bbox[1]
    x = (w * SS - tw) / 2
    y = (h * SS - th) / 2 - bbox[1]
    # gradient text with a dark outline
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).text((x, y), text, font=f, fill=255)
    outline = mask.filter(ImageFilter.MaxFilter(4 * SS - 1 if (4 * SS - 1) % 2 else 4 * SS + 1))
    grad = Image.new("RGBA", im.size)
    gd = ImageDraw.Draw(grad)
    for yy in range(im.size[1]):
        t = yy / im.size[1]
        c = lerp((255, 250, 150), (250, 110, 20), t)
        gd.line([(0, yy), (im.size[0], yy)], fill=tuple(clamp8(v) for v in c) + (255,))
    im.paste((30, 16, 40, 255), (0, 0), outline)
    im.paste(grad, (SS, SS), mask)
    return down(im, w, h)


def draw_bonnet(w, h):
    """The car's bonnet seen from the driver's seat (cockpit view): drawn
    at half the screen's width (the DMA doubles it). Its front edge is a
    flat curve, highest in the middle; near the edge the paint mirrors the
    sky, further back it darkens; a bulge with twin stripes runs down the
    middle and the wings rise a little at the sides."""
    im = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    px = im.load()
    body = (198, 20, 28)
    cx = w * SS / 2
    for x in range(w * SS):
        u = (x - cx) / cx                       # -1 .. 1 across the screen
        wing = max(0.0, abs(u) - 0.72) / 0.28   # the wings rise at the sides
        top = (0.30 + 0.30 * u * u - 0.16 * wing * wing) * h * SS
        for y in range(int(top), h * SS):
            d = (y - top) / SS                  # pixels behind the front edge
            t = (y - top) / (h * SS - top + 1)
            c = lerp(shade(body, 1.08), shade(body, 0.5), min(1.0, t * 1.2))
            # the sky in the paint, just behind the edge
            if d < 5:
                c = lerp(c, (236, 170, 180), 0.55 * (1 - d / 5))
            if d < 0.8:
                c = (255, 210, 210)
            # the bulge, lit from the left
            if abs(u) < 0.24:
                c = shade(c, 1.07)
                if -0.24 < u < -0.2:
                    c = lerp(c, (250, 160, 160), 0.5)
                if 0.2 < u < 0.24:
                    c = shade(c, 0.72)
            # a soft streak of reflected light across each side
            for b0 in (-0.55, 0.5):
                dd = u - b0 + t * 0.25 * (1 if b0 < 0 else -1)
                if abs(dd) < 0.07:
                    c = lerp(c, (232, 120, 130), 0.45 * (1 - abs(dd) / 0.07))
            # the wings' crease
            if 0.70 < abs(u) < 0.74:
                c = shade(c, 0.7)
            px[x, y] = tuple(clamp8(v) for v in c) + (255,)
    d = ImageDraw.Draw(im)
    for sx in (-5.5, 2.5):
        poly(d, [(w / 2 + sx, h * 0.30), (w / 2 + sx + 3, h * 0.30), (w / 2 + sx * 1.6 + 4.8, h),
                 (w / 2 + sx * 1.6, h)], (246, 246, 246, 255))
    for k in range(3):
        for side in (-1, 1):
            x0 = w / 2 + side * 26
            rect(d, x0 - 7, h * 0.56 + k * 4, x0 + 7, h * 0.56 + k * 4 + 1.4, (62, 8, 12, 255))
    return down(im, w, h)



def draw_house(w, h):
    """A farmhouse: white boards, a grey roof, a porch, windows lit warm."""
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    wall_top = h * 0.42
    rect(d, 6, wall_top, w - 7, h - 1, (236, 232, 220, 255))
    for k in range(int(wall_top) + 2, h - 1, 3):          # the boards
        rect(d, 6, k, w - 7, k + 0.5, (206, 200, 186, 255))
    rect(d, 6, wall_top, 12, h - 1, (214, 208, 194, 255))  # shade on the left end
    poly(d, [(1, wall_top + 1), (w / 2, 3), (w - 2, wall_top + 1)], (96, 98, 110, 255))
    poly(d, [(1, wall_top + 1), (w / 2, 3), (w / 2, 6), (8, wall_top + 1)], (126, 128, 140, 255))
    rect(d, w * 0.68, 6, w * 0.68 + 6, wall_top * 0.7, (150, 70, 52, 255))  # chimney
    for wx in (0.2, 0.42, 0.62, 0.8):
        x0 = w * wx - 4
        rect(d, x0, wall_top + 7, x0 + 8, wall_top + 17, (60, 60, 70, 255))
        rect(d, x0 + 1, wall_top + 8, x0 + 7, wall_top + 16, (250, 220, 140, 255))
        rect(d, x0 + 3.5, wall_top + 8, x0 + 4.5, wall_top + 16, (60, 60, 70, 255))
        rect(d, x0 - 1.5, wall_top + 7, x0, wall_top + 17, (40, 90, 60, 255))     # shutters
        rect(d, x0 + 8, wall_top + 7, x0 + 9.5, wall_top + 17, (40, 90, 60, 255))
    # the porch and the door
    rect(d, w * 0.36, h - 20, w * 0.64, h - 18, (120, 70, 50, 255))
    for px in (w * 0.37, w * 0.5 - 0.5, w * 0.63 - 1):
        rect(d, px, h - 18, px + 1.2, h - 1, (240, 236, 226, 255))
    rect(d, w * 0.47, h - 15, w * 0.53, h - 1, (110, 60, 40, 255))
    return down(im, w, h)


def draw_barn(w, h):
    """A red barn with a gambrel roof, white trim and a crossed door."""
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    red, dark = (176, 34, 30, 255), (120, 22, 20, 255)
    eave = h * 0.45
    poly(d, [(2, h - 1), (2, eave), (w * 0.18, h * 0.16), (w / 2, 2), (w * 0.82, h * 0.16),
             (w - 3, eave), (w - 3, h - 1)], red)
    poly(d, [(2, eave), (w * 0.18, h * 0.16), (w / 2, 2), (w / 2, 5), (w * 0.2, h * 0.19), (5, eave)],
         (70, 70, 78, 255))
    poly(d, [(w - 3, eave), (w * 0.82, h * 0.16), (w / 2, 2), (w / 2, 5), (w * 0.8, h * 0.19), (w - 6, eave)],
         (70, 70, 78, 255))
    for k in range(4, w - 4, 4):                                   # boards
        rect(d, k, eave + 1, k + 0.6, h - 1, dark)
    # the big door with its white cross
    x0, x1, y0 = w * 0.32, w * 0.68, h * 0.52
    rect(d, x0, y0, x1, h - 1, (240, 236, 230, 255))
    rect(d, x0 + 1.5, y0 + 1.5, x1 - 1.5, h - 1, red)
    d.line(S(x0 + 1.5, y0 + 1.5, x1 - 1.5, h - 1), fill=(240, 236, 230, 255), width=SS + 1)
    d.line(S(x1 - 1.5, y0 + 1.5, x0 + 1.5, h - 1), fill=(240, 236, 230, 255), width=SS + 1)
    # the hay loft door
    rect(d, w * 0.43, h * 0.22, w * 0.57, h * 0.38, (240, 236, 230, 255))
    rect(d, w * 0.45, h * 0.24, w * 0.55, h * 0.36, (60, 30, 20, 255))
    return down(im, w, h)


def draw_silo(w, h):
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    for x in range(w * SS):
        t = x / (w * SS)
        c = lerp((120, 124, 132), (220, 224, 232), max(0.0, 1 - abs(t - 0.35) * 2.4))
        d.line([(x, 10 * SS), (x, h * SS)], fill=tuple(clamp8(v) for v in c) + (255,))
    ellipse(d, 0, 2, w - 1, 20, (176, 180, 190, 255))
    ellipse(d, 3, 4, w * 0.5, 14, (222, 226, 234, 255))
    for k in range(18, h, 9):                                      # hoops
        rect(d, 0, k, w - 1, k + 0.7, (100, 104, 112, 255))
    return down(im, w, h)


def draw_hay(w, h):
    """Two round bales."""
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    for cx, r in ((w * 0.3, h * 0.48), (w * 0.72, h * 0.4)):
        cy = h - r - 0.5
        ellipse(d, cx - r * 1.15, cy - r, cx + r * 1.15, cy + r, (196, 160, 70, 255))
        ellipse(d, cx - r * 0.75, cy - r * 0.75, cx + r * 0.75, cy + r * 0.75, (226, 192, 96, 255))
        for k in (0.55, 0.3):
            d.ellipse(S(cx - r * k, cy - r * k, cx + r * k, cy + r * k), outline=(176, 140, 60, 255), width=SS)
    return down(im, w, h)


def draw_cow(w, h):
    """A black and white cow, side on, grazing."""
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    white, black = (240, 238, 232, 255), (30, 30, 34, 255)
    for lx in (0.28, 0.36, 0.66, 0.74):                            # legs
        rect(d, w * lx, h * 0.6, w * lx + 2.4, h - 1, (210, 206, 200, 255))
        rect(d, w * lx, h - 3, w * lx + 2.4, h - 1, black)
    ellipse(d, w * 0.22, h * 0.25, w * 0.82, h * 0.7, white)       # body
    ellipse(d, w * 0.3, h * 0.28, w * 0.48, h * 0.55, black)
    ellipse(d, w * 0.58, h * 0.36, w * 0.74, h * 0.62, black)
    ellipse(d, w * 0.5, h * 0.24, w * 0.6, h * 0.38, black)
    ellipse(d, w * 0.48, h * 0.58, w * 0.6, h * 0.72, (240, 180, 180, 255))  # udder
    poly(d, [(w * 0.24, h * 0.36), (w * 0.12, h * 0.62), (w * 0.05, h * 0.76), (w * 0.14, h * 0.8),
             (w * 0.22, h * 0.56)], white)                            # neck, head down
    ellipse(d, w * 0.01, h * 0.66, w * 0.15, h * 0.86, (236, 190, 180, 255))
    rect(d, w * 0.08, h * 0.6, w * 0.13, h * 0.68, black)            # ear
    d.line(S(w * 0.82, h * 0.34, w * 0.9, h * 0.62), fill=black, width=SS)  # tail
    return down(im, w, h)


def draw_fence_wood(w, h):
    """Post and rail: posts at the ends and the middle, two rails."""
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    for y in (h * 0.25, h * 0.55):
        rect(d, 0, y, w - 1, y + 2.2, (132, 96, 60, 255))
        rect(d, 0, y, w - 1, y + 0.8, (168, 128, 84, 255))
    for px in (1, w / 2 - 1.5, w - 4):
        rect(d, px, h * 0.12, px + 3, h - 1, (110, 78, 48, 255))
        rect(d, px, h * 0.12, px + 1, h - 1, (150, 112, 72, 255))
    return down(im, w, h)


def draw_picket(w, h):
    """A white picket fence."""
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    rect(d, 0, h * 0.35, w - 1, h * 0.35 + 1.5, (210, 210, 206, 255))
    rect(d, 0, h * 0.7, w - 1, h * 0.7 + 1.5, (210, 210, 206, 255))
    for k in range(0, w - 2, 5):
        poly(d, [(k, h - 1), (k, 3), (k + 1.6, 0.5), (k + 3.2, 3), (k + 3.2, h - 1)], (246, 246, 242, 255))
        rect(d, k + 2.4, 3, k + 3.2, h - 1, (206, 206, 202, 255))
    return down(im, w, h)


def draw_wall(w, h, seed):
    """A dry-stone wall."""
    rnd = random.Random(seed)
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    rect(d, 0, 2, w - 1, h - 1, (92, 90, 84, 255))
    y = 2.0
    while y < h - 1:
        x = -rnd.uniform(0, 4)
        rh = rnd.uniform(3, 4.5)
        while x < w:
            sw = rnd.uniform(4, 8)
            c = rnd.randint(130, 176)
            ellipse(d, x + 0.4, y + 0.3, x + sw - 0.4, y + rh - 0.2, (c, c - 4, c - 14, 255))
            x += sw
        y += rh
    for k in range(0, w, 6):                                          # moss on the top
        ellipse(d, k, 0.5, k + 6, 3.5, (96, 120, 64, 255))
    return down(im, w, h)


def draw_mailbox(w, h):
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    rect(d, w / 2 - 1.2, h * 0.4, w / 2 + 1.2, h - 1, (110, 80, 50, 255))
    rect(d, 1, h * 0.12, w - 2, h * 0.42, (40, 70, 140, 255))
    ellipse(d, 1, h * 0.04, w - 2, h * 0.24, (40, 70, 140, 255))
    rect(d, 2, h * 0.14, w * 0.4, h * 0.38, (70, 100, 170, 255))
    rect(d, w - 4, h * 0.06, w - 2.5, h * 0.24, (220, 30, 30, 255))      # the flag
    return down(im, w, h)


def draw_pole(w, h):
    """A telegraph pole: a crossbar, insulators."""
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    rect(d, w / 2 - 1.8, 6, w / 2 + 1.8, h - 1, (96, 72, 50, 255))
    rect(d, w / 2 - 1.8, 6, w / 2 - 0.6, h - 1, (128, 98, 70, 255))
    rect(d, 1, 10, w - 2, 12.5, (96, 72, 50, 255))
    rect(d, 4, 18, w - 5, 20, (96, 72, 50, 255))
    for x in (2, 6, w - 7, w - 3):
        rect(d, x - 1, 7.5, x + 1, 10, (200, 220, 230, 255))
    for x in (5, w - 6):
        rect(d, x - 1, 15.5, x + 1, 18, (200, 220, 230, 255))
    return down(im, w, h)



# --- more of the countryside and the towns ---------------------------------

def draw_birch(w, h, seed):
    rnd = random.Random(seed)
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    cx = w / 2
    rect(d, cx - 1.6, h * 0.3, cx + 1.6, h - 1, (236, 234, 226, 255))
    for k in range(8):                                      # the bark's dark marks
        y = h * 0.35 + k * (h * 0.6) / 8 + rnd.uniform(0, 2)
        rect(d, cx - 1.6, y, cx + rnd.uniform(-0.5, 1.6), y + 1, (50, 50, 46, 255))
    blobs = sorted((rnd.uniform(4, h * 0.6), cx + rnd.uniform(-w * 0.32, w * 0.32), rnd.uniform(3, 6))
                   for _ in range(22))
    for y, x, r in blobs:
        c = lerp((112, 160, 56), (176, 204, 84), rnd.random())
        ellipse(d, x - r, y - r * 1.2, x + r, y + r * 1.2, tuple(clamp8(v) for v in c) + (255,))
    return down(im, w, h)


def draw_poplar(w, h):
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    cx = w / 2
    rect(d, cx - 1.4, h - 10, cx + 1.4, h, (96, 74, 50, 255))
    ellipse(d, cx - w * 0.42, 1, cx + w * 0.42, h - 6, (50, 104, 44, 255))
    ellipse(d, cx - w * 0.38, 3, cx + w * 0.05, h - 12, (78, 136, 58, 255))
    for k in range(10):
        y = 6 + k * (h - 18) / 10
        ellipse(d, cx - w * 0.3, y, cx - w * 0.05, y + 3, (98, 156, 70, 255))
    return down(im, w, h)


def draw_apple(w, h, seed):
    rnd = random.Random(seed)
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    cx = w / 2
    poly(d, [(cx - 3, h), (cx + 3, h), (cx + 2, h * 0.55), (cx - 2, h * 0.55)], (104, 76, 50, 255))
    ellipse(d, cx - w * 0.47, h * 0.08, cx + w * 0.47, h * 0.68, (56, 120, 46, 255))
    ellipse(d, cx - w * 0.4, h * 0.1, cx + w * 0.1, h * 0.48, (86, 150, 60, 255))
    for _ in range(14):
        x, y = cx + rnd.uniform(-w * 0.38, w * 0.38), rnd.uniform(h * 0.15, h * 0.6)
        ellipse(d, x - 1.3, y - 1.3, x + 1.3, y + 1.3, (220, 40, 36, 255))
    return down(im, w, h)


def draw_deadtree(w, h, seed):
    rnd = random.Random(seed)
    im = canvas(w, h)
    d = ImageDraw.Draw(im)

    def branch(x, y, a, length, width, depth):
        x2, y2 = x + math.cos(a) * length, y - math.sin(a) * length
        d.line(S(x, y, x2, y2), fill=(90, 74, 60, 255), width=max(SS, int(width * SS)))
        if depth:
            for da in (-0.5, 0.45):
                branch(x2, y2, a + da + rnd.uniform(-0.2, 0.2), length * 0.68, width * 0.62, depth - 1)
    branch(w / 2, h - 1, math.pi / 2, h * 0.4, 4.0, 4)
    return down(im, w, h)


def draw_rocks(w, h, seed, big):
    """A cluster of small rocks, or one big boulder."""
    rnd = random.Random(seed)
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    pieces = [(w / 2, w * 0.48, h * 0.95)] if big else [
        (rnd.uniform(w * 0.2, w * 0.8), rnd.uniform(w * 0.12, w * 0.24), rnd.uniform(h * 0.4, h * 0.8))
        for _ in range(5)]
    for cx, rw, rh in sorted(pieces, key=lambda p: -p[2]):
        pts = []
        for k in range(9):
            a = math.pi + math.pi * k / 8
            r = rnd.uniform(0.75, 1.0)
            pts.append((cx + math.cos(a) * rw * r, h - 1 + math.sin(a) * rh * r))
        base = rnd.choice([(128, 120, 108), (110, 106, 100), (140, 128, 112)])
        poly(d, pts, base + (255,))
        poly(d, pts[:5] + [(cx, h - 1)], shade(base, 1.2) + (255,))
        if big:
            for _ in range(6):                               # cracks and lichen
                x, y = rnd.uniform(cx - rw * 0.6, cx + rw * 0.6), rnd.uniform(h * 0.3, h * 0.9)
                ellipse(d, x - 2, y - 1, x + 2, y + 1, (150, 160, 110, 255))
    return down(im, w, h)


def draw_hedge(w, h, seed):
    rnd = random.Random(seed)
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    rect(d, 1, h * 0.25, w - 2, h - 1, (52, 104, 40, 255))
    for _ in range(40):
        x, y = rnd.uniform(2, w - 2), rnd.uniform(h * 0.2, h - 3)
        r = rnd.uniform(2, 4)
        c = lerp((40, 92, 34), (96, 150, 60), rnd.random())
        ellipse(d, x - r, y - r, x + r, y + r, tuple(clamp8(v) for v in c) + (255,))
    return down(im, w, h)


def draw_shrub(w, h, seed, flower):
    rnd = random.Random(seed)
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    for _ in range(12):
        x = rnd.uniform(w * 0.2, w * 0.8)
        r = rnd.uniform(w * 0.15, w * 0.25)
        y = h - r - rnd.uniform(0, h * 0.4)
        c = lerp((50, 110, 46), (110, 160, 70), rnd.random())
        ellipse(d, x - r, y - r, x + r, y + r, tuple(clamp8(v) for v in c) + (255,))
    for _ in range(16):
        x, y = rnd.uniform(w * 0.15, w * 0.85), rnd.uniform(h * 0.15, h * 0.8)
        ellipse(d, x - 1.2, y - 1.2, x + 1.2, y + 1.2, flower + (255,))
    return down(im, w, h)


def draw_cottage(w, h, wall, roof):
    """A small house: walls, a pitched roof, a door, two windows."""
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    top = h * 0.45
    rect(d, 4, top, w - 5, h - 1, wall + (255,))
    rect(d, 4, top, 8, h - 1, shade(wall, 0.8) + (255,))
    poly(d, [(0, top + 1), (w / 2, 2), (w - 1, top + 1)], roof + (255,))
    poly(d, [(0, top + 1), (w / 2, 2), (w / 2, 5), (6, top + 1)], shade(roof, 1.25) + (255,))
    rect(d, w * 0.7, 6, w * 0.7 + 4, top * 0.8, (120, 60, 46, 255))
    rect(d, w * 0.44, h - 13, w * 0.56, h - 1, (90, 54, 36, 255))
    for wx in (0.18, 0.7):
        rect(d, w * wx, top + 5, w * wx + 8, top + 13, (60, 60, 70, 255))
        rect(d, w * wx + 1, top + 6, w * wx + 7, top + 12, (180, 210, 230, 255))
    return down(im, w, h)


def draw_shed(w, h):
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    rect(d, 2, h * 0.35, w - 3, h - 1, (122, 88, 56, 255))
    for x in range(3, w - 3, 3):
        rect(d, x, h * 0.35, x + 0.6, h - 1, (96, 68, 42, 255))
    poly(d, [(0, h * 0.38), (w * 0.3, 1), (w - 1, h * 0.2), (w - 1, h * 0.38)], (80, 84, 90, 255))
    rect(d, w * 0.35, h * 0.55, w * 0.65, h - 1, (70, 50, 32, 255))
    return down(im, w, h)


def draw_windpump(w, h):
    """A farm's wind pump: a lattice tower and a bladed wheel."""
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    col = (150, 150, 156, 255)
    d.line(S(w * 0.2, h - 1, w * 0.45, h * 0.3), fill=col, width=SS)
    d.line(S(w * 0.8, h - 1, w * 0.55, h * 0.3), fill=col, width=SS)
    for k in range(5):
        y = h - 1 - k * (h * 0.7) / 5
        t = k / 5
        d.line(S(w * (0.2 + 0.25 * t), y, w * (0.8 - 0.25 * t), y), fill=col, width=SS)
    cx, cy, r = w / 2, h * 0.25, w * 0.42
    for k in range(16):
        a = k * math.tau / 16
        d.line(S(cx, cy, cx + math.cos(a) * r, cy + math.sin(a) * r), fill=(196, 196, 200, 255), width=SS)
    d.ellipse(S(cx - r, cy - r, cx + r, cy + r), outline=(176, 176, 184, 255), width=SS)
    poly(d, [(cx, cy), (cx + r * 1.2, cy - 2), (cx + r * 1.2, cy + 4)], (196, 40, 36, 255))   # the tail
    return down(im, w, h)


def draw_tractor(w, h):
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    green = (40, 130, 50, 255)
    rect(d, w * 0.15, h * 0.4, w * 0.75, h * 0.72, green)                 # the body
    rect(d, w * 0.5, h * 0.08, w * 0.85, h * 0.45, (60, 60, 64, 255))     # the cab
    rect(d, w * 0.54, h * 0.12, w * 0.81, h * 0.38, (150, 190, 220, 255))
    rect(d, w * 0.25, h * 0.15, w * 0.29, h * 0.4, (60, 60, 64, 255))     # the exhaust
    ellipse(d, w * 0.55, h * 0.38, w * 0.98, h - 1, (30, 30, 32, 255))
    ellipse(d, w * 0.66, h * 0.55, w * 0.87, h * 0.85, (230, 200, 40, 255))
    ellipse(d, w * 0.08, h * 0.6, w * 0.34, h - 1, (30, 30, 32, 255))
    ellipse(d, w * 0.15, h * 0.7, w * 0.27, h * 0.9, (230, 200, 40, 255))
    return down(im, w, h)


def draw_sheep(w, h, seed):
    rnd = random.Random(seed)
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    for lx in (0.3, 0.42, 0.62, 0.74):
        rect(d, w * lx, h * 0.62, w * lx + 1.6, h - 1, (40, 40, 40, 255))
    for _ in range(10):
        x, y = rnd.uniform(w * 0.28, w * 0.8), rnd.uniform(h * 0.3, h * 0.6)
        ellipse(d, x - 4, y - 3.5, x + 4, y + 3.5, (236, 234, 224, 255))
    ellipse(d, w * 0.08, h * 0.32, w * 0.3, h * 0.58, (50, 46, 44, 255))
    return down(im, w, h)


def draw_fingerpost(w, h):
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    rect(d, w / 2 - 1.2, 6, w / 2 + 1.2, h - 1, (236, 236, 230, 255))
    poly(d, [(2, 7), (w / 2, 7), (w / 2, 12), (2, 12), (0, 9.5)], (240, 240, 236, 255))
    poly(d, [(w / 2, 14), (w - 3, 14), (w - 1, 16.5), (w - 3, 19), (w / 2, 19)], (240, 240, 236, 255))
    rect(d, 4, 9, w / 2 - 2, 10, (40, 40, 40, 255))
    rect(d, w / 2 + 2, 16, w - 5, 17, (40, 40, 40, 255))
    return down(im, w, h)


def draw_shop(w, h, wall, awning, text):
    """A two-storey shop: a window and an awning below, a sign, flats above."""
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    rect(d, 1, 3, w - 2, h - 1, wall + (255,))
    rect(d, 1, 1, w - 2, 4, shade(wall, 0.7) + (255,))
    for wx in range(3):                                        # upstairs windows
        x0 = 5 + wx * (w - 10) / 3
        rect(d, x0, 9, x0 + (w - 10) / 3 - 5, h * 0.45, (70, 80, 100, 255))
        rect(d, x0 + 1, 10, x0 + (w - 10) / 3 - 6, h * 0.43, (160, 190, 220, 255))
    rect(d, 2, h * 0.5, w - 3, h * 0.6, (240, 236, 220, 255))   # the sign
    f = font(int(h * 0.1 * SS))
    tw = d.textlength(text, font=f)
    d.text(((w * SS - tw) / 2, h * 0.5 * SS), text, font=f, fill=(30, 30, 40, 255))
    for k in range(8):                                         # the striped awning
        x0 = 2 + k * (w - 4) / 8
        poly(d, [(x0, h * 0.6), (x0 + (w - 4) / 8, h * 0.6), (x0 + (w - 4) / 8 + 1, h * 0.68),
                 (x0 + 1, h * 0.68)], awning + (255,) if k % 2 == 0 else (240, 240, 236, 255))
    rect(d, 4, h * 0.7, w * 0.62, h - 2, (60, 70, 86, 255))     # the shop window
    rect(d, 5, h * 0.71, w * 0.61, h - 3, (130, 160, 190, 255))
    rect(d, w * 0.68, h * 0.7, w * 0.88, h - 1, (90, 50, 30, 255))   # the door
    return down(im, w, h)


def draw_terrace(w, h):
    """Brick terraced houses, three doors."""
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    rect(d, 1, h * 0.25, w - 2, h - 1, (160, 74, 56, 255))
    for y in range(int(h * 0.25) + 2, h, 3):
        rect(d, 1, y, w - 2, y + 0.4, (130, 60, 46, 255))
    poly(d, [(0, h * 0.27), (4, 2), (w - 5, 2), (w - 1, h * 0.27)], (70, 72, 82, 255))
    for k in range(3):
        x0 = 3 + k * (w - 6) / 3
        cw = (w - 6) / 3
        rect(d, x0 + cw * 0.12, h * 0.36, x0 + cw * 0.5, h * 0.56, (240, 240, 236, 255))
        rect(d, x0 + cw * 0.16, h * 0.38, x0 + cw * 0.46, h * 0.54, (120, 150, 180, 255))
        rect(d, x0 + cw * 0.6, h * 0.66, x0 + cw * 0.88, h - 1, [(30, 60, 120, 255), (30, 100, 60, 255), (140, 30, 30, 255)][k])
        rect(d, x0 + cw * 0.12, h * 0.66, x0 + cw * 0.5, h * 0.86, (120, 150, 180, 255))
        if k:
            rect(d, x0 - 0.5, h * 0.25, x0 + 0.5, h - 1, (110, 52, 40, 255))
    rect(d, w * 0.3, 0, w * 0.36, 4, (110, 52, 40, 255))      # chimneys
    rect(d, w * 0.66, 0, w * 0.72, 4, (110, 52, 40, 255))
    return down(im, w, h)


def draw_flats(w, h):
    """A block of flats."""
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    rect(d, 1, 1, w - 2, h - 1, (200, 196, 184, 255))
    rect(d, 1, 1, 5, h - 1, (170, 166, 156, 255))
    rows = 6
    for r in range(rows):
        y = 5 + r * (h - 14) / rows
        for c in range(4):
            x = 7 + c * (w - 12) / 4
            rect(d, x, y, x + (w - 12) / 4 - 4, y + (h - 14) / rows - 4, (70, 84, 104, 255))
            rect(d, x + 0.8, y + 0.8, x + (w - 12) / 4 - 4.8, y + (h - 14) / rows - 4.8,
                 (150, 180, 210, 255) if (r + c) % 3 else (240, 220, 140, 255))
    rect(d, w * 0.42, h - 9, w * 0.58, h - 1, (60, 60, 70, 255))
    return down(im, w, h)


def draw_church(w, h):
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    stone = (190, 184, 166)
    rect(d, w * 0.36, h * 0.2, w * 0.64, h - 1, stone + (255,))           # the tower
    poly(d, [(w * 0.33, h * 0.2), (w / 2, 0), (w * 0.67, h * 0.2)], (80, 84, 96, 255))
    rect(d, 2, h * 0.55, w - 3, h - 1, stone + (255,))                    # the nave
    poly(d, [(0, h * 0.56), (w * 0.36, h * 0.38), (w * 0.36, h * 0.56)], (90, 92, 104, 255))
    poly(d, [(w * 0.64, h * 0.38), (w - 1, h * 0.56), (w * 0.64, h * 0.56)], (90, 92, 104, 255))
    ellipse(d, w * 0.44, h * 0.28, w * 0.56, h * 0.36, (240, 240, 230, 255))   # the clock
    for x in (0.12, 0.24, 0.76, 0.88):
        poly(d, [(w * x - 2, h - 4), (w * x - 2, h * 0.68), (w * x, h * 0.64), (w * x + 2, h * 0.68),
                 (w * x + 2, h - 4)], (60, 70, 110, 255))
    poly(d, [(w * 0.45, h - 1), (w * 0.45, h * 0.78), (w / 2, h * 0.72), (w * 0.55, h * 0.78),
             (w * 0.55, h - 1)], (90, 54, 36, 255))
    return down(im, w, h)


def draw_petrol(w, h):
    """A filling station's canopy and pumps."""
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    for px in (w * 0.12, w * 0.86):
        rect(d, px - 1.5, h * 0.3, px + 1.5, h - 1, (220, 220, 224, 255))
    rect(d, 0, h * 0.16, w - 1, h * 0.34, (240, 240, 240, 255))
    rect(d, 0, h * 0.24, w - 1, h * 0.3, (30, 110, 60, 255))
    f = font(int(h * 0.12 * SS))
    d.text((w * 0.3 * SS, h * 0.16 * SS), "FUEL", font=f, fill=(30, 110, 60, 255))
    for px in (w * 0.35, w * 0.62):
        rect(d, px - 3, h * 0.62, px + 3, h - 1, (200, 40, 40, 255))
        rect(d, px - 2, h * 0.66, px + 2, h * 0.74, (230, 230, 230, 255))
    rect(d, w * 0.25, h - 3, w * 0.75, h - 1, (180, 180, 176, 255))
    return down(im, w, h)



def draw_ramp(w, h):
    """A jump's ramp from behind: its surface rises away, hazard-striped,
    on a dark frame."""
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    # the sides (dark), then the surface: wide at the road, narrower at the lip
    poly(d, [(0, h - 1), (w - 1, h - 1), (w - 7, 2), (6, 2)], (54, 50, 46, 255))
    poly(d, [(3, h - 2), (w - 4, h - 2), (w - 9, 3), (9, 3)], (190, 162, 108, 255))
    # chevrons pointing up the ramp
    for k in range(4):
        y0 = h - 4 - k * (h - 6) / 4
        y1 = y0 - (h - 6) / 8
        poly(d, [(w * 0.3, y0), (w / 2, y1), (w * 0.7, y0), (w * 0.7, y0 - 2.4), (w / 2, y1 - 2.4),
                 (w * 0.3, y0 - 2.4)], (250, 204, 30, 255))
    for x0 in (3, w - 8):
        for k in range(0, h - 3, 6):
            rect(d, x0, h - 3 - k - 3, x0 + 4, h - 3 - k, (250, 204, 30, 255))
    rect(d, 9, 1.5, w - 10, 3.2, (240, 240, 236, 255))                   # the lip
    return down(im, w, h)


def draw_keep(w, h):
    """A keep-left / keep-right sign on the nose of a traffic island."""
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    rect(d, w / 2 - 1.2, h * 0.45, w / 2 + 1.2, h - 4, (190, 190, 196, 255))
    rect(d, 1, h - 5, w - 2, h - 1, (230, 230, 226, 255))                 # the island's kerb
    rect(d, 1, h - 3, w - 2, h - 1, (200, 30, 30, 255))
    ellipse(d, 2, 1, w - 3, w - 3, (255, 255, 255, 255))
    ellipse(d, 3.5, 2.5, w - 4.5, w - 4.5, (30, 80, 190, 255))
    r = (w - 6) / 2
    cx, cy = w / 2, 1 + r + 1.5
    for sgn in (-1, 1):                                                   # arrows down-left, down-right
        poly(d, [(cx, cy - r * 0.55), (cx + sgn * r * 0.55, cy + r * 0.2), (cx + sgn * r * 0.3, cy + r * 0.2),
                 (cx + sgn * r * 0.62, cy + r * 0.62), (cx + sgn * r * 0.15, cy + r * 0.62),
                 (cx + sgn * r * 0.15, cy + r * 0.35), (cx, cy - r * 0.2)], (255, 255, 255, 255))
    return down(im, w, h)


def draw_rail(w, h):
    """A bridge's railing: posts and two rails."""
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    for y in (h * 0.2, h * 0.55):
        rect(d, 0, y, w - 1, y + 2, (210, 214, 220, 255))
        rect(d, 0, y + 1.4, w - 1, y + 2, (140, 144, 150, 255))
    for px in range(1, w - 1, 8):
        rect(d, px, h * 0.15, px + 2, h - 1, (120, 124, 130, 255))
    rect(d, 0, h - 3, w - 1, h - 1, (160, 160, 156, 255))                 # the deck's edge
    return down(im, w, h)


def draw_jumpsign(w, h):
    """A warning sign: a car leaping off a ramp."""
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    rect(d, w / 2 - 1.3, h * 0.5, w / 2 + 1.3, h - 1, (180, 180, 186, 255))
    s2 = (w - 2) / 2
    cx, cy = w / 2, s2 + 1
    poly(d, [(cx, cy - s2), (cx + s2, cy), (cx, cy + s2), (cx - s2, cy)], (30, 30, 34, 255))
    poly(d, [(cx, cy - s2 + 2), (cx + s2 - 2, cy), (cx, cy + s2 - 2), (cx - s2 + 2, cy)], (250, 206, 30, 255))
    poly(d, [(cx - s2 * 0.6, cy + s2 * 0.35), (cx - s2 * 0.05, cy + s2 * 0.35), (cx - s2 * 0.05, cy)],
         (30, 30, 34, 255))                                               # the ramp
    poly(d, [(cx + s2 * 0.0, cy - s2 * 0.15), (cx + s2 * 0.45, cy - s2 * 0.4), (cx + s2 * 0.55, cy - s2 * 0.25),
             (cx + s2 * 0.1, cy + s2 * 0.0)], (30, 30, 34, 255))           # the car, in the air
    ellipse(d, cx + s2 * 0.05, cy - s2 * 0.02, cx + s2 * 0.18, cy + s2 * 0.1, (30, 30, 34, 255))
    ellipse(d, cx + s2 * 0.38, cy - s2 * 0.3, cx + s2 * 0.51, cy - s2 * 0.18, (30, 30, 34, 255))
    return down(im, w, h)


# (name, texture w, h, world width, drawing)
# the cars: a model in a colour each (livery 0 is the player's to start
# with); the world width of each model
LIVERIES = [("GT", (204, 22, 30)), ("GT", (30, 84, 214)), ("HATCH", (240, 196, 24)),
            ("HATCH", (28, 160, 76)), ("ROADSTER", (232, 232, 238)), ("ROADSTER", (244, 122, 20)),
            ("WEDGE", (130, 50, 190)), ("WEDGE", (54, 58, 66))]
CAR_WW = {"GT": 380, "HATCH": 340, "ROADSTER": 350, "WEDGE": 400}
CAR_TW, CAR_TH = 96, 44
TYRES = [False, True]     # tyre types: standard, fat


# scenery placement, per kind: (out from the middle of the road, up to this
# much further, half the width it is hit over, the hit's centre from the
# sprite's); the scenery comes first, up to GANTRY
SCENERY = {
    "PINE": (RW + 410, 900, 110, 0), "PINE2": (RW + 380, 900, 100, 0),
    "PALM": (RW + 460, 700, 50, 0), "OAK": (RW + 580, 900, 90, 0),
    "BUSH": (RW + 280, 700, 140, 0), "ROCK": (RW + 340, 800, 210, 0),
    "SIGNL": (RW + 280, 0, 150, 0), "SIGNR": (RW + 280, 0, 150, 0),
    "LAMPL": (RW + 200, 0, 40, -136), "LAMPR": (RW + 200, 0, 40, 136),
    "BILL1": (RW + 750, 400, 70, 0), "BILL2": (RW + 750, 400, 70, 0), "BILL3": (RW + 750, 400, 70, 0),
    "BARRIER": (RW + 300, 0, 300, 0),
    "HOUSE": (RW + 1500, 500, 600, 0), "BARN": (RW + 1700, 700, 700, 0), "SILO": (RW + 1100, 300, 160, 0),
    "HAY": (RW + 600, 1000, 240, 0), "COW": (RW + 800, 1400, 180, 0),
    "FENCEW": (RW + 330, 0, 300, 0), "PICKET": (RW + 330, 0, 300, 0), "WALL": (RW + 340, 0, 310, 0),
    "MAILBOX": (RW + 190, 0, 60, 0), "POLE": (RW + 380, 0, 40, 0),
    "JUMPSIGN": (RW + 300, 0, 150, 0),
    "BIRCH": (RW + 400, 1100, 60, 0), "POPLAR": (RW + 420, 300, 50, 0),
    "APPLE": (RW + 560, 1000, 80, 0), "DEAD": (RW + 520, 900, 40, 0),
    "STONES": (RW + 260, 900, 150, 0), "BOULDER": (RW + 520, 1200, 240, 0),
    "HEDGE": (RW + 360, 60, 320, 0), "SHRUB": (RW + 250, 900, 110, 0), "GORSE": (RW + 260, 1000, 120, 0),
    "COTTAGE": (RW + 1100, 700, 450, 0), "BUNGALOW": (RW + 1150, 700, 500, 0),
    "SHED": (RW + 800, 900, 240, 0), "WINDPUMP": (RW + 1000, 1200, 200, 0),
    "TRACTOR": (RW + 1000, 1400, 240, 0), "SHEEP": (RW + 700, 1500, 130, 0),
    "FINGER": (RW + 250, 0, 60, 0),
    "SHOP1": (RW + 780, 60, 520, 0), "SHOP2": (RW + 780, 60, 520, 0), "SHOP3": (RW + 780, 60, 520, 0),
    "TERRACE": (RW + 960, 60, 700, 0), "FLATS": (RW + 880, 60, 600, 0),
    "CHURCH": (RW + 1000, 100, 600, 0), "PETROL": (RW + 1000, 60, 700, 0),
    # (placed by the track itself: the tunnels' wall lamps, on the divided road)
    "TLAMP": (int(RW * 73 / 64) + 20, 0, 0, 0),     # (in the wall: render.s45's TUN_U)
    "RAMP": (0, 0, 0, 0), "KEEP": (0, 0, 120, 0), "RAIL": (0, 0, 40, 0),
    "GANTRY": (0, 0, 0, 0),
}


def sprite_defs():
    defs = [
        ("PINE", 40, 88, 520, lambda: draw_pine(40, 88, 1)),
        ("PINE2", 36, 72, 440, lambda: draw_pine(36, 72, 7)),
        ("PALM", 56, 88, 700, lambda: draw_palm(56, 88, 3)),
        ("OAK", 60, 72, 760, lambda: draw_oak(60, 72, 5)),
        ("BUSH", 44, 22, 420, lambda: draw_bush(44, 22, 9)),
        ("ROCK", 48, 30, 520, lambda: draw_rock(48, 30, 11)),
        ("SIGNL", 40, 40, 360, lambda: draw_sign(40, 40, -1)),
        ("SIGNR", 40, 40, 360, lambda: draw_sign(40, 40, 1)),
        ("LAMPL", 28, 96, 380, lambda: draw_lamp(28, 96, 1)),
        ("LAMPR", 28, 96, 380, lambda: draw_lamp(28, 96, -1)),
        ("BILL1", 96, 52, 1100, lambda: draw_billboard(96, 52, "MEGA65", ((20, 40, 120, 255), (255, 255, 255, 255), (230, 40, 40, 255)))),
        ("BILL2", 96, 52, 1100, lambda: draw_billboard(96, 52, "CC45", ((250, 200, 30, 255), (30, 30, 30, 255), (20, 20, 20, 255)))),
        ("BILL3", 96, 52, 1100, lambda: draw_billboard(96, 52, "TURBO", ((220, 30, 40, 255), (255, 240, 200, 255), (255, 255, 255, 255)))),
        ("BARRIER", 64, 16, 640, lambda: draw_barrier(64, 16)),
        ("HOUSE", 96, 64, 1500, lambda: draw_house(96, 64)),
        ("BARN", 96, 72, 1700, lambda: draw_barn(96, 72)),
        ("SILO", 28, 96, 420, lambda: draw_silo(28, 96)),
        ("HAY", 48, 28, 620, lambda: draw_hay(48, 28)),
        ("COW", 48, 32, 480, lambda: draw_cow(48, 32)),
        ("FENCEW", 64, 20, 640, lambda: draw_fence_wood(64, 20)),
        ("PICKET", 64, 18, 640, lambda: draw_picket(64, 18)),
        ("WALL", 64, 16, 660, lambda: draw_wall(64, 16, 13)),
        ("MAILBOX", 16, 28, 150, lambda: draw_mailbox(16, 28)),
        ("POLE", 24, 120, 330, lambda: draw_pole(24, 120)),
        ("JUMPSIGN", 40, 60, 380, lambda: draw_jumpsign(40, 60)),
        ("BIRCH", 36, 84, 440, lambda: draw_birch(36, 84, 21)),
        ("POPLAR", 24, 96, 360, lambda: draw_poplar(24, 96)),
        ("APPLE", 48, 52, 560, lambda: draw_apple(48, 52, 23)),
        ("DEAD", 48, 64, 600, lambda: draw_deadtree(48, 64, 25)),
        ("STONES", 48, 16, 520, lambda: draw_rocks(48, 16, 27, False)),
        ("BOULDER", 48, 40, 640, lambda: draw_rocks(48, 40, 29, True)),
        ("HEDGE", 64, 18, 680, lambda: draw_hedge(64, 18, 31)),
        ("SHRUB", 32, 24, 340, lambda: draw_shrub(32, 24, 33, (250, 230, 240))),
        ("GORSE", 32, 20, 360, lambda: draw_shrub(32, 20, 35, (250, 210, 40))),
        ("COTTAGE", 64, 52, 1000, lambda: draw_cottage(64, 52, (214, 206, 186), (150, 70, 52))),
        ("BUNGALOW", 72, 40, 1100, lambda: draw_cottage(72, 40, (236, 226, 196), (70, 74, 86))),
        ("SHED", 40, 30, 520, lambda: draw_shed(40, 30)),
        ("WINDPUMP", 40, 88, 600, lambda: draw_windpump(40, 88)),
        ("TRACTOR", 48, 36, 520, lambda: draw_tractor(48, 36)),
        ("SHEEP", 32, 22, 300, lambda: draw_sheep(32, 22, 37)),
        ("FINGER", 28, 44, 300, lambda: draw_fingerpost(28, 44)),
        ("SHOP1", 72, 80, 1100, lambda: draw_shop(72, 80, (226, 214, 180), (200, 40, 40), "BAKERY")),
        ("SHOP2", 72, 80, 1100, lambda: draw_shop(72, 80, (180, 200, 214), (40, 90, 170), "CAFE")),
        ("SHOP3", 72, 80, 1100, lambda: draw_shop(72, 80, (214, 190, 200), (40, 140, 70), "BOOKS")),
        ("TERRACE", 96, 64, 1500, lambda: draw_terrace(96, 64)),
        ("FLATS", 72, 120, 1300, lambda: draw_flats(72, 120)),
        ("CHURCH", 72, 112, 1300, lambda: draw_church(72, 112)),
        ("PETROL", 96, 56, 1500, lambda: draw_petrol(96, 56)),
        ("TLAMP", 12, 44, 150, lambda: draw_tlamp(12, 44)),
        ("RAMP", 64, 28, 470, lambda: draw_ramp(64, 28)),
        ("KEEP", 24, 44, 260, lambda: draw_keep(24, 44)),
        ("RAIL", 64, 14, 660, lambda: draw_rail(64, 14)),
        ("GANTRY", 240, 60, 2 * RW + 520, lambda: draw_gantry(240, 60)),
    ]
    # cars: a body per livery * 3 + view (left, straight, right), and the
    # axles (shadow and tyres) per tyre type * 6 + view * 2 + tread frame;
    # an axle is as wide as a body in the world (it is drawn the same size)
    for li, (model, col) in enumerate(LIVERIES):
        for v, vn in ((-1, "L"), (0, "S"), (1, "R")):
            defs.append(("CAR_%d_%s" % (li, vn), CAR_TW, CAR_TH, CAR_WW[model],
                         (lambda m=model, c=col, vv=v: draw_car(CAR_TW, CAR_TH, m, c, vv))))
    # the opponents' bodies, a wing baked in (liveries 1-7: livery 0 is the
    # player's alone)
    for li, (model, col) in enumerate(LIVERIES):
        if li == 0:
            continue
        for v, vn in ((-1, "L"), (0, "S"), (1, "R")):
            defs.append(("CARW_%d_%s" % (li, vn), CAR_TW, CAR_TH, CAR_WW[model],
                         (lambda m=model, c=col, vv=v, wg=AI_WING[li]: draw_car(CAR_TW, CAR_TH, m, c, vv, wg))))
    # the player's wing layers: style (1-3) * 3 + view (as wide as a body in
    # the world: drawn the same width)
    for st in (1, 2, 3):
        for v, vn in ((-1, "L"), (0, "S"), (1, "R")):
            defs.append(("WING_%d_%s" % (st, vn), CAR_TW, WING_H, 360,
                         (lambda ss=st, vv=v: draw_wing_layer(CAR_TW, WING_H, ss, vv))))
    for ti, fat in enumerate(TYRES):
        for v, vn in ((-1, "L"), (0, "S"), (1, "R")):
            for wf in (0, 1):
                defs.append(("AXLE_%d_%s%d" % (ti, vn, wf), CAR_TW, AXLE_H, 360,
                             (lambda f=fat, vv=v, ww=wf: draw_axle(CAR_TW, AXLE_H, vv, ww, f))))
    defs += [
        ("BGO", 112, 56, 0, lambda: draw_banner(112, 56, "GO!", 46)),
        ("BFINAL", 200, 36, 0, lambda: draw_banner(200, 36, "FINAL LAP", 28)),
        ("BFINISH", 180, 44, 0, lambda: draw_banner(180, 44, "FINISH", 36)),
        ("BTITLE", 240, 50, 0, lambda: draw_banner(240, 50, "VELOCITY 65", 30)),
        ("BPAUSE", 140, 36, 0, lambda: draw_banner(140, 36, "PAUSED", 28)),
        ("BONNET", 160, 44, 0, lambda: draw_bonnet(160, 44)),
        # the route map's markers: a circuit locked, open, won; the cursor
        ("MK_LOCK", 12, 12, 0, lambda: draw_marker(12, (92, 92, 100), None)),
        ("MK_OPEN", 12, 12, 0, lambda: draw_marker(12, (250, 206, 40), None)),
        ("MK_DONE", 12, 12, 0, lambda: draw_marker(12, (60, 200, 80), (255, 255, 255))),
        ("MK_SEL", 20, 20, 0, lambda: draw_ring(20)),
        # the start lights, the nitro's flames, the rear-view mirror and the
        # cars in it, a streak of rain
        ("LIGHTS", 112, 30, 0, lambda: draw_lights(112, 30)),
        ("LAMP_OFF", 14, 14, 0, lambda: draw_startlamp(14, False)),
        ("LAMP_ON", 14, 14, 0, lambda: draw_startlamp(14, True)),
        ("FLAME_0", 20, 20, 0, lambda: draw_flame(20, 20, 0)),
        ("FLAME_1", 20, 20, 0, lambda: draw_flame(20, 20, 1)),
        ("MIRROR", 104, 22, 0, lambda: draw_mirror(104, 22)),
    ]
    for li, (model, col) in enumerate(LIVERIES):
        defs.append(("FRONT_%d" % li, 32, 16, 380, (lambda m=model, c=col: draw_front(32, 16, m, c))))
    defs.append(("RAIN", 1, 16, 0, lambda: draw_rain(1, 16)))
    # the wet road: spray behind the cars, their reflections below them
    defs.append(("SPRAY", 40, 18, 440, lambda: draw_spray(40, 18)))
    defs.append(("REFL", 40, 22, 380, lambda: draw_reflection(40, 22)))
    return defs


def draw_lights(w, h):
    """The start lights' panel: dark, five sockets (the lamps are drawn
    over it), on a bar."""
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    rect(d, 2, 0, w - 3, 3, (60, 60, 66, 255))
    d.rounded_rectangle([SS, 3 * SS, (w - 2) * SS, (h - 1) * SS], radius=5 * SS, fill=(20, 20, 24, 255),
                        outline=(96, 96, 104, 255), width=SS)
    for i in range(5):
        cx = w / 2 - 44 + i * 22
        ellipse(d, cx - 8, h / 2 - 7, cx + 8, h / 2 + 9, (6, 6, 8, 255))
    return down(im, w, h)


def draw_startlamp(w, on):
    """A start light, off or lit (red)."""
    im = canvas(w, w)
    d = ImageDraw.Draw(im)
    if on:
        ellipse(d, 0, 0, w - 1, w - 1, (200, 16, 16, 255))
        ellipse(d, 2, 2, w - 3, w - 3, (255, 50, 36, 255))
        ellipse(d, w * 0.28, w * 0.24, w * 0.62, w * 0.56, (255, 196, 170, 255))
    else:
        ellipse(d, 0, 0, w - 1, w - 1, (40, 8, 10, 255))
        ellipse(d, w * 0.25, w * 0.2, w * 0.5, w * 0.44, (84, 30, 30, 255))
    return down(im, w, w)


def draw_flame(w, h, frame):
    """A nitro flame out of an exhaust, seen from behind: a white-blue core,
    a halo of orange, flickering (two frames)."""
    rnd = random.Random(41 + frame)
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    for k, (col, f) in enumerate((((250, 120, 20), 1.0), ((255, 200, 60), 0.72), ((150, 200, 255), 0.46),
                                  ((240, 248, 255), 0.26))):
        rw = w / 2 * f * (0.9 + 0.2 * rnd.random())
        rh = h / 2 * f * (0.9 + 0.2 * rnd.random())
        cx, cy = w / 2 + rnd.uniform(-0.6, 0.6), h * 0.55
        ellipse(d, cx - rw, cy - rh * 1.2, cx + rw, cy + rh * 0.8, col + (255,))
    return down(im, w, h)


def draw_mirror(w, h):
    """The rear-view mirror: a dark frame round the road behind - the sky
    above its horizon, the road narrowing to it, grass beside."""
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, w * SS - 1, h * SS - 1], radius=5 * SS, fill=(22, 22, 26, 255))
    x0, y0, x1, y1 = 3, 2, w - 4, h - 3
    hz = 9
    vgrad(d, x0, y0, x1, hz, (150, 164, 180), (184, 190, 198))   # (greyish: not glass by night)
    rect(d, x0, hz, x1, y1, (70, 128, 58, 255))
    poly(d, [(w / 2 - 5, hz), (w / 2 + 5, hz), (w / 2 + 44, y1), (w / 2 - 44, y1)], (98, 98, 106, 255))
    for k in range(3):
        yy = hz + 1.5 + k * 3.4
        rect(d, w / 2 - 0.4 - k * 0.2, yy, w / 2 + 0.4 + k * 0.2, yy + 1.4 + k * 0.4, (230, 230, 222, 255))
    d.rounded_rectangle([x0 * SS, y0 * SS, (x1 + 1) * SS, (y1 + 1) * SS], radius=3 * SS, outline=(70, 70, 78, 255),
                        width=SS)
    return down(im, w, h)


def draw_front(w, h, model, body):
    """A car from the front, small (in the mirror): its body, the
    windscreen, the headlights and the grille."""
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    light, dark = tuple(clamp8(v * 1.2 + 20) for v in body), shade(body, 0.55)
    roof = {"GT": 0.30, "HATCH": 0.16, "ROADSTER": 0.46, "WEDGE": 0.34}[model]
    rect(d, 2, h - 4, 7, h - 1, (16, 16, 18, 255))
    rect(d, w - 8, h - 4, w - 3, h - 1, (16, 16, 18, 255))
    poly(d, [(1, h - 3), (2, h * 0.5), (w * 0.22, h * roof + 1), (w * 0.78, h * roof + 1), (w - 2, h * 0.5),
             (w - 1, h - 3)], body + (255,))
    poly(d, [(w * 0.26, h * 0.5), (w * 0.3, h * roof + 2), (w * 0.7, h * roof + 2), (w * 0.74, h * 0.5)],
         (40, 50, 66, 255))
    rect(d, 2, h * 0.5, w - 3, h * 0.56, light + (255,))
    for cx in (w * 0.17, w * 0.83):
        ellipse(d, cx - 3.4, h * 0.6, cx + 3.4, h * 0.6 + 3.2, (255, 252, 230, 255))
    rect(d, w * 0.36, h * 0.66, w * 0.64, h * 0.82, (24, 24, 28, 255))
    rect(d, 2, h - 3, w - 3, h - 2, dark + (255,))
    return down(im, w, h)


def draw_rain(w, h):
    """A streak of rain (one column; render.s45's rd_rain copies it)."""
    im = Image.new("RGBA", (w, h))
    im.putdata([(178 + y * 3, 192 + y * 2, 212 + y * 2, 255) for y in range(h) for _ in range(w)])
    return im


def draw_tlamp(w, h):
    """A tunnel's wall lamp: a glowing box high up (the rest is clear: it
    stands on the road)."""
    im = canvas(w, h)
    d = ImageDraw.Draw(im)
    rect(d, 0, 0, w - 1, 8, (60, 60, 64, 255))
    rect(d, 1, 1, w - 2, 7, (255, 236, 170, 255))
    rect(d, 2, 2, w - 3, 4, (255, 252, 230, 255))
    return down(im, w, h)


def draw_spray(w, h):
    """The spray thrown up behind a car in the rain: a mist of pale drops,
    thick at the bottom, thin above (one pixel in two or three clear, so
    the car shows through)."""
    rnd = random.Random(53)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = im.load()
    for y in range(h):
        t = y / (h - 1)                         # 0 top .. 1 bottom
        for x in range(w):
            u = abs(x - (w - 1) / 2) / (w / 2)
            dens = (0.06 + 0.34 * t) * (1 - u * u)
            if rnd.random() < dens:
                c = 196 + rnd.randrange(40)
                px[x, y] = (c, c + 4, c + 10, 255)
    return im


def draw_reflection(w, h):
    """A car's reflection on the wet road, below it: a dark smear of the
    car and two red streaks from its tail lights, fading downwards (with
    gaps, the road between)."""
    rnd = random.Random(59)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = im.load()
    for y in range(h):
        t = y / (h - 1)                         # 0 top (at the car) .. 1
        for x in range(w):
            u = abs(x - (w - 1) / 2) / (w / 2)
            tail = min(abs(u - 0.62), 0.5) < 0.09
            if tail and rnd.random() < 0.9 - 0.7 * t:
                px[x, y] = (250, 60 + int(60 * t), 50, 255)
            elif u < 0.85 and (x + y) % 2 == 0 and rnd.random() < 0.7 - 0.7 * t:
                px[x, y] = (40, 42, 50, 255)
    return im


def draw_marker(w, fill, tick):
    im = canvas(w, w)
    d = ImageDraw.Draw(im)
    ellipse(d, 0, 0, w - 1, w - 1, (20, 20, 24, 255))
    ellipse(d, 1.5, 1.5, w - 2.5, w - 2.5, fill + (255,))
    ellipse(d, 3, 2.5, w * 0.55, w * 0.45, tuple(min(255, v + 60) for v in fill) + (255,))
    if tick:
        d.line(S(3.2, 6.2, 5.2, 8.4, 9, 3.6), fill=tick + (255,), width=SS * 2)
    return down(im, w, w)


def draw_ring(w):
    im = canvas(w, w)
    d = ImageDraw.Draw(im)
    ellipse(d, 0, 0, w - 1, w - 1, (20, 20, 24, 255))
    ellipse(d, 1.5, 1.5, w - 2.5, w - 2.5, (255, 255, 255, 255))
    ellipse(d, 3.5, 3.5, w - 4.5, w - 4.5, (20, 20, 24, 255))
    ellipse(d, 4.5, 4.5, w - 5.5, w - 5.5, (0, 0, 0, 0))
    return down(im, w, w)


# =============================================================================
# sky, panoramas and the road
# =============================================================================

def periodic_noise(rnd, n, terms, amp0, decay):
    """A random curve over 0..n that wraps around (sum of whole-period sines)."""
    comps = []
    a = amp0
    for k in terms:
        comps.append((k, a * rnd.uniform(0.6, 1.0), rnd.uniform(0, math.tau)))
        a *= decay
    return [sum(a * math.sin(math.tau * k * x / n + p) for k, a, p in comps) for x in range(n)]


def sky_colour(row):
    """The sky at a screen row (above the horizon: a gradient)."""
    t = max(0.0, min(1.0, row / (HZ + 4)))
    if t < 0.55:
        return lerp(SKY_TOP, SKY_MID, t / 0.55)
    return lerp(SKY_MID, HAZE, ((t - 0.55) / 0.45) ** 1.2)


def make_pano_far():
    rnd = random.Random(42)
    im = Image.new("RGBA", (PANO_W, PANO_FAR_H))
    px = im.load()
    for y in range(PANO_FAR_H):
        c = sky_colour(PANO_FAR_Y + y)
        for x in range(PANO_W):
            px[x, y] = tuple(clamp8(v) for v in c) + (255,)
    # clouds: soft flat-bottomed puffs
    d = ImageDraw.Draw(im)
    for k in range(9):
        cx = rnd.uniform(0, PANO_W)
        cy = rnd.uniform(4, 16)
        for j in range(7):
            ox = rnd.uniform(-22, 22)
            r = rnd.uniform(4, 9)
            for wrap in (-PANO_W, 0, PANO_W):
                x = cx + ox + wrap
                d.ellipse([x - r * 1.6, cy - r * 0.7, x + r * 1.6, cy + r * 0.5],
                          fill=(244, 246, 250, 255) if j % 3 else (226, 232, 242, 255))
    # two mountain ranges: far (hazy, snow capped) and near (darker)
    far = periodic_noise(rnd, PANO_W, [1, 2, 3, 5, 8, 13, 21, 34], 14, 0.62)
    near = periodic_noise(rnd, PANO_W, [2, 3, 4, 7, 11, 17, 29, 47], 9, 0.6)
    for x in range(PANO_W):
        top_f = int(PANO_FAR_H * 0.48 - far[x])
        top_n = int(PANO_FAR_H * 0.78 - near[x])
        slope_f = far[(x + 1) % PANO_W] - far[x]
        slope_n = near[(x + 1) % PANO_W] - near[x]
        for y in range(max(0, top_f), PANO_FAR_H):
            depth = (y - top_f) / PANO_FAR_H
            base = (122, 136, 170) if slope_f > 0 else (104, 116, 152)
            if y - top_f < 4 and top_f < PANO_FAR_H * 0.42:
                base = (236, 240, 248) if slope_f > 0 else (196, 206, 226)
            c = lerp(base, HAZE, 0.25 + depth * 0.6)
            px[x, y] = tuple(clamp8(v) for v in c) + (255,)
        for y in range(max(0, top_n), PANO_FAR_H):
            depth = (y - top_n) / PANO_FAR_H
            base = (74, 96, 120) if slope_n > 0 else (60, 80, 108)
            c = lerp(base, HAZE, 0.18 + depth * 0.8)
            px[x, y] = tuple(clamp8(v) for v in c) + (255,)
    return im


def make_pano_near():
    rnd = random.Random(7)
    im = Image.new("RGBA", (PANO_W, PANO_NEAR_H), (0, 0, 0, 0))
    px = im.load()
    hills = periodic_noise(rnd, PANO_W, [1, 3, 4, 6, 9], 5, 0.6)
    trees = [rnd.random() for _ in range(PANO_W)]
    for x in range(PANO_W):
        top = int(PANO_NEAR_H * 0.55 - hills[x])
        for y in range(max(0, top), PANO_NEAR_H):
            t = (y - top) / PANO_NEAR_H
            c = lerp((70, 128, 70), (120, 168, 108), t)
            px[x, y] = tuple(clamp8(v) for v in c) + (255,)
    # rows of trees on the hills
    for x0 in range(0, PANO_W, 3):
        if trees[x0] < 0.55:
            continue
        top = int(PANO_NEAR_H * 0.55 - hills[x0])
        hgt = 2 + int(trees[x0] * 4)
        for dx in range(-1, 2):
            x = (x0 + dx) % PANO_W
            for y in range(max(0, top - hgt + abs(dx)), top + 1):
                px[x, y] = (40, 88, 52, 255)
    return im


def make_pano_far_sea():
    """The coast's far panorama: sky and clouds, the sea to the horizon with
    a few low islands on it."""
    rnd = random.Random(99)
    im = Image.new("RGBA", (PANO_W, PANO_FAR_H))
    px = im.load()
    sea_y = PANO_FAR_H - 9                       # (screen row HZ - 3)
    for y in range(PANO_FAR_H):
        c = sky_colour(PANO_FAR_Y + y)
        if y >= sea_y:
            t = (y - sea_y) / (PANO_FAR_H - sea_y)
            c = lerp((96, 150, 196), (40, 104, 168), t)
        for x in range(PANO_W):
            px[x, y] = tuple(clamp8(v) for v in c) + (255,)
    d = ImageDraw.Draw(im)
    for k in range(7):
        cx = rnd.uniform(0, PANO_W)
        cy = rnd.uniform(4, 18)
        for j in range(6):
            ox = rnd.uniform(-24, 24)
            r = rnd.uniform(4, 8)
            for wrap in (-PANO_W, 0, PANO_W):
                x = cx + ox + wrap
                d.ellipse([x - r * 1.7, cy - r * 0.6, x + r * 1.7, cy + r * 0.45],
                          fill=(246, 244, 240, 255) if j % 3 else (228, 226, 232, 255))
    isl = periodic_noise(rnd, PANO_W, [2, 3, 5, 7, 11, 19], 6, 0.65)
    for x in range(PANO_W):
        h = int(isl[x] - 2)
        for y in range(max(0, sea_y - h), sea_y):
            px[x, y] = tuple(clamp8(v) for v in lerp((118, 140, 160), HAZE, 0.35)) + (255,)
    # glints on the water
    for k in range(260):
        x, y = rnd.randrange(PANO_W), rnd.randrange(sea_y + 1, PANO_FAR_H)
        for dx in range(rnd.randrange(2, 5)):
            px[(x + dx) % PANO_W, y] = (214, 234, 246, 255)
    return im


def make_pano_near_sea():
    """The coast's near panorama: dunes and headlands, the sea between."""
    rnd = random.Random(17)
    im = Image.new("RGBA", (PANO_W, PANO_NEAR_H), (0, 0, 0, 0))
    px = im.load()
    hills = periodic_noise(rnd, PANO_W, [1, 2, 3, 5, 8], 6, 0.6)
    for x in range(PANO_W):
        top = int(PANO_NEAR_H - 4 - max(0, hills[x] + 2))
        for y in range(max(0, top), PANO_NEAR_H):
            t = (y - top) / PANO_NEAR_H
            c = lerp((200, 184, 132), (150, 156, 96), min(1, t * 2.5)) if hills[x] < 3 else \
                lerp((118, 128, 92), (90, 120, 76), t)
            px[x, y] = tuple(clamp8(v) for v in c) + (255,)
    return im


# road variants: light / dark segments, the start line (checks of either
# phase), ploughed fields either side (light / dark), a driveway off to
# the left / the right, a jump's ramp (light / dark), the water in a jump's
# gap, the far bank's face, a divided road (two carriageways round a grass
# island, on a road 1.8 times as wide: light / dark), and its river: a
# one-lane bridge on the left carriageway, water elsewhere (light / dark),
# a town's street: kerbs and pavements instead of verges (light / dark),
# a tunnel: walkways, then its walls (lit by its lamps / between them)
NVAR = 20
V_FARM, V_DRIVE_L, V_DRIVE_R = 4, 6, 7
V_RAMP, V_WATER, V_BANK, V_SPLIT, V_BRIDGE, V_TOWN, V_TUNNEL = 8, 10, 11, 12, 14, 16, 18
# the variant a far segment (a single row) is drawn as: never the dark one
VAR_ONE = [0, 0, 2, 2, 4, 4, 6, 7, 8, 8, 10, 11, 12, 12, 14, 14, 16, 16, 18, 18]
# a tunnel: its half-width in road half-widths (render.s45: 1 + 1/8 + 1/64),
# its ceiling's height, the rock face round its mouth (world units)
TUN_U = 73 / 64
TUN_H = 1000
PORTAL_H = 2400
CEIL_RGB = [(176, 168, 146), (66, 66, 70)]     # lit by a lamp / between
UWALL_RGB = [(118, 114, 106), (84, 82, 78)]    # the walls' upper part, concrete
FACE_RGB = (116, 106, 92)
# where the divided road's parts are, in its own half-widths
SPLIT_W = 1.8
ISLAND = 0.2
DECK = (-0.85, -0.35)


def water_rgb(u, light):
    w = (40, 96, 176) if light else (34, 84, 160)
    return lerp(w, (90, 150, 210), 0.35) if int((u + 2) * 23) % 5 == 0 else w


def profile_rgb(u, variant):
    """The colour at u half-widths from the middle of the road."""
    a = abs(u)
    light = variant in (0, 2, 4, 6, 7, 8, 12, 14, 16, 18)
    drive = (variant == V_DRIVE_L and u < 0) or (variant == V_DRIVE_R and u > 0)
    asphalt = (106, 106, 114) if light else (96, 96, 104)
    if variant == V_WATER:
        return water_rgb(u, True)
    if variant in (V_TUNNEL, V_TUNNEL + 1):
        if a >= TUN_U + 0.035:
            return (196, 190, 170) if light else (150, 146, 132)       # the walls' tiles
        if a >= TUN_U:
            return (46, 46, 50)                                        # the skirting
        if a >= 1.0:
            return (166, 162, 152) if light else (148, 144, 136)       # the walkways
        if 0.90 <= a < 0.94:
            return (226, 226, 220)
        if light and abs(a - 1 / 3) < 0.022:
            return (236, 236, 228)
        return (86, 86, 92) if light else (76, 76, 82)
    if variant == V_BANK:
        return (122, 92, 60) if int((u + 2) * 9) % 3 else (104, 78, 50)
    if variant in (V_BRIDGE, V_BRIDGE + 1):
        lo, hi = DECK
        if lo <= u < hi:
            if u < lo + 0.03 or u >= hi - 0.03:
                return (196, 196, 190)                   # the kerbs
            if abs(u - (lo + hi) / 2) < 0.012 and light:
                return (236, 236, 228)
            return asphalt
        return water_rgb(u, light)
    if variant in (V_TOWN, V_TOWN + 1) and a >= 1.0:
        if a < 1.04:
            return (206, 204, 196)                         # the kerb
        slab = (178, 174, 166) if light else (168, 164, 156)
        return shade(slab, 0.85) if int((a - 1.04) * 30) % 4 == 0 else slab
    if variant in (V_RAMP, V_RAMP + 1):
        if a < 1.0:
            if a >= 0.88:                                 # hazard edges
                return (250, 204, 30) if light else (30, 30, 30)
            return (186, 156, 104) if light else (168, 138, 90)   # planks
    if variant in (V_SPLIT, V_SPLIT + 1) and a < 1.0:
        if a < ISLAND:
            if a >= ISLAND - 0.04:
                return (236, 236, 236) if light else (204, 30, 30)  # the island's kerb
            return (76, 156, 62) if light else (66, 140, 54)
        if ISLAND + 0.01 <= a < ISLAND + 0.03 or 0.95 <= a < 0.97:
            return (226, 226, 220)
        if light and abs(a - (1 + ISLAND) / 2) < 0.013:
            return (236, 236, 228)
        return asphalt
    if a < 1.0:
        if variant in (2, 3) and a < 0.995:
            sq = int(math.floor((u + 1) * 8))
            return (240, 240, 240) if (sq + variant) % 2 else (24, 24, 28)
        if 0.90 <= a < 0.94 and not drive:
            return (226, 226, 220)
        if light and abs(a - 1 / 3) < 0.022:
            return (236, 236, 228)
        for lane in (-2 / 3, 0, 2 / 3):
            for tyre in (-0.14, 0.14):
                if abs(u - lane - tyre) < 0.05:
                    return shade(asphalt, 0.9)
        return asphalt
    if drive:
        # gravel, with two darker wheel tracks along it (across the screen)
        return (170, 156, 118) if (a * 40) % 3 > 0.6 else (150, 136, 100)
    rumble = 1.07 if variant in (V_SPLIT, V_SPLIT + 1) else 1.13
    if a < rumble:
        return (236, 236, 236) if light else (204, 30, 30)
    if a < rumble + 0.07:
        return (150, 140, 104) if light else (138, 128, 94)
    if variant in (4, 5):
        # ploughed furrows running along the road, a grass verge first
        if a < 1.26:
            return (88, 150, 60)
        f = int((a - 1.26) / 0.05) % 2
        if variant == 4:
            return (124, 92, 56) if f else (100, 72, 44)
        return (116, 86, 52) if f else (94, 68, 42)
    return (68, 152, 56) if light else (58, 136, 48)


def tunnel_colours():
    """The tunnels' ceiling (lit, between the lamps) and the rock round
    their mouths, per mip level (as hazy as the road there)."""
    ceil = ([[lerp(CEIL_RGB[l], HAZE, FOG[m] / 2) for m in range(NMIP)] for l in (0, 1)]
            + [[lerp(UWALL_RGB[l], HAZE, FOG[m] / 2) for m in range(NMIP)] for l in (0, 1)])
    face = [lerp(FACE_RGB, HAZE, FOG[m]) for m in range(NMIP)]
    return ceil, face


def make_profiles():
    """Each variant's mip levels (lists of rgb), plus the haze for each."""
    out = []
    for variant in range(NVAR):
        n0 = int(2 * PSPAN * 256)
        base = [profile_rgb((i + 0.5) / 256 - PSPAN, variant) for i in range(n0)]
        mips = []
        for m in range(NMIP):
            f = 1 << m
            row = []
            for j in range(n0 // f):
                acc = [0, 0, 0]
                for k in range(f):
                    c = base[j * f + k]
                    acc = [acc[0] + c[0], acc[1] + c[1], acc[2] + c[2]]
                c = tuple(v / f for v in acc)
                row.append(lerp(c, HAZE, FOG[m]))
            mips.append(row)
        out.append(mips)
    return out



# =============================================================================
# the circuit
# =============================================================================
#
# A section eases a curve in, holds it and eases it out, while the height
# follows a smooth (cosine) step to its new level; its theme says what grows
# or stands by the road. Curves are in quarter world units per segment
# squared (render.s45), heights in world units. The lap closes: the last
# section brings the height back to where the first starts. The track is
# 9 arrays of TRK_MAX bytes in attic RAM: curve, height low, high, road
# variant, scenery left, right (sprite + 1), their x / 32, road width (64 =
# RW).

TRK_ADDR = 0x8100000
TRK_MAX = 4096
START_SEG = 24
JUMP_LEN, FORK_LEN = 64, 96
MAX_FEAT = 4
FEAT_JUMP, FEAT_FORK = 1, 2

# the circuits: (name, look, panorama, sections); each section (enter,
# hold, leave, curve, dy, theme). Every one starts and ends on the "start"
# theme (the grid at START_SEG). Looks: 0 day, 1 dusk, 2 overcast, 3 night
# (a palette each); panoramas: 0 the mountains, 1 the coast
LOOKS = ["DAY", "DUSK", "OVERCAST", "NIGHT", "FOG", "RAIN"]
LOOK_FOG, LOOK_RAIN = 4, 5
W_RAIN, W_FOG = 1, 2      # the weather (the circuit's header), by its look
CIRCUITS = [
    ("MEGA CIRCUIT", 0, 0, [
        (4, 40, 4, 0, 0, "start"), (30, 60, 30, 8, 600, "city"), (10, 120, 10, 0, 0, "farm"),
        (0, JUMP_LEN, 0, 0, 0, "jump"), (25, 40, 25, -12, -900, "forest"), (20, 30, 20, 10, 400, "forest"),
        (10, 80, 10, 0, 2200, "rocks"), (8, 44, 8, 5, 0, "tunnel"), (20, 40, 20, -6, -2400, "rocks"), (30, 70, 30, 16, 0, "coast"),
        (20, 40, 20, -16, 300, "coast"), (25, 50, 25, 6, -600, "coast"), (0, FORK_LEN, 0, 0, 0, "fork"),
        (10, 110, 10, 3, 1400, "farm"), (15, 30, 15, -20, -1400, "field"), (20, 40, 20, 12, 1800, "forest"),
        (10, 40, 10, 0, -1600, "forest"), (30, 40, 30, -10, 0, "city"), (20, 60, 20, 4, -800, "city"),
        (5, 60, 5, 0, 0, "start")]),
    ("FARMLANDS", 0, 0, [
        (4, 40, 4, 0, 0, "start"), (20, 60, 20, 6, 300, "field"), (10, 140, 10, 0, 0, "farm"),
        (0, JUMP_LEN, 0, 0, 0, "jump"), (25, 40, 25, -10, -500, "field"), (10, 120, 10, 4, 800, "farm"),
        (20, 50, 20, -8, -600, "forest"), (15, 40, 15, 12, 0, "field"), (0, JUMP_LEN, 0, 0, 0, "jump"),
        (10, 100, 10, -4, 400, "farm"), (20, 40, 20, 9, -400, "field"), (5, 60, 5, 0, 0, "start")]),
    ("HARBOUR BAY", 0, 1, [
        (4, 40, 4, 0, 0, "start"), (30, 50, 30, 10, 200, "city"), (20, 60, 20, -14, -300, "coast"),
        (0, FORK_LEN, 0, 0, 0, "fork"), (25, 40, 25, 16, 0, "coast"), (20, 60, 20, -6, 500, "city"),
        (30, 50, 30, 12, -400, "coast"), (20, 40, 20, -9, 0, "coast"), (5, 60, 5, 0, 0, "start")]),
    ("PINE RIDGE", LOOK_FOG, 0, [
        (4, 40, 4, 0, 0, "start"), (20, 40, 20, -10, 1200, "forest"), (15, 30, 15, 14, 1600, "rocks"),
        (0, JUMP_LEN, 0, 0, 0, "jump"), (20, 40, 20, -16, -1400, "forest"), (10, 60, 10, 8, 2000, "rocks"),
        (8, 50, 8, -6, 0, "tunnel"), (25, 40, 25, -12, -2200, "forest"), (20, 50, 20, 10, -800, "field"), (15, 40, 15, -14, 600, "forest"),
        (5, 60, 5, 0, 0, "start")]),
    ("SUNSET COAST", 1, 1, [
        (4, 40, 4, 0, 0, "start"), (30, 70, 30, 12, 0, "coast"), (20, 40, 20, -18, 400, "coast"),
        (0, JUMP_LEN, 0, 0, 0, "jump"), (20, 60, 20, 8, -400, "field"), (0, FORK_LEN, 0, 0, 0, "fork"),
        (30, 50, 30, -14, 300, "coast"), (25, 60, 25, 6, -300, "coast"), (5, 60, 5, 0, 0, "start")]),
    ("CANYON RUN", 1, 0, [
        (4, 40, 4, 0, 0, "start"), (15, 60, 15, 0, 1800, "rocks"), (0, JUMP_LEN, 0, 0, 0, "jump"),
        (20, 30, 20, -18, -900, "rocks"), (20, 30, 20, 18, 600, "rocks"), (0, JUMP_LEN, 0, 0, 0, "jump"),
        (15, 50, 15, -8, -1500, "rocks"), (0, FORK_LEN, 0, 0, 0, "fork"), (20, 40, 20, 14, 0, "rocks"),
        (10, 60, 10, 0, 0, "tunnel"), (20, 30, 20, -12, 700, "rocks"), (5, 60, 5, 0, 0, "start")]),
    ("NIGHT CITY", 3, 0, [
        (4, 40, 4, 0, 0, "start"), (30, 60, 30, 10, 300, "city"), (20, 40, 20, -16, -300, "city"),
        (0, FORK_LEN, 0, 0, 0, "fork"), (25, 50, 25, 14, 500, "city"), (6, 36, 6, 0, 0, "tunnel"),
        (20, 60, 20, -8, -200, "city"),
        (0, JUMP_LEN, 0, 0, 0, "jump"), (30, 40, 30, 12, -300, "city"), (20, 40, 20, -10, 0, "city"),
        (5, 60, 5, 0, 0, "start")]),
    ("GRAND TOUR", LOOK_RAIN, 1, [
        (4, 40, 4, 0, 0, "start"), (30, 60, 30, 8, 600, "city"), (10, 120, 10, 0, 0, "farm"),
        (0, JUMP_LEN, 0, 0, 0, "jump"), (25, 40, 25, -12, -900, "forest"), (10, 80, 10, 0, 2200, "rocks"),
        (8, 60, 8, 4, 0, "tunnel"),
        (20, 40, 20, -6, -2400, "rocks"), (30, 70, 30, 16, 0, "coast"), (0, FORK_LEN, 0, 0, 0, "fork"),
        (20, 40, 20, -16, 300, "coast"), (15, 30, 15, -20, -1400, "field"), (0, JUMP_LEN, 0, 0, 0, "jump"),
        (20, 40, 20, 12, 1800, "forest"), (10, 40, 10, 0, -1600, "forest"), (0, FORK_LEN, 0, 0, 0, "fork"),
        (30, 40, 30, -10, 0, "city"), (20, 60, 20, 4, -800, "city"), (5, 60, 5, 0, 0, "start")]),
]
# the route map: each circuit's place (screen pixels), the routes between
# them (each to both of the next pair); layer = (c + 1) // 2
MAP_NODES = [(36, 88), (106, 48), (96, 122), (176, 38), (180, 120), (248, 50), (258, 120), (296, 86)]
MAP_ROUTES = [(0, 1), (0, 2), (1, 3), (1, 4), (2, 3), (2, 4), (3, 5), (3, 6), (4, 5), (4, 6), (5, 7), (6, 7)]
# each circuit's length in segments (0: as written): longer along the route
CIRC_LEN = [0, 1400, 1450, 1550, 1600, 1750, 1800, 2700]


def stretch(sections, n):
    """The sections with their curves and hills drawn out (the start, the
    jumps and the forks as they are) to make about n segments."""
    if not n:
        return sections
    fixed = sum(e + h + l for e, h, l, _, _, t in sections if t in ("start", "jump", "fork", "tunnel"))
    free = sum(e + h + l for e, h, l, _, _, t in sections if t not in ("start", "jump", "fork", "tunnel"))
    k = (n - fixed) / free
    return [sec if sec[5] in ("start", "jump", "fork", "tunnel") else
            (int(sec[0] * k), int(sec[1] * k), int(sec[2] * k)) + sec[3:] for sec in sections]


CIRC_SIZE = 0xA000        # each circuit's share of attic RAM from TRK_ADDR:
CIRC_HDR = 9 * 4096       #   the 9 arrays, then its header
MAP_ADDR = 0x8160000      # the route map's picture (column-major, as a buffer)

# a jump: the approach (warning signs), the ramp, the gap - water far
# below, its last segment the far bank's face - then the landing
J_RAMP = 30
J_GAP = J_RAMP + 6
J_LAND = J_GAP + 8
RAMP_RISE = 60
GAP_DEPTH = 600

# a divided road: it widens, splits round an island (a sign on its nose),
# the right carriageway has a ramp before a river, the left a one-lane
# bridge with railings; then it narrows again
F_WIDE = 20
F_SPLIT = F_WIDE + 16
F_RAMP = F_SPLIT + 12
F_RIVER = F_RAMP + 3
F_DRY = F_RIVER + 10
F_NARROW = F_DRY + 12
F_END = F_NARROW + 16
SPLIT_W64 = int(round(SPLIT_W * 64))
RAMP_X = int((1 + ISLAND) / 2 * SPLIT_W * RW)        # the right carriageway's middle


def build_track(ids, sections, seed):
    rnd = random.Random(seed).randrange
    S = lambda name: ids[name] + 1
    arrays = [[0] * TRK_MAX for _ in range(9)]
    feats = []

    def scen_x(sp):
        if not sp:
            return 0
        off, jit, _, _ = SCENERY[NAMES[sp - 1]]
        return (off + rnd(jit + 1)) // 32

    def scenery(theme, n, side, curve, var):
        """What stands at segment n of a section, on one side (0 left, 1
        right); may change the road variant. Returns (sprite + 1, variant)."""
        r = rnd(256)
        if abs(curve) >= 9 and n % 6 == 0:
            if curve > 0 and side == 0:
                return S("SIGNR"), var
            if curve < 0 and side == 1:
                return S("SIGNL"), var
        if theme == "city":
            # a town: pavements, shops, terraces, flats, a church and a
            # filling station; street lamps
            var = V_TOWN + (var & 1)
            if (n + 4 * side) % 8 == 0:
                if n == 48 and side == 0:
                    return S("CHURCH"), var
                if n == 80 and side == 1:
                    return S("PETROL"), var
                return S(("SHOP1", "SHOP2", "SHOP3", "TERRACE", "TERRACE", "FLATS")[r % 6]), var
            if (n + 5 * side) % 12 == 2:
                return S("LAMPR") if side else S("LAMPL"), var
            if r < 20:
                return S("SHRUB"), var
            return 0, var
        if theme == "forest":
            if n % 5 == 0:
                return S("PINE") if r & 1 else S("PINE2"), var
            if n % 5 == 2 and r < 90:
                return S("BIRCH"), var
            if r < 14:
                return S("SHRUB"), var
            if r < 24:
                return S("STONES"), var
            if r < 28:
                return S("DEAD"), var
        elif theme == "coast":
            if n % 5 == 0:
                return S("PALM"), var
            if r < 16:
                return S("BOULDER"), var
            if r < 32:
                return S("STONES"), var
            if r < 48:
                return S("GORSE"), var
        elif theme == "field":
            # meadows: hedges, rows of trees, a cottage with its driveway
            # now and then, sheds, sheep, a fingerpost at a lane
            c = n % 72
            cs = (n // 72) & 1
            if side == cs and c in (30, 31):
                return (S("MAILBOX") if c == 30 else 0), (V_DRIVE_R if side else V_DRIVE_L)
            if side == cs and c == 34:
                return S("COTTAGE") if (n // 72) & 2 else S("BUNGALOW"), var
            if side == cs and c == 40 and r < 160:
                return S("SHED"), var
            if side != cs and c < 50 and not n & 1:
                return S("HEDGE"), var
            if n % 9 == 4:
                return S(("OAK", "APPLE", "POPLAR", "BIRCH")[r % 4]), var
            if n % 80 == 60 and side:
                return S("FINGER"), var
            if r < 22:
                return S("SHEEP"), var
            if r < 34:
                return S("GORSE"), var
            if r < 42:
                return S("STONES"), var
        elif theme == "rocks":
            if n % 4 == 0 and r < 140:
                return S("BOULDER") if r & 1 else S("ROCK"), var
            if r < 30:
                return S("STONES"), var
            if r < 44:
                return S("PINE2"), var
            if r < 52:
                return S("DEAD"), var
            if r < 64:
                return S("GORSE"), var
        elif theme in ("jump", "fork"):
            if n % 9 == 0:
                return S(("OAK", "BIRCH", "APPLE")[r % 3]), var
            if r < 26:
                return S("SHRUB"), var
            if r < 40:
                return S("STONES"), var
        elif theme == "start":
            if n % 8 == 4:
                return S("BARRIER"), var
            if n % 16 == 8:
                return S("BILL1") + r % 3, var
        return 0, var

    def farm(n, side, var):
        """A farm every 64 segments, on alternate sides: a driveway with a
        mailbox, the house, the barn and its silo, a picket fence; then
        ploughed fields with hay bales on both sides. Across the road a
        fence or a wall, telegraph poles, cows. Returns (sprite, variant)."""
        p, fs, r = n % 64, (n // 64) & 1, rnd(256)
        field = p >= 40
        if field:
            var |= V_FARM
        if side == fs:
            if p in (6, 7):
                return (S("MAILBOX") if p == 6 else 0), (V_DRIVE_R if side else V_DRIVE_L)
            sp = {10: "HOUSE", 14: "SHED", 17: "BARN", 21: "SILO"}.get(p)
            if sp:
                return S(sp), var
            if 23 < p < 40 and not p & 1:
                return S("PICKET"), var
            if field and (p & 7) == 3 and r < 170:
                return S("HAY"), var
            if field and p == 52:
                return S("TRACTOR"), var
            return 0, var
        if not p & 1:
            return (0 if field else S("WALL") if (n // 64) & 2 else S("FENCEW")), var
        if p % 16 == 5:
            return S("POLE"), var
        if p == 33:
            return S("WINDPUMP"), var
        if not field and r < 50:
            return S("COW") if (n // 64) & 1 else S("SHEEP"), var
        return 0, var

    seg = 0
    y = 0
    for si, (enter, hold, leave, curve, dy, theme) in enumerate(sections):
        length = enter + hold + leave
        y0 = y
        if si == len(sections) - 1:
            dy = -y0                                  # close the lap
        if theme in ("jump", "fork"):
            if theme == "jump":
                feats.append((FEAT_JUMP, seg + J_RAMP - 4, seg + J_LAND + 4, 0))
            else:
                feats.append((FEAT_FORK, seg + F_WIDE, seg + F_NARROW, seg + F_RAMP))
        for n in range(length):
            if n < enter:
                c = int(curve * n / enter)
            elif n < enter + hold:
                c = curve
            else:
                c = int(curve * (length - n) / leave)
            a = (n * 128) // length
            y = y0 + int(dy * (1 - math.cos(math.pi * a / 128)) / 2)
            v = (seg // 3) & 1
            if seg == START_SEG:
                v = 2
            if seg == START_SEG + 1:
                v = 3
            w = 64
            if seg == START_SEG:
                l, r = S("GANTRY"), 0
            elif theme == "tunnel":
                # a lamp on each wall every sixth segment
                v = V_TUNNEL + (v & 1)
                l = r = S("TLAMP") if n % 6 == 1 else 0
            elif theme == "farm":
                l, v = farm(n, 0, v)
                r, v = farm(n, 1, v)
            else:
                l, v = scenery(theme, n, 0, curve, v)
                r, v = scenery(theme, n + 2, 1, curve, v)
            ol, orr = -scen_x(l), scen_x(r)
            if theme == "jump":
                y = y0
                if J_RAMP <= n < J_GAP:
                    y = y0 + (n - J_RAMP + 1) * RAMP_RISE
                    v = V_RAMP + (v & 1)
                elif J_GAP <= n < J_LAND:
                    y = y0 - GAP_DEPTH
                    v = V_BANK if n == J_LAND - 1 else V_WATER
                if n in (J_RAMP - 24, J_RAMP - 12):
                    l = r = S("JUMPSIGN")
                    ol, orr = -scen_x(l), scen_x(r)
                if J_GAP - 1 <= n < J_LAND:
                    l = r = 0
            elif theme == "fork":
                k = v & 1
                if n < F_WIDE:
                    if n == F_WIDE - 14:
                        l = r = S("JUMPSIGN")
                        ol, orr = -scen_x(l), scen_x(r)
                elif n < F_END:
                    if n < F_SPLIT:
                        w = 64 + (SPLIT_W64 - 64) * (n - F_WIDE) // (F_SPLIT - F_WIDE)
                    elif n < F_NARROW:
                        w = SPLIT_W64
                    else:
                        w = SPLIT_W64 - (SPLIT_W64 - 64) * (n - F_NARROW) // (F_END - F_NARROW)
                    v = (V_BRIDGE if F_RIVER <= n < F_DRY else V_SPLIT) + k
                    extra = (w - 64) * RW // 64 // 32       # the scenery stands outside
                    ol, orr = ol - extra, orr + extra
                    if n == F_WIDE:
                        l, ol, r = S("KEEP"), 0, 0
                    elif n == F_RAMP:
                        r, orr = S("RAMP"), RAMP_X // 32
                    elif F_RIVER <= n < F_DRY:
                        deck = [d * SPLIT_W * RW for d in DECK]
                        l, ol = S("RAIL"), int(deck[0] - 10) // 32
                        r, orr = S("RAIL"), int(deck[1] + 10) // 32
            for i, val in enumerate((c, y & 0xFF, (y >> 8) & 0xFF, v, l, r, ol, orr, w)):
                arrays[i][seg] = val & 0xFF
            seg += 1
        y = y0 + dy
    assert seg <= TRK_MAX and len(feats) <= MAX_FEAT
    return seg, b"".join(bytes(a) for a in arrays), feats

# =============================================================================
# looks (a palette each) and the route map
# =============================================================================

def night_colour(r, g, b, part):
    """A colour by night; part 0 the sky, 1 the road, 2 the sprites. Most
    goes dark and blue; what would be lit stays bright: the windows (pale
    blue-grey glass, by night warm lamplight), the lamps and white signs,
    and (in the headlights) the road's lines and the bright paint of signs."""
    lum = 0.3 * r + 0.59 * g + 0.11 * b
    sat = max(r, g, b) - min(r, g, b)
    if part == 0:      # a deep navy sky, the hills black against it
        return (r * 0.07 + 2, g * 0.09 + 3, b * 0.2 + 10)
    if part == 2 and lum > 105 and sat < 80 and b > r + 25 and b >= g:
        k = min(1.0, (lum - 105) / 80)                     # glass: lit windows
        return (150 + 90 * k, 112 + 70 * k, 46 + 30 * k)
    if part == 2 and r > 225 and g < 140 and b < 140:      # tail lights, neon: aglow
        return (r * 0.95, g * 0.8, b * 0.8)
    if lum > 215 and sat < 90:                             # lamps, white signs, lines
        k = 0.82 if part == 2 else 0.6
        return (r * k, g * k, b * k * 0.92)
    if part == 2 and sat > 110 and max(r, g, b) > 170:     # bright paint, caught by lights
        return (r * 0.5, g * 0.48, b * 0.5 + 6)
    if part == 1 and lum > 150:                            # the road's paler parts
        return (r * 0.36, g * 0.37, b * 0.44 + 6)
    k = (0.2, 0.22, 0.32) if part == 1 else (0.24, 0.25, 0.34)
    return (r * k[0], g * k[1], b * k[2] + 5)


FOG_RGB = (176, 180, 184)


def look_palette(pal, look):
    """The palette for a look: 0 day as drawn, 1 dusk, 2 overcast, 3 night,
    4 fog, 5 rain. The sky's colours (16-63) change most, the road's and the
    sprites' less; the near road's (P_ROAD..P_ROADF) apart from the far's:
    by night the headlights light it, in fog it is clearer."""
    out = list(pal)
    for i in range(16, 256):
        r, g, b = pal[i]
        sky = i < P_ROAD
        part = 0 if sky else 1 if i < P_SPR else 2
        near = P_ROAD <= i < P_ROADF
        if look == 3 and near:     # night: the headlights' pool on the road
            c = (r * 0.8 + 8, g * 0.76 + 6, b * 0.62 + 4)
        elif look == 4:    # fog: the far road and the sky lost in it
            c = lerp((r, g, b), FOG_RGB, 0.86 if sky else (0.14 if near else 0.8) if part == 1 else 0.3)
        elif look == 5:    # rain: dark and grey, the road wet, the far in spray
            l = 0.3 * r + 0.59 * g + 0.11 * b
            if sky:
                c = tuple((v * 0.35 + l * 0.65) * 0.62 + 8 for v in (r, g, b))
            elif part == 1:
                c = tuple((v * 0.55 + l * 0.45) * 0.68 for v in (r, g, b))
                c = (c[0], c[1] + 2, c[2] + 8)
                if near:   # wet: a sheen of the sky on the near road
                    c = lerp(c, (132, 142, 160), 0.24)
                else:
                    c = lerp(c, (104, 108, 116), 0.5)
            else:
                c = tuple((v * 0.6 + l * 0.4) * 0.8 + 4 for v in (r, g, b))
        elif look == 1:    # dusk: a warm low sun
            if sky:
                c = (r * 0.95 + 46, g * 0.62 + 20, b * 0.55 + 26)
            else:
                c = (r * 0.92 + 14, g * 0.78 + 4, b * 0.66)
        elif look == 2:    # overcast: grey, flat
            l = 0.3 * r + 0.59 * g + 0.11 * b
            k = 0.62 if sky else 0.42
            c = tuple((v * (1 - k) + l * k) * 0.88 + 6 for v in (r, g, b))
        elif look == 3:    # night: near black, lit by its windows, lamps and signs
            c = night_colour(r, g, b, part)
        else:
            c = (r, g, b)
        out[i] = tuple(clamp8(v) for v in c)
    return out


def make_map(pal, circ_info):
    """The route map: land and sea, the regions the circuits run through,
    the roads between them, their names. Returned as a buffer's bytes
    (column-major cells, COLSTRIDE apart), in the day palette's colours."""
    rnd = random.Random(65)
    w, h = W, H
    im = Image.new("RGB", (w, h))
    px = im.load()
    # sea below a wavy coast (with bays at the coastal circuits), land above
    coast = periodic_noise(rnd, w, [1, 2, 3, 5, 8], 5, 0.6)
    tex = periodic_noise(rnd, w, [3, 7, 13, 29], 1, 0.7)
    for x in range(w):
        cy = 146 + coast[x] + 10 * math.exp(-((x - 104) / 26) ** 2) * -1 \
            + 8 * math.exp(-((x - 176) / 24) ** 2) * -1 + (x - 260) * 0.25 * (x > 260)
        for y in range(h):
            if y > cy:
                t = min(1, (y - cy) / 30)
                c = lerp((70, 130, 186), (38, 84, 150), t)
                if (x * 7 + y * 13) % 53 == 0:
                    c = (120, 170, 210)
            elif y > cy - 3:
                c = (222, 206, 150)                  # the beach
            else:
                n = tex[(x + y * 3) % w] + ((x * 31 + y * 17) % 7 - 3) * 0.6
                c = (98 + n * 3, 150 + n * 4, 82 + n * 2)
            px[x, y] = tuple(clamp8(v) for v in c)
    d = ImageDraw.Draw(im)

    def blob(cx, cy, rx, ry, n, fn):
        for _ in range(n):
            a = rnd.uniform(0, math.tau)
            r = math.sqrt(rnd.random())
            fn(cx + math.cos(a) * rx * r, cy + math.sin(a) * ry * r)

    # farmland round Farmlands: a patchwork of fields
    for _ in range(46):
        x, y = rnd.uniform(66, 146), rnd.uniform(20, 82)
        fw, fh = rnd.uniform(8, 16), rnd.uniform(5, 10)
        col = rnd.choice([(196, 186, 92), (150, 176, 70), (170, 132, 82), (128, 168, 84)])
        d.rectangle([x, y, x + fw, y + fh], fill=col, outline=(110, 120, 60))
    # forest round Pine Ridge
    blob(176, 42, 40, 24, 170, lambda x, y: d.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(46, 96, 52)))
    # mountains to the north
    for k in range(14):
        x = 150 + k * 13 + rnd.uniform(-4, 4)
        y = 26 + rnd.uniform(-6, 4)
        mh = rnd.uniform(12, 20)
        d.polygon([(x - mh * 0.8, y + mh * 0.6), (x, y - mh * 0.6), (x + mh * 0.8, y + mh * 0.6)], fill=(120, 112, 104))
        d.polygon([(x, y - mh * 0.6), (x + mh * 0.8, y + mh * 0.6), (x + 2, y + mh * 0.6)], fill=(96, 88, 84))
        d.polygon([(x - mh * 0.25, y - mh * 0.25), (x, y - mh * 0.6), (x + mh * 0.25, y - mh * 0.25)], fill=(240, 242, 246))
    # the canyon's red rock
    blob(248, 52, 34, 22, 120, lambda x, y: d.ellipse([x - 3, y - 2, x + 3, y + 2], fill=rnd.choice(
        [(200, 112, 70), (176, 92, 60), (222, 140, 88)])))
    # towns: Mega Circuit's, the harbour, Night City
    for (cx, cy, rx, ry, n) in ((36, 88, 22, 16, 40), (96, 122, 18, 9, 26), (258, 120, 24, 13, 54)):
        blob(cx, cy, rx, ry, n, lambda x, y: d.rectangle([x - 2, y - 2, x + 2, y + 1],
             fill=rnd.choice([(190, 186, 180), (150, 150, 158), (176, 120, 100)]), outline=(90, 90, 96)))
    # the routes: a road each (dark edge, pale middle), bending a little
    for a, b in MAP_ROUTES:
        (x0, y0), (x1, y1) = MAP_NODES[a], MAP_NODES[b]
        mx, my = (x0 + x1) / 2 + (y1 - y0) * 0.05, (y0 + y1) / 2 - (x1 - x0) * 0.04
        pts = []
        for i in range(25):
            t = i / 24
            pts.append(((1 - t) ** 2 * x0 + 2 * (1 - t) * t * mx + t * t * x1,
                        (1 - t) ** 2 * y0 + 2 * (1 - t) * t * my + t * t * y1))
        d.line(pts, fill=(52, 44, 36), width=5)
        d.line(pts, fill=(236, 216, 160), width=2)
    # the names
    f = font(8)
    for c, (x, y) in enumerate(MAP_NODES):
        name = circ_info[c][0]
        tw = d.textlength(name, font=f)
        tx = min(max(2, x - tw / 2), w - 2 - tw)
        ty = y - 18 if y < 80 else y + 8
        if c == 0:
            tx, ty = 2, y + 14
        for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1), (1, 1)):
            d.text((tx + ox, ty + oy), name, font=f, fill=(16, 16, 20))
        d.text((tx, ty), name, font=f, fill=(255, 255, 240))
    im.save(os.path.join(sys.argv[1] if len(sys.argv) > 1 else "build", "map.png"))
    # to the palette's nearest colours (16-255), column-major
    cache = {}

    def near(c):
        if c not in cache:
            cache[c] = min(range(16, 256), key=lambda i: 3 * (pal[i][0] - c[0]) ** 2 + 4 * (pal[i][1] - c[1]) ** 2
                           + 2 * (pal[i][2] - c[2]) ** 2)
        return cache[c]
    out = bytearray(W * H)
    for x in range(w):
        for y in range(h):
            out[(x >> 3) * COLSTRIDE + y * 8 + (x & 7)] = near(px[x, y])
    return bytes(out)


# =============================================================================

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "build"
    os.makedirs(out, exist_ok=True)

    # --- sky and panoramas ---
    pano_far = make_pano_far()
    pano_near = make_pano_near()
    sea_far = make_pano_far_sea()
    sea_near = make_pano_near_sea()
    sky_rows = [sky_colour(y) for y in range(H)]
    sky_img = Image.new("RGBA", (1, H))
    sky_img.putdata([tuple(clamp8(v) for v in c) + (255,) for c in sky_rows])
    haze_img = Image.new("RGBA", (8, 1), tuple(clamp8(v) for v in HAZE) + (255,))
    sky_pal, sky_index, sky_near = build_palette([pano_far, pano_near, sea_far, sea_near, sky_img, haze_img],
                                                 N_SKY, P_SKY)
    far_idx = sky_index(pano_far)
    near_idx = sky_index(pano_near)
    sea_far_idx = sky_index(sea_far)
    sea_near_idx = sky_index(sea_near)
    sky_idx = sky_index(sky_img)
    haze_idx = sky_near(tuple(clamp8(v) for v in HAZE))

    # --- road ---
    profiles = make_profiles()
    road_imgs = []
    for mips in profiles:
        for row in mips:
            im = Image.new("RGBA", (len(row), 1))
            im.putdata([tuple(clamp8(v) for v in c) + (255,) for c in row])
            road_imgs.append(im)
    # (two groups by distance; the tunnels' ceiling and rock with them)
    t_ceil, t_face = tunnel_colours()

    def swatch(c):
        return Image.new("RGBA", (4, 1), tuple(clamp8(v) for v in c) + (255,))

    def group(near):
        ms = [m for m in range(NMIP) if (m < NEAR_MIPS) == near]
        return ([road_imgs[v * NMIP + m] for v in range(NVAR) for m in ms]
                + [swatch(t_ceil[l][m]) for l in range(4) for m in ms] + [swatch(t_face[m]) for m in ms])
    road_pal_n, road_index_n, road_near_n = build_palette(group(True), N_ROADN, P_ROAD)
    road_pal_f, road_index_f, road_near_f = build_palette(group(False), N_ROADF, P_ROADF)
    road_pal = road_pal_n + road_pal_f
    road_rows = [(road_index_n if i % NMIP < NEAR_MIPS else road_index_f)(im) for i, im in enumerate(road_imgs)]
    rgb8 = lambda c: tuple(clamp8(v) for v in c)
    ceil_idx = [(road_near_n if m < NEAR_MIPS else road_near_f)(rgb8(t_ceil[l][m])) for l in range(4) for m in range(NMIP)]
    face_idx = [(road_near_n if m < NEAR_MIPS else road_near_f)(rgb8(t_face[m])) for m in range(NMIP)]

    # --- sprites ---
    defs = sprite_defs()
    spr_imgs = [fn() for (_, _, _, _, fn) in defs]
    spr_pal, spr_index, _ = build_palette(spr_imgs, N_SPR, P_SPR)

    # --- the circuit ---
    global NAMES
    NAMES = [d[0] for d in defs]
    circ_built = [build_track({nm: i for i, nm in enumerate(NAMES)}, stretch(secs, CIRC_LEN[ci]), 0x5EED + 17 * ci)
                  for ci, (_, _, _, secs) in enumerate(CIRCUITS)]

    # --- the palette ---
    pal = [(0, 0, 0)] * 256
    for i, c in enumerate(sky_pal):
        pal[P_SKY + i] = c
    for i, c in enumerate(road_pal):
        pal[P_ROAD + i] = c
    for i, c in enumerate(spr_pal):
        pal[P_SPR + i] = c

    # --- chip RAM art: far panorama, near panorama, road profiles ---
    chip = bytearray()

    def chip_put(data, align=1):
        while (CHIP_BASE + len(chip)) % align:
            chip.append(0)
        a = CHIP_BASE + len(chip)
        chip.extend(bytes(data))
        return a

    # profiles: per variant, per mip; each with 8 texels of margin both sides
    # (the outermost colour), so a rounded start can't read a neighbour
    prof_addr = []
    prof_grass = []          # what lies beyond the profile on the left,
    prof_grass_r = []        #   and on the right
    for v in range(NVAR):
        for m in range(NMIP):
            row = road_rows[v * NMIP + m]
            margin = 4
            data = [row[0]] * margin + row + [row[-1]] * margin
            prof_addr.append(chip_put(data) + margin)
            prof_grass.append(row[0])
            prof_grass_r.append(row[-1])
    assert CHIP_BASE + len(chip) <= CHIP_END, "chip art too large: %x" % (CHIP_BASE + len(chip))

    # --- attic art: the panoramas, then the sprites ---
    attic = bytearray()
    pano_far_addr = ATTIC_BASE
    attic.extend(bytes(far_idx))
    pano_near_addr = ATTIC_BASE + len(attic)
    attic.extend(bytes(near_idx))
    spr_rows = []
    for (name, tw, th, ww, _), im in zip(defs, spr_imgs):
        assert im.size == (tw, th)
        idx = spr_index(im)
        # both ways round: rd_sprite draws by rows or by columns, whichever
        # takes fewer DMA jobs on the screen
        a = ATTIC_BASE + len(attic)
        attic.extend(bytes(idx))
        ac = ATTIC_BASE + len(attic)
        attic.extend(bytes(idx[y * tw + x] for x in range(tw) for y in range(th)))
        spr_rows.append((name, a, tw, th, ww, ac))

    # the coast's panoramas (each on a page)
    while len(attic) & 0xFF:
        attic.append(0)
    sea_far_addr = ATTIC_BASE + len(attic)
    attic.extend(bytes(sea_far_idx))
    sea_near_addr = ATTIC_BASE + len(attic)
    attic.extend(bytes(sea_near_idx))

    with open(os.path.join(out, "art_chip.bin"), "wb") as f:
        f.write(bytes(chip))
    # the palettes, a look each (entries 16-255, nybble-swapped as the
    # VIC-IV wants them): red, green, blue, 240 bytes each, copied by DMA
    # to the registers
    pal_addr = ATTIC_BASE + len(attic)
    for look in range(len(LOOKS)):
        lp = look_palette(pal, look)
        for k in range(3):
            attic.extend(bytes(nib(p[k]) for p in lp[16:]))
    # tables that never change, for low RAM (render.s45 LOWTAB): each
    # sprite's texture width, height and width in the world, each row's sky
    lowtab_attic = ATTIC_BASE + len(attic)
    lowtab = (bytes(r[2] for r in spr_rows) + bytes(r[3] for r in spr_rows)
              + b"".join(bytes([r[4] & 0xFF, r[4] >> 8]) for r in spr_rows) + bytes(sky_idx))
    attic.extend(lowtab)
    assert LOWTAB + len(lowtab) <= 0x1000, "the low tables: %d bytes" % len(lowtab)
    # in ATTIC_PARTS files of ATTIC_SPLIT bytes (each is loaded into 64 KB
    # before it is unpacked); the Makefile packs them for ATTIC_BASE + k * ATTIC_SPLIT
    assert (ATTIC_PARTS - 1) * ATTIC_SPLIT < len(attic) <= ATTIC_PARTS * ATTIC_SPLIT, "attic art: %d" % len(attic)
    assert ATTIC_BASE + len(attic) <= TRK_ADDR, "attic art reaches the circuits: %d" % len(attic)

    # --- the circuits: each its arrays and header, at TRK_ADDR + c * CIRC_SIZE ---
    circ_info = []
    for ci, ((name, look, pano, secs), (n, data, feats)) in enumerate(zip(CIRCUITS, circ_built)):
        hdr = bytearray()
        weather = W_RAIN if look == LOOK_RAIN else W_FOG if look == LOOK_FOG else 0
        hdr += bytes([n & 0xFF, n >> 8, len(feats), look, pano, weather])
        feats4 = feats + [(0, 0, 0, 0)] * (MAX_FEAT - len(feats))
        hdr += bytes(ft[0] for ft in feats4)
        for j in (1, 2, 3):
            for ft in feats4:
                hdr += bytes([ft[j] & 0xFF, ft[j] >> 8])
        assert len(data) == CIRC_HDR
        with open(os.path.join(out, "art_circ%d.bin" % ci), "wb") as f:
            f.write(data + bytes(hdr))
        circ_info.append((name, look, n, sum(1 for ft in feats if ft[0] == FEAT_JUMP),
                          sum(1 for ft in feats if ft[0] == FEAT_FORK)))
        print("circuit %d %-12s %4d segments, %d features" % (ci, name, n, len(feats)))

    # --- the route map ---
    with open(os.path.join(out, "art_map.bin"), "wb") as f:
        f.write(make_map(pal, circ_info))
    for k in range(ATTIC_PARTS):
        with open(os.path.join(out, "art_attic%s.bin" % ("" if k == 0 else k + 1)), "wb") as f:
            f.write(bytes(attic[k * ATTIC_SPLIT:(k + 1) * ATTIC_SPLIT]))

    # --- the preview sheet ---
    def to_rgb(idx, w, h):
        im = Image.new("RGB", (w, h))
        im.putdata([pal[i] if i else (255, 0, 255) for i in idx])
        return im

    sheet = Image.new("RGB", (640, 760), (40, 40, 40))
    sheet.paste(to_rgb(far_idx, PANO_W, PANO_FAR_H), (0, 0))
    sheet.paste(to_rgb(near_idx, PANO_W, PANO_NEAR_H), (0, PANO_FAR_H + 2))
    y = PANO_FAR_H + PANO_NEAR_H + 6
    for v in range(NVAR):
        r = road_rows[v * NMIP]
        sheet.paste(to_rgb(r, len(r), 1).resize((len(r) // 2, 6)), (0, y))
        y += 8
    x, rowh = 0, 0
    y += 4
    for (name, tw, th, ww, _), im in zip(defs, spr_imgs):
        if x + tw > 640:
            x, y = 0, y + rowh + 2
            rowh = 0
        sheet.paste(to_rgb(spr_index(im), tw, th), (x, y))
        x += tw + 2
        rowh = max(rowh, th)
    sheet.save(os.path.join(out, "preview.png"))

    # --- gen_config.h ---
    defs_h = [
        ("SCR_W", W), ("SCR_H", H), ("SCR_CW", CW), ("SCR_CH", CH), ("COLSTRIDE", COLSTRIDE),
        ("HZ", HZ), ("FOCAL", FOCAL), ("SEG", SEG), ("RW", RW),
        ("CAMH_COCKPIT", CAMH_COCKPIT), ("CAMH_CHASE", CAMH_CHASE), ("CHASE_BACK", CHASE_BACK),
        ("DRAW", DRAW), ("NEAR", NEAR), ("NMIP", NMIP),
        ("PROF_C0", int(PSPAN * 256)), ("PROF_SPAN", int(PSPAN * 256)),
        ("PANO_W", PANO_W), ("PANO_FAR_H", PANO_FAR_H), ("PANO_FAR_Y", PANO_FAR_Y),
        ("PANO_NEAR_H", PANO_NEAR_H), ("PANO_NEAR_Y", PANO_NEAR_Y),
        ("HAZE_COL", haze_idx), ("NSPR", len(defs)), ("NLIVERY", len(LIVERIES)), ("NTYRE", len(TYRES)), ("CAR_TW", CAR_TW), ("CAR_TH", CAR_TH),
        ("NWING", len(WING_NAMES)), ("WING_H", WING_H),
        ("NVAR", NVAR), ("NSCEN", [d[0] for d in defs].index("GANTRY") + 1),
        ("TRK_MAX", TRK_MAX), ("START_SEG", START_SEG), ("RAMP_X", RAMP_X),
        ("MAX_FEAT", MAX_FEAT), ("FEAT_JUMP", FEAT_JUMP), ("FEAT_FORK", FEAT_FORK),
        ("NCIRC", len(CIRCUITS)), ("NLOOK", len(LOOKS)),
        ("V_TUNNEL", V_TUNNEL), ("TUN_U256", int(TUN_U * 256)), ("TUN_H", TUN_H), ("PORTAL_H", PORTAL_H),
        ("W_RAIN", W_RAIN), ("W_FOG", W_FOG), ("FOG_DRAW", 48), ("FOG_SCEN", 40), ("SCEN_FAR", 110),
        ("NDROP", 32), ("REFL_H", 380 * 22 // 40),
    ]
    with open(os.path.join(out, "gen_config.h"), "w") as f:
        f.write("/* generated by tools/gen_assets.py - do not edit */\n#pragma once\n")
        for name, val in defs_h:
            f.write("#define %s %d\n" % (name, val))
        f.write("#define BUF_A 0x%XUL\n#define BUF_B 0x%XUL\n" % tuple(BUF))
        f.write("#define CHIP_ART 0x%XUL\n#define ATTIC_ART 0x%XUL\n" % (CHIP_BASE, ATTIC_BASE))
        f.write("#define TRK_ADDR 0x%XUL\n" % TRK_ADDR)
        f.write("#define CIRC_SIZE 0x%XUL\n#define CIRC_HDR 0x%XUL\n" % (CIRC_SIZE, CIRC_HDR))
        f.write("#define MAP_ADDR 0x%XUL\n" % MAP_ADDR)
        f.write("#define PROF_A2 %d\n#define SPR_A3 %d\n" % ((CHIP_BASE >> 16) & 0xFF, ATTIC_BASE >> 24))
        f.write("#define LOWTAB 0x%X\n#define LOWTAB_SIZE %d\n#define LOWTAB_ATTIC 0x%XUL\n" % (LOWTAB, len(lowtab), lowtab_attic))
        f.write("#define PAL_ADDR 0x%XUL\n#define PAL_SIZE 720\n" % pal_addr)
        for a in (pano_far_addr, pano_near_addr, sea_far_addr, sea_near_addr):
            assert a & 0xFF == 0
        f.write("#define PANO_FAR_0 0x%XUL\n#define PANO_NEAR_0 0x%XUL\n" % (pano_far_addr, pano_near_addr))
        f.write("#define PANO_FAR_1 0x%XUL\n#define PANO_NEAR_1 0x%XUL\n" % (sea_far_addr, sea_near_addr))
        for name, v in (("V_RAMP", V_RAMP), ("V_WATER", V_WATER), ("V_BANK", V_BANK),
                        ("V_SPLIT", V_SPLIT), ("V_BRIDGE", V_BRIDGE), ("V_TOWN", V_TOWN)):
            f.write("#define %s %d\n" % (name, v))
        f.write("#define SPLIT_W %d\n" % int(round(SPLIT_W * 64)))
        f.write("#define ISLAND_U %d\n#define DECK_LO %d\n#define DECK_HI %d\n"
                % (int(ISLAND * 256), int(DECK[0] * 256), int(DECK[1] * 256)))
        for i, row in enumerate(spr_rows):
            f.write("#define SPR_%s %d\n" % (row[0], i))

    # --- tables.c ---
    with open(os.path.join(out, "tables.c"), "w") as f:
        f.write("/* generated by tools/gen_assets.py - do not edit */\n")
        f.write('#include "gen_config.h"\n\n')
        f.write("/* palette entries 16..255 (nybble-swapped, as the VIC-IV wants) */\n")
        nscen = [r[0] for r in spr_rows].index("GANTRY") + 1
        f.write("/* road profiles: [variant * NMIP + mip] -> texel 0's address, byte by byte */\n")
        assert len(prof_addr) <= 256
        assert all(a >> 16 == CHIP_BASE >> 16 for a in prof_addr), "the road profiles must be in one bank"
        for k in range(2):
            f.write(carray("unsigned char", "prof_a%d" % k, [(a >> (8 * k)) & 0xFF for a in prof_addr]))
        f.write(carray("unsigned char", "var_one", VAR_ONE))
        f.write("/* each model's body row (of CAR_TH) a wing layer's bottom sits on, + 1 */\n")
        f.write(carray("unsigned char", "wing_row", [wing_row(CAR_TH, m) for m in ("GT", "HATCH", "ROADSTER", "WEDGE")]))
        f.write(carray("unsigned char", "prof_grass", prof_grass))
        f.write(carray("unsigned char", "prof_grass_r", prof_grass_r))
        f.write("/* the tunnels: per mip the ceiling's colour (lit, between the lamps), the walls'\n"
                " * upper part's (lit, between); the rock's */\n")
        f.write(carray("unsigned char", "ceil_col", ceil_idx))
        f.write(carray("unsigned char", "face_col", face_idx))
        f.write("/* scenery: the hit's half-width and centre */\n")
        scen = [SCENERY[r[0]] for r in spr_rows[:nscen]]
        f.write(carray("unsigned int", "scen_hw", [v[2] for v in scen]))
        f.write(carray("signed int", "scen_hx", [v[3] for v in scen]))
        f.write("/* sprites */\n")
        assert len(spr_rows) <= 256
        assert all(r[1] >> 24 == ATTIC_BASE >> 24 and r[5] == r[1] + r[2] * r[3] for r in spr_rows)
        for k in range(3):
            f.write(carray("unsigned char", "spr_a%d" % k, [(r[1] >> (8 * k)) & 0xFF for r in spr_rows]))
    # --- circuits.h: the circuits' names and facts, the map (map.c's) ---
    with open(os.path.join(out, "circuits.h"), "w") as f:
        f.write("/* generated by tools/gen_assets.py - do not edit (map.c's) */\n")
        f.write("static const char *const __noescape circ_name[NCIRC] = {\n")
        for (name, look, n, nj, nf) in circ_info:
            f.write('    s"%s",\n' % name.lower().ljust(12))
        f.write("};\n")
        f.write("static const char *const __noescape look_name[NLOOK] = { %s };\n"
                % ", ".join('s"%s"' % l.lower().ljust(8) for l in LOOKS))
        f.write("/* length in 0.1 km (a segment is 1.84 m), jumps, forks */\n")
        f.write("static const unsigned char __noescape circ_km10[NCIRC] = { %s };\n"
                % ", ".join(str(round(ci[2] * 1.84 / 100)) for ci in circ_info))
        f.write("static const unsigned char __noescape circ_jumps[NCIRC] = { %s };\n"
                % ", ".join(str(ci[3]) for ci in circ_info))
        f.write("static const unsigned char __noescape circ_forks[NCIRC] = { %s };\n"
                % ", ".join(str(ci[4]) for ci in circ_info))
        f.write("static const unsigned char __noescape circ_look[NCIRC] = { %s };\n"
                % ", ".join(str(ci[1]) for ci in circ_info))
        f.write("static const unsigned int __noescape node_x[NCIRC] = { %s };\n"
                % ", ".join(str(p[0]) for p in MAP_NODES))
        f.write("static const unsigned char __noescape node_y[NCIRC] = { %s };\n"
                % ", ".join(str(p[1]) for p in MAP_NODES))
    print("chip art %d bytes, attic art %d bytes, %d sprites" % (len(chip), len(attic), len(defs)))


if __name__ == "__main__":
    main()
