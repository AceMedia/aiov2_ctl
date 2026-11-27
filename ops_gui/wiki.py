"""
The Wiki tab: the project's wiki, read offline from the checkout.

The pages are the same Markdown files the website's wiki is published from (docs/wiki beside this app), so the
two cannot drift apart. The tab is there when the installer's 'Local wiki' feature is on. The pictures are a
second feature: lossless WebP files downloaded once into the user's own folder. Without them a page shows a
line of words where each picture would be. Nothing is fetched while reading; a link to another site is handed
to the system's web browser, and only when it is clicked.

Made for the uConsole's trackball and D-pad. The list of pages on the left has tall rows, so each is an easy
target, and it is an ordinary list, so D-pad up and down move through it and the page follows. A (or Enter)
moves to the page, where up and down then scroll; B (or the trackball's right button, or Esc) goes back to the
list.

Everything above build() is pure, so it can be tested without a screen.
"""
import os
import re

SIDEBAR = "_Sidebar.md"
HOME = "Home"
ROW_HEIGHT, HEADING_HEIGHT = 44, 34            # a row is a touch-sized target; a heading is not a target at all
HINT_LIST = "Up and down: choose a page.  A or Enter: read it.  Left and right: other tabs."
HINT_PAGE = "Up and down: scroll.  B, Esc or the right button: back to the list of pages."
_PICTURE_LINK = re.compile(r"\[!\[([^\]]*)\]\(([^)\s]+)\)\]\(([^)\s]+)\)")      # a picture that links to itself
_PICTURE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
_HTML_IMG = re.compile(r"<img\b[^>]*>", re.I)
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_SIDE_LINK = re.compile(r"^\s*[-*]\s*\[([^\]]+)\]\(([^)\s]+)\)\s*$")
_SIDE_HEADING = re.compile(r"^\s*(?:\*\*(.+?)\*\*|#+\s+(.+?))\s*$")


def pages_folder():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "wiki")


def user_folder(home=None):
    """Where the installer keeps this user's wiki switch and downloaded pictures."""
    return os.path.join(home or os.path.expanduser("~"), ".local", "share", "aiov2_ctl", "wiki")


def enabled(pages=None, user=None, isfile=os.path.isfile):
    """True when the Local wiki feature is on for this user and the pages are here to show."""
    return isfile(os.path.join(user or user_folder(), "enabled")) and \
        isfile(os.path.join(pages or pages_folder(), HOME + ".md"))


def plain(name):
    """A page name for comparing: the wiki has one file named with a Unicode hyphen, links use a plain one."""
    return name.replace("‐", "-").replace("%E2%80%90", "-")


def page_file(name, names):
    """The file in `names` that holds the page `name`, or None."""
    want = plain(name).lower()
    for candidate in names:
        if candidate.endswith(".md") and plain(candidate[:-3]).lower() == want:
            return candidate
    return None


def title(name):
    return plain(name).replace("-", " ").replace("  ", " ").strip() or HOME


def link_kind(target):
    """What a clicked link is: ('web', address), ('picture', path), ('anchor', name) or ('page', name, anchor)."""
    target = (target or "").strip()
    if _SCHEME.match(target):
        return ("web", target)
    if target.startswith("#"):
        return ("anchor", target[1:])
    if target.startswith("img/"):
        return ("picture", target)
    name, _, anchor = target.partition("#")
    return ("page", name or HOME, anchor)


def sidebar_entries(text):
    """The sidebar as rows for a list: ('heading', words) and ('page', words, page name), in order. Anything
    that is neither a heading nor a link to a page is left out."""
    rows = []
    for line in (text or "").splitlines():
        link = _SIDE_LINK.match(line)
        if link:
            kind = link_kind(link.group(2))
            if kind[0] == "page":
                rows.append(("page", link.group(1).strip(), kind[1]))
            continue
        heading = _SIDE_HEADING.match(line)
        if heading:
            rows.append(("heading", (heading.group(1) or heading.group(2)).strip()))
    return rows


def row_of(entries, name):
    """The row of the list that shows the page `name`, or -1."""
    want = plain(name).lower()
    for i, row in enumerate(entries):
        if row[0] == "page" and plain(row[2]).lower() == want:
            return i
    return -1


def prepare(text, have_picture):
    """A page's Markdown made ready to show offline. A picture that is here is kept (without the link to
    itself, which would only open the same file); one that is not becomes a line saying what it shows. A
    picture that lives on another site is never fetched: it becomes a line saying so."""
    def shown(alt, path):
        if path.startswith("img/") and have_picture(path):
            return f"![{alt}]({path})"
        if path.startswith("img/"):
            return f"*[Picture: {alt}]*" if alt else ""
        return "*[A picture on the website]*"
    text = _PICTURE_LINK.sub(lambda m: shown(m.group(1), m.group(2)), text)
    text = _PICTURE.sub(lambda m: shown(m.group(1), m.group(2)), text)
    return _HTML_IMG.sub("*[A picture on the website]*", text)


def with_title(text, name):
    """The page with its name as a heading, unless it already opens with a heading of its own."""
    for line in text.splitlines():
        if line.strip():
            return text if line.lstrip().startswith("# ") else f"# {title(name)}\n\n{text}"
    return f"# {title(name)}\n"


def has_pictures(text):
    """True when a page links any picture of the wiki's own."""
    return any(m.group(2).startswith("img/") for m in _PICTURE.finditer(text))


def fitted(width, height, room):
    """(width, height) of a picture shown no wider than `room`, keeping its shape. Never made larger."""
    if width <= 0 or height <= 0 or room <= 0 or width <= room:
        return width, height
    return room, max(1, round(height * room / width))


def build(win):
    """The Wiki page: the list of pages on the left, the page on the right. Qt is imported here."""
    from PyQt6.QtCore import QSize, Qt, QUrl
    from PyQt6.QtGui import QColor, QDesktopServices, QImage, QImageReader, QTextCursor
    from PyQt6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QSplitter, QTextBrowser, QVBoxLayout, QWidget
    from . import style

    pages, user = pages_folder(), user_folder()
    state = {"page": None, "quiet": False}

    def tokens():
        return style.TOKENS["dark" if getattr(win, "theme", "dark") == "dark" else "light"]

    def names():
        try:
            return sorted(os.listdir(pages))
        except OSError:
            return []

    def read(file):
        try:
            with open(os.path.join(pages, file), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    # Qt reads WebP through a plug-in (Debian: qt6-image-formats-plugins), which the Wiki pictures feature
    # installs. Without it a picture cannot be drawn, so it is treated as not here.
    can_draw = b"webp" in [bytes(f) for f in QImageReader.supportedImageFormats()]

    def on_disk(path):
        return os.path.isfile(os.path.join(user, path))

    def have_picture(path):
        return can_draw and on_disk(path)

    box = QWidget()
    outer = QVBoxLayout(box)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(4)
    note = QLabel("")
    note.setWordWrap(True)
    note.hide()
    outer.addWidget(note)
    split = QSplitter()
    side, page = QListWidget(), QTextBrowser()
    page.setOpenLinks(False)
    page.setOpenExternalLinks(False)
    side.setUniformItemSizes(False)
    side.setWordWrap(False)
    side.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    side.setStyleSheet("QListWidget { font-size: 14pt; } QListWidget::item { padding-left: 14px; }")
    page.setStyleSheet("QTextBrowser { font-size: 12pt; }")
    split.addWidget(side)
    split.addWidget(page)
    split.setStretchFactor(0, 0)
    split.setStretchFactor(1, 1)
    split.setSizes([300, 900])
    outer.addWidget(split, 1)
    hint = QLabel(HINT_LIST)
    hint.setStyleSheet("font-size: 10pt;")
    outer.addWidget(hint)

    entries = sidebar_entries(read(SIDEBAR))
    for row in entries:
        item = QListWidgetItem(row[1].upper() if row[0] == "heading" else row[1])
        if row[0] == "heading":
            item.setFlags(Qt.ItemFlag.NoItemFlags)             # a label: not selectable, skipped by up and down
            item.setSizeHint(QSize(0, HEADING_HEIGHT))
            font = item.font()
            font.setBold(True)
            item.setFont(font)
        else:
            item.setSizeHint(QSize(0, ROW_HEIGHT))
        side.addItem(item)

    def spans(view, wanted):
        """[(position, length, format)] of the pieces of a page that `wanted` picks. Collected first and changed
        afterwards: changing a piece while walking the page moves the pieces under the walk."""
        found = []
        block = view.document().begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                fmt = frag.charFormat()
                if wanted(fmt):
                    found.append((frag.position(), frag.length(), fmt))
                it += 1
            block = block.next()
        return found

    def restyle(view, position, length, fmt):
        cur = QTextCursor(view.document())
        cur.setPosition(position)
        cur.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
        cur.setCharFormat(fmt)

    def tint_links():
        """Every link in the theme's second colour. Qt would draw them in its own dark blue, which nobody can
        read on the dark page, and neither the palette nor a style sheet changes that under the app's own
        style sheet, so each link is given its colour in the page itself."""
        ink = QColor(tokens()["accent2"])
        for position, length, fmt in spans(page, lambda f: f.isAnchor() and not f.isImageFormat()):
            fmt.setForeground(ink)
            fmt.setFontUnderline(False)
            restyle(page, position, length, fmt)

    def fit_pictures():
        room = max(200, page.viewport().width() - 28)
        for position, length, fmt in spans(page, lambda f: f.isImageFormat()):
            img = fmt.toImageFormat()
            picture = QImage(os.path.join(user, img.name()))
            if not picture.isNull():
                w, h = fitted(picture.width(), picture.height(), room)
                img.setWidth(w)
                img.setHeight(h)
                restyle(page, position, length, img)

    def go(name, anchor=""):
        file = page_file(name, names())
        if not file:
            note.setText(f"There is no page called {title(name)} in this copy of the wiki.")
            note.show()
            return
        text = read(file)
        page.document().setBaseUrl(QUrl.fromLocalFile(user + os.sep))
        page.setMarkdown(with_title(prepare(text, have_picture), file[:-3]))
        tint_links()
        fit_pictures()
        state["page"] = file
        row = row_of(entries, file[:-3])
        if row >= 0 and side.currentRow() != row:              # reached by a link in a page: the list follows
            state["quiet"] = True
            side.setCurrentRow(row)
            state["quiet"] = False
        if has_pictures(text) and not any(have_picture(m.group(2)) for m in _PICTURE.finditer(text)):
            if any(on_disk(m.group(2)) for m in _PICTURE.finditer(text)):
                note.setText("The pictures are here, but this system cannot draw WebP yet. Install the package "
                             "qt6-image-formats-plugins, or add \"Wiki pictures\" again in Settings > Features.")
            else:
                note.setText("The pictures are not installed. Add \"Wiki pictures\" in Settings > Features to "
                             "download them once; the pages read the same without them.")
            note.show()
        else:
            note.hide()
        if anchor:
            page.scrollToAnchor(anchor)
        else:
            page.verticalScrollBar().setValue(0)

    def chosen(row):
        if state["quiet"] or not 0 <= row < len(entries) or entries[row][0] != "page":
            return
        go(entries[row][2])

    def clicked(url):
        kind = link_kind(url.toString())
        if kind[0] == "web":
            QDesktopServices.openUrl(QUrl(kind[1]))             # the system's browser, because it was clicked
        elif kind[0] == "anchor":
            page.scrollToAnchor(kind[1])
        elif kind[0] == "page":
            go(kind[1], kind[2])

    def read_page():
        """A, or Enter on the list: move to the page, where up and down scroll."""
        page.setFocus()
        hint.setText(HINT_PAGE)

    def to_list():
        """B, Esc or the right button, while reading: back to the list. False when already there."""
        if not page.hasFocus():
            return False
        side.setFocus()
        hint.setText(HINT_LIST)
        return True

    def main_action():
        if page.hasFocus():
            to_list()
        else:
            read_page()

    side.currentRowChanged.connect(chosen)
    side.itemActivated.connect(lambda _item: read_page())
    page.anchorClicked.connect(clicked)
    first = row_of(entries, HOME)
    if first >= 0:
        side.setCurrentRow(first)
    if state["page"] is None:
        go(HOME)
    box.go, box.current = go, (lambda: state["page"])
    box.main_action, box.back, box.side, box.page = main_action, to_list, side, page
    return box

