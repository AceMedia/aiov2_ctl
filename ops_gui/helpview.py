"""
The Help tab as a picture: keycap chips for keys, drawn symbols for the uConsole's D-pad, face buttons and trackball,
a small icon beside each tab shortcut, and LED rows for the companions. Everything is drawn with QPainter from bitmaps
and the feature colour tokens, with no smoothing, so it follows the theme and the colour you pick.

The bitmaps and the row lists at the top are plain data, so they can be tested without a screen.
"""

# '.' clear, '#' ink (the text colour), 'o' fill (the feature colour), letters are parts that light up as a variant.
DPAD = [
    "................",
    ".....######.....",
    ".....#uuuu#.....",
    ".....#uuuu#.....",
    ".....#uuuu#.....",
    ".#####....#####.",
    ".#llll....rrrr#.",
    ".#llll.##.rrrr#.",
    ".#llll.##.rrrr#.",
    ".#llll....rrrr#.",
    ".#####....#####.",
    ".....#dddd#.....",
    ".....#dddd#.....",
    ".....#dddd#.....",
    ".....######.....",
    "................",
]
DISC = [
    "................",
    ".....######.....",
    "...##oooooo##...",
    "..#oooooooooo#..",
    "..#oooooooooo#..",
    ".#oooooooooooo#.",
    ".#oooooooooooo#.",
    ".#oooooooooooo#.",
    ".#oooooooooooo#.",
    ".#oooooooooooo#.",
    ".#oooooooooooo#.",
    "..#oooooooooo#..",
    "..#oooooooooo#..",
    "...##oooooo##...",
    ".....######.....",
    "................",
]
LETTERS = {
    "A": [".####.", "##..##", "##..##", "######", "##..##", "##..##", "##..##"],
    "B": ["#####.", "##..##", "##..##", "#####.", "##..##", "##..##", "#####."],
    "X": ["##..##", "##..##", ".####.", "..##..", ".####.", "##..##", "##..##"],
    "Y": ["##..##", "##..##", ".####.", "..##..", "..##..", "..##..", "..##.."],
}
TRACKBALL = [
    "................",
    ".....######.....",
    "....#......#....",
    "...#..####..#...",
    "...#.######.#...",
    "...#.######.#...",
    "...#..####..#...",
    "....#......#....",
    ".....######.....",
    "................",
    ".##############.",
    ".#llll#mm#rrrr#.",
    ".#llll#mm#rrrr#.",
    ".##############.",
    "................",
    "................",
]
ARROW_RIGHT = [
    "................", "................", "................", "........#.......",
    "........##......", "........###.....", "..##########....", "..###########...",
    "..###########...", "..##########....", "........###.....", "........##......",
    "........#.......", "................", "................", "................",
]
TAB_ICONS = {
    "Home": [
        "................", ".......##.......", "......####......", ".....##..##.....",
        "....##....##....", "...##......##...", "..##........##..", ".##############.",
        "...#........#...", "...#..oooo..#...", "...#..o..o..#...", "...#..o..o..#...",
        "...#..o..o..#...", "...##########...", "................", "................"],
    "Overview": [
        "................", ".######..######.", ".#....#..#oooo#.", ".#....#..#oooo#.",
        ".#....#..#oooo#.", ".#....#..#oooo#.", ".######..######.", "................",
        "................", ".######..######.", ".#....#..#....#.", ".#....#..#....#.",
        ".#....#..#....#.", ".#....#..#....#.", ".######..######.", "................"],
    "Everything": [
        "................", ".#..............", ".#...........oo.", ".#..........o...",
        ".#.........o....", ".#....oo..o.....", ".#...o..oo......", ".#..o...........",
        ".#.o............", ".#o.............", ".#..............", ".#..............",
        ".##############.", "................", "................", "................"],
    "Wi-Fi": [
        "................", "................", "....########....", "..###......###..",
        ".##..........##.", ".....######.....", "...###....###...", "...#........#...",
        "......####......", ".....##..##.....", "................", ".......oo.......",
        ".......oo.......", "................", "................", "................"],
    "Bluetooth": [
        "................", ".......#........", ".......##.......", ".......#.#......",
        "....#..#..#.....", ".....#.#.#......", "......###.......", "......###.......",
        ".....#.#.#......", "....#..#..#.....", ".......#.#......", ".......##.......",
        ".......#........", "................", "................", "................"],
    "Network": [
        "................", ".....######.....", ".....#oooo#.....", ".....#oooo#.....",
        ".....######.....", ".......##.......", ".......##.......", "...##########...",
        "...#........#...", "...#........#...", ".#####....#####.", ".#...#....#...#.",
        ".#...#....#...#.", ".#####....#####.", "................", "................"],
    "Marauder": [
        "................", "..############..", "..#..........#..", "..#....oo....#..",
        "..#....oo....#..", "..#..oooooo..#..", "..#..oooooo..#..", "..#....oo....#..",
        "..#....oo....#..", "...#........#...", "...#........#...", "....#......#....",
        ".....#....#.....", "......#..#......", ".......##.......", "................"],
    "GPS": [
        "................", ".....######.....", "...##......##...", "..#....oo....#..",
        "..#....oo....#..", ".#.....oo.....#.", ".#.....oo.....#.", ".#.....oo.....#.",
        ".#.....##.....#.", ".#.....##.....#.", ".#.....##.....#.", "..#....##....#..",
        "..#....##....#..", "...##......##...", ".....######.....", "................"],
    "LoRa": [
        "................", "................", "................", ".##############.",
        ".##..........##.", ".#.##......##.#.", ".#...##..##...#.", ".#.....oo.....#.",
        ".#.....oo.....#.", ".#............#.", ".#............#.", ".#............#.",
        ".##############.", "................", "................", "................"],
    "SDR": [
        "................", "....######......", "...#......#.....", "..#..oooo..#....",
        ".#..oooooo..#...", ".#..oooooo..#...", ".#..oooooo..#...", ".#..oooooo..#...",
        "..#..oooo..#....", "...#......##....", "....######.##...", "...........###..",
        "............###.", ".............##.", "................", "................"],
    "Flipper": [
        "................", "................", "................", ".##############.",
        "#..............#", "#.#######......#", "#.#ooooo#..##..#", "#.#ooooo#.####.#",
        "#.#ooooo#.####.#", "#.#######..##..#", "#..............#", ".##############.",
        "................", "................", "................", "................"],
    "Settings": [
        "................", "......####......", "..##..####..##..", "..############..",
        "...##########...", "...###....###...", ".####......####.", ".####..oo..####.",
        ".####..oo..####.", ".####......####.", "...###....###...", "...##########...",
        "..############..", "..##..####..##..", "......####......", "................"],
    "Help": [
        "................", ".....######.....", "....##....##....", "....##....##....",
        "..........##....", ".........##.....", "........##......", ".......##.......",
        ".......##.......", ".......##.......", "................", ".......oo.......",
        ".......oo.......", "................", "................", "................"],
}

# the rows of each card: (keys, what it does). A key list may hold "+" or "/" between keys.
GENERAL_KEYS = [
    (["Tab"], "next tab"),
    (["Shift", "+", "Tab"], "previous tab"),
]
KEYS = [
    (["R"], "refresh"),
    (["S"], "scan Wi-Fi"),
    (["I"], "Flipper live display (read only)"),
    (["T"], "switch light or dark mode"),
    (["X"], "terminal drawer: activity and a shell"),
    (["E"], "Everything: open it and start a session"),
    (["F", "/", "F11"], "full screen on or off"),
    (["Esc"], "leave full screen, close the drawer"),
    (["Q"], "quit"),
]
RAILS = [
    (["G"], "GPS rail"),
    (["M"], "LoRa rail"),
    (["D"], "SDR rail"),
    (["U"], "USB rail"),
    (["Shift", "+", "G"], "measure a rail's power draw"),
]
# (symbol, variant, what it does): symbol is dpad, face, trackball
BUTTONS = [
    ("dpad", "lr", "move the highlight; along the tabs"),
    ("dpad", "ud", "move the highlight; up to the tabs"),
    ("face", "A", "press what is highlighted"),
    ("face", "B", "back one level"),
    ("face", "X", "refresh"),
    ("face", "Y", "full screen"),
    ("trackball", "rm", "trackball: right back, middle refresh"),
]
TIP = "In the drawer, WINDOW opens your system terminal and CONSOLE swaps to the ops console."
PAD_NOTE = "A B X Y: gamepad mode on the back switch. In keyboard mode they send j k u i."


def variant(rows, hot=""):
    """The bitmap with the parts named in `hot` lit (fill colour) and the other lettered parts clear."""
    letters = set("udlrm")
    return ["".join(("o" if c in hot else ".") if c in letters else c for c in row) for row in rows]


def face(letter):
    """A round pixel button with its letter stamped on it in ink."""
    rows = [list(r) for r in DISC]
    for y, line in enumerate(LETTERS[letter.upper()]):
        for x, c in enumerate(line):
            if c == "#":
                rows[4 + y][5 + x] = "#"
    return ["".join(r) for r in rows]


def arrow(direction):
    right = ARROW_RIGHT
    if direction == "right":
        return list(right)
    if direction == "left":
        return [r[::-1] for r in right]
    down = ["".join(col) for col in zip(*right)]
    return down if direction == "down" else down[::-1]

TAB_ICONS["Games"] = [                                 # a gamepad
    "................",
    "................",
    "................",
    "..####....####..",
    ".##############.",
    ".###o######o###.",
    ".##ooo####o#o##.",
    ".###o######o###.",
    ".##############.",
    ".####......####.",
    ".###........###.",
    ".###........###.",
    "..#..........#..",
    "................",
    "................",
    "................",
]

TAB_ICONS["Wiki"] = [                                  # a page of text
    "................",
    "..############..",
    "..#oooooooooo#..",
    "..#o########o#..",
    "..#oooooooooo#..",
    "..#o######ooo#..",
    "..#oooooooooo#..",
    "..#o########o#..",
    "..#oooooooooo#..",
    "..#o#####oooo#..",
    "..#oooooooooo#..",
    "..#o########o#..",
    "..#oooooooooo#..",
    "..############..",
    "................",
    "................",
]


def tab_icon(name):
    return TAB_ICONS.get(name, TAB_ICONS["Overview"])


def symbol(kind, which):
    if kind == "dpad":
        return variant(DPAD, "lr" if which == "lr" else "ud")
    if kind == "face":
        return face(which)
    return variant(TRACKBALL, which)


def chip_width(label):
    return max(24, 8 * len(label) + 12)


RAIL_KEYS = {"G": "GPS", "M": "LORA", "D": "SDR", "U": "USB"}
CARD_NAME = "WI-FI + BT CARD"


def key_rows(names):
    """The Keys card's rows for the tabs that are showing: a key that opens something which is not there
    (the Flipper's display with no Flipper plugged in) is left out."""
    return [(keys, text) for keys, text in KEYS if "Flipper" not in text or "Flipper" in (names or ())]


def rail_rows(snapshot):
    """[(keys, text, state)]: each rail's key with whether that rail is on or off now ('' when not known,
    and for the row that is not a rail)."""
    rails = (snapshot or {}).get("rails") or {}
    rows = []
    for keys, text in RAILS:
        state = rails.get(RAIL_KEYS.get(keys[0])) if len(keys) == 1 else None
        rows.append((keys, text, state.upper() if state in ("on", "off") else ""))
    return rows


def companion_rows(comp_result, snapshot, pad_text, flip_shown=False, card=None):
    """[(name, state, kind)] with kind present, waiting or absent: the three fixed rows, the internal Wi-Fi and
    Bluetooth card when `card` says whether it is there (True or False), then any other device."""
    # aiov2_ctl calls the list "devices"; "companions" is what this page was first written against
    items = (comp_result.get("devices") or comp_result.get("companions")) if isinstance(comp_result, dict) else None
    items = [d for d in (items or []) if isinstance(d, dict)]
    flip = next((d for d in items if "flipper" in str(d.get("label") or d.get("name") or "").lower()), None)
    rows = []
    if flip or flip_shown:
        rows.append(("FLIPPER", (flip or {}).get("tty", "").replace("/dev/", "") or "found", "present"))
    else:
        rows.append(("FLIPPER", "not plugged in", "absent"))
    pad = (pad_text or "").strip()
    if pad and "waiting" not in pad.lower():
        rows.append(("GAMEPAD", pad.replace("Gamepad: ", "")[:34], "present"))
    else:
        rows.append(("GAMEPAD", "waiting for a button", "waiting"))
    usb = ((snapshot or {}).get("rails") or {}).get("USB")
    rows.append(("USB RAIL", "on" if usb == "on" else "off", "present" if usb == "on" else "absent"))
    if card is not None:                       # the card is powered by the USB rail: off with it, on with it
        rows.append((CARD_NAME, "ready", "present") if card else
                    (CARD_NAME, "not found yet", "waiting") if usb == "on" else
                    (CARD_NAME, "off with the USB rail", "absent"))
    for d in items:
        if d is flip:
            continue
        label = str(d.get("label") or d.get("name") or "device")
        rows.append((label.upper()[:18], str(d.get("tty", "")).replace("/dev/", "") or "found", "present"))
    return rows


def build(win, card):
    """The Help page widget and a refresh(names, comp_result, snapshot, pad_text) function. Qt is imported here."""
    from PyQt6.QtCore import QPoint, QRect, Qt
    from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
    from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget
    from . import skin, style

    cache = {}

    def tokens():
        return style.TOKENS["dark" if getattr(win, "theme", "dark") == "dark" else "light"]

    def pixmap(rows, scale=1):
        t = tokens()
        key = ("".join(rows), t["text"], t["accent"], scale)
        if key not in cache:
            img = QImage(16, 16, QImage.Format.Format_ARGB32)
            img.fill(0)
            for y, row in enumerate(rows):
                for x, c in enumerate(row):
                    if c in "#o":
                        img.setPixelColor(x, y, QColor(t["text"] if c == "#" else t["accent"]))
            pm = QPixmap.fromImage(img)
            if scale > 1:
                pm = pm.scaled(16 * scale, 16 * scale, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
            cache[key] = pm
        return cache[key]

    def font():
        return style.pixel_font(16)

    class Row(QWidget):
        """Keys on the left, a gap, then what they do. Drawn in one go so the baselines and spacing always match."""

        def __init__(self, keys=(), icon=None, text="", keycol=136, height=28, symbol_rows=None, numeric=False,
                     state=""):
            super().__init__()
            self.keys, self.icon, self.text, self.keycol = list(keys), icon, text, keycol
            self.state = state                                  # ON or OFF at the right-hand end, or nothing
            self.symbol_rows, self.numeric = symbol_rows, numeric
            self.setFixedHeight(height)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        def paintEvent(self, _):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            t, f = tokens(), font()
            p.setFont(f)
            h = self.height()
            x = 0
            if self.symbol_rows is not None:
                pm = pixmap(self.symbol_rows, 2)
                p.drawPixmap(x, (h - pm.height()) // 2, pm)
            for k in self.keys:
                if k in ("+", "/"):
                    p.setPen(QColor(t["muted"]))
                    p.drawText(QRect(x, 0, 20, h), Qt.AlignmentFlag.AlignCenter, k)
                    x += 20
                    continue
                w, ch = chip_width(k), 24
                y = (h - ch) // 2
                border, face_c = QColor(t["border"]), QColor(t["row"])
                p.fillRect(x + 1, y, w - 2, 1, border)
                p.fillRect(x + 1, y + ch - 1, w - 2, 1, border)
                p.fillRect(x, y + 1, 1, ch - 2, border)
                p.fillRect(x + w - 1, y + 1, 1, ch - 2, border)
                p.fillRect(x + 1, y + 1, w - 2, ch - 5, face_c.lighter(125))
                p.fillRect(x + 1, y + ch - 4, w - 2, 3, face_c.darker(160))
                p.fillRect(x + 1, y + 1, w - 2, 1, face_c.lighter(160))
                p.fillRect(x + 1, y + 1, 1, ch - 5, face_c.lighter(160))
                p.setPen(QColor(t["accent"] if self.numeric else t["text"]))
                p.drawText(QRect(x, y, w, ch - 3), Qt.AlignmentFlag.AlignCenter, k)
                x += w + 4
            x = max(x + 8, self.keycol + 12) if self.keys else (36 if self.symbol_rows is not None else x)
            if self.icon is not None:
                pm = pixmap(self.icon, 1)
                p.drawPixmap(x, (h - 16) // 2, pm)
                x += 24
            p.setPen(QColor(t["text"]))
            p.drawText(QRect(x, 0, self.width() - x, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text)
            if self.state:
                p.setPen(QColor(style.good_colour(getattr(win, "theme", "dark")) if self.state == "ON"
                                else t["muted"]))
                p.drawText(QRect(0, 0, self.width() - 4, h),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, self.state)
            p.end()

    class Status(QWidget):
        """An LED, a capital name, a dotted leader and the state on the right."""

        def __init__(self, name, state, kind):
            super().__init__()
            self.name, self.state, self.kind = name, state, kind
            self.setFixedHeight(28)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        def paintEvent(self, _):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            t, h = tokens(), self.height()
            p.setFont(font())
            colour = {"present": style.good_colour(getattr(win, "theme", "dark")), "waiting": t["accent2"],
                      "absent": t["muted"]}[self.kind]
            y = (h - 10) // 2
            if self.kind == "absent":
                p.fillRect(0, y, 10, 10, QColor(t["muted"]))
                p.fillRect(2, y + 2, 6, 6, QColor(t["card"]))
            else:
                p.fillRect(0, y, 10, 10, QColor("#111111"))
                p.fillRect(1, y + 1, 8, 8, QColor(colour))
                p.fillRect(1, y + 1, 2, 2, QColor(colour).lighter(160))
            p.setPen(QColor(t["text"]))
            p.drawText(QRect(20, 0, 160, h), Qt.AlignmentFlag.AlignVCenter, self.name)
            state_w = 9 * len(self.state)
            p.setPen(QColor(t["muted"]))
            p.drawText(QRect(0, 0, self.width(), h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, self.state)
            lead_from, lead_to = 20 + 9 * len(self.name) + 8, self.width() - state_w - 8
            for x in range(lead_from, max(lead_from, lead_to), 6):
                p.fillRect(x, h // 2 + 6, 2, 2, QColor(t["border"]))
            p.end()

    page = QWidget()
    grid = QGridLayout(page)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(8)
    tabs_box, tabs_l = card("Tabs")
    keys_box, keys_l = card("Keys")
    rails_box, rails_l = card("Rails")
    buttons_box, buttons_l = card("uConsole buttons")
    comp_box, comp_l = card("Companions")
    tab_rows, comp_rows, key_widgets, rail_widgets = [], [], [], []
    for kind, which, text in BUTTONS:
        buttons_l.addWidget(Row((), None, text, height=32, symbol_rows=symbol(kind, which)))
    note = QLabel(PAD_NOTE)
    note.setWordWrap(True)
    note.setStyleSheet(f"font-size: 10pt; color: {tokens()['accent2']};")
    buttons_l.addWidget(note)
    buttons_l.addStretch(1)
    grid.addWidget(tabs_box, 0, 0, 2, 1)
    grid.addWidget(keys_box, 0, 1)
    grid.addWidget(rails_box, 1, 1)
    grid.addWidget(buttons_box, 0, 2)
    grid.addWidget(comp_box, 1, 2)
    tip_row = QHBoxLayout()
    tip_tag = QLabel("TIP")
    tip_tag.setObjectName("tag")
    tip_label = QLabel(TIP)
    tip_label.setStyleSheet("font-size: 11pt;")
    tip_row.addWidget(tip_tag)
    tip_row.addWidget(tip_label, 1)
    grid.addLayout(tip_row, 2, 0, 1, 3)
    for col, stretch in enumerate((3, 4, 4)):
        grid.setColumnStretch(col, stretch)
    grid.setRowStretch(0, 5)
    grid.setRowStretch(1, 3)
    state = {"names": None, "keys": None, "rails": None}

    def clear(layout, widgets):
        for w in widgets:
            layout.removeWidget(w)
            w.deleteLater()
        widgets.clear()
        while layout.count() and layout.itemAt(layout.count() - 1).spacerItem():      # the closing stretch
            layout.takeAt(layout.count() - 1)

    def refresh(names, comp_result, snapshot, pad_text):
        if state["names"] != tuple(names):
            state["names"] = tuple(names)
            clear(tabs_l, tab_rows)
            for i, name in enumerate(names[:9]):
                r = Row([str(i + 1)], tab_icon(name), name, keycol=36, numeric=True)
                tabs_l.addWidget(r)
                tab_rows.append(r)
            for keys, text in GENERAL_KEYS:
                r = Row(keys, None, text, keycol=112)
                tabs_l.addWidget(r)
                tab_rows.append(r)
            tabs_l.addStretch(1)
        wanted = key_rows(names)
        if state["keys"] != wanted:                              # a key for a tab that is not there is left out
            state["keys"] = wanted
            clear(keys_l, key_widgets)
            for keys, text in wanted:
                r = Row(keys, None, text, keycol=84)
                keys_l.addWidget(r)
                key_widgets.append(r)
            keys_l.addStretch(1)
        wanted = rail_rows(snapshot)
        if state["rails"] != wanted:                             # each rail's key says whether it is on now
            state["rails"] = wanted
            clear(rails_l, rail_widgets)
            for keys, text, on in wanted:
                r = Row(keys, None, text, keycol=100, state=on)
                rails_l.addWidget(r)
                rail_widgets.append(r)
            rails_l.addStretch(1)
        clear(comp_l, comp_rows)
        has_card = getattr(win, "bt_available", None)
        for name, st, kind in companion_rows(comp_result, snapshot, pad_text, flip_shown="Flipper" in names,
                                             card=has_card() if callable(has_card) else None):
            r = Status(name, st, kind)
            comp_l.addWidget(r)
            comp_rows.append(r)
        comp_l.addStretch(1)
        page.update()

    return page, refresh
