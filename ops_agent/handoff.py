"""
Swap between the native app and the terminal console (ops.py) on a desktop.

Each side starts the other, detached, a moment after it has quit, so the SDR dongle, the
serial ports and gpsd are released before the other side asks for them. The terminal console
is started with --no-prompts so it does not ask about rails on the way in or out: it keeps
the rails exactly as they are.
Pure helpers, so they can be tested without a screen.
"""
import os
import shlex
import shutil
import subprocess

SWAP_DELAY_SECONDS = 1.5

# (program, argv template). {cmd} is one shell command string, {dir} the install folder.
TERMINALS = [
    ("lxterminal", ["lxterminal", "--working-directory={dir}", "-e", "{cmd}"]),
    ("x-terminal-emulator", ["x-terminal-emulator", "-e", "sh", "-c", "{cmd}"]),
    ("foot", ["foot", "sh", "-c", "{cmd}"]),
    ("alacritty", ["alacritty", "-e", "sh", "-c", "{cmd}"]),
    ("xterm", ["xterm", "-e", "sh", "-c", "{cmd}"]),
]


def has_display(env=None):
    env = os.environ if env is None else env
    return bool(env.get("WAYLAND_DISPLAY") or env.get("DISPLAY"))


def install_dir():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def console_command(folder):
    return f"cd {shlex.quote(folder)} && exec python3 ops.py --no-prompts"


def app_command(folder, tab=""):
    tab_arg = f" --tab {shlex.quote(tab)}" if tab else ""
    return f"cd {shlex.quote(folder)} && exec python3 -m ops_gui{tab_arg}"


def _ordered_terminals(env=None):
    """The terminals to try, the system's own preference first: $TERMINAL, then Debian's
    x-terminal-emulator alternative, then the known ones."""
    env = os.environ if env is None else env
    chosen = (env.get("TERMINAL") or "").strip()
    out = []
    if chosen and "/" not in chosen and not chosen.startswith("-"):
        known = dict(TERMINALS)
        out.append((chosen, known.get(chosen) or [chosen, "-e", "sh", "-c", "{cmd}"]))
    known = dict(TERMINALS)
    out.append(("x-terminal-emulator", known["x-terminal-emulator"]))
    out += [t for t in TERMINALS if t[0] not in ("x-terminal-emulator", chosen)]
    return out


def terminal_argv(folder, which=shutil.which, env=None):
    """The command that opens a terminal running the console, or None if no terminal is installed."""
    cmd = console_command(folder)
    for program, template in _ordered_terminals(env):
        if which(program):
            return [part.format(cmd=cmd, dir=folder) for part in template]
    return None


def plain_terminal_argv(folder, which=shutil.which, env=None):
    """A terminal window with just a shell in it, in the user's preferred terminal, or None."""
    for program, template in _ordered_terminals(env):
        if which(program):
            argv = []
            for part in template:
                if part in ("-e", "sh") or "{cmd}" in part:
                    break                                   # everything from here on is the command
                argv.append(part.format(dir=folder))
            return argv
    return None


def delayed(argv_or_shell, delay=SWAP_DELAY_SECONDS):
    """Wrap a command so it starts after `delay` seconds, as a single sh -c argv."""
    if isinstance(argv_or_shell, (list, tuple)):
        inner = " ".join(shlex.quote(a) for a in argv_or_shell)
    else:
        inner = argv_or_shell
    return ["sh", "-c", f"sleep {delay}; {inner}"]


def launch_detached(argv):
    """Start a command that outlives this process. Returns True if it started."""
    try:
        subprocess.Popen(argv, start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def with_shift_aliases(bindings):
    """Let every "shift+letter" binding also answer to the capital letter.

    Terminals send Shift+G as a plain "G", and Textual does not treat that as "shift+g", so the
    shifted bindings (Open app, Chan -, Ring - and the rest) never fired. The alias fixes them all.
    """
    out = []
    for binding in bindings:
        key = binding[0]
        parts = key.split(",")
        extra = [p.split("+", 1)[1].upper() for p in parts
                 if p.startswith("shift+") and len(p) == 7 and p[6].isalpha() and p[6].isascii()]
        if extra:
            key = ",".join(parts + [e for e in extra if e not in parts])
        out.append((key,) + tuple(binding[1:]))
    return out
