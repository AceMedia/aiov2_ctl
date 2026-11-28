"""Screensavers: something to look at when the app has been left alone.

Off until switched on in Settings > General, where the wait is chosen too. After that long with no key, button,
click or pointer movement, the chosen one covers the app's window; any of those takes it away again and is
used up doing so, so the key that wakes the app does nothing else. It only draws. No rail is switched, nothing
is started or stopped, and whatever the app was doing carries on underneath.

It covers the app's own window, not the desktop: with the app in full screen that is the whole screen.

Each one is drawn dark, in the app's pixel style and the feature colour, whatever the theme:
  mandelbrot  a slow, steady dive into the Mandelbrot set, each picture gliding into the next
  system      the processor, temperature, memory and disk as bars, with the last minutes as graphs
  stars       a starfield
  rain        falling code
  clock       the time and date, drifting so nothing stays in one place
  signals     what the radios the app is already listening to can hear, as a level meter per radio

Everything above build() is plain Python and is tested without a screen."""
import math
import queue
import random
import threading
import time

KINDS = (("mandelbrot", "Mandelbrot"), ("system", "System"), ("stars", "Starfield"), ("rain", "Falling code"),
         ("clock", "Clock"), ("signals", "Signals"))
MINUTES = (1, 2, 5, 10, 30)
DEFAULT_KIND, DEFAULT_MINUTES = "mandelbrot", 5
WAKE_GRACE = 0.7            # seconds after it appears in which pointer movement does not count: showing it moves things
WAKE_DISTANCE = 6           # pixels the pointer has to travel to count as someone being there


def kind_of(value):
    return value if value in dict(KINDS) else DEFAULT_KIND


def minutes_of(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MINUTES
    return value if value in MINUTES else DEFAULT_MINUTES


def due(idle_seconds, on, minutes):
    """Whether it is time: switched on, and nothing has been touched for the chosen wait."""
    return bool(on) and idle_seconds >= minutes_of(minutes) * 60


def moved_enough(start, now, since_shown):
    """Whether pointer movement means someone is there: not in the first moment, and more than a twitch."""
    if since_shown < WAKE_GRACE or start is None:
        return False
    return abs(now[0] - start[0]) + abs(now[1] - start[1]) > WAKE_DISTANCE


def mix(a, b, k):
    """A colour k of the way from a to b, both '#rrggbb'."""
    ra, rb = [int(a[i:i + 2], 16) for i in (1, 3, 5)], [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * k) for x, y in zip(ra, rb))


# ---- Mandelbrot
# Places worth diving into, each on the edge of the set where the detail never runs out.
TARGETS = ((-0.743643887037151, 0.131825904205330), (-1.25066, 0.02012), (-0.16070135, 1.0375665),
           (0.001643721971153, -0.822467633298876), (-0.7453, 0.1127))
ZOOM, START_SPAN, LEVELS = 1.55, 3.4, 26            # 3.4 / 1.55**26 is about 4e-5 across: far inside what a float holds
HOLD_SECONDS = 2.5                                  # how long the deepest picture stays before the next dive starts
BUDGET = 0.010                                      # without numpy: seconds of working out per tick
SECONDS_PER_LEVEL = 5.0                             # how long the glide from one picture to the next, closer one takes
FADE_SECONDS = 0.7                                  # the sharper picture is faded in over the magnified one
DUTY = 0.25                                         # with numpy: the share of one processor core the working out may use
BAILOUT = 256.0                                     # |z| squared at which a point has left: far enough out that the
                                                    # shading runs evenly from one band into the next
DENSITY = 5.0                                       # colours stepped through for each step a point takes to leave
PALETTE = 240                                       # colours in one turn of the ramp
LOG2 = math.log(2.0)


def numpy_or_none():
    """numpy if it is installed (it comes with the ops console), or None. With it the picture is worked out a
    strip at a time off the main thread and can be three times as fine; without it, it is drawn in blocks."""
    try:
        import numpy
        return numpy
    except Exception:
        return None


def smooth(cx, cy, limit):
    """How long z = z*z + c takes to leave, as a number with a fraction (so the colour changes evenly, with no
    bands), or -1.0 when it has not left after `limit` steps, which is taken to mean c is in the set."""
    x = y = 0.0
    for n in range(limit):
        xx, yy = x * x, y * y
        if xx + yy > BAILOUT:
            return max(0.0, n + 1.0 - math.log(math.log(xx + yy) / 2.0) / LOG2)
        x, y = xx - yy + cx, 2.0 * x * y + cy
    return -1.0


def view(level, target):
    """(centre x, centre y, width) of the picture at a zoom level, which may be a fraction. The dive closes on
    the target exactly as fast as the picture shrinks, so every picture lies wholly inside the one before it
    and the last one can be magnified into the next without an edge showing. It starts from the whole set."""
    span = START_SPAN / (ZOOM ** level)
    k = span / START_SPAN
    start_x, start_y = -0.6 + (target[0] + 0.6) * 0.5, target[1] * 0.5
    return target[0] + (start_x - target[0]) * k, target[1] + (start_y - target[1]) * k, span


def limit_for(level):
    """More steps the deeper it goes, or the detail would smear into the set."""
    return 48 + 9 * int(level)


def smooth_row(y, cols, rows, cx, cy, span, limit):
    """One row of the picture: smooth() for each point across it."""
    step = span / cols
    im = cy + (y - rows / 2.0 + 0.5) * step
    left = cx - span / 2.0 + step / 2.0
    return [smooth(left + x * step, im, limit) for x in range(cols)]


def smooth_rows_np(np, y0, y1, cols, rows, cx, cy, span, limit):
    """Rows y0 up to y1 of the picture at once, with numpy: the same numbers smooth_row() gives, as an array."""
    step = span / cols
    cr = np.repeat((cx - span / 2.0 + step / 2.0 + np.arange(cols) * step)[None, :], y1 - y0, 0)
    ci = np.repeat((cy + (np.arange(y0, y1) - rows / 2.0 + 0.5) * step)[:, None], cols, 1)
    x, y = np.zeros_like(cr), np.zeros_like(cr)
    out = np.full(cr.shape, -1.0)
    live = np.ones(cr.shape, dtype=bool)
    for n in range(limit):
        xx, yy = x * x, y * y
        mag = xx + yy
        gone = live & (mag > BAILOUT)
        if gone.any():
            out[gone] = np.maximum(0.0, n + 1.0 - np.log(np.log(mag[gone]) / 2.0) / LOG2)
            live &= ~gone
            if not live.any():
                break
        y = np.where(live, 2.0 * x * y + ci, 0.0)
        x = np.where(live, xx - yy + cr, 0.0)
    return out


def work_rows(np, job, put, alive, duty=DUTY, strip=24, clock=time.monotonic, sleep=time.sleep):
    """Work a whole picture out, a strip of rows at a time, handing each to put(first row, numbers). Run on a
    thread of its own. After each strip it rests long enough that the working out takes only `duty` of a
    processor core, and it gives up as soon as alive() says no. True when the picture was finished."""
    cols, rows, (cx, cy, span), limit = job
    y = 0
    while y < rows and alive():
        y1 = min(rows, y + strip)
        began = clock()
        put(y, smooth_rows_np(np, y, y1, cols, rows, cx, cy, span, limit))
        y = y1
        if 0 < duty < 1 and y < rows and alive():
            sleep((clock() - began) * (1.0 / duty - 1.0))
    return y >= rows


def ramp(bg, accent, accent2, steps=32):
    """The colours of the picture: the first is the set itself, the rest run from the background up through
    the feature colour and its partner to a pale tip and back down, so neighbouring bands flow into each other."""
    pale = mix(accent2, "#ffffff", 0.6)
    stops = [mix(bg, accent, 0.25), accent, accent2, pale, accent2, accent, mix(bg, accent, 0.25)]
    out = [bg]
    per = max(1, steps // (len(stops) - 1))
    for a, b in zip(stops, stops[1:]):
        out += [mix(a, b, i / per) for i in range(per)]
    return out


def colour_index(count, size):
    """Which of `size` colours a point gets from smooth(): the first for the set, the rest in a ring."""
    return 0 if count < 0 else 1 + int(count * DENSITY) % (size - 1)


def colour_words(colours):
    """Each '#rrggbb' as the number a picture's memory holds for it."""
    return [0xff000000 | int(c[1:], 16) for c in colours]


def glide(level, seconds, newest):
    """The zoom `seconds` later: steady, but never more than one step past the newest finished picture, so
    what is on screen is never magnified further than that while the next one is being worked out."""
    return min(level + seconds / SECONDS_PER_LEVEL, newest + 1.0)


def window_in(picture, now, cols, rows):
    """Where the view `now` lies inside a picture worked out for the view `picture`, in that picture's own
    points: (left, top, width, height), fractions and all. Both views are (centre x, centre y, width)."""
    scale = cols / picture[2]
    w = now[2] * scale
    h = w * rows / cols
    return (now[0] - picture[0]) * scale + cols / 2.0 - w / 2.0, (now[1] - picture[1]) * scale + rows / 2.0 - h / 2.0, w, h


def placed(window, cols, rows, width, height):
    """(the part of the picture to take, where on a width x height screen to put it), each (left, top, width,
    height), for a window from window_in(). The window is cut down to what the picture actually has, and the
    place on screen is cut down to match; None when none of it is in the picture."""
    x, y, w, h = window
    if w <= 0 or h <= 0:
        return None
    x0, y0, x1, y1 = max(0.0, x), max(0.0, y), min(float(cols), x + w), min(float(rows), y + h)
    if x1 <= x0 or y1 <= y0:
        return None
    fx, fy = width / w, height / h
    return (x0, y0, x1 - x0, y1 - y0), ((x0 - x) * fx, (y0 - y) * fy, (x1 - x0) * fx, (y1 - y0) * fy)


def next_dive(level, target_index):
    """(level, target index) after a finished picture: one deeper, or back out to a new place."""
    if level + 1 >= LEVELS:
        return 0, (target_index + 1) % len(TARGETS)
    return level + 1, target_index


# ---- starfield
def new_star(rng, far=False):
    return [rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(0.6, 1.0) if far else rng.uniform(0.05, 1.0)]


def star_ink(index, text, accent, accent2):
    """The colour a star shines in: most are the plain text colour, one in four the feature colour and one in
    twelve its partner, so the sky belongs to the same look as everything else."""
    if index % 12 == 6:
        return accent2
    return accent if index % 4 == 0 else text


def star_step(star, dt, rng, speed=0.22):
    """Move a star towards the viewer; one that has passed starts again far away."""
    star[2] -= speed * dt
    if star[2] <= 0.02:
        star[:] = new_star(rng, far=True)
    return star


def star_at(star, width, height):
    """(x, y, size in pixels, brightness 0 to 1) on the screen, or None when it has left it."""
    x = width / 2 + star[0] / star[2] * width / 2
    y = height / 2 + star[1] / star[2] * height / 2
    if not (0 <= x < width and 0 <= y < height):
        return None
    near = 1.0 - min(1.0, star[2])
    return int(x), int(y), 2 + int(near * 6) // 2 * 2, 0.3 + 0.7 * near


# ---- falling code
GLYPHS = "0123456789ABCDEF<>[]{}/\\|=+-*#$%&"


def new_drop(col, rows, rng, anywhere=False):
    return {"col": col, "y": rng.uniform(-rows, rows if anywhere else 0), "speed": rng.uniform(6, 18),
            "length": rng.randint(5, 18)}


def rain_step(drops, rows, dt, rng):
    """Move every drop down; one whose tail has left the bottom starts again above the top."""
    for i, d in enumerate(drops):
        d["y"] += d["speed"] * dt
        if d["y"] - d["length"] > rows:
            drops[i] = new_drop(d["col"], rows, rng)
    return drops


def drop_cells(drop, rows):
    """[(row, brightness 0 to 1)] for a drop: brightest at its head, fading up its tail."""
    head = int(drop["y"])
    return [(head - i, 1.0 - i / drop["length"]) for i in range(drop["length"]) if 0 <= head - i < rows]


# ---- clock
def drift(t, width, height, box_w, box_h):
    """Where the clock's top left corner is at time t: a slow wander that keeps the whole box on the screen and
    never rests, so no pixel is lit all the time."""
    free_w, free_h = max(0, width - box_w), max(0, height - box_h)
    return (int(free_w * (0.5 + 0.5 * math.sin(t / 37.0))), int(free_h * (0.5 + 0.5 * math.sin(t / 23.0 + 1.3))))


# ---- signals
# What the app's radios are already hearing, drawn as bars: Wi-Fi networks, Bluetooth devices, mesh nodes and
# GPS satellites. It only reads what the pages already hold; it never starts a scan, switches a rail or asks a
# radio anything, and the software radio is left out altogether. A bar's height is signal strength and nothing
# more: no distance, direction, position or movement is worked out from it.
# signal_bands() and signal_layout() are kept apart from the widget so that a later, optional "ghost hunt"
# add-on can reuse them with the same data.
SIGNAL_SOURCES = (  # key, title, the word for one and for many, the field holding the strength, its range, its unit
    ("wifi", "WI-FI", "NETWORK", "NETWORKS", "rssi", -100.0, -30.0, "dBm"),
    ("bt", "BLUETOOTH", "DEVICE", "DEVICES", "rssi", -100.0, -30.0, "dBm"),
    ("mesh", "MESH", "NODE", "NODES", "snr", -20.0, 12.0, "dB"),
    ("gps", "GPS", "SATELLITE", "SATELLITES", "ss", 0.0, 50.0, "dB-Hz"))
SIGNAL_MIN_BAR, SIGNAL_MAX_BAR, SIGNAL_GAP = 6, 48, 4   # pixels: the narrowest and widest a bar may be, and between two
SIGNAL_SEGMENT, SIGNAL_SEGMENT_GAP = 6, 2               # pixels: one block of a bar, and the gap above it
SIGNAL_WANDER = 8                   # the whole picture drifts up to twice this many pixels each way, slowly
SIGNAL_SCALE = 112                  # pixels kept at the right of each band for the two ends of its scale


def dbm_of(text):
    """The number in a table's signal text such as '-50 dBm', or None when there is none."""
    try:
        return float(str(text).split()[0])
    except (IndexError, ValueError):
        return None


def wifi_heard(survey_rows, scan_networks):
    """The Wi-Fi networks the app has heard, for signal_bands(): [{"id", "rssi"}], each network once. Two pages
    hold them: the listening survey's table (signal as text, '-50 dBm') and the ordinary scan's list
    ('signal_dbm'). The survey's reading is used when both have a network; a network without a usable reading
    is left out."""
    out, seen = [], set()
    for row in survey_rows or ():
        if isinstance(row, dict):
            key, dbm = str(row.get("bssid") or "").lower(), dbm_of(row.get("signal"))
            if key and key not in seen and dbm is not None:
                seen.add(key)
                out.append({"id": key, "rssi": dbm})
    for net in scan_networks or ():
        if isinstance(net, dict):
            key, dbm = str(net.get("bssid") or "").lower(), net.get("signal_dbm")
            if key and key not in seen and isinstance(dbm, (int, float)) and not isinstance(dbm, bool):
                seen.add(key)
                out.append({"id": key, "rssi": dbm})
    return out


def strength(value, lo, hi):
    """A reading from lo to hi as 0 to 1, clamped at both ends; None when it is not a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or hi <= lo:
        return None
    return max(0.0, min(1.0, (float(value) - lo) / (hi - lo)))


def signal_bands(sources):
    """The bands to draw from plain data: `sources` maps 'wifi', 'bt', 'mesh' and 'gps' to lists of dicts, each
    with its strength under 'rssi' (dBm), 'snr' (dB) or 'ss' (dB-Hz), and optionally an 'id'. Anything else,
    including the SDR, is ignored. All four bands are always returned, in a fixed order, so each keeps its place:
    [{"key", "title", "caption", "state", "bars", "lo", "hi", "unit"}]. The state is "heard" with at least one
    usable reading, "nothing" when the source is there but has nothing usable, and "off" when it is not there at
    all (None or missing). The bars are strongest first (ties by id), as 0 to 1."""
    out = []
    sources = sources if isinstance(sources, dict) else {}
    for key, title, one, many, field, lo, hi, unit in SIGNAL_SOURCES:
        items = sources.get(key)
        found = []
        for item in items or ():
            if isinstance(item, dict):
                k = strength(item.get(field), lo, hi)
                if k is not None:
                    found.append((-k, str(item.get("id", "")), k))
        found.sort()
        state = "heard" if found else ("off" if items is None else "nothing")
        caption = f"{title}  {len(found)} {one if len(found) == 1 else many}" if found else title
        out.append({"key": key, "title": title, "caption": caption, "state": state,
                    "bars": [k for _, _, k in found], "lo": lo, "hi": hi, "unit": unit})
    return out


def nothing_heard(bands):
    """Whether not one of the bands has a bar to draw."""
    return not any(b.get("bars") for b in bands or ())


def scale_text(lo, hi, unit):
    """(the label for the bottom of a band's scale, the label for its top), in the band's own unit."""
    return f"{lo:g}", f"{hi:g} {unit}"


def fit_bars(count, room, least=SIGNAL_MIN_BAR, most=SIGNAL_MAX_BAR, gap=SIGNAL_GAP):
    """(how many bars are shown, how wide each is) for `count` bars across `room` pixels: as wide as fits with a
    gap between, from `least` to `most`; when more than fit at `least`, only as many as fit (the strongest)."""
    if count <= 0 or room < least:
        return 0, 0
    shown = min(count, (room + gap) // (least + gap))
    return shown, max(least, min(most, (room + gap) // shown - gap))


def segments_lit(k, total):
    """How many of a bar's `total` blocks are lit for a strength k from 0 to 1: at least one, so every thing
    heard shows."""
    return max(1, min(total, int(round(k * total))))


def segment_colour(i, total, k, bg, low, high):
    """The colour of block i (0 at the bottom) of a bar `total` blocks tall at strength k: from `low` at the
    bottom of the scale to `high` at its top, and dimmer, nearer the background, the weaker the bar."""
    ink = mix(low, high, i / (total - 1) if total > 1 else 1.0)
    return mix(bg, ink, 0.45 + 0.55 * max(0.0, min(1.0, k)))


def signal_layout(bands, width, height, t, margin=36, caption=34):
    """Where everything goes on a width x height screen at time t, from signal_bands(). The bands stack down the
    screen, always in the same places, and the whole picture drifts a few pixels on a slow cycle so nothing stays
    lit in one spot; the bars themselves keep their order. Returns {} on a screen too small, otherwise
    {"offset": (dx, dy), "message": (x, y, w, text) or None, "bands": [...]} where each band is
    {"key", "dim", "caption": (x, y, text), "note": (x, y, text) or None, "baseline": (x, y, w, h),
     "scale": ((x, y, w, top text), (x, y, w, bottom text)), "segments": blocks in a full bar,
     "bars": [(x, baseline y, w, blocks lit, strength)]}.
    Texts at (x, y, w, ...) are right-aligned (the scale) or centred (the message) in w."""
    wander = SIGNAL_WANDER
    inner_w, inner_h = width - 2 * margin - 2 * wander, height - 2 * margin - 2 * wander
    room_w = inner_w - SIGNAL_SCALE
    if not bands or room_w < SIGNAL_MIN_BAR or inner_h < len(bands) * (caption + 30):
        return {}
    dx, dy = drift(t, width, height, width - 2 * wander, height - 2 * wander)
    left, top0 = margin + dx, margin + dy
    tall = inner_h // len(bands)
    pitch = SIGNAL_SEGMENT + SIGNAL_SEGMENT_GAP
    total = max(1, (tall - caption - 14) // pitch)
    idle = nothing_heard(bands)
    out = []
    for i, band in enumerate(bands):
        top = top0 + i * tall
        base = top + caption + total * pitch                # the bars stand on this line
        shown, bw = fit_bars(len(band["bars"]), room_w)
        text = band["caption"]
        if shown < len(band["bars"]):
            text += f", STRONGEST {shown} SHOWN"
        note = None
        if band["state"] != "heard" and not idle:
            note = (left, top + caption + (total * pitch) // 2 - 8,
                    "NOT LISTENING" if band["state"] == "off" else "NOTHING HEARD")
        bottom, top_text = scale_text(band["lo"], band["hi"], band["unit"])
        out.append({"key": band["key"], "dim": band["state"] != "heard", "caption": (left, top, text), "note": note,
                    "baseline": (left, base + 2, inner_w, 2),
                    "scale": ((left + room_w, top + caption, SIGNAL_SCALE, top_text),
                              (left + room_w, base - 18, SIGNAL_SCALE, bottom)),
                    "segments": total,
                    "bars": [(left + j * (bw + SIGNAL_GAP), base, bw, segments_lit(k, total), k)
                             for j, k in enumerate(band["bars"][:shown])]})
    message = None
    if idle:                                                # in the empty room of the band just above the middle
        mid = out[(len(out) - 1) // 2]
        message = (left, mid["caption"][1] + caption + (total * pitch) // 2 - 12, inner_w, "NOTHING BEING RECEIVED")
    return {"offset": (dx, dy), "message": message, "bands": out}


# ---- system
HISTORY = 180               # readings kept for the graphs: three minutes at one a second


def push(history, value, keep=HISTORY):
    """Add a reading to a graph's history, dropping the oldest past `keep`. A missing reading repeats the last."""
    if value is None:
        value = history[-1] if history else 0.0
    history.append(float(value))
    del history[:-keep]
    return history


def graph_points(values, lo, hi, width, height):
    """[(x, y)] for a graph of `values` in a box, newest at the right edge, y measured down from the top."""
    if not values or hi <= lo:
        return []
    step = width / max(1, HISTORY - 1)
    start = width - step * (len(values) - 1)
    return [(round(start + i * step), round(height - (min(hi, max(lo, v)) - lo) / (hi - lo) * height))
            for i, v in enumerate(values)]


def lit(value, total, segments):
    """How many of a bar's segments are lit."""
    if value is None or total <= 0:
        return 0
    return max(0, min(segments, round(value / total * segments)))


def build(win):
    """The widget that covers the window. Qt is imported here."""
    from PyQt6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
    from PyQt6.QtGui import QColor, QCursor, QImage, QPainter, QPen
    from PyQt6.QtWidgets import QWidget
    from . import pixel, statusbar, style

    np = numpy_or_none()
    BLOCK = 2 if np is not None else 6                      # screen pixels to each point of the Mandelbrot picture

    class Saver(QWidget):
        def __init__(self):
            super().__init__(win)
            self.hide()
            self.setMouseTracking(True)
            self.setCursor(Qt.CursorShape.BlankCursor)
            # It paints every pixel itself. Saying so stops Qt drawing the whole app underneath on every frame,
            # which was most of the cost: a screensaver should leave the processor nearly idle.
            self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
            self.setAutoFillBackground(False)
            self.kind, self.shown_at, self.pointer = DEFAULT_KIND, 0.0, None
            self.rng = random.Random()
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.tick)
            self.state = {}

        # ---- showing and hiding
        def start(self, kind):
            self.kind = kind_of(kind)
            self.pal = pixel.palette("dark")                # dark whatever the theme; high contrast when that is on
            self.setGeometry(win.rect())
            self.shown_at, self.pointer = time.monotonic(), None
            self.last = time.monotonic()
            self.reset()
            self.show()
            self.raise_()
            self.timer.start({"mandelbrot": 50, "stars": 66, "rain": 100, "signals": 500}.get(self.kind, 1000))

        def stop(self):
            self.timer.stop()
            self.hide()
            self.give_up()
            self.state = {}

        def give_up(self):
            """Tell any picture still being worked out on its own thread to stop."""
            for flag in self.state.get("flags", ()):
                flag.set()

        def mouseMoveEvent(self, event):
            now = (event.position().x(), event.position().y())
            if self.pointer is None:
                self.pointer = now
            if moved_enough(self.pointer, now, time.monotonic() - self.shown_at):
                win.saver_stop()

        def resizeEvent(self, event):
            if self.isVisible():
                self.reset()

        # ---- each one's state
        def reset(self):
            w, h = max(1, self.width()), max(1, self.height())
            self.give_up()
            s = self.state = {}
            if self.kind == "mandelbrot":
                s["cols"], s["rows"] = max(8, w // BLOCK), max(8, h // BLOCK)
                s["colours"] = colour_words(ramp(self.pal["bg"], self.pal["accent"], self.pal["accent2"], PALETTE))
                if np is not None:
                    s["lut"] = np.array(s["colours"], dtype=np.uint32)
                s["flags"] = []
                s["target"], s["zoom"], s["hold"] = self.rng.randrange(len(TARGETS)), 0.0, 0.0
                s["shown"], s["fade"], s["restart"] = None, None, False
                s["look"] = view(0.0, TARGETS[s["target"]])
                s["work"] = self.new_picture(0)
            elif self.kind == "stars":
                s["stars"] = [new_star(self.rng) for _ in range(240)]
            elif self.kind == "rain":
                s["cell"] = 16
                s["rows"] = h // s["cell"] + 1
                s["drops"] = [new_drop(c, s["rows"], self.rng, anywhere=True) for c in range(0, w // s["cell"] + 1)
                              if c % 3 != 2]
                s["glyphs"] = {}
            elif self.kind == "system":
                s["cpu"], s["temp"], s["sample"], s["stats"] = [], [], None, {}
                self.read_system()
            elif self.kind == "signals":
                s["bands"] = signal_bands(self.heard())

        def heard(self):
            """What the pages already hold, as plain lists for signal_bands(). Read only: the same tables the
            Everything page reads, and nothing is asked of any radio. A source whose page or engine is not there
            at all is None, so it can be shown as not listening rather than as having heard nothing."""
            out = {}
            try:
                panel = getattr(win, "wifi_panel", None)
                out["wifi"] = None if panel is None else wifi_heard(
                    getattr(getattr(panel, "survey", None), "ap_list", None),
                    (getattr(panel, "scan_data", None) or {}).get("networks"))
            except Exception:
                pass
            try:
                page = (getattr(win, "pages", None) or {}).get("Bluetooth")
                snap = getattr(page, "last_snap", None) or {}
                out["bt"] = None if page is None else [{"id": d.get("addr"), "rssi": d.get("rssi")}
                                                       for d in snap.get("devices") or ()]
            except Exception:
                pass
            try:
                engine = getattr(getattr(win, "mesh_hub", None), "engine", None)
                nodes = (engine.snapshot().get("node_info") or {}) if engine is not None else {}
                out["mesh"] = None if engine is None else [{"id": n, "snr": info.get("snr")}
                                                           for n, info in nodes.items() if isinstance(info, dict)]
            except Exception:
                pass
            try:
                gps = getattr(win, "gps_widget", None)
                sats = getattr(getattr(gps, "sky", None), "sats", None) or ()
                out["gps"] = None if gps is None else [{"id": s.get("PRN"), "ss": s.get("ss")}
                                                       for s in sats if isinstance(s, dict)]
            except Exception:
                pass
            return out

        def read_system(self):
            s = self.state
            stats = statusbar.read_stats(s.get("sample"))
            s["sample"], s["stats"] = stats["sample"], stats
            push(s["cpu"], stats.get("cpu"))
            push(s["temp"], stats.get("temp"))

        # ---- the Mandelbrot dive
        # One picture is on screen and is magnified, steadily, towards the target. Meanwhile the next picture,
        # one step closer, is worked out; when the zoom reaches it, it is faded in over the magnified one, which
        # it matches point for point but is sharper than. So the dive never stops and never jumps.
        def new_picture(self, level):
            s = self.state
            cols, rows = s["cols"], s["rows"]
            pic = {"level": level, "view": view(level, TARGETS[s["target"]]), "y": 0, "done": False}
            if np is not None:
                pic["points"] = np.full((rows, cols), s["colours"][0], dtype=np.uint32)
                pic["image"] = QImage(pic["points"].data, cols, rows, cols * 4, QImage.Format.Format_RGB32)
                pic["strips"], flag = queue.Queue(), threading.Event()
                s["flags"].append(flag)
                threading.Thread(target=work_rows, daemon=True,
                                 args=(np, (cols, rows, pic["view"], limit_for(level)),
                                       lambda y, numbers, strips=pic["strips"]: strips.put((y, numbers)),
                                       lambda flag=flag: not flag.is_set())).start()
            else:
                pic["image"] = QImage(cols, rows, QImage.Format.Format_RGB32)
                pic["image"].fill(QColor(self.pal["bg"]))
            return pic

        def fill(self, pic):
            """Put what has been worked out since last time into the picture."""
            s = self.state
            cols, rows, size = s["cols"], s["rows"], len(s["colours"])
            if np is not None:
                while True:
                    try:
                        y, numbers = pic["strips"].get_nowait()
                    except queue.Empty:
                        break
                    index = np.where(numbers < 0, 0, 1 + (numbers * DENSITY).astype(np.int64) % (size - 1))
                    pic["points"][y:y + len(numbers)] = s["lut"][index]
                    pic["y"] = y + len(numbers)
            else:
                cx, cy, span = pic["view"]
                limit, until = limit_for(pic["level"]), time.monotonic() + BUDGET
                while pic["y"] < rows and time.monotonic() < until:
                    for x, n in enumerate(smooth_row(pic["y"], cols, rows, cx, cy, span, limit)):
                        pic["image"].setPixel(x, pic["y"], s["colours"][colour_index(n, size)])
                    pic["y"] += 1
            pic["done"] = pic["y"] >= rows

        def closer(self, pic):
            return self.new_picture(pic["level"] + 1) if pic["level"] + 1 < LEVELS else None

        def dive(self, now, dt):
            s = self.state
            work, shown = s["work"], s["shown"]
            if work is not None and not work["done"]:
                self.fill(work)
            if s["hold"]:
                if now >= s["hold"]:                        # the deepest picture has had its moment: somewhere new
                    s["hold"], s["restart"] = 0.0, True
                    s["target"] = next_dive(LEVELS - 1, s["target"])[1]
                    s["work"] = self.new_picture(0)
            elif shown is None:                             # the very first picture is shown as it is worked out
                if work["done"]:
                    s["shown"], s["work"] = work, self.closer(work)
            elif s["restart"]:                              # the old dive stays put until the new one's first picture
                if work["done"]:
                    s["fade"] = (shown, s["look"], now)
                    s["shown"], s["zoom"], s["restart"] = work, 0.0, False
                    s["work"] = self.closer(work)
            else:
                s["zoom"] = glide(s["zoom"], dt, shown["level"] if work is not None else shown["level"] - 1.0)
                if work is None:
                    if s["zoom"] >= shown["level"]:
                        s["hold"] = now + HOLD_SECONDS
                elif work["done"] and s["zoom"] >= work["level"]:
                    s["fade"] = (shown, None, now)
                    s["shown"], s["work"] = work, self.closer(work)
            if not s["restart"]:
                s["look"] = view(s["zoom"], TARGETS[s["target"]])

        def picture(self, p, pic, look):
            s = self.state
            where = placed(window_in(pic["view"], look, s["cols"], s["rows"]), s["cols"], s["rows"],
                           self.width(), self.height())
            if where:
                p.drawImage(QRectF(*where[1]), pic["image"], QRectF(*where[0]))

        def tick(self):
            now = time.monotonic()
            dt, self.last = min(0.25, now - self.last), now
            s = self.state
            if self.kind == "mandelbrot":
                self.dive(now, dt)
            elif self.kind == "stars":
                for star in s["stars"]:
                    star_step(star, dt, self.rng)
            elif self.kind == "rain":
                rain_step(s["drops"], s["rows"], dt, self.rng)
            elif self.kind == "system":
                self.read_system()
            elif self.kind == "signals":
                s["bands"] = signal_bands(self.heard())
            self.update()

        # ---- drawing
        def words(self, p, x, y, text, px, colour, align="left", width=0):
            p.setFont(style.pixel_font(px))
            p.setPen(QColor(colour))
            w = p.fontMetrics().horizontalAdvance(text)
            if align == "right":
                x = x + width - w
            elif align == "centre":
                x = x + (width - w) // 2
            p.drawText(QPoint(int(x), int(y + px - max(2, px // 8))), text)
            return w

        def bar(self, p, x, y, w, h, on, segments, colour):
            gap = 4
            sw = (w - gap * (segments - 1)) / segments
            for i in range(segments):
                p.fillRect(QRect(int(x + i * (sw + gap)), y, max(2, int(sw)), h),
                           QColor(colour if i < on else self.pal["off"]))

        def paintEvent(self, _event):
            p = QPainter(self)
            pal, s = self.pal, self.state
            w, h = self.width(), self.height()
            p.fillRect(self.rect(), QColor(pal["bg"]))
            if self.kind == "mandelbrot" and "look" in s:
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                shown, look = s["shown"], s["look"]
                if shown is None:
                    p.drawImage(self.rect(), s["work"]["image"])
                else:
                    part = 1.0
                    if s["fade"]:
                        old, where, began = s["fade"]
                        part = (time.monotonic() - began) / FADE_SECONDS
                        if part >= 1.0:
                            s["fade"], part = None, 1.0
                        else:
                            self.picture(p, old, where or look)
                    p.setOpacity(part)
                    self.picture(p, shown, look)
                    p.setOpacity(1.0)
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
                self.words(p, 16, h - 34, f"MANDELBROT  x{ZOOM ** s['zoom']:,.0f}", 16, pal["dim"])
            elif self.kind == "stars":
                for i, star in enumerate(s.get("stars", ())):
                    at = star_at(star, w, h)
                    if at:
                        x, y, size, bright = at
                        ink = star_ink(i, pal["text"], pal["accent"], pal["accent2"])
                        p.fillRect(x, y, size, size, QColor(mix(pal["bg"], ink, bright)))
            elif self.kind == "rain":
                cell = s["cell"]
                p.setFont(style.pixel_font(16))
                for d in s.get("drops", ()):
                    for r, bright in drop_cells(d, s["rows"]):
                        key = (d["col"], r)
                        if key not in s["glyphs"] or self.rng.random() < 0.02:
                            s["glyphs"][key] = self.rng.choice(GLYPHS)
                        colour = pal["text"] if bright > 0.93 else mix(pal["bg"], pal["accent"], 0.15 + 0.85 * bright)
                        p.setPen(QColor(colour))
                        p.drawText(QPoint(d["col"] * cell, r * cell + 14), s["glyphs"][key])
                if len(s["glyphs"]) > 6000:
                    s["glyphs"].clear()
            elif self.kind == "clock":
                now = time.localtime()
                box_w, box_h = 640, 250
                x, y = drift(time.monotonic(), w, h, box_w, box_h)
                p.save()                                    # the largest pixel font, doubled: still sharp
                p.translate(x, y)
                p.scale(2, 2)
                self.words(p, 0, 0, time.strftime("%H:%M", now), 64, pal["text"], "centre", box_w // 2)
                p.restore()
                self.words(p, x, y + 144, time.strftime("%S", now), 32, pal["accent"], "centre", box_w)
                self.words(p, x, y + 198, time.strftime("%a %d %b %Y", now).upper(), 24, pal["label"], "centre", box_w)
            elif self.kind == "system":
                self.paint_system(p, w, h)
            elif self.kind == "signals":
                self.paint_signals(p, w, h)
            p.end()

        def paint_signals(self, p, w, h):
            pal, s = self.pal, self.state
            layout = signal_layout(s.get("bands") or [], w, h, time.monotonic())
            if not layout:
                return
            inks = s.setdefault("inks", {})
            if len(inks) > 4000:
                inks.clear()
            pitch = SIGNAL_SEGMENT + SIGNAL_SEGMENT_GAP
            for band in layout["bands"]:
                dim = band["dim"]
                x, y, text = band["caption"]
                self.words(p, x, y, text, 24, mix(pal["bg"], pal["dim"], 0.6) if dim else pal["label"])
                if band["note"]:
                    x, y, text = band["note"]
                    self.words(p, x, y, text, 16, pal["dim"])
                p.fillRect(*band["baseline"], QColor(pal["off"] if dim else pal["line"]))
                for x, y, sw, text in band["scale"]:
                    self.words(p, x, y, text, 16, pal["off"] if dim else pal["dim"], "right", sw)
                total = band["segments"]
                for x, base, bw, on, k in band["bars"]:
                    for i in range(on):
                        ink = inks.get((i, total, k))
                        if ink is None:
                            ink = inks[(i, total, k)] = QColor(segment_colour(i, total, k, pal["bg"], pal["accent2"],
                                                                              pal["accent"]))
                        p.fillRect(x, base - (i + 1) * pitch + SIGNAL_SEGMENT_GAP, bw, SIGNAL_SEGMENT, ink)
            if layout["message"]:
                x, y, mw, text = layout["message"]
                self.words(p, x, y, text, 24, pal["dim"], "centre", mw)

        def paint_system(self, p, w, h):
            pal, s = self.pal, self.state
            stats = s.get("stats") or {}
            margin = 36
            self.words(p, margin, 24, "SYSTEM", 32, pal["accent"])
            self.words(p, margin, 30, time.strftime("%H:%M:%S"), 24, pal["dim"], "right", w - 2 * margin)
            mem = stats.get("mem")
            rows = (("CPU", stats.get("cpu"), 100, "%", pal["good"]),
                    ("TEMP", stats.get("temp"), 90, "C", pal["data"]),
                    ("RAM", mem[2] if mem else None, 100, "%", pal["warn"]),
                    ("DISK", stats.get("disk"), 100, "%", pal["label"]))
            y = 84
            for name, value, total, unit, colour in rows:
                self.words(p, margin, y, name, 24, pal["label"])
                self.words(p, margin + 110, y, "--" if value is None else f"{value:.0f}{unit}", 24, pal["text"])
                self.bar(p, margin + 210, y + 2, w - 2 * margin - 210, 22, lit(value, total, 40), 40, colour)
                y += 42
            top = y + 14
            gh = max(60, (h - top - 84) // 2 - 22)
            for title, values, lo, hi, colour in (("PROCESSOR, THE LAST THREE MINUTES", s.get("cpu", []), 0, 100, pal["good"]),
                                                  ("TEMPERATURE", s.get("temp", []), 20, 90, pal["data"])):
                self.words(p, margin, top, title, 16, pal["dim"])
                box = QRect(margin, top + 22, w - 2 * margin, gh)
                p.setPen(QPen(QColor(pal["line"]), 2))
                p.drawRect(box)
                pts = graph_points(values, lo, hi, box.width() - 4, box.height() - 4)
                p.setPen(QPen(QColor(colour), 2))
                for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                    p.drawLine(box.x() + 2 + x0, box.y() + 2 + y0, box.x() + 2 + x1, box.y() + 2 + y0)
                    p.drawLine(box.x() + 2 + x1, box.y() + 2 + y0, box.x() + 2 + x1, box.y() + 2 + y1)
                top += gh + 44
            power = (getattr(win, "snap", None) or {}).get("power") or {}
            foot = []
            if stats.get("load") is not None:
                foot.append(f"LOAD {stats['load']:.2f}")
            if stats.get("uptime") is not None:
                foot.append("UP " + statusbar.uptime_text(stats["uptime"]).upper())
            if power.get("capacity") is not None:
                foot.append(f"BATTERY {power['capacity']}%")
            self.words(p, margin, h - 44, "   ".join(foot), 24, pal["dim"])

    return Saver()
