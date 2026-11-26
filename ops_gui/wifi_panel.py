"""
The Wi-Fi tab: scan every radio, compare them, see the channels, and revisit saved scans.

It runs `aiov2_ctl --wifi scan --json`, so the command line and this tab share one backend. A scan is
passive (a normal managed mode scan, plus the Flipper's Marauder board when it answers), only runs when
the button is pressed, and is only saved to disk when "Save each scan" is on.
The helpers at the top are pure so they can be tested without a screen or a radio.
"""
import glob
import json
import os
import re
import subprocess
import threading
import time

CAPTURES = os.path.join(os.path.expanduser("~"), "aiov2-captures")
SCAN_TIMEOUT = 90
BANDS = (("", "All bands"), ("2.4", "2.4 GHz"), ("5", "5 GHz"), ("6", "6 GHz"))
CHANNELS_5 = (36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144,
              149, 153, 157, 161, 165)
IFACE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")
FLIPPER_RADIO = "flipper-marauder"


def scan_args(radio="", save=False):
    """The aiov2_ctl arguments for a scan. An odd radio name is dropped rather than passed on."""
    args = ["aiov2_ctl", "--wifi", "scan", "--json"]
    if radio and (IFACE_RE.match(radio) or radio == FLIPPER_RADIO):
        args += ["--iface", radio]
    if save:
        args.append("--save")
    return args


def band_of(n):
    f = n.get("freq_mhz")
    if isinstance(f, (int, float)) and f:
        return "2.4" if f < 3000 else "5" if f < 5925 else "6"
    ch = n.get("channel")
    return "2.4" if isinstance(ch, int) and 1 <= ch <= 14 else "5" if isinstance(ch, int) else "?"


def _dbm(n):
    s = n.get("signal_dbm")
    return s if isinstance(s, (int, float)) else None


def bars(dbm):
    if dbm is None:
        return "----"
    steps = sum(dbm >= t for t in (-85, -75, -65, -55))
    return "#" * steps + "-" * (4 - steps)


def filter_networks(networks, radio="", band="", text=""):
    out = []
    needle = (text or "").strip().lower()
    for n in networks or []:
        if not isinstance(n, dict):
            continue
        if radio and n.get("radio") != radio:
            continue
        if band and band_of(n) != band:
            continue
        if needle and needle not in f"{n.get('ssid', '')} {n.get('bssid', '')}".lower():
            continue
        out.append(n)
    return out


def merged_rows(networks):
    """One row per network, strongest first, with what each radio heard of it.

    Networks are the same when they share a BSSID (or, when a radio cannot see the BSSID, an SSID and channel).
    """
    groups = {}
    for n in networks or []:
        if not isinstance(n, dict):
            continue
        key = (n.get("bssid") or f"{n.get('ssid', '')}|{n.get('channel')}").lower()
        groups.setdefault(key, []).append(n)
    rows = []
    for key, items in groups.items():
        items.sort(key=lambda n: _dbm(n) if _dbm(n) is not None else -999, reverse=True)
        best = items[0]
        top = _dbm(best)
        heard = " | ".join(f"{n.get('radio') or '?'} {_dbm(n)}" if _dbm(n) is not None
                           else str(n.get("radio") or "?") for n in items)
        security = best.get("security") or "?"
        rows.append({
            "ssid": best.get("ssid") or "(hidden)", "bssid": best.get("bssid") or "",
            "channel": str(best.get("channel", "?")), "band": band_of(best),
            "signal": "-" if top is None else f"{bars(top)}  {top} dBm", "dbm": top if top is not None else -999,
            "security": security, "open": security.lower() == "open", "heard": heard,
        })
    rows.sort(key=lambda r: r["dbm"], reverse=True)
    return rows


def summary_line(scan, rows):
    radios = [r for r in (scan or {}).get("radios") or [] if isinstance(r, dict)]
    up = [r.get("name", "?") for r in radios if r.get("up")]
    opens = sum(1 for r in rows if r["open"])
    bits = [f"{len(rows)} networks", f"scanned with {', '.join(up)}" if up else "no radio up"]
    if opens:
        bits.append(f"{opens} open")
    return "  |  ".join(bits)


def channel_chart(networks, band):
    """[(channel, networks on it, strongest dBm)] for a band, channels with nothing included for 2.4 and 5."""
    seen = {}
    for n in networks or []:
        if isinstance(n, dict) and band_of(n) == band and isinstance(n.get("channel"), int):
            count, top = seen.get(n["channel"], (0, -999))
            d = _dbm(n)
            seen[n["channel"]] = (count + 1, max(top, d if d is not None else -999))
    base = list(range(1, 14)) if band == "2.4" else list(CHANNELS_5) if band == "5" else []
    return [(c, seen.get(c, (0, None))[0], seen.get(c, (0, None))[1] if c in seen else None)
            for c in sorted(set(base) | set(seen))]


def channel_advice(networks):
    """Plain advice lines: the quietest of 1, 6 and 11, and the emptiest wide 5 GHz channels."""
    lines = []
    if not any(isinstance(n, dict) for n in networks or []):
        return ["Scan first to see the channels."]
    two = {c: n for c, n, _ in channel_chart(networks, "2.4")}
    if two:
        load = {c: sum(n for ch, n in two.items() if abs(ch - c) < 5) for c in (1, 6, 11)}
        best = min(load, key=lambda c: (load[c], c))
        lines.append(f"Quietest 2.4 GHz choice: channel {best} ({load[best]} overlapping).")
    five = {c: n for c, n, _ in channel_chart(networks, "5")}
    if five:
        empty = [c for c in CHANNELS_5 if five.get(c, 0) == 0]
        lines.append(f"Empty 5 GHz channels: {', '.join(map(str, empty[:8]))}" + (" ..." if len(empty) > 8 else "")
                     if empty else "Every standard 5 GHz channel has a network on it.")
    return lines or ["Scan first to see the channels."]


def saved_scans(directory=None):
    """[(path, label)] of saved scans, newest first."""
    out = []
    for path in sorted(glob.glob(os.path.join(directory or CAPTURES, "wifi-scan-*.json")), reverse=True):
        m = re.search(r"wifi-scan-(\d{8})-(\d{6})", os.path.basename(path))
        label = os.path.basename(path)
        if m:
            try:
                label = time.strftime("%d %b %Y %H:%M:%S", time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S"))
            except ValueError:
                pass
        out.append((path, label))
    return out


def load_scan(path):
    """A saved scan as a dict with a networks list, or {} when it is not one."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and isinstance(data.get("networks"), list) else {}


def radio_names(scan):
    return [r.get("name") for r in (scan or {}).get("radios") or [] if isinstance(r, dict) and r.get("name")]


def radio_text(r):
    state = "up" if r.get("up") else "down"
    kind = str(r.get("kind", "")).replace("-", " ")
    return f"{r.get('name', '?')}   {kind}   {r.get('driver') or 'no driver'}   {state}"


def build_panel(win, Switch):
    from PyQt6.QtCore import QObject, QRectF, Qt, pyqtSignal
    from PyQt6.QtGui import QColor, QPainter, QPalette, QPen
    from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
                                 QLineEdit, QListWidget, QPushButton, QSizePolicy, QTabWidget,
                                 QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
    from .foldertabs import FolderTabWidget

    class Signals(QObject):
        done = pyqtSignal(object)

    def button(text, slot):
        b = QPushButton(text)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.clicked.connect(slot)
        return b

    def card():
        box = QFrame()
        box.setObjectName("card")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(3)
        return box, lay

    def muted(text=""):
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setStyleSheet("font-size: 12pt; color: #9e9e9e;")
        return lab

    class Chart(QWidget):
        def __init__(self):
            super().__init__()
            self.setMinimumHeight(150)
            self.items = []

        def paintEvent(self, _):
            p = QPainter(self)
            text = self.palette().color(QPalette.ColorRole.WindowText)
            if not self.items:
                p.setPen(text)
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Scan to see the channels")
                p.end()
                return
            top = max([n for _, n, _ in self.items] + [1])
            slot = self.width() / len(self.items)
            base = self.height() - 22
            for i, (ch, n, strongest) in enumerate(self.items):
                x = i * slot
                h = (base - 16) * n / top
                colour = QColor("#37b24d" if n == 0 else "#f59f00" if n < 3 else "#e03131")
                p.setPen(QPen(QColor("#111111"), 1))
                p.setBrush(colour if n else QColor(text.red(), text.green(), text.blue(), 40))
                p.drawRect(QRectF(x + 2, base - max(h, 3), max(slot - 4, 2), max(h, 3)))
                p.setPen(text)
                p.drawText(QRectF(x, base + 2, slot, 16), Qt.AlignmentFlag.AlignCenter, str(ch))
                if n:
                    p.drawText(QRectF(x, base - h - 16, slot, 14), Qt.AlignmentFlag.AlignCenter, str(n))
            p.end()

    class Panel(FolderTabWidget):
        def __init__(self):
            super().__init__()
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.setObjectName("subtabs")
            self.scan_data, self.shown_networks, self.running = {}, [], False
            self.signals = Signals()
            self.signals.done.connect(self.show_scan)
            self.addTab(self.build_networks(), "Networks")
            self.addTab(self.build_channels(), "Channels")
            self.addTab(self.build_radios(), "Radios and saved")
            from . import sense
            self.sense = sense.build_page(win)
            self.addTab(self.sense, "Sense")
            from . import survey
            self.survey = survey.build_page(win, self.sense_network)
            self.addTab(self.survey, "Survey")
            self.refresh_saved()

        def sense_network(self, bssid, channel):
            """Survey's "Sense this network": show the Sense page and have it watch that one access point."""
            self.setCurrentIndex(self.indexOf(self.sense))
            self.sense.watch_only(bssid, channel)

        # ---- pages
        def build_networks(self):
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(4, 2, 4, 2)
            lay.setSpacing(3)
            head, hl = card()
            self.status = QLabel("Press Scan to listen for networks. Passive only.")
            self.status.setStyleSheet("font-size: 13pt; font-weight: bold;")
            self.status.setWordWrap(False)
            self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            row = QHBoxLayout()
            row.setSpacing(6)
            self.scan_b = button("Scan", self.scan)
            self.radio = QComboBox()
            self.radio.addItem("All radios", "")
            self.band = QComboBox()
            for key, label in BANDS:
                self.band.addItem(label, key)
            self.find = QLineEdit()
            self.find.setPlaceholderText("Filter by name or address")
            for w in (self.radio, self.band):
                w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                w.currentIndexChanged.connect(self.apply_filters)
            self.find.textChanged.connect(self.apply_filters)
            self.save = Switch()
            self.save.clicked.connect(self.toggle_save)
            self.save_on = False
            row.addWidget(self.scan_b)
            row.addWidget(self.status, 3)
            row.addWidget(self.radio, 1)
            row.addWidget(self.band, 1)
            row.addWidget(self.find, 2)
            row.addWidget(QLabel("Save each scan"))
            row.addWidget(self.save)
            hl.addLayout(row)
            lay.addWidget(head)
            box, bl = card()
            bl.setContentsMargins(2, 2, 2, 2)
            self.table = QTableWidget(0, 6)
            self.table.setHorizontalHeaderLabels(["Network", "Ch", "Band", "Signal", "Security", "Heard by"])
            self.table.verticalHeader().setVisible(False)
            self.table.verticalHeader().setDefaultSectionSize(24)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            h = self.table.horizontalHeader()
            h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            for c in (1, 2, 3, 4, 5):
                h.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
            bl.addWidget(self.table)
            lay.addWidget(box, 1)
            return page

        def build_channels(self):
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(4, 2, 4, 2)
            lay.setSpacing(3)
            box, bl = card()
            bar = QHBoxLayout()
            self.chart_band = QComboBox()
            self.chart_band.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.chart_band.addItem("2.4 GHz", "2.4")
            self.chart_band.addItem("5 GHz", "5")
            self.chart_band.currentIndexChanged.connect(self.update_chart)
            bar.addWidget(QLabel("Networks per channel"))
            bar.addStretch(1)
            bar.addWidget(self.chart_band)
            bl.addLayout(bar)
            self.chart = Chart()
            bl.addWidget(self.chart, 1)
            self.advice = muted("")
            bl.addWidget(self.advice)
            lay.addWidget(box, 1)
            return page

        def build_radios(self):
            page = QWidget()
            lay = QHBoxLayout(page)
            left, ll = card()
            ll.addWidget(muted("Radios"))
            self.radio_list = QListWidget()
            self.radio_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            ll.addWidget(self.radio_list)
            lay.addWidget(left, 1)
            right, rl = card()
            rl.addWidget(muted("Saved scans (private, in ~/aiov2-captures)"))
            self.saved = QListWidget()
            self.saved.itemSelectionChanged.connect(lambda: self.load_b.setEnabled(self.saved.currentRow() >= 0))
            rl.addWidget(self.saved, 1)
            self.load_b = button("Show this scan", self.load_saved)
            self.load_b.setEnabled(False)
            rl.addWidget(self.load_b)
            lay.addWidget(right, 1)
            return page

        # ---- scanning
        def toggle_save(self):
            self.save_on = not self.save_on
            self.save.show_state(self.save_on)

        def scan(self):
            if self.running:
                return
            self.running = True
            self.scan_b.setEnabled(False)
            self.status.setText("Scanning...")
            args = scan_args(self.radio.currentData() or "", self.save_on)

            def work():
                try:
                    out = subprocess.run(args, text=True, capture_output=True, timeout=SCAN_TIMEOUT).stdout
                    data = json.loads(out)
                except Exception:
                    data = {}
                self.signals.done.emit(data if isinstance(data, dict) else {})

            threading.Thread(target=work, daemon=True).start()

        def show_scan(self, scan):
            self.running = False
            self.scan_b.setEnabled(True)
            if not scan or not isinstance(scan.get("networks"), list):
                self.status.setText("No scan result. Is the Wi-Fi radio up? Try: aiov2_ctl --wifi radios")
                return
            self.set_scan(scan, "")
            self.refresh_saved()

        def set_scan(self, scan, note):
            self.scan_data = scan
            from . import registry
            new, events = win.bssid_registry.observe_wifi_scan((scan or {}).get("networks"))
            if new:
                win.bssid_registry.save()
            for event in events:                                    # a toast, if that kind is switched on in Settings
                win.notifier.add(*registry.describe(event))
            keep = self.radio.currentData()
            self.radio.blockSignals(True)
            self.radio.clear()
            self.radio.addItem("All radios", "")
            for name in radio_names(scan):
                self.radio.addItem(name, name)
            idx = self.radio.findData(keep)
            self.radio.setCurrentIndex(idx if idx >= 0 else 0)
            self.radio.blockSignals(False)
            self.note = note
            self.apply_filters()
            self.radio_list.clear()
            self.radio_list.addItems([radio_text(r) for r in scan.get("radios") or [] if isinstance(r, dict)])
            self.update_chart()

        def apply_filters(self, *_):
            nets = filter_networks(self.scan_data.get("networks"), self.radio.currentData() or "",
                                   self.band.currentData() or "", self.find.text())
            self.shown_networks = nets
            rows = merged_rows(nets)
            self.table.setRowCount(len(rows))
            for r, row in enumerate(rows):
                for c, key in enumerate(("ssid", "channel", "band", "signal", "security", "heard")):
                    self.table.setItem(r, c, QTableWidgetItem(row[key]))
            if self.scan_data:
                self.status.setText((self.note + "  " if getattr(self, "note", "") else "") +
                                    summary_line(self.scan_data, rows))

        def update_chart(self, *_):
            band = self.chart_band.currentData()
            self.chart.items = channel_chart(self.scan_data.get("networks"), band) if self.scan_data else []
            self.chart.update()
            self.advice.setText("\n".join(channel_advice(self.scan_data.get("networks"))) if self.scan_data else "")

        # ---- saved scans
        def refresh_saved(self):
            self.saved_items = saved_scans()
            self.saved.clear()
            self.saved.addItems([label for _, label in self.saved_items])

        def load_saved(self):
            row = self.saved.currentRow()
            if 0 <= row < len(self.saved_items):
                scan = load_scan(self.saved_items[row][0])
                if scan:
                    self.set_scan(scan, f"Saved scan from {self.saved_items[row][1]}.")
                    self.setCurrentIndex(0)
                else:
                    self.status.setText("That saved scan could not be read.")

    return Panel()
