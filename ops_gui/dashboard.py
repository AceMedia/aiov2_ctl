"""
The Overview dashboard: which cards show, in what order, and which rails appear on it.

The choice is kept in the app settings and edited on Settings > Dashboard. The helpers are pure so
they can be tested without a screen. Cards with nothing to show (no Flipper, no companions) stay
hidden whatever the choice, so turning one on never leaves an empty box.
"""
WIDGETS = ("battery", "rails", "companions", "radios", "system", "gps", "lora", "sdr", "wifi")
LABELS = {
    "battery": "Battery and power",
    "rails": "Rails and their switches",
    "companions": "Companions (the Flipper, when plugged in, shows inside it)",
    "radios": "Wi-Fi radios",
    "system": "System",
    "gps": "GPS status (when the GPS rail is on)",
    "lora": "LoRa status (when the LoRa rail is on)",
    "sdr": "SDR status (when the SDR rail is on)",
    "wifi": "Last Wi-Fi scan",
}
STATUS_ITEMS = ("battery", "cpu", "temp", "ram", "load", "disk", "uptime", "ip", "power")
STATUS_LABELS = {
    "battery": "Battery", "cpu": "CPU use", "temp": "Temperature", "ram": "Memory", "load": "Load average",
    "disk": "Disk use", "uptime": "Uptime", "ip": "Network address", "power": "Power warnings",
}
FULL_WIDTH = ("battery", "rails")
RAILS = ("GPS", "LORA", "SDR", "USB")
RAIL_NAMES = {"GPS": "GPS", "LORA": "LoRa", "SDR": "SDR", "USB": "USB hub"}


def default():
    return {"order": list(WIDGETS), "hidden": [], "rails_hidden": [], "status_hidden": []}


def normalize(cfg):
    """A complete, valid layout from whatever was saved: unknown names dropped, new ones added."""
    cfg = cfg if isinstance(cfg, dict) else {}
    seen, order = set(), []
    for name in cfg.get("order") if isinstance(cfg.get("order"), list) else []:
        if name in WIDGETS and name not in seen:
            seen.add(name)
            order.append(name)
    order += [w for w in WIDGETS if w not in seen]
    hidden = [n for n in dict.fromkeys(cfg.get("hidden") if isinstance(cfg.get("hidden"), list) else [])
              if n in WIDGETS]
    rails_hidden = [n for n in dict.fromkeys(cfg.get("rails_hidden")
                                             if isinstance(cfg.get("rails_hidden"), list) else [])
                    if n in RAILS]
    status_hidden = [n for n in dict.fromkeys(cfg.get("status_hidden")
                                              if isinstance(cfg.get("status_hidden"), list) else [])
                     if n in STATUS_ITEMS]
    return {"order": order, "hidden": hidden, "rails_hidden": rails_hidden, "status_hidden": status_hidden}


def move(cfg, name, step):
    """Move a card up (-1) or down (+1) in the order."""
    cfg = normalize(cfg)
    order = cfg["order"]
    if name not in order:
        return cfg
    i = order.index(name)
    j = max(0, min(len(order) - 1, i + step))
    order.insert(j, order.pop(i))
    return cfg


def set_shown(cfg, name, shown):
    cfg = normalize(cfg)
    if name in WIDGETS:
        cfg["hidden"] = [n for n in cfg["hidden"] if n != name] + ([] if shown else [name])
    return cfg


def set_rail_shown(cfg, rail, shown):
    cfg = normalize(cfg)
    if rail in RAILS:
        cfg["rails_hidden"] = [n for n in cfg["rails_hidden"] if n != rail] + ([] if shown else [rail])
    return cfg


def set_status_shown(cfg, item, shown):
    cfg = normalize(cfg)
    if item in STATUS_ITEMS:
        cfg["status_hidden"] = [n for n in cfg["status_hidden"] if n != item] + ([] if shown else [item])
    return cfg


def status_shown(cfg, item):
    return item not in normalize(cfg)["status_hidden"]


def is_shown(cfg, name):
    return name not in normalize(cfg)["hidden"]


def shown_rails(cfg, present):
    """The rails to draw, in the usual order: ones the board reports and the user has not hidden."""
    hidden = normalize(cfg)["rails_hidden"]
    return [r for r in RAILS if r in present and r not in hidden]


def plan(cfg, has_content):
    """The layout as rows: ("full", name) for wide cards, ("grid", [names]) for runs of small ones.

    `has_content` maps a card to whether it has anything to show; cards with none are left out.
    """
    cfg = normalize(cfg)
    rows, run = [], []
    for name in cfg["order"]:
        if name in cfg["hidden"] or not has_content.get(name, True):
            continue
        if name in FULL_WIDTH:
            if run:
                rows.append(("grid", run))
                run = []
            rows.append(("full", name))
        else:
            run.append(name)
    if run:
        rows.append(("grid", run))
    return rows


def build_page(win):
    """The Settings > Dashboard page. `win` provides dash, set_dashboard(cfg)."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                                 QVBoxLayout, QWidget)

    inner = QWidget()
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidget(inner)
    root = QVBoxLayout(inner)

    def card(title):
        box = QFrame()
        box.setObjectName("card")
        v = QVBoxLayout(box)
        head = QLabel(title.upper())
        head.setObjectName("tag")
        v.addWidget(head, 0, Qt.AlignmentFlag.AlignLeft)
        root.addWidget(box)
        return v

    def small_button(text, slot):
        b = QPushButton(text)
        b.setStyleSheet("font-size: 12pt; padding: 4px 12px;")
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.clicked.connect(slot)
        return b

    v = card("Cards on the Overview")
    note = QLabel("Tick what you want, and move cards up or down. Cards with nothing to show, "
                  "like Flipper status with no Flipper, stay hidden.")
    note.setWordWrap(True)
    note.setStyleSheet("font-size: 12pt; color: #9e9e9e;")
    v.addWidget(note)
    cards_lay = QVBoxLayout()
    v.addLayout(cards_lay)

    def fill_cards(cfg):
        while cards_lay.count():
            item = cards_lay.takeAt(0)
            if item.layout():
                while item.layout().count():
                    w = item.layout().takeAt(0).widget()
                    if w:
                        w.deleteLater()
        for name in cfg["order"]:
            h = QHBoxLayout()
            box = QCheckBox(LABELS[name])
            box.setStyleSheet("font-size: 13pt;")
            box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            box.setChecked(name not in cfg["hidden"])
            box.clicked.connect(lambda checked, n=name: win.set_dashboard(set_shown(win.dash, n, checked)))
            h.addWidget(box, 1)
            h.addWidget(small_button("Up", lambda _=False, n=name: win.set_dashboard(move(win.dash, n, -1))))
            h.addWidget(small_button("Down", lambda _=False, n=name: win.set_dashboard(move(win.dash, n, 1))))
            cards_lay.addLayout(h)

    v = card("Status bar")
    v.addWidget(QLabel("What shows along the bottom of the window."))
    status_boxes = {}
    for item in STATUS_ITEMS:
        box = QCheckBox(STATUS_LABELS[item])
        box.setStyleSheet("font-size: 13pt;")
        box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        box.clicked.connect(lambda checked, i=item: win.set_dashboard(set_status_shown(win.dash, i, checked)))
        v.addWidget(box)
        status_boxes[item] = box

    v = card("Rails shown")
    rail_boxes = {}
    for rail in RAILS:
        box = QCheckBox(RAIL_NAMES[rail])
        box.setStyleSheet("font-size: 13pt;")
        box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        box.clicked.connect(lambda checked, r=rail: win.set_dashboard(set_rail_shown(win.dash, r, checked)))
        v.addWidget(box)
        rail_boxes[rail] = box
    v.addWidget(small_button("Reset to the default layout", lambda _=False: win.set_dashboard(default())))
    root.addStretch(1)

    def refresh():
        cfg = normalize(win.dash)
        fill_cards(cfg)
        for rail, box in rail_boxes.items():
            box.setChecked(rail not in cfg["rails_hidden"])
        for item, box in status_boxes.items():
            box.setChecked(item not in cfg["status_hidden"])

    scroll.refresh = refresh
    return scroll
