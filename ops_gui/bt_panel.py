"""
The Bluetooth page: the Bluetooth Low Energy devices advertising around you, heard passively.

It has a tab of its own, shown while the internal card's Bluetooth radio is there (the USB rail is on). It runs
`aiov2_ctl --bt-scan` (root through an exact sudoers rule) on that radio, which the app did not use before. The scan is passive: the radio only receives what devices broadcast anyway. It never sends a
scan request, never connects, never pairs and never advertises.

What it can and cannot tell you: a name (if the device sends one), who made it (from the maker number it sends, or
from a public address), what sort of address it uses and how strong its signal is here. It cannot tell you where a
device is, how far away it is, or who it belongs to, and it does not try. Phones, watches and earbuds change their
address every few minutes for privacy, so one device can appear as several rows over time.
The Known column and the line about several places come from bt_memory, which holds the rules and the wording.
Item trackers (AirTag and other Find My accessories, SmartTag, Tile, Chipolo, Google Find My Device) are named from
markers in the advertisement by bt_trackers. That only says what kind of device it claims to be.
The helpers at the top are pure so they can be tested without a radio or a screen.
"""
import json
import os
import subprocess
import threading
import time

from . import bt_memory, bt_trackers, position, sense, survey

ADAPTER = "hci1"                         # the internal USB card's Bluetooth radio
SECONDS = 600
COLUMNS = (("Name", "name"), ("Maker", "maker"), ("Address", "addr"), ("Address type", "kind"), ("Known", "known"),
           ("Signal", "signal"),
           ("Best", "best"), ("Heard", "count"), ("Seen", "seen"), ("Says it is", "what"))
NO_NAME = "(sends no name)"
SAVE_EVERY = 300                         # seconds between timed saves of the position log while a scan runs


def scan_args():
    """The aiov2_ctl command, in exactly the form the sudoers rule allows."""
    return ["sudo", "-n", "aiov2_ctl", "--bt-scan", "--adapter", ADAPTER, "--seconds", str(SECONDS), "--json"]


def adapter_available(sys_bt="/sys/class/bluetooth"):
    return os.path.isdir(os.path.join(sys_bt, ADAPTER))


def parse_snapshot(line):
    """One scan snapshot from the listener, or None for anything else."""
    try:
        snap = json.loads(line)
    except (TypeError, ValueError):
        return None
    if not isinstance(snap, dict) or not isinstance(snap.get("devices"), list) or not isinstance(snap.get("t"), (int, float)):
        return None
    if not all(isinstance(d, dict) and isinstance(d.get("addr"), str) for d in snap["devices"]):
        return None
    return snap


def ago(now, then):
    s = max(0, int(now - then))
    return "now" if s < 3 else f"{s}s ago" if s < 90 else f"{s // 60}m ago"


def dbm_text(value):
    return f"{value:.0f} dBm" if isinstance(value, (int, float)) else "-"


def device_rows(snap, now=None, memory=None, travel=None, session_start=None):
    """One row of text per device, strongest signal first, as the listener sent them. Known says whether the
    address was heard before this scan (from `memory`) and at how many places in this one (from `travel`)."""
    now = snap["t"] if now is None else now
    session_start = now if session_start is None else session_start
    rows = []
    for d in snap["devices"]:
        facts = travel.tracker_facts(d["addr"]) if travel else None
        known = bt_memory.known_text(d.get("kind"), memory.known(d["addr"]) if memory else None, now,
                                     session_start, travel.places(d["addr"]) if travel else 0)
        if facts:
            known = ", ".join(p for p in (known, bt_memory.tracker_mark(facts)) if p)
        rows.append({
            "known": known,
            "name": d.get("name") or NO_NAME, "maker": d.get("maker") or "-", "addr": d["addr"],
            "kind": d.get("kind") or "-", "signal": dbm_text(d.get("rssi")), "best": dbm_text(d.get("best")),
            "count": str(d.get("count", 0)), "seen": ago(now, d.get("last", now)),
            "what": what_text(d),
        })
    return rows


def known_tooltip(device, travel=None):
    """The Known cell's tooltip: how addresses are remembered, then the tracker facts if its address met the rule."""
    facts = travel.tracker_facts(device["addr"]) if travel else None
    return "\n\n".join(t for t in (bt_memory.known_tip(device.get("kind") or ""),
                                    bt_memory.tracker_text(facts) if facts else "") if t)


def what_text(device):
    """The Says it is column: a tracker label first when the advertisement carries a known marker, then the
    services it lists (without repeating the label)."""
    found = bt_trackers.identify(device)
    services = [s for s in (device.get("services") or []) if not found or s != found["label"]]
    return ", ".join(([found["label"]] if found else []) + services) or "-"


def tracker_tip(device):
    """Why a row was named a tracker, for its tooltip. Empty when it was not."""
    found = bt_trackers.identify(device)
    return f"Named from the advertisement: {found['basis']}. {bt_trackers.NOTE}" if found else ""


def summary(snap):
    """A plain count of what is in the table. Rows, not people or even devices: private addresses change."""
    devices = snap["devices"]
    named = sum(1 for d in devices if d.get("name"))
    private = sum(1 for d in devices if str(d.get("kind", "")).startswith("private"))
    text = f"{len(devices)} addresses heard" if len(devices) != 1 else "1 address heard"
    parts = ([f"{named} with a name"] if named else []) + ([f"{private} private (they change)"] if private else [])
    return text + (": " + ", ".join(parts) if parts else "")


class BtLog(survey.WigleLog):
    """Bluetooth devices heard while the GPS had a fix, each kept with the position where it was strongest, in the
    same WiGLE CSV file format as the Wi-Fi wardriving log (rows of type BLE)."""
    prefix = "bluetooth"

    def add(self, snap, tpv, now=None):
        """Record the devices in a snapshot at the current position. Returns how many are new."""
        if not position.good_fix(tpv):
            return 0
        now = snap.get("t") if now is None else now
        new = 0
        for d in snap["devices"]:
            addr, rssi = d.get("addr"), d.get("rssi")
            if not addr or not isinstance(rssi, (int, float)) or not survey.heard_now(d, snap.get("t")):
                continue
            known = self.nets.get(addr)
            if known is None:
                new += 1
            if known is None or rssi > known["rssi"]:
                err = max(tpv.get("epx") or 0, tpv.get("epy") or 0)
                self.nets[addr] = {"ssid": d.get("name") or "", "auth": "[LE]", "type": "BLE", "channel": 0,
                                   "first": known["first"] if known else now, "rssi": rssi,
                                   "lat": tpv["lat"], "lon": tpv["lon"], "alt": tpv.get("alt") or 0.0, "acc": err or 10.0}
        return new


def log_note(rail_on, logging, tpv, count):
    """The one line under the table about logging with position: what is happening, or what it is waiting for."""
    if not rail_on:
        return "Switch the GPS rail on to log what is heard with its position."
    if not logging:
        return "The GPS rail is on: tick Log with position to keep where each device was heard."
    if not position.good_fix(tpv):
        return "Waiting for a GPS fix. Nothing is logged without one."
    return f"Logging: {count} address{'es' if count != 1 else ''} with a position."


RESUME_LIMIT = 2                         # times one scan carries on by itself after the radio restarts
RESUME_WAIT = 5.0                        # seconds the card is given to come back before the scan starts again
RESUME_GIVE_UP = 30.0                    # and how long to wait for it before saying so and stopping


def radio_restarted(error):
    """True when the scan died the way it does when the card's radio restarts under it.
    The internal card now and then fails to answer one command. The kernel logs 'hci1: command tx timeout',
    resets the card, and it is back within a few seconds. It has happened with nothing scanning at all (one
    plain read after hours idle), and could not be made to happen on demand: not by idle gaps up to eight
    minutes, nor by the card's Wi-Fi half scanning at the same time. The scan command dies with a broken pipe."""
    low = (error or "").lower()
    return "broken pipe" in low or "errno 32" in low or "network is down" in low or "errno 100" in low


def adapter_up(text):
    """True when `hciconfig hci1` says the radio is switched on. After a restart the card is listed again a few
    seconds before it is up, and a scan started in between dies at once."""
    return any("UP" in line.split() and "RUNNING" in line.split() for line in (text or "").splitlines())


def adapter_ready(run=subprocess.run):
    """Listed and switched on. Asking is a local question to the kernel: nothing is sent to the card."""
    if not adapter_available():
        return False
    try:
        return adapter_up(run(["hciconfig", ADAPTER], capture_output=True, text=True, timeout=5).stdout)
    except (OSError, subprocess.SubprocessError):
        return False


def resume_step(resumes, waited, adapter_there, limit=RESUME_LIMIT, wait=RESUME_WAIT, give_up=RESUME_GIVE_UP):
    """What to do while waiting for a restarted radio: 'wait', 'start' again, or 'stop' for good.
    `resumes` counts this one included. Past the limit it stops: a radio restarting over and over is a fault
    to be told about, not one to paper over."""
    if resumes > limit or waited > give_up:
        return "stop"
    return "start" if adapter_there and waited >= wait else "wait"


def scan_again(error, logging, code=0):
    """True when a scan that has just ended should be started again by itself: it ran its full time without an
    error, and it is logging with position. A walk lasts longer than one scan; a scan nobody is logging is left
    to end, so the radio is not kept listening for no reason. `code` is what the scan command ended with: anything
    but 0 is a failure even when it printed no reason, and a failed scan is never started again blindly."""
    return not (error or "").strip() and code == 0 and bool(logging)


def stop_reason(error):
    """What to tell someone when the scan ended by itself, from the last line the command printed."""
    text = (error or "").strip()
    low = text.lower()
    if not text:
        return ""
    if radio_restarted(text):
        return ("The Bluetooth radio keeps restarting itself, so the scan has stopped. It comes back by itself in "
                "a few seconds: start the scan again, or switch the USB rail off and on if it carries on.")
    if "password is required" in low or "not allowed" in low or "sudo:" in low:
        return "The scan is not allowed to run yet. Run 'sudo aiov2_ctl --setup-sudoers' once, then try again."
    return text[len("Error: "):] if text.startswith("Error: ") else text


class Feed(sense.AirFeed):
    """The scan process. Stopping it is enough: it switches scanning off itself, and no Wi-Fi radio is involved."""

    def stop(self, runner=None):
        if self.alive():
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def build_page(win):
    from PyQt6.QtCore import Qt, QTimer, pyqtSignal

    from . import style
    from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                                 QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

    class Page(QWidget):
        memory_read = pyqtSignal(object)                             # the file, read off the screen's thread

        def __init__(self):
            super().__init__()
            self.feed, self.log, self.saved_at = None, BtLog(), 0.0
            # What was heard on other days (a private file) and where each fixed address was heard on this
            # outing (never written anywhere). Neither is reset by the scan restarting itself.
            self.memory, self.travel = bt_memory.Memory(), bt_memory.Travel()
            self.session_start, self.cleared = time.time(), bt_memory.cleared()
            self.last_snap, self.row_info, self.picked, self.banner_mode = None, [], None, None
            self.memory_read.connect(self.use_memory)
            threading.Thread(target=lambda: self.memory_read.emit(bt_memory.Memory().load()), daemon=True).start()
            self.resumes, self.waiting = 0, 0.0                    # radio restarts ridden through; waiting since
            self.heard = 0                                          # addresses in the last scan, for the Home screen
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.tick)
            self.context_timer = QTimer(self)                        # follows the GPS rail while the page shows
            self.context_timer.timeout.connect(self.sync_context)
            root = QVBoxLayout(self)
            root.setContentsMargins(4, 2, 4, 2)
            root.setSpacing(3)
            head = QFrame()
            head.setObjectName("card")
            hl = QVBoxLayout(head)
            hl.setContentsMargins(10, 4, 10, 4)
            hl.setSpacing(3)
            self.state = QLabel("Bluetooth scan is off.")
            self.state.setStyleSheet("font-size: 14pt; font-weight: bold;")
            self.state.setWordWrap(True)
            self.go = QPushButton("Start scan")
            self.go.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.go.clicked.connect(self.toggle)
            row = QHBoxLayout()
            row.setSpacing(6)
            self.drive = QCheckBox("Log with position")
            self.drive.setToolTip("Needs the GPS rail on and a fix")
            self.export = QPushButton("Save log")
            for w in (self.drive, self.export):
                w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.drive.toggled.connect(lambda _=False: self.sync_context())
            self.export.clicked.connect(self.save_log)
            row.addWidget(self.state, 1)
            row.addWidget(self.drive)
            row.addWidget(self.export)
            row.addWidget(self.go)
            hl.addLayout(row)
            self.detail = QLabel(
                "Listens for the Bluetooth devices advertising nearby, using the internal card. Nothing is sent: it "
                "does not ask devices for more, connect or pair. Phones, watches and earbuds change their address "
                "every few minutes, so one device can show as several rows.")
            self.detail.setWordWrap(True)
            self.detail.setStyleSheet("font-size: 11pt; color: #9e9e9e;")
            hl.addWidget(self.detail)
            self.log_line = QLabel("")
            self.log_line.setStyleSheet("font-size: 11pt; color: #9e9e9e;")
            hl.addWidget(self.log_line)
            self.places_line = QLabel("")
            self.places_line.setWordWrap(True)
            self.places_line.setStyleSheet("font-size: 11pt; color: #9e9e9e;")
            hl.addWidget(self.places_line)
            self.banner = QLabel("")                                 # addresses heard at several places, if any
            self.banner.setWordWrap(True)
            self.banner.hide()
            hl.addWidget(self.banner)
            pick = QHBoxLayout()
            pick.setSpacing(6)
            self.pick_line = QLabel("")
            self.pick_line.setWordWrap(True)
            self.pick_line.setStyleSheet("font-size: 11pt; color: #9e9e9e;")
            self.mine = QPushButton("Mark as mine")
            self.mine.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.mine.clicked.connect(self.toggle_mine)
            pick.addWidget(self.pick_line, 1)
            pick.addWidget(self.mine)
            hl.addLayout(pick)
            self.pick_line.hide()
            self.mine.hide()
            root.addWidget(head)
            self.table = QTableWidget(0, len(COLUMNS))
            self.table.setHorizontalHeaderLabels([title for title, _ in COLUMNS])
            self.table.verticalHeader().setVisible(False)
            self.table.verticalHeader().setDefaultSectionSize(24)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            for c in range(len(COLUMNS)):
                self.table.horizontalHeader().setSectionResizeMode(
                    c, QHeaderView.ResizeMode.Stretch if c in (0, len(COLUMNS) - 1) else QHeaderView.ResizeMode.ResizeToContents)
            self.table.cellClicked.connect(self.pick_row)
            root.addWidget(self.table, 1)
            self.tracker_note = QLabel(bt_trackers.NOTE + " " + bt_memory.TRACKER_HELP)
            self.tracker_note.setWordWrap(True)
            self.tracker_note.setStyleSheet("font-size: 10pt; color: #9e9e9e;")
            root.addWidget(self.tracker_note)

        def use_memory(self, loaded):
            """The file has been read. Anything heard in the moment before it arrived is kept."""
            for addr, entry in self.memory.entries.items():
                loaded.entries.setdefault(addr, entry)
            loaded.dirty = self.memory.dirty
            self.memory = loaded

        def check_cleared(self):
            """Clear local data in Settings removed the file: forget what is held here too."""
            if self.cleared != bt_memory.cleared():
                self.cleared = bt_memory.cleared()
                self.memory.clear()
                self.travel.clear()
                self.session_start = time.time()

        def save_memory(self, wait=False):
            """Write the memory file: in the background while scanning, at once when stopping."""
            self.check_cleared()
            text = self.memory.text_to_save(time.time())
            if wait:
                bt_memory.write_private(bt_memory.memory_path(), text)
            else:
                threading.Thread(target=bt_memory.write_private, args=(bt_memory.memory_path(), text),
                                 daemon=True).start()

        def pick_row(self, row, _column):
            if 0 <= row < len(self.row_info):
                self.picked = self.row_info[row]
                self.show_pick()

        def show_pick(self):
            if not self.picked:
                return
            addr, kind, name = self.picked
            can = bt_memory.rememberable(kind) and self.memory.known(addr) is not None
            mine = self.memory.is_mine(addr)
            self.pick_line.setText(bt_memory.pick_line(addr, kind, name, mine))
            self.mine.setText("Not mine" if mine else "Mark as mine")
            self.mine.setEnabled(can)
            self.pick_line.show()
            self.mine.show()

        def toggle_mine(self):
            if not self.picked:
                return
            addr = self.picked[0]
            if self.memory.set_mine(addr, not self.memory.is_mine(addr)):
                self.save_memory()
                win.activity.add("Bluetooth: an address was marked as yours" if self.memory.is_mine(addr)
                                 else "Bluetooth: an address is no longer marked as yours")
            self.show_pick()
            if self.last_snap:
                self.fill_table(self.last_snap)
            self.sync_context()

        def show_places(self, rail_on, tpv):
            """The line about the several-places check, and the banner when an address has met the rule."""
            flagged = self.travel.flagged(self.memory)
            self.places_line.setText(bt_memory.places_note(rail_on, tpv, bool(self.feed), len(self.travel.tallies),
                                                           len(flagged)))
            text = bt_memory.banner_text(flagged)
            mode = "light" if getattr(win, "theme", "dark") == "light" else "dark"
            if mode != self.banner_mode:                             # readable on both themes
                self.banner_mode, tok = mode, style.TOKENS[mode]
                self.banner.setStyleSheet(f"font-size: 12pt; color: {tok['text']}; background: {tok['row']}; "
                                          f"border: 2px solid {tok['accent2']}; padding: 4px;")
            if text != self.banner.text():
                self.banner.setText(text)
            self.banner.setVisible(bool(text))

        def showEvent(self, event):
            super().showEvent(event)
            self.sync_context()
            self.context_timer.start(3000)

        def hideEvent(self, event):
            super().hideEvent(event)
            self.context_timer.stop()

        def sync_context(self, note=None):
            """Logging with position is only offered while the GPS rail is on; the line says where things stand."""
            rail_on = position.gps_rail_on(getattr(win, "snap", None))
            self.drive.setVisible(rail_on)
            self.export.setVisible(rail_on or bool(self.log.nets))
            logging = rail_on and self.drive.isChecked()
            # The position is asked for while logging or scanning; with the rail off there is none to ask for.
            tpv = position.current(win) if (logging or self.feed) else None
            self.log_line.setText(note or log_note(rail_on, logging, tpv, len(self.log.nets)))
            self.check_cleared()
            self.show_places(rail_on, tpv)
            return logging, tpv

        def save_log(self):
            if not self.log.nets:
                self.sync_context("Nothing to save yet: nothing has been heard with a GPS fix.")
                return
            path = self.log.save()
            self.sync_context(f"Saved {len(self.log.nets)} addresses to {path} (private).")
            win.activity.add(f"Bluetooth log saved: {os.path.basename(path)}")

        def toggle(self):
            self.stop() if (self.feed or self.waiting) else self.start()

        def start(self, resuming=False):
            if not adapter_available():
                self.state.setText("The internal Bluetooth radio is not there.")
                self.detail.setText("Switch the USB rail on, wait a few seconds, then try again.")
                return
            try:
                self.feed = Feed(args=scan_args(), parse=parse_snapshot)
            except OSError as exc:
                self.state.setText("Could not start the scan.")
                self.detail.setText(str(exc))
                return
            self.waiting = 0.0
            self.timer.start(1000)
            self.go.setText("Stop scan")
            self.state.setText("Listening...")
            if resuming == "again":                                 # logging a walk: same log, next ten minutes
                self.detail.setText("Still logging with position, so the scan started again by itself. Nothing "
                                    "is sent. Untick Log with position, or press Stop, to end it.")
                win.activity.add("Bluetooth scan started again (logging with position)")
                return
            if resuming:                                            # same scan: keep the list and the log
                self.detail.setText("The Bluetooth radio restarted itself and the scan has carried on. Nothing is "
                                    f"sent; it stops by itself after {SECONDS // 60} minutes.")
                win.activity.add("Bluetooth scan carried on after the radio restarted")
                return
            self.log, self.saved_at, self.resumes = BtLog(), time.time(), 0   # first timed save: five minutes on
            self.session_start = time.time()                        # "new" means first heard from here on
            self.detail.setText("Devices appear as they are heard. Nothing is sent; the scan stops by itself after "
                                f"{SECONDS // 60} minutes.")
            win.activity.add("Bluetooth scan started")

        def stop(self, why="Bluetooth scan is off."):
            self.timer.stop()
            self.waiting = 0.0
            if self.feed:
                self.feed.stop()
                self.feed = None
            self.go.setText("Start scan")
            self.state.setText(why)
            if self.log.nets:
                self.save_log()
            if self.memory.dirty:
                self.save_memory(wait=True)                         # the app may be closing: do not leave it to a thread
            win.activity.add("Bluetooth scan stopped")

        def leaving(self):
            if self.feed:
                self.stop()

        def tick(self):
            if self.waiting:                                        # the radio restarted: carry on when it is back
                step = resume_step(self.resumes, time.time() - self.waiting, adapter_ready())
                if step == "start":
                    self.start(resuming=True)
                elif step == "stop":
                    self.stop("The scan stopped.")
                    self.detail.setText(stop_reason("broken pipe"))
                return
            done = bool(self.feed) and self.feed.finished()     # asked first, so its last snapshots are not missed
            snaps = self.feed.records() if self.feed else []
            if snaps:
                self.show_snapshot(snaps[-1])
            if done and not snaps:
                error, code = self.feed.problem(), self.feed.exit_code()
                if radio_restarted(error) and self.resumes < RESUME_LIMIT:
                    self.resumes += 1
                    self.feed, self.waiting = None, time.time()
                    self.state.setText("The Bluetooth radio restarted itself.")
                    self.detail.setText("It comes back in a few seconds and the scan carries on by itself.")
                    win.activity.add("Bluetooth radio restarted during the scan")
                    return
                logging, _tpv = self.sync_context()
                if scan_again(error, logging, code):
                    if self.log.nets:
                        self.save_log()                             # each ten minutes is on disk before the next
                    self.feed, self.resumes = None, 0
                    self.start(resuming="again")
                    return
                self.stop("The scan stopped." if error else "The scan finished.")
                self.detail.setText(stop_reason(error) if error else
                                    f"It ran its {SECONDS // 60} minutes. Press Start scan to listen again.")

        def fill_table(self, snap):
            rows = device_rows(snap, memory=self.memory, travel=self.travel, session_start=self.session_start)
            self.heard = len(rows)
            self.row_info = [(d["addr"], d.get("kind") or "", d.get("name") or "") for d in snap["devices"]]
            self.table.setRowCount(len(rows))
            for r, row in enumerate(rows):
                for c, (_, key) in enumerate(COLUMNS):
                    item = QTableWidgetItem(row[key])
                    if key == "known":
                        item.setToolTip(known_tooltip(snap["devices"][r], self.travel))
                    elif key == "what" and tracker_tip(snap["devices"][r]):
                        item.setToolTip(tracker_tip(snap["devices"][r]))
                    self.table.setItem(r, c, item)

        def show_snapshot(self, snap):
            self.check_cleared()
            self.last_snap = snap
            self.memory.see_snapshot(snap)
            if self.memory.due(time.time()):
                self.save_memory()
            logging, tpv = self.sync_context()
            self.travel.add(snap, tpv)                              # notes nothing without a usable fix
            for tally in self.travel.to_tell(self.memory):
                win.notify_user(*bt_memory.describe(tally))
            for addr, facts in self.travel.trackers_to_tell(self.memory):
                win.notify_user(*bt_memory.describe_tracker(addr, facts))
            self.show_places(position.gps_rail_on(getattr(win, "snap", None)), tpv)
            self.fill_table(snap)
            self.state.setText("Listening: " + summary(snap))
            if logging and position.good_fix(tpv):
                self.log.add(snap, tpv)
                self.sync_context()
                if time.time() - self.saved_at > SAVE_EVERY and self.log.nets:   # keep what a long walk has gathered
                    self.saved_at = time.time()
                    self.save_log()

    return Page()
