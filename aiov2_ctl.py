#!/usr/bin/python3
import sys
import os
import subprocess
import time
import json
import shutil
import re
import stat
import pwd
import tempfile
import threading
from statistics import mean

INSTALL_META_PATH = "/usr/local/share/aiov2_ctl/install.json"
CONFIG_PATH = "/usr/local/share/aiov2_ctl/config.json"
USER_CONFIG_PATH = os.path.expanduser("~/.config/aiov2_ctl/config.json")
RAILS_BOOT_SERVICE = "aiov2-rails-boot.service"
RAILS_BOOT_SERVICE_PATH = f"/etc/systemd/system/{RAILS_BOOT_SERVICE}"

INSTALLED_PATH = "/usr/local/bin/aiov2_ctl"


def is_trusted_file(path):
    """True if only root can modify this file (and it lives in a root owned dir)."""
    try:
        st = os.stat(path)
        parent = os.stat(os.path.dirname(os.path.realpath(path)))
    except OSError:
        return False
    unsafe = stat.S_IWGRP | stat.S_IWOTH
    return (
        st.st_uid == 0 and not st.st_mode & unsafe
        and parent.st_uid == 0 and not parent.st_mode & unsafe
    )


def rerun_with_sudo(extra_args=None, allow_untrusted=False):
    """
    Re-run this tool as root. Root only ever executes a copy that root alone
    can modify (the installed one, or this file if it is root owned), so a
    user writable checkout cannot be used to escalate. The update flow is
    the one exception, and passes allow_untrusted after the user has
    reviewed and confirmed the incoming changes.
    """
    python3 = shutil.which("python3") or "/usr/bin/python3"

    script = None
    if is_trusted_file(INSTALLED_PATH):
        script = INSTALLED_PATH
    elif is_trusted_file(os.path.realpath(__file__)):
        script = os.path.realpath(__file__)
    elif allow_untrusted:
        script = os.path.realpath(__file__)

    if script is None:
        print("Refusing to run this from a user writable location as root.")
        print("Install first:  sudo python3 ./aiov2_ctl.py --install")
        print("Then re-run:    sudo aiov2_ctl " + " ".join(extra_args or sys.argv[1:]))
        sys.exit(1)

    cmd = ["sudo", python3, script]
    if extra_args:
        cmd += extra_args
    else:
        cmd += sys.argv[1:]

    os.execvp("sudo", cmd)

def is_ssh_session():
    return any(
        os.environ.get(v)
        for v in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")
    )


def has_desktop_session():
    return any(
        os.environ.get(v)
        for v in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE")
    )

def run_cmd(cmd, cwd=None):
    try:
        subprocess.check_call(cmd, cwd=cwd)
        return True
    except subprocess.CalledProcessError:
        return False

def get_git_root(cwd=None):
    # Anchored to the running script by default: the caller's working
    # directory may well be an unrelated repository.
    cwd = cwd or os.path.dirname(os.path.realpath(__file__))
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            stderr=subprocess.DEVNULL,
            text=True
        ).strip()
    except (subprocess.CalledProcessError, OSError):
        return None


def get_git_branch(repo):
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo,
            text=True
        ).strip()
    except subprocess.CalledProcessError:
        return None


def load_install_meta():
    try:
        with open(INSTALL_META_PATH) as f:
            return json.load(f)
    except Exception:
        return None

def load_config():
    config = {}
    for path in (CONFIG_PATH, USER_CONFIG_PATH):
        try:
            with open(path) as f:
                config.update(json.load(f))
        except Exception:
            continue
    return config


def load_system_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}

def save_config(config):
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=2)
        return
    except PermissionError:
        pass

    print(
        f"Warning: could not write {CONFIG_PATH} (permission denied).\n"
        f"Saved to {USER_CONFIG_PATH} instead. The boot service only reads the "
        "system config, so boot settings saved this way will NOT apply at boot. "
        "Re-run with sudo to fix.",
        file=sys.stderr,
    )
    os.makedirs(os.path.dirname(USER_CONFIG_PATH), exist_ok=True)
    with open(USER_CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def _rails_on_boot_from_config(config):
    rails = {feature: False for feature in GPIO_MAP}
    raw = config.get("rails_on_boot")
    if isinstance(raw, dict):
        for feature in rails:
            if feature in raw:
                rails[feature] = bool(raw[feature])
    return rails


def get_rails_on_boot_config(system_only=False):
    if system_only:
        return _rails_on_boot_from_config(load_system_config())
    return _rails_on_boot_from_config(load_config())


def set_feature_on_boot(feature, enabled):
    feature = feature.upper()
    if feature not in GPIO_MAP:
        return False

    config = load_config()
    rails = _rails_on_boot_from_config(config)
    rails[feature] = bool(enabled)
    config["rails_on_boot"] = rails
    save_config(config)
    return True


def print_rails_on_boot_status():
    rails = get_rails_on_boot_config(system_only=True)
    print("GPIO rails on boot:")
    for feature in GPIO_MAP:
        state = "ON" if rails.get(feature, False) else "OFF"
        print(f"  {feature:<5}: {state}")


def apply_rails_on_boot(announce=False):
    rails = get_rails_on_boot_config(system_only=True)
    if announce:
        print("Applying GPIO rail boot states...")

    for feature in GPIO_MAP:
        desired = bool(rails.get(feature, False))
        GpioController.set_feature(feature, desired)
        if announce:
            print(f"  {feature:<5} -> {'ON' if desired else 'OFF'}")

    return 0

def apply_mesh_on_boot(enabled, update_config=True, announce=True):
    if update_config:
        config = load_config()
        config["mesh_on_boot"] = bool(enabled)
        save_config(config)

    action = ["enable"] if enabled else ["disable", "--now"]
    subprocess.call(
        ["sudo", "-n", "systemctl", *action, "meshtasticd"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if announce:
        if enabled:
            print("Meshtastic will start on boot.")
        else:
            print("Meshtastic will not start on boot (LORA controls startup).")

def disable_mesh_autostart_if_default(announce=True):
    config = load_config()
    if bool(config.get("mesh_on_boot", False)):
        return False
    if not GpioController._service_enabled("meshtasticd"):
        return False
    apply_mesh_on_boot(False, update_config=False, announce=announce)
    return True

def get_mesh_on_boot_status():
    config = load_config()
    config_enabled = bool(config.get("mesh_on_boot", False))
    service_enabled = GpioController._service_enabled("meshtasticd")
    return {
        "config_enabled": config_enabled,
        "service_enabled": service_enabled,
    }

def format_mesh_on_boot_status(status):
    config_state = "on" if status["config_enabled"] else "off"
    service_state = "enabled" if status["service_enabled"] else "disabled"
    if status["config_enabled"]:
        mode = "override enabled"
    else:
        mode = "default (LORA controls startup)"
    return f"Meshtastic autostart: {service_state} | mesh_on_boot: {config_state} ({mode})"

def print_mesh_on_boot_status():
    print(format_mesh_on_boot_status(get_mesh_on_boot_status()))

def report_and_disable_mesh_autostart_if_default(context_label=None):
    if context_label:
        print(f"\n{context_label}")
    else:
        print("\nMeshtastic boot config status:")

    print_mesh_on_boot_status()
    meshtastic_disabled = disable_mesh_autostart_if_default(announce=False)
    if meshtastic_disabled:
        print("\nMeshtastic autostart disabled (LORA controls startup).")
        print("Override: aiov2_ctl --mesh-on-boot")
        print("Meshtastic boot config status:")
        print_mesh_on_boot_status()
    return meshtastic_disabled

def print_mesh_on_boot_status_hint(context_label=None):
    if context_label:
        print(f"\n{context_label}")
    else:
        print("\nMeshtastic boot config status:")
    print_mesh_on_boot_status()
    print("Override: aiov2_ctl --mesh-on-boot")

BANNER = """
   db    88  dP"Yb      Yb    dP oP"Yb.
  dPYb   88 dP   Yb      Yb  dP  "' dP'
 dP__Yb  88 Yb   dP       YbdP     dP'
dP\"\"\"\"Yb 88  YbodP         YP    .d8888
"""

APP_HEADER = f"""{BANNER}
aiov2_ctl — HackerGadgets uConsole AIOv2 control + telemetry tool
"""

BASH_COMPLETION = r"""# Bash completion for aiov2_ctl

_aiov2_ctl()
{
    local cur prev opts features

    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    opts="
        --help
        --status
        --power
        --watch
        --gui
        --measure
        --install
        --update
        --check-update
        --autostart
        --no-autostart
        --mesh-on-boot
        --mesh-off-boot
        --boot-rail
        --boot-rails-status
        --boot-device
        --add-apps
        --remove-apps
        --setup-sudoers
        --companions
        --fix-sdr
        --sdr-biastee
        --wifi-monitor
        --sync-rtc
    "

    features="GPS LORA SDR USB"

    if [[ ${COMP_CWORD} -eq 1 ]]; then
        COMPREPLY=( $(compgen -W "${opts} ${features}" -- "${cur}") )
        return 0
    fi

    if [[ "${prev}" =~ ^(GPS|LORA|SDR|USB)$ ]] && [[ "${COMP_WORDS[1]}" != "--boot-rail" ]]; then
        COMPREPLY=( $(compgen -W "on off" -- "${cur}") )
        return 0
    fi

    if [[ "${prev}" == "--boot-rail" ]]; then
        COMPREPLY=( $(compgen -W "${features}" -- "${cur}") )
        return 0
    fi

    if [[ ${COMP_CWORD} -eq 3 ]] && [[ "${COMP_WORDS[1]}" == "--boot-rail" ]]; then
        COMPREPLY=( $(compgen -W "on off status" -- "${cur}") )
        return 0
    fi

    if [[ "${prev}" == "--mesh-on-boot" || "${prev}" == "--sdr-biastee" || "${prev}" == "--wifi-monitor" ]]; then
        COMPREPLY=( $(compgen -W "on off status" -- "${cur}") )
        return 0
    fi

    if [[ "${prev}" == "--boot-device" ]]; then
        COMPREPLY=( $(compgen -W "status sd nvme toggle" -- "${cur}") )
        return 0
    fi

    if [[ "${prev}" == "--measure" ]]; then
        COMPREPLY=( $(compgen -W "${features}" -- "${cur}") )
        return 0
    fi

    if [[ ${COMP_CWORD} -ge 3 ]] && [[ "${COMP_WORDS[1]}" == "--measure" ]]; then
        COMPREPLY=( $(compgen -W "--seconds --interval --settle" -- "${cur}") )
        return 0
    fi
}

complete -F _aiov2_ctl aiov2_ctl
"""



def clear_screen():
    # Works in SSH, local TTY, tmux
    os.system("clear")

def draw_header(title=None):
    clear_screen()
    print(BANNER)
    if title:
        print(f"\n{title}")
        print("-" * len(title))
    print()


# ==============================
# Configuration
# ==============================
GPIO_MAP = {
    "GPS": 27,
    "LORA": 16,
    "SDR": 7,
    "USB": 23,
}

# If pinctrl reports level "--" (not driven), fall back to a boot default.
# Keep this per-pin; do NOT infer state using "no pu" globally.
BOOT_DEFAULTS = {
    7: True,
    16: False,
    23: False,
    27: False,
}

FEATURE_META = {
    "GPS":  {"device": "/dev/ttyAMA0", "type": "serial"},
    "LORA": {"type": "spi"},
    "SDR":  {"type": "usb"},
    "USB":  {"type": "usb"},
}

BATTERY_SUPPLY = "axp20x-battery"
AC_SUPPLY = "axp22x-ac"





# ==============================
# Post-install / Usage tips
# ==============================
POST_INSTALL_TIPS = """
NEXT STEPS / TIPS

Meshtastic
----------
An open-source, off-grid, decentralised mesh network for low-power devices.

 • Launch: meshtastic-ui
 • Set your call sign and country in settings
 • US users: set Frequency Slot to 20
 • Map packs go in: /home/USER/.portduino/default/maps
 • Ensure the LoRa module is powered on (use aiov2_ctl if needed)

SDR++ (Brown)
-------------
A lightweight, open-source SDR application.

 • Launch: sdrpp
 • Select the RTL-SDR from Sources (top-left)
 • Click ▶ Play to start receiving

Audio on Debian Trixie (PipeWire):
 • Open the left sidebar
 • Go to Module Manager
 • Search for: audio
 • Add: linux_pulseaudio_sink
 • Open Sinks and select your audio output

tar1090
-------
Web interface for ADS-B decoders (readsb / dump1090-fa).

 • tar1090 runs on top of readsb
 • The SDR can only be used by one app at a time
 • This setup starts readsb when tar1090 launches and stops it on exit

PyGPSClient
-----------
Graphical GPS / GNSS diagnostics tool.

 • Launch: pygpsclient
 • Initial GPS lock may take time (antenna dependent)
 • Opens on /dev/serial0 (the GPS UART) at 9600 baud
 • Click the USB/UART icon to connect
 • Keep the GPS powered at boot: sudo aiov2_ctl --boot-rail GPS on

General
-------
 • A reboot after install is recommended
"""



# ==============================
# Help text
# ==============================
HELP_TEXT = (
    APP_HEADER
    + f"""
USAGE:
  aiov2_ctl
  aiov2_ctl --status
  aiov2_ctl --power
  aiov2_ctl --watch
  aiov2_ctl --gui
  aiov2_ctl --measure <FEATURE> [--seconds N] [--interval S] [--settle S]
  aiov2_ctl <FEATURE> on|off
  aiov2_ctl --install
  aiov2_ctl --update
  aiov2_ctl --check-update
  aiov2_ctl --help
  aiov2_ctl --autostart
  aiov2_ctl --no-autostart
    aiov2_ctl --mesh-on-boot [on|off|status]
    aiov2_ctl --boot-rail <FEATURE> on|off|status
    aiov2_ctl --boot-rails-status
  sudo aiov2_ctl --boot-device <status|sd|nvme|toggle>
  sudo aiov2_ctl --add-apps
  sudo aiov2_ctl --remove-apps
  sudo aiov2_ctl --fix-sdr
  sudo aiov2_ctl --sdr-biastee on|off|status
  sudo aiov2_ctl --wifi-monitor on|off|status
  sudo aiov2_ctl --sync-rtc

FEATURES:
  {', '.join(GPIO_MAP.keys())}

COMMANDS:
  --status     One-shot system + battery snapshot
  --power      Live power monitor (Ctrl+C to exit)
  --watch      Compact live GPIO + power line
  --gui        System tray controller
  --measure    Measure power delta of a feature
  --install    Install tool to /usr/local/bin
  --update     Pull latest version from git and reinstall
  --check-update     Check for updates and prompt to install
  --autostart        Enable GUI autostart on login
  --no-autostart     Disable GUI autostart
        --mesh-on-boot     Enable meshtasticd autostart (or status)
        --mesh-off-boot    Disable meshtasticd autostart (default)
      --boot-rail      Set per-rail GPIO state to apply on boot
      --boot-rails-status   Show configured per-rail boot states
  --boot-device   Set EEPROM boot order (SD/eMMC vs NVMe first)
  --add-apps   Install HackerGadgets AIO apps
  --fix-sdr    Free the RTL-SDR from the kernel DVB driver
  --sdr-biastee  Power the SDR antenna input (bias tee) while readsb runs
  --wifi-monitor Keep NetworkManager off the USB WiFi for monitor mode
  --sync-rtc   Write current system time to hardware RTC
  --remove-apps   Remove HackerGadgets AIO apps
  --companions    Detect a Flipper Zero or ESP32 board on USB (detection only)
  --setup-sudoers Let the tray change rails/services without a password (--remove to undo)
  --update --yes  Apply updates without the confirmation prompt

NOTES:
  • Battery power is the truth source
  • <0.05 W deltas are below noise floor
  • --status is a one-shot snapshot
  • GUI left-click opens status window
  • GUI right-click opens menu
"""
    + POST_INSTALL_TIPS
)




# ==============================
# Autostart (XDG)
# ==============================
AUTOSTART_DESKTOP = """[Desktop Entry]
Type=Application
Name=AIO v2 Controller
Comment=GPIO tray controller
Exec=sh -c 'sleep 5; exec env QT_QPA_PLATFORM=xcb /usr/bin/python3 /usr/local/bin/aiov2_ctl --gui'
Terminal=false
X-GNOME-Autostart-enabled=true
"""

SYSTEM_DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=AIO v2 Controller
Comment=HackerGadgets uConsole AIO v2 controller
Exec=/usr/bin/python3 /usr/local/bin/aiov2_ctl --gui
Icon=utilities-system-monitor
Terminal=false
Categories=System;Utility;
StartupNotify=false
"""

RAILS_BOOT_SERVICE_UNIT = """[Unit]
Description=AIO v2 apply GPIO rail boot states
After=local-fs.target
Wants=local-fs.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/bin/aiov2_ctl --apply-boot-rails

[Install]
WantedBy=multi-user.target
"""


def autostart_path():
    return os.path.expanduser("~/.config/autostart/aiov2_ctl.desktop")

def enable_autostart():
    if os.geteuid() == 0:
        print("Do not run --autostart as root.")
        return 1

    path = autostart_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)

    if os.path.exists(path):
        print("Autostart already enabled.")
        return 0

    with open(path, "w") as f:
        f.write(AUTOSTART_DESKTOP)

    print(f"Autostart enabled: {path}")
    return 0

def disable_autostart():
    if os.geteuid() == 0:
        print("Do not run --no-autostart as root.")
        return 1

    path = autostart_path()
    if os.path.exists(path):
        os.remove(path)
        print("Autostart disabled.")
    else:
        print("Autostart already disabled.")

    return 0

def read_os_release():
    info = {}
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if "=" not in line:
                    continue
                key, _, value = line.strip().partition("=")
                info[key] = value.strip('"')
    except OSError:
        pass
    return info


def meshtastic_repo_dir():
    """
    Pick the OBS repo flavour matching this host.

    The Meshtastic builds are per-suite: the Debian_12 build links against
    libgpiod2, which trixie-based systems (Kali rolling included) no longer
    ship, so picking the wrong one makes meshtasticd uninstallable.
    """
    info = read_os_release()
    codename = info.get("VERSION_CODENAME", "").lower()
    distro_id = info.get("ID", "").lower()

    prefix = "Raspbian" if distro_id == "raspbian" else "Debian"

    if codename in ("bookworm", "kali-last-snapshot"):
        return f"{prefix}_12"
    if codename in ("trixie", "forky", "sid", "unstable", "kali-rolling"):
        return f"{prefix}_13"

    # Rolling or unknown codename: fall back to the numeric base version.
    try:
        with open("/etc/debian_version") as f:
            major = int(f.read().strip().split(".")[0])
        return f"{prefix}_12" if major <= 12 else f"{prefix}_13"
    except (OSError, ValueError):
        return f"{prefix}_13"


def ensure_meshtastic_repo():
    """
    Point the Meshtastic APT source at the suite this host can actually
    install, rewriting it if something (e.g. the AIO board package's
    postinst) left the wrong one behind. Returns True if it was changed.
    """
    list_path = "/etc/apt/sources.list.d/network:Meshtastic:beta.list"
    key_path = "/etc/apt/trusted.gpg.d/network_Meshtastic_beta.gpg"
    repo_dir = meshtastic_repo_dir()
    base = "http://download.opensuse.org/repositories/network:/Meshtastic:/beta"
    wanted = f"deb {base}/{repo_dir}/ /\n"

    try:
        with open(list_path) as f:
            current = f.read()
    except OSError:
        current = ""

    if current.strip() == wanted.strip() and os.path.exists(key_path):
        return False

    print(f"Pointing Meshtastic repo at {repo_dir}…")
    with open(list_path, "w") as f:
        f.write(wanted)
    os.chmod(list_path, 0o644)

    key_url = (
        "https://download.opensuse.org/repositories/"
        f"network:Meshtastic:beta/{repo_dir}/Release.key"
    )
    try:
        armoured = subprocess.check_output(
            ["curl", "-fsSL", key_url],
            stderr=subprocess.DEVNULL
        )
        dearmoured = subprocess.run(
            ["gpg", "--dearmor"],
            input=armoured,
            stdout=subprocess.PIPE,
            check=True
        ).stdout
        with open(key_path, "wb") as f:
            f.write(dearmoured)
        os.chmod(key_path, 0o644)
    except (subprocess.CalledProcessError, OSError):
        print("Warning: could not refresh the Meshtastic signing key.")

    return True


def apt_install(packages, recommends=False):
    """Install packages, returning the list that failed."""
    # Several of these packages ship conffiles that earlier source-based
    # installs already wrote, and a conffile prompt would hang a -y run.
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"

    failed = []
    for pkg in packages:
        cmd = ["apt", "install"]
        if recommends:
            cmd.append("--install-recommends")
        cmd += [
            "-o", "Dpkg::Options::=--force-confdef",
            "-o", "Dpkg::Options::=--force-confold",
            pkg, "-y",
        ]

        print(f"\nInstalling {pkg}…")
        if subprocess.call(cmd, env=env) != 0:
            print(f"Failed to install {pkg}.")
            failed.append(pkg)

    return failed


def target_user():
    """The desktop user behind a sudo invocation."""
    user = os.environ.get("SUDO_USER")
    if user and user != "root":
        return user
    try:
        return subprocess.check_output(["logname"], text=True).strip()
    except (subprocess.CalledProcessError, OSError):
        return None


def target_home(user=None):
    user = user or target_user()
    if not user:
        return None
    try:
        return pwd.getpwnam(user).pw_dir
    except KeyError:
        return None


PYGPSCLIENT_PATH_LINE = 'export PATH="$HOME/.pygpsclient/bin:$PATH"'

PYGPSCLIENT_CONFIG = """{
  "userport_s": "/dev/serial0",
  "bpsrate_n": 9600
}
"""


def prepare_pygpsclient_profile():
    """
    The pygpsclient package's installer appends a PATH line to the login
    profile and then sources it with bash. On a zsh system that profile
    contains zsh-only builtins, so bash aborts and the postinst never
    creates the launcher symlink. Guard the zsh-only block and drop the
    duplicate PATH lines earlier failed runs left behind.
    """
    home = target_home()
    if not home:
        return

    for name in (".zprofile", ".zshrc"):
        path = os.path.join(home, name)
        if not os.path.isfile(path):
            continue

        try:
            with open(path) as f:
                lines = f.read().split("\n")
        except OSError:
            continue

        out = []
        changed = False
        seen_path_line = False

        for line in lines:
            stripped = line.strip()

            # Collapse the repeated PyGPSClient PATH stanza to one copy.
            if stripped == "# Path to PyGPSClient executable":
                if seen_path_line:
                    changed = True
                    continue
                out.append(line)
                continue

            if stripped.startswith('export PATH=') and ".pygpsclient/bin" in stripped:
                if seen_path_line:
                    changed = True
                    continue
                seen_path_line = True
                # The installer spells the home as /home/"$(logname)", which
                # comes out empty without a controlling terminal.
                if stripped != PYGPSCLIENT_PATH_LINE:
                    indent = line[:len(line) - len(line.lstrip())]
                    line = indent + PYGPSCLIENT_PATH_LINE
                    changed = True
                out.append(line)
                continue

            # zsh-only builtin: skip it when a POSIX shell sources the file.
            if stripped.startswith("emulate ") and "ZSH_VERSION" not in line:
                indent = line[:len(line) - len(line.lstrip())]
                out.append(f'{indent}[ -n "$ZSH_VERSION" ] && {stripped}')
                changed = True
                continue

            out.append(line)

        if changed:
            print(f"Making {path} safe to source from sh…")
            with open(path, "w") as f:
                f.write("\n".join(out))


def finalize_pygpsclient():
    """
    Finish what the pygpsclient postinst leaves undone: the launcher it
    skips when its install script trips, the venv it builds as root inside
    the user's home, and a first-run config that finds the GPS.
    """
    user = target_user()
    home = target_home(user)
    if not home:
        return

    venv = os.path.join(home, ".pygpsclient")
    src = os.path.join(venv, "bin", "pygpsclient")
    link = "/usr/local/bin/pygpsclient"

    if not os.path.isfile(src):
        return

    if not os.path.exists(link):
        print(f"Linking {link} → {src}")
        try:
            os.symlink(src, link)
        except OSError as exc:
            print(f"Could not create the pygpsclient launcher: {exc}")

    # A root-owned venv stops PyGPSClient's in-app updater installing into it.
    owner = pwd.getpwnam(user)
    if os.stat(venv).st_uid != owner.pw_uid:
        print(f"Handing {venv} back to {user}…")
        subprocess.call(["chown", "-R", f"{owner.pw_uid}:{owner.pw_gid}", venv])

    # With no config PyGPSClient preselects the first scanned port,
    # /dev/ttyACM0 — the uConsole keyboard. /dev/serial0 is the GPS UART and,
    # being a symlink, is left out of the scan, so as the user-defined port
    # it is listed first and selected.
    config = os.path.join(home, "pygpsclient.json")
    if not os.path.exists(config):
        print(f"Pointing PyGPSClient at the GPS UART → {config}")
        with open(config, "w") as f:
            f.write(PYGPSCLIENT_CONFIG)
        os.chown(config, owner.pw_uid, owner.pw_gid)


def readsb_json_present():
    return os.path.isfile("/run/readsb/aircraft.json")


def readsb_service_running():
    """
    A decoder only counts if the unit is settled. readsb writes its json
    before it opens the SDR, so with no dongle attached the files appear
    and are then wiped again by the Restart=always loop.
    """
    out = subprocess.run(
        ["systemctl", "is-active", "readsb"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True
    ).stdout.strip()
    return out == "active" and readsb_json_present()


SDR_BLACKLIST_PATH = "/etc/modprobe.d/blacklist-rtlsdr.conf"
SDR_UDEV_PATH = "/etc/udev/rules.d/99-rtlsdr-nosuspend.rules"

SDR_UDEV_RULES = """# RTL2832U SDR on the AIO v2 board.
# USB autosuspend (usbcore default: 2s) leaves the R820T tuner
# unresponsive on I2C (r82xx_write: i2c wr failed=-9) and can drop the
# device off the bus entirely. Keep it powered whenever it is attached.
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="0bda", ATTR{idProduct}=="2832", ATTR{power/control}="on", ATTR{power/autosuspend}="-1"
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="0bda", ATTR{idProduct}=="2838", ATTR{power/control}="on", ATTR{power/autosuspend}="-1"
"""

SDR_BLACKLIST = """# The RTL2832U on the AIO v2 board is used as an SDR via librtlsdr
# (readsb, SDR++, rtl_433). The kernel DVB-T driver claims the device
# first and blocks them, so keep it out of the way.
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2830
blacklist dvb_usb_v2
"""

SDR_RECOVERY_DROPIN_PATH = "/etc/systemd/system/readsb.service.d/10-aiov2-sdr-recovery.conf"
SDR_BIASTEE_DROPIN_PATH = "/etc/systemd/system/readsb.service.d/20-aiov2-sdr-biastee.conf"
RTL_BIAST = "/usr/bin/rtl_biast"
SDR_WATCHDOG_SERVICE = "aiov2-sdr-watchdog.service"
SDR_WATCHDOG_TIMER = "aiov2-sdr-watchdog.timer"

SDR_RECOVERY_DROPIN = """# Installed by aiov2_ctl --install.
# The on-board RTL2832U intermittently wedges at tuner init or drops off
# USB, and readsb's Restart= loop cannot bring it back on its own. Wait
# for the tuner before starting, and power cycle the SDR rail after a
# failed run.
[Unit]
After=aiov2-rails-boot.service

[Service]
ExecStartPre=+/usr/bin/python3 /usr/local/bin/aiov2_ctl --sdr-recovery prestart
ExecStopPost=+/usr/bin/python3 /usr/local/bin/aiov2_ctl --sdr-recovery poststop
TimeoutStartSec=60
"""

SDR_BIASTEE_DROPIN = f"""# Installed by aiov2_ctl --sdr-biastee on.
# Debian/Kali readsb builds accept --enable-biastee but never switch the
# rtlsdr bias tee, so turn it (GPIO0) on just before readsb opens the
# tuner. It stays on until the SDR loses power and is set again on every
# start, including after an SDR rail power cycle.
[Service]
ExecStartPre=+{RTL_BIAST} -d 0 -b 1
"""

WIFI_MONITOR_CONF_PATH = "/etc/NetworkManager/conf.d/99-aiov2-usb-wifi-unmanaged.conf"

WIFI_MONITOR_CONF = """# Installed by aiov2_ctl --wifi-monitor on.
# Leave the AIO v2 USB WiFi (MT7961, mt7921u: wlan1 / wlan1mon) to
# monitor-mode tools. NetworkManager otherwise re-randomises its MAC for
# scans and fights monitor mode, which is why airmon-ng suggests
# "check kill" -- and that stops the NetworkManager and wpa_supplicant
# keeping the onboard WiFi online. Matching the driver survives both the
# MAC changes and airmon-ng renaming the interface.
[keyfile]
unmanaged-devices=driver:mt7921u
"""

SDR_WATCHDOG_SERVICE_UNIT = """[Unit]
Description=AIO v2 recover readsb if the RTL-SDR stops delivering samples

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/bin/aiov2_ctl --sdr-recovery check
"""

SDR_WATCHDOG_TIMER_UNIT = """[Unit]
Description=AIO v2 check readsb RTL-SDR sample flow every minute

[Timer]
OnBootSec=3min
OnUnitActiveSec=1min

[Install]
WantedBy=timers.target
"""


def rtlsdr_present():
    """True if an RTL2832U is on the USB bus."""
    out = GpioController.run(["lsusb"]) or ""
    return "0bda:2832" in out or "0bda:2838" in out


def dvb_driver_loaded():
    return "dvb_usb_rtl28xxu" in (GpioController.run(["lsmod"]) or "")


def rtlsdr_usable():
    """True if librtlsdr can claim the tuner (no DVB driver in the way)."""
    return rtlsdr_present() and not dvb_driver_loaded()


def set_sdr_rail(state):
    pin = GPIO_MAP.get("SDR")
    if pin is None:
        return False
    GpioController.set_gpio(pin, state)
    return True


def wait_for_rtlsdr(seconds=10, check=None):
    check = check or rtlsdr_present
    for _ in range(seconds):
        time.sleep(1)
        if check():
            return True
    return False


def cycle_sdr_rail():
    """
    Power cycle the SDR rail so the tuner re-enumerates.

    The RTL2832U can wedge after a driver unbind or a marginal power
    event — it drops to full-speed and answers descriptor reads with
    -EPIPE until the rail is dropped. We own that rail, so use it.
    """
    if not set_sdr_rail(False):
        return False

    print("Power cycling the SDR rail…")
    time.sleep(3)
    set_sdr_rail(True)
    return wait_for_rtlsdr()


def unload_dvb_driver():
    """
    Remove the kernel DVB-T driver from the tuner.

    It holds a reference for as long as the device is attached, so a
    plain `modprobe -r` fails while the rail is up — and the driver then
    simply rebinds the next time the device enumerates. Drop the rail
    first, unload, then bring it back with the blacklist in force.
    """
    if not dvb_driver_loaded():
        return True

    print("Unloading dvb_usb_rtl28xxu…")
    set_sdr_rail(False)
    time.sleep(2)

    for mod in ("dvb_usb_rtl28xxu", "dvb_usb_v2"):
        subprocess.call(["modprobe", "-r", mod],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    removed = not dvb_driver_loaded()

    set_sdr_rail(True)
    wait_for_rtlsdr()

    if not removed:
        print("dvb_usb_rtl28xxu is still loaded; a reboot will apply the blacklist.")

    return removed


def ensure_sdr_driver():
    """
    Keep the kernel DVB-T driver off the RTL2832U.

    Left to itself, dvb_usb_rtl28xxu binds the tuner at enumeration and
    librtlsdr consumers — readsb, SDR++, rtl_433 — fail with
    `usb_claim_interface error -6`. Blacklisting it is a standard
    RTL-SDR setup step and this board is no exception.
    """
    try:
        with open(SDR_BLACKLIST_PATH) as f:
            current = f.read()
    except OSError:
        current = None

    if current != SDR_BLACKLIST:
        print(f"Blacklisting the DVB-T driver → {SDR_BLACKLIST_PATH}")
        with open(SDR_BLACKLIST_PATH, "w") as f:
            f.write(SDR_BLACKLIST)
        os.chmod(SDR_BLACKLIST_PATH, 0o644)

    ensure_sdr_udev_rules()

    return unload_dvb_driver()


def ensure_sdr_udev_rules():
    """Keep USB autosuspend away from the tuner."""
    try:
        with open(SDR_UDEV_PATH) as f:
            current = f.read()
    except OSError:
        current = None

    if current == SDR_UDEV_RULES:
        return

    print(f"Disabling USB autosuspend for the tuner → {SDR_UDEV_PATH}")
    with open(SDR_UDEV_PATH, "w") as f:
        f.write(SDR_UDEV_RULES)
    os.chmod(SDR_UDEV_PATH, 0o644)

    subprocess.call(["udevadm", "control", "--reload-rules"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ensure_rtlsdr_ready():
    """Get the on-board tuner into a state librtlsdr can open."""
    ensure_sdr_driver()

    if rtlsdr_usable():
        return True

    if not rtlsdr_present():
        print("No RTL-SDR on the USB bus; attempting recovery…")
        cycle_sdr_rail()

    return rtlsdr_usable()


def ensure_adsb_decoder():
    """
    tar1090's installer refuses to run unless a decoder is already
    producing aircraft.json. Install readsb and get it emitting one.

    With no SDR dongle attached readsb exits at sdrOpen(), so fall back to
    reconfiguring the unit for net-only operation: the installer only
    needs the json to exist, and tar1090's own postinst rebuilds readsb
    and restarts the service partway through, which would wipe any
    throwaway instance we started alongside it.

    Returns a handle for stop_temp_decoder(), or None if nothing to undo.
    """
    if readsb_service_running():
        return None

    if not shutil.which("readsb"):
        print("\nInstalling readsb (required by tar1090)…")
        if apt_install(["readsb"]):
            print("readsb could not be installed; skipping tar1090.")
            return None

    ensure_rtlsdr_ready()

    subprocess.call(["systemctl", "start", "readsb"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for _ in range(10):
        if readsb_service_running():
            return None
        time.sleep(1)

    # A readsb that dies mid-stream can leave the tuner wedged: it drops
    # off the bus and re-enumerates at full speed with failing descriptor
    # reads. Stop the restart loop, power cycle the rail, and retry once.
    print("readsb did not come up; recovering the tuner…")
    subprocess.call(["systemctl", "stop", "readsb"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)

    if ensure_rtlsdr_ready():
        subprocess.call(["systemctl", "start", "readsb"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(10):
            if readsb_service_running():
                return None
            time.sleep(1)
        subprocess.call(["systemctl", "stop", "readsb"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print("No SDR decoder running; running readsb net-only for the install…")
    print("(tar1090 only needs aircraft.json to exist to complete setup.)")

    defaults = "/etc/default/readsb"
    try:
        with open(defaults) as f:
            original = f.read()
    except OSError:
        print("Could not read /etc/default/readsb; skipping tar1090 setup.")
        return None

    patched, count = re.subn(
        r"^RECEIVER_OPTIONS=.*$",
        'RECEIVER_OPTIONS="--net-only"',
        original,
        flags=re.MULTILINE
    )
    if not count:
        patched = original.rstrip("\n") + '\nRECEIVER_OPTIONS="--net-only"\n'

    with open(defaults, "w") as f:
        f.write(patched)

    subprocess.call(["systemctl", "restart", "readsb"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    handle = {"defaults": defaults, "original": original}

    for _ in range(15):
        if readsb_service_running():
            return handle
        time.sleep(1)

    print("Could not get a decoder to produce aircraft.json.")
    return handle


def stop_temp_decoder(handle):
    """Put the receiver config back the way we found it."""
    if not handle:
        return

    try:
        with open(handle["defaults"], "w") as f:
            f.write(handle["original"])
    except OSError as exc:
        print(f"Could not restore {handle['defaults']}: {exc}")
        return

    # tar1090's postinst leaves readsb stopped and disabled on purpose —
    # the launcher starts it on demand — so only reload a running unit.
    if subprocess.run(
        ["systemctl", "is-active", "readsb"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    ).returncode == 0:
        subprocess.call(["systemctl", "restart", "readsb"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def configure_meshtasticd_gpiochip():
    """Point meshtasticd's LoRa config at the RP1 gpiochip used on Pi 5 / CM5."""
    yaml_path = "/etc/meshtasticd/config.yaml"
    if not os.path.exists(yaml_path):
        return
    try:
        with open(yaml_path, "r") as f:
            content = f.read()
        if re.search(r"^[ \t]*gpiochip:", content, flags=re.MULTILINE):
            return
        content = re.sub(r"^([Ll]ora:.*)$", r"\1\n  gpiochip: 15", content, count=1, flags=re.MULTILINE)
        with open(yaml_path, "w") as f:
            f.write(content)
        print(f"Configured {yaml_path}: added gpiochip: 15 under Lora:")
    except Exception as e:
        print(f"Warning: failed to configure {yaml_path}: {e}")


def add_apps():
    if os.geteuid() != 0:
        print("This command requires sudo.")
        print("Run: sudo aiov2_ctl --add-apps")
        return 1

    draw_header("Installing HackerGadgets AIO applications")

    sdr_original_state = GpioController.get_gpio(GPIO_MAP["SDR"])
    if not sdr_original_state:
        print("Turning on SDR for installation...")
        GpioController.set_feature("SDR", True)
        time.sleep(2)

    subprocess.call(["apt", "update"])

    # The board package drops in the Meshtastic APT source, so install it
    # first and correct the suite afterwards.
    failed = apt_install(["hackergadgets-uconsole-aio-board"], recommends=True)

    try:
        if ensure_meshtastic_repo():
            subprocess.call(["apt", "update"])

        # Installed one at a time so a single broken package does not block
        # the rest of the set.
        failed += apt_install(["meshtastic-mui", "sdrpp-brown"])
        configure_meshtasticd_gpiochip()

        prepare_pygpsclient_profile()
        failed += apt_install(["pygpsclient"])
        finalize_pygpsclient()
        prepare_pygpsclient_profile()

        decoder = ensure_adsb_decoder()
        try:
            failed += apt_install(["tar1090"])
        finally:
            stop_temp_decoder(decoder)
    finally:
        if not sdr_original_state:
            print("Restoring SDR original status...")
            GpioController.set_feature("SDR", False)

    # tar1090 starts readsb on demand, so keep it out of OS autostart. Meshtastic
    # autostart is handled below by the mesh_on_boot setting.
    subprocess.call(["systemctl", "disable", "readsb.service"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if failed:
        print("\nInstallation finished with errors.")
        print("These packages could not be installed:")
        for pkg in failed:
            print(f"  - {pkg}")
        print("\nRe-run after checking the apt output above.\n")
    else:
        print("\nInstallation complete.\n")

    print(POST_INSTALL_TIPS)
    report_and_disable_mesh_autostart_if_default("Meshtastic boot config status:")

    if sys.stdin.isatty():
        print("\n" + "=" * 50)
        print("A reboot is recommended so the new drivers and services load correctly.")
        print("=" * 50 + "\n")
        while True:
            resp = input("Would you like to reboot now? [Y/n] ").strip().lower()
            if resp in ("y", "yes", ""):
                print("Rebooting system...")
                subprocess.call(["reboot"])
                break
            if resp in ("n", "no"):
                print("Please remember to reboot later.")
                break

    return 1 if failed else 0


def remove_apps():
    if os.geteuid() != 0:
        print("This command requires sudo.")
        print("Run: sudo aiov2_ctl --remove-apps")
        return 1

    draw_header("Removing HackerGadgets AIO applications")

    print("Stopping related services before removal...")
    subprocess.call(["systemctl", "disable", "--now", "readsb.service", "meshtasticd.service"])
    subprocess.call([
        "apt", "remove",
        "meshtastic-mui",
        "sdrpp-brown",
        "tar1090",
        "pygpsclient",
        "-y"
    ])

    subprocess.call([
        "apt", "remove",
        "hackergadgets-uconsole-aio-board",
        "-y"
    ])

    # No -y here: autoremove can pick up packages unrelated to this tool,
    # so let apt show the list and ask. Skipped when not interactive.
    if sys.stdin.isatty():
        print("\nRunning apt autoremove. Check the list before agreeing.")
        subprocess.call(["apt", "autoremove"])
    else:
        print("\nSkipping apt autoremove (not interactive). Run it yourself if wanted.")

    print("=== Starting readsb Cleanup Process ===")
    print("Removing binary programs, logs, and configuration directories...")
    for path in [
        "/lib/systemd/system/readsb.service",
        "/usr/bin/readsb",
        "/etc/default/readsb",
        "/usr/share/readsb",
        "/var/log/readsb",
        "/run/readsb",
        "/usr/local/bin/readsb-gain",
        "/usr/local/bin/readsb-set-location"
    ]:
        if os.path.exists(path) or os.path.islink(path):
            try:
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
            except Exception as e:
                print(f"Warning: Failed to remove {path}: {e}")

    subprocess.call(["systemctl", "daemon-reload"])

    uninstall_script = "/usr/local/share/tar1090/uninstall.sh"
    if os.path.isfile(uninstall_script):
        print("Found tar1090 web interface. Running map uninstaller...")
        subprocess.call(["/bin/bash", uninstall_script])
    else:
        print("tar1090 web interface not detected or already removed.")

    print("=== Cleanup complete! readsb components have been removed. ===")

    yaml_path = "/etc/meshtasticd/config.yaml"
    if os.path.exists(yaml_path):
        try:
            os.remove(yaml_path)
            print(f"Removed {yaml_path}")
        except Exception as e:
            print(f"Warning: Failed to remove {yaml_path}: {e}")

    print("\nApplications removed.")
    return 0



def fix_sdr():
    if os.geteuid() != 0:
        print("This command requires sudo.")
        print("Run: sudo aiov2_ctl --fix-sdr")
        return 1

    draw_header("Repairing RTL-SDR access")

    if ensure_rtlsdr_ready():
        print("\nRTL-SDR is available to librtlsdr.")
        out = GpioController.run(["lsusb"]) or ""
        for line in out.split("\n"):
            if "0bda:2832" in line or "0bda:2838" in line:
                print(f"  {line.strip()}")
        print("\nOnly one app can use the SDR at a time.")
        return 0

    print("\nRTL-SDR is still unavailable.")
    print("Check that the SDR rail is on (aiov2_ctl SDR on) and the module is seated.")
    return 1


def read_sysfs(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def rtlsdr_sysfs():
    """sysfs directory of the on-board RTL2832U, or None."""
    base = "/sys/bus/usb/devices"
    try:
        names = os.listdir(base)
    except OSError:
        return None

    for name in names:
        dev = os.path.join(base, name)
        if (read_sysfs(os.path.join(dev, "idVendor")) == "0bda"
                and read_sysfs(os.path.join(dev, "idProduct")) in ("2832", "2838")):
            return dev
    return None


def rtlsdr_high_speed():
    """A wedged tuner re-enumerates at full speed (12M) or not at all."""
    dev = rtlsdr_sysfs()
    return dev is not None and read_sysfs(os.path.join(dev, "speed")) == "480"


def rtlsdr_in_use():
    """True if another program (SDR++, gqrx, …) holds the tuner open."""
    dev = rtlsdr_sysfs()
    if not dev:
        return False

    try:
        node = "/dev/bus/usb/%03d/%03d" % (
            int(read_sysfs(os.path.join(dev, "busnum"))),
            int(read_sysfs(os.path.join(dev, "devnum"))),
        )
        return subprocess.call(["fuser", "-s", node],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL) == 0
    except (TypeError, ValueError, OSError):
        return False


def readsb_stalled(grace=150, stale=90):
    """
    readsb is up but the tuner has stopped delivering samples. readsb exits
    when it loses the device, but a wedged tuner can also leave it running
    with nothing coming in, or with no tuner at all: librtlsdr then falls
    back to direct sampling, which keeps samples flowing but decodes nothing.
    """
    if not readsb_service_running():
        return False

    pid = GpioController.run(["systemctl", "show", "readsb", "-p", "MainPID", "--value"])
    uptime = GpioController.run(["ps", "-o", "etimes=", "-p", pid or "0"])
    if not uptime or int(uptime) < grace:
        return False

    log = GpioController.run(["journalctl", "-b", "-u", "readsb", f"_PID={pid}",
                              "-o", "cat", "-q", "--no-pager"])
    if log and "No supported tuner found" in log:
        return True

    try:
        with open("/run/readsb/stats.json") as f:
            stats = json.load(f)
        return (time.time() - stats["now"] >= stale
                or stats["last1min"]["local"]["samples_processed"] == 0)
    except (OSError, ValueError, KeyError, TypeError):
        return True


def sdr_recovery(stage):
    """
    Hooks behind SDR_RECOVERY_DROPIN and the SDR watchdog timer. Always
    succeeds: a failing hook would only stack a unit failure on top of
    readsb's own Restart= loop.
    """
    # Leave a rail that was switched off on purpose alone.
    if not GpioController.get_gpio(GPIO_MAP["SDR"]):
        if stage == "prestart":
            print("SDR rail is off; turn it on with: aiov2_ctl SDR on")
        return 0

    if stage == "prestart":
        if not wait_for_rtlsdr(20, rtlsdr_high_speed):
            print("RTL-SDR has not enumerated at high speed.")

    elif stage == "poststop":
        # Set by systemd; "success" covers a plain `systemctl stop`.
        if os.environ.get("SERVICE_RESULT", "success") == "success":
            return 0
        if rtlsdr_in_use():
            print("RTL-SDR is held by another program; not power cycling.")
            return 0
        if not cycle_sdr_rail():
            print("RTL-SDR did not come back after the power cycle.")

    elif stage == "check":
        if not readsb_stalled():
            return 0
        print("readsb is running without a working tuner; recovering it…")
        subprocess.call(["systemctl", "stop", "readsb"])
        cycle_sdr_rail()
        subprocess.call(["systemctl", "start", "readsb"])

    return 0


def sdr_biastee(state):
    """Power the SDR antenna input (bias tee) whenever readsb runs."""
    enabled = os.path.exists(SDR_BIASTEE_DROPIN_PATH)
    if state == "status":
        print(f"SDR bias tee for readsb: {'ON' if enabled else 'OFF'}")
        return 0

    if os.geteuid() != 0:
        rerun_with_sudo(["--sdr-biastee", state])

    if state == "on" and not os.path.exists(RTL_BIAST):
        print(f"{RTL_BIAST} not found. Install it with: sudo apt install rtl-sdr")
        return 1

    readsb_active = subprocess.call(["systemctl", "is-active", "--quiet", "readsb"]) == 0

    if state == "on":
        os.makedirs(os.path.dirname(SDR_BIASTEE_DROPIN_PATH), exist_ok=True)
        with open(SDR_BIASTEE_DROPIN_PATH, "w") as f:
            f.write(SDR_BIASTEE_DROPIN)
        os.chmod(SDR_BIASTEE_DROPIN_PATH, 0o644)
    elif enabled:
        os.remove(SDR_BIASTEE_DROPIN_PATH)

    subprocess.call(["systemctl", "daemon-reload"])

    def biast_off():
        if os.path.exists(RTL_BIAST):
            subprocess.call([RTL_BIAST, "-d", "0", "-b", "0"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # The bias tee outlives the process that set it, so switching it off
    # needs the tuner free for a moment.
    if readsb_active and state == "on":
        subprocess.call(["systemctl", "restart", "readsb"])
    elif readsb_active:
        subprocess.call(["systemctl", "stop", "readsb"])
        biast_off()
        subprocess.call(["systemctl", "start", "readsb"])
    elif state == "off" and rtlsdr_present() and not rtlsdr_in_use():
        biast_off()

    print(f"SDR bias tee for readsb set to {state.upper()}")
    if state == "on" and not readsb_active:
        print("It switches on the next time readsb starts.")
    return 0


def wifi_monitor(state):
    """
    Reserve the USB WiFi for monitor mode, so airmon-ng no longer needs
    "check kill" and the onboard WiFi stays connected.
    """
    enabled = os.path.exists(WIFI_MONITOR_CONF_PATH)
    if state == "status":
        print(f"USB WiFi reserved for monitor mode: {'ON' if enabled else 'OFF'}")
        return 0

    if os.geteuid() != 0:
        rerun_with_sudo(["--wifi-monitor", state])

    if state == "on":
        os.makedirs(os.path.dirname(WIFI_MONITOR_CONF_PATH), exist_ok=True)
        with open(WIFI_MONITOR_CONF_PATH, "w") as f:
            f.write(WIFI_MONITOR_CONF)
        os.chmod(WIFI_MONITOR_CONF_PATH, 0o644)
    elif enabled:
        os.remove(WIFI_MONITOR_CONF_PATH)

    # A configuration reload applies unmanaged-devices without touching
    # active connections; a restart would drop the onboard WiFi.
    if shutil.which("nmcli"):
        subprocess.call(["nmcli", "general", "reload", "conf"])
    else:
        print("NetworkManager not found; the setting applies once it runs.")

    print(f"USB WiFi reserved for monitor mode: {state.upper()}")
    if state == "on":
        print("Start monitor mode with: sudo airmon-ng start wlan1")
        print("Skip 'airmon-ng check kill' — it takes the onboard WiFi down too.")
    return 0


def sync_rtc():
    if os.geteuid() != 0:
        print("RTC sync requires sudo.")
        print("Run: sudo aiov2_ctl --sync-rtc")
        return 1

    if not shutil.which("hwclock"):
        print("hwclock not found. RTC sync skipped.")
        return 1

    print("Syncing system time to RTC…")
    rc = subprocess.call(["hwclock", "-w"])
    if rc != 0:
        print(f"hwclock failed (exit code {rc}). RTC was not updated.")
        return 1
    print("RTC updated and synced.")
    return 0


# ==============================
# Boot device / EEPROM boot order
# ==============================
# BOOT_ORDER nibbles are processed right-to-left on the CM5 bootloader:
#   1 = SD/eMMC, 6 = NVMe, f = restart (loop).
SD_FIRST_BOOT_ORDER = "0xf61"     # SD/eMMC -> NVMe -> repeat
NVME_FIRST_BOOT_ORDER = "0xf16"   # NVMe -> SD/eMMC -> repeat


def have_rpi_eeprom_config():
    return shutil.which("rpi-eeprom-config") is not None


def get_boot_order():
    if not have_rpi_eeprom_config():
        return None

    try:
        out = subprocess.check_output(
            ["rpi-eeprom-config"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except Exception:
        return None

    for line in out.splitlines():
        line = line.strip()
        if line.startswith("BOOT_ORDER="):
            return line.split("=", 1)[1].strip()

    return None


def set_boot_order(order):
    if not have_rpi_eeprom_config():
        print("Missing rpi-eeprom-config.")
        print("Try: sudo apt install rpi-eeprom")
        return 1

    try:
        current = subprocess.check_output(
            ["rpi-eeprom-config"],
            stderr=subprocess.STDOUT,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        print("Failed to read EEPROM config:")
        print(e.output)
        return 1

    found = False
    new_lines = []

    for line in current.splitlines():
        if line.startswith("BOOT_ORDER="):
            new_lines.append(f"BOOT_ORDER={order}")
            found = True
        else:
            new_lines.append(line)

    if not found:
        new_lines.append(f"BOOT_ORDER={order}")

    with tempfile.NamedTemporaryFile("w", delete=False) as f:
        tmp_path = f.name
        f.write("\n".join(new_lines) + "\n")

    try:
        subprocess.check_call(["rpi-eeprom-config", "--apply", tmp_path])
    except subprocess.CalledProcessError:
        print("Failed to apply EEPROM config.")
        return 1
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    return 0


def current_root_device():
    try:
        return subprocess.check_output(
            ["findmnt", "-n", "-o", "SOURCE", "/"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


def current_boot_device_guess(rootdev=None):
    if rootdev is None:
        rootdev = current_root_device()

    if rootdev.startswith("/dev/nvme"):
        return "nvme"
    if rootdev.startswith("/dev/mmcblk") or rootdev.startswith("/dev/sd"):
        return "sd"

    return "unknown"


def boot_device_mode_from_order(order):
    if order == SD_FIRST_BOOT_ORDER:
        return "SD/eMMC first, NVMe fallback"
    if order == NVME_FIRST_BOOT_ORDER:
        return "NVMe first, SD/eMMC fallback"
    if not order:
        return "unknown"
    return "custom/unknown"


def print_boot_device_status():
    order = get_boot_order()
    rootdev = current_root_device()
    guess = current_boot_device_guess(rootdev)

    print("Boot device status")
    print("====================")
    print(f"Current root device : {rootdev}")
    print(f"Likely booted from  : {guess}")
    print(f"BOOT_ORDER          : {order or 'unknown'}")
    print(f"Mode                : {boot_device_mode_from_order(order)}")
    print()
    print("Known modes:")
    print(f"  sd   : {SD_FIRST_BOOT_ORDER}   SD/eMMC -> NVMe -> repeat")
    print(f"  nvme : {NVME_FIRST_BOOT_ORDER}  NVMe -> SD/eMMC -> repeat")


def maybe_prompt_reboot():
    if not sys.stdin.isatty():
        print("Reboot required for boot order change to take effect.")
        return

    print()
    ans = input("Reboot now? [y/N] ").strip().lower()
    if ans in ("y", "yes"):
        subprocess.call(["systemctl", "reboot"])
    else:
        print("Not rebooting. Change takes effect after next reboot.")


def boot_device_command(mode):
    mode = (mode or "status").lower()

    if mode == "status":
        print_boot_device_status()
        return 0

    # Read once up front; reused for the toggle decision and the no-op check.
    # May be None if reading the EEPROM isn't permitted unprivileged.
    current = get_boot_order()

    if mode in ("sd", "sdcard", "scard"):
        target = SD_FIRST_BOOT_ORDER
        label = "SD/eMMC first, NVMe fallback"
    elif mode == "nvme":
        target = NVME_FIRST_BOOT_ORDER
        label = "NVMe first, SD/eMMC fallback"
    elif mode == "toggle":
        if current == NVME_FIRST_BOOT_ORDER:
            target = SD_FIRST_BOOT_ORDER
            label = "SD/eMMC first, NVMe fallback"
        else:
            target = NVME_FIRST_BOOT_ORDER
            label = "NVMe first, SD/eMMC fallback"
    else:
        print("Usage: sudo aiov2_ctl --boot-device <status|sd|nvme|toggle>")
        return 2

    # Short-circuit a no-op before prompting for sudo. If the read above was
    # unprivileged-blocked (current is None) we fall through and escalate.
    if current is not None and current == target:
        print(f"Already set: {label}. No change.")
        return 0

    # Re-execs and re-enters this function as root, where `current` is read
    # authoritatively and the no-op check above catches an already-set order.
    if os.geteuid() != 0:
        rerun_with_sudo(["--boot-device", mode])

    print(f"Setting boot mode: {label}")
    rc = set_boot_order(target)
    if rc != 0:
        return rc

    print()
    print_boot_device_status()
    maybe_prompt_reboot()
    return 0


def parse_pinctrl_level(out: str):
    """
    Extract pin level from `pinctrl get <pin>` output.
    Returns: "hi", "lo", "--", or None.
    """
    if not out:
        return None

    if re.search(r"\bhi\b", out):
        return "hi"
    if re.search(r"\blo\b", out):
        return "lo"
    if re.search(r"(?<!\S)--(?!\S)", out):
        return "--"

    return None


# ==============================
# GPIO Helpers
# ==============================
class GpioController:
    @staticmethod
    def run(cmd):
        try:
            return subprocess.check_output(cmd, text=True).strip()
        except Exception:
            return None

    @staticmethod
    def _service_active(name):
        cmd = ["systemctl", "is-active", name]
        if subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            return True

        return subprocess.run(
            ["sudo", "-n", *cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0

    @staticmethod
    def _service_enabled(name):
        cmd = ["systemctl", "is-enabled", name]
        if subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            return True

        return subprocess.run(
            ["sudo", "-n", *cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode == 0

    @staticmethod
    def _service_installed(name):
        cmd = ["systemctl", "show", "-p", "LoadState", name]
        try:
            output = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
            return output != "LoadState=not-found"
        except Exception:
            return False

    @staticmethod
    def _run_service(action, service_name="meshtasticd"):
        if action == "start":
            reset_cmd = ["systemctl", "reset-failed", service_name]
            if os.geteuid() == 0:
                subprocess.run(reset_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.run(["sudo", "-n", *reset_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if isinstance(action, (list, tuple)):
            cmd = ["systemctl", *action, service_name]
        else:
            cmd = ["systemctl", action, service_name]
        if os.geteuid() == 0:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return

        subprocess.run(
            ["sudo", "-n", *cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @staticmethod
    def set_gpio(pin, state):
        subprocess.call([
            "pinctrl",
            "set",
            str(pin),
            "op",
            "dh" if state else "dl",
        ])

    @staticmethod
    def set_feature(feature, state):
        pin = GPIO_MAP.get(feature)
        if pin is None:
            return
        GpioController.set_gpio(pin, state)
        if feature == "LORA":
            if state:
                # Allow device enumeration after power-on
                time.sleep(0.5)
                GpioController._run_service("start", "meshtasticd.service")
            else:
                GpioController._run_service("stop", "meshtasticd.service")
                disable_mesh_autostart_if_default(announce=False)

    @staticmethod
    def get_gpio(pin):
        state, _ = GpioController.get_pin_state(pin)
        return state

    @staticmethod
    def get_pin_state(pin):
        out = GpioController.run(["pinctrl", "get", str(pin)])
        level = parse_pinctrl_level(out)

        # Truth-first
        if level == "hi":
            return True, "pinctrl"
        if level == "lo":
            return False, "pinctrl"

        # Only not-driven pins use explicit per-pin boot defaults
        if level == "--":
            return bool(BOOT_DEFAULTS.get(pin, False)), "boot_default"

        # Unparseable / command-failure path
        return False, "unknown"

# ==============================
# Telemetry
# ==============================
class Telemetry:
    @staticmethod
    def _read_int(path):
        try:
            with open(path) as f:
                return int(f.read().strip())
        except Exception:
            return None

    @staticmethod
    def ac_online():
        v = Telemetry._read_int(
            f"/sys/class/power_supply/{AC_SUPPLY}/online"
        )
        return bool(v) if v is not None else None

    @staticmethod
    def battery_status():
        try:
            with open(
                f"/sys/class/power_supply/{BATTERY_SUPPLY}/status"
            ) as f:
                return f.read().strip()
        except Exception:
            return None

    @staticmethod
    def battery_capacity():
        return Telemetry._read_int(
            f"/sys/class/power_supply/{BATTERY_SUPPLY}/capacity"
        )

    @staticmethod
    def battery_v_i_w():
        v = Telemetry._read_int(
            f"/sys/class/power_supply/{BATTERY_SUPPLY}/voltage_now"
        )
        i = Telemetry._read_int(
            f"/sys/class/power_supply/{BATTERY_SUPPLY}/current_now"
        )
        if v is None or i is None:
            return None

        v /= 1e6
        i /= 1e6

        return {
            "voltage": round(v, 2),
            "current": round(i, 2),      # signed
            "power": round(v * i, 2),    # signed
        }

    @staticmethod
    def power_summary():
        viw = Telemetry.battery_v_i_w()
        if not viw:
            return None

        ac = Telemetry.ac_online()
        status = Telemetry.battery_status() or "n/a"
        cap = Telemetry.battery_capacity()

        cur = viw["current"]

        if cur > 0.05:
            direction = "charging"
        elif cur < -0.05:
            direction = "discharging"
        else:
            direction = "idle"

        if ac and direction == "charging":
            mode = "AC powering system + battery"
        elif ac:
            mode = "AC powering system"
        else:
            mode = "Battery powering system"

        return {
            "source": "AC" if ac else "BAT",
            "status": status,
            "capacity": cap,
            "direction": direction,
            "mode": mode,
            "voltage": viw["voltage"],
            "current": round(abs(cur), 2),
            "power": round(abs(viw["power"]), 2),
        }

# ==============================
# Status (snapshot)
# ==============================
# ==============================
# Companion devices (Flipper Zero, ESP32 boards)
# ==============================
# Detection only for now: nothing is opened, written to or reset.
USB_SYSFS_BASE = "/sys/bus/usb/devices"

# (vendor, product) -> (kind, label)
COMPANION_IDS = {
    ("0483", "5740"): ("flipper", "Flipper Zero"),
    ("303a", "0002"): ("esp32", "ESP32-S2 (native USB)"),
    ("303a", "1001"): ("esp32", "ESP32 (USB serial/JTAG)"),
}
# Generic USB-serial chips found on many ESP32 boards. Only a hint: plenty of
# other things use these, so they are reported as "possible".
GENERIC_SERIAL_IDS = {
    ("10c4", "ea60"): "CP210x USB-serial",
    ("1a86", "7523"): "CH340 USB-serial",
}

COMPANION_UDEV_PATH = "/etc/udev/rules.d/99-aiov2-companions.rules"
# uaccess gives the logged in user access to the port (no world writable
# nodes), and ID_MM_DEVICE_IGNORE stops ModemManager grabbing it.
COMPANION_UDEV_RULES = """# Managed by aiov2_ctl --install
# Flipper Zero
SUBSYSTEM=="tty", ATTRS{idVendor}=="0483", ATTRS{idProduct}=="5740", TAG+="uaccess", ENV{ID_MM_DEVICE_IGNORE}="1"
# Espressif native USB (ESP32-S2 and friends)
SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", TAG+="uaccess", ENV{ID_MM_DEVICE_IGNORE}="1"
"""


def _tty_nodes(dev_path):
    """tty names (ttyACM0, ttyUSB0...) belonging to a USB device, if any."""
    names = []
    try:
        for iface in os.listdir(dev_path):
            tty_dir = os.path.join(dev_path, iface, "tty")
            if os.path.isdir(tty_dir):
                names += sorted(os.listdir(tty_dir))
    except OSError:
        pass
    return names


def detect_companions():
    """
    Look for a Flipper Zero or ESP32 style board on the USB bus.

    Returns a list of dicts: kind ('flipper', 'esp32', 'possible-esp32'),
    name, vid, pid, tty (e.g. '/dev/ttyACM0' or None), serial.
    """
    found = []
    try:
        names = sorted(os.listdir(USB_SYSFS_BASE))
    except OSError:
        return found

    for name in names:
        # Skip interfaces such as "1-1:1.0"; we want whole devices.
        if ":" in name:
            continue
        dev = os.path.join(USB_SYSFS_BASE, name)
        vid = read_sysfs(os.path.join(dev, "idVendor"))
        pid = read_sysfs(os.path.join(dev, "idProduct"))
        if not vid or not pid:
            continue

        key = (vid.lower(), pid.lower())
        if key in COMPANION_IDS:
            kind, label = COMPANION_IDS[key]
        elif key in GENERIC_SERIAL_IDS:
            kind, label = "possible-esp32", GENERIC_SERIAL_IDS[key]
        else:
            continue

        ttys = _tty_nodes(dev)
        found.append({
            "kind": kind,
            "name": read_sysfs(os.path.join(dev, "product")) or label,
            "label": label,
            "vid": key[0],
            "pid": key[1],
            "tty": f"/dev/{ttys[0]}" if ttys else None,
            "serial": read_sysfs(os.path.join(dev, "serial")),
        })
    return found


def companions_summary_line(devices):
    if not devices:
        return "Companions: none"
    parts = []
    for d in devices:
        tag = d["label"] + (" (possible)" if d["kind"] == "possible-esp32" else "")
        parts.append(f"{tag}" + (f" @ {d['tty']}" if d["tty"] else ""))
    return "Companions: " + ", ".join(parts)


def show_companions():
    devices = detect_companions()
    print("Companion devices")
    print("-----------------")
    if not devices:
        print("None found.")
        usb_pin = GPIO_MAP.get("USB")
        if usb_pin is not None and not GpioController.get_gpio(usb_pin):
            print("The USB rail is OFF. Turn it on with: aiov2_ctl USB on")
        print("Plug the device in and check the cable carries data, not just power.")
        return 0

    for d in devices:
        print(f"{d['label']}" + (" (possible, generic USB-serial chip)" if d["kind"] == "possible-esp32" else ""))
        print(f"  USB ID : {d['vid']}:{d['pid']}")
        print(f"  Product: {d['name']}")
        print(f"  Serial : {d['serial'] or 'n/a'}")
        tty = d["tty"]
        print(f"  Port   : {tty or 'no serial port found'}")
        if tty and not os.access(tty, os.R_OK | os.W_OK):
            print("  Access : no permission. Run 'sudo aiov2_ctl --install' to add the udev rule, "
                  "then replug the device.")
    return 0


def ensure_companion_udev_rules():
    """Install the udev rule so a logged in user can open the serial port."""
    try:
        with open(COMPANION_UDEV_PATH) as f:
            if f.read() == COMPANION_UDEV_RULES:
                return
    except OSError:
        pass

    print(f"Installing companion device udev rules → {COMPANION_UDEV_PATH}\n")
    with open(COMPANION_UDEV_PATH, "w") as f:
        f.write(COMPANION_UDEV_RULES)
    os.chmod(COMPANION_UDEV_PATH, 0o644)
    subprocess.call(["udevadm", "control", "--reload-rules"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.call(["udevadm", "trigger", "--subsystem-match=tty"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def show_status():
    print("AIO v2 Status")
    print("====================")

    debug = os.environ.get("AIOV2_CTL_DEBUG") == "1"

    for f, p in GPIO_MAP.items():
        state_bool, source = GpioController.get_pin_state(p)
        state = "ON" if state_bool else "OFF"
        if debug:
            print(f"{f:<5} GPIO{p}: {state} ({source})")
        else:
            print(f"{f:<5} GPIO{p}: {state}")

    print("--------------------")
    print(companions_summary_line(detect_companions()))
    print("--------------------")

    summary = Telemetry.power_summary()
    if not summary:
        print("Power: n/a")
        return

    print(f"Source    : {summary['source']}")
    print(f"Status    : {summary['status']}")
    print(f"Capacity  : {summary['capacity']}%")
    print(f"Direction : {summary['direction']}")
    print(f"Mode      : {summary['mode']}")
    print(f"Voltage   : {summary['voltage']} V")
    print(f"Current   : {summary['current']} A")
    print(f"Power     : {summary['power']} W")

# ==============================
# Sampling / Measurement
# ==============================
def sample_battery_power(seconds=3.0, interval=0.2):
    currents, powers = [], []
    voltage = None

    end = time.time() + seconds
    while time.time() < end:
        viw = Telemetry.battery_v_i_w()
        if viw:
            voltage = viw["voltage"]
            currents.append(viw["current"])
            powers.append(viw["power"])
        time.sleep(interval)

    if not currents:
        return None

    return {
        "voltage": voltage,
        "current_mean": round(mean(currents), 2),
        "power_mean": round(mean(powers), 2),
        "samples": len(currents),
    }

def measure_feature(feature, seconds=3.0, settle=1.0, interval=0.2):
    feature = feature.upper()
    if feature not in GPIO_MAP:
        print(f"Unknown feature '{feature}'")
        return 2

    pin = GPIO_MAP[feature]
    orig = GpioController.get_gpio(pin)

    print(f"Measure: {feature} (GPIO{pin})")
    print(f"Power source: {'AC online' if Telemetry.ac_online() else 'Battery'}")
    print(f"Current state: {'ON' if orig else 'OFF'}")

    try:
        if orig:
            on = sample_battery_power(seconds, interval)
            GpioController.set_feature(feature, False)
            time.sleep(settle)
            off = sample_battery_power(seconds, interval)
        else:
            off = sample_battery_power(seconds, interval)
            GpioController.set_feature(feature, True)
            time.sleep(settle)
            on = sample_battery_power(seconds, interval)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    finally:
        # Always put the rail back how we found it.
        GpioController.set_feature(feature, orig)

    if not on or not off:
        print("Sampling failed.")
        return 1

    print("\nResults")
    print("----------------")
    print(f"OFF: {off['power_mean']:.2f} W")
    print(f"ON : {on['power_mean']:.2f} W")
    print(f"Δ   {on['power_mean'] - off['power_mean']:+.2f} W")
    return 0

# ==============================
# Live modes
# ==============================
def show_power_live():
    try:
        while True:
            summary = Telemetry.power_summary()
            os.system("clear")
            print("Power Monitor (Ctrl+C)")
            print("----------------------")

            if not summary:
                print("Telemetry unavailable")
            else:
                print(f"Source: {summary['source']} | {summary['status']}")
                print(
                    f"Battery: {summary['capacity']}% | "
                    f"{summary['current']} A ({summary['direction']})"
                )
                print(
                    f"Battery rail: {summary['voltage']} V | "
                    f"{summary['power']} W"
                )
                print(f"Mode: {summary['mode']}")

            time.sleep(1)
    except KeyboardInterrupt:
        pass

def show_watch():
    try:
        while True:
            states = [
                f"{f}:{'ON' if GpioController.get_gpio(p) else 'OFF'}"
                for f, p in GPIO_MAP.items()
            ]

            summary = Telemetry.power_summary()
            if summary:
                print(
                    "  ".join(states)
                    + f"  Src:{summary['source']}"
                    + f"  Batt:{summary['status']}"
                    + f"  {summary['capacity']}%"
                    + f"  {summary['power']:.2f}W",
                    end="\r",
                    flush=True,
                )
            else:
                print("  ".join(states) + "  Power:n/a", end="\r", flush=True)

            time.sleep(1)
    except KeyboardInterrupt:
        pass

# ==============================
# GUI (Tray + Left-click window)
# ==============================
def run_gui():
    if os.geteuid() == 0:
        print("Do not run GUI as root.")
        sys.exit(1)

    if is_ssh_session() and not has_desktop_session():
        print("GUI cannot be launched over SSH.")
        print("Run this on the device desktop or enable autostart.")
        sys.exit(1)

    if not has_desktop_session():
        print("No graphical session detected.")
        print("Run the GUI from the device desktop or enable autostart.")
        sys.exit(1)

    try:
        from PyQt6.QtWidgets import (
            QApplication, QSystemTrayIcon, QMenu,
            QWidget, QVBoxLayout, QLabel, QCheckBox
        )
        from PyQt6.QtGui import QAction, QActionGroup, QIcon, QCursor
        from PyQt6.QtCore import Qt, QTimer, QLockFile, QObject, pyqtSignal
    except ImportError:
        # PyQt6 from apt lives in the system Python's dist-packages. If a venv
        # python (e.g. ~/.pygpsclient/bin) is first on PATH it can't see it,
        # so retry once with the system interpreter before giving up.
        system_python = "/usr/bin/python3"
        if (
            os.path.exists(system_python)
            and os.path.realpath(sys.executable) != os.path.realpath(system_python)
            and not os.environ.get("AIOV2_CTL_REEXEC")
        ):
            os.environ["AIOV2_CTL_REEXEC"] = "1"
            os.execv(system_python, [system_python, os.path.realpath(__file__), *sys.argv[1:]])

        print("PyQt6 could not be imported.")
        print(f"Python in use: {sys.executable}")
        print("Install it with:")
        print("  sudo apt install python3-pyqt6")
        print("If it is already installed, a virtualenv python may be first on your PATH.")
        print("Check with: which python3  (expected: /usr/bin/python3)")
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setDesktopFileName("aiov2_ctl")

    # QLockFile detects stale locks left by a crashed process, unlike
    # QSharedMemory which can stay claimed on Linux after a crash.
    lock_dir = os.path.expanduser("~/.cache/aiov2_ctl")
    os.makedirs(lock_dir, exist_ok=True)
    instance_lock = QLockFile(os.path.join(lock_dir, "gui.lock"))
    instance_lock.setStaleLockTime(0)
    if not instance_lock.tryLock(100):
        print("AIOv2 GUI already running.")
        return

    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("No system tray available in this session.")
        print("On GNOME (e.g. Ubuntu) install the AppIndicator extension:")
        print("  sudo apt install gnome-shell-extension-appindicator")
        sys.exit(1)

    tray = QSystemTrayIcon()

    # 1) Prefer installed PCB icon (system-wide asset path)
    icon = QIcon("/usr/local/share/aiov2_ctl/img/pcb-board.png")

    # 2) Fallback to desktop theme icon (same as autostart)
    if icon.isNull():
        icon = QIcon.fromTheme("utilities-system-monitor")

    # 3) Absolute last-resort Qt fallback
    if icon.isNull():
        icon = app.style().standardIcon(
            app.style().StandardPixmap.SP_ComputerIcon
        )

    tray.setIcon(icon)
    tray.setToolTip("AIOv2 Tools")



    # -------- Init Right-click menu --------
    menu = QMenu()
    actions = {}


    meta = load_install_meta()
    repo = None

    if meta and "repo_path" in meta and os.path.isdir(meta["repo_path"]):
        repo = os.path.realpath(meta["repo_path"])

    update_action = QAction("Checking for updates…")
    update_action.setEnabled(False)


    for f in GPIO_MAP:
        a = QAction(f)
        a.setCheckable(True)
        a.triggered.connect(
            lambda checked, f=f: GpioController.set_feature(f, checked)
        )
        menu.addAction(a)
        actions[f] = a

    menu.addSeparator()
    boot_menu = QMenu("Rails on boot")
    boot_actions = {}

    for f in GPIO_MAP:
        a = QAction(f)
        a.setCheckable(True)
        def on_boot_toggled(checked, f=f):
            state = "on" if checked else "off"
            rc = subprocess.call(
                ["sudo", "-n", "/usr/local/bin/aiov2_ctl", "--boot-rail", f, state],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if rc != 0:
                tray.showMessage(
                    "AIOv2 Boot Rails",
                    "Boot rail changes need permission. Run once: sudo aiov2_ctl --setup-sudoers",
                    QSystemTrayIcon.MessageIcon.Warning,
                    5000,
                )
            refresh()

        a.triggered.connect(on_boot_toggled)
        boot_menu.addAction(a)
        boot_actions[f] = a

    menu.addMenu(boot_menu)

    # Boot device (EEPROM boot order). Reads spawn a subprocess, so this
    # submenu is refreshed only at startup and after a change, not per tick.
    boot_device_menu = QMenu("Boot device")
    boot_device_actions = {}
    boot_device_group = QActionGroup(boot_device_menu)
    boot_device_group.setExclusive(True)

    def refresh_boot_device():
        order = get_boot_order()
        boot_device_actions["sd"].blockSignals(True)
        boot_device_actions["nvme"].blockSignals(True)
        boot_device_actions["sd"].setChecked(order == SD_FIRST_BOOT_ORDER)
        boot_device_actions["nvme"].setChecked(order == NVME_FIRST_BOOT_ORDER)
        boot_device_actions["sd"].blockSignals(False)
        boot_device_actions["nvme"].blockSignals(False)

    for mode, text in (("sd", "SD/eMMC first"), ("nvme", "NVMe first")):
        a = QAction(text)
        a.setCheckable(True)
        def on_boot_device_selected(checked, mode=mode):
            rc = subprocess.call(
                ["sudo", "-n", "/usr/local/bin/aiov2_ctl", "--boot-device", mode],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if rc != 0:
                tray.showMessage(
                    "AIOv2 Boot Device",
                    "Boot device changes need permission. Run once: sudo aiov2_ctl --setup-sudoers",
                    QSystemTrayIcon.MessageIcon.Warning,
                    5000,
                )
            else:
                tray.showMessage(
                    "AIOv2 Boot Device",
                    "Boot order updated. Reboot for it to take effect.",
                    QSystemTrayIcon.MessageIcon.Information,
                    5000,
                )
            refresh_boot_device()

        a.triggered.connect(on_boot_device_selected)
        boot_device_group.addAction(a)
        boot_device_menu.addAction(a)
        boot_device_actions[mode] = a

    menu.addMenu(boot_device_menu)
    refresh_boot_device()

    app_services_menu = QMenu("App Services")

    mesh_title_action = QAction("meshtasticd.service")
    mesh_title_action.setEnabled(False)
    mesh_status_action = QAction("    Status: Stopped")
    mesh_status_action.setEnabled(False)
    mesh_start_action = QAction("    ▶ Start")
    mesh_stop_action = QAction("    ■ Stop")
    mesh_restart_action = QAction("    ↻ Restart")
    mesh_start_action.triggered.connect(lambda _=False: (GpioController._run_service("start", "meshtasticd.service"), refresh()))
    mesh_stop_action.triggered.connect(lambda _=False: (GpioController._run_service("stop", "meshtasticd.service"), refresh()))
    mesh_restart_action.triggered.connect(lambda _=False: (GpioController._run_service("restart", "meshtasticd.service"), refresh()))

    app_services_menu.addAction(mesh_title_action)
    app_services_menu.addAction(mesh_status_action)
    app_services_menu.addAction(mesh_start_action)
    app_services_menu.addAction(mesh_stop_action)
    app_services_menu.addAction(mesh_restart_action)

    app_services_menu.addSeparator()

    readsb_title_action = QAction("readsb.service")
    readsb_title_action.setEnabled(False)
    readsb_status_action = QAction("    Status: Stopped")
    readsb_status_action.setEnabled(False)
    readsb_start_action = QAction("    ▶ Start")
    readsb_stop_action = QAction("    ■ Stop")
    readsb_restart_action = QAction("    ↻ Restart")
    readsb_start_action.triggered.connect(lambda _=False: (GpioController._run_service("start", "readsb.service"), refresh()))
    readsb_stop_action.triggered.connect(lambda _=False: (GpioController._run_service("stop", "readsb.service"), refresh()))
    readsb_restart_action.triggered.connect(lambda _=False: (GpioController._run_service("restart", "readsb.service"), refresh()))

    app_services_menu.addAction(readsb_title_action)
    app_services_menu.addAction(readsb_status_action)
    app_services_menu.addAction(readsb_start_action)
    app_services_menu.addAction(readsb_stop_action)
    app_services_menu.addAction(readsb_restart_action)

    menu.addMenu(app_services_menu)

    menu.addSeparator()
    power_action = QAction("Power: -- W")
    power_action.setEnabled(False)
    menu.addAction(power_action)

    companion_action = QAction("Companions: --")
    companion_action.setEnabled(False)
    menu.addAction(companion_action)

    menu.addSeparator()
    menu.addAction(update_action)

    menu.addSeparator()
    menu.addAction("Quit", app.quit)
    menu_active = [False]
    def on_menu_show():
        menu_active[0] = True
        refresh(force=True)
    def on_menu_hide():
        menu_active[0] = False
        
    menu.aboutToShow.connect(on_menu_show)
    menu.aboutToHide.connect(on_menu_hide)
    tray.setContextMenu(menu)

    # -------- Left-click window --------
    window = QWidget()
    window.setWindowTitle("AIO v2 Status")
    window.setWindowFlags(Qt.WindowType.Tool)
    layout = QVBoxLayout(window)

    power_label = QLabel("Power: -- W")
    layout.addWidget(power_label)

    checkboxes = {}
    for f in GPIO_MAP:
        cb = QCheckBox(f)
        cb.toggled.connect(
            lambda checked, f=f: GpioController.set_feature(f, checked)
        )
        layout.addWidget(cb)
        checkboxes[f] = cb

    def refresh(force=False):
        if not force and not window.isVisible() and not menu_active[0]:
            return
        summary = Telemetry.power_summary()
        rails_on_boot = get_rails_on_boot_config(system_only=True)
        if summary:
            power_label.setText(
                f"{summary['mode']} | {summary['power']} W | {summary['capacity']}%"
            )
            power_action.setText(f"Power: {summary['power']} W")
        else:
            power_label.setText("Power: n/a")
            power_action.setText("Power: n/a")

        companion_action.setText(companions_summary_line(detect_companions()))

        for f, p in GPIO_MAP.items():
            state = GpioController.get_gpio(p)

            checkboxes[f].blockSignals(True)
            checkboxes[f].setChecked(state)
            checkboxes[f].blockSignals(False)

            actions[f].blockSignals(True)
            actions[f].setChecked(state)
            actions[f].blockSignals(False)

            boot_actions[f].blockSignals(True)
            boot_actions[f].setChecked(bool(rails_on_boot.get(f, False)))
            boot_actions[f].blockSignals(False)

        if not GpioController._service_installed("meshtasticd.service"):
            mesh_state = "Not installed"
            mesh_start_action.setVisible(False)
            mesh_stop_action.setVisible(False)
            mesh_restart_action.setVisible(False)
        else:
            mesh_active = GpioController._service_active("meshtasticd.service")
            mesh_state = "Running" if mesh_active else "Stopped"
            mesh_start_action.setVisible(True)
            mesh_stop_action.setVisible(True)
            mesh_restart_action.setVisible(True)
            mesh_start_action.setEnabled(not mesh_active)
            mesh_stop_action.setEnabled(mesh_active)

        mesh_status_action.setText(f"    Status: {mesh_state}")

        if not GpioController._service_installed("readsb.service"):
            readsb_state = "Not installed"
            readsb_start_action.setVisible(False)
            readsb_stop_action.setVisible(False)
            readsb_restart_action.setVisible(False)
        else:
            readsb_active = GpioController._service_active("readsb.service")
            readsb_state = "Running" if readsb_active else "Stopped"
            readsb_start_action.setVisible(True)
            readsb_stop_action.setVisible(True)
            readsb_restart_action.setVisible(True)
            readsb_start_action.setEnabled(not readsb_active)
            readsb_stop_action.setEnabled(readsb_active)
            
        readsb_status_action.setText(f"    Status: {readsb_state}")

    def on_activate(reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            refresh(force=True)
            window.move(QCursor.pos())
            window.show()
            window.raise_()
            window.activateWindow()

    tray.activated.connect(on_activate)

    timer = QTimer()
    timer.timeout.connect(refresh)
    timer.start(1000)


    class UpdateSignal(QObject):
        result = pyqtSignal(bool)

    update_signal = UpdateSignal()

    def run_update():
        subprocess.Popen([
            "x-terminal-emulator",
            "-e",
            "aiov2_ctl --update"
        ])
        update_action.setText("Updating...")
        update_action.setEnabled(False)
        # Recheck after 30 seconds (time for update to complete)
        QTimer.singleShot(30000, update_check)

    # Connected once. Connecting inside update_check stacked handlers.
    update_action.triggered.connect(run_update)

    def on_update_result(available):
        if available:
            update_action.setText("Update available → Install")
            update_action.setEnabled(True)
            tray.showMessage(
                "AIOv2 Update",
                "A new version is available. Right-click to install.",
                QSystemTrayIcon.MessageIcon.Information,
                30000  # Show for 30 seconds
            )
        else:
            update_action.setText("Up to date")
            update_action.setEnabled(False)

    update_signal.result.connect(on_update_result)

    def update_check():
        if not repo:
            update_action.setText("Not installed from git")
            return

        # git fetch can take seconds, so keep it off the Qt main thread.
        threading.Thread(
            target=lambda: update_signal.result.emit(check_update_available(repo)),
            daemon=True,
        ).start()

    # Initial check after 10 seconds
    QTimer.singleShot(10000, update_check)
    
    # Periodic recheck every 3 hours
    update_timer = QTimer()
    update_timer.timeout.connect(update_check)
    update_timer.start(10800000)  # 3 hours in milliseconds


    tray.show()
    sys.exit(app.exec())


def ensure_sudo_path_link(dst):
    """
    sudo's secure_path does not include /usr/local/bin on every distro
    (Kali, for one), which breaks the documented `sudo aiov2_ctl ...`
    commands. Drop a symlink somewhere secure_path does cover.
    """
    try:
        out = subprocess.check_output(
            ["sudo", "-n", "sh", "-c", "printf %s \"$PATH\""],
            stderr=subprocess.DEVNULL,
            text=True
        )
        sudo_path = out.split(":")
    except (subprocess.CalledProcessError, OSError):
        return

    bin_dir = os.path.dirname(dst)
    if bin_dir in sudo_path:
        return

    for candidate in ("/usr/bin", "/usr/sbin"):
        if candidate not in sudo_path:
            continue
        link = os.path.join(candidate, os.path.basename(dst))
        if os.path.islink(link) and os.path.realpath(link) == os.path.realpath(dst):
            return
        if os.path.exists(link) and not os.path.islink(link):
            return
        print(f"sudo does not search {bin_dir}; linking {link} → {dst}\n")
        if os.path.islink(link):
            os.remove(link)
        os.symlink(dst, link)
        return


def install_self():
    if os.geteuid() != 0:
        print("Install requires sudo.")
        print("Run: sudo aiov2_ctl --install")
        return 1

    draw_header("Installing aiov2_ctl system-wide")

    dst = "/usr/local/bin/aiov2_ctl"
    asset_base = "/usr/local/share/aiov2_ctl"
    completion_path = "/etc/bash_completion.d/aiov2_ctl"
    desktop_path = "/usr/share/applications/aiov2_ctl.desktop"

    stored_meta = load_install_meta() or {}
    repo = get_git_root()
    if not repo:
        repo_from_meta = stored_meta.get("repo_path")
        if repo_from_meta and os.path.isdir(repo_from_meta):
            repo = repo_from_meta

    if repo and os.path.isfile(os.path.join(repo, "aiov2_ctl.py")):
        repo = os.path.realpath(repo)
        src = os.path.join(repo, "aiov2_ctl.py")
        img_src = os.path.join(repo, "img")
    else:
        repo = None
        src = os.path.realpath(__file__)
        img_src = None

    meta = stored_meta.copy() if stored_meta else {}

    if repo:
        meta["repo_path"] = repo
        try:
            meta["remote"] = subprocess.check_output(
                ["git", "remote", "get-url", "origin"],
                cwd=repo,
                text=True
            ).strip()
        except Exception:
            pass

        meta["branch"] = get_git_branch(repo) or "main"

    # ------------------------------
    # Install executable
    # ------------------------------
    print(f"Installing executable → {dst}\n")
    src_real = os.path.realpath(src)
    dst_real = os.path.realpath(dst)
    if src_real == dst_real:
        print("Executable already installed, skipping copy.\n")
    else:
        subprocess.check_call(["cp", src, dst])
        subprocess.check_call(["chmod", "+x", dst])

    ensure_sudo_path_link(dst)

    # ------------------------------
    # Install assets (icons, etc.)
    # ------------------------------
    if img_src and os.path.isdir(img_src):
        print(f"Installing assets → {asset_base}\n")
        subprocess.check_call(["mkdir", "-p", asset_base])
        subprocess.check_call(["cp", "-r", img_src, asset_base])
    else:
        print("No assets found, skipping.\n")

    # ------------------------------
    # Install bash completion
    # ------------------------------
    print("Installing bash completion…\n")
    with open(completion_path, "w") as f:
        f.write(BASH_COMPLETION)
    os.chmod(completion_path, 0o644)

    # ------------------------------
    # Install system desktop entry
    # ------------------------------
    print(f"Installing desktop entry → {desktop_path}\n")
    with open(desktop_path, "w") as f:
        f.write(SYSTEM_DESKTOP_ENTRY)
    os.chmod(desktop_path, 0o644)

    # ------------------------------
    # Install boot rail systemd unit
    # ------------------------------
    ensure_companion_udev_rules()

    print(f"Installing rail boot service → {RAILS_BOOT_SERVICE_PATH}\n")
    with open(RAILS_BOOT_SERVICE_PATH, "w") as f:
        f.write(RAILS_BOOT_SERVICE_UNIT)
    os.chmod(RAILS_BOOT_SERVICE_PATH, 0o644)

    # ------------------------------
    # Install readsb SDR recovery
    # ------------------------------
    print(f"Installing readsb SDR recovery → {SDR_RECOVERY_DROPIN_PATH}\n")
    os.makedirs(os.path.dirname(SDR_RECOVERY_DROPIN_PATH), exist_ok=True)
    for path, content in (
        (SDR_RECOVERY_DROPIN_PATH, SDR_RECOVERY_DROPIN),
        (f"/etc/systemd/system/{SDR_WATCHDOG_SERVICE}", SDR_WATCHDOG_SERVICE_UNIT),
        (f"/etc/systemd/system/{SDR_WATCHDOG_TIMER}", SDR_WATCHDOG_TIMER_UNIT),
    ):
        with open(path, "w") as f:
            f.write(content)
        os.chmod(path, 0o644)

    subprocess.call(["systemctl", "daemon-reload"])
    subprocess.call(["systemctl", "enable", RAILS_BOOT_SERVICE])
    subprocess.call(["systemctl", "enable", "--now", SDR_WATCHDOG_TIMER])

    # ------------------------------
    # Install sudoers rule for systemctl status checks
    # ------------------------------
    sudoers_path = "/etc/sudoers.d/aiov2_ctl"
    print(f"Installing sudoers rule → {sudoers_path}\n")

    # Detect the real systemctl path
    systemctl = shutil.which("systemctl") or "/bin/systemctl"
    systemctl_paths = sorted(list({systemctl, "/bin/systemctl", "/usr/bin/systemctl"}))

    cmds = []
    for s_path in systemctl_paths:
        for action in ("status", "start", "stop", "restart", "is-active", "disable", "reset-failed"):
            for svc in ("readsb", "readsb.service", "meshtasticd", "meshtasticd.service"):
                cmds.append(f"{s_path} {action} {svc}")

    sudoers_rules = [
        f"ALL ALL=(ALL) NOPASSWD: {', '.join(cmds)}"
    ]

    sudoers_content = "# Generated by aiov2_ctl --install\n" + "\n".join(sudoers_rules) + "\n"

    with open(sudoers_path, "w") as f:
        f.write(sudoers_content)
    os.chmod(sudoers_path, 0o440)

    os.makedirs(os.path.dirname(INSTALL_META_PATH), exist_ok=True)
    with open(INSTALL_META_PATH, "w") as f:
        json.dump(meta, f, indent=2)

    print("Install complete.")
    print("Open a new shell for bash completion to activate.")
    if "--show-mesh-status" in sys.argv:
        print_mesh_on_boot_status_hint("Meshtastic boot config status:")
    return 0


SUDOERS_PATH = "/etc/sudoers.d/aiov2_ctl"
_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_SUDOERS_SERVICES = ("meshtasticd", "meshtasticd.service", "readsb", "readsb.service")


def build_sudoers(user):
    """
    Exact, argument-by-argument rules for what the tray needs. No wildcards,
    so the user cannot smuggle extra arguments past sudo.
    """
    systemctl = shutil.which("systemctl") or "/usr/bin/systemctl"
    cmds = []
    for feature in GPIO_MAP:
        for state in ("on", "off"):
            cmds.append(f"{INSTALLED_PATH} --boot-rail {feature} {state}")
    for mode in ("sd", "nvme"):
        cmds.append(f"{INSTALLED_PATH} --boot-device {mode}")
    for svc in _SUDOERS_SERVICES:
        for action in ("start", "stop", "restart", "reset-failed"):
            cmds.append(f"{systemctl} {action} {svc}")
    cmds.append(f"{systemctl} enable meshtasticd")
    cmds.append(f"{systemctl} disable --now meshtasticd")

    lines = [
        "# Managed by aiov2_ctl --setup-sudoers. Remove with --setup-sudoers --remove.",
        "# Lets the tray GUI change rails and services without a password prompt.",
        f"{user} ALL=(root) NOPASSWD: " + ", \\\n    ".join(cmds),
        "",
    ]
    return "\n".join(lines)


def setup_sudoers(remove=False):
    if os.geteuid() != 0:
        print("This command requires sudo.")
        print("Run: sudo aiov2_ctl --setup-sudoers" + (" --remove" if remove else ""))
        return 1

    if remove:
        try:
            os.remove(SUDOERS_PATH)
            print(f"Removed {SUDOERS_PATH}")
        except FileNotFoundError:
            print("Nothing to remove.")
        return 0

    if not is_trusted_file(INSTALLED_PATH):
        print(f"{INSTALLED_PATH} must be installed and owned by root first.")
        print("Run: sudo python3 ./aiov2_ctl.py --install")
        return 1

    user = target_user()
    if not user or not _USERNAME_RE.match(user):
        print("Could not work out a safe username to grant. Run this with sudo from your own account.")
        return 1

    visudo = shutil.which("visudo", path=os.environ.get("PATH", "") + ":/usr/sbin:/sbin")
    if not visudo:
        print("visudo not found, so the rules cannot be validated. Nothing written.")
        return 1

    content = build_sudoers(user)
    # Names containing '.' or '~' are ignored by sudo, so a half written temp
    # file in this directory can never take effect.
    fd, tmp = tempfile.mkstemp(prefix="aiov2_ctl.", dir="/etc/sudoers.d")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.chmod(tmp, 0o440)
        if subprocess.call([visudo, "-cf", tmp], stdout=subprocess.DEVNULL) != 0:
            print("Generated sudoers rules failed validation. Nothing written.")
            return 1
        os.replace(tmp, SUDOERS_PATH)
        tmp = None
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)

    print(f"Installed {SUDOERS_PATH} for user '{user}'.")
    print("The tray can now change boot rails, boot device and the meshtasticd/readsb services without a password.")
    print("Remove it any time with: sudo aiov2_ctl --setup-sudoers --remove")
    return 0


def update_self(confirmed=False):
    if os.geteuid() == 0:
        print("Do not run --update with sudo.")
        print("Run: aiov2_ctl --update")
        return 1

    draw_header("Updating aiov2_ctl")

    meta = load_install_meta()
    if not meta or "repo_path" not in meta:
        print("No install metadata found.")
        print("Reinstall from the original git checkout.")
        return 1

    repo = os.path.realpath(meta["repo_path"])
    if not os.path.isdir(repo):
        print(f"Source repo not found: {repo}")
        print("Reinstall required.")
        return 1

    branch = meta.get("branch") or get_git_branch(repo) or "main"
    print(f"Current branch: {branch}\n")

    # The install step runs the new code as root, so look before pulling.
    print("Fetching…\n")
    if not run_cmd(["git", "fetch", "--quiet", "origin", branch], cwd=repo):
        print("Git fetch failed.")
        return 1

    incoming = f"HEAD..origin/{branch}"
    summary = subprocess.run(
        ["git", "log", "--oneline", "--no-decorate", incoming],
        cwd=repo, text=True, capture_output=True,
    ).stdout.strip()
    if not summary:
        print("Already up to date.")
        return 0

    print("Incoming changes:\n")
    print(summary)
    run_cmd(["git", "diff", "--stat", incoming], cwd=repo)

    if load_config().get("require_signed_updates"):
        if not run_cmd(["git", "verify-commit", f"origin/{branch}"], cwd=repo):
            print("\nrequire_signed_updates is on and the newest commit is not "
                  "validly signed. Not updating.")
            return 1
        print("\nSignature on origin/%s verified." % branch)

    if not confirmed and "--yes" not in sys.argv:
        if not sys.stdin.isatty():
            print("\nNot interactive. Re-run with --yes to apply these changes as root.")
            return 1
        resp = input("\nThis will run the new code as root to install it. Continue? [y/N]: ")
        if resp.strip().lower() not in ("y", "yes"):
            print("Update cancelled.")
            return 0

    print("\nPulling latest changes…\n")
    if not run_cmd(["git", "pull", "--ff-only", "origin", branch], cwd=repo):
        print("Git pull failed. Resolve manually.")
        return 1

    print("\nReinstalling updated version…\n")

    report_and_disable_mesh_autostart_if_default("Meshtastic boot config status:")

    # escalate only for install; the user has just reviewed the changes
    rerun_with_sudo(["--install", "--show-mesh-status"], allow_untrusted=True)
    return 0

def check_update_interactive():
    """Check for updates, show diff, and prompt user to update."""
    draw_header("Checking for updates")

    meta = load_install_meta()
    if not meta or "repo_path" not in meta:
        print("No install metadata found.")
        print("Reinstall from the original git checkout.")
        return 1

    repo = os.path.realpath(meta["repo_path"])
    if not os.path.isdir(repo):
        print(f"Source repo not found: {repo}")
        print("Reinstall required.")
        return 1

    branch = meta.get("branch") or get_git_branch(repo) or "main"
    print(f"Repository: {repo}")
    print(f"Branch: {branch}\n")

    print("Fetching latest changes...\n")
    try:
        subprocess.check_call(
            ["git", "fetch", "--quiet"],
            cwd=repo,
            timeout=10,
        )
    except Exception as e:
        print(f"Failed to fetch updates: {e}")
        return 1

    # Check how many commits behind
    try:
        behind_count = subprocess.check_output(
            ["git", "rev-list", "--count", f"HEAD..origin/{branch}"],
            cwd=repo,
            text=True,
        ).strip()
        behind = int(behind_count)
    except Exception:
        print("Could not determine update status.")
        return 1

    if behind == 0:
        print("✓ Already up to date.")
        return 0

    print(f"📦 {behind} new commit{'s' if behind > 1 else ''} available:\n")

    # Show commit log
    try:
        log_output = subprocess.check_output(
            ["git", "log", "--oneline", "--decorate", "--color=always",
             f"HEAD..origin/{branch}"],
            cwd=repo,
            text=True,
        ).strip()
        print(log_output)
    except Exception:
        print("Could not retrieve commit log.")

    print("\n" + "="*60)
    response = input("\nUpdate now? [Y/n]: ").strip().lower()

    if response in ("", "y", "yes"):
        print("\nStarting update...\n")
        return update_self(confirmed=True)
    else:
        print("\nUpdate cancelled.")
        return 0


def check_update_available(repo):
    """Quick check if updates are available (used by GUI)."""
    try:
        meta = load_install_meta()
        branch = "main"
        if meta and "branch" in meta:
            branch = meta["branch"]
        elif repo:
            branch = get_git_branch(repo) or "main"

        # fetch quietly, timeout-safe
        subprocess.check_call(
            ["git", "fetch", "--quiet"],
            cwd=repo,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )

        behind = subprocess.check_output(
            ["git", "rev-list", "--count", f"HEAD..origin/{branch}"],
            cwd=repo,
            text=True,
        ).strip()

        return int(behind) > 0
    except Exception:
        return False

# ==============================
# Entrypoint
# ==============================
def main():
    if len(sys.argv) == 1:
        for f, p in GPIO_MAP.items():
            print(f"{f}: {'ON' if GpioController.get_gpio(p) else 'OFF'}")
        return

    arg = sys.argv[1]

    if arg in ("--help", "-h"):
        print(HELP_TEXT)

    elif arg == "--install":
        sys.exit(install_self())

    elif arg == "--update":
        sys.exit(update_self())

    elif arg == "--check-update":
        sys.exit(check_update_interactive())

    elif arg == "--status":
        show_status()

    elif arg == "--power":
        show_power_live()

    elif arg == "--watch":
        show_watch()

    elif arg == "--gui":
        run_gui()

    elif arg == "--autostart":
        sys.exit(enable_autostart())

    elif arg == "--no-autostart":
        sys.exit(disable_autostart())

    elif arg == "--mesh-on-boot":
        state = None
        if len(sys.argv) > 2:
            state = sys.argv[2].lower()
            if state not in ("on", "off", "status"):
                print("Usage: aiov2_ctl --mesh-on-boot [on|off|status]")
                sys.exit(1)
        if state == "status":
            print_mesh_on_boot_status()
            return
        if state == "off":
            apply_mesh_on_boot(False)
        else:
            apply_mesh_on_boot(True)

    elif arg == "--mesh-off-boot":
        apply_mesh_on_boot(False)

    elif arg == "--boot-rail":
        if len(sys.argv) < 4:
            print("Usage: aiov2_ctl --boot-rail <FEATURE> on|off|status")
            sys.exit(1)

        feature = sys.argv[2].upper()
        state = sys.argv[3].lower()

        if feature not in GPIO_MAP or state not in ("on", "off", "status"):
            print("Usage: aiov2_ctl --boot-rail <FEATURE> on|off|status")
            sys.exit(1)

        if state == "status":
            rails = get_rails_on_boot_config(system_only=True)
            print(f"{feature} boot state: {'ON' if rails.get(feature, False) else 'OFF'}")
        else:
            if os.geteuid() != 0:
                rerun_with_sudo(["--boot-rail", feature, state])
            set_feature_on_boot(feature, state == "on")
            print(f"{feature} boot state set to {'ON' if state == 'on' else 'OFF'}")

    elif arg == "--boot-rails-status":
        print_rails_on_boot_status()

    elif arg == "--apply-boot-rails":
        sys.exit(apply_rails_on_boot())

    elif arg == "--sdr-recovery":
        stage = sys.argv[2] if len(sys.argv) > 2 else ""
        if stage not in ("prestart", "poststop", "check"):
            print("Usage: aiov2_ctl --sdr-recovery prestart|poststop|check")
            sys.exit(1)
        sys.exit(sdr_recovery(stage))

    elif arg == "--wifi-monitor":
        state = sys.argv[2].lower() if len(sys.argv) > 2 else ""
        if state not in ("on", "off", "status"):
            print("Usage: aiov2_ctl --wifi-monitor on|off|status")
            sys.exit(1)
        sys.exit(wifi_monitor(state))

    elif arg == "--sdr-biastee":
        state = sys.argv[2].lower() if len(sys.argv) > 2 else ""
        if state not in ("on", "off", "status"):
            print("Usage: aiov2_ctl --sdr-biastee on|off|status")
            sys.exit(1)
        sys.exit(sdr_biastee(state))

    elif arg == "--add-apps":
        sys.exit(add_apps())

    elif arg == "--companions":
        sys.exit(show_companions())

    elif arg == "--setup-sudoers":
        sys.exit(setup_sudoers(remove="--remove" in sys.argv[2:]))

    elif arg == "--remove-apps":
        sys.exit(remove_apps())

    elif arg == "--fix-sdr":
        sys.exit(fix_sdr())

    elif arg == "--sync-rtc":
        sys.exit(sync_rtc())

    elif arg == "--boot-device":
        mode = sys.argv[2] if len(sys.argv) > 2 else "status"
        sys.exit(boot_device_command(mode))

    elif arg == "--measure":
        if len(sys.argv) < 3:
            print("Usage: aiov2_ctl --measure <FEATURE> [--seconds N] [--interval S] [--settle S]")
            sys.exit(1)
        
        feature = sys.argv[2]
        kwargs = {}
        
        # Parse optional arguments
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--seconds" and i + 1 < len(sys.argv):
                try:
                    kwargs["seconds"] = float(sys.argv[i + 1])
                    i += 2
                except ValueError:
                    print(f"Invalid value for --seconds: {sys.argv[i + 1]}")
                    sys.exit(1)
            elif sys.argv[i] == "--interval" and i + 1 < len(sys.argv):
                try:
                    kwargs["interval"] = float(sys.argv[i + 1])
                    i += 2
                except ValueError:
                    print(f"Invalid value for --interval: {sys.argv[i + 1]}")
                    sys.exit(1)
            elif sys.argv[i] == "--settle" and i + 1 < len(sys.argv):
                try:
                    kwargs["settle"] = float(sys.argv[i + 1])
                    i += 2
                except ValueError:
                    print(f"Invalid value for --settle: {sys.argv[i + 1]}")
                    sys.exit(1)
            else:
                print(f"Unknown argument: {sys.argv[i]}")
                sys.exit(1)
        
        sys.exit(measure_feature(feature, **kwargs))

    elif len(sys.argv) == 3:
        feature = arg.upper()
        state = sys.argv[2].lower()

        if feature not in GPIO_MAP or state not in ("on", "off"):
            print("Use --help")
            sys.exit(1)

        GpioController.set_feature(feature, state == "on")

    else:
        print("Use --help")
        sys.exit(1)

if __name__ == "__main__":
    main()
