"""How long the battery will last at the present draw, worked out only from what the gauge reports.

The uConsole's gauge (read by aiov2_ctl) gives a percentage, a voltage, a current and so a power in watts. It
gives no energy or charge figure, so the size of the battery is not known. This module learns it while the app
watches: it adds up the watt hours drawn between the moments the percentage steps down, which gives "watt hours
per one percent". Time left is then the percent remaining times that figure, divided by the average draw of the
last few minutes. Until the percentage has fallen far enough to learn from, it says "working it out" and shows
no number. The learned figure is kept in a small file so the next start of the app does not begin from nothing.

Everything here is plain arithmetic and sentences with no Qt, so all of it is unit tested.
"""
import json
import os
import threading
import time

WINDOW = 300            # seconds of draw the average is taken over
MIN_SPAN = 60           # the average needs at least this many seconds of readings behind it...
MIN_SAMPLES = 5         # ...and this many readings
GAP = 150               # a longer silence than this (suspend, a stuck reader) and the sums start again.
                        # The slowest refresh the Settings offer is 60 s, so two missed readings is a gap
MIN_POINTS = 2          # percent the battery must fall (or rise) between steps before a rate is believed
JUMP = 3                # a step this big between two readings is the gauge re-thinking, not real use
CHARGE_POINTS = 3       # time to full looks at the last few percent gained, as charging slows as it fills
CHARGE_TOP = 90         # above this the charger tapers off and a straight line guess would be too hopeful
MIN_WATTS = 0.2         # below this the reading is not a believable draw for this machine
WH_PER_PCT_RANGE = (0.02, 2.0)      # a 2 Wh to 200 Wh battery; anything outside is a bad sum, not a battery
RAIL_LABEL = {"GPS": "GPS", "LORA": "LoRa", "SDR": "SDR", "USB": "USB"}
RAILS = ("GPS", "LORA", "SDR", "USB")


# ---- reading the status

def direction(power):
    """'discharging', 'charging' or 'idle' from the status' power block, or None when there is no reading."""
    if not isinstance(power, dict) or power.get("capacity") is None:
        return None
    d = str(power.get("direction") or "").lower()
    if d in ("discharging", "charging", "idle"):
        return d
    status = str(power.get("status") or "").lower()
    return "discharging" if "disch" in status else "charging" if "charg" in status else "idle"


def number(value):
    """A float, or None for anything that is not a plain number (a bool is not a reading)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


# ---- arithmetic

def mean_power(samples, now, window=WINDOW):
    """(mean watts, seconds covered) of the (time, watts) samples in the last `window` seconds, or (None, 0)
    when there are too few of them to trust. A plain mean over minutes, so one spike barely moves it."""
    recent = [(t, w) for t, w in samples if 0 <= now - t <= window]
    if len(recent) < MIN_SAMPLES:
        return None, 0
    span = max(t for t, _ in recent) - min(t for t, _ in recent)
    if span < MIN_SPAN:
        return None, 0
    return sum(w for _, w in recent) / len(recent), span


def sane_capacity(wh_per_pct):
    v = number(wh_per_pct)
    return v is not None and WH_PER_PCT_RANGE[0] <= v <= WH_PER_PCT_RANGE[1]


def hours_left(pct, wh_per_pct, watts):
    """Hours until empty: percent remaining times watt hours per percent, over the draw. None if any part is
    missing or not believable."""
    pct, watts = number(pct), number(watts)
    if pct is None or watts is None or watts < MIN_WATTS or not sane_capacity(wh_per_pct) or pct < 0:
        return None
    return pct * wh_per_pct / watts


def charge_rate(edges, points=CHARGE_POINTS):
    """(percent per hour, percent it was measured over) from the [(time, percent)] steps up seen while
    charging, looking only at the last few percent. None until enough has been gained."""
    if len(edges) < 2:
        return None, 0
    last_t, last_p = edges[-1]
    first_t, first_p = edges[0]
    for t, p in reversed(edges):
        first_t, first_p = t, p
        if last_p - p >= points:
            break
    gained, took = last_p - first_p, last_t - first_t
    if gained < MIN_POINTS or took <= 0:
        return None, 0
    return gained * 3600.0 / took, gained


def hours_to_full(pct, rate):
    pct, rate = number(pct), number(rate)
    if pct is None or rate is None or rate <= 0 or pct > CHARGE_TOP or pct >= 100:
        return None
    return (100 - pct) / rate


# ---- words

def _minutes(hours):
    """Whole minutes, rounded as coarsely as the estimate deserves: 5 minutes under an hour, 10 under ten
    hours, whole hours beyond."""
    m = hours * 60
    step = 5 if m < 60 else 10 if m < 600 else 60
    return int(round(m / step) * step)


def duration_words(hours):
    """'2 h 40 min', '45 min', '11 h', 'under 5 min', 'over 24 h'."""
    if hours > 24:
        return "over 24 h"
    m = _minutes(hours)
    if m < 5:
        return "under 5 min"
    h, rest = divmod(m, 60)
    return f"{rest} min" if not h else f"{h} h" if not rest else f"{h} h {rest} min"


def duration_brief(hours):
    """The same for the Home screen's pixel font: '2H 40M', '45M', '11H', '<5M', '>24H'."""
    if hours > 24:
        return ">24H"
    m = _minutes(hours)
    if m < 5:
        return "<5M"
    h, rest = divmod(m, 60)
    return f"{rest}M" if not h else f"{h}H" if not rest else f"{h}H {rest:02d}M"


def span_words(seconds):
    """What an average was taken over: '5 min', '90 s'."""
    seconds = int(round(seconds))
    return f"{seconds} s" if seconds < 120 else f"{int(round(seconds / 60))} min"


def short_options(est):
    """What Home's power block may add after the watts, longest first, so it can take the one that fits:
    ('~2H 40M LEFT', '~2H 40M'). Empty when there is nothing honest to say."""
    est = est or {}
    hours = est.get("hours")
    if est.get("state") == "discharging":
        if hours is None:
            return ("WORKING IT OUT",)
        brief = duration_brief(hours)
        mark = "" if brief[0] in "<>" else "~"
        return (f"{mark}{brief} LEFT", f"{mark}{brief}")
    if est.get("state") == "charging" and hours is not None:
        brief = duration_brief(hours)
        mark = "" if brief[0] in "<>" else "~"
        return (f"{mark}{brief} TO FULL", f"{mark}{brief}")      # the line above it already says CHARGING
    return ()


def sentence(est):
    """One plain sentence for the Overview's battery card, with what the number rests on. '' when there is
    nothing to say (idle on the charger, no reading, or charging with too little seen)."""
    est = est or {}
    state, hours = est.get("state"), est.get("hours")
    if state == "discharging":
        if hours is None:
            if est.get("wh_per_pct") is None:
                return (f"Time left: working it out. It needs the battery to fall {MIN_POINTS}% while the app "
                        "is watching.")
            return "Time left: working it out. It needs a minute of readings."
        if est.get("known"):
            learned = f"battery size {est['wh_per_pct'] * 100:.1f} Wh, as the battery reports it"
        else:
            learned = f"battery size worked out from {est.get('points', 0)}% of use"
            if est.get("earlier"):
                learned += " on an earlier run"
        return (f"About {duration_words(hours)} left at this draw ({est['watts']:.1f} W, from the last "
                f"{span_words(est.get('span', 0))}; {learned}). An estimate.")
    if state == "charging" and hours is not None:
        return (f"About {duration_words(hours)} to full at this rate (from the last {est.get('points', 0)}% "
                "gained). An estimate: charging slows near the top.")
    return ""


def rail_draws(snapshot, cache):
    """{rail: watts} for the rails that are on and whose draw was measured on battery. A measurement taken on
    the charger is left out, as the app already says it is not reliable."""
    rails = (snapshot or {}).get("rails") or {}
    out = {}
    for name in RAILS:
        m = (cache or {}).get(name)
        if rails.get(name) != "on" or not isinstance(m, dict) or m.get("source") == "AC":
            continue
        delta = number(m.get("delta"))
        if delta is not None and delta > 0:
            out[name] = delta
    return out


def _names(names):
    names = [RAIL_LABEL.get(n, n) for n in names]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def rails_lines(draws, est):
    """['GPS and USB rails are about 1.3 W of the 3.1 W.', 'Without them: about 4 h (an estimate ...).'].
    Only while running on battery with a draw to compare against, and only when the measured rails add up
    to less than that draw: if they do not, the old measurements no longer describe this machine."""
    est = est or {}
    watts = est.get("watts")
    if est.get("state") != "discharging" or watts is None or not draws:
        return []
    total = sum(draws.values())
    if total <= 0 or total >= watts * 0.95:
        return []
    one = len(draws) == 1
    head = (f"The {_names(draws)} rail is" if one else f"{_names(list(draws))} rails are")
    lines = [f"{head} about {total:.1f} W of the {watts:.1f} W (measured earlier, one at a time)."]
    without = hours_left(est.get("pct"), est.get("wh_per_pct"), watts - total)
    if est.get("hours") is not None and without is not None:
        lines.append(f"Without {'it' if one else 'them'}: about {duration_words(without)} (an estimate).")
    return lines


def card_lines(est, draws=None):
    """Every line the Overview's battery card adds."""
    first = sentence(est)
    return ([first] if first else []) + rails_lines(draws or {}, est)


# ---- the learned battery size, kept between runs

def capacity_path():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "ops_gui", "battery-capacity.json")


def load_capacity(path=None):
    """(watt hours per percent, percent it was learned over) from the file, or (None, 0)."""
    try:
        with open(path or capacity_path()) as f:
            d = json.load(f)
        wh, points = d.get("wh_per_pct"), d.get("points")
        if sane_capacity(wh) and isinstance(points, int) and points >= MIN_POINTS:
            return float(wh), points
    except (OSError, ValueError, AttributeError):
        pass
    return None, 0


def save_capacity(wh_per_pct, points, path=None):
    path = path or capacity_path()
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"wh_per_pct": round(wh_per_pct, 4), "points": points}, f)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


# ---- the estimator

class Estimator:
    """Fed each status snapshot's power block with the time it was read; asked for an estimate at any time.

    `path` is where the learned battery size is kept (None: nowhere, as in the tests). Saving is a few bytes
    once every percent or so, done in a short thread so the screen never waits on the disk.
    """

    def __init__(self, path=None, wh_per_pct=None, points=0):
        self.path = path
        self.wh_per_pct, self.points, self.earlier = None, 0, False
        if sane_capacity(wh_per_pct):
            self.wh_per_pct, self.points, self.earlier = float(wh_per_pct), points, True
        self.dir, self.pct, self.last_t, self.last_w = None, None, None, None
        self.window = []                # (time, watts) while discharging
        self.known = False              # True once the battery's own stated size is in use
        self.restart()

    def use_known_size(self, wh_per_pct):
        """Use the size the battery itself reports, and stop learning one. A learned size is only as good as
        the percentage it was learned from: when the percentage on this board jumped from a stuck 100 to an
        estimate of 55, the app "learned" a 3 Wh battery from it, saved that, and said ten minutes were left."""
        if sane_capacity(wh_per_pct):
            self.wh_per_pct, self.points, self.earlier, self.known = float(wh_per_pct), 0, False, True

    @classmethod
    def from_file(cls, path=None):
        path = path or capacity_path()
        wh, points = load_capacity(path)
        return cls(path, wh, points)

    def restart(self):
        """Forget the steps seen so far (the direction changed, the gauge jumped, time went wrong). The
        learned battery size is a fact about the battery, so it is kept."""
        self.extreme = None             # lowest percent seen while discharging, highest while charging
        self.first_edge = None          # (time, percent) of the first step: sums start from a clean edge
        self.energy = 0.0               # watt hours drawn since that first step
        self.edges = []                 # every step, for the charging rate

    def feed(self, power, now=None):
        now = time.time() if now is None else now
        d = direction(power)
        if d is None:
            return
        pct, watts = number(power.get("capacity")), number(power.get("power"))
        if pct is None:
            return
        broken = self.last_t is not None and (now < self.last_t or now - self.last_t > GAP)
        if broken:                      # the clock went backwards or the app was asleep: no sum spans that
            self.restart()
            self.window = []
        if d != self.dir:
            self.restart()
            if d != "discharging":
                self.window = []        # the draw while charging is the charger's, not the machine's
        prev_t, prev_w = (None, None) if broken or d != self.dir else (self.last_t, self.last_w)
        self.dir, self.pct, self.last_t, self.last_w = d, pct, now, watts
        if d == "idle":
            return
        if d == "discharging" and watts is not None:
            self.window = [(t, w) for t, w in self.window if now - t <= WINDOW] + [(now, abs(watts))]
        if self.extreme is None:
            self.extreme = pct
            return
        if d == "discharging" and self.first_edge and prev_t is not None and prev_w is not None and watts is not None:
            self.energy += (abs(prev_w) + abs(watts)) / 2 * (now - prev_t) / 3600.0
        step = (self.extreme - pct) if d == "discharging" else (pct - self.extreme)
        if step >= JUMP or step <= -2:  # the gauge changed its mind; what was summed no longer lines up
            self.restart()
            self.extreme = pct
            return
        if step < 1:                    # no change, or one percent of flicker back the other way
            return
        self.extreme = pct
        self.edges = (self.edges + [(now, pct)])[-60:]
        if self.first_edge is None:
            self.first_edge, self.energy = (now, pct), 0.0
            return
        points = int(abs(pct - self.first_edge[1]))
        if d == "discharging" and points >= MIN_POINTS and not self.known:
            wh = self.energy / points
            if sane_capacity(wh):
                self.wh_per_pct, self.points, self.earlier = wh, points, False
                self.save()

    def save(self):
        if self.path:
            threading.Thread(target=save_capacity, args=(self.wh_per_pct, self.points, self.path),
                             daemon=True).start()

    def estimate(self, now=None):
        """{'state', 'hours' (None while working it out), 'watts', 'span', 'points', 'earlier', 'pct',
        'wh_per_pct'} for the sentences above."""
        now = time.time() if now is None else now
        out = {"state": self.dir, "hours": None, "pct": self.pct}
        if self.dir is None or self.last_t is None or now < self.last_t or now - self.last_t > GAP:
            out["state"] = None         # nothing recent: say nothing rather than repeat an old figure
            return out
        if self.dir == "discharging":
            watts, span = mean_power(self.window, now)
            out.update(watts=watts, span=span, points=self.points, earlier=self.earlier,
                       wh_per_pct=self.wh_per_pct, known=self.known)
            out["hours"] = hours_left(self.pct, self.wh_per_pct, watts)
        elif self.dir == "charging":
            rate, gained = charge_rate(self.edges)
            out.update(points=gained, hours=hours_to_full(self.pct, rate))
        return out
