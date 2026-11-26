"""
Wi-Fi sensing: noticing movement near the uConsole from how Wi-Fi signals wobble.

Passive. It reads the signal strength the Wi-Fi driver already reports and never sends or scans anything. The maths
is in sense_dsp.py (a motion score in dB over a self measured noise floor, gates with minimum on and off times, a
multi-link shared-wobble measure and a note of a sharp change followed by stillness), borrowed from the ideas in wifisense-pi
and WiFi-Sense. This module is the glue: reading sources, the state machine, and the page.

It is a coarse "is something moving" sensor, not a camera. It cannot see who or what or where, and signal strength
alone cannot do pose or heart rate (the pose and heart rate claims of RuView are not used).
The helpers at the top are pure so they can be tested without a radio.
"""
import json
import os
import queue
import re
import subprocess
import threading
import time

from . import radar, room3d, style
from . import sense_dsp as dsp

SAMPLE_MS = 200                         # five readings a second from the connected link
CALIBRATE_SECONDS = 30
CHART_SECONDS = 60
WATERFALL_COLUMNS = 120                 # one column a second, two minutes
SENSITIVITY = {"Low": 2.0, "Medium": 0.0, "High": -2.0}       # dB added to the on and off lines
STATE_WORDS = {"calibrating": "LEARNING THE ROOM", "quiet": "ALL QUIET", "recent": "RECENT ACTIVITY",
               "movement": "MOVEMENT"}
STATE_LEVEL = {"quiet": "good", "recent": "warn", "movement": "hot", "calibrating": "dim"}
HEAT_STOPS = ((0.0, "#1d1724"), (4.0, "#2b5f8a"), (8.0, "#3fe0d0"), (13.0, "#ffd23f"), (20.0, "#ff3b3b"))
STRIP_SECONDS = 180.0                   # how far back each link's history strip goes
STRIP_COLUMNS = 60                      # so one cell is three seconds
UNSURE = -1                             # a strip cell with readings too sparse to call quiet or not
RELIABLE = 0.4                          # below this share of the expected readings a link's score is not trusted
ACTIVITY_WORDS = ("quiet", "subtle", "strong")
STRIP_CAPTION = ("Each link over the last 3 minutes, newest last: green quiet, orange subtle, red strong, "
                 "hatched not sure.")
LINK_CAPTION = "A lit line or arc means that link's signal is disturbed. It does not show a place in the room."
# Under the two flat views. Where a sender is drawn is only a tidy slot, and the picture has to say so.
RADAR_CAPTION = ("not a map: position on screen means nothing", "each arc is one link, nearer the centre = stronger signal")
ABOVE_CAPTION = ("not a map: position on screen means nothing", "nearer the centre = stronger signal")
PULSE_TIP = ("Pulses with how strongly the signal is being disturbed. It is not a picture of anything in the room.")


def parse_proc_wireless(text):
    """{interface: signal level in dBm} for every connected wireless interface in /proc/net/wireless."""
    out = {}
    for line in (text or "").splitlines()[2:]:
        parts = line.replace(":", " ", 1).split()
        if len(parts) < 5:
            continue
        try:
            level = float(parts[3].rstrip("."))
        except ValueError:
            continue
        if -120 <= level <= 0:
            out[parts[0]] = level
    return out


def read_levels(path="/proc/net/wireless"):
    try:
        with open(path) as f:
            return parse_proc_wireless(f.read())
    except OSError:
        return {}


def source_options(levels):
    """The interfaces that can be sensed through right now, strongest first, as [(name, text)]."""
    return [(name, f"{name}  {lvl:.0f} dBm") for name, lvl in sorted((levels or {}).items(), key=lambda kv: -kv[1])]


# ---- the air source: every sender heard by the internal MT7921 in monitor mode ----------------------------
# aiov2_ctl --wifi-listen does the radio work (root, passive, always restores the radio) and prints one JSON
# record a second. We pick the few steadiest senders and treat each as a link.
AIR_PREFIX = "air:"
AIR_IFACE = "wlan1"
AIR_CHANNELS = tuple(range(1, 14)) + (36, 40, 44, 48, 149, 153, 157, 161, 165)    # the ones the sudoers rules allow
AIR_LINKS = 3                 # how many senders to follow
AIR_SETTLE = 10               # seconds spent choosing them
AIR_MIN_SCORE = 5.0           # a steady sender is heard at least a few times a second


def air_args(channel):
    return ["sudo", "-n", "aiov2_ctl", "--wifi-listen", "--iface", AIR_IFACE, "--channel", str(channel),
            "--seconds", "3600", "--json"]


AIR_RESTORE = ["sudo", "-n", "aiov2_ctl", "--wifi-listen", "--restore", "--iface", AIR_IFACE]


def air_available(sys_net="/sys/class/net"):
    return os.path.isdir(os.path.join(sys_net, AIR_IFACE, "wireless"))


def air_channel(iw_text, default=6):
    """The channel the connected Wi-Fi uses (its access point is the best thing to listen to), or a default."""
    m = re.search(r"channel\s+(\d+)", iw_text or "")
    return int(m.group(1)) if m and int(m.group(1)) in AIR_CHANNELS else default


MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def air_target(bssid, channel):
    """(bssid, channel) for one access point the air source can be pointed at, or None when it cannot: no proper
    address, no channel known yet, or a channel the listener is not allowed to sit on."""
    mac = str(bssid or "").strip().lower()
    try:
        ch = int(channel)
    except (TypeError, ValueError):
        return None
    return (mac, ch) if MAC_RE.match(mac) and ch in AIR_CHANNELS else None


def air_source(channel, bssid=None):
    """The name of an air source in the source list: 'air:6' is every sender on channel 6, 'air:6:aa:bb:...' is
    just that one sender on it."""
    return f"{AIR_PREFIX}{channel}" + (f":{bssid}" if bssid else "")


def parse_air_source(source):
    """(channel, bssid or None) from an air source's name. ValueError for anything that is not one."""
    if not str(source or "").startswith(AIR_PREFIX):
        raise ValueError(f"not an air source: {source}")
    channel, _, bssid = source[len(AIR_PREFIX):].partition(":")
    return int(channel), (bssid.lower() or None)


def air_blocker(available, levels):
    """Why the air source cannot run right now, as (what is wrong, what to do), or None when it can. Survey and
    Sense share this so they say the same thing. The rail is never switched on here: the user is told to."""
    if not available:
        return ("The internal Wi-Fi radio is not there.", "Switch the USB rail on, wait a few seconds, then try again.")
    if AIR_IFACE in (levels or {}):
        return (f"{AIR_IFACE} is connected to a network.", "Disconnect it first: monitor mode would drop that connection.")
    return None


def source_title(source, label_for=None):
    """How a source is named in a notice or the activity list: a single followed sender by its name and channel,
    anything else as it is."""
    try:
        channel, bssid = parse_air_source(source)
    except ValueError:
        return source
    return f"{(label_for or mac_name)(bssid)} (channel {channel})" if bssid else source


def waiting_text(name, channel):
    """What the page says while one chosen sender has not been heard yet. It may never be: say why, plainly."""
    return (f"Nothing heard from {name} on channel {channel} yet. It may have moved to another channel or be out "
            "of range. Nothing is sent.")


def parse_air_line(line):
    """One JSON record from the listener, or None for anything else."""
    try:
        rec = json.loads(line)
    except (TypeError, ValueError):
        return None
    if not isinstance(rec, dict) or not isinstance(rec.get("links"), dict) or not isinstance(rec.get("t"), (int, float)):
        return None
    return rec


def mac_name(mac):
    return "ap " + mac[-8:]


class AirLinks:
    """Chooses the steadiest senders in the first few seconds, then reports just those, second by second.

    `label_for` names a sender: given the BSSID registry's own label() it shows a real SSID once one is known
    (from Survey or an ordinary Wi-Fi scan), instead of always just the last few characters of the address.

    `only` is one sender's address, picked by the user (in Survey): then there is nothing to choose, and just that
    sender is followed from the first record, whether or not it turns out to be a steady one."""

    def __init__(self, keep=AIR_LINKS, settle=AIR_SETTLE, label_for=None, only=None):
        self.keep, self.settle, self.label_for = keep, settle, label_for or mac_name
        self.only = (only or "").lower() or None
        self.score, self.seen, self.pinned = {}, 0, ([self.only] if self.only else None)
        self.last = {}                # the latest signal strength of each followed sender

    def _links(self, rec):
        """The record's senders. A chosen sender is matched in lower case, however the listener wrote it."""
        links = rec.get("links", {})
        return {str(m).lower(): v for m, v in links.items()} if self.only else links

    def feed(self, rec):
        """{link name: dBm} for the chosen senders in this record (empty while choosing)."""
        links = self._links(rec)
        if self.pinned is None:
            self.seen += 1
            for mac in set(self.score) | set(links):
                n = links.get(mac, [0, 0])[0]
                self.score[mac] = self.score.get(mac, 0.0) * 0.9 + n
            if self.seen < self.settle:
                return {}
            best = sorted((m for m, s in self.score.items() if s >= AIR_MIN_SCORE), key=lambda m: -self.score[m])
            if not best:
                return {}
            self.pinned = best[:self.keep]
        return {self.label_for(m): links[m][1] for m in self.pinned if m in links}

    def readings(self, rec):
        """{link name: [(time, dBm), ...]} for the chosen senders: every reading when the listener sent them,
        otherwise just the average for the second. Call after feed()."""
        out, links = {}, self._links(rec)
        for m in self.pinned or []:
            entry = links.get(m)
            if not entry:
                continue
            pts = entry[2] if len(entry) > 2 and entry[2] else None
            name = self.label_for(m)
            if pts:
                out[name] = sorted((rec["t"] + float(off), float(dbm)) for off, dbm in pts)
            else:
                out[name] = [(rec["t"], float(entry[1]))]
            self.last[name] = out[name][-1][1]
        return out


class AirFeed:
    """Runs the listener and hands its records over without blocking the page."""

    def __init__(self, channel=None, runner=subprocess.Popen, args=None, parse=None):
        self.channel, self.queue, self.error = channel, queue.Queue(), ""
        self.parse = parse or parse_air_line
        self.proc = runner(args or air_args(channel), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        self.exited_at = None
        self.readers = [threading.Thread(target=self._read, daemon=True),
                        threading.Thread(target=self._errors, daemon=True)]
        for reader in self.readers:
            reader.start()

    def _read(self):
        for line in self.proc.stdout:
            rec = self.parse(line)
            if rec:
                self.queue.put(rec)

    def _errors(self):
        self.error = (self.proc.stderr.read() or "").strip()

    def records(self):
        out = []
        while True:
            try:
                out.append(self.queue.get_nowait())
            except queue.Empty:
                return out

    def alive(self):
        return self.proc.poll() is None

    READERS_GRACE = 5.0          # seconds to wait for the last output after the process has gone

    def finished(self, clock=time.monotonic):
        """True once the process has ended AND everything it printed has been taken in. The process ending is
        noticed a moment before its last records are queued and its error text is stored, so asking alive()
        alone could make a scan that failed look like one that simply finished. Ask this first, then records().
        (If something else keeps the pipes open, the wait is given up after a few seconds.)"""
        if self.proc.poll() is None:
            return False
        if self.exited_at is None:
            self.exited_at = clock()
        return not any(r.is_alive() for r in self.readers) or clock() - self.exited_at > self.READERS_GRACE

    def exit_code(self):
        """What the process ended with (0 is a clean end), or None while it is still running."""
        return self.proc.poll()

    def problem(self):
        """Why it ended, in one line, or '' when it ended cleanly. Only meaningful once finished(). A process
        that ended with a failure code but said nothing still counts as a failure."""
        lines = (self.error or "").strip().splitlines()
        if lines:
            return lines[-1]
        code = self.exit_code()
        return f"It ended with an error (code {code}) and gave no reason." if code else ""

    def stop(self, runner=subprocess.run):
        """Ask it to stop (it puts the radio back itself), then make sure, whatever happened."""
        if self.alive():
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        try:
            runner(AIR_RESTORE, capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            pass


def heat(db):
    """A colour for a motion score: dark when still, then blue, cyan, yellow and red."""
    from .pixel import blend
    if db <= HEAT_STOPS[0][0]:
        return HEAT_STOPS[0][1]
    for (a, ca), (b, cb) in zip(HEAT_STOPS, HEAT_STOPS[1:]):
        if db <= b:
            return blend(ca, cb, (db - a) / (b - a))
    return HEAT_STOPS[-1][1]


def chart_range(values):
    """A steady (low, high) for the signal chart: the data with a little room, never narrower than 8 dB."""
    vals = [v for v in values if v is not None]
    if not vals:
        return -90.0, -40.0
    lo, hi = min(vals) - 2, max(vals) + 2
    if hi - lo < 8:
        mid = (hi + lo) / 2
        lo, hi = mid - 4, mid + 4
    return lo, hi


def q_trail(heights):
    """A few earlier heights of a post, oldest first, spaced a few seconds apart, excluding the newest."""
    return [h for h in heights[:-1][::-4]][:3]


def activity_level(db):
    """0 quiet, 1 subtle, 2 strong: the three steps a link's history strip is drawn in."""
    db = db or 0.0
    return 0 if db < dsp.QUIET_DB else 1 if db < 10.0 else 2


def link_sure(reliability):
    """Whether enough readings are arriving from a link for its score to mean anything."""
    return (reliability or 0.0) >= RELIABLE


def state_colour(state, mode="dark"):
    """The colour of the state word and the pulse, readable on this theme's card (yellow on white is not)."""
    from . import pixel
    m = "light" if mode == "light" else "dark"
    level = STATE_LEVEL.get(state, "dim")
    if level == "good":
        return style.good_colour(m)
    return pixel.palette(m)[level] if level in ("warn", "hot") else style.MUTED[m]


def pulse_rings(level, tick):
    """The header's activity mark: sizes (0 to 1) of the rings round its dot. None when still, up to three as the
    motion level (0 to 1) rises, and they swell on every other reading so it beats while something is going on.
    It is driven by readings, not a clock, and it sits in the header: it is not a picture of anything in the room."""
    n = int(round(max(0.0, min(1.0, level or 0.0)) * 3))
    swell = 0.12 if tick % 2 else 0.0
    return [min(1.0, (i + 1) / 3.0 * 0.88 + swell) for i in range(n)]


def join_names(names):
    names = [str(n) for n in names]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def paths_to(labels):
    """'the path to A' or 'the paths to A, B and C'. A path, between the uConsole and a sender, is the only kind of
    'where' signal strength can give."""
    labels = list(labels)
    return ("the path to " if len(labels) == 1 else "the paths to ") + join_names(labels)


def span_text(seconds):
    s = max(0, int(seconds or 0))
    return f"{s} s" if s < 90 else f"{s // 60} min"


def room_line(state, dbs, labels=None, on_db=dsp.PRESENCE_ON_DB, moving_for=None, still_for=None, unsure=()):
    """The one plain sentence over the room, built only from counts of links and seconds.

    dbs is {link: motion dB}, labels {link: how to name its sender}, on_db the score at which a link counts as
    disturbed, unsure the links with too few readings. It says which links are disturbed and for how long. It
    never says what is moving, how many, or where in the room: the radio cannot know."""
    labels = labels or {}

    def lab(name):
        return labels.get(name, name)
    if state == "calibrating" or not dbs:
        return "Learning the room. Nothing can be said about movement yet."
    doubt = sorted((n for n in unsure if n in dbs), key=str)
    tail = ""
    if doubt:
        tail = (f" Too few readings on {paths_to(lab(n) for n in doubt)} to say whether "
                f"{'it is' if len(doubt) == 1 else 'they are'} quiet.")
    if state == "movement":
        trusted = {n: d for n, d in dbs.items() if n not in doubt} or dbs
        ranked = sorted(trusted, key=lambda n: (-(trusted[n] or 0.0), str(n)))
        hot = [n for n in ranked if (trusted[n] or 0.0) >= on_db] or ranked[:1]
        span = f", for {span_text(moving_for)}" if moving_for is not None else ""
        near = f"Something is moving on or near {paths_to(lab(n) for n in sorted(hot, key=str))}"
        if len(dbs) == 1:
            return f"{near}{span}.{tail}"
        if len(hot) == 1:
            return f"Movement on 1 of {len(dbs)} links{span}. {near}.{tail}"
        return (f"Movement on {len(hot)} of {len(dbs)} links, strongest on {paths_to([lab(hot[0])])}{span}. "
                f"{near}.{tail}")
    if len(doubt) == len(dbs):
        return "Too few readings to say whether anything is moving."
    if state == "recent":
        ago = f" The last movement stopped {span_text(still_for)} ago." if still_for is not None else ""
        return f"Still now.{ago}{tail}"
    still = f"Still for {span_text(still_for)}." if still_for is not None else "Still since the room was learned."
    return still + tail


class LinkHistory:
    """The last few minutes of every link's motion score, a reading a second, for the fading colour on its line in
    the room and the strip under it. It keeps scores only: what the signal did, not what caused it."""

    def __init__(self, keep=STRIP_SECONDS):
        self.keep, self.rows = keep, {}

    def push(self, now, dbs, sure=None):
        """Add this second's {link: dB}. `sure` is {link: False} for the ones whose score cannot be trusted."""
        for name, db in dbs.items():
            row = self.rows.setdefault(name, [])
            row.append((now, max(0.0, db or 0.0), bool((sure or {}).get(name, True))))
            while row and row[0][0] <= now - self.keep:
                row.pop(0)

    def trail(self, name, now):
        """What is left of the link's disturbance over the last minute. Untrusted scores leave no trail."""
        return room3d.trail_db([(t, db) for t, db, ok in self.rows.get(name, []) if ok], now)

    def strip(self, name, now, columns=STRIP_COLUMNS):
        """`columns` cells, oldest first: None for no reading, UNSURE, or the strongest activity level in that
        cell. A cell is only UNSURE when nothing in it could be trusted."""
        out, width = [None] * columns, self.keep / columns
        for t, db, ok in self.rows.get(name, []):
            k = columns - 1 - int((now - t) / width)
            if not 0 <= k < columns:
                continue
            code = activity_level(db) if ok else UNSURE
            out[k] = code if out[k] is None else max(out[k], code)
        return out


def event_text(kind, when=None):
    stamp = time.strftime("%H:%M:%S", time.localtime(when))
    words = {"movement_start": "movement started", "movement_end": "movement stopped",
             "presence_start": "activity in the room", "presence_end": "room quiet again",
             "learned": "room learned, now watching",
             "sharp_then_still": "a sharp change, then still"}
    return f"{stamp}  {words.get(kind, kind)}"


def csv_line(now, state, db, levels):
    """One row for the recording: time, state, motion dB, then each link's signal."""
    return ",".join([f"{now:.2f}", state, f"{db:.2f}"] + [f"{levels[k]:.1f}" if levels[k] is not None else "" for k in sorted(levels)])


class ActivityLog:
    """What has been going on, from the engine's states: how long since anything moved, how much movement in the
    last ten minutes, and which link is being disturbed most. Everything here is a count of what the signal did,
    nothing about who or what caused it."""

    WINDOW = 600.0

    def __init__(self):
        self.last_state, self.last_t = None, None
        self.moving_since, self.last_moved = None, None
        self.episodes = []            # (start, end) of each stretch of movement

    def update(self, state, now):
        if state == "movement" and self.moving_since is None:
            self.moving_since = now
        if state == "movement":
            self.last_moved = now
        elif self.moving_since is not None:
            self.episodes.append((self.moving_since, now))
            self.moving_since, self.last_moved = None, now
        self.episodes = [(a, b) for a, b in self.episodes if now - b < self.WINDOW]
        self.last_state, self.last_t = state, now

    def moved_seconds(self, now):
        total = sum(b - max(a, now - self.WINDOW) for a, b in self.episodes)
        if self.moving_since is not None:
            total += now - self.moving_since
        return max(0.0, total)

    def text(self, now, link_dbs=None):
        if self.last_state in (None, "calibrating"):
            return "Still learning the room."
        if self.last_state == "movement":
            first = "Moving now."
        elif self.last_moved is None:
            first = "Nothing has moved since the room was learned."
        else:
            gap = int(now - self.last_moved)
            first = f"Nothing moving for {gap}s." if gap < 90 else f"Nothing moving for {gap // 60} min."
        second = self.history_text(now)
        hot = max((link_dbs or {}).items(), key=lambda kv: kv[1], default=None)
        third = f"Most disturbed: {hot[0]} ({hot[1]:+.0f} dB)." if hot and len(link_dbs) > 1 and hot[1] > 3 else ""
        return " ".join(x for x in (first, second, third) if x)

    def history_text(self, now):
        """Just the count for the last ten minutes, for under the room, where the line above it already says
        whether anything is moving now."""
        if self.last_state in (None, "calibrating"):
            return ""
        n = len(self.episodes) + (1 if self.moving_since is not None else 0)
        return (f"Last 10 minutes: {n} stretch{'es' if n != 1 else ''} of movement, "
                f"{int(self.moved_seconds(now))}s in all.")


class Engine:
    """The sensing state machine. Feed it readings and call step(); it says what changed.

    States: calibrating (learning what a still room looks like), quiet, recent (activity in the last while, but
    nothing moving now) and movement."""

    def __init__(self, sensitivity="Medium"):
        self.offset = SENSITIVITY.get(sensitivity, 0.0)
        self.reset()

    def reset(self):
        self.links, self.started = {}, None
        self.state, self.db, self.fusion = "calibrating", 0.0, 0.0
        self.motion = self.presence = None
        self.sharp = dsp.SharpChange()
        self.last_step = None
        self.activity = ActivityLog()

    def set_sensitivity(self, name):
        self.offset = SENSITIVITY.get(name, 0.0)
        if self.motion:
            self._gates()

    def _gates(self):
        o = self.offset
        self.motion = dsp.Gate(dsp.MOTION_ON_DB + o, dsp.MOTION_OFF_DB + o, dsp.MOTION_MIN_ON, dsp.MOTION_MIN_OFF)
        self.presence = dsp.Gate(dsp.PRESENCE_ON_DB + o, dsp.PRESENCE_OFF_DB + o, dsp.PRESENCE_MIN_ON, dsp.PRESENCE_MIN_OFF)

    def add(self, name, t, value, native=5.0):
        link = self.links.get(name)
        if link is None:
            link = self.links[name] = dsp.LinkSense(name, native)
        if self.started is None:
            self.started = t
        link.add(t, value)

    @property
    def calibrate_s(self):
        """Learning takes longer for slow sources, whose score needs a long window to settle."""
        extra = max([l.window - 6.0 for l in self.links.values()] or [0.0])
        return CALIBRATE_SECONDS + 2.0 * max(0.0, extra)

    @property
    def progress(self):
        return 0.0 if self.started is None or not self.last_step else min(1.0, (self.last_step - self.started) / self.calibrate_s)

    def link_dbs(self):
        return {name: link.db for name, link in self.links.items()}

    def step(self, now):
        """Recompute scores and the state. Returns a list of event kinds that happened ('movement_start' ...)."""
        events = []
        dt = 0.0 if self.last_step is None else max(0.0, now - self.last_step)
        self.last_step = now
        for link in self.links.values():
            link.update()
        dbs = [link.db for link in self.links.values()]
        self.db = max(dbs) if dbs else 0.0
        if self.started is None:
            return events
        if self.state == "calibrating":
            if now - self.started >= self.calibrate_s:
                for link in self.links.values():
                    link.freeze()
                self._gates()
                self.state = "quiet"
                events.append("learned")
            return events
        was_move, was_pres = self.motion.state, self.presence.state
        move = self.motion.update(self.db, dt)
        pres = self.presence.update(self.db, dt) or move
        if move and not was_move:
            events.append("movement_start")
        if was_move and not move:
            events.append("movement_end")
        if pres and not was_pres:
            events.append("presence_start")
        if was_pres and not pres:
            events.append("presence_end")
        self.state = "movement" if move else "recent" if pres else "quiet"
        if self.sharp.update(self.db, now):
            events.append("sharp_then_still")
        self.activity.update(self.state, now)
        return events

    def reliability(self):
        rels = [l.reliability() for l in self.links.values()]
        return min(rels) if rels else 0.0

    def link_reliability(self):
        """{link: 0 to 1} measured up to the last step, not up to each link's own last reading, so a link that
        has gone silent reads as unreliable instead of as steady as when it was last heard."""
        return {name: link.reliability(now=self.last_step) for name, link in self.links.items()}

    def level(self):
        """0 to 1 for the meter: how far the score is toward the movement line."""
        top = dsp.MOTION_ON_DB + self.offset
        return max(0.0, min(1.0, self.db / (top * 1.6))) if self.state != "calibrating" else 0.0


def build_page(win):
    """The Sense page for the Wi-Fi tab."""
    from PyQt6.QtCore import QPoint, QPointF, QRect, Qt, QTimer
    from PyQt6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPalette, QPen, QPolygon
    from PyQt6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSplitter,
                                 QStackedWidget, QVBoxLayout, QWidget)
    from . import pixel

    def card():
        box = QFrame()
        box.setObjectName("card")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(3)
        return box, lay

    class Chart(QWidget):
        """The signal strength of one link, as a pixel line with a dithered fill."""

        def __init__(self):
            super().__init__()
            self.setMinimumHeight(110)
            self.values, self.colour = [], "#3fe0d0"

        def paintEvent(self, _):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            text = self.palette().color(QPalette.ColorRole.WindowText)
            w, h = self.width(), self.height()
            vals = [v for v in self.values if v is not None]
            if len(vals) < 2:
                p.setPen(text)
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No signal yet")
                p.end()
                return
            lo, hi = chart_range(vals)
            cols = pixel.spark_columns(self.values, lo, hi, w - 12, h - 20, step=4)
            c, fill = QColor(self.colour), QColor(self.colour)
            fill.setAlpha(60)
            prev = None
            for cx, cy in cols:
                for yy in range(cy + 4, h - 20, 4):
                    if (cx // 4 + yy // 4) % 2 == 0:
                        p.fillRect(6 + cx, 6 + yy, 4, 2, fill)
                p.fillRect(6 + cx, 6 + cy, 4, 2, c)
                if prev is not None and prev != cy:
                    p.fillRect(6 + cx, 6 + min(prev, cy), 2, abs(prev - cy) + 2, c)
                prev = cy
            p.setPen(text)
            p.drawText(QPointF(6, h - 4), f"{lo:.0f} to {hi:.0f} dBm, last {CHART_SECONDS} seconds")
            p.end()

    class Waterfall(QWidget):
        """Motion over the last two minutes, one row for each link, hot colours where it wobbled."""

        def __init__(self):
            super().__init__()
            self.setMinimumHeight(70)
            self.rows = {}

        def push(self, dbs):
            for name in set(self.rows) | set(dbs):
                self.rows[name] = (self.rows.get(name, []) + [dbs.get(name, 0.0)])[-WATERFALL_COLUMNS:]
            self.update()

        def paintEvent(self, _):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            w, h = self.width(), self.height()
            names = sorted(self.rows)
            if not names:
                p.end()
                return
            row_h = max(6, min(22, (h - 14) // len(names)))
            cell = max(2, w // WATERFALL_COLUMNS)
            for r, name in enumerate(names):
                series = self.rows[name]
                x0 = w - len(series) * cell
                for i, db in enumerate(series):
                    p.fillRect(x0 + i * cell, 2 + r * (row_h + 2), cell, row_h, QColor(heat(db)))
            p.setPen(self.palette().color(QPalette.ColorRole.WindowText))
            p.drawText(QPointF(4, h - 2), "2 minutes ago")
            p.drawText(QPointF(w - 28, h - 2), "now")
            p.end()

    def theme():
        return getattr(win, "theme", "dark")

    class Room3D(QWidget):
        """The room in 3D: the uConsole in the middle, a post for each followed sender, and a line for each link
        that lights up while its signal is disturbed and fades over the next minute. The floor is a plain grid and
        is never shaded, because no spot on it is a real place. It shows which links are disturbed, not people and
        not a place. Drag to turn it (it settles on a tidy angle), double click to reset."""

        def __init__(self):
            super().__init__()
            self.setMinimumSize(240, 230)
            self.posts, self.state = [], "calibrating"
            self.trails = {}
            self.yaw, self.drag = room3d.YAWS[0], None
            self.tick = 0                          # readings so far: the only thing that moves the picture

        def set_nodes(self, nodes, state):
            """New readings once a second: lay the posts out here, not while painting."""
            self.posts = room3d.layout(nodes)
            self.state = state
            self.tick += 1
            for p in self.posts:
                tr = self.trails.setdefault(p["name"], [])
                tr.append(p["height"])
                del tr[:-13]
            self.update()

        def mousePressEvent(self, e):
            self.drag = (e.position().x(), self.yaw)

        def mouseMoveEvent(self, e):
            if self.drag:
                self.yaw = self.drag[1] + (e.position().x() - self.drag[0]) * 0.012
                self.update()

        def mouseReleaseEvent(self, _):
            if self.drag:
                self.yaw, self.drag = room3d.snap_yaw(self.yaw), None
                self.update()

        def mouseDoubleClickEvent(self, _):
            self.yaw = room3d.YAWS[0]
            self.update()

        def paintEvent(self, _):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            w, h = self.width(), self.height()
            scale, cx, cy = room3d.fit(w, h, self.yaw)
            mode = theme()
            col = room3d.colours(mode)

            def P(x, y, z):
                sx, sy, d = room3d.project(x, y, z, self.yaw, scale, cx, cy)
                return QPoint(round(sx), round(sy)), d
            outline = QPen(QColor(col["grid"]), 1)
            n, step = room3d.CELLS, 2 * room3d.FLOOR / room3d.CELLS
            for i in range(n):
                for j in range(n):
                    x0, z0 = -room3d.FLOOR + i * step, -room3d.FLOOR + j * step
                    poly = QPolygon([P(x0, 0, z0)[0], P(x0 + step, 0, z0)[0], P(x0 + step, 0, z0 + step)[0], P(x0, 0, z0 + step)[0]])
                    p.setPen(outline)                                         # a plain grid: no tile is ever lit
                    p.setBrush(QColor(col["floor"]))
                    p.drawPolygon(poly)
            middle = P(0, 0, 0)[0]
            for q in self.posts:                                                 # each link, under the posts
                look = room3d.link_look(q["db"], q["trail"], q["sure"])
                if look["kind"] == "unsure":                                     # not known: dashed, never "quiet"
                    p.setPen(QPen(QColor(col["unsure"]), 1, Qt.PenStyle.DashLine))
                elif look["kind"] == "quiet":
                    p.setPen(QPen(QColor(col["quiet"]), 1))
                else:
                    p.setPen(QPen(QColor(room3d.heat_colour(look["shown"], mode)), look["width"]))
                p.drawLine(middle, P(q["x"], 0, q["z"])[0])
                p.setPen(QPen(QColor(col["text"]), 1))
                for (ax, az), (bx, bz) in room3d.ripple_bars(q, self.tick):      # bars all along it, stepping per reading
                    p.drawLine(P(ax, 0, az)[0], P(bx, 0, bz)[0])

            def prism(x, z, half, height, colour, edge):
                base = [(x - half, z - half), (x + half, z - half), (x + half, z + half), (x - half, z + half)]
                faces = []
                for k in range(4):
                    (ax, az), (bx, bz) = base[k], base[(k + 1) % 4]
                    quad = QPolygon([P(ax, 0, az)[0], P(bx, 0, bz)[0], P(bx, height, bz)[0], P(ax, height, az)[0]])
                    faces.append((P((ax + bx) / 2, 0, (az + bz) / 2)[1], quad, 110 + 25 * k))
                for _d, quad, dark in sorted(faces, key=lambda f: f[0]):          # far sides first
                    p.setPen(QPen(edge, 1))
                    p.setBrush(QColor(colour).darker(dark))
                    p.drawPolygon(quad)
                p.setBrush(QColor(colour))
                p.drawPolygon(QPolygon([P(ax_, height, az_)[0] for ax_, az_ in base]))

            edge = QColor(col["edge"])
            items = [(P(0, 0, 0)[1], "device", None)] + [(P(q["x"], 0, q["z"])[1], "post", q) for q in self.posts]
            for _d, kind, q in sorted(items, key=lambda it: it[0]):                # far ones first, near ones over them
                if kind == "device":
                    prism(0, 0, 0.13, 0.32, col["device"], QColor("#ff9a6a") if self.state == "movement" else edge)
                    continue
                if not q["sure"]:                                                 # an empty outline: nothing known
                    prism(q["x"], q["z"], 0.09, 0.06, col["floor"], QColor(col["unsure"]))
                    continue
                for old in q_trail(self.trails.get(q["name"], [])):                # faint caps where it was a few seconds ago
                    p.setPen(QPen(QColor(room3d.heat_colour(4.0, mode)), 1))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    h0 = 0.09
                    p.drawPolygon(QPolygon([P(q["x"] + dx, old, q["z"] + dz)[0] for dx, dz in ((-h0, -h0), (h0, -h0), (h0, h0), (-h0, h0))]))
                prism(q["x"], q["z"], 0.09, q["height"], room3d.heat_colour(q["db"], mode), edge)
            font = QFont(self.font())
            font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
            font.setPointSize(8)
            p.setFont(font)
            fm = QFontMetrics(font)
            spots = sorted(((P(q["x"], q["height"] if q["sure"] else 0.06, q["z"])[0], q) for q in self.posts),
                           key=lambda s: s[0].y())
            last_y = -99
            for top, q in spots:                                                  # labels last, pushed apart, never cut off
                text = room3d.post_label(q["label"], q["db"], q["sure"])
                tw = fm.horizontalAdvance(text) + 6
                y = max(last_y + 13, top.y() - 16)
                y = min(y, h - 30)
                last_y = y
                x = max(2, min(w - tw - 2, top.x() - tw // 2))
                p.fillRect(QRect(x, y, tw, 13), QColor(col["label_bg"]))
                p.setPen(QPen(QColor(col["label_edge"]), 1))
                p.drawRect(QRect(x, y, tw, 13))
                p.setPen(QColor(col["text"] if q["sure"] else col["caption"]))
                p.drawText(QPoint(x + 3, y + 10), text)
            p.setPen(QColor(col["caption"]))
            p.drawText(QPoint(4, h - 4), "nearer = stronger signal, direction unknown")
            p.end()

    class Strips(QWidget):
        """A thin strip for each link under the room: the last three minutes as quiet, subtle or strong, hatched
        where too few readings came in to say. Redrawn only when a new reading arrives."""

        ROW, NAME = 12, 108

        def __init__(self):
            super().__init__()
            self.rows = []
            self.setFixedHeight(0)

        def set_rows(self, rows):
            self.rows = rows
            self.setFixedHeight(len(rows) * (self.ROW + 2) + 2 if rows else 0)
            self.update()

        def paintEvent(self, _):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            mode = theme()
            col = room3d.colours(mode)
            font = QFont(self.font())
            font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
            font.setPointSize(8)
            p.setFont(font)
            cell = max(2, (self.width() - self.NAME - 2) // STRIP_COLUMNS)
            for r, (label, codes) in enumerate(self.rows):
                y = 1 + r * (self.ROW + 2)
                p.setPen(QColor(col["text"]))
                p.drawText(QPoint(2, y + 10), str(label)[-16:])
                p.fillRect(self.NAME, y, cell * len(codes), self.ROW, QColor(col["floor"]))
                for i, code in enumerate(codes):
                    if code is None:
                        continue
                    brush = QBrush(QColor(room3d.strip_colour(code, mode)))
                    if code < 0:                                              # not sure: hatched, not a flat colour
                        brush.setStyle(Qt.BrushStyle.Dense5Pattern)
                    p.fillRect(self.NAME + i * cell, y, cell, self.ROW, brush)
            p.end()

    class Pulse(QWidget):
        """The header's activity mark: a dot with rings that grow with the motion level. Abstract on purpose, and
        in the header rather than the room: it is not a picture of anything and has no place."""

        def __init__(self):
            super().__init__()
            self.setFixedSize(30, 30)
            self.rings, self.colour = [], style.MUTED["dark"]
            self.setToolTip(PULSE_TIP)

        def show_rings(self, rings, colour):
            self.rings, self.colour = rings, colour
            self.update()

        def paintEvent(self, _):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            c = QColor(self.colour)
            mid = self.width() // 2
            p.fillRect(mid - 2, mid - 2, 5, 5, c)
            p.setPen(QPen(c, 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            for f in self.rings:
                half = 3 + round(f * (mid - 4))
                p.drawRect(QRect(mid - half, mid - half, 2 * half, 2 * half))
            p.end()

    def link_pen(look, col, mode):
        """The pen for one link in the flat views, from the same look the room's lines use: dashed and muted when
        not sure, a thin calm line when quiet, else wider and hotter the more it is disturbed."""
        if look["kind"] == "unsure":
            return QPen(QColor(col["unsure"]), 1, Qt.PenStyle.DashLine)
        if look["kind"] == "quiet":
            return QPen(QColor(col["quiet"]), 1)
        return QPen(QColor(room3d.heat_colour(look["shown"], mode)), look["width"])

    def flat_font(p, widget):
        font = QFont(widget.font())
        font.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
        font.setPointSize(8)
        p.setFont(font)
        return QFontMetrics(font)

    def flat_label(p, fm, col, x, y, text, sure, w, h):
        """A name box centred on x with its top at y, kept inside the widget and above the caption."""
        tw = fm.horizontalAdvance(text) + 6
        x = max(2, min(w - tw - 2, x - tw // 2))
        y = max(2, min(h - 44, y))
        p.fillRect(QRect(x, y, tw, 13), QColor(col["label_bg"]))
        p.setPen(QPen(QColor(col["label_edge"]), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)                 # or the outline would fill with the last brush used
        p.drawRect(QRect(x, y, tw, 13))
        p.setPen(QColor(col["text"] if sure else col["caption"]))
        p.drawText(QPoint(x + 3, y + 10), text)

    def flat_caption(p, fm, col, w, h, parts):
        lines = radar.caption_lines(parts, w - 8, fm.horizontalAdvance)
        p.setPen(QColor(col["caption"]))
        for i, text in enumerate(lines):
            p.drawText(QPoint(4, h - 4 - 12 * (len(lines) - 1 - i)), text)

    class Radar(QWidget):
        """Rings for signal strength and one arc for each sender. The arc thickens and heats while that link's
        signal is disturbed, keeps a fading colour for a minute after, and is dashed when too few readings arrive
        to say. There is no sweeping beam: a beam that turns looks like the radio searching in a direction, which
        it cannot do. Which part of the circle an arc takes means nothing. Redrawn only when a reading arrives."""

        def __init__(self):
            super().__init__()
            self.setMinimumSize(320, 260)
            self.segments, self.state = [], "calibrating"
            self.tick = 0                          # readings so far: the only thing that changes the picture

        def set_nodes(self, nodes, state):
            self.segments, self.state = radar.segments(nodes), state
            self.tick += 1
            self.update()

        def paintEvent(self, _):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            w, h = self.width(), self.height()
            cx, cy, big = w // 2, (h - 28) // 2, max(20, min(w, h - 28) // 2 - 10)
            mode = theme()
            col = room3d.colours(mode)
            p.fillRect(cx - big - 4, cy - big - 4, 2 * big + 8, 2 * big + 8, QColor(col["floor"]))
            p.setPen(QPen(QColor(col["grid"]), 1))
            for f in radar.RINGS:                                                # only a scale for signal strength
                p.drawPolyline(QPolygon([QPoint(x, y) for x, y in radar.ring_points(cx, cy, big * f)]))
            for s in self.segments:                                              # each link is its own arc
                look = room3d.link_look(s["db"], s["trail"], s["sure"])
                p.setPen(link_pen(look, col, mode))
                arc = radar.arc_points(cx, cy, big * s["r"], s["a0"], s["a1"])
                p.drawPolyline(QPolygon([QPoint(x, y) for x, y in arc]))
                p.setPen(QPen(QColor(col["text"]), 1))
                for (ax, ay), (bx, by) in radar.arc_ticks(s, self.tick, cx, cy, big):
                    p.drawLine(ax, ay, bx, by)
            p.setPen(QPen(QColor(col["edge"]), 1))
            p.setBrush(QColor(col["device"]))
            p.drawRect(QRect(cx - 5, cy - 5, 10, 10))
            fm = flat_font(p, self)
            for s in self.segments:
                x, y = radar.label_spot(s, cx, cy, big)
                flat_label(p, fm, col, x, y - 6, room3d.post_label(s["label"], s["db"], s["sure"]), s["sure"], w, h)
            flat_caption(p, fm, col, w, h, RADAR_CAPTION)
            p.end()

    class Ring(QWidget):
        """The room's own layout looked at straight down: the uConsole in the middle, a small plain square for
        each sender in its made up slot, and the link line between them, which is the only thing that lights up.
        The floor is a plain grid and no spot on it is a real place. Redrawn only when a reading arrives."""

        def __init__(self):
            super().__init__()
            self.setMinimumSize(240, 230)
            self.posts, self.state = [], "calibrating"
            self.tick = 0

        def set_nodes(self, nodes, state):
            self.posts, self.state = room3d.layout(nodes), state
            self.tick += 1
            self.update()

        def paintEvent(self, _):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            w, h = self.width(), self.height()
            scale, cx, cy = radar.plan_fit(w, h - 12)
            mode = theme()
            col = room3d.colours(mode)

            def P(x, z):
                return QPoint(*radar.plan_point(x, z, scale, cx, cy))
            corner, far = P(-room3d.FLOOR, -room3d.FLOOR), P(room3d.FLOOR, room3d.FLOOR)
            p.fillRect(QRect(corner, far), QColor(col["floor"]))                 # one flat colour: no patch is ever lit
            p.setPen(QPen(QColor(col["grid"]), 1))
            for (ax, ay), (bx, by) in radar.plan_grid(scale, cx, cy):
                p.drawLine(ax, ay, bx, by)
            middle = P(0, 0)
            for q in self.posts:
                look = room3d.link_look(q["db"], q["trail"], q["sure"])
                p.setPen(link_pen(look, col, mode))
                p.drawLine(middle, P(q["x"], q["z"]))
                p.setPen(QPen(QColor(col["text"]), 1))
                for (ax, az), (bx, bz) in room3d.ripple_bars(q, self.tick):      # bars all along it, stepping per reading
                    p.drawLine(P(ax, az), P(bx, bz))
            for q in self.posts:                                                 # the sender: plain, it never lights up
                at = P(q["x"], q["z"])
                p.setPen(QPen(QColor(col["text"] if q["sure"] else col["unsure"]), 1))
                p.setBrush(QColor(col["label_bg"]))
                p.drawRect(QRect(at.x() - 4, at.y() - 4, 8, 8))
            p.setPen(QPen(QColor(col["edge"]), 1))
            p.setBrush(QColor(col["device"]))
            p.drawRect(QRect(middle.x() - 6, middle.y() - 6, 12, 12))
            fm = flat_font(p, self)
            for q in self.posts:
                at = P(q["x"], q["z"])
                flat_label(p, fm, col, at.x(), at.y() + 7, room3d.post_label(q["label"], q["db"], q["sure"]),
                           q["sure"], w, h)
            flat_caption(p, fm, col, w, h, ABOVE_CAPTION)
            p.end()

    class Page(QWidget):
        def __init__(self):
            super().__init__()
            self.engine, self.source, self.recording = Engine(), None, None
            self.only = None                       # (bssid, channel) of the one network picked in Survey, if any
            self.history, self.tick_count = [], 0
            self.links = LinkHistory()
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.tick)
            root = QVBoxLayout(self)
            root.setContentsMargins(0, 0, 0, 0)
            root.setSpacing(3)
            bar, bl = card()                                         # one row: the state, then the controls
            row = QHBoxLayout()
            row.setSpacing(6)
            self.state = QLabel("Watch is off.")
            self.state.setStyleSheet("font-size: 16pt; font-weight: bold;")
            self.state.setMinimumWidth(260)
            self.detail = QLabel("It listens to the signal strength of the network you are connected to. "
                                 "Nothing is sent or scanned.")
            self.detail.setWordWrap(True)
            self.detail.setStyleSheet("font-size: 11pt; color: #9e9e9e;")
            self.watch_b = QPushButton("Start watching")
            self.cal_b = QPushButton("Learn again")
            self.rec_b = QPushButton("Record")
            self.sens = QComboBox()
            for name in SENSITIVITY:
                self.sens.addItem(name)
            self.sens.setCurrentText("Medium")
            self.sens.setToolTip("Sensitivity")
            self.src = QComboBox()
            self.src.setToolTip("What to listen through")
            self.view = QComboBox()
            self.view.addItem("3D room", 0)
            self.view.addItem("From above", 1)
            self.view.addItem("Radar", 2)
            self.view.setToolTip("How to draw the room")
            for w in (self.watch_b, self.cal_b, self.rec_b, self.sens, self.src, self.view):
                w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.watch_b.clicked.connect(self.toggle)
            self.cal_b.clicked.connect(self.recalibrate)
            self.rec_b.clicked.connect(self.toggle_record)
            self.sens.activated.connect(lambda _=0: self.engine.set_sensitivity(self.sens.currentText()))
            self.pulse = Pulse()
            row.addWidget(self.pulse)
            row.addWidget(self.state)
            for w in (self.watch_b, self.cal_b, self.rec_b, self.sens):
                row.addWidget(w)
            row.addWidget(self.src, 1)
            row.addWidget(self.view)
            bl.addLayout(row)
            bar.setToolTip("The air source listens to every sender around wlan1 in monitor mode and puts the radio back when "
                           "you stop. Breathing, heart rate and pose are not offered: signal strength cannot give them.")
            root.addWidget(bar)
            self.chart, self.waterfall = Chart(), Waterfall()
            self.meter = QLabel("")
            self.meter.setStyleSheet("font-size: 12pt;")
            self.meter.setWordWrap(True)
            self.ring, self.room, self.radar = Ring(), Room3D(), Radar()
            self.stack = QStackedWidget()
            self.stack.addWidget(self.room)
            self.stack.addWidget(self.ring)
            self.stack.addWidget(self.radar)
            self.view.activated.connect(lambda _=0: self.stack.setCurrentIndex(self.view.currentData()))
            self.vitals = QLabel("")
            self.vitals.setWordWrap(True)
            self.vitals.setStyleSheet("font-size: 12pt;")
            self.log = QPlainTextEdit()
            self.log.setReadOnly(True)
            hero, hl2 = card()                                       # the room takes the left, most of the page
            room_tag = QLabel("THE ROOM")
            room_tag.setObjectName("tag")
            self.line = QLabel("")                                  # which links are disturbed, in plain words
            self.line.setWordWrap(True)
            self.line.setStyleSheet("font-size: 12pt; font-weight: bold;")
            self.strips = Strips()
            self.strip_note = QLabel(LINK_CAPTION + " " + STRIP_CAPTION)
            self.strip_note.setWordWrap(True)
            self.strip_note.setStyleSheet("font-size: 10pt; color: #9e9e9e;")
            self.strip_note.hide()                                   # nothing to explain until there is a strip
            hl2.addWidget(room_tag, 0, Qt.AlignmentFlag.AlignLeft)
            hl2.addWidget(self.line)
            hl2.addWidget(self.stack, 1)
            hl2.addWidget(self.strips)
            hl2.addWidget(self.strip_note)
            hl2.addWidget(self.vitals)
            side, sl = card()                                        # the signal behind it, stacked on the right
            signal_tag = QLabel("SIGNAL")
            signal_tag.setObjectName("tag")
            sl.addWidget(signal_tag, 0, Qt.AlignmentFlag.AlignLeft)
            sl.addWidget(self.detail)
            sl.addWidget(self.meter)
            sl.addWidget(self.chart, 2)
            sl.addWidget(self.waterfall, 3)
            sl.addWidget(self.log, 1)
            self.split = QSplitter(Qt.Orientation.Horizontal)
            self.split.setChildrenCollapsible(False)
            self.split.addWidget(hero)
            self.split.addWidget(side)
            self.split.setStretchFactor(0, 3)
            self.split.setStretchFactor(1, 2)
            self.split.setSizes([700, 460])
            root.addWidget(self.split, 1)
            self.refresh_sources()

        def refresh_sources(self):
            levels = read_levels()
            options = source_options(levels)
            if air_available() and AIR_IFACE not in levels:
                try:
                    where = subprocess.run(["/usr/sbin/iw", "dev", "wlan0", "info"], capture_output=True, text=True,
                                           timeout=3).stdout
                except (OSError, subprocess.SubprocessError):
                    where = ""
                ch = air_channel(where)
                options.append((air_source(ch), f"Air around {AIR_IFACE} (channel {ch}, every sender)"))
                if self.only:                      # the network picked in Survey stays in the list to go back to
                    mac, och = self.only
                    options.append((air_source(och, mac), f"Air around {AIR_IFACE} (channel {och}, only "
                                                          f"{win.bssid_registry.label(mac)})"))
            keep = self.src.currentData()                 # the interface name, not the display text: the text
            self.src.blockSignals(True)                    # includes the dBm reading, which changes every refresh
            self.src.clear()
            for name, text in options:
                self.src.addItem(text, name)
            if not options:
                self.src.addItem("no connected Wi-Fi", None)
            self.src.setCurrentIndex(max(0, self.src.findData(keep)))
            self.src.blockSignals(False)

        def toggle(self):
            self.stop() if self.timer.isActive() else self.start()

        def watch_only(self, bssid, channel):
            """Survey's "Sense this network": watch just that access point, on its channel. Whoever calls this has
            already stopped the survey, so the radio is free. If it cannot run, the page says why and stays off."""
            if self.timer.isActive():
                self.stop()
            self.only = (bssid, channel)
            blocked = air_blocker(air_available(), read_levels())
            if blocked:
                self.state.setText(blocked[0])
                self.detail.setText(blocked[1])
                return
            self.refresh_sources()
            at = self.src.findData(air_source(channel, bssid))
            if at < 0:
                return
            self.src.setCurrentIndex(at)
            self.start()

        def start(self):
            self.refresh_sources()
            self.source = self.src.currentData()
            if not self.source:
                self.state.setText("Nothing to listen through.")
                self.detail.setText("Connect wlan0 to a network first. This only listens to that connection.")
                return
            self.engine = Engine(self.sens.currentText())
            self.history, self.tick_count = [], 0
            self.links = LinkHistory()
            self.log.clear()
            self.waterfall.rows = {}
            self.feed, self.native = None, {}
            if self.source.startswith(AIR_PREFIX):
                try:
                    channel, only = parse_air_source(self.source)
                    self.air = AirLinks(label_for=win.bssid_registry.label, only=only)
                    self.feed = AirFeed(channel)
                except (OSError, ValueError) as exc:
                    self.state.setText("Could not start the air listener.")
                    self.detail.setText(str(exc))
                    return
                self.timer.start(1000)
                if only:
                    self.state.setText("Waiting to hear it...")
                    self.detail.setText(f"Listening for {win.bssid_registry.label(only)} on channel {channel}. "
                                        "Nothing is sent.")
                else:
                    self.state.setText("Choosing which senders to follow...")
                    self.detail.setText("Listening to every sender in the air around the internal Wi-Fi radio. "
                                        "Nothing is sent.")
            else:
                self.timer.start(SAMPLE_MS)
            self.watch_b.setText("Stop watching")
            win.activity.add(f"Wi-Fi sense started on {source_title(self.source, win.bssid_registry.label)}")

        def stop(self):
            self.timer.stop()
            if getattr(self, "feed", None):
                self.feed.stop()
                self.feed = None
            self.watch_b.setText("Start watching")
            self.state.setText("Watch is off.")
            self.line.setText("")                      # nothing is being measured now, so say nothing about the room
            self.pulse.show_rings([], style.MUTED["light" if theme() == "light" else "dark"])
            if self.recording:
                self.toggle_record()
            win.activity.add("Wi-Fi sense stopped")

        def recalibrate(self):
            if getattr(self, "feed", None):
                self.air, self.native = AirLinks(label_for=win.bssid_registry.label, only=self.air.only), {}
            self.engine = Engine(self.sens.currentText())
            self.history = []
            self.links = LinkHistory()
            self.waterfall.rows = {}
            self.log.clear()

        def toggle_record(self):
            if self.recording:
                self.recording.close()
                win.activity.add(f"Wi-Fi sense recording saved: {self.recording.name}")
                self.recording = None
                self.rec_b.setText("Record")
                return
            folder = os.path.join(os.path.expanduser("~"), "aiov2-captures")
            os.makedirs(folder, mode=0o700, exist_ok=True)
            path = os.path.join(folder, time.strftime("sense-%Y%m%d-%H%M%S.csv"))
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            self.recording = os.fdopen(fd, "w")
            self.recording.write("time,state,motion_db," + (self.source or "link") + "\n")
            self.rec_b.setText("Stop recording")

        def tick(self):
            if getattr(self, "feed", None):
                return self.tick_air()
            now = time.time()
            level = read_levels().get(self.source)
            self.engine.add(self.source, now, level, native=1000.0 / SAMPLE_MS)
            self.history = (self.history + [level])[-int(CHART_SECONDS * 1000 / SAMPLE_MS):]
            self.tick_count += 1
            if self.tick_count % 5:                      # the maths and the drawing run once a second
                return
            self.render(now, level, self.engine.step(now))

        def tick_air(self):
            """Take what the listener has printed since last time: one record a second."""
            done = self.feed.finished()                 # asked before taking records, so the last ones are not missed
            recs = self.feed.records()
            if not recs and done:
                self.timer.stop()
                msg = self.feed.problem() or "The listener stopped."
                self.state.setText("The air listener stopped.")
                self.detail.setText(msg + "  (Needs the internal USB Wi-Fi: switch the USB rail on, and run "
                                    "'sudo aiov2_ctl --setup-sudoers' once.)")
                self.feed.stop()
                self.feed = None
                self.watch_b.setText("Start watching")
                return
            for rec in recs:
                got = self.air.feed(rec)
                for name, pts in self.air.readings(rec).items():
                    rate = self.native.setdefault(name, 10.0 if len(pts) >= 6 else 1.0)
                    for when, dbm in pts:
                        self.engine.add(name, when, dbm, native=rate)
                # one chosen sender: chart its own signal, not the average of everything heard on the channel
                level = next(iter(got.values()), None) if self.air.only else rec.get("rssi")
                self.history = (self.history + [level])[-CHART_SECONDS:]
                # step() must run every record, not only when a pinned sender happened to appear in this one,
                # or a state such as MOVEMENT never gets the chance to time out once that sender goes quiet.
                events = self.engine.step(rec["t"])
                if self.air.only and self.engine.started is None:
                    # not heard at all yet: do not pretend to be learning a room from no readings
                    channel, _mac = parse_air_source(self.source)
                    self.detail.setText(waiting_text(self.air.label_for(self.air.only), channel))
                    continue
                if self.air.pinned is None:
                    self.detail.setText(f"Choosing which senders to follow ({self.air.seen} of {AIR_SETTLE} seconds)...")
                    continue
                self.render(rec["t"], level, events)

        def render(self, now, level, events):
            for kind in events:
                self.log.appendPlainText(event_text(kind, now))
                win.activity.add("Wi-Fi sense: " + event_text(kind, now).split("  ", 1)[1])
                if kind == "movement_start":
                    on = source_title(self.source, win.bssid_registry.label)
                    win.notify_user("sense", "Movement sensed", f"Wi-Fi signal on {on} is wobbling", "warn")
            e = self.engine
            if self.recording:
                self.recording.write(csv_line(now, e.state, e.db, {self.source: level}) + "\n")
                self.recording.flush()
            colour = state_colour(e.state, theme())
            word = STATE_WORDS[e.state] + (f" {int(e.progress * 100)}%" if e.state == "calibrating" else "")
            self.state.setText(word)
            self.state.setStyleSheet(f"font-size: 16pt; font-weight: bold; color: {colour};")
            self.chart.values, self.chart.colour = self.history, colour
            self.chart.update()
            self.waterfall.push(e.link_dbs())
            air = bool(getattr(self, "feed", None))
            dbm = dict(self.air.last) if air else {self.source: level}
            dbs, rel = e.link_dbs(), e.link_reliability()
            doubt = [n for n in dbs if not link_sure(rel.get(n))]
            # while learning nothing is known either, so every link is drawn as uncertain, not as quiet
            sure = {n: e.state != "calibrating" and n not in doubt for n in dbs}
            self.links.push(now, dbs, sure)
            nodes = [{"name": n, "db": d, "dbm": dbm.get(n), "label": n, "sure": sure[n],
                      "trail": self.links.trail(n, now)} for n, d in dbs.items()]
            self.room.set_nodes(nodes, e.state)                  # all three views get the same readings
            self.ring.set_nodes(nodes, e.state)
            self.radar.set_nodes(nodes, e.state)
            # the connected link is named by its interface, so say whose access point the path runs to
            said = {n: n if air else f"the {n} access point" for n in dbs}
            act = e.activity
            self.line.setText(room_line(
                e.state, dbs, said, dsp.PRESENCE_ON_DB + e.offset,
                moving_for=None if act.moving_since is None else now - act.moving_since,
                still_for=None if act.last_moved is None else now - act.last_moved, unsure=doubt))
            self.strips.set_rows([(n, self.links.strip(n, now)) for n in sorted(dbs, key=str)])
            self.strip_note.setVisible(bool(dbs))
            self.pulse.show_rings(pulse_rings(e.level(), self.room.tick), colour)
            now_s = f"{level:.0f} dBm" if level is not None else "no reading"
            self.meter.setText(f"Signal {now_s}    Motion {e.db:+.1f} dB ({dsp.classify(e.db)})    "
                               f"Readings {int(e.reliability() * 100)}% of expected")
            self.detail.setText({
                "calibrating": "Learning what a still room looks like. Keep the uConsole still and stay out of the way.",
                "quiet": "The signal is steady. Anything moving nearby will make it wobble.",
                "recent": "There was activity a moment ago, nothing moving right now.",
                "movement": "The signal is being disturbed: something is moving on or near a path it takes."}[e.state])
            self.vitals.setText(e.activity.history_text(now))

    return Page()
