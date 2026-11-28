"""The loading screen: the app's name in big pixels, on screen before the window behind it has been built.

Building the window takes most of a second on a uConsole, longer on a cold start, and until now nothing showed
in that time. This is put up first: it needs no font, no picture and none of the app's own modules beyond the
colours, so it is on screen in a fraction of the time. It fills the screen the way the app is about to (full
screen, or maximised), in the app's theme and feature colour, and goes when the real window is showing.

It shows no progress, because there is nothing honest to measure: the strip under the name is decoration.

Everything above show() is plain Python and is tested without a screen."""
import re

TITLE = "UCONSOLE AIO"
STRIP = 12                  # blocks in the strip under the name

# Five wide, seven tall. Only the letters the title uses.
GLYPHS = {
    "U": ("X...X", "X...X", "X...X", "X...X", "X...X", "X...X", ".XXX."),
    "C": (".XXX.", "X...X", "X....", "X....", "X....", "X...X", ".XXX."),
    "O": (".XXX.", "X...X", "X...X", "X...X", "X...X", "X...X", ".XXX."),
    "N": ("X...X", "XX..X", "X.X.X", "X.X.X", "X..XX", "X...X", "X...X"),
    "S": (".XXXX", "X....", "X....", ".XXX.", "....X", "....X", "XXXX."),
    "L": ("X....", "X....", "X....", "X....", "X....", "X....", "XXXXX"),
    "E": ("XXXXX", "X....", "X....", "XXXX.", "X....", "X....", "XXXXX"),
    "A": (".XXX.", "X...X", "X...X", "XXXXX", "X...X", "X...X", "X...X"),
    "I": ("XXXXX", "..X..", "..X..", "..X..", "..X..", "..X..", "XXXXX"),
    " ": (".....",) * 7,
}
ADVANCE, ROWS = 6, 7        # a glyph and the gap after it; rows in a glyph


def cells(text):
    """The lit squares of `text`, as (column, row) pairs counted in squares from its top left."""
    out = []
    for i, ch in enumerate(text):
        for r, line in enumerate(GLYPHS.get(ch, GLYPHS[" "])):
            out += [(i * ADVANCE + c, r) for c, mark in enumerate(line) if mark == "X"]
    return out


def columns(text):
    """How many squares wide `text` is (no gap after the last letter)."""
    return max(0, len(text) * ADVANCE - 1)


def layout(width, height, text=TITLE, strip=STRIP):
    """Where everything goes on a width x height screen: {'size': pixels per square, 'title': (x, y) of the
    name's top left, 'strip': [(x, y, w, h), ...] the blocks under it}. The name takes about three fifths of
    the width, in whole pixels per square so it stays sharp, and all of it stays on the screen."""
    size = max(1, min(int(width * 0.6) // max(1, columns(text)), int(height * 0.2) // ROWS))
    title_w, title_h = columns(text) * size, ROWS * size
    block, gap = size * 2, size
    strip_w = strip * block + (strip - 1) * gap
    total_h = title_h + size * 3 + block
    top = max(0, (height - total_h) // 2)
    left = (width - strip_w) // 2
    return {"size": size, "title": ((width - title_w) // 2, top),
            "strip": [(left + i * (block + gap), top + title_h + size * 3, block, block) for i in range(strip)]}


def blend(a, b, k):
    """A colour k of the way from a to b, both '#rrggbb'."""
    ra, rb = [int(a[i:i + 2], 16) for i in (1, 3, 5)], [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * k) for x, y in zip(ra, rb))


def fade(bg, accent, count=STRIP):
    """The strip's colours: the feature colour at the left, dying away into the background."""
    return [blend(accent, bg, i / count) for i in range(count)]


def colours(settings, system_is_dark, tokens):
    """(background, name, feature colour) for the loading screen, from the saved settings: the same theme, high
    contrast and feature colour the window is about to use, so nothing flashes when it takes over."""
    choice = settings.get("theme")
    mode = choice if choice in ("light", "dark") else ("dark" if system_is_dark else "light")
    tok = tokens[mode]
    accent = settings.get("accent")
    if not (isinstance(accent, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", accent)):
        accent = tok["accent"]
    if settings.get("high_contrast"):
        return ("#000000", "#ffffff", accent) if mode == "dark" else ("#ffffff", "#000000", accent)
    return tok["bg"], tok["text"], accent


def wanted(argv):
    """Whether to show it: always, unless started with --no-splash."""
    return "--no-splash" not in argv


def show(app, full, settings=None, wait=0.4):
    """Put the loading screen up and return it once it has been drawn (or after `wait` seconds, whichever is
    first), so the caller can get on with building the window. Qt is imported here."""
    import time
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor, QPainter
    from PyQt6.QtWidgets import QWidget
    from . import data, style

    saved = settings if settings is not None else data.load_settings()
    dark = app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    bg, name, accent = colours(saved, dark, style.TOKENS)

    class Loading(QWidget):
        drawn = False

        def paintEvent(self, _event):
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(bg))
            lay = layout(self.width(), self.height())
            size, (x0, y0) = lay["size"], lay["title"]
            ink = QColor(name)
            for c, r in cells(TITLE):
                p.fillRect(x0 + c * size, y0 + r * size, size, size, ink)
            for (x, y, w, h), colour in zip(lay["strip"], fade(bg, accent)):
                p.fillRect(x, y, w, h, QColor(colour))
            p.end()
            self.drawn = True

    screen = Loading()
    screen.setWindowTitle("uConsole AIO")
    screen.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
    screen.resize(720, 640)
    if full:
        screen.showFullScreen()
    else:
        screen.showMaximized()
    until = time.monotonic() + wait
    while not screen.drawn and time.monotonic() < until:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    return screen
