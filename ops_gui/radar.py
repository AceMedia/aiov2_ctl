"""
The geometry of the two flat views of the Sense page: the radar and the view from above. Pure, so it can be
tested without a screen.

Like the 3D room they show which links are being disturbed and how strong each signal is, never where anything
is: signal strength from one receive chain has no direction.

The radar gives each sender an arc, a piece of a ring. How far the arc is from the middle is the signal strength
(strong is near). Which part of the circle it takes is only an even share in name order, so a sender keeps its own
place; an arc is drawn rather than a dot so that it cannot be taken for a spot. A single sender gets the whole
ring. Disturbance is drawn on the arc itself, with the same steps, fading trail and dashed "not sure" look as the
lines of the room (room3d.link_look decides all three views).

There is no sweeping beam. A beam that turns and "finds" something says the radio is looking in a direction, which
it is not, and it also needed a timer of its own. The picture now changes only when a reading arrives.

The view from above is the room's own layout looked at straight down: the same slots and the same link lines.
"""
import math

from . import room3d

TAU = 2 * math.pi
RINGS = (0.25, 0.5, 0.75, 1.0)
SHARE = 0.8                       # how much of its share of the circle an arc fills; the rest is the gap between arcs
TICK_HALF = (4, 6)                # half length in pixels of the marks across a disturbed arc, on even and odd readings


def segments(nodes):
    """[{'name', 'label', 'db', 'sure', 'trail', 'r', 'a0', 'a1', 'mid'}]: one arc for each sender, in name order.
    r runs from 0.3 (strong) to 1 (weak). a0 to a1 is an even share of the circle and means nothing; one sender
    alone gets the whole ring."""
    ordered = sorted(nodes, key=lambda n: str(n["name"]))
    count = len(ordered)
    span = TAU if count == 1 else TAU / max(1, count) * SHARE
    out = []
    for i, n in enumerate(ordered):
        mid = TAU * i / max(1, count) - math.pi / 2
        out.append({"name": n["name"], "label": n.get("label") or n["name"], "db": max(0.0, n.get("db") or 0.0),
                    "sure": bool(n.get("sure", True)), "trail": max(0.0, n.get("trail") or 0.0),
                    "r": room3d.radius_of(n.get("dbm")) / room3d.FLOOR,
                    "a0": mid - span / 2, "a1": mid + span / 2, "mid": mid})
    return out


def ring_points(cx, cy, radius, steps=72):
    """Integer points round a circle, for drawing a ring without smoothing."""
    return arc_points(cx, cy, radius, 0.0, TAU, steps)


def arc_points(cx, cy, radius, a0, a1, steps=None):
    """Integer points along a piece of a circle from a0 to a1, a point about every five degrees."""
    if steps is None:
        steps = max(2, int(math.ceil(abs(a1 - a0) / (TAU / 72))))
    return [(round(cx + radius * math.cos(a0 + (a1 - a0) * i / steps)),
             round(cy + radius * math.sin(a0 + (a1 - a0) * i / steps))) for i in range(steps + 1)]


def arc_ticks(seg, tick, cx, cy, big):
    """Short marks across a disturbed arc, as [((x, y), (x, y))] in pixels. They are spread evenly along the whole
    arc, more of them the stronger the disturbance, and they stay where they are: they only get longer and shorter
    with each new reading (`tick` counts readings), so the arc beats without anything travelling round the circle.
    They mark the link as a whole, never one spot on it. None on a quiet link or one that cannot be trusted."""
    lv = room3d.level(seg.get("db"))
    if not lv or not seg.get("sure", True):
        return []
    count = 2 + lv
    half = TICK_HALF[tick % 2]
    radius = big * seg["r"]
    out = []
    for k in range(count):
        a = seg["a0"] + (seg["a1"] - seg["a0"]) * (k + 0.5) / count
        c, s = math.cos(a), math.sin(a)
        out.append(((round(cx + (radius - half) * c), round(cy + (radius - half) * s)),
                    (round(cx + (radius + half) * c), round(cy + (radius + half) * s))))
    return out


def label_spot(seg, cx, cy, big, push=12):
    """Where an arc's name goes: just outside the middle of the arc."""
    radius = big * seg["r"] + push
    return round(cx + radius * math.cos(seg["mid"])), round(cy + radius * math.sin(seg["mid"]))


def plan_fit(width, height, margin=14):
    """(scale, cx, cy) that make the square floor fill the widget seen from straight above, leaving a line for
    the caption."""
    scale = max(1.0, (min(width, height - 16) - 2 * margin) / (2 * room3d.FLOOR))
    return scale, width / 2.0, (height - 16) / 2.0


def plan_point(x, z, scale, cx, cy):
    """A floor point of the room's layout as a whole pixel, looking straight down."""
    return round(cx + x * scale), round(cy + z * scale)


def plan_grid(scale, cx, cy):
    """The plain floor grid from above, as [((x, y), (x, y))]: the same lines as the room's floor, never shaded."""
    n, f = room3d.CELLS, room3d.FLOOR
    lines = []
    for i in range(n + 1):
        v = -f + 2 * f * i / n
        lines.append((plan_point(v, -f, scale, cx, cy), plan_point(v, f, scale, cx, cy)))
        lines.append((plan_point(-f, v, scale, cx, cy), plan_point(f, v, scale, cx, cy)))
    return lines


def caption_lines(parts, width, measure):
    """The caption under a view: all on one line when it fits, else one part to a line. `measure` gives the width
    of a piece of text in pixels."""
    whole = "; ".join(parts)
    return [whole] if measure(whole) <= width else list(parts)
