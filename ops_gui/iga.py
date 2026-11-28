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
    """What Escape does on the IGA tab, one level at a time. `where` is 'page' (a web page has the keyboard),
    'address' (the address bar or another IGA control has it) or anything else. Returns 'find' (close the find
    bar), 'stop' (stop loading), 'address' (out of the page to the address bar), 'tabs' (out of IGA to the app's
    row of tabs), or None to leave Escape to the app."""
    if finding:
        return "find"
    if where == "page":
        return "stop" if loading else "address"
    if where == "address":
        return "tabs"
    return None


def page_keeps(escape, function_key, ctrl, alt, meta):
    """Whether a key pressed while a web page has the keyboard belongs to the page rather than to one of the
    app's own shortcuts: letters, numbers and Tab do; Escape, F-keys and Ctrl or Alt combinations do not."""
    return not (escape or function_key or ctrl or alt or meta)


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
    last one leaves one blank page."""

    def __init__(self):
        self.order, self.current, self._next = [], None, 1
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
            f"for. Ctrl+L goes to the address bar; Escape twice goes back to the app's tabs.</p>")
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

