"""The AIO Ops logo in the AceMedia Host pixel style, on a transparent background.

Everything is drawn on a small grid and scaled up by a whole number, so every pixel stays square. A dark navy
stroke with a pale stroke outside it goes round every part, so the logo reads on a light page and on a dark one.

Usage: python3 tools/logo.py [FOLDER] [--preview FILE.png]

Writes aio-ops-logo.png (the logo), aio-ops-logo-full.png (with the sub line) and divider.png into FOLDER,
which defaults to docs/wiki/img/app/ in this repository. --preview also writes all three on a light page and
on a dark one. Needs only Pillow. The font is tools/fonts/press-start-2p.ttf (SIL Open Font Licence).
"""
import argparse
import os
import sys
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT = os.path.join(HERE, "fonts", "press-start-2p.ttf")
OUT = os.path.join(os.path.dirname(HERE), "docs", "wiki", "img", "app")
NAMES = {"logo": "aio-ops-logo.png", "full": "aio-ops-logo-full.png", "divider": "divider.png"}
NAVY, PALE = "#10294b", "#f4f8f5"
ORANGE = ("#ffe25c", "#ffad00", "#f39510", "#cc760c")           # top to bottom
BLUE = ("#b7e5fa", "#83c7ee", "#3b82f6", "#1d4fb8")
RAILS = (("GPS", ("#85ffc0", "#00cf70", "#008b50")), ("LORA", ("#e5d6ff", "#c4a1ff", "#832ef2")),
         ("SDR", ("#b7e5fa", "#13c9f2", "#087fae")), ("USB", ("#ffe7aa", "#ffad00", "#b66b12")))
CELL, PAD, SCALE = 4, 8, 4
BAYER = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))

# The A is the AceMedia Host mark, taken square for square from the site's own wordmark. O and S are from the
# same wordmark; I and P are drawn to match them.
GLYPHS = {
    "A": ("....#######", "...#######.", "..###...##.", "..##....##.", "..##....##.", "..########.", "..########.",
          ".###....##.", "#.##....##.", "#.##....###"),
    "I": ("######", "######", "..##..", "..##..", "..##..", "..##..", "..##..", "..##..", "######", "######"),
    "O": (".######.", "########", "##....##", "##....##", "##....##", "##....##", "##....##", "##....##",
          "########", ".######."),
    "P": ("#######.", "########", "##....##", "##....##", "########", "#######.", "##......", "##......",
          "##......", "##......"),
    "S": (".#######", "########", "##......", "##......", "#######.", ".#######", "......##", "......##",
          "########", "#######."),
}


def glyph_mask(text, cell, gap=1):
    cols = sum(len(GLYPHS[c][0]) for c in text) + gap * (len(text) - 1)
    m = Image.new("L", (cols * cell, 10 * cell), 0)
    d = ImageDraw.Draw(m)
    x = 0
    for c in text:
        for y, row in enumerate(GLYPHS[c]):
            for i, bit in enumerate(row):
                if bit == "#":
                    d.rectangle([(x + i) * cell, y * cell, (x + i + 1) * cell - 1, (y + 1) * cell - 1], fill=255)
        x += len(GLYPHS[c][0]) + gap
    return m


def dilate(m, r):
    return m.filter(ImageFilter.MaxFilter(2 * r + 1)) if r else m


def moved(m, dx, dy):
    out = Image.new("L", m.size, 0)
    out.paste(m, (dx, dy))
    return out


def dithered(mask, tones, band=4, block=2, mix=None):
    """The mask filled top to bottom in bands, one per tone. Each band joins the next through `mix` rows of
    checkerboard made of the two tones (blocks of `block` pixels), the way a pixel artist blends two colours."""
    out = Image.new("RGBA", mask.size, (0, 0, 0, 0))
    box = mask.getbbox()
    if not box:
        return out
    mix = 2 * block if mix is None else mix
    rgb = [Image.new("RGB", (1, 1), t).getpixel((0, 0)) + (255,) for t in tones]
    src, dst = mask.load(), out.load()
    top, height = box[1], box[3] - box[1]
    size = height / len(tones)
    for y in range(box[1], box[3]):
        rel = y - top
        i = min(len(tones) - 1, int(rel / size))
        edge = (i + 1) * size - rel                    # rows left in this band
        blend = i < len(tones) - 1 and edge <= mix
        for x in range(box[0], box[2]):
            if src[x, y]:
                k = i + 1 if blend and (x // block + rel // block) % 2 == 0 else i
                dst[x, y] = rgb[k]
    return out


def on(size, layer, xy):
    """`layer` placed on an empty canvas of `size`."""
    out = Image.new(layer.mode, size, 0)
    out.paste(layer, xy)
    return out


def monitor():
    """A flat-screen monitor: thin dark bezel with a deeper chin and a power light, a lit screen with a glare,
    a neck and a foot. The AceMedia A sits in the middle of the screen, the same space above it as below."""
    w, h = 60, 47
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 59, 37], fill="#2e3742")
    for x, y in ((0, 0), (1, 0), (0, 1), (59, 0), (58, 0), (59, 1), (0, 37), (1, 37), (0, 36), (59, 37), (58, 37), (59, 36)):
        im.putpixel((x, y), (0, 0, 0, 0))
    d.line([(2, 1), (57, 1)], fill="#4b5866")
    d.rectangle([3, 3, 56, 30], fill=NAVY)
    screen = Image.new("L", (w, h), 0)
    ImageDraw.Draw(screen).rectangle([4, 4, 55, 29], fill=255)
    im.alpha_composite(dithered(screen, ("#3b82f6", "#3676b0", "#243d6c"), block=1, mix=3))
    px = im.load()
    for y in range(4, 16):
        for x in range(4, 56):
            k = x + y
            if (14 <= k <= 17 or 21 <= k <= 22) and (x + y * 3) % 2 == 0:
                px[x, y] = (131, 199, 238, 255)
    a = glyph_mask("A", 2)
    ax, ay = 4 + (52 - a.width) // 2, 4 + (26 - a.height) // 2
    full = on((w, h), a, (ax, ay))
    im.paste(Image.new("RGBA", (w, h), NAVY), (0, 0), moved(full, 1, 1))
    im.alpha_composite(dithered(full, ("#ffe25c", "#ffad00", "#f39510"), block=1, mix=2))
    d.rectangle([53, 33, 54, 34], fill="#00cf70")
    d.rectangle([28, 33, 31, 34], fill="#4b5866")
    d.line([(1, 2), (1, 35)], fill="#4b5866")
    d.rectangle([26, 38, 33, 41], fill="#8a96a3")
    d.rectangle([31, 38, 33, 41], fill="#4b5866")
    d.rectangle([24, 42, 35, 43], fill="#8a96a3")
    d.rectangle([33, 42, 35, 43], fill="#4b5866")
    d.rectangle([13, 44, 46, 46], fill="#4b5866")
    d.line([(14, 44), (45, 44)], fill="#8a96a3")
    for x, y in ((13, 44), (46, 44)):
        im.putpixel((x, y), (0, 0, 0, 0))
    return im


def small(text):
    font = ImageFont.truetype(FONT, 8)
    m = Image.new("1", (int(font.getlength(text)), 8), 0)
    ImageDraw.Draw(m).text((0, 0), text, font=font, fill=1)
    return m.convert("L")


def chip(name, tones, ink=NAVY, light=None):
    """An arcade key: a face with a lighter top edge and a darker bottom edge, and the name on it. `light` adds
    a small lamp in that colour at the left, lit, with a glint."""
    t = small(name)
    lamp = 9 if light else 0
    w, h = t.width + 7 + lamp, 14
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, w - 1, h - 1], fill=tones[1])
    d.rectangle([0, 0, w - 1, 1], fill=tones[0])
    d.rectangle([0, h - 2, w - 1, h - 1], fill=tones[2])
    for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        im.putpixel((x, y), (0, 0, 0, 0))
    if light:
        d.rectangle([3, 3, 9, 9], fill=NAVY)
        d.rectangle([4, 4, 8, 8], fill=light)
        d.rectangle([4, 4, 5, 5], fill="#ffffff")
    im.paste(Image.new("RGBA", t.size, ink), (4 + lamp, 3), t)
    return im


def compose(size, parts):
    """`parts` is [(layer, (x, y), navy stroke, pale stroke, shadow)]. All the pale strokes go down first, then
    all the navy ones, then the parts, so no stroke ever covers a neighbour."""
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    navy_all, pale_all = Image.new("L", size, 0), Image.new("L", size, 0)
    placed = []
    for layer, xy, navy, pale, shadow in parts:
        full = on(size, layer, xy)
        sil = full.getchannel("A").point(lambda v: 255 if v else 0)
        n = dilate(sil, navy)
        if shadow:
            n = ImageChops.lighter(n, moved(n, shadow, shadow))
        navy_all = ImageChops.lighter(navy_all, n)
        pale_all = ImageChops.lighter(pale_all, dilate(n, pale))
        placed.append(full)
    canvas.paste(Image.new("RGBA", size, PALE), (0, 0), pale_all)
    canvas.paste(Image.new("RGBA", size, NAVY), (0, 0), navy_all)
    for full in placed:
        canvas.alpha_composite(full)
    return canvas


def logo(sub=False):
    aio, ops = glyph_mask("AIO", CELL, gap=2), glyph_mask("OPS", CELL, gap=2)
    mon = monitor()
    gap, space = 12, 4 * CELL
    w = PAD + mon.width + gap + aio.width + space + ops.width + PAD
    h = PAD + mon.height + PAD
    tx, ty = PAD + mon.width + gap, PAD + 1
    parts = [(mon, (PAD, PAD), 2, 2, 0),
             (dithered(aio, ORANGE), (tx, ty), 2, 2, 0),
             (dithered(ops, BLUE), (tx + aio.width + space, ty), 2, 2, 0)]
    if sub:
        y = h - 1
        h = y + 14 + PAD
        parts.append((chip("UCONSOLE AIO V2", ("#3676b0", "#243d6c", "#10233a"), ink="#ffffff"), (PAD, y), 2, 2, 0))
        chips = [chip(name, tones) for name, tones in RAILS]
        x = w - PAD - sum(c.width for c in chips) - 7 * (len(chips) - 1)
        for c in chips:
            parts.append((c, (x, y), 2, 2, 0))
            x += c.width + 7
    img = compose((w, h), parts)
    return img.resize((w * SCALE, h * SCALE), Image.NEAREST)


def divider():
    """A rule for section headings in the same style: orange, solid in the middle and breaking up into dither
    towards the ends, with the four rail colours as keys at the centre."""
    w, h = 320, 16
    line = Image.new("RGBA", (w - 8, 2), (0, 0, 0, 0))
    px = line.load()
    for x in range(w - 8):
        edge = min(x, w - 9 - x)
        for y in range(2):
            solid = edge > 110
            sparse = edge > 60 and (x // 2 + y) % 2 == 0
            dots = edge > 8 and (x // 2) % 4 == 0 and y == 0
            if solid or sparse or dots:
                px[x, y] = (243, 149, 16, 255)
    keys = []
    x = w // 2 - 21
    for _, tones in RAILS:
        k = Image.new("RGBA", (8, 8), tones[1])
        ImageDraw.Draw(k).rectangle([0, 0, 7, 1], fill=tones[0])
        ImageDraw.Draw(k).rectangle([0, 6, 7, 7], fill=tones[2])
        keys.append((k, (x, 4), 2, 2, 0))
        x += 12
    img = compose((w, h), [(line, (4, 7), 2, 2, 0)] + keys)
    return img.resize((w * SCALE, h * SCALE), Image.NEAREST)


def preview(images):
    """Each image about 900 pixels wide, on a white page and on a dark one."""
    bw, pad = 900, 28
    scaled = [im.resize((bw, im.height * bw // im.width), Image.LANCZOS) for im in images]
    block = sum(s.height for s in scaled) + pad * (len(scaled) + 1)
    out = Image.new("RGBA", (bw + pad * 2, block * 2), "#ffffff")
    ImageDraw.Draw(out).rectangle([0, block, out.width, out.height], fill="#0d1117")
    for i in range(2):
        y = i * block + pad
        for s in scaled:
            out.alpha_composite(s, (pad, y))
            y += s.height + pad
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Draw the AIO Ops logo and divider.")
    ap.add_argument("folder", nargs="?", default=OUT, help="where to write the PNGs (default: docs/wiki/img/app/)")
    ap.add_argument("--preview", metavar="FILE.png", help="also write all three on a light and a dark page")
    args = ap.parse_args()
    os.makedirs(args.folder, exist_ok=True)
    plain, full, rule = logo(), logo(sub=True), divider()
    for label, im in (("logo", plain), ("full", full), ("divider", rule)):
        im.save(os.path.join(args.folder, NAMES[label]), optimize=True)
    if args.preview:
        preview([plain, full, rule]).save(args.preview, optimize=True)
    for label, im in (("logo", plain), ("full", full), ("divider", rule)):
        alpha = im.getchannel("A")
        clear = sum(1 for v in alpha.getdata() if v == 0) * 100 // (im.width * im.height)
        print(NAMES[label], im.size, im.mode, f"{clear}% of it is see-through; corner alpha {alpha.getpixel((0, 0))}")
