"""
Notifications: pixel toasts inside the app, and optional system notifications.

Things worth knowing about (a mesh message arriving, a GPS fix gained or lost, a low battery, a hot CPU) go to one
place, the notification centre. It decides whether to show them (per source switches in Settings), drops
repeats, rate limits bursts, keeps a short history, and hands each one to a toast and, if you turned them on,
to the desktop's own notification daemon. Nothing is ever sent anywhere else.
The helpers at the top are pure so they can be tested without a screen.
"""
import time

SOURCES = (("mesh", "Mesh messages and check-ins"), ("nodes", "New mesh nodes heard"), ("gps", "GPS fix gained or lost"),
           ("rails", "Rails switched on or off"), ("warnings", "Warnings: battery, heat, power"),
           ("sense", "Wi-Fi sensing: activity nearby"), ("survey", "Wi-Fi survey: deauthentication bursts"),
           ("netsec", "Networks: security that changes, or a known name with none"),
           ("netnew", "Networks: ones never heard before"),
           ("btplaces", "Bluetooth: an address heard at several places along your way"),
           ("bttrackers", "Bluetooth: a tracker's address heard at several places over 30 minutes"),
           ("updates", "Updates waiting to be installed"),
           ("crashes", "A crash report was saved"))
DEFAULTS = {"toasts": True, "system": False, "mesh": True, "nodes": False, "gps": True, "rails": False, "warnings": True, "sense": False, "survey": False,
            "netsec": True, "netnew": False, "btplaces": True, "bttrackers": False, "updates": True,
            "crashes": True}
LEVELS = ("info", "good", "warn", "hot")
HISTORY = 60
DEDUPE_SECONDS = 5.0
BURST = (4, 10.0)                       # at most this many toasts per this many seconds, then they are folded together


def config(settings):
    """The notification settings with defaults filled in and anything unexpected ignored."""
    saved = (settings or {}).get("notifications")
    saved = saved if isinstance(saved, dict) else {}
    return {k: (bool(saved[k]) if k in saved and isinstance(saved[k], (bool, int)) else v) for k, v in DEFAULTS.items()}


def offer_allowed(cfg, source):
    """True when a notice with buttons may be shown: pop-ups are on, and so is this kind of notice."""
    return bool(cfg.get("toasts") and (source not in DEFAULTS or cfg.get(source)))


def shorten(text, limit):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:max(1, limit - 1)] + "~"


class Center:
    """Decides what is shown. `clock` is injectable so tests do not need to wait."""

    def __init__(self, cfg=None, clock=time.time):
        self.cfg, self.clock = dict(cfg or DEFAULTS), clock
        self.history, self.recent, self.shown_at, self.folded = [], {}, [], 0
        self.listeners = []

    def add(self, source, title, body="", level="info"):
        """Record a notification and tell the listeners. Returns the entry, or None when it was held back."""
        if source in DEFAULTS and not self.cfg.get(source):
            return None
        title, body = shorten(title, 40), shorten(body, 160)
        if not title:
            return None
        now = self.clock()
        key = (source, title, body)
        if now - self.recent.get(key, -1e9) < DEDUPE_SECONDS:
            return None
        self.recent[key] = now
        self.recent = {k: t for k, t in self.recent.items() if now - t < 60}
        level = level if level in LEVELS else "info"
        entry = {"time": now, "source": source, "title": title, "body": body, "level": level}
        self.history = (self.history + [entry])[-HISTORY:]
        limit, window = BURST
        self.shown_at = [t for t in self.shown_at if now - t < window]
        if len(self.shown_at) >= limit:
            self.folded += 1                      # kept in the history, but not another toast right now
            return entry
        self.shown_at.append(now)
        for fn in list(self.listeners):
            try:
                fn(dict(entry, folded=self.folded))
            except Exception:
                pass
        self.folded = 0
        return entry


# ---- what each source watches for

class GpsWatcher:
    """Turns gpsd's mode (0 to 3) into 'fix gained' and 'fix lost' events, once each."""

    def __init__(self):
        self.had = None

    def update(self, mode):
        if mode is None:
            return None
        has = mode >= 2
        event = None
        if self.had is not None and has != self.had:
            event = ("GPS fix gained", f"{'3D' if mode >= 3 else '2D'} fix", "good") if has else \
                ("GPS fix lost", "searching again", "warn")
        elif self.had is None and has:
            event = ("GPS fix", f"{'3D' if mode >= 3 else '2D'} fix", "good")
        self.had = has
        return event


class WarningWatcher:
    """Battery, heat and power warnings, each raised when it starts and again only after it has cleared."""

    def __init__(self):
        self.active = set()

    def update(self, pct=None, charging=False, temp=None, throttled=""):
        events, now = [], set()
        if pct is not None and pct < 15 and not charging:
            now.add("battery")
            if "battery" not in self.active:
                events.append((f"Battery {pct}%", "Plug in soon", "hot" if pct < 8 else "warn"))
        if temp is not None and temp >= 80:
            now.add("heat")
            if "heat" not in self.active:
                events.append((f"CPU is hot: {temp:.0f}C", "It may slow down", "hot"))
        if throttled:
            now.add("power")
            if "power" not in self.active:
                events.append(("Power warning", throttled, "hot"))
        self.active = now
        return events


def activity_key(a):
    return (a.get("time"), a.get("from_id"), a.get("summary"), a.get("sent"))


def mesh_events(activities, seen, known_nodes=None):
    """New incoming things from the mesh, as [(source, title, body, level)], and the updated `seen` set.

    The first call (seen is None) only records what is already there, so opening the app does not announce
    the last forty packets. Our own sent messages are never announced."""
    first = seen is None
    seen = set() if first else set(seen)
    events = []
    for a in activities or []:
        key = activity_key(a)
        if key in seen:
            continue
        seen.add(key)
        if first or a.get("sent"):
            continue
        kind, who = a.get("kind"), a.get("from") or "Unknown"
        if kind == "text":
            events.append(("mesh", who, a.get("summary", ""), "info"))
        elif kind == "checkin":
            events.append(("mesh", f"{who} checked in", a.get("summary", ""), "good"))
        elif kind == "user" and known_nodes is not None and a.get("from_id") not in known_nodes:
            known_nodes.add(a.get("from_id"))
            events.append(("nodes", "New node heard", who, "info"))
    return events, set(list(seen)[-400:])


def system_args(title, body, level):
    """Arguments for the desktop notification call: (summary, body, urgency 0 to 2, timeout ms)."""
    urgency = {"hot": 2, "warn": 1}.get(level, 0)
    return shorten(title, 60), shorten(body, 200), urgency, 8000 if urgency < 2 else 0


def build(win, settings):
    """The notification centre wired to a toast stack and the desktop, for window `win`."""
    from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRect, QTimer, Qt
    from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

    colours = {"info": "#3fe0d0", "good": "#62e06b", "warn": "#ffd23f", "hot": "#ff3b3b"}

    class Toast(QFrame):
        def __init__(self, entry, actions=()):
            super().__init__(win)
            self.sticky = bool(actions)             # one with buttons stays until a button is pressed
            self.setObjectName("toast")
            lay = QVBoxLayout(self)
            lay.setContentsMargins(14, 8, 14, 8)
            lay.setSpacing(2)
            c = colours.get(entry["level"], colours["info"])
            title = QLabel(entry["title"] + (f"   (+{entry['folded']} more)" if entry.get("folded") else ""))
            title.setStyleSheet(f'font-family: "Spleen 12x24"; font-size: 24px; color: {c};')
            body = QLabel(entry["body"])
            body.setWordWrap(True)
            body.setStyleSheet('font-family: "Spleen 8x16"; font-size: 16px; color: #e8e2ee;')
            lay.addWidget(title)
            if entry["body"]:
                lay.addWidget(body)
            if actions:
                lay.addSpacing(6)
                row = QHBoxLayout()
                row.setSpacing(8)
                for text, fn in actions:
                    b = QPushButton(text)
                    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                    b.clicked.connect(lambda _=False, fn=fn: (self.dismiss(), fn()))
                    row.addWidget(b)
                row.addStretch(1)
                lay.addLayout(row)
            self.setStyleSheet(f"#toast {{ background: #15111a; border: 2px solid {c}; border-left: 8px solid {c}; }}")
            from . import style
            style.crisp_fonts(self)
            self.setFixedWidth(440)
            self.adjustSize()
            self.anim = None
            if not self.sticky:
                QTimer.singleShot(7000, self.dismiss)

        def mousePressEvent(self, event):
            if not self.sticky:
                self.dismiss()

        def dismiss(self):
            if self in stack:
                stack.remove(self)
                self.hide()
                self.deleteLater()
                place()

    stack = []

    def place():
        y = 60
        for t in stack:
            target = QRect(win.width() - t.width() - 14, y, t.width(), t.height())
            if t.isHidden():
                t.setGeometry(win.width(), y, t.width(), t.height())
                t.show()
                t.raise_()
                t.anim = QPropertyAnimation(t, b"geometry", t)
                t.anim.setDuration(140)
                t.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
                t.anim.setStartValue(t.geometry())
                t.anim.setEndValue(target)
                t.anim.start()
            else:
                t.setGeometry(target)
            y += t.height() + 8

    centre = Center(config(settings))

    def trim():
        while len(stack) > 3:
            passing = [t for t in stack if not t.sticky]
            (passing or stack)[0].dismiss()

    def offer(source, title, body, actions, level="info"):
        """A notice that stays until one of its buttons is pressed. `actions` is ((label, what to do), ...).
        False when pop-ups, or notices of this kind, are switched off."""
        if not offer_allowed(centre.cfg, source):
            return False
        stack.append(Toast({"title": shorten(title, 40), "body": shorten(body, 160), "level": level}, actions))
        trim()
        place()
        win.activity.add(f"{title}: {body}".strip(": "))
        return True

    def show_toast(entry):
        if centre.cfg.get("toasts"):
            stack.append(Toast(entry))
            trim()
            place()
        if centre.cfg.get("system"):
            send_system(entry)

    def send_system(entry):
        try:
            from PyQt6.QtDBus import QDBusConnection, QDBusInterface
            bus = QDBusConnection.sessionBus()
            iface = QDBusInterface("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                                   "org.freedesktop.Notifications", bus)
            if not iface.isValid():
                return False
            summary, body, urgency, timeout = system_args(entry["title"], entry["body"], entry["level"])
            iface.call("Notify", "uConsole AIO", 0, "", summary, body, [], {"urgency": urgency}, timeout)
            return True
        except Exception:
            return False

    def daemon_available():
        try:
            from PyQt6.QtDBus import QDBusConnection
            return bool(QDBusConnection.sessionBus().interface().isServiceRegistered("org.freedesktop.Notifications").value())
        except Exception:
            return False

    centre.listeners.append(show_toast)
    centre.listeners.append(lambda e: win.activity.add(f"{e['title']}: {e['body']}".strip(": ")))
    centre.stack, centre.reposition, centre.daemon_available = stack, place, daemon_available
    centre.offer = offer
    return centre
