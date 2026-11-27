"""Opening the app in full screen when the desktop starts, and getting the taskbar back while it is.

Off until it is switched on in Settings > General. Switching it on writes one file into the person's own
autostart folder, which is how a desktop is told what to open at login; switching it off removes that file.
The app is started with --fullscreen and nothing else: no rail is switched on because of it.

In full screen the app covers the desktop's taskbar. Resting the pointer on the bottom edge of the screen
takes the app out of full screen so the taskbar shows, and moving back up into the app puts it back
(peek_step). F11, Y on the gamepad and the button at the top right still leave full screen for good.

Plain Python: nothing here needs a screen."""
import os

FILE = "uconsole-ops-fullscreen.desktop"
FLAG = "--fullscreen"
EDGE = 2                # the pointer is "on the bottom edge" within this many pixels of it
HOLD = 2                # for this many looks in a row (a look is a quarter of a second), so a pass does not count
AWAY = 90               # back in the app means this far above the bottom of the window: clear of the taskbar


def repo_dir():
    """The folder the app runs from: the one that holds the ops_gui package."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def autostart_dir(home=None, env=None):
    env = os.environ if env is None else env
    home = os.path.expanduser("~") if home is None else home
    return os.path.join(env.get("XDG_CONFIG_HOME") or os.path.join(home, ".config"), "autostart")


def path(home=None, env=None):
    return os.path.join(autostart_dir(home, env), FILE)


def quote(text):
    return "'" + text.replace("'", "'\\''") + "'"


def entry(repo, python="/usr/bin/python3"):
    """The autostart file. It goes to the app's folder first and does nothing if that has gone, so a file left
    behind after the app is removed starts nothing and reports nothing."""
    run = f"cd {quote(repo)} 2>/dev/null && exec {quote(python)} -m ops_gui {FLAG}"
    return ("[Desktop Entry]\nType=Application\nName=uConsole Ops (full screen)\n"
            "Comment=Open uConsole Ops in full screen when the desktop starts\n"
            f"Exec=sh -c {quote(run).replace('%', '%%')}\n"
            "Icon=utilities-system-monitor\nTerminal=false\nStartupNotify=false\n"
            "X-GNOME-Autostart-enabled=true\n")


def enabled(home=None, env=None, exists=os.path.isfile):
    return bool(exists(path(home, env)))


def set_enabled(on, repo=None, home=None, env=None, python="/usr/bin/python3"):
    """Write the autostart file, or remove it. Returns its path. Raises OSError when that cannot be done."""
    target = path(home, env)
    if on:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w") as f:
            f.write(entry(repo or repo_dir(), python))
    elif os.path.lexists(target):
        os.remove(target)
    return target


def wants_fullscreen(argv):
    return FLAG in argv


def peek_step(state, y, height, inside=True):
    """One look at the pointer while the app is in full screen or has stepped out of it for the taskbar.
    `state` is {"peeking", "count"}; `y` is the pointer's height in the window, `height` the window's, `inside`
    whether the pointer is over the window at all. Returns (new state, "peek" | "return" | None):
    "peek" to leave full screen so the taskbar shows, "return" to go back into it."""
    peeking, count = bool(state.get("peeking")), int(state.get("count", 0))
    if not peeking:
        count = count + 1 if inside and y >= height - EDGE else 0
        if count >= HOLD:
            return {"peeking": True, "count": 0}, "peek"
        return {"peeking": False, "count": count}, None
    count = count + 1 if inside and 0 <= y < height - AWAY else 0
    if count >= HOLD:
        return {"peeking": False, "count": 0}, "return"
    return {"peeking": True, "count": count}, None
