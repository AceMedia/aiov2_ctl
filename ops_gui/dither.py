"""
Pixel dithering for gradients. The Home screen fades Debian red into the background with a 4 by 4 ordered dither in
2 pixel cells; this does the same for any vertical gradient, so every gradient in the app is made of crisp pixels.

It is used as the whole window's own background, behind the tab bar and everything else: strongest right at the
top (behind the tabs, where the window starts), fading out towards the bottom, the same way the Home screen's own
banner fades. Cards, panes and tabs are drawn semi-transparent (see skin.py's GLASS_ALPHA) so it shows through them
rather than stopping at their edges.
`levels` is pure so it can be tested without Qt; gradient_tile needs Qt and caches its result.
"""
BAYER = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))
CELL = 2
TOP_TINT = "#1a1015"                    # the background starts with a hint of Debian red at the very top
STRENGTH = 0.85                         # how far down the dither runs, as a share of full strength


def levels(height, strength=STRENGTH, cell=CELL):
    """[(y, x_cells)]: for each row of cells, which of the four dither columns are drawn in the second colour.
    Strongest at the top (y=0), fading to nothing by the bottom."""
    rows = []
    for cy in range(0, max(1, height), cell):
        level = int(16 * strength * (1.0 - min(1.0, cy / max(1, height - 1))))
        rows.append((cy, [bx for bx in range(4) if BAYER[(cy // cell) % 4][bx] < level]))
    return rows


_cache = {}


def gradient_tile(height, base, tint=TOP_TINT):
    """An 8 pixel wide tile as tall as the window: the base colour with the tint dithered in from the top."""
    from PyQt6.QtGui import QColor, QPainter, QPixmap
    key = (height, QColor(base).name(), tint)
    if key not in _cache:
        pm = QPixmap(4 * CELL, max(1, height))
        pm.fill(QColor(base))
        p = QPainter(pm)
        colour = QColor(tint)
        for cy, xs in levels(height):
            for bx in xs:
                p.fillRect(bx * CELL, cy, CELL, CELL, colour)
        p.end()
        _cache.clear()
        _cache[key] = pm
    return _cache[key]
