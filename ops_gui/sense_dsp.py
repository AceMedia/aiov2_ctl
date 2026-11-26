"""
The signal processing behind Wi-Fi sensing, in plain numpy so it stays cheap enough for a battery.

The ideas come from reading wifisense-pi (a motion score as dB over a self measured noise floor, hysteresis with
minimum on and off times), and from WiFi-Sense
(a robust, simple, always on filter). The pose and heart rate claims of RuView are not used: they do not hold up.

Everything here takes numbers and returns numbers, so it is tested with synthetic signals, no radio needed.
A "link" is any series of signal strength readings: the connected access point, one access point heard in monitor
mode, or the motion score from a Marauder board.
"""
import math

import numpy as np

RATE = 20.0                         # every link is resampled to this many readings a second
MOTION_BAND = (0.5, 9.0)            # Hz: walking, gesturing, a door opening
MOTION_ON_DB, MOTION_OFF_DB = 6.0, 3.5
MOTION_MIN_ON, MOTION_MIN_OFF = 0.6, 2.0          # seconds
PRESENCE_ON_DB, PRESENCE_OFF_DB = 5.0, 2.5
PRESENCE_MIN_ON, PRESENCE_MIN_OFF = 4.0, 10.0
CLASS_EDGES = ((18.0, "vigorous"), (10.0, "active"), (0.0, "subtle"))
QUIET_DB = 3.0                     # readings below this count as quiet and may nudge the floor


def resample(times, values, rate=RATE, end=None, span=None):
    """Uniform readings from irregular ones: (grid of times, values). Gaps longer than a second keep the last value."""
    t = np.asarray(times, dtype=float)
    v = np.asarray(values, dtype=float)
    ok = ~np.isnan(v)
    t, v = t[ok], v[ok]
    if len(t) < 2:
        return np.zeros(0), np.zeros(0)
    stop = t[-1] if end is None else end
    start = t[0] if span is None else max(t[0], stop - span)
    grid = np.arange(start, stop, 1.0 / rate)
    if len(grid) == 0:
        return grid, np.zeros(0)
    return grid, np.interp(grid, t, v)


def hampel(x, half=3, sigmas=3.0):
    """Replace readings that are far from their neighbours (spikes) with the neighbourhood median."""
    x = np.asarray(x, dtype=float)
    if len(x) < 2 * half + 1:
        return x.copy()
    padded = np.pad(x, half, mode="reflect")
    win = np.lib.stride_tricks.sliding_window_view(padded, 2 * half + 1)
    med = np.median(win, axis=1)
    mad = 1.4826 * np.median(np.abs(win - med[:, None]), axis=1)
    mad = np.maximum(mad, 1e-6)
    out = x.copy()
    bad = np.abs(x - med) > sigmas * mad
    out[bad] = med[bad]
    return out


def band_power(x, rate, lo, hi):
    """Power of the signal between lo and hi Hz (the mean is removed first, so a steady level reads zero)."""
    x = np.asarray(x, dtype=float)
    if len(x) < 8:
        return 0.0
    x = x - np.mean(x)
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2 / len(x)
    freqs = np.fft.rfftfreq(len(x), 1.0 / rate)
    sel = (freqs >= lo) & (freqs <= hi)
    return float(np.sum(spec[sel])) if sel.any() else 0.0


def to_db(power, floor):
    return 10.0 * math.log10(max(power, 1e-12) / max(floor, 1e-12))


def noise_floor(history, floor_min=0.02, percentile=50):
    """The quiet level: the median of band powers measured while the room was still."""
    vals = [h for h in history if h is not None]
    if not vals:
        return floor_min
    return max(float(np.percentile(vals, percentile)), floor_min)


def classify(db):
    for edge, name in CLASS_EDGES:
        if db >= edge:
            return name
    return "still"


class Gate:
    """On above one level, off below a lower one, and only after staying there for a minimum time."""

    def __init__(self, on_db, off_db, min_on, min_off):
        self.on_db, self.off_db, self.min_on, self.min_off = on_db, off_db, min_on, min_off
        self.state, self.held = False, 0.0

    def update(self, db, dt):
        if not self.state:
            self.held = self.held + dt if db >= self.on_db else 0.0
            if self.held >= self.min_on:
                self.state, self.held = True, 0.0
        else:
            self.held = self.held + dt if db <= self.off_db else 0.0
            if self.held >= self.min_off:
                self.state, self.held = False, 0.0
        return self.state


def fusion_db(window):
    """Multi-link motion from a T x K matrix of readings: how much of the variation is one shared wobble.

    The first singular component carries what all links share, which is what a body moving through the room does,
    while the rest is each link's own noise. Returns (energy of the first component, energy of the rest), per
    sample, so the caller can turn the ratio into dB against a floor."""
    m = np.asarray(window, dtype=float)
    if m.ndim != 2 or m.shape[0] < 16 or m.shape[1] < 1:
        return 0.0, 0.0
    m = m - m.mean(axis=0, keepdims=True)
    s = np.linalg.svd(m, compute_uv=False)
    total = float(np.sum(s ** 2))
    first = float(s[0] ** 2)
    return first / m.shape[0], (total - first) / m.shape[0]


class LinkSense:
    """One link's time series and its running motion score."""

    def __init__(self, name, native_rate=5.0, keep_s=120):
        self.name, self.native = name, native_rate
        self.times, self.values = [], []
        self.keep = keep_s
        self.powers = []                  # band powers measured while learning the room
        self.quiet = []                   # band powers measured while it was quiet, to follow slow drift
        self.floor, self.smooth = None, None
        self.db = 0.0
        hi = min(MOTION_BAND[1], 0.45 * native_rate)          # never ask for a band the source cannot see
        if native_rate < 2.0:
            # A slow source (one reading a second from monitor mode) only shows the slow wobble, so listen to
            # that part of the spectrum, over a longer window to get enough cycles of it.
            self.band, self.window = (0.03, max(0.2, hi)), 30.0
        else:
            self.band, self.window = (MOTION_BAND[0], max(MOTION_BAND[0] + 0.5, hi)), 6.0

    def add(self, t, value):
        if value is None:
            return
        self.times.append(t)
        self.values.append(value)
        cut = t - self.keep
        while self.times and self.times[0] < cut:
            self.times.pop(0)
            self.values.pop(0)

    def grid(self, seconds):
        # Remove spikes at the rate the readings really arrive, before resampling stretches them into ramps.
        clean = hampel(self.values) if len(self.values) >= 7 else self.values
        return resample(self.times, clean, span=seconds)

    def update(self, window_s=None):
        """Recompute the motion score in dB from the last few seconds. Returns it."""
        window_s = window_s or self.window
        g, v = self.grid(window_s)
        if len(v) < int(window_s * RATE * 0.8):
            return self.db
        power = band_power(v, RATE, *self.band)
        # Smooth the power a little before comparing it: one window of noisy readings jumps around by several dB.
        self.smooth = power if self.smooth is None else 0.6 * self.smooth + 0.4 * power
        if self.floor is None:
            self.powers = (self.powers + [self.smooth])[-1200:]
            self.db = to_db(self.smooth, noise_floor(self.powers))
        else:
            self.db = to_db(self.smooth, self.floor)
            if self.db < QUIET_DB:
                self.quiet = (self.quiet + [self.smooth])[-900:]
                if len(self.quiet) >= 30:
                    self.floor = noise_floor(self.quiet)
        return self.db

    def freeze(self):
        """Learning is over: fix the quiet level from what was measured, then only quiet readings may move it."""
        if self.powers:
            self.floor = noise_floor(self.powers)
            self.quiet = list(self.powers[-300:])

    def reliability(self, seconds=10.0, now=None):
        """0 to 1: how steadily readings are arriving compared with what the source should deliver."""
        if not self.times:
            return 0.0
        now = self.times[-1] if now is None else now
        recent = [t for t in self.times if t > now - seconds]
        expected = self.native * seconds
        return float(min(1.0, len(recent) / expected)) if expected else 0.0


class SharpChange:
    """Notices one shape in the motion score: a sharp burst that drops straight to stillness and stays still.

    That is all it is, a shape in a signal. Signal strength cannot tell what made it, so it is only ever written
    in the page's log as "a sharp change, then still", with no guess at a cause and no notification."""

    def __init__(self, burst_db=16.0, still_db=3.0, drop_s=4.0, still_s=25.0, cooldown_s=180.0):
        self.burst_db, self.still_db, self.drop_s, self.still_s, self.cooldown_s = burst_db, still_db, drop_s, still_s, cooldown_s
        self.peak_at = None          # when the last burst was highest
        self.still_since = None
        self.quiet_until = -1e9

    def update(self, db, now):
        """Returns 'sharp_then_still' once, when the pattern completes, else None."""
        if now < self.quiet_until:
            return None
        if db >= self.burst_db:
            self.peak_at, self.still_since = now, None
            return None
        if self.peak_at is None:
            return None
        if db > self.still_db:
            # still disturbed after the burst: it only counts if it settles quickly, so give up once the drop time passes
            if now - self.peak_at > self.drop_s:
                self.peak_at, self.still_since = None, None
            self.still_since = None
            return None
        if self.still_since is None:
            if now - self.peak_at > self.drop_s * 2:
                self.peak_at = None
                return None
            self.still_since = now
        if now - self.still_since >= self.still_s:
            self.peak_at, self.still_since = None, None
            self.quiet_until = now + self.cooldown_s
            return "sharp_then_still"
        return None
