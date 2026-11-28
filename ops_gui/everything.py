"""
The Everything tab: one session that records what every source that is already on can hear, side by side.

It does not own a radio. It drives the pages that do (the Wi-Fi survey and the Bluetooth scan), so there is only
ever one listener per radio, and it reads the GPS, the mesh and the SDR only while they are already running. It
never switches a rail on and never starts a service: a source that is off is shown greyed with the reason, joins
by itself if its rail comes on during the session, and drops out if it goes off. Stop only stops the scans this
session started; a scan you had started by hand is left running.

Everything here is passive. Nothing is sent. What is recorded is plain counts and readings, each stamped with the
time and (with a GPS fix) where the uConsole was. It cannot say where anything else is, which way it is, whether
a device is a person, or whether two addresses are the same device, and it does not try. "What changed together"
means two counts changed in the same minute, and nothing more.

Aircraft are a count and nothing else: if the ADS-B receiver service (readsb) is already running, the number of
aircraft it currently hears is read from the file it writes. It is never started and the SDR rail is never switched.

After Stop (or for a saved session) there is a map: the uConsole's own track from the session's GPS readings, with a
mark for each network or address from a wardrive or Bluetooth log saved during the session, placed where the
uConsole was when it heard it strongest. That is not where the device is, and the map says so (see walkmap.py).

A session is written as it goes to a private file beside the field log (session-YYYYMMDD-HHMMSS.jsonl, one JSON
object a line, pushed to the disk at every reading so a flat battery loses nothing). It holds positions, so it
never goes into a problem report, and Settings > Clear removes it with the other logs.

Bookmark keeps the last few minutes of the readings the tab already holds (the session being recorded, or the
saved one that is open, up to the moment picked on the chart) as a small session file of its own, with an optional
note: session-YYYYMMDD-HHMMSS-bookmark.jsonl, in the same format and the same private folder, so it opens from
Saved sessions like any other. It takes no reading of its own. Bookmarks are never deleted by themselves.

The helpers above build_page are pure so they can be tested without a radio, a receiver or a window.
"""
import bisect
import csv
import html
import io
import json
import os
import re
import threading
import time

from . import fieldlog, gps_track, position, walkmap

SAMPLE_EVERY = 5.0                   # seconds between readings
BUCKET_SECONDS = 60.0                # "what changed together" compares one minute with the next
MIN_READINGS = 12                    # fewer readings than this behind a pair of counts: too few to compare
STRONG_SHARE = 0.5                   # changed together in at least half the minutes compared: "strongly"
CAVEAT = ("Readings that change together are not shown to be connected: two things can both follow a third, "
          "such as the time of day or this computer being moved.")
FILE_CAP = 20 * 1024 * 1024          # a session stops growing here (an hour is about a third of a megabyte)
RESTART_SLACK = 20.0                 # a scan that ended this close to its time limit ended by itself
MIN_MOVE_M = 5.0                     # GPS wander smaller than this is not counted as distance moved
VIEWS = (("Whole session", 0), ("Last 5 minutes", 300), ("Last 30 minutes", 1800))
BOOKMARK_SECONDS = 300.0             # how much a bookmark keeps: the last five minutes, or all there is if less
NOTE_MAX = 500                       # characters of a bookmark's note that are kept
VERSION = 1

SOURCES = (("wifi", "Wi-Fi"), ("bt", "Bluetooth"), ("gps", "GPS"), ("mesh", "Mesh"), ("sdr", "SDR"),
           ("adsb", "Aircraft"), ("sys", "This uConsole"))
AIRCRAFT_FILE = "/run/readsb/aircraft.json"   # written about once a second by readsb while it runs
AIRCRAFT_RECENT = 60.0               # an aircraft counts as heard now if a message came from it this recently
AIRCRAFT_STALE = 30.0                # a file not rewritten for this long means the receiver has stopped
AIRCRAFT_MAX_BYTES = 4 * 1024 * 1024
SCANS = ("wifi", "bt")               # the two sources that are a scan on another page, which a session may start
SCAN_NAME = {"wifi": "Wi-Fi survey", "bt": "Bluetooth scan"}
# One lane of the chart each: (key, label, unit). The key is "source.field" in a reading.
SERIES = (("wifi.nets", "Wi-Fi networks", ""), ("wifi.devs", "Wi-Fi device addresses", ""),
          ("bt.addrs", "Bluetooth addresses", ""), ("gps.used", "GPS satellites used", ""),
          ("gps.eph", "GPS accuracy", "m"), ("gps.alt", "Altitude", "m"), ("mesh.nodes", "Mesh nodes", ""),
          ("sdr.mhz", "SDR frequency", "MHz"), ("adsb.heard", "Aircraft heard", ""),
          ("adsb.pos", "Aircraft giving a position", ""), ("sys.battery", "Battery", "%"),
          ("sys.temp", "Temperature", "C"))
# Running totals of different addresses heard since the session started. Addresses, not devices: phones change
# theirs every few minutes, so these can only ever be an upper count.
TOTALS = (("wifi.nets_total", "Different Wi-Fi networks heard"), ("wifi.devs_total", "Different Wi-Fi device addresses heard"),
          ("bt.addrs_total", "Different Bluetooth addresses heard"))
TOGETHER = ("wifi.nets", "wifi.devs", "bt.addrs", "gps.used", "mesh.nodes")    # the counts compared minute by minute
LABELS = {key: label for key, label, _unit in SERIES}
UNITS = {key: unit for key, _label, unit in SERIES}
RAIL_LABEL = {"GPS": "GPS", "LORA": "LoRa", "SDR": "SDR", "USB": "USB"}
# The activity log lines worth a mark on the chart: what the survey noticed about the networks around.
NOTABLE = (("New network", "network"), ("Network security changed", "security"), ("Same name, no security", "security"),
           ("Deauthentication burst", "security"))
_MAC = re.compile(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}")
_NAME = re.compile(r"^session-\d{8}-\d{6}(-bookmark(-\d+)?)?\.(jsonl|csv|gpx|geojson)$")
_STEM = re.compile(r"^session-(\d{8}-\d{6})(-bookmark(?:-\d+)?)?\.jsonl$")


# ---- which sources can be read right now

def availability(snapshot, bt_adapter=False, air_there=False, air_connected=False, air_in_use=False,
                 mesh_service=False, mesh_connected=False, sdr_mhz=None, air_name="wlan1", aircraft=None):
    """{source: (usable now, why not)} from the app's last status and a few plain facts. Nothing here switches
    anything: a source that is off is reported as off, with the reason to show beside it."""
    rails = (snapshot or {}).get("rails") or {}

    def on(rail):
        return rails.get(rail) == "on"
    usb = "" if on("USB") else " (the USB rail is off)"
    out = {"sys": (True, "")}
    if not air_there:
        out["wifi"] = (False, "the internal Wi-Fi radio is not there" + usb)
    elif air_in_use:
        out["wifi"] = (False, f"Sense is listening on {air_name}")
    elif air_connected:
        out["wifi"] = (False, f"{air_name} is connected to a network")
    else:
        out["wifi"] = (True, "")
    out["bt"] = (True, "") if bt_adapter else (False, "the internal Bluetooth radio is not there" + usb)
    out["gps"] = (True, "") if on("GPS") else (False, "the GPS rail is off")
    if not on("LORA"):
        out["mesh"] = (False, "the LoRa rail is off")
    elif not mesh_service:
        out["mesh"] = (False, "the mesh service is not running")
    elif not mesh_connected:
        out["mesh"] = (False, "the app is not connected to the mesh")
    else:
        out["mesh"] = (True, "")
    if not on("SDR"):
        out["sdr"] = (False, "the SDR rail is off")
    elif sdr_mhz is None:
        out["sdr"] = (False, "nothing is receiving (the SDR runs while its own tab is showing)")
    else:
        out["sdr"] = (True, "")
    if not on("SDR"):
        out["adsb"] = (False, "the SDR rail is off")
    elif aircraft is None:
        out["adsb"] = (False, "the aircraft receiver is not running")
    else:
        out["adsb"] = (True, "")
    return out


def _real(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def aircraft_counts(text, now, recent=AIRCRAFT_RECENT, stale=AIRCRAFT_STALE):
    """{'heard': aircraft a message came from in the last minute, 'pos': how many of those are giving a position}
    from the text of readsb's aircraft.json, or None when it is not a live file: damaged, not the right shape, or
    not rewritten lately (the receiver has stopped and left its last file behind). Only the two counts leave
    here: no aircraft address, callsign or position is kept."""
    try:
        doc = json.loads(text)
    except (TypeError, ValueError, RecursionError):
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("aircraft"), list):
        return None
    wrote = doc.get("now")
    if not _real(wrote) or abs(now - wrote) > stale:
        return None
    heard = placed = 0
    for craft in doc["aircraft"]:
        if not isinstance(craft, dict):
            continue
        seen = craft.get("seen")
        if not _real(seen) or seen < 0 or seen > recent:
            continue
        heard += 1
        age = craft.get("seen_pos", 0)                             # how old its last position is, when it says
        if _real(craft.get("lat")) and _real(craft.get("lon")) and _real(age) and 0 <= age <= recent:
            placed += 1
    return {"heard": heard, "pos": placed}


def read_aircraft(path=None, now=None):
    """The counts from the file the aircraft receiver writes, or None when it is not running. Only reads a file:
    it never starts the receiver. Called from a worker thread."""
    try:
        with open(path or AIRCRAFT_FILE, errors="replace") as f:
            text = f.read(AIRCRAFT_MAX_BYTES)
    except OSError:
        return None
    return aircraft_counts(text, time.time() if now is None else now)


def ended_by_itself(ran_seconds, limit):
    """True when a scan stopped about when its own time limit ran out, rather than being stopped early."""
    return limit is not None and ran_seconds >= limit - RESTART_SLACK


def reconcile(avail, running, owned, was_usable, ran=None, limits=None):
    """What a session should do about the two scans right now: {'start': [...], 'owned': set, 'notes': [...]}.

    A scan is started when its radio has just become usable (at the start of the session everything counts as
    just become usable), or when one this session started ran its full time and ended by itself. A scan that
    stopped early was stopped by someone, or by a fault: it is left off and let go of, not started again.
    A scan that is already running is used as it is and never taken over."""
    start, notes, keep = [], [], set(owned or ())
    for src in SCANS:
        ok = bool((avail.get(src) or (False, ""))[0])
        if running.get(src):
            continue
        if not ok:
            if src in keep:
                keep.discard(src)
                notes.append(f"{SCAN_NAME[src]} stopped: {avail[src][1]}")
            continue
        if not (was_usable or {}).get(src):
            start.append(src)
        elif src in keep:
            if ended_by_itself((ran or {}).get(src, 0.0), (limits or {}).get(src)):
                start.append(src)
            else:
                keep.discard(src)
                notes.append(f"{SCAN_NAME[src]} was stopped outside this session, so it is left off")
    return {"start": start, "owned": keep, "notes": notes}


def active_sources(avail, running):
    """The sources a reading is taken from: a scan only while it is running, the rest while they are usable."""
    out = {"sys"}
    out.update(src for src in SCANS if running.get(src))
    out.update(src for src in ("gps", "mesh", "sdr", "adsb") if (avail.get(src) or (False, ""))[0])
    return out


def source_changes(before, now, avail):
    """One line for each source that joined or dropped out since the last reading. Nothing the first time."""
    if before is None:
        return []
    names, out = dict(SOURCES), []
    for key, _label in SOURCES:
        if key in now and key not in before:
            out.append(f"{names[key]} joined the session")
        elif key in before and key not in now:
            why = (avail.get(key) or (False, ""))[1]
            out.append(f"{names[key]} dropped out" + (f": {why}" if why else ""))
    return out


def source_states(avail, running, owned=(), recording=False, sdr_mhz=None):
    """[{'key', 'label', 'on', 'text'}] for the row of sources: what each is doing, or why it is not in."""
    out = []
    for key, label in SOURCES:
        ok, why = avail.get(key) or (False, "")
        if key in SCANS:
            going = bool(running.get(key))
            if going and key in owned:
                text = "listening (started by this session)"
            elif going:
                text = "listening (it was already running, so Stop leaves it running)"
            elif not ok:
                text = why
            else:
                text = "not running" if recording else "ready (Start begins listening)"
            on = going or (ok and not recording)
        elif key == "sys":
            on, text = True, "battery and temperature"
        else:
            on = ok
            text = why if not ok else {"gps": "fix, satellites, accuracy and altitude", "mesh": "nodes the mesh has heard",
                                       "adsb": "how many aircraft the receiver that is already running hears",
                                       "sdr": f"receiving on {sdr_mhz:.3f} MHz" if sdr_mhz is not None else "receiving"}[key]
        out.append({"key": key, "label": label, "on": on, "text": text})
    return out


def sources_html(states, good="#62e06b", muted="#9e9e9e"):
    """The row of sources as rich text: the ones in use in the 'on' colour, the rest greyed with the reason."""
    parts = []
    for s in states:
        colour = good if s["on"] else muted
        parts.append(f'<span style="color:{colour}"><b>{html.escape(s["label"])}</b>: {html.escape(s["text"])}</span>')
    return " &nbsp; ".join(parts)


# ---- building one reading

class Uniques:
    """How many different addresses of each kind have been heard since the session started. The addresses stay
    in memory for counting only: just the counts are written to the session file."""

    def __init__(self):
        self.seen = {}

    def total(self, kind, ids):
        known = self.seen.setdefault(kind, set())
        known.update(i for i in ids or () if i)
        return len(known)


def mac_of(label):
    """The address inside a device label ('printer  (aa:bb:..)' or the bare address), lower case, so a device the
    Network page learns a name for half way through is not counted twice."""
    m = _MAC.search(label or "")
    return m.group(0).lower() if m else (label or "").strip().lower()


def wifi_counts(bssids, device_labels, uniques):
    """What the survey page is showing now, and how many different addresses it has shown this session."""
    nets = [b.lower() for b in bssids or () if b]
    devs = [mac_of(d) for d in device_labels or () if d]
    return {"nets": len(nets), "devs": len(devs), "nets_total": uniques.total("wifi.nets", nets),
            "devs_total": uniques.total("wifi.devs", devs)}


def bt_counts(addrs, uniques):
    addrs = [a.lower() for a in addrs or () if a]
    return {"addrs": len(addrs), "addrs_total": uniques.total("bt.addrs", addrs)}


def stamp(now):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))


def _clean(fields):
    return {k: v for k, v in fields.items() if v is not None and v != ""}


def build_sample(now, active, wifi=None, bt=None, tpv=None, sky=None, mesh_nodes=None, sdr_mhz=None, power=None,
                 temp=None, aircraft=None):
    """One reading: the time, the position when there is a fix, then a small dict for each source that is in.
    A source that is not in has no key at all, which is how the chart knows to leave a gap there."""
    rec = {"k": "sample", "t": stamp(now), "ts": round(now, 1)}
    if "gps" in active:
        f = fieldlog.gps_fields(tpv, sky)
        if f.get("answer") is False:
            rec["gps"] = {"answer": False}
        else:
            rec["gps"] = _clean({"mode": f.get("mode"), "used": f.get("used"), "seen": f.get("seen"),
                                 "eph": f.get("eph"), "alt": f.get("alt_msl"), "hdop": f.get("hdop")})
            if position.good_fix(tpv):
                rec["lat"], rec["lon"] = round(tpv["lat"], 6), round(tpv["lon"], 6)
    if "wifi" in active and wifi is not None:
        rec["wifi"] = dict(wifi)
    if "bt" in active and bt is not None:
        rec["bt"] = dict(bt)
    if "mesh" in active and mesh_nodes is not None:
        rec["mesh"] = {"nodes": mesh_nodes}
    if "sdr" in active and sdr_mhz is not None:
        rec["sdr"] = {"mhz": round(sdr_mhz, 4)}
    if "adsb" in active and aircraft is not None:
        rec["adsb"] = {"heard": int(aircraft.get("heard") or 0), "pos": int(aircraft.get("pos") or 0)}   # counts only
    p = power or {}
    machine = _clean({"battery": p.get("capacity"), "watts": p.get("power"), "temp": temp})
    if machine:
        rec["sys"] = machine
    return rec


def event(now, what, text):
    """A moment worth a mark on the chart. `what` is rail, fix, source, network or security."""
    return {"k": "event", "t": stamp(now), "ts": round(now, 1), "what": what, "text": " ".join(str(text).split())}


def start_record(now, states, every=SAMPLE_EVERY):
    return {"k": "start", "t": stamp(now), "ts": round(now, 1), "v": VERSION, "every": every,
            "sources": {s["key"]: {"on": bool(s["on"]), "text": s["text"]} for s in states}}


def stop_record(now, summary):
    return {"k": "stop", "t": stamp(now), "ts": round(now, 1), "summary": summary}


def rail_changes(before, now):
    """'GPS rail on' and the like, for each rail that changed between two looks at the status."""
    if before is None:
        return []
    return [f"{RAIL_LABEL.get(name, name)} rail {state}" for name, state in sorted((now or {}).items())
            if before.get(name) not in (None, state)]


def fix_event_text(kind, fields=None):
    """Words for what FixWatch noticed. The rail coming on or going off is marked from the status instead."""
    took = (fields or {}).get("seconds_since_rail_on")
    if kind == "gps_first_fix":
        return "First GPS fix" + (f" ({took:.0f} s after the rail came on)" if isinstance(took, (int, float)) else "")
    return {"gps_fix_lost": "GPS fix lost", "gps_fix_back": "GPS fix back"}.get(kind)


def activity_event(text):
    """(what, text) for an activity log line that belongs on the chart, or None for the rest of the log."""
    for title, what in NOTABLE:
        if (text or "").startswith(title):
            return what, text
    return None


# ---- a session, in memory

class Session:
    """The readings and events of one session, as they were written. Built live, or read back from a file."""

    def __init__(self, path=None):
        self.path, self.start, self.stop, self.samples, self.events = path, None, None, [], []

    def add(self, rec):
        kind = (rec or {}).get("k")
        if not isinstance((rec or {}).get("ts"), (int, float)):
            return False
        if kind == "sample":
            self.samples.append(rec)
        elif kind == "event":
            self.events.append(rec)
        elif kind == "start":
            self.start = rec
        elif kind == "stop":
            self.stop = rec
        else:
            return False
        return True

    @property
    def every(self):
        every = (self.start or {}).get("every")
        return every if isinstance(every, (int, float)) and every > 0 else SAMPLE_EVERY

    @property
    def t0(self):
        if self.start:
            return self.start["ts"]
        first = [r["ts"] for r in self.samples[:1] + self.events[:1]]
        return min(first) if first else 0.0

    @property
    def t1(self):
        """The end: when it was stopped, or the last thing written if it never was (a flat battery, say)."""
        last = [r["ts"] for r in self.samples[-1:] + self.events[-1:]]
        if self.stop:
            last.append(self.stop["ts"])
        return max(last) if last else self.t0


def value(sample, key):
    """The number at 'source.field' in a reading, or None when that source was not in or gave no number."""
    src, _, field = key.partition(".")
    v = (sample.get(src) or {}).get(field) if isinstance(sample.get(src), dict) else None
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def series(session, key):
    """[(time, value or None)] with one entry for every reading, so gaps keep their place."""
    return [(s["ts"], value(s, key)) for s in session.samples]


def span(values):
    """(lowest, highest) of the numbers there are, or None with none."""
    nums = [v for v in values if v is not None]
    return (min(nums), max(nums)) if nums else None


def normalise(v, lo, hi):
    """Where a value sits in its own range, 0 to 1. A series that never changed sits in the middle."""
    if v is None:
        return None
    return 0.5 if hi <= lo else min(1.0, max(0.0, (v - lo) / (hi - lo)))


def fmt(key, v):
    """A value as it is shown: counts as whole numbers, the rest with the precision that means something."""
    if v is None:
        return "-"
    unit = UNITS.get(key.replace("_total", ""), "")
    if unit == "MHz":
        return f"{v:.3f} MHz"
    if unit == "m" or unit == "C":
        return f"{v:.1f} {'m' if unit == 'm' else chr(176) + 'C'}"
    return f"{v:.0f}{unit}"


# ---- the chart, as numbers

def view(session, seconds=0):
    """(from, to) for the time axis. Never shorter than a minute, so the first readings do not jump about, and
    it ends one reading past the newest so that one has room to be drawn."""
    t0 = session.t0
    last = session.samples[-1]["ts"] if session.samples else t0
    t1 = max(t0 + 60.0, last + session.every, session.t1)
    if seconds and t1 - seconds > t0:
        t0 = t1 - seconds
    return t0, t1


def to_columns(points, t0, t1, cols, hold):
    """One value (or None) per chart column. A reading is held until the next one, but never longer than `hold`
    seconds, so a source that went quiet leaves a gap rather than a flat line that was never measured."""
    out = [None] * max(0, cols)
    if t1 <= t0 or cols < 1:
        return out
    scale = cols / (t1 - t0)
    for i, (ts, v) in enumerate(points):
        if v is None:
            continue
        end = min(points[i + 1][0] if i + 1 < len(points) else ts + hold, ts + hold)
        a, b = int((ts - t0) * scale), int((end - t0) * scale)
        if end <= t0 or a >= cols:
            continue
        for c in range(max(a, 0), min(max(b, a + 1), cols)):
            out[c] = v
    return out


def lanes(session, t0, t1, cols, at=None):
    """The chart's lanes, one for each series that has any numbers in the session: its label, its real range and
    the value to print (at the reading `at`, or the newest), and for each column where the line sits in the
    lane, 0 to 1. Each lane is scaled to its own range: lanes are for comparing when things changed, not how
    big they are, which is why the real numbers are printed beside them."""
    out = []
    hold = session.every * 2.5
    for key, label, unit in SERIES:
        points = series(session, key)
        rng = span(v for _t, v in points)
        if rng is None:
            continue
        lo, hi = rng
        shown = value(at, key) if at is not None else next((v for _t, v in reversed(points) if v is not None), None)
        out.append({"key": key, "label": label, "unit": unit, "lo": lo, "hi": hi, "shown": shown,
                    "cols": [normalise(v, lo, hi) for v in to_columns(points, t0, t1, cols, hold)]})
    return out


def marks(session, t0, t1, cols):
    """[(column, what, [texts])] for the events in view, one mark a column."""
    found = {}
    if t1 <= t0 or cols < 1:
        return []
    for e in session.events:
        if t0 <= e["ts"] <= t1:
            c = min(cols - 1, int((e["ts"] - t0) * cols / (t1 - t0)))
            found.setdefault(c, (e.get("what", ""), []))[1].append(e.get("text", ""))
    return [(c, what, texts) for c, (what, texts) in sorted(found.items())]


def time_ticks(t0, t1, count=4):
    """[(place 0 to 1, clock time)] along the time axis."""
    if t1 <= t0:
        return []
    fmt_ = "%H:%M:%S" if t1 - t0 < 1800 else "%H:%M"
    return [(i / count, time.strftime(fmt_, time.localtime(t0 + (t1 - t0) * i / count))) for i in range(count + 1)]


def sample_at(session, ts):
    """The reading in force at a time: the newest one at or before it (the first one, before any)."""
    if not session.samples:
        return None
    i = bisect.bisect_right([s["ts"] for s in session.samples], ts) - 1
    return session.samples[max(0, i)]


def step_sample(session, ts, step):
    """The time of the reading `step` readings on from the one in force at `ts` (for the arrow keys)."""
    if not session.samples:
        return ts
    times = [s["ts"] for s in session.samples]
    i = max(0, bisect.bisect_right(times, ts) - 1)
    return times[min(len(times) - 1, max(0, i + step))]


def readout(sample):
    """One line saying everything a single reading holds."""
    if not sample:
        return ""
    parts = [sample["t"][11:]]
    for key, label, _unit in SERIES:
        v = value(sample, key)
        if v is not None:
            parts.append(f"{label} {fmt(key, v)}")
    gps = sample.get("gps") or {}
    if "lat" in sample:
        parts.append(f"at {sample['lat']:.5f}, {sample['lon']:.5f}")
    elif gps.get("answer") is False:
        parts.append("GPS gave no answer")
    elif "gps" in sample:
        parts.append("no GPS fix")
    return "  |  ".join(parts)


# ---- what the session amounts to

def distance_m(samples):
    """How far the uConsole moved, from the readings that had a fix, or None with fewer than two. Wander smaller
    than MIN_MOVE_M is not added up: standing still for an hour is not a walk."""
    fixes = [s for s in samples if "lat" in s and "lon" in s]
    if len(fixes) < 2:
        return None
    total, anchor = 0.0, fixes[0]
    for s in fixes[1:]:
        step = gps_track.haversine_m(anchor, s)
        if step >= MIN_MOVE_M:
            total, anchor = total + step, s
    return round(total, 1)


def summarise(session, end=None):
    """The session in a few numbers: how long, how far, and for each series its lowest, highest and last."""
    t0 = session.t0
    t1 = end if end is not None else session.t1
    out = {"start": stamp(t0) if t0 else "", "seconds": round(max(0.0, t1 - t0), 1), "samples": len(session.samples),
           "fix_samples": sum(1 for s in session.samples if "lat" in s), "distance_m": distance_m(session.samples),
           "series": {}, "totals": {}, "events": len(session.events)}
    for key, _label, _unit in SERIES:
        nums = [v for _t, v in series(session, key) if v is not None]
        if nums:
            out["series"][key] = {"min": min(nums), "peak": max(nums), "last": nums[-1], "readings": len(nums)}
    for key, _label in TOTALS:
        nums = [v for _t, v in series(session, key) if v is not None]
        if nums:
            out["totals"][key] = max(nums)
    return out


def duration_text(seconds):
    s = int(max(0, seconds))
    h, rest = divmod(s, 3600)
    m, sec = divmod(rest, 60)
    return f"{h} h {m:02d} min" if h else f"{m} min {sec:02d} s" if m else f"{sec} s"


def summary_lines(summary, events=()):
    """The summary as lines of text for the review."""
    lines = [f"Started {summary.get('start', '').replace('T', ' ')}, ran {duration_text(summary.get('seconds', 0))}, "
             f"{summary.get('samples', 0)} readings."]
    dist = summary.get("distance_m")
    if summary.get("fix_samples"):
        lines.append(f"GPS fix in {summary['fix_samples']} of {summary.get('samples', 0)} readings. "
                     + (f"Moved about {gps_track.distance_text(dist)}." if dist else "Did not move."))
    else:
        lines.append("No GPS fix during the session, so nothing has a position.")
    for key, label, _unit in SERIES:
        s = (summary.get("series") or {}).get(key)
        if s:
            lines.append(f"{label}: {fmt(key, s['min'])} to {fmt(key, s['peak'])}, last {fmt(key, s['last'])}")
    for key, label in TOTALS:
        total = (summary.get("totals") or {}).get(key)
        if total is not None:
            lines.append(f"{label}: {total:.0f}")
    if (summary.get("totals") or {}):
        lines.append("These count addresses, not devices or people: phones, watches and earbuds change their "
                     "address every few minutes.")
    events = list(events)
    lines.append(f"Events: {len(events)}" if events else "Events: none")
    lines += [f"  {e['t'][11:]}  {e.get('text', '')}" for e in events[-30:]]
    return lines


def compact_summary(summary):
    """The summary flattened to a few fields, for one line of the field log."""
    out = {"seconds": summary.get("seconds"), "samples": summary.get("samples"), "fix_samples": summary.get("fix_samples"),
           "distance_m": summary.get("distance_m"), "events": summary.get("events")}
    out.update({key.replace(".", "_") + "_peak": s["peak"] for key, s in (summary.get("series") or {}).items()})
    out.update({key.replace(".", "_"): v for key, v in (summary.get("totals") or {}).items()})
    return out


# ---- what changed together

def bucket_means(session, seconds=BUCKET_SECONDS):
    """The session cut into stretches of `seconds`: for each, the average of every count that had readings in
    it, and the last position. Stretches with no readings are simply not there."""
    t0, found = session.t0, {}
    for s in session.samples:
        i = int((s["ts"] - t0) // seconds)
        b = found.setdefault(i, {"i": i, "t": t0 + i * seconds, "sums": {}, "lat": None, "lon": None, "n": 0})
        b["n"] += 1
        for key in TOGETHER:
            v = value(s, key)
            if v is not None:
                total, n = b["sums"].get(key, (0.0, 0))
                b["sums"][key] = (total + v, n + 1)
        if "lat" in s and "lon" in s:
            b["lat"], b["lon"] = s["lat"], s["lon"]
    return [{"i": b["i"], "t": b["t"], "lat": b["lat"], "lon": b["lon"], "n": b["n"],
             "means": {k: total / n for k, (total, n) in b["sums"].items()}} for _i, b in sorted(found.items())]


def direction(before, after):
    """1 for a rise, -1 for a fall, 0 for no real change. A real change is at least one whole thing and at least
    a tenth of what was there, so 40 networks becoming 41 is not called a rise."""
    need = max(1.0, 0.1 * abs(before))
    change = after - before
    return 1 if change >= need else -1 if change <= -need else 0


def changed_together(session, seconds=BUCKET_SECONDS):
    """Minute by minute, where the counts of two different sources both changed.

    Returns {'rows': [...], 'pairs': [...]}. A row is one minute and one pair of counts that both rose or fell
    against the minute before, with the figures. A pair is the tally over the whole session.
    A minute is left out of the tallies (and its rows flagged 'settling') when a scan had only just started or
    stopped: a scan's count always climbs for its first minute or two as it fills in, and that is the scan, not
    the surroundings. This is co-occurrence in time and nothing more: it does not say the same devices were
    involved, or that one change had anything to do with the other."""
    buckets = bucket_means(session, seconds)
    by_i = {b["i"]: b for b in buckets}
    shaken = [e["ts"] for e in session.events if e.get("what") == "source"]
    rows, tally, used = [], {}, {}
    pairs = [(a, b) for n, a in enumerate(TOGETHER) for b in TOGETHER[n + 1:] if a.split(".")[0] != b.split(".")[0]]
    for cur in buckets:
        prev = by_i.get(cur["i"] - 1)
        if prev is None:
            continue
        older = by_i.get(cur["i"] - 2)
        near_change = any(prev["t"] - seconds <= ts < cur["t"] + seconds for ts in shaken)
        moved = None
        if None not in (prev["lat"], cur["lat"]):
            moved = round(gps_track.haversine_m(prev, cur), 0)
        for a, b in pairs:
            if not all(k in bucket["means"] for k in (a, b) for bucket in (prev, cur)):
                continue
            new = older is None or a not in older["means"] or b not in older["means"]
            settling = near_change or new
            da = direction(prev["means"][a], cur["means"][a])
            db = direction(prev["means"][b], cur["means"][b])
            if not settling:
                t = tally.setdefault((a, b), {"a": a, "b": b, "minutes": 0, "same": 0, "opposite": 0, "readings": 0})
                t["minutes"] += 1
                seen = used.setdefault((a, b), set())
                for bucket in (prev, cur):                       # the readings behind the minutes compared
                    if bucket["i"] not in seen:
                        seen.add(bucket["i"])
                        t["readings"] += bucket.get("n", 0)
                if da and db:
                    t["same" if da == db else "opposite"] += 1
            if da and db:
                rows.append({"t": cur["t"], "a": a, "b": b, "a_from": round(prev["means"][a], 1),
                             "a_to": round(cur["means"][a], 1), "b_from": round(prev["means"][b], 1),
                             "b_to": round(cur["means"][b], 1), "same": da == db, "settling": settling,
                             "moved_m": moved, "lat": cur["lat"], "lon": cur["lon"]})
    return {"rows": rows, "pairs": [tally[p] for p in pairs if p in tally]}


def pair_explanation(tally, minimum=MIN_READINGS):
    """One plain line about a pair of counts: which way they changed together, over how many minutes, from how
    many readings, and how often, as a word and the figure it comes from. It says what was measured and no more."""
    a, b = LABELS.get(tally.get("a"), tally.get("a")), LABELS.get(tally.get("b"), tally.get("b"))
    minutes, readings = int(tally.get("minutes") or 0), int(tally.get("readings") or 0)
    same, opposite = int(tally.get("same") or 0), int(tally.get("opposite") or 0)
    if readings < minimum or minutes < 1:
        return f"{a} and {b}: too few readings to compare."
    over = f"over {minutes} minute{'s' if minutes != 1 else ''} compared, from {readings} readings"
    if not same and not opposite:
        return f"{a} and {b}: did not change in the same minute ({over})."
    if same > opposite:
        how, count = "rose together or fell together", same
    elif opposite > same:
        how, count = "one rose as the other fell", opposite
    else:
        how, count = "changed in the same minute, as often the same way as opposite ways", same + opposite
    share = count / minutes
    word = "strongly" if share >= STRONG_SHARE else "loosely"
    return f"{a} and {b}: {how}, {word} ({share:.2f}: {count} of {minutes} minutes), {over}."


def together_lines(result, limit=40):
    """The 'what changed together' list as lines of text, with what it does and does not mean said first."""
    lines = ["What changed together (one minute against the minute before):",
             "This only says two counts changed in the same minute. It does not say the same devices were involved, "
             "or why. Counts change as you move; that is expected."]
    rows, pairs = result.get("rows") or [], result.get("pairs") or []
    if not pairs and not rows:
        return lines + ["Not enough yet: it needs two sources with counts for at least three minutes."]
    for p in pairs:
        both = p["same"] + p["opposite"]
        lines.append(f"{LABELS[p['a']]} and {LABELS[p['b']]}: in {p['minutes']} minute{'s' if p['minutes'] != 1 else ''} "
                     f"compared, both changed in {both} (the same way in {p['same']}, opposite ways in {p['opposite']}).")
        lines.append("  " + pair_explanation(p))
    if pairs:
        lines.append(CAVEAT)

    def word(a, b):
        return "rose" if b > a else "fell"
    for r in rows[-limit:]:
        text = (f"  {time.strftime('%H:%M', time.localtime(r['t']))}  {LABELS[r['a']]} {word(r['a_from'], r['a_to'])} "
                f"{r['a_from']:.0f} to {r['a_to']:.0f}, {LABELS[r['b']]} {word(r['b_from'], r['b_to'])} "
                f"{r['b_from']:.0f} to {r['b_to']:.0f}")
        if r.get("moved_m") is not None:
            text += f", moved about {r['moved_m']:.0f} m"
        if r.get("settling"):
            text += " (a scan had just started or stopped, so not counted above)"
        lines.append(text)
    if not rows:
        lines.append("  No minute where two counts both changed.")
    return lines


def live_lines(session, count=8):
    """What is shown under the chart while recording: the newest events."""
    if not session.events:
        return ["Events appear here as they happen: a rail switched, a GPS fix, a source joining or dropping out, "
                "and what the survey notices about the networks around."]
    return [f"{e['t'][11:]}  {e.get('text', '')}" for e in session.events[-count:]]


# ---- the file

def session_dir():
    from . import reports
    return reports.state_dir()


class Writer:
    """The session file: made private, appended to one line at a time. Never raises once it is open: a session
    that cannot be written is not a reason for the app to stop."""

    def __init__(self, now=None, directory=None, cap=FILE_CAP):
        now = time.time() if now is None else now
        directory = directory or session_dir()
        os.makedirs(directory, mode=0o700, exist_ok=True)
        for later in range(60):                               # two sessions in one second: the next second's name
            self.path = os.path.join(directory, time.strftime("session-%Y%m%d-%H%M%S.jsonl", time.localtime(now + later)))
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND, 0o600)
                break
            except FileExistsError:
                continue
        else:
            raise FileExistsError(f"too many sessions started at once in {directory}")
        self.file, self.lock, self.cap, self.size, self.full = os.fdopen(fd, "a"), threading.Lock(), cap, 0, False

    def write(self, rec, sync=False):
        """Append one line. With `sync` it is pushed all the way to the disk, so a flat battery a second later
        still has it. Past the size cap readings are dropped (said once in the file); the end is still written."""
        try:
            with self.lock:
                if self.file is None:
                    return False
                if self.size >= self.cap and rec.get("k") in ("sample", "event"):
                    if self.full:
                        return False
                    self.full = True
                    rec = event(rec.get("ts", time.time()), "source", "The session file is full: nothing more is recorded")
                line = json.dumps(rec, separators=(",", ":"), default=str) + "\n"
                self.file.write(line)
                self.file.flush()
                if sync:
                    os.fsync(self.file.fileno())
                self.size += len(line)
            return True
        except (OSError, TypeError, ValueError):
            return False

    def close(self):
        with self.lock:
            f, self.file = self.file, None
        if f is not None:
            try:
                os.fsync(f.fileno())
                f.close()
            except OSError:
                pass


def files(directory=None):
    """Every saved session on this device, oldest first."""
    d = directory or session_dir()
    try:
        return sorted(os.path.join(d, f) for f in os.listdir(d) if _NAME.match(f) and f.endswith(".jsonl"))
    except OSError:
        return []


def all_files(directory=None):
    """Every file this tab has written: the sessions and the CSV, GPX and GeoJSON copies made from them. For
    Clear."""
    d = directory or session_dir()
    try:
        return sorted(os.path.join(d, f) for f in os.listdir(d) if _NAME.match(f))
    except OSError:
        return []


def load(path):
    """Read a session back. Lines that are cut short or are not ours are skipped: a session that ended with the
    battery is still worth reading."""
    session = Session(path)
    try:
        with open(path) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    session.add(rec)
    except OSError:
        pass
    session.samples.sort(key=lambda r: r["ts"])
    session.events.sort(key=lambda r: r["ts"])
    return session


def picker_label(path, size=None):
    """How a saved session is named in the picker: when it started (or, for a bookmark, when it was made) and
    how big it is."""
    name = os.path.basename(path)
    found = _STEM.match(name)
    try:
        when = time.strftime("%d %b %Y %H:%M:%S", time.strptime(found.group(1), "%Y%m%d-%H%M%S"))
        if found.group(2):
            when = "Bookmark " + when
    except (AttributeError, ValueError):
        when = name
    if size is None:
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
    return f"{when}  ({max(1, round(size / 1024))} KB)"


def last_window(samples, seconds=BOOKMARK_SECONDS, now=None):
    """The readings from the `seconds` up to `now`, both ends included, oldest first. Readings stamped after
    `now` (a moment picked further back on the chart) are left out, and so is anything without a time."""
    kept = [s for s in samples or () if isinstance(s, dict) and isinstance(s.get("ts"), (int, float))
            and not isinstance(s.get("ts"), bool)]
    if now is None:
        now = max((s["ts"] for s in kept), default=0.0)
    return sorted((s for s in kept if now - seconds <= s["ts"] <= now), key=lambda s: s["ts"])


def bookmark_until(recording, scrub, samples, now):
    """The moment a bookmark ends: now while recording, else the moment picked on the chart, else the last
    reading of the saved session that is open."""
    if recording:
        return now
    if isinstance(scrub, (int, float)):
        return scrub
    return max((s["ts"] for s in samples or () if isinstance(s.get("ts"), (int, float))), default=now)


def clean_note(note):
    return " ".join(str(note or "").split())[:NOTE_MAX]


def bookmark_records(session, samples, made, note="", seconds=BOOKMARK_SECONDS):
    """The lines of a bookmark file, in the same format as a session: a start line (saying it is a bookmark,
    when it was made and the note), the readings, the events among them, and a stop line with the summary.
    None when there are no readings, so nothing is saved."""
    samples = sorted(samples or (), key=lambda s: s["ts"])
    if not samples:
        return None
    t0, t1 = samples[0]["ts"], samples[-1]["ts"]
    every = getattr(session, "every", SAMPLE_EVERY)
    start = {"k": "start", "t": stamp(t0), "ts": t0, "v": VERSION, "every": every,
             "sources": dict((getattr(session, "start", None) or {}).get("sources") or {}),
             "bookmark": {"made": stamp(made), "seconds": seconds, "note": clean_note(note),
                          "from": os.path.basename(getattr(session, "path", None) or "")}}
    events = sorted((e for e in getattr(session, "events", ()) or () if t0 <= e.get("ts", -1) <= t1),
                    key=lambda e: e["ts"])
    part = Session()
    for rec in [start] + samples + events:
        part.add(rec)
    return [start] + samples + events + [stop_record(t1, summarise(part, t1))]


def bookmark_text(records):
    """The file's content: one JSON object a line, exactly as a session is written."""
    return "".join(json.dumps(rec, separators=(",", ":"), default=str) + "\n" for rec in records or ())


def bookmark_note(session):
    """The note of a bookmark that has been opened, or None when it is an ordinary session."""
    mark = (getattr(session, "start", None) or {}).get("bookmark")
    return clean_note(mark.get("note")) if isinstance(mark, dict) else None


def save_bookmark(records, made=None, directory=None):
    """Write a bookmark beside the sessions, private, under a name that cannot be taken twice. Returns the
    path, or None when there was nothing to save."""
    if not records:
        return None
    made = time.time() if made is None else made
    directory = directory or session_dir()
    os.makedirs(directory, mode=0o700, exist_ok=True)
    base = time.strftime("session-%Y%m%d-%H%M%S-bookmark", time.localtime(made))
    for n in range(1, 1000):
        path = os.path.join(directory, base + ("" if n == 1 else f"-{n}") + ".jsonl")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            break
        except FileExistsError:
            continue
    else:
        raise FileExistsError(f"too many bookmarks made at once in {directory}")
    with os.fdopen(fd, "w") as f:
        f.write(bookmark_text(records))
        f.flush()
        os.fsync(f.fileno())
    os.chmod(path, 0o600)
    return path


def csv_text(session):
    """The session as a spreadsheet: one row a reading, a column for each series and running total, and the
    events that happened since the reading before in the last column."""
    keys = [key for key, _l, _u in SERIES] + [key for key, _l in TOTALS]
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["time", "epoch", "lat", "lon"] + keys + ["events"])
    events, at = list(session.events), 0
    for n, s in enumerate(session.samples):
        said = []
        last = n == len(session.samples) - 1
        while at < len(events) and (events[at]["ts"] <= s["ts"] or last):
            said.append(events[at].get("text", ""))
            at += 1
        w.writerow([s["t"], s["ts"], s.get("lat", ""), s.get("lon", "")]
                   + ["" if value(s, k) is None else value(s, k) for k in keys] + ["; ".join(said)])
    for e in events[at:] if not session.samples else []:
        w.writerow([e["t"], e["ts"], "", ""] + [""] * len(keys) + [e.get("text", "")])
    return out.getvalue()


def csv_path(path):
    return os.path.splitext(path)[0] + ".csv"


def export_csv(session, path=None):
    """Write the CSV next to the session file, private like it, and return its path."""
    path = path or csv_path(session.path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(csv_text(session))
    os.chmod(path, 0o600)
    return path


def delete(path):
    """Remove a session and the CSV, GPX and GeoJSON made from it. Only ever a session file: anything else is
    refused."""
    if not _NAME.match(os.path.basename(path or "")) or not path.endswith(".jsonl"):
        return 0
    removed = 0
    for p in [path, csv_path(path)] + [walkmap.export_path(path, kind) for kind in walkmap.EXPORTS]:
        try:
            os.remove(p)
            removed += 1
        except OSError:
            pass
    return removed


# ---- the page

def build_page(win):
    from PyQt6.QtCore import QObject, QPoint, QTimer, Qt, pyqtSignal
    from PyQt6.QtGui import QColor, QFont, QFontDatabase, QKeySequence, QPainter, QShortcut
    from PyQt6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
                                 QWidget)
    from . import bt_panel, home_panel, pixel, sense, statusbar, style, survey

    limits = {"wifi": survey.SECONDS, "bt": bt_panel.SECONDS}
    GUTTER, STEP, AXIS = 240, 4, 20          # the labels' column, pixels a chart column, the time axis
    LANE_COLOURS = ("data", "good", "label", "warn", "accent2", "hot", "dim", "data", "good", "label")
    MARK_COLOURS = {"rail": "accent", "fix": "good", "source": "dim", "network": "data", "security": "hot"}

    def theme():
        return win.theme if getattr(win, "theme", None) in ("light", "dark") else "dark"

    def gpsd_lines():
        """Ask gpsd, the one shared way where this checkout has it. Only ever called from the worker thread."""
        ask = getattr(position, "gpsd_lines", None)
        return ask(SAMPLE_EVERY) if ask else home_panel.query_gpsd()

    class Signals(QObject):
        procs = pyqtSignal(object, bool)
        sample = pyqtSignal(object)
        loaded = pyqtSignal(object)
        heard = pyqtSignal(object)

    class Chart(QWidget):
        """Every series on one time axis, a lane each. Repainted when it is given something new, or scrubbed."""
        scrubbed = pyqtSignal()

        def __init__(self):
            super().__init__()
            self.session, self.span, self.scrub = None, (0.0, 1.0), None
            self.fonts, self.available = {}, set(QFontDatabase.families())
            self.setMinimumHeight(170)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        def show_session(self, session, span):
            self.session, self.span = session, span
            self.update()

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

        def cols(self):
            return max(1, (self.width() - GUTTER - 8) // STEP)

        def at_cursor(self):
            if self.session is None or self.scrub is None:
                return None
            return sample_at(self.session, self.scrub)

        def paintEvent(self, _):
            pal = pixel.palette(theme())
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(pal["panel"]))
            s = self.session
            if s is None or not s.samples:
                self.text(p, 12, 12, "Press Start to record what the sources that are already on can hear.", pal["dim"])
                p.end()
                return
            t0, t1 = self.span
            cols, h = self.cols(), self.height()
            rows = lanes(s, t0, t1, cols, self.at_cursor())
            lane_h = max(20, (h - AXIS - 2) // max(1, len(rows)))
            for i, lane in enumerate(rows):
                y = 2 + i * lane_h
                colour = QColor(pal[LANE_COLOURS[i % len(LANE_COLOURS)]])
                p.fillRect(0, y + lane_h - 1, self.width(), 1, QColor(pal["line"]))
                shown = fmt(lane["key"], lane["shown"])
                if lane_h >= 36:                                   # room for two lines: the name, then the numbers
                    self.text(p, 6, y + 1, lane["label"], pal["text"])
                    self.text(p, 6, y + 17, f"{shown}  ({fmt(lane['key'], lane['lo'])} to {fmt(lane['key'], lane['hi'])})",
                              colour.name())
                else:
                    self.text(p, 6, y + 1, pixel.fit(f"{lane['label']} {shown}", (GUTTER - 10) // 8), colour.name())
                room, prev = max(2, lane_h - 10), None
                for c, frac in enumerate(lane["cols"]):
                    if frac is None:
                        prev = None
                        continue
                    yy = y + 4 + int((1.0 - frac) * room) // 2 * 2
                    p.fillRect(GUTTER + c * STEP, yy, STEP, 2, colour)
                    if prev is not None and prev != yy:
                        p.fillRect(GUTTER + c * STEP, min(prev, yy), 2, abs(prev - yy) + 2, colour)
                    prev = yy
            bottom = h - AXIS
            for c, what, _texts in marks(s, t0, t1, cols):
                colour = QColor(pal[MARK_COLOURS.get(what, "dim")])
                p.fillRect(GUTTER + c * STEP, 2, 6, 6, colour)
                colour.setAlpha(120)
                p.fillRect(GUTTER + c * STEP, 8, 2, bottom - 8, colour)
            for place, words in time_ticks(t0, t1):
                x = GUTTER + int(place * cols * STEP)
                p.fillRect(min(x, self.width() - 2), bottom, 1, 4, QColor(pal["dim"]))
                wide = len(words) * 8
                self.text(p, max(GUTTER, min(x - wide // 2, self.width() - wide - 2)), bottom + 3, words, pal["dim"])
            if self.scrub is not None and t0 <= self.scrub <= t1:
                x = GUTTER + int((self.scrub - t0) / (t1 - t0) * cols * STEP)
                p.fillRect(x, 0, 2, bottom, QColor(pal["text"]))
            p.end()

        def scrub_to(self, x):
            if self.session is None or not self.session.samples:
                return
            t0, t1 = self.span
            place = min(1.0, max(0.0, (x - GUTTER) / max(1, self.cols() * STEP)))
            self.scrub = t0 + place * (t1 - t0)
            self.update()
            self.scrubbed.emit()

        def mousePressEvent(self, event):
            self.setFocus()
            self.scrub_to(event.position().x())

        def mouseMoveEvent(self, event):
            self.scrub_to(event.position().x())

        def mouseDoubleClickEvent(self, _):
            self.scrub = None                                     # back to following the newest reading
            self.update()
            self.scrubbed.emit()

        def keyPressEvent(self, event):
            step = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1}.get(event.key())
            if step is None or self.session is None or not self.session.samples:
                super().keyPressEvent(event)
                return
            start = self.scrub if self.scrub is not None else self.session.samples[-1]["ts"]
            self.scrub = step_sample(self.session, start, step)
            self.update()
            self.scrubbed.emit()

    class Page(QWidget):
        def __init__(self):
            super().__init__()
            self.recording, self.starting, self.busy, self.run = False, False, False, 0
            self.session, self.writer, self.uniques, self.watch = None, None, None, None
            self.owned, self.was_usable, self.started_at, self.prev_active = set(), {}, {}, None
            self.ticked = set()                                     # scans whose position logging we switched on
            self.prev_rails, self.last_sample, self.procs, self.confirm_until = None, 0.0, (None, False), 0.0
            self.air, self.track, self.heard, self.mapping = None, [], None, False
            self.sig = Signals()
            self.sig.procs.connect(self.got_procs)
            self.sig.sample.connect(self.got_sample)
            self.sig.loaded.connect(self.show_loaded)
            self.sig.heard.connect(self.got_heard)
            self.timer = QTimer(self)                                # runs for the whole session, shown or not
            self.timer.timeout.connect(self.tick)
            self.idle = QTimer(self)                                 # keeps the row of sources true while looking
            self.idle.timeout.connect(lambda: self.recording or self.fetch_procs(False))
            root = QVBoxLayout(self)
            root.setContentsMargins(4, 2, 4, 2)
            root.setSpacing(3)
            head = QFrame()
            head.setObjectName("card")
            hl = QVBoxLayout(head)
            hl.setContentsMargins(10, 4, 10, 4)
            hl.setSpacing(3)
            row = QHBoxLayout()
            row.setSpacing(6)
            self.state = QLabel("No session running.")
            self.state.setStyleSheet("font-size: 14pt; font-weight: bold;")
            self.state.setWordWrap(True)
            self.span_pick = QComboBox()
            for label, seconds in VIEWS:
                self.span_pick.addItem(label, seconds)
            self.span_pick.currentIndexChanged.connect(lambda _=0: self.redraw())
            self.pick = QComboBox()
            self.pick.setToolTip("Open a saved session")
            self.pick.activated.connect(self.open_picked)
            self.export = QPushButton("Export CSV")
            self.export.clicked.connect(self.export_csv)
            self.gpx = QPushButton("GPX")
            self.gpx.setToolTip("Save the track and what was heard as a GPX file beside the session")
            self.gpx.clicked.connect(lambda _=False: self.export_walk("gpx"))
            self.geojson = QPushButton("GeoJSON")
            self.geojson.setToolTip("Save the track and what was heard as a GeoJSON file beside the session")
            self.geojson.clicked.connect(lambda _=False: self.export_walk("geojson"))
            self.map_b = QPushButton("Map")
            self.map_b.setToolTip("Where the uConsole went, and where it was when it heard each thing strongest")
            self.map_b.clicked.connect(self.toggle_map)
            self.remove = QPushButton("Delete")
            self.remove.clicked.connect(self.delete)
            self.go = QPushButton("Start")
            self.go.clicked.connect(self.toggle)
            self.mark = QPushButton("Bookmark")
            self.mark.setToolTip("Keep the last 5 minutes of readings shown here as a small saved session, with a "
                                 "note (Ctrl+B)")
            self.mark.clicked.connect(self.bookmark)
            QShortcut(QKeySequence("Ctrl+B"), self, activated=self.bookmark,
                      context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
            row.addWidget(self.state, 1)
            for w in (self.span_pick, self.pick, self.map_b, self.export, self.gpx, self.geojson, self.remove,
                      self.mark, self.go):
                w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                row.addWidget(w)
            hl.addLayout(row)
            self.sources = QLabel("")
            self.sources.setTextFormat(Qt.TextFormat.RichText)
            self.sources.setWordWrap(True)
            self.sources.setStyleSheet("font-size: 11pt;")
            hl.addWidget(self.sources)
            self.detail = QLabel(
                "Records what the sources that are already on can hear, side by side on one time line. It never "
                "switches a rail on and nothing is sent. Sessions are kept on this uConsole only, in a private file.")
            self.detail.setWordWrap(True)
            self.detail.setStyleSheet("font-size: 11pt; color: #9e9e9e;")
            hl.addWidget(self.detail)
            root.addWidget(head)
            self.chart = Chart()
            self.chart.scrubbed.connect(self.show_readout)
            root.addWidget(self.chart, 3)
            self.reading = QLabel("")
            self.reading.setWordWrap(True)
            self.reading.setStyleSheet("font-size: 11pt;")
            root.addWidget(self.reading)
            self.map = walkmap.build_view(win)                       # shown in place of the text, by the Map button
            self.map.moved.connect(self.map_moved)
            self.map.picked.connect(self.map_picked)
            root.addWidget(self.map, 3)
            self.map_info = QLabel("")
            self.map_info.setWordWrap(True)
            self.map_info.setStyleSheet("font-size: 11pt;")
            root.addWidget(self.map_info)
            self.notes = QPlainTextEdit()
            self.notes.setReadOnly(True)
            self.notes.setStyleSheet("font-size: 11pt;")
            root.addWidget(self.notes, 2)
            self.sync_buttons()

        # ---- looking at the page
        def showEvent(self, event):
            super().showEvent(event)
            if not self.recording:
                self.fill_picker()
                self.fetch_procs(False)
            self.idle.start(5000)

        def hideEvent(self, event):
            super().hideEvent(event)
            self.idle.stop()

        def say(self, text):
            self.detail.setText(text)

        def sync_buttons(self):
            saved = self.session is not None and not self.recording and bool(self.session.path)
            for b in (self.export, self.gpx, self.geojson, self.map_b):
                b.setEnabled(saved)
            on = saved and self.mapping                              # the map is for a finished session only
            self.map.setVisible(on)
            self.map_info.setVisible(on)
            self.notes.setVisible(not on)
            self.map_b.setText("Summary" if on else "Map")
            self.remove.setEnabled(saved)
            self.remove.setText("Delete")
            self.pick.setEnabled(not self.recording and not self.starting)
            self.go.setEnabled(not self.starting)
            self.go.setText("Stop" if self.recording else "Start")

        def fill_picker(self, keep=None):
            self.pick.blockSignals(True)
            self.pick.clear()
            found = list(reversed(files()))
            self.pick.addItem(f"Saved sessions ({len(found)})", "")
            for path in found:
                self.pick.addItem(picker_label(path), path)
            at = self.pick.findData(keep) if keep else 0
            self.pick.setCurrentIndex(max(0, at))
            self.pick.blockSignals(False)

        # ---- the facts, read on the screen's own thread (all of them quick, local questions)
        def scan_page(self, src):
            if src == "bt":
                return win.pages.get("Bluetooth")
            return getattr(getattr(win, "wifi_panel", None), "survey", None)

        def facts(self):
            bt, sv = self.scan_page("bt"), self.scan_page("wifi")
            listener = getattr(getattr(win, "wifi_panel", None), "sense", None)
            mhz, mesh = self.procs
            avail = availability(
                getattr(win, "snap", None), bt_adapter=bt is not None and bt_panel.adapter_available(),
                air_there=sv is not None and sense.air_available(), air_connected=sense.AIR_IFACE in sense.read_levels(),
                air_in_use=bool(getattr(listener, "feed", None)), mesh_service=bool(mesh),
                mesh_connected=getattr(getattr(win, "mesh_hub", None), "engine", None) is not None, sdr_mhz=mhz,
                air_name=sense.AIR_IFACE, aircraft=self.air)
            running = {"wifi": bool(getattr(sv, "feed", None)),
                       "bt": bool(getattr(bt, "feed", None) or getattr(bt, "waiting", 0))}
            return avail, running

        def show_sources(self, facts=None):
            avail, running = facts or self.facts()
            states = source_states(avail, running, self.owned, self.recording, self.procs[0])
            self.sources.setText(sources_html(states, style.good_colour(theme()), style.MUTED[theme()]))
            return states

        def fetch_procs(self, begin):
            """Whether the mesh service is running, what the SDR is tuned to and whether the aircraft receiver
            is writing its file: a walk through /proc and a file read, so not on the screen's thread."""
            threading.Thread(target=lambda: self.sig.procs.emit((home_panel.scan_processes(), read_aircraft()), begin),
                             daemon=True).start()

        def got_procs(self, found, begin):
            self.procs, self.air = found
            if begin:
                self.begin()
            elif not self.recording:
                self.show_sources()

        def page_counts(self, active):
            """What the two scan pages are showing now. Only their own tables are read; nothing is asked of a radio."""
            out = {}
            sv, bt = self.scan_page("wifi"), self.scan_page("bt")
            try:
                if "wifi" in active and sv is not None:
                    labels = [sv.stas.item(r, 0).text() for r in range(sv.stas.rowCount()) if sv.stas.item(r, 0)]
                    out["wifi"] = wifi_counts([r.get("bssid") for r in sv.ap_list], labels, self.uniques)
                if "bt" in active and bt is not None:
                    addrs = [bt.table.item(r, 2).text() for r in range(bt.table.rowCount()) if bt.table.item(r, 2)]
                    out["bt"] = bt_counts(addrs, self.uniques)
                engine = getattr(getattr(win, "mesh_hub", None), "engine", None)
                if "mesh" in active and engine is not None:
                    out["mesh"] = len(engine.snapshot().get("node_info") or {})
            except Exception:                                    # a page half way through a refill: skip this reading
                pass
            return out

        # ---- starting and stopping
        def toggle(self):
            self.stop() if self.recording else self.start()

        def start(self):
            if self.recording or self.starting:
                return
            self.starting = True
            self.sync_buttons()
            self.fetch_procs(True)

        def put(self, rec):
            """Write a line and keep it in memory. Used from the screen's thread, for events."""
            if self.writer is not None:
                self.writer.write(rec)
            if self.session is not None:
                self.session.add(rec)

        def drive(self, now):
            """Start the scans that should be started, and let go of the ones that stopped. Returns the facts."""
            avail, running = self.facts()
            ran = {src: now - self.started_at.get(src, now) for src in SCANS}
            plan = reconcile(avail, running, self.owned, self.was_usable, ran, limits)
            notes = list(plan["notes"])
            for src in plan["start"]:
                try:
                    self.scan_page(src).start()
                except Exception as exc:
                    notes.append(f"{SCAN_NAME[src]} could not be started: {exc}")
            if plan["start"]:
                running = self.facts()[1]
            self.owned = set(plan["owned"])
            for src in plan["start"]:
                if running.get(src):
                    self.owned.add(src)
                    self.started_at[src] = now
                    notes.append(f"{SCAN_NAME[src]} started by this session")
                    # On the first real walk the session started both scans but neither kept a position log,
                    # because their own "log with position" boxes were not ticked: so there was nothing to put
                    # on the map afterwards. A scan the session starts logs with position for the session.
                    box = getattr(self.scan_page(src), "drive", None)
                    if box is not None and not box.isChecked():
                        box.setChecked(True)
                        self.ticked.add(src)
                        notes.append(f"{SCAN_NAME[src]}: logging with position switched on for this session")
                else:
                    self.owned.discard(src)
                    notes.append(f"{SCAN_NAME[src]} did not start (its own page says why)")
            self.was_usable = {src: bool(avail[src][0] or running.get(src)) for src in SCANS}
            return avail, running, notes

        def begin(self):
            self.starting = False
            now = time.time()
            try:
                self.writer = Writer(now)
            except OSError as exc:
                self.state.setText("Could not start the session.")
                self.say(str(exc))
                self.sync_buttons()
                return
            self.session = Session(self.writer.path)
            self.uniques, self.watch = Uniques(), fieldlog.FixWatch()
            self.owned, self.was_usable, self.started_at, self.prev_active = set(), {}, {}, None
            self.ticked = set()                                     # scans whose position logging we switched on
            self.prev_rails = dict((getattr(win, "snap", None) or {}).get("rails") or {})
            self.recording, self.busy, self.run = True, False, self.run + 1
            self.chart.scrub = None
            avail, running, notes = self.drive(now)
            states = source_states(avail, running, self.owned, True, self.procs[0])
            self.put(start_record(now, states))
            for text in notes:
                self.put(event(now, "source", text))
            self.prev_active = active_sources(avail, running)
            win.activity.listeners.append(self.on_activity)
            win.activity.add("Everything session started")
            self.field_log("everything_start", file=os.path.basename(self.writer.path),
                           sources=sorted(self.prev_active))
            self.say("Recording. Nothing is sent and no rail is switched. Stop ends the scans this session started "
                     "and leaves alone any that were already running.")
            self.sync_buttons()
            self.timer.start(1000)
            self.sample(now, (avail, running))

        def stop(self):
            if not self.recording:
                return
            now = time.time()
            self.timer.stop()
            self.recording, self.busy, self.run = False, False, self.run + 1
            if self.on_activity in win.activity.listeners:
                win.activity.listeners.remove(self.on_activity)
            running = self.facts()[1]
            for src in sorted(self.owned):                           # only the scans this session started
                page = self.scan_page(src)
                if page is not None and running.get(src):
                    try:
                        page.stop()
                    except Exception:
                        pass
            for src in sorted(getattr(self, "ticked", ())):          # put the boxes back as they were found
                box = getattr(self.scan_page(src), "drive", None)
                if box is not None:
                    box.setChecked(False)
            self.owned, self.ticked = set(), set()
            summary = summarise(self.session, now)
            self.put(stop_record(now, summary))
            self.writer.close()
            name = os.path.basename(self.writer.path)
            self.writer = None
            win.activity.add(f"Everything session stopped: {name}")
            self.field_log("everything_stop", file=name, **compact_summary(summary))
            self.fill_picker(self.session.path)
            self.show_review(f"Stopped after {duration_text(summary['seconds'])}: {summary['samples']} readings.")
            self.say(f"Saved to {self.session.path} (private). Drag across the chart to read any moment; "
                     "double click to let go.")
            self.show_sources()

        def shutdown(self):
            """The app is closing: end the session properly so the file has its last line."""
            if self.recording:
                self.stop()

        def field_log(self, what, **fields):
            log = getattr(win, "fieldlog", None)                    # writes only while Settings > Debug logging is on
            if log is not None:
                log.event("session", what=what, **fields)

        def on_activity(self, entry):
            found = activity_event(entry[2])
            if found and self.recording:
                self.put(event(time.time(), *found))

        # ---- one reading
        def tick(self):
            now = time.time()
            rails = dict((getattr(win, "snap", None) or {}).get("rails") or {})
            for text in rail_changes(self.prev_rails, rails):
                self.put(event(now, "rail", text))
            self.prev_rails = rails
            if self.busy and now - self.last_sample < 30:            # the last reading is still being taken
                return
            if now - self.last_sample >= SAMPLE_EVERY:
                self.sample(now)

        def sample(self, now, facts=None):
            self.busy, self.last_sample = True, now
            if facts is None:
                avail, running, notes = self.drive(now)
                for text in notes:
                    self.put(event(now, "source", text))
            else:
                avail, running = facts
            active = active_sources(avail, running)
            for text in source_changes(self.prev_active, active, avail):
                self.put(event(now, "source", text))
            self.prev_active = active
            self.show_sources((avail, running))
            counts = self.page_counts(active)
            power = (getattr(win, "snap", None) or {}).get("power")
            writer, watch, run = self.writer, self.watch, self.run

            def work():
                rec, events, procs, air = None, [], None, None
                try:
                    tpv = sky = None
                    if "gps" in active:
                        lines = gpsd_lines()
                        tpv, sky = fieldlog.newest(lines, "TPV"), fieldlog.newest(lines, "SKY")
                    mode = (tpv or {}).get("mode") if "gps" in active else None
                    for kind, fields in watch.update("gps" in active, mode, now):
                        text = fix_event_text(kind, fields)
                        if text:
                            events.append(event(now, "fix", text))
                    procs = home_panel.scan_processes()
                    temp = statusbar.read_stats(None, 0).get("temp")
                    air = read_aircraft()
                    rec = build_sample(now, active, counts.get("wifi"), counts.get("bt"), tpv, sky, counts.get("mesh"),
                                       procs[0], power, temp, air)
                    for e in events:
                        writer.write(e)
                    writer.write(rec, sync=True)
                except Exception:                                # one reading that failed must not end the session
                    pass
                finally:
                    self.sig.sample.emit((run, rec, events, procs, air))
            threading.Thread(target=work, daemon=True).start()

        def got_sample(self, payload):
            run, rec, events, procs, air = payload
            if run != self.run or not self.recording:               # a reading that came back after Stop
                return
            self.busy = False
            if procs is not None:
                self.procs, self.air = procs, air
            for e in events:
                self.session.add(e)
            if rec is not None:
                self.session.add(rec)
            took = duration_text(time.time() - self.session.t0)
            self.state.setText(f"Recording: {took}, {len(self.session.samples)} readings.")
            self.redraw()
            text = "\n".join(live_lines(self.session))
            if text != self.notes.toPlainText():
                self.notes.setPlainText(text)

        # ---- showing a session
        def redraw(self):
            if self.session is None:
                self.chart.show_session(None, (0.0, 1.0))
                self.reading.setText("")
                return
            self.chart.show_session(self.session, view(self.session, self.span_pick.currentData() or 0))
            self.show_readout()

        def show_readout(self):
            s = self.session
            if s is None or not s.samples:
                self.reading.setText("")
                return
            self.reading.setText(readout(self.chart.at_cursor() or s.samples[-1]))
            self.map.set_here(self.chart.scrub, s.every * 2.5)       # "you were here", only while a moment is picked

        def show_review(self, headline):
            self.chart.scrub = None
            self.state.setText(headline)
            self.sync_buttons()
            self.redraw()
            lines = summary_lines(summarise(self.session), self.session.events) + [""] + \
                together_lines(changed_together(self.session))
            self.notes.setPlainText("\n".join(lines))
            self.load_walk()

        # ---- the map of a finished session
        def toggle_map(self):
            self.mapping = not self.mapping
            self.sync_buttons()

        def load_walk(self):
            """The session's own track straight away; what the saved logs heard during it from a worker thread,
            because that is reading files."""
            s = self.session
            self.track, self.heard = walkmap.track_of(s.samples), None
            if not self.track:
                self.heard = []                                      # no fix: nothing can be placed, so nothing is read
                self.show_walk()
                return
            self.show_walk()
            t0, t1 = s.t0, s.t1

            def work():
                try:
                    rows = walkmap.read_heard(t0, t1)
                except Exception:                                    # a log we cannot make sense of: the track alone
                    rows = []
                self.sig.heard.emit((s, rows))
            threading.Thread(target=work, daemon=True).start()

        def got_heard(self, payload):
            session, rows = payload
            if session is not self.session or self.recording:        # another session was opened meanwhile
                return
            self.heard = rows
            self.show_walk()

        def show_walk(self):
            words = walkmap.status_line(self.track, self.heard)
            self.map.show_walk(self.track, self.heard or [], words)
            self.map_info.setText("\n".join([words] + (walkmap.legend_lines() if self.track else [])))

        def map_moved(self, ts):
            """The track was touched: move the chart's cursor to that moment, which moves "you were here" too."""
            if self.session is None or self.recording:
                return
            self.chart.scrub = ts
            self.chart.update()
            self.show_readout()

        def map_picked(self, rows):
            self.map_info.setText("\n".join(walkmap.picked_lines(rows)))

        def export_walk(self, kind):
            if self.session is None or self.recording or not self.session.path:
                return
            label = {"gpx": "GPX", "geojson": "GeoJSON"}[kind]
            if not self.track:
                self.say(f"No GPS fix during this session, so there is nothing to put in a {label} file.")
                return
            if self.heard is None:
                self.say("Still reading the saved logs. Try again in a moment.")
                return
            try:
                path = walkmap.export(kind, self.session.path, self.track, self.heard)
            except OSError as exc:
                self.say(f"Could not write the {label} file: {exc}")
                return
            self.say(f"Exported to {path} (private): the track and {len(self.heard)} heard. Each of those points is "
                     f"{walkmap.MEANING}.")
            win.activity.add(f"Everything session exported: {os.path.basename(path)}")

        def open_picked(self, index):
            path = self.pick.itemData(index)
            if not path or self.recording:
                return
            self.state.setText("Opening...")
            threading.Thread(target=lambda: self.sig.loaded.emit(load(path)), daemon=True).start()

        def show_loaded(self, session):
            if self.recording:
                return
            self.session = session
            self.span_pick.setCurrentIndex(0)
            ended = "" if session.stop else " It has no end line: it was cut short."
            note = bookmark_note(session)
            if note is None:
                self.show_review(f"Saved session of {picker_label(session.path).split('  (')[0]}.{ended}")
            else:
                self.show_review(f"{picker_label(session.path).split('  (')[0]}." + (f" Note: {note}" if note else ""))
            self.say(f"{session.path} (private). Drag across the chart to read any moment; double click to let go.")

        def bookmark(self):
            """Keep the last few minutes of what the tab holds. Nothing is read or started for it."""
            s = self.session
            if s is None or not s.samples:
                self.say("Nothing to bookmark: there are no readings here yet. Readings are only taken while a "
                         "session is recording, or come from a saved session you open.")
                return
            until = bookmark_until(self.recording, self.chart.scrub, s.samples, time.time())
            picked = last_window(s.samples, BOOKMARK_SECONDS, until)
            if not picked:
                self.say("Nothing to bookmark: there are no readings in the 5 minutes up to that moment.")
                return
            from PyQt6.QtWidgets import QInputDialog
            note, ok = QInputDialog.getText(self, "Bookmark", "A note for this bookmark (you can leave it empty). "
                                            "Enter saves it; Escape saves nothing.")
            if not ok:
                self.say("Bookmark not saved.")
                return
            try:
                path = save_bookmark(bookmark_records(s, picked, time.time(), note))
            except OSError as exc:
                self.say(f"Could not save the bookmark: {exc}")
                return
            took = duration_text(picked[-1]["ts"] - picked[0]["ts"])
            self.say(f"Bookmarked {len(picked)} readings ({took}) to {path} (private). It opens from Saved "
                     "sessions like any other session.")
            win.activity.add(f"Everything bookmark saved: {os.path.basename(path)}")
            if not self.recording:
                self.fill_picker(s.path)

        def export_csv(self):
            if self.session is None or self.recording or not self.session.path:
                return
            try:
                path = export_csv(self.session)
            except OSError as exc:
                self.say(f"Could not write the CSV: {exc}")
                return
            self.say(f"Exported to {path} (private).")
            win.activity.add(f"Everything session exported: {os.path.basename(path)}")

        def delete(self):
            if self.session is None or self.recording or not self.session.path:
                return
            if time.time() > self.confirm_until:                     # asked twice: this cannot be undone
                self.confirm_until = time.time() + 4
                self.remove.setText("Really delete?")
                QTimer.singleShot(4000, lambda: self.remove.setText("Delete"))
                return
            self.confirm_until = 0.0
            name = os.path.basename(self.session.path)
            delete(self.session.path)
            self.session, self.track, self.heard = None, [], None
            self.map.show_walk([], [])
            self.state.setText("No session running.")
            self.say(f"Deleted {name} and the files exported from it.")
            self.notes.setPlainText("")
            self.fill_picker()
            self.sync_buttons()
            self.redraw()

    return Page()
