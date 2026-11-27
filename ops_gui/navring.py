"""
The highlight that shows where the D-pad is: a frame drawn round whichever control has the keyboard.

One frame for every control, the app's hand-drawn ones included, so the highlight looks the same everywhere and
no control has to draw its own. It is only there while the D-pad or the arrow keys are in use, and goes away
when the pointer is used. It never takes a click: it is cut down to the frame itself, and clicks pass through.
"""


def frame_region(width, height, thick):
    """The frame as four rectangles (x, y, w, h): the edge of a width by height box, `thick` pixels wide."""
    t = max(1, min(thick, width // 2, height // 2))
    return [(0, 0, width, t), (0, height - t, width, t), (0, t, t, height - 2 * t), (width - t, t, t, height - 2 * t)]


def make(win, rect_of_focus, colour):
    """The frame, as a child of `win`. `rect_of_focus()` gives the highlighted control's rectangle in the
    window's coordinates, or None when there is nothing to show; `colour()` gives the frame's colour."""
    from PyQt6.QtCore import QRect, Qt, QTimer
    from PyQt6.QtGui import QColor, QPainter, QRegion
    from PyQt6.QtWidgets import QWidget

    THICK, OUT = 3, 3

    class Ring(QWidget):
        def __init__(self):
            super().__init__(win)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.hide()
            self.timer = QTimer(self)                      # the control can move (a page scrolls, a tab changes)
            self.timer.setInterval(150)
            self.timer.timeout.connect(self.place)

        def follow(self):
            self.place()
            if self.isVisible() and not self.timer.isActive():
                self.timer.start()

        def drop(self):
            self.timer.stop()
            self.hide()

        def place(self):
            rect = rect_of_focus()
            if rect is None:
                self.drop()
                return
            x, y, w, h = rect
            geo = QRect(x - OUT, y - OUT, w + 2 * OUT, h + 2 * OUT).intersected(win.rect())
            if geo.width() < 2 * THICK or geo.height() < 2 * THICK:
                self.drop()
                return
            if geo != self.geometry():
                self.setGeometry(geo)
                region = QRegion()
                for fx, fy, fw, fh in frame_region(geo.width(), geo.height(), THICK):
                    region = region.united(QRegion(fx, fy, fw, fh))
                self.setMask(region)
            self.show()
            self.raise_()

        def paintEvent(self, _):
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(colour()))
            p.end()

    return Ring()

