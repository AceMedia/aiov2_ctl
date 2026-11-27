"""
The uConsole's own buttons: the D-pad, A B X Y, and the trackball's left, middle and right buttons.

The stock keyboard firmware sends the D-pad as the arrow keys in keyboard mode, and as a joystick
(/dev/input/js0) in gamepad mode. A, B, X and Y are the letters j, k, u and i in keyboard mode, which
clash with the shortcuts for the USB rail and the Flipper display, so they are only used in
gamepad mode. The helpers at the top are pure so they can be tested without a device.

    D-pad                move the highlight: up to the row of tabs, left and right along it, down into the
                         page, and between the controls of a page (the rules are in focusnav.py)
    A                    press what is highlighted  B                 back one level
    X                    refresh                    Y                 full screen on or off
    Right button         back                       Middle button     refresh
"""
import os
import struct

JS_DEVICE = "/dev/input/js0"
JS_EVENT = struct.Struct("<IhBB")          # time ms, value, type, number
JS_BUTTON, JS_AXIS, JS_INIT = 0x01, 0x02, 0x80
AXIS_THRESHOLD = 16000
REPEAT_FIRST_MS, REPEAT_MS = 400, 140

# Button numbers as the firmware reports them (button(1) is X, 2 is A, 3 is B, 4 is Y; zero based here).
DEFAULT_BUTTONS = {0: "x", 1: "a", 2: "b", 3: "y"}
AXES = {0: ("left", "right"), 1: ("up", "down")}

ACTIONS = {
    "left": "highlight left", "right": "highlight right", "up": "highlight up", "down": "highlight down",
    "a": "press what is highlighted", "b": "back one level", "x": "refresh", "y": "full screen",
}


def parse_events(buffer):
    """(events, leftover bytes) from raw joystick data. Initial state events are skipped."""
    events = []
    usable = len(buffer) - len(buffer) % JS_EVENT.size
    for off in range(0, usable, JS_EVENT.size):
        _t, value, kind, number = JS_EVENT.unpack_from(buffer, off)
        if kind & JS_INIT:
            continue
        if kind == JS_BUTTON:
            events.append({"kind": "button", "number": number, "value": value})
        elif kind == JS_AXIS:
            events.append({"kind": "axis", "number": number, "value": value})
    return events, buffer[usable:]


def axis_direction(number, value):
    """'left', 'right', 'up', 'down' while an axis is pushed past the threshold, else None."""
    pair = AXES.get(number)
    if not pair or abs(value) < AXIS_THRESHOLD:
        return None
    return pair[0] if value < 0 else pair[1]


def button_name(number, mapping=None):
    return (mapping or DEFAULT_BUTTONS).get(number)


def action_for(event, mapping=None):
    """The action a joystick event means, as (action, pressed). Releases and unknowns give (None, False)."""
    if event["kind"] == "button":
        name = button_name(event["number"], mapping)
        return (name, True) if name and event["value"] else (None, False)
    d = axis_direction(event["number"], event["value"])
    return (d, True) if d else (None, False)


def describe(event, mapping=None):
    """Plain words for the Help page, so a button can be checked against the guess above."""
    if event["kind"] == "button":
        name = button_name(event["number"], mapping)
        label = f"button {event['number']}" + (f" ({name.upper()})" if name else " (not mapped)")
        return f"{label} {'pressed' if event['value'] else 'released'}"
    d = axis_direction(event["number"], event["value"])
    return f"D-pad {d}" if d else f"axis {event['number']} centred"


def help_lines():
    return [
        "uConsole buttons:",
        "D-pad  move the highlight: up to the tabs, left and right along them, down into the page",
        "A  press what is highlighted (with nothing highlighted, the tab's main action)",
        "B  back one level: out of the page to the tabs, then out of full screen    X  refresh    Y  full screen",
        "Right trackball button  back    Middle trackball button  refresh",
        "f or F11  full screen on or off (Esc leaves it)",
        "A B X Y need gamepad mode on the back switch: in keyboard mode they are the letters j k u i.",
    ]


def open_device(path=JS_DEVICE):
    """A non blocking file descriptor for the joystick, or None when there is none or no access."""
    try:
        return os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None


def read_events(fd, leftover=b""):
    """Read what is waiting. Returns (events, leftover), or (None, b"") if the device went away."""
    try:
        chunk = os.read(fd, 8 * 64)
    except BlockingIOError:
        return [], leftover
    except OSError:
        return None, b""
    if not chunk:
        return None, b""
    return parse_events(leftover + chunk)
