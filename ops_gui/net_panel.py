"""
The Network tab: your wired link and the hosts around you, through `aiov2_ctl --net` (one backend with the CLI).

What it does by default is only read: the link (cable, speed, address) and the neighbours the system already
knows. Listening is passive (it hears what hosts announce, sends nothing) and takes thirty seconds. The two
active actions, a sweep of your own subnet and a port check of one host, ask first, only work on your own private
network, and say what they will do. Nothing here attacks, spoofs or guesses passwords.
The helpers at the top are pure so they can be tested without a cable.
"""
import json
import subprocess
import threading


LISTEN_ARGS = ["sudo", "-n", "aiov2_ctl", "--net", "listen", "--seconds", "30", "--json"]
SWEEP_ARGS = ["sudo", "-n", "aiov2_ctl", "--net", "sweep", "--yes", "--json"]
STATUS_ARGS = ["aiov2_ctl", "--net", "status", "--json"]
NEIGH_ARGS = ["aiov2_ctl", "--net", "neighbors", "--json"]


def ports_args(host):
    return ["aiov2_ctl", "--net", "ports", host, "--yes", "--json"]


def link_summary(info):
    """(headline, detail, level) for the link card from one interface dict of `--net status --json`."""
    if not info:
        return "NO WIRED PORT", "No wired network interface was found.", "off"
    name = info.get("name", "eth")
    if not info.get("carrier"):
        return f"{name.upper()}  NO LINK", info.get("hint") or "Plug a cable in.", "warn"
    speed = f"{info['speed_mbps']} Mbit/s" if info.get("speed_mbps") else "link up"
    addrs = ", ".join(info.get("addresses") or []) or "waiting for an address (DHCP)"
    return f"{name.upper()}  {speed}", addrs, "good"


def can_probe(info):
    """Active actions need a link and an address."""
    return bool(info and info.get("carrier") and info.get("addresses"))


def merge_hosts(*lists):
    """One row per MAC from neighbours, a listen and a sweep: the addresses, names and sources of each."""
    hosts = {}
    for source, rows in lists:
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            mac = (r.get("mac") or "").lower()
            if not mac:
                continue
            h = hosts.setdefault(mac, {"mac": mac, "ip": "", "name": "", "vendor": "", "via": []})
            for key in ("ip", "name", "vendor"):
                if r.get(key) and not h[key]:
                    h[key] = r[key]
            if source not in h["via"]:
                h["via"].append(source)
    rows = list(hosts.values())

    def order(h):
        try:
            return tuple(int(x) for x in h["ip"].split(", ")[0].split("."))
        except ValueError:
            return (999, 0, 0, 0)
    return sorted(rows, key=order)


AIR_KEEP = 600.0                         # a device last heard on the air longer ago than this is not matched


def wifi_note(mac, air, now):
    """What Survey has heard of this host on Wi-Fi, matched by address: the network it is using and how strongly
    it is heard here. '-' when Survey has not heard it (it may be wired, or Survey may not have run)."""
    seen = (air or {}).get((mac or "").lower())
    if not seen or now - (seen.get("last") or 0) > AIR_KEEP:
        return "-"
    rssi = seen.get("rssi")
    return (seen.get("network") or "a network") + (f", {rssi:.0f} dBm" if isinstance(rssi, (int, float)) else "")


def host_columns(air):
    """The table's headings. The Wi-Fi column is only there once Survey has heard devices to match against."""
    return ["Address", "Name", "Maker", "MAC", "Seen by"] + (["On Wi-Fi"] if air else [])


def host_table_rows(hosts, air=None, now=0.0):
    rows = [(h["ip"] or "-", h["name"] or "-", h["vendor"] or "-", h["mac"], ", ".join(h["via"])) for h in hosts]
    return [row + (wifi_note(h["mac"], air, now),) for row, h in zip(rows, hosts)] if air else rows


def hosts_by_mac(hosts):
    """{mac: {'ip', 'name'}} for Survey, so a device it hears can be shown by the name this page knows it by."""
    return {h["mac"]: {"ip": h.get("ip", ""), "name": h.get("name", "")} for h in hosts if h.get("mac")}


def parse_json_list(text, key=None):
    """The list a --json command printed, or [] when it printed an error or nothing."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return []
    if key and isinstance(data, dict):
        data = data.get(key)
    return data if isinstance(data, list) else []


def error_line(text):
    for line in (text or "").splitlines():
        if line.startswith("Error:"):
            return line[6:].strip()
    return ""


def sweep_warning(info):
    addr = ", ".join((info or {}).get("addresses") or [])
    return (f"Sweep {addr}? This sends one ARP request to every address on your own subnet, about a hundred a "
            "second, and lists who answers. It never leaves your network. Only do this on a network you own or "
            "have permission to look at.")


def ports_warning(host):
    return (f"Check {host}? This opens an ordinary TCP connection to about twenty common ports on that one "
            "machine to see which are open. Only do this on a machine you own or have permission to check.")


def build_panel(win, Switch=None):
    from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
    from PyQt6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                                 QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

    class Signals(QObject):
        done = pyqtSignal(str, str)

    class Page(QWidget):
        def __init__(self):
            super().__init__()
            self.info, self.lists, self.busy = None, {"known": [], "heard": [], "swept": []}, False
            self.signals = Signals()
            self.signals.done.connect(self.on_done)
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.refresh)
            root = QVBoxLayout(self)
            head = QFrame()
            head.setObjectName("card")
            hl = QVBoxLayout(head)
            link_tag = QLabel("WIRED LINK")
            link_tag.setObjectName("tag")
            tag_row = QHBoxLayout()
            tag_row.addWidget(link_tag)
            tag_row.addStretch(1)
            hl.addLayout(tag_row)
            self.title = QLabel("Looking for a wired port...")
            self.title.setStyleSheet("font-size: 16pt; font-weight: bold;")
            self.detail = QLabel("")
            self.detail.setWordWrap(True)
            self.detail.setStyleSheet("font-size: 12pt; color: #9e9e9e;")
            hl.addWidget(self.title)
            hl.addWidget(self.detail)
            row = QHBoxLayout()
            self.listen_b = self.button("Listen 30s (passive)", self.listen)
            self.sweep_b = self.button("Sweep my subnet", self.sweep)
            self.ports_b = self.button("Check ports", self.check_ports)
            self.refresh_b = self.button("Refresh", self.refresh)
            for b in (self.listen_b, self.sweep_b, self.ports_b, self.refresh_b):
                row.addWidget(b)
            row.addStretch(1)
            hl.addLayout(row)
            root.addWidget(head)
            box = QFrame()
            box.setObjectName("card")
            bl = QVBoxLayout(box)
            hosts_tag = QLabel("NEIGHBOURS")
            hosts_tag.setObjectName("tag")
            hosts_row = QHBoxLayout()
            hosts_row.addWidget(hosts_tag)
            hosts_row.addStretch(1)
            bl.addLayout(hosts_row)
            self.table = QTableWidget(0, 5)
            self.table.setHorizontalHeaderLabels(["Address", "Name", "Maker", "MAC", "Seen by"])
            self.table.verticalHeader().setVisible(False)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            for col in (0, 2, 3, 4):                                  # addresses and MACs are shown whole
                self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
            self.table.itemSelectionChanged.connect(self.sync)
            bl.addWidget(self.table)
            root.addWidget(box, 1)
            self.note = QLabel("Listening and the neighbour list only read. Sweep and Check ports ask first.")
            self.note.setWordWrap(True)
            self.note.setStyleSheet("font-size: 12pt; color: #9e9e9e;")
            root.addWidget(self.note)
            self.hosts = []
            self.sync()

        @staticmethod
        def button(text, slot):
            b = QPushButton(text)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(slot)
            return b

        # ---- lifecycle: it only works while it is showing
        def showEvent(self, event):
            super().showEvent(event)
            self.fill()                                       # Survey may have heard more since last time
            self.refresh()
            self.timer.start(5000)

        def hideEvent(self, event):
            super().hideEvent(event)
            self.timer.stop()

        def sync(self):
            ok = can_probe(self.info) and not self.busy
            self.listen_b.setEnabled(bool(self.info and self.info.get("carrier")) and not self.busy)
            self.sweep_b.setEnabled(ok)
            row = self.table.currentRow()
            self.ports_b.setEnabled(ok and 0 <= row < len(self.hosts) and bool(self.hosts[row]["ip"]))

        def run(self, tag, args, timeout):
            if self.busy:
                return
            self.busy = True
            self.sync()

            def work():
                try:
                    out = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
                    text = out.stdout or out.stderr
                except Exception as e:
                    text = f"Error: {e}"
                self.signals.done.emit(tag, text)

            threading.Thread(target=work, daemon=True).start()

        def refresh(self):
            self.run("status", STATUS_ARGS, 10)

        def listen(self):
            self.note.setText("Listening for 30 seconds. Nothing is sent.")
            self.run("listen", LISTEN_ARGS, 60)

        def sweep(self):
            if win.ask(sweep_warning(self.info)):
                self.note.setText("Sweeping your subnet...")
                win.activity.add("network sweep started")
                self.run("sweep", SWEEP_ARGS, 120)

        def check_ports(self):
            row = self.table.currentRow()
            if 0 <= row < len(self.hosts):
                host = self.hosts[row]["ip"].split(", ")[0]
                if host and win.ask(ports_warning(host)):
                    self.note.setText(f"Checking {host}...")
                    win.activity.add(f"port check of {host}")
                    self.run("ports", ports_args(host), 60)

        def on_done(self, tag, text):
            self.busy = False
            why = error_line(text)
            if tag == "status":
                rows = parse_json_list(text, "interfaces")
                self.info = rows[0] if rows else None
                head, detail, level = link_summary(self.info)
                self.title.setText(head)
                self.detail.setText(detail)
                self.run("neigh", NEIGH_ARGS, 10)
                return
            if tag == "neigh":
                self.lists["known"] = parse_json_list(text)
            elif tag == "listen":
                if why:
                    self.note.setText(why)
                else:
                    self.lists["heard"] = parse_json_list(text)
                    self.note.setText(f"Heard {len(self.lists['heard'])} hosts announce themselves.")
            elif tag == "sweep":
                if why:
                    self.note.setText(why)
                else:
                    self.lists["swept"] = parse_json_list(text)
                    self.note.setText(f"{len(self.lists['swept'])} hosts answered the sweep.")
                    win.activity.add(f"network sweep: {len(self.lists['swept'])} hosts")
            elif tag == "ports":
                try:
                    found = json.loads(text)
                    self.note.setText(f"{found['host']}: open ports {', '.join(map(str, found['open'])) or 'none'} "
                                      f"(checked {found['checked']}).")
                except (ValueError, KeyError, TypeError):
                    self.note.setText(why or "No answer from the port check.")
            self.fill()
            self.sync()

        def fill(self):
            self.hosts = merge_hosts(("known", self.lists["known"]), ("heard", self.lists["heard"]),
                                     ("swept", self.lists["swept"]))
            import time
            air = getattr(win, "air_devices", None)                   # what Survey has heard, if it has run
            win.net_hosts = hosts_by_mac(self.hosts)                  # and what Survey can name its devices by
            columns = host_columns(air)
            if self.table.columnCount() != len(columns):
                self.table.setColumnCount(len(columns))
                self.table.setHorizontalHeaderLabels(columns)
                self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
                for extra in range(5, len(columns)):                  # the Wi-Fi column is as wide as what it says
                    self.table.horizontalHeader().setSectionResizeMode(extra, QHeaderView.ResizeMode.ResizeToContents)
            rows = host_table_rows(self.hosts, air, time.time())
            self.table.setRowCount(len(rows))
            for r, row in enumerate(rows):
                for c, text in enumerate(row):
                    self.table.setItem(r, c, QTableWidgetItem(text))

    return Page()
