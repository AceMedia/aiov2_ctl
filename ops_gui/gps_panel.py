"""
The GPS tab: fix, position, speed, accuracy, a sky plot and per satellite signal bars.

It reads gpsd through the same engine as the ops console (ops_agent.gps.GPS), so there is one
backend. The socket is only open while this tab is showing, and nothing is ever sent anywhere.
The helpers at the top are pure so they can be tested without a screen or a receiver.
"""
import math
import os
import time

COMPASS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
FIX_NAMES = {0: "Unknown", 1: "No fix", 2: "2D fix", 3: "3D fix"}
TICK_MS = 500


def fix_label(mode):
    return FIX_NAMES.get(mode, "No fix")


def format_coords(lat, lon, digits=5):
    """'51.50740 N, 0.12780 W' or '' when there is no position."""
    if lat is None or lon is None:
        return ""
    return (f"{abs(lat):.{digits}f} {'N' if lat >= 0 else 'S'}, "
            f"{abs(lon):.{digits}f} {'E' if lon >= 0 else 'W'}")


def compass_point(degrees):
    if degrees is None:
        return "-"
    return COMPASS[int(((degrees % 360) + 11.25) // 22.5) % 16]


def speed_text(mps):
    if mps is None:
        return "-"
    return f"{mps * 3.6:.1f} km/h  ({mps * 2.23694:.1f} mph)"


def metres(value):
    return "-" if value is None else f"{value:.0f} m"


def sky_xy(az, el, radius):
    """Position of a satellite on a polar plot: zenith in the centre, horizon on the rim."""
    if az is None or el is None:
        return None
    r = (90 - max(0.0, min(90.0, el))) / 90.0 * radius
    rad = math.radians(az)
    return r * math.sin(rad), -r * math.cos(rad)


def sat_rows(sky_data):
    """Satellites that have a signal, strongest first, used ones before unused at equal strength."""
    rows = [s for s in (sky_data or []) if s.get("ss")]
    return sorted(rows, key=lambda s: (-(s["ss"] or 0), not s.get("used")))


def waypoint_line(tpv, when=None):
    """One CSV line for a saved waypoint, or None when there is no position."""
    if not tpv or tpv.get("lat") is None or tpv.get("lon") is None:
        return None
    stamp = when or time.strftime("%Y-%m-%dT%H:%M:%S%z")
    alt = "" if tpv.get("alt") is None else f"{tpv['alt']:.1f}"
    return f"{stamp},{tpv['lat']:.6f},{tpv['lon']:.6f},{alt}\n"


def waypoint_path():
    return os.path.join(os.path.expanduser("~"), "aiov2-captures", "waypoints.csv")


def save_waypoint(tpv, path=None):
    """Append a waypoint to a private file. Returns the path, or None if there was no position."""
    line = waypoint_line(tpv)
    if line is None:
        return None
    path = path or waypoint_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    new = not os.path.exists(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as f:
        if new:
            f.write("time,lat,lon,alt_m\n")
        f.write(line)
    return path


def build_panel(win, Switch=None):
    """Return (widget, activate, deactivate)."""
    from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
    from PyQt6.QtGui import QColor, QPainter, QPalette, QPen
    from PyQt6.QtWidgets import (QApplication, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                                 QTabWidget, QVBoxLayout, QWidget)
    from .foldertabs import FolderTabWidget
    from . import gps_track

    class SkyView(QWidget):
        def __init__(self):
            super().__init__()
            self.setMinimumSize(200, 140)
            self.sats = []

        def paintEvent(self, _):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            text = self.palette().color(QPalette.ColorRole.WindowText)
            side = min(self.width(), self.height()) - 36
            cx, cy, radius = self.width() / 2, self.height() / 2, side / 2
            ring = QColor(text)
            ring.setAlpha(70)
            p.setPen(QPen(ring, 1))
            for frac in (1.0, 2 / 3, 1 / 3):
                p.drawEllipse(QPointF(cx, cy), radius * frac, radius * frac)
            p.drawLine(QPointF(cx - radius, cy), QPointF(cx + radius, cy))
            p.drawLine(QPointF(cx, cy - radius), QPointF(cx, cy + radius))
            p.setPen(text)
            for label, dx, dy in (("N", 0, -1), ("E", 1, 0), ("S", 0, 1), ("W", -1, 0)):
                p.drawText(QRectF(cx + dx * (radius + 16) - 10, cy + dy * (radius + 16) - 10, 20, 20),
                           Qt.AlignmentFlag.AlignCenter, label)
            for s in self.sats:
                pos = sky_xy(s.get("az"), s.get("el"), radius)
                if not pos:
                    continue
                used = s.get("used")
                size = 5 + (s.get("ss") or 0) / 8
                p.setPen(QPen(QColor("#111111"), 1))
                p.setBrush(QColor("#43a047" if used else "#8b93a1"))
                p.drawEllipse(QPointF(cx + pos[0], cy + pos[1]), size, size)
                p.setPen(text)
                p.drawText(QPointF(cx + pos[0] + size + 2, cy + pos[1] + 4), str(s.get("prn") or ""))
            p.end()

    class BarsView(QWidget):
        def __init__(self):
            super().__init__()
            self.setMinimumHeight(80)
            self.rows = []

        def paintEvent(self, _):
            p = QPainter(self)
            text = self.palette().color(QPalette.ColorRole.WindowText)
            base = self.height() - 18
            n = max(1, len(self.rows))
            w = min(34, (self.width() - 10) / n)
            for i, s in enumerate(self.rows[:24]):
                ss = max(0, min(60, s.get("ss") or 0))
                h = (base - 6) * ss / 60
                x = 6 + i * w
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#43a047" if s.get("used") else "#8b93a1"))
                p.drawRect(QRectF(x, base - h, w - 4, h))
                p.setPen(text)
                p.drawText(QRectF(x - 4, base + 2, w + 4, 14), Qt.AlignmentFlag.AlignCenter, str(s.get("prn") or ""))
            if not self.rows:
                p.setPen(text)
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No satellites heard yet")
            p.end()

    class LivePage(QWidget):
        def __init__(self):
            super().__init__()
            self.on_fix = None
            self.gps = None
            self.tpv = None
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.tick)
            root = QVBoxLayout(self)

            top = QFrame()
            top.setObjectName("card")
            tl = QHBoxLayout(top)
            self.fix = QLabel("No fix")
            self.fix.setStyleSheet("font-size: 24pt; font-weight: bold;")
            self.coords = QLabel("")
            self.coords.setStyleSheet("font-size: 18pt;")
            col = QVBoxLayout()
            col.addWidget(self.fix)
            col.addWidget(self.coords)
            self.format = QComboBox()
            for key, label in gps_track.FORMATS:
                self.format.addItem(label, key)
            self.format.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.format.currentIndexChanged.connect(self.set_format)
            tl.addLayout(col, 1)
            self.status = QLabel("")
            self.status.setWordWrap(True)
            self.status.setStyleSheet("font-size: 12pt; color: #9e9e9e;")
            side = QVBoxLayout()
            side.addWidget(self.format)
            side.addWidget(self.status)
            tl.addLayout(side, 1)
            root.addWidget(top)

            mid = QHBoxLayout()
            box = QFrame()
            box.setObjectName("card")
            grid = QGridLayout(box)
            grid.setVerticalSpacing(0)
            self.values = {}
            for r, (key, name) in enumerate((("alt", "Altitude"), ("speed", "Speed"), ("track", "Heading"),
                                             ("acc", "Accuracy"), ("dop", "Dilution"), ("sats", "Satellites"),
                                             ("time", "UTC time"))):
                a = QLabel(name)
                a.setStyleSheet("font-size: 12pt; color: #9e9e9e;")
                b = QLabel("-")
                b.setStyleSheet("font-size: 14pt;")
                grid.addWidget(a, r, 0)
                grid.addWidget(b, r, 1)
                self.values[key] = b
            mid.addWidget(box, 1)
            sky = QFrame()
            sky.setObjectName("card")
            sl = QVBoxLayout(sky)
            self.sky = SkyView()
            sl.addWidget(self.sky)
            sky.setMinimumHeight(0)
            mid.addWidget(sky, 1)
            root.addLayout(mid, 3)

            bars = QFrame()
            bars.setObjectName("card")
            bl = QVBoxLayout(bars)
            cap = QLabel("Signal per satellite (green = used in the fix)")
            cap.setStyleSheet("font-size: 12pt; color: #9e9e9e;")
            self.bars = BarsView()
            bl.addWidget(cap)
            bl.addWidget(self.bars)
            root.addWidget(bars, 1)

            row = QHBoxLayout()
            self.copy_b = QPushButton("Copy coordinates")
            self.save_b = QPushButton("Save waypoint")
            for b in (self.copy_b, self.save_b):
                b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                b.setStyleSheet("font-size: 12pt; padding: 4px 12px;")
                b.setEnabled(False)
                row.addWidget(b)
            row.addStretch(1)
            root.addLayout(row)
            self.copy_b.clicked.connect(self.copy)
            self.save_b.clicked.connect(self.save)

        def activate(self):
            if self.gps:
                return
            try:
                from ops_agent.gps import GPS
                self.gps = GPS()
            except Exception as e:
                self.status.setText(f"GPS engine unavailable: {e}")
                return
            self.timer.start(TICK_MS)
            self.tick()

        def deactivate(self):
            self.timer.stop()
            engine, self.gps = self.gps, None
            if engine and engine.sock:
                try:
                    engine.sock.close()
                except OSError:
                    pass

        def tick(self):
            g = self.gps
            if not g:
                return
            data = g.read()
            if data:
                self.tpv = data
                if self.on_fix:
                    self.on_fix(data)
            self.status.setText(g.get_status())
            tpv = self.tpv or {}
            self.fix.setText(fix_label(tpv.get("mode")))
            self.coords.setText(gps_track.format_position(tpv.get("lat"), tpv.get("lon"), win.gps_format)
                                or "Waiting for a position")
            q = g.sky_quality or {}
            self.values["alt"].setText(metres(tpv.get("alt")))
            self.values["speed"].setText(speed_text(tpv.get("speed")))
            self.values["track"].setText("-" if tpv.get("track") is None
                                         else f"{tpv['track']:.0f} deg  {compass_point(tpv['track'])}")
            ex, ey = tpv.get("epx"), tpv.get("epy")
            self.values["acc"].setText("-" if ex is None or ey is None else f"+/- {max(ex, ey):.0f} m")
            self.values["dop"].setText("-" if not q.get("hdop") else f"HDOP {q['hdop']:.1f}   PDOP {q.get('pdop') or 0:.1f}")
            self.values["sats"].setText(f"{q.get('used', 0)} used of {q.get('visible', 0)} visible")
            self.values["time"].setText(tpv.get("time") or "-")
            self.sky.sats = list(g.sky_data)
            self.sky.update()
            self.bars.rows = sat_rows(g.sky_data)
            self.bars.update()
            has_pos = format_coords(tpv.get("lat"), tpv.get("lon")) != ""
            self.copy_b.setEnabled(has_pos)
            self.save_b.setEnabled(has_pos)

        def set_format(self, _=0):
            win.gps_format = self.format.currentData()
            self.tick()

        def copy(self):
            t = self.tpv or {}
            QApplication.clipboard().setText(f"{t.get('lat'):.6f}, {t.get('lon'):.6f}")
            self.status.setText("Coordinates copied.")

        def save(self):
            path = save_waypoint(self.tpv)
            self.status.setText(f"Waypoint saved to {path}" if path else "No position to save.")
            if path:
                win.gps_track_page.load_waypoints()

    class Panel(FolderTabWidget):
        def __init__(self):
            super().__init__()
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.setObjectName("subtabs")
            win.gps_format = "decimal"
            self.live = LivePage()
            win.gps_track_page = gps_track.build_page(win, Switch, waypoint_path)
            self.live.on_fix = win.gps_track_page.feed
            self.addTab(self.live, "Live")
            self.addTab(win.gps_track_page, "Track")
            self.currentChanged.connect(lambda _=0: win.gps_track_page.load_waypoints())

        def activate(self):
            self.live.activate()

        def deactivate(self):
            win.gps_track_page.leaving()
            self.live.deactivate()

    panel = Panel()
    return panel, panel.activate, panel.deactivate
