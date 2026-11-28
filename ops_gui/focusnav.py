"""
Moving round the whole app with the D-pad or the arrow keys.

The rule a person can hold in their head: the highlight goes where the arrow points. Up from the top of a page
reaches the row of tabs, left and right walk along it, down goes back into the page. Inside a page the arrows
move between the controls; a control that has something of its own to do with an arrow (a list with more rows,
a page of text with more to scroll, a slider, a box being typed in, a row of sub-tabs) keeps that arrow until
it has no more use for it, and then the highlight moves on. A presses whatever is highlighted; B steps back.

Everything here is pure geometry and rules, so it can be tested without a screen. A rectangle is (x, y, width,
height) in any one set of coordinates.
"""

DIRECTIONS = ("up", "down", "left", "right")
SKEW = 3                 # being off to the side costs this many times what being further along does


def centre(rect):
    x, y, w, h = rect
    return x + w / 2, y + h / 2


def _gap(a_lo, a_hi, b_lo, b_hi):
    """How far apart two spans are along one axis: 0 when they overlap."""
    return max(0, b_lo - a_hi, a_lo - b_hi)


def distance(current, other, direction):
    """How far `other` is from `current` going `direction`, or None when it does not lie that way. Lower is
    nearer. A control straight ahead beats a closer one that is off to the side."""
    cx, cy, cw, ch = current
    ox, oy, ow, oh = other
    mx, my = centre(current)
    nx, ny = centre(other)
    if direction == "down":
        if not (ny > my and oy + oh > cy + ch):
            return None
        along, aside = max(0, oy - (cy + ch)), _gap(cx, cx + cw, ox, ox + ow)
    elif direction == "up":
        if not (ny < my and oy < cy):
            return None
        along, aside = max(0, cy - (oy + oh)), _gap(cx, cx + cw, ox, ox + ow)
    elif direction == "right":
        if not (nx > mx and ox + ow > cx + cw):
            return None
        along, aside = max(0, ox - (cx + cw)), _gap(cy, cy + ch, oy, oy + oh)
    elif direction == "left":
        if not (nx < mx and ox < cx):
            return None
        along, aside = max(0, cx - (ox + ow)), _gap(cy, cy + ch, oy, oy + oh)
    else:
        return None
    # the last term only breaks ties: of two equally placed controls, the one whose middle is nearer wins
    return along + SKEW * aside + (abs(nx - mx) + abs(ny - my)) / 10000.0


def next_in_direction(current, candidates, direction):
    """The key of the candidate the highlight moves to from `current`, or None when nothing lies that way.
    `candidates` is [(key, rect)], without the current control."""
    best, best_score = None, None
    for key, rect in candidates:
        score = distance(current, rect, direction)
        if score is not None and (best_score is None or score < best_score):
            best, best_score = key, score
    return best


def first_in_reading_order(candidates):
    """The key of the control a person would read first: the top row, and the leftmost of that row. Controls
    whose tops are within half a control's height of each other count as one row."""
    if not candidates:
        return None
    top = min(rect[1] for _, rect in candidates)
    band = min(rect[3] for _, rect in candidates) / 2.0
    row = [(key, rect) for key, rect in candidates if rect[1] - top <= band]
    return min(row, key=lambda kr: (kr[1][0], kr[1][1]))[0]


def passes_through(kind, direction, at_start=True, at_end=True):
    """True when the highlighted control should have the arrow itself.

    kind is 'list' (rows to move through), 'text' (a page to scroll), 'line' (a box to type in), 'slider',
    'spin' (a number box), 'tabs' (a row of tabs) or anything else. `at_start` and `at_end` say whether a list,
    a page or a row of tabs is already at its first or last, which is when the arrow is free to leave it."""
    if kind in ("list", "text"):
        return (direction == "up" and not at_start) or (direction == "down" and not at_end)
    if kind == "tabs":                  # along the row; past its first or last tab the highlight moves on
        return (direction == "left" and not at_start) or (direction == "right" and not at_end)
    if kind in ("line", "slider"):
        return direction in ("left", "right")
    if kind == "spin":
        return direction in ("up", "down")
    return False


def custom_clickable(cls, package="ops_gui"):
    """True for one of the app's own hand-drawn controls that answers a click: a class from this package that
    handles the mouse itself. They take no part in Qt's own focus, so the D-pad has to be told about them."""
    for klass in getattr(cls, "__mro__", ()):
        if (getattr(klass, "__module__", "") or "").split(".")[0] == package and \
                any(name in vars(klass) for name in ("mousePressEvent", "mouseReleaseEvent")):
            return True
    return False


def back_step(drawer_open, reading, in_page, full_screen, first_tab, web=None):
    """What B, Esc or the trackball's right button does, one level at a time: 'drawer' (close it), 'reading'
    (a page of the wiki back to its list), 'web' (the IGA tab's own step, `web`, from iga.escape_step: leave
    find, stop loading, page to address bar, or address bar to the row of tabs with IGA still selected), 'tabs'
    (out of the page to the row of tabs), 'window' (leave full screen), 'first' (to the first tab) or None when
    there is nowhere further back."""
    if drawer_open:
        return "drawer"
    if web:
        return "web"
    if reading:
        return "reading"
    if in_page:
        return "tabs"
    if full_screen:
        return "window"
    return None if first_tab else "first"



def typing_keeps(kind, direction, from_keyboard, has_text):
    """Whether a box that is typed in keeps an up or down arrow instead of letting the highlight leave it.
    Once the highlight has left a box, letters are shortcuts again (g, m and d switch rails, q quits), so a
    stray arrow key in the middle of typing must not move it: a box with something typed in it keeps the
    keyboard's up and down. The terminal's box always keeps them, since there they recall earlier commands.
    The gamepad's D-pad is not a typing key, so it can always move on."""
    if direction not in ("up", "down") or not from_keyboard:
        return False
    if kind == "history":
        return True
    return kind == "line" and bool(has_text)
