"""
Start menu entries for the Ops GUI and the Ops console (CLI).

`python3 -m ops_gui --install-launchers` writes two .desktop files into the user's applications folder (so they
appear under the start menu's applications, no root needed) pointing at this checkout, so updating with git pull
is all that is ever needed. `--remove-launchers` takes them away again. Nothing here runs as root.
"""
import os
import shlex
import sys

GUI_FILE = "uconsole-ops-gui.desktop"
CLI_FILE = "uconsole-ops-console.desktop"
IGA_FILE = "iga-browser.desktop"          # only for the optional IGA browser feature, written by its install step


def quote(path):
    """A path as the Exec key of a desktop file wants it."""
    if any(c in path for c in ' \t"\'\\$`'):
        return '"' + path.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$") + '"'
    return path


def icon_for(repo, name, fallback, isfile=os.path.isfile):
    """What a launcher's Icon line says: the project's own icon, by its full path, when this checkout has it
    (img/ in the repository, drawn by tools/icons.py), otherwise a name from the desktop's icon theme."""
    path = os.path.join(repo, "img", name)
    return path if isfile(path) else fallback


def entries(repo, python="/usr/bin/python3", isfile=os.path.isfile):
    """{file name: contents} for the two launchers."""
    repo = os.path.realpath(repo)
    app_icon = icon_for(repo, "ops-app.png", "utilities-system-monitor", isfile)
    console_icon = icon_for(repo, "ops-console.png", "utilities-terminal", isfile)
    gui = (
        "[Desktop Entry]\nType=Application\nName=uConsole Ops\n"
        "Comment=Rails, radios, GPS, LoRa, SDR and the Flipper in one window\n"
        f"Exec={python} -m ops_gui\nPath={repo}\nIcon={app_icon}\nTerminal=false\n"
        "Categories=System;Utility;\nStartupNotify=false\n"
    )
    cli = (
        "[Desktop Entry]\nType=Application\nName=uConsole Ops Console\n"
        "Comment=The same tools in a terminal\n"
        f"Exec={python} {quote(os.path.join(repo, 'ops.py'))}\nPath={repo}\nIcon={console_icon}\nTerminal=true\n"
        "Categories=System;Utility;ConsoleOnly;\nStartupNotify=false\n"
    )
    return {GUI_FILE: gui, CLI_FILE: cli}


def iga_entry(repo, python="/usr/bin/python3", isfile=os.path.isfile):
    """The 'IGA browser' launcher: the ops app, opened straight on its IGA tab."""
    repo = os.path.realpath(repo)
    icon = icon_for(repo, "iga.png", "web-browser", isfile)
    return (
        "[Desktop Entry]\nType=Application\nName=IGA browser\n"
        "Comment=A small web browser, in the uConsole Ops app\n"
        f"Exec={python} -m ops_gui --tab IGA\nPath={repo}\nIcon={icon}\nTerminal=false\n"
        "Categories=Network;WebBrowser;\nStartupNotify=false\n"
    )


def install_iga(repo, target=None, python="/usr/bin/python3"):
    """Write the IGA launcher. Returns its path. Refuses to run as root."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise PermissionError("Run this as your own user, not root: the launcher belongs to your menu.")
    if not os.path.isdir(os.path.join(repo, "ops_gui")):
        raise FileNotFoundError(f"{repo} does not look like the Ops checkout.")
    target = target or applications_dir()
    os.makedirs(target, exist_ok=True)
    path = os.path.join(target, IGA_FILE)
    with open(path, "w") as f:
        f.write(iga_entry(repo, python))
    os.chmod(path, 0o755)
    return path


def remove_iga(target=None):
    path = os.path.join(target or applications_dir(), IGA_FILE)
    if os.path.isfile(path):
        os.remove(path)
        return [path]
    return []


def applications_dir(environ=None):
    env = os.environ if environ is None else environ
    base = env.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "applications")


def install(repo, target=None, python="/usr/bin/python3"):
    """Write both launchers. Returns the paths written. Refuses to run as root."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise PermissionError("Run this as your own user, not root: the launchers belong to your menu.")
    if not os.path.isfile(os.path.join(repo, "ops.py")) or not os.path.isdir(os.path.join(repo, "ops_gui")):
        raise FileNotFoundError(f"{repo} does not look like the Ops checkout.")
    target = target or applications_dir()
    os.makedirs(target, exist_ok=True)
    written = []
    for name, text in entries(repo, python).items():
        path = os.path.join(target, name)
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, 0o755)
        written.append(path)
    return written


def remove(target=None):
    target = target or applications_dir()
    gone = []
    for name in (GUI_FILE, CLI_FILE):
        path = os.path.join(target, name)
        if os.path.isfile(path):
            os.remove(path)
            gone.append(path)
    return gone


def command(argv, repo=None):
    """Handle --install-launchers and --remove-launchers. Returns an exit code, or None for any other arguments."""
    repo = repo or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        if "--install-launchers" in argv:
            for path in install(repo):
                print(f"Wrote {path}")
            print("Find them in the start menu under Applications > System: uConsole Ops and uConsole Ops Console.")
            return 0
        if "--install-iga-launcher" in argv:
            print(f"Wrote {install_iga(repo)}")
            return 0
        if "--remove-iga-launcher" in argv:
            for path in remove_iga():
                print(f"Removed {path}")
            return 0
        if "--remove-launchers" in argv:
            for path in remove():
                print(f"Removed {path}")
            return 0
    except (OSError, PermissionError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return None
