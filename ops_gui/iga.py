"""
IGA: a small web browser as an optional tab of the ops app, named after the ninja training region.

It is there only when the installer's 'IGA browser' feature is on (an empty file, enabled, in
~/.local/share/aiov2_ctl/iga, written by the installer as the user, and the package python3-pyqt6.qtwebengine).
Without that file the app does not add the tab and nothing here is used.

It costs nothing until it is used: Qt WebEngine is imported only the first time the tab is shown, by
load_engine(), and never when the app starts or when the tab is built. If that import fails the tab says so,
with the command that would fix it, and the rest of the app carries on.

It sends nothing by itself. It loads only what the person asks for: the start page is built here, from the
bookmarks, and never fetched. Text in the address bar that is not an address goes to a search engine only when
one has been chosen in Settings > IGA; by default it goes nowhere.

Everything that decides something is a plain function here and is tested without Qt. build(win) is thin.
"""
import html
import ipaddress
import json
import os
import re
from urllib.parse import quote_plus, urlsplit

TAB = "IGA"
START = "iga:start"                     # the address bar's name for the app's own start page
MOST_PAGES = 6
HISTORY_MOST = 500
PACKAGE = "python3-pyqt6.qtwebengine"
FIX_COMMAND = f"sudo apt install {PACKAGE}"
NOT_ADDRESS = "That is not an address. Choose a search engine in Settings > IGA to search from the address bar."
TOO_MANY = "Six pages is the most IGA keeps open, to save memory. Close one first."
SENDS = ("IGA loads only the pages you ask for, and sends nothing of its own: no usage reports, no start page "
         "from the internet, no spelling or translation service. What you type in the address bar goes to a "
         "search engine only if you choose one here. The sites you visit can still do what web sites do.")
DUCKDUCKGO = "https://duckduckgo.com/?q=%s"
SEARCH_CHOICES = (("none", "No search (addresses only)"), ("duckduckgo", "DuckDuckGo"), ("custom", "Custom"))
# Keys in the app's settings file (data.load_settings) and what each is when it has never been set.
DEFAULTS = {"iga_search": "none", "iga_search_custom": "", "iga_history": False, "iga_clear_on_close": False}
ZOOMS = (0.5, 0.67, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0)


# ---- switched on or not
def user_folder(home=None):
    """Where the installer keeps this user's IGA switch; bookmarks, history and the browser profile go here too."""
    return os.path.join(home or os.path.expanduser("~"), ".local", "share", "aiov2_ctl", "iga")


def profile_folder(home=None):
    return os.path.join(user_folder(home), "profile")


def enabled(user=None, isfile=os.path.isfile):
    """True when the installer's IGA browser feature is on for this user: its switch file is there."""
    return isfile(os.path.join(user or user_folder(), "enabled"))


def wanted(user=None, isfile=os.path.isfile):
    """Whether the app should add the tab at all. Only when the feature is installed; never otherwise."""
    return enabled(user, isfile)


def engine_missing_text(error):
    """What the tab says when Qt WebEngine cannot be loaded."""
    why = str(error).strip() or type(error).__name__
    return (f"IGA cannot start: Qt WebEngine is not installed or does not work here ({why}).\n\n"
            f"Install it with:\n{FIX_COMMAND}\n\nthen restart the ops app. The rest of the app is not affected.")


# ---- settings
def settings_from(saved):
    """The IGA settings from the app's settings, each one checked, with the defaults for anything missing or odd."""
    saved = saved if isinstance(saved, dict) else {}
    out = dict(DEFAULTS)
    if saved.get("iga_search") in dict(SEARCH_CHOICES):
        out["iga_search"] = saved["iga_search"]
    if isinstance(saved.get("iga_search_custom"), str):
        out["iga_search_custom"] = saved["iga_search_custom"].strip()
    for key in ("iga_history", "iga_clear_on_close"):
        out[key] = saved.get(key) is True
    return out


def good_template(template):
    """True for a custom search address: http or https, a host, and %s where the words go."""
    if not isinstance(template, str) or "%s" not in template or any(c.isspace() for c in template):
        return False
    parts = urlsplit(template.replace("%s", "x"))
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def search_template(choice, custom=""):
    """The search address for a choice, with %s for the words, or None when there is no search."""
    if choice == "duckduckgo":
        return DUCKDUCKGO
    if choice == "custom" and good_template(custom):
        return custom
    return None


# ---- the address bar
_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):")
_HOST = re.compile(r"^(?P<host>[^/\s:?#\[\]@]+)(?::(?P<port>\d{1,5}))?(?P<rest>[/?#]\S*)?$")
_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def _ip(host):
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _is_hostname(host):
    labels = host.split(".")
    return len(labels) >= 2 and all(_LABEL.match(l) for l in labels) and labels[-1].isalpha()


def private_host(host):
    """True for localhost, a .local name and addresses on the local network, which are opened with http://."""
    host = host.lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return True
    ip = _ip(host)
    return bool(ip and (ip.is_private or ip.is_loopback or ip.is_link_local))


def to_url(text, search=None):
    """What the address bar's text means: ('url', address), ('search', address), ('refuse', reason), or None for
    an empty box. `search` is the search address with %s, or None when no search engine is chosen."""
    text = (text or "").strip()
    if not text:
        return None
    lower = text.lower()
    if lower in ("about:blank", START):
        return "url", lower
    if lower.startswith(("http://", "https://")):
        if not urlsplit(text).netloc:
            return "refuse", "That address has no site name after the //."
        return "url", text.replace(" ", "%20")
    m = _HOST.match(text)
    if m and not any(c.isspace() for c in text):
        host, port = m.group("host"), m.group("port")
        if port is not None and not 0 < int(port) < 65536:
            return "refuse", "That port number is out of range."
        if host.lower() == "localhost" or _ip(host) is not None or _is_hostname(host):
            scheme = "http" if private_host(host) else "https"
            return "url", f"{scheme}://{text}"
    s = _SCHEME.match(text)
    if s and not any(c.isspace() for c in text):
        scheme = s.group(1).lower()
        if scheme == "file":
            return "refuse", "IGA does not open files on this device from the address bar."
        return "refuse", f"IGA does not open '{scheme}:' addresses from the address bar, only web addresses."
    if search:
        return "search", search.replace("%s", quote_plus(text))
    return "refuse", NOT_ADDRESS


def shown_address(url):
    """What the address bar shows for a page: the start page by its own name, everything else as it is."""
    if not url or url in ("about:blank",):
        return url or ""
    if url.startswith("data:") or url.startswith("iga-internal:"):
        return START
    return url


def security_mark(url):
    """The small words beside the address bar."""
    url = url or ""
    if url == START or url.startswith("iga-internal:") or url.startswith("data:"):
        return "this app's own page"
    if url.startswith("https://"):
        return "secure connection"
    if url.startswith("http://"):
        return "not secure"
    return ""


def page_label(title, url, most=18):
    """What a page's tab in the strip shows: its title, else its site, else 'New page', cut to `most`."""
    url = url or ""
    if security_mark(url) == "this app's own page":
        words = "Start"
    else:
        words = " ".join((title or "").split())
        if not words or words == url:
            host = (urlsplit(url).hostname or "") if _web(url) else ""
            words = host[4:] if host.startswith("www.") else host
        words = words or "New page"
    return words if len(words) <= most else words[:most - 1].rstrip() + "…"


def escape_step(finding, loading, where):
    """What one Escape does on the IGA tab. `where` is 'page' (a web page has the keyboard), 'address' (the address
    bar or any other IGA control has it) or anything else (the row of tabs, say). Returns 'find' (close the find
    bar), 'stop' (stop loading), 'address' (out of the page to the address bar), 'tabs' (to the app's row of tabs,
    IGA still selected), or None: Escape is then the app's own next step (focusnav.back_step), as on any tab."""
    if finding:
        return "find"
    if where == "page":
        return "stop" if loading else "address"
    if where == "address":
        return "tabs"
    return None


def tab_key_owner(iga_showing, where, key, ctrl, shift):
    """Who a Tab key belongs to while the app runs. `where` is 'app_tabs' (the app's row of tabs has the keyboard),
    'page' (a web page has it) or anything else. Returns 'next' or 'previous' (IGA's pages: Ctrl+Tab and
    Ctrl+Shift+Tab whenever the IGA tab is showing, except from the app's row of tabs, where the app's own
    switching stays), 'page' (a plain Tab inside a web page goes to the page, for its form fields), or None (the
    app's usual handling)."""
    if key not in ("Tab", "Backtab") or not iga_showing:
        return None
    backwards = key == "Backtab" or shift
    if ctrl:
        return None if where == "app_tabs" else ("previous" if backwards else "next")
    return "page" if where == "page" else None


def page_keeps(escape, function_key, ctrl, alt, meta):
    """Whether a key pressed while a web page has the keyboard belongs to the page rather than to one of the
    app's own shortcuts: letters, numbers and Tab do; Escape, F-keys and Ctrl or Alt combinations do not."""
    return not (escape or function_key or ctrl or alt or meta)


# IGA's own keys. Every one has Ctrl or Alt, so none clashes with the main window's shortcuts (single letters,
# Shift+letter, the numbers, F1 to F6, F11, Escape and Tab).
SHORTCUTS = (("Ctrl+L", "address"), ("Ctrl+T", "new"), ("Ctrl+W", "close"), ("Ctrl+Tab", "next"),
             ("Ctrl+Shift+Tab", "previous"), ("Ctrl+R", "reload"), ("Ctrl+F", "find"), ("Ctrl++", "zoom_in"),
             ("Ctrl+=", "zoom_in"), ("Ctrl+-", "zoom_out"), ("Ctrl+0", "zoom_reset"), ("Alt+Left", "back"),
             ("Alt+Right", "forward"))


def key_action(key, ctrl=False, shift=False, alt=False):
    """Which IGA action a key press is, or None. `key` is the key's name: a letter, a digit, '+', '=', '-',
    'Tab', 'Backtab', 'Left' or 'Right'. Used for a key that reaches the IGA tab without its shortcut firing."""
    if alt and not ctrl:
        return {"Left": "back", "Right": "forward"}.get(key)
    if not ctrl or alt:
        return None
    if key == "Backtab" or (key == "Tab" and shift):
        return "previous"
    if key == "Tab":
        return "next"
    return {"L": "address", "T": "new", "W": "close", "R": "reload", "F": "find", "+": "zoom_in", "=": "zoom_in",
            "-": "zoom_out", "0": "zoom_reset"}.get(key.upper() if len(key) == 1 else key)


def zoom_step(current, direction):
    """The next zoom level up (+1) or down (-1), or 1.0 for 0."""
    if direction == 0:
        return 1.0
    if direction > 0:
        return next((z for z in ZOOMS if z > current + 1e-6), ZOOMS[-1])
    return next((z for z in reversed(ZOOMS) if z < current - 1e-6), ZOOMS[0])


# ---- the open pages
class Pages:
    """The open pages, by number, in the order of the strip, and which is current. At most MOST_PAGES; closing the
    last one leaves one blank page. The tab starts with none (open_first=False) and opens its first page itself,
    so that every page in the model has a view and a place in the strip."""

    def __init__(self, open_first=True):
        self.order, self.current, self._next = [], None, 1
        if open_first:
            self.open()

    def open(self):
        """Open a page after the current one and make it current. Returns (its number, '') or (None, why not)."""
        if len(self.order) >= MOST_PAGES:
            return None, TOO_MANY
        pid, self._next = self._next, self._next + 1
        at = self.order.index(self.current) + 1 if self.current in self.order else len(self.order)
        self.order.insert(at, pid)
        self.current = pid
        return pid, ""

    def close(self, pid):
        """Close a page. Returns the number of a page opened in its place when it was the last one, else None."""
        if pid not in self.order:
            return None
        at = self.order.index(pid)
        self.order.remove(pid)
        if not self.order:
            self.current = None
            return self.open()[0]
        if self.current == pid:
            self.current = self.order[min(at, len(self.order) - 1)]
        return None

    def move(self, pid, to):
        if pid in self.order:
            self.order.remove(pid)
            self.order.insert(max(0, min(to, len(self.order))), pid)

    def select(self, pid):
        if pid in self.order:
            self.current = pid

    def step(self, d):
        """The page d along from the current one, going round; it becomes current."""
        at = self.order.index(self.current)
        self.current = self.order[(at + d) % len(self.order)]
        return self.current

    def index(self, pid):
        return self.order.index(pid)


# ---- files kept on the device
def _private_folder(folder):
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)


def _write_private(path, value):
    """Write JSON so that only the user can read it: folder 0700, file 0600, replaced in one step."""
    _private_folder(os.path.dirname(path))
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(value, f, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _read_list(path):
    """(the list in a JSON file, True) or ([], False) when it is there but cannot be read as one; ([], True) when
    there is no file yet."""
    try:
        with open(path) as f:
            value = json.load(f)
    except FileNotFoundError:
        return [], True
    except (OSError, ValueError):
        return [], False
    return (value, True) if isinstance(value, list) else ([], False)


def _web(url):
    return isinstance(url, str) and url.startswith(("http://", "https://"))


# ---- bookmarks
def bookmarks_path(folder):
    return os.path.join(folder, "bookmarks.json")


def load_bookmarks(folder):
    """[{'title', 'url'}] in the order they were added. A file that cannot be read counts as empty."""
    items, _ = _read_list(bookmarks_path(folder))
    return [{"title": str(b.get("title") or b["url"]), "url": b["url"]}
            for b in items if isinstance(b, dict) and _web(b.get("url"))]


def add_bookmark(folder, title, url):
    """Add a page (or rename it, when it is already there). Returns False for an address that cannot be kept.
    A corrupt file is replaced only here, when the person adds one."""
    if not _web(url):
        return False
    items = load_bookmarks(folder)
    title = " ".join((title or "").split()) or url
    for b in items:
        if b["url"] == url:
            b["title"] = title
            break
    else:
        items.append({"title": title, "url": url})
    _write_private(bookmarks_path(folder), items)
    return True


def remove_bookmark(folder, url):
    items, ok = _read_list(bookmarks_path(folder))
    if not ok:
        return False
    kept = [b for b in load_bookmarks(folder) if b["url"] != url]
    if len(kept) == len(load_bookmarks(folder)):
        return False
    _write_private(bookmarks_path(folder), kept)
    return True


def rename_bookmark(folder, url, title):
    items, ok = _read_list(bookmarks_path(folder))
    title = " ".join((title or "").split())
    if not ok or not title:
        return False
    marks = load_bookmarks(folder)
    if not any(b["url"] == url for b in marks):
        return False
    for b in marks:
        if b["url"] == url:
            b["title"] = title
    _write_private(bookmarks_path(folder), marks)
    return True


# ---- history: only when 'Remember visited pages' is on
def history_path(folder):
    return os.path.join(folder, "history.json")


def load_history(folder):
    items, _ = _read_list(history_path(folder))
    return [h for h in items if isinstance(h, dict) and _web(h.get("url"))]


def add_visit(folder, url, title, when, remember, most=HISTORY_MOST):
    """Note a visited page, newest first, at most `most`. Does nothing unless `remember` is on."""
    if not remember or not _web(url):
        return False
    items = [h for h in load_history(folder) if h["url"] != url]
    items.insert(0, {"url": url, "title": " ".join((title or "").split()), "time": int(when)})
    _write_private(history_path(folder), items[:most])
    return True


def clear_history(folder):
    try:
        os.remove(history_path(folder))
    except FileNotFoundError:
        pass


# ---- downloads
def downloads_folder(home=None):
    return os.path.join(home or os.path.expanduser("~"), "Downloads")


def safe_name(suggested, folder, exists=os.path.exists):
    """A file name for a download: no folders, no leading dot, nothing unprintable, never empty, and a counter
    when a file of that name is already there."""
    name = (suggested or "").replace("\\", "/").split("/")[-1]
    name = "".join(c for c in name if c.isprintable() and c not in '<>:"|?*').strip().lstrip(".").strip()
    if not name or name in (".", ".."):
        name = "download"
    name = name[:200]
    stem, dot, ext = name.rpartition(".")
    if not stem:
        stem, dot, ext = name, "", ""
    candidate, n = name, 2
    while exists(os.path.join(folder, candidate)):
        candidate = f"{stem} ({n}){dot}{ext}"
        n += 1
    return candidate


def download_line(name, received, total, state):
    """One line of the Downloads list."""
    def mb(n):
        return f"{n / 1e6:.1f} MB"
    if state == "done":
        return f"{name}: saved ({mb(received)})"
    if state == "failed":
        return f"{name}: failed"
    if state == "cancelled":
        return f"{name}: cancelled"
    return f"{name}: {mb(received)} of {mb(total)}" if total and total > 0 else f"{name}: {mb(received)}"


# ---- the app's own pages: the start page and notices. Their colours are the app's.
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_COLOUR_KEYS = ("bg", "card", "text", "muted", "border", "accent")
_FALLBACK = {"bg": "#0f1114", "card": "#1f2329", "text": "#e6e8eb", "muted": "#8b93a1", "border": "#2f343c",
             "accent": "#d70a53"}


def page_colours(tokens):
    """The colours for the app's own pages, from the app's tokens for the current theme (style.TOKENS[mode],
    which already carries the contrast setting and the feature colour). Anything not a plain #rrggbb falls back."""
    tokens = tokens if isinstance(tokens, dict) else {}
    return {k: tokens[k] if isinstance(tokens.get(k), str) and _HEX.match(tokens[k]) else _FALLBACK[k]
            for k in _COLOUR_KEYS}


def _page(title, body, colours):
    c = page_colours(colours)
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'\">"
            f"<title>{html.escape(title)}</title><style>"
            f"body{{background:{c['bg']};color:{c['text']};font-family:'Spleen 8x16',monospace;font-size:16px;"
            f"margin:24px;}}h1{{font-size:20px;color:{c['text']};border-bottom:2px solid {c['accent']};"
            f"padding-bottom:6px;}}a{{color:{c['text']};text-decoration:none;}}a:focus,a:hover{{color:{c['accent']};"
            f"}}li{{background:{c['card']};border:1px solid {c['border']};margin:4px 0;padding:6px 10px;"
            f"list-style:none;}}ul{{padding:0;}}.muted{{color:{c['muted']};}}.addr{{color:{c['muted']};"
            f"font-size:13px;}}</style></head><body>{body}</body></html>")


def start_page(bookmarks, colours):
    """The start page: the bookmarks as links and a line about IGA. Every word in it is escaped."""
    rows = []
    for b in bookmarks or ():
        url = b.get("url") if isinstance(b, dict) else None
        if not _web(url):
            continue
        title = b.get("title") or url
        rows.append(f"<li><a href=\"{html.escape(url, quote=True)}\">{html.escape(title)}</a><br>"
                    f"<span class='addr'>{html.escape(url)}</span></li>")
    marks = f"<ul>{''.join(rows)}</ul>" if rows else \
        "<p class='muted'>No bookmarks yet. Open a page and press Bookmark to keep it here.</p>"
    body = (f"<h1>IGA</h1>{marks}<p class='muted'>IGA is part of the uConsole Ops app. It loads only what you ask "
            f"for. Ctrl+L goes to the address bar. Escape from a page goes to the address bar, and from the address bar to "
            f"the app's row of tabs.</p>")
    return _page("Start", body, colours)


def notice_page(title, text, colours, address=""):
    """A plain page of the app's own: an address that was refused, or a page that could not be loaded."""
    body = f"<h1>{html.escape(title)}</h1><p>{html.escape(text)}</p>"
    if address:
        body += f"<p class='addr'>{html.escape(address)}</p>"
    return _page(title, body, colours)


def load_error_text(code_name):
    """Plain words for why a page did not load."""
    return {"dns": "The site's name could not be found. Check the address, or whether this device is online.",
            "refused": "The site refused the connection.",
            "timeout": "The site took too long to answer.",
            "certificate": "The site's certificate could not be trusted, so IGA did not open it.",
            }.get(code_name, "The page could not be loaded. Check the address, or whether this device is online.")



# ---- the engine: loaded only when the tab is first shown
# Settings of QWebEngineSettings.WebAttribute set on IGA's profile: nothing is fetched ahead of the person, no
# plugins, local pages cannot reach the network or files, WebRTC does not give away local addresses. Each is set
# only if this Qt has it, so an older or newer Qt does not stop the tab.
ENGINE_SETTINGS = (
    ("DnsPrefetchEnabled", False),
    ("PluginsEnabled", False),
    ("LocalContentCanAccessRemoteUrls", False),
    ("LocalContentCanAccessFileUrls", False),
    ("ScreenCaptureEnabled", False),
    ("WebRTCPublicInterfacesOnly", True),
    ("PlaybackRequiresUserGesture", True),
    ("FullScreenSupportEnabled", False),
    ("ErrorPageEnabled", False),            # IGA shows its own page, in the app's colours, when a page fails
    ("JavascriptCanAccessClipboard", False),
)
QUIET_FLAG = "--disable-background-networking"


def chromium_flags(existing):
    """QTWEBENGINE_CHROMIUM_FLAGS with Chromium's background networking switched off. Whatever was there already is
    kept as it is; nothing that weakens the sandbox or web security is ever added."""
    words = (existing or "").split()
    if QUIET_FLAG not in words:
        words.append(QUIET_FLAG)
    return " ".join(words)


def error_kind(code):
    """A Chromium network error number in IGA's words: 'dns', 'refused', 'timeout', 'certificate', 'other', or None
    for a load the person stopped or left (which needs no page)."""
    if code in (None, 0, -3):                  # -3: aborted, by Stop or by going somewhere else
        return None
    if code in (-105, -137):
        return "dns"
    if code in (-102, -104):
        return "refused"
    if code in (-7, -118):
        return "timeout"
    if -299 <= code <= -200:
        return "certificate"
    return "other"


def prepare_app():
    """Called before the QApplication is made, only when the feature is on. Qt WebEngine can only be loaded after
    the application exists when OpenGL contexts are shared from the start; this sets that and loads nothing."""
    from PyQt6.QtCore import QCoreApplication, Qt
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)


def load_engine():
    """Import Qt WebEngine: the one place IGA does it. Tests replace this. Returns (QtWebEngineWidgets,
    QtWebEngineCore) or raises."""
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = chromium_flags(os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS"))
    from PyQt6 import QtWebEngineCore, QtWebEngineWidgets
    return QtWebEngineWidgets, QtWebEngineCore


def build(win):
    """The IGA tab. It holds a light placeholder until it is first shown; only then is Qt WebEngine loaded. Every
    control is an ordinary widget of the app, so it takes the app's style sheet, theme, contrast, feature colour
    and fonts; only the web page itself is styled by the site."""
    import time
    from PyQt6.QtCore import Qt, QTimer, QUrl
    from PyQt6.QtGui import QKeySequence, QShortcut
    from PyQt6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu,
                                 QMessageBox, QProgressBar, QPushButton, QStackedWidget, QVBoxLayout, QWidget)
    from . import data, style
    from .foldertabs import FolderTabBar

    folder = user_folder()

    def colours():
        return page_colours(style.TOKENS["dark" if getattr(win, "theme", "dark") == "dark" else "light"])

    def prefs():
        return settings_from(getattr(win, "settings", None) or data.load_settings())

    def button(text, slot, tip=""):
        b = QPushButton(text)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)        # as on the other tabs: the D-pad highlight reaches it
        b.setToolTip(tip)
        b.clicked.connect(lambda *_: slot())
        return b

    def tell(text):
        QMessageBox.information(page, "IGA", text)

    class Page(QWidget):
        def keyPressEvent(self, event):
            """A key with Ctrl or Alt that no child used comes up to here: if it is one of IGA's, do it."""
            k, mods = int(event.key()), event.modifiers()
            names = {int(Qt.Key.Key_Tab): "Tab", int(Qt.Key.Key_Backtab): "Backtab", int(Qt.Key.Key_Left): "Left",
                     int(Qt.Key.Key_Right): "Right", int(Qt.Key.Key_Plus): "+", int(Qt.Key.Key_Equal): "=",
                     int(Qt.Key.Key_Minus): "-"}
            name = names.get(k) or (chr(k) if int(Qt.Key.Key_A) <= k <= int(Qt.Key.Key_Z) or
                                    int(Qt.Key.Key_0) <= k <= int(Qt.Key.Key_9) else "")
            what = key_action(name, bool(mods & Qt.KeyboardModifier.ControlModifier),
                              bool(mods & Qt.KeyboardModifier.ShiftModifier),
                              bool(mods & Qt.KeyboardModifier.AltModifier))
            if what and what in st["actions"]:
                st["actions"][what]()
                event.accept()
                return
            super().keyPressEvent(event)

        def showEvent(self, event):
            super().showEvent(event)
            if not st["started"]:
                st["started"] = True
                QTimer.singleShot(0, start)

    page = Page()
    outer = QVBoxLayout(page)
    outer.setContentsMargins(6, 6, 6, 6)
    outer.setSpacing(4)
    waiting = QLabel("IGA starts when this tab is first opened.")
    waiting.setWordWrap(True)
    waiting.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    waiting.setStyleSheet("font-size: 13pt;")
    outer.addWidget(waiting, 1, Qt.AlignmentFlag.AlignTop)
    st = {"started": False, "ready": False, "views": {}, "info": {}, "downloads": [], "finding": False,
          "actions": {}}
    page.key_act = lambda what: st["actions"].get(what, lambda: None)()
    page.escape_plan = lambda: None            # until the engine is there, Escape is the app's as on any tab
    page.escape_do = lambda _step: None
    page.recolour = lambda: None
    page.stop = lambda: None

    def start():
        try:
            widgets, core = load_engine()
        except Exception as exc:                   # missing or broken: say so and leave the rest of the app be
            waiting.setText(engine_missing_text(exc))
            return
        try:
            assemble(widgets, core)
        except Exception as exc:
            waiting.setText(f"IGA could not start: {exc}")
            return
        waiting.hide()

    def assemble(widgets, core):
        QWebEngineView = widgets.QWebEngineView
        QWebEngineProfile, QWebEnginePage = core.QWebEngineProfile, core.QWebEnginePage
        QWebEngineSettings = core.QWebEngineSettings

        _private_folder(folder)
        _private_folder(profile_folder())
        profile = QWebEngineProfile("aiov2-iga", page)          # one profile with a storage name of its own
        profile.setPersistentStoragePath(profile_folder())
        profile.setCachePath(os.path.join(profile_folder(), "cache"))
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        try:
            profile.setSpellCheckEnabled(False)
        except Exception:
            pass
        try:
            profile.setDownloadPath(downloads_folder())
        except Exception:
            pass
        es = profile.settings()
        for name, value in ENGINE_SETTINGS:
            attr = getattr(QWebEngineSettings.WebAttribute, name, None)
            if attr is not None:
                es.setAttribute(attr, value)
        st["profile"] = profile
        pages = Pages(open_first=False)         # open_page() below makes the first, with its view and strip entry
        st["pages"] = pages

        class WebPage(QWebEnginePage):
            def createWindow(self, _kind):                       # a link that wants a new window: a new IGA page
                pid = open_page(blank=True)
                return st["views"][pid].page() if pid is not None else None

        # ---- the row of controls
        row = QHBoxLayout()
        row.setSpacing(4)
        back_b = button("Back", lambda: act("back"), "Back (Alt+Left)")
        fwd_b = button("Forward", lambda: act("forward"), "Forward (Alt+Right)")
        reload_b = button("Reload", lambda: act("reload_or_stop"), "Reload (Ctrl+R), or Stop while loading")
        address = QLineEdit()
        address.setPlaceholderText("Address (Ctrl+L)")
        address.setStyleSheet("font-size: 13pt; padding: 6px;")          # as the mesh message box
        mark = QLabel("")
        mark.setStyleSheet("font-size: 10pt; color: #9e9e9e;")           # the app's helper-text grey
        mark_b = button("Bookmark", lambda: bookmark_this(), "Keep this page on the start page")
        menu_b = QPushButton("Menu")
        menu_b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        menu = QMenu(menu_b)
        menu_b.setMenu(menu)
        for w in (back_b, fwd_b, reload_b):
            row.addWidget(w)
        row.addWidget(address, 1)
        row.addWidget(mark)
        row.addWidget(mark_b)
        row.addWidget(menu_b)
        outer.addLayout(row)

        strip = FolderTabBar()
        strip.setObjectName("subtabs")
        strip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        strip.setMovable(True)
        strip.setExpanding(False)
        holder = QWidget()
        holder.setObjectName("subtabs")                  # so the strip takes the sub-tab look from the style sheet
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(strip)
        outer.addWidget(holder)

        progress = QProgressBar()
        progress.setTextVisible(False)
        progress.setMaximumHeight(6)
        progress.setRange(0, 100)
        progress.hide()
        outer.addWidget(progress)

        stack = QStackedWidget()
        outer.addWidget(stack, 1)

        findrow = QWidget()
        fr = QHBoxLayout(findrow)
        fr.setContentsMargins(0, 0, 0, 0)
        find_box = QLineEdit()
        find_box.setPlaceholderText("Find in page")
        find_box.setStyleSheet("font-size: 13pt; padding: 6px;")
        fr.addWidget(find_box, 1)
        fr.addWidget(button("Next", lambda: find(True), "Next match (Enter)"))
        fr.addWidget(button("Previous", lambda: find(False), "Previous match"))
        fr.addWidget(button("Close", lambda: close_find(), "Close (Escape)"))
        findrow.hide()
        outer.addWidget(findrow)

        def current():
            return st["views"].get(pages.current)

        def info(pid=None):
            return st["info"].setdefault(pages.current if pid is None else pid, {})

        def shown_url(pid):
            i = st["info"].get(pid, {})
            if i.get("own"):
                return i.get("shown", START)
            view = st["views"].get(pid)
            return view.url().toString() if view is not None else ""

        def refresh_row():
            view = current()
            if view is None:
                return
            url = shown_url(pages.current)
            if not address.hasFocus():
                address.setText("" if url == "about:blank" else url)
            mark.setText(security_mark(START if info().get("own") == "start" else url))
            if info().get("own") == "notice":
                mark.setText(security_mark(START))
            h = view.history()
            back_b.setEnabled(h.canGoBack())
            fwd_b.setEnabled(h.canGoForward())
            reload_b.setText("Stop" if info().get("loading") else "Reload")
            mark_b.setEnabled(url.startswith(("http://", "https://")) and not info().get("own"))

        def relabel(pid):
            view = st["views"].get(pid)
            if view is None or pid not in pages.order or pages.index(pid) >= strip.count():
                return
            i = st["info"].get(pid, {})
            title = "" if i.get("own") else view.title()
            text = page_label(title, START if i.get("own") == "start" else shown_url(pid))
            at = pages.index(pid)
            if strip.tabText(at) != text:
                strip.setTabText(at, text)
            strip.setTabToolTip(at, shown_url(pid))

        def show_own(pid, kind, title="", text="", shown=START):
            view = st["views"][pid]
            st["info"][pid] = dict(st["info"].get(pid, {}), own=kind, shown=shown, title=title, text=text)
            if kind == "start":
                body = start_page(load_bookmarks(folder), colours())
            else:
                body = notice_page(title, text, colours(), address=shown if shown != START else "")
            view.setHtml(body, QUrl("about:blank"))
            relabel(pid)
            refresh_row()

        def make_view(pid):
            view = QWebEngineView()
            view.setProperty("typing_zone", True)        # the app's letter shortcuts leave a web page alone
            wp = WebPage(profile, view)
            view.setPage(wp)
            deny_permissions(wp, QWebEnginePage)
            view.titleChanged.connect(lambda *_: (relabel(pid), refresh_row() if pid == pages.current else None))
            view.urlChanged.connect(lambda u: url_changed(pid, u))
            view.loadStarted.connect(lambda: loading(pid, True))
            view.loadProgress.connect(lambda n: progress.setValue(n) if pid == pages.current else None)
            view.loadFinished.connect(lambda ok: finished(pid, ok))
            if hasattr(wp, "loadingChanged"):
                wp.loadingChanged.connect(lambda li: failed(pid, li))
            st["views"][pid] = view
            st["info"][pid] = {}
            stack.addWidget(view)
            return view

        def url_changed(pid, url):
            i = st["info"].setdefault(pid, {})
            if url.scheme() in ("http", "https"):
                i["was_own"], i["own"] = i.get("own") or i.get("was_own"), None
            elif not i.get("own") and i.get("was_own"):        # Back to one of the app's own pages
                i["own"] = i["was_own"]
            relabel(pid)
            if pid == pages.current:
                refresh_row()

        def loading(pid, on):
            st["info"].setdefault(pid, {})["loading"] = on
            if pid == pages.current:
                progress.setVisible(on)
                if on:
                    progress.setValue(0)
                refresh_row()

        def finished(pid, ok):
            loading(pid, False)
            view = st["views"].get(pid)
            if ok and view is not None and not st["info"][pid].get("own"):
                add_visit(folder, view.url().toString(), view.title(), time.time(), prefs()["iga_history"])

        def failed(pid, li):
            try:
                if li.status() != li.LoadStatus.LoadFailedStatus:
                    return
                kind = error_kind(li.errorCode())
                url = li.url().toString()
            except Exception:
                return
            if kind is not None:
                show_own(pid, "notice", "Not loaded", load_error_text(kind), shown=url)

        def open_page(blank=False, url=None):
            pid, why = pages.open()
            if pid is None:
                tell(why)
                return None
            make_view(pid)
            strip.blockSignals(True)
            strip.insertTab(pages.index(pid), "New page")
            strip.blockSignals(False)
            choose(pid)
            if url:
                go(url)
            elif not blank:
                show_own(pid, "start")
            return pid

        def choose(pid):
            pages.select(pid)
            view = st["views"][pid]
            stack.setCurrentWidget(view)
            strip.blockSignals(True)
            strip.setCurrentIndex(pages.index(pid))
            strip.blockSignals(False)
            progress.setVisible(bool(info().get("loading")))
            refresh_row()

        def close_page(pid=None):
            pid = pages.current if pid is None else pid
            at = pages.index(pid)
            fresh = pages.close(pid)
            view = st["views"].pop(pid)
            st["info"].pop(pid, None)
            strip.blockSignals(True)
            strip.removeTab(at)
            strip.blockSignals(False)
            stack.removeWidget(view)
            view.page().deleteLater()            # the page goes first: Qt WebEngine warns if a profile outlives it
            view.deleteLater()
            if fresh is not None:
                make_view(fresh)
                strip.insertTab(0, "New page")
                show_own(fresh, "start")
            choose(pages.current)

        def go(text=None):
            text = address.text() if text is None else text
            p = prefs()
            meaning = to_url(text, search_template(p["iga_search"], p["iga_search_custom"]))
            if meaning is None:
                return
            kind, where = meaning
            view = current()
            if kind == "refuse":
                show_own(pages.current, "notice", "Not opened", where, shown=text.strip())
            elif where == START:
                show_own(pages.current, "start")
            else:
                info()["own"] = None
                view.setUrl(QUrl(where))
            view.setFocus()

        def act(what):
            view = current()
            if view is None:
                return
            if what == "back":
                view.back()
            elif what == "forward":
                view.forward()
            elif what == "reload_or_stop":
                if info().get("loading"):
                    view.stop()
                elif info().get("own") == "start":
                    show_own(pages.current, "start")
                else:
                    view.reload()
            elif what == "reload":
                view.reload() if not info().get("own") else None
            elif what == "stop":
                view.stop()

        def zoom(direction):
            view = current()
            if view is not None:
                view.setZoomFactor(zoom_step(view.zoomFactor(), direction))

        def find(forward=True):
            view = current()
            text = find_box.text()
            if view is None or not text:
                return
            if forward:
                view.findText(text)
            else:
                view.findText(text, QWebEnginePage.FindFlag.FindBackward)

        def open_find():
            st["finding"] = True
            findrow.show()
            find_box.setFocus()
            find_box.selectAll()

        def close_find():
            st["finding"] = False
            view = current()
            if view is not None:
                view.findText("")
                view.setFocus()
            findrow.hide()

        def bookmark_this():
            view = current()
            if view is None or info().get("own"):
                return
            url = view.url().toString()
            title, ok = QInputDialog.getText(page, "Bookmark", "Name for this page:", QLineEdit.EchoMode.Normal,
                                             view.title() or url)
            if ok and not add_bookmark(folder, title, url):
                tell("Only web addresses (http and https) can be kept as bookmarks.")
            refresh_start_pages()

        def manage_bookmarks():
            marks = load_bookmarks(folder)
            if not marks:
                tell("No bookmarks yet. Open a page and press Bookmark to keep it.")
                return
            names = [f"{b['title']}  ({b['url']})" for b in marks]
            pick, ok = QInputDialog.getItem(page, "Bookmarks", "Choose a bookmark:", names, 0, False)
            if not ok:
                return
            chosen = marks[names.index(pick)]
            what, ok = QInputDialog.getItem(page, "Bookmarks", chosen["title"], ["Open", "Rename", "Remove"], 0,
                                            False)
            if not ok:
                return
            if what == "Open":
                go(chosen["url"])
            elif what == "Rename":
                title, ok = QInputDialog.getText(page, "Rename bookmark", "New name:", QLineEdit.EchoMode.Normal,
                                                 chosen["title"])
                if ok:
                    rename_bookmark(folder, chosen["url"], title)
            elif win.ask(f"Remove the bookmark '{chosen['title']}'?"):
                remove_bookmark(folder, chosen["url"])
            refresh_start_pages()

        def refresh_start_pages():
            for pid in list(pages.order):
                own = st["info"].get(pid, {}).get("own")
                if own:
                    i = st["info"][pid]
                    show_own(pid, own, i.get("title", ""), i.get("text", ""), i.get("shown", START))
            choose(pages.current)

        def downloads_list():
            lines = [download_line(d["name"], d["got"], d["total"], d["state"]) for d in st["downloads"]]
            tell("\n".join(lines) if lines else "Nothing downloaded since the app started.")

        def clear_now(ask=True):
            if ask and not win.ask("Clear IGA's cookies, site storage, cache and history now? You will be signed out of sites. Bookmarks are kept."):
                return
            clear_data()
            if ask:
                tell("Cookies, cache and history are cleared.")

        def clear_data():
            try:
                profile.clearHttpCache()
                profile.cookieStore().deleteAllCookies()
                profile.clearAllVisitedLinks()
            except Exception:
                pass
            clear_history(folder)

        def download(req):
            try:
                suggested = req.downloadFileName()
            except Exception:
                suggested = ""
            start_in = downloads_folder()
            name = safe_name(suggested, start_in)
            path, _ = QFileDialog.getSaveFileName(page, "Save download", os.path.join(start_in, name))
            if not path:
                req.cancel()
                return
            req.setDownloadDirectory(os.path.dirname(path))
            req.setDownloadFileName(os.path.basename(path))
            entry = {"name": os.path.basename(path), "got": 0, "total": 0, "state": "going"}
            st["downloads"].append(entry)

            def update(*_):
                try:
                    entry["got"], entry["total"] = req.receivedBytes(), req.totalBytes()
                    s = req.state()
                    D = type(s)
                    entry["state"] = {D.DownloadCompleted: "done", D.DownloadCancelled: "cancelled",
                                      D.DownloadInterrupted: "failed"}.get(s, "going")
                except Exception:
                    pass
                if entry["state"] in ("done", "failed"):
                    win.say(download_line(entry["name"], entry["got"], entry["total"], entry["state"]))
            for sig in ("receivedBytesChanged", "stateChanged", "isFinishedChanged"):
                if hasattr(req, sig):
                    getattr(req, sig).connect(update)
            req.accept()
        profile.downloadRequested.connect(download)

        # ---- the menu
        def item(text, slot, keys=""):
            a = menu.addAction(text + (f"\t{keys}" if keys else ""))
            a.triggered.connect(lambda *_: slot())
        item("New page", lambda: open_page(), "Ctrl+T")
        item("Close page", lambda: close_page(), "Ctrl+W")
        menu.addSeparator()
        item("Start page", lambda: show_own(pages.current, "start"))
        item("Bookmarks", manage_bookmarks)
        item("Downloads", downloads_list)
        item("Find in page", open_find, "Ctrl+F")
        menu.addSeparator()
        item("Zoom in", lambda: zoom(1), "Ctrl++")
        item("Zoom out", lambda: zoom(-1), "Ctrl+-")
        item("Zoom reset", lambda: zoom(0), "Ctrl+0")
        menu.addSeparator()
        item("Clear data now", clear_now)

        # ---- signals and keys
        address.returnPressed.connect(lambda: go())
        find_box.returnPressed.connect(lambda: find(True))
        strip.currentChanged.connect(lambda i: choose(pages.order[i]) if 0 <= i < len(pages.order) else None)
        strip.tabMoved.connect(lambda frm, to: pages.move(pages.order[frm], to))

        st["actions"].update({
            "address": lambda: (address.setFocus(), address.selectAll()),
            "new": lambda: (open_page(), address.setFocus()),
            "close": lambda: close_page(),
            "next": lambda: choose(pages.step(1)),
            "previous": lambda: choose(pages.step(-1)),
            "reload": lambda: act("reload"),
            "find": open_find,
            "zoom_in": lambda: zoom(1),
            "zoom_out": lambda: zoom(-1),
            "zoom_reset": lambda: zoom(0),
            "back": lambda: act("back"),
            "forward": lambda: act("forward"),
        })
        # Parented to the tab's top widget and live wherever the keyboard is inside it (the address bar, a
        # button, the strip, or Chromium's own widget inside a view), and nowhere else: not on another tab.
        for seq, what in SHORTCUTS:
            sc = QShortcut(QKeySequence(seq), page)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(st["actions"][what])
            st.setdefault("shortcuts", []).append(sc)

        def escape_plan():
            focus = QApplication.focusWidget()
            view = current()
            in_page = focus is not None and view is not None and (focus is view or view.isAncestorOf(focus))
            where = "page" if in_page else ("address" if focus is not None and page.isAncestorOf(focus) else None)
            return escape_step(st["finding"], bool(info().get("loading")), where)

        def escape_do(step_):
            if step_ == "find":
                close_find()
            elif step_ == "stop":
                act("stop")
            elif step_ == "address":
                address.setFocus()
                address.selectAll()

        def stop():
            if prefs()["iga_clear_on_close"]:
                clear_data()

        page.escape_plan = escape_plan
        page.escape_do = escape_do
        page.recolour = refresh_start_pages
        page.stop = stop
        st["ready"] = True
        open_page()
        address.setFocus()

    return page


def deny_permissions(web_page, QWebEnginePage):
    """Location, camera, microphone, notifications and the rest are refused without asking, in this first version.
    Newer Qt (6.8 and later) asks through permissionRequested; older Qt through featurePermissionRequested."""
    if hasattr(web_page, "permissionRequested"):
        web_page.permissionRequested.connect(lambda p: p.deny())
    elif hasattr(web_page, "featurePermissionRequested"):
        denied = QWebEnginePage.PermissionPolicy.PermissionDeniedByUser
        web_page.featurePermissionRequested.connect(
            lambda url, feature: web_page.setFeaturePermission(url, feature, denied))

