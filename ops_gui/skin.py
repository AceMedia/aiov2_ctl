"""
The pixel skin: small images with 45 degree cut corners that the stylesheet stretches over cards, buttons,
fields and tabs, so the whole app shares the diagonal corner of the Home screen.

Each image is drawn pixel by pixel from the theme colours and written once to a cache folder. Qt slices it
(border-image) so the corners stay crisp at any widget size. The geometry is pure, so it can be tested without
a screen. To restyle a shape, change its entry in SPECS; to restyle colours, change TOKENS in style.py.
"""
import os

def tab_art(slice_px=10, foot=7, cut=5, stripe=False, border=3):
    """The folder tab as rows of text: '.' clear, '#' line, 'o' fill, '=' accent stripe. A cut top left corner, a
    `border` pixel wall on the right, wholly inside the right slice so the stretched middle stays one colour, that
    stops `foot` pixels short of the box, and a foot that flares out a pixel a row over the last `foot` rows, so it
    can sit over the tab to its right. No bottom edge is drawn."""
    size = slice_px * 2 + 4
    wall = size - foot - 1
    rows = []
    for y in range(size):
        start = max(0, cut - y)
        end = wall - 1 if y == 0 else wall if y < size - foot else wall + (y - (size - foot) + 1)
        row = ["."] * size
        for x in range(start, end + 1):
            edge = x < start + (border if y < cut else border - 1) or x > end - border or y < 2
            row[x] = "#" if edge else "o"
        if stripe and y < 3:
            for x in range(start, end + 1):
                row[x] = "="
        rows.append("".join(row))
    return rows


TAB_OFF = tab_art()               # a bigger slice than the card/button corners, so the foot reads clearly
TAB_ON = tab_art(stripe=True)

# 0-255: how solid a glass surface is, so the window's own gradient shows through it. There are only ever two
# sheets of glass over the window's background: the tabs with the pane (one sheet), and whatever sits inside the
# pane (cards, rows, the Home screen's panels). Nothing else is stacked, so the same value looks the same everywhere.
GLASS_ALPHA = 185

# name: (slice in pixels, chamfer, which corners are cut, fill token, line token, extras)
SPECS = {
    "card": (8, 6, "tl br", "card", "border", {"tick": "accent2", "glass": True}),
    "row": (8, 6, "tl br", "card", "border", {"tick": "accent2", "glass": True}),
    "tile": (8, 6, "tl br", "row", "border", {}),
    "button": (6, 4, "tl br", "btn", "border", {"bevel": "raised"}),
    "button_hover": (6, 4, "tl br", "tab_hover", "accent", {"bevel": "raised"}),
    "button_press": (6, 4, "tl br", "accent", "accent", {"bevel": "sunk"}),
    "field": (6, 4, "tl br", "field", "border", {}),
    # tabs: a cut top left corner, no bottom edge, and a flared foot at the bottom right (see TAB_OFF). Their
    # images are solid: tabs overlap each other, so the tab bar draws them all into one layer and makes that
    # layer glass in a single step (foldertabs.py). Glass baked into each image would double up at every overlap.
    "tab_off": (10, 5, "tl", "tab_off", "border", {"art": TAB_OFF}),
    "tab_hover": (10, 5, "tl", "tab_hover", "border", {"art": TAB_OFF}),
    "tab_on": (10, 5, "tl", "panel", "border", {"open_bottom": True, "stripe": "accent", "art": TAB_ON}),
    "chip_on": (6, 4, "tl br", "#e95420", "#e95420", {"bevel": "raised"}),
    "chip_off": (6, 4, "tl br", "#616161", "#616161", {}),
    "pane": (8, 8, "br", "panel", "border", {"glass": True}),
    "drawer": (8, 6, "tl br", "panel", "accent", {}),
}
BORDER = 2


def shape(w, h, chamfer, corners="tl br", open_bottom=False):
    """The set of (x, y) pixels inside a w by h box with 45 degree cuts at the named corners."""
    cut = set(corners.split())
    pixels = set()
    for y in range(h):
        for x in range(w):
            if "tl" in cut and x + y < chamfer:
                continue
            if "tr" in cut and (w - 1 - x) + y < chamfer:
                continue
            if "br" in cut and (w - 1 - x) + (h - 1 - y) < chamfer:
                continue
            if "bl" in cut and x + (h - 1 - y) < chamfer:
                continue
            pixels.add((x, y))
    return pixels


def border_pixels(pixels, w, h, width=BORDER, open_bottom=False):
    """The outer `width` pixels of a shape. With open_bottom the bottom edge is not drawn (a tab joining its pane)."""
    inner = set(pixels)

    def inside(p, current):
        x, y = p
        if open_bottom and y >= h:
            return True
        return p in current

    for _ in range(width):
        inner = {p for p in inner
                 if all(inside(n, inner) for n in ((p[0] + 1, p[1]), (p[0] - 1, p[1]), (p[0], p[1] + 1), (p[0], p[1] - 1)))}
    return pixels - inner


def image_size(slice_px):
    return slice_px * 2 + 4


def _rgb(hex_colour):
    return tuple(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))


def mix(a, b, k):
    """A colour k of the way from a to b, both '#rrggbb'."""
    ra, rb = _rgb(a), _rgb(b)
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * k) for x, y in zip(ra, rb))


def pixel_map(name, tokens):
    """{(x, y): '#rrggbb'} for one skin image, from the theme tokens."""
    slice_px, chamfer, corners, fill_t, line_t, extra = SPECS[name]
    fill_t, line_t = tokens.get(fill_t, fill_t), tokens.get(line_t, line_t)      # a token name or a plain #rrggbb
    size = image_size(slice_px)
    if extra.get("art"):
        colours = {"#": line_t, "o": fill_t, "=": tokens[extra.get("stripe", "accent")]}
        return {(x, y): colours[c] for y, row in enumerate(extra["art"]) for x, c in enumerate(row) if c in colours}
    open_bottom = bool(extra.get("open_bottom"))
    body = shape(size, size, chamfer, corners)
    edge = border_pixels(body, size, size, BORDER, open_bottom)
    inner = border_pixels(body - edge, size, size, 1) if extra.get("bevel") else set()
    light, dark = mix(fill_t, "#ffffff", 0.22), mix(fill_t, "#000000", 0.45)
    if extra.get("bevel") == "sunk":
        light, dark = dark, light
    out = {}
    for p in body:
        out[p] = line_t if p in edge else fill_t
        if p in inner:                                      # a lit edge top left and a shaded one bottom right
            out[p] = light if p[0] + p[1] < size - 1 else dark
    if extra.get("tick"):                                   # a short accent mark on the top edge, like the Home panels
        for x in range(size - 8, size - 3):
            for y in range(0, 2):
                out[(x, y)] = tokens[extra["tick"]]
        if extra.get("stripe") and p[1] < 3:
            out[p] = tokens[extra["stripe"]]
    return out


def build(mode, tokens, directory, alpha=None, variant=""):
    """Write every skin image for a theme and return {name: path}. Needs Qt (QImage)."""
    from PyQt6.QtGui import QColor, QImage
    folder = os.path.join(directory, mode + variant + "-" + tokens["accent"].lstrip("#"))      # a folder per colour: Qt caches by path
    os.makedirs(folder, exist_ok=True)
    try:                                                                            # keep only a few old colours around
        olds = sorted((os.path.join(directory, d) for d in os.listdir(directory)), key=os.path.getmtime)
        for old in olds[:-8]:
            for f in os.listdir(old):
                os.remove(os.path.join(old, f))
            os.rmdir(old)
    except OSError:
        pass
    paths = {}
    for name, spec in SPECS.items():
        size = image_size(spec[0])
        img = QImage(size, size, QImage.Format.Format_ARGB32)
        img.fill(0)
        for (x, y), colour in pixel_map(name, tokens).items():
            img.setPixelColor(x, y, QColor(colour))
        if spec[5].get("glass"):
            apply_glass(img, GLASS_ALPHA if alpha is None else alpha)
        path = os.path.join(folder, f"{name}.png")
        img.save(path)
        paths[name] = path
    return paths


def apply_glass(img, alpha):
    """Make every pixel already drawn in the image (card, tab, pane...) this see-through, so the window's own
    background gradient shows through its body and border alike, rather than the surface looking solid. Pixels
    that are still fully transparent (the cut corners) are left alone."""
    for y in range(img.height()):
        for x in range(img.width()):
            colour = img.pixelColor(x, y)
            if colour.alpha() > 0:
                colour.setAlpha(min(colour.alpha(), alpha))
                img.setPixelColor(x, y, colour)


def border_image(name, paths):
    """The stylesheet lines that stretch a skin image over a widget."""
    s = SPECS[name][0]
    return f'border-image: url("{paths[name]}") {s} {s} {s} {s} stretch stretch; border-width: {s}px;'


def cache_dir():
    return os.path.join(os.path.expanduser("~"), ".cache", "ops_gui", "skin")
