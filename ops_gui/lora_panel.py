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

from ops_agent import geo, nodeid

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


CHAT_LIMIT = 300                     # as many as the engine keeps (LoRa.max_chat)


def chat_events(snapshot):
    """The events the Messages view is built from: the engine's own chat list, which telemetry cannot push out,
    or the activity list from an engine too old to keep one."""
    snap = snapshot or {}
    chat = snap.get("chat")
    return chat if isinstance(chat, list) else snap.get("activities") or []


def _number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def link_reading(info):
    """(SNR in dB, RSSI in dBm, when) for the last reading of a node's signal, each None where unknown. The
    engine's own reading of the last packet heard from the node (linkMetrics, which carries RSSI and its time)
    comes first; otherwise the mesh service's SNR, as of when the node was last heard."""
    info = info if isinstance(info, dict) else {}
    link = info.get("linkMetrics") if isinstance(info.get("linkMetrics"), dict) else {}
    snr, rssi = _number(link.get("rxSnr")), _number(link.get("rxRssi"))
    if snr is not None or rssi is not None:
        return snr, rssi, link.get("time")
    snr = _number(info.get("snr"))
    return snr, None, info.get("lastHeard") if snr is not None else None


SIGNAL_HEADER = "SNR / RSSI"


def signal_cell(snr, rssi, when=None, now=None):
    """The figures alone, compact enough for the table: "+6.2 dB  -91 dBm", with "-" in place of a figure that is
    not known and "-" for the whole cell when neither is. The header ("SNR / RSSI") says which is which, and the
    age of the reading is left to the Heard column; signal_tooltip() has the full wording. Never bars, and never a
    distance: signal strength does not say how far away a node is. `when` and `now` are accepted and unused, so
    the call can pass a whole link_reading()."""
    if snr is None and rssi is None:
        return "-"
    return f"{'-' if snr is None else f'{snr:+.1f} dB'}  {'-' if rssi is None else f'{rssi:.0f} dBm'}"


def signal_tooltip(snr, rssi, when=None, now=None):
    """The same reading in full, saying what each figure is and how old it is: "SNR +6.2 dB  RSSI -91 dBm  2m ago"."""
    bits = []
    if snr is not None:
        bits.append(f"SNR {snr:+.1f} dB")
    if rssi is not None:
        bits.append(f"RSSI {rssi:.0f} dBm")
    if not bits:
        return "No signal reading yet."
    if when:
        bits.append(age_text(when, now))
    return "  ".join(bits)


def message_rows(activities, channel=None, limit=CHAT_LIMIT):
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
                     "snr": a.get("rx_snr"), "from_id": a.get("from_id"),
                     "packet_id": a.get("packet_id"), "stamp": a.get("stamp")})
    return rows[-limit:]


def row_signature(row):
    """A row's identity, stable across refreshes, for marking how far the user has read: the sender (node id when
    known), the radio's packet id when it gave one, the channel and the time (exact when known, else the clock
    text). Without a packet id the text is part of it too. The channel matters: the same words on two channels in
    the same second are two messages."""
    if not row:
        return None
    sender = row.get("from_id") or row.get("who")
    when = row.get("stamp") or row.get("time")
    if row.get("packet_id") is not None:
        return (sender, row["packet_id"], row.get("channel", 0), when)
    return (sender, None, row.get("channel", 0), when, row.get("text"))


def stick_to_bottom(position, maximum, slack=4):
    """Whether a refresh should scroll to the newest message: only when the reader was already at the bottom
    (within `slack` pixels). Someone reading older messages keeps their place."""
    try:
        return position >= maximum - slack
    except TypeError:
        return True


def scroll_after_fill(first_fill, position, maximum, slack=4):
    """Where the Messages view goes after it is filled: "bottom" (the newest message) or "keep" (where the reader
    was). The first time it is filled, and the first time after the channel filter changes, it always goes to the
    newest message: the scroll position then belongs to an empty view or to other messages, and used to leave the
    view at the oldest. After that stick_to_bottom() decides, so someone reading older messages keeps their place."""
    if first_fill:
        return "bottom"
    return "bottom" if stick_to_bottom(position, maximum, slack) else "keep"


def first_fill_done(has_rows, visible):
    """Whether the first fill has really happened. Not while the view is empty, and not while it is hidden behind
    another tab: the view is filled in the background, where it has no size and so no true end to scroll to, and
    the newest message must still be shown when the reader first opens it."""
    return bool(has_rows and visible)


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
    """(lat, lon) from a node's position, or None. The one checked converter, ops_agent.geo.latlon()."""
    return geo.latlon(info.get("position")) if isinstance(info, dict) else None


def own_position(node_info, self_id):
    """This mesh node's own position, if it has one (a GPS fix or a fixed position).

    Node ids are compared as strings: node_info may key its entries differently from how self_id
    is reported (one an int, the other a hex string), and a direct dict lookup would then always
    miss, same as it would for every other node here (see radar_points, which compares as strings)."""
    if self_id is None or not node_info:
        return None
    for node_id, info in node_info.items():
        if nodeid.same(node_id, self_id):
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
            if nodeid.same(node_id, self_id):
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
    if not own or not where or nodeid.same(node_id, self_id):
        return "-"
    return distance_text(haversine_km(own[0], own[1], where[0], where[1]))


NAME_MIN_PX = 160                    # the name column never gets less than this, so names stay readable


def column_widths(total, needs, stretch=0, minimum=NAME_MIN_PX):
    """Pixel widths for the node table's columns. Every column but `stretch` (the name) gets what its header and
    cells need, and the name gets all the rest, never less than `minimum`. When the table is too narrow for that,
    the other columns give up width in proportion to what they need, so the name is the last to be cut."""
    total = max(0, int(total))
    needs = [max(0, int(n)) for n in needs]
    if not needs:
        return []
    others = sum(n for i, n in enumerate(needs) if i != stretch)
    if total - others >= minimum:
        widths = list(needs)
    else:
        room = max(0, total - minimum)
        scale = room / others if others else 0.0
        widths = [int(n * scale) for n in needs]
    widths[stretch] = max(0, total - sum(w for i, w in enumerate(widths) if i != stretch))
    return widths


def node_rows(node_info, self_id=None, now=None, own=None):
    """Rows for the node table: this node first, then the rest by when they were last heard."""
    rows = []
    for node_id, info in (node_info or {}).items():
        if not isinstance(info, dict):
            continue
        metrics = info.get("deviceMetrics") or {}
        battery = metrics.get("batteryLevel")
        hops = info.get("hopsAway")
        reading = link_reading(info)
        rows.append({
            "id": str(node_id),
            "name": node_name(info, node_id),
            "signal": signal_cell(*reading, now=now),
            "signal_tip": signal_tooltip(*reading, now=now),
            "heard": age_text(info.get("lastHeard"), now),
            "battery": "-" if battery is None else ("plugged in" if battery > 100 else f"{battery}%"),
            "hops": "-" if hops is None else str(hops),
            "distance": _row_distance(info, node_id, self_id, own),
            "self": nodeid.same(node_id, self_id),
            "_heard": info.get("lastHeard") or 0,
        })
    rows.sort(key=lambda r: (not r["self"], -r["_heard"]))
    return rows


NODE_COLUMNS = ("name", "signal", "distance", "heard", "battery", "hops")


def rows_signature(rows):
    """What the nodes table shows, as a tuple, so a refresh can tell whether anything changed at all."""
    return tuple(tuple(r.get(k) for k in NODE_COLUMNS) + (r.get("signal_tip"), bool(r.get("self")))
                 for r in rows or [])


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

    class NodeTable(QTableWidget):
        """The nodes table. The name column takes the spare width and the others only what they need
        (see column_widths); Qt's own stretch mode let content-sized columns squeeze names to a few letters."""

        def resizeEvent(self, event):
            super().resizeEvent(event)
            self.fit_columns()

        def fit_columns(self):
            head = self.horizontalHeader()
            needs = [0] + [max(self.sizeHintForColumn(c), head.sectionSizeHint(c)) + 12
                           for c in range(1, self.columnCount())]
            for c, w in enumerate(column_widths(self.viewport().width(), needs)):
                head.resizeSection(c, w)

    class MeshPage(QWidget):
        def __init__(self):
            super().__init__()
            self.on_snap = None
            self.lora = None
            self.rows_sig = None
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
            self.table = NodeTable(0, 6)
            self.table.setHorizontalHeaderLabels(["Name", SIGNAL_HEADER, "Distance", "Heard", "Battery", "Hops"])
            self.table.verticalHeader().setVisible(False)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
            self.table.horizontalHeader().setStretchLastSection(False)
            self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
            nl.addWidget(cap)
            nl.addWidget(self.table)
            mid.addWidget(nodes, 3)          # the nodes table gets the larger share: names need the room
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
            mid.addWidget(feed, 2)
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
                # no connection, or one that has just been dropped: nothing here may go on using the old one
                self.timer.stop()
                self.lora = None
                self.allow_on = False
                self.allow.show_state(False)
                self.update_send()
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
            sig = rows_signature(rows)
            if sig != self.rows_sig:                     # rebuild only when what is shown has changed
                self.rows_sig = sig
                self.table.setRowCount(len(rows))
                for r, row in enumerate(rows):
                    for c, key in enumerate(NODE_COLUMNS):
                        text = row[key] + (" (this node)" if key == "name" and row["self"] else "")
                        item = QTableWidgetItem(text)
                        if key == "signal":
                            item.setToolTip(row["signal_tip"])
                        self.table.setItem(r, c, item)
                self.table.fit_columns()
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
            self.first_fill = True               # the next fill shows the newest message (scroll_after_fill)
            root = QVBoxLayout(self)
            bar = QHBoxLayout()
            self.count = QLabel("No messages yet.")
            self.count.setStyleSheet("font-size: 16pt; font-weight: bold;")
            self.channel = QComboBox()
            self.channel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.channel.addItem("All channels", None)
            self.channel.activated.connect(self.filter_changed)
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

        def filter_changed(self, _=0):
            self.last_sig, self.first_fill = None, True

        def shown(self):
            """The reader has opened this view: if it has not yet been seen filled, show the newest message."""
            if self.first_fill and self.rows:
                bar = self.view.verticalScrollBar()
                QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))
                self.first_fill = False

        def update_from(self, snap):
            acts = chat_events(snap)
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
            sig = tuple(row_signature(r) for r in self.rows)
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
            bar = self.view.verticalScrollBar()
            where, keep = scroll_after_fill(self.first_fill, bar.value(), bar.maximum()), bar.value()
            self.view.setHtml("".join(html_rows) or "<p style='color:#9a90a6'>Nothing heard on the mesh yet.</p>")
            bar.setValue(bar.maximum() if where == "bottom" else keep)
            if where == "bottom":
                # the page may not be laid out yet, so its true end is only known once Qt has done that
                QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))
            if first_fill_done(bool(self.rows), self.view.isVisible()):
                self.first_fill = False
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
            rows = message_rows(chat_events(snap))
            if self.currentWidget() is self.messages:
                self.messages.read_mark = row_signature(rows[-1]) if rows else self.messages.read_mark
            unread = unread_count(rows, self.messages.read_mark)
            self.setTabText(1, f"Messages ({unread})" if unread else "Messages")

        def tab_changed(self, _=0):
            if self.currentWidget() is self.messages:
                self.messages.shown()
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
