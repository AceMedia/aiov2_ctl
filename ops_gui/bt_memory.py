"""
What the Bluetooth page remembers: which addresses it has heard on other days, and which ones were heard at
several places along the way.

Two things live here, both fed by the passive scan's snapshots and nothing else.

Memory is a small private file of the addresses that can be remembered at all: public ones and random ones that
stay fixed. Each has its name and maker, when it was first and last heard and on how many different days. Private
addresses change every few minutes, so remembering one would only fill the file with addresses that never come
back; they are never stored. The same file holds the addresses you marked as your own.

Travel is a tally kept in memory only, never on disk: for each rememberable address, the places the uConsole
itself was standing when it heard it. An address heard at three or more places at least 300 m apart over at least
ten minutes has moved along with you, and the page says so in exactly those words. That is all that is measured.
It does not know where the device is, what it is or whose it is, and the things you carry yourself show up the
same way. A device that changes its address cannot be picked out like this.

Item trackers get a second, slower rule from Kismet's documentation, kept in the same in-memory Travel: the same
tracker address heard over at least 30 minutes at three or more places at least 100 m apart. For each address that
bt_trackers names as a tracker, the uConsole's own position is noted each time it is heard (at most one note every
30 seconds, the latest few hundred kept, and an address not heard for 48 hours forgotten). Trackers change their
address, some every 15 minutes or so, and different addresses are never joined up by guesswork, so a tracker
that changes its address is not noticed and the absence of a notice means little.
Everything above the classes is a pure function so it can be tested without a radio, a receiver or a screen.
"""
import json
import math
import os
import re
import threading
import time

from . import bt_trackers, position, reports, survey

REMEMBERED = ("public", "random (fixed)")   # the address kinds aiov2_ctl reports that stay the same over days
MAX_ENTRIES = 3000                          # the file stays small on a uConsole that listens for years
SAVE_EVERY = 300                            # seconds between writes while a scan runs and something changed

PLACE_METRES = 300.0                        # two positions closer than this are the same place
MIN_PLACES = 3                              # places an address must be heard at before it is pointed out
MIN_SECONDS = 600                           # and the least time between its first and last hearing
MAX_FIX_ERROR = 100.0                       # a fix worse than this (metres) is too vague to call a place
MAX_PLACES = 40                             # places kept per address: 12 km of walking is plenty
MAX_TALLIED = 2000                          # addresses followed at once
TALLY_KEEP = 2 * 3600                       # an address not heard for this long starts again from nothing

TRACKER_MINUTES = 30                        # Kismet's rule for a separated tracker: heard over this long,
TRACKER_PLACES = 3                          # at this many places,
TRACKER_METRES = 100.0                      # each at least this far from the others (this computer's positions)
MAX_SIGHTINGS = 300                         # sightings kept per tracker address, the latest ones
SIGHTING_EVERY = 30                         # seconds between two sightings noted for one address
TRACKER_KEEP = 48 * 3600                    # a tracker address not heard for this long is forgotten
TRACKER_SOURCE = "bttrackers"               # its optional notification, off unless switched on in Settings
TRACKER_CAVEAT = "A tracker that changes its address is not noticed by this check."
TRACKER_HELP = ("While a scan runs with a GPS fix, a tracker's address heard for 30 minutes or more at 3 places at "
                "least 100 m apart (this computer's own positions) is pointed out in its row. " + TRACKER_CAVEAT +
                " Trackers change their address, so seeing no such note means little.")

# The notification is on unless switched off in Settings. It can only fire while you are scanning with the GPS
# on, once per address, and a needless one costs a glance where a missed one could matter.
NOTIFY_SOURCE = "btplaces"
_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
_WRITE = threading.Lock()                   # a timed write and the one at Stop never share the temp file
_cleared = 0                                # goes up each time Clear local data removes the file

WHY_NOT_REMEMBERED = ("This address changes every few minutes, so it cannot be remembered from one day to the "
                      "next or noticed at several places.")
CAVEAT = ("Things you carry yourself (a watch, earbuds, a phone) show up this way. A device that changes its "
          "address cannot be picked out like this. Tap a row, then Mark as mine, for anything that is yours.")


# ---- pure rules

def normalise(addr):
    """A plain lower case 'aa:bb:cc:dd:ee:ff', or '' for anything not shaped like one."""
    text = addr.strip().lower() if isinstance(addr, str) else ""
    return text if _MAC_RE.match(text) else ""


def rememberable(kind):
    """True for an address that is the same tomorrow: public, or random but fixed. Private addresses and any
    kind this does not know are left out, so nothing is remembered on a guess."""
    return kind in REMEMBERED


def day_of(t):
    """The local calendar day of a time, as '2026-11-25'."""
    return time.strftime("%Y-%m-%d", time.localtime(t))


def haversine_m(lat1, lon1, lat2, lon2):
    """Metres between two positions over the ground (great circle, mean Earth radius)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371008.8 * math.asin(min(1.0, math.sqrt(a)))


def add_place(places, lat, lon, metres=PLACE_METRES, limit=MAX_PLACES):
    """Add a position to a list of places if it is a new place: at least `metres` from every one already there.
    So every pair of places in the list is at least that far apart. Returns True when it was added."""
    if len(places) >= limit or any(haversine_m(lat, lon, p[0], p[1]) < metres for p in places):
        return False
    places.append((lat, lon))
    return True


def spread_m(places):
    """The greatest distance between any two of the places, in metres."""
    return max((haversine_m(a[0], a[1], b[0], b[1]) for i, a in enumerate(places) for b in places[i + 1:]),
               default=0.0)


def usable_fix(tpv, max_error=MAX_FIX_ERROR):
    """True for a fix good enough to call a place: a real fix whose stated error, if it gives one, is modest."""
    if not position.good_fix(tpv):
        return False
    lat, lon = tpv.get("lat"), tpv.get("lon")
    if not (_number(lat) and _number(lon)) or (lat == 0 and lon == 0):   # NaN, or a receiver's empty 0,0
        return False
    errors = [e for e in (tpv.get("epx"), tpv.get("epy")) if isinstance(e, (int, float))]
    return not errors or max(errors) <= max_error


def moved_along(tally, places=MIN_PLACES, seconds=MIN_SECONDS):
    """The rule itself: heard at enough places far enough apart, over long enough."""
    return len(tally.get("places") or []) >= places and tally.get("last", 0) - tally.get("first", 0) >= seconds


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def seen_at_several_places(sightings, min_minutes=TRACKER_MINUTES, min_places=TRACKER_PLACES,
                           min_metres=TRACKER_METRES):
    """Kismet's rule for one address. `sightings` are (time, lat, lon): when the address was heard and where this
    computer itself was then (lat and lon None when it had no fix). Places are found greedily in time order: a
    sighting starts a new place when it is at least `min_metres` from every place so far. Sightings without a
    fix count for the time span but not for places. Returns None unless the span and the places both reach the
    thresholds, otherwise {'first', 'last', 'minutes', 'places'}."""
    timed = sorted((s for s in sightings or () if len(s) == 3 and _number(s[0])), key=lambda s: s[0])
    if not timed:
        return None
    places = []
    for _, lat, lon in timed:
        if _number(lat) and _number(lon):
            add_place(places, lat, lon, metres=min_metres, limit=len(timed))
    first, last = timed[0][0], timed[-1][0]
    minutes = (last - first) / 60.0
    if minutes < min_minutes or len(places) < min_places:
        return None
    return {"first": first, "last": last, "minutes": minutes, "places": len(places)}


def tracker_mark(facts):
    """The short mark added to the row's Known column: '3 places in 47 min'."""
    return f"{facts['places']} places in {int(facts['minutes'])} min"


def tracker_text(facts):
    """The facts in words, for the row's tooltip."""
    return (f"This tracker's address has been heard for {int(facts['minutes'])} minutes at {facts['places']} places "
            f"at least {TRACKER_METRES:.0f} m apart (this computer's own positions). It may belong to someone "
            f"travelling the same way. {TRACKER_CAVEAT}")


def describe_tracker(addr, facts):
    """(source, title, body, level) for the optional notification. Plain information, not a warning."""
    body = (f"Tracker address {addr} heard for {int(facts['minutes'])} min at {facts['places']} places at least "
            f"{TRACKER_METRES:.0f} m apart (this computer's positions). Address changes are not noticed.")
    return (TRACKER_SOURCE, "Bluetooth: a tracker's address heard at several places", body, "info")


def distance_text(metres):
    return f"{metres / 1000:.1f} km" if metres >= 1000 else f"{int(round(metres / 10.0) * 10)} m"


def minutes_text(seconds):
    return f"{int(seconds // 60)} min"


def measured_text(tally):
    """Exactly what was measured, and nothing else: 'Heard at 4 places up to 1.2 km apart over 23 min'."""
    places = tally.get("places") or []
    return (f"Heard at {len(places)} places up to {distance_text(spread_m(places))} apart over "
            f"{minutes_text(tally.get('last', 0) - tally.get('first', 0))}")


def who_text(tally, name_limit=24):
    """Name (if it sends one), maker (if known) and address."""
    name = " ".join(str(tally.get("name") or "").split())
    name = name if len(name) <= name_limit else name[:name_limit - 1] + "~"
    maker = str(tally.get("maker") or "").strip()
    first = name or "No name"
    return f"{first} ({maker}, {tally.get('addr', '')})" if maker else f"{first} ({tally.get('addr', '')})"


def strongest_text(tally):
    best = tally.get("best")
    return f"strongest {best:.0f} dBm" if isinstance(best, (int, float)) else "signal strength not given"


def flag_line(tally):
    """One line for the page about one address."""
    return f"{who_text(tally)}: {measured_text(tally).lower()}, {strongest_text(tally)}."


def describe(tally):
    """(notification source, title, body, level) for one address that met the rule. The body says what was
    measured and that your own things do the same; it fits the notification centre's 160 characters."""
    measured = measured_text(tally)
    measured = measured[0].lower() + measured[1:]
    rest = f" {measured}, {strongest_text(tally)}. Things you carry do this too."
    body = who_text(tally) + rest
    if len(body) > 160:                      # a long name and maker: the address and the measurement come first
        body = who_text(dict(tally, maker=""), name_limit=16) + rest
    return (NOTIFY_SOURCE, "Bluetooth: heard at several places", body, "warn")


def banner_text(flagged, limit=3):
    """The banner on the page: each address that met the rule, then what that does and does not mean.
    '' when there is nothing to say."""
    if not flagged:
        return ""
    lines = [flag_line(t) for t in flagged[:limit]]
    if len(flagged) > limit:
        more = len(flagged) - limit
        lines.append(f"And {more} more address{'es' if more != 1 else ''} heard the same way.")
    return "\n".join(lines + [CAVEAT])


def places_note(rail_on, tpv, scanning, tallied, flagged):
    """The line about the several-places check: what it is doing, or what it needs. It never switches the rail."""
    if not rail_on:
        return ("Noticing an address heard at several places needs the GPS rail on and a fix. The rail is never "
                "switched on for you.")
    if not scanning:
        return "The GPS rail is on: while a scan runs, the place each fixed address is heard is noted."
    if not position.good_fix(tpv):
        return "Waiting for a GPS fix. No place is noted without one."
    if not usable_fix(tpv):
        return "The GPS fix is too vague to call a place yet."
    if flagged:
        return (f"{flagged} address{'es' if flagged != 1 else ''} heard at {MIN_PLACES} or more places "
                f"{PLACE_METRES:.0f} m apart over {MIN_SECONDS // 60} minutes or longer.")
    return (f"Noting where {tallied} fixed address{'es are' if tallied != 1 else ' is'} heard. None at "
            f"{MIN_PLACES} places {PLACE_METRES:.0f} m apart over {MIN_SECONDS // 60} minutes so far. Tick Log "
            f"with position to keep listening for longer than one scan.")


def known_text(kind, entry, now, session_start, places=0):
    """The Known column for one row. `entry` is what the memory held, this scan's hearing included.
    'new' means first heard since this scan was started."""
    if not rememberable(kind):
        return "changes" if str(kind or "").startswith("private") else ""
    if entry is None or entry.get("first", now) >= session_start:
        text = "new"
    elif entry.get("days", 1) <= 1 and entry.get("day") == day_of(now):
        text = "seen today"
    else:
        days = max(1, int(entry.get("days", 1)))
        text = f"seen on {days} day{'s' if days != 1 else ''}"
    if entry is not None and entry.get("mine"):
        text += ", mine"
    if places >= 2:
        text += f", {places} places"
    return text


def known_tip(kind):
    """The tooltip for a Known cell."""
    if not rememberable(kind):
        return WHY_NOT_REMEMBERED if str(kind or "").startswith("private") else "This kind of address is not remembered."
    return "Remembered on this uConsole only, in a private file. Clear local data in Settings removes it."


def pick_line(addr, kind, name, mine):
    """The line by the Mark as mine button for the row that was tapped."""
    label = f"{name} ({addr})" if name else addr
    if not rememberable(kind):
        return f"{label}: {WHY_NOT_REMEMBERED} It cannot be marked."
    return f"{label} is marked as yours and is not pointed out." if mine else f"{label} picked."


# ---- the private file

def memory_path():
    return os.path.join(reports.state_dir(), "bluetooth_memory.json")


def clean_entry(raw):
    """One remembered address read from disk, with anything missing or of the wrong type put right; None if
    it is not an entry at all."""
    if not isinstance(raw, dict) or not isinstance(raw.get("first"), (int, float)) or isinstance(raw.get("first"), bool):
        return None
    first = float(raw["first"])
    last = raw.get("last")
    last = float(last) if isinstance(last, (int, float)) and not isinstance(last, bool) and last >= first else first
    days = raw.get("days")
    return {"name": raw["name"] if isinstance(raw.get("name"), str) else "",
            "maker": raw["maker"] if isinstance(raw.get("maker"), str) else "",
            "kind": raw["kind"] if rememberable(raw.get("kind")) else "",
            "first": first, "last": last,
            "days": days if isinstance(days, int) and not isinstance(days, bool) and days >= 1 else 1,
            "day": raw["day"] if isinstance(raw.get("day"), str) else day_of(last),
            "mine": raw.get("mine") is True}


def trimmed(entries, limit=MAX_ENTRIES):
    """At most `limit` entries: the ones you marked as yours first, then the most recently heard."""
    if len(entries) <= limit:
        return dict(entries)
    order = sorted(entries.items(), key=lambda kv: (bool(kv[1].get("mine")), kv[1].get("last", 0)), reverse=True)
    return dict(order[:limit])


def write_private(path, text):
    """Write a file only its owner can read, whole or not at all. Never raises; returns True when written."""
    with _WRITE:
        try:
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            tmp = path + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(text)
            os.chmod(tmp, 0o600)             # an older temp file may have been left with wider permissions
            os.replace(tmp, path)
            return True
        except OSError:
            return False


def files():
    """Every file this module may have written, for Clear local data."""
    return [p for p in (memory_path(), memory_path() + ".tmp") if os.path.exists(p)]


def clear_files():
    """Remove the file (Clear local data). A page that is open notices through cleared() and forgets what it
    holds too, so the next save does not put everything back. Returns how many files were removed."""
    global _cleared
    removed = 0
    with _WRITE:
        for path in files():
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
        _cleared += 1
    return removed


def cleared():
    return _cleared


class Memory:
    """entries: addr -> {'name', 'maker', 'kind', 'first', 'last', 'days', 'day', 'mine'}."""

    def __init__(self):
        self.entries, self.dirty, self.saved_at = {}, False, 0.0

    def load(self, path=None):
        """Read the file if it is there. Never raises: a missing or damaged file leaves the memory empty, and
        a damaged entry is dropped without losing the rest."""
        self.entries = {}
        try:
            with open(path or memory_path()) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return self
        devices = data.get("devices") if isinstance(data, dict) else None
        for addr, raw in (devices.items() if isinstance(devices, dict) else []):
            entry = clean_entry(raw)
            if normalise(addr) and entry is not None:
                self.entries[normalise(addr)] = entry
        return self

    def to_json(self):
        return json.dumps({"version": 1, "devices": trimmed(self.entries)}, separators=(",", ":"))

    def save(self, path=None):
        self.entries = trimmed(self.entries)
        ok = write_private(path or memory_path(), self.to_json())
        self.dirty = self.dirty and not ok
        return ok

    def text_to_save(self, now):
        """The file's text, for a write in the background; the memory counts as saved from here."""
        self.entries = trimmed(self.entries)
        self.dirty, self.saved_at = False, now
        return self.to_json()

    def due(self, now, every=SAVE_EVERY):
        """True when something changed and the last write was long enough ago."""
        return self.dirty and now - self.saved_at >= every

    def see(self, addr, kind, name="", maker="", when=None):
        """Remember one hearing. Returns True the first time this address is ever heard. Addresses that
        change are not stored at all."""
        mac = normalise(addr)
        if not mac or not rememberable(kind):
            return False
        when = time.time() if when is None else when
        today = day_of(when)
        entry = self.entries.get(mac)
        if entry is None:
            self.entries[mac] = {"name": name or "", "maker": maker or "", "kind": kind, "first": when, "last": when,
                                 "days": 1, "day": today, "mine": False}
            self.dirty = True
            return True
        if entry["day"] != today and when > entry["last"]:      # a new calendar day, and not an old clock
            entry["days"], entry["day"] = entry["days"] + 1, today
        if (name and name != entry["name"]) or (maker and maker != entry["maker"]) or kind != entry["kind"]:
            entry["name"], entry["maker"], entry["kind"] = name or entry["name"], maker or entry["maker"], kind
        if when > entry["last"]:
            entry["last"] = when
            self.dirty = True
        return False

    def see_snapshot(self, snap):
        """Feed one scan snapshot. Returns the addresses never heard before."""
        new = []
        for d in snap.get("devices") or []:
            last = d.get("last")
            when = last if isinstance(last, (int, float)) else snap.get("t")
            if self.see(d.get("addr"), d.get("kind"), d.get("name") or "", d.get("maker") or "", when):
                new.append(normalise(d.get("addr")))
        return new

    def known(self, addr):
        return self.entries.get(normalise(addr))

    def is_mine(self, addr):
        return bool((self.known(addr) or {}).get("mine"))

    def set_mine(self, addr, mine=True):
        """Mark an address already in the memory as your own, or take the mark off. Returns True if it changed."""
        entry = self.known(addr)
        if entry is None or entry["mine"] == bool(mine):
            return False
        entry["mine"], self.dirty = bool(mine), True
        return True

    def clear(self):
        self.entries, self.dirty = {}, False


class Travel:
    """Where the uConsole was each time a rememberable address was heard. In memory only.
    tallies: addr -> {'addr', 'name', 'maker', 'places': [(lat, lon)], 'first', 'last', 'best', 'told'}."""

    def __init__(self):
        self.tallies = {}
        self.trackers = {}                  # addr -> {'addr', 'sightings': [(t, lat, lon)], 'last', 'told'}

    def add_trackers(self, snap, tpv):
        """Note this computer's position against each address bt_trackers names as a tracker that was heard in
        the last few seconds of this snapshot. Nothing is noted without a usable fix."""
        now = snap.get("t")
        if not usable_fix(tpv) or not _number(now):
            return
        for d in snap.get("devices") or []:
            mac = normalise(d.get("addr"))
            if not mac or not survey.heard_now(d, now) or not bt_trackers.identify(d):
                continue
            when = d.get("last") if _number(d.get("last")) else now
            entry = self.trackers.setdefault(mac, {"addr": mac, "sightings": [], "last": when, "told": False})
            entry["last"] = max(entry["last"], when)
            # at most one sighting per SIGHTING_EVERY; one earlier than the last (the clock was set back) is taken
            # and becomes the new latest, rather than refusing every sighting until the clock catches up
            if entry["sightings"] and 0 <= when - entry["sightings"][-1][0] < SIGHTING_EVERY:
                continue
            entry["sightings"].append((when, tpv["lat"], tpv["lon"]))
            del entry["sightings"][:-MAX_SIGHTINGS]
        for mac in [a for a, e in self.trackers.items() if now - e["last"] > TRACKER_KEEP]:
            del self.trackers[mac]
        if len(self.trackers) > MAX_TALLIED:                        # trackers change address: keep the latest
            keep = sorted(self.trackers.values(), key=lambda e: e["last"], reverse=True)[:MAX_TALLIED]
            self.trackers = {e["addr"]: e for e in keep}

    def tracker_facts(self, addr):
        """The facts for one tracker address that meets the rule, or None."""
        entry = self.trackers.get(normalise(addr))
        return seen_at_several_places(entry["sightings"]) if entry else None

    def trackers_to_tell(self, memory=None):
        """(addr, facts) for each tracker address that met the rule and was not handed out before."""
        out = []
        for mac, entry in sorted(self.trackers.items()):
            if entry["told"] or (memory and memory.is_mine(mac)):
                continue
            facts = seen_at_several_places(entry["sightings"])
            if facts:
                entry["told"] = True
                out.append((mac, facts))
        return out

    def add(self, snap, tpv):
        """Note the current position against every rememberable address heard in the last few seconds of this
        snapshot. Without a usable fix nothing is noted. Returns the tallies that met the rule just now."""
        self.add_trackers(snap, tpv)
        if not usable_fix(tpv):
            return []
        now, met = snap.get("t"), []
        for d in snap.get("devices") or []:
            mac = normalise(d.get("addr"))
            if not mac or not rememberable(d.get("kind")) or not survey.heard_now(d, now):
                continue
            last = d.get("last")
            when = last if isinstance(last, (int, float)) else now
            if not isinstance(when, (int, float)):
                continue
            tally = self.tallies.get(mac)
            if tally is None or when - tally["last"] > TALLY_KEEP:
                tally = self.tallies[mac] = {"addr": mac, "name": "", "maker": "", "places": [], "first": when,
                                             "last": when, "best": None, "told": False}
            was = moved_along(tally)
            tally["name"], tally["maker"] = d.get("name") or tally["name"], d.get("maker") or tally["maker"]
            tally["last"] = max(tally["last"], when)
            rssi = d.get("rssi")
            if isinstance(rssi, (int, float)) and (tally["best"] is None or rssi > tally["best"]):
                tally["best"] = rssi
            add_place(tally["places"], tpv["lat"], tpv["lon"])
            if not was and moved_along(tally):
                met.append(tally)
        if len(self.tallies) > MAX_TALLIED:                         # drop the ones not heard for longest
            keep = sorted(self.tallies.values(), key=lambda t: t["last"], reverse=True)[:MAX_TALLIED]
            self.tallies = {t["addr"]: t for t in keep}
        return met

    def places(self, addr):
        return len((self.tallies.get(normalise(addr)) or {}).get("places") or [])

    def flagged(self, memory=None):
        """Every address that meets the rule and is not marked as yours, the most places first."""
        out = [t for t in self.tallies.values() if moved_along(t) and not (memory and memory.is_mine(t["addr"]))]
        return sorted(out, key=lambda t: (-len(t["places"]), t["addr"]))

    def to_tell(self, memory=None):
        """The flagged addresses not yet notified about; each is handed out once."""
        fresh = [t for t in self.flagged(memory) if not t["told"]]
        for t in fresh:
            t["told"] = True
        return fresh

    def clear(self):
        self.tallies, self.trackers = {}, {}
