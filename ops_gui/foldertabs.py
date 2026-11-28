"""
Tab bars whose tabs overlap like folder tabs. Each tab's flared foot sits over the tab to its right, so the tabs look
as if each one is tucked under the one before it. Qt paints tabs left to right, which puts the neighbour over the foot,
so these paint the unselected tabs right to left and the selected tab last.

The tabs and the pane under them are one sheet of glass over the window's background, never two stacked:
  * the tab shapes are drawn solid into one off-screen layer and that layer is laid down once at the glass opacity,
    so a foot lying over its neighbour is not twice as solid as the rest of the tab;
  * no tab overlaps the pane (see style.py), and the pane's top border is left out under the selected tab, so the
    open tab and the pane are the same colour and opacity right through the join, with no line between them.
"""
try:
    from PyQt6.QtCore import QEvent, QPointF, QRect, Qt
    from PyQt6.QtGui import QColor, QMouseEvent, QPalette, QPixmap, QRegion
    from PyQt6.QtWidgets import (QStyle, QStyleOptionTab, QStyleOptionTabWidgetFrame, QStylePainter, QTabBar,
                                 QTabWidget, QToolButton)
except ImportError:                      # the geometry below is testable without Qt
    QTabBar = QTabWidget = object

from .skin import BORDER, GLASS_ALPHA

OVERLAP = 4          # how far a top level tab is drawn past its own box, over its neighbour (style.py's -4px margin)
WALL_LEFT = 2        # the selected tab's left wall at its bottom row, in pixels (see skin.tab_art)
WALL_RIGHT = 3       # and the wall of its foot on the right
DRAG_START = 12      # a press has to move this many pixels before it is a drag and not a tap on a tab
DRAG_STEP = 70       # and then every this many pixels of drag scrolls the bar by one tab


def glass_opacity(high_contrast):
    """How solid the tab shapes are laid down: the app's glass, or fully solid in high contrast."""
    return 1.0 if high_contrast else GLASS_ALPHA / 255


def paint_order(count, current):
    """The order to draw tab indexes in: unselected from the right to the left, then the selected one on top."""
    return [i for i in reversed(range(count)) if i != current] + ([current] if 0 <= current < count else [])


def seam_span(left, right, lo, hi, overlap=OVERLAP, wall_left=WALL_LEFT, wall_right=WALL_RIGHT):
    """(x, width) of the stretch of the pane's top border to leave out under the selected tab, so the two join with
    no line: the inside of the tab between its two walls. `left` and `right` are the tab's own first and last pixel
    columns, `lo` and `hi` the tab bar's (a tab scrolled partly out of view only opens what can be seen). None when
    nothing of it shows."""
    a = max(left + wall_left, lo)
    b = min(right + overlap - wall_right, hi)
    return (a, b - a + 1) if b >= a else None


def drag_steps(dx, step=DRAG_STEP):
    """How many tabs a drag of `dx` pixels scrolls the bar by: one for every `step` pixels. Dragging the tabs to the
    left (dx below zero) brings later tabs in, so that is the positive direction."""
    return int(-dx / step)


def reanchor(x, done, step=DRAG_STEP):
    """Where a drag would have had to start for the pointer at `x` to have scrolled exactly `done` tabs. Used when
    the bar runs out of tabs mid-drag, so dragging back the other way answers at once."""
    return x + done * step


def tab_zone(width, arrow_w):
    """(first, last) pixel column the tabs may be drawn in when the bar is `width` wide and has a scroll arrow
    `arrow_w` wide at each end: the tabs never go under an arrow."""
    return arrow_w, max(arrow_w, width - arrow_w - 1)


def arrow_cells(direction):
    """[(x, y, w, h)] in grid cells for a pixel arrow head pointing "left" or "right": 4 cells wide, 7 tall."""
    cols = [(c, 3 - c, 1, 2 * c + 1) for c in range(4)]
    return cols if direction == "left" else [(3 - x, y, w, h) for x, y, w, h in cols]


ARROW_UNIT = 3       # screen pixels per cell of the arrow head


class FolderTabBar(QTabBar):
    """Too many tabs to fit: a scroll arrow sits at each end of the bar (Qt puts both at the right), each drawn as
    a small tab of its own in space kept for it, so no tab is ever under an arrow; and the tabs can be dragged
    sideways.

    Qt only knows how to keep room for both arrows at the right hand end. So the tabs are drawn, and the pointer
    is read, one arrow's width to the right of where Qt thinks they are: the room Qt keeps on the right is then
    split evenly between the two ends."""

    def __init__(self, *args):
        super().__init__(*args)
        self._press, self._dragging, self._done = None, False, 0
        self._hover = -1
        self.currentChanged.connect(lambda _=0: self.update())

    def inset(self):
        """How far right of Qt's own positions the tabs are drawn: the left arrow's width, or 0 if they all fit."""
        left, _ = self.scrollers()
        return left.width() if left is not None and left.isVisibleTo(self) else 0

    def zone(self):
        """(first, last) pixel column of the bar the tabs are drawn in."""
        return tab_zone(self.width(), self.inset()) if self.inset() else (0, self.width() - 1)

    def shifted(self, event):
        """The same mouse event, where Qt expects it."""
        pos = event.position() - QPointF(self.inset(), 0)
        return QMouseEvent(event.type(), pos, event.globalPosition(), event.button(), event.buttons(),
                           event.modifiers())

    def scrollers(self):
        """Qt's own (left, right) scroll buttons; either may be None."""
        left = right = None
        for button in self.findChildren(QToolButton):
            if button.arrowType() == Qt.ArrowType.LeftArrow:
                left = button
            elif button.arrowType() == Qt.ArrowType.RightArrow:
                right = button
        return left, right

    def overflowing(self):
        return any(b is not None and b.isVisibleTo(self) for b in self.scrollers())

    def place_scrollers(self):
        """Qt lays both arrows out at the right hand end. Move the left one to the left hand end."""
        left, right = self.scrollers()
        for button in (left, right):
            if button is not None:
                button.installEventFilter(self)
        if left is not None and left.x() != 0:
            left.move(0, left.y())
            left.raise_()

    def eventFilter(self, obj, event):
        if isinstance(obj, QToolButton):
            if event.type() == QEvent.Type.Paint:
                return True                              # the bar draws the arrows itself, as tabs (paintEvent)
            if event.type() in (QEvent.Type.Enter, QEvent.Type.Leave, QEvent.Type.EnabledChange,
                                QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
                self.update()
        return super().eventFilter(obj, event)

    def tabLayoutChange(self):
        super().tabLayoutChange()
        self.place_scrollers()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.place_scrollers()

    def showEvent(self, event):
        super().showEvent(event)
        self.place_scrollers()

    # ---- dragging the tabs sideways. Only when they do not all fit; otherwise a press works as Qt has it.
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.overflowing():
            self._press, self._dragging, self._done = event.position().x(), False, 0
            event.accept()                               # the tab is picked on release, if this was not a drag
            return
        super().mousePressEvent(event)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._hover = -1
        self.update()

    def mouseMoveEvent(self, event):
        if self._press is None:
            if not self.inset():
                return super().mouseMoveEvent(event)
            moved = self.shifted(event)
            super().mouseMoveEvent(moved)
            over = self.tabAt(moved.position().toPoint())
            if over != self._hover:                      # Qt repaints where it thinks the tab is; do the real place
                self._hover = over
                self.update()
            return
        x = event.position().x()
        if not self._dragging and abs(x - self._press) >= DRAG_START:
            self._dragging = True
        if not self._dragging:
            return
        want = drag_steps(x - self._press)
        left, right = self.scrollers()
        while self._done != want:
            forward = want > self._done
            button = right if forward else left
            if button is None or not button.isEnabled():     # at the end of the tabs
                self._press = reanchor(x, self._done)
                break
            button.click()
            self._done += 1 if forward else -1

    def mouseReleaseEvent(self, event):
        if self._press is None:
            return super().mouseReleaseEvent(event)
        dragged, self._press, self._dragging = self._dragging, None, False
        if not dragged and event.button() == Qt.MouseButton.LeftButton:
            lo, hi = self.zone()
            i = self.tabAt(self.shifted(event).position().toPoint()) if lo <= event.position().x() <= hi else -1
            if i >= 0 and self.isTabEnabled(i):
                self.setCurrentIndex(i)

    def arrow_tabs(self):
        """[(button, direction, QRect)] for the arrows that are showing, right one first (it is drawn first, so the
        tabs and then the left arrow's foot lie over what is to their right, as every tab's does)."""
        left, right = self.scrollers()
        out = []
        if right is not None and right.isVisibleTo(self):
            out.append((right, "right", QRect(right.x(), 0, right.width() - OVERLAP, self.height())))
        if left is not None and left.isVisibleTo(self):
            out.append((left, "left", QRect(left.x(), 0, left.width() - OVERLAP, self.height())))
        return out

    def arrow_option(self, button, rect):
        opt = QStyleOptionTab()
        opt.initFrom(self)
        opt.rect = rect
        opt.shape = self.shape()
        opt.state = QStyle.StateFlag.State_None
        if button.isEnabled():
            opt.state |= QStyle.StateFlag.State_Enabled
            if button.underMouse():
                opt.state |= QStyle.StateFlag.State_MouseOver
        return opt

    def arrow_colour(self, button):
        """The arrow head: as a tab's words, brighter under the pointer, faint when there is nowhere to scroll."""
        from . import style
        dark = self.palette().color(QPalette.ColorRole.Window).lightness() < 128
        tok = style.TOKENS["dark" if dark else "light"]
        if not button.isEnabled():
            return QColor(tok["border"])
        return QColor(tok["accent"] if button.isDown() else tok["text"] if button.underMouse() else tok["muted"])

    def paintEvent(self, _):
        order = paint_order(self.count(), self.currentIndex())
        arrows = self.arrow_tabs()
        inset = self.inset()
        lo, hi = self.zone()
        tabs_clip = QRect(0, 0, hi - lo + 1, self.height())           # in the tabs' own (shifted) coordinates
        ratio = self.devicePixelRatioF()
        layer = QPixmap(max(1, int(self.width() * ratio)), max(1, int(self.height() * ratio)))
        layer.setDevicePixelRatio(ratio)
        layer.fill(Qt.GlobalColor.transparent)
        opt = QStyleOptionTab()
        shapes = QStylePainter(layer, self)
        for button, direction, rect in arrows:
            if direction == "right":
                shapes.drawControl(QStyle.ControlElement.CE_TabBarTabShape, self.arrow_option(button, rect))
        shapes.save()
        shapes.translate(inset, 0)
        shapes.setClipRect(tabs_clip)
        for i in order:
            self.initStyleOption(opt, i)
            shapes.drawControl(QStyle.ControlElement.CE_TabBarTabShape, opt)
        shapes.restore()
        for button, direction, rect in arrows:
            if direction == "left":
                shapes.drawControl(QStyle.ControlElement.CE_TabBarTabShape, self.arrow_option(button, rect))
        shapes.end()
        painter = QStylePainter(self)
        try:
            from . import style
            painter.setOpacity(glass_opacity(style.HIGH_CONTRAST))
        except Exception:
            painter.setOpacity(GLASS_ALPHA / 255)
        painter.drawPixmap(0, 0, layer)
        painter.setOpacity(1.0)
        painter.save()
        painter.translate(inset, 0)
        painter.setClipRect(tabs_clip)
        for i in order:                                  # icons and words are not glass: full strength, on top
            self.initStyleOption(opt, i)
            painter.drawControl(QStyle.ControlElement.CE_TabBarTabLabel, opt)
        painter.restore()
        u = ARROW_UNIT
        for button, direction, rect in arrows:
            colour = self.arrow_colour(button)
            body = rect.width() + OVERLAP - 7                         # the tab's body, without its foot
            x0 = rect.x() + (body - 4 * u) // 2
            y0 = 6 + (rect.height() - 6 - 7 * u) // 2                 # tabs that are not open start 6px down
            for cx, cy, w, h in arrow_cells(direction):
                painter.fillRect(x0 + cx * u, y0 + cy * u, w * u, h * u, colour)
        painter.end()
        owner = self.parent()
        if isinstance(owner, FolderTabWidget):
            owner.seam_moved()


class FolderTabWidget(QTabWidget):
    def __init__(self, *args):
        super().__init__(*args)
        self.setTabBar(FolderTabBar())
        self._seam = None

    def pane_rect(self):
        opt = QStyleOptionTabWidgetFrame()
        self.initStyleOption(opt)
        return opt, self.style().subElementRect(QStyle.SubElement.SE_TabWidgetTabPane, opt, self)

    def seam_rect(self, pane):
        """The piece of the pane's top border that lies under the selected tab, or None."""
        bar = self.tabBar()
        i = bar.currentIndex()
        if i < 0 or not bar.isVisible() or self.tabPosition() != QTabWidget.TabPosition.North:
            return None
        tab = bar.tabRect(i).translated(bar.pos()).translated(bar.inset(), 0)
        lo, hi = bar.zone()
        span = seam_span(tab.left(), tab.right(), bar.x() + lo, bar.x() + hi)
        return QRect(span[0], pane.top(), span[1], BORDER) if span else None

    def seam_moved(self):
        """The tab bar has just drawn itself: if the selected tab is somewhere new, the pane's edge follows it."""
        if self.seam_rect(self.pane_rect()[1]) != self._seam:
            self.update()

    def paintEvent(self, event):
        if self.documentMode():
            return super().paintEvent(event)
        opt, pane = self.pane_rect()
        opt.rect = pane
        gap = self._seam = self.seam_rect(pane)
        p = QStylePainter(self)
        if gap is None:
            p.drawPrimitive(QStyle.PrimitiveElement.PE_FrameTabWidget, opt)
            return
        p.setClipRegion(QRegion(self.rect()) - QRegion(gap))
        p.drawPrimitive(QStyle.PrimitiveElement.PE_FrameTabWidget, opt)
        # under the open tab: the same pane image a border's width higher, so its fill lands where the line was
        p.setClipRect(gap)
        opt.rect = pane.adjusted(0, -BORDER, 0, 0)
        p.drawPrimitive(QStyle.PrimitiveElement.PE_FrameTabWidget, opt)
