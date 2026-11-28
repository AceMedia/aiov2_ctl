"""
The app's small pixel icons: a bitmap of '#' (ink) and anything else (clear), drawn in one colour with square
pixels and scaled by a whole number with no smoothing. The window's top-bar icons (sun, moon, terminal, full
screen) are drawn here at twice their size; IGA's row icons at their own size.

ink_bounds() and lone_pixels() are plain Python, for tests.
"""


def ink_bounds(rows):
    """(left, top, right, bottom) of the ink in a bitmap, inclusive, or None when there is none."""
    cells = [(x, y) for y, row in enumerate(rows) for x, c in enumerate(row) if c == "#"]
    if not cells:
        return None
    xs, ys = [x for x, _ in cells], [y for _, y in cells]
    return min(xs), min(ys), max(xs), max(ys)


def lone_pixels(rows):
    """Ink pixels with no ink beside them across or up and down: a stroke one pixel thin."""
    def ink(x, y):
        return 0 <= y < len(rows) and 0 <= x < len(rows[y]) and rows[y][x] == "#"
    return [(x, y) for y, row in enumerate(rows) for x, c in enumerate(row)
            if c == "#" and not (ink(x - 1, y) or ink(x + 1, y) or ink(x, y - 1) or ink(x, y + 1))]


def bitmap_image(rows, colour, scale=1):
    """A QImage of the bitmap in `colour` (a QColor), `scale` screen pixels to each bitmap pixel."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QImage
    w, h = max(len(r) for r in rows), len(rows)
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(0)
    for y, row in enumerate(rows):
        for x, cell in enumerate(row):
            if cell == "#":
                img.setPixelColor(x, y, colour)
    if scale > 1:
        img = img.scaled(w * scale, h * scale, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.FastTransformation)
    return img


def bitmap_icon(rows, colour, scale=1, disabled=None):
    """A QIcon of the bitmap; with `disabled` (a QColor) the greyed-out look is drawn in that colour too."""
    from PyQt6.QtGui import QIcon, QPixmap
    icon = QIcon(QPixmap.fromImage(bitmap_image(rows, colour, scale)))
    if disabled is not None:
        icon.addPixmap(QPixmap.fromImage(bitmap_image(rows, disabled, scale)), QIcon.Mode.Disabled)
    return icon
