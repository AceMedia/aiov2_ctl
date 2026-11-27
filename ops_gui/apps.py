"""The Apps tab: the programs installed on this computer, as a grid of tiles to start them from the app.

Optional, off until it is switched on in Settings > General. A program is listed when it has a menu entry (a
.desktop file), which is how the desktop's own menu knows it, so this shows what that menu shows: packages,
Flatpaks, Snaps and anything a person added themselves. They are sorted into the menu's own sections, Games
first, one sub-tab each. Nothing is downloaded and nothing is sent; starting a program runs the command its menu
entry gives, exactly as the menu would.

Everything above build() is plain Python and is tested without a screen."""
import os
import shlex

SETTING = "apps_tab"
OLD_SETTING = "games_tab"                      # the tab's first name; someone who switched that on keeps the tab
FIELD_CODES = "fFuUdDnNickvm"                  # %f, %U and the rest: places for a file or an address; none here
FLATPAK_MARKS = ("@@", "@@u")                  # Flatpak wraps those places in these; with no file they go too
ICON_SIZES = ("scalable", "512x512", "256x256", "128x128", "96x96", "64x64", "48x48", "32x32")
ICON_TYPES = (".png", ".svg")
# The sections of the desktop's menu, in the order of the sub-tabs, with the categories that put a program there.
SECTIONS = (
    ("Games", ("Game",)),
    ("Internet", ("Network",)),
    ("Office", ("Office",)),
    ("Graphics", ("Graphics",)),
    ("Sound & Video", ("AudioVideo", "Audio", "Video")),
    ("Programming", ("Development",)),
    ("Education", ("Education", "Science")),
    ("Accessories", ("Utility",)),
    ("System", ("System",)),
    ("Settings", ("Settings",)),
)
OTHER = "Other"
NONE_FOUND = "No programs found. A program shows here when it is installed with an entry in the desktop's menu."
HINT = "A or Enter  open        X or R  look again"


def wanted(settings):
    """Whether the tab is switched on, under its name now or its first one."""
    return bool(settings.get(SETTING) or settings.get(OLD_SETTING))


def parse_entry(text):
    """The keys of a menu entry's [Desktop Entry] part. Translated names (Name[de]) and other parts are left."""
    values, here = {}, False
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            here = line == "[Desktop Entry]"
            continue
        if here and "=" in line:
            key, value = line.split("=", 1)
            key = key.strip()
            if "[" not in key:
                values.setdefault(key, value.strip())
    return values


def _yes(value):
    return str(value or "").strip().lower() == "true"


def _listed(value):
    return [part.strip() for part in (value or "").split(";") if part.strip()]


def is_shown(entry, desktops=()):
    """A program the menu shows: not hidden, with a name and something to run, and not kept for a desktop
    other than this one. `desktops` are the names this session goes by (XDG_CURRENT_DESKTOP)."""
    if entry.get("Type", "Application") != "Application" or _yes(entry.get("NoDisplay")) or _yes(entry.get("Hidden")):
        return False
    if not entry.get("Name") or not entry.get("Exec"):
        return False
    only, never = _listed(entry.get("OnlyShowIn")), _listed(entry.get("NotShowIn"))
    if only and not set(only) & set(desktops):
        return False
    return not set(never) & set(desktops)


def command(exec_line):
    """The program and its arguments from an entry's Exec line, with the places for a file taken out. [] when
    the line cannot be read."""
    try:
        parts = shlex.split(exec_line or "")
    except ValueError:
        return []
    out = []
    for part in parts:
        if part in FLATPAK_MARKS:
            continue
        kept, i, had_code = "", 0, False
        while i < len(part):
            if part[i] == "%" and i + 1 < len(part):
                code = part[i + 1]
                if code == "%":
                    kept += "%"
                elif code in FIELD_CODES:
                    had_code = True
                else:
                    kept += "%" + code
                i += 2
            else:
                kept += part[i]
                i += 1
        if kept or not had_code:
            out.append(kept)
    return out


def data_dirs(env, home):
    """Where menu entries and icons live, the person's own first so that theirs wins. Flatpak's and Snap's are
    always looked in: an app started outside the desktop session is not told about them."""
    own = env.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    shared = (env.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    extra = [os.path.join(own, "flatpak", "exports", "share"), "/var/lib/flatpak/exports/share",
             "/var/lib/snapd/desktop"]
    out = []
    for d in [own] + shared + extra:
        d = d.rstrip("/") if len(d) > 1 else d
        if d and d not in out:
            out.append(d)
    return out


def desktops(env):
    return [d for d in (env.get("XDG_CURRENT_DESKTOP") or "").split(":") if d]


def find_apps(dirs, listdir, read, which, session=()):
    """Every program the menu shows, by name. `listdir(folder)` gives file names or raises OSError,
    `read(path)` gives the text or None, `which(program)` gives its path or None. The first folder to have an
    entry of a given file name decides it, as on the desktop: a person's own copy can change or hide the
    system's."""
    seen, apps = set(), []
    for d in dirs:
        folder = os.path.join(d, "applications")
        try:
            names = sorted(listdir(folder))
        except OSError:
            continue
        for name in names:
            if not name.endswith(".desktop") or name in seen:
                continue
            text = read(os.path.join(folder, name))
            if text is None:
                continue
            seen.add(name)
            entry = parse_entry(text)
            if not is_shown(entry, session):
                continue
            argv = command(entry["Exec"])
            if not argv or which(entry.get("TryExec") or argv[0]) is None:
                continue                                # its program is not here any more
            apps.append({"id": name[:-len(".desktop")], "name": entry["Name"], "about": entry.get("Comment", ""),
                         "argv": argv, "icon": entry.get("Icon", ""), "terminal": _yes(entry.get("Terminal")),
                         "folder": entry.get("Path", ""), "categories": _listed(entry.get("Categories"))})
    return sorted(apps, key=lambda a: (a["name"].casefold(), a["id"]))


def sections(apps):
    """[(section, its programs)] in the order of the sub-tabs, empty sections left out. A program is in every
    section its categories name, as in the desktop's menu, and in Other when they name none."""
    out, placed = [], set()
    for title, cats in SECTIONS:
        members = [a for a in apps if set(cats) & set(a["categories"])]
        if members:
            out.append((title, members))
            placed.update(a["id"] for a in members)
    rest = [a for a in apps if a["id"] not in placed]
    if rest:
        out.append((OTHER, rest))
    return out


def grid_step(index, count, columns, direction):
    """Which tile an arrow moves to in a grid filled row by row, or None at the edge, where the highlight is
    free to leave the grid. Down from above a short last row lands on the last tile."""
    if count <= 0 or columns <= 0 or not 0 <= index < count:
        return None
    if direction == "left":
        return index - 1 if index % columns else None
    if direction == "right":
        return index + 1 if index + 1 < count and (index + 1) % columns else None
    if direction == "up":
        return index - columns if index >= columns else None
    if direction == "down":
        if index + columns < count:
            return index + columns
        return count - 1 if (count - 1) // columns > index // columns else None
    return None


def launch_argv(app, terminal):
    """What to run to start a program. One that draws in a terminal is given one; None when there is none."""
    if not app["terminal"]:
        return list(app["argv"])
    if not terminal:
        return None
    return [terminal, "-e"] + list(app["argv"])


def icon_file(name, dirs, exists):
    """A picture file for an entry's Icon, for when the desktop's icon theme does not know it."""
    if not name:
        return None
    if os.path.isabs(name):
        return name if exists(name) else None
    for d in dirs:
        for size in ICON_SIZES:
            for kind in ICON_TYPES:
                path = os.path.join(d, "icons", "hicolor", size, "apps", name + kind)
                if exists(path):
                    return path
    for d in dirs:
        for kind in ICON_TYPES + (".xpm",):
            path = os.path.join(d, "pixmaps", name + kind)
            if exists(path):
                return path
    return None


# ---- the desktop's icon theme
# Many entries name a picture from the desktop's icon theme rather than their own (this project's launchers ask
# for "utilities-system-monitor"). Qt only knows that theme when the desktop tells it, which it does not for an
# app started outside the session's own launcher, so the theme is found and searched here.

THEME_KEYS = ("gtk-icon-theme-name", "icon_theme", "icon-theme")
THEME_SETTINGS = (".config/gtk-3.0/settings.ini", ".config/gtk-4.0/settings.ini", ".config/qt6ct/qt6ct.conf",
                  ".config/qt5ct/qt5ct.conf", ".config/lxqt/lxqt.conf")
WANTED_SIZE = 64


def theme_from_settings(text):
    """The icon theme a desktop settings file names, or None."""
    for line in (text or "").splitlines():
        line = line.strip()
        for key in THEME_KEYS:
            if line.startswith(key) and "=" in line and line.split("=", 1)[0].strip() == key:
                value = line.split("=", 1)[1].strip().strip("\"'")
                if value:
                    return value
    return None


def theme_parents(index_text):
    """The themes an icon theme falls back on: the Inherits line of its index.theme."""
    for line in (index_text or "").splitlines():
        line = line.strip()
        if line.startswith("Inherits") and "=" in line:
            return [t.strip() for t in line.split("=", 1)[1].split(",") if t.strip()]
    return []


def theme_chain(name, read_index, limit=8):
    """The themes to look in, in order: the desktop's, the ones it falls back on, and hicolor last.
    `read_index(theme)` gives that theme's index.theme text, or None."""
    chain, queue = [], [name] if name else []
    while queue and len(chain) < limit:
        theme = queue.pop(0)
        if theme in chain or theme == "hicolor":
            continue
        chain.append(theme)
        queue.extend(theme_parents(read_index(theme)))
    return chain + ["hicolor"]


def icon_size(path):
    """The size of the pictures in a theme folder, read from the path: .../48x48/apps/x.png and .../apps/48/x.svg
    are both 48. A folder of drawings that scale counts as large; an unknown one as 0."""
    for part in reversed(path.split("/")[:-1]):
        if part == "scalable":
            return 512
        digits = ""
        for ch in part:
            if not ch.isdigit():
                break
            digits += ch
        if digits:
            return int(digits)
    return 0


def _rank(path):
    size = icon_size(path)
    return (size >= WANTED_SIZE, -size if size >= WANTED_SIZE else size, path.endswith(".png"))


def theme_index(paths):
    """{icon name: the best file for a tile} from every file of a theme. Best is the smallest that is at least
    the tile's size, else the largest there is. Outline-only ("symbolic") pictures are left out."""
    best = {}
    for path in paths:
        file = path.rsplit("/", 1)[-1]
        name, dot, kind = file.rpartition(".")
        if not dot or "." + kind not in ICON_TYPES or name.endswith("-symbolic"):
            continue
        if name not in best or _rank(path) > _rank(best[name]):
            best[name] = path
    return best


def initial(name):
    """The letter drawn on the tile of a program that has no picture: its name's first letter or digit."""
    for ch in name or "":
        if ch.isalnum():
            return ch.upper()
    return "?"


def about_text(app, count, section):
    """The line under the grid: what the chosen program is, and how many are in this section."""
    many = "1 program" if count == 1 else f"{count} programs"
    if app is None:
        return NONE_FOUND if not count else f"{section}: {many}"
    return f"{app['name']}: {app['about']}" if app["about"] else app["name"]


def started_text(app, error=None, no_terminal=False):
    if no_terminal:
        return f"{app['name']} draws in a terminal, and no terminal program was found to give it."
    if error:
        return f"Could not start {app['name']}: {error}"
    return f"Started {app['name']}."


def build(win):
    """The Apps page: a sub-tab per section, each a grid of tiles, and a button to open the chosen one. Qt is
    imported here."""
    import shutil
    import subprocess
    from PyQt6.QtCore import QRect, QSize, Qt
    from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
    from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QListView, QListWidget, QListWidgetItem, QPushButton,
                                 QVBoxLayout, QWidget)
    from .foldertabs import FolderTabWidget

    state = {"apps": None, "dirs": [], "titles": [], "themes": {}, "theme": False}

    def read(path):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return None

    def lettered(app):
        """A plain tile with the program's first letter, so one with no picture still lines up with the rest."""
        pm = QPixmap(64, 64)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(76, 86, 116))
        p.drawRoundedRect(4, 4, 56, 56, 10, 10)
        font = QFont(p.font())
        font.setPixelSize(30)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor(236, 239, 247))
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, initial(app["name"]))
        p.end()
        return QIcon(pm)

    def desktop_theme():
        """The desktop's icon theme: what Qt was told, else what the desktop's settings files say."""
        if state["theme"] is False:
            found = QIcon.themeName() if QIcon.themeName() not in ("", "hicolor") else None
            for file in THEME_SETTINGS:
                if found:
                    break
                found = theme_from_settings(read(os.path.join(os.path.expanduser("~"), file)))
            state["theme"] = found
        return state["theme"]

    def theme_files(theme):
        """Every file of one icon theme, across the folders it may be spread over. Read once."""
        if theme not in state["themes"]:
            paths = []
            for d in state["dirs"]:
                for folder, _dirs, files in os.walk(os.path.join(d, "icons", theme)):
                    paths.extend(folder + "/" + f for f in files)
            state["themes"][theme] = theme_index(paths)
        return state["themes"][theme]

    def themed(name):
        def index_of(theme):
            for d in state["dirs"]:
                text = read(os.path.join(d, "icons", theme, "index.theme"))
                if text is not None:
                    return text
            return None
        for theme in theme_chain(desktop_theme(), index_of):
            path = theme_files(theme).get(name)
            if path:
                return path
        return None

    def icon_of(app):
        name = app["icon"]
        if name and not os.path.isabs(name) and QIcon.hasThemeIcon(name):
            icon = QIcon.fromTheme(name)
        else:
            path = icon_file(name, state["dirs"], os.path.isfile)
            if path is None and name and not os.path.isabs(name):
                path = themed(name)
            icon = QIcon(path) if path else QIcon()
        return lettered(app) if icon.isNull() or icon.pixmap(64, 64).isNull() else icon

    class Grid(QListWidget):
        """One section. The arrows move tile to tile in all four directions (grid_step), and at an edge the
        highlight leaves for the sub-tabs above or the buttons below."""

        def __init__(self, members):
            super().__init__()
            self.members = members
            self.setViewMode(QListView.ViewMode.IconMode)
            self.setFlow(QListView.Flow.LeftToRight)
            self.setWrapping(True)
            self.setResizeMode(QListView.ResizeMode.Adjust)
            self.setMovement(QListView.Movement.Static)
            self.setWordWrap(True)                             # a long name takes a second line
            self.setTextElideMode(Qt.TextElideMode.ElideRight)
            self.setSpacing(0)
            self.setIconSize(QSize(64, 64))
            self.setGridSize(QSize(158, 132))                  # big enough to hit with the trackball
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.setStyleSheet("QListWidget { font-size: 10pt; padding: 6px; }")
            for app in members:
                item = QListWidgetItem(icon_of(app), app["name"])
                item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
                item.setToolTip(app["about"])
                self.addItem(item)
            if members:
                self.setCurrentRow(0)

        def showEvent(self, event):                            # laid out while hidden, it had no width to go by
            super().showEvent(event)
            self.scheduleDelayedItemsLayout()

        def chosen(self):
            i = self.currentRow()
            return self.members[i] if 0 <= i < len(self.members) else None

        def columns(self):
            if not self.count():
                return 1
            top = self.visualItemRect(self.item(0)).top()
            n = 0
            while n < self.count() and self.visualItemRect(self.item(n)).top() == top:
                n += 1
            return max(1, n)

        def nav_enter(self):
            if self.currentRow() < 0 and self.count():
                self.setCurrentRow(0)

        def nav_move(self, direction):
            there = grid_step(self.currentRow(), self.count(), self.columns(), direction)
            if there is None:
                return False
            self.setCurrentRow(there)
            self.scrollToItem(self.item(there))
            return True

        def nav_rect(self):
            item = self.currentItem()
            if item is None:
                return None
            r = self.visualItemRect(item)
            return QRect(self.viewport().mapTo(self, r.topLeft()), r.size())

        def nav_activate(self):
            play()

    class Page(QWidget):
        def showEvent(self, event):                     # a program installed since is there the next time
            super().showEvent(event)
            refresh()

    box = Page()
    outer = QVBoxLayout(box)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(6)
    subs = FolderTabWidget()
    outer.addWidget(subs, 1)
    about = QLabel("")
    about.setWordWrap(True)
    about.setStyleSheet("font-size: 11pt;")
    outer.addWidget(about)
    row = QHBoxLayout()
    open_b, again_b = QPushButton("Open"), QPushButton("Look again")
    for b in (open_b, again_b):
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setMinimumHeight(44)
        b.setStyleSheet("font-size: 13pt; padding: 4px 18px;")
        row.addWidget(b)
    row.addStretch(1)
    hint = QLabel(HINT)
    hint.setStyleSheet("font-size: 10pt;")
    row.addWidget(hint)
    outer.addLayout(row)

    def grid():
        return subs.currentWidget()

    def chosen():
        g = grid()
        return g.chosen() if g is not None else None

    def show_about(*_):
        g, app = grid(), chosen()
        title = state["titles"][subs.currentIndex()] if 0 <= subs.currentIndex() < len(state["titles"]) else ""
        about.setText(about_text(app, len(g.members) if g is not None else 0, title))
        open_b.setEnabled(app is not None)

    def refresh():
        state["dirs"] = data_dirs(os.environ, os.path.expanduser("~"))
        found = find_apps(state["dirs"], os.listdir, read, shutil.which, desktops(os.environ))
        if found == state["apps"]:
            return
        was_title = state["titles"][subs.currentIndex()] if 0 <= subs.currentIndex() < len(state["titles"]) else None
        was_app = (chosen() or {}).get("id")
        state["apps"] = found
        subs.blockSignals(True)
        while subs.count():
            old = subs.widget(0)
            subs.removeTab(0)
            old.deleteLater()
        titles = []
        for title, members in sections(found):
            g = Grid(members)
            g.currentRowChanged.connect(show_about)
            g.itemActivated.connect(play)
            subs.addTab(g, title.replace("&", "&&"))            # a lone & would be read as a shortcut mark
            titles.append(title)
        state["titles"] = titles
        if was_title in titles:
            subs.setCurrentIndex(titles.index(was_title))
            ids = [a["id"] for a in grid().members]
            if was_app in ids:
                grid().setCurrentRow(ids.index(was_app))
        subs.blockSignals(False)
        show_about()

    def play(*_):
        app = chosen()
        if app is None:
            return
        argv = launch_argv(app, shutil.which("x-terminal-emulator"))
        if argv is None:
            text = started_text(app, no_terminal=True)
        else:
            try:
                subprocess.Popen(argv, cwd=app["folder"] or os.path.expanduser("~"), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                text = started_text(app)
            except OSError as e:
                text = started_text(app, error=e.strerror or str(e))
        about.setText(text)
        activity = getattr(win, "activity", None)
        if activity is not None:
            activity.add("Apps: " + text)

    subs.currentChanged.connect(show_about)
    open_b.clicked.connect(play)
    again_b.clicked.connect(lambda: refresh())
    box.main_action, box.refresh, box.subs, box.grid, box.open_button = play, refresh, subs, grid, open_b
    box.icon_source = lambda app: ("theme" if app["icon"] and not os.path.isabs(app["icon"])
                                   and QIcon.hasThemeIcon(app["icon"]) else
                                   icon_file(app["icon"], state["dirs"], os.path.isfile)
                                   or (themed(app["icon"]) if app["icon"] and not os.path.isabs(app["icon"]) else None))
    box.apps, box.note, box.titles = (lambda: list(state["apps"] or [])), about, (lambda: list(state["titles"]))
    return box
