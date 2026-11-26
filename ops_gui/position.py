"""
Where the uConsole is, for anything that wants to stamp what it hears with a position (the wardriving logs of
Survey and Bluetooth).

The GPS tab only reads the receiver while that tab is showing, so its last reading goes stale the moment you look
at another tab, which is exactly when a log wants it. This asks gpsd itself, in the background, at most every few
seconds, and only hands a reading out while it is fresh. It never switches the GPS rail: with the rail off there
is no gpsd answer and so no position, and callers say so.

Fresh means the receiver itself said so. gpsd can hand the same old report out again and again, so a reading is
dated by the receiver's own time stamp, not by when it reached us: a report whose stamp has not moved on is the
same reading, however recently it arrived. How long ago things happened here is counted on a clock that only
runs forwards, so the system clock being set (which GPS itself does) never makes an old reading look new.
The helpers at the top are pure so they can be tested without a receiver.
"""
import json
import threading
import time
from datetime import datetime, timezone

POLL_SECONDS = 4.0           # ask gpsd no more often than this
MAX_AGE = 12.0               # a reading older than this is not "where we are" any more


class GpsdLines:
    """The one place the app asks gpsd. Home, the fix notifier, the field log and the position stamp each used
    to open their own connection and wait a second or two for the same reports. Now whoever asks first does the
    asking, anyone asking at the same moment waits for that answer, and anyone asking within `max_age` seconds
    gets it again. Always called from a worker thread, never the screen's."""

    def __init__(self, query=None, clock=time.monotonic):
        self.query, self.clock = query, clock
        self.lines, self.got_at, self.lock = [], None, threading.Lock()

    def get(self, max_age=POLL_SECONDS):
        with self.lock:
            now = self.clock()
            if self.got_at is not None and 0 <= now - self.got_at <= max_age:
                return self.lines
            query = self.query
            if query is None:
                from .home_panel import query_gpsd as query
            self.lines, self.got_at = list(query() or []), self.clock()
            return self.lines


_GPSD = GpsdLines()


def gpsd_lines(max_age=POLL_SECONDS):
    """Raw gpsd reports, no older than `max_age` seconds, shared by everything in the app that wants them."""
    return _GPSD.get(max_age)


def tpv_from_lines(lines):
    """The newest position report (TPV) among raw gpsd JSON lines, or None."""
    found = None
    for line in lines or []:
        try:
            rec = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("class") == "TPV":
            found = rec
    return found


def good_fix(tpv):
    """True for a real fix: 2D or 3D, with both coordinates."""
    return bool(tpv) and (tpv.get("mode") or 0) >= 2 and tpv.get("lat") is not None and tpv.get("lon") is not None


def fresh(got_at, now, max_age=MAX_AGE):
    return got_at is not None and 0 <= now - got_at <= max_age


def tpv_time(tpv):
    """The receiver's own time for a report, as seconds since 1970, or None when it gave none (a receiver that
    has only just been switched on) or one that cannot be read."""
    text = (tpv or {}).get("time")
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        when = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)            # gpsd always means UTC
    return when.timestamp()


def reading_step(tpv, last_time, wall_now, max_age=MAX_AGE):
    """What to make of a report just received, given the receiver time of the last one taken (`last_time`, or
    None when there is none yet). Returns one of:

    "new"    a reading taken just now: keep it and date it from this moment;
    "same"   the report already held, handed out again: it is no fresher than when it first arrived;
    "old"    older than the one held, or a first report that cannot be shown to be current: do not use it;
    "start"  a first report whose time disagrees with this computer's clock. Either the report is stale or the
             clock is wrong (a uConsole outdoors has no network to set it by), and one report cannot tell which,
             so it is not used but its time is remembered: the next report is new if the time has moved on.

    A report with no time at all is taken as new: a receiver without the time yet has no fix either."""
    when = tpv_time(tpv)
    if when is None:
        return "new"
    if last_time is None:
        return "new" if abs(wall_now - when) <= max_age else "start"
    if when == last_time:
        return "same"
    if when > last_time:
        return "new"
    # Going backwards a little is a late report. Going backwards a lot is the receiver correcting its own clock
    # (they can start up with a wrong date): begin again from there instead of refusing everything until its
    # clock catches up with the wrong one.
    return "old" if last_time - when <= 60 else "start"


def gps_rail_on(snapshot):
    """True when the app's last status says the GPS rail is powered."""
    return ((snapshot or {}).get("rails") or {}).get("GPS") == "on"


class Position:
    """The last reading from gpsd and when it was taken. `query`, `clock` (one that only runs forwards, for
    how long ago) and `wall` (the time of day, to compare with the receiver's) are injectable for tests."""

    def __init__(self, query=None, clock=time.monotonic, wall=time.time):
        self.query, self.clock, self.wall = query, clock, wall
        self.tpv, self.got_at, self.asked_at, self.busy = None, None, None, False
        self.receiver_time = None          # the receiver's own time on the newest report seen

    def take(self, tpv):
        """Keep a report if it is a newer reading than the one held. Returns what it was (see reading_step)."""
        step = reading_step(tpv, self.receiver_time, self.wall())
        if step == "new":
            self.tpv, self.got_at = tpv, self.clock()
        if step in ("new", "start"):
            self.receiver_time = tpv_time(tpv)
        return step

    def forget(self):
        """Drop the reading: the GPS rail went off, so whatever was held is where the uConsole was, not is."""
        self.tpv, self.got_at, self.asked_at, self.receiver_time = None, None, None, None

    def _ask(self):
        try:
            query = self.query or (lambda: gpsd_lines(POLL_SECONDS / 2))
            tpv = tpv_from_lines(query())
            if tpv is not None:
                self.take(tpv)
        finally:
            self.busy = False

    def poll(self, background=True):
        """Ask gpsd again if it is time to. Returns at once; the answer arrives a moment later."""
        now = self.clock()
        if self.busy or (self.asked_at is not None and now - self.asked_at < POLL_SECONDS):
            return False
        self.busy, self.asked_at = True, now
        if background:
            threading.Thread(target=self._ask, daemon=True).start()
        else:
            self._ask()
        return True

    def current(self):
        """The reading, if it is fresh; otherwise None. A reading with no fix is still returned: check good_fix."""
        return self.tpv if fresh(self.got_at, self.clock()) else None


def shared(win):
    """The one Position the whole window uses."""
    if getattr(win, "position", None) is None:
        win.position = Position()
    return win.position


def current(win):
    """A fresh position for the window, asking gpsd in the background when it is due. None while the GPS rail is
    off (nothing is asked then) or no fresh reading has arrived yet."""
    if not gps_rail_on(getattr(win, "snap", None)):
        held = getattr(win, "position", None)
        if held is not None:
            held.forget()                  # a reading from before the rail went off is never handed out after
        return None
    pos = shared(win)
    pos.poll()
    return pos.current()
