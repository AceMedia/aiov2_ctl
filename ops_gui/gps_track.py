"""
The GPS Track page: coordinate formats, an opt in track recorder, trip stats, a trace and waypoints.

Recording is off until the switch is turned on, nothing leaves the uConsole, and track files are
private (mode 600) GPX files in ~/aiov2-captures/gps. The helpers at the top are pure so they can
be tested without a screen or a receiver.
"""
import math
import os
import time
from xml.sax.saxutils import escape

EARTH_M = 6371000.0
MAX_POINTS = 20000
MIN_MOVE_M = 3.0
SAVE_EVERY = 20
FORMATS = (("decimal", "Decimal degrees"), ("dm", "Degrees and minutes"),
           ("dms", "Degrees, minutes, seconds"), ("grid", "Maidenhead locator"))


# ---- coordinate formats

def _split(value, digits_min=0):
    sign = -1 if value < 0 else 1
    value = abs(value)
    deg = int(value)
    return sign, deg, (value - deg) * 60


def maidenhead(lat, lon, pairs=3):
    """Maidenhead locator such as 'IO91wm' (2 chars per pair, up to 4 pairs)."""
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return ""
    lon += 180.0
    lat += 90.0
    lon = min(lon, 359.999999)
    lat = min(lat, 179.999999)
    out = [chr(ord("A") + int(lon // 20)), chr(ord("A") + int(lat // 10))]
    lon, lat = lon % 20, lat % 10
    if pairs >= 2:
        out += [str(int(lon // 2)), str(int(lat // 1))]
        lon, lat = lon % 2, lat % 1
    if pairs >= 3:
        out += [chr(ord("a") + int(lon * 12)), chr(ord("a") + int(lat * 24))]
        lon, lat = (lon * 12) % 1, (lat * 24) % 1
    if pairs >= 4:
        out += [str(int(lon * 10)), str(int(lat * 10))]
    return "".join(out)


def format_position(lat, lon, fmt="decimal"):
    if lat is None or lon is None:
        return ""
    ns, ew = ("N" if lat >= 0 else "S"), ("E" if lon >= 0 else "W")
    if fmt == "grid":
        return maidenhead(lat, lon)
    if fmt == "dm":
        _, d1, m1 = _split(lat)
        _, d2, m2 = _split(lon)
        return f"{d1} {m1:06.3f}' {ns}, {d2} {m2:06.3f}' {ew}"
    if fmt == "dms":
        _, d1, m1 = _split(lat)
        _, d2, m2 = _split(lon)
        return (f"{d1} {int(m1)}' {(m1 % 1) * 60:04.1f}\" {ns}, "
                f"{d2} {int(m2)}' {(m2 % 1) * 60:04.1f}\" {ew}")
    return f"{abs(lat):.5f} {ns}, {abs(lon):.5f} {ew}"


# ---- recording and stats

def haversine_m(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * EARTH_M * math.asin(math.sqrt(h))


def add_point(track, tpv, now=None, min_move=MIN_MOVE_M):
    """Append a fix to the track when it is a real position and has moved. Returns True if added."""
    if not tpv or tpv.get("mode", 0) < 2 or tpv.get("lat") is None or tpv.get("lon") is None:
        return False
    point = {"lat": tpv["lat"], "lon": tpv["lon"], "alt": tpv.get("alt"),
             "speed": tpv.get("speed"), "t": now if now is not None else time.time()}
    if track and haversine_m(track[-1], point) < min_move:
        return False
    if len(track) >= MAX_POINTS:
        return False
    track.append(point)
    return True


def track_stats(track):
    if not track:
        return {"points": 0, "distance": 0.0, "duration": 0.0, "avg": None, "max": None,
                "alt_min": None, "alt_max": None}
    dist = sum(haversine_m(a, b) for a, b in zip(track, track[1:]))
    duration = max(0.0, track[-1]["t"] - track[0]["t"])
    speeds = [p["speed"] for p in track if p.get("speed") is not None]
    alts = [p["alt"] for p in track if p.get("alt") is not None]
    return {"points": len(track), "distance": dist, "duration": duration,
            "avg": dist / duration if duration > 0 else None,
            "max": max(speeds) if speeds else None,
            "alt_min": min(alts) if alts else None, "alt_max": max(alts) if alts else None}


def distance_text(m):
    return f"{m:.0f} m" if m < 1000 else f"{m / 1000:.2f} km"


def duration_text(s):
    s = int(s)
    h, rest = divmod(s, 3600)
    m, sec = divmod(rest, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {sec:02d}s"


def speed_kmh(mps):
    return "-" if mps is None else f"{mps * 3.6:.1f} km/h"


def stats_lines(stats):
    alt = "-" if stats["alt_min"] is None else f"{stats['alt_min']:.0f} to {stats['alt_max']:.0f} m"
    return [("Points", str(stats["points"])), ("Distance", distance_text(stats["distance"])),
            ("Time", duration_text(stats["duration"])), ("Average", speed_kmh(stats["avg"])),
            ("Top speed", speed_kmh(stats["max"])), ("Altitude", alt)]


# ---- drawing

def nice_length(metres):
    """A round scale bar length at or below `metres`."""
    if metres <= 0:
        return 0
    base = 10 ** math.floor(math.log10(metres))
    for m in (5, 2, 1):
        if m * base <= metres:
            return m * base
    return base


def project(track, width, height, pad=16):
    """Pixel positions for a north up trace that fits the box, plus metres per pixel.

    Returns ([(x, y), ...], metres_per_pixel). One point sits in the middle.
    """
    if not track:
        return [], 0.0
    lat0 = sum(p["lat"] for p in track) / len(track)
    kx = math.cos(math.radians(lat0)) * math.pi / 180 * EARTH_M
    ky = math.pi / 180 * EARTH_M
    xs = [p["lon"] * kx for p in track]
    ys = [-p["lat"] * ky for p in track]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    avail_w, avail_h = max(1.0, width - 2 * pad), max(1.0, height - 2 * pad)
    span = max(w / avail_w, h / avail_h)
    if span <= 0:
        return [(width / 2, height / 2) for _ in track], 0.0
    ox = pad + (avail_w - w / span) / 2
    oy = pad + (avail_h - h / span) / 2
    return [(ox + (x - min(xs)) / span, oy + (y - min(ys)) / span) for x, y in zip(xs, ys)], span


# ---- files

def gps_dir():
    return os.path.join(os.path.expanduser("~"), "aiov2-captures", "gps")


def gpx_text(track, name="uConsole track"):
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<gpx version="1.1" creator="uConsole AIO" xmlns="http://www.topografix.com/GPX/1/1">',
             f"<trk><name>{escape(name)}</name><trkseg>"]
    for p in track:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(p["t"]))
        ele = "" if p.get("alt") is None else f"<ele>{p['alt']:.1f}</ele>"
        lines.append(f'<trkpt lat="{p["lat"]:.6f}" lon="{p["lon"]:.6f}">{ele}<time>{stamp}</time></trkpt>')
    lines += ["</trkseg></trk>", "</gpx>", ""]
    return "\n".join(lines)


def new_track_path(directory=None, now=None):
    directory = directory or gps_dir()
    os.makedirs(directory, mode=0o700, exist_ok=True)
    return os.path.join(directory, time.strftime("track-%Y%m%d-%H%M%S.gpx", time.localtime(now or time.time())))


def write_track(track, path):
    """Write the whole track privately. Rewrites the same file, never follows a symlink."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(gpx_text(track))
    return path


def read_waypoints(path, limit=200):
    """Newest first rows from the waypoints CSV the Live tab writes. Missing file gives []."""
    try:
        with open(path) as f:
            lines = f.read().splitlines()[1:]
    except OSError:
        return []
    rows = []
    for line in lines:
        parts = line.split(",")
        if len(parts) < 3:
            continue
        try:
            rows.append({"time": parts[0], "lat": float(parts[1]), "lon": float(parts[2]),
                         "alt": parts[3] if len(parts) > 3 else ""})
        except ValueError:
            continue
    return rows[::-1][:limit]


def build_page(win, Switch, waypoint_file):
    from PyQt6.QtCore import QPointF, QRectF, Qt
    from PyQt6.QtGui import QColor, QPainter, QPalette, QPen
    from PyQt6.QtWidgets import (QAbstractItemView, QApplication, QFrame, QGridLayout, QHBoxLayout,
                                 QHeaderView, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
                                 QVBoxLayout, QWidget)

    class Trace(QWidget):
        def __init__(self):
            super().__init__()
            self.setMinimumSize(260, 180)
            self.track = []

        def paintEvent(self, _):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            text = self.palette().color(QPalette.ColorRole.WindowText)
            pts, per_px = project(self.track, self.width(), self.height())
            if not pts:
                p.setPen(text)
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Nothing recorded yet")
                p.end()
                return
            p.setPen(QPen(QColor("#2e9bff"), 3))
            for a, b in zip(pts, pts[1:]):
                p.drawLine(QPointF(*a), QPointF(*b))
            p.setPen(QPen(QColor("#111111"), 1))
            p.setBrush(QColor("#37b24d"))
            p.drawEllipse(QPointF(*pts[0]), 5, 5)
            p.setBrush(QColor("#f59f00"))
            p.drawEllipse(QPointF(*pts[-1]), 6, 6)
            p.setPen(text)
            p.drawText(QRectF(self.width() - 26, 4, 22, 18), Qt.AlignmentFlag.AlignCenter, "N")
            if per_px > 0:
                length = nice_length(per_px * self.width() / 4)
                if length:
                    px = length / per_px
                    y = self.height() - 12
                    p.drawLine(QPointF(10, y), QPointF(10 + px, y))
                    p.drawText(QPointF(14 + px, y + 4), distance_text(length))
            p.end()

    class Page(QWidget):
        def __init__(self):
            super().__init__()
            self.track = []
            self.path = None
            self.recording = False
            self.last = None
            root = QHBoxLayout(self)

            left = QVBoxLayout()
            rec = QFrame()
            rec.setObjectName("card")
            rl = QVBoxLayout(rec)
            row = QHBoxLayout()
            self.switch = Switch()
            self.switch.clicked.connect(self.toggle)
            title = QLabel("Record track")
            title.setStyleSheet("font-size: 14pt; font-weight: bold;")
            row.addWidget(title)
            row.addStretch(1)
            row.addWidget(self.switch)
            rl.addLayout(row)
            self.note = QLabel("Off. Turn it on to log where you go to a private GPX file on this device.")
            self.note.setWordWrap(True)
            self.note.setStyleSheet("font-size: 12pt; color: #9e9e9e;")
            rl.addWidget(self.note)
            left.addWidget(rec)
            stats = QFrame()
            stats.setObjectName("card")
            grid = QGridLayout(stats)
            self.values = {}
            for r, (name, _) in enumerate(stats_lines(track_stats([]))):
                a = QLabel(name)
                a.setStyleSheet("font-size: 13pt; color: #9e9e9e;")
                b = QLabel("-")
                b.setStyleSheet("font-size: 15pt;")
                grid.addWidget(a, r, 0)
                grid.addWidget(b, r, 1)
                self.values[name] = b
            left.addWidget(stats)
            self.trace = Trace()
            tr = QFrame()
            tr.setObjectName("card")
            QVBoxLayout(tr).addWidget(self.trace)
            left.addWidget(tr, 1)
            root.addLayout(left, 3)

            right = QFrame()
            right.setObjectName("card")
            wl = QVBoxLayout(right)
            cap = QLabel("Saved waypoints")
            cap.setStyleSheet("font-size: 13pt; color: #9e9e9e;")
            wl.addWidget(cap)
            self.table = QTableWidget(0, 2)
            self.table.setHorizontalHeaderLabels(["When", "Position"])
            self.table.verticalHeader().setVisible(False)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            self.table.itemSelectionChanged.connect(self.sync)
            wl.addWidget(self.table, 1)
            self.copy_b = QPushButton("Copy position")
            self.copy_b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.copy_b.clicked.connect(self.copy)
            wl.addWidget(self.copy_b)
            root.addWidget(right, 2)
            self.rows = []
            self.load_waypoints()
            self.sync()

        def sync(self):
            self.copy_b.setEnabled(0 <= self.table.currentRow() < len(self.rows))

        def load_waypoints(self):
            self.rows = read_waypoints(waypoint_file())
            self.table.setRowCount(len(self.rows))
            for r, row in enumerate(self.rows):
                self.table.setItem(r, 0, QTableWidgetItem(row["time"].replace("T", " ")[:19]))
                self.table.setItem(r, 1, QTableWidgetItem(format_position(row["lat"], row["lon"],
                                                                          win_format())))
            self.sync()

        def copy(self):
            r = self.table.currentRow()
            if 0 <= r < len(self.rows):
                QApplication.clipboard().setText(format_position(self.rows[r]["lat"], self.rows[r]["lon"],
                                                                 win_format()))

        def toggle(self):
            if self.recording:
                self.stop()
            else:
                self.start()

        def start(self):
            self.track = []
            self.path = new_track_path()
            self.recording = True
            self.switch.show_state(True)
            self.note.setText("Recording. Waiting for a fix... " + self.path)
            self.show_stats()

        def stop(self):
            if self.recording and self.track:
                try:
                    write_track(self.track, self.path)
                    self.note.setText(f"Saved {len(self.track)} points to {self.path} (private).")
                except OSError as e:
                    self.note.setText(f"Could not save the track: {e}")
            elif self.recording:
                self.note.setText("Stopped. Nothing was recorded, so no file was written.")
            self.recording = False
            self.switch.show_state(False)

        def feed(self, tpv):
            if not self.recording:
                return
            if add_point(self.track, tpv):
                if len(self.track) % SAVE_EVERY == 1:
                    try:
                        write_track(self.track, self.path)
                    except OSError:
                        pass
                self.note.setText("Recording to " + self.path)
                self.show_stats()

        def show_stats(self):
            for name, value in stats_lines(track_stats(self.track)):
                self.values[name].setText(value)
            self.trace.track = list(self.track)
            self.trace.update()

        def leaving(self):
            """The GPS link closes when the tab is left, so close the file off properly."""
            self.stop()

    def win_format():
        return getattr(win, "gps_format", "decimal")

    return Page()
