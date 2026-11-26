"""
Local logs, crash reports and manual problem reports for the native app.

Everything here is opt in and local. Nothing is ever sent by the app: a report is
built on screen, the person reads it, and then copies it or opens a pre-filled
GitHub issue in their own browser and submits it themselves.
"""
import hashlib
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.parse

from . import data

ISSUE_REPO = "AceMedia/aiov2_ctl"
LOG_KEEP_LINES = 400


def state_dir():
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "ops_gui")


def log_path():
    return os.path.join(state_dir(), "ops_gui.log")


def crash_dir():
    return os.path.join(state_dir(), "crashes")


def _private_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def redact(text, home=None, user=None, host=None):
    """Strip what identifies a person or their network from text meant to be shared."""
    home = home or os.path.expanduser("~")
    user = user or os.environ.get("USER") or os.path.basename(home)
    if not host:
        try:
            host = os.uname().nodename
        except AttributeError:
            host = ""
    out = text or ""
    if home and home != "/":
        out = out.replace(home, "~")
    if user and len(user) > 1:
        out = re.sub(rf"\b{re.escape(user)}\b", "<user>", out)
    if host and len(host) > 1:
        out = re.sub(rf"\b{re.escape(host)}\b", "<host>", out)
    out = re.sub(r"\b[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}\b", "<mac>", out)
    out = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<ip>", out)
    return out


def write_log(message, enabled):
    """Append one line to the local log, only when the person turned logging on."""
    if not enabled:
        return False
    try:
        _private_dir(state_dir())
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n"
        fd = os.open(log_path(), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(line)
        trim_log()
        return True
    except OSError:
        return False


def trim_log(keep=LOG_KEEP_LINES):
    try:
        with open(log_path()) as f:
            lines = f.readlines()
        if len(lines) > keep * 2:
            with open(log_path(), "w") as f:
                f.writelines(lines[-keep:])
    except OSError:
        pass


def tail_log(count=200):
    try:
        with open(log_path()) as f:
            return "".join(f.readlines()[-count:])
    except OSError:
        return ""


def save_crash(exc_type, exc, tb):
    """Write a crash file. Called only from the hook below, which checks the opt in."""
    try:
        d = _private_dir(crash_dir())
        name = os.path.join(d, f"crash-{time.strftime('%Y%m%d-%H%M%S')}.txt")
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("".join(traceback.format_exception(exc_type, exc, tb)))
        return name
    except OSError:
        return None


def install_crash_hook(is_enabled):
    """Wrap sys.excepthook. `is_enabled` is read at crash time, so turning it off takes effect at once."""
    previous = sys.excepthook

    def hook(exc_type, exc, tb):
        try:
            if is_enabled():
                save_crash(exc_type, exc, tb)
        finally:
            previous(exc_type, exc, tb)

    sys.excepthook = hook


def pending_crashes():
    try:
        return sorted(f for f in os.listdir(crash_dir()) if f.endswith(".txt"))
    except OSError:
        return []


_FRAME = re.compile(r'^\s*File "([^"]+)", line \d+, in (\S+)', re.M)


def crash_signature(text):
    """(fingerprint, summary) for a saved crash, or None when it holds no error line. The fingerprint is made
    from the kind of error and the functions it passed through, not from line numbers, messages or folders, so
    the same crash on another uConsole or a later version gets the same one and reports of it can be found
    together. The summary is 'KeyError in wifi_ui.refresh'."""
    kind = ""
    for line in reversed((text or "").strip().splitlines()):
        m = re.match(r"^([A-Za-z_][\w.]*)(:|$)", line)
        if m and not line.startswith(("Traceback", "File ")):
            kind = m.group(1)
            break
    if not kind:
        return None
    frames = [(os.path.splitext(os.path.basename(p.replace("\\", "/")))[0], fn) for p, fn in _FRAME.findall(text)]
    key = kind + "|" + "|".join(f"{mod}.{fn}" for mod, fn in frames)
    where = f" in {frames[-1][0]}.{frames[-1][1]}" if frames else ""
    return hashlib.sha256(key.encode()).hexdigest()[:8], f"{kind}{where}"


def crash_title(text):
    """The issue title for a crash: it starts with the fingerprint, which is what a search finds."""
    sig = crash_signature(text)
    return f"Crash {sig[0]}: {sig[1]}" if sig else "uConsole app crash"


def latest_crash():
    """(file name, text) of the newest saved crash, or None."""
    names = pending_crashes()
    if not names:
        return None
    try:
        with open(os.path.join(crash_dir(), names[-1])) as f:
            return names[-1], f.read()
    except OSError:
        return None


def crash_notice(latest, dismissed=""):
    """What the pop-up says when a crash was saved and has not been looked at: {id, title, body} or None."""
    if not latest or latest[0] == dismissed:
        return None
    sig = crash_signature(latest[1])
    what = f" ({sig[1]})" if sig else ""
    return {"id": latest[0], "title": "The app crashed",
            "body": f"A crash report was saved on this uConsole{what}. Nothing has been sent."}


def find_url(text):
    """A search of the project's issues for this crash's fingerprint, open or closed. Only the fingerprint
    goes into the address."""
    sig = crash_signature(text)
    if not sig:
        return None
    q = urllib.parse.urlencode({"q": f'is:issue "Crash {sig[0]}"'})
    return f"https://github.com/{ISSUE_REPO}/issues?{q}"


def clear_all():
    """Delete the local log, every field log, every Everything session (and the CSV copies made from them),
    every crash file and the Bluetooth page's memory of addresses. Returns how many files were removed."""
    from . import bt_memory, everything, fieldlog
    removed = bt_memory.clear_files()
    for path in ([log_path()] + fieldlog.files() + everything.all_files()
                 + [os.path.join(crash_dir(), f) for f in pending_crashes()]):
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
    return removed


def _run(args, timeout=20):
    try:
        r = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
        return (r.stdout or r.stderr or "").strip()
    except Exception as e:
        return f"(could not run: {e})"


def build_report(tab_names=None):
    """The text a person reviews before sharing. Redacted, and no scan or capture data."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rev = _run(["git", "-C", here, "rev-parse", "--short", "HEAD"], 5)
    try:
        from PyQt6.QtCore import QT_VERSION_STR, PYQT_VERSION_STR
        qt = f"Qt {QT_VERSION_STR}, PyQt {PYQT_VERSION_STR}"
    except Exception:
        qt = "Qt unknown"
    parts = [
        "## Environment",
        f"ops_gui revision: {rev}",
        f"Python {sys.version.split()[0]}, {qt}",
        f"Tabs shown: {', '.join(tab_names or [])}",
        "",
        "## aiov2_ctl status",
        _run(["aiov2_ctl", "--status", "--json"]),
        "",
        "## aiov2_ctl --doctor",
        _run(["aiov2_ctl", "--doctor"], 40),
        "",
        "## Recent app log",
        tail_log() or "(logging is off or empty)",
    ]
    crashes = pending_crashes()
    if crashes:
        parts += ["", "## Crash reports on disk"]
        for name in crashes[-3:]:
            try:
                with open(os.path.join(crash_dir(), name)) as f:
                    parts += [f"### {name}", f.read()]
            except OSError:
                pass
    return redact("\n".join(parts))


def issue_url(title="uConsole app problem", labels=()):
    """A pre-filled new issue page. The body only asks for the report to be pasted."""
    body = ("What happened:\n\n\nWhat I expected:\n\n\nPaste the report here "
            "(the app copied it for you, or saved it to a file):\n")
    fields = {"title": title, "body": body}
    if labels:
        fields["labels"] = ",".join(labels)
    q = urllib.parse.urlencode(fields)
    return f"https://github.com/{ISSUE_REPO}/issues/new?{q}"
