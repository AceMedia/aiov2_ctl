"""
A simple 3D view of the room, as the signals see it. Pure geometry so it can be tested without a screen.

The uConsole sits at the centre. Each followed sender is a post standing on the floor: nearer the middle when its
signal is stronger, taller and hotter the more its signal is being disturbed. That shows which signal is being
disturbed, not where a person is: signal strength has no direction and cannot see a body. Where the posts stand
around the circle is arbitrary (evenly spaced, in name order); only the distance from the middle means anything.

Because the posts stand at made up angles, no part of the floor is a real place. So the floor is never shaded: a
warm patch of tiles would read as "something is here", which the radio cannot know. The floor is only a plain grid
to stand things on.

The line from the uConsole to a post is that link. It is the one honest place to show movement: it thickens and
gets cross bars while that link's signal is disturbed, keeps a fading colour for a minute after, and is drawn
dashed when too few readings arrive to say anything. Several lines lighting one after another reads as something
passing through, without the picture ever putting a mark at a spot in the room.
"""
import math

FLOOR = 1.3                       # the floor is a square from -FLOOR to FLOOR
MAX_HEIGHT = 1.1
CELLS = 8                         # grid lines on the floor, only so it reads as a floor
LEVELS = (3.0, 6.0, 10.0, 16.0)   # dB at which a link steps up a level
HEAT = ((0.0, "#3d3a3a"), (5.0, "#8a3a2a"), (10.0, "#e95420"), (18.0, "#d70a53"))
HEAT_LIGHT = ((0.0, "#bdb8b4"), (5.0, "#d99a84"), (10.0, "#e95420"), (18.0, "#d70a53"))    # the same ramp from a pale grey
TRAIL_SECONDS = 60.0              # how long a link keeps some colour after it was disturbed
WIDTHS = (1, 2, 3, 5, 7)          # pixels wide for a link's line at each level of disturbance
RIPPLE_STEPS = 4                  # readings a cross bar takes to move one gap along the link
YAWS = tuple(math.radians(45 + 90 * k) for k in range(4))
SIN_PITCH, COS_PITCH = 0.5, math.sqrt(0.75)       # 2:1 dimetric, so floor lines make clean pixel steps


def radius_of(dbm):
    """Distance from the centre for a signal strength: strong is near. Unknown signals sit mid way."""
    if dbm is None:
        return 0.65 * FLOOR
    return max(0.3, min(1.0, (-dbm - 35.0) / 55.0)) * FLOOR


def layout(nodes):
    """[{'name', 'db', 'dbm', 'label'}] to posts with x, z, height. Spread evenly round the circle in name order,
    so a sender keeps its place however loud it is; the bearing means nothing, only the distance does."""
    ordered = sorted(nodes, key=lambda n: str(n["name"]))
    out = []
    for i, n in enumerate(ordered):
        a = 2 * math.pi * i / max(1, len(ordered)) + 0.4
        r = radius_of(n.get("dbm"))
        db = max(0.0, n.get("db") or 0.0)
        out.append({"name": n["name"], "label": n.get("label") or n["name"], "db": db,
                    "sure": bool(n.get("sure", True)), "trail": max(0.0, n.get("trail") or 0.0),
                    "x": r * math.cos(a), "z": r * math.sin(a), "height": min(1.0, db / 20.0) * MAX_HEIGHT + 0.06})
    return out


def snap_yaw(yaw):
    """The nearest of the four tidy views (45, 135, 225 and 315 degrees)."""
    return min(YAWS, key=lambda y: abs(math.atan2(math.sin(yaw - y), math.cos(yaw - y))))


def project(x, y, z, yaw=YAWS[0], scale=100.0, cx=0.0, cy=0.0):
    """Screen x, y and depth for a point (y is up). Orthographic 2:1 dimetric. A bigger depth is nearer the viewer."""
    c, s = math.cos(yaw), math.sin(yaw)
    xr, zr = x * c - z * s, x * s + z * c
    return cx + xr * scale, cy + zr * scale * SIN_PITCH - y * scale * COS_PITCH, zr


def fit(width, height, yaw=YAWS[0], margin=14, tallest=MAX_HEIGHT):
    """(scale, cx, cy) that make the floor and the tallest post fill the widget."""
    pts = [project(x, 0, z, yaw) for x in (-FLOOR, FLOOR) for z in (-FLOOR, FLOOR)]
    pts += [project(0, tallest + 0.3, 0, yaw), project(0, 0, 0, yaw)]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    sw, sh = max(xs) - min(xs), max(ys) - min(ys)
    scale = 100.0 * min((width - 2 * margin) / sw, (height - 2 * margin - 16) / sh)
    cx = width / 2 - (max(xs) + min(xs)) / 2 * scale / 100.0
    cy = (height - 16) / 2 - (max(ys) + min(ys)) / 2 * scale / 100.0
    return scale, cx, cy


def level(db):
    """0 to 4: how many of the LEVELS a value reaches."""
    return sum(1 for edge in LEVELS if (db or 0.0) >= edge)


def heat_colour(db, mode="dark"):
    """A colour along the grey, rust, orange and Debian red ramp. The light theme starts from a pale grey, so a
    still post does not sit on a light floor as a near black lump."""
    from .pixel import blend
    ramp = HEAT_LIGHT if mode == "light" else HEAT
    db = max(0.0, db or 0.0)
    if db <= ramp[0][0]:
        return ramp[0][1]
    for (a, ca), (b, cb) in zip(ramp, ramp[1:]):
        if db <= b:
            return blend(ca, cb, (db - a) / (b - a))
    return ramp[-1][1]


def colours(mode):
    """Every fixed colour the room is drawn with, taken from the app's theme so the floor is a light slab on the
    light theme's white card and a dark one on the dark theme."""
    from . import style
    from .pixel import blend
    m = "light" if mode == "light" else "dark"
    t = style.TOKENS[m]
    floor = t["bg"] if m == "light" else t["panel"]
    return {"floor": floor, "grid": blend(floor, t["text"], 0.14), "text": t["text"], "label_bg": t["card"],
            "label_edge": t["border"], "caption": style.MUTED[m], "device": t["accent"],
            "edge": blend(floor, t["text"], 0.75) if m == "light" else "#111111",
            # a link that is known to be quiet is a calm green line; one that is not known is muted and dashed
            "quiet": blend(floor, style.good_colour(m), 0.7), "unsure": style.MUTED[m]}


def trail_db(samples, now, fade=TRAIL_SECONDS):
    """What is left of a link's recent disturbance: each past reading [(time, dB)] counts for less the older it is,
    and for nothing after `fade` seconds. Never less than the reading taken right now."""
    best = 0.0
    for t, db in samples:
        age = now - t
        if 0.0 <= age < fade:
            best = max(best, max(0.0, db or 0.0) * (1.0 - age / fade))
    return best


def link_look(db, trail=0.0, sure=True):
    """How to draw the line from the uConsole to a post.

    'unsure': too few readings (or still learning), so it is dashed and dim rather than shown as confidently quiet.
    'quiet': a thin calm line. 'trail': quiet now but disturbed in the last minute, thin and still coloured.
    'active': disturbed now, wider the stronger it is. `shown` is the dB to colour it by."""
    if not sure:
        return {"kind": "unsure", "width": 1, "dashed": True, "shown": 0.0}
    db = max(0.0, db or 0.0)
    shown = max(db, trail or 0.0)
    lv = level(db)
    if lv:
        return {"kind": "active", "width": WIDTHS[lv], "dashed": False, "shown": shown}
    if level(shown):
        return {"kind": "trail", "width": 2, "dashed": False, "shown": shown}
    return {"kind": "quiet", "width": 1, "dashed": False, "shown": shown}


def ripple_bars(post, tick, half=0.07):
    """Short bars across a disturbed link, as [((x, z), (x, z))] on the floor. They are spread evenly along the
    whole link, more of them the stronger the disturbance, and step towards the uConsole (the way the signal
    travels) once for each new reading: `tick` is a count of readings, so nothing moves between readings and no
    timer is needed. They mark the link as a whole, never one spot on it."""
    lv = level(post.get("db"))
    length = math.hypot(post["x"], post["z"])
    if not lv or not post.get("sure", True) or length == 0:
        return []
    count = 2 + lv
    nx, nz = -post["z"] / length, post["x"] / length          # across the link, on the floor
    phase = (tick % RIPPLE_STEPS) / RIPPLE_STEPS
    bars = []
    for k in range(count):
        f = (k + 1 - phase) / count
        mx, mz = post["x"] * f, post["z"] * f
        bars.append(((mx - nx * half, mz - nz * half), (mx + nx * half, mz + nz * half)))
    return bars


def post_label(label, db, sure=True):
    """The words on a post: its name and motion score, or a question mark when the score cannot be trusted."""
    return f"{str(label)[-14:]} {db:+.0f}" if sure else f"{str(label)[-14:]} ?"


def strip_colour(code, mode="dark"):
    """The colour of one cell of a link's history strip: None is no reading at all, -1 not sure, then quiet,
    subtle and strong."""
    col = colours(mode)
    if code is None:
        return col["floor"]
    if code < 0:
        return col["unsure"]
    return (col["quiet"], heat_colour(8.0, mode), heat_colour(18.0, mode))[min(2, code)]


def avoid_overlaps(labels, height, gap=12):
    """Push label boxes apart by screen y. labels: [[x, y]] sorted by caller; returns adjusted y values."""
    ys = []
    for x, y in sorted(labels, key=lambda l: l[1]):
        if ys and y - ys[-1] < gap:
            y = ys[-1] + gap
        ys.append(min(y, height - 4))
    return ys