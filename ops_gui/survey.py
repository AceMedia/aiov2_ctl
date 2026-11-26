"""
The Survey page: what the internal Wi-Fi radio hears around you, passively.

It runs `aiov2_ctl --wifi-survey` (root through an exact sudoers rule, monitor mode on wlan1, hops channels, never
transmits, always restores the radio) and shows access points, stations, probing devices, deauthentication bursts
(detection only) and how busy each channel is. Optionally it saves a capture (data frame payloads cut off) and keeps
a wardriving log: each network with the best position it was heard from, exported as a WiGLE CSV. Everything it
saves is private (mode 600).
The helpers at the top are pure so they can be tested without a radio or a screen.
"""
import csv
import json
import os
import time

from . import position, registry, sense

BANDS = (("all", "2.4 and 5 GHz"), ("2g", "2.4 GHz only"), ("5g", "5 GHz only"))
SECONDS = 3600
WIGLE_HEADER = ("WigleWifi-1.4,appRelease=uConsole-Ops,model=uConsole,release=1,device=uConsole,"
                "display=,board=,brand=ClockworkPi")
WIGLE_COLUMNS = ("MAC", "SSID", "AuthMode", "FirstSeen", "Channel", "RSSI", "CurrentLatitude", "CurrentLongitude",
                 "AltitudeMeters", "AccuracyMeters", "Type")
ALERT_EVERY = 60.0                       # one alert per access point a minute at most


def survey_args(band="all", pcap=False):
    """The aiov2_ctl command, in exactly the form the sudoers rule allows."""
    if band not in dict(BANDS):
        band = "all"
    args = ["sudo", "-n", "aiov2_ctl", "--wifi-survey", "--iface", sense.AIR_IFACE, "--band", band, "--seconds", str(SECONDS)]
    return args + (["--pcap"] if pcap else []) + ["--json"]


def parse_snapshot(line):
    """One survey snapshot from the listener, or None for anything else."""
    try:
        snap = json.loads(line)
    except (TypeError, ValueError):
        return None
    if not isinstance(snap, dict) or not isinstance(snap.get("aps"), list) or not isinstance(snap.get("t"), (int, float)):
        return None
    for key in ("stations", "probes", "deauth"):
        if not isinstance(snap.get(key, []), list):
            return None
    if not isinstance(snap.get("channels", {}), dict):
        return None
    return snap


def ago(now, then):
    s = max(0, int(now - then))
    return f"{s}s" if s < 90 else f"{s // 60}m"


def dbm_text(value):
    """'-50 dBm', or '-' when there is no reading yet (None, not just a missing key)."""
    return f"{value:.0f} dBm" if value is not None else "-"


def ap_rows(snap, now=None):
    now = snap["t"] if now is None else now
    return [{"ssid": a.get("ssid") or "<hidden>", "bssid": a.get("bssid", ""), "channel": str(a.get("channel") or "-"),
             "security": a.get("security", ""), "signal": dbm_text(a.get("rssi")), "seen": ago(now, a.get("last", now))}
            for a in snap["aps"]]


def row_of(rows, bssid):
    """Where an access point is in the table now, or -1. The table is refilled every second and rows move, so
    the one the user picked is remembered by its address, not by where it was."""
    return next((i for i, r in enumerate(rows or []) if bssid and r.get("bssid") == bssid), -1)


def sense_offer(row, available, levels):
    """What "Sense this network" would do for a picked access point row: ((bssid, channel) or None, what to say).
    None means the button is not offered, and the words say why. `available` and `levels` are the same two
    facts Sense itself checks before it listens (is the radio there, is it busy being connected)."""
    if not row:
        return None, ""
    name = row.get("ssid") if row.get("ssid") not in (None, "", "<hidden>") else (row.get("bssid") or "this network")
    channel = row.get("channel")
    if not sense.MAC_RE.match(str(row.get("bssid") or "").lower()):
        return None, "This row has no address to listen for."
    if not str(channel or "").isdigit():
        return None, f"The channel of {name} is not known yet, so Sense cannot be pointed at it."
    target = sense.air_target(row.get("bssid"), channel)
    if target is None:
        return None, f"Sense cannot listen on channel {channel}, so it cannot watch {name}."
    blocked = sense.air_blocker(available, levels)
    if blocked:
        return None, " ".join(blocked)
    return target, (f"Stops the survey and watches the signal of {name} on channel {target[1]} in Sense. "
                    "Nothing is sent.")


AIR_KEEP = 600.0                         # how long a device heard on the air stays matched to a wired host


def air_devices(snap, known=None):
    """{mac: {'network', 'rssi', 'last'}} for the devices in a snapshot that are using a network, added to what
    was already known. This is what lets the Network page say which of its hosts is on Wi-Fi and how strongly it
    is heard. Devices only probing are left out: they are not on a network to be matched with."""
    out = dict(known or {})
    names = {a.get("bssid"): (a.get("ssid") or a.get("bssid", "")) for a in snap.get("aps", [])}
    for s in snap.get("stations", []):
        mac = (s.get("mac") or "").lower()
        if mac:
            out[mac] = {"network": names.get(s.get("bssid"), s.get("bssid", "")), "rssi": s.get("rssi"),
                        "last": s.get("last", snap.get("t", 0.0))}
    return out


def device_label(mac, hosts):
    """A device's address, with the name or IP address the Network page knows it by, if it knows it."""
    host = (hosts or {}).get((mac or "").lower())
    known_as = (host or {}).get("name") or (host or {}).get("ip")
    return f"{known_as}  ({mac})" if known_as else mac


def station_rows(snap, now=None, hosts=None):
    """Every device heard: those seen using a network, then those only heard probing for one. With `hosts`
    ({mac: {'ip', 'name'}} from the Network page) a device that is also a known host is shown by that name."""
    now = snap["t"] if now is None else now
    names = {a.get("bssid"): (a.get("ssid") or a.get("bssid", "")) for a in snap["aps"]}
    rows = [{"mac": device_label(s.get("mac", ""), hosts), "network": names.get(s.get("bssid"), s.get("bssid", "")),
             "signal": dbm_text(s.get("rssi")), "frames": str(s.get("frames", 0)), "seen": ago(now, s.get("last", now))}
            for s in snap.get("stations", [])]
    known = {s.get("mac", "") for s in snap.get("stations", [])}
    for p in snap.get("probes", []):
        if p.get("mac") in known:
            continue
        asked = ", ".join(p.get("ssids", []))
        rows.append({"mac": p.get("mac", ""), "network": ("probing for " + asked) if asked else "probing (no network named)",
                     "signal": dbm_text(p.get("rssi")), "frames": str(p.get("count", 0)),
                     "seen": ago(now, p.get("last", now))})
    return rows


def probe_rows(snap):
    return [{"mac": p.get("mac", ""), "looking": ", ".join(p.get("ssids", [])) or "(any network)",
             "count": str(p.get("count", 0)), "signal": f"{p.get('rssi', 0):.0f} dBm"} for p in snap.get("probes", [])]


def channel_shares(snap):
    """[(channel, share 0..1 of the busiest)] in channel order, for the bars."""
    frames = {int(c): v.get("frames", 0) for c, v in snap.get("channels", {}).items() if str(c).isdigit()}
    top = max(frames.values(), default=0)
    return [(c, (n / top if top else 0.0)) for c, n in sorted(frames.items())]


def summary(snap):
    deauth = snap.get("deauth", [])
    text = f"{len(snap['aps'])} access points, {len(station_rows(snap))} devices"
    return text + (f". {len(deauth)} deauthentication burst{'s' if len(deauth) != 1 else ''} now." if deauth else ".")


def deauth_alerts(snap, last_alert, now):
    """[(bssid, text)] for bursts not already announced in the last minute; updates last_alert."""
    names = {a.get("bssid"): a.get("ssid") for a in snap["aps"]}
    out = []
    for d in snap.get("deauth", []):
        b = d.get("bssid", "")
        if now - last_alert.get(b, -1e9) >= ALERT_EVERY:
            last_alert[b] = now
            out.append((b, f"{d.get('frames', 0)} deauthentication frames in a few seconds on "
                           f"{names.get(b) or b}. Someone may be knocking devices off it."))
    return out


def wigle_auth(security):
    """WiGLE's bracketed capability string for our security word."""
    return {
        "Open": "[ESS]", "WEP": "[WEP][ESS]", "WPA": "[WPA-PSK-TKIP][ESS]", "WPA2": "[WPA2-PSK-CCMP][ESS]",
        "WPA3": "[WPA3-SAE-CCMP][ESS]", "WPA2/WPA3": "[WPA2-PSK-CCMP][WPA3-SAE-CCMP][ESS]",
        "WPA2-Enterprise": "[WPA2-EAP-CCMP][ESS]", "WPA3-Enterprise": "[WPA3-EAP-CCMP][ESS]", "OWE": "[OWE][ESS]",
    }.get(security, "[ESS]")


def good_fix(tpv):
    return bool(tpv) and (tpv.get("mode") or 0) >= 2 and tpv.get("lat") is not None and tpv.get("lon") is not None


HEARD_NOW = 6.0                      # a sighting older than this was not heard "here"


def heard_now(item, snap_time, window=HEARD_NOW):
    """True when this network or device was heard in the last few seconds of its snapshot. A snapshot lists
    everything heard since the scan began; stamping all of it with the current position put things heard
    streets ago at the spot where the GPS happened to get its fix. With no time to go by, it counts as now."""
    last = item.get("last")
    if not isinstance(last, (int, float)) or not isinstance(snap_time, (int, float)):
        return True
    return snap_time - last <= window


class WigleLog:
    """Networks heard while the GPS had a fix, each kept with the position where it was strongest."""
    prefix = "wardrive"                  # the start of the saved file's name

    def __init__(self):
        self.nets = {}

    def add(self, snap, tpv, now=None):
        """Record the access points in a snapshot at the current position. Returns how many are new."""
        if not good_fix(tpv):
            return 0
        now = time.time() if now is None else now
        new = 0
        for a in snap["aps"]:
            bssid, rssi = a.get("bssid"), a.get("rssi")
            if not bssid or rssi is None or not heard_now(a, snap.get("t")):
                continue
            known = self.nets.get(bssid)
            if known is None:
                new += 1
            if known is None or rssi > known["rssi"]:
                err = max(tpv.get("epx") or 0, tpv.get("epy") or 0)
                self.nets[bssid] = {"ssid": a.get("ssid", ""), "auth": wigle_auth(a.get("security", "")),
                                    "first": known["first"] if known else now, "channel": a.get("channel") or "",
                                    "rssi": rssi, "lat": tpv["lat"], "lon": tpv["lon"],
                                    "alt": tpv.get("alt") or 0.0, "acc": err or 10.0}
        return new

    def csv_text(self):
        import io
        out = io.StringIO()
        out.write(WIGLE_HEADER + "\n")
        w = csv.writer(out, lineterminator="\n")
        w.writerow(WIGLE_COLUMNS)
        for bssid, n in sorted(self.nets.items(), key=lambda kv: kv[1]["first"]):
            w.writerow([bssid.upper(), n["ssid"], n["auth"], time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(n["first"])),
                        n["channel"], int(round(n["rssi"])), f"{n['lat']:.6f}", f"{n['lon']:.6f}",
                        f"{n['alt']:.1f}", f"{n['acc']:.1f}", n.get("type", "WIFI")])
        return out.getvalue()

    def save(self, directory=None, now=None):
        """Write the log to a new private file and return its path. Never overwrites an existing file, but two
        saves in the same second (Save, then Stop, say) get their own files rather than a crash."""
        directory = directory or os.path.join(os.path.expanduser("~"), "aiov2-captures")
        os.makedirs(directory, mode=0o700, exist_ok=True)
        stamp = time.strftime(self.prefix + "-%Y%m%d-%H%M%S", time.localtime(now))
        for suffix in [""] + [f"-{n}" for n in range(1, 100)]:
            path = os.path.join(directory, f"{stamp}{suffix}.wigle.csv")
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                break
            except FileExistsError:
                continue
        else:
            raise FileExistsError(f"too many wardrive logs saved in the same second in {directory}")
        with os.fdopen(fd, "w") as f:
            f.write(self.csv_text())
        return path


def build_page(win, sense_network=None):
    """The Survey page. `sense_network(bssid, channel)` is what the Wi-Fi tab gives it to hand one access point
    over to the Sense page; without it the button is never shown."""
    from PyQt6.QtCore import QItemSelectionModel, QRectF, Qt, QTimer
    from PyQt6.QtGui import QColor, QPainter
    from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
                                 QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)
    from .foldertabs import FolderTabWidget

    def card():
        f = QFrame()
        f.setObjectName("card")
        lay = QVBoxLayout(f)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(3)
        return f, lay

    class Bars(QWidget):
        def __init__(self):
            super().__init__()
            self.shares = []
            self.setMinimumHeight(160)

        def paintEvent(self, _):
            p = QPainter(self)
            w, h = self.width(), self.height()
            if not self.shares:
                p.setPen(QColor("#9e9e9e"))
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Nothing heard yet.")
                return
            n = len(self.shares)
            slot = w / n
            for i, (ch, share) in enumerate(self.shares):
                bar = max(2.0, share * (h - 28))
                p.fillRect(QRectF(i * slot + 2, h - 18 - bar, max(2.0, slot - 4), bar), QColor("#e95420"))
                p.setPen(QColor("#b9aec6"))
                p.drawText(QRectF(i * slot, h - 16, slot, 16), Qt.AlignmentFlag.AlignCenter, str(ch))

    def table(headers, stretch=0):
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(24)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        for c in range(len(headers)):
            t.horizontalHeader().setSectionResizeMode(
                c, QHeaderView.ResizeMode.Stretch if c == stretch else QHeaderView.ResizeMode.ResizeToContents)
        return t

    def fill(t, rows, keys):
        t.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, k in enumerate(keys):
                t.setItem(r, c, QTableWidgetItem(row[k]))

    class Page(QWidget):
        def __init__(self):
            super().__init__()
            self.feed, self.log, self.last_alert, self.saved_at = None, WigleLog(), {}, 0.0
            self.ap_list, self.picked = [], ""        # the rows in the access point table, and the one picked
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.tick)
            root = QVBoxLayout(self)
            root.setContentsMargins(4, 2, 4, 2)
            root.setSpacing(3)
            head, hl = card()
            self.state = QLabel("Survey is off.")
            self.state.setStyleSheet("font-size: 14pt; font-weight: bold;")
            self.state.setWordWrap(True)
            self.detail = QLabel("Listens to every Wi-Fi network and device around the internal radio, hopping "
                                 "channels. Nothing is sent. Needs the USB rail on and wlan1 not connected.")
            self.detail.setWordWrap(True)
            self.detail.setStyleSheet("font-size: 11pt; color: #9e9e9e;")
            self.alert = QLabel("")
            self.alert.setWordWrap(True)
            self.alert.setStyleSheet("font-size: 13pt; color: #ff7a45; font-weight: bold;")
            hl.addWidget(self.detail)
            hl.addWidget(self.alert)
            row = QHBoxLayout()
            row.setSpacing(6)
            self.go = QPushButton("Start survey")
            self.band = QComboBox()
            for key, label in BANDS:
                self.band.addItem(label, key)
            self.pcap = QCheckBox("Save a capture")
            self.drive = QCheckBox("Wardrive")
            self.drive.setToolTip("Needs a GPS fix")
            self.export = QPushButton("Save wardrive log")
            for w in (self.go, self.band, self.pcap, self.drive, self.export):
                w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.go.clicked.connect(self.toggle)
            self.export.clicked.connect(self.save_log)
            row.addWidget(self.state, 1)
            for w in (self.go, self.band, self.pcap, self.drive, self.export):
                row.addWidget(w)
            hl.insertLayout(0, row)
            self.drive_note = QLabel("")
            self.drive_note.setStyleSheet("font-size: 11pt; color: #9e9e9e;")
            hl.addWidget(self.drive_note)
            self.drive_note.setVisible(False)
            self.alert.setVisible(False)
            root.addWidget(head)
            self.tabs = FolderTabWidget()
            self.tabs.setObjectName("subtabs")
            self.aps = table(["Network", "Address", "Ch", "Security", "Signal", "Seen"])
            self.stas = table(["Device", "Network", "Signal", "Frames", "Seen"], 1)
            self.probes = table(["Device", "Looking for", "Count", "Signal"], 1)
            self.bars = Bars()
            # one access point can be picked, to hand it to Sense. The row under the table only shows once one is.
            self.aps.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.aps.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.aps.itemSelectionChanged.connect(self.pick)
            ap_page = QWidget()
            al = QVBoxLayout(ap_page)
            al.setContentsMargins(0, 0, 0, 0)
            al.setSpacing(3)
            al.addWidget(self.aps, 1)
            self.offer = QWidget()
            ol = QHBoxLayout(self.offer)
            ol.setContentsMargins(6, 0, 6, 2)
            ol.setSpacing(6)
            self.offer_note = QLabel("")
            self.offer_note.setWordWrap(True)
            self.offer_note.setStyleSheet("font-size: 11pt; color: #9e9e9e;")
            self.sense_b = QPushButton("Sense this network")
            self.sense_b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.sense_b.clicked.connect(self.sense_picked)
            ol.addWidget(self.offer_note, 1)
            ol.addWidget(self.sense_b)
            al.addWidget(self.offer)
            self.offer.setVisible(False)
            self.tabs.addTab(ap_page, "Access points")
            self.tabs.addTab(self.stas, "Devices")
            self.tabs.addTab(self.probes, "Probing")
            self.tabs.addTab(self.bars, "Channels")
            root.addWidget(self.tabs, 1)

        def toggle(self):
            self.stop() if self.feed else self.start()

        def pick(self):
            """The user picked an access point row (or the selection went away)."""
            rows = self.aps.selectionModel().selectedRows()
            at = rows[0].row() if rows else -1
            self.picked = self.ap_list[at]["bssid"] if 0 <= at < len(self.ap_list) else ""
            self.show_offer()

        def show_offer(self):
            """Show, under the table, what can be done with the picked access point. The button is only there when
            Sense can really be pointed at it; otherwise the words say why not."""
            at = row_of(self.ap_list, self.picked)
            row = self.ap_list[at] if at >= 0 and sense_network else None
            target, words = sense_offer(row, sense.air_available(), sense.read_levels())
            self.offer_note.setText(words)
            self.sense_b.setEnabled(target is not None)
            self.offer.setVisible(row is not None)

        def keep_pick(self):
            """After the table is refilled: put the selection back on the picked access point, wherever its row is
            now, or drop it when that access point is no longer listed. Only the selection is touched, not the
            current cell, so the table does not jump while it is being scrolled."""
            at = row_of(self.ap_list, self.picked)
            self.aps.blockSignals(True)
            if at < 0:
                self.picked = ""
                self.aps.clearSelection()
            else:
                flags = QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows
                self.aps.selectionModel().select(self.aps.model().index(at, 0), flags)
            self.aps.blockSignals(False)
            self.show_offer()

        def sense_picked(self):
            """Hand the picked access point to Sense. Checked again here, as things may have changed since the
            button was drawn. The survey is stopped first, and waited for, so the radio is put back before Sense
            takes it; Sense then only listens on that one channel."""
            at = row_of(self.ap_list, self.picked)
            row = self.ap_list[at] if at >= 0 else None
            target, _words = sense_offer(row, sense.air_available(), sense.read_levels())
            if target is None or not sense_network:
                self.show_offer()
                return
            if self.feed:
                self.stop()
            sense_network(*target)

        def start(self):
            blocked = sense.air_blocker(sense.air_available(), sense.read_levels())
            if blocked:
                self.state.setText(blocked[0])
                self.detail.setText(blocked[1])
                return
            try:
                self.feed = sense.AirFeed(args=survey_args(self.band.currentData(), self.pcap.isChecked()), parse=parse_snapshot)
            except OSError as exc:
                self.state.setText("Could not start the survey.")
                self.detail.setText(str(exc))
                return
            self.last_alert, self.log = {}, WigleLog()
            self.saved_at = time.time()
            self.timer.start(1000)
            self.go.setText("Stop survey")
            self.state.setText("Listening...")
            self.detail.setText("The first results appear after a couple of seconds and fill in as it hops channels.")
            win.activity.add("Wi-Fi survey started")

        def stop(self):
            self.timer.stop()
            if self.feed:
                self.feed.stop()
                self.feed = None
            self.go.setText("Start survey")
            self.state.setText("Survey is off.")
            if self.log.nets:
                self.save_log()
            win.activity.add("Wi-Fi survey stopped")

        def leaving(self):
            if self.feed:
                self.stop()

        def say(self, text):
            self.drive_note.setText(text)
            self.drive_note.setVisible(bool(text))

        def save_log(self):
            if not self.log.nets:
                self.say("Nothing to save yet: no network has been heard with a GPS fix.")
                return
            path = self.log.save()
            self.say(f"Saved {len(self.log.nets)} networks to {path} (private).")
            win.activity.add(f"Wardrive log saved: {os.path.basename(path)}")

        def tick(self):
            done = bool(self.feed) and self.feed.finished()     # asked first, so its last snapshots are not missed
            snaps = self.feed.records() if self.feed else []
            if not snaps and done:
                msg = self.feed.problem() or "The survey stopped."
                self.timer.stop()
                self.feed.stop()
                self.feed = None
                self.go.setText("Start survey")
                self.state.setText("The survey stopped.")
                if self.log.nets:                               # ended by itself: keep what the walk gathered
                    self.save_log()
                win.activity.add("Wi-Fi survey ended by itself")
                self.detail.setText(msg + "  (Needs the USB rail on, and 'sudo aiov2_ctl --setup-sudoers' once.)")
                return
            if not snaps:
                return
            for s in snaps:
                new, events = win.bssid_registry.observe_survey(s)
                if new:
                    win.bssid_registry.save()
                for event in events:                                # a toast, if that kind is switched on in Settings
                    win.notifier.add(*registry.describe(event))
            snap = snaps[-1]
            now = snap["t"]
            self.ap_list = ap_rows(snap, now)
            self.aps.blockSignals(True)        # a refill is not the user picking something else
            fill(self.aps, self.ap_list, ("ssid", "bssid", "channel", "security", "signal", "seen"))
            self.aps.blockSignals(False)
            self.keep_pick()
            win.air_devices = air_devices(snap, getattr(win, "air_devices", None))     # for the Network page
            fill(self.stas, station_rows(snap, now, getattr(win, "net_hosts", None)),
                 ("mac", "network", "signal", "frames", "seen"))
            fill(self.probes, probe_rows(snap), ("mac", "looking", "count", "signal"))
            self.bars.shares = channel_shares(snap)
            self.bars.update()
            self.state.setText(summary(snap))
            self.detail.setText(f"Listening on channel {snap.get('channel')}. Nothing is sent.")
            alerts = deauth_alerts(snap, self.last_alert, now)
            self.alert.setText(" ".join(t for _b, t in alerts) if alerts else
                               (self.alert.text() if snap.get("deauth") else ""))
            self.alert.setVisible(bool(self.alert.text()))
            for _b, text in alerts:
                win.notify_user("survey", "Deauthentication burst", text, "warn")
            if self.drive.isChecked():
                # asked from gpsd in the background: the GPS tab's own reading is only fresh while it is showing
                tpv = position.current(win)
                if good_fix(tpv):
                    self.log.add(snap, tpv, now)
                    self.say(f"Wardrive: {len(self.log.nets)} networks with a position.")
                else:
                    self.say("Wardrive is waiting for a GPS fix (switch the GPS rail on and wait for a fix).")
                if time.time() - self.saved_at > 300 and self.log.nets:
                    self.saved_at = time.time()
                    self.save_log()

    return Page()
