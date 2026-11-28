"""
The Ghost hunt tab: an optional add-on, for fun only.

It is there only when the installer's 'Ghost hunt (for fun)' feature is on (an empty file, enabled, in the
user's own folder below); otherwise nothing of it shows or runs. It cannot detect ghosts or anything else. Every
playful meter is a real measurement of what the app's radios are already hearing, and each one says underneath,
all the time, what it really measures and its real value.

It only reads what the pages already hold, the same way the Signals screensaver does (screensaver.read_heard):
Wi-Fi networks, Bluetooth devices, mesh nodes and GPS satellites. It never switches a rail, starts a scan, a
service or a radio, and never runs a command. The software radio is left out altogether.

A session can be recorded: one line of JSON every two seconds, kept only on this device in the user's own folder
(the folder 0700, each file 0600) and never sent anywhere. A line holds the time, how many things each source
hears and their strengths (rounded), and the meters. No device address, name or position is ever written.

Everything above build() is plain Python and is tested without a screen.
"""
import bisect
import json
import math
import os
import re
import time

from . import screensaver as ss

TAB = "Ghost hunt"
FOR_FUN = ("For fun. This cannot detect ghosts. Every reading here is a real radio or system measurement, "
           "named underneath.")
FORMAT, FORMAT_VERSION = "aiov2-ghost-session", 1
ENTERTAINMENT = "entertainment: not evidence of anything"
SAMPLE_SECONDS = 1.0            # how often the tab reads what the pages hold
RECORD_SECONDS = 2.0            # one line in a session this often
ENERGY_WINDOW = 10.0            # seconds of history ENERGY looks over
ARRIVAL_WINDOW = 60.0           # ARRIVALS compares now with this many seconds ago
HISTORY_SECONDS = 70.0          # how much history is kept in memory
RECORD_MOST_SECONDS = 4 * 3600  # a recording stops by itself after this long
RECORD_MOST_BYTES = 20 * 1000 * 1000   # or once its file would grow past this (20 MB), whichever comes first
STILL_DB = 3.0                  # a change bigger than this (dB), or in how many are heard, ends the stillness
FLARE = 0.08                    # a bar that moves more than this share of its scale flares for a moment
FLARE_HOLD = 1.5                # seconds a flare stays bright
ENERGY_FULL_DB, PRESENCE_FULL, ARRIVAL_FULL, STILL_FULL = 6.0, 40, 10, 120.0   # where each needle reaches the top
ADDRESS = re.compile(r"[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}")
SESSION_NAME = re.compile(r"^ghost-\d{8}-\d{6}\.jsonl$")
NOTHING_SAYS = "silence"
NOTHING_REALLY = "Really: nothing is being received by the radios the app is listening to, so there is nothing to measure."
NAMES = {"wifi": ("Wi-Fi network", "Wi-Fi networks"), "bt": ("Bluetooth device", "Bluetooth devices"),
         "mesh": ("mesh node", "mesh nodes"), "gps": ("GPS satellite", "GPS satellites")}
SAYS = {  # quietest first; chosen by which quarter of its scale the needle is in
    "ENERGY": ("the air is calm", "a faint stirring", "the air is restless", "the air is wild"),
    "PRESENCES": ("an empty-feeling ether", "a few voices in the ether", "a crowded ether", "the ether is packed"),
    "ARRIVALS": ("nothing comes or goes", "a little coming and going", "busy comings and goings",
                 "the ether is bustling"),
    "STILLNESS": ("restless", "settling", "hushed", "deathly still"),
}
INSTRUMENTS = ("ENERGY", "PRESENCES", "ARRIVALS", "STILLNESS")
FIELDS = {key: field for key, _t, _o, _m, field, _lo, _hi, _u in ss.SIGNAL_SOURCES}


# ---- switched on or not
def user_folder(home=None):
    """Where the installer keeps this user's Ghost hunt switch; sessions go in a folder inside it."""
    return os.path.join(home or os.path.expanduser("~"), ".local", "share", "aiov2_ctl", "ghost")


def sessions_folder(home=None):
    return os.path.join(user_folder(home), "sessions")


def enabled(user=None, isfile=os.path.isfile):
    """True when the installer's Ghost hunt feature is on for this user: its switch file is there."""
    return isfile(os.path.join(user or user_folder(), "enabled"))


def wanted(user=None, isfile=os.path.isfile):
    """Whether the app should add the tab at all. Only when the feature is installed; never otherwise."""
    return enabled(user, isfile)


# ---- readings
def real_values(band):
    """A band's bars back in its own unit (dBm, dB or dB-Hz), rounded to a tenth, strongest first."""
    lo, hi = band["lo"], band["hi"]
    return [round(lo + k * (hi - lo), 1) for k in band["bars"]]


def heard_ids(heard):
    """{source: set of ids} for the things in `heard` with a usable strength. Kept in memory only, to tell
    arrivals from departures; never shown and never written to a session."""
    out = {}
    for key, _t, _o, _m, field, lo, hi, _u in ss.SIGNAL_SOURCES:
        items = (heard or {}).get(key)
        if items is None:
            continue
        out[key] = {str(i.get("id", "")) for i in items
                    if isinstance(i, dict) and ss.strength(i.get(field), lo, hi) is not None}
    return out


def make_frame(heard, t):
    """One reading of what the app hears: the visualiser's bands, and the ids (memory only)."""
    return {"t": float(t), "bands": ss.signal_bands(heard), "ids": heard_ids(heard)}


def remember(history, frame, keep=HISTORY_SECONDS):
    """Add a frame, dropping those older than `keep` seconds before it."""
    history.append(frame)
    while history and history[0]["t"] < frame["t"] - keep:
        history.pop(0)
    return history


def counts(bands):
    """{source: how many heard} for the sources that are listening (not off)."""
    return {b["key"]: len(b["bars"]) for b in bands if b["state"] != "off"}


def _band(bands, key):
    for b in bands:
        if b["key"] == key:
            return b
    return None


def changes_db(before, after):
    """Every change in strength between two sets of bands, in dB, comparing each band's strongest with its
    strongest, second with second and so on (no device is followed by name)."""
    out = []
    for b in after:
        a = _band(before, b["key"])
        if a is None:
            continue
        out += [abs(x - y) for x, y in zip(real_values(a), real_values(b))]
    return out


def changed(before, after, threshold=STILL_DB):
    """Whether anything moved more than `threshold` dB, or a band hears a different number of things."""
    if counts(before) != counts(after):
        return True
    return any(d > threshold for d in changes_db(before, after))


def says(name, value):
    words = SAYS[name]
    return words[min(len(words) - 1, int(max(0.0, min(1.0, value)) * len(words)))]


def _instrument(name, value, really):
    value = max(0.0, min(1.0, value))
    return {"name": name, "value": round(value, 3), "says": says(name, value), "really": really}


def _quiet(name):
    return {"name": name, "value": 0.0, "says": NOTHING_SAYS, "really": NOTHING_REALLY}


def energy(history, window=ENERGY_WINDOW):
    now = history[-1]
    recent = [f for f in history if f["t"] >= now["t"] - window]
    diffs = []
    for a, b in zip(recent, recent[1:]):
        diffs += changes_db(a["bands"], b["bands"])
    db = sum(diffs) / len(diffs) if diffs else 0.0
    return _instrument("ENERGY", db / ENERGY_FULL_DB,
                       f"Really: how much the signal strengths it can already hear changed, on average, in the "
                       f"last {window:g} seconds ({db:.1f} dB). Interference, a transmitter changing power, or "
                       f"anything moving near a radio path all cause this.")


def breakdown(bands):
    """'14 Wi-Fi networks, 3 Bluetooth devices, 2 mesh nodes', for the sources that are listening."""
    parts = []
    for key, n in counts(bands).items():
        one, many = NAMES.get(key, (key, key))
        parts.append(f"{n} {one if n == 1 else many}")
    return ", ".join(parts) or "no radio listening"


def presences(history):
    bands = history[-1]["bands"]
    total = sum(counts(bands).values())
    return _instrument("PRESENCES", total / PRESENCE_FULL,
                       f"Really: how many things the radios can hear now ({total}): {breakdown(bands)}.")


def arrivals(history, window=ARRIVAL_WINDOW):
    now = history[-1]
    then = next((f for f in history if f["t"] >= now["t"] - window), now)
    came = went = 0
    for key in set(now.get("ids", {})) | set(then.get("ids", {})):
        a, b = then.get("ids", {}).get(key, set()), now.get("ids", {}).get(key, set())
        came, went = came + len(b - a), went + len(a - b)
    span = now["t"] - then["t"]
    return _instrument("ARRIVALS", (came + went) / ARRIVAL_FULL,
                       f"Really: {came} heard now that were not heard {span:.0f} s ago, and {went} heard then but "
                       f"not now: devices coming into and going out of radio range, or switching on and off.")


def stillness(history, threshold=STILL_DB):
    now = history[-1]
    since, sure = history[0]["t"], False
    for a, b in zip(reversed(history[:-1]), reversed(history)):
        if changed(a["bands"], b["bands"], threshold):
            since, sure = b["t"], True
            break
    seconds = now["t"] - since
    words = f"{seconds:.0f} s" if sure else f"at least {seconds:.0f} s"
    return _instrument("STILLNESS", seconds / STILL_FULL,
                       f"Really: {words} since any signal strength changed by more than {threshold:g} dB or the "
                       f"number of things heard changed.")


def instruments(history):
    """The four meters, from the history of frames (newest last). With nothing received, each says so."""
    if not history:
        return [_quiet(n) for n in INSTRUMENTS]
    if ss.nothing_heard(history[-1]["bands"]):
        return [_quiet(n) for n in INSTRUMENTS]
    return [energy(history), presences(history), arrivals(history), stillness(history)]


# ---- change flares
def flares(before, after, threshold=FLARE):
    """{source: [bar index]} for the bars that jumped by more than `threshold` of their scale since the last
    reading, or are new at that place, so the tab can brighten them for a moment."""
    out = {}
    for b in after or ():
        a = _band(before or (), b["key"])
        old = a["bars"] if a else []
        jumped = [i for i, k in enumerate(b["bars"]) if i >= len(old) or abs(k - old[i]) > threshold]
        if jumped and a is not None:
            out[b["key"]] = jumped
    return out


def keep_flares(lit, new, now, hold=FLARE_HOLD):
    """The flares still bright at `now`: {(source, index): until}, with the new ones added."""
    out = {k: until for k, until in (lit or {}).items() if until > now}
    for key, indices in (new or {}).items():
        for i in indices:
            out[(key, i)] = now + hold
    return out


# ---- sessions
def header_line(started, app="aiov2_ctl ops app"):
    return json.dumps({"format": FORMAT, "version": FORMAT_VERSION, "app": app, "started": round(float(started), 1),
                       "note": ENTERTAINMENT}, sort_keys=True)


def session_line(frame, meters, started):
    """One line of a session: the time, per source how many are heard and their strengths, and the meters.
    Built only from numbers and this module's own words: no id, address or name can get in."""
    bands = [{"source": b["key"], "state": b["state"], "count": len(b["bars"]), "strengths": real_values(b)}
             for b in frame["bands"]]
    line = json.dumps({"time": round(frame["t"], 1), "elapsed": round(frame["t"] - started, 1), "bands": bands,
                       "instruments": [{"name": m["name"], "value": m["value"], "says": m["says"],
                                        "really": m["really"]} for m in meters]}, sort_keys=True)
    if ADDRESS.search(line):
        raise ValueError("a session line may not hold anything that looks like a device address")
    return line


def bands_from(saved):
    """The visualiser's bands again from a line's saved bands."""
    sources = {}
    for b in saved or ():
        key = b.get("source") if isinstance(b, dict) else None
        if key not in FIELDS:
            continue
        if b.get("state") == "off":
            sources[key] = None
        else:
            sources[key] = [{FIELDS[key]: v} for v in b.get("strengths") or () if isinstance(v, (int, float))]
    return ss.signal_bands(sources)


def parse_session(text):
    """(header, [records]) from a session file's text; each record {"time", "elapsed", "bands", "instruments"}.
    Raises ValueError when the first line is not this format's header. A broken line (the last one, cut off when
    the app stopped) is left out."""
    lines = (text or "").splitlines()
    try:
        head = json.loads(lines[0]) if lines else None
    except ValueError:
        head = None
    if not isinstance(head, dict) or head.get("format") != FORMAT or head.get("version") != FORMAT_VERSION \
            or head.get("note") != ENTERTAINMENT:
        raise ValueError("this is not a Ghost hunt session")
    records = []
    for line in lines[1:]:
        try:
            r = json.loads(line)
            if not (math.isfinite(float(r["time"])) and math.isfinite(float(r["elapsed"]))):
                continue
            time.localtime(float(r["time"]))            # a time the replay line cannot show is not kept
            records.append({"time": float(r["time"]), "elapsed": float(r["elapsed"]), "bands": bands_from(r["bands"]),
                            "instruments": [m for m in r.get("instruments") or () if isinstance(m, dict)]})
        except (ValueError, KeyError, TypeError, OverflowError, OSError):
            continue
    records.sort(key=lambda r: r["elapsed"])
    return head, records


def frame_at(records, t):
    """The record showing at `t` seconds into a replay: the last one at or before t (the first before it
    starts), or None for an empty session."""
    if not records:
        return None
    i = bisect.bisect_right([r["elapsed"] for r in records], t) - 1
    return records[max(0, i)]


def replay_length(records):
    return records[-1]["elapsed"] if records else 0.0


def step(records, t, direction):
    """The elapsed time of the next (direction 1) or previous (-1) record from t."""
    times = [r["elapsed"] for r in records]
    if not times:
        return 0.0
    if direction > 0:
        i = bisect.bisect_right(times, t)
        return times[min(i, len(times) - 1)]
    i = bisect.bisect_left(times, t) - 1
    return times[max(0, i)]


def due(last, now, every=RECORD_SECONDS):
    """Whether it is time for the next line of a recording."""
    return last is None or now - last >= every - 1e-6


def recording_limit(started, now, written, next_line=0, most_seconds=RECORD_MOST_SECONDS,
                    most_bytes=RECORD_MOST_BYTES):
    """Why a recording must stop by itself before writing a line of `next_line` bytes, as the words shown on the
    tab, or "" while it may go on: after four hours, or when the file would grow past 20 MB."""
    if now - started >= most_seconds:
        return "Recording stopped by itself after four hours, its longest"
    if written + next_line > most_bytes:
        return "Recording stopped by itself at 20 MB, its largest"
    return ""


def session_name(started):
    return time.strftime("ghost-%Y%m%d-%H%M%S.jsonl", time.localtime(started))


def session_title(name):
    """'28 Nov 2026 14:03:22' for a session's file name, or the name itself."""
    try:
        return time.strftime("%d %b %Y %H:%M:%S", time.strptime(name, "ghost-%Y%m%d-%H%M%S.jsonl"))
    except ValueError:
        return name


def list_sessions(folder, listdir=os.listdir):
    """The saved sessions' file names, newest first."""
    try:
        names = listdir(folder)
    except OSError:
        return []
    return sorted((n for n in names if SESSION_NAME.match(n)), reverse=True)


def open_session(folder, started):
    """A new session file, private to this user: the folders 0700, the file 0600. Returns (path, file)."""
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    parent = os.path.dirname(folder)
    if os.path.basename(parent) == "ghost":
        os.chmod(parent, 0o700)
    path = os.path.join(folder, session_name(started))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND, 0o600)
    os.chmod(path, 0o600)
    return path, os.fdopen(fd, "a", encoding="utf-8")


def replay_mark(head, record):
    """'REPLAY  28 Nov 2026 14:03:22  +00:42' for the record on screen."""
    if record is None:
        return "REPLAY  (empty session)"
    e = int(record["elapsed"])
    return f"REPLAY  {time.strftime('%d %b %Y %H:%M:%S', time.localtime(record['time']))}  +{e // 60:02d}:{e % 60:02d}"



# ---- what the tab's words and buttons say
def button_labels(recording, replaying, playing):
    """The changing buttons' words."""
    return {"record": "Stop recording" if recording else "Record",
            "play": "Pause" if playing else "Play",
            "live": "Back to live" if replaying else "Live"}


def status_text(recording, replay_text, nothing, error=""):
    """The line under the visualiser: what is on screen, whether a session is being recorded, and any problem."""
    parts = [replay_text] if replay_text else ["LIVE: only what the app's radios are already hearing"]
    if not replay_text and nothing:
        parts.append("nothing is being received")
    if recording:
        parts.append("recording, on this device only")
    if error:
        parts.append(error)
    return ".  ".join(parts) + "."


def shown_meters(meters):
    """Four meters to draw, whatever a session file held: any missing one says nothing is being received."""
    by_name = {m.get("name"): m for m in meters or () if isinstance(m, dict)}
    out = []
    for name in INSTRUMENTS:
        m = by_name.get(name)
        if not m or not isinstance(m.get("value"), (int, float)) or isinstance(m.get("value"), bool):
            out.append(_quiet(name))
        else:
            out.append({"name": name, "value": max(0.0, min(1.0, float(m["value"]))),
                        "says": str(m.get("says", "")), "really": str(m.get("really", ""))})
    return out


METER_SEGMENTS = 24


def build(win):
    """The Ghost hunt page. Qt is imported here; what to show is decided by the plain functions above. It only
    reads what the app's pages already hold (screensaver.read_heard) and never starts or switches anything."""
    from PyQt6.QtCore import QPoint, Qt, QTimer
    from PyQt6.QtGui import QColor, QPainter
    from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
                                 QWidget)
    from . import pixel, style

    pal = pixel.palette("dark")             # drawn dark like the Signals screensaver, whatever the theme
    folder = sessions_folder()
    st = {"history": [], "bands": ss.signal_bands({}), "meters": instruments([]), "lit": {}, "error": "",
          "rec": None, "rec_started": None, "rec_last": None, "rec_bytes": 0,
          "records": None, "head": None, "pos": 0.0, "playing": False, "shown": None}

    def words(p, x, y, text, px, colour, align="left", width=0):
        p.setFont(style.pixel_font(px))
        p.setPen(QColor(colour))
        w = p.fontMetrics().horizontalAdvance(text)
        if align == "right":
            x = x + width - w
        elif align == "centre":
            x = x + (width - w) // 2
        p.drawText(QPoint(int(x), int(y + px - max(2, px // 8))), text)

    class Visual(QWidget):
        """The antenna visualiser, laid out by screensaver.signal_layout; a bar that just jumped is brighter."""
        def __init__(self):
            super().__init__()
            self.setMinimumSize(520, 380)
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        def paintEvent(self, _event):
            p = QPainter(self)
            w, h = self.width(), self.height()
            p.fillRect(self.rect(), QColor(pal["bg"]))
            layout = ss.signal_layout(st["bands"], w, h - 28, 0.0, margin=16)
            pitch = ss.SIGNAL_SEGMENT + ss.SIGNAL_SEGMENT_GAP
            for band in (layout or {}).get("bands", ()):
                dim = band["dim"]
                x, y, text = band["caption"]
                words(p, x, y, text, 16, ss.mix(pal["bg"], pal["dim"], 0.6) if dim else pal["label"])
                if band["note"]:
                    x, y, text = band["note"]
                    words(p, x, y, text, 16, pal["dim"])
                p.fillRect(*band["baseline"], QColor(pal["off"] if dim else pal["line"]))
                for x, y, sw, text in band["scale"]:
                    words(p, x, y, text, 16, pal["off"] if dim else pal["dim"], "right", sw)
                total = band["segments"]
                for j, (x, base, bw, on, k) in enumerate(band["bars"]):
                    flare = (band["key"], j) in st["lit"]
                    for i in range(on):
                        ink = pal["text"] if flare else ss.segment_colour(i, total, k, pal["bg"], pal["accent2"],
                                                                         pal["accent"])
                        p.fillRect(x, base - (i + 1) * pitch + ss.SIGNAL_SEGMENT_GAP, bw, ss.SIGNAL_SEGMENT,
                                   QColor(ink))
            if layout and layout["message"]:
                x, y, mw, text = layout["message"]
                words(p, x, y, text, 24, pal["dim"], "centre", mw)
            if st["records"] is not None:                   # a replay is always marked as one
                words(p, 16, h - 26, replay_mark(st["head"], st["shown"]), 16, pal["accent"])
            elif st["rec"] is not None:
                words(p, 16, h - 26, "REC", 16, pal["warn"])
            p.end()

    class Needle(QWidget):
        """One meter's pixel bar."""
        def __init__(self):
            super().__init__()
            self.value = 0.0
            self.setFixedHeight(16)
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        def paintEvent(self, _event):
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(pal["bg"]))
            gap = 3
            sw = max(2, (self.width() - gap * (METER_SEGMENTS - 1)) // METER_SEGMENTS)
            on = ss.lit(self.value, 1.0, METER_SEGMENTS)
            for i in range(METER_SEGMENTS):
                ink = ss.mix(pal["accent2"], pal["accent"], i / (METER_SEGMENTS - 1)) if i < on else pal["off"]
                p.fillRect(i * (sw + gap), 2, sw, 12, QColor(ink))
            p.end()

    page = QWidget()
    root = QVBoxLayout(page)
    root.setContentsMargins(8, 6, 8, 6)
    root.setSpacing(6)
    fun = QLabel(FOR_FUN)                                   # always there, whatever else is on screen
    fun.setWordWrap(True)
    fun.setFont(style.pixel_font(16))
    root.addWidget(fun)

    middle = QHBoxLayout()
    visual = Visual()
    middle.addWidget(visual, 3)
    right = QVBoxLayout()
    right.setSpacing(2)
    meter_widgets = []
    for name in INSTRUMENTS:
        head = QLabel(name)
        head.setFont(style.pixel_font(16))
        needle = Needle()
        really = QLabel("")
        really.setWordWrap(True)
        really.setFont(style.pixel_font(16))
        for widget in (head, needle, really):
            right.addWidget(widget)
        right.addSpacing(6)
        meter_widgets.append((head, needle, really))
    sessions = QListWidget()
    sessions.setVisible(False)
    right.addWidget(sessions, 1)
    right.addStretch(1)
    middle.addLayout(right, 2)
    root.addLayout(middle, 1)

    status = QLabel("")
    status.setWordWrap(True)
    root.addWidget(status)
    row = QHBoxLayout()
    buttons = {}
    for key, text in (("record", "Record"), ("open", "Open session"), ("play", "Play"), ("back", "Step back"),
                      ("next", "Step on"), ("live", "Live")):
        b = QPushButton(text)
        b.setMinimumHeight(40)
        row.addWidget(b)
        buttons[key] = b
    row.addStretch(1)
    root.addLayout(row)

    def replaying():
        return st["records"] is not None

    def refresh():
        for (head, needle, really), m in zip(meter_widgets, shown_meters(st["meters"])):
            head.setText(f"{m['name']}   {m['says']}")
            needle.value = m["value"]
            needle.update()
            really.setText(m["really"])
        labels = button_labels(st["rec"] is not None, replaying(), st["playing"])
        for key in ("record", "play", "live"):
            buttons[key].setText(labels[key])
        for key in ("play", "back", "next", "live"):
            buttons[key].setEnabled(replaying())
        status.setText(status_text(st["rec"] is not None, replay_mark(st["head"], st["shown"]) if replaying() else "",
                                   ss.nothing_heard(st["bands"]), st["error"]))
        visual.update()

    def show(bands, meters, now):
        before, st["bands"], st["meters"] = st["bands"], bands, meters
        st["lit"] = keep_flares(st["lit"], flares(before, bands), now)

    def show_replay(now):
        record = frame_at(st["records"], st["pos"])
        if record is not st["shown"]:
            st["shown"] = record
            show(record["bands"] if record else ss.signal_bands({}), record["instruments"] if record else [], now)

    def tick():
        now = time.time()
        frame = make_frame(ss.read_heard(win), now)        # read only: what the pages already hold
        remember(st["history"], frame)
        live_meters = instruments(st["history"])
        if st["rec"] is not None and due(st["rec_last"], now):
            try:
                line = session_line(frame, live_meters, st["rec_started"]) + "\n"
                size = len(line.encode("utf-8"))
                why = recording_limit(st["rec_started"], now, st["rec_bytes"], size)
                if why:
                    stop_recording()
                    st["error"] = why
                else:
                    st["rec"].write(line)
                    st["rec"].flush()
                    st["rec_bytes"] += size
                    st["rec_last"] = now
            except (OSError, ValueError) as e:
                stop_recording()
                st["error"] = f"Recording stopped: {e}"
        if replaying():
            if st["playing"]:
                st["pos"] += SAMPLE_SECONDS
                if st["pos"] >= replay_length(st["records"]):
                    st["pos"], st["playing"] = replay_length(st["records"]), False
            show_replay(now)
        else:
            show(frame["bands"], live_meters, now)
        refresh()

    def stop_recording():
        if st["rec"] is not None:
            try:
                st["rec"].close()
            except OSError:
                pass
        st["rec"], st["rec_last"] = None, None

    def record():
        st["error"] = ""
        if st["rec"] is not None:
            stop_recording()
        else:
            started = time.time()
            try:
                _path, f = open_session(folder, started)
                head = header_line(started) + "\n"
                f.write(head)
                f.flush()
                st["rec"], st["rec_started"], st["rec_last"] = f, started, None
                st["rec_bytes"] = len(head.encode("utf-8"))
            except OSError as e:
                st["error"] = f"Could not start a recording: {e.strerror or e}"
        refresh()

    def open_list():
        st["error"] = ""
        if sessions.isVisible():
            sessions.setVisible(False)
            buttons["open"].setFocus()
            return
        sessions.clear()
        for name in list_sessions(folder):
            item = QListWidgetItem(session_title(name))
            item.setData(Qt.ItemDataRole.UserRole, name)
            sessions.addItem(item)
        if not sessions.count():
            st["error"] = "No saved sessions yet"
            refresh()
            return
        sessions.setVisible(True)
        sessions.setCurrentRow(0)
        sessions.setFocus()

    def load(item):
        name = item.data(Qt.ItemDataRole.UserRole)
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                head, records = parse_session(f.read())
        except (OSError, ValueError) as e:
            st["error"] = f"Could not open that session: {e}"
            refresh()
            return
        st["head"], st["records"], st["pos"], st["playing"], st["shown"] = head, records, 0.0, False, None
        sessions.setVisible(False)
        buttons["play"].setFocus()
        show_replay(time.time())
        refresh()

    def play():
        if replaying():
            if not st["playing"] and st["pos"] >= replay_length(st["records"]):
                st["pos"] = 0.0
            st["playing"] = not st["playing"]
            refresh()

    def stepped(direction):
        if replaying():
            st["playing"] = False
            st["pos"] = step(st["records"], st["pos"], direction)
            show_replay(time.time())
            refresh()

    def live():
        st["records"], st["head"], st["playing"], st["shown"] = None, None, False, None
        buttons["record"].setFocus()
        refresh()

    buttons["record"].clicked.connect(record)
    buttons["open"].clicked.connect(open_list)
    buttons["play"].clicked.connect(play)
    buttons["back"].clicked.connect(lambda: stepped(-1))
    buttons["next"].clicked.connect(lambda: stepped(1))
    buttons["live"].clicked.connect(live)
    sessions.itemActivated.connect(load)

    timer = QTimer(page)
    timer.timeout.connect(tick)
    timer.start(int(SAMPLE_SECONDS * 1000))
    page.ghost_timer = timer
    page.stop_recording = stop_recording
    refresh()
    return page

