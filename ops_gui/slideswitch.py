"""
The one slide switch used everywhere: the Overview's rail cards, every panel's own switches, and the Home screen's
rack. It is drawn on a 2 pixel grid: a track with stepped round corners and a round pixel knob, green with the knob
on the right when on, dark with the knob on the left when off.

`cells` is pure (no Qt), so the look can be tested anywhere; whoever draws it just fills each cell it returns.
"""
UNIT = 2                          # screen pixels per grid cell
GRID_W, GRID_H = 38, 18           # the switch in grid cells
WIDTH, HEIGHT = GRID_W * UNIT, GRID_H * UNIT
KNOB_LEFT, KNOB_TRAVEL, KNOB_TOP = 3, 20, 3

TRACK = ["...###", ".##ooo", ".#oooo", "#ooooo", "#ooooo", "#ooooo"]
KNOB = ["...######...", ".##########.", ".##########.", "############", "############", "############",
        "############", "############", "############", ".#########s.", ".########ss.", "...ssssss..."]

ON, OFF, BUSY = ("#2e7d32", "#1b5e20"), ("#2f343c", "#4a505a"), ("#a86b00", "#7a4e00")     # (track, its edge)
MARK_ON, MARK_OFF = "#ffffff", "#8b93a1"


def knob_x(pos):
    """The knob's left grid column for a position from 0 (off) to 1 (on)."""
    return KNOB_LEFT + round(min(1.0, max(0.0, pos)) * KNOB_TRAVEL)


def cells(on, pos=None, down=False, enabled=True, busy=False):
    """[(x, y, w, h, '#rrggbb')] in grid cells, in the order to draw them. `pos` is where the knob is on its way
    (0 to 1) for an animated switch; left out, it sits at its end. `busy` is a switch waiting on its rail: amber."""
    pos = (1.0 if on else 0.0) if pos is None else pos
    track, line = BUSY if busy else ON if on else OFF
    face, shade = "#f2eef5", "#b9b0c4"
    if down:
        face = shade = "#d9d2e0"
    elif not enabled:
        face = shade = "#8b93a1"
    out = []
    for y in range(GRID_H):
        for x in range(GRID_W):
            ch = TRACK[min(y, GRID_H - 1 - y, 5)][min(x, GRID_W - 1 - x, 5)]
            if ch != ".":
                out.append((x, y, 1, 1, line if ch == "#" else track))
    if on:                                                            # a white I where the knob has left
        out.append((12, 6, 1, 6, MARK_ON))
    else:                                                             # an O ring on the right
        out += [(23, 6, 4, 1, MARK_OFF), (23, 11, 4, 1, MARK_OFF), (23, 6, 1, 6, MARK_OFF), (26, 6, 1, 6, MARK_OFF)]
    kx = knob_x(pos)
    for ky, row in enumerate(KNOB):
        for kcol, ch in enumerate(row):
            if ch != ".":
                out.append((kx + kcol, KNOB_TOP + ky, 1, 1, shade if ch == "s" else face))
    return out
