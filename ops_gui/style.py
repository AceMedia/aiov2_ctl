"""
One stylesheet for the whole native app, light and dark.

Pure text, so it can be tested without a screen. Colours live in TOKENS and nowhere else.
"""
import re

FONT_FAMILIES = ["Inter", "Cantarell", "Noto Sans", "DejaVu Sans"]
BASE_POINT_SIZE = 12

TOKENS = {
    "dark": {
        "bg": "#0f1114", "panel": "#171a1f", "card": "#1f2329", "row": "#252a31", "border": "#2f343c",
        "text": "#e6e8eb", "muted": "#8b93a1", "tab_off": "#0a0c0e", "tab_hover": "#262b32",
        "btn": "#262b32", "accent": "#d70a53", "accent2": "#e95420", "field": "#101216",
    },
    "light": {
        "bg": "#e4e8ee", "panel": "#f5f6f8", "card": "#ffffff", "row": "#f8f9fb", "border": "#d5d9e0",
        "text": "#1b1f24", "muted": "#6b7280", "tab_off": "#d3d8e0", "tab_hover": "#eceff3",
        "btn": "#ffffff", "accent": "#d70a53", "accent2": "#e95420", "field": "#ffffff",
    },
}


PIXEL_FAMILY = {16: "Spleen 8x16", 24: "Spleen 12x24", 32: "Spleen 16x32", 64: "Spleen 32x64"}
_PT = re.compile(r"font-size:\s*(\d+(?:\.\d+)?)pt")


def pixel_size_for_pt(pt):
    """The Spleen size that stands in for a point size: only 16, 24, 32 and 64 are crisp."""
    return 64 if pt >= 30 else 32 if pt >= 18 else 24 if pt >= 14 else 16


def pixel_font(px):
    """A Spleen font drawn with no smoothing and no hinting, exactly as the Home screen draws it."""
    from PyQt6.QtGui import QFont
    f = QFont(PIXEL_FAMILY.get(px, "Spleen 12x24"))
    f.setPixelSize(px)
    f.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


def crisp_fonts(root):
    """Give every widget under `root` that asks for the pixel font in its style sheet the unsmoothed font,
    and the tab bars theirs. Qt smooths text by default, which makes the pixel font look soft."""
    from PyQt6.QtWidgets import QTabBar, QWidget
    for w in root.findChildren(QWidget):
        sheet = w.styleSheet()
        m = re.search(r"font-size:\s*(\d+)px", sheet) if "Spleen" in sheet else None
        if m and int(m.group(1)) in PIXEL_FAMILY:
            w.setFont(pixel_font(int(m.group(1))))
    from PyQt6.QtWidgets import QAbstractItemView, QCheckBox, QComboBox, QHeaderView, QLineEdit, QPushButton
    for kind in (QPushButton, QComboBox, QLineEdit, QCheckBox, QAbstractItemView, QHeaderView):
        for w in root.findChildren(kind):
            if w.objectName() != "iconbtn":
                w.setFont(pixel_font(16))
    from PyQt6.QtWidgets import QAbstractItemView, QCheckBox, QComboBox, QHeaderView, QLineEdit, QPushButton
    for kind in (QPushButton, QComboBox, QLineEdit, QCheckBox, QAbstractItemView, QHeaderView):
        for w in root.findChildren(kind):
            if w.objectName() != "iconbtn":
                w.setFont(pixel_font(16))
    from PyQt6.QtWidgets import QLabel
    for lab in root.findChildren(QLabel):
        if lab.objectName() == "tag":                       # a section tag, like RACK and VITALS on the Home screen
            lab.setFont(pixel_font(16))
        if lab.objectName() == "title":                    # card headlines: Spleen with a second strike, so they stand out
            f = pixel_font(24 if lab.font().pixelSize() < 0 or True else 16)
            f.setBold(True)
            lab.setFont(f)
    for bar in root.findChildren(QTabBar):
        sub = bar.parent() is not None and bar.parent().objectName() == "subtabs"
        bar.setFont(pixel_font(16 if sub else 24))


def pixelise_sheet(sheet, force=False):
    """Turn a heading's inline style into the pixel font: anything bold or 14pt and up, or any style if `force`.
    Body text (small and not bold) is left alone. Spleen has one weight, so bold is dropped."""
    m = _PT.search(sheet or "")
    if not m or "Spleen" in sheet:
        return sheet
    pt = float(m.group(1))
    if not (force or "bold" in sheet or pt >= 14):
        return sheet
    px = pixel_size_for_pt(pt)
    out = _PT.sub(f'font-family: "{PIXEL_FAMILY[px]}"; font-size: {px}px', sheet, count=1)
    return out.replace("font-weight: bold;", "").replace("font-weight: bold", "")


# The grey of helper text, written into dozens of style sheets as the dark theme's value. On the light theme's
# white cards that grey is too faint to read (about 2.7 to 1), so it is swapped for a darker one there.
MUTED = {"dark": "#9e9e9e", "light": "#5f6368"}
GOOD = {"dark": "#62e06b", "light": "#1f8f2e"}       # "on" and "present": bright on dark, deep on light


def muted_sheet(sheet, mode):
    """The style sheet with its helper-text grey set to the one for this theme, whichever it had before."""
    want = MUTED["light" if mode == "light" else "dark"]
    out = sheet or ""
    for colour in MUTED.values():
        if colour != want:
            out = out.replace(colour, want)
    return out


def good_colour(mode):
    return GOOD["light" if mode == "light" else "dark"]


def is_caption(sheet, text):
    """A short muted heading such as 'Nodes heard' that should also be in the pixel font."""
    m = _PT.search(sheet or "")
    muted = any(colour[1:] in (sheet or "") for colour in MUTED.values())
    return bool(m) and float(m.group(1)) >= 13 and muted and len(text or "") <= 24


def set_feature(hex_colour):
    """Make this the feature colour (borders, the open tab, pressed buttons, highlights) in every theme. Returns the
    colour actually used: a very dark pick is lightened so the interface stays readable."""
    from . import colour
    hx = colour.readable(colour.valid_hex(hex_colour) or colour.DEFAULT)
    for mode in ("dark", "light"):
        TOKENS[mode]["accent"] = hx
        TOKENS[mode]["accent2"] = colour.secondary(hx)
    return hx


def stylesheet(mode, skin=None):
    """The stylesheet for a theme. With `skin` (name -> image path, see skin.py) cards, buttons, fields and tabs
    get the pixel cut corners; without it they fall back to plain rounded boxes."""
    t = TOKENS["dark" if mode == "dark" else "light"]
    if skin:
        from . import skin as skinmod
        def img(name):
            return "background: transparent; " + skinmod.border_image(name, skin)
        card, row, tile = img("card"), img("row"), img("tile")
        button, hover, press = img("button"), img("button_hover"), img("button_press")
        field, pane, drawer = img("field"), img("pane"), img("drawer")
        tab_off, tab_hover, tab_on = img("tab_off"), img("tab_hover"), img("tab_on")
        button_pad, field_pad = "padding: 0px 8px;", "padding: 0px 6px;"
        # left padding is bigger than the right: Qt puts a fixed gap between the icon and the text that the
        # right padding does not need to repeat, so matching the two literally would leave more space after the
        # icon than before it.
        tab_pad = "padding: 0px 6px 0px 7px; margin: 6px -4px 0px 0px;"
        # No tab overlaps the pane: both are glass, and where two sheets of glass overlap they look more solid
        # than either. The selected tab ends exactly where the pane starts, and the pane leaves its top border
        # out under it (foldertabs.py), so the two are one surface with no line between them.
        tab_on_pad = "padding: 6px 6px 0px 7px; margin: 0px -4px 0px 0px;"
    else:
        card = f"background: {t['card']}; border: 1px solid {t['border']}; border-radius: 12px;"
        row = card
        tile = f"background: {t['row']}; border: 1px solid {t['border']}; border-radius: 10px;"
        button = f"background: {t['btn']}; border: 1px solid {t['border']}; border-radius: 8px;"
        hover = f"background: {t['tab_hover']}; border: 1px solid {t['accent']}; border-radius: 8px;"
        press = f"background: {t['accent']}; border: 1px solid {t['accent']}; border-radius: 8px;"
        field = f"background: {t['field']}; border: 1px solid {t['border']}; border-radius: 8px;"
        pane = (f"border: 1px solid {t['border']}; border-radius: 0px 12px 12px 12px; background: {t['panel']};")
        drawer = f"background: {t['panel']}; border: 2px solid {t['accent']};"
        tab_off = (f"background: {t['tab_off']}; border: 1px solid {t['border']}; border-bottom: none; "
                   "border-top-left-radius: 10px; border-top-right-radius: 10px;")
        tab_hover = f"background: {t['tab_hover']};"
        tab_on = (f"background: {t['panel']}; border-top: 3px solid {t['accent']}; "
                  f"border-left-color: {t['border']}; border-right-color: {t['border']};")
        button_pad, field_pad = "padding: 6px 14px;", "padding: 6px 12px;"
        tab_pad = "padding: 8px 10px; margin-right: 4px; margin-top: 9px;"
        tab_on_pad = "padding-top: 14px; padding-bottom: 13px; margin-top: 0px; margin-bottom: -2px;"
    no_img = "border-image: none;" if skin else ""
    pixel_tab = 'font-family: "Spleen 12x24"; font-size: 24px;'
    pixel_sub = 'font-family: "Spleen 8x16"; font-size: 16px;'
    return f"""
* {{ outline: none; }}
#window {{ background: transparent; }}
QLabel {{ background: transparent; color: {t['text']}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
#card {{ {card} }}
#tile {{ {tile} }}
#row {{ {row} }}
#plain {{ background: transparent; border: none; }}
#drawer {{ {drawer} }}
#drawer QPlainTextEdit, #drawer QLineEdit {{ background: {t['field']}; }}

QTabWidget::pane {{ {pane} top: 0px; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ {tab_off} color: {t['muted']}; {tab_pad} {pixel_tab} }}
QTabBar::tab:hover {{ {tab_hover} color: {t['text']}; }}
QTabBar::tab:selected {{ {tab_on} color: {t['text']}; {tab_on_pad} }}
QTabBar::tab:last {{ margin-right: 0px; }}
QTabBar::scroller {{ width: 72px; }}
#subtabs QTabBar::tab {{ {pixel_sub} padding: 0px 8px 0px 2px; margin: 4px -6px 0px 0px; }}
#subtabs QTabBar::tab:selected {{ padding: 3px 8px 1px 2px; margin: 0px -6px -2px 0px; }}

#statusbar {{ border: none; background: transparent; }}
QTableWidget::item {{ border-bottom: 1px dotted {t['border']}; padding: 0px 4px; }}
QTableWidget::item:selected {{ background: {t['accent']}; color: #ffffff; }}
#tag {{ background: {t['accent']}; border: none; {no_img} color: #ffffff; padding: 0px 6px; margin: 0px 0px 4px 0px; font-family: "Spleen 8x16"; font-size: 16px; }}
QHeaderView::section {{ color: {t['accent2']}; }}
QTabWidget#subtabs::pane {{ {'border-image: none;' if skin else ''} border: none; background: transparent; top: -1px; }}
QPushButton:focus, QComboBox:focus, QLineEdit:focus, QTableWidget:focus {{ border: 2px solid {t['accent2']}; }}
QPushButton, QComboBox, QLineEdit {{ min-height: 28px; }}
QPushButton#iconbtn {{ min-height: 0px; }}
QPushButton, QComboBox, QLineEdit, QCheckBox, QHeaderView::section, QTableWidget, QListWidget {{ font-family: "Spleen 8x16"; font-size: 16px; }}
QPushButton, QComboBox, QLineEdit {{ min-height: 28px; }}
QPushButton#iconbtn {{ min-height: 0px; }}
QPushButton, QComboBox, QLineEdit, QCheckBox, QHeaderView::section, QTableWidget, QListWidget {{ font-family: "Spleen 8x16"; font-size: 16px; }}

QPushButton {{ {button} color: {t['text']}; {button_pad} }}
QPushButton:hover {{ {hover} }}
QPushButton:pressed {{ {press} color: white; }}
QPushButton:checked {{ {press} color: white; }}
QPushButton:disabled {{ color: {t['muted']}; }}
QPushButton#iconbtn {{ background: transparent; border: none; {no_img} padding: 0px; }}
QPushButton#iconbtn:hover {{ background: {t['tab_hover']}; border: none; {no_img} }}

QCheckBox {{ color: {t['text']}; spacing: 10px; }}
QCheckBox::indicator {{ width: 22px; height: 22px; border: 2px solid {t['muted']}; border-radius: 0px;
                        background: {t['field']}; }}
QCheckBox::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']}; }}

QLineEdit, QComboBox, QAbstractSpinBox {{ {field} color: {t['text']}; {field_pad} }}
QComboBox QAbstractItemView {{ background: {t['card']}; color: {t['text']};
                               selection-background-color: {t['accent']}; border: 1px solid {t['border']}; }}
QPlainTextEdit, QTextBrowser, QTableWidget, QListWidget {{ {field} color: {t['text']};
                                placeholder-text-color: {t['muted']}; padding: 0px; }}
QHeaderView::section {{ background: {t['card']}; color: {t['muted']}; border: none; padding: 3px 6px; }}

QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t['border']}; border-radius: 0px; min-height: 36px; }}
QScrollBar::handle:vertical:hover {{ background: {t['muted']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0px; width: 0px; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QToolTip {{ background: {t['card']}; color: {t['text']}; border: 1px solid {t['border']}; padding: 4px; }}
QMessageBox {{ background: {t['card']}; }}
"""


def check(sheet):
    """Names of problems in a stylesheet: unbalanced braces or colours that are not hex."""
    problems = []
    if sheet.count("{") != sheet.count("}"):
        problems.append("unbalanced braces")
    for value in re.findall(r"#[0-9A-Za-z]+(?=[;\s}])", sheet):
        if value.startswith("#") and re.fullmatch(r"#[0-9a-fA-F]{3,8}", value) is None and \
                not re.fullmatch(r"#[A-Za-z]+", value):
            problems.append(f"bad colour {value}")
    return problems
