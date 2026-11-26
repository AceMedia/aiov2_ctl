"""Settings > Features: what the installer has put on this uConsole, and adding or removing one feature.

Everything here is plain data in, plain data out. The list comes from `aiov2_ctl --features --json`, and a change
is made by the same installer the command line uses (`aiov2_ctl --install --only KEY` or `--uninstall --only KEY`),
so this page cannot disagree with it about what a feature is."""
import re
import json

REPORT_ARGS = ["aiov2_ctl", "--features", "--json"]
OLD_CLI = ("This needs a newer aiov2_ctl: the installed one cannot list its features for the app. "
           "Update it with: aiov2_ctl --update")
UPDATE_ARGV = ["x-terminal-emulator", "-e", "aiov2_ctl --update"]
UNFINISHED = ("The last update was not finished: the new version is in place but its installer has not run, so "
              "some of it is not set up. Update finishes it.")
UPDATE_HELP = ("An update brings the controller, the tray and this app up to date together. It keeps to the "
               "features you have and never switches a rail. Stable is the tested version; beta gets changes "
               "first. Restart this app afterwards to use the new version.")
CHANNEL_ARGS = ["aiov2_ctl", "--channel", "--json"]
CHANNEL_WAIT = "Checking for updates..."
CHANNEL_OLD = "Channels need a newer aiov2_ctl. Update first."
# Features this page will not remove, and why. Removing the app from inside the app would pull the folder it is
# running from out from under it.
KEPT = {
    "app": "This is the app you are looking at. To remove it, close it and run: sudo aiov2_ctl --uninstall --only app",
}
FIELDS = ("key", "title", "summary", "required", "installed", "missing", "adds", "removes", "hardware",
          "hardware_found")


def parse_report(text):
    """The report as {'root': 'sudo' | 'pkexec' | None, 'features': [row]}, or None when the text is not one
    (an older aiov2_ctl prints its usage instead)."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("features"), list):
        return None
    rows = []
    for row in data["features"]:
        if not isinstance(row, dict) or not all(k in row for k in FIELDS) or not isinstance(row["key"], str):
            return None
        rows.append(row)
    root = data.get("root")
    return {"root": root if root in ("sudo", "pkexec") else None, "features": rows,
            "unfinished": data.get("unfinished") is True}


def titles(report):
    return {row["key"]: row["title"] for row in report["features"]}


def state_text(row):
    """A few words on where a feature stands."""
    if row["installed"]:
        words = "Installed" + (", missing: " + ", ".join(row["missing"]) if row["missing"] else "")
    else:
        words = "Not installed" + (", missing: " + ", ".join(row["missing"]) if row.get("missing") else "")
    if row["hardware"] and row["hardware_found"] is False:
        words += f". {row['hardware'][:1].upper()}{row['hardware'][1:]} not found now"
    return words + "."


def action(row):
    """'add', 'remove' or None: what this page offers for a feature."""
    if not row["installed"]:
        return "add"
    if row["required"] or row["key"] in KEPT:
        return None
    return "remove"


def kept_reason(row):
    """Why an installed feature has no Remove button, or ''."""
    if not row["installed"]:
        return ""
    if row["required"]:
        return "Always installed: everything else needs it."
    return KEPT.get(row["key"], "")


def _names(keys, names):
    return ", ".join(names.get(k, k) for k in keys)


def confirm_text(row, names, what):
    """What the person is asked before a change, saying everything it brings or takes with it."""
    if what == "add":
        text = f"Add {row['title']}?"
        if row["adds"]:
            text += f"\n\nThis also adds what it needs: {_names(row['adds'], names)}."
        if row["hardware"] and row["hardware_found"] is False:
            text += f"\n\n{row['hardware'][:1].upper()}{row['hardware'][1:]} was not found just now, so it may not work yet."
        return text
    text = f"Remove {row['title']}?"
    if row["removes"]:
        text += f"\n\nThis also removes what cannot work without it: {_names(row['removes'], names)}."
    return text + "\n\nPackages, rails and your saved settings are left as they are."


def change_arguments(what, key):
    return ["--install" if what == "add" else "--uninstall", "--only", key, "--yes"]


def change_argv(root, what, key, program="/usr/local/bin/aiov2_ctl"):
    """The command that makes the change as root, or None when this page has no way to get root."""
    if what not in ("add", "remove") or root not in ("sudo", "pkexec"):
        return None
    lead = ["sudo", "-n"] if root == "sudo" else ["pkexec"]
    return [*lead, program, *change_arguments(what, key)]


def command_text(what, key):
    """The same change as a person would type it."""
    return "sudo aiov2_ctl " + " ".join(change_arguments(what, key)[:-1])


def left_out(output):
    """True when the installer's result list says a feature was not installed (its package is not in this
    system's package sources, or there is no checkout to run the app from). The installer still ends well."""
    return bool(re.search(r"(?m)^\s+\S+\s+not installed: ", output or ""))


def result_text(what, title, code, left=False):
    """One line on how a change ended. 126 and 127 are what pkexec gives when the password box is dismissed or
    refused. `left` is left_out() of what the installer said."""
    verb = "Adding" if what == "add" else "Removing"
    if code == 0 and what == "add" and left:
        return f"{title}: not everything could be installed. What the installer said is below."
    if code == 0:
        return f"{title} {'added' if what == 'add' else 'removed'}."
    if code in (126, 127):
        return f"{verb} {title} was not allowed, so nothing was changed."
    if code is None:
        return f"{verb} {title} could not be started."
    return f"{verb} {title} did not finish (exit {code}). What it said is below."


def parse_channel(text):
    """What `aiov2_ctl --channel --json` said, or None when the text is not that (an older aiov2_ctl, or it
    could not say where it was installed from)."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("channels"), list) or not isinstance(data.get("branch"), str):
        return None
    channels = []
    for ch in data["channels"]:
        if not isinstance(ch, dict) or not isinstance(ch.get("name"), str):
            return None
        channels.append({"name": ch["name"], "available": ch.get("available") is True,
                         "current": ch.get("current") is True})
    incoming = data.get("incoming")
    if not isinstance(incoming, list) or not all(isinstance(x, str) for x in incoming):
        incoming = None
    channel = data.get("channel")
    root = data.get("root")
    return {"channel": channel if isinstance(channel, str) else None, "branch": data["branch"],
            "channels": channels, "incoming": incoming, "root": root if root in ("sudo", "pkexec") else None,
            "error": data.get("error") if isinstance(data.get("error"), str) else ""}


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def channel_text(ch):
    """One line: the channel, and whether anything is waiting."""
    where = f"Channel: {ch['channel']}." if ch["channel"] else f"On its own branch, {ch['branch']}, not a channel."
    if ch["error"]:
        return f"{where} {ch['error']}"
    if ch["incoming"] is None:
        return where
    n = len(ch["incoming"])
    return f"{where} " + ("Up to date." if n == 0 else f"{_plural(n, 'change')} waiting.")


def incoming_text(ch, limit=6):
    """The waiting changes, newest first, or ''."""
    lines = ch["incoming"] or []
    shown = [line.split(" ", 1)[-1] for line in lines[:limit]]     # without the commit's short name
    if len(lines) > limit:
        shown.append(f"and {len(lines) - limit} more")
    return "\n".join(shown)


def can_update(ch, unfinished, seen=False):
    """True when the Update button has something to do. With no channel report it is usable only once aiov2_ctl
    has answered (`seen`) and turned out too old to give one, since updating is how it gets channels. While the
    answer is still on its way the button stays off: pressed then, the page would not yet know how to get root."""
    if unfinished:
        return True
    if ch is None:
        return bool(seen)
    return bool(ch["incoming"])


def update_notice(ch, dismissed="", busy=False):
    """The pop-up that offers an update: {'id', 'title', 'body'}, or None when there is nothing to offer. `id`
    is the newest change waiting, so a notice dismissed once stays away until something newer arrives."""
    if busy or not ch or not ch["incoming"]:
        return None
    words = ch["incoming"][0].split()
    if not words or words[0] == (dismissed or ""):
        return None
    where = f" on the {ch['channel']} channel" if ch["channel"] else ""
    return {"id": words[0], "title": "Update available",
            "body": f"{_plural(len(ch['incoming']), 'change')} waiting{where}."}


def other_channel(ch):
    """The channel the switch button offers, as {'name', 'available'}, or None (not on a channel, so there is
    nothing to switch from)."""
    if not ch or not ch["channel"]:
        return None
    for c in ch["channels"]:
        if not c["current"]:
            return {"name": c["name"], "available": c["available"]}
    return None


def switch_note(ch):
    """Why the switch button is off, or ''."""
    other = other_channel(ch)
    if other and not other["available"] and not ch["error"]:
        return f"There is no {other['name']} channel yet."
    return ""


def job_confirm_text(what, ch=None, name=""):
    """What the person is asked before an update or a switch."""
    if what == "switch":
        text = f"Switch to the {name} channel?\n\n"
        text += ("Beta gets changes first, before they are well tested. It also turns on saving a crash report "
                 "on this uConsole if the app crashes: nothing is sent, and you can turn it off in Settings, "
                 "Debug. " if name == "beta"
                 else "This goes back to the tested version, which is older. What beta set up is not removed. ")
        return text + "The controller, the tray and this app change together, and the features you have are kept."
    text = "Update now?"
    waiting = incoming_text(ch) if ch else ""
    if waiting:
        text += "\n\n" + waiting
    return text + "\n\nThe controller, the tray and this app update together. The features you have are kept."


def job_root(ch, report):
    """How an update or a switch gets root: 'sudo', 'pkexec' or None. Either report says; the channel report is
    asked first because its buttons can be pressed before the features report has arrived."""
    for source in (ch, report):
        if source and source.get("root"):
            return source["root"]
    return None


def job_argv(what, name="", terminal=False):
    """The command for an update or a switch. Run as the user: aiov2_ctl asks for root itself, once. In a
    terminal it shows what is coming and asks there; run by this app the question was asked here, so --yes."""
    words = ["aiov2_ctl", "--update"] if what == "update" else ["aiov2_ctl", "--channel", name]
    if terminal:
        return ["x-terminal-emulator", "-e", " ".join(words)]
    return [*words, "--yes"]


def job_start_text(what, name=""):
    return "Updating... this can take a few minutes." if what == "update" else f"Switching to {name}... this can take a few minutes."


def job_result_text(what, name, code):
    """One line on how an update or a switch ended."""
    doing = "The update" if what == "update" else f"The switch to {name}"
    if code == 0:
        return ("Updated." if what == "update" else f"Now on the {name} channel.") + " Restart the app to use the new version."
    if code in (126, 127):
        return f"{doing} was not allowed, so nothing was changed."
    if code is None:
        return f"{doing} could not be started."
    return f"{doing} did not finish (exit {code}). What it said is below."


def restart_argv(python):
    """Start this app again, a moment after this one has gone."""
    return ["sh", "-c", 'sleep 1; exec "$0" -m ops_gui', python]


def tail(text, lines=40):
    """The end of a long output, which is where the result is."""
    rows = (text or "").strip().splitlines()
    return "\n".join(rows[-lines:])

