"""
The map of a walk: where the uConsole went during an Everything session, and what it heard on the way.

The line is the uConsole's own track, from the session's GPS readings. Each mark comes from a wardrive or Bluetooth
log saved while the session ran, and sits where the uConsole was when it heard that network or address strongest.
It is NOT where the network or device is: one radio with one aerial cannot tell which way a signal came from or how
far away it started, so this never says so and never guesses. The time kept with a mark is when it was first heard.

There are no map tiles and nothing is fetched: the drawing is north up on a plain background with a scale bar.
The exports (GPX and GeoJSON) are private files beside the session, removed with it.

Everything above build_view is pure, so it can be tested without a window, a receiver or a radio.
"""
import bisect
import calendar
import csv
import json
import math
import os
import re
import time
from xml.sax.saxutils import escape

from . import gps_track

KINDS = {"wifi": "Wi-Fi", "bt": "Bluetooth"}
WHAT = {"wifi": "Wi-Fi network", "bt": "Bluetooth address"}
MEANING = "where the uConsole was when it heard this strongest, not where the device is"
EXPORTS = ("gpx", "geojson")
LOG_NAME = re.compile(r"^(wardrive|bluetooth)-(\d{8}-\d{6})(?:-\d+)?\.wigle\.csv$")
MAX_LOG_BYTES = 8 * 1024 * 1024      # a saved log is a few hundred bytes a network; more than this is not one of ours
MAX_HEARD = 5000                     # marks drawn and exported at most (the strongest are kept)
MIN_PER_PX = 0.1                     # metres a pixel at the closest: GPS wander is not blown up to fill the screen
REACH = 16                           # how near a tap has to be to a mark, in pixels (a finger, not a mouse)
TRACK_REACH = 48                     # and to the track
TOGETHER_PX = 6                      # marks this close on screen are read out together
NO_FIX = "No GPS fix during this session, so there is no track to draw and nothing can be placed on a map."
_BAD_XML = re.compile("[^\x09\x0a\x0d\x20-퟿-�\U00010000-\U0010ffff]")


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _place(lat, lon):
    return _number(lat) and _number(lon) and -90 <= lat <= 90 and -180 <= lon <= 180


# ---- the uConsole's own track

def track_of(samples):
    """The readings that had a fix, as track points ({'lat', 'lon', 't', 'alt'}), oldest first. A reading that is
    damaged or has no position is left out rather than guessed at."""
    out = []
    for s in samples or ():
        if not isinstance(s, dict) or not _number(s.get("ts")) or not _place(s.get("lat"), s.get("lon")):
            continue
        gps = s.get("gps") if isinstance(s.get("gps"), dict) else {}
        alt = gps.get("alt")
        out.append({"lat": s["lat"], "lon": s["lon"], "t": s["ts"], "alt": alt if _number(alt) else None})
    out.sort(key=lambda p: p["t"])
    return out


def here_index(track, ts, hold):
    """Which track point says where the uConsole was at a time: the newest at or before it, but only if that is
    no older than `hold` seconds. None when there was no fix around then, so no marker is drawn from old news."""
    if not track or ts is None:
        return None
    i = bisect.bisect_right([p["t"] for p in track], ts) - 1
    if i < 0 or ts - track[i]["t"] > hold:
        return None
    return i


# ---- what the saved logs say was heard

def _float(text):
    try:
        v = float(text)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _first_seen(text):
    """A log's FirstSeen column (UTC, to the second) as seconds since 1970, or None."""
    try:
        return float(calendar.timegm(time.strptime((text or "").strip(), "%Y-%m-%d %H:%M:%S")))
    except (ValueError, OverflowError):
        return None


def parse_wigle(text):
    """The rows of a WiGLE CSV as [{'kind', 'addr', 'name', 'rssi', 'ts', 'lat', 'lon', 'channel', 'auth'}].
    'kind' is wifi or bt. Anything that is not a whole, sensible row (cut short, no position, no time, a type
    we do not write) is skipped: a damaged log gives fewer marks, never a wrong one."""
    rows, columns = [], None
    try:
        for parts in csv.reader((text or "").replace("\x00", "").splitlines()):
            if columns is None:
                if "MAC" in parts and "CurrentLatitude" in parts:
                    columns = {name.strip(): i for i, name in enumerate(parts)}
                continue

            def cell(name):
                i = columns.get(name)
                return parts[i].strip() if i is not None and i < len(parts) else ""
            kind = {"WIFI": "wifi", "BLE": "bt", "BT": "bt"}.get(cell("Type").upper())
            addr, ts, rssi = cell("MAC").lower(), _first_seen(cell("FirstSeen")), _float(cell("RSSI"))
            lat, lon = _float(cell("CurrentLatitude")), _float(cell("CurrentLongitude"))
            if kind is None or not addr or ts is None or rssi is None or not _place(lat, lon):
                continue
            rows.append({"kind": kind, "addr": addr, "name": cell("SSID"), "rssi": rssi, "ts": ts, "lat": lat,
                         "lon": lon, "channel": cell("Channel"), "auth": cell("AuthMode")})
    except csv.Error:
        pass
    return rows


def in_span(rows, t0, t1):
    """The rows first heard between two times. The log keeps whole seconds, so the start is rounded down."""
    lo = math.floor(t0)
    return [r for r in rows if lo <= r["ts"] <= t1]


def strongest(rows, limit=MAX_HEARD):
    """One row an address: the strongest (the same network is in every log saved during a walk). Oldest first.
    Past `limit` the weakest are dropped, so a huge log cannot make the map crawl."""
    best = {}
    for r in rows:
        key = (r["kind"], r["addr"])
        if key not in best or r["rssi"] > best[key]["rssi"]:
            best[key] = r
    kept = sorted(best.values(), key=lambda r: -r["rssi"])[:max(0, limit)]
    return sorted(kept, key=lambda r: (r["ts"], r["kind"], r["addr"]))


def log_dir():
    """Where the Wi-Fi survey and the Bluetooth scan save their logs."""
    return os.path.join(os.path.expanduser("~"), "aiov2-captures")


def log_files(directory=None, since=None):
    """The saved wardrive and Bluetooth logs, oldest first. With `since`, a log saved well before that time is
    left out without being opened: it cannot hold anything first heard later. (An hour's slack, because the
    name is in local time and the clocks may have changed.)"""
    d = directory or log_dir()
    found = []
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    for name in names:
        m = LOG_NAME.match(name)
        if not m:
            continue
        if since is not None:
            try:
                saved = time.mktime(time.strptime(m.group(2), "%Y%m%d-%H%M%S"))
            except (ValueError, OverflowError):
                saved = None
            if saved is not None and saved < since - 3700:
                continue
        found.append(os.path.join(d, name))
    return found


def read_heard(t0, t1, directory=None):
    """Everything the saved logs say was first heard between two times, one row an address. Reads files, so it
    is called from a worker thread. A log that cannot be read is skipped."""
    rows = []
    for path in log_files(directory, t0):
        try:
            with open(path, errors="replace", newline="") as f:
                text = f.read(MAX_LOG_BYTES)
        except OSError:
            continue
        rows += in_span(parse_wigle(text), t0, t1)
    return strongest(rows)


# ---- where things go on the screen

def layout(track, heard, width, height, pad=28):
    """Pixel places for a north up drawing that fits the track and the marks in the box:
    {'track': [(x, y)], 'heard': [(x, y)], 'per_px': metres a pixel}. Never closer than MIN_PER_PX."""
    points = list(track) + list(heard)
    places, per_px = gps_track.project(points, width, height, pad)
    if places and per_px < MIN_PER_PX:
        cx, cy, shrink = width / 2, height / 2, per_px / MIN_PER_PX
        places = [(cx + (x - cx) * shrink, cy + (y - cy) * shrink) for x, y in places]
        per_px = MIN_PER_PX
    return {"track": places[:len(track)], "heard": places[len(track):], "per_px": per_px}


def scale_bar(per_px, width):
    """A round length about a quarter of the width: {'metres', 'px', 'text'}, or None with nothing to measure."""
    if per_px <= 0 or width <= 0:
        return None
    metres = gps_track.nice_length(per_px * width / 4)
    if metres <= 0:
        return None
    return {"metres": metres, "px": metres / per_px, "text": gps_track.distance_text(metres)}


def nearest(places, x, y, reach):
    """The index of the place nearest a tap, if one is within `reach` pixels."""
    best, best_d = None, reach * reach
    for i, (px, py) in enumerate(places):
        d = (px - x) ** 2 + (py - y) ** 2
        if d <= best_d:
            best, best_d = i, d
    return best


def together(places, i, within=TOGETHER_PX):
    """Every place drawn on top of place `i` (itself first). Many networks are heard strongest from the same
    spot, so one tap has to be able to read them all out."""
    x, y = places[i]
    rest = [j for j, (px, py) in enumerate(places) if j != i and (px - x) ** 2 + (py - y) ** 2 <= within * within]
    return [i] + rest


# ---- words

def clock(ts):
    return time.strftime("%H:%M:%S", time.localtime(ts))


def mark_text(row):
    """One heard thing in a line: what it is, its name, address, signal and when it was first heard."""
    name = " ".join((row.get("name") or "").split()) or "(no name)"
    return f"{KINDS[row['kind']]}  {name}  {row['addr']}  {row['rssi']:.0f} dBm  first heard {clock(row['ts'])}"


def picked_lines(rows, limit=5):
    """What a tap on a mark says. The position is the uConsole's, and the words say so."""
    if not rows:
        return []
    at = f"{rows[0]['lat']:.5f}, {rows[0]['lon']:.5f}"
    what = "this" if len(rows) == 1 else f"these {len(rows)}"
    lines = [f"The uConsole was here ({at}) when it heard {what} strongest. That is not where "
             f"{'it is' if len(rows) == 1 else 'they are'}."]
    lines += [mark_text(r) for r in rows[:limit]]
    if len(rows) > limit:
        lines.append(f"and {len(rows) - limit} more at the same spot")
    return lines


def legend_lines():
    return ["Line: the uConsole's own track, from this session's GPS readings, with its start and end marked.",
            f"Square: a Wi-Fi network. Diamond: a Bluetooth address. Each mark is {MEANING}.",
            "Marks come from the wardrive and Bluetooth logs saved during the session. Tap a mark to read it; "
            "tap the track to go to that moment."]


def status_line(track, heard):
    """What the map holds, or plainly why it holds nothing."""
    if not track:
        return NO_FIX
    wifi = sum(1 for r in heard or () if r["kind"] == "wifi")
    bt = sum(1 for r in heard or () if r["kind"] == "bt")
    text = f"{len(track)} positions of the uConsole."
    if heard is None:
        return text + " Reading the saved logs..."
    if not heard:
        return text + " No saved wardrive or Bluetooth log has anything first heard during this session."
    return text + (f" {wifi} Wi-Fi network{'' if wifi == 1 else 's'} and {bt} Bluetooth address{'' if bt == 1 else 'es'} "
                   "from the saved logs, first heard during this session.")


# ---- exports

def _xml(text):
    """Text safe inside XML: network names are whatever a stranger chose to broadcast."""
    return escape(_BAD_XML.sub("", str(text)))


def _utc(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def describe(row):
    return (f"{WHAT[row['kind']]} {row['addr']}, {row['rssi']:.0f} dBm at its strongest, first heard {_utc(row['ts'])}. "
            f"This point is {MEANING}.")


def gpx_text(track, heard, name="uConsole walk"):
    """The walk as GPX 1.1: a waypoint for each heard thing, then the track. Built on the GPS page's own writer."""
    lines = gps_track.gpx_text(track, name).split("\n")
    points = []
    for r in heard or ():
        label = " ".join((r.get("name") or "").split()) or r["addr"]
        points.append(f'<wpt lat="{r["lat"]:.6f}" lon="{r["lon"]:.6f}"><time>{_utc(r["ts"])}</time>'
                      f"<name>{_xml(label)}</name><desc>{_xml(describe(r))}</desc><type>{KINDS[r['kind']]}</type></wpt>")
    return "\n".join(lines[:2] + points + lines[2:])          # waypoints go before the track in a GPX file


def geojson_text(track, heard, name="uConsole walk"):
    """The walk as GeoJSON: a LineString for the track (a Point if there was only one fix) and a Point with
    properties for each heard thing."""
    features = []
    if track:
        line = [[round(p["lon"], 6), round(p["lat"], 6)] for p in track]
        shape = {"type": "LineString", "coordinates": line} if len(line) > 1 else {"type": "Point", "coordinates": line[0]}
        features.append({"type": "Feature", "geometry": shape,
                         "properties": {"kind": "track", "name": name, "what": "the uConsole's own track",
                                        "start": _utc(track[0]["t"]), "end": _utc(track[-1]["t"]),
                                        "times": [_utc(p["t"]) for p in track]}})
    for r in heard or ():
        features.append({"type": "Feature",
                         "geometry": {"type": "Point", "coordinates": [round(r["lon"], 6), round(r["lat"], 6)]},
                         "properties": {"kind": "heard", "type": KINDS[r["kind"]], "name": r.get("name") or "",
                                        "address": r["addr"], "rssi_dbm": r["rssi"], "first_heard": _utc(r["ts"]),
                                        "channel": r.get("channel") or "", "security": r.get("auth") or "",
                                        "position_is": MEANING}})
    return json.dumps({"type": "FeatureCollection", "features": features}, indent=1) + "\n"


def export_path(session_path, kind):
    return os.path.splitext(session_path)[0] + "." + kind


def write_private(path, text):
    """Write a file only its owner can read, replacing an older copy. Never follows a symlink."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as f:
        os.chmod(f.fileno(), 0o600)                           # an older copy keeps its mode unless told
        f.write(text)
    return path


def export(kind, session_path, track, heard):
    """Write the GPX or GeoJSON beside the session file, private like it, and return its path."""
    if kind not in EXPORTS:
        raise ValueError(f"unknown export: {kind}")
    name = os.path.splitext(os.path.basename(session_path))[0]
    text = gpx_text(track, heard, name) if kind == "gpx" else geojson_text(track, heard, name)
    return write_private(export_path(session_path, kind), text)


# ---- the drawing

def build_view(win):
    from PyQt6.QtCore import QPoint, pyqtSignal
    from PyQt6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPen
    from PyQt6.QtWidgets import QWidget
    from . import pixel

    def theme():
        return win.theme if getattr(win, "theme", None) in ("light", "dark") else "dark"

    class MapView(QWidget):
        """The track and the marks. Repainted only when it is given something new or touched."""
        moved = pyqtSignal(float)                # the track was touched: the time of that position
        picked = pyqtSignal(object)              # marks were touched: their rows

        def __init__(self):
            super().__init__()
            self.track, self.heard, self.here, self.chosen, self.words = [], [], None, [], ""
            self.fonts, self.available = {}, set(QFontDatabase.families())
            self.setMinimumHeight(170)

        def show_walk(self, track, heard, words=""):
            self.track, self.heard, self.words = list(track or ()), list(heard or ()), words
            self.here, self.chosen = None, []
            self.update()

        def set_here(self, ts, hold):
            here = here_index(self.track, ts, hold)
            if here != self.here:
                self.here = here
                self.update()

        def placed(self):
            return layout(self.track, self.heard, self.width(), self.height())

        def pixel_font(self, px):
            if px not in self.fonts:
                family = pixel.font_family(px, self.available)
                f = QFont(family) if family else QFont()
                f.setPixelSize(px)
                f.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
                self.fonts[px] = f
            return self.fonts[px]

        def text(self, p, x, y, s, colour, px=16):
            p.setFont(self.pixel_font(px))
            p.setPen(QColor(colour))
            p.drawText(QPoint(int(x), int(y + px - 3)), s)

        def mark(self, p, kind, x, y, colour, grow=0):
            """A square for Wi-Fi, a diamond for Bluetooth: told apart by shape as well as colour."""
            x, y, c = int(x), int(y), QColor(colour)
            if kind == "wifi":
                p.fillRect(x - 4 - grow, y - 4 - grow, 9 + 2 * grow, 9 + 2 * grow, c)
            else:
                for half in (1, 3, 5):
                    other = 6 - half + grow
                    p.fillRect(x - half - grow, y - other, 2 * (half + grow) + 1, 2 * other + 1, c)

        def paintEvent(self, _):
            pal = pixel.palette(theme())
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(pal["panel"]))
            w, h = self.width(), self.height()
            if not self.track:
                wide = max(20, (w - 24) // 8)
                words, line, y = (self.words or NO_FIX).split(), "", 12
                for word in words + [""]:
                    if word and len(line) + len(word) + 1 <= wide:
                        line = f"{line} {word}".strip()
                        continue
                    self.text(p, 12, y, line, pal["text"])
                    line, y = word, y + 20
                p.end()
                return
            lay = self.placed()
            line = lay["track"]
            p.setPen(QPen(QColor(pal["dim"]), 2))
            for a, b in zip(line, line[1:]):
                p.drawLine(QPoint(int(a[0]), int(a[1])), QPoint(int(b[0]), int(b[1])))
            colours = {"wifi": pal["data"], "bt": pal["accent2"]}
            for j in self.chosen:
                if j < len(lay["heard"]):
                    self.mark(p, self.heard[j]["kind"], *lay["heard"][j], pal["text"], grow=2)
            for row, (x, y) in zip(self.heard, lay["heard"]):
                self.mark(p, row["kind"], x, y, colours[row["kind"]])
            for (x, y), word, colour in ((line[0], "start", pal["good"]), (line[-1], "end", pal["hot"])):
                p.fillRect(int(x) - 5, int(y) - 5, 11, 11, QColor(pal["text"]))
                p.fillRect(int(x) - 3, int(y) - 3, 7, 7, QColor(colour))
                self.text(p, min(int(x) + 9, w - len(word) * 8 - 2), int(y) - (18 if word == "start" else -2), word,
                          pal["text"])
            if self.here is not None and self.here < len(line):
                x, y = int(line[self.here][0]), int(line[self.here][1])
                for dx, dy, ww, hh in ((-12, -1, 9, 3), (4, -1, 9, 3), (-1, -12, 3, 9), (-1, 4, 3, 9)):
                    p.fillRect(x + dx, y + dy, ww, hh, QColor(pal["warn"]))
                self.text(p, min(x + 14, w - 15 * 8), max(2, y - 20), "you were here", pal["warn"])
            self.text(p, w - 20, 4, "N", pal["text"])
            p.fillRect(w - 17, 22, 2, 12, QColor(pal["text"]))
            bar = scale_bar(lay["per_px"], w)
            if bar:
                px, y = int(bar["px"]), h - 12
                p.fillRect(10, y, px, 2, QColor(pal["text"]))
                p.fillRect(10, y - 4, 2, 6, QColor(pal["text"]))
                p.fillRect(8 + px, y - 4, 2, 6, QColor(pal["text"]))
                self.text(p, 16 + px, y - 12, bar["text"], pal["text"])
            p.end()

        def touch(self, x, y):
            if not self.track:
                return
            lay = self.placed()
            i = nearest(lay["heard"], x, y, REACH)
            if i is not None:
                chosen = together(lay["heard"], i)
                if chosen != self.chosen:
                    self.chosen = chosen
                    self.update()
                    self.picked.emit([self.heard[j] for j in chosen])
                return
            i = nearest(lay["track"], x, y, TRACK_REACH)
            if i is not None:
                self.moved.emit(float(self.track[i]["t"]))

        def mousePressEvent(self, event):
            self.touch(event.position().x(), event.position().y())

        def mouseMoveEvent(self, event):
            self.touch(event.position().x(), event.position().y())

    return MapView()
