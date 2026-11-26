"""
The Marauder tab: passive Wi-Fi scanning with the ESP32 board on the Flipper.

It drives `aiov2_ctl --marauder`, so the command line and this tab share one backend (and the
same serial lock, so they cannot fight over the port). Only the passive scans the CLI allows are
offered: no attack, spoofing or portal commands exist here. A scan only runs when the button is
pressed, always has an end time, and is stopped when the tab is left.
The helpers at the top are pure so they can be tested without a board.
"""
import os
import re
import signal
import subprocess
import threading

# Every one of these only listens. They mirror `aiov2_ctl --marauder modes`; the CLI refuses anything else.
MODES = (
    ("all", "Everything (APs and stations)"),
    ("ap", "Access points only"),
    ("stations", "Stations and their access points"),
    ("beacon", "Beacons: networks announcing themselves"),
    ("probe", "Probe requests: devices asking for networks"),
    ("deauthwatch", "Watch for devices being kicked off (detect only)"),
    ("pwn", "Pwnagotchi devices nearby"),
    ("esp", "ESP32 / ESP8266 devices nearby"),
    ("bt", "Bluetooth devices (needs a board with Bluetooth)"),
    ("skimmer", "Bluetooth card skimmers (needs Bluetooth)"),
    ("packets", "Packet counts (busy-ness meter)"),
    ("pineapple", "Wi-Fi Pineapple devices nearby (detect only)"),
    ("multissid", "One device announcing many names (detect only)"),
    ("remoteid", "Drone Remote ID broadcasts"),
    ("raw", "Every raw frame"),
)
NETWORK_MODES = ("all", "ap", "beacon")          # the others print different things, so the live output is the answer
DURATIONS = (15, 30, 60, 120)
DEFAULT_SECONDS = 30
# The signal is only taken as a separate field when it is set off by whitespace: without that, a network name
# that happens to end "-2" (an SSID some routers generate) would otherwise be read as "Home" at -2 dBm.
AP_RE = re.compile(r"^\[(\d+)\]\s*\[CH:\s*(\d+)\]\s*(.*?)(?:\s+(-\d+))?\s*$")


def popen_env(base=None):
    """The environment for the scan command: the usual one, with Python's output buffering switched off."""
    env = dict(os.environ if base is None else base)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def clamp_seconds(value, default=DEFAULT_SECONDS):
    try:
        return max(1, min(3600, int(value)))
    except (TypeError, ValueError):
        return default


def scan_args(mode, seconds):
    """The aiov2_ctl arguments for a scan. Unknown modes fall back to the safe default."""
    if mode not in dict(MODES):
        mode = "all"
    return ["aiov2_ctl", "--marauder", "scan", mode, "--seconds", str(clamp_seconds(seconds))]


def parse_ap_line(line):
    """{'index', 'channel', 'ssid', 'rssi'} from '[0][CH:6] HomeNet -50', else None."""
    m = AP_RE.match((line or "").strip())
    if not m:
        return None
    return {"index": int(m.group(1)), "channel": int(m.group(2)),
            "ssid": m.group(3) or "", "rssi": int(m.group(4)) if m.group(4) else None}


def parse_aps(lines):
    """Access points from scan output. The last listing wins, as the board numbers them again."""
    best = {}
    for line in lines or []:
        ap = parse_ap_line(line)
        if ap:
            best[(ap["ssid"], ap["channel"])] = ap
    return sorted(best.values(), key=lambda a: a["rssi"] if a["rssi"] is not None else -999, reverse=True)


def ap_rows(aps):
    rows = []
    for ap in aps:
        rows.append({"ssid": ap["ssid"] or "<hidden>", "channel": str(ap["channel"]),
                     "signal": "-" if ap["rssi"] is None else f"{ap['rssi']} dBm"})
    return rows


def summary(aps, mode="all", lines=0):
    if mode not in NETWORK_MODES:
        return f"{lines} lines heard." if lines else "Nothing heard."
    if not aps:
        return "Nothing heard yet."
    busiest = {}
    for ap in aps:
        busiest[ap["channel"]] = busiest.get(ap["channel"], 0) + 1
    ch = min(busiest, key=lambda c: (-busiest[c], c))
    return f"{len(aps)} networks heard. Busiest channel {ch} ({busiest[ch]})."


def saved_path(lines):
    """The capture file the CLI reports at the end of a scan, if any."""
    for line in reversed(lines or []):
        m = re.match(r"^Saved to (.+?) \(private", line.strip())
        if m:
            return m.group(1)
    return ""


def failure_text(lines):
    """The CLI's own error line, so the tab can say why a scan did not start."""
    for line in lines or []:
        if line.startswith("Error:"):
            return line[6:].strip()
    return ""


def build_panel(win, Switch=None):
    from PyQt6.QtCore import QObject, Qt, pyqtSignal
    from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView,
                                 QLabel, QPlainTextEdit, QPushButton, QTableWidget,
                                 QTableWidgetItem, QVBoxLayout, QWidget)

    class Signals(QObject):
        line = pyqtSignal(str)
        done = pyqtSignal(int)

    class Panel(QWidget):
        def __init__(self):
            super().__init__()
            self.proc = None
            self.lines = []
            self.signals = Signals()
            self.signals.line.connect(self.on_line)
            self.signals.done.connect(self.on_done)
            root = QVBoxLayout(self)

            head = QFrame()
            head.setObjectName("card")
            hl = QVBoxLayout(head)
            self.status = QLabel("Ready. Passive scanning only.")
            self.status.setStyleSheet("font-size: 16pt; font-weight: bold;")
            self.status.setWordWrap(True)
            self.note = QLabel("The Flipper must be running its USB-UART Bridge app. "
                               "Nothing is sent: it listens for beacons and probes.")
            self.note.setStyleSheet("font-size: 12pt; color: #9e9e9e;")
            self.note.setWordWrap(True)
            hl.addWidget(self.status)
            hl.addWidget(self.note)
            controls = QHBoxLayout()
            self.mode = QComboBox()
            for key, label in MODES:
                self.mode.addItem(label, key)
            self.length = QComboBox()
            for s in DURATIONS:
                self.length.addItem(f"{s} seconds", s)
            self.length.setCurrentIndex(DURATIONS.index(DEFAULT_SECONDS))
            self.go = QPushButton("Start scan")
            self.go.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.go.clicked.connect(self.toggle)
            controls.addWidget(self.mode, 2)
            controls.addWidget(self.length, 1)
            controls.addWidget(self.go, 1)
            hl.addLayout(controls)
            root.addWidget(head)

            mid = QHBoxLayout()
            nets = QFrame()
            nets.setObjectName("card")
            nl = QVBoxLayout(nets)
            cap = QLabel("NETWORKS HEARD")
            cap.setObjectName("tag")
            cap_row = QHBoxLayout()
            cap_row.addWidget(cap)
            cap_row.addStretch(1)
            self.table = QTableWidget(0, 3)
            self.table.setHorizontalHeaderLabels(["Network", "Channel", "Signal"])
            self.table.verticalHeader().setVisible(False)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            for c in (1, 2):
                self.table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
            nl.addLayout(cap_row)
            nl.addWidget(self.table)
            mid.addWidget(nets, 3)
            feed = QFrame()
            feed.setObjectName("card")
            fl = QVBoxLayout(feed)
            cap = QLabel("LIVE OUTPUT")
            cap.setObjectName("tag")
            cap_row = QHBoxLayout()
            cap_row.addWidget(cap)
            cap_row.addStretch(1)
            self.feed = QPlainTextEdit()
            self.feed.setReadOnly(True)
            self.feed.setStyleSheet("font-size: 11pt;")
            fl.addLayout(cap_row)
            fl.addWidget(self.feed)
            mid.addWidget(feed, 4)
            root.addLayout(mid, 1)

        # ---- lifecycle (the board is only opened while a scan runs)
        def activate(self):
            pass

        def deactivate(self):
            self.stop()

        # ---- running
        def toggle(self):
            if self.proc:
                self.stop()
            else:
                self.start()

        def start(self):
            args = scan_args(self.mode.currentData(), self.length.currentData())
            self.lines = []
            self.feed.clear()
            self.status.setText("Scanning...")
            self.go.setText("Stop")
            try:
                # Unbuffered, or the command line tool holds its output back until the scan ends.
                self.proc = subprocess.Popen(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                             bufsize=1, env=popen_env())
            except OSError as e:
                self.proc = None
                self.go.setText("Start scan")
                self.status.setText(f"Could not start: {e}")
                return
            proc = self.proc

            def work():
                for line in proc.stdout:
                    self.signals.line.emit(line.rstrip("\n"))
                self.signals.done.emit(proc.wait())

            threading.Thread(target=work, daemon=True).start()

        def stop(self):
            proc = self.proc
            if proc and proc.poll() is None:
                try:
                    proc.send_signal(signal.SIGINT)  # the CLI sends stopscan on Ctrl+C
                except OSError:
                    pass
                self.status.setText("Stopping...")

        def on_line(self, line):
            self.lines.append(line)
            self.feed.appendPlainText(line)
            self.fill(parse_aps(self.lines))

        def on_done(self, code):
            self.proc = None
            self.go.setText("Start scan")
            aps = parse_aps(self.lines)
            self.fill(aps)
            why = failure_text(self.lines)
            if why:
                self.status.setText(why)
            else:
                path = saved_path(self.lines)
                self.status.setText(summary(aps, self.mode.currentData(), len(self.lines)))
                self.note.setText(f"Saved to {path} (private)." if path else self.note.text())

        def fill(self, aps):
            rows = ap_rows(aps)
            self.table.setRowCount(len(rows))
            for r, row in enumerate(rows):
                for c, key in enumerate(("ssid", "channel", "signal")):
                    self.table.setItem(r, c, QTableWidgetItem(row[key]))

    panel = Panel()
    return panel, panel.activate, panel.deactivate
