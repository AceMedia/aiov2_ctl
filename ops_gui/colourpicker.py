"""
A pixel art colour picker: a saturation and value square, a hue strip, a hex box and a row of presets. It reports
changes live as you drag (`changed`) and again when you let go or press Enter (`committed`).
"""
from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from . import colour

CELLS = 32                  # the square is 32 by 32 chunky cells


class SquareAndStrip(QWidget):
    """The saturation and value square with the hue strip beside it."""
    moved = pyqtSignal(float, float, float, bool)          # hue, saturation, value, finished

    def __init__(self, size=168):
        super().__init__()
        self.hue, self.sat, self.val = 340.0, 0.9, 0.84
        self.side = size
        self.strip = 28
        self.setFixedSize(size + 12 + self.strip, size)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.drag = None
        self._square, self._square_hue = None, None

    def set_hsv(self, h, s, v):
        self.hue, self.sat, self.val = h, s, v
        self.update()

    def square_image(self):
        if self._square is None or self._square_hue != round(self.hue):
            img = QImage(CELLS, CELLS, QImage.Format.Format_RGB32)
            for y in range(CELLS):
                for x in range(CELLS):
                    img.setPixelColor(x, y, QColor(colour.hsv_to_hex(self.hue, x / (CELLS - 1), 1 - y / (CELLS - 1))))
            self._square, self._square_hue = img, round(self.hue)
        return self._square

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.drawImage(QRectF(0, 0, self.side, self.side), self.square_image())
        x0 = self.side + 12
        for y in range(CELLS):                                           # the hue strip, in the same chunky cells
            p.fillRect(int(x0), int(y * self.side / CELLS), self.strip, int(self.side / CELLS) + 1,
                       QColor(colour.hsv_to_hex(360.0 * y / (CELLS - 1), 1.0, 1.0)))
        p.setPen(QPen(QColor("#111111"), 3))
        cx, cy = self.sat * (self.side - 1), (1 - self.val) * (self.side - 1)
        p.drawRect(QRectF(cx - 6, cy - 6, 12, 12))
        p.setPen(QPen(QColor("#ffffff"), 1))
        p.drawRect(QRectF(cx - 6, cy - 6, 12, 12))
        hy = self.hue / 360.0 * (self.side - 1)
        p.setPen(QPen(QColor("#111111"), 3))
        p.drawRect(QRectF(x0 - 3, hy - 3, self.strip + 6, 6))
        p.setPen(QPen(QColor("#ffffff"), 1))
        p.drawRect(QRectF(x0 - 3, hy - 3, self.strip + 6, 6))
        if self.hasFocus():
            p.setPen(QPen(QColor("#e95420"), 2))
            p.drawRect(QRectF(1, 1, self.width() - 2, self.height() - 2))
        p.end()

    def pick(self, pos, finished):
        x, y = pos.x(), pos.y()
        if self.drag == "square":
            self.sat = max(0.0, min(1.0, x / (self.side - 1)))
            self.val = max(0.0, min(1.0, 1 - y / (self.side - 1)))
        elif self.drag == "strip":
            self.hue = max(0.0, min(359.9, y / (self.side - 1) * 360.0))
        self.update()
        self.moved.emit(self.hue, self.sat, self.val, finished)

    def mousePressEvent(self, e):
        self.setFocus()
        self.drag = "square" if e.position().x() <= self.side else "strip"
        self.pick(e.position(), False)

    def mouseMoveEvent(self, e):
        if self.drag:
            self.pick(e.position(), False)

    def mouseReleaseEvent(self, e):
        if self.drag:
            self.pick(e.position(), True)
            self.drag = None

    def keyPressEvent(self, e):
        step = 0.04
        k = e.key()
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Right) and e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.hue = (self.hue + (6 if k == Qt.Key.Key_Right else -6)) % 360
        elif k == Qt.Key.Key_Left:
            self.sat = max(0.0, self.sat - step)
        elif k == Qt.Key.Key_Right:
            self.sat = min(1.0, self.sat + step)
        elif k == Qt.Key.Key_Up:
            self.val = min(1.0, self.val + step)
        elif k == Qt.Key.Key_Down:
            self.val = max(0.0, self.val - step)
        else:
            return super().keyPressEvent(e)
        self.update()
        self.moved.emit(self.hue, self.sat, self.val, True)


class ColourPicker(QWidget):
    changed = pyqtSignal(str)               # while dragging or typing
    committed = pyqtSignal(str)             # when it is settled: let go, Enter, a preset or reset

    def __init__(self, current=colour.DEFAULT):
        super().__init__()
        self.current = colour.valid_hex(current) or colour.DEFAULT
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)
        self.area = SquareAndStrip()
        self.area.moved.connect(self.from_area)
        root.addWidget(self.area)
        right = QVBoxLayout()
        right.setSpacing(8)
        self.swatch = QLabel("")
        self.swatch.setFixedHeight(40)
        self.hexbox = QLineEdit()
        self.hexbox.setMaxLength(7)
        self.hexbox.setPlaceholderText("#d70a53")
        self.hexbox.setFixedWidth(140)
        self.hexbox.textEdited.connect(self.from_hex)
        self.hexbox.editingFinished.connect(lambda: self.committed.emit(self.current))
        right.addWidget(QLabel("Colour"))
        right.addWidget(self.swatch)
        row = QHBoxLayout()
        row.addWidget(QLabel("Hex"))
        row.addWidget(self.hexbox)
        row.addStretch(1)
        right.addLayout(row)
        grid = QGridLayout()
        grid.setSpacing(8)
        grid.setAlignment(Qt.AlignmentFlag.AlignLeft)
        for i, hx in enumerate(colour.PRESETS):
            b = QPushButton("")
            b.setFixedSize(44, 44)
            b.setToolTip(hx)
            b.setStyleSheet(f"QPushButton {{ background: {hx}; border: 2px solid #111111; border-image: none; padding: 0px; "
                            f"min-height: 0px; }} QPushButton:hover {{ border: 2px solid #ffffff; }}")
            b.clicked.connect(lambda _=False, c=hx: self.set_colour(c, True))
            grid.addWidget(b, i // 4, i % 4)
        right.addLayout(grid)
        reset = QPushButton("Back to Debian red")
        reset.clicked.connect(lambda: self.set_colour(colour.DEFAULT, True))
        right.addWidget(reset)
        right.addStretch(1)
        root.addLayout(right, 1)
        self.set_colour(self.current, False, emit=False)

    def show(self, hx):
        self.current = hx
        self.swatch.setStyleSheet(f"background: {hx}; border: 2px solid #111111;")
        if not self.hexbox.hasFocus():
            self.hexbox.setText(hx)

    def set_colour(self, text, commit, emit=True):
        hx = colour.valid_hex(text)
        if not hx:
            return
        self.show(hx)
        self.hexbox.setText(hx)
        self.area.set_hsv(*colour.hex_to_hsv(hx))
        if emit:
            self.changed.emit(hx)
            if commit:
                self.committed.emit(hx)

    def from_area(self, h, s, v, finished):
        hx = colour.hsv_to_hex(h, s, v)
        self.show(hx)
        self.changed.emit(hx)
        if finished:
            self.committed.emit(hx)

    def from_hex(self, text):
        hx = colour.valid_hex(text)
        if hx:
            self.current = hx
            self.swatch.setStyleSheet(f"background: {hx}; border: 2px solid #111111;")
            self.area.set_hsv(*colour.hex_to_hsv(hx))
            self.changed.emit(hx)
