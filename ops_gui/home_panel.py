"""
The Home tab: a pixel art start screen in Debian colours.

Three blocks along the top (clock, power, a ticker screen) and three columns below (a live rack of what is
switched on and what it is doing, the machine's vitals, and the radios), then five soft keys (F1 to F5).
Everything that carries data is drawn at 24 pixels or more in the Spleen pixel font (Debian: fonts-spleen).

Built to be cheap on a battery. The frames, labels and background are drawn once into a pixmap. Once a
second each block works out what it would show, and only a block whose text changed is repainted. The
ticker's cursor ticks twice a second. Slow things (addresses, gpsd, the SDR and mesh) are fetched in a
thread every ten seconds. All timers stop while the tab is hidden. No effects, no antialiasing.
"""
import os
import socket
import subprocess
import threading
import time

from . import data as appdata, pixel, runtime, slideswitch, statusbar

SLOW_SECONDS = 10


def query_gpsd(wait=1.6):
    """Raw JSON lines from gpsd for a moment, or [] when it is not there."""
    lines = []
    try:
        with socket.create_connection(("127.0.0.1", 2947), timeout=0.5) as s:
            s.sendall(b'?WATCH={"enable":true,"json":true}\n')
            s.settimeout(0.4)
            end, buf = time.time() + wait, b""
            while time.time() < end:
                try:
                    chunk = s.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buf += chunk
                lines = buf.decode(errors="ignore").splitlines()
                if '"TPV"' in buf.decode(errors="ignore") and '"SKY"' in buf.decode(errors="ignore"):
                    break
    except OSError:
        return []
    return lines


def scan_processes():
    """(rtl_sdr frequency in MHz or None, whether meshtasticd is running) from /proc, no subprocess."""
    mhz, mesh = None, False
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return None, False
    for pid in pids:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                raw = f.read(400).decode(errors="ignore")
        except OSError:
            continue
        if "rtl_sdr" in raw and mhz is None:
            mhz = pixel.parse_rtl_cmdline(raw)
        if raw.split("\x00", 1)[0].endswith("meshtasticd"):
            mesh = True
    return mhz, mesh


def build_panel(win):
    from PyQt6.QtCore import QObject, QPoint, QRect, QRectF, QTimer, Qt, pyqtSignal
    from PyQt6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPixmap, QPolygon
    from PyQt6.QtWidgets import QWidget

    class Slow(QObject):
        got = pyqtSignal(object)

    class Home(QWidget):
        def __init__(self):
            super().__init__()
            self.setMinimumSize(900, 480)
            self.mode, self.pal = "dark", pixel.palette("dark")
            self.lay = pixel.layout(1256, 560)
            self.static = None
            self.leds = {}
            self.scan = None
            self.fonts = {}
            self.available = set(QFontDatabase.families())
            self.host = socket.gethostname()
            self.content = {}
            self.vit, self.cpu_hist, self.cpu_sample = [], [], None
            self.history = {k: [] for k in pixel.SPARK_RANGES}
            self.toggling, self.toggle_until = None, 0.0
            self.hover_target, self.hover_level, self.pressed = None, {}, None
            self.anim = QTimer(self)
            self.anim.setInterval(40)
            self.anim.timeout.connect(self.step_hover)
            self.setMouseTracking(True)
            self.ticks = 0
            self.throttled = 0
            self.gps, self.sdr_mhz, self.mesh_up = None, None, None
            self.addrs, self.nmdev = {}, {}
            self.slow_runs = 0
            self.wifi_key, self.wifi_cache = None, ((0, ""), [])
            self.slow = Slow()
            self.slow.got.connect(self.set_slow)
            self.timer = QTimer(self)
            self.timer.setTimerType(Qt.TimerType.VeryCoarseTimer)
            self.timer.timeout.connect(self.tick)
            self.slow_timer = QTimer(self)
            self.slow_timer.timeout.connect(self.fetch_slow)
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # ---- nothing runs while the tab is hidden
        def showEvent(self, event):
            super().showEvent(event)
            self.fetch_slow()
            self.refresh(True)
            self.timer.start(500)
            self.slow_timer.start(SLOW_SECONDS * 1000)

        def hideEvent(self, event):
            super().hideEvent(event)
            self.timer.stop()
            self.slow_timer.stop()
            self.anim.stop()
            self.hover_level.clear()

        def resizeEvent(self, event):
            super().resizeEvent(event)
            self.lay = pixel.layout(self.width(), self.height())
            self.static = None
            self.content = {}

        def refresh_palette(self):
            """The feature colour changed: draw everything again with it."""
            self.pal = pixel.palette(self.mode)
            self.static, self.leds, self.scan, self.content = None, {}, None, {}
            self.update()

        def set_mode(self, mode):
            if mode != self.mode:
                self.mode, self.pal = mode, pixel.palette(mode)
                self.static, self.leds, self.scan, self.content = None, {}, None, {}
                self.update()

        # ---- data
        def tick(self):
            self.ticks += 1
            self.refresh(self.ticks % 2 == 0)

        def fetch_slow(self):
            snap = win.snap or {}
            gps_on = (snap.get("rails") or {}).get("GPS") == "on"
            self.slow_runs += 1
            full = self.slow_runs % 3 == 1

            def run(cmd):
                try:
                    return subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
                except (OSError, subprocess.SubprocessError):
                    return ""

            def work():
                out = {}
                out["sdr"], out["mesh"] = scan_processes()
                if gps_on:
                    from . import position
                    out["gps"] = pixel.parse_gpsd(position.gpsd_lines())
                else:
                    out["gps"] = None
                if full:
                    out["addrs"] = pixel.parse_ip_addr(run(["ip", "-4", "-o", "addr"]))
                    out["nm"] = pixel.parse_nmcli_dev(run(["nmcli", "-t", "-f", "DEVICE,STATE,CONNECTION", "dev"]))
                    out["throttled"] = statusbar.read_throttled()
                self.slow.got.emit(out)

            threading.Thread(target=work, daemon=True).start()

        def set_slow(self, out):
            self.sdr_mhz, self.mesh_up = out.get("sdr"), out.get("mesh")
            g = out.get("gps")
            self.gps = g if g and g[0] is not None else None
            if "addrs" in out:
                self.addrs, self.nmdev, self.throttled = out["addrs"], out["nm"], out["throttled"]
            self.refresh(True)

        def wifi_info(self):
            """((networks, best SSID), channel histogram) from the last scan, cached until a new scan arrives."""
            panel = getattr(win, "wifi_panel", None)
            data = getattr(panel, "scan_data", None) or {}
            key = id(data)
            if key != self.wifi_key:
                from . import wifi_panel
                rows = wifi_panel.merged_rows(data.get("networks"))
                info = (len(rows), rows[0]["ssid"] if rows else "") if rows else None
                self.wifi_key = key
                self.wifi_cache = (info, pixel.channel_histogram(data.get("networks")) if rows else None)
            return self.wifi_cache

        def refresh(self, second):
            """Work out what each block shows; repaint only the ones that changed."""
            snap = win.snap or {}
            new = {}
            if second:
                stats = statusbar.read_stats(self.cpu_sample, self.throttled)
                self.cpu_sample = stats["sample"]
                self.cpu_hist = (self.cpu_hist + [stats["cpu"]])[-3:]
                stats["cpu"] = pixel.average(self.cpu_hist)
                self.vit = pixel.vital_rows(stats, self.vit)
                self.stats = stats
                for row in self.vit:
                    value = {"TEMP": stats.get("temp")}.get(row[0], row[2])
                    self.history[row[0]] = pixel.push_history(self.history[row[0]], value)
            else:
                stats = getattr(self, "stats", {})
            self.set_mode(win.theme if getattr(win, "theme", None) in ("light", "dark") else "dark")
            now = time.time()
            new["clock"] = (pixel.clock_text(now, int(now) % 2 == 0), pixel.date_text(now), self.host)
            pct, state, watts = pixel.battery_summary(snap)
            colour = "good" if (pct or 0) >= 30 else "warn" if (pct or 0) >= 15 else "hot"
            est = getattr(win, "runtime", None)
            if est is not None and pct is not None:     # time left goes after the watts when there is room
                watts = pixel.power_line(watts, runtime.short_options(est.estimate(now)),
                                         pixel.power_chars(self.lay["power"][2], pct, appdata.battery_estimated(snap),
                                                           pixel.battery_size()[0] + pixel.BATTERY_GAP))
            charging = appdata.is_charging(snap.get("power"))
            new["power"] = (pct, state, watts, colour if pct is not None else "dim", appdata.battery_estimated(snap),
                            charging, self.ticks if charging else 0)     # the tick moves the charging sweep along
            wifi, hist = self.wifi_info()
            flip = (getattr(win, "flip_status", None) or {}).get("mode")
            names = getattr(win, "names", ())
            flip = flip if "Flipper" in names else None          # unplugged: its tab has gone, so has its row
            bt = getattr(getattr(win, "pages", {}).get("Bluetooth"), "heard", 0) if "Bluetooth" in names else None
            rack = pixel.rack_rows(snap, self.gps, self.sdr_mhz, self.mesh_up, flip, wifi, bt)
            if self.toggling and (time.time() > self.toggle_until or not getattr(win, "rail_busy", False)):
                self.toggling = None
            for i in range(len(pixel.SOFT_KEYS)):
                new[("key", i)] = 0
            for i, row in enumerate(rack):
                busy = self.toggling is not None and pixel.rack_toggle(i) == self.toggling
                new[("rack", i)] = row + (pixel.rack_toggle(i) is not None, busy)
            for i, row in enumerate(self.vit):
                series = self.history.get(row[0], [])
                new[("vit", i)] = row + (tuple(series[-60:]),)
            cards = pixel.interface_cards(getattr(win, "radio_result", {}), self.addrs, self.nmdev)
            wired = pixel.ethernet_card(addrs=self.addrs)
            if wired:                                   # a cable that is plugged in takes the second slot
                cards = cards[:1] + [wired] + cards[1:]
            for i in range(2):
                new[("link", i)] = tuple(sorted(cards[i].items())) if i < len(cards) else ()
            new[("link", 2)] = tuple(h[1] for h in hist) if hist else ()
            gps_msg = None
            if (snap.get("rails") or {}).get("GPS") == "on":
                mode, sats = self.gps if self.gps else (None, None)
                gps_msg = ({3: "3D FIX", 2: "2D FIX"}.get(mode, "NO FIX" if mode is not None else "WAITING"),
                           f"{sats} SATELLITES USED" if sats else "SEARCHING", "good" if mode in (2, 3) else "warn")
            ctx = {"pct": pct, "charging": "CHARG" in state and "DISCH" not in state, "state": state,
                   "temp": (stats or {}).get("temp"), "throttled": statusbar.throttle_text(self.throttled),
                   "gps": gps_msg, "wifi": wifi, "sdr": f"{self.sdr_mhz:.3f}M" if self.sdr_mhz else None,
                   "lora": bool(self.mesh_up) if (snap.get("rails") or {}).get("LORA") == "on" else None,
                   "bt": bt, "flipper": flip if flip in ("cli", "bridge") else None,
                   "uptime": (stats or {}).get("uptime"), "load": (stats or {}).get("load") or 0.0,
                   "ip": self.addrs.get("wlan0") or next(iter(self.addrs.values()), ""),
                   "ssid": (self.nmdev.get("wlan0") or ("", ""))[1]}
            every = getattr(win, "pages", {}).get("Everything") if "Everything" in names else None
            if getattr(every, "recording", False) and getattr(every, "session", None) is not None:
                ctx["session"] = now - every.session.t0
            head, sub, level, _pinned = pixel.pick_ticker(pixel.ticker_messages(ctx), self.ticks)
            new["ticker"] = (head, sub, level, self.ticks % 2 == 0)
            for key, state_ in new.items():
                if self.content.get(key) != state_:
                    self.content[key] = state_
                    rect = self.rect_of(key)
                    if rect is not None:
                        self.update(rect)

        def rect_of(self, key):
            lay = self.lay
            if isinstance(key, tuple):
                kind, i = key
                src = {"rack": lay["rack"], "vit": lay["vitals"], "link": lay["links"], "key": lay["keys"]}[kind]
                return QRect(*src[i])
            return QRect(*lay[key])

        # ---- drawing helpers
        def font(self, px):
            size = pixel.nearest_size(px)
            if size not in self.fonts:
                fam = pixel.font_family(size, self.available)
                f = QFont(fam) if fam else QFont()
                f.setPixelSize(size)
                f.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
                f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
                self.fonts[size] = f
            return self.fonts[size]

        def text(self, p, x, y, s, px, colour, align="left", width=0):
            f = self.font(px)
            p.setFont(f)
            p.setPen(QColor(colour))
            size = pixel.nearest_size(px)
            w = p.fontMetrics().horizontalAdvance(s)
            if align == "right":
                x = x + width - w
            elif align == "centre":
                x = x + (width - w) // 2
            p.drawText(QPoint(x, y + size - max(2, size // 8)), s)
            return w

        def colour(self, level):
            return self.pal.get(level, self.pal["dim"]) if level not in ("off", "dim") else self.pal["dim"]

        def panel(self, p, rect, fill=None, title=None, tick=True, line=None, part="all"):
            """A panel with two chamfered corners, a red title tab and an orange tick. `part` draws only the
            "shape" (the body and its frame) or only the "marks" (tick and title), for the static layer, where
            the shapes are glass and the marks are not."""
            x, y, w, h = rect
            c = 8
            if part != "marks":
                poly = QPolygon([QPoint(x + c, y), QPoint(x + w - 1, y), QPoint(x + w - 1, y + h - 1 - c),
                                 QPoint(x + w - 1 - c, y + h - 1), QPoint(x, y + h - 1), QPoint(x, y + c)])
                p.setPen(QColor(line or self.pal["line"]))
                p.setBrush(QColor(fill or self.pal["panel"]))
                pen = p.pen()
                pen.setWidth(2)
                p.setPen(pen)
                p.drawPolygon(poly)
            if part == "shape":
                return
            if tick:
                p.fillRect(x + w - 22, y, 14, 4, QColor(self.pal["accent2"]))
            if title:
                self.text(p, x, y, "", 16, "#ffffff")
                tw = len(title) * 8 + 20
                p.fillRect(x + 14, y - 2, tw, 22, QColor(self.pal["accent"]))
                self.text(p, x + 24, y + 1, title, 16, "#ffffff")

        def spark(self, p, x, y, w, h, hist, label, col):
            """A pixel line chart: a 2 pixel line on a 4 pixel grid with a dithered fill underneath."""
            lo, hi = pixel.SPARK_RANGES[label]
            cols = pixel.spark_columns(list(hist), lo, hi, w, h)
            if len(cols) < 2:
                return
            c = QColor(col)
            fill = QColor(col)
            fill.setAlpha(70)
            prev = None
            for cx, cy in cols:
                for yy in range(cy + 4, h, 4):
                    if (cx // 4 + yy // 4) % 2 == 0:
                        p.fillRect(x + cx, y + yy, 4, 2, fill)
                p.fillRect(x + cx, y + cy, 4, 2, c)
                if prev is not None and prev != cy:
                    p.fillRect(x + cx, y + min(prev, cy), 2, abs(prev - cy) + 2, c)
                prev = cy

        def divider(self, p, x, y, w):
            """A pixel divider: dashes with an orange cap at each end, like a cut line on a circuit board."""
            line = QColor(self.pal["line"])
            for dx in range(6, w - 6, 12):
                p.fillRect(x + dx, y, 8, 2, line)
            for cap in (x, x + w - 4):
                p.fillRect(cap, y - 1, 4, 4, QColor(self.pal["accent2"]))

        def segments(self, p, x, y, w, h, percent, colour, n=20):
            gap = 3
            sw = (w - gap * (n - 1)) // n
            lit = pixel.lit_segments(percent, n)
            for i in range(n):
                p.fillRect(x + i * (sw + gap), y, sw, h, QColor(colour if i < lit else self.pal["off"]))

        def pixel_switch(self, p, x, y, on, busy):
            """The same slide switch as the Overview's and every other panel's (slideswitch.py), drawn at x, y.
            Amber while its rail is still switching."""
            u = slideswitch.UNIT
            for cx, cy, w, h, colour in slideswitch.cells(on, busy=busy):
                p.fillRect(x + cx * u, y + cy * u, w * u, h * u, QColor(colour))

        @staticmethod
        def stair_rect(p, x, y, w, h, colour):
            c = QColor(colour)
            p.fillRect(x + 2, y, w - 4, h, c)
            p.fillRect(x, y + 2, w, h - 4, c)
            p.fillRect(x + 1, y + 1, w - 2, h - 2, c)

        def led(self, level):
            if level not in self.leds:
                pm = QPixmap(36, 36)
                pm.fill(Qt.GlobalColor.transparent)
                q = QPainter(pm)
                col = QColor(self.colour(level) if level in ("good", "warn", "hot") else self.pal["off"])
                if level in ("good", "warn", "hot"):
                    halo = QColor(col)
                    halo.setAlpha(70)
                    for yy in range(0, 36, 2):
                        for xx in range((yy // 2) % 2, 36, 2):
                            if min(xx, yy, 35 - xx, 35 - yy) < 4:
                                q.fillRect(xx, yy, 1, 1, halo)
                q.fillRect(4, 4, 28, 28, QColor(self.pal["line"]))
                q.fillRect(6, 6, 24, 24, col)
                q.end()
                self.leds[level] = pm
            return self.leds[level]

        # ---- the static layer: drawn once
        def build_static(self):
            # Home has no background of its own: the pane it sits in (and the window's gradient behind that) is
            # the background, as on every other tab. Its panels are glass like the cards elsewhere: drawn solid
            # into one layer, which is then laid down once at the glass opacity so a frame and its body, or two
            # panels that touch, never stack up.
            from .skin import GLASS_ALPHA
            pal, lay = self.pal, self.lay

            def panels(q, part):
                for key in ("clock", "power"):
                    self.panel(q, lay[key], part=part)
                self.panel(q, lay["ticker"], pal["screen"], part=part)
                self.panel(q, lay["rack_panel"], title="RACK", part=part)
                self.panel(q, lay["vitals_panel"], title="VITALS", part=part)
                for i, rect in enumerate(lay["links"]):
                    self.panel(q, rect, title="AIR" if i == 2 else None, part=part)
                for rect in lay["keys"]:
                    self.panel(q, rect, pal["raised"], tick=False, part=part)

            shapes = QPixmap(max(1, self.width()), max(1, self.height()))
            shapes.fill(Qt.GlobalColor.transparent)
            q = QPainter(shapes)
            panels(q, "shape")
            q.end()
            pm = QPixmap(shapes.size())
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setOpacity(GLASS_ALPHA / 255)
            p.drawPixmap(0, 0, shapes)
            p.setOpacity(1.0)
            panels(p, "marks")
            for i, name in enumerate(pixel.RACK_NAMES):
                x, y, w, h = lay["rack"][i]
                if i != 4:                               # the fifth row's name changes with what is plugged in
                    self.text(p, x + 14 + slideswitch.WIDTH + 12, y + (h - 24) // 2, name, 24, pal["label"])
                if i:
                    self.divider(p, x + 12, y, w - 24)
            for i, name in enumerate(("CPU", "TEMP", "RAM", "DISK")):
                x, y, w, h = lay["vitals"][i]
                self.text(p, x + 14, y + 8, name, 24, pal["label"])
                if i:
                    self.divider(p, x + 12, y, w - 24)
            rx, ry, rw, rh = lay["rack_panel"]
            self.text(p, rx + 100, ry + 4, "TAP A RAIL TO SWITCH IT", 16, pal["dim"])
            for rect, (key, label) in zip(lay["keys"], pixel.SOFT_KEYS):
                x, y, w, h = rect
                kx, lx = pixel.key_text_x(w)
                self.text(p, x + kx, y + (h - 24) // 2, key, 24, pal["label"])
                self.text(p, x + lx, y + (h - 24) // 2, label, 24, pal["text"])
            p.end()
            self.static = pm

        def scanlines(self, size):
            if self.scan is None or self.scan.size() != size:
                pm = QPixmap(size)
                pm.fill(Qt.GlobalColor.transparent)
                q = QPainter(pm)
                for y in range(0, size.height(), 2):
                    q.fillRect(0, y, size.width(), 1, QColor(0, 0, 0, 64))
                q.end()
                self.scan = pm
            return self.scan

        # ---- the dynamic blocks
        def paintEvent(self, event):
            if self.static is None or self.static.size() != self.size():
                self.build_static()
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            r = event.rect()
            p.drawPixmap(r, self.static, r)
            if not self.content:
                p.end()
                return
            for key, state in self.content.items():
                rect = self.rect_of(key)
                if rect is not None and r.intersects(rect):
                    self.draw_block(p, key, state, rect)
            p.end()

        def draw_block(self, p, key, st, rect):
            pal = self.pal
            x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
            if key == "clock":
                time_s, date_s, host = st
                self.text(p, x + 14, y + 12, time_s, 64, pal["text"])
                self.text(p, x, y + 14, date_s, 24, pal["label"], "right", w - 14)
                self.text(p, x, y + 46, pixel.fit(host, 14), 24, pal["dim"], "right", w - 14)
                self.text(p, x + 14, y + 86, "DEBIAN 13", 24, pal["label"])
            elif key == "power":
                pct, state, watts, level, estimated, charging, phase = st
                col = self.colour(level)
                # the Overview's pixel battery, small, to the left of the percentage
                u = pixel.BATTERY_UNIT
                bw, bh = pixel.battery_size()
                bx, by = x + 14, y + 10 + (64 - bh) // 2
                shades = {"wall": pal["text"], "lit": col, "empty": pal["off"], "bolt": "#ffffff", "stroke": "#0f1114",
                          "sweep": pixel.blend(col, "#ffffff", 0.55), "pulse": pixel.blend(col, pal["off"], 0.5)}
                for cx, cy, cw, ch, role in pixel.battery_cells(pct, charging, phase):
                    p.fillRect(bx + cx * u, by + cy * u, cw * u, ch * u, QColor(shades[role]))
                self.text(p, bx + bw + pixel.BATTERY_GAP, y + 10, appdata.percent_text(pct, estimated), 64, col)
                self.text(p, x, y + 14, state, 24, col, "right", w - 14)
                self.text(p, x, y + 46, watts, 24, pal["dim"], "right", w - 14)
                self.segments(p, x + 14, y + h - 32, w - 28, 18, pct, col)
            elif key == "ticker":
                # the ticker is a dark "screen" in both themes, so it keeps the dark palette's bright text
                head, sub, level, cursor = st
                dp = pixel.palette("dark")
                head_col = dp[level] if level in ("good", "warn", "hot", "data") else dp["text"]
                self.text(p, x + 14, y + 18, pixel.fit(head, (w - 28) // 16), 32, head_col)
                end = self.text(p, x + 14, y + 66, pixel.fit(sub, (w - 56) // 12), 24, dp["dim"])
                if cursor:
                    p.fillRect(x + 14 + end + 8, y + 68, 12, 22, QColor(dp["data"]))
                p.drawPixmap(x, y, self.scanlines(rect.size()))
            elif key[0] == "key":
                level = self.hover_level.get(key, 0)
                if level:
                    f = min(1.0, level / 3)
                    fill = pixel.blend(pal["raised"], pal["accent"], 0.65 * f)
                    self.panel(p, (x, y, w, h), fill, tick=False, line=pal["accent2"] if level >= 2 else pal["accent"])
                    p.fillRect(x + 6, y + 8, 4, int((h - 16) * f), QColor(pal["accent2"]))
                    kk, label = pixel.SOFT_KEYS[key[1]]
                    kx, lx = pixel.key_text_x(w)
                    self.text(p, x + kx, y + (h - 24) // 2, kk, 24, "#ffffff")
                    self.text(p, x + lx, y + (h - 24) // 2, label, 24, "#ffffff")
                    if level >= 2:
                        self.text(p, x, y + (h - 24) // 2, ">", 24, pal["accent2"] if level < 4 else "#ffffff", "right", w - 14)
            elif key[0] == "rack":
                name, level, value, is_rail, busy = st
                hov = self.hover_level.get(("row", key[1]), 0) if is_rail or key[1] in (4, 5) else 0
                if hov:
                    tint = QColor(pal["accent"])
                    tint.setAlpha(28 * hov)
                    p.fillRect(x + 6, y + 2, w - 12, h - 4, tint)
                    p.fillRect(x + 6, y + 6, 3 * hov, h - 12, QColor(pal["accent2"]))
                if is_rail:
                    self.pixel_switch(p, x + 14, y + (h - slideswitch.HEIGHT) // 2, level in ("good", "warn", "hot", "dim") and value != "OFF", busy)
                else:
                    p.drawPixmap(x + 14 + (slideswitch.WIDTH - 36) // 2, y + (h - 36) // 2, self.led(level))
                if key[1] == 4:
                    self.text(p, x + 14 + slideswitch.WIDTH + 12, y + (h - 24) // 2, name, 24, pal["label"])
                value = "WAIT" if busy else value
                self.text(p, x, y + (h - 24) // 2, value, 24, self.colour(level) if level in ("good", "warn", "hot") else pal["dim"], "right", w - 16)
            elif key[0] == "vit":
                label, value, pct, level, hist = st
                col = self.colour(level)
                if label == "TEMP":
                    last = hist[-1] if hist else None
                    col = pixel.temp_colour(last, self.mode == "dark") or col
                self.text(p, x, y + 4, value, 32, col, "right", w - 16)
                self.segments(p, x + 14, y + h - 26, w - 28, 16, pct, col)
                self.spark(p, x + 84, y + 6, w - 84 - 96, 34, hist, label, col)
            elif key[0] == "link":
                if key[1] < 2:
                    card = dict(st)
                    if not card:
                        self.text(p, x + 14, y + 40, "NO RADIO", 24, pal["dim"])
                        return
                    self.text(p, x + 14, y + 10, pixel.fit(card["name"].upper(), 14), 24, pal["label"])
                    self.text(p, x, y + 10, card["state"], 24, self.colour(card["level"]), "right", w - 14)
                    self.text(p, x + 14, y + 42, pixel.fit(card["detail"], (w - 28) // 12), 24, pal["text"])
                    self.text(p, x + 14, y + 72, card["ip"], 24, pal["dim"])
                else:
                    if not st:
                        self.text(p, x + 14, y + 44, "PRESS F1 TO SCAN", 24, pal["dim"])
                        return
                    top = max(max(st), 1)
                    inner_w, base = w - 28, y + h - 26
                    bw = inner_w // len(st)
                    for i, n in enumerate(st):
                        bh = int((h - 56) * n / top) if n else 2
                        col = pal["data"] if i < 13 else pal["accent2"]
                        p.fillRect(x + 14 + i * bw, base - bh, bw - 3, bh, QColor(col if n else pal["off"]))
                    for idx, label in ((0, "1"), (5, "6"), (10, "11"), (13, "5G")):
                        self.text(p, x + 14 + idx * bw, base + 2, label, 16, pal["dim"])

        def hit(self, x, y):
            """What the pointer is over that does something: a soft key, a rail row or the Wi-Fi row."""
            i = pixel.key_at(x, y, self.lay["keys"])
            if i is not None:
                return ("key", i)
            row = pixel.rack_at(x, y, self.lay["rack"])
            if row is not None and (pixel.rack_toggle(row) or row in (4, 5)):
                return ("row", row)
            return None

        def mouseMoveEvent(self, event):
            target = self.hit(event.position().x(), event.position().y())
            if target != self.hover_target:
                self.hover_target = target
                self.setCursor(Qt.CursorShape.PointingHandCursor if target else Qt.CursorShape.ArrowCursor)
                if not self.anim.isActive():
                    self.anim.start()

        def leaveEvent(self, event):
            self.hover_target = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            if not self.anim.isActive():
                self.anim.start()

        def mousePressEvent(self, event):
            target = self.hit(event.position().x(), event.position().y())
            if target and target[0] == "key":
                self.pressed = target
                self.hover_level[target] = 4
                self.update(self.rect_of(target))

        def step_hover(self):
            """Move each hover level one step toward where it is heading; stop the timer when all are settled."""
            keys = set(self.hover_level) | ({self.hover_target} if self.hover_target else set())
            moving = False
            for k in keys:
                goal = (3 if k == self.hover_target else 0)
                now = self.hover_level.get(k, 0)
                new = now + (1 if goal > now else -1) if now != goal else now
                if now == 4 and goal == 3:
                    new = 3
                if new != now:
                    moving = True
                    self.hover_level[k] = new
                    rect = self.rect_of(("key", k[1])) if k[0] == "key" else self.rect_of(("rack", k[1]))
                    if rect is not None:
                        self.update(rect)
                if new == 0:
                    self.hover_level.pop(k, None)
            if not moving:
                self.anim.stop()

        def mouseReleaseEvent(self, event):
            self.pressed = None
            x, y = event.position().x(), event.position().y()
            i = pixel.key_at(x, y, self.lay["keys"])
            if i is not None:
                win.home_key(i)
                return
            row = pixel.rack_at(x, y, self.lay["rack"])
            rail = pixel.rack_toggle(row) if row is not None else None
            if rail:
                self.toggling, self.toggle_until = rail, time.time() + 25
                win.toggle_rail(rail)
                self.refresh(True)
            elif row == 5:
                win.home_key(0)
            elif row == 4:                              # the Flipper or Bluetooth row opens its tab
                tab = pixel.rack_tab((self.content.get(("rack", 4)) or ("",))[0])
                if tab in getattr(win, "names", ()):
                    win.goto(win.names.index(tab))

    return Home()
