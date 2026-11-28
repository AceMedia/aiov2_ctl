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


def setting_to_save(saved, key, value, opening=False):
    """(key, value) to write to the settings file, or None. Opening the page writes nothing, and nor does a value
    that is what the setting already is (saved, or its default): only a change the person makes is saved."""
    if opening or key not in DEFAULTS:
        return None
    return None if settings_from(saved)[key] == value else (key, value)


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


def escape_step(finding, loading, where, panel=False, editing=False, fullscreen=0):
    """What one Escape does on the IGA tab. `where` is 'page' (a web page has the keyboard), 'address' (the address
    field or any other IGA control has it) or anything else (the row of tabs, say). In order: 'panel' (close the
    open panel), 'collapse' (fold the address field back into the current page's tab), 'find' (close find),
    'stop' (stop loading), 'fullscreen' (one full-screen state back), 'address' (out of the page to the address
    field), 'tabs' (to the app's row of tabs, IGA still selected), or None: Escape is then the app's own next step
    (focusnav.back_step), as on any tab."""
    if panel:
        return "panel"
    if editing:
        return "collapse"
    if finding:
        return "find"
    if where == "page" and loading:
        return "stop"
    if fullscreen:
        return "fullscreen"
    if where == "page":
        return "address"
    if where == "address":
        return "tabs"
    return None


def key_owner(iga_showing, where, key, ctrl=False, shift=False, alt=False):
    """Who a key press belongs to while the app runs. `where` is where the keyboard is: 'page' (inside a web page),
    'address' (IGA's address field), 'iga' (another IGA control), 'app_tabs' (the app's row of tabs), 'corner'
    (one of the window's corner buttons) or anything else. With the IGA tab showing, IGA's own keys (Ctrl or Alt
    ones, key_action: Ctrl+L, Ctrl+T, Ctrl+Tab...) are IGA's wherever the keyboard is in the window; a plain Tab
    inside a web page is the page's ('page'); everything else (plain Tab and the numbers on the row of tabs, say)
    is the app's (None). On any other tab nothing is IGA's."""
    if not iga_showing:
        return None
    what = key_action(key, ctrl, shift, alt)
    if what:
        return what
    if key in ("Tab", "Backtab") and not (ctrl or alt) and where == "page":
        return "page"
    return None


def tab_key_owner(iga_showing, where, key, ctrl, shift):
    """The Tab keys' part of key_owner: 'next', 'previous', 'page' or None."""
    if key not in ("Tab", "Backtab"):
        return None
    return key_owner(iga_showing, where, key, ctrl, shift)


def key_name(code, keys):
    """The name key_action() uses for a Qt key code, or ''. `keys` is Qt.Key (passed in, so this stays plain)."""
    names = {int(keys.Key_Tab): "Tab", int(keys.Key_Backtab): "Backtab", int(keys.Key_Left): "Left",
             int(keys.Key_Right): "Right", int(keys.Key_Plus): "+", int(keys.Key_Equal): "=", int(keys.Key_Minus): "-"}
    code = int(code)
    if code in names:
        return names[code]
    if int(keys.Key_A) <= code <= int(keys.Key_Z) or int(keys.Key_0) <= code <= int(keys.Key_9):
        return chr(code)
    return ""


def arrival_focus(own, url):
    """Where the keyboard goes when the IGA tab is shown or a page is opened: 'field' (the address field, open and
    ready to type) for the start page or an empty page, otherwise 'page' (the web page)."""
    return "field" if own == "start" or not url or url == "about:blank" else "page"


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
            "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'; img-src data:\">"
            f"<title>{html.escape(title)}</title><style>"
            f"body{{background:{c['bg']};color:{c['text']};font-family:'Spleen 8x16',monospace;font-size:16px;"
            f"margin:24px;}}h1{{font-size:20px;color:{c['text']};border-bottom:2px solid {c['accent']};"
            f"padding-bottom:6px;}}a{{color:{c['text']};text-decoration:none;}}a:focus,a:hover{{color:{c['accent']};"
            f"}}li{{background:{c['card']};border:1px solid {c['border']};margin:4px 0;padding:6px 10px;"
            f"list-style:none;}}ul{{padding:0;}}.muted{{color:{c['muted']};}}.addr{{color:{c['muted']};"
            f"font-size:13px;}}</style></head><body>{body}</body></html>")


def start_page(bookmarks, colours, icons=None):
    """The start page: the bookmarks as links, each with its site's favicon where one was seen this session
    (`icons`: host to a PNG data: address), and a line about IGA. Every word in it is escaped."""
    icons = icons if isinstance(icons, dict) else {}
    rows = []
    for b in bookmarks or ():
        url = b.get("url") if isinstance(b, dict) else None
        if not _web(url):
            continue
        title = b.get("title") or url
        icon = icons.get(site_key(url))
        img = f"<img src=\"{icon}\" width='16' height='16' alt=''> " if favicon_ok(icon) else ""
        rows.append(f"<li>{img}<a href=\"{html.escape(url, quote=True)}\">{html.escape(title)}</a><br>"
                    f"<span class='addr'>{html.escape(url)}</span></li>")
    marks = f"<ul>{''.join(rows)}</ul>" if rows else \
        "<p class='muted'>No bookmarks yet. Open a page and press Bookmark to keep it here.</p>"
    body = (f"<h1>IGA</h1>{marks}<p class='muted'>IGA is part of the uConsole Ops app. It loads only what you ask "
            f"for. Click the current page's tab, or press Ctrl+L, to type an address. Escape goes back one step at a "
            f"time: from a page to the address, from the address to the app's row of tabs.</p>")
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



# ---- the slim top row, drawn as the app's own folder tabs
# One row 28 pixels high: three icon buttons, the strip of pages (folder tabs, the current one the address), three
# icon buttons, with ROW_GAP between those seven items and none between the tabs, which overlap as folder tabs do.
ROW_HEIGHT = 28
BUTTON_WIDTH = 28
ROW_GAP = 4
ROW_BUTTONS = ("back", "forward", "reload", "new", "bookmark", "menu")
ICON_TAB = 40                   # a page shown by its favicon only, with its whole folder outline
TITLE_TAB_MIN, TITLE_TAB_MAX = 140, 196
ADDRESS_MIN = 420               # the current page's tab always keeps this much for its address
TAB_OVERLAP = 4                 # how far a tab's foot lies over its right-hand neighbour (style: margin-right -4px)
TAB_INSET_LEFT, TAB_INSET_RIGHT = 10, 14      # clear of the cut corner on the left and of the foot on the right
ICON_SLOT, ICON_GAP, TEXT_GAP = 16, 4, 6
CENTRE_LINE = 14                # icons and words are centred this far down the row


def strip_width(width):
    """What is left of a row `width` wide for the tabs, after the six buttons and the six gaps round the strip."""
    return max(0, int(width) - len(ROW_BUTTONS) * BUTTON_WIDTH - len(ROW_BUTTONS) * ROW_GAP)


def row_layout(width, count, current):
    """How the strip shares out among `count` pages, `current` being the index of the current one: [{'mode':
    'address' | 'title' | 'icon', 'width'}] in page order. Widths are each tab's advance along the strip (the
    overlap is inside them) and add up to the strip exactly. The other pages show favicon and title while each can
    have TITLE_TAB_MIN after the address has ADDRESS_MIN, otherwise favicon only; the current page takes the rest."""
    count = max(1, int(count))
    current = min(max(0, int(current)), count - 1)
    strip = strip_width(width)
    others = count - 1
    if others and (strip - ADDRESS_MIN) // others >= TITLE_TAB_MIN:
        mode, each = "title", min(TITLE_TAB_MAX, (strip - ADDRESS_MIN) // others)
    else:
        mode, each = "icon", ICON_TAB
    rest = max(0, strip - others * each)
    return [{"mode": "address", "width": rest} if i == current else {"mode": mode, "width": each}
            for i in range(count)]


def address_slots(tab_width, editing=False):
    """Where things sit inside the current page's tab, in its own pixels: {'mark', 'favicon', 'text'} as (x, y,
    w, h). The security mark has a 16 pixel slot, then the favicon, then the words, all centred on CENTRE_LINE;
    while typing there is no favicon, so the words start sooner. 'favicon' is None while typing."""
    top = CENTRE_LINE - ICON_SLOT // 2
    mark = (TAB_INSET_LEFT, top, ICON_SLOT, ICON_SLOT)
    favicon = None if editing else (mark[0] + ICON_SLOT + ICON_GAP, top, ICON_SLOT, ICON_SLOT)
    x = (favicon or mark)[0] + ICON_SLOT + TEXT_GAP
    return {"mark": mark, "favicon": favicon, "text": (x, top, max(0, int(tab_width) - x - TAB_INSET_RIGHT),
                                                      ICON_SLOT)}


def editor_box(tab_width):
    """The typing box inside the opened tab: where the words go while typing, a little taller than the words."""
    x, _, w, _ = address_slots(tab_width, editing=True)["text"]
    return x, CENTRE_LINE - 12, w, 24


def address_runs(url):
    """The address shown in the current page's tab as (lead, host, rest): https:// is left out (the padlock says
    it), http:// is kept; anything not a web address is all in `rest`."""
    url = url or ""
    if not _web(url):
        return "", "", url
    parts = urlsplit(url)
    lead = "" if parts.scheme == "https" else parts.scheme + "://"
    head = parts.scheme + "://" + parts.netloc
    return lead, parts.netloc, url[len(head):]


def elide_address(lead, host, rest, fits):
    """Cut the address to fit: the path goes first, from its end; the whole host is kept as long as it can be.
    `fits(text)` says whether a text fits (the font's own measure, in the tab)."""
    if fits(lead + host + rest):
        return lead, host, rest
    for k in range(len(rest) - 1, -1, -1):
        cut = rest[:k] + "…"
        if fits(lead + host + cut):
            return lead, host, cut
    for k in range(len(host) - 1, -1, -1):
        cut = host[:k] + "…"
        if fits(lead + cut):
            return lead, cut, ""
    return "", "…", ""


def address_label(lead, host, rest, colours):
    """The address as one line: the site in the app's text colour, the rest in its muted colour, all escaped."""
    c = page_colours(colours)
    return (f"<span style=\"color:{c['text']}\">{html.escape(lead + host)}</span>"
            f"<span style=\"color:{c['muted']}\">{html.escape(rest)}</span>")


def mark_icon(url, own=False):
    """Which small icon sits at the left of the current page's tab: 'lock', 'unlock', 'app' or None. Its words
    (security_mark) are the tooltip and the accessible name."""
    if own:
        return "app"
    word = security_mark(url)
    return {"secure connection": "lock", "not secure": "unlock", "this app's own page": "app"}.get(word)


def mark_words(url, own=False):
    """The tooltip for the mark: what the connection is, without promising the site can be trusted."""
    kind = mark_icon(url, own)
    return {"lock": "Secure connection (https): what you send is encrypted on the way. It does not vouch for the "
                    "site.",
            "unlock": "Not secure (http): what you send can be read on the way.",
            "app": "This app's own page."}.get(kind, "")


# ---- the app's pixel icons for IGA's buttons: one 16 by 16 grid, strokes two pixels wide, ink centred
def _art(rows):
    rows = [r.ljust(16, ".")[:16] for r in rows]
    return rows + ["." * 16] * (16 - len(rows))


_BACK = _art(["", "", "......##", ".....##", "....##", "...##", "..##", "..############", "..############",
              "..##", "...##", "....##", ".....##", "......##"])
_BODY = ["..############", "..############", "..#####..#####", "..#####..#####", "..#####..#####", "..############",
         "..############", "..############"]
ICONS = {
    "back": _BACK,
    "forward": [r[::-1] for r in _BACK],
    "reload": _art(["", "", "....######..##", "...########.##", "..##......####", "..##.....#####", "..##",
                    "..##", "..##........##", "..##........##", "...##......##", "...##......##", "....########",
                    ".....######"]),
    "stop": _art(["", "", "..##........##", "..###......###", "...###....###", "....###..###", ".....######",
                  "......####", "......####", ".....######", "....###..###", "...###....###", "..###......###",
                  "..##........##"]),
    "new": _art(["", ""] + [".......##"] * 5 + ["..############"] * 2 + [".......##"] * 5),
    "menu": _art(["", "", "", "..############", "..############", "", "", "..############", "..############", "",
                  "", "..############", "..############"]),
    "bookmark": _art(["", "", "...##########", "...##########"] + ["...##......##"] * 7 +
                     ["...##..##..##", "...##.####.##", "...####..####"]),
    "lock": _art(["", "", ".....######", "....##....##", "....##....##", "....##....##"] + _BODY),
    "unlock": _art(["", "", ".......######", "......##....##", "......##....##", "......##"] + _BODY),
    "app": _art(["", "", "..############", "..############", "..##........##", "..##........##",
                 "..##..####..##", "..##..####..##", "..##..####..##", "..##..####..##", "..##........##",
                 "..##........##", "..############", "..############"]),
    "page": _art(["", "", ".....######", "...###....###", "..##........##", "..##........##", ".##..........##",
                  ".##############", ".##############", ".##..........##", "..##........##", "..##........##",
                  "...###....###", ".....######"]),
}
ICON_WORDS = {"back": "Back (Alt+Left)", "forward": "Forward (Alt+Right)", "reload": "Reload (Ctrl+R)",
              "stop": "Stop loading (Escape)", "new": "New page (Ctrl+T)", "bookmark": "Bookmark this page",
              "menu": "IGA menu: pages, bookmarks, downloads, find, zoom, clear data"}


# ---- panels: IGA's own, non-modal, dropping under the row in place of stock dialogs and menus
MENU_ITEMS = (("new", "New page  Ctrl+T"), ("close_page", "Close page  Ctrl+W"), ("start", "Start page"),
              ("bookmarks", "Bookmarks"), ("downloads", "Downloads"), ("find", "Find in page  Ctrl+F"),
              ("zoom_in", "Zoom in  Ctrl++"), ("zoom_out", "Zoom out  Ctrl+-"), ("zoom_reset", "Zoom reset  Ctrl+0"),
              ("clear", "Clear data now"), ("close", "Close menu"))


def panel_spec(kind, **kw):
    """What a panel shows: {'kind', 'text', 'field' (text or None), 'list' (lines or None), 'buttons' [(id,
    words)]}. Its words are plain text, never markup."""
    def spec(text, buttons, field=None, items=None):
        return {"kind": kind, "text": text, "field": field, "list": items, "buttons": list(buttons)}
    if kind == "message":
        return spec(kw.get("text", ""), [("ok", "OK")])
    if kind == "menu":
        return spec("", MENU_ITEMS)
    if kind == "bookmark":
        return spec("Name for this bookmark:", [("save", "Save"), ("cancel", "Cancel")], field=kw.get("title", ""))
    if kind == "rename":
        return spec("New name:", [("save", "Save"), ("cancel", "Cancel")], field=kw.get("title", ""))
    if kind == "bookmarks":
        items = [f"{b['title']}  ({b['url']})" for b in kw.get("bookmarks", ())]
        if not items:
            return spec("No bookmarks yet. Open a page and press Bookmark to keep it.", [("ok", "OK")])
        return spec("Bookmarks:", [("open", "Open"), ("rename", "Rename"), ("remove", "Remove"),
                                   ("close", "Close")], items=items)
    if kind == "remove":
        return spec(f"Remove the bookmark '{kw.get('title', '')}'?", [("remove", "Remove"), ("cancel", "Cancel")])
    if kind == "clear":
        return spec("Clear IGA's cookies, site storage, cache and history now? You will be signed out of sites. "
                    "Bookmarks are kept.", [("clear", "Clear"), ("cancel", "Cancel")])
    if kind == "downloads":
        lines = list(kw.get("lines", ()))
        return spec("Downloads:" if lines else "Nothing downloaded since the app started.", [("close", "Close")],
                    items=lines or None)
    if kind == "download":
        return spec(f"Save {kw.get('name', 'the file')} to {kw.get('folder', 'Downloads')}?",
                    [("save", "Save"), ("choose", "Choose folder"), ("cancel", "Cancel")])
    if kind == "folders":
        return spec(f"Save {kw.get('name', 'the file')} in {kw.get('folder', '')}:",
                    [("open", "Open folder"), ("here", "Save here"), ("cancel", "Cancel")],
                    items=[label for label, _ in kw.get("entries", ())])
    if kind == "find":
        return spec("Find in page:", [("next", "Next"), ("previous", "Previous"), ("close", "Close")],
                    field=kw.get("text", ""))
    raise ValueError(kind)


def folder_entries(path, names, isdir):
    """The rows of the folder chooser: up one level (unless at /), then the folders in `path` that are not hidden,
    by name. [(words, path)]."""
    out = []
    parent = os.path.dirname(path.rstrip("/")) or "/"
    if path.rstrip("/"):
        out.append((".. (up one folder)", parent))
    for name in sorted((n for n in names if not n.startswith(".")), key=str.lower):
        full = os.path.join(path, name)
        if isdir(full):
            out.append((name + "/", full))
    return out


def pending_folder(home=None):
    """Where a download is written while you choose where it goes: in IGA's own private folder."""
    return os.path.join(user_folder(home), "pending")


def download_next(state, target, moved):
    """What to do with a download now: 'move' (finished and you chose where: move it there), 'delete' (it was
    cancelled or failed: remove what was written), 'wait' (still going, or still waiting for your choice), or None
    (nothing more)."""
    if moved:
        return None
    if state in ("cancelled", "failed"):
        return "delete"
    if state == "done" and target:
        return "move"
    return "wait"


# ---- full screen in three states while the IGA tab shows
# 0 normal; 1 browser full screen (window full screen, the app's tabs and status bar hidden); 2 page full screen
# (IGA's row hidden too).
def next_fullscreen(state, action, before=0):
    """The next state. 'toggle' (the full-screen button, f, the gamepad's Y): 0 to 1 to 2 and back to 0. 'back'
    (F11 in state 1 or 2, or Escape): one state back. 'leave': straight to 0. 'page_on' (a page asked for full
    screen, a video say): 2. 'page_off': back to `before`, the state it was in when the page asked."""
    state = state if state in (0, 1, 2) else 0
    if action == "toggle":
        return (state + 1) % 3
    if action == "back":
        return max(0, state - 1)
    if action == "leave":
        return 0
    if action == "page_on":
        return 2
    if action == "page_off":
        return before if before in (0, 1) else 0
    return state


def fullscreen_view(state):
    """What shows in a state: {'window_full', 'app_bars' (the app's row of tabs, its corner buttons and the
    status bar), 'iga_row'}."""
    return {"window_full": state > 0, "app_bars": state == 0, "iga_row": state < 2}


def f11_action(state):
    """F11 on the IGA tab: back one state when in full screen, otherwise the same as the full-screen button."""
    return "back" if state > 0 else "toggle"


def top_peek_step(shown, count, y, inside=True, edge=2, rest=2, leave=60):
    """In states 1 and 2, resting the pointer on the top edge brings the small strip into view at the top right;
    moving away from it takes it away. Returns (shown, count, 'show' | 'hide' | None). `count` counts the ticks
    the pointer has rested on the edge."""
    if not shown:
        if inside and y <= edge:
            count += 1
            if count >= rest:
                return True, 0, "show"
            return False, count, None
        return False, 0, None
    if not inside or y > leave:
        return False, 0, "hide"
    return True, 0, None


def favicon_ok(uri):
    """True for a favicon kept for the start page: a PNG as a data: address, nothing else, not too big."""
    return isinstance(uri, str) and len(uri) < 20000 and re.fullmatch(r"data:image/png;base64,[A-Za-z0-9+/=]+", uri)\
        is not None


def site_key(url):
    """The host a favicon is remembered by."""
    return (urlsplit(url).hostname or "").lower() if _web(url) else ""


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
    ("FullScreenSupportEnabled", True),     # a page (a video) may ask for full screen: IGA's state 2
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
    control is an ordinary widget of the app (icon buttons like the window's own top-bar buttons, the app's
    buttons, line edits, list and card frames), so it takes the app's style sheet, theme, contrast, feature colour
    and fonts; only the web page itself is styled by the site. Every decision is a plain function above."""
    import shutil
    import time
    from PyQt6.QtCore import QSize, Qt, QTimer, QUrl
    from PyQt6.QtGui import QColor, QFontMetrics, QIcon, QKeySequence, QPixmap, QShortcut
    from PyQt6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                                 QProgressBar, QPushButton, QStackedWidget, QVBoxLayout, QWidget)
    from . import data, style

    folder = user_folder()

    def colours():
        return page_colours(style.TOKENS["dark" if getattr(win, "theme", "dark") == "dark" else "light"])

    def prefs():
        return settings_from(getattr(win, "settings", None) or data.load_settings())

    def pixel_icon(name):
        """One of IGA's 16 by 16 pixel icons, drawn by the same renderer as the window's top-bar icons: the theme's
        text colour, and its muted colour when the button is off."""
        from . import pixelicon
        t = style.TOKENS["dark" if getattr(win, "theme", "dark") == "dark" else "light"]
        return pixelicon.bitmap_icon(ICONS[name], QColor(t["text"]), 1, QColor(t["muted"]))

    class Page(QWidget):
        def keyPressEvent(self, event):
            """A key with Ctrl or Alt that no child used comes up to here: if it is one of IGA's, do it."""
            mods = event.modifiers()
            name = key_name(event.key(), Qt.Key)
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

        def resizeEvent(self, event):
            super().resizeEvent(event)
            if st["ready"]:
                QTimer.singleShot(0, st["relayout"])

    page = Page()
    outer = QVBoxLayout(page)
    outer.setContentsMargins(4, 2, 4, 2)                # slim: less than the other tabs' toolbars
    outer.setSpacing(2)
    waiting = QLabel("IGA starts when this tab is first opened.")
    waiting.setWordWrap(True)
    waiting.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    waiting.setStyleSheet("font-size: 13pt;")
    outer.addWidget(waiting, 1, Qt.AlignmentFlag.AlignTop)
    st = {"started": False, "ready": False, "views": {}, "info": {}, "downloads": [], "finding": False,
          "actions": {}, "panel": None, "editing": False, "row_hidden": False, "favicons": {}, "asks": [],
          "relayout": lambda: None, "peek": (False, 0)}
    page.key_act = lambda what: st["actions"].get(what, lambda: None)()
    page.escape_plan = lambda: None            # until the engine is there, Escape is the app's as on any tab
    page.escape_do = lambda _step: None
    page.recolour = lambda: None
    page.stop = lambda: None
    page.set_row_hidden = lambda hidden: None
    page.arrive = lambda: None

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
        _private_folder(pending_folder())
        profile = QWebEngineProfile("aiov2-iga", page)          # one profile with a storage name of its own
        profile.setPersistentStoragePath(profile_folder())
        profile.setCachePath(os.path.join(profile_folder(), "cache"))
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        for call, value in (("setSpellCheckEnabled", False), ("setDownloadPath", pending_folder())):
            try:
                getattr(profile, call)(value)
            except Exception:
                pass
        es = profile.settings()
        for name, value in ENGINE_SETTINGS:
            attr = getattr(QWebEngineSettings.WebAttribute, name, None)
            if attr is not None:
                es.setAttribute(attr, value)
        st["profile"] = profile
        pages = Pages(open_first=False)         # open_page() below makes the first, with its view and its tab
        st["pages"] = pages

        class WebPage(QWebEnginePage):
            def createWindow(self, _kind):                       # a link that wants a new window: a new IGA page
                pid = open_page(blank=True)
                return st["views"][pid].page() if pid is not None else None

        # ---- the one slim row: the app's own folder tabs, with the icon buttons in its two corners
        from .foldertabs import FolderTabBar, FolderTabWidget

        class PageBar(FolderTabBar):
            """The app's folder tab bar, with each tab as wide as row_layout() says."""

            def __init__(self):
                super().__init__()
                self.widths = []

            def tabSizeHint(self, i):
                w = self.widths[i] if 0 <= i < len(self.widths) else ICON_TAB
                return QSize(max(0, int(w)), ROW_HEIGHT)

            def minimumTabSizeHint(self, i):
                return self.tabSizeHint(i)

            def keyPressEvent(self, event):
                if int(event.key()) in (int(Qt.Key.Key_Return), int(Qt.Key.Key_Enter)):
                    expand()
                    return
                super().keyPressEvent(event)

            def tabLayoutChange(self):
                super().tabLayoutChange()
                QTimer.singleShot(0, place_address)

            def resizeEvent(self, event):
                super().resizeEvent(event)
                QTimer.singleShot(0, place_address)

        tabs = FolderTabWidget()
        bar = PageBar()
        tabs.setTabBar(bar)                      # before any tab is added
        tabs.setObjectName("igatabs")            # style.py: the compact folder tabs of IGA's row
        bar.setDrawBase(False)
        bar.setExpanding(False)
        bar.setUsesScrollButtons(False)
        bar.setElideMode(Qt.TextElideMode.ElideRight)
        bar.setIconSize(QSize(16, 16))
        bar.setFocusPolicy(Qt.FocusPolicy.StrongFocus)          # Escape from a page lands on the tabs
        bar.setAccessibleName("IGA's open pages. The current one is the address: press Enter or Ctrl+L to type")
        outer.addWidget(tabs, 1)
        buttons = {}

        def icon_button(name, slot):
            b = QPushButton("")
            b.setObjectName("iconbtn")                   # styled as the window's own top-bar icon buttons
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)     # as on the other tabs: the D-pad highlight reaches it
            b.setFixedSize(BUTTON_WIDTH, ROW_HEIGHT)
            b.setIconSize(QSize(16, 16))
            b.setToolTip(ICON_WORDS[name])
            b.setAccessibleName(ICON_WORDS[name])
            b.clicked.connect(lambda *_: slot())
            buttons[name] = b
            return b

        def corner(names_slots, left):
            box = QWidget()
            lay = QHBoxLayout(box)
            lay.setContentsMargins(0, 0, ROW_GAP, 0) if left else lay.setContentsMargins(ROW_GAP, 0, 0, 0)
            lay.setSpacing(ROW_GAP)
            for name, slot in names_slots:
                lay.addWidget(icon_button(name, slot))
            box.setFixedHeight(ROW_HEIGHT)
            return box
        left_box = corner((("back", lambda: act("back")), ("forward", lambda: act("forward")),
                           ("reload", lambda: act("reload_or_stop"))), True)
        right_box = corner((("new", lambda: (open_page(), arrive())), ("bookmark", lambda: bookmark_this()),
                            ("menu", lambda: toggle_menu())), False)
        tabs.setCornerWidget(left_box, Qt.Corner.TopLeftCorner)
        tabs.setCornerWidget(right_box, Qt.Corner.TopRightCorner)

        # inside the current page's tab: the mark, the favicon and the address, or the typing box
        address = QWidget(bar)
        address.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)   # a click is the tab's
        mark_lab, fav_lab, text_lab = QLabel(address), QLabel(address), QLabel(address)
        text_lab.setTextFormat(Qt.TextFormat.RichText)
        text_lab.setFont(style.pixel_font(16))

        class AddressField(QLineEdit):
            def focusOutEvent(self, event):
                super().focusOutEvent(event)
                if st["editing"]:
                    QTimer.singleShot(0, collapse)

        field = AddressField(bar)
        field.setObjectName("tabfield")         # style.py: the field colour inside the open tab, no frame of its own
        field.setFont(style.pixel_font(16))
        field.setPlaceholderText("Type an address, then Enter")
        field.setAccessibleName("Address of the current page")
        field.hide()

        # under the row, inside the pane: the panels (attached, no gap) and the thin progress line
        top = QWidget()
        tv = QVBoxLayout(top)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.setSpacing(0)
        panel = QWidget()
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(8, 8, 8, 8)
        pl.setSpacing(6)
        panel.hide()
        tv.addWidget(panel)
        progress = QProgressBar()
        progress.setTextVisible(False)
        progress.setMaximumHeight(4)
        progress.setRange(0, 100)
        progress.hide()
        tv.addWidget(progress)
        st["holders"] = {}

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

        def favicon(pid):
            i = st["info"].get(pid, {})
            if i.get("own"):
                return pixel_icon("app")
            icon = i.get("icon")
            return icon if icon is not None and not icon.isNull() else pixel_icon("page")

        def move_top(pid):
            holder = st["holders"].get(pid)
            if holder is not None and top.parent() is not holder:
                holder.layout().insertWidget(0, top)
            top.show()

        def corners_visible(on):
            left_box.setVisible(on)
            right_box.setVisible(on)

        # ---- the pages in the row
        def relayout():
            n = len(pages.order)
            if not n or pages.current not in pages.order:
                return
            cur = pages.index(pages.current)
            width = max(0, page.width() - 8)
            if st["editing"]:
                widths = [width if i == cur else 0 for i in range(n)]
                plan = [{"mode": "address" if i == cur else "hidden", "width": w} for i, w in enumerate(widths)]
            else:
                plan = row_layout(width, n, cur)
            bar.widths = [p["width"] for p in plan]
            for i, (pid, place) in enumerate(zip(pages.order, plan)):
                if i >= bar.count():
                    break
                url = shown_url(pid)
                own = st["info"].get(pid, {}).get("own")
                view = st["views"].get(pid)
                title = "" if own or view is None else view.title()
                label = page_label(title, url, 60)
                try:
                    bar.setTabVisible(i, place["mode"] != "hidden")
                except Exception:
                    pass
                if place["mode"] == "address":
                    bar.setTabIcon(i, QIcon())
                    bar.setTabText(i, "")
                    tip = mark_words(url, bool(own))
                    bar.setTabToolTip(i, (tip + "\n" if tip else "") + (url if url != "about:blank" else "New page")
                                      + "\nPress, or Ctrl+L, to type an address.")
                else:
                    bar.setTabIcon(i, favicon(pid))
                    bar.setTabText(i, label if place["mode"] == "title" else "")
                    bar.setTabToolTip(i, label + ("\n" + url if url and url != label else ""))
                try:
                    bar.setTabData(i, label)
                except Exception:
                    pass
            corners_visible(not st["editing"] and not st["row_hidden"])
            bar.setElideMode(bar.elideMode())               # asks the bar to lay its tabs out again
            bar.updateGeometry()
            bar.update()
            QTimer.singleShot(0, place_address)
            refresh_buttons()

        st["relayout"] = relayout

        def place_address():
            """Put the mark, favicon and address (or the typing box) inside the current tab, at address_slots()."""
            try:
                if not pages.order or pages.current not in pages.order or not bar.isVisible():
                    address.hide()
                    return
                i = pages.index(pages.current)
                r = bar.tabRect(i)
                if r.width() <= 0:
                    address.hide()
                    return
                pid = pages.current
                url = shown_url(pid)
                own = bool(st["info"].get(pid, {}).get("own"))
                slots = address_slots(r.width(), st["editing"])
                address.setGeometry(r.x() + bar.inset(), r.y(), r.width(), r.height())
                kind = mark_icon(url, own)
                mark_lab.setGeometry(*slots["mark"])
                mark_lab.setPixmap(pixel_icon(kind).pixmap(16, 16) if kind else QPixmap())
                mark_lab.setToolTip(mark_words(url, own))
                mark_lab.setAccessibleName(mark_words(url, own))
                if slots["favicon"] is None:
                    fav_lab.hide()
                else:
                    fav_lab.setGeometry(*slots["favicon"])
                    fav_lab.setPixmap(favicon(pid).pixmap(16, 16))
                    fav_lab.show()
                x, y, w, h = slots["text"]
                text_lab.setGeometry(x, y, w, h)
                if st["editing"]:
                    text_lab.hide()
                    fx, fy, fw, fh = editor_box(r.width())
                    field.setGeometry(r.x() + bar.inset() + fx, r.y() + fy, fw, fh)
                    field.show()
                    field.raise_()
                else:
                    field.hide()
                    metrics = QFontMetrics(text_lab.font())
                    if own or not _web(url):
                        view = st["views"].get(pid)
                        runs = ("", "", page_label("" if own or view is None else view.title(), url, 200))
                    else:
                        runs = address_runs(url)
                    runs = elide_address(*runs, lambda s: metrics.horizontalAdvance(s) <= w)
                    text_lab.setText(address_label(*runs, colours()))
                    text_lab.show()
                address.show()
                address.raise_()
            except Exception:                     # never let the row's drawing take the tab down
                address.hide()

        def refresh_buttons():
            view = current()
            if view is None:
                return
            h = view.history()
            buttons["back"].setEnabled(h.canGoBack())
            buttons["forward"].setEnabled(h.canGoForward())
            loading_now = bool(info().get("loading"))
            buttons["reload"].setIcon(pixel_icon("stop" if loading_now else "reload"))
            words = ICON_WORDS["stop" if loading_now else "reload"]
            buttons["reload"].setToolTip(words)
            buttons["reload"].setAccessibleName(words)
            for name in ("back", "forward", "new", "bookmark", "menu"):
                buttons[name].setIcon(pixel_icon(name))
            url = shown_url(pages.current)
            buttons["bookmark"].setEnabled(_web(url) and not info().get("own"))

        def focus_current():
            if bar.isVisible():
                bar.setFocus()

        def expand():
            """The current page's tab opens across the whole row, the same folder tab, with its address selected."""
            st["editing"] = True
            if st["row_hidden"]:
                bar.show()                      # page full screen: the row comes back just to type
            url = shown_url(pages.current)
            field.setText("" if url in ("about:blank", START) else url)
            relayout()
            place_address()
            field.show()
            field.setFocus()
            field.selectAll()

        def collapse(to_view=False):
            if not st["editing"]:
                return
            st["editing"] = False
            field.hide()
            if st["row_hidden"]:
                bar.hide()
            relayout()
            view = current()
            if to_view and view is not None:
                view.setFocus()
            elif not st["row_hidden"]:
                focus_current()

        def tab_pressed(i):
            if not 0 <= i < len(pages.order):
                return
            if pages.order[i] == pages.current:
                expand()
            else:
                choose(pages.order[i])

        bar.tabBarClicked.connect(tab_pressed)
        tabs.currentChanged.connect(lambda i: choose(pages.order[i])
                                    if 0 <= i < len(pages.order) and pages.order[i] != pages.current else None)

        def go(text):
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

        field.returnPressed.connect(lambda: (go(field.text()), collapse(to_view=True)))

        # ---- panels: IGA's own, non-modal, under the row
        def close_panel():
            kind = st["panel"]
            st["panel"] = None
            if kind == "find":
                st["finding"] = False
                view = current()
                if view is not None:
                    view.findText("")
                    view.setFocus()
            panel.hide()
            while pl.count():
                item_ = pl.takeAt(0)
                w = item_.widget()
                if w is not None:
                    w.hide()
                    w.deleteLater()
                elif item_.layout() is not None:
                    lay = item_.layout()
                    while lay.count():
                        sub = lay.takeAt(0).widget()
                        if sub is not None:
                            sub.hide()
                            sub.deleteLater()
            if st["asks"]:
                QTimer.singleShot(0, lambda: ask_download(st["asks"].pop(0)))

        def show_panel(spec, handler):
            """Show a panel from panel_spec(); handler(button id, field text, chosen row) is called on a press."""
            if st["panel"] is not None:
                close_panel()
            st["panel"] = spec["kind"]
            if spec["text"]:
                lab = QLabel(spec["text"])
                lab.setTextFormat(Qt.TextFormat.PlainText)
                lab.setWordWrap(True)
                pl.addWidget(lab)
            box = None
            if spec["field"] is not None:
                box = QLineEdit(spec["field"])
                box.setObjectName("rowfield")
                box.setFixedHeight(ROW_HEIGHT)
            lst = None
            if spec["list"]:
                lst = QListWidget()
                lst.addItems(spec["list"])
                lst.setCurrentRow(0)
                lst.setMaximumHeight(170)
                pl.addWidget(lst)
            line = QHBoxLayout()
            line.setSpacing(6)
            if box is not None:
                line.addWidget(box, 1)

            def press(bid):
                handler(bid, box.text() if box is not None else "", lst.currentRow() if lst is not None else -1)
            for bid, words in spec["buttons"]:
                b = QPushButton(words)
                b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                b.clicked.connect(lambda *_, i=bid: press(i))
                line.addWidget(b)
            if box is None:
                line.addStretch(1)
            pl.addLayout(line)
            first = spec["buttons"][0][0]
            if box is not None:
                box.returnPressed.connect(lambda: press(first))
                if spec["kind"] == "find":
                    box.textChanged.connect(lambda t: find(t, True))
            if lst is not None:
                lst.itemActivated.connect(lambda *_: press(first))
            panel.show()
            if box is not None:
                box.setFocus()
                box.selectAll()
            elif lst is not None:
                lst.setFocus()

        def message(text):
            show_panel(panel_spec("message", text=text), lambda *_: close_panel())

        def toggle_menu():
            if st["panel"] == "menu":
                close_panel()
                return

            def chosen(bid, *_):
                close_panel()
                {"new": lambda: (open_page(), arrive()), "close_page": lambda: close_page(),
                 "start": lambda: show_own(pages.current, "start"), "bookmarks": manage_bookmarks,
                 "downloads": downloads_list, "find": open_find, "zoom_in": lambda: zoom(1),
                 "zoom_out": lambda: zoom(-1), "zoom_reset": lambda: zoom(0), "clear": clear_now,
                 "close": lambda: None}.get(bid, lambda: None)()
            show_panel(panel_spec("menu"), chosen)

        # ---- the views
        def show_own(pid, kind, title="", text="", shown=START):
            view = st["views"][pid]
            st["info"][pid] = dict(st["info"].get(pid, {}), own=kind, shown=shown, title=title, text=text)
            if kind == "start":
                body = start_page(load_bookmarks(folder), colours(), st["favicons"])
            else:
                body = notice_page(title, text, colours(), address=shown if shown != START else "")
            view.setHtml(body, QUrl("about:blank"))
            relayout()

        def make_view(pid):
            view = QWebEngineView()
            view.setProperty("typing_zone", True)        # the app's letter shortcuts leave a web page alone
            wp = WebPage(profile, view)
            view.setPage(wp)
            deny_permissions(wp, QWebEnginePage)
            view.titleChanged.connect(lambda *_: relayout())
            view.urlChanged.connect(lambda u: url_changed(pid, u))
            view.loadStarted.connect(lambda: loading(pid, True))
            view.loadProgress.connect(lambda n: progress.setValue(n) if pid == pages.current else None)
            view.loadFinished.connect(lambda ok: finished(pid, ok))
            if hasattr(view, "iconChanged"):
                view.iconChanged.connect(lambda icon: got_icon(pid, icon))
            if hasattr(wp, "loadingChanged"):
                wp.loadingChanged.connect(lambda li: failed(pid, li))
            if hasattr(wp, "fullScreenRequested"):
                wp.fullScreenRequested.connect(page_fullscreen)
            st["views"][pid] = view
            st["info"][pid] = {}
            holder = QWidget()
            hv = QVBoxLayout(holder)
            hv.setContentsMargins(0, 0, 0, 0)
            hv.setSpacing(0)
            hv.addWidget(view, 1)
            st["holders"][pid] = holder
            tabs.blockSignals(True)
            tabs.insertTab(pages.index(pid), holder, "")
            tabs.blockSignals(False)
            return view

        def got_icon(pid, icon):
            if pid not in st["info"]:
                return
            st["info"][pid]["icon"] = icon
            try:
                from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
                raw = QByteArray()
                buf = QBuffer(raw)
                buf.open(QIODevice.OpenModeFlag.WriteOnly)
                icon.pixmap(16, 16).save(buf, "PNG")
                uri = "data:image/png;base64," + bytes(raw.toBase64()).decode("ascii")
                key = site_key(shown_url(pid))
                if key and favicon_ok(uri):
                    st["favicons"][key] = uri           # this session only, from the page itself
            except Exception:
                pass
            relayout()

        def page_fullscreen(req):
            try:
                req.accept()
                on = req.toggleOn()
            except Exception:
                return
            hook = getattr(win, "iga_fullscreen", None)
            if hook is not None:
                hook("page_on" if on else "page_off")

        def url_changed(pid, url):
            i = st["info"].setdefault(pid, {})
            if url.scheme() in ("http", "https"):
                i["was_own"], i["own"] = i.get("own") or i.get("was_own"), None
            elif not i.get("own") and i.get("was_own"):        # Back to one of the app's own pages
                i["own"] = i["was_own"]
            relayout()

        def loading(pid, on):
            st["info"].setdefault(pid, {})["loading"] = on
            if pid == pages.current:
                progress.setVisible(on)
                if on:
                    progress.setValue(0)
                refresh_buttons()

        def finished(pid, ok):
            loading(pid, False)
            view = st["views"].get(pid)
            if ok and view is not None and not st["info"][pid].get("own"):
                add_visit(folder, view.url().toString(), view.title(), time.time(), prefs()["iga_history"])
            relayout()

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
                message(why)
                return None
            make_view(pid)
            choose(pid)
            if url:
                go(url)
            elif not blank:
                show_own(pid, "start")
            return pid

        def choose(pid):
            pages.select(pid)
            tabs.blockSignals(True)
            tabs.setCurrentIndex(pages.index(pid))
            tabs.blockSignals(False)
            bar.update()
            move_top(pid)
            progress.setVisible(bool(info().get("loading")))
            relayout()

        def close_page(pid=None):
            pid = pages.current if pid is None else pid
            at = pages.index(pid)
            fresh = pages.close(pid)
            view = st["views"].pop(pid)
            st["info"].pop(pid, None)
            holder = st["holders"].pop(pid, None)
            if top.parent() is holder:
                top.setParent(page)                 # kept: it moves to the next current page
                top.hide()
            tabs.blockSignals(True)
            tabs.removeTab(at)
            tabs.blockSignals(False)
            view.page().deleteLater()            # the page goes first: Qt WebEngine warns if a profile outlives it
            view.deleteLater()
            if holder is not None:
                holder.deleteLater()
            if fresh is not None:
                make_view(fresh)
                show_own(fresh, "start")
            choose(pages.current)

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
                elif not info().get("own"):
                    view.reload()
            elif what == "reload":
                if not info().get("own"):
                    view.reload()
            elif what == "stop":
                view.stop()

        def zoom(direction):
            view = current()
            if view is not None:
                view.setZoomFactor(zoom_step(view.zoomFactor(), direction))

        def find(text, forward=True):
            view = current()
            if view is None:
                return
            if forward:
                view.findText(text)
            else:
                view.findText(text, QWebEnginePage.FindFlag.FindBackward)

        def open_find():
            def pressed(bid, text, _row):
                if bid == "close":
                    close_panel()
                elif text:
                    find(text, bid == "next")
            show_panel(panel_spec("find"), pressed)
            st["finding"] = True

        def bookmark_this():
            view = current()
            if view is None or info().get("own"):
                return
            url = view.url().toString()

            def pressed(bid, text, _row):
                close_panel()
                if bid == "save":
                    if add_bookmark(folder, text, url):
                        refresh_start_pages()
                    else:
                        message("Only web addresses (http and https) can be kept as bookmarks.")
            show_panel(panel_spec("bookmark", title=view.title() or url), pressed)

        def manage_bookmarks():
            marks = load_bookmarks(folder)

            def pressed(bid, _text, row_):
                close_panel()
                if not marks or bid in ("ok", "close") or not 0 <= row_ < len(marks):
                    return
                chosen = marks[row_]
                if bid == "open":
                    go(chosen["url"])
                elif bid == "rename":
                    def renamed(b2, text, _r):
                        close_panel()
                        if b2 == "save":
                            rename_bookmark(folder, chosen["url"], text)
                            refresh_start_pages()
                    show_panel(panel_spec("rename", title=chosen["title"]), renamed)
                elif bid == "remove":
                    def removed(b2, *_):
                        close_panel()
                        if b2 == "remove":
                            remove_bookmark(folder, chosen["url"])
                            refresh_start_pages()
                    show_panel(panel_spec("remove", title=chosen["title"]), removed)
            show_panel(panel_spec("bookmarks", bookmarks=marks), pressed)

        def refresh_start_pages():
            for pid in list(pages.order):
                own = st["info"].get(pid, {}).get("own")
                if own:
                    i = st["info"][pid]
                    show_own(pid, own, i.get("title", ""), i.get("text", ""), i.get("shown", START))
            relayout()

        def downloads_list():
            lines = [download_line(d["name"], d["got"], d["total"],
                                   "going" if d["state"] == "done" and not d.get("moved") else d["state"])
                     for d in st["downloads"]]
            show_panel(panel_spec("downloads", lines=lines), lambda *_: close_panel())

        def clear_now():
            def pressed(bid, *_):
                close_panel()
                if bid == "clear":
                    clear_data()
                    message("Cookies, site storage, cache and history are cleared.")
            show_panel(panel_spec("clear"), pressed)

        def clear_data():
            for call in (lambda: profile.clearHttpCache(), lambda: profile.cookieStore().deleteAllCookies(),
                         lambda: profile.clearAllVisitedLinks()):
                try:
                    call()
                except Exception:
                    pass
            clear_history(folder)

        # ---- downloads: written to IGA's private folder while you choose where they go
        def settle(entry):
            what = download_next(entry["state"], entry.get("target"), entry.get("moved"))
            src = os.path.join(pending_folder(), entry["file"])
            try:
                if what == "move" and os.path.exists(src):
                    shutil.move(src, entry["target"])
                    entry["moved"] = True
                    win.say(f"Saved {entry['target']}")
                elif what == "delete" and os.path.exists(src):
                    os.remove(src)
                    entry["moved"] = True
            except OSError as exc:
                entry["state"] = "failed"
                message(f"{entry['name']} could not be saved: {exc}")

        def choose_target(entry, where):
            name = safe_name(entry["name"], where)
            entry["target"] = os.path.join(where, name)
            entry["name"] = name
            settle(entry)

        def ask_download(entry):
            if st["panel"] is not None:
                st["asks"].append(entry)
                return
            if entry["state"] in ("cancelled", "failed"):
                return
            home_dl = downloads_folder()

            def pressed(bid, *_):
                close_panel()
                if bid == "save":
                    os.makedirs(home_dl, exist_ok=True)
                    choose_target(entry, home_dl)
                elif bid == "choose":
                    browse(entry, home_dl if os.path.isdir(home_dl) else os.path.expanduser("~"))
                else:
                    try:
                        entry["req"].cancel()
                    except Exception:
                        pass
                    entry["state"] = "cancelled"
                    settle(entry)
            show_panel(panel_spec("download", name=entry["name"], folder=home_dl), pressed)

        def browse(entry, where):
            try:
                names = os.listdir(where)
            except OSError:
                names = []
            rows = folder_entries(where, names, os.path.isdir)

            def pressed(bid, _text, row_):
                close_panel()
                if bid == "open" and 0 <= row_ < len(rows):
                    browse(entry, rows[row_][1])
                elif bid == "here":
                    choose_target(entry, where)
                else:
                    ask_download(entry)
            show_panel(panel_spec("folders", name=entry["name"], folder=where, entries=rows), pressed)

        def download(req):
            try:
                suggested = req.downloadFileName()
            except Exception:
                suggested = ""
            name = safe_name(suggested, pending_folder())
            entry = {"name": name, "file": name, "got": 0, "total": 0, "state": "going", "req": req}
            try:
                req.setDownloadDirectory(pending_folder())
                req.setDownloadFileName(name)
                req.accept()
            except Exception as exc:
                message(f"The download could not start: {exc}")
                return
            st["downloads"].append(entry)

            def update(*_):
                try:
                    entry["got"], entry["total"] = req.receivedBytes(), req.totalBytes()
                    s = req.state()
                    kinds = type(s)
                    entry["state"] = {kinds.DownloadCompleted: "done", kinds.DownloadCancelled: "cancelled",
                                      kinds.DownloadInterrupted: "failed"}.get(s, "going")
                except Exception:
                    pass
                if entry["state"] != "going":
                    settle(entry)
            for sig in ("receivedBytesChanged", "stateChanged", "isFinishedChanged"):
                if hasattr(req, sig):
                    getattr(req, sig).connect(update)
            ask_download(entry)
        profile.downloadRequested.connect(download)

        # ---- the strip that slides in at the top right in full screen
        strip = QFrame(win)
        strip.setObjectName("card")
        sl = QHBoxLayout(strip)
        sl.setContentsMargins(6, 2, 6, 2)
        strip_addr = QLabel("")
        strip_addr.setTextFormat(Qt.TextFormat.PlainText)
        sl.addWidget(strip_addr)
        for words, action in (("Back one step", "back"), ("Leave full screen", "leave")):
            b = QPushButton(words)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda *_, a=action: (strip.hide(), getattr(win, "iga_fullscreen", lambda _a: None)(a)))
            sl.addWidget(b)
        strip.hide()

        def peek_tick():
            from PyQt6.QtGui import QCursor
            state = getattr(win, "iga_fs", 0)
            if not state or not page.isVisible():
                if strip.isVisible():
                    strip.hide()
                st["peek"] = (False, 0)
                return
            pos = win.mapFromGlobal(QCursor.pos())
            shown, count, what = top_peek_step(st["peek"][0], st["peek"][1], pos.y(), win.rect().contains(pos))
            st["peek"] = (shown, count)
            if what == "show":
                strip_addr.setText(shown_url(pages.current) if state == 2 else "")
                strip_addr.setVisible(state == 2)
                strip.adjustSize()
                strip.move(max(0, win.width() - strip.width() - 8), 0)
                strip.raise_()
                strip.show()
                try:
                    from PyQt6.QtCore import QPoint, QPropertyAnimation
                    anim = QPropertyAnimation(strip, b"pos", strip)
                    anim.setDuration(150)
                    anim.setStartValue(QPoint(strip.x(), -strip.height()))
                    anim.setEndValue(QPoint(strip.x(), 0))
                    anim.start()
                    st["anim"] = anim
                except Exception:
                    pass
            elif what == "hide":
                strip.hide()
        peek_timer = QTimer(page)
        peek_timer.timeout.connect(peek_tick)
        peek_timer.start(250)

        # ---- keys
        st["actions"].update({
            "address": expand,
            "new": lambda: (open_page(), arrive()),
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
        # Parented to the tab's top widget and live wherever the keyboard is inside it (the address field, a
        # button, a panel, or Chromium's own widget inside a view), and nowhere else: not on another tab.
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
            return escape_step(st["finding"], bool(info().get("loading")), where, panel=st["panel"] is not None,
                               editing=st["editing"], fullscreen=getattr(win, "iga_fs", 0))

        def escape_do(step_):
            if step_ in ("panel", "find"):
                close_panel()
            elif step_ == "collapse":
                collapse()
            elif step_ == "stop":
                act("stop")
            elif step_ == "fullscreen":
                hook = getattr(win, "iga_fullscreen", None)
                if hook is not None:
                    hook("back")
            elif step_ == "address":
                focus_current()

        def arrive():
            """Put the keyboard inside IGA: on the page, or in the open address field for an empty page."""
            view = current()
            if view is None:
                return
            own = info().get("own")
            if arrival_focus(own, "" if own else view.url().toString()) == "field":
                expand()
            else:
                view.setFocus()

        def set_row_hidden(hidden):
            st["row_hidden"] = bool(hidden)
            if not st["editing"]:
                bar.setVisible(not hidden)
                corners_visible(not hidden)
            if hidden:
                strip.hide()
            elif not getattr(win, "iga_fs", 0):
                strip.hide()

        def stop():
            if prefs()["iga_clear_on_close"]:
                clear_data()

        def recolour():
            refresh_start_pages()               # the app's own pages, never a site's
            relayout()

        page.escape_plan = escape_plan
        page.escape_do = escape_do
        page.recolour = recolour
        page.stop = stop
        page.set_row_hidden = set_row_hidden
        page.arrive = arrive
        st["ready"] = True
        open_page()
        set_row_hidden(getattr(win, "iga_fs", 0) == 2)
        arrive()

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

