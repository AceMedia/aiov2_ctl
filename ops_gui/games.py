"""The Games tab: the games installed on this computer, to start from the app.

Optional, off until it is switched on in Settings > General. A game is a program with a menu entry (a .desktop
file) in the Games category, which is how the desktop's own menu knows them, so this lists what that menu
lists: packages, Flatpaks and anything a person added themselves. Nothing is downloaded and nothing is sent;
starting a game runs the command its menu entry gives, exactly as the menu would.

Everything above build() is plain Python and is tested without a screen."""
import os
import shlex

SETTING = "games_tab"
FIELD_CODES = "fFuUdDnNickvm"                  # %f, %U and the rest: places for a file or an address; none here
FLATPAK_MARKS = ("@@", "@@u")                  # Flatpak wraps those places in these; with no file they go too
ICON_SIZES = ("scalable", "512x512", "256x256", "128x128", "96x96", "64x64", "48x48", "32x32")
ICON_TYPES = (".png", ".svg")
NONE_FOUND = ("No games found. A game shows here when it is installed with an entry in the Games part of the "
              "desktop's menu.")
HINT = "A or Enter  play        X or R  look again"


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


def is_game(entry):
    """A program the menu shows under Games: not hidden, with a name and something to run."""
    if entry.get("Type", "Application") != "Application" or _yes(entry.get("NoDisplay")) or _yes(entry.get("Hidden")):
        return False
    if not entry.get("Name") or not entry.get("Exec"):
        return False
    return "Game" in [c.strip() for c in entry.get("Categories", "").split(";")]


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


def find_games(dirs, listdir, read, which):
    """Every game, by name. `listdir(folder)` gives file names or raises OSError, `read(path)` gives the text or
    None, `which(program)` gives its path or None. The first folder to have an entry of a given file name
    decides it, as on the desktop: a person's own copy can change or hide the system's."""
    seen, games = set(), []
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
            if not is_game(entry):
                continue
            argv = command(entry["Exec"])
            if not argv or which(entry.get("TryExec") or argv[0]) is None:
                continue                                # its program is not here any more
            games.append({"id": name[:-len(".desktop")], "name": entry["Name"], "about": entry.get("Comment", ""),
                          "argv": argv, "icon": entry.get("Icon", ""), "terminal": _yes(entry.get("Terminal")),
                          "folder": entry.get("Path", "")})
    return sorted(games, key=lambda g: (g["name"].casefold(), g["id"]))


def launch_argv(game, terminal):
    """What to run to start a game. One that draws in a terminal is given one; None when there is none."""
    if not game["terminal"]:
        return list(game["argv"])
    if not terminal:
        return None
    return [terminal, "-e"] + list(game["argv"])


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


def summary(count):
    return NONE_FOUND if not count else ("1 game" if count == 1 else f"{count} games")


def started_text(game, error=None, no_terminal=False):
    if no_terminal:
        return f"{game['name']} draws in a terminal, and no terminal program was found to give it."
    if error:
        return f"Could not start {game['name']}: {error}"
    return f"Started {game['name']}."


def build(win):
    """The Games page: the list, and a button to play the chosen one. Qt is imported here."""
    import shutil
    import subprocess
    from PyQt6.QtCore import QSize, Qt
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

    state = {"games": [], "dirs": []}

    def read(path):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return None

    class Page(QWidget):
        def showEvent(self, event):                     # a game installed since is there the next time
            super().showEvent(event)
            refresh()

    box = Page()
    outer = QVBoxLayout(box)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(6)
    count = QLabel("")
    count.setWordWrap(True)
    count.setStyleSheet("font-size: 12pt;")
    outer.addWidget(count)
    games = QListWidget()
    games.setIconSize(QSize(40, 40))
    games.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    games.setStyleSheet("QListWidget { font-size: 15pt; padding: 4px; } "
                        "QListWidget::item { min-height: 54px; padding-left: 8px; }")   # big enough for the trackball
    outer.addWidget(games, 1)
    about = QLabel("")
    about.setWordWrap(True)
    about.setStyleSheet("font-size: 11pt;")
    outer.addWidget(about)
    row = QHBoxLayout()
    play_b, again_b = QPushButton("Play"), QPushButton("Look again")
    for b in (play_b, again_b):
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.setMinimumHeight(44)
        b.setStyleSheet("font-size: 13pt; padding: 4px 18px;")
        row.addWidget(b)
    row.addStretch(1)
    hint = QLabel(HINT)
    hint.setStyleSheet("font-size: 10pt;")
    row.addWidget(hint)
    outer.addLayout(row)

    def icon_of(game):
        name = game["icon"]
        if name and not os.path.isabs(name) and QIcon.hasThemeIcon(name):
            return QIcon.fromTheme(name)
        path = icon_file(name, state["dirs"], os.path.isfile)
        return QIcon(path) if path else QIcon()

    def chosen():
        i = games.currentRow()
        return state["games"][i] if 0 <= i < len(state["games"]) else None

    def show_about(*_):
        game = chosen()
        about.setText(game["about"] if game else "")
        play_b.setEnabled(game is not None)

    def refresh():
        state["dirs"] = data_dirs(os.environ, os.path.expanduser("~"))
        found = find_games(state["dirs"], os.listdir, read, shutil.which)
        if found == state["games"] and games.count() == len(found):
            return
        keep = (chosen() or {}).get("id")
        state["games"] = found
        games.clear()
        for game in found:
            games.addItem(QListWidgetItem(icon_of(game), game["name"]))
        ids = [g["id"] for g in found]
        if found:
            games.setCurrentRow(ids.index(keep) if keep in ids else 0)
        count.setText(summary(len(found)))
        show_about()

    def play(*_):
        game = chosen()
        if game is None:
            return
        argv = launch_argv(game, shutil.which("x-terminal-emulator"))
        if argv is None:
            text = started_text(game, no_terminal=True)
        else:
            try:
                subprocess.Popen(argv, cwd=game["folder"] or os.path.expanduser("~"), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                text = started_text(game)
            except OSError as e:
                text = started_text(game, error=e.strerror or str(e))
        about.setText(text)
        activity = getattr(win, "activity", None)
        if activity is not None:
            activity.add("Games: " + text)

    games.currentRowChanged.connect(show_about)
    games.itemActivated.connect(play)
    play_b.clicked.connect(play)
    again_b.clicked.connect(lambda: refresh())
    box.main_action, box.refresh, box.list, box.play_button = play, refresh, games, play_b
    box.games, box.note = (lambda: list(state["games"])), about
    return box
