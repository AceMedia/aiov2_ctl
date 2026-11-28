"""
The Flipper tab: status, the live screen, the SD card, and the apps it can launch.

Everything goes through `aiov2_ctl --flipper`, so the command line and this tab share one
backend and the same serial lock. The Flipper only answers these in its normal CLI mode; while
the USB-UART Bridge app is running (for Marauder) the controls are switched off and the tab says
how to get the CLI back. Files are only read from the SD card and copied to the private captures
folder, never overwritten. Launching an app asks first.
The helpers at the top are pure so they can be tested without a Flipper.
"""
import posixpath
import re
import subprocess
import threading

from . import flipper_session as fs

SD_ROOT = "/ext"
ROW_RE = re.compile(r"^([d-])\s{2}(.+?)(?:\s{2}(\d+\s*\w*))?$")
SAFE_RE = re.compile(r"^[A-Za-z0-9_./ +()\-]{1,200}$")


def usable(status):
    """True when the Flipper answers the CLI (not in bridge mode, and it described itself)."""
    s = status or {}
    return s.get("mode") != "bridge" and bool(s.get("device"))


def mode_note(status):
    s = status or {}
    if usable(status):
        return ""
    if s.get("mode") == "bridge":
        return ("The Flipper is in USB-UART Bridge mode (for the Marauder tab). Press Back on it "
                "to get the CLI back, then press Refresh here for the screen, files and apps.")
    return "No answer from the Flipper CLI yet. Press Refresh once it is awake."


def is_sd_path(path):
    return bool(SAFE_RE.match(path or "")) and ".." not in (path or "").split("/") and \
        (path == SD_ROOT or (path or "").startswith(SD_ROOT + "/"))


def join_path(path, name):
    return posixpath.join(path, name)


def parent_path(path):
    """One level up, never above the SD card."""
    if not is_sd_path(path) or path == SD_ROOT:
        return SD_ROOT
    return posixpath.dirname(path.rstrip("/")) or SD_ROOT


def parse_ls(text):
    """(kind, name, size) from the CLI's 'd  name' and '-  name  size' lines."""
    entries = []
    for line in (text or "").splitlines():
        m = ROW_RE.match(line.rstrip())
        if m:
            entries.append(("dir" if m.group(1) == "d" else "file", m.group(2), (m.group(3) or "").strip()))
    return sorted(entries, key=lambda e: (e[0] != "dir", e[1].lower()))


def parse_apps(text):
    """App names from `loader list`: one per line, headings and blanks dropped."""
    names = []
    for line in (text or "").splitlines():
        name = line.strip()
        if not name or name.endswith(":") or name.lower().startswith(("error", "usage")):
            continue
        if SAFE_RE.match(name) and name not in names:
            names.append(name)
    return sorted(names, key=str.lower)


def error_text(text):
    for line in (text or "").splitlines():
        if line.startswith("Error:"):
            return line[6:].strip()
    return ""


def ls_args(path):
    return ["aiov2_ctl", "--flipper", "ls", path if is_sd_path(path) else SD_ROOT]


def get_args(path):
    return ["aiov2_ctl", "--flipper", "get", path]


def open_args(app):
    return ["aiov2_ctl", "--flipper", "open", app]


def open_warning(app):
    return (f"Launch {app} on the Flipper? Some apps transmit as soon as they open, "
            "so only do this if you know what it does.")


def saved_line(text):
    for line in (text or "").splitlines():
        if line.startswith("Saved "):
            return line
    return ""


CATEGORIES = ("Tools", "GPIO", "Bluetooth", "Games", "Infrared", "Media", "NFC", "RFID", "Sub-GHz", "USB",
              "iButton", "Misc")
GPIO_PINS = (("PA7", 2), ("PA6", 3), ("PA4", 4), ("PB3", 5), ("PB2", 6), ("PC3", 7), ("PC1", 15), ("PC0", 16))
NAME_RE = re.compile(r"^[A-Za-z0-9_+() \-][A-Za-z0-9_.+() \-]{0,60}$")
CATALOG = (
    ("ESP32 Wi-Fi Marauder", "The Flipper side of the Marauder board: scans from the Flipper's own menu.",
     "Needs the Wi-Fi dev board on the GPIO header."),
    ("Air Mouse", "Turns the Flipper into a motion mouse over USB or Bluetooth.",
     "Needs an IMU module (such as an MPU6050 board) on the GPIO header."),
    ("GPS NMEA viewer", "Shows satellites and position from a GPS module on the header.",
     "Needs a UART GPS module. The uConsole already has its own GPS."),
    ("USB-UART Bridge", "Already in the firmware (GPIO > USB-UART Bridge). The Marauder tab uses it.",
     "Nothing to install."),
)
CATALOG_HINT = ("Get a .fap from the official Flipper app catalogue (lab.flipper.net/apps) or the app's own "
                "release page, check it is the build for your firmware, then use Install a .fap below.")


def valid_name(text):
    """A plain file or folder name for the SD card: no slashes, dots first, or odd characters."""
    name = (text or "").strip()
    return bool(NAME_RE.match(name)) and ".." not in name


def put_args(local, remote_dir, name=None):
    import os
    name = name or os.path.basename(local)
    return ["aiov2_ctl", "--flipper", "put", local, join_path(remote_dir, name)]


def mkdir_args(path):
    return ["aiov2_ctl", "--flipper", "mkdir", path]


def install_args(local, category):
    return ["aiov2_ctl", "--flipper", "install", local,
            "--category", category if category in CATEGORIES else "Tools"]


def gpio_read_args(pin):
    return ["aiov2_ctl", "--flipper", "gpio", "read", pin]


def gpio_set_args(pin, value):
    return ["aiov2_ctl", "--flipper", "gpio", "set", pin, "1" if value else "0", "--yes"]


def parse_gpio_reply(text):
    """(value or None, problem or '') from the CLI's answer to a pin read."""
    problem = error_text(text)
    m = re.search(r"reads ([01])", text or "")
    return (int(m.group(1)) if m else None), problem


def gpio_warning(pin, value):
    return (f"Drive {pin} {'high' if value else 'low'}? This puts a voltage on whatever is connected to "
            "that pin, including the Wi-Fi board if it is fitted.")


def sd_send_warning(name, folder):
    return f"Send {name} to {folder} on the Flipper's SD card? An existing file with that name is not replaced."


def build_panel(win, Switch):
    from PyQt6.QtCore import QObject, Qt, pyqtSignal
    from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
                                 QInputDialog, QLabel, QListWidget, QPushButton, QTabWidget, QTableWidget,
                                 QTableWidgetItem, QVBoxLayout, QWidget)
    from .foldertabs import FolderTabWidget

    class Signals(QObject):
        result = pyqtSignal(str, str)

    def card(title):
        box = QFrame()
        box.setObjectName("card")
        lay = QVBoxLayout(box)
        head = QLabel(title.upper())
        head.setObjectName("tag")
        lay.addWidget(head, 0, Qt.AlignmentFlag.AlignLeft)
        return box, lay

    def button(text, slot):
        b = QPushButton(text)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.clicked.connect(slot)
        return b

    class Panel(QWidget):
        def __init__(self):
            super().__init__()
            self.path = SD_ROOT
            self.entries = []
            self.busy = False
            self.ok = False
            self.record = {}
            self.caps = {}
            self.want_clock = False
            self.clock_seen = False
            self.signals = Signals()
            self.signals.result.connect(self.on_result)
            root = QHBoxLayout(self)

            left = QVBoxLayout()
            screen, sl = card("Screen")
            self.img = QLabel("Switch the live display on to watch the Flipper.")
            self.img.setStyleSheet("font-size: 12pt; background: #212121; color: #9e9e9e;")
            self.img.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.img.setFixedSize(384, 192)
            sl.addWidget(self.img, 0, Qt.AlignmentFlag.AlignHCenter)
            row = QHBoxLayout()
            self.live_switch = Switch()
            self.live_switch.clicked.connect(self.toggle_live)
            row.addWidget(QLabel("Live display"))
            row.addWidget(self.live_switch)
            row.addStretch(1)
            sl.addLayout(row)
            left.addWidget(screen)
            info, il = card("Status")
            self.text = QLabel("")
            self.text.setTextFormat(Qt.TextFormat.RichText)
            self.text.setWordWrap(True)
            self.text.setStyleSheet("font-size: 11pt;")
            self.text.setAlignment(Qt.AlignmentFlag.AlignTop)
            il.addWidget(self.text)
            left.addWidget(info, 1)
            about, abl = card("About this Flipper")
            self.about = QLabel("")
            self.about.setTextFormat(Qt.TextFormat.RichText)
            self.about.setWordWrap(True)
            self.about.setStyleSheet("font-size: 11pt;")
            abl.addWidget(self.about)
            self.clock_l = QLabel("")
            self.clock_l.setWordWrap(True)
            self.clock_l.setStyleSheet("font-size: 11pt;")
            abl.addWidget(self.clock_l)
            self.clock_b = button("Set Flipper clock from this computer", self.set_clock)
            abl.addWidget(self.clock_b)
            left.addWidget(about)
            root.addLayout(left, 0)

            right, rl = card("On the Flipper")
            self.note = QLabel("")
            self.note.setWordWrap(True)
            self.note.setStyleSheet("font-size: 12pt; color: #9e9e9e;")
            rl.addWidget(self.note)
            self.subs = FolderTabWidget()
            self.subs.setObjectName("subtabs")
            self.subs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            files = QWidget()
            fl = QVBoxLayout(files)
            bar = QHBoxLayout()
            self.where = QLabel(SD_ROOT)
            self.where.setStyleSheet("font-size: 12pt; font-weight: bold;")
            self.up_b = button("Up", self.go_up)
            self.copy_b = button("Copy to uConsole", self.copy_file)
            self.send_b = button("Send a file here", self.send_file)
            self.folder_b = button("New folder", self.new_folder)
            bar.addWidget(self.where, 1)
            bar.addWidget(self.up_b)
            bar.addWidget(self.copy_b)
            bar.addWidget(self.send_b)
            bar.addWidget(self.folder_b)
            fl.addLayout(bar)
            self.table = QTableWidget(0, 2)
            self.table.setHorizontalHeaderLabels(["Name", "Size"])
            self.table.verticalHeader().setVisible(False)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            self.table.cellDoubleClicked.connect(self.open_row)
            self.table.itemSelectionChanged.connect(self.sync)
            fl.addWidget(self.table, 1)
            self.subs.addTab(files, "Files")
            apps = QWidget()
            al = QVBoxLayout(apps)
            self.apps = QListWidget()
            self.apps.itemSelectionChanged.connect(self.sync)
            self.open_b = button("Open on the Flipper", self.open_app)
            al.addWidget(self.apps, 1)
            al.addWidget(self.open_b)
            inst = QHBoxLayout()
            self.category = QComboBox()
            self.category.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            for c in CATEGORIES:
                self.category.addItem(c, c)
            self.install_b = button("Install a .fap", self.install_app)
            inst.addWidget(QLabel("Category"))
            inst.addWidget(self.category, 1)
            inst.addWidget(self.install_b, 1)
            al.addLayout(inst)
            self.subs.addTab(apps, "Apps")
            cat = QWidget()
            cl = QVBoxLayout(cat)
            hint = QLabel(CATALOG_HINT)
            hint.setWordWrap(True)
            hint.setStyleSheet("font-size: 12pt; color: #9e9e9e;")
            cl.addWidget(hint)
            self.catalog = QListWidget()
            self.catalog.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            for name, what, needs in CATALOG:
                self.catalog.addItem(f"{name}\n    {what}\n    {needs}")
            cl.addWidget(self.catalog, 1)
            self.subs.addTab(cat, "Get apps")
            pins = QWidget()
            pl = QVBoxLayout(pins)
            pn = QLabel("The Flipper's header pins. Reading is safe. Driving a pin needs the switch below. "
                        "The Flipper must be on its home screen with no app open.")
            pn.setWordWrap(True)
            pn.setStyleSheet("font-size: 12pt; color: #9e9e9e;")
            pl.addWidget(pn)
            self.pin_table = QTableWidget(len(GPIO_PINS), 3)
            self.pin_table.setHorizontalHeaderLabels(["Pin", "Header", "Reads"])
            self.pin_table.verticalHeader().setVisible(False)
            self.pin_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.pin_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.pin_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.pin_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            for r, (pin, header) in enumerate(GPIO_PINS):
                for c, text in enumerate((pin, str(header), "-")):
                    self.pin_table.setItem(r, c, QTableWidgetItem(text))
            self.pin_table.itemSelectionChanged.connect(self.sync)
            pl.addWidget(self.pin_table, 1)
            prow = QHBoxLayout()
            self.read_b = button("Read this pin", self.read_pin)
            self.read_all_b = button("Read all", self.read_all)
            self.allow_out = Switch()
            self.allow_out.clicked.connect(self.toggle_outputs)
            self.outputs_on = False
            self.high_b = button("Drive high", lambda: self.drive(1))
            self.low_b = button("Drive low", lambda: self.drive(0))
            prow.addWidget(self.read_b)
            prow.addWidget(self.read_all_b)
            prow.addStretch(1)
            prow.addWidget(QLabel("Allow outputs"))
            prow.addWidget(self.allow_out)
            prow.addWidget(self.high_b)
            prow.addWidget(self.low_b)
            pl.addLayout(prow)
            self.subs.addTab(pins, "GPIO")
            rl.addWidget(self.subs, 1)
            self.refresh_b = button("Refresh", self.refresh)
            rl.addWidget(self.refresh_b)
            root.addWidget(right, 1)
            self.sync()

        # ---- called by the window
        def update_status(self, status, lines):
            was = self.ok
            self.ok = usable(status)
            from . import data
            self.text.setText(data.facts_html(data.flipper_facts(status, with_apps=True),
                                              data.flipper_tips(status)))
            self.record = fs.parse_device_info((status or {}).get("device"))
            self.caps = fs.capabilities(self.record, status)
            off = [(f"Not offered: {n}", why) for n, why in fs.unavailable_rows(self.caps)]
            self.about.setText(data.facts_html(fs.about_rows(self.record) + off))
            note = mode_note(status)
            if note or was != self.ok:          # do not wipe a result message on every poll
                self.note.setText(note)
            self.sync()
            if self.ok and not was:
                self.refresh()

        def sync_live(self, live):
            self.live_switch.show_state(bool(live))

        def toggle_live(self):
            win.toggle_live()
            self.sync_live(win.live)

        # ---- controls
        def sync(self):
            idle = self.ok and not self.busy
            can = lambda feature: fs.available(self.caps, feature)    # noqa: E731
            self.refresh_b.setEnabled(not self.busy)
            self.up_b.setEnabled(idle and can("files") and self.path != SD_ROOT)
            row = self.table.currentRow()
            self.copy_b.setEnabled(idle and can("copy") and 0 <= row < len(self.entries) and self.entries[row][0] == "file")
            self.open_b.setEnabled(idle and self.apps.currentRow() >= 0)
            self.send_b.setEnabled(idle and can("send"))
            self.folder_b.setEnabled(idle and can("send"))
            self.install_b.setEnabled(idle and can("send"))
            pin_row = self.pin_table.currentRow() >= 0
            self.read_b.setEnabled(idle and can("pins") and pin_row)
            self.read_all_b.setEnabled(idle and can("pins"))
            for b in (self.high_b, self.low_b):
                b.setEnabled(idle and pin_row and self.outputs_on)
            self.table.setEnabled(idle and can("files"))
            self.apps.setEnabled(idle and can("apps"))
            self.clock_b.setEnabled(idle and can("clock") and self.clock_seen)
            # Switching the live display off always stays possible.
            self.live_switch.setEnabled(can("live_screen") or bool(getattr(win, "live", False)))
            for widget, feature in ((self.live_switch, "live_screen"), (self.table, "files"),
                                    (self.copy_b, "copy"), (self.send_b, "send"), (self.folder_b, "send"),
                                    (self.install_b, "send"), (self.apps, "apps"), (self.read_b, "pins"),
                                    (self.read_all_b, "pins"), (self.clock_b, "clock")):
                widget.setToolTip(self.caps.get(feature, (True, ""))[1])

        def run(self, tag, args, timeout=60):
            if self.busy:
                return
            self.busy = True
            self.sync()

            def work():
                try:
                    out = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
                    text = (out.stdout or "") + (out.stderr or "")
                except Exception as e:
                    text = f"Error: {e}"
                self.signals.result.emit(tag, text)

            threading.Thread(target=work, daemon=True).start()

        def refresh(self):
            self.note.setText(mode_note(win.flip_status) or "Reading the Flipper...")
            self.want_clock = True                       # read once the listing is done; never set here
            win.refresh()
            if self.ok and fs.available(self.caps, "files"):
                self.list_dir(self.path)                  # the app list follows the folder listing
            elif self.ok and fs.available(self.caps, "apps"):
                self.run("apps", ["aiov2_ctl", "--flipper", "apps"])

        def list_dir(self, path):
            self.path = path
            self.where.setText(path)
            self.run("ls", ls_args(path))

        def go_up(self):
            self.list_dir(parent_path(self.path))

        def open_row(self, row, _col=0):
            if 0 <= row < len(self.entries) and self.entries[row][0] == "dir":
                self.list_dir(join_path(self.path, self.entries[row][1]))

        def copy_file(self):
            row = self.table.currentRow()
            if 0 <= row < len(self.entries) and self.entries[row][0] == "file":
                self.note.setText("Copying...")
                self.run("get", get_args(join_path(self.path, self.entries[row][1])))

        def open_app(self):
            item = self.apps.currentItem()
            if item and win.ask(open_warning(item.text())):
                self.run("open", open_args(item.text()))

        def send_file(self):
            local, _ = QFileDialog.getOpenFileName(self, "Send a file to the Flipper's SD card")
            if not local:
                return
            import os
            name = os.path.basename(local)
            if not valid_name(name):
                self.note.setText("That file name has characters the Flipper cannot take. Rename it first.")
                return
            if win.ask(sd_send_warning(name, self.path)):
                self.note.setText(f"Sending {name}...")
                self.run("put", put_args(local, self.path), timeout=300)

        def new_folder(self):
            name, ok = QInputDialog.getText(self, "New folder", f"Folder name inside {self.path}:")
            if ok and valid_name(name):
                self.run("mkdir", mkdir_args(join_path(self.path, name.strip())))
            elif ok:
                self.note.setText("Use letters, numbers, spaces, dashes and brackets for the folder name.")

        def install_app(self):
            local, _ = QFileDialog.getOpenFileName(self, "Choose a Flipper app", "", "Flipper apps (*.fap)")
            if local and win.ask(f"Install {local.rsplit('/', 1)[-1]} in Apps > {self.category.currentData()} "
                                 "on the Flipper? It will run on the Flipper, so only install apps you trust."):
                self.note.setText("Installing...")
                self.run("install", install_args(local, self.category.currentData()), timeout=300)

        def set_clock(self):
            if self.clock_seen and win.ask(fs.CLOCK_SET_QUESTION):
                self.clock_l.setText("Setting the Flipper's clock...")
                self.run("clockset", fs.clock_set_args())

        def toggle_outputs(self):
            self.outputs_on = not self.outputs_on
            self.allow_out.show_state(self.outputs_on)
            self.sync()

        def read_pin(self):
            row = self.pin_table.currentRow()
            if row >= 0:
                self.pin_reading = [row]
                self.run("gpio", gpio_read_args(GPIO_PINS[row][0]))

        def read_all(self):
            self.pin_reading = list(range(len(GPIO_PINS)))
            self.next_pin()

        def next_pin(self):
            if getattr(self, "pin_reading", None):
                self.run("gpio", gpio_read_args(GPIO_PINS[self.pin_reading[0]][0]))

        def drive(self, value):
            row = self.pin_table.currentRow()
            if row >= 0 and self.outputs_on and win.ask(gpio_warning(GPIO_PINS[row][0], value)):
                self.pin_reading = [row]
                self.run("gpioset", gpio_set_args(GPIO_PINS[row][0], value))

        def on_result(self, tag, text):
            self.busy = False
            why = error_text(text)
            if tag == "ls":
                self.entries = [] if why else parse_ls(text)
                self.table.setRowCount(len(self.entries))
                for r, (kind, name, size) in enumerate(self.entries):
                    self.table.setItem(r, 0, QTableWidgetItem(("[folder] " if kind == "dir" else "") + name))
                    self.table.setItem(r, 1, QTableWidgetItem(size))
                self.note.setText(why or ("Empty folder." if not self.entries else
                                          "Double click a folder to open it."))
                if not why and not self.apps.count():
                    self.run("apps", ["aiov2_ctl", "--flipper", "apps"])
                    return
            elif tag == "apps":
                self.apps.clear()
                self.apps.addItems([] if why else parse_apps(text))
            elif tag in ("gpio", "gpioset"):
                value, problem = parse_gpio_reply(text)
                if tag == "gpioset":
                    self.note.setText(problem or "Done. Read the pin to set it back to an input.")
                elif problem:
                    self.pin_reading = []
                    self.note.setText(problem)
                else:
                    row = self.pin_reading.pop(0)
                    self.pin_table.item(row, 2).setText("-" if value is None else str(value))
                    if self.pin_reading:
                        self.busy = False
                        self.next_pin()
                        return
                    self.note.setText("Pins read.")
            elif tag in ("put", "mkdir", "install"):
                self.note.setText(why or {"put": "Sent. Checksum verified.", "mkdir": "Folder made.",
                                          "install": "Installed. Open it on the Flipper under Apps."}[tag])
                if not why and tag in ("put", "mkdir"):
                    self.busy = False
                    self.list_dir(self.path)
                    return
            elif tag == "get":
                line = saved_line(text)
                preview = "" if why else fs.preview_file(fs.saved_path(line))   # reads the local copy only
                self.note.setText(why or " ".join(x for x in (line or "Copied.", preview) if x))
            elif tag == "open":
                self.note.setText(why or "Asked the Flipper to open it.")
            elif tag in ("clock", "clockset"):
                flipper, computer = fs.clock_reading(text)
                offset = fs.clock_offset(flipper, computer)
                self.clock_seen = offset is not None
                self.clock_l.setText(why or fs.clock_text(offset))
                if tag == "clockset":
                    self.note.setText(why or "Clock set and read back.")
            if self.want_clock and self.ok and fs.available(self.caps, "clock"):
                self.want_clock = False
                self.run("clock", fs.clock_args())
                return
            self.sync()

    panel = Panel()
    return panel
