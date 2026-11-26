"""
The SDR tab: a spectrum and waterfall with the frequency drawn over it, and rows of big keys below for
tuning, modes, seek and scan, listening, volume, squelch and Bias-T.

It drives the same engine as the ops console (ops_agent.sdr.SDR), so both share one backend. The dongle
has a single owner, so the engine only runs while this tab is on screen and is stopped as soon as you
leave it. Receive only: nothing here can transmit.

Everything above build_panel is pure (layout sums, wording, squelch and seek logic), so it can be tested
without a screen or a dongle.
"""
import numpy as np

# Kept here, not imported: ops_agent.sdr must only load after bootstrap.np is set (see build_panel).
# tests/test_upconverter.py checks this matches the engine.
UPCONVERTER_PRESETS = (("None", 0.0), ("Ham It Up (+125 MHz)", 125.0))

TICK_MS = 100
WATERFALL_ROWS = 180            # the most history kept; a short screen shows fewer (waterfall_rows)
WATERFALL_COLS = 600
MODES = ("WFM", "FM", "NFM", "AM", "USB", "LSB", "CW")


def format_freq(mhz):
    """'98.000 000' style readout: MHz with the kHz and Hz groups split, easy to read at a glance."""
    if mhz is None:
        return "--- . --- ---"
    hz = int(round(mhz * 1e6))
    return f"{hz // 1_000_000}.{(hz // 1000) % 1000:03d} {hz % 1000:03d}"


def x_to_mhz(fraction, center_mhz, sample_rate):
    """Frequency under a click, `fraction` (0 to 1) across the displayed window."""
    fraction = min(1.0, max(0.0, fraction))
    return round(center_mhz + (fraction - 0.5) * sample_rate / 1e6, 5)


def mhz_to_x(mhz, center_mhz, sample_rate):
    return (mhz - center_mhz) / (sample_rate / 1e6) + 0.5


def downsample(spectrum, width):
    """Average a long FFT down to `width` columns."""
    arr = np.asarray(spectrum, dtype=np.float32)
    if arr.size == 0 or width <= 0:
        return np.zeros(0, dtype=np.float32)
    if arr.size <= width:
        return arr
    edges = np.linspace(0, arr.size, width + 1).astype(int)
    return np.array([arr[a:b].mean() for a, b in zip(edges[:-1], edges[1:])], dtype=np.float32)


def _build_lut():
    stops = [(0.0, (8, 10, 40)), (0.35, (20, 80, 170)), (0.65, (30, 190, 190)),
             (0.85, (240, 220, 60)), (1.0, (255, 80, 40))]
    lut = np.zeros((256, 3), dtype=np.uint8)
    for i in range(256):
        f = i / 255
        for (a, ca), (b, cb) in zip(stops[:-1], stops[1:]):
            if a <= f <= b:
                k = (f - a) / (b - a)
                lut[i] = [int(ca[j] + (cb[j] - ca[j]) * k) for j in range(3)]
                break
    return lut


LUT = _build_lut()


def waterfall_row(spectrum, width, floor_db, ceil_db):
    """One waterfall line as (width, 3) uint8 colours."""
    cols = downsample(spectrum, width)
    span = max(1.0, ceil_db - floor_db)
    idx = np.clip(((cols - floor_db) / span) * 255, 0, 255).astype(np.uint8)
    return LUT[idx]


def display_range(spectrum, previous=None):
    """A steady floor and ceiling for the plot: noise floor minus a little, peak plus a little."""
    arr = np.asarray(spectrum, dtype=np.float32)
    if arr.size == 0:
        return previous or (0.0, 60.0)
    floor = float(np.percentile(arr, 10)) - 3
    ceil = max(float(arr.max()) + 3, floor + 20)
    if previous:
        floor = previous[0] * 0.9 + floor * 0.1
        ceil = previous[1] * 0.9 + ceil * 0.1
    return floor, ceil


def snr_fraction(snr_db, full_db=40.0):
    if snr_db is None:
        return 0.0
    return min(1.0, max(0.0, snr_db / full_db))


def fit(text, chars):
    """Cut text to a width in characters, ending in ~ when it was too long."""
    text = text or ""
    return text if len(text) <= chars else text[:max(1, chars - 1)] + "~"


# ---- colours: every painted colour comes from the theme, asked for at paint time

def contrast(a, b):
    """WCAG contrast ratio of two hex colours, 1 (the same) to 21 (black on white)."""
    from .colour import luminance
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def colours(mode):
    """The colours the spectrum is painted with on this theme. The waterfall keeps its own fixed colour map
    (it is data, dark at the quiet end on both themes), so the marker drawn over it is always the pale one."""
    from . import style
    from .pixel import blend
    m = "light" if mode == "light" else "dark"
    t = style.TOKENS[m]
    base = t["card"] if m == "light" else t["field"]
    return {"base": base, "grid": blend(base, t["text"], 0.14), "text": t["text"], "muted": style.MUTED[m],
            "trace": t["accent"], "peak": t["accent2"], "tuned": t["text"], "strip": base, "strip_edge": t["border"],
            "meter_on": style.good_colour(m), "meter_off": blend(base, t["text"], 0.18),
            "waterfall_marker": style.TOKENS["dark"]["text"]}


# ---- wording: every sentence the page can show

PLACEHOLDER = "Freq or CB: 98.8  cb 19"
ENTRY_TIP = "Type a frequency in MHz (98.8, 145.5, 27.185) or a CB channel (cb 19) and press Enter."
BIAS_WARNING = "Bias-T puts voltage on the antenna lead. Keep it off unless an amplified antenna or LNA needs it."
BIAS_TIP = ("Bias-T feeds a few volts up the antenna lead to power an amplified antenna, LNA or upconverter. "
            "It can damage an antenna that is a DC short. It is never switched on for you, and it is switched "
            "off when you leave this tab.")
SEEK_TIP = "Seek: move {way} to the next signal in this band and stop there."
SCAN_TIP = ("Scan: keep seeking through this band, waiting a few seconds on each signal. It goes the way of the last "
            "seek (up at first). Press again, or tune, to stop.")
SQUELCH_TIP = "Squelch mutes the speaker until a signal is stronger than the level. Higher is stricter."
VOLUME_TIP = "Audio volume of the decoded sound. The radio's own gain is automatic."
UPCONVERTER_TIP = ("An upconverter such as the Nooelec Ham It Up lets the dongle hear short wave. "
                   "Frequencies are then the real ones. If it is powered from the dongle, use Bias-T.")
STARTING = "Starting the SDR..."
NO_DONGLE = "No RTL-SDR found. Is the SDR rail on?"


def signal_text(snr_db):
    return "-- dB over noise" if snr_db is None else f"{snr_db:+.0f} dB over noise"


def status_line(modulation, if_bw_khz, listening=False, squelch_level=0, squelch_open=True, bias_on=False,
                upconverter_mhz=0.0, seeking=None, problem=None):
    """The small line under the frequency: what the radio is set to right now. A problem replaces it."""
    if problem:
        return problem
    parts = [modulation, f"IF {if_bw_khz} kHz"]
    if seeking:
        parts.append(seeking)
    parts.append("Listening" if listening else "Muted")
    if squelch_level > 0:
        # whether the squelch is open is only measured while the sound is being decoded
        parts.append(f"Sq {squelch_level} {'open' if squelch_open else 'shut'}" if listening else f"Sq {squelch_level}")
    else:
        parts.append("Sq off")
    if bias_on:
        parts.append("Bias-T ON")
    if upconverter_mhz:
        parts.append(f"Upconv +{upconverter_mhz:g} MHz")
    return " | ".join(parts)


def bias_result_text(wanted, ok, error=None):
    """What to say after asking the dongle to switch Bias-T."""
    if ok:
        return "Bias-T is ON: voltage on the antenna lead." if wanted else "Bias-T is off."
    reason = (error or "unknown error").strip().splitlines()[-1][:60] if (error or "").strip() else "unknown error"
    return f"Could not switch Bias-T {'on' if wanted else 'off'}: {reason}"


def upconverter_text(mhz):
    if not mhz:
        return "Upconverter off."
    return (f"Upconverter +{mhz:g} MHz. Tune the real frequency, for example 7.1 for 40m. "
            "If it is powered from the dongle, turn on Bias-T.")


# ---- squelch and volume: the engine only has a level (0 means off), so the page remembers the level
# while the switch is off

SQUELCH_MIN, SQUELCH_MAX, SQUELCH_STEP, SQUELCH_DEFAULT = 5, 60, 5, 30
VOLUME_LEVELS = (0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 49)      # dB, the same span the console uses


def squelch_engine_level(on, level):
    """What to store in the engine: the level when the switch is on, 0 (off) when it is not."""
    return clamp_squelch(level) if on else 0


def clamp_squelch(level):
    return min(SQUELCH_MAX, max(SQUELCH_MIN, int(level)))


def squelch_step(level, direction):
    return clamp_squelch(level + (SQUELCH_STEP if direction > 0 else -SQUELCH_STEP))


def squelch_from_engine(engine_level, remembered):
    """(switch on, level to show) from the engine's value, keeping the remembered level while it is off."""
    if engine_level and engine_level > 0:
        return True, clamp_squelch(engine_level)
    return False, clamp_squelch(remembered)


def volume_step(gain_db, direction):
    """The next volume up or down the ladder from wherever the engine is now."""
    if direction > 0:
        higher = [v for v in VOLUME_LEVELS if v > gain_db]
        return higher[0] if higher else VOLUME_LEVELS[-1]
    lower = [v for v in VOLUME_LEVELS if v < gain_db]
    return lower[-1] if lower else VOLUME_LEVELS[0]


# ---- measuring: the level at the tuned frequency and the next signal along, from the spectrum itself

SMOOTH_BINS = 9                 # about 10 kHz: one raw FFT is too noisy to call any single bin a signal
SIGNAL_DB = 10.0                # this far over the noise floor counts as a signal
EDGE = 0.05                     # the outer 5% of the window is the dongle's filter skirt, not trusted
MIN_RUN = 3


def smooth_db(spectrum):
    """The spectrum with power averaged over a few neighbouring bins, in dB. The three bins at the very centre
    are bridged first: a dongle leaves a small spike there that is not a station."""
    arr = np.asarray(spectrum, dtype=np.float64)
    if arr.size < SMOOTH_BINS * 2:
        return arr
    power = 10.0 ** (arr / 10.0)
    c = arr.size // 2
    power[c - 1:c + 2] = (power[c - 3:c - 1].mean() + power[c + 2:c + 4].mean()) / 2.0
    kernel = np.ones(SMOOTH_BINS) / SMOOTH_BINS
    padded = np.concatenate((np.full(SMOOTH_BINS // 2, power[0]), power, np.full(SMOOTH_BINS // 2, power[-1])))
    return 10.0 * np.log10(np.convolve(padded, kernel, mode="valid") + 1e-12)


def noise_floor(smoothed):
    arr = np.asarray(smoothed, dtype=np.float64)
    return float(np.percentile(arr, 20)) if arr.size else None


def signal_db(spectrum, center_mhz, sample_rate, mhz, if_bw_khz):
    """How far the power inside the receive filter at `mhz` stands over the noise floor, in dB. None when
    there is no spectrum or the frequency is outside the window that was measured."""
    n = len(spectrum) if spectrum is not None else 0
    if n < SMOOTH_BINS * 2 or center_mhz is None or mhz is None:
        return None
    bin_mhz = sample_rate / 1e6 / n
    i = int(round((mhz - center_mhz) / bin_mhz)) + n // 2
    half = max(1, int(if_bw_khz / 1000.0 / bin_mhz) // 2)
    lo, hi = max(0, i - half), min(n, i + half + 1)
    if i < 0 or i >= n or hi <= lo:
        return None
    sm = smooth_db(spectrum)
    power = 10.0 ** (sm[lo:hi] / 10.0)
    return float(10.0 * np.log10(power.mean() + 1e-12) - noise_floor(sm))


def next_signal(spectrum, center_mhz, sample_rate, direction, from_mhz=None, low=None, high=None):
    """The next signal in this window going `direction` (+1 up, -1 down) from `from_mhz`, or from the window's
    near edge when that is None. Returns (MHz at the middle of the signal, dB over noise) or None.

    A signal the search starts on is left first, so seeking from a station finds the next one, not the same
    one again. A signal that runs off the far edge is left for the next window, where all of it can be seen.
    """
    n = len(spectrum) if spectrum is not None else 0
    if n < SMOOTH_BINS * 4 or center_mhz is None:
        return None
    sm = smooth_db(spectrum)
    floor = noise_floor(sm)
    hot = sm > (floor + SIGNAL_DB)
    bin_mhz = sample_rate / 1e6 / n
    lo_i, hi_i = int(n * EDGE), n - 1 - int(n * EDGE)
    step = 1 if direction > 0 else -1
    far = hi_i if step > 0 else lo_i

    def freq(i):
        return center_mhz + (i - n // 2) * bin_mhz

    if from_mhz is None:
        i = lo_i if step > 0 else hi_i
    else:
        i = int(round((from_mhz - center_mhz) / bin_mhz)) + n // 2
        if (step > 0 and i > hi_i) or (step < 0 and i < lo_i):
            return None
        i = min(hi_i, max(lo_i, i))
        while lo_i <= i <= hi_i and hot[i]:
            i += step
    while lo_i <= i <= hi_i:
        if not hot[i]:
            i += step
            continue
        j = i
        while lo_i <= j + step <= hi_i and hot[j + step]:
            j += step
        mid = freq((i + j) / 2.0)
        inside = (low is None or mid >= low) and (high is None or mid <= high)
        if abs(j - i) + 1 >= MIN_RUN and j != far and inside:
            a, b = min(i, j), max(i, j)
            return round(mid, 5), float(sm[a:b + 1].max() - floor)
        i = j + step
    return None


def tidy_hit(mhz, modulation, channels=()):
    """A found signal's frequency put on the grid its band uses: the channel plan when the band has one,
    50 kHz for broadcast FM, otherwise the nearest kHz. The middle of a measured signal is only near right."""
    if channels:
        nearest = min(channels, key=lambda c: abs(c["freq_mhz"] - mhz))
        if abs(nearest["freq_mhz"] - mhz) <= 0.007:
            return nearest["freq_mhz"]
    if modulation == "WFM":
        return round(round(mhz / 0.05) * 0.05, 3)
    return round(mhz, 3)


def seek_band(freq_mhz, profile, limits):
    """(low, high) MHz a seek stays inside: the band you are in, or 10 MHz either side when you are in none."""
    if profile and profile["range_low_mhz"] <= freq_mhz <= profile["range_high_mhz"]:
        return profile["range_low_mhz"], profile["range_high_mhz"]
    return max(limits[0], freq_mhz - 10.0), min(limits[1], freq_mhz + 10.0)


def needs_retune(new_mhz, center_mhz, sample_rate):
    """The engine's own rule: the dongle is only moved when the new frequency is outside the middle 90%
    of what it already sees."""
    return center_mhz is None or abs((new_mhz - center_mhz) * 1e6) > (sample_rate / 2) * 0.90


class Seeker:
    """Seek and scan, done honestly with what the engine gives: look along the measured spectrum for the next
    signal, and when this window has none, move the dongle one window along and look again.

    `step` is called on the page's existing tick and returns None or (frequency to tune or None, message
    or None). A seek stops on the first signal. A scan waits DWELL seconds on it and carries on, until a
    whole pass of the band finds nothing or it is stopped. Receive only.
    """
    DWELL = 5.0                 # seconds a scan waits on each signal
    SETTLE = 0.8                # after moving the dongle, before its spectrum is believed
    FRESH = 0.4                 # two more spectra after the first one from the new place
    STALL = 8.0                 # give up when the radio stops answering

    def __init__(self):
        self.active = False
        self.scanning = False
        self.direction = 1
        self.reset()

    def reset(self):
        self.active = False
        self.scanning = False
        self.from_mhz = None
        self.expect_center = None
        self.wait_until = 0.0
        self.dwell_until = None
        self.deadline = 0.0
        self.windows_left = 0
        self.wrapped = False

    def begin(self, direction, freq_mhz, low, high, sample_rate, now, scanning=False, modulation="WFM",
              channels=()):
        self.reset()
        self.active = True
        self.scanning = bool(scanning)
        self.direction = 1 if direction > 0 else -1
        self.low, self.high = low, high
        self.sample_rate = sample_rate
        self.modulation = modulation
        self.channels = tuple(channels or ())
        self.from_mhz = freq_mhz
        self._new_pass(now)

    def _new_pass(self, now):
        step = 0.8 * self.sample_rate / 1e6
        self.windows_left = int((self.high - self.low) / step) + 2
        self.deadline = now + self.STALL

    def stop(self):
        self.reset()

    def describe(self):
        """A few words for the status line, or None when idle."""
        if not self.active:
            return None
        way = "up" if self.direction > 0 else "down"
        if self.scanning:
            return f"Scan {way}: holding" if self.dwell_until is not None else f"Scanning {way}"
        return f"Seeking {way}"

    def step(self, now, spectrum, spec_center, busy):
        if not self.active:
            return None
        if self.dwell_until is not None:
            if now < self.dwell_until:
                return None
            self.dwell_until = None
            self._new_pass(now)
        if now > self.deadline:
            self.reset()
            return None, "Stopped: no fresh spectrum from the radio."
        if busy or now < self.wait_until or not len(spectrum or ()) or spec_center is None:
            return None
        if self.expect_center is not None:
            if abs(spec_center - self.expect_center) > 1e-4:
                return None                                 # still the picture from the old place
            self.expect_center = None
            self.wait_until = now + self.FRESH
            return None
        found = next_signal(spectrum, spec_center, self.sample_rate, self.direction, self.from_mhz,
                            self.low, self.high)
        if found:
            mhz = tidy_hit(found[0], self.modulation, self.channels)
            words = f"Signal at {mhz:.3f} MHz, {signal_text(found[1])}"
            if not self.scanning:
                self.reset()
                return mhz, words
            self.from_mhz = found[0]
            self.dwell_until = now + self.DWELL
            return mhz, f"{words}. Scan holds here {self.DWELL:.0f} s"
        return self._move_on(now, spec_center)

    def _move_on(self, now, spec_center):
        """Nothing in this window: go one window along, or round to the other end of the band."""
        self.windows_left -= 1
        if self.windows_left <= 0:
            what = "Scan" if self.scanning else "Seek"
            self.reset()
            return None, f"{what} found nothing in this band."
        span = self.sample_rate / 1e6
        usable = span * (0.5 - EDGE)
        if self.direction > 0:
            at_end = spec_center + usable >= self.high
            new = self.low + 0.4 * span if at_end else spec_center + 0.8 * span
            edge = self.low
        else:
            at_end = spec_center - usable <= self.low
            new = self.high - 0.4 * span if at_end else spec_center - 0.8 * span
            edge = self.high
        new = round(new, 5)
        self.deadline = now + self.STALL
        if not needs_retune(new, spec_center, self.sample_rate):
            if abs(edge - spec_center) <= usable:
                # The band's other end is already on screen (a narrow band such as CB): look again from
                # there without moving the dongle.
                self.from_mhz = edge
                return None
            # Too near for the engine to move the dongle, too far to see: step back half a window, which
            # it will do, and that brings the band's other end into view.
            new = round(spec_center - self.direction * 0.5 * span, 5)
        self.from_mhz = edge if at_end else None
        self.expect_center = new
        self.wait_until = now + self.SETTLE
        return new, None


def start_seek(seeker, engine, direction, now, scanning=False):
    """Begin a seek or scan from where the engine is tuned, inside the band it is in."""
    freq = engine.pending_freq if engine.pending_freq is not None else engine.freq_mhz
    low, high = seek_band(freq, engine.active_band_profile(), engine.tune_limits())
    profile = engine.active_band_profile()
    in_band = bool(profile) and profile["range_low_mhz"] <= freq <= profile["range_high_mhz"]
    seeker.begin(direction, freq, low, high, engine.sample_rate, now, scanning=scanning,
                 modulation=engine.modulation, channels=engine.channel_plan(profile) if in_band else ())


def run_seek(seeker, engine, now):
    """One tick of an active seek. Tunes the engine when the seeker says so; returns a message or None."""
    if not seeker.active:
        return None
    busy = bool(engine.changing_freq) or engine.pending_freq is not None
    action = seeker.step(now, engine.spectrum, getattr(engine, "spectrum_center_mhz", None), busy)
    if not action:
        return None
    mhz, message = action
    if mhz is not None:
        engine.request_tune(mhz, recenter=True)
    engine.dirty = True
    return message


def set_squelch(engine, on, level):
    engine.squelch_level = squelch_engine_level(on, level)
    engine.dirty = True


def close_engine(engine):
    """Give the dongle back. If Bias-T was switched on here it is switched off first, so no voltage is left
    on the antenna lead by a tab nobody is looking at."""
    try:
        engine.stop_audio()
        engine.stop_scan()
        if getattr(engine, "bias_t_enabled", False):
            engine.set_bias_t(False)
    except Exception:
        pass
    try:
        engine.shutdown()
    except Exception:
        pass


# ---- layout: real pixels from the size of the page, so nothing depends on how Qt feels that day

SWITCH_W, SWITCH_H = 76, 36     # ops_gui/slideswitch.py; a test checks these still match
COLS = 12
MARGIN = GAP = 8
HINT_H = 20


def key_height(height):
    """Touch sized keys: 48 px, or 40 px on a short window so the spectrum keeps some room."""
    return 48 if height >= 520 else 40


def layout(width, height):
    """Rectangles (x, y, w, h) for the spectrum and every control. Twelve equal columns; three rows of keys
    and one line of small print fill the bottom, the spectrum and waterfall take everything above."""
    m, g, kh = MARGIN, GAP, key_height(height)
    inner = width - 2 * m

    def span(i, n=1):
        x0 = m + (i * (inner + g)) // COLS
        return x0, m + ((i + n) * (inner + g)) // COLS - g - x0

    hint_y = height - m - HINT_H
    y3 = hint_y - g - kh
    y2 = y3 - g - kh
    y1 = y2 - g - kh

    def key(y, i, n=1):
        x, w = span(i, n)
        return (x, y, w, kh)

    def switched(y, i, n=2):
        x, _y, w, h = key(y, i, n)
        return {"label": (x, y, w - SWITCH_W - 4, h),
                "switch": (x + w - SWITCH_W, y + (h - SWITCH_H) // 2, SWITCH_W, SWITCH_H)}

    return {
        "key_h": kh,
        "view": (m, m, inner, max(60, y1 - g - m)),
        "entry": key(y1, 0, 3), "band": key(y1, 3, 2), "cb": key(y1, 5, 2), "preset": key(y1, 7, 2),
        "upconv": key(y1, 9, 3),
        "modes": [key(y2, i) for i in range(7)],
        "steps": [key(y2, 7 + i) for i in range(4)],
        "scan": key(y2, 11),
        "listen": switched(y3, 0),
        "vol_down": key(y3, 2), "vol_value": key(y3, 3), "vol_up": key(y3, 4),
        "squelch": switched(y3, 5),
        "sq_down": key(y3, 7), "sq_value": key(y3, 8), "sq_up": key(y3, 9),
        "bias": switched(y3, 10),
        "hint": (m, hint_y, inner, HINT_H),
    }


def all_rects(lay):
    """Every rectangle in a layout, flattened, for fit and overlap checks."""
    out = []
    for value in lay.values():
        if isinstance(value, tuple):
            out.append(value)
        elif isinstance(value, dict):
            out.extend(value.values())
        elif isinstance(value, list):
            out.extend(value)
    return out


def caption(options, pixels, char_px=8):
    """The longest wording that fits: options run from the full word to its shortest form."""
    for text in options:
        if len(text) * char_px <= pixels:
            return text
    return options[-1]


CAPTIONS = {"listen": ("Listen",), "squelch": ("Squelch", "Sql"), "bias": ("Bias-T",)}
STEP_KEYS = (("<<", -1, "seek"), ("<", -1, "nudge"), (">", 1, "nudge"), (">>", 1, "seek"))


def waterfall_rows(height):
    """How many history lines a waterfall this tall shows: about 4 px each, so the picture stays chunky."""
    return min(WATERFALL_ROWS, max(20, height // 4))


def rows_to_draw(filled, height):
    """Lines of real history to stretch over the waterfall. Until enough has arrived the few lines there are
    fill the whole area, so there is never an empty band under it."""
    return min(filled, waterfall_rows(height))


def view_layout(width, height, freq_chars=12, status_chars=0):
    """Where everything inside the spectrum widget goes: the trace on top, a thin line of frequency labels,
    the waterfall filling the rest, and the readout drawn over the top left of the trace on a backing strip
    with the signal meter top right."""
    axis_h = 20
    plot_h = max(70, int(height * 0.45))
    big = 64 if (plot_h >= 150 and width >= 900) else 32
    pad = 8
    meter_w = 216 if width >= 640 else 0
    freq_w = freq_chars * (big // 2)
    unit_w = 4 * 8                                             # " MHz" in the small font
    room = width - 12 - (meter_w + 8 if meter_w else 0)        # the widest the strip may be
    strip_w = min(room, max(freq_w + unit_w, status_chars * 8) + 2 * pad)
    strip = (6, 6, strip_w, big + 16 + 2 * pad)
    out = {
        "plot_h": plot_h, "axis": (0, plot_h, width, axis_h),
        "waterfall": (0, plot_h + axis_h, width, max(0, height - plot_h - axis_h)),
        "big": big, "strip": strip,
        "freq": (6 + pad, 6 + pad, freq_w, big),
        "unit": (6 + pad + freq_w, 6 + pad + big - 16, unit_w, 16),
        "status": (6 + pad, 6 + pad + big, strip_w - 2 * pad, 16),
        "status_chars": max(1, (strip_w - 2 * pad) // 8),
        "meter": None, "meter_text": None, "meter_bar": None,
    }
    if meter_w:
        mx = width - 6 - meter_w
        out["meter"] = (mx, 6, meter_w, 16 + 12 + 2 * pad + 4)
        out["meter_text"] = (mx + pad, 6 + pad, meter_w - 2 * pad, 16)
        out["meter_bar"] = (mx + pad, 6 + pad + 16 + 4, meter_w - 2 * pad, 12)
    return out


METER_SEGMENTS = 20


def meter_segments(fraction, segments=METER_SEGMENTS):
    return int(round(min(1.0, max(0.0, fraction)) * segments))


def build_panel(win, Switch):
    """Return (widget, activate, deactivate). Qt is imported here so the helpers load without it."""
    import threading
    import time
    from PyQt6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, pyqtSignal, QObject
    from PyQt6.QtGui import QColor, QImage, QPainter, QPalette, QPen, QPolygonF
    from PyQt6.QtWidgets import QComboBox, QLabel, QLineEdit, QListView, QPushButton, QWidget
    from . import style

    def theme():
        return "light" if getattr(win, "theme", "dark") == "light" else "dark"

    LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

    class Signals(QObject):
        ready = pyqtSignal(object)
        bias = pyqtSignal(object)

    class Entry(QLineEdit):
        """The frequency box. The app hands every widget a plain palette when the theme changes, which leaves
        the hint text dark grey on the dark field, so the hint colour is put back each time."""

        def changeEvent(self, e):
            super().changeEvent(e)
            if e.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange):
                self.fix_hint()

        def fix_hint(self):
            want = QColor(style.MUTED[theme()])
            pal = self.palette()
            if pal.color(QPalette.ColorRole.PlaceholderText) != want:
                pal.setColor(QPalette.ColorRole.PlaceholderText, want)
                self.setPalette(pal)

    class SpectrumView(QWidget):
        def __init__(self, parent):
            super().__init__(parent)
            self.setMouseTracking(True)
            self.spectrum = []
            self.center = 98.0
            self.sample_rate = 2_400_000
            self.tuned = 98.0
            self.peaks = []
            self.range = (0.0, 60.0)
            self.rows = np.zeros((WATERFALL_ROWS, WATERFALL_COLS, 3), dtype=np.uint8)
            self.filled = 0
            self.on_click = None
            self.hover = None
            self.freq_text = format_freq(None)
            self.status = ""
            self.message = ""           # shown in the middle while there is no radio to draw
            self.snr = None

        def clear(self):
            self.spectrum = []
            self.peaks = []
            self.filled = 0
            self.snr = None
            self.freq_text = format_freq(None)
            self.update()

        def push(self, sdr):
            if not sdr.spectrum:
                return
            self.spectrum = sdr.spectrum
            self.center = getattr(sdr, "spectrum_center_mhz", None) or sdr.freq_mhz
            self.sample_rate = sdr.sample_rate
            self.tuned = sdr.freq_mhz
            self.peaks = list(getattr(sdr, "peaks", []) or [])
            self.range = display_range(self.spectrum, self.range)
            row = waterfall_row(self.spectrum, self.rows.shape[1], *self.range)
            self.rows = np.roll(self.rows, 1, axis=0)
            self.rows[0] = row
            self.filled = min(WATERFALL_ROWS, self.filled + 1)
            self.update()

        def mousePressEvent(self, e):
            if self.on_click and self.width():
                self.on_click(x_to_mhz(e.position().x() / self.width(), self.center, self.sample_rate))

        def mouseMoveEvent(self, e):
            self.hover = e.position().x() / max(1, self.width())
            self.update()

        def leaveEvent(self, _):
            self.hover = None
            self.update()

        def paintEvent(self, _):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            w, h = self.width(), self.height()
            col = colours(theme())                  # asked for now, so a theme change shows on the next paint
            lay = view_layout(w, h, max(10, len(self.freq_text)), len(self.status))
            plot_h = lay["plot_h"]
            text = QColor(col["text"])
            small = style.pixel_font(16)
            p.setFont(small)
            p.fillRect(0, 0, w, h, QColor(col["base"]))
            p.setPen(QPen(QColor(col["grid"]), 1))
            for i in range(1, 8):
                p.drawLine(int(w * i / 8), 0, int(w * i / 8), plot_h)
            for i in range(1, 4):
                p.drawLine(0, int(plot_h * i / 4), w, int(plot_h * i / 4))
            tx = None
            if self.spectrum:
                cols = downsample(self.spectrum, max(2, w // 2))
                lo, hi = self.range
                span = max(1.0, hi - lo)
                pts = [QPointF(i * w / (len(cols) - 1), plot_h - 2 - ((v - lo) / span) * (plot_h - 6))
                       for i, v in enumerate(cols)]
                trace = QColor(col["trace"])
                fill = QColor(trace)
                fill.setAlpha(60)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(fill)
                p.drawPolygon(QPolygonF(pts + [QPointF(w, plot_h), QPointF(0, plot_h)]))
                p.setPen(QPen(trace, 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPolyline(QPolygonF(pts))
                n = len(self.spectrum)
                p.setPen(QPen(QColor(col["peak"]), 2))
                for b in self.peaks[:12]:               # ticks along the bottom, clear of the readout
                    x = int(b / n * w)
                    p.drawLine(x, plot_h - 8, x, plot_h)
                tx = int(mhz_to_x(self.tuned, self.center, self.sample_rate) * w)
                p.setPen(QPen(QColor(col["tuned"]), 2))
                p.drawLine(tx, 0, tx, plot_h)
            elif self.message:
                p.setPen(text)
                p.drawText(QRectF(12, lay["strip"][1] + lay["strip"][3], w - 24, max(20, plot_h - lay["strip"][3] - 6)),
                           Qt.AlignmentFlag.AlignCenter.value | Qt.TextFlag.TextWordWrap.value, self.message)

            p.setPen(QColor(col["muted"]))
            half = self.sample_rate / 2e6
            ax = lay["axis"]
            for frac, mhz in ((0.0, self.center - half), (0.5, self.center), (1.0, self.center + half)):
                label = f"{mhz:.3f}"
                x = max(2, min(w - len(label) * 8 - 2, int(frac * w) - len(label) * 4))
                p.drawText(QRectF(x, ax[1], len(label) * 8 + 2, ax[3]), LEFT, label)

            wx, wy, ww, wh = lay["waterfall"]
            n = rows_to_draw(self.filled, wh)
            if n and wh > 0:
                shown = np.ascontiguousarray(self.rows[:n])
                img = QImage(shown.data, shown.shape[1], n, shown.shape[1] * 3, QImage.Format.Format_RGB888)
                p.drawImage(QRectF(wx, wy, ww, wh), img)
                if tx is not None:
                    p.setPen(QPen(QColor(col["waterfall_marker"]), 2))
                    p.drawLine(tx, wy, tx, h)

            if self.hover is not None:
                hx = int(self.hover * w)
                p.setPen(QPen(QColor(col["muted"]), 1, Qt.PenStyle.DashLine))
                p.drawLine(hx, 0, hx, plot_h)
                label = f"{x_to_mhz(self.hover, self.center, self.sample_rate):.4f} MHz"
                lw = len(label) * 8 + 8
                lx = min(max(2, hx + 6), w - lw - 2)
                back = QColor(col["strip"])
                back.setAlpha(220)
                p.fillRect(QRectF(lx, plot_h - 30, lw, 20), back)
                p.setPen(text)
                p.drawText(QRectF(lx, plot_h - 30, lw, 20), Qt.AlignmentFlag.AlignCenter, label)

            # The readout sits over the trace on a see-through strip of the page colour, so it reads on either theme.
            back = QColor(col["strip"])
            back.setAlpha(170)
            edge = QPen(QColor(col["strip_edge"]), 1)
            p.setPen(edge)
            p.setBrush(back)
            p.drawRect(QRectF(*lay["strip"]))
            p.setPen(text)
            p.setFont(style.pixel_font(lay["big"]))
            p.drawText(QRectF(*lay["freq"]), LEFT,
                       self.freq_text)
            p.setFont(small)
            p.setPen(QColor(col["muted"]))
            fx = lay["freq"][0]
            p.drawText(QRectF(fx + len(self.freq_text) * (lay["big"] // 2), lay["unit"][1], lay["unit"][2], 16),
                       LEFT, " MHz")
            p.setPen(text)
            p.drawText(QRectF(*lay["status"]), LEFT,
                       fit(self.status, lay["status_chars"]))
            if lay["meter"]:
                p.setPen(edge)
                p.setBrush(back)
                p.drawRect(QRectF(*lay["meter"]))
                p.setPen(text)
                p.drawText(QRectF(*lay["meter_text"]), LEFT,
                           signal_text(self.snr))
                bx, by, bw, bh = lay["meter_bar"]
                lit = meter_segments(snr_fraction(self.snr))
                seg = bw / METER_SEGMENTS
                for i in range(METER_SEGMENTS):
                    p.fillRect(QRectF(bx + i * seg, by, max(2.0, seg - 2), bh),
                               QColor(col["meter_on"] if i < lit else col["meter_off"]))
            p.end()

    PIXEL_16 = 'font-family: "Spleen 8x16"; font-size: 16px;'
    PIXEL_24 = 'font-family: "Spleen 12x24"; font-size: 24px;'

    class Panel(QWidget):
        def __init__(self):
            super().__init__()
            self.setMinimumSize(480, 300)
            self.sdr = None
            self.starting = False
            self.bias_busy = False
            self.shown = {}
            self.seeker = Seeker()
            self.sq_level = SQUELCH_DEFAULT
            self.note_text = ""
            self.note_until = 0.0
            self.signals = Signals()
            self.signals.ready.connect(self.started)
            self.signals.bias.connect(self.bias_done)
            self.timer = QTimer(self)               # the one timer the page had already; nothing faster is added
            self.timer.timeout.connect(self.tick)

            self.view = SpectrumView(self)
            self.view.on_click = self.click_tune

            self.entry = Entry(self)
            self.entry.setPlaceholderText(PLACEHOLDER)
            self.entry.setToolTip(ENTRY_TIP)
            # #9e9e9e is the helper grey the app swaps for a darker one on the light theme (style.muted_sheet)
            self.entry.setStyleSheet("placeholder-text-color: #9e9e9e;")
            self.entry.returnPressed.connect(self.enter_freq)
            self.entry.fix_hint()
            self.band = self.combo("Band")
            self.cb = self.combo("CB channel")
            self.preset = self.combo("FM station")
            self.upconv = self.combo(None)
            for name, _mhz in UPCONVERTER_PRESETS:
                self.upconv.addItem(name)
            self.upconv.addItem("Custom offset...")
            self.upconv.setToolTip(UPCONVERTER_TIP)
            self.upconv.activated.connect(self.pick_upconverter)
            self.band.activated.connect(self.pick_band)
            self.cb.activated.connect(self.pick_cb)
            self.preset.activated.connect(self.pick_preset)

            self.mode_buttons = {}
            for m in MODES:
                b = self.button(m, lambda _=False, m=m: self.set_mode(m))
                b.setCheckable(True)
                self.mode_buttons[m] = b
            self.step_buttons = []
            for text, direction, kind in STEP_KEYS:
                fn = self.seek if kind == "seek" else self.nudge
                b = self.button(text, lambda _=False, fn=fn, d=direction: fn(d))
                b.setToolTip(SEEK_TIP.format(way="up" if direction > 0 else "down") if kind == "seek"
                             else "Step one channel " + ("up." if direction > 0 else "down."))
                self.step_buttons.append(b)
            self.scan_btn = self.button("SCAN", lambda _=False: self.toggle_scan())
            self.scan_btn.setCheckable(True)
            self.scan_btn.setToolTip(SCAN_TIP)

            self.listen_label = self.label("Listen")
            self.listen = Switch()
            self.listen.setParent(self)
            self.listen.clicked.connect(self.toggle_listen)
            self.vol_down = self.button("VOL -", lambda _=False: self.volume(-1))
            self.vol_up = self.button("VOL +", lambda _=False: self.volume(1))
            self.vol_value = self.label("-- dB", PIXEL_24, centre=True)
            for wdg in (self.vol_down, self.vol_up, self.vol_value):
                wdg.setToolTip(VOLUME_TIP)

            self.sq_label = self.label("Squelch")
            self.sq_switch = Switch()
            self.sq_switch.setParent(self)
            self.sq_switch.clicked.connect(self.toggle_squelch)
            self.sq_down = self.button("SQ -", lambda _=False: self.squelch(-1))
            self.sq_up = self.button("SQ +", lambda _=False: self.squelch(1))
            self.sq_value = self.label(str(self.sq_level), PIXEL_24, centre=True)
            for wdg in (self.sq_label, self.sq_switch, self.sq_down, self.sq_up, self.sq_value):
                wdg.setToolTip(SQUELCH_TIP)

            self.bias_label = self.label("Bias-T")
            self.bias = Switch()
            self.bias.setParent(self)
            self.bias.clicked.connect(self.toggle_bias)
            self.bias.setToolTip(BIAS_TIP)
            self.bias_label.setToolTip(BIAS_TIP)
            self.hint = self.label(BIAS_WARNING, PIXEL_16 + " color: #9e9e9e;")

            self.view.message = "The SDR starts when this tab is open."
            self.set_enabled(False)

        # ---- small builders
        def button(self, text, slot):
            b = QPushButton(text, self)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(slot)
            return b

        def label(self, text, sheet=PIXEL_16, centre=False):
            lab = QLabel(text, self)
            lab.setStyleSheet(sheet)
            if centre:
                lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            return lab

        def combo(self, first):
            c = QComboBox(self)
            c.setView(QListView())                  # a plain list, so the rows below can be made finger sized
            c.setStyleSheet("QComboBox QAbstractItemView::item { min-height: 36px; }")
            c.setMaxVisibleItems(8)
            if first:
                c.addItem(first)
            return c

        def resizeEvent(self, _):
            lay = layout(self.width(), self.height())
            self.view.setGeometry(*lay["view"])
            for name in ("entry", "band", "cb", "preset", "upconv", "vol_down", "vol_value", "vol_up",
                         "sq_down", "sq_value", "sq_up", "hint"):
                getattr(self, name).setGeometry(*lay[name])
            self.scan_btn.setGeometry(*lay["scan"])
            for b, rect in zip(self.mode_buttons.values(), lay["modes"]):
                b.setGeometry(*rect)
            for b, rect in zip(self.step_buttons, lay["steps"]):
                b.setGeometry(*rect)
            for key, lab, switch in (("listen", self.listen_label, self.listen),
                                     ("squelch", self.sq_label, self.sq_switch),
                                     ("bias", self.bias_label, self.bias)):
                lab.setGeometry(*lay[key]["label"])
                lab.setText(caption(CAPTIONS[key], lay[key]["label"][2]))
                switch.move(lay[key]["switch"][0], lay[key]["switch"][1])
            self.hint.setText(fit(BIAS_WARNING, max(1, lay["hint"][2] // 8)))

        # ---- lifecycle
        def controls(self):
            return (self.entry, self.band, self.cb, self.preset, self.upconv, self.scan_btn, self.listen,
                    self.vol_down, self.vol_up, self.sq_switch, self.sq_down, self.sq_up, self.bias,
                    *self.mode_buttons.values(), *self.step_buttons)

        def show_switch(self, switch, on):
            """Move a switch to the real state, but only when it is not there already: moving it restarts its
            little animation, and doing that five times a second would repaint for nothing."""
            on = bool(on)
            if self.shown.get(switch) != on or switch.isChecked() != on:
                self.shown[switch] = on
                switch.show_state(on)

        def set_enabled(self, on):
            for w in self.controls():
                w.setEnabled(on)

        def say(self, text, seconds=5.0):
            """A passing message on the line under the frequency."""
            self.note_text = text or ""
            self.note_until = time.monotonic() + seconds
            self.view.status = self.note_text
            self.view.update()

        def activate(self):
            if self.sdr or self.starting or self.bias_busy:
                return
            self.starting = True
            self.view.message = STARTING
            self.view.update()

            def work():
                try:
                    # The console fills bootstrap.np through its interactive dependency check, which
                    # can prompt and exit. The app only needs numpy itself, so hand it over directly
                    # before the engine module is imported.
                    import numpy
                    from ops_agent import bootstrap
                    bootstrap.np = numpy
                    from ops_agent.sdr import SDR
                    engine = SDR()
                except Exception as e:      # missing numpy, no tools, anything else
                    engine = e
                self.signals.ready.emit(engine)

            threading.Thread(target=work, daemon=True).start()

        def started(self, engine):
            self.starting = False
            if isinstance(engine, Exception):
                self.view.message = f"SDR engine unavailable: {engine}"
                self.view.update()
                return
            if not engine.active:
                self.view.message = engine.status_reason or NO_DONGLE
                self.view.update()
                return
            self.sdr = engine
            self.view.message = ""
            self.fill_bands(engine)
            self.sync_upconverter(engine)
            for n in range(1, 41):
                self.cb.addItem(f"CB {n}")
            for name, mhz in engine.FM_PRESETS:
                self.preset.addItem(f"{name}  {mhz}")
            start = engine.startup_frequency()
            engine.apply_startup_profile(start)
            engine.start_scan(start, bandwidth=engine.if_bw_khz / 1000.0, recenter=True)
            engine.finish_startup_tune()
            engine.prime_spectrum()
            self.set_enabled(True)
            self.refresh_labels()
            self.timer.start(TICK_MS)

        def deactivate(self):
            """Give the dongle back. Safe to call at any time."""
            self.timer.stop()
            engine, self.sdr = self.sdr, None
            self.seeker.stop()
            self.note_text = ""
            self.set_enabled(False)
            for switch in (self.listen, self.sq_switch, self.bias):
                self.show_switch(switch, False)
            self.scan_btn.setChecked(False)
            for combo, first in ((self.band, "Band"), (self.cb, "CB channel"), (self.preset, "FM station")):
                combo.clear()
                combo.addItem(first)
            self.view.status = ""
            self.view.message = "The SDR starts when this tab is open."
            self.view.clear()
            # While a Bias-T change is still running in its thread the engine is not ours to close: bias_done
            # closes it when the change has finished.
            if engine and not self.bias_busy:
                close_engine(engine)

        # ---- running
        def tick(self):
            e = self.sdr
            if not e:
                return
            e.apply_pending_tune()
            e.read_panadapter()
            now = time.monotonic()
            message = run_seek(self.seeker, e, now)
            if message:
                self.say(message)
            if self.note_text and now >= self.note_until:
                self.note_text = ""
                e.dirty = True
            if e.dirty:
                e.dirty = False
                self.view.push(e)
                self.refresh_labels()

        def refresh_labels(self):
            e = self.sdr
            if not e:
                return
            shown = e.pending_freq if e.pending_freq is not None else e.freq_mhz
            problem = (e.last_error if e.link_state != "ok" and e.last_error else None) or e.audio_error
            self.view.freq_text = format_freq(shown)
            self.view.status = self.note_text or status_line(
                e.modulation, e.if_bw_khz, listening=bool(e.audio_enabled), squelch_level=e.squelch_level,
                squelch_open=bool(e.squelch_open), bias_on=bool(e.bias_t_enabled),
                upconverter_mhz=e.upconverter_mhz, seeking=self.seeker.describe(), problem=problem)
            self.view.snr = signal_db(e.spectrum, getattr(e, "spectrum_center_mhz", None), e.sample_rate,
                                      e.freq_mhz, e.if_bw_khz)
            self.view.update()
            for m, b in self.mode_buttons.items():
                b.setChecked(m == e.modulation)
            self.scan_btn.setChecked(self.seeker.active and self.seeker.scanning)
            self.show_switch(self.listen, e.audio_enabled)
            on, self.sq_level = squelch_from_engine(e.squelch_level, self.sq_level)
            self.show_switch(self.sq_switch, on)
            self.sq_value.setText(str(self.sq_level))
            self.vol_value.setText(f"{e.audio_gain} dB")
            self.show_switch(self.bias, e.bias_t_enabled)

        # ---- actions
        def halt(self):
            """Any tuning by hand ends a seek or scan."""
            self.seeker.stop()

        def click_tune(self, mhz):
            if self.sdr:
                self.halt()
                self.sdr.request_tune(mhz, recenter=False)

        def enter_freq(self):
            if not self.sdr:
                return
            text = self.entry.text().strip()
            if text:
                self.halt()
                tuned = self.sdr.tune_to_frequency(text)
                self.say(f"Tuned to {tuned:.5f} MHz" if tuned else f"Could not tune to '{text[:20]}'")
                self.entry.clear()

        def set_mode(self, mode):
            e = self.sdr
            if e:
                e.band_profile_name = None
                e.set_modulation(mode)
                e.bandwidth_mhz = e.if_bw_khz / 1000.0
                e.dirty = True

        def nudge(self, direction):
            e = self.sdr
            if e:
                self.halt()
                now = e.pending_freq if e.pending_freq is not None else e.freq_mhz
                e.request_tune(now + direction * e.fine_tune_step_mhz(), recenter=True)

        def seek(self, direction):
            e = self.sdr
            if e:
                start_seek(self.seeker, e, direction, time.monotonic())
                self.say(f"Seeking {'up' if direction > 0 else 'down'}... (any tuning stops it)", 3.0)
                e.dirty = True

        def toggle_scan(self):
            e = self.sdr
            if not e:
                return
            if self.seeker.active and self.seeker.scanning:
                self.halt()
                self.say("Scan stopped.", 3.0)
            else:
                start_seek(self.seeker, e, self.seeker.direction, time.monotonic(), scanning=True)
                self.say(f"Scanning {'up' if self.seeker.direction > 0 else 'down'}: press SCAN again to stop.", 3.0)
            e.dirty = True

        def toggle_listen(self):
            e = self.sdr
            if not e:
                return
            if e.audio_enabled:
                e.stop_audio()
            else:
                e.start_audio()
            e.dirty = True

        def volume(self, direction):
            e = self.sdr
            if e:
                e.audio_gain = volume_step(e.audio_gain, direction)
                e.dirty = True

        def toggle_squelch(self):
            e = self.sdr
            if e:
                set_squelch(e, e.squelch_level <= 0, self.sq_level)

        def squelch(self, direction):
            e = self.sdr
            if e:
                self.sq_level = squelch_step(self.sq_level, direction)
                self.sq_value.setText(str(self.sq_level))
                if e.squelch_level > 0:             # the level only reaches the radio while the switch is on
                    set_squelch(e, True, self.sq_level)

        def toggle_bias(self):
            """Only ever from a press of the switch. The dongle tool can take a few seconds and pauses the
            spectrum, so it runs in a thread with the page's timer and controls held until it answers."""
            e = self.sdr
            if not e or self.bias_busy:
                return
            wanted = not e.bias_t_enabled
            self.show_switch(self.bias, e.bias_t_enabled)     # the switch shows the real state, not the wish
            self.halt()
            self.bias_busy = True
            self.timer.stop()
            self.set_enabled(False)
            self.say(f"Switching Bias-T {'on' if wanted else 'off'}...", 30.0)

            def work():
                try:
                    ok = bool(e.set_bias_t(wanted))
                except Exception as exc:
                    e.last_bias_t_error = str(exc)
                    ok = False
                self.signals.bias.emit((e, wanted, ok))

            threading.Thread(target=work, daemon=True).start()

        def bias_done(self, result):
            engine, wanted, ok = result
            self.bias_busy = False
            if self.sdr is not engine:              # the tab was left meanwhile: finish giving the dongle back
                close_engine(engine)
                if self.isVisible():
                    self.activate()
                return
            self.set_enabled(True)
            self.say(bias_result_text(wanted, ok, engine.last_bias_t_error), 8.0)
            self.refresh_labels()
            self.timer.start(TICK_MS)

        def pick_band(self, i):
            if self.sdr and i > 0:
                self.halt()
                profile = self.sdr.band_profiles()[i - 1]
                self.sdr.apply_band_profile(profile)
                self.sdr.request_tune(profile["freq_mhz"], recenter=True)
                self.band.setCurrentIndex(0)

        def fill_bands(self, engine):
            self.band.clear()
            self.band.addItem("Band")
            for entry in engine.band_profiles():
                self.band.addItem(entry["name"])

        def sync_upconverter(self, engine):
            """Show the engine's offset in the combo without triggering a change."""
            known = [m for _n, m in UPCONVERTER_PRESETS]
            idx = known.index(engine.upconverter_mhz) if engine.upconverter_mhz in known else len(known)
            self.upconv.blockSignals(True)
            self.upconv.setCurrentIndex(idx)
            self.upconv.blockSignals(False)

        def pick_upconverter(self, i):
            e = self.sdr
            if not e:
                return
            if i < len(UPCONVERTER_PRESETS):
                mhz = UPCONVERTER_PRESETS[i][1]
            else:
                from PyQt6.QtWidgets import QInputDialog
                mhz, ok = QInputDialog.getDouble(self, "Upconverter offset", "Offset in MHz (0 for none):",
                                                 e.upconverter_mhz or 125.0, 0.0, 1000.0, 3)
                if not ok:
                    self.sync_upconverter(e)
                    return
            self.halt()
            if e.set_upconverter(mhz):
                self.fill_bands(e)
                self.say(upconverter_text(mhz), 8.0)
            self.sync_upconverter(e)

        def pick_cb(self, i):
            if self.sdr and i > 0:
                self.halt()
                self.sdr.tune_to_cb_channel(i)
                self.cb.setCurrentIndex(0)

        def pick_preset(self, i):
            if self.sdr and i > 0:
                self.halt()
                name, mhz = self.sdr.FM_PRESETS[i - 1]
                self.sdr.set_modulation("WFM")
                self.sdr.request_tune(mhz, recenter=True)
                self.preset.setCurrentIndex(0)

    panel = Panel()
    return panel, panel.activate, panel.deactivate
