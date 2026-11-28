"""The project's own launcher icons, in the same pixel style and colours as the logo (tools/logo.py).

Usage: python3 tools/icons.py [FOLDER] [--preview FILE.png]

Writes ops-app.png, ops-console.png and installer.png into FOLDER, which defaults to img/ in this repository
(the folder the installer copies beside the program). Each is drawn on a 32 by 32 grid and scaled up by a
whole number to 128 pixels, so every pixel stays square. --preview also writes all three, at three sizes, on
a light page and on a dark one. Needs only Pillow.

shapes() is plain Python and is tested without Pillow.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GRID, SCALE = 32, 4
NAVY, PALE, ORANGE, DEEP = "#10294b", "#f4f8f5", "#ffad00", "#0a1a31"
RAILS = ("#00cf70", "#c4a1ff", "#13c9f2", "#ffad00")            # GPS, LoRa, SDR, USB: as in the logo
NAMES = {"app": "ops-app.png", "console": "ops-console.png", "installer": "installer.png"}


def tile():
    """The plate every icon sits on: a pale edge round a navy square with its corners cut."""
    out = []
    for inset, colour in ((0, PALE), (1, NAVY)):
        lo, hi = inset, GRID - inset
        out += [(lo + 2, lo, hi - 2, hi, colour), (lo, lo + 2, hi, hi - 2, colour), (lo + 1, lo + 1, hi - 1, hi - 1, colour)]
    return out


def rail_dots(y=25):
    """The four rail colours as a row of small squares."""
    return [(6 + i * 6, y, 9 + i * 6, y + 3, colour) for i, colour in enumerate(RAILS)]


def shapes(kind):
    """[(left, top, right, bottom, colour)] on the 32 by 32 grid, drawn in order, right and bottom exclusive."""
    out = tile()
    if kind == "app":                                           # a window: title bar, then the four rails as meters
        out += [(4, 4, 28, 7, ORANGE), (5, 5, 7, 6, NAVY), (8, 5, 10, 6, NAVY)]
        out += [(4, 8, 28, 28, DEEP)]
        for i, (colour, height) in enumerate(zip(RAILS, (14, 9, 12, 6))):
            x = 6 + i * 6
            out += [(x, 26 - height, x + 4, 26, colour)]
    elif kind == "console":                                     # a terminal: a prompt, a cursor, the rails below
        out += [(4, 4, 28, 7, ORANGE), (5, 5, 7, 6, NAVY), (8, 5, 10, 6, NAVY)]
        out += [(4, 8, 28, 28, DEEP)]
        out += [(7 + i, 11 + i, 9 + i, 13 + i, PALE) for i in range(4)]         # the chevron, going down
        out += [(7 + i, 19 - i, 9 + i, 21 - i, PALE) for i in range(4)]         # and coming back
        out += [(14, 19, 21, 21, ORANGE)]                                       # the cursor
        out += rail_dots(24)
    elif kind == "installer":                                   # an arrow coming down into a tray
        out += [(14, 5, 18, 14, ORANGE)]
        out += [(10 + i, 14 + i, 22 - i, 15 + i, ORANGE) for i in range(6)]
        out += [(5, 19, 7, 24, PALE), (25, 19, 27, 24, PALE), (5, 22, 27, 24, PALE)]
        out += rail_dots(26)
    else:
        raise ValueError(kind)
    return out


def draw(kind, scale=SCALE):
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (GRID, GRID), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for left, top, right, bottom, colour in shapes(kind):
        d.rectangle((left, top, right - 1, bottom - 1), fill=colour)
    return im.resize((GRID * scale, GRID * scale), Image.NEAREST)


def main(argv):
    from PIL import Image
    preview = argv[argv.index("--preview") + 1] if "--preview" in argv[:-1] else None
    args = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or argv[i - 1] != "--preview")]
    folder = args[0] if args else os.path.join(os.path.dirname(HERE), "img")
    os.makedirs(folder, exist_ok=True)
    for kind, name in NAMES.items():
        draw(kind).save(os.path.join(folder, name), optimize=True)
        print(os.path.join(folder, name))
    if preview:
        out = preview
        sheet = Image.new("RGB", (2 * 760, 200), "#ffffff")
        for half, page in enumerate(("#f2f4f8", "#101216")):
            sheet.paste(Image.new("RGB", (760, 200), page), (half * 760, 0))
            x = half * 760 + 16
            for kind in NAMES:
                for scale in (4, 2, 1):
                    icon = draw(kind, scale)
                    sheet.paste(icon, (x, 36 if scale == 4 else 100 - icon.height // 2), icon)
                    x += icon.width + 8
                x += 14
        sheet.save(out)
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
