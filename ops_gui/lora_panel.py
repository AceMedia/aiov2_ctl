"""
The LoRa tab: mesh status, nodes heard, a live activity feed and an opt in message box.

It reads the same Meshtastic engine as the ops console (ops_agent.lora.LoRa) through
meshtasticd on localhost, so there is one backend, shared with other tabs and with notifications
through mesh_hub.py: the connection stays open while the LoRa rail is on and either this tab is
showing or mesh notifications are wanted, not only while this tab is showing. Receiving is the
default; sending has to be switched on in the tab each time, because a message goes out over the air.
The helpers at the top are pure so they can be tested without a radio.
"""
import html
import math
import time

TICK_MS = 1000


def node_name(info, node_id=""):
    user = (info or {}).get("user") or {}
    return user.get("longName") or user.get("shortName") or str(node_id) or "unknown"


DATED_AFTER_DAYS = 14                # older than this, say the date: "309d ago" made people do sums


def age_text(last_heard, now=None, dated=True):
    """When a node was last heard. The mesh service remembers nodes between runs, with the time it last heard
    each one, so a node can be listed that has not been heard for months. Up to two weeks this is "5m ago",
    "3d ago"; beyond that it is the date ("20 Jan 2026"), which says plainly that it is remembered and not
    heard lately. `dated=False` keeps days however long, for sentences such as "Not heard for 309d"."""
    if not last_heard:
        return "never"
    secs = max(0, int((now or time.time()) - last_heard))
    if secs < 60:
        return "now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    days = secs // 86400
    if dated and days > DATED_AFTER_DAYS:
        return time.strftime("%d %b %Y", time.localtime(last_heard)).lstrip("0")
    return f"{days}d ago"


def signal_text(snr, rssi=None):
    bits = []
    if snr is not None:
        bits.append(f"{snr:+.1f} dB")
    if rssi is not None:
        bits.append(f"{rssi} dBm")
    return "  ".join(bits) or "-"


def message_rows(activities, channel=None, limit=200):
    """Text messages and check-ins from the activity list, oldest first, optionally for one channel."""
    rows = []
    for a in activities or []:
        if not isinstance(a, dict) or a.get("kind") not in ("text", "checkin"):
            continue
        if channel is not None and a.get("channel") != channel:
            continue
        rows.append({"time": a.get("time", ""), "who": "Me" if a.get("sent") else a.get("from") or "Unknown",
                     "text": a.get("summary", ""), "channel": a.get("channel", 0), "sent": bool(a.get("sent")),
                     "checkin": a.get("kind") == "checkin",
                     "snr": a.get("rx_snr")})
    return rows[-limit:]


def row_signature(row):
    """A row's identity, stable across refreshes, for marking how far the user has read."""
    return (row["time"], row["who"], row["text"]) if row else None


def unread_count(rows, read_mark):
    """Incoming messages after the row last marked as read. Our own messages never count.

    `read_mark` is a row_signature(), not a count: `activities` is capped (message_rows keeps only the newest
    `limit`), so once that cap is reached the *count* of incoming messages stops changing even as new ones arrive
    and old ones fall off the front, and a count-based unread total gets stuck at 0 forever. Tracking identity
    instead keeps counting correctly past the cap; if the marked row has itself scrolled out of the window, every
    incoming row still in it is treated as unread, which is a safer default than staying stuck at 0."""
    if read_mark is None:
        return sum(1 for r in rows if not r["sent"])
    for i, r in enumerate(rows):
        if row_signature(r) == read_mark:
            return sum(1 for r in rows[i + 1:] if not r["sent"])
    return sum(1 for r in rows if not r["sent"])


def message_channels(activities):
    return sorted({a.get("channel", 0) for a in activities or [] if isinstance(a, dict) and a.get("kind") in ("text", "checkin")})


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))


def bearing_deg(lat1, lon1, lat2, lon2):
    """Compass bearing from the first point to the second, 0 to 360."""
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def node_latlon(info):
    """(lat, lon) from a node's position, or None. Accepts float and 1e-7 integer forms."""
    pos = (info or {}).get("position") if isinstance(info, dict) else None
    if not isinstance(pos, dict):
        return None
    lat, lon = pos.get("latitude"), pos.get("longitude")
    if lat is None and pos.get("latitudeI") is not None:
        lat = pos["latitudeI"] / 1e7
    if lon is None and pos.get("longitudeI") is not None:
        lon = pos["longitudeI"] / 1e7
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    return lat, lon


def own_position(node_info, self_id):
    """This mesh node's own position, if it has one (a GPS fix or a fixed position).

    Node ids are compared as strings: node_info may key its entries differently from how self_id
    is reported (one an int, the other a hex string), and a direct dict lookup would then always
    miss, same as it would for every other node here (see radar_points, which compares as strings)."""
    if self_id is None or not node_info:
        return None
    self_id = str(self_id)
    for node_id, info in node_info.items():
        if str(node_id) == self_id:
            return node_latlon(info)
    return None


def distance_text(km):
    if km is None:
        return "-"
    return f"{km * 1000:.0f} m" if km < 1 else f"{km:.1f} km"


def radar_points(node_info, self_id, own, radius_km, now=None, stale_after=900):
    """Nodes with a position as plot coordinates around `own`, plus the range plotted in km.

    x runs east and y runs north, both scaled so the edge of the plot is 1. The range is twice the
    proximity ring or the farthest node, whichever is larger, so the ring is always visible.
    """
    now = now or time.time()
    found = []
    if own:
        for node_id, info in (node_info or {}).items():
            if str(node_id) == str(self_id):
                continue
            where = node_latlon(info)
            if not where:
                continue
            km = haversine_km(own[0], own[1], where[0], where[1])
            heard = (info or {}).get("lastHeard")
            found.append({"id": str(node_id), "name": node_name(info, node_id), "km": km,
                          "bearing": bearing_deg(own[0], own[1], where[0], where[1]),
                          "inside": km <= radius_km,
                          "stale": bool(heard) and now - heard >= stale_after})
    span = max([radius_km * 2] + [p["km"] * 1.1 for p in found]) or 1.0
    for p in found:
        rad = math.radians(p["bearing"])
        p["x"], p["y"] = p["km"] / span * math.sin(rad), p["km"] / span * math.cos(rad)
    found.sort(key=lambda p: p["km"])
    return found, span


def channel_choices(snapshot):
    """[(channel number, label)] for the channels with recent activity, plus the selected one."""
    snap = snapshot or {}
    items = {}
    for c in snap.get("active_channels") or []:
        items[c["channel"]] = f"Channel {c['channel']}  ({c.get('count', 0)} packets)"
    sel = snap.get("selected_channel")
    if sel is not None and sel not in items:
        items[sel] = f"Channel {sel}"
    if not items:
        items[0] = "Channel 0"
    return sorted(items.items())


def alert_lines(recent_alerts, stale, node_info=None):
    """Newest first: proximity alerts, then nodes not heard for a while."""
    lines = [f"{a.get('time', '')}  {a.get('message', '')}" for a in reversed(recent_alerts or [])]
    for node_id, age in (stale or [])[:5]:
        name = node_name((node_info or {}).get(node_id), node_id)
        lines.append(f"Not heard for {age_text(time.time() - age, dated=False).replace(' ago', '')}: {name}")
    return lines


def _row_distance(info, node_id, self_id, own):
    where = node_latlon(info)
    if not own or not where or str(node_id) == str(self_id):
        return "-"
    return distance_text(haversine_km(own[0], own[1], where[0], where[1]))


def node_rows(node_info, self_id=None, now=None, own=None):
    """Rows for the node table: this node first, then the rest by when they were last heard."""
    rows = []
    for node_id, info in (node_info or {}).items():
        if not isinstance(info, dict):
            continue
        metrics = info.get("deviceMetrics") or {}
        battery = metrics.get("batteryLevel")
        hops = info.get("hopsAway")
        rows.append({
            "id": str(node_id),
            "name": node_name(info, node_id),
            "signal": signal_text(info.get("snr")),
            "heard": age_text(info.get("lastHeard"), now),
            "battery": "-" if battery is None else ("plugged in" if battery > 100 else f"{battery}%"),
            "hops": "-" if hops is None else str(hops),
            "distance": _row_distance(info, node_id, self_id, own),
            "self": str(node_id) == str(self_id),
            "_heard": info.get("lastHeard") or 0,
        })
    rows.sort(key=lambda r: (not r["self"], -r["_heard"]))
    return rows


def activity_line(a):
    sig = ""
    if a.get("rx_snr") is not None:
        sig = f"  ({a['rx_snr']:+.1f} dB)"
    arrow = ">" if a.get("sent") else "<"
    return f"{a.get('time', '')}  ch{a.get('channel', 0)}  {arrow} {a.get('from', '?')}: {a.get('summary', '')}{sig}"


def hint(library_ok, service_active, rail):
    """The next step when the mesh is not reachable, in plain words."""
    if not library_ok:
        return "The meshtastic Python library is not installed: pip3 install --break-system-packages meshtastic"
    if rail != "on":
        return "The LoRa rail is off."
    if not service_active:
        return "meshtasticd is stopped. Turning the LoRa rail off and on again starts it, or run: sudo systemctl start meshtasticd"
    return ""


def can_send(enabled, connected, text):
    return bool(enabled and connected and (text or "").strip())


def build_panel(win, Switch):
    """Return (widget, activate, deactivate)."""
    import subprocess
    import threading

    from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, QObject, pyqtSignal
    from PyQt6.QtGui import QColor, QPainter, QPalette, QPen
    from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
                                 QLineEdit, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem,
                                 QTabWidget, QTextBrowser, QVBoxLayout, QWidget)
    from .foldertabs import FolderTabWidget

    class Signals(QObject):
        ready = pyqtSignal(object)

    class Radar(QWidget):
        """Nodes around this one by bearing and distance, with the proximity ring."""

        def __init__(self):
            super().__init__()
            self.setMinimumSize(240, 200)
            self.points, self.span, self.radius, self.waiting = [], 1.0, 1.0, True

        def paintEvent(self, _):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            text = self.palette().color(QPalette.ColorRole.WindowText)
            side = min(self.width(), self.height()) - 36
            cx, cy, r = self.width() / 2, self.height() / 2, side / 2
            ring = QColor(text)
            ring.setAlpha(70)
            p.setPen(QPen(ring, 1))
            p.drawEllipse(QPointF(cx, cy), r, r)
            p.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
            p.drawLine(QPointF(cx, cy - r), QPointF(cx, cy + r))
            if self.span > 0:
                rr = r * self.radius / self.span
                p.setPen(QPen(QColor("#37b24d"), 2))
                p.drawEllipse(QPointF(cx, cy), rr, rr)
            p.setPen(text)
            for label, dx, dy in (("N", 0, -1), ("E", 1, 0), ("S", 0, 1), ("W", -1, 0)):
                p.drawText(QRectF(cx + dx * (r + 16) - 10, cy + dy * (r + 16) - 10, 20, 20),
                           Qt.AlignmentFlag.AlignCenter, label)
            if self.waiting:
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                           "Waiting for this node's position")
                p.end()
                return
            p.setPen(QPen(QColor("#111111"), 1))
            p.setBrush(QColor("#2e9bff"))
            p.drawEllipse(QPointF(cx, cy), 6, 6)
            for n in self.points:
                p.setBrush(QColor("#868e96") if n["stale"] else QColor("#37b24d") if n["inside"]
                           else QColor("#f59f00"))
                x, y = cx + n["x"] * r, cy - n["y"] * r
                p.drawEllipse(QPointF(x, y), 5, 5)
                p.setPen(text)
                p.drawText(QPointF(x + 8, y + 4), n["name"][:14])
                p.setPen(QPen(QColor("#111111"), 1))
            p.setPen(text)
            p.drawText(QPointF(10, self.height() - 8), f"Edge of the plot: {distance_text(self.span)}")
            p.end()

    class MeshPage(QWidget):
        def __init__(self):
            super().__init__()
            self.on_snap = None
            self.lora = None
            self.starting = False
            self.signals = Signals()
            self.signals.ready.connect(self.started)
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.tick)
            root = QVBoxLayout(self)

            head = QFrame()
            head.setObjectName("card")
            hl = QVBoxLayout(head)
            self.status = QLabel("")
            self.status.setStyleSheet("font-size: 16pt; font-weight: bold;")
            self.status.setWordWrap(True)
            self.note = QLabel("")
            self.note.setStyleSheet("font-size: 12pt; color: #9e9e9e;")
            self.note.setWordWrap(True)
            hl.addWidget(self.status)
            hl.addWidget(self.note)
            root.addWidget(head)

            chans = QHBoxLayout()
            chans.addWidget(QLabel("Send on"))
            self.channel = QComboBox()
            self.channel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.channel.activated.connect(self.pick_channel)
            chans.addWidget(self.channel, 2)
            self.best_b = QPushButton("Best")
            self.best_b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.best_b.clicked.connect(self.best_channel)
            chans.addWidget(self.best_b)
            self.follow = Switch()
            self.follow.clicked.connect(self.toggle_follow)
            self.lock = Switch()
            self.lock.clicked.connect(self.toggle_lock)
            chans.addWidget(QLabel("Follow"))
            chans.addWidget(self.follow)
            chans.addWidget(QLabel("Lock"))
            chans.addWidget(self.lock)
            chans.addStretch(1)
            self.export_b = QPushButton("Export history")
            self.export_b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.export_b.clicked.connect(self.export)
            chans.addWidget(self.export_b)
            root.addLayout(chans)

            mid = QHBoxLayout()
            nodes = QFrame()
            nodes.setObjectName("card")
            nl = QVBoxLayout(nodes)
            cap = QLabel("Nodes heard")
            cap.setStyleSheet("font-size: 13pt; color: #9e9e9e;")
            self.table = QTableWidget(0, 6)
            self.table.setHorizontalHeaderLabels(["Name", "Signal", "Distance", "Heard", "Battery", "Hops"])
            self.table.verticalHeader().setVisible(False)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            for c in range(1, 6):
                self.table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
            nl.addWidget(cap)
            nl.addWidget(self.table)
            mid.addWidget(nodes, 3)
            feed = QFrame()
            feed.setObjectName("card")
            fl = QVBoxLayout(feed)
            cap = QLabel("Activity")
            cap.setStyleSheet("font-size: 13pt; color: #9e9e9e;")
            self.feed = QPlainTextEdit()
            self.feed.setReadOnly(True)
            self.feed.setStyleSheet("font-size: 11pt;")
            fl.addWidget(cap)
            fl.addWidget(self.feed)
            mid.addWidget(feed, 4)
            root.addLayout(mid, 1)

            row = QHBoxLayout()
            self.allow = Switch()
            self.allow.clicked.connect(self.toggle_allow)
            self.allow_on = False
            row.addWidget(QLabel("Allow sending"))
            row.addWidget(self.allow)
            self.entry = QLineEdit()
            self.entry.setPlaceholderText("Message to the mesh (turn on Allow sending first)")
            self.entry.setStyleSheet("font-size: 13pt; padding: 6px;")
            self.entry.textChanged.connect(self.update_send)
            self.entry.returnPressed.connect(self.send)
            self.send_b = QPushButton("Send")
            self.send_b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.send_b.clicked.connect(self.send)
            row.addWidget(self.entry, 1)
            row.addWidget(self.send_b)
            root.addLayout(row)
            self.update_send()

        # ---- lifecycle
        def activate(self):
            """Ask the shared mesh connection (see mesh_hub.py) for the engine; it may already be open."""
            if self.lora or self.starting:
                return
            self.starting = True
            self.note_until = 0.0
            self.status.setText("Checking the mesh...")
            win.mesh_hub.acquire("tab", self.signals.ready.emit)

        def started(self, result):
            self.starting = False
            self.note.setText(result["hint"])
            engine = result["lora"]
            if engine is None:
                self.show_note(result["hint"])
                self.status.setText("Mesh not reachable")
                return
            self.lora = engine
            self.timer.start(TICK_MS)
            self.tick()

        def deactivate(self):
            self.timer.stop()
            self.lora = None
            self.starting = False
            self.allow_on = False
            self.allow.show_state(False)
            self.update_send()
            win.mesh_hub.release("tab")          # the hub closes the connection unless notifications still want it

        def show_note(self, text, hold_s=4.0):
            """A status message (Sent, Exported to ..., a hint) that tick() leaves alone for a few seconds."""
            self.note.setText(text)
            self.note_until = time.time() + hold_s

        # ---- running
        def tick(self):
            e = self.lora
            if not e:
                return
            e.refresh_nodes()
            snap = e.snapshot()
            self.status.setText(e.get_status())
            tx = snap.get("last_tx_status")
            if time.time() >= self.note_until:          # hold a just-set status (Sent, Exported to...) a moment,
                self.note.setText(f"Sending goes to channel {snap.get('selected_channel', 0)}." +  # rather than
                                  (f"  Last send: {tx}" if tx else ""))                             # overwrite it at once
            own = own_position(snap["node_info"], snap.get("self_id"))
            rows = node_rows(snap["node_info"], snap.get("self_id"), own=own)
            self.table.setRowCount(len(rows))
            for r, row in enumerate(rows):
                for c, key in enumerate(("name", "signal", "distance", "heard", "battery", "hops")):
                    text = row[key] + (" (this node)" if key == "name" and row["self"] else "")
                    self.table.setItem(r, c, QTableWidgetItem(text))
            lines = [activity_line(a) for a in snap["activities"][-40:]]
            self.feed.setPlainText("\n".join(reversed(lines)))
            self.sync_channels(snap)
            if own:
                e.update_proximity({"lat": own[0], "lon": own[1]}, haversine_km)
            if self.on_snap:
                self.on_snap(e, snap, own)
            self.update_send()

        # ---- channels and export
        def sync_channels(self, snap):
            choices = channel_choices(snap)
            current = [(self.channel.itemData(i), self.channel.itemText(i)) for i in range(self.channel.count())]
            if current != choices:
                self.channel.blockSignals(True)
                self.channel.clear()
                for number, label in choices:
                    self.channel.addItem(label, number)
                self.channel.blockSignals(False)
            idx = self.channel.findData(snap.get("selected_channel"))
            if idx >= 0 and idx != self.channel.currentIndex():
                self.channel.setCurrentIndex(idx)
            self.follow.show_state(bool(snap.get("auto_follow_channel")))
            self.lock.show_state(bool(snap.get("channel_locked")))

        def pick_channel(self, _=0):
            if self.lora and self.channel.currentData() is not None:
                self.lora.set_selected_channel(self.channel.currentData())
                self.tick()

        def best_channel(self):
            if self.lora:
                self.lora.jump_to_best_channel()
                self.tick()

        def toggle_follow(self):
            if self.lora:
                self.lora.toggle_auto_follow_channel()
                self.tick()

        def toggle_lock(self):
            if self.lora:
                self.lora.toggle_channel_lock()
                self.tick()

        def export(self):
            if not self.lora:
                return
            from ops_agent.preferences import export_telemetry_snapshot
            snap = self.lora.snapshot()
            own = own_position(snap["node_info"], snap.get("self_id"))
            path = export_telemetry_snapshot(snap, {"lat": own[0], "lon": own[1]} if own else None)
            self.show_note(f"Exported to {path}" if path else "Export failed.")

        # ---- sending
        def toggle_allow(self):
            self.allow_on = not self.allow_on
            self.allow.show_state(self.allow_on)
            self.update_send()

        def update_send(self, *_):
            self.send_b.setEnabled(can_send(self.allow_on, bool(self.lora), self.entry.text()))
            self.entry.setEnabled(self.allow_on and bool(self.lora))

        def send(self):
            text = self.entry.text().strip()
            if not can_send(self.allow_on, bool(self.lora), text):
                return
            ok = self.lora.send_message(text)
            self.show_note("Sent." if ok else "Send failed. See the activity feed.")
            if ok:
                self.entry.clear()
            self.tick()

    class MessagesPage(QWidget):
        """Every text message and check-in heard or sent, newest at the bottom, with a channel filter."""

        def __init__(self):
            super().__init__()
            self.read_mark, self.last_sig, self.rows = None, None, []
            root = QVBoxLayout(self)
            bar = QHBoxLayout()
            self.count = QLabel("No messages yet.")
            self.count.setStyleSheet("font-size: 16pt; font-weight: bold;")
            self.channel = QComboBox()
            self.channel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.channel.addItem("All channels", None)
            self.channel.activated.connect(lambda _=0: setattr(self, "last_sig", None))
            reply = QPushButton("Reply on the Mesh tab")
            reply.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            reply.clicked.connect(self.reply)
            bar.addWidget(self.count, 1)
            bar.addWidget(self.channel)
            bar.addWidget(reply)
            root.addLayout(bar)
            box = QFrame()
            box.setObjectName("card")
            bl = QVBoxLayout(box)
            self.view = QTextBrowser()
            self.view.setStyleSheet("font-size: 13pt;")
            bl.addWidget(self.view)
            root.addWidget(box, 1)
            self.on_reply = None

        def reply(self):
            if self.on_reply:
                self.on_reply()

        def update_from(self, snap):
            acts = snap.get("activities")
            chans = message_channels(acts)
            have = [self.channel.itemData(i) for i in range(1, self.channel.count())]
            if have != chans:
                keep = self.channel.currentData()
                self.channel.blockSignals(True)
                self.channel.clear()
                self.channel.addItem("All channels", None)
                for c in chans:
                    self.channel.addItem(f"Channel {c}", c)
                idx = self.channel.findData(keep)
                self.channel.setCurrentIndex(max(0, idx))
                self.channel.blockSignals(False)
            self.rows = message_rows(acts, self.channel.currentData())
            sig = tuple((r["time"], r["who"], r["text"]) for r in self.rows)
            if sig == self.last_sig:
                return
            self.last_sig = sig
            html_rows = []
            for r in self.rows:
                colour = "#3fe0d0" if r["sent"] else "#ff7a45"
                who = html.escape(r["who"])
                tag = " checked in" if r["checkin"] else ""
                html_rows.append(f'<p style="margin:2px 0"><span style="color:#9a90a6">{r["time"]}  ch{r["channel"]}</span> '
                                 f'<b style="color:{colour}">{who}</b>{tag}<br>{html.escape(r["text"])}</p>')
            self.view.setHtml("".join(html_rows) or "<p style='color:#9a90a6'>Nothing heard on the mesh yet.</p>")
            self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())
            incoming = sum(1 for r in self.rows if not r["sent"])
            self.count.setText(f"{len(self.rows)} messages, {incoming} heard" if self.rows else "No messages yet.")

    class RadarPage(QWidget):
        def __init__(self):
            super().__init__()
            self.engine = None
            root = QHBoxLayout(self)
            left = QFrame()
            left.setObjectName("card")
            ll = QVBoxLayout(left)
            bar = QHBoxLayout()
            self.ring = QLabel("")
            self.ring.setStyleSheet("font-size: 13pt;")
            less = QPushButton("Ring -")
            more = QPushButton("Ring +")
            for b, step in ((less, -1), (more, 1)):
                b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                b.clicked.connect(lambda _=False, s=step: self.adjust(s))
            bar.addWidget(self.ring, 1)
            bar.addWidget(less)
            bar.addWidget(more)
            ll.addLayout(bar)
            self.radar = Radar()
            ll.addWidget(self.radar, 1)
            root.addWidget(left, 3)
            right = QFrame()
            right.setObjectName("card")
            rl = QVBoxLayout(right)
            cap = QLabel("Alerts")
            cap.setStyleSheet("font-size: 13pt; color: #9e9e9e;")
            self.alerts = QPlainTextEdit()
            self.alerts.setReadOnly(True)
            self.alerts.setStyleSheet("font-size: 11pt;")
            self.legend = QLabel("Green: inside the ring.  Amber: outside.  Grey: not heard for 15 minutes.")
            self.legend.setWordWrap(True)
            self.legend.setStyleSheet("font-size: 11pt; color: #9e9e9e;")
            rl.addWidget(cap)
            rl.addWidget(self.alerts, 1)
            rl.addWidget(self.legend)
            root.addWidget(right, 2)

        def adjust(self, step):
            if self.engine:
                self.engine.adjust_proximity_radius(step)

        def update_from(self, engine, snap, own):
            self.engine = engine
            radius = snap.get("proximity_radius_km") or 1.0
            self.ring.setText(f"Proximity ring {radius:g} km")
            points, span = radar_points(snap["node_info"], snap.get("self_id"), own, radius)
            self.radar.points, self.radar.span, self.radar.radius = points, span, radius
            self.radar.waiting = own is None
            self.radar.update()
            lines = alert_lines(snap.get("recent_alerts"), engine.stale_nodes(), snap["node_info"])
            self.alerts.setPlainText("\n".join(lines) if lines else "No alerts.")

    class Panel(FolderTabWidget):
        def __init__(self):
            super().__init__()
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.setObjectName("subtabs")
            self.mesh = MeshPage()
            self.radar_page = RadarPage()
            self.messages = MessagesPage()
            self.messages.on_reply = self.go_reply
            self.mesh.on_snap = self.fan_out
            self.addTab(self.mesh, "Mesh")
            self.addTab(self.messages, "Messages")
            self.addTab(self.radar_page, "Radar")
            self.currentChanged.connect(self.tab_changed)

        def fan_out(self, engine, snap, own):
            self.radar_page.update_from(engine, snap, own)
            self.messages.update_from(snap)
            rows = message_rows(snap.get("activities"))
            if self.currentWidget() is self.messages:
                self.messages.read_mark = row_signature(rows[-1]) if rows else self.messages.read_mark
            unread = unread_count(rows, self.messages.read_mark)
            self.setTabText(1, f"Messages ({unread})" if unread else "Messages")

        def tab_changed(self, _=0):
            if self.currentWidget() is self.messages and self.messages.rows:
                self.messages.read_mark = row_signature(self.messages.rows[-1])
                self.setTabText(1, "Messages")

        def go_reply(self):
            self.setCurrentWidget(self.mesh)
            self.mesh.entry.setFocus()

        def activate(self):
            self.mesh.activate()

        def deactivate(self):
            self.mesh.deactivate()

    panel = Panel()
    return panel, panel.activate, panel.deactivate
