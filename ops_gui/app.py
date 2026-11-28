"""
Native uConsole app (PyQt6). Read only and passive: battery, rails, Wi-Fi scan,
Flipper display and companions. Tabs for hardware only appear when it is there.
Every action is a key, as the uConsole has no touchscreen.

    python3 -m ops_gui
"""
import os
import shutil
import sys
import tempfile
import threading
import time

from ops_agent import handoff

from . import fieldlog
from . import startup
from . import runtime
from . import focusnav, navring
from . import screensaver
from . import (dither, helpview, controls, dashboard, data, drawer, flipper_panel, gps_panel, home_panel, lora_panel, marauder_panel, registry,
                    mesh_hub, net_panel, notify, pixel, reports, sdr_panel, settings_ui, skin, statusbar, style, wifi_panel)

REFRESH_MS = 5000
LIVE_MS = 1000
COLOURS = {"ok": "#43a047", "warn": "#e65100", "none": "#757575", "on": "#2e7d32", "off": "#616161"}
BIG = "font-size: 16pt;"


class FollowUp:
    """One remembered request. A refresh asked for because a rail was switched elsewhere used to be dropped when
    a refresh was already running, and that one may have read the rails before the switch. Now it is remembered
    and run when the current one ends. Asked from the window and taken from the worker, hence the lock."""

    def __init__(self):
        self.lock, self.wanted = threading.Lock(), False

    def remember(self):
        with self.lock:
            self.wanted = True

    def take(self):
        """True, once, if a request was remembered."""
        with self.lock:
            wanted, self.wanted = self.wanted, False
        return wanted


def closing_listeners(win):
    """The pages that are listening when the window closes, so each can be stopped properly: Bluetooth, Survey
    and Sense save what they hold and put their radio back in their own stop(). Closing used to skip that."""
    wifi = getattr(win, "wifi_panel", None)
    pages = [getattr(win, "pages", {}).get("Bluetooth"), getattr(wifi, "survey", None), getattr(wifi, "sense", None)]
    out = []
    for page in pages:
        if page is None:
            continue
        timer = getattr(page, "timer", None)
        busy = bool(getattr(page, "feed", None)) or bool(getattr(page, "waiting", 0)) or \
            bool(timer is not None and timer.isActive())
        if busy:
            out.append(page)
    if getattr(win, "ghost_on", False):            # the optional Ghost hunt tab: its stop() ends a recording
        from . import ghost
        page = getattr(win, "pages", {}).get(ghost.TAB)
        if page is not None and callable(getattr(page, "stop", None)):
            out.append(page)
    if getattr(win, "iga_on", False):              # the optional IGA browser: its stop() clears data if asked to
        page = getattr(win, "pages", {}).get("IGA")
        if page is not None and callable(getattr(page, "stop", None)):
            out.append(page)
    return out


def build_window():
    from PyQt6.QtCore import QTimer, pyqtSignal, pyqtProperty, QObject, Qt, QSize, QRectF, QPointF, QPropertyAnimation, QEasingCurve, QEvent, QSocketNotifier
    from PyQt6.QtGui import QShortcut, QKeySequence, QPixmap, QIcon, QImage, QPainter, QPen, QPolygonF, QFont, QKeyEvent
    from PyQt6.QtWidgets import (QFrame, QLabel, QVBoxLayout, QHBoxLayout, QWidget, QTabWidget,
                                 QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
                                 QScrollArea, QPushButton, QApplication, QMessageBox, QAbstractButton, QGridLayout,
                                 QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSizePolicy)
    from .foldertabs import FolderTabWidget
    from PyQt6.QtGui import QPalette, QColor

    class Bridge(QObject):
        got = pyqtSignal(dict)
        scan = pyqtSignal(dict)
        flipper = pyqtSignal(dict)
        comps = pyqtSignal(dict)
        radios = pyqtSignal(dict)
        gpsmode = pyqtSignal(int)
        railop = pyqtSignal(str)
        again = pyqtSignal()
        report = pyqtSignal(str)
        text = pyqtSignal(str, str)
        frame = pyqtSignal(str)

    def card(title):
        box = QFrame()
        box.setObjectName("card")
        lay = QVBoxLayout(box)
        if title:
            head = QLabel(title.upper())
            head.setObjectName("tag")
            lay.addWidget(head, 0, Qt.AlignmentFlag.AlignLeft)
        return box, lay

    def scrolled(inner):
        """Put a tall page in a scroll area, so it never makes the whole window taller than the screen."""
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setWidget(inner)
        return area

    def text_label():
        lab = QLabel("")
        lab.setStyleSheet("font-size: 13pt;")
        lab.setWordWrap(True)
        return lab

    BOLT = ["....###", "...###.", "..###..", ".###...", "######.", ".######", "...###.", "..###..", ".###...", "##....."]

    class BatteryGauge(QWidget):
        """A chunky pixel battery with corners cut like the icons. While it charges a bright
        sweep runs along the lit blocks and the next block pulses."""

        U = 5                       # screen pixels per icon pixel, drawn without smoothing

        def __init__(self):
            super().__init__()
            self.setFixedSize(50 * self.U + 8, 20 * self.U + 8)
            self.percent = 0
            self.charging = False
            self.colour = QColor("#757575")
            self.phase = 0
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.step)

        def set_state(self, percent, charging, colour):
            self.percent, self.charging, self.colour = percent, charging, QColor(colour)
            if charging and not self.timer.isActive():
                self.timer.start(140)
            elif not charging and self.timer.isActive():
                self.timer.stop()
                self.phase = 0
            self.update()

        def step(self):
            self.phase += 1
            self.update()

        def paintEvent(self, _):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            u = self.U
            ink = self.palette().color(QPalette.ColorRole.WindowText)
            empty = self.palette().color(QPalette.ColorRole.Mid)

            def px(x, y, w=1, h=1, colour=ink):
                p.fillRect(4 + x * u, 4 + y * u, w * u, h * u, colour)

            # A 47 wide body with a two pixel wall, ten bars 3 wide and 1 apart, and two clear pixels all round them.
            px(1, 0, 45, 2)
            px(1, 18, 45, 2)
            px(0, 1, 2, 18)
            px(45, 1, 2, 18)
            px(47, 6, 2, 8)
            px(49, 7, 1, 6)
            blocks = 10
            lit = 0 if self.percent <= 0 else min(blocks, -(-int(self.percent) // 10))
            for i in range(blocks):
                colour = self.colour if i < lit else empty
                if self.charging and i < lit and (i - self.phase) % (lit + 3) == 0:
                    colour = self.colour.lighter(165)
                if self.charging and i == lit and i < blocks and (self.phase // 3) % 2 == 0:
                    colour = QColor(self.colour.red(), self.colour.green(), self.colour.blue(), 130)
                px(4 + i * 4, 4, 3, 12, colour)
            if self.charging:
                # white bolt with a one pixel near black stroke all round it: it reads on green, amber, red and empty bars
                cells = {(x, y) for y, row in enumerate(BOLT) for x, c in enumerate(row) if c == "#"}
                stroke = {(x + dx, y + dy) for x, y in cells for dx in (-1, 0, 1) for dy in (-1, 0, 1)} - cells
                for x, y in stroke:
                    px(20 + x, 5 + y, 1, 1, QColor("#0f1114"))
                for x, y in cells:
                    px(20 + x, 5 + y, 1, 1, QColor("#ffffff"))
            p.end()

    class Switch(QAbstractButton):
        """An I/O slide switch. It shows the real rail state; a click only asks for a change."""

        def __init__(self):
            super().__init__()
            self.setCheckable(True)
            from . import slideswitch
            self.setFixedSize(slideswitch.WIDTH, slideswitch.HEIGHT)
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self._pos = 0.0
            self.anim = QPropertyAnimation(self, b"knob", self)
            self.anim.setDuration(120)
            self.anim.setEasingCurve(QEasingCurve.Type.Linear)

        def get_knob(self):
            return self._pos

        def set_knob(self, v):
            self._pos = v
            self.update()

        knob = pyqtProperty(float, get_knob, set_knob)

        def show_state(self, on):
            """Move to the given state without emitting clicks."""
            self.blockSignals(True)
            self.setChecked(on)
            self.blockSignals(False)
            self.anim.stop()
            self.anim.setStartValue(self._pos)
            self.anim.setEndValue(1.0 if on else 0.0)
            self.anim.start()

        def paintEvent(self, _):
            """The shared pixel slide switch (slideswitch.py), with the knob wherever the animation has it."""
            from . import slideswitch
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            u = slideswitch.UNIT
            alpha = 110 if not self.isEnabled() else 255
            for x, y, w, h, colour in slideswitch.cells(self.isChecked(), self._pos, self.isDown(), self.isEnabled()):
                c = QColor(colour)
                c.setAlpha(alpha)
                p.fillRect(x * u, y * u, w * u, h * u, c)
            if self.isDown():
                p.setPen(QPen(QColor("#e95420"), 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(slideswitch.knob_x(self._pos) * u, slideswitch.KNOB_TOP * u, 12 * u - 1, 12 * u - 1)
            p.end()

    def theme_icon(kind, colour):
        """The sun or moon bitmap in `colour`, scaled by a whole number with no smoothing."""
        rows = {"sun": data.SUN, "moon": data.MOON, "terminal": data.TERMINAL,
                "fullscreen": data.FULLSCREEN}[kind]
        img = QImage(16, 16, QImage.Format.Format_ARGB32)
        img.fill(0)
        for y, row in enumerate(rows):
            for x, cell in enumerate(row):
                if cell == "#":
                    img.setPixelColor(x, y, colour)
        img = img.scaled(32, 32, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.FastTransformation)
        return QIcon(QPixmap.fromImage(img))

    def pixelise_titles(root):
        """Put headings, big numbers and short captions in the pixel font. Body text is left as it is."""
        for lab in root.findChildren(QLabel):
            sheet = lab.styleSheet()
            new = style.pixelise_sheet(sheet, force=style.is_caption(sheet, lab.text()))
            if new != sheet:
                lab.setStyleSheet(new)

    def tab_icon(name):
        icon = QIcon()
        for size in (32, 64):
            path = data.tab_icon_path(name, size)
            if path:
                icon.addFile(path)
        return icon

    def system_is_dark():
        try:
            return QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        except Exception:
            return False

    skin_paths = {}

    def pixel_skin(mode):
        """The pixel corner images for this theme, written once per run. None if they cannot be made."""
        try:
            solid = style.HIGH_CONTRAST                      # high contrast: nothing see-through
            skin_paths[mode] = skin.build(mode, style.TOKENS[mode], skin.cache_dir(),
                                          alpha=255 if solid else None, variant="-hc" if solid else "")
        except Exception:
            skin_paths[mode] = None
        return skin_paths[mode]

    def make_palette(mode):
        app = QApplication.instance()
        app.setStyle("Fusion")
        font = QFont()
        font.setFamilies(style.families())
        font.setPointSize(style.BASE_POINT_SIZE)
        app.setFont(font)
        app.setStyleSheet(style.stylesheet(mode, pixel_skin(mode)))
        pal = QPalette(app.style().standardPalette())
        if mode == "dark":
            for role, colour in ((QPalette.ColorRole.Window, "#1e1e1e"), (QPalette.ColorRole.WindowText, "#e0e0e0"),
                                 (QPalette.ColorRole.Base, "#252525"), (QPalette.ColorRole.AlternateBase, "#2c2c2c"),
                                 (QPalette.ColorRole.Text, "#e0e0e0"), (QPalette.ColorRole.Button, "#333333"),
                                 (QPalette.ColorRole.ButtonText, "#e0e0e0"), (QPalette.ColorRole.Highlight, "#3d7ab8"),
                                 (QPalette.ColorRole.HighlightedText, "#ffffff"), (QPalette.ColorRole.ToolTipBase, "#333333"),
                                 (QPalette.ColorRole.ToolTipText, "#e0e0e0")):
                pal.setColor(role, QColor(colour))
        # The hint inside an empty box ("Filter by name or address"). Qt takes its colour from the palette, not
        # the style sheet, and the stock one could not be seen on the dark themes.
        pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(style.TOKENS[mode]["muted"]))
        app.setPalette(pal)
        return pal

    class Window(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("uConsole AIO")
            self.setObjectName("window")
            self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            self.bridge = Bridge()
            self.settings = data.load_settings()
            self.apply_feature(self.settings.get("accent"))
            style.set_contrast(self.settings.get("high_contrast"))
            pixel.set_contrast(self.settings.get("high_contrast"))
            style.set_font(self.settings.get("font"))
            self.refresh_seconds = int(self.settings.get("refresh_s", REFRESH_MS // 1000))
            reports.install_crash_hook(lambda: bool(self.settings.get("crash_reports")))
            self.bridge.got.connect(self.show_snapshot)
            self.bridge.scan.connect(self.show_scan)
            self.bridge.flipper.connect(self.show_flipper)
            self.bridge.comps.connect(self.show_comps)
            self.bridge.radios.connect(self.show_radios)
            self.bridge.gpsmode.connect(self.got_gps_mode)
            self.bridge.railop.connect(self.rail_done)
            self.rail_busy = False
            self.radio_result = {}
            self.rail_cache = data.load_rail_power()
            self.bridge.frame.connect(self.show_frame)
            self.busy = self.scanning = self.frame_busy = False
            self.follow_up = FollowUp()
            self.bridge.again.connect(self.refresh_marked)
            self.live = False
            self.activity = drawer.ActivityLog()
            self.activity.add("app started")
            self.notifier = notify.build(self, self.settings)
            self.fieldlog = fieldlog.attach(self)             # writes only while Settings > Debug logging is on
            self.warn_watch, self.gps_watch, self.prev_rails = notify.WarningWatcher(), notify.GpsWatcher(), None
            self.mesh_hub = mesh_hub.build_hub(self)
            self.bssid_registry = registry.Registry()
            self.bssid_registry.load()
            self.message = ""
            self.snap = {}
            self.runtime = runtime.Estimator.from_file()     # time left on the battery, fed each snapshot
            self.tmpdir = tempfile.mkdtemp(prefix="ops-flipper-")
            root = QVBoxLayout(self)
            root.setContentsMargins(8, 6, 8, 0)
            root.setSpacing(4)
            self.tabs = FolderTabWidget()
            self.tabs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.tabs.setIconSize(QSize(24, 24))
            root.addWidget(self.tabs, 1)

            self.dash = dashboard.normalize(data.load_settings().get("dashboard"))
            self.flipper_found = False
            self.card_content = {"companions": False, "radios": False,
                                 "gps": False, "lora": False, "sdr": False, "wifi": False}
            self.overview = self.build_overview()
            self.wifi = self.build_wifi()
            self.flip = self.build_flipper()
            self.help = self.build_help()
            self.ant_text = {}
            self.home = home_panel.build_panel(self)
            self.pages = {"Home": self.home, "Network": net_panel.build_panel(self), "Overview": self.overview, "Wi-Fi": self.wifi, "Flipper": self.flip,
                          "Help": self.help}
            from . import apps
            self.pages["Apps"] = apps.build(self)
            from . import wiki
            self.wiki_on = wiki.enabled()
            if self.wiki_on:
                self.pages["Wiki"] = wiki.build(self)
            from . import ghost
            self.ghost_on = ghost.wanted()          # the optional, for-fun add-on: only when it is installed
            if self.ghost_on:
                self.pages[ghost.TAB] = ghost.build(self)
            from . import iga
            self.iga_on = iga.wanted()              # the optional IGA browser: only when it is installed
            if self.iga_on:
                self.pages[iga.TAB] = iga.build(self)   # a placeholder; Qt WebEngine loads when it is first shown
            from . import bt_panel
            self.bt_available = bt_panel.adapter_available
            self.pages["Bluetooth"] = bt_panel.build_page(self)
            from . import everything
            self.pages["Everything"] = everything.build_page(self)   # drives the two pages above; owns no radio
            for name, _ in data.ANTENNA_RAILS:
                if name not in ("SDR", "GPS", "LoRa"):
                    self.pages[name] = self.build_antenna(name)
            self.gps_widget, self.gps_on, self.gps_off = gps_panel.build_panel(self, Switch)
            self.pages["GPS"] = self.gps_widget
            lora_widget, self.lora_on, self.lora_off = lora_panel.build_panel(self, Switch)
            self.pages["LoRa"] = lora_widget
            panel, self.sdr_on, self.sdr_off = sdr_panel.build_panel(self, Switch)
            self.pages["SDR"] = panel
            self.marauder_shown = False
            marauder_widget, self.marauder_on, self.marauder_off = marauder_panel.build_panel(self)
            self.marauder_busy = lambda: marauder_widget.proc is not None
            self.pages["Marauder"] = marauder_widget
            self.flip_shown = False
            self.flip_status = {}
            settings_page, self.settings_refresh = settings_ui.build_settings(self, self.bridge)
            self.pages["Settings"] = settings_page
            for page in self.pages.values():      # pages are not attached to the window yet, so go page by page
                pixelise_titles(page)
                style.crisp_fonts(page)
            pixelise_titles(self)
            self.rebuild_tabs()
            style.crisp_fonts(self)
            self.build_theme_button()
            self.start_gamepad()
            self.drawer = drawer.build_drawer(self)
            self.gps_busy = False
            self.gps_timer = QTimer(self)
            self.gps_timer.timeout.connect(self.watch_gps)
            self.gps_timer.start(10000)
            self.tabs.currentChanged.connect(lambda _=0: self.current_name() == "Settings" and self.settings_refresh())
            self.tabs.currentChanged.connect(lambda _=0: self.sdr_sync())

            bar = QFrame()
            bar.setObjectName("statusbar")
            bl = QHBoxLayout(bar)
            bl.setContentsMargins(6, 0, 6, 0)
            bar.setFixedHeight(24)
            self.foot = QLabel("")
            self.foot.setStyleSheet(style.with_font('color: #9e9e9e; font-family: "Spleen 8x16"; font-size: 16px;'))
            self.stats_label = QLabel("")
            self.stats_label.setTextFormat(Qt.TextFormat.RichText)
            self.stats_label.setStyleSheet(style.with_font('font-family: "Spleen 8x16"; font-size: 16px;'))
            self.stats_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            # Neither label may force the window wider than the screen: they share the bar and clip if they must.
            for lab in (self.foot, self.stats_label):
                lab.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            bl.addWidget(self.foot, 1)
            bl.addWidget(self.stats_label, 4)
            root.addWidget(bar)
            self.status_frame = bar
            self.cpu_sample, self.throttled, self.stat_ticks = None, 0, 0
            self.stat_timer = QTimer(self)
            self.stat_timer.timeout.connect(self.update_stats)
            self.stat_timer.start(2000)
            self.update_stats()
            self.update_footer()
            self.tabs.currentChanged.connect(self.update_footer)

            QShortcut(QKeySequence("q"), self, activated=self.guard(self.close))
            QShortcut(QKeySequence("r"), self, activated=self.guard(self.refresh))
            QShortcut(QKeySequence("s"), self, activated=self.guard(self.start_scan))
            QShortcut(QKeySequence("i"), self, activated=self.guard(self.toggle_live))
            QShortcut(QKeySequence("t"), self, activated=self.guard(self.toggle_theme))
            QShortcut(QKeySequence("x"), self, activated=self.guard(self.toggle_drawer))
            for rail, key in data.RAIL_KEYS.items():
                QShortcut(QKeySequence(key), self, activated=self.guard(lambda r=rail: self.toggle_rail(r)))
                QShortcut(QKeySequence("Shift+" + key.upper()), self, activated=self.guard(lambda r=rail: self.measure_rail(r)))
            for n in range(1, 10):
                QShortcut(QKeySequence(str(n)), self, activated=self.guard(lambda n=n: self.goto(n - 1)))
            for n in range(6):
                QShortcut(QKeySequence(f"F{n + 1}"), self, activated=lambda n=n: self.home_key(n))
            QShortcut(QKeySequence("e"), self, activated=self.guard(lambda: self.home_key(5)))
            for key in ("f", "F11"):
                QShortcut(QKeySequence(key), self, activated=lambda k=key: self.fullscreen_key(k))
            self.tabs.currentChanged.connect(lambda _=0: self.iga_tab_left())
            QShortcut(QKeySequence("Escape"), self, activated=self.back)
            QShortcut(QKeySequence("Tab"), self, activated=lambda: self.step(1))
            QShortcut(QKeySequence("Shift+Tab"), self, activated=lambda: self.step(-1))
            QShortcut(QKeySequence("Backtab"), self, activated=lambda: self.step(-1))

            self.timer = QTimer(self)
            self.timer.timeout.connect(self.refresh)
            self.watch_rails_mark()
            self.timer.start(self.refresh_seconds * 1000)
            self.live_timer = QTimer(self)
            self.live_timer.timeout.connect(self.grab_frame)
            self.refresh()

        # ---- tab construction
        def build_overview(self):
            inner = QWidget()
            page = QScrollArea()
            page.setWidgetResizable(True)
            page.setFrameShape(QFrame.Shape.NoFrame)
            page.setWidget(inner)
            root = self.over_root = QVBoxLayout(inner)
            root.setContentsMargins(0, 0, 0, 0)
            root.setSpacing(8)
            self.bat_box, lay = card("")
            top = QHBoxLayout()
            self.gauge = BatteryGauge()
            top.addWidget(self.gauge)
            info = QVBoxLayout()
            self.bat_big = QLabel("...")
            self.bat_big.setStyleSheet("font-size: 30pt; font-weight: bold;")
            self.chip = QLabel("")
            chip_row = QHBoxLayout()
            chip_row.addWidget(self.chip)
            chip_row.addStretch(1)
            info.addWidget(self.bat_big)
            info.addLayout(chip_row)
            top.addSpacing(16)
            top.addLayout(info)
            top.addStretch(1)
            self.tiles = []
            for _ in range(3):
                tile = QFrame()
                tile.setObjectName("tile")
                tl = QVBoxLayout(tile)
                cap = QLabel("")
                cap.setStyleSheet("font-size: 11pt; color: #9e9e9e;")
                val = QLabel("")
                val.setStyleSheet("font-size: 16pt; font-weight: bold;")
                tl.addWidget(cap)
                tl.addWidget(val)
                tile.setMinimumWidth(120)
                top.addWidget(tile)
                self.tiles.append((tile, cap, val))
            lay.addLayout(top)
            self.bat_text = text_label()
            lay.addWidget(self.bat_text)
            self.rail_box, lay = card("")
            self.rail_box.setObjectName("plain")      # no wrapper: each rail is its own card
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(4)
            self.rail_grid = QGridLayout()
            lay.addLayout(self.rail_grid)
            self.rail_widgets = {}
            for name in data.RAILS:
                label, _icon, blurb = data.RAIL_INFO[name]
                row = QFrame()
                row.setObjectName("row")
                h = QHBoxLayout(row)
                icon = QLabel()
                path = data.rail_icon_path(name, 64)
                if path:
                    icon.setPixmap(QPixmap(path).scaled(56, 56, Qt.AspectRatioMode.KeepAspectRatio,
                                                         Qt.TransformationMode.FastTransformation))
                icon.setFixedWidth(64)
                names = QVBoxLayout()
                title = QLabel(label)
                title.setStyleSheet("font-size: 14pt; font-weight: bold;")
                sub = QLabel(blurb)
                sub.setStyleSheet("font-size: 11pt; color: #9e9e9e;")
                names.addWidget(title)
                names.addWidget(sub)
                power = QLabel("")
                power.setStyleSheet("font-size: 12pt; color: #9e9e9e;")
                power.setWordWrap(True)
                meas = QPushButton("Measure")
                meas.setStyleSheet("font-size: 12pt; padding: 4px 10px;")
                meas.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                meas.clicked.connect(lambda _=False, n=name: self.measure_rail(n))
                switch = Switch()
                switch.setToolTip(f"{label} power ({data.RAIL_KEYS[name]})")
                switch.clicked.connect(lambda _=False, n=name: self.toggle_rail(n))
                h.addWidget(icon)
                h.addLayout(names, 2)
                h.addWidget(power, 3)
                h.addWidget(meas)
                h.addWidget(switch)
                self.rail_grid.addWidget(row, 0, 0)
                self.rail_widgets[name] = (row, switch, power, meas)
            self.rail_total = QLabel("")
            self.rail_total.setStyleSheet("font-size: 13pt;")
            self.rail_total.hide()                    # no dead space reserved when there is nothing to measure yet
            lay.addWidget(self.rail_total)
            self.rail_note = QLabel("")
            self.rail_note.setStyleSheet("font-size: 12pt; color: #9e9e9e;")
            self.rail_note.setWordWrap(True)
            self.rail_note.hide()
            lay.addWidget(self.rail_note)
            self.comp_box, lay = card("Companions")
            self.comp_text = text_label()
            lay.addWidget(self.comp_text)
            self.flip_heading = QLabel("Flipper")
            self.flip_heading.setStyleSheet("font-size: 11pt; color: #9e9e9e; margin-top: 6px;")
            lay.addWidget(self.flip_heading)
            self.flip_card_text = text_label()
            self.flip_card_text.setTextFormat(Qt.TextFormat.RichText)
            self.flip_card_text.setStyleSheet("font-size: 11pt;")
            lay.addWidget(self.flip_card_text)
            self.flip_heading.hide()
            self.flip_card_text.hide()
            self.radio_box, lay = card("Wi-Fi radios")
            self.radio_text = text_label()
            lay.addWidget(self.radio_text)
            self.sys_box, lay = card("System")
            self.sys_text = text_label()
            lay.addWidget(self.sys_text)
            self.ant_cards = {}
            for key, title in (("gps", "GPS"), ("lora", "LoRa"), ("sdr", "SDR"), ("wifi", "Last Wi-Fi scan")):
                box, lay = card(title)
                label = text_label()
                lay.addWidget(label)
                self.ant_cards[key] = (box, label)
            self.cards = {"battery": self.bat_box, "rails": self.rail_box,
                          "companions": self.comp_box, "radios": self.radio_box, "system": self.sys_box,
                          **{k: v[0] for k, v in self.ant_cards.items()}}
            for box in self.cards.values():                 # cards not shown yet are not attached to the page
                pixelise_titles(box)
            self.custom_btn = QPushButton("Customise this dashboard")
            self.custom_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.custom_btn.setToolTip("Choose which cards, rails and status bar items show")
            self.custom_btn.clicked.connect(lambda: self.goto_settings("Dashboard"))
            self.apply_dashboard()
            return page

        def build_antenna(self, name):
            page = QWidget()
            root = QVBoxLayout(page)
            box, lay = card(name)
            label = text_label()
            lay.addWidget(label)
            root.addWidget(box)
            root.addStretch(1)
            self.ant_text[name] = label
            return page

        def build_theme_button(self):
            corner = QWidget()
            row = QHBoxLayout(corner)
            row.setContentsMargins(8, 0, 6, 0)
            row.setSpacing(10)
            self.term_btn = QPushButton("")
            self.term_btn.setObjectName("iconbtn")
            self.term_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.term_btn.setIconSize(QSize(32, 32))
            self.term_btn.setFixedSize(48, 44)
            self.term_btn.clicked.connect(self.toggle_drawer)
            self.full_btn = QPushButton("")
            self.full_btn.setObjectName("iconbtn")
            self.full_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.full_btn.setIconSize(QSize(32, 32))
            self.full_btn.setFixedSize(48, 44)
            self.full_btn.clicked.connect(self.toggle_fullscreen)
            self.theme_btn = QPushButton("")
            self.theme_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.theme_btn.setIconSize(QSize(32, 32))
            self.theme_btn.setFixedSize(48, 44)
            self.theme_btn.setObjectName("iconbtn")
            self.theme_btn.clicked.connect(self.toggle_theme)
            row.addWidget(self.term_btn)
            row.addWidget(self.full_btn)
            row.addWidget(self.theme_btn)
            self.tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)
            self.saved_theme = data.load_settings().get("theme")
            try:
                QApplication.styleHints().colorSchemeChanged.connect(lambda *_: self.set_theme())
            except Exception:
                pass
            self.set_theme()

        def set_theme(self):
            self.theme = data.resolve_theme(self.saved_theme, system_is_dark())
            pal = make_palette(self.theme)
            # Widgets that carry their own style sheet ignore an application palette change,
            # so hand the palette to every page and child explicitly.
            for root in [self] + list(self.pages.values()):
                root.setPalette(pal)
                for child in root.findChildren(QWidget):
                    child.setPalette(pal)
                    sheet = child.styleSheet()
                    if sheet:                                   # helper-text grey that suits this theme
                        swapped = style.muted_sheet(sheet, self.theme)
                        if swapped != sheet:
                            child.setStyleSheet(swapped)
            wiki_page = self.pages.get("Wiki")
            if wiki_page is not None and hasattr(wiki_page, "recolour"):
                wiki_page.recolour()                        # its page carries its colours inside it
            iga_page = self.pages.get("IGA") if getattr(self, "iga_on", False) else None
            if iga_page is not None and hasattr(iga_page, "recolour"):
                iga_page.recolour()                         # the app's own pages in IGA, never a site's
            can = handoff.has_display() and handoff.terminal_argv(handoff.install_dir()) is not None
            self.term_btn.setVisible(True)
            self.term_btn.setIcon(theme_icon("terminal", pal.color(QPalette.ColorRole.ButtonText)))
            self.term_btn.setToolTip("Terminal drawer (x): recent activity and a shell, without leaving the app")
            self.full_btn.setIcon(theme_icon("fullscreen", pal.color(QPalette.ColorRole.ButtonText)))
            self.full_btn.setToolTip("Full screen on or off (f or F11)")
            going = "light" if self.theme == "dark" else "dark"
            self.theme_btn.setIcon(theme_icon("sun" if going == "light" else "moon",
                                              pal.color(QPalette.ColorRole.ButtonText)))
            self.theme_btn.setToolTip(f"Switch to {going} mode (t)")
            self.theme_btn.setAccessibleName(f"Switch to {going} mode")

        def open_terminal(self):
            """Swap to the terminal console: quit first, so the dongle and ports are free, and let a
            detached shell start the terminal a moment later."""
            argv = handoff.terminal_argv(handoff.install_dir())
            if not (handoff.has_display() and argv):
                return
            if handoff.launch_detached(handoff.delayed(argv)):
                self.close()

        def toggle_theme(self):
            self.saved_theme = "light" if self.theme == "dark" else "dark"
            data.save_settings(dict(data.load_settings(), theme=self.saved_theme))
            self.set_theme()

        def build_wifi(self):
            self.wifi_panel = wifi_panel.build_panel(self, Switch)
            return self.wifi_panel

        def build_flipper(self):
            self.flip_panel = flipper_panel.build_panel(self, Switch)
            self.flip_img = self.flip_panel.img
            self.flip_text = self.flip_panel.text
            return scrolled(self.flip_panel)

        def build_help(self):
            page, self.help_refresh = helpview.build(self, card)
            self.pad_label = QLabel("")                       # the gamepad status, read by the Help companions rows
            self.pad_label.hide()
            return page

        def rebuild_tabs(self):
            """Overview, Wi-Fi, a tab per powered antenna, Flipper (only if detected), Help.

            Only the tabs that appeared or went away are touched, with repainting held off, so
            switching a rail on or off does not make the whole bar flash."""
            names = data.tab_names(self.snap, self.flip_shown, self.marauder_shown,
                                  self.settings.get("home_tab") is not False, data.has_ethernet(),
                                  self.bt_available(), everything=True,
                                  wiki=getattr(self, "wiki_on", False), ghost=getattr(self, "ghost_on", False),
                                  iga=getattr(self, "iga_on", False),
                                  apps=bool(self.settings.get("apps_tab") or self.settings.get("games_tab")))
            if getattr(self, "names", None) == names:
                self.refresh_help()
                return
            current = self.current_name() if self.tabs.count() else names[0]    # first run: the first tab, Home
            self.tabs.setUpdatesEnabled(False)
            self.tabs.blockSignals(True)
            try:
                for i in range(self.tabs.count() - 1, -1, -1):
                    if self.tabs.tabText(i) not in names:
                        self.tabs.removeTab(i)
                for pos, name in enumerate(names):
                    if pos < self.tabs.count() and self.tabs.tabText(pos) == name:
                        continue
                    self.tabs.insertTab(pos, self.pages[name], tab_icon(name), name)
                self.names = names
                self.tabs.setCurrentIndex(names.index(current) if current in names else 0)
            finally:
                self.tabs.blockSignals(False)
                self.tabs.setUpdatesEnabled(True)
            self.refresh_help()
            self.sdr_sync()

        def sdr_sync(self):
            """The SDR engine owns the dongle, so it only runs while its tab is showing."""
            if self.current_name() == "SDR" and "SDR" in self.names:
                self.sdr_on()
            else:
                self.sdr_off()
            if self.current_name() == "GPS" and "GPS" in self.names:
                self.gps_on()
            else:
                self.gps_off()
            if self.current_name() == "LoRa" and "LoRa" in self.names:
                self.lora_on()
            else:
                self.lora_off()
            if self.current_name() != "Marauder":
                self.marauder_off()

        @staticmethod
        def set_style(widget, sheet):
            """Only restyle when the style really changed: restyling every refresh makes widgets flicker."""
            sheet = style.pixelise_sheet(sheet)
            if widget.styleSheet() != sheet:
                widget.setStyleSheet(sheet)
                style.crisp_fonts(widget)

        def say(self, text, seconds=6):
            """A short message in the status bar, in place of the key hints, so nothing in the page moves."""
            self.activity.add(text)
            self.message = text
            self.update_footer()
            QTimer.singleShot(seconds * 1000, lambda: self.clear_say(text))

        def clear_say(self, text):
            if self.message == text:
                self.message = ""
                self.update_footer()

        def refresh_help(self):
            self.help_refresh(self.names, getattr(self, "comp_result", {}), self.snap, self.pad_label.text())
            self.update_footer()

        # ---- keys
        # ---- the uConsole's own buttons (see controls.py)
        def set_contrast(self, on):
            """High contrast on or off, now and for next time."""
            self.set_flag("high_contrast", on)
            style.set_contrast(on)
            pixel.set_contrast(on)                                  # the hand-drawn screens have their own colours
            self.set_theme()
            front = self.pages.get("Home")
            if front is not None and hasattr(front, "refresh_palette"):
                front.refresh_palette()
            for page in self.pages.values():
                page.update()
            self.update()

        def set_background(self, choice):
            """The window's background: the dotted gradient, one flat colour or solid black."""
            self.settings["background"] = style.background(choice)
            data.save_settings(dict(data.load_settings(), background=self.settings["background"]))
            self.update()

        def set_font_family(self, family):
            """A font from this computer in place of the app's own; an empty name goes back to the app's own."""
            self.settings["font"] = style.set_font(family) or ""
            data.save_settings(dict(data.load_settings(), font=self.settings["font"]))
            self.set_theme()
            style.crisp_fonts(self)

        def peek_tick(self):
            """In full screen the taskbar is covered: resting the pointer on the bottom edge steps out of full
            screen so it shows, and moving back up into the app steps back in (startup.peek_step)."""
            from PyQt6.QtGui import QCursor
            if not self.isVisible() or not (self.isFullScreen() or self.peek["peeking"]):
                self.peek["count"] = 0
                return
            pos = self.mapFromGlobal(QCursor.pos())
            self.peek, action = startup.peek_step(self.peek, pos.y(), self.height(), self.rect().contains(pos))
            if action == "peek":
                self.showMaximized()
            elif action == "return":
                self.showFullScreen()

        def fullscreen_key(self, key):
            """f and F11. On the IGA tab F11 steps back out of IGA's full-screen states (iga.f11_action)."""
            if key == "F11" and self.iga_showing():
                from . import iga
                self.iga_fullscreen(iga.f11_action(getattr(self, "iga_fs", 0)))
            else:
                self.toggle_fullscreen()

        def iga_showing(self):
            return bool(getattr(self, "iga_on", False) and self.tabs.count() and self.current_name() == "IGA")

        def iga_fullscreen(self, action):
            """The IGA tab's three full-screen states (iga.next_fullscreen): normal, the browser full screen with
            the app's tabs and status bar hidden, and the page alone."""
            from . import iga
            old = getattr(self, "iga_fs", 0)
            if action == "page_on" and old != 2:
                self.iga_fs_before = old
            self.apply_iga_fs(iga.next_fullscreen(old, action, getattr(self, "iga_fs_before", 0)))

        def apply_iga_fs(self, state, keep_window=False):
            from . import iga
            view = iga.fullscreen_view(state)
            old = getattr(self, "iga_fs", 0)
            if old == 0 and state > 0:
                self.iga_was_full = self.isFullScreen()
            self.iga_fs = state
            self.tabs.tabBar().setVisible(view["app_bars"])
            corner = self.tabs.cornerWidget(Qt.Corner.TopRightCorner)
            if corner is not None:
                corner.setVisible(view["app_bars"])
            if getattr(self, "status_frame", None) is not None:
                self.status_frame.setVisible(view["app_bars"])
            page = self.pages.get("IGA")
            if page is not None and hasattr(page, "set_row_hidden"):
                page.set_row_hidden(not view["iga_row"])
            self.peek = {"peeking": False, "count": 0}
            if view["window_full"] and not self.isFullScreen():
                self.showFullScreen()
            elif not view["window_full"] and not keep_window and old > 0 and self.isFullScreen() and \
                    not getattr(self, "iga_was_full", False):
                self.showMaximized()

        def iga_tab_left(self):
            """Another tab was chosen while IGA was in one of its full-screen states: the app's tabs and status bar
            come back; the window stays as it is."""
            if getattr(self, "iga_fs", 0) and not self.iga_showing():
                self.apply_iga_fs(0, keep_window=True)

        def toggle_fullscreen(self):
            if self.iga_showing():                              # on the IGA tab the control cycles its states
                self.iga_fullscreen("toggle")
                return
            self.peek = {"peeking": False, "count": 0}          # asked for by hand: no stepping back in
            if self.isFullScreen():
                self.showMaximized()
            else:
                self.showFullScreen()

        def notify_user(self, source, title, body="", level="info"):
            """Raise a notification: a toast, the activity log, and the desktop if you turned that on."""
            return self.notifier.add(source, title, body, level)

        def set_notification(self, key, value):
            self.settings["notifications"] = dict(self.settings.get("notifications") or {}, **{key: bool(value)})
            data.save_settings(dict(data.load_settings(), notifications=self.settings["notifications"]))
            self.notifier.cfg = notify.config(self.settings)
            self.mesh_hub.sync()

        def watch_gps(self):
            """Every ten seconds, while the GPS rail is on and you want to hear about it, ask gpsd for the fix."""
            rails = (self.snap or {}).get("rails") or {}
            if self.gps_busy or rails.get("GPS") != "on" or not self.notifier.cfg.get("gps"):
                if rails.get("GPS") != "on":
                    self.gps_watch.had = None
                return
            self.gps_busy = True

            def work():
                try:
                    from . import pixel, position
                    mode, _sats = pixel.parse_gpsd(position.gpsd_lines())
                    self.bridge.gpsmode.emit(-1 if mode is None else mode)
                finally:
                    self.gps_busy = False

            threading.Thread(target=work, daemon=True).start()

        def got_gps_mode(self, mode):
            event = self.gps_watch.update(None if mode < 0 else mode)
            if event:
                self.notify_user("gps", *event)

        def toggle_drawer(self):
            self.drawer.toggle()

        def typing(self):
            """True while a text box has the keyboard, so single-letter shortcuts leave it alone."""
            w = QApplication.focusWidget()
            return isinstance(w, (QLineEdit, QPlainTextEdit, QTextEdit)) or self.in_typing_zone(w)

        def in_typing_zone(self, w=None):
            """True when `w` (or the widget with the keyboard) is inside a widget marked as a place where the person
            types, such as a web page: the property typing_zone set on it or on one of its parents."""
            w = QApplication.focusWidget() if w is None else w
            while w is not None and hasattr(w, "property"):
                if w.property("typing_zone"):
                    return True
                w = w.parentWidget() if hasattr(w, "parentWidget") else None
            return False

        def guard(self, fn):
            """A single-letter shortcut does nothing while a box is being typed in, while the screensaver is
            showing, or in the moment after the key that took it away."""
            return lambda *a: None if self.typing() or self.saver_blocks() else fn()

        # ---- the screensaver (screensaver.py)
        def saver_showing(self):
            saver = getattr(self, "saver", None)
            return saver is not None and saver.isVisible()

        def saver_blocks(self):
            return self.saver_showing() or time.monotonic() - getattr(self, "saver_woke", 0.0) < 0.4

        def saver_start(self, kind=None):
            """Cover the window with a screensaver: the chosen one, or `kind` to try another."""
            self.nav_drop()
            self.saver.start(kind or self.settings.get("saver_kind"))

        def saver_stop(self):
            if self.saver_showing():
                self.saver.stop()
                self.saver_woke = time.monotonic()
            self.last_input = time.monotonic()

        def saver_wake(self):
            """Someone touched something. True when that took the screensaver away, so the touch is used up."""
            if self.saver_showing():
                self.saver_stop()
                return True
            self.last_input = time.monotonic()
            return False

        def idle_tick(self):
            """Every few seconds: has the pointer moved, and has nothing been touched for long enough?"""
            from PyQt6.QtGui import QCursor
            pos = QCursor.pos()
            here = (pos.x(), pos.y())
            if here != getattr(self, "idle_pos", None):
                self.idle_pos = here
                if not self.saver_showing():
                    self.last_input = time.monotonic()
            if self.saver_showing() or not self.isVisible() or self.isMinimized() or \
                    QApplication.activeWindow() is not self or QApplication.activeModalWidget() is not None or \
                    QApplication.activePopupWidget() is not None:
                return
            if screensaver.due(time.monotonic() - self.last_input, self.settings.get("saver_on"),
                               self.settings.get("saver_minutes")):
                self.saver_start()

        def set_saver(self, key, value):
            """Remember a screensaver setting: saver_on, saver_kind or saver_minutes."""
            self.settings[key] = value
            data.save_settings(dict(data.load_settings(), **{key: value}))
            self.last_input = time.monotonic()

        def back(self):
            """One level back at a time (the order is focusnav.back_step): close the terminal drawer, leave a
            wiki page for its list, leave the page for the row of tabs, leave full screen, go to the first tab."""
            iga_page = self.pages.get("IGA") if getattr(self, "iga_on", False) else None
            web = iga_page.escape_plan() if iga_page is not None and self.current_name() == "IGA" else None
            step = focusnav.back_step(self.drawer.isVisible(),
                                      self.current_name() == "Wiki" and self.pages["Wiki"].page.hasFocus(),
                                      self.nav_on and self.nav_in_page(), self.isFullScreen(),
                                      not self.names or self.current_name() == self.names[0], web=web)
            if step == "drawer":
                self.drawer.close_drawer()
            elif step == "web":                     # one IGA step; IGA never changes which tab is selected
                iga_page.escape_do(web)
                if web == "tabs":
                    self.nav_to(self.tabs.tabBar())
            elif step == "reading":
                self.pages["Wiki"].back()
            elif step == "tabs":
                self.nav_to(self.tabs.tabBar())
            elif step == "window":
                self.showMaximized()
            elif step == "first":
                self.goto(0)

        def primary(self):
            """The main action of the current tab, for the A button."""
            name = self.current_name()
            if name == "Wi-Fi":
                self.start_scan()
            elif name == "Marauder":
                self.pages["Marauder"].toggle()
            elif name == "Flipper":
                self.toggle_live()
            elif name == "Wiki":
                self.pages["Wiki"].main_action()        # to the page to read it, or back to the list
            elif name == "Apps":
                self.pages["Apps"].main_action()        # open the chosen program
            elif name == "GPS":
                live = self.pages["GPS"].live
                if live.save_b.isEnabled():
                    live.save()
            else:
                self.refresh()

        # ---- moving round the whole app with the D-pad or the arrow keys (the rules are in focusnav.py)
        def nav_widgets(self):
            """Every control the highlight can land on now: the row of tabs, the buttons beside it and what is
            showing on this tab, or the terminal drawer while that is open. A control's own inner parts (the
            rows of a list, the box inside a drop-down, the arrows of a row of tabs) are not separate stops."""
            from PyQt6.QtWidgets import QAbstractSlider, QAbstractSpinBox, QScrollBar, QTabBar
            if self.drawer.isVisible():
                roots = [self.drawer]
            else:
                roots = [self.tabs.tabBar(), self.tabs.cornerWidget(Qt.Corner.TopRightCorner),
                         self.pages.get(self.current_name())]
            kinds = (QAbstractButton, QComboBox, QLineEdit, QAbstractItemView, QAbstractSlider, QAbstractSpinBox,
                     QTextEdit, QPlainTextEdit, QTabBar)
            whole = (QAbstractItemView, QComboBox, QAbstractSpinBox, QTabBar, QTextEdit, QPlainTextEdit)
            found = []
            for root in roots:
                if root is None or not root.isVisible():
                    continue
                for w in [root] + root.findChildren(QWidget):
                    if not w.isVisible() or not w.isEnabled() or w.width() < 6 or w.height() < 6 or \
                            isinstance(w, QScrollBar):
                        continue
                    if not (isinstance(w, kinds) or hasattr(w, "nav_move") or focusnav.custom_clickable(type(w))):
                        continue
                    inside, p = False, w.parentWidget()
                    while p is not None and p is not self:
                        if isinstance(p, whole) or hasattr(p, "nav_move"):
                            inside = True
                            break
                        p = p.parentWidget()
                    if not inside:
                        found.append(w)
            return found

        def nav_rect(self, w):
            """A control's rectangle in the window's coordinates. A hand-drawn control says which part of
            itself is highlighted."""
            inner = w.nav_rect() if hasattr(w, "nav_rect") else None
            r = inner if inner is not None else w.rect()
            a, b = w.mapToGlobal(r.topLeft()), self.mapToGlobal(self.rect().topLeft())
            return (a.x() - b.x(), a.y() - b.y(), r.width(), r.height())

        def nav_focus(self, widgets=None):
            """(the control that has the highlight, or None; every control it could move to)."""
            widgets = self.nav_widgets() if widgets is None else widgets
            f = QApplication.focusWidget()
            while f is not None and f not in widgets:
                f = f.parentWidget()
            return f, widgets

        def nav_kind(self, w):
            """(what sort of control this is to an arrow key, whether it is at its first row, at its last)."""
            from PyQt6.QtWidgets import QAbstractSlider, QAbstractSpinBox, QTabBar, QTreeWidget
            if hasattr(w, "nav_move"):                         # it moves its own highlight; at its edge, on we go
                return "other", True, True
            if isinstance(w, QTreeWidget):
                def more(step):
                    item = step(w.currentItem()) if w.currentItem() is not None else None
                    while item is not None:
                        if item.flags() & Qt.ItemFlag.ItemIsSelectable:
                            return True
                        item = step(item)
                    return False
                return "list", not more(w.itemAbove), not more(w.itemBelow)
            if isinstance(w, QAbstractItemView):
                model, index = w.model(), w.currentIndex()
                rows = model.rowCount(w.rootIndex()) if model is not None else 0
                row = index.row() if index.isValid() else -1
                return "list", row <= 0, row >= rows - 1
            if isinstance(w, (QTextEdit, QPlainTextEdit)):
                bar = w.verticalScrollBar()
                return "text", bar.value() <= bar.minimum(), bar.value() >= bar.maximum()
            if isinstance(w, QLineEdit):
                return ("history" if hasattr(w, "history") else "line"), True, True
            if isinstance(w, QAbstractSlider):
                return "slider", True, True
            if isinstance(w, QAbstractSpinBox):
                return "spin", True, True
            if isinstance(w, QTabBar):
                return "tabs", w.currentIndex() <= 0, w.currentIndex() >= w.count() - 1
            return "other", True, True

        def nav_into_page(self, widgets):
            """Where down from the row of tabs lands: where the highlight last was on this tab, or the first
            control a person would read."""
            page = self.pages.get(self.current_name())
            mine = [w for w in widgets if page is not None and (w is page or page.isAncestorOf(w))]
            last = self.nav_last.get(self.current_name())
            if last in mine:
                return last
            return focusnav.first_in_reading_order([(w, self.nav_rect(w)) for w in mine])

        def nav_to(self, w):
            """Give a control the highlight, and bring it into view if it is in a list that scrolls."""
            from PyQt6.QtWidgets import QScrollArea
            if w.focusPolicy() == Qt.FocusPolicy.NoFocus:      # made so that letter shortcuts kept working
                self.nav_promoted[w] = Qt.FocusPolicy.NoFocus  # nav_drop puts it back
                w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            w.setFocus(Qt.FocusReason.TabFocusReason)
            if hasattr(w, "nav_enter"):
                w.nav_enter()
            area = w.parentWidget()
            while area is not None and not isinstance(area, QScrollArea):
                area = area.parentWidget()
            if area is not None:
                area.ensureWidgetVisible(w, 30, 40)
            page = self.pages.get(self.current_name())
            if page is not None and (w is page or page.isAncestorOf(w)):
                self.nav_last[self.current_name()] = w
            self.nav_on = True
            self.ring.follow()

        def nav_drop(self):
            """The pointer is in use: the highlight goes, and with it everything that let A, Enter or Space
            reach a control. A control that only took the keyboard for the highlight's sake stops taking it,
            and gives it up if it has it, so nothing can be pressed that is not shown as highlighted."""
            self.nav_on = False
            self.ring.drop()
            focus = QApplication.focusWidget()
            promoted, self.nav_promoted = self.nav_promoted, {}
            for w, policy in promoted.items():
                try:
                    w.setFocusPolicy(policy)
                except RuntimeError:
                    continue                                   # the control has been deleted since
                if w is focus:
                    self.setFocus()

        def nav_tab_changed(self, *_):
            """Another tab was opened by a shortcut while the highlight was on a control of the old tab. Qt
            would hand the keyboard to whatever comes next; the highlight goes to the row of tabs instead."""
            if self.nav_on and QApplication.focusWidget() is not self.tabs.tabBar():
                self.nav_to(self.tabs.tabBar())

        def nav(self, direction, from_keyboard=False):
            """Move the highlight one step. True when the arrow was used up here; False when the highlighted
            control should have the key itself (only asked for a real key press, which Qt then delivers)."""
            f, widgets = self.nav_focus()
            bar = self.tabs.tabBar()
            if f is None:                                      # nothing highlighted yet: start somewhere sensible
                if self.drawer.isVisible():                    # the row of tabs is behind the drawer
                    target = focusnav.first_in_reading_order([(w, self.nav_rect(w)) for w in widgets])
                else:
                    target = self.nav_into_page(widgets) if direction == "down" else None
                self.nav_to(target if target is not None else bar)
                return True
            self.nav_on = True
            if hasattr(f, "nav_move") and f.nav_move(direction):
                self.ring.follow()
                return True
            kind, at_start, at_end = self.nav_kind(f)
            if kind in ("line", "history"):
                if focusnav.typing_keeps(kind, direction, from_keyboard, f.text()):
                    return False                               # the box has the key: see typing_keeps
                kind = "line"
            if focusnav.passes_through(kind, direction, at_start, at_end):
                self.ring.follow()
                if from_keyboard:
                    return False
                key = {"up": Qt.Key.Key_Up, "down": Qt.Key.Key_Down, "left": Qt.Key.Key_Left,
                       "right": Qt.Key.Key_Right}[direction]
                self.nav_sending = True
                try:
                    for press in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                        QApplication.sendEvent(f, QKeyEvent(press, key, Qt.KeyboardModifier.NoModifier))
                finally:
                    self.nav_sending = False
                return True
            if f is bar and direction == "down":
                target = self.nav_into_page(widgets)
            else:
                here = self.nav_rect(f)
                target = focusnav.next_in_direction(here, [(w, self.nav_rect(w)) for w in widgets if w is not f],
                                                    direction)
            if target is not None:
                self.nav_to(target)
            return True

        def nav_press(self, from_keyboard=False):
            """A, or Enter: press what is highlighted. True when that was done here; False when the control
            should have the key itself (a list row, a box being typed in), or there is nothing highlighted."""
            from PyQt6.QtCore import QPointF
            from PyQt6.QtGui import QMouseEvent
            from PyQt6.QtWidgets import QTabBar
            if not self.nav_on:                                # no highlight showing: nothing here to press
                return False
            f, _ = self.nav_focus()
            if f is None:
                return False
            if hasattr(f, "nav_activate"):
                f.nav_activate()
                self.ring.follow()
                return True
            if isinstance(f, QAbstractButton):
                f.click()
                return True
            if isinstance(f, QComboBox):
                f.showPopup()
                return True
            if isinstance(f, (QTabBar, QAbstractItemView, QTextEdit, QPlainTextEdit, QLineEdit)):
                return False                                   # on the row of tabs A is still the tab's main action
            if focusnav.custom_clickable(type(f)):                 # a hand-drawn control: the same as a click on it
                at = QPointF(f.rect().center())
                self.nav_sending = True                            # not the pointer: the highlight stays
                try:
                    for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
                        QApplication.sendEvent(f, QMouseEvent(kind, at, f.mapToGlobal(at),
                                                              Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                                                              Qt.KeyboardModifier.NoModifier))
                finally:
                    self.nav_sending = False
                return True
            return False

        def nav_ring_rect(self):
            """The highlighted control's rectangle for the frame, or None when the frame should not show."""
            if not self.nav_on or QApplication.activeWindow() is not self:
                return None
            f, _ = self.nav_focus()
            return self.nav_rect(f) if f is not None and f.isVisible() else None

        def nav_in_page(self):
            f, _ = self.nav_focus()
            page = self.pages.get(self.current_name())
            return f is not None and page is not None and (f is page or page.isAncestorOf(f))

        def dpad(self, direction):
            self.nav(direction)

        def pad_to_dialog(self, action):
            """While a question is on the screen the gamepad answers it: the D-pad moves between its buttons,
            A presses the one that is highlighted and B is Esc."""
            box = QApplication.activeModalWidget() or QApplication.activePopupWidget()
            if box is None:
                return False
            key = {"up": Qt.Key.Key_Up, "down": Qt.Key.Key_Down, "left": Qt.Key.Key_Left, "right": Qt.Key.Key_Right,
                   "a": Qt.Key.Key_Return, "b": Qt.Key.Key_Escape}.get(action)
            if key is not None:
                target = QApplication.focusWidget() or box
                self.nav_sending = True
                try:
                    for press in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                        QApplication.sendEvent(target, QKeyEvent(press, key, Qt.KeyboardModifier.NoModifier))
                finally:
                    self.nav_sending = False
            return True

        def act(self, action):
            if self.saver_wake():                       # the button that wakes the app does nothing else
                return
            if self.pad_to_dialog(action):
                return
            if action in ("left", "right", "up", "down"):
                self.dpad(action)
            elif action == "a":
                if not self.nav_press():                # nothing to press here: the tab's own main action
                    self.primary()
            elif action == "b":
                self.back()
            elif action == "x":
                self.refresh()
            elif action == "y":
                self.toggle_fullscreen()

        def eventFilter(self, obj, event):
            """The arrow keys move the highlight and Enter presses it (see nav); the trackball's right button
            goes back and its middle button refreshes."""
            kind = event.type()
            if kind in (QEvent.Type.KeyPress, QEvent.Type.MouseButtonPress, QEvent.Type.Wheel,
                        QEvent.Type.TouchBegin, QEvent.Type.ShortcutOverride):
                if self.saver_showing():
                    # The touch that takes the screensaver away does nothing else. A shortcut is claimed here
                    # so that it arrives as an ordinary key press, which is then used up the same way.
                    if kind == QEvent.Type.ShortcutOverride:
                        event.accept()
                    else:
                        self.saver_stop()
                    return True
                self.last_input = time.monotonic()
            if kind in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress) and getattr(self, "iga_on", False) and \
                    int(event.key()) in (int(Qt.Key.Key_Tab), int(Qt.Key.Key_Backtab)) and \
                    self.tabs.count() and self.current_name() == "IGA":
                # Ctrl+Tab and Ctrl+Shift+Tab are IGA's own (its pages) while its tab shows, except from the row of
                # tabs; the main window's tab switching would otherwise take them (iga.tab_key_owner).
                from . import iga
                focus = QApplication.focusWidget()
                where = "app_tabs" if focus is self.tabs.tabBar() else ("page" if self.in_typing_zone() else "")
                mods = event.modifiers()
                owner = iga.tab_key_owner(True, where, "Backtab" if int(event.key()) == int(Qt.Key.Key_Backtab)
                                          else "Tab", bool(mods & Qt.KeyboardModifier.ControlModifier),
                                          bool(mods & Qt.KeyboardModifier.ShiftModifier))
                if owner in ("next", "previous"):
                    event.accept()
                    if kind == QEvent.Type.KeyPress:
                        page = self.pages.get("IGA")
                        if page is not None and hasattr(page, "key_act"):
                            page.key_act(owner)
                    return True
            if kind == QEvent.Type.ShortcutOverride and getattr(self, "iga_on", False) and self.in_typing_zone():
                # A web page has the keyboard: letters, numbers and Tab are the page's, so claim them from the app's
                # shortcuts and they arrive as ordinary key presses. Escape, F-keys and Ctrl or Alt keys still work.
                from . import iga
                k, mods = int(event.key()), event.modifiers()
                if iga.page_keeps(k == int(Qt.Key.Key_Escape), int(Qt.Key.Key_F1) <= k <= int(Qt.Key.Key_F35),
                                  bool(mods & Qt.KeyboardModifier.ControlModifier),
                                  bool(mods & Qt.KeyboardModifier.AltModifier),
                                  bool(mods & Qt.KeyboardModifier.MetaModifier)):
                    event.accept()
                    return True
            if kind == QEvent.Type.KeyPress and not self.nav_sending and QApplication.activeWindow() is self and \
                    QApplication.activePopupWidget() is None and QApplication.activeModalWidget() is None and \
                    obj is (QApplication.focusWidget() or self) and not self.in_typing_zone() and \
                    event.modifiers() in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.KeypadModifier):
                direction = {Qt.Key.Key_Up: "up", Qt.Key.Key_Down: "down", Qt.Key.Key_Left: "left",
                             Qt.Key.Key_Right: "right"}.get(event.key())
                if direction is not None:
                    if self.nav(direction, from_keyboard=True):
                        return True
                elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.nav_on and \
                        not event.isAutoRepeat() and self.nav_press(from_keyboard=True):
                    return True                         # a held key presses once, as a button does
            elif kind == QEvent.Type.MouseButtonPress and self.nav_on and not self.nav_sending and \
                    ((obj.isWidgetType() and obj.window() is self) or obj is self.windowHandle()):
                self.nav_drop()                         # seen at the window first, before Qt moves the keyboard
            if event.type() == QEvent.Type.MouseButtonRelease and obj.isWidgetType() and \
                    obj.window() is self and not isinstance(obj, (QLineEdit, QPlainTextEdit, QComboBox)) and \
                    not self.in_typing_zone(obj):
                if event.button() == Qt.MouseButton.RightButton:
                    self.back()
                    return True
                if event.button() == Qt.MouseButton.MiddleButton:
                    self.refresh()
                    return True
            return False

        def start_gamepad(self):
            self.pad_fd, self.pad_left, self.pad_dir = controls.open_device(), b"", None
            self.pad_timer = QTimer(self)
            self.pad_timer.timeout.connect(self.pad_repeat)
            if self.pad_fd is not None:
                self.pad_notifier = QSocketNotifier(self.pad_fd, QSocketNotifier.Type.Read, self)
                self.pad_notifier.activated.connect(self.pad_read)
            self.pad_text = "Gamepad: none found." if self.pad_fd is None else "Gamepad: waiting for a button."
            self.pad_label.setText(self.pad_text)
            self.nav_on, self.nav_sending, self.nav_last, self.nav_promoted = False, False, {}, {}
            # The window is the size the desktop gives it and no bigger. Left to itself it grows to fit whatever
            # its pages ask for, and with a taller font that was 30 pixels more than the screen: the status row
            # went under the taskbar.
            from PyQt6.QtWidgets import QLayout
            if self.layout() is not None:
                self.layout().setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
            self.saver = screensaver.build(self)
            self.last_input, self.saver_woke, self.idle_pos = time.monotonic(), 0.0, None
            self.idle_timer = QTimer(self)
            self.idle_timer.timeout.connect(self.idle_tick)
            self.idle_timer.start(5000)
            self.peek = {"peeking": False, "count": 0}
            self.peek_timer = QTimer(self)
            self.peek_timer.timeout.connect(self.peek_tick)
            self.peek_timer.start(250)
            self.tabs.currentChanged.connect(lambda *_: QTimer.singleShot(0, self.nav_tab_changed))
            self.ring = navring.make(self, self.nav_ring_rect,
                                     lambda: style.TOKENS["dark" if self.theme == "dark" else "light"]["accent2"])
            QApplication.instance().installEventFilter(self)

        def pad_read(self, *_):
            events, self.pad_left = controls.read_events(self.pad_fd, self.pad_left)
            if events is None:
                self.pad_notifier.setEnabled(False)
                self.pad_label.setText("Gamepad: disconnected.")
                return
            for ev in events:
                self.pad_label.setText("Gamepad: " + controls.describe(ev))
                action, pressed = controls.action_for(ev)
                if ev["kind"] == "axis":
                    self.pad_dir = action
                    self.pad_timer.stop()
                    if action:
                        self.act(action)
                        self.pad_timer.start(controls.REPEAT_FIRST_MS)
                elif action and pressed:
                    self.act(action)

        def pad_repeat(self):
            if self.pad_dir:
                self.pad_timer.setInterval(controls.REPEAT_MS)
                self.act(self.pad_dir)
            else:
                self.pad_timer.stop()

        def home_key(self, i):
            """The Home screen's soft keys: scan, rails, terminal, theme, full screen, record everything."""
            if i == 0 and "Wi-Fi" in self.names:
                self.goto(self.names.index("Wi-Fi"))
                self.start_scan()
            elif i == 1 and "Overview" in self.names:
                self.goto(self.names.index("Overview"))
            elif i == 2:
                self.toggle_drawer()
            elif i == 3:
                self.toggle_theme()
            elif i == 4:
                self.toggle_fullscreen()
            elif i == 5 and "Everything" in self.names:
                self.goto(self.names.index("Everything"))
                self.pages["Everything"].start()         # does nothing while a session is running or starting

        def goto_settings(self, sub_name):
            if "Settings" not in self.names:
                return
            self.goto(self.names.index("Settings"))
            sub = self.pages["Settings"]
            for i in range(sub.count()):
                if sub.tabText(i) == sub_name:
                    sub.setCurrentIndex(i)

        def goto(self, i):
            if 0 <= i < self.tabs.count():
                self.tabs.setCurrentIndex(i)

        def step(self, d):
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + d) % self.tabs.count())

        def current_name(self):
            return self.names[self.tabs.currentIndex()] if self.tabs.count() else ""

        def update_stats(self):
            if getattr(self, "names", None) and self.current_name() == "Help":
                self.refresh_help()
            """CPU, memory and friends in the bottom bar. Reads /proc, so it is cheap."""
            if self.stat_ticks % 5 == 0:
                self.throttled = statusbar.read_throttled()
            self.stat_ticks += 1
            stats = statusbar.read_stats(self.cpu_sample, self.throttled)
            self.cpu_sample = stats["sample"]
            extra = []
            power = (self.snap or {}).get("power") or {}
            if power.get("capacity") is not None:
                state = str(power.get("status", "")).lower()
                extra.append((f"Battery {power['capacity']}%" + (" charging" if data.is_charging(power) else ""),
                              "good" if power["capacity"] >= 30 else "warn" if power["capacity"] >= 15 else "hot"))
            hidden = self.dash["status_hidden"]
            extra = [] if "battery" in hidden else extra
            ground = style.TOKENS["dark" if self.theme == "dark" else "light"]["bg"]
            self.stats_label.setText(statusbar.chips_html(statusbar.chips(stats, hidden, self.theme == "dark"), extra,
                                                          ground))
            if hasattr(self, "warn_watch"):
                state = str(power.get("status", "")).lower()
                for event in self.warn_watch.update(power.get("capacity"), "charg" in state and "disch" not in state,
                                                    stats.get("temp"), statusbar.throttle_text(stats.get("throttled"))):
                    self.notify_user("warnings", *event)

        def update_footer(self, *_):
            if not hasattr(self, "foot"):
                return
            self.foot.setText(self.message or data.footer_for(self.current_name(), self.tabs.count(), self.live))

        # ---- background work
        def bg(self, fn, signal):
            threading.Thread(target=lambda: signal.emit(fn()), daemon=True).start()

        def watch_rails_mark(self):
            """Refresh at once when a rail is switched somewhere else (the tray, the command line). aiov2_ctl
            replaces a small file each time; a replaced file drops out of the watcher, so it is put back."""
            from PyQt6.QtCore import QFileSystemWatcher
            path = data.rails_stamp_path()
            self.rails_watcher = QFileSystemWatcher(self)
            self.rails_kick = QTimer(self)
            self.rails_kick.setSingleShot(True)
            self.rails_kick.setInterval(300)                        # one refresh for a burst of switches
            self.rails_kick.timeout.connect(self.refresh_marked)

            def arm():
                if not os.path.exists(path):
                    try:
                        open(path, "a").close()
                    except OSError:
                        return
                if path not in self.rails_watcher.files():
                    self.rails_watcher.addPath(path)

            def changed(_path):
                QTimer.singleShot(200, arm)
                self.rails_kick.start()

            self.rails_watcher.fileChanged.connect(changed)
            arm()

        def refresh_marked(self):
            """A rail was switched elsewhere. If a refresh is running it may already have read the old state, so
            one more is remembered and runs as soon as that one ends."""
            if self.busy:
                self.follow_up.remember()
                if self.busy or not self.follow_up.take():    # it ended this instant: its worker may have taken it
                    return
            self.refresh()

        def refresh(self):
            if getattr(self, "names", None) and self.current_name() == "Apps":
                self.pages["Apps"].refresh()                   # a program installed or removed since
            if self.busy:
                return
            self.busy = True
            self.follow_up.take()                              # one starting now reads the board after the switch

            def work():
                try:
                    snap = data.fetch_snapshot()
                    self.bridge.got.emit(snap)
                    plugged = data.flipper_plugged()           # sysfs: costs nothing, asks no device
                    probe = data.fetch_flipper_status() if plugged else {}   # nothing plugged: nothing to ask
                    self.bridge.flipper.emit(data.flipper_report(probe, plugged))
                    self.bridge.comps.emit(data.fetch_companions(snap))
                    self.bridge.radios.emit(data.fetch_radios())
                finally:
                    self.busy = False
                    if self.follow_up.take():                  # a rail was switched meanwhile: look again
                        self.bridge.again.emit()

            threading.Thread(target=work, daemon=True).start()

        def start_scan(self):
            self.wifi_panel.scan()

        def toggle_live(self):
            if not self.flip_shown:
                return
            self.live = not self.live
            if self.live:
                self.live_timer.start(LIVE_MS)
                self.grab_frame()
            else:
                self.live_timer.stop()
            self.flip_panel.sync_live(self.live)
            self.update_footer()

        def grab_frame(self):
            if self.frame_busy:
                return
            self.frame_busy = True

            def work():
                try:
                    path = data.fetch_flipper_png(self.tmpdir)
                    self.bridge.frame.emit(path or "")
                finally:
                    self.frame_busy = False

            threading.Thread(target=work, daemon=True).start()

        # ---- display
        def show_frame(self, path):
            pix = QPixmap(path) if path else QPixmap()
            if pix.isNull():
                self.flip_img.setText("No frame from the Flipper.")
            else:
                w, h = data.fit_frame(pix.width(), pix.height(), self.flip_img.width(), self.flip_img.height())
                self.flip_img.setPixmap(pix.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                                                   Qt.TransformationMode.FastTransformation))

        def show_scan(self, scan):
            self.wifi_panel.show_scan(scan)
            self.activity.add(f"Wi-Fi scan finished: {len((scan or {}).get('networks') or [])} networks")
            text = self.wifi_panel.status.text() if self.wifi_panel.scan_data else ""
            self.ant_cards["wifi"][1].setText(text)
            self.set_content("wifi", bool(text))

        def flag(self, key):
            return self.settings.get(key)

        def set_flag(self, key, value):
            self.settings[key] = bool(value)
            data.save_settings(dict(data.load_settings(), **{key: bool(value)}))
            if key == "debug_log":
                reports.write_log("logging turned " + ("on" if value else "off"), True)

        def set_refresh_seconds(self, seconds):
            self.refresh_seconds = int(seconds)
            self.settings["refresh_s"] = self.refresh_seconds
            data.save_settings(dict(data.load_settings(), refresh_s=self.refresh_seconds))
            self.timer.start(self.refresh_seconds * 1000)

        def apply_feature(self, hex_colour):
            """Switch the feature colour in the tokens and on the Home screen. Returns the colour used."""
            hx = style.set_feature(hex_colour)
            pixel.set_accent(hx, style.TOKENS["dark"]["accent2"])
            self.feature = hx
            return hx

        def feature_colour(self):
            return getattr(self, "feature", style.TOKENS["dark"]["accent"])

        def set_feature_colour(self, hex_colour, save=False):
            """Change the feature colour live. The restyle is held back a moment so a drag stays smooth."""
            self.apply_feature(hex_colour)
            if save:
                values = data.load_settings()
                values["accent"] = self.feature
                data.save_settings(values)
            timer = getattr(self, "feature_timer", None)
            if timer is None:
                timer = self.feature_timer = QTimer(self)
                timer.setSingleShot(True)
                timer.timeout.connect(self.restyle)
            timer.start(0 if save else 120)

        def restyle(self):
            self.set_theme()
            style.crisp_fonts(self)
            home = self.pages.get("Home")
            if home is not None:
                home.refresh_palette()
            self.update()

        def set_theme_choice(self, choice):
            self.saved_theme = choice
            values = data.load_settings()
            if choice is None:
                values.pop("theme", None)
            else:
                values["theme"] = choice
            data.save_settings(values)
            self.set_theme()

        def show_flipper(self, status):
            if status and status.get("absent"):                 # unplugged: its tab and its details go
                self.flip_status = {}
                self.flip_heading.setVisible(False)
                self.flip_card_text.setVisible(False)
                self.flipper_found = False
                self.update_companions_content()
                if self.flip_shown:
                    self.flip_shown = False
                    if self.live:
                        self.toggle_live_off()
                    self.rebuild_tabs()
                return
            self.flip_status = status or self.flip_status
            if not status:
                return      # the probe failed (port busy, timeout) but it is still plugged in: keep what we showed
            lines = data.flipper_lines(status)
            note = flipper_panel.mode_note(status)             # what mode it is in and why there is not more detail
            html = data.facts_html(data.flipper_facts(status))
            if note:
                html += ("<br>" if html else "") + f'<span style="color:#9e9e9e;">{note}</span>'
            found = bool(lines) or bool(note) or bool(status.get("mode"))
            self.flip_panel.update_status(status, lines)
            self.flip_card_text.setText(html)
            self.flip_heading.setVisible(found)
            self.flip_card_text.setVisible(found)
            self.flipper_found = found
            self.update_companions_content()
            if found != self.flip_shown:
                self.flip_shown = found
                if not found and self.live:
                    self.toggle_live_off()
                self.rebuild_tabs()

        def update_companions_content(self):
            """Companions stays on the Overview if it has an actual companion device, or the Flipper is plugged
            in: it carries the Flipper's own details now, as a smaller subsection within it."""
            self.set_content("companions", bool(getattr(self, "comps_found", False)) or self.flipper_found)

        def toggle_live_off(self):
            self.live = False
            self.live_timer.stop()
            self.flip_panel.sync_live(False)

        def reflow(self):
            self.apply_dashboard()

        def set_content(self, name, has):
            """A card with nothing to show is left off the Overview, whatever the dashboard says."""
            if self.card_content.get(name) != has:
                self.card_content[name] = has
                self.apply_dashboard()

        def set_dashboard(self, cfg):
            self.dash = dashboard.normalize(cfg)
            data.save_settings(dict(data.load_settings(), dashboard=self.dash))
            self.apply_dashboard()
            self.update_rails()
            self.dash_refresh()

        def apply_dashboard(self):
            """Lay the Overview cards out as the dashboard setting says, for the current width."""
            width = self.width()
            present = set(((self.snap or {}).get("rails") or {})) or set(data.RAILS)
            rails = dashboard.shown_rails(self.dash, present)
            plan = dashboard.plan(self.dash, dict(self.card_content, rails=bool(rails)))
            lcols, cols = data.lower_columns(width), data.rail_columns(width)
            sig = (tuple((k, tuple(v) if isinstance(v, list) else v) for k, v in plan), lcols, cols, tuple(rails))
            if sig == getattr(self, "_dash_sig", None):
                return
            self._dash_sig = sig
            lay = self.over_root
            while lay.count():
                item = lay.takeAt(0)
                grid = item.layout()
                if grid is not None:
                    while grid.count():
                        grid.takeAt(0)
                    grid.deleteLater()
            for box in self.cards.values():
                box.hide()
            for name in data.RAILS:
                self.rail_grid.removeWidget(self.rail_widgets[name][0])
            for i, name in enumerate(rails):
                self.rail_grid.addWidget(self.rail_widgets[name][0], i // cols, i % cols)
            for kind, what in plan:
                if kind == "full":
                    lay.addWidget(self.cards[what])
                    self.cards[what].show()
                else:
                    grid = QGridLayout()
                    for i, name in enumerate(what):
                        box = self.cards[name]
                        if not getattr(box, "_tops", False):          # contents hug the top of a card, whatever its height
                            box.layout().addStretch(1)
                            box._tops = True
                        grid.addWidget(box, i // lcols, i % lcols)
                        self.cards[name].show()
                    lay.addLayout(grid)
            self.custom_btn.hide()                      # customising lives in Settings, in one place, not on every screen
            lay.addStretch(1)

        def paintEvent(self, event):
            p = QPainter(self)
            dark = self.theme == "dark"
            tok = style.TOKENS["dark" if dark else "light"]
            flat = style.background_fill(self.settings.get("background"), self.theme)
            if flat:
                p.fillRect(self.rect(), QColor(flat))
            else:
                p.drawTiledPixmap(self.rect(), dither.gradient_tile(self.height(), tok["bg"], skin.mix(tok["bg"], tok["accent"], 0.65 if dark else 0.7)))
            p.end()

        def resizeEvent(self, event):
            super().resizeEvent(event)
            self.reflow()
            if self.saver_showing():
                self.saver.setGeometry(self.rect())
            if hasattr(self, "drawer"):
                self.drawer.reposition()

        def update_rails(self):
            rails = (self.snap or {}).get("rails") or {}
            battery = data.on_battery(self.snap)
            for name, (row, switch, power, meas) in self.rail_widgets.items():
                present = name in rails
                row.setVisible(present and name not in self.dash["rails_hidden"])
                switch.show_state(rails.get(name) == "on")
                switch.setEnabled(present)
                # Power is only worth showing once measured; a measure button only where it can work.
                text = data.rail_power_text(name, self.rail_cache)
                power.setText("" if text == "not measured" else text)
                meas.setVisible(battery)
                meas.setEnabled(present)
            est = data.rails_estimate(self.snap, self.rail_cache)
            self.rail_total.setText("" if est is None else f"Measured draw of the rails that are on: about {est:.2f} W")
            self.rail_total.setVisible(est is not None)

        def ask(self, text):
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Question)
            box.setText(text)
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            return box.exec() == QMessageBox.StandardButton.Yes

        def toggle_rail(self, name):
            if self.rail_busy or name not in ((self.snap or {}).get("rails") or {}):
                return
            turning_on = self.snap["rails"][name] != "on"
            warn = data.rail_warning(name, turning_on, self.snap, self.radio_result, self.flip_shown)
            if warn and not self.ask(warn + " Continue?"):
                return
            self.run_rail_job(f"Turning {data.RAIL_LABEL[name]} {'on' if turning_on else 'off'}...",
                              lambda: data.set_rail(name, turning_on)[1])

        def measure_rail(self, name):
            if self.rail_busy or name not in ((self.snap or {}).get("rails") or {}):
                return
            if not self.ask(data.measure_warning(name) + " Continue?"):
                return

            def job():
                import time as _t
                result = data.measure_rail(name)
                if not result:
                    return f"Measuring {data.RAIL_LABEL[name]} failed. The rail was left as aiov2_ctl found it."
                when = _t.strftime("%d %b %H:%M")
                data.save_rail_power(name, result, when)
                self.rail_cache = data.load_rail_power()
                return f"{data.RAIL_LABEL[name]}: {result['delta']:+.2f} W ({result['off']:.2f} to {result['on']:.2f} W)."

            self.run_rail_job(f"Measuring {data.RAIL_LABEL[name]} (about 10 seconds)...", job)

        def run_rail_job(self, message, fn):
            self.rail_busy = True
            self.say(message, 30)
            self.update_rails()

            def work():
                try:
                    note = fn()
                finally:
                    self.rail_busy = False
                self.bridge.railop.emit(note)

            threading.Thread(target=work, daemon=True).start()

        def rail_done(self, note):
            self.say(note, 8)
            self.update_rails()
            self.busy = False
            self.refresh()

        def show_radios(self, result):
            self.radio_result = result
            lines = data.radios_overview(result)
            self.set_content("radios", bool(lines))
            self.radio_text.setText("\n".join(lines))
            self.update_marauder(result)

        def update_marauder(self, result):
            """Show the Marauder tab only while the Flipper's board is listed. A scan holds the
            port, so a poll that cannot see the board during a scan must not hide the tab."""
            if data.marauder_present(result):
                shown = True
            elif not (result or {}).get("radios") or self.marauder_busy():
                return
            else:
                shown = False
            if shown != self.marauder_shown:
                self.marauder_shown = shown
                self.rebuild_tabs()
                late = getattr(self, "late_tab", "")
                if late in self.names:
                    self.late_tab = ""
                    self.tabs.setCurrentIndex(self.names.index(late))

        def show_comps(self, result):
            if result != getattr(self, "comp_result", None):
                self.comp_result = result
                self.rebuild_tabs()

        def show_snapshot(self, snap):
            self.snap = snap
            size = data.known_wh_per_percent(snap)             # the pack's size, when the kernel reports it:
            if size and not self.runtime.known:                # it beats anything learned by watching, always
                self.runtime.use_known_size(size)
            self.runtime.feed((snap or {}).get("power"))
            want = getattr(self, "pending_tab", "")
            if want:
                self.pending_tab = ""
                self.rebuild_tabs()
                top, _, subname = want.partition("/")
                if top not in self.names:
                    self.late_tab = top  # a tab that appears once the radios are known
                if top in self.names:
                    self.tabs.setCurrentIndex(self.names.index(top))
                    inner = self.pages.get(top)
                    if subname and hasattr(inner, "count"):
                        for i in range(inner.count()):
                            if inner.tabText(i) == subname:
                                inner.setCurrentIndex(i)
            self.sys_text.setText("\n".join(data.system_lines()))
            for name, label in self.ant_text.items():
                label.setText("\n".join(data.antenna_lines(name, snap)))
            self.rebuild_tabs()
            if not snap:
                self.bat_big.setText("n/a")
                self.bat_text.setText("aiov2_ctl did not answer. Is it installed?")
                return
            big, lines, level = data.battery_card(snap)
            power = snap.get("power") or {}
            pct = power.get("capacity") or 0
            charging = data.is_charging(power)
            colour = COLOURS[level] if level != "ok" else ("#43a047" if pct >= 30 else ("#e65100" if pct >= 15 else "#c62828"))
            self.bat_big.setText(big)
            self.set_style(self.bat_big, f"font-size: 30pt; font-weight: bold; color: {colour};")
            self.gauge.set_state(pct, charging, colour)
            chip = f"{power.get('status', 'No reading')}  |  {power.get('mode', '')}".strip(" |") if power else "No battery reading"
            self.chip.setText(chip)
            chip_colour = style.TOKENS["dark" if self.theme == "dark" else "light"]["accent2"] if charging else "#9e9e9e"
            self.set_style(self.chip, f"font-size: 13pt; font-weight: bold; padding: 0px; border: none; "
                                      f"background: transparent; color: {chip_colour};")
            tiles = data.stat_tiles(snap)
            for (tile, cap, val), item in zip(self.tiles, tiles + [None] * 3):
                tile.setVisible(item is not None)
                if item:
                    cap.setText(item[0])
                    val.setText(item[1])
            extra = lines[2:] + runtime.card_lines(self.runtime.estimate(), runtime.rail_draws(snap, self.rail_cache))
            self.bat_text.setText("\n".join(extra))
            self.bat_text.setVisible(bool(extra))
            self.update_rails()
            rails_now = (snap or {}).get("rails") or {}
            if self.prev_rails is not None:
                for name, state_ in rails_now.items():
                    if self.prev_rails.get(name) not in (None, state_):
                        self.notify_user("rails", f"{data.RAIL_LABEL.get(name, name)} {state_}", "", "info")
            self.prev_rails = dict(rails_now)
            self.mesh_hub.sync()
            for key, tab, rail in (("gps", "GPS", "GPS"), ("lora", "LoRa", "LORA"), ("sdr", "SDR", "SDR")):
                lines = data.antenna_lines(tab, snap) if rails_now.get(rail) == "on" else []
                self.ant_cards[key][1].setText("\n".join(lines))
                self.set_content(key, bool(lines))
            comps = data.companion_lines(snap)
            self.comps_found = bool(comps)
            self.update_companions_content()
            self.comp_text.setText("\n".join(comps))

        def closeEvent(self, event):
            if getattr(self, "saver", None) is not None:
                self.saver.stop()
            self.pages["Everything"].shutdown()               # a running session gets its last line written
            for listener in closing_listeners(self):            # each stop saves its log and frees its radio
                try:
                    listener.stop()
                except Exception:
                    pass                                        # one that fails to stop must not block the rest
            self.sdr_off()
            self.gps_off()
            self.lora_off()
            self.marauder_off()
            self.drawer.shutdown()
            self.mesh_hub.stop()
            self.live_timer.stop()
            self.pad_timer.stop()
            if self.pad_fd is not None:
                self.pad_notifier.setEnabled(False)             # first, or Qt warns that its socket has gone
                os.close(self.pad_fd)
                self.pad_fd = None
            shutil.rmtree(self.tmpdir, ignore_errors=True)
            super().closeEvent(event)

    return Window()


def finish(code, streams=None, leave=os._exit):
    """End the program once the window has gone. Everything the app saves or stops, it saves and stops in the
    window's closeEvent, which has run by now. What would follow is Qt and Python each taking the same objects
    apart from their own side, in an order neither controls, and on the uConsole that ended in a segmentation
    fault more often than not: after the window had closed, so nothing was lost, but it is still a crash. There
    is nothing left that is worth it, so what has been printed is flushed and the program leaves at once.
    The jobs registered to run as Python ends are deliberately not run: PyQt's own is one of them, and running
    them here brought the crash straight back (4 of 6 exits on the uConsole). The only other one is the
    radio's, which stops its helper programs; closeEvent has already done that through sdr_off, and those
    helpers are started so that they end with this program in any case."""
    for stream in (sys.stdout, sys.stderr) if streams is None else streams:
        try:
            stream.flush()
        except Exception:
            pass                                    # a closed or broken pipe must not stop the program leaving
    leave(int(code or 0))


def main():
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QTimer
    from . import iga, splash, startup
    if iga.wanted():                                      # only with the IGA browser installed; loads nothing
        iga.prepare_app()
    app = QApplication(sys.argv)
    full = startup.wants_fullscreen(sys.argv)            # opened at login in full screen (Settings > General)
    # Building the window takes most of a second: put the loading screen up first, so something shows at once.
    loading = splash.show(app, full) if splash.wanted(sys.argv) else None
    win = build_window()
    win.resize(720, 640)
    if "--tab" in sys.argv:
        win.pending_tab = sys.argv[sys.argv.index("--tab") + 1] if len(sys.argv) > sys.argv.index("--tab") + 1 else ""
    if full:
        win.showFullScreen()
    else:
        win.showMaximized()
    if loading is not None:
        QTimer.singleShot(250, loading.close)             # once the window has had time to draw over it
    finish(app.exec())


if __name__ == "__main__":
    sys.exit(main())
