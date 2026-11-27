#!/usr/bin/env python3
"""
Record the ops app being used, as numbered pictures that tools/wiki-clip.py turns into a moving one.

    python3 tools/wiki-record.py tools/clips/dpad.py /tmp/frames-dpad

Run it on the uConsole, in the desktop session (or with that session's display variables set), from the
checkout. It opens the app, waits for it to settle, runs the clip script, and closes the app again. A clip
script is a Python file with one function, clip(rec); `rec` is the Recorder below. Every shot is one frame,
with how long it should stay on screen when played, so a clip is as slow or as fast as its script says and a
minute of charts can play back in a few seconds (rec.watch).

What a recording can never do, whatever its script says:
  - save a setting: a look or a tab switched on for the clip is gone when the app closes;
  - switch a rail: the app's rail switch is replaced by one that does nothing. A clip of a radio's screen
    needs its rail switched on by a person beforehand.
The app's own offers (an update is waiting, a crash report was saved) are held back so none lands on a frame.
What would identify the person or their network is swapped for a stand-in (STAND_IN): the computer's
name, its network address and the name of the network it is joined to. The swap is made where the app reads
those values, before the app starts, so the real ones are never on the screen during a recording, not even
for a frame. The home folder is written as ~ in any text before each frame. That covers what every screen
shows. It does not cover what a particular screen lists: the Network tab's hosts, a Wi-Fi scan's networks, a
GPS position, mesh nodes. Keep clips off those, or cover them by hand.

The frames are ordinary screenshots: look at every clip for anything private before it goes anywhere.
"""
import json
import os
import runpy
import subprocess
import sys
import time

SETTLE_MS = 8000            # the app's first refresh, and anything it offers at start-up, before the first frame
FRAME_MS = 400


def frame_name(number):
    return f"{number:04d}.png"


def watch_plan(seconds, every, play_ms):
    """How a stretch of time is recorded: (how many frames, seconds between them, how long each plays).
    A minute watched with a frame every 2 seconds is 30 frames; at 100 ms each it plays back in 3 seconds."""
    every = max(0.2, float(every))
    count = max(1, int(round(float(seconds) / every)))
    return count, every, max(20, int(play_ms))


# What a frame shows in place of the real thing. The address is from the range kept for examples (RFC 5737).
STAND_IN = {"host": "uconsole", "address": "192.0.2.10", "network": "HomeNetwork"}


def home_shortened(text, home):
    """Text with the person's home folder written as ~, so a path in a frame does not name their account."""
    return text.replace(home, "~") if home and home != "/" and home in text else text


def scrubbed(text, swaps):
    """Text with each real value swapped for its stand-in. `swaps` is [(real, stand-in)]; a real value too
    short to be sure of (under 4 characters) is left, since swapping it would mangle ordinary words."""
    for real, stand in swaps:
        if real and len(real) >= 4 and real in text:
            text = text.replace(real, stand)
    return text


def stand_in_addresses(addresses, address):
    """The table of network addresses, {device: address}, with every address replaced."""
    return {device: address for device in addresses}


def stand_in_network(devices, name):
    """The Home screen's table of network devices, {device: (state, network name)}, with every network name
    that is there replaced."""
    out = {}
    for device, row in devices.items():
        if isinstance(row, (tuple, list)) and len(row) > 1 and row[1]:
            row = type(row)([row[0], name, *row[2:]])
        out[device] = row
    return out


class Recorder:
    """What a clip script is given. Everything here is in terms of the app: no pointer is moved."""

    def __init__(self, win, folder, pump, grab):
        self.win, self.folder, self._pump, self._grab = win, folder, pump, grab
        self.frames, self.asked_rails = [], []

    # ---- frames
    def wait(self, ms):
        self._pump(ms)

    def shot(self, ms=FRAME_MS):
        """One frame, to stay on screen for `ms` when the clip plays."""
        self._pump(150)
        name = frame_name(len(self.frames) + 1)
        self._grab(os.path.join(self.folder, name))
        self.frames.append({"file": name, "ms": int(ms)})

    def watch(self, seconds, every=2.0, play_ms=100):
        """Record a stretch of real time, to play back faster: a frame every `every` seconds for `seconds`."""
        count, every, play_ms = watch_plan(seconds, every, play_ms)
        for _ in range(count):
            self.shot(play_ms)
            self._pump(int(every * 1000))

    # ---- driving the app
    def tab(self, name, ms=FRAME_MS):
        self.win.goto(list(self.win.names).index(name))
        self._pump(700)
        self.shot(ms)

    def settings(self, page, ms=FRAME_MS):
        self.win.goto_settings(page)
        self._pump(900)
        self.shot(ms)

    def dpad(self, *directions, ms=FRAME_MS):
        """Press the D-pad, one frame after each press."""
        for direction in directions:
            self.win.act(direction)
            self.shot(ms)

    def press(self, button, ms=FRAME_MS):
        """A gamepad button: a, b, x or y."""
        self.win.act(button)
        self._pump(300)
        self.shot(ms)

    def show_tab(self, name):
        """Switch an optional tab on for this recording only (Apps, Home)."""
        key = {"Apps": "apps_tab", "Home": "home_tab"}[name]
        self.win.settings[key] = True
        self.win.rebuild_tabs()
        self._pump(500)

    def to_tabs(self, ms=FRAME_MS, most=8):
        """Press up until the highlight is on the row of tabs, a frame after each press."""
        from PyQt6.QtWidgets import QApplication
        for _ in range(most):
            if QApplication.focusWidget() is self.win.tabs.tabBar():
                break
            self.dpad("up", ms=ms)

    def clear_highlight(self):
        self.win.nav_drop()
        self._pump(200)


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip())
        return 2
    script, folder = argv[1], argv[2]
    clip = runpy.run_path(script).get("clip")
    if not callable(clip):
        print(f"{script} has no clip(rec) function.")
        return 2
    os.makedirs(folder, exist_ok=True)
    for old in os.listdir(folder):
        if old.endswith(".png") or old == "frames.json":
            os.remove(os.path.join(folder, old))
    sys.argv = ["ops_gui"]
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication, QLabel
    import socket
    from ops_gui import app as appmod, data, pixel, statusbar
    home = os.path.expanduser("~")
    # Stand-ins go in where the app reads the real values, before any window exists.
    swaps = [(statusbar.local_address(), STAND_IN["address"]), (socket.gethostname(), STAND_IN["host"])]
    read_addresses, read_devices = pixel.parse_ip_addr, pixel.parse_nmcli_dev
    statusbar.local_address = lambda: STAND_IN["address"]
    socket.gethostname = lambda: STAND_IN["host"]
    pixel.parse_ip_addr = lambda text: stand_in_addresses(read_addresses(text), STAND_IN["address"])
    pixel.parse_nmcli_dev = lambda text: stand_in_network(read_devices(text), STAND_IN["network"])
    state = {"code": 0}

    def pump(ms):
        end = time.time() + ms / 1000
        while time.time() < end:
            QApplication.processEvents()
            time.sleep(0.01)

    def run():
        win = next(w for w in QApplication.topLevelWidgets() if hasattr(w, "names"))

        def grab(path):
            for lab in win.findChildren(QLabel):
                shown = scrubbed(home_shortened(lab.text(), home), swaps)
                if shown != lab.text():
                    lab.setText(shown)
            pump(120)                                               # let what was just drawn reach the screen
            subprocess.run(["grim", path], check=True)

        rec = Recorder(win, folder, pump, grab)
        data.save_settings = lambda *a, **k: None                       # nothing a clip does is remembered
        win.toggle_rail = lambda *a, **k: rec.asked_rails.append(a)     # and no rail is switched
        if getattr(win, "notifier", None) is not None:                  # no "update available" over a frame
            win.notifier.offer = lambda *a, **k: False
        try:
            clip(rec)
            with open(os.path.join(folder, "frames.json"), "w") as f:
                json.dump({"frames": rec.frames}, f, indent=1)
            print(f"{len(rec.frames)} frames in {folder}")
            if rec.asked_rails:
                print(f"note: the clip asked to switch a rail {len(rec.asked_rails)} time(s); nothing was switched")
        except Exception:
            import traceback
            traceback.print_exc()
            state["code"] = 1
        finally:
            win.close()

    QTimer.singleShot(0, lambda: QTimer.singleShot(SETTLE_MS, run))
    appmod.main()
    return state["code"]


if __name__ == "__main__":
    sys.exit(main(sys.argv))
