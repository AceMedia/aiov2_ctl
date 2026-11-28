"""Screensavers: something to look at when the app has been left alone.

Off until switched on in Settings > General, where the wait is chosen too. After that long with no key, button,
click or pointer movement, the chosen one covers the app's window; any of those takes it away again and is
used up doing so, so the key that wakes the app does nothing else. It only draws. No rail is switched, nothing
is started or stopped, and whatever the app was doing carries on underneath.

It covers the app's own window, not the desktop: with the app in full screen that is the whole screen.

Each one is drawn dark, in the app's pixel style and the feature colour, whatever the theme:
  mandelbrot  a slow dive into the Mandelbrot set, drawn in blocks, a few rows at a time
  system      the processor, temperature, memory and disk as bars, with the last minutes as graphs
  stars       a starfield
  rain        falling code
  clock       the time and date, drifting so nothing stays in one place

Everything above build() is plain Python and is tested without a screen."""
import math
import random
import time

KINDS = (("mandelbrot", "Mandelbrot"), ("system", "System"), ("stars", "Starfield"), ("rain", "Falling code"),
         ("clock", "Clock"))
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
HOLD_SECONDS = 2.5                                  # how long a finished picture stays before the next one starts
BUDGET = 0.010                                      # seconds of working out per tick: a fifth of the processor, no more


def escape(cx, cy, limit):
    """How many steps z = z*z + c takes to leave the circle of radius 2, or `limit` when it has not by then
    (which is taken to mean c is in the set)."""
    x = y = 0.0
    for n in range(limit):
        xx, yy = x * x, y * y
        if xx + yy > 4.0:
            return n
        x, y = xx - yy + cx, 2.0 * x * y + cy
    return limit


def view(level, target):
    """(centre x, centre y, width) of the picture at a zoom level. The first few levels slide from the whole
    set to the target, so the dive starts from something recognisable."""
    t = min(1.0, level / 4.0)
    return -0.6 + (target[0] + 0.6) * t, target[1] * t, START_SPAN / (ZOOM ** level)


def limit_for(level):
    """More steps the deeper it goes, or the detail would smear into the set."""
    return 48 + 9 * level


def row(y, cols, rows, cx, cy, span, limit):
    """One row of the picture: the escape count of each block."""
    step = span / cols
    im = cy + (y - rows / 2.0 + 0.5) * step
    left = cx - span / 2.0 + step / 2.0
    return [escape(left + x * step, im, limit) for x in range(cols)]


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


def shade(count, limit, colours):
    """The colour of a block from its escape count."""
    return colours[0] if count >= limit else colours[1 + count % (len(colours) - 1)]


def next_dive(level, target_index):
    """(level, target index) after a finished picture: one deeper, or back out to a new place."""
    if level + 1 >= LEVELS:
        return 0, (target_index + 1) % len(TARGETS)
    return level + 1, target_index


# ---- starfield
def new_star(rng, far=False):
    return [rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(0.6, 1.0) if far else rng.uniform(0.05, 1.0)]


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
    from PyQt6.QtCore import QPoint, QRect, Qt, QTimer
    from PyQt6.QtGui import QColor, QCursor, QImage, QPainter, QPen
    from PyQt6.QtWidgets import QWidget
    from . import pixel, statusbar, style

    BLOCK = 6

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
            self.timer.start({"mandelbrot": 50, "stars": 66, "rain": 100}.get(self.kind, 1000))

        def stop(self):
            self.timer.stop()
            self.hide()
            self.state = {}

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
            s = self.state = {}
            if self.kind == "mandelbrot":
                s["cols"], s["rows"] = max(8, w // BLOCK), max(8, h // BLOCK)
                s["image"] = QImage(s["cols"], s["rows"], QImage.Format.Format_RGB32)
                s["image"].fill(QColor(self.pal["bg"]))
                s["colours"] = [QColor(c).rgb() for c in ramp(self.pal["bg"], self.pal["accent"], self.pal["accent2"])]
                s["level"], s["target"], s["y"], s["hold"] = 0, self.rng.randrange(len(TARGETS)), 0, 0.0
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

        def read_system(self):
            s = self.state
            stats = statusbar.read_stats(s.get("sample"))
            s["sample"], s["stats"] = stats["sample"], stats
            push(s["cpu"], stats.get("cpu"))
            push(s["temp"], stats.get("temp"))

        def tick(self):
            now = time.monotonic()
            dt, self.last = min(0.25, now - self.last), now
            s = self.state
            if self.kind == "mandelbrot":
                if s["hold"]:
                    if now >= s["hold"]:
                        s["level"], s["target"] = next_dive(s["level"], s["target"])
                        s["y"], s["hold"] = 0, 0.0
                else:
                    cx, cy, span = view(s["level"], TARGETS[s["target"]])
                    limit, until = limit_for(s["level"]), now + BUDGET        # a few rows, then let the app breathe
                    first = s["y"]
                    while s["y"] < s["rows"] and time.monotonic() < until:
                        colours = s["colours"]
                        for x, n in enumerate(row(s["y"], s["cols"], s["rows"], cx, cy, span, limit)):
                            s["image"].setPixel(x, s["y"], colours[0] if n >= limit else colours[1 + n % (len(colours) - 1)])
                        s["y"] += 1
                    if s["y"] >= s["rows"]:
                        s["hold"] = now + HOLD_SECONDS
                    # only the rows just worked out, and the caption at the foot, need drawing again
                    self.update(0, first * BLOCK - BLOCK, self.width(), (s["y"] - first + 2) * BLOCK)
                    self.update(0, self.height() - 40, 420, 40)
                return
            elif self.kind == "stars":
                for star in s["stars"]:
                    star_step(star, dt, self.rng)
            elif self.kind == "rain":
                rain_step(s["drops"], s["rows"], dt, self.rng)
            elif self.kind == "system":
                self.read_system()
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
            if self.kind == "mandelbrot" and s.get("image") is not None:
                p.drawImage(self.rect(), s["image"])
                self.words(p, 16, h - 34, f"MANDELBROT  x{ZOOM ** s['level']:,.0f}", 16, pal["dim"])
            elif self.kind == "stars":
                for star in s.get("stars", ()):
                    at = star_at(star, w, h)
                    if at:
                        x, y, size, bright = at
                        p.fillRect(x, y, size, size, QColor(mix(pal["bg"], pal["text"], bright)))
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
            p.end()

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
