#!/usr/bin/python3
import sys
import os
import subprocess
import time
import json
import hashlib
import shutil
import re
import stat
import select
import socket
import termios
import fcntl
import tty
import pwd
import tempfile
import threading
from statistics import mean, median

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
        # git refuses to look at another user's checkout (root, started by pkexec). A .git folder beside this
        # file says the same thing without asking git.
        return cwd if os.path.isdir(os.path.join(cwd, ".git")) else None


def get_git_branch(repo):
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo,
            stderr=subprocess.DEVNULL,
            text=True
        ).strip()
    except (subprocess.CalledProcessError, OSError):
        # git refuses to look at another user's checkout (root, started by pkexec). The answer is in a file.
        try:
            with open(os.path.join(repo, ".git", "HEAD")) as f:
                return branch_from_head(f.read())
        except OSError:
            return None


def origin_from_config(text):
    """The address of the remote called origin, from the text of a checkout's .git/config, or None."""
    section = None
    for line in (text or "").splitlines():
        line = line.strip()
        if line.startswith("["):
            section = line
        elif section == '[remote "origin"]' and re.match(r"url\s*=", line):
            return line.split("=", 1)[1].strip() or None
    return None


def branch_from_head(text):
    """The branch named by the text of a checkout's .git/HEAD, or None when it is not on a branch."""
    prefix = "ref: refs/heads/"
    text = (text or "").strip()
    return text[len(prefix):] if text.startswith(prefix) and len(text) > len(prefix) else None


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
        --installer
        --apply
        --features
        --repair
        --uninstall
        --update
        --channel
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
        --doctor
        --flipper
        --marauder
        --wifi
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

    if [[ "${COMP_WORDS[1]}" == "--uninstall" ]]; then
        COMPREPLY=( $(compgen -W "--only --dry-run --yes" -- "${cur}") )
        return 0
    fi

    if [[ "${COMP_WORDS[1]}" == "--repair" ]]; then
        COMPREPLY=( $(compgen -W "--dry-run --yes" -- "${cur}") )
        return 0
    fi

    if [[ "${COMP_WORDS[1]}" == "--install" ]]; then
        COMPREPLY=( $(compgen -W "--pick --all --only --without --dry-run --yes" -- "${cur}") )
        return 0
    fi

    if [[ "${prev}" == "--wifi" ]]; then
        COMPREPLY=( $(compgen -W "radios scan" -- "${cur}") )
        return 0
    fi

    if [[ "${prev}" == "--iface" ]]; then
        COMPREPLY=( $(compgen -W "wlan0 wlan1" -- "${cur}") )
        return 0
    fi

    if [[ ${COMP_CWORD} -ge 3 ]] && [[ "${COMP_WORDS[1]}" == "--wifi" ]]; then
        COMPREPLY=( $(compgen -W "--iface --json --save" -- "${cur}") )
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
  aiov2_ctl --channel [stable|beta]
  aiov2_ctl --check-update
  aiov2_ctl --help
  aiov2_ctl --autostart
  aiov2_ctl --no-autostart
    aiov2_ctl --mesh-on-boot [on|off|status]
    aiov2_ctl --boot-rail <FEATURE> on|off|status
    aiov2_ctl --boot-rails-status
    aiov2_ctl --net status|neighbors|listen|sweep|ports   (wired network, passive first)
    aiov2_ctl --wifi-listen [--channel 6]                (passive Wi-Fi signal listening for Sense, needs root)
    aiov2_ctl --wifi-survey [--band all] [--pcap]        (passive survey: access points, stations, probes, deauth floods; needs root)
    aiov2_ctl --bt-scan [--seconds 30]                   (passive Bluetooth scan: devices advertising nearby; needs root)
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
  --install    Install everything: the controller, tray, ops app and console, and the radio packages
               [--pick] [--all | --only LIST | --without LIST] [--dry-run] [--yes]
  --installer  The same installer as a window: tick features, see the plan, install or repair
  --apply      Make the installed features exactly a list: the rest are removed, these installed
               --keep LIST [--dry-run] [--yes]
  --features   What can be installed, what is on by default and what is installed now
               (--features --json gives the same as data, for a program to read)
  --repair     Go over what is installed and put back what is missing  [--dry-run] [--yes]
  --uninstall  Remove what this tool installed; packages, rails and your data are left
               [--only LIST] [--dry-run] [--yes]
  --update     Pull latest version from git and reinstall
  --check-update     Check for updates and prompt to install
  --channel          Show the update channel, or switch it: --channel stable|beta [--yes]
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
  --doctor        Read only health check for the uConsole (RTC, kernel, EEPROM, battery, storage)
  --flipper       Flipper info, ls, apps, open (see: aiov2_ctl --flipper)
  --marauder      Passive ESP32 Marauder scanning and logging (see: aiov2_ctl --marauder)
  --wifi          Wi-Fi radios and a passive scan: radios, scan [--iface wlan0|wlan1] [--json] [--save]
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
Icon=/usr/local/share/aiov2_ctl/img/pcb-board.png
Terminal=false
Categories=System;Utility;
StartupNotify=false
"""

INSTALLER_DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=AIO v2 Installer
Comment=Add, remove or repair the uConsole AIO v2 features
Exec=/usr/bin/python3 /usr/local/bin/aiov2_ctl --installer
Icon=/usr/local/share/aiov2_ctl/img/installer.png
Terminal=false
Categories=System;Settings;
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


def autostart_is_stale(text):
    """Our own older autostart entry. It started the tray at once, before the panel's
    tray host existed, so no icon appeared. Files that are not ours are left alone."""
    return "aiov2_ctl --gui" in (text or "") and text != AUTOSTART_DESKTOP


def check_tray_autostart(text, tray_running):
    """Doctor check. text is the autostart file or None when there is none."""
    if text is None:
        return []
    if autostart_is_stale(text):
        return [("warn", "Tray: your autostart entry is an older one that starts the tray before the "
                         "panel is ready, so the icon can go missing at login. Fix it with: "
                         "aiov2_ctl --autostart")]
    if not tray_running:
        return [("info", "Tray: autostart is set up but the tray is not running. "
                         "Start it now with: aiov2_ctl --gui")]
    return [("ok", "Tray: autostart is current and the tray is running.")]


def autostart_path():
    return os.path.expanduser("~/.config/autostart/aiov2_ctl.desktop")

def enable_autostart():
    if os.geteuid() == 0:
        print("Do not run --autostart as root.")
        return 1

    path = autostart_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)

    if os.path.exists(path):
        try:
            with open(path) as f:
                current = f.read()
        except OSError:
            current = ""
        if not autostart_is_stale(current):
            print("Autostart already enabled.")
            return 0
        with open(path, "w") as f:
            f.write(AUTOSTART_DESKTOP)
        print(f"Autostart updated: {path}")
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


# ---- one backend, three ways in -----------------------------------------------------------------------------------
# The tray, the command line and the ops app all switch the same rails. Whichever one does it leaves a mark (a small
# file in the user's runtime folder) so the others can show the change at once instead of at their next refresh.
# The tray also opens the ops app and the ops console, through the same shortcuts the start menu uses.

OPS_STARTUP_FILE = "uconsole-ops-fullscreen.desktop"   # written by the app (ops_gui/startup.py), in ~/.config/autostart
OPS_SHORTCUTS = (("gui", "uconsole-ops-gui.desktop", "Open uConsole Ops"),
                 ("console", "uconsole-ops-console.desktop", "Open Ops Console"))
OPS_GUI_PROCESS = "python3 -m ops_gui"
OPS_GUI_WINDOW = "uConsole AIO"


def rails_stamp_path(environ=None, uid=None):
    """The file touched whenever a rail is switched. Under sudo it is still the calling user's file, since that
    user's tray and app are the ones watching it."""
    env = os.environ if environ is None else environ
    if uid is None:
        sudo_uid = env.get("SUDO_UID", "")
        uid = int(sudo_uid) if sudo_uid.isdigit() else os.getuid()
    base = f"/run/user/{uid}"
    return os.path.join(base, "aiov2_ctl.rails") if os.path.isdir(base) else f"/tmp/aiov2_ctl-{uid}.rails"


def shared_owner_uid(environ=None, euid=None):
    """Whose files the mark and the kept status are: the user who ran sudo, otherwise whoever is running this."""
    env = os.environ if environ is None else environ
    euid = os.geteuid() if euid is None else euid
    sudo_uid = env.get("SUDO_UID", "")
    return int(sudo_uid) if euid == 0 and sudo_uid.isdigit() else euid


def folder_is_private(st, uid):
    """True for a real folder that belongs to `uid` and that nobody else can write into. Only there is it safe
    for root to leave a file for that user: anywhere else someone could have put a trap in the way."""
    return stat.S_ISDIR(st.st_mode) and st.st_uid == uid and not stat.S_IMODE(st.st_mode) & 0o022


def write_private_file(path, text, uid=None):
    """Write `text` to `path` whole, for the user `uid`, without ever trusting a name in that folder.

    This may run as root (sudo) writing into the calling user's folder. The old way wrote to a name that could be
    guessed and then changed its owner by name, so a link planted under that name made root empty, and hand over,
    some other file. Now: the folder is opened once and must be a real folder owned by that user and writable by
    nobody else; the temporary file gets a random name and is only ever created new (never opened through a
    link); its owner is changed through the open file, not its name; and it is moved over the real name inside
    that same opened folder. Anything going wrong removes the temporary file. Raises OSError when refused."""
    uid = shared_owner_uid() if uid is None else uid
    folder, name = os.path.split(path)
    if not name:
        raise OSError("no file name")
    dfd = os.open(folder or ".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if not folder_is_private(os.fstat(dfd), uid):
            raise OSError(f"{folder} is not a private folder of user {uid}")
        tmp = f".{name}.{os.urandom(8).hex()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dfd)
        try:
            try:
                if os.geteuid() == 0 and uid != 0:
                    os.fchown(fd, uid, -1)
                os.write(fd, text.encode())
            finally:
                os.close(fd)
            os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
            raise
    finally:
        os.close(dfd)


def read_private_file(path, uid=None, limit=1 << 20):
    """The text of `path`, only if it is an ordinary file (not a link, not a pipe) that belongs to `uid` or to
    root and that nobody else can write. Raises OSError otherwise."""
    uid = shared_owner_uid() if uid is None else uid
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid not in (uid, 0) or stat.S_IMODE(st.st_mode) & 0o022:
            raise OSError(f"{path} is not a private file of user {uid}")
        return os.read(fd, limit).decode("utf-8", "replace")
    finally:
        os.close(fd)


def mark_rails_changed(path=None, uid=None, now=None):
    """Leave the mark. Written beside and moved into place, so a watcher never sees half a file and a mark left
    by root can be replaced by the user. Never raises: failing to tell the others must not stop a rail switching."""
    try:
        path = path or rails_stamp_path(uid=uid)
        write_private_file(path, f"{time.time() if now is None else now:.3f}\n")
        return True
    except (OSError, ValueError):
        return False


STATUS_SHARE_SECONDS = 2.0          # how long one reading of the hardware serves everyone who asks
MENU_OPEN_LIMIT = 60.0              # a tray menu "open" for longer than this is taken to have closed unseen


def status_cache_path(environ=None, uid=None):
    """Where the last full status reading is kept, beside the rails mark."""
    return rails_stamp_path(environ, uid)[:-len(".rails")] + ".status.json"


def status_cache_fresh(written, now, mark_time, max_age):
    """True when a reading taken at `written` can still be used at `now`: it is no older than `max_age`, it is
    not from the future, and no rail has been switched since it was taken (`mark_time` is when the rails mark
    last changed, or None when there is no mark)."""
    if written is None or max_age <= 0:
        return False
    if not 0 <= now - written <= max_age:
        return False
    return mark_time is None or mark_time <= written


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def read_shared_status(max_age=STATUS_SHARE_SECONDS, now=None, path=None, mark=None):
    """The last full status reading, if someone took one recently enough; otherwise None."""
    path = path or status_cache_path()
    try:
        kept = json.loads(read_private_file(path))     # never a link, never someone else's file
        written, status = kept.get("written"), kept.get("status")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(status, dict) or "rails" not in status or not isinstance(written, (int, float)):
        return None
    now = time.time() if now is None else now
    mark_time = _mtime(mark or rails_stamp_path())
    return status if status_cache_fresh(written, now, mark_time, max_age) else None


def write_shared_status(status, now=None, path=None):
    """Keep a reading for the others. Private to the user, moved into place whole. Never raises."""
    try:
        path = path or status_cache_path()
        write_private_file(path, json.dumps({"written": time.time() if now is None else now, "status": status}))
        return True
    except (OSError, TypeError, ValueError):
        return False


def shared_status(max_age=STATUS_SHARE_SECONDS, build=None, now=None, path=None, mark=None):
    """The status of the board, for the tray, the command line and the ops app alike. If one of them read the
    hardware within `max_age` seconds and no rail has been switched since, that reading is handed out again;
    otherwise the hardware is read once (status_data) and the reading kept for the others. max_age 0 always
    reads the hardware."""
    kept = read_shared_status(max_age, now, path, mark) if max_age > 0 else None
    if kept is not None:
        return kept
    stamp = time.time() if now is None else now      # taken before reading: a switch during the read outdates it
    mark = mark or rails_stamp_path()
    before = _mtime(mark)
    status = (build or status_data)()
    # A rail switched while the board was being read: this reading may show it either way. The one who asked
    # still gets it, but it is not kept for the others, so the next one to ask reads the board again.
    if _mtime(mark) == before:
        write_shared_status(status, stamp, path)
    return status


def menu_counts_as_open(active, opened_at, now, limit=MENU_OPEN_LIMIT):
    """Whether the tray menu should still be treated as open. Some desktops never tell the tray its menu closed,
    which left the tray reading the hardware every second for hours; an "open" menu is believed for a minute."""
    return bool(active) and opened_at is not None and 0 <= now - opened_at <= limit


def status_max_age(args):
    """The --max-age SECONDS given with --status, or 0 (always read the hardware) when absent or not a number."""
    if "--max-age" in args:
        at = args.index("--max-age")
        try:
            return max(0.0, float(args[at + 1]))
        except (IndexError, ValueError):
            return 0.0
    return 0.0


def ops_shortcut_dir(environ=None):
    env = os.environ if environ is None else environ
    base = env.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "applications")


def ops_shortcuts(directory=None):
    """[(kind, path, label)] for the ops app and ops console shortcuts that are installed. The ops app writes them
    (python3 -m ops_gui --install-launchers); with none there the tray offers nothing it cannot open."""
    d = directory or ops_shortcut_dir()
    return [(kind, os.path.join(d, name), label) for kind, name, label in OPS_SHORTCUTS
            if os.path.isfile(os.path.join(d, name))]


def ops_launch_env(environ=None):
    """The environment a shortcut is started with: the tray's own, minus the Qt platform it was forced onto, so
    the app starts exactly as it does from the start menu."""
    env = dict(os.environ if environ is None else environ)
    env.pop("QT_QPA_PLATFORM", None)
    return env


def ops_gui_running(runner=subprocess.run):
    try:
        return runner(["pgrep", "-f", OPS_GUI_PROCESS], capture_output=True, text=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def ops_open(kind, path, runner=subprocess.run, popen=subprocess.Popen):
    """Open a shortcut. Returns 'opened', 'already' (the app is running: it is brought forward if the desktop
    allows, never started twice) or 'failed'."""
    if kind == "gui" and ops_gui_running(runner):
        try:
            runner(["wmctrl", "-a", OPS_GUI_WINDOW], capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass
        return "already"
    try:
        popen(["gio", "launch", path], env=ops_launch_env(), start_new_session=True,
              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "opened"
    except OSError:
        return "failed"


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
# How often the tray asks systemd about a service
# ==============================
# The tray used to ask "is readsb running?" on every one second tick. Each
# ask is a new process (and, when the answer was no, a sudo line in the
# journal), all to redraw a status line that changes a few times a day.
# Ten seconds is plenty for a status line.
SERVICE_CHECK_INTERVAL_S = 10


def service_check_due(rail_on, visible, last_asked, now, forced=False,
                      interval=SERVICE_CHECK_INTERVAL_S):
    """
    Should the tray ask systemd about a service now?

    rail_on     the power rail the service needs is on
    visible     the menu or window that shows the answer is open
    last_asked  when we last asked (same clock as now), None if never
    forced      the user just opened the menu or pressed start/stop, so
                the answer must be fresh

    A service whose rail is off cannot be running usefully, and nobody
    can see the answer while the menu is shut, so then we do not ask at
    all. Otherwise we ask at most once per interval.
    """
    if forced:
        return True
    if not rail_on and not visible:
        return False
    if last_asked is None:
        return True
    if now < last_asked:
        # The clock went backwards. Ask rather than wait an unknown time.
        return True
    return now - last_asked >= interval


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
        # Any user may ask systemd whether a unit is active, and "no" is
        # an ordinary answer, not a failure. This used to ask again under
        # sudo whenever the answer was no, which gave the same answer and
        # wrote a sudo line to the journal each time.
        cmd = ["systemctl", "is-active", name]
        return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0

    @staticmethod
    def _service_enabled(name):
        # Any user may ask this, so it is asked once and plainly. It used to be asked again under sudo when the
        # answer was no, which only repeated the answer and wrote a line in the journal each time.
        cmd = ["systemctl", "is-enabled", name]
        return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0

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
        mark_rails_changed()                     # the tray and the ops app show the change at once

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

    # Single cell voltage range. The AXP223 gauge on the revised board sometimes
    # returns 1.3 to 1.7 V with 0 A for a moment, so a lone reading is not
    # trusted. Take a few and ignore the ones that cannot be a real cell.
    BATTERY_MIN_V = 2.5
    BATTERY_MAX_V = 4.5
    GAUGE_SAMPLES = 8
    GAUGE_DELAY = 0.05
    GAUGE_SPREAD_V = 0.15
    last_discarded = (0, 0)   # (ignored, taken) from the latest battery_v_i_w()

    @staticmethod
    def battery_v_i_w():
        base = f"/sys/class/power_supply/{BATTERY_SUPPLY}"
        good = []
        taken = 0
        for n in range(Telemetry.GAUGE_SAMPLES):
            if n:
                time.sleep(Telemetry.GAUGE_DELAY)
            v = Telemetry._read_int(f"{base}/voltage_now")
            i = Telemetry._read_int(f"{base}/current_now")
            if v is None or i is None:
                continue
            taken += 1
            v, i = v / 1e6, i / 1e6
            if Telemetry.BATTERY_MIN_V <= v <= Telemetry.BATTERY_MAX_V:
                good.append((v, i))

        # The glitch only ever reads low, and on the board it ramps through
        # everything between about 1.3 V and the real value. So trust the
        # highest plausible reading and keep only those close to it.
        if good:
            top = max(g[0] for g in good)
            good = [g for g in good if top - g[0] <= Telemetry.GAUGE_SPREAD_V]

        Telemetry.last_discarded = (taken - len(good), taken)
        if not good:
            return None

        v = median(g[0] for g in good)
        i = median(g[1] for g in good)
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

        # The AXP gauge on this board has been seen stuck at 100% ("full") for a whole hour of discharging at
        # 8 W, with the cell at 3.65 V. A percentage the voltage says cannot be true is not passed on: an
        # estimate from the voltage is given instead, and marked as one.
        gauge = cap
        estimate = percent_from_voltage(viw["voltage"], cur)
        source = "gauge"
        if gauge_unbelievable(gauge, estimate, amps=cur):
            cap, source = estimate, "voltage"
        full_wh = Telemetry._read_int(f"/sys/class/power_supply/{BATTERY_SUPPLY}/energy_full_design")

        return {
            "source": "AC" if ac else "BAT",
            "status": status,
            "capacity": cap,
            "capacity_source": source,
            "capacity_gauge": gauge,
            "energy_full_wh": round(full_wh / 1e6, 2) if full_wh and full_wh > 0 else None,
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
SUBSYSTEM=="tty", ATTRS{idVendor}=="0483", ATTRS{idProduct}=="5740", TAG+="uaccess", ENV{ID_MM_DEVICE_IGNORE}="1", SYMLINK+="aiov2-flipper"
# Espressif native USB (ESP32-S2 and friends)
SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", TAG+="uaccess", ENV{ID_MM_DEVICE_IGNORE}="1", SYMLINK+="aiov2-esp32"
"""


def usb_rail_state():
    """'on', 'off' or None when it cannot be read (not a uConsole, no pinctrl)."""
    pin = GPIO_MAP.get("USB")
    if pin is None:
        return None
    try:
        level = GpioController.get_gpio(pin)
    except Exception:
        return None
    if level is None:
        return None
    return "on" if level else "off"


def usb_rail_hint():
    """A sentence to add to 'not found' errors, or '' if the rail looks fine."""
    if usb_rail_state() == "off":
        return " The USB rail is OFF, so turn it on with: aiov2_ctl USB on."
    return ""


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


def show_companions(as_json=False):
    devices = detect_companions()
    if as_json:
        import json
        print(json.dumps({"usb_rail": usb_rail_state(), "devices": devices}, indent=2))
        return 0
    print("Companion devices")
    print("-----------------")
    if not devices:
        print("None found.")
        hint = usb_rail_hint()
        if hint:
            print(hint.strip())
        print("Plug the device in and check the cable carries data, not just power.")
        return 0

    for d in devices:
        print(f"{d['label']}" + (" (possible, generic USB-serial chip)" if d["kind"] == "possible-esp32" else ""))
        print(f"  USB ID : {d['vid']}:{d['pid']}")
        print(f"  Product: {d['name']}")
        print(f"  Serial : {d['serial'] or 'n/a'}")
        tty = d["tty"]
        print(f"  Port   : {tty or 'no serial port found'}")
        if d["kind"] == "flipper":
            print("  Next   : aiov2_ctl --flipper status   (battery, SD card, apps, Wi-Fi board, tips)")
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


# ==============================
# Flipper Zero (phase 2: read only basics over the text CLI)
# ==============================
# Uses the Flipper's own serial CLI with the standard library only, so there
# is nothing extra to install. File transfer needs the RPC protocol and is a
# later phase. The CLI details below follow Flipper's published CLI docs but
# have not been checked on real hardware yet.
FLIPPER_PROMPT = ">: "
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# Anything that reaches the Flipper CLI goes through this, so a path or app
# name can never smuggle in a second command.
_FLIPPER_ARG_RE = re.compile(r"^[A-Za-z0-9_./ +()\-]{1,200}$")


class DeviceBusy(Exception):
    """Another aiov2_ctl process held the device for the whole wait."""


def _lock_dir():
    base = os.environ.get("XDG_RUNTIME_DIR")
    if not (base and os.path.isdir(base)):
        base = os.path.join(tempfile.gettempdir(), f"aiov2_ctl-{os.getuid()}")
        os.makedirs(base, mode=0o700, exist_ok=True)
        st = os.lstat(base)
        if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
            raise DeviceBusy(f"Unsafe lock directory {base}")
    return base


def acquire_device_lock(path, wait=15.0):
    """Queue behind other aiov2_ctl processes using the same serial device.

    The Flipper and the Marauder board only allow one opener, so two callers at
    once used to make one of them fail or miss the board. Returns a file
    descriptor to pass to release_device_lock.
    """
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.basename(path)) or "device"
    lock_path = os.path.join(_lock_dir(), f"aiov2_ctl-{name}.lock")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                raise DeviceBusy(f"{path} is in use by another aiov2_ctl command. Try again in a moment.")
            time.sleep(0.1)


def release_device_lock(fd):
    if fd is not None:
        try:
            os.close(fd)      # closing the descriptor drops the flock
        except OSError:
            pass


class FlipperError(Exception):
    pass


def flipper_safe_arg(value):
    if not _FLIPPER_ARG_RE.match(value or "") or ".." in value.split("/"):
        raise FlipperError(f"Refusing unusual argument: {value!r}")
    return value


class FlipperCli:
    """Minimal client for the Flipper's serial CLI. One command at a time."""

    def __init__(self, path, timeout=3.0):
        self.path = path
        self.timeout = timeout
        self.fd = None
        self.lock_fd = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    def open(self):
        try:
            self.lock_fd = acquire_device_lock(self.path)
        except (DeviceBusy, OSError) as exc:
            raise FlipperError(str(exc))
        try:
            self.fd = os.open(self.path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except PermissionError:
            release_device_lock(self.lock_fd)
            self.lock_fd = None
            raise FlipperError(
                f"No permission to open {self.path}. Run 'sudo aiov2_ctl --install' "
                "to add the udev rule, then replug the Flipper."
            )
        except OSError as exc:
            release_device_lock(self.lock_fd)
            self.lock_fd = None
            raise FlipperError(f"Could not open {self.path}: {exc}")

        try:
            tty.setraw(self.fd)
            # Exclusive: refuse to share the port with qFlipper and friends.
            fcntl.ioctl(self.fd, termios.TIOCEXCL)
        except (OSError, termios.error) as exc:
            self.close()
            raise FlipperError(f"Could not set up {self.path}: {exc}")

        # Wake the CLI and wait for its prompt.
        os.write(self.fd, b"\r")
        try:
            self._read_until_prompt()
        except FlipperError:
            self.close()
            raise FlipperError(
                "No CLI prompt from the Flipper. Is another app using it "
                "(qFlipper, the USB-UART Bridge app), or is it locked?"
            )

    def close(self):
        if self.fd is not None:
            try:
                # Give the port back explicitly so the next run can open it.
                fcntl.ioctl(self.fd, termios.TIOCNXCL)
            except (OSError, termios.error):
                pass
            try:
                os.close(self.fd)
            finally:
                self.fd = None
        release_device_lock(getattr(self, "lock_fd", None))
        self.lock_fd = None

    def _read_until_prompt(self):
        buf = b""
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FlipperError("Timed out waiting for the Flipper.")
            ready, _, _ = select.select([self.fd], [], [], remaining)
            if not ready:
                continue
            try:
                chunk = os.read(self.fd, 4096)
            except BlockingIOError:
                continue
            except OSError as exc:
                raise FlipperError(f"Lost the Flipper: {exc}")
            if not chunk:
                raise FlipperError("The Flipper closed the connection.")
            buf += chunk
            text = _ANSI_RE.sub("", buf.decode("utf-8", "replace"))
            if text.rstrip(" ").endswith(FLIPPER_PROMPT.rstrip(" ")) or text.endswith(FLIPPER_PROMPT):
                return text

    def run(self, command):
        if "\r" in command or "\n" in command:
            raise FlipperError("Command may not contain line breaks.")
        os.write(self.fd, command.encode() + b"\r")
        text = self._read_until_prompt()
        lines = text.replace("\r", "").split("\n")
        # Drop the echo of what we typed and the trailing prompt.
        while lines and (not lines[0].strip() or command in lines[0]):
            lines.pop(0)
        while lines and (not lines[-1].strip() or lines[-1].strip().startswith(">:")):
            lines.pop()
        return "\n".join(lines)


def parse_flipper_kv(text):
    """'key : value' lines to a dict."""
    out = {}
    for line in text.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            if key.strip():
                out[key.strip()] = value.strip()
    return out


def parse_flipper_storage_list(text):
    """Lines like '[D] apps' and '[F] notes.txt 123b' to (kind, name, size)."""
    if "storage error" in text.lower():
        raise FlipperError(text.strip().splitlines()[0])
    entries = []
    for line in text.splitlines():
        m = re.match(r"^\s*\[([DF])\]\s+(.*?)(?:\s+(\d+\s*\w*))?$", line)
        if m:
            kind = "dir" if m.group(1) == "D" else "file"
            entries.append((kind, m.group(2), (m.group(3) or "").strip()))
    return entries


def _flipper_port(explicit=None):
    if explicit:
        return explicit
    for dev in detect_companions():
        if dev["kind"] == "flipper" and dev["tty"]:
            return dev["tty"]
    raise FlipperError(
        "No Flipper found. Plug it in, or pass --port /dev/ttyACMx. "
        "'aiov2_ctl --companions' shows what is detected." + usb_rail_hint()
    )


# ---- RPC (protobuf) for file transfer -------------------------------------
# A small hand written client for the few storage messages needed, so there is
# no protobuf dependency. Field numbers come from flipperdevices/flipperzero-protobuf
# (flipper.proto, storage.proto).
RPC_MAX_FRAME = 4096          # a hostile device cannot make us allocate more
RPC_CHUNK = 512               # File.data max_size in storage.options
MAX_TRANSFER_BYTES = 64 * 1024 * 1024

_RPC_STATUS = {
    1: "unknown error", 2: "decode error", 3: "not implemented", 4: "busy",
    5: "storage not ready (is the SD card in?)", 6: "already exists",
    7: "does not exist", 8: "invalid parameter", 9: "access denied",
    10: "invalid name", 11: "internal storage error", 13: "already open",
    14: "transfer interrupted", 15: "invalid parameters", 17: "another app is running",
    18: "directory not empty",
}
RPC_NOT_EXIST = 7


class FlipperRpcError(FlipperError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"Flipper replied: {_RPC_STATUS.get(status, f'error {status}')}")


def pb_varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def pb_read_varint(buf, pos):
    shift = result = 0
    while True:
        if pos >= len(buf) or shift > 63:
            raise FlipperError("Malformed reply from the Flipper.")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def pb_varint_field(num, value):
    return pb_varint(num << 3) + pb_varint(value) if value else b""


def pb_bytes_field(num, data):
    if isinstance(data, str):
        data = data.encode()
    return pb_varint((num << 3) | 2) + pb_varint(len(data)) + data


def pb_parse(buf):
    """Message to {field_number: [int or bytes]}. Varint and length-delimited only."""
    out, pos = {}, 0
    while pos < len(buf):
        tag, pos = pb_read_varint(buf, pos)
        num, wire = tag >> 3, tag & 7
        if wire == 0:
            value, pos = pb_read_varint(buf, pos)
        elif wire == 2:
            length, pos = pb_read_varint(buf, pos)
            if pos + length > len(buf):
                raise FlipperError("Malformed reply from the Flipper.")
            value, pos = bytes(buf[pos:pos + length]), pos + length
        else:
            raise FlipperError("Unexpected data in the Flipper's reply.")
        out.setdefault(num, []).append(value)
    return out


def pb_main(command_id, content_field, content, has_next=False):
    """Encode a PB.Main message with a length prefix, ready to send."""
    body = (pb_varint_field(1, command_id) + pb_varint_field(3, 1 if has_next else 0)
            + pb_bytes_field(content_field, content))
    return pb_varint(len(body)) + body


class FlipperRpc:
    """Storage RPC over an already open FlipperCli. Always ends the session."""

    def __init__(self, cli):
        self.cli = cli
        self.next_id = 1
        self.active = False

    def __enter__(self):
        fd = self.cli.fd
        os.write(fd, b"start_rpc_session\r")
        # The CLI echoes the command line; after that it is protobuf only.
        deadline = time.monotonic() + self.cli.timeout
        seen = b""
        while b"\n" not in seen:
            seen += self._read(1, deadline)
        self.active = True
        return self

    def __exit__(self, *exc):
        if self.active:
            try:
                os.write(self.cli.fd, pb_main(self._id(), 19, b""))  # StopSession
                time.sleep(0.2)
            except OSError:
                pass
            self.active = False

    def _id(self):
        self.next_id += 1
        return self.next_id

    def _read(self, count, deadline):
        buf = b""
        while len(buf) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FlipperError("Timed out waiting for the Flipper.")
            ready, _, _ = select.select([self.cli.fd], [], [], remaining)
            if not ready:
                continue
            try:
                chunk = os.read(self.cli.fd, count - len(buf))
            except BlockingIOError:
                continue
            except OSError as exc:
                raise FlipperError(f"Lost the Flipper: {exc}")
            if not chunk:
                raise FlipperError("The Flipper closed the connection.")
            buf += chunk
        return buf

    def _frame(self, timeout):
        deadline = time.monotonic() + timeout
        length = shift = 0
        while True:
            b = self._read(1, deadline)[0]
            length |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                break
            if shift > 28:
                raise FlipperError("Malformed reply from the Flipper.")
        if length > RPC_MAX_FRAME:
            raise FlipperError("The Flipper sent an oversized message. Aborting.")
        return pb_parse(self._read(length, deadline))

    def request(self, command_id, field, content, timeout=None):
        """Send one request; return the list of reply Main dicts until has_next is clear."""
        timeout = timeout or self.cli.timeout
        os.write(self.cli.fd, pb_main(command_id, field, content))
        return self.replies(command_id, timeout)

    def replies(self, command_id, timeout=None):
        timeout = timeout or self.cli.timeout
        out = []
        while True:
            msg = self._frame(timeout)
            if msg.get(1, [0])[0] != command_id:
                continue
            status = msg.get(2, [0])[0]
            if status:
                raise FlipperRpcError(status)
            out.append(msg)
            if not msg.get(3, [0])[0]:
                return out

    # -- storage operations --
    def exists(self, path):
        try:
            self.request(self._id(), 24, pb_bytes_field(1, path))  # StatRequest
            return True
        except FlipperRpcError as exc:
            if exc.status == RPC_NOT_EXIST:
                return False
            raise

    def mkdir(self, path):
        self.request(self._id(), 13, pb_bytes_field(1, path))

    def md5(self, path):
        reply = self.request(self._id(), 14, pb_bytes_field(1, path))[0]
        return pb_parse(reply[15][0]).get(1, [b""])[0].decode()

    def write(self, path, data, progress=None):
        cid = self._id()
        chunks = [data[i:i + RPC_CHUNK] for i in range(0, len(data), RPC_CHUNK)] or [b""]
        for n, chunk in enumerate(chunks):
            last = n == len(chunks) - 1
            file_msg = pb_bytes_field(4, chunk)
            req = pb_bytes_field(1, path) + pb_bytes_field(2, file_msg)
            os.write(self.cli.fd, pb_main(cid, 11, req, has_next=not last))
            if progress:
                progress(min(len(data), (n + 1) * RPC_CHUNK), len(data))
        self.replies(cid, timeout=10)

    def read(self, path, progress=None):
        cid = self._id()
        os.write(self.cli.fd, pb_main(cid, 9, pb_bytes_field(1, path)))
        data = bytearray()
        for msg in self.replies(cid, timeout=10):
            if 10 in msg:
                file_msg = pb_parse(pb_parse(msg[10][0]).get(1, [b""])[0])
                data += file_msg.get(4, [b""])[0]
                if len(data) > MAX_TRANSFER_BYTES:
                    raise FlipperError("File is bigger than the transfer limit.")
                if progress:
                    progress(len(data), None)
        return bytes(data)

    def screen_frames(self, seconds):
        """
        Yield (data, orientation) screen frames for `seconds`. Read only: this
        only watches the display, it never sends button presses. The stream is
        always stopped again, even on Ctrl+C or an error.
        """
        cid = self._id()
        os.write(self.cli.fd, pb_main(cid, FLIPPER_START_SCREEN, b""))
        end = time.monotonic() + seconds
        try:
            while time.monotonic() < end:
                msg = self._frame(max(0.5, end - time.monotonic() + 1.0))
                if FLIPPER_SCREEN_FRAME in msg:
                    frame = pb_parse(msg[FLIPPER_SCREEN_FRAME][0])
                    data = frame.get(1, [b""])[0]
                    if len(data) == FLIPPER_SCREEN_BYTES:
                        yield data, frame.get(2, [0])[0]
        finally:
            try:
                os.write(self.cli.fd, pb_main(self._id(), FLIPPER_STOP_SCREEN, b""))
                time.sleep(0.2)
            except OSError:
                pass


# ---- Live screen -------------------------------------------------------------
# Field numbers follow flipperdevices/flipperzero-protobuf (gui.proto).
FLIPPER_START_SCREEN = 20
FLIPPER_STOP_SCREEN = 21
FLIPPER_SCREEN_FRAME = 22
FLIPPER_SCREEN_W, FLIPPER_SCREEN_H = 128, 64
FLIPPER_SCREEN_BYTES = FLIPPER_SCREEN_W * FLIPPER_SCREEN_H // 8


def decode_flipper_frame(data, orientation=0):
    """1024 bytes to 64 rows of 128 booleans (True = pixel on). Bytes are 8 pixel
    tall columns: byte i is x = i % 128, page = i // 128, bit 0 is the top."""
    if len(data) != FLIPPER_SCREEN_BYTES:
        raise FlipperError("Unexpected screen frame size.")
    rows = [[False] * FLIPPER_SCREEN_W for _ in range(FLIPPER_SCREEN_H)]
    for i, byte in enumerate(data):
        x, page = i % FLIPPER_SCREEN_W, i // FLIPPER_SCREEN_W
        for bit in range(8):
            if byte >> bit & 1:
                rows[page * 8 + bit][x] = True
    if orientation == 1:  # horizontal, flipped: turn it back the right way up
        rows = [row[::-1] for row in rows[::-1]]
    return rows


def frame_to_braille(rows):
    """Two by four pixels per character, so the 128x64 screen fits in 64x16."""
    dots = ((0, 0, 0x01), (0, 1, 0x02), (0, 2, 0x04), (1, 0, 0x08),
            (1, 1, 0x10), (1, 2, 0x20), (0, 3, 0x40), (1, 3, 0x80))
    lines = []
    for y in range(0, len(rows), 4):
        line = ""
        for x in range(0, len(rows[0]), 2):
            code = 0
            for dx, dy, bit in dots:
                if rows[y + dy][x + dx]:
                    code |= bit
            line += chr(0x2800 + code)
        lines.append(line)
    return "\n".join(lines)


def frame_to_png(rows, scale=4):
    """A plain black on white PNG using only the standard library."""
    import struct
    import zlib
    w, h = len(rows[0]) * scale, len(rows) * scale
    raw = bytearray()
    for row in rows:
        line = bytearray([0])
        bits = [0 if px else 1 for px in row for _ in range(scale)]
        for i in range(0, w, 8):
            chunk = bits[i:i + 8] + [1] * (8 - len(bits[i:i + 8]))
            line.append(int("".join(map(str, chunk)), 2))
        raw += bytes(line) * scale

    def chunk(kind, body):
        crc = zlib.crc32(kind + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 1, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))


def flipper_screen(cli, seconds, live, png_path):
    """Show the Flipper's screen: one frame, live for `seconds`, or saved as a PNG."""
    first = True
    last = None
    with FlipperRpc(cli) as rpc:
        for data, orientation in rpc.screen_frames(seconds):
            last = decode_flipper_frame(data, orientation)
            if png_path:
                break
            if live:
                print(("" if first else "\x1b[H") + frame_to_braille(last), end="\n", flush=True)
                first = False
            else:
                break
    if last is None:
        raise FlipperError("No screen frames arrived. Is the Flipper awake and not in the bridge app?")
    if png_path:
        fd = os.open(png_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(frame_to_png(last))
        print(f"Saved the Flipper screen to {png_path}")
    elif not live:
        print(frame_to_braille(last))


def flipper_sd_path(value):
    """Transfers only touch the SD card, never the Flipper's internal storage."""
    path = flipper_safe_arg(value)
    if path != "/ext" and not path.startswith("/ext/"):
        raise FlipperError("Remote paths must be on the SD card (start with /ext/).")
    return path


def _progress_printer(label):
    last = [-1]

    def show(done, total):
        pct = int(done * 100 / total) if total else None
        step = pct // 10 if pct is not None else done // (64 * 1024)
        if step != last[0]:
            last[0] = step
            print(f"  {label} {pct}%" if pct is not None else f"  {label} {done} bytes")
    return show


def flipper_transfer(cli, sub, rest, force):
    import hashlib
    with FlipperRpc(cli) as rpc:
        if sub == "mkdir":
            rpc.mkdir(flipper_sd_path(rest[0]))
            print(f"Created {rest[0]}")
        elif sub == "put":
            local, remote = rest[0], flipper_sd_path(rest[1])
            if not os.path.isfile(local):
                raise FlipperError(f"{local} is not a file.")
            size = os.path.getsize(local)
            if size > MAX_TRANSFER_BYTES:
                raise FlipperError("File is bigger than the 64 MiB transfer limit.")
            if rpc.exists(remote) and not force:
                raise FlipperError(f"{remote} already exists. Use --force to overwrite.")
            with open(local, "rb") as f:
                data = f.read()
            print(f"Sending {local} ({size} bytes) to {remote}")
            rpc.write(remote, data, _progress_printer("sent"))
            if rpc.md5(remote) != hashlib.md5(data).hexdigest():
                raise FlipperError("Checksum mismatch after sending. The copy on the Flipper is not trusted.")
            print("Done. Checksum verified.")
        elif sub == "get":
            remote = flipper_sd_path(rest[0])
            if len(rest) > 1:
                local = rest[1]
            else:
                # No destination given: keep it in the private captures folder.
                folder = os.path.join(CAPTURES_DIR, "flipper")
                os.makedirs(folder, mode=0o700, exist_ok=True)
                local = os.path.join(folder, os.path.basename(remote))
            if os.path.exists(local):
                raise FlipperError(f"{local} already exists. Choose a new name.")
            data = rpc.read(remote, _progress_printer("received"))
            fd = os.open(local, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            print(f"Saved {len(data)} bytes to {local}")


# ---- Status, module detection and suggestions -------------------------------
# Read only. The Flipper cannot say what is on its GPIO header, so the Wi-Fi
# dev board is only confirmed when the USB-UART Bridge app is running and the
# board answers Marauder's "info". Otherwise it is reported as unchecked.
FLIPPER_KNOWN_MODULES = ("wifi-devboard", "nrf24", "gps", "cc1101", "other")
FLIPPER_APP_HINTS = (
    ("marauder", "ESP32 Marauder"),
    ("uart_terminal", "UART Terminal"),
    ("nrf24", "NRF24 tools"),
    ("gps", "GPS"),
)


def flipper_declared_modules(config=None):
    """Modules the user says are fitted ('flipper_modules' in the config)."""
    config = load_config() if config is None else config
    raw = config.get("flipper_modules", [])
    if not isinstance(raw, list):
        return []
    return [m for m in raw if isinstance(m, str) and m in FLIPPER_KNOWN_MODULES]


def gather_flipper_status(cli, declared=()):
    """Facts from a Flipper whose CLI is open. Each part fails on its own."""
    status = {"mode": "cli", "device": {}, "battery": {}, "sd_card": "unknown",
              "apps": [], "declared_modules": list(declared)}
    try:
        status["device"] = parse_flipper_kv(cli.run("device_info"))
    except FlipperError:
        pass
    try:
        pwr = parse_flipper_kv(cli.run("info power"))
        if not any(k.startswith(("charge", "battery")) for k in pwr):
            pwr = parse_flipper_kv(cli.run("power info"))
        status["battery"] = {k: v for k, v in pwr.items()
                             if k.startswith(("charge", "battery", "gauge", "capacity"))}
    except FlipperError:
        pass
    try:
        parse_flipper_storage_list(cli.run("storage list /ext"))
        status["sd_card"] = "present"
    except FlipperError:
        status["sd_card"] = "missing"
    if status["sd_card"] == "present":
        try:
            cats = [n for k, n, _ in parse_flipper_storage_list(cli.run("storage list /ext/apps"))
                    if k == "dir"]
            for cat in cats[:20]:
                for kind, name, _ in parse_flipper_storage_list(
                        cli.run(f"storage list /ext/apps/{flipper_safe_arg(cat)}")):
                    if kind == "file" and name.lower().endswith(".fap"):
                        status["apps"].append(name[:-4])
        except FlipperError:
            pass
    return status


def probe_wifi_board(path, attempts=2):
    """'present' or 'not-detected' for a Marauder board behind the bridge app.

    The board can miss one "info" if it is still busy from an earlier command, so
    it gets one more try before it is reported as not detected."""
    for attempt in range(attempts):
        try:
            with MarauderSerial(path) as ser:
                lines = ser.collect("info", idle=1.0, limit=4.0)
        except MarauderError:
            lines = []
        blob = " ".join(lines).lower()
        if "marauder" in blob or "esp32" in blob:
            return "present"
        if attempt + 1 < attempts:
            time.sleep(0.5)
    return "not-detected"


def flipper_suggestions(status):
    """Plain sentences with the exact command to run. Pure, so it is easy to test."""
    tips = []
    mode = status.get("mode")
    if mode == "bridge":
        tips.append("The Flipper looks to be in USB-UART Bridge mode. Press Back on it to get the "
                    "CLI back for 'aiov2_ctl --flipper info' and file transfers.")
        if status.get("wifi_board") == "present":
            tips.append("The Wi-Fi dev board answers. Try: aiov2_ctl --marauder scan")
        return tips
    if status.get("sd_card") == "missing":
        tips.append("No SD card was found. Apps and captures need one.")
    apps = " ".join(a.lower() for a in status.get("apps", []))
    if status.get("sd_card") == "present":
        if "marauder" not in apps and "wifi-devboard" in status.get("declared_modules", []):
            tips.append("You say a Wi-Fi dev board is fitted but no Marauder app is on the SD card. "
                        "Install it from the Flipper's own app catalogue, then flash the board.")
        tips.append("Copy a file over with: aiov2_ctl --flipper put LOCAL /ext/NAME")
    if status.get("wifi_board") in (None, "unchecked"):
        tips.append("Wi-Fi dev board: not checked. Open GPIO > USB-UART Bridge on the Flipper, "
                    "then run: aiov2_ctl --flipper status")
    return tips


def flipper_status(port=None, as_json=False):
    declared = flipper_declared_modules()
    path = _flipper_port(port)
    try:
        with FlipperCli(path) as cli:
            status = gather_flipper_status(cli, declared)
        status["wifi_board"] = "unchecked"
    except FlipperError as exc:
        if "No CLI prompt" not in str(exc):
            raise
        status = {"mode": "bridge", "device": {}, "battery": {}, "sd_card": "unknown",
                  "apps": [], "declared_modules": list(declared),
                  "wifi_board": probe_wifi_board(path)}
    status["port"] = path
    status["suggestions"] = flipper_suggestions(status)
    if as_json:
        print(json.dumps(status, indent=2))
        return status
    print("Flipper status")
    print("--------------")
    dev = status["device"]
    if dev:
        print(f"Firmware : {dev.get('firmware_version', 'n/a')} ({dev.get('firmware_origin_fork', 'n/a')})")
    print(f"Mode     : {'USB-UART Bridge' if status['mode'] == 'bridge' else 'CLI'}")
    if status["battery"].get("charge.level"):
        print(f"Battery  : {status['battery']['charge.level']}% ({status['battery'].get('charge.state', 'n/a')})")
    print(f"SD card  : {status['sd_card']}")
    print(f"Apps     : {', '.join(status['apps']) if status['apps'] else 'none seen'}")
    print(f"Wi-Fi board: {status['wifi_board']}")
    if status["declared_modules"]:
        print(f"Declared modules: {', '.join(status['declared_modules'])}")
    for tip in status["suggestions"]:
        print(f"Tip: {tip}")
    return status


# ---- Installing apps and the header pins (phase: Flipper as an extension) ---------------------------
# Apps are .fap files, which are ELF programs that run on the Flipper. They are only ever written to
# /ext/apps/<Category>/ on the SD card, only when the file really is an ELF, and never over an existing
# file without --force. The header pins are limited to the ones on the Flipper's GPIO connector, and
# driving one needs --yes because it puts a voltage on whatever is attached (the Wi-Fi board included).
FLIPPER_APP_CATEGORIES = ("Bluetooth", "GPIO", "Games", "Infrared", "Media", "NFC", "RFID", "Sub-GHz",
                          "Tools", "USB", "iButton", "Misc")
FLIPPER_MAX_APP_BYTES = 8 * 1024 * 1024
FLIPPER_GPIO_PINS = ("PA7", "PA6", "PA4", "PB3", "PB2", "PC3", "PC1", "PC0")
FLIPPER_GPIO_HEADER = {"PA7": 2, "PA6": 3, "PA4": 4, "PB3": 5, "PB2": 6, "PC3": 7, "PC1": 15, "PC0": 16}
FLIPPER_BUSY_TEXT = "cannot be run while an application is open"


def flipper_check_app(local, category):
    """(remote path, bytes) for a .fap about to be installed, or FlipperError."""
    if category not in FLIPPER_APP_CATEGORIES:
        raise FlipperError(f"Category must be one of: {', '.join(FLIPPER_APP_CATEGORIES)}.")
    if not local.lower().endswith(".fap") or not os.path.isfile(local):
        raise FlipperError(f"{local} is not a .fap file.")
    if os.path.getsize(local) > FLIPPER_MAX_APP_BYTES:
        raise FlipperError("That app is bigger than the 8 MiB limit.")
    with open(local, "rb") as f:
        data = f.read()
    if data[:4] != b"\x7fELF":
        raise FlipperError("That file is not a Flipper app (it is not an ELF program).")
    name = flipper_safe_arg(os.path.basename(local))
    return f"/ext/apps/{category}/{name}", data


def flipper_install(cli, local, category="Tools", force=False):
    import hashlib
    remote, data = flipper_check_app(local, category)
    folder = remote.rsplit("/", 1)[0]
    with FlipperRpc(cli) as rpc:
        if not rpc.exists(folder):
            rpc.mkdir(folder)
        if rpc.exists(remote) and not force:
            raise FlipperError(f"{remote} is already there. Use --force to replace it.")
        print(f"Installing {os.path.basename(local)} ({len(data)} bytes) to {remote}")
        rpc.write(remote, data, _progress_printer("sent"))
        if rpc.md5(remote) != hashlib.md5(data).hexdigest():
            raise FlipperError("Checksum mismatch after sending. The copy on the Flipper is not trusted.")
    print("Done. Open it on the Flipper under Apps > " + category + ".")
    return remote


def parse_flipper_gpio(text):
    """0 or 1 from the Flipper's answer to 'gpio read', or None when it is not recognisable."""
    for line in reversed([l.strip() for l in (text or "").splitlines() if l.strip()]):
        m = re.search(r"(?:=|<=|:)\s*([01])\s*$", line) or re.search(r"(?<![\w.])([01])$", line)
        if m:
            return int(m.group(1))
    return None


def flipper_gpio(cli, rest, yes=False):
    if rest and rest[0] == "list":
        for pin in FLIPPER_GPIO_PINS:
            print(f"{pin}  header pin {FLIPPER_GPIO_HEADER[pin]}")
        return
    if len(rest) < 2 or rest[1] not in FLIPPER_GPIO_PINS or rest[0] not in ("read", "set"):
        raise FlipperError("Usage: --flipper gpio list | read PIN | set PIN 0|1 --yes. "
                           f"Pins: {', '.join(FLIPPER_GPIO_PINS)}.")
    pin = rest[1]
    if rest[0] == "read":
        out = cli.run(f"gpio mode {pin} 0")          # make it an input first, so reading is harmless
        if FLIPPER_BUSY_TEXT in out:
            raise FlipperError("Close the app on the Flipper (go to its home screen) before using the pins.")
        out = cli.run(f"gpio read {pin}")
        value = parse_flipper_gpio(out)
        print(f"{pin} reads {value}" if value is not None else out or f"No answer for {pin}.")
        return
    if len(rest) != 3 or rest[2] not in ("0", "1"):
        raise FlipperError("Usage: --flipper gpio set PIN 0|1 --yes")
    if not yes:
        raise FlipperError(f"Driving {pin} puts a voltage on whatever is plugged into that pin. "
                           "Add --yes if you are sure.")
    out = cli.run(f"gpio mode {pin} 1")
    if FLIPPER_BUSY_TEXT in out:
        raise FlipperError("Close the app on the Flipper (go to its home screen) before using the pins.")
    cli.run(f"gpio set {pin} {rest[2]}")
    print(f"{pin} is now an output set to {rest[2]}. Use 'gpio read {pin}' to turn it back into an input.")


def flipper_command(args):
    usage = (
        "Usage: aiov2_ctl --flipper <command> [--port /dev/ttyACMx]\n"
        "  status [--json]       mode, battery, SD card, apps, Wi-Fi board and next steps\n"
        "  screen [--live] [--seconds N] [--png FILE]   show the Flipper's screen (watch only)\n"
        "  info                  name, firmware and battery\n"
        "  ls [PATH]             list files (default /ext, the SD card)\n"
        "  apps                  list apps the Flipper can launch\n"
        "  open APP              launch an app on the Flipper\n"
        "  put LOCAL REMOTE [--force]   send a file to the SD card (checksum verified)\n"
        "  get REMOTE [LOCAL]    copy a file from the SD card (default: ~/aiov2-captures/flipper/)\n"
        "  mkdir REMOTE          make a folder on the SD card\n"
        "  install APP.fap [--category Tools] [--force]   put an app in /ext/apps/<Category>\n"
        "  gpio list | read PIN | set PIN 0|1 --yes       the Flipper's header pins (home screen only)\n"
        "Transfers only touch /ext (the SD card), never overwrite without --force "
        "and never overwrite local files."
    )
    args = list(args)
    port = None
    if "--port" in args:
        i = args.index("--port")
        if i + 1 >= len(args):
            print(usage)
            return 1
        port = args[i + 1]
        del args[i:i + 2]
    force = "--force" in args
    if force:
        args.remove("--force")
    yes = "--yes" in args
    if yes:
        args.remove("--yes")
    category = "Tools"
    if "--category" in args:
        i = args.index("--category")
        if i + 1 >= len(args):
            print(usage)
            return 1
        category = args[i + 1]
        del args[i:i + 2]

    as_json = "--json" in args
    if as_json:
        args.remove("--json")
    live = "--live" in args
    if live:
        args.remove("--live")
    png_path = None
    if "--png" in args:
        i = args.index("--png")
        if i + 1 >= len(args):
            print(usage)
            return 1
        png_path = args[i + 1]
        del args[i:i + 2]
    seconds = 10.0
    if "--seconds" in args:
        i = args.index("--seconds")
        try:
            seconds = float(args[i + 1])
            if not 1 <= seconds <= 600:
                raise ValueError
        except (IndexError, ValueError):
            print("--seconds needs a number between 1 and 600.")
            return 1
        del args[i:i + 2]
    arity = {"screen": (0, 0), "status": (0, 0), "info": (0, 0), "apps": (0, 0), "ls": (0, 1), "open": (1, 1),
             "put": (2, 2), "get": (1, 2), "mkdir": (1, 1), "install": (1, 1), "gpio": (1, 3)}
    if not args or args[0] not in arity:
        print(usage)
        return 1
    sub, rest = args[0], args[1:]
    low, high = arity[sub]
    if not low <= len(rest) <= high:
        print(usage)
        return 1

    if sub == "status":
        try:
            flipper_status(port, as_json)
        except FlipperError as exc:
            print(f"Error: {exc}")
            return 1
        return 0
    try:
        with FlipperCli(_flipper_port(port)) as cli:
            if sub in ("put", "get", "mkdir"):
                flipper_transfer(cli, sub, rest, force)
                return 0
            if sub == "install":
                flipper_install(cli, rest[0], category, force)
                return 0
            if sub == "gpio":
                flipper_gpio(cli, rest, yes)
                return 0
            if sub == "screen":
                flipper_screen(cli, seconds if live else 5.0, live, png_path)
                return 0
            if sub == "info":
                dev = parse_flipper_kv(cli.run("device_info"))
                # Newer firmware (checked on Unleashed unlshd-095) uses "info power".
                # Older firmware used "power info", which newer builds answer with a usage message.
                pwr = parse_flipper_kv(cli.run("info power"))
                if not any(k.startswith(("charge", "battery")) for k in pwr):
                    pwr = parse_flipper_kv(cli.run("power info"))
                print("Flipper Zero")
                print("------------")
                for key in ("hardware_name", "hardware_ver", "firmware_version",
                            "firmware_origin_fork", "firmware_commit"):
                    if key in dev:
                        print(f"{key:<22}: {dev[key]}")
                shown = [k for k in pwr if k.startswith(("charge", "battery", "gauge", "capacity"))]
                if shown:
                    print("Battery")
                    for key in shown:
                        print(f"  {key:<20}: {pwr[key]}")
            elif sub == "ls":
                path = flipper_safe_arg(rest[0] if rest else "/ext")
                entries = parse_flipper_storage_list(cli.run(f"storage list {path}"))
                if not entries:
                    print("(empty)")
                for kind, name, size in entries:
                    print(f"{'d' if kind == 'dir' else '-'}  {name}" + (f"  {size}" if size else ""))
            elif sub == "apps":
                print(cli.run("loader list") or "(none)")
            elif sub == "open":
                app = flipper_safe_arg(rest[0])
                out = cli.run(f"loader open {app}")
                print(out or f"Asked the Flipper to open {app}.")
    except FlipperError as exc:
        print(f"Error: {exc}")
        return 1
    return 0


# ==============================
# ESP32 Marauder (phase 3: passive scanning and logging only)
# ==============================
# Talks to Marauder's serial command line (documented as 115200 baud) on an
# ESP32 board, either plugged in directly or through the Flipper's USB-UART
# Bridge app. Only a short allow list of passive commands is built in. There
# is deliberately no way to send attack, spoofing or portal commands from
# here: anything that transmits frames is a separate, explicit decision.
#
# The Marauder wiki does not document the line ending or the output format.
# Checked on a Flipper WiFi dev board running v1.21.0: "\n" works, and the
# output is streamed as raw lines, so the exact format does not matter here.
MARAUDER_BAUD = termios.B115200
MARAUDER_EOL = b"\n"
CAPTURES_DIR = os.path.expanduser("~/aiov2-captures")
MARAUDER_LOG_DIR = CAPTURES_DIR
# Every scan the tool will start, all of them listen only. mode -> (firmware command, what it listens for).
MARAUDER_MODE_INFO = {
    "all": ("scanall", "Everything: access points and the stations talking to them"),
    "ap": ("scanap", "Access points only"),
    "stations": ("scansta", "Stations (phones, laptops) and which access point they are with"),
    "beacon": ("sniffbeacon", "Beacon frames: networks announcing themselves"),
    "probe": ("sniffprobe", "Probe requests: devices asking for networks by name"),
    "deauthwatch": ("sniffdeauth", "Deauthentication frames in the air (detects someone else attacking, sends nothing)"),
    "pwn": ("sniffpwn", "Pwnagotchi devices nearby"),
    "esp": ("sniffesp", "Other ESP32 and ESP8266 devices nearby"),
    "bt": ("sniffbt", "Bluetooth devices nearby"),
    "skimmer": ("sniffskim", "Bluetooth card skimmers (by their usual names)"),
    "packets": ("packetcount", "Packet counts by type, a busy-ness meter"),
    "pineapple": ("sniffpinescan", "Wi-Fi Pineapple devices nearby (detects them, sends nothing)"),
    "multissid": ("sniffmultissid", "One device announcing many network names (detects beacon spam)"),
    "remoteid": ("remoteid scan", "Drone Remote ID broadcasts"),
    "raw": ("sniffraw", "Every raw frame, unfiltered"),
}
MARAUDER_SCAN_MODES = {mode: cmd for mode, (cmd, _d) in MARAUDER_MODE_INFO.items()}
MARAUDER_LISTS = {"aps": "list -a", "stations": "list -c", "ssids": "list -s"}
# Firmware commands that transmit, spoof, capture handshakes or attack. They are never sent, and asking for one says so.
MARAUDER_REFUSED = frozenset({
    "attack", "deauth", "beaconspam", "beacon-spam", "rickroll", "probeflood", "karma", "evilportal", "evil-portal",
    "sniffpmkid", "sniffeapol", "handshake", "pmkid", "blespam", "sourapple", "swiftpair", "spoof", "join", "ping",
    "portscan", "arpscan", "sendpacket", "ssid", "update", "reboot", "sniffsae", "sae", "wardrive-attack",
})


class MarauderError(Exception):
    pass


class MarauderSerial:
    """Raw line oriented serial client. One command at a time."""

    def __init__(self, path):
        self.path = path
        self.fd = None
        self.lock_fd = None
        self._partial = b""

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    def open(self):
        try:
            self.lock_fd = acquire_device_lock(self.path)
        except (DeviceBusy, OSError) as exc:
            raise MarauderError(str(exc))
        try:
            self.fd = os.open(self.path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except PermissionError:
            release_device_lock(self.lock_fd)
            self.lock_fd = None
            raise MarauderError(
                f"No permission to open {self.path}. Run 'sudo aiov2_ctl --install' "
                "to add the udev rule, then replug the device."
            )
        except OSError as exc:
            release_device_lock(self.lock_fd)
            self.lock_fd = None
            raise MarauderError(f"Could not open {self.path}: {exc}")
        try:
            # TCSANOW: the default (TCSAFLUSH) waits for queued output to drain and can block
            # forever when the board is not reading, which hung --wifi radios.
            tty.setraw(self.fd, termios.TCSANOW)
            attrs = termios.tcgetattr(self.fd)
            attrs[4] = attrs[5] = MARAUDER_BAUD  # ispeed, ospeed
            termios.tcsetattr(self.fd, termios.TCSANOW, attrs)
            fcntl.ioctl(self.fd, termios.TIOCEXCL)
        except (OSError, termios.error) as exc:
            self.close()
            raise MarauderError(f"Could not set up {self.path}: {exc}")

    def close(self):
        if self.fd is not None:
            try:
                fcntl.ioctl(self.fd, termios.TIOCNXCL)
            except (OSError, termios.error):
                pass
            try:
                os.close(self.fd)
            finally:
                self.fd = None
        release_device_lock(getattr(self, "lock_fd", None))
        self.lock_fd = None

    def send(self, command):
        if "\r" in command or "\n" in command:
            raise MarauderError("Command may not contain line breaks.")
        os.write(self.fd, command.encode() + MARAUDER_EOL)

    def read_lines(self, wait):
        """Lines that arrive within `wait` seconds (may be empty)."""
        lines = []
        ready, _, _ = select.select([self.fd], [], [], wait)
        while ready:
            try:
                chunk = os.read(self.fd, 4096)
            except BlockingIOError:
                break
            except OSError as exc:
                raise MarauderError(f"Lost the device: {exc}")
            if not chunk:
                raise MarauderError("The device closed the connection.")
            self._partial += chunk
            ready, _, _ = select.select([self.fd], [], [], 0)
        *complete, self._partial = self._partial.split(b"\n")
        for raw in complete:
            text = _ANSI_RE.sub("", raw.decode("utf-8", "replace")).strip("\r ")
            if text:
                lines.append(text)
        return lines

    def collect(self, command, idle=1.0, limit=8.0):
        """Send a command and gather output until it goes quiet."""
        self.send(command)
        out = []
        end = time.monotonic() + limit
        while time.monotonic() < end:
            got = self.read_lines(idle)
            if not got:
                break
            out += got
        return out


class MarauderLog:
    """Capture log. Output can contain SSIDs and MAC addresses, so keep it private."""

    def __init__(self, directory=None):
        directory = directory or MARAUDER_LOG_DIR
        os.makedirs(directory, mode=0o700, exist_ok=True)
        self.path = os.path.join(
            directory, time.strftime("marauder-%Y%m%d-%H%M%S.log"))
        self.fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)

    def write(self, line):
        os.write(self.fd, (line + "\n").encode())

    def close(self):
        os.close(self.fd)


def _marauder_port(explicit=None):
    if explicit:
        return explicit, None
    devices = [d for d in detect_companions() if d["tty"]]
    for kind in ("esp32", "possible-esp32"):
        for dev in devices:
            if dev["kind"] == kind:
                return dev["tty"], None
    for dev in devices:
        if dev["kind"] == "flipper":
            return dev["tty"], (
                "Using the Flipper's port. This only works with the Flipper's "
                "USB-UART Bridge app running (channel 0, 115200 baud)."
            )
    raise MarauderError(
        "No Marauder board found. Plug it in, or pass --port /dev/ttyXXX. "
        "'aiov2_ctl --companions' shows what is detected." + usb_rail_hint()
    )


def marauder_scan(ser, mode, seconds, log, say=print):
    """Run a passive scan, stream and log what comes back, always stop it."""
    command = MARAUDER_SCAN_MODES[mode]
    ser.send(command)
    log.write(f"# {time.strftime('%Y-%m-%d %H:%M:%S')} started: {command} for {seconds}s")
    end = time.monotonic() + seconds
    unsupported = False
    try:
        while time.monotonic() < end and not unsupported:
            for line in ser.read_lines(min(0.5, max(0.0, end - time.monotonic()))):
                say(line)
                log.write(line)
                if "not supported" in line.lower():
                    unsupported = True
    except KeyboardInterrupt:
        say("\nStopping…")
    finally:
        # Never leave the board scanning, even on Ctrl+C or an error.
        try:
            ser.send("stopscan")
        except (OSError, MarauderError):
            pass
        time.sleep(0.3)
    log.write(f"# {time.strftime('%Y-%m-%d %H:%M:%S')} stopped")
    if unsupported:
        raise MarauderError(f"This board cannot do '{mode}' (it said: not supported). The Flipper Wi-Fi board has no Bluetooth.")


def marauder_command(args):
    args = list(args)
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    usage = (
        "Usage: aiov2_ctl --marauder <command> [--port /dev/ttyXXX]\n"
        "  info                       board and firmware info\n"
        "  help                       list the firmware's own commands\n"
        "  scan [MODE] [--seconds N]  (default all and 30 seconds; under about 15 may hear nothing)\n"
        "                             passive scan, streamed and saved to ~/aiov2-captures\n"
        "  modes [--json]             every scan mode and what it listens for\n"
        "  list [aps|stations|ssids]  show what the board has found\n"
        "  channel [N]                show or set the Wi-Fi channel (1-14)\n"
        "  stop                       stop any running scan\n"
        "Passive commands only. Attack, spoofing and portal commands are not available."
    )
    args = list(args)
    port = None
    if "--port" in args:
        i = args.index("--port")
        if i + 1 >= len(args):
            print(usage)
            return 1
        port = args[i + 1]
        del args[i:i + 2]

    seconds = 30.0
    if "--seconds" in args:
        i = args.index("--seconds")
        try:
            seconds = float(args[i + 1])
            if not 1 <= seconds <= 3600:
                raise ValueError
        except (IndexError, ValueError):
            print("--seconds needs a number between 1 and 3600.")
            return 1
        del args[i:i + 2]

    if not args:
        print(usage)
        return 1
    sub, rest = args[0], args[1:]
    if sub in MARAUDER_REFUSED or (sub == "scan" and rest and rest[0] in MARAUDER_REFUSED):
        name = rest[0] if sub == "scan" and rest else sub
        print(f"'{name}' is not available: it transmits, spoofs or captures handshakes. "
              "This tool only listens. 'aiov2_ctl --marauder modes' shows what it can do.")
        return 1
    if sub == "modes" and not rest:
        if as_json:
            print(json.dumps([{"mode": m, "command": c, "listens_for": d} for m, (c, d) in MARAUDER_MODE_INFO.items()], indent=2))
        else:
            for m, (c, d) in MARAUDER_MODE_INFO.items():
                print(f"  {m:<9} {d}")
        return 0

    # Work out the exact device commands up front and refuse anything else
    # before a port is even opened.
    simple = {"info": "info", "help": "help", "stop": "stopscan"}
    if sub in simple and not rest:
        plan = ("simple", simple[sub])
    elif sub == "scan" and len(rest) <= 1 and (not rest or rest[0] in MARAUDER_SCAN_MODES):
        plan = ("scan", rest[0] if rest else "all")
    elif sub == "list" and len(rest) <= 1 and (not rest or rest[0] in MARAUDER_LISTS):
        plan = ("simple", MARAUDER_LISTS[rest[0] if rest else "aps"])
    elif sub == "channel" and len(rest) <= 1:
        if rest:
            if not rest[0].isdigit() or not 1 <= int(rest[0]) <= 14:
                print("Channel must be a number from 1 to 14.")
                return 1
            plan = ("simple", f"channel -s {int(rest[0])}")
        else:
            plan = ("simple", "channel")
    else:
        print(usage)
        return 1

    try:
        path, note = _marauder_port(port)
        if note:
            print(note)
        with MarauderSerial(path) as ser:
            if plan[0] == "scan":
                log = MarauderLog()
                try:
                    print(f"Scanning ({plan[1]}) for {seconds:g}s on {path}. Ctrl+C stops early.")
                    marauder_scan(ser, plan[1], seconds, log)
                    # Show what the board collected, and keep it in the log.
                    for cmd in MARAUDER_LISTS.values():
                        log.write(f"# {cmd}")
                        for line in ser.collect(cmd):
                            print(line)
                            log.write(line)
                finally:
                    log.close()
                print(f"\nSaved to {log.path} (private, mode 600).")
            else:
                for line in ser.collect(plan[1]):
                    print(line)
    except MarauderError as exc:
        print(f"Error: {exc}")
        return 1
    return 0


# ---- Wi-Fi radios and passive scanning ---------------------------------------
# Read only and passive. Radios are found from sysfs (and 'iw dev' when it is
# installed). Scanning uses NetworkManager (nmcli), which a normal user may do
# without root, so no sudoers rule is needed. Nothing here transmits anything
# other than the ordinary probe requests of a managed mode scan. The Flipper
# Wi-Fi dev board is a third source, but only when a Flipper is plugged in AND
# the board answers; with no Flipper on the bus its port is never opened.
SYS_NET = "/sys/class/net"
FLIPPER_RADIO = "flipper-marauder"
MT7921_USB_ID = ("0e8d", "7961")
WIFI_SCAN_SECONDS = 8.0
_IFACE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")
_MARAUDER_AP_RE = re.compile(r"^\[\d+\]\s*\[CH:\s*(\d+)\]\s*(.*?)\s*(-\d+)?\s*$")


def _wifi_driver_info(iface, base=SYS_NET):
    """(driver, bus, usb_id) for a network interface, from sysfs only."""
    dev = os.path.join(base, iface, "device")
    driver, bus = "", ""
    try:
        driver = os.path.basename(os.readlink(os.path.join(dev, "driver")))
    except OSError:
        pass
    try:
        bus = os.path.basename(os.readlink(os.path.join(dev, "subsystem")))
    except OSError:
        pass
    uevent = read_sysfs(os.path.join(dev, "uevent")) or ""
    fields = dict(line.split("=", 1) for line in uevent.splitlines() if "=" in line)
    driver = driver or fields.get("DRIVER", "")
    usb_id = None
    if "PRODUCT" in fields:  # USB: vendor/product/bcd, hex without leading zeros
        parts = fields["PRODUCT"].split("/")
        if len(parts) >= 2:
            try:
                usb_id = (f"{int(parts[0], 16):04x}", f"{int(parts[1], 16):04x}")
            except ValueError:
                pass
        bus = bus or "usb"
    elif "SDIO_ID" in fields:
        bus = bus or "sdio"
    return driver, bus, usb_id


def classify_wifi_radio(bus, usb_id):
    """'onboard', 'internal-usb' or 'external-usb' from the bus and USB id."""
    if bus == "usb":
        return "internal-usb" if usb_id == MT7921_USB_ID else "external-usb"
    return "onboard"


def parse_iw_dev(text):
    """Interface names from 'iw dev' output."""
    return [m.group(1) for m in re.finditer(r"^\s*Interface\s+(\S+)", text or "", re.M)]


def _iw_dev_names():
    iw = shutil.which("iw") or next(
        (p for p in ("/usr/sbin/iw", "/sbin/iw") if os.access(p, os.X_OK)), None)
    if not iw:
        return []
    try:
        out = subprocess.run([iw, "dev"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_iw_dev(out.stdout) if out.returncode == 0 else []


def detect_wifi_radios(base=SYS_NET, iw_names=None):
    """Wi-Fi radios as [{name, kind, driver, up}], without touching the Flipper."""
    try:
        names = [n for n in sorted(os.listdir(base))
                 if os.path.isdir(os.path.join(base, n, "wireless"))]
    except OSError:
        names = []
    for n in (_iw_dev_names() if iw_names is None else iw_names):
        if n not in names and os.path.isdir(os.path.join(base, n)):
            names.append(n)
    radios = []
    for name in sorted(names):
        driver, bus, usb_id = _wifi_driver_info(name, base)
        flags = read_sysfs(os.path.join(base, name, "flags")) or "0"
        try:
            up = bool(int(flags, 16) & 1)  # IFF_UP
        except ValueError:
            up = False
        radios.append({"name": name, "kind": classify_wifi_radio(bus, usb_id),
                       "driver": driver, "up": up})
    return radios


def _nmcli_split(line):
    """Split one nmcli terse line on unescaped colons."""
    fields, cur, i = [], [], 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            cur.append(line[i + 1])
            i += 2
            continue
        if ch == ":":
            fields.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    fields.append("".join(cur))
    return fields


def wifi_security_label(raw):
    raw = (raw or "").strip()
    if raw in ("", "--"):
        return "Open"
    return "/".join(raw.split())


def parse_nmcli_wifi_list(text, radio):
    """Parse 'nmcli -t -f BSSID,SSID,CHAN,FREQ,SIGNAL,SECURITY dev wifi list'.

    NetworkManager reports signal as 0-100, not dBm. It derives that figure
    as 2 * (dBm + 100), so the inverse is used to get an approximate dBm.
    """
    networks = []
    for line in (text or "").splitlines():
        f = _nmcli_split(line)
        if len(f) != 6 or not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", f[0]):
            continue
        try:
            channel = int(f[2])
            freq = int(f[3].split()[0])
            pct = max(0, min(100, int(f[4])))
        except (ValueError, IndexError):
            continue
        networks.append({
            "ssid": f[1], "bssid": f[0].lower(), "channel": channel,
            "freq_mhz": freq, "signal_dbm": round(pct / 2 - 100),
            "security": wifi_security_label(f[5]), "radio": radio,
        })
    return networks


def parse_marauder_aps(lines, radio=FLIPPER_RADIO):
    """Parse Marauder 'list -a' lines such as '[0][CH:6] HomeNet -50'.

    The list carries no BSSID, so that field is empty, and no frequency is
    known for 2.4 GHz channels outside 1-14, so it is derived only for those.
    """
    networks = []
    for line in lines:
        m = _MARAUDER_AP_RE.match(line)
        if not m:
            continue
        channel = int(m.group(1))
        freq = 2407 + 5 * channel if 1 <= channel <= 13 else (2484 if channel == 14 else 0)
        networks.append({
            "ssid": m.group(2), "bssid": "", "channel": channel, "freq_mhz": freq,
            "signal_dbm": int(m.group(3)) if m.group(3) else 0,
            "security": "unknown", "radio": radio,
        })
    return networks


def dedupe_wifi_networks(networks):
    """One entry per (network, radio) pair, keeping the strongest reading."""
    best = {}
    for n in networks:
        key = (n["radio"], n["bssid"] or f"{n['ssid']}|{n['channel']}")
        if key not in best or n["signal_dbm"] > best[key]["signal_dbm"]:
            best[key] = n
    return sorted(best.values(), key=lambda n: n["signal_dbm"], reverse=True)


def scan_wifi_nmcli(iface):
    """Managed mode scan via NetworkManager. Returns (networks, error or None)."""
    nmcli = shutil.which("nmcli")
    if not nmcli:
        return [], "nmcli is not installed"
    cmd = [nmcli, "-t", "-f", "BSSID,SSID,CHAN,FREQ,SIGNAL,SECURITY",
           "dev", "wifi", "list", "ifname", iface, "--rescan"]
    err = None
    for rescan in ("yes", "no"):  # NM refuses back to back rescans; fall back to its cache
        try:
            out = subprocess.run(cmd + [rescan], capture_output=True, text=True, timeout=40)
        except (OSError, subprocess.SubprocessError) as exc:
            return [], str(exc)
        if out.returncode == 0:
            return parse_nmcli_wifi_list(out.stdout, iface), None
        err = (out.stderr or out.stdout).strip() or f"nmcli exited {out.returncode}"
    return [], err


class _NullLog:
    def write(self, line):
        pass


def scan_wifi_flipper(seconds=WIFI_SCAN_SECONDS):
    """Access points from a Flipper's Wi-Fi dev board, or None to skip silently.

    Never opens a port unless a Flipper is on the USB bus and the board answers.
    """
    flippers = [d for d in detect_companions() if d["kind"] == "flipper" and d["tty"]]
    if not flippers:
        return None
    path = flippers[0]["tty"]
    if probe_wifi_board(path) != "present":
        return None
    try:
        with MarauderSerial(path) as ser:
            marauder_scan(ser, "all", seconds, _NullLog(), say=lambda _l: None)
            lines = ser.collect(MARAUDER_LISTS["aps"])
    except (MarauderError, OSError):
        return None
    return parse_marauder_aps(lines)


def flipper_board_radio():
    """The Flipper's Wi-Fi dev board as a radio entry, or None.

    Like the scan, this never opens a port unless a Flipper is plugged in and
    the board answers, so people without one see no change at all.
    """
    flippers = [d for d in detect_companions() if d["kind"] == "flipper" and d["tty"]]
    if not flippers or probe_wifi_board(flippers[0]["tty"]) != "present":
        return None
    return {"name": FLIPPER_RADIO, "kind": FLIPPER_RADIO, "driver": "marauder", "up": True}


def collect_wifi_scan(iface=None, say=None):
    """Scan every available radio. Returns the documented JSON structure."""
    radios = detect_wifi_radios()
    networks = []
    for r in radios:
        if iface and r["name"] != iface:
            continue
        found, err = scan_wifi_nmcli(r["name"])
        if err and say:
            say(f"{r['name']}: could not scan ({err})")
        networks += found
    if iface in (None, FLIPPER_RADIO):
        flipper = scan_wifi_flipper()
        if flipper is not None:
            radios.append({"name": FLIPPER_RADIO, "kind": FLIPPER_RADIO,
                           "driver": "marauder", "up": True})
            networks += flipper
    if iface:
        radios = [r for r in radios if r["name"] == iface]
    return {"radios": radios, "networks": dedupe_wifi_networks(networks),
            "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%S")}


def format_wifi_radios(radios):
    if not radios:
        return "No Wi-Fi radios found."
    rows = [f"{'RADIO':<18}{'KIND':<18}{'DRIVER':<12}STATE"]
    for r in radios:
        rows.append(f"{r['name']:<18}{r['kind']:<18}{r['driver'] or '-':<12}"
                    f"{'up' if r['up'] else 'down'}")
    return "\n".join(rows)


def format_wifi_networks(networks):
    if not networks:
        return "No networks found."
    rows = [f"{'SIGNAL':>6}  {'CH':>3}  {'RADIO':<9} {'SECURITY':<14} SSID"]
    for n in sorted(networks, key=lambda n: n["signal_dbm"], reverse=True):
        rows.append(f"{n['signal_dbm']:>6}  {n['channel']:>3}  {n['radio'][:9]:<9} "
                    f"{n['security'][:14]:<14} {n['ssid'] or '(hidden)'}")
    return "\n".join(rows)


def save_wifi_scan(data, directory=None):
    """Write a scan as JSON. Folder 0700, file 0600, never overwrites."""
    directory = directory or CAPTURES_DIR
    os.makedirs(directory, mode=0o700, exist_ok=True)
    stamp = time.strftime("wifi-scan-%Y%m%d-%H%M%S")
    for n in range(100):
        path = os.path.join(directory, stamp + (f"-{n}" if n else "") + ".json")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        return path
    raise OSError("Could not find a free file name in " + directory)


def wifi_command(args):
    usage = (
        "Usage: aiov2_ctl --wifi radios [--json]\n"
        "       aiov2_ctl --wifi scan [--iface wlan0|wlan1] [--json] [--save]\n"
        "Passive only: lists Wi-Fi radios and does a normal managed mode scan.\n"
        "No root needed. --save keeps the scan in ~/aiov2-captures (private)."
    )
    args = list(args)
    as_json = "--json" in args
    save = "--save" in args
    args = [a for a in args if a not in ("--json", "--save")]
    iface = None
    if "--iface" in args:
        i = args.index("--iface")
        if i + 1 >= len(args) or not (
                _IFACE_RE.match(args[i + 1]) or args[i + 1] == FLIPPER_RADIO):
            print(usage)
            return 1
        iface = args[i + 1]
        del args[i:i + 2]
    if len(args) != 1 or args[0] not in ("radios", "scan") or (
            args[0] == "radios" and (iface or save)):
        print(usage)
        return 1

    if args[0] == "radios":
        radios = detect_wifi_radios()
        board = flipper_board_radio()
        if board:
            radios.append(board)
        if as_json:
            print(json.dumps({"radios": radios}, indent=2))
        else:
            print(format_wifi_radios(radios))
        return 0

    known = [r["name"] for r in detect_wifi_radios()]
    if iface and iface not in known + [FLIPPER_RADIO]:
        print(f"No such Wi-Fi radio: {iface}. Found: {', '.join(known) or 'none'}.",
              file=sys.stderr)
        return 1
    data = collect_wifi_scan(iface, say=lambda m: print(m, file=sys.stderr))
    if as_json:
        print(json.dumps(data, indent=2))
    else:
        print(format_wifi_radios(data["radios"]))
        print()
        print(format_wifi_networks(data["networks"]))
    if save:
        try:
            path = save_wifi_scan(data)
        except OSError as exc:
            print(f"Could not save: {exc}", file=sys.stderr)
            return 1
        print(f"Saved to {path} (private, mode 600).", file=sys.stderr if as_json else sys.stdout)
    return 0


# ---- Wired network: link, neighbours, passive listening, and an opt-in look around your own LAN ------------------
# Passive first. `status` and `neighbors` only read. `listen` opens a raw socket and just listens for the
# announcements hosts make on their own (ARP, mDNS, DHCP, LLDP), so it needs root but sends nothing.
# `sweep` and `ports` are active (they send ARP requests or open TCP connections), so they need --yes, only
# work on private address ranges of an interface this machine is on, and are rate limited. Nothing here attacks,
# spoofs, guesses passwords or goes beyond your own subnet.
import ipaddress
import struct

NET_PRIVATE = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16")]
NET_SWEEP_LIMIT = 1024            # hosts: a /22 at most
NET_SWEEP_RATE = 100              # ARP requests per second
NET_PORT_LIMIT = 200
NET_COMMON_PORTS = (21, 22, 23, 25, 53, 80, 110, 139, 143, 443, 445, 554, 631, 1883, 3389, 5900, 8000, 8080, 8443, 9100)
OUI_PATH = "/usr/share/ieee-data/oui.txt"
_OUI = {}


def is_private_ip(text):
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    return ip.version == 4 and any(ip in n for n in NET_PRIVATE)


def ethernet_interfaces(base=SYS_NET):
    """Names of real wired interfaces: ethernet type, not wireless, not virtual (docker, bridges, tunnels)."""
    out = []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return out
    for name in names:
        path = os.path.join(base, name)
        if read_sysfs(os.path.join(path, "type")) != "1" or os.path.isdir(os.path.join(path, "wireless")):
            continue
        try:
            target = os.path.realpath(path)
        except OSError:
            continue
        if "/devices/virtual/" in target:
            continue
        out.append(name)
    return out


def link_info(name, base=SYS_NET):
    """Link facts for one interface from sysfs: up or down, carrier, speed, duplex, address and driver."""
    path = os.path.join(base, name)
    carrier = read_sysfs(os.path.join(path, "carrier")) == "1"
    speed = read_sysfs(os.path.join(path, "speed"))
    try:
        speed = int(speed) if speed and int(speed) > 0 else None
    except ValueError:
        speed = None
    driver = ""
    try:
        driver = os.path.basename(os.readlink(os.path.join(path, "device", "driver")))
    except OSError:
        pass
    return {"name": name, "state": read_sysfs(os.path.join(path, "operstate")) or "unknown", "carrier": carrier,
            "speed_mbps": speed if carrier else None, "duplex": read_sysfs(os.path.join(path, "duplex")) if carrier else None,
            "mac": read_sysfs(os.path.join(path, "address")) or "", "driver": driver}


def parse_ip_addr4(text):
    """[(address, prefix length)] from `ip -4 -o addr show dev X`."""
    out = []
    for line in (text or "").splitlines():
        m = re.search(r"\binet (\d+\.\d+\.\d+\.\d+)/(\d+)", line)
        if m:
            out.append((m.group(1), int(m.group(2))))
    return out


def parse_ip_neigh(text):
    """[{ip, mac, state, dev}] from `ip -4 neigh`, skipping entries with no address."""
    out = []
    for line in (text or "").splitlines():
        parts = line.split()
        if not parts or not re.match(r"^\d+\.\d+\.\d+\.\d+$", parts[0]):
            continue
        mac = parts[parts.index("lladdr") + 1] if "lladdr" in parts and parts.index("lladdr") + 1 < len(parts) else ""
        dev = parts[parts.index("dev") + 1] if "dev" in parts and parts.index("dev") + 1 < len(parts) else ""
        if mac:
            out.append({"ip": parts[0], "mac": mac.lower(), "state": parts[-1], "dev": dev})
    return out


def oui_vendor(mac, path=OUI_PATH):
    """The maker of a network card from its address (needs the ieee-data package), or ''."""
    if not _OUI.get(path):
        table = {}
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = re.match(r"^([0-9A-F]{2})-([0-9A-F]{2})-([0-9A-F]{2})\s+\(hex\)\s+(.+)$", line)
                    if m:
                        table["".join(m.groups()[:3]).lower()] = m.group(4).strip()
        except OSError:
            pass
        _OUI[path] = table
    key = re.sub(r"[^0-9a-f]", "", (mac or "").lower())[:6]
    return _OUI[path].get(key, "")


def _mac(b):
    return ":".join(f"{x:02x}" for x in b)


def _dns_name(data, off, depth=0):
    """A DNS name starting at `off`, following compression pointers a few times. (name, next offset)."""
    labels, jumped, end = [], False, off
    for _ in range(40):
        if off >= len(data):
            return ".".join(labels), end
        n = data[off]
        if n == 0:
            return ".".join(labels), (end if jumped else off + 1)
        if n & 0xC0 == 0xC0 and off + 1 < len(data) and depth < 4:
            ptr = ((n & 0x3F) << 8) | data[off + 1]
            if not jumped:
                end = off + 2
            jumped = True
            off = ptr
            depth += 1
            continue
        labels.append(data[off + 1:off + 1 + n].decode("ascii", "replace"))
        off += 1 + n
    return ".".join(labels), end


def parse_frame(raw):
    """What a captured Ethernet frame tells us about a host, as {mac, ip, name, proto}, or None.

    Understands ARP, LLDP, mDNS (names the host announces) and DHCP (the host's own name for itself)."""
    if len(raw) < 14:
        return None
    src, ethertype = _mac(raw[6:12]), struct.unpack("!H", raw[12:14])[0]
    body = raw[14:]
    if ethertype == 0x0806 and len(body) >= 28:
        op = struct.unpack("!H", body[6:8])[0]
        sip = ".".join(str(b) for b in body[14:18])
        if sip != "0.0.0.0":
            return {"mac": _mac(body[8:14]), "ip": sip, "name": "", "proto": "arp" if op in (1, 2) else "arp?"}
        return None
    if ethertype == 0x88CC:
        name, pos = "", 0
        while pos + 2 <= len(body):
            head = struct.unpack("!H", body[pos:pos + 2])[0]
            kind, length = head >> 9, head & 0x1FF
            if kind == 0:
                break
            if kind == 5:
                name = body[pos + 2:pos + 2 + length].decode("ascii", "replace")
            pos += 2 + length
        return {"mac": src, "ip": "", "name": name, "proto": "lldp"}
    if ethertype == 0x0800 and len(body) >= 20:
        ihl = (body[0] & 0x0F) * 4
        proto, sip = body[9], ".".join(str(b) for b in body[12:16])
        if proto == 17 and len(body) >= ihl + 8:
            sport, dport = struct.unpack("!HH", body[ihl:ihl + 4])
            payload = body[ihl + 8:]
            if 5353 in (sport, dport) and len(payload) >= 12:
                qd, an = struct.unpack("!HH", payload[4:8])
                off = 12
                name = ""
                for _ in range(qd):
                    name, off = _dns_name(payload, off)
                    off += 4
                if an and not name:
                    name, off = _dns_name(payload, off)
                if an:
                    n2, _off = _dns_name(payload, 12) if qd == 0 else (name, off)
                    name = n2 or name
                return {"mac": src, "ip": sip, "name": name if an else "", "proto": "mdns"}
            if 68 in (sport, dport) and len(payload) >= 240 and payload[236:240] == b"\x63\x82\x53\x63":
                opts, pos, host = payload[240:], 0, ""
                while pos + 2 <= len(opts) and opts[pos] != 255:
                    if opts[pos] == 0:
                        pos += 1
                        continue
                    code, length = opts[pos], opts[pos + 1]
                    if code == 12:
                        host = opts[pos + 2:pos + 2 + length].decode("ascii", "replace")
                    pos += 2 + length
                return {"mac": src, "ip": sip if sip != "0.0.0.0" else "", "name": host, "proto": "dhcp"}
        if sip != "0.0.0.0":
            return {"mac": src, "ip": sip, "name": "", "proto": "ip"}
    return None


class HostTable:
    """Hosts seen on the wire, one per MAC address, with everything learned about each."""

    def __init__(self, own_macs=()):
        self.hosts, self.own = {}, {m.lower() for m in own_macs}

    def see(self, obs, now=None):
        if not obs or not obs.get("mac") or obs["mac"] in self.own or obs["mac"] == "ff:ff:ff:ff:ff:ff":
            return
        h = self.hosts.setdefault(obs["mac"], {"mac": obs["mac"], "ips": [], "names": [], "protos": [], "count": 0})
        h["count"] += 1
        h["seen"] = now if now is not None else time.time()
        for key, field in (("ip", "ips"), ("name", "names"), ("proto", "protos")):
            val = obs.get(key)
            if val and val not in h[field]:
                h[field].append(val)

    def rows(self):
        out = []
        for h in self.hosts.values():
            out.append({"mac": h["mac"], "ip": ", ".join(h["ips"]), "name": ", ".join(h["names"]),
                        "via": ", ".join(h["protos"]), "packets": h["count"], "vendor": oui_vendor(h["mac"])})
        return sorted(out, key=lambda r: (ipaddress.ip_address(r["ip"].split(", ")[0]) if r["ip"] else ipaddress.ip_address("255.255.255.255"), r["mac"]))


def sweep_targets(address, prefix, limit=NET_SWEEP_LIMIT):
    """The host addresses an ARP sweep of this subnet would ask about, or [] when it is not allowed:
    only private ranges, at most `limit` hosts, never our own address."""
    if not is_private_ip(address):
        return []
    net = ipaddress.ip_network(f"{address}/{prefix}", strict=False)
    if net.num_addresses - 2 > limit or net.prefixlen < 16:
        return []
    return [str(h) for h in net.hosts() if str(h) != address]


def arp_request(src_mac, src_ip, target_ip):
    """A raw ARP 'who has' frame, the same thing any computer sends before talking to a neighbour."""
    mac = bytes(int(x, 16) for x in src_mac.split(":"))
    return (b"\xff" * 6 + mac + b"\x08\x06" + struct.pack("!HHBBH", 1, 0x0800, 6, 4, 1) + mac +
            socket.inet_aton(src_ip) + b"\x00" * 6 + socket.inet_aton(target_ip))


def parse_ports(text):
    """A sorted list of ports from '22,80,8000-8010', or None when it is not valid or too long."""
    ports = set()
    try:
        for part in (text or "").split(","):
            part = part.strip()
            if "-" in part:
                a, b = (int(x) for x in part.split("-", 1))
                ports.update(range(a, b + 1))
            elif part:
                ports.add(int(part))
    except ValueError:
        return None
    if not ports or min(ports) < 1 or max(ports) > 65535 or len(ports) > NET_PORT_LIMIT:
        return None
    return sorted(ports)


def host_in_our_subnets(host, subnets):
    """True only when `host` is a private address inside one of our own interface subnets."""
    if not is_private_ip(host):
        return False
    ip = ipaddress.ip_address(host)
    return any(ip in net for net in subnets)


def _net_iface_addrs(name):
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", name], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_ip_addr4(out)


def net_status():
    """Every wired interface: link, speed, addresses, and a plain next step when there is no cable."""
    rows = []
    for name in ethernet_interfaces():
        info = link_info(name)
        info["addresses"] = [f"{a}/{p}" for a, p in _net_iface_addrs(name)]
        info["hint"] = "" if info["carrier"] else "No link. Plug a cable into the port and check the switch or router is on."
        rows.append(info)
    return rows


def net_neighbors(dev=None):
    try:
        out = subprocess.run(["ip", "-4", "neigh"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows = parse_ip_neigh(out)
    for r in rows:
        r["vendor"] = oui_vendor(r["mac"])
    return [r for r in rows if not dev or r["dev"] == dev]


def _pick_iface(iface):
    names = ethernet_interfaces()
    if iface:
        if iface not in names:
            raise OSError(f"{iface} is not a wired interface. Found: {', '.join(names) or 'none'}.")
        return iface
    for name in names:
        if link_info(name)["carrier"]:
            return name
    raise OSError("No wired interface has a link. Plug in a cable (or pass --iface).")


def net_listen(iface, seconds, say=print):
    """Listen without sending anything and return the hosts that announced themselves. Needs root."""
    try:
        sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(0x0003))
    except PermissionError:
        raise OSError("Listening needs root: run it as 'sudo aiov2_ctl --net listen'.")
    own = [link_info(iface)["mac"]]
    table = HostTable(own)
    try:
        sock.bind((iface, 0))
        sock.settimeout(0.5)
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                raw = sock.recv(2048)
            except socket.timeout:
                continue
            table.see(parse_frame(raw))
    finally:
        sock.close()
    return table.rows()


def net_sweep(iface, say=print):
    """Ask every address on our own subnet 'who has it?' with ARP, at a gentle rate, and return who answers."""
    addrs = _net_iface_addrs(iface)
    if not addrs:
        raise OSError(f"{iface} has no IPv4 address yet, so there is nothing to sweep.")
    address, prefix = addrs[0]
    targets = sweep_targets(address, prefix)
    if not targets:
        raise OSError(f"{address}/{prefix} is not a private subnet of 1022 hosts or fewer, so it is not swept.")
    mac = link_info(iface)["mac"]
    try:
        sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(0x0003))
    except PermissionError:
        raise OSError("Sweeping needs root: run it as 'sudo aiov2_ctl --net sweep --yes'.")
    table = HostTable([mac])
    try:
        sock.bind((iface, 0))
        sock.settimeout(0.05)
        for i, target in enumerate(targets):
            sock.send(arp_request(mac, address, target))
            deadline = time.monotonic() + 1.0 / NET_SWEEP_RATE
            while time.monotonic() < deadline:
                try:
                    obs = parse_frame(sock.recv(2048))
                except socket.timeout:
                    break
                if obs and obs["proto"] == "arp" and obs["ip"] in targets:
                    table.see(obs)
        end = time.monotonic() + 2.0                      # let the stragglers answer
        while time.monotonic() < end:
            try:
                obs = parse_frame(sock.recv(2048))
            except socket.timeout:
                continue
            if obs and obs["proto"] == "arp" and obs["ip"] in targets:
                table.see(obs)
    finally:
        sock.close()
    return table.rows()


def net_ports(host, ports):
    """Which of `ports` accept a normal TCP connection on a host in our own subnet. Needs no root."""
    from concurrent.futures import ThreadPoolExecutor

    def probe(port):
        try:
            with socket.create_connection((host, port), timeout=0.6):
                return port
        except OSError:
            return None
    with ThreadPoolExecutor(max_workers=16) as pool:
        return [p for p in pool.map(probe, ports) if p]


def net_command(args):
    usage = (
        "Usage: aiov2_ctl --net status [--json]\n"
        "       aiov2_ctl --net neighbors [--json]\n"
        "       aiov2_ctl --net listen [--iface eth0] [--seconds 30] [--json]   (passive, needs root)\n"
        "       aiov2_ctl --net sweep [--iface eth0] --yes [--json]             (active ARP sweep of your own subnet, needs root)\n"
        "       aiov2_ctl --net ports HOST [--ports 22,80,443] --yes [--json]  (TCP connect check of one host on your subnet)\n"
        "Passive by default. sweep and ports send traffic, so they need --yes and only work on private addresses\n"
        "of a network this machine is on. Nothing here attacks, spoofs or guesses passwords."
    )
    args = list(args)
    as_json = "--json" in args
    yes = "--yes" in args
    args = [a for a in args if a not in ("--json", "--yes")]

    def take(flag, default=None):
        if flag in args:
            i = args.index(flag)
            if i + 1 >= len(args):
                raise ValueError(flag)
            value = args[i + 1]
            del args[i:i + 2]
            return value
        return default
    try:
        iface, seconds, port_text = take("--iface"), take("--seconds", "30"), take("--ports")
        seconds = float(seconds)
    except ValueError:
        print(usage)
        return 1
    if not args or args[0] not in ("status", "neighbors", "listen", "sweep", "ports") or not 1 <= seconds <= 600:
        print(usage)
        return 1
    sub, rest = args[0], args[1:]

    def show(rows, header, empty):
        if as_json:
            print(json.dumps(rows, indent=2))
        elif not rows:
            print(empty)
        else:
            print(header)
            for r in rows:
                print("  " + "  ".join(f"{v}" for v in r.values() if v not in ("", None)))

    try:
        if sub == "status" and not rest:
            rows = net_status()
            if as_json:
                print(json.dumps({"interfaces": rows}, indent=2))
            elif not rows:
                print("No wired network interface found.")
            for r in ([] if as_json else rows):
                speed = f", {r['speed_mbps']} Mbit/s {r['duplex'] or ''}".rstrip() if r["speed_mbps"] else ""
                print(f"{r['name']}: {'link up' if r['carrier'] else 'no link'}{speed}  {', '.join(r['addresses']) or 'no address'}  ({r['driver'] or 'unknown driver'})")
                if r["hint"]:
                    print(f"  {r['hint']}")
            return 0
        if sub == "neighbors" and not rest:
            show(net_neighbors(), "Neighbours the system already knows (nothing was sent):", "No neighbours known yet.")
            return 0
        if sub == "listen" and not rest:
            name = _pick_iface(iface)
            print(f"Listening on {name} for {seconds:g}s. Nothing is sent.", file=sys.stderr if as_json else sys.stdout)
            show(net_listen(name, seconds), "Hosts that announced themselves:", "Nobody announced themselves in that time.")
            return 0
        if sub == "sweep" and not rest:
            if not yes:
                print("A sweep asks every address on your subnet who it is. Add --yes if this is your own network.")
                return 1
            name = _pick_iface(iface)
            print(f"Sweeping the subnet on {name} (about {NET_SWEEP_RATE} requests a second)...", file=sys.stderr if as_json else sys.stdout)
            show(net_sweep(name), "Hosts that answered:", "Nobody answered.")
            return 0
        if sub == "ports" and len(rest) == 1:
            if not yes:
                print("A port check opens a TCP connection to each port. Add --yes if this is your own network.")
                return 1
            ports = parse_ports(port_text) if port_text else list(NET_COMMON_PORTS)
            if ports is None:
                print(f"--ports must be numbers from 1 to 65535, at most {NET_PORT_LIMIT} of them.")
                return 1
            subnets = [ipaddress.ip_network(f"{a}/{p}", strict=False) for n in ethernet_interfaces() for a, p in _net_iface_addrs(n)]
            if not host_in_our_subnets(rest[0], subnets):
                print(f"{rest[0]} is not a private address on a wired network this machine is on, so it is not checked.")
                return 1
            found = net_ports(rest[0], ports)
            if as_json:
                print(json.dumps({"host": rest[0], "open": found, "checked": len(ports)}, indent=2))
            else:
                print(f"{rest[0]}: {len(found)} of {len(ports)} ports open" + (": " + ", ".join(map(str, found)) if found else ""))
            return 0
    except OSError as exc:
        print(f"Error: {exc}")
        return 1
    print(usage)
    return 1


# ---- Wi-Fi monitor mode, for Sense -------------------------------------------
# Passive only: the radio is put in monitor mode, every frame it hears is read
# for its signal strength and who sent it, and nothing is ever transmitted.
# The radio is always put back (managed, NetworkManager in charge again) when
# this stops, however it stops. Needs root and the 'iw' package.
MONITOR_IFACE = "wlan1"
MONITOR_CHANNELS = tuple(range(1, 14)) + (36, 40, 44, 48, 149, 153, 157, 161, 165)
MONITOR_SECONDS = 3600
MONITOR_LINKS_PER_SECOND = 8          # senders reported in full (every reading) each second: the busiest
MONITOR_BRIEF_LINKS = 56              # further senders reported as just [frames, average], so none simply vanishes
_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
SYSTEM_PACKAGES = {"iw": "/usr/sbin/iw"}      # apt package -> a file it provides


def radiotap_info(raw):
    """(signal_dbm or None, bad_checksum, header_length) from a radiotap header, or None if it is not one."""
    got = radiotap_fields(raw)
    return got[:3] if got else None


def radiotap_fields(raw):
    """(signal_dbm or None, bad_checksum, header_length, freq_mhz or None) from a radiotap header, or None."""
    if len(raw) < 8 or raw[0] != 0:
        return None
    length = struct.unpack_from("<H", raw, 2)[0]
    if length < 8 or length > len(raw):
        return None
    present = struct.unpack_from("<I", raw, 4)[0]
    pos, word = 8, present
    while word & 0x80000000:
        if pos + 4 > length:
            return None
        word = struct.unpack_from("<I", raw, pos)[0]
        pos += 4
    flags, signal, freq = 0, None, None
    for bit, (align, size) in enumerate(((8, 8), (1, 1), (1, 1), (2, 4), (2, 2), (1, 1))):
        if not present & (1 << bit):
            continue
        pos = (pos + align - 1) // align * align
        if pos + size > length:
            return None
        if bit == 1:
            flags = raw[pos]
        elif bit == 3:
            freq = struct.unpack_from("<H", raw, pos)[0]
        elif bit == 5:
            signal = struct.unpack_from("<b", raw, pos)[0]
        pos += size
    return signal, bool(flags & 0x40), length, freq


def monitor_frame(raw):
    """(transmitter MAC, signal dBm) for a good frame that carries both, else None."""
    info = radiotap_info(raw)
    if not info or info[0] is None or info[1]:
        return None
    signal, _bad, off = info
    if not -120 <= signal < 0 or len(raw) < off + 16:
        return None
    if (raw[off] >> 2) & 3 == 1:               # control frames mostly lack a transmitter address
        return None
    mac = raw[off + 10:off + 16]
    if mac[0] & 1:                             # a transmitter is never a group address
        return None
    return ":".join(f"{b:02x}" for b in mac), signal


MONITOR_READINGS_PER_LINK = 40


def monitor_summary(seen, now, only=None):
    """One second of readings as a record the Sense page can read.

    `seen` holds (mac, dbm) pairs, or (mac, dbm, time) triples. With times, each sender also gets its individual
    readings as [seconds before the end of the record, dbm], so Sense can follow the signal frame by frame.

    The busiest senders are reported in full. A quiet one used to be left out altogether, so an access point
    chosen to be watched could seem not to be heard while it was. Now the next busiest are still listed, as
    frames and average only, and `only` (one sender's address) is always reported in full when it was heard."""
    per, timed = {}, {}
    for item in seen:
        mac, dbm = item[0], item[1]
        per.setdefault(mac, []).append(dbm)
        if len(item) > 2:
            timed.setdefault(mac, []).append([round(item[2] - now, 2), dbm])
    ranked = sorted(per.items(), key=lambda kv: -len(kv[1]))
    links = ranked[:MONITOR_LINKS_PER_SECOND + MONITOR_BRIEF_LINKS]
    full = {m for m, _v in ranked[:MONITOR_LINKS_PER_SECOND]}
    if only and only in per:
        full.add(only)
        if only not in dict(links):
            links.append((only, per[only]))
    out = {}
    for m, v in links:
        entry = [len(v), round(mean(v), 1)]
        if m in timed and m in full:
            readings = timed[m]
            step = max(1, len(readings) // MONITOR_READINGS_PER_LINK)
            entry.append(readings[::step][:MONITOR_READINGS_PER_LINK])
        out[m] = entry
    return {
        "t": round(now, 2),
        "frames": len(seen),
        "rssi": round(mean(item[1] for item in seen), 1) if seen else None,
        "links": out,
    }


def _tool(name):
    return shutil.which(name) or shutil.which(name, path="/usr/sbin:/sbin:/usr/bin:/bin")


def _monitor_cmd(*cmd, check=True):
    return subprocess.run(list(cmd), check=check, capture_output=True, text=True, timeout=15)


def monitor_restore(iface):
    """Put the radio back the way NetworkManager expects it. Safe to run any time."""
    ip, iw = _tool("ip"), _tool("iw")
    if iw and ip:
        _monitor_cmd(ip, "link", "set", iface, "down", check=False)
        _monitor_cmd(iw, "dev", iface, "set", "type", "managed", check=False)
        _monitor_cmd(ip, "link", "set", iface, "up", check=False)
    if _tool("nmcli"):
        _monitor_cmd("nmcli", "device", "set", iface, "managed", "yes", check=False)


_RADIO_OWNED = {}                    # iface -> lock held by this process for as long as it listens


def own_radio(iface, acquire=None):
    """Make this process the one listener on a radio, or refuse. Survey and Sense both put the internal radio
    into monitor mode; two at once fought over its channel and one's clean-up pulled the radio from under the
    other. The claim is a lock held until this process ends, however it ends, so a crash never leaves the radio
    claimed."""
    if iface in _RADIO_OWNED:
        return _RADIO_OWNED[iface]
    try:
        _RADIO_OWNED[iface] = (acquire or acquire_device_lock)(f"radio-{iface}", wait=0)
    except DeviceBusy:
        raise OSError(f"{iface} is already listening for something else (Survey or Sense). Stop that first.")
    return _RADIO_OWNED[iface]


def monitor_check(iface):
    """Refuse before touching anything: needs iw, a real Wi-Fi interface, one that is not connected, and one
    that nothing else is already listening on."""
    iw, ip = _tool("iw"), _tool("ip")
    if not iw or not ip:
        raise OSError("'iw' is not installed. Run: sudo aiov2_ctl --install   (or: sudo apt install iw)")
    if not _IFACE_RE.match(iface) or not os.path.isdir(os.path.join(SYS_NET, iface, "wireless")):
        raise OSError(f"{iface} is not a Wi-Fi interface on this machine.")
    link = _monitor_cmd(iw, "dev", iface, "link", check=False).stdout
    if "Connected to" in link:
        raise OSError(f"{iface} is connected to a network. Disconnect it first; monitor mode would drop that connection.")
    own_radio(iface)


def monitor_setup(iface, channel):
    """Put the radio in monitor mode on a channel and return a raw socket reading it. The caller must restore."""
    iw, ip = _tool("iw"), _tool("ip")
    if _tool("nmcli"):
        _monitor_cmd("nmcli", "device", "set", iface, "managed", "no", check=False)
    _monitor_cmd(ip, "link", "set", iface, "down")
    _monitor_cmd(iw, "dev", iface, "set", "type", "monitor")
    _monitor_cmd(ip, "link", "set", iface, "up")
    _monitor_cmd(iw, "dev", iface, "set", "channel", str(channel))
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(0x0003))
    sock.bind((iface, 0))
    sock.settimeout(0.25)
    return sock


def monitor_run(iface, channel, seconds, emit, only=None):
    """Listen on one channel for `seconds`, calling emit(record) every second. Restores the radio afterwards.
    `only` is one sender's address that is always reported in full when heard."""
    import signal
    if os.geteuid() != 0:
        raise OSError("Monitor mode needs root: run it as 'sudo aiov2_ctl --wifi-listen'.")
    monitor_check(iface)

    def stop(_sig, _frm):
        raise KeyboardInterrupt
    old = signal.signal(signal.SIGTERM, stop)
    sock = None
    try:
        sock = monitor_setup(iface, channel)
        end = time.monotonic() + seconds
        bucket_end, seen = time.monotonic() + 1.0, []
        while time.monotonic() < end:
            try:
                got = monitor_frame(sock.recv(4096))
                if got:
                    seen.append(got + (time.time(),))
            except socket.timeout:
                pass
            if time.monotonic() >= bucket_end:
                emit(monitor_summary(seen, time.time(), only))
                bucket_end, seen = bucket_end + 1.0, []
    except KeyboardInterrupt:
        pass
    except subprocess.CalledProcessError as exc:
        raise OSError(f"Could not set {iface} up for monitoring: {(exc.stderr or '').strip() or exc}")
    finally:
        if sock:
            sock.close()
        monitor_restore(iface)
        signal.signal(signal.SIGTERM, old)


def monitor_command(args):
    usage = (
        "Usage: aiov2_ctl --wifi-listen [--iface wlan1] [--channel 6] [--seconds 3600] [--only aa:bb:cc:dd:ee:ff|-] [--json]   (passive, needs root)\n"
        "       aiov2_ctl --wifi-listen --restore [--iface wlan1]                                  (put the radio back to normal)\n"
        "Listens to the Wi-Fi traffic in the air on one channel and reports its signal strength once a second.\n"
        "--only names one sender that is always reported in full; '--only -' reads that address from standard input.\n"
        "Never transmits. The radio is returned to normal mode when it stops."
    )
    args = list(args)
    as_json, restore = "--json" in args, "--restore" in args
    args = [a for a in args if a not in ("--json", "--restore")]
    opts = {"--iface": MONITOR_IFACE, "--channel": "6", "--seconds": str(MONITOR_SECONDS), "--only": ""}
    while args:
        if args[0] not in opts or len(args) < 2:
            print(usage)
            return 1
        opts[args[0]] = args[1]
        del args[:2]
    try:
        channel, seconds = int(opts["--channel"]), float(opts["--seconds"])
    except ValueError:
        print(usage)
        return 1
    iface = opts["--iface"]
    if channel not in MONITOR_CHANNELS or not 1 <= seconds <= MONITOR_SECONDS or not _IFACE_RE.match(iface):
        print(usage)
        return 1
    only = opts["--only"]
    if only == "-" and not restore:
        # The address comes on standard input, not as an argument: the password-free sudo rules list every allowed
        # command word for word, and an address cannot be one of a fixed list. It is only ever compared with the
        # addresses heard, and anything that is not exactly an address is refused.
        only = sys.stdin.readline(64).strip()
        if not only:
            print(usage)
            return 1
    only = only.lower() if only != "-" else ""
    if only and not _MAC_RE.match(only):
        print(usage)
        return 1
    try:
        if restore:
            if os.geteuid() != 0:
                raise OSError("Restoring needs root: run it as 'sudo aiov2_ctl --wifi-listen --restore'.")
            monitor_restore(iface)
            print(f"{iface} put back to normal.")
            return 0

        def emit(rec):
            if as_json:
                print(json.dumps(rec), flush=True)
            else:
                print(f"{rec['frames']:4d} frames  {rec['rssi'] if rec['rssi'] is not None else '--':>6} dBm  {len(rec['links'])} senders", flush=True)
        if only:
            monitor_run(iface, channel, seconds, emit, only=only)
        else:
            monitor_run(iface, channel, seconds, emit)
        return 0
    except OSError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


# ---- Passive survey: access points, stations, probes, deauth floods and channel use ---------------------------
# Listens only. Hops between channels, reads the 802.11 headers of what it hears and keeps tables. The optional
# capture file holds radiotap frames with the payload of data frames cut off (headers only), private to the user.
SURVEY_BANDS = {"2g": tuple(range(1, 14)), "5g": (36, 40, 44, 48, 149, 153, 157, 161, 165),
                "all": tuple(range(1, 14)) + (36, 40, 44, 48, 149, 153, 157, 161, 165)}
SURVEY_DWELL = 0.5
SURVEY_EVERY = 2.0
SURVEY_ROWS = 150
DEAUTH_WINDOW, DEAUTH_ALERT = 5.0, 10
_SURVEY_SUBTYPES = {8: "beacon", 5: "proberesp", 4: "probereq", 12: "deauth", 10: "disassoc"}


def freq_to_channel(freq):
    if not freq:
        return None
    if freq == 2484:
        return 14
    if 2412 <= freq <= 2472:
        return (freq - 2407) // 5
    if 5000 <= freq <= 5900:
        return (freq - 5000) // 5
    return None


def _mac(b):
    return ":".join(f"{x:02x}" for x in b)


def _ies(buf):
    """{element id: first body} for a run of information elements (stops quietly at anything malformed)."""
    out, pos = {}, 0
    while pos + 2 <= len(buf):
        eid, n = buf[pos], buf[pos + 1]
        if pos + 2 + n > len(buf):
            break
        out.setdefault(eid, buf[pos + 2:pos + 2 + n])
        if eid == 221 and n >= 4 and buf[pos + 2:pos + 6] == b"\x00\x50\xf2\x01":
            out.setdefault("wpa", True)
        pos += 2 + n
    return out


def security_of(capability, ies):
    rsn = ies.get(48)
    if rsn is not None:
        akms = set()
        try:
            pos = 2 + 4
            pairs = struct.unpack_from("<H", rsn, pos)[0]
            pos += 2 + 4 * pairs
            count = struct.unpack_from("<H", rsn, pos)[0]
            pos += 2
            for _ in range(count):
                if rsn[pos:pos + 3] == b"\x00\x0f\xac":
                    akms.add(rsn[pos + 3])
                pos += 4
        except (struct.error, IndexError):
            pass
        if akms & {1, 5, 11, 12}:
            return "WPA3-Enterprise" if 8 in akms else "WPA2-Enterprise"
        if 18 in akms and not akms & {2, 8}:
            return "OWE"
        if 8 in akms:
            return "WPA3" if not akms & {2, 6} else "WPA2/WPA3"
        return "WPA2"
    if ies.get("wpa"):
        return "WPA"
    return "WEP" if capability & 0x10 else "Open"


def parse_dot11(raw, off):
    """What a frame says about who is around, or None. Never reads past the header unless it must.

    Every result that names a sender carries "tx", the transmitter address (address 2). The signal strength in
    the radiotap header was measured on that transmitter's frame, so it belongs to "tx" and to nobody else: an
    access point's frame to a station says nothing about how strongly the station is heard."""
    if len(raw) < off + 16:
        return None
    fc0, fc1 = raw[off], raw[off + 1]
    ftype, sub = (fc0 >> 2) & 3, fc0 >> 4
    a1, a2 = raw[off + 4:off + 10], raw[off + 10:off + 16]
    if ftype == 1:
        # Request to send, block ack and block ack request name both ends. Which one is the access point is
        # decided later, from the access points already heard.
        if sub in (8, 9, 11) and not a1[0] & 1 and not a2[0] & 1:
            return {"kind": "pair", "a": _mac(a1), "b": _mac(a2), "tx": _mac(a2)}
        return None
    if len(raw) < off + 24:
        return None
    a3 = raw[off + 16:off + 22]
    if ftype == 0:
        kind = _SURVEY_SUBTYPES.get(sub)
        if kind in ("beacon", "proberesp"):
            if len(raw) < off + 36 or a2[0] & 1:
                return None
            cap = struct.unpack_from("<H", raw, off + 34)[0]
            ies = _ies(raw[off + 36:])
            ssid = ies.get(0, b"")
            ssid = "" if ssid and not any(ssid) else ssid.decode("utf-8", "replace")
            ch = ies.get(3)[0] if ies.get(3) else (ies.get(61)[0] if ies.get(61) else None)
            return {"kind": kind, "bssid": _mac(a3), "ssid": ssid, "channel": ch, "security": security_of(cap, ies),
                    "tx": _mac(a2)}
        if kind == "probereq":
            if a2[0] & 1:
                return None
            ssid = _ies(raw[off + 24:]).get(0, b"")
            return {"kind": kind, "sta": _mac(a2), "ssid": ssid.decode("utf-8", "replace") if ssid and any(ssid) else "",
                    "tx": _mac(a2)}
        if kind in ("deauth", "disassoc"):
            return {"kind": kind, "src": _mac(a2), "dst": _mac(a1), "bssid": _mac(a3)}
        if sub in (0, 1, 2, 3, 11, 13) and not a1[0] & 1 and not a2[0] & 1 and not a3[0] & 1:
            # Association, reassociation, authentication and action frames: whichever end is the BSSID is the
            # access point, the other is a device talking to it.
            if a3 == a2:
                return {"kind": "data", "bssid": _mac(a3), "sta": _mac(a1), "tx": _mac(a2)}
            if a3 == a1:
                return {"kind": "data", "bssid": _mac(a3), "sta": _mac(a2), "tx": _mac(a2)}
        return None
    if ftype == 2:
        # To DS only: a1 = BSSID, a2 = station (the transmitter). From DS only: a1 = station, a2 = BSSID (the
        # transmitter). Neither (a direct or ad hoc link) and both (a bridge between access points) do not say
        # which end is a station of which network, so they name nobody.
        to_ds, from_ds = bool(fc1 & 1), bool(fc1 & 2)
        if to_ds == from_ds:
            return None
        bssid, sta = (a1, a2) if to_ds else (a2, a1)
        if sta[0] & 1 or bssid[0] & 1 or sta == bssid:
            return None
        return {"kind": "data", "bssid": _mac(bssid), "sta": _mac(sta), "tx": _mac(a2)}
    return None


def channel_activity(counts, dwell):
    """{channel: activity} from frame counts and the seconds each channel was actually listened to.

    Channels are listened to for different lengths of time (a failed channel change, or the report cycle, can
    leave one tuned longer), so raw counts are not comparable. Each entry keeps the raw "frames" and "bytes",
    adds "seconds" listened and "per_second" (frames per second of listening: a frame rate, not airtime), and
    a channel never listened to has "per_second" None and "note" "not listened to" rather than a zero."""
    out = {}
    for ch in sorted(set(counts) | set(dwell)):
        got = counts.get(ch, {})
        frames, seconds = got.get("frames", 0), max(0.0, float(dwell.get(ch, 0.0)))
        entry = {"frames": frames, "bytes": got.get("bytes", 0), "seconds": round(seconds, 2)}
        if seconds > 0:
            entry["per_second"] = round(frames / seconds, 2)
        else:
            entry["per_second"], entry["note"] = None, "not listened to"
        out[ch] = entry
    return out


class Survey:
    """The tables a survey fills in. Feed it frames with see(); say how long each channel was listened to with
    listened(); read it with snapshot()."""

    def __init__(self):
        self.aps, self.stations, self.probes, self.deauth, self.channels = {}, {}, {}, {}, {}
        self.dwell = {}

    def listened(self, channel, seconds):
        """Add `seconds` of actual listening on `channel` (only time the radio was really tuned there)."""
        if channel is not None and seconds > 0:
            self.dwell[channel] = self.dwell.get(channel, 0.0) + seconds

    def see(self, raw, now):
        fields = radiotap_fields(raw)
        if not fields or fields[1]:
            return None
        signal, _bad, off, freq = fields
        ch = freq_to_channel(freq)
        if ch:
            slot = self.channels.setdefault(ch, {"frames": 0, "bytes": 0})
            slot["frames"] += 1
            slot["bytes"] += max(0, len(raw) - off)
        info = parse_dot11(raw, off)
        if not info or signal is None or not -120 <= signal < 0:
            return info
        kind = info["kind"]
        tx = info.get("tx")
        if kind == "pair":
            if info["a"] in self.aps:
                info = {"kind": "data", "bssid": info["a"], "sta": info["b"], "tx": tx}
            elif info["b"] in self.aps:
                info = {"kind": "data", "bssid": info["b"], "sta": info["a"], "tx": tx}
            else:
                return info
            kind = "data"
        if kind in ("beacon", "proberesp"):
            if tx != info["bssid"] and info["bssid"] not in self.aps:
                return info                      # sent by someone other than the access point it names
            ap = self.aps.setdefault(info["bssid"], {"first": now, "beacons": 0, "ssid": "", "rssi": float(signal)})
            if tx == info["bssid"]:
                ap["rssi"] = 0.7 * ap["rssi"] + 0.3 * signal
            ap["last"], ap["security"] = now, info["security"]
            ap["channel"] = info["channel"] or ch or ap.get("channel")
            if info["ssid"]:
                ap["ssid"] = info["ssid"]
            if kind == "beacon":
                ap["beacons"] += 1
        elif kind == "data":
            # The frame counts towards the station either way, but its signal goes to whichever end sent it.
            sta = self.stations.setdefault(info["sta"], {"frames": 0, "rssi": None})
            sta.update(bssid=info["bssid"], last=now)
            sta["frames"] += 1
            if tx == info["sta"]:
                sta["rssi"] = float(signal) if sta["rssi"] is None else 0.7 * sta["rssi"] + 0.3 * signal
            elif tx == info["bssid"] and info["bssid"] in self.aps:
                ap = self.aps[info["bssid"]]
                ap["rssi"] = 0.7 * ap["rssi"] + 0.3 * signal
        elif kind == "probereq":
            pr = self.probes.setdefault(info["sta"], {"ssids": [], "count": 0, "rssi": float(signal)})
            pr["count"] += 1
            pr["last"], pr["rssi"] = now, 0.7 * pr["rssi"] + 0.3 * signal
            if info["ssid"] and info["ssid"] not in pr["ssids"] and len(pr["ssids"]) < 8:
                pr["ssids"].append(info["ssid"])
        elif kind in ("deauth", "disassoc"):
            d = self.deauth.setdefault(info["bssid"], {"total": 0, "recent": []})
            d["total"] += 1
            d["recent"] = [x for x in d["recent"] if now - x < DEAUTH_WINDOW] + [now]
            d["last"] = now
        return info

    def snapshot(self, now, channel=None):
        aps = sorted(self.aps.items(), key=lambda kv: -kv[1]["rssi"])[:SURVEY_ROWS]
        stas = sorted(self.stations.items(), key=lambda kv: -kv[1]["last"])[:SURVEY_ROWS]
        probes = sorted(self.probes.items(), key=lambda kv: -kv[1]["last"])[:SURVEY_ROWS]
        floods = []
        for bssid, d in self.deauth.items():
            recent = len([x for x in d["recent"] if now - x < DEAUTH_WINDOW])
            if recent >= DEAUTH_ALERT:
                floods.append({"bssid": bssid, "frames": recent, "total": d["total"]})
        return {
            "t": round(now, 2), "channel": channel,
            "aps": [{"bssid": b, "ssid": a["ssid"], "channel": a.get("channel"), "security": a.get("security", ""),
                     "rssi": round(a["rssi"], 1), "beacons": a["beacons"], "first": round(a["first"], 1),
                     "last": round(a["last"], 1)} for b, a in aps],
            "stations": [{"mac": m, "bssid": s["bssid"],
                          "rssi": None if s["rssi"] is None else round(s["rssi"], 1), "frames": s["frames"],
                          "last": round(s["last"], 1)} for m, s in stas],
            "probes": [{"mac": m, "ssids": p["ssids"], "count": p["count"], "rssi": round(p["rssi"], 1),
                        "last": round(p["last"], 1)} for m, p in probes],
            "deauth": sorted(floods, key=lambda f: -f["frames"]),
            "channels": {str(c): v for c, v in channel_activity(self.channels, self.dwell).items()},
        }


_KEEP_WHOLE = (4, 5, 8)            # probe request, probe response, beacon: what lists networks and devices
_KEEP_REASON = (10, 12)            # disassociation, deauthentication: the header and the two-byte reason code
_EAPOL_SNAP = b"\xaa\xaa\x03\x00\x00\x00\x88\x8e"


def capture_keep(frame):
    """The part of one 802.11 frame (without radiotap) that may be written to a capture file, or None to drop it.

    Beacons, probe requests and probe responses are kept whole, as they are what lists networks and devices.
    Deauthentication and disassociation frames keep their header and reason code. Every other management frame,
    authentication and association included, keeps its header only, so no authentication body reaches the file.
    Control frames carry no body and are kept. Data frames keep their header only, and a data frame carrying
    EAPOL (the key exchange, where the PMKID and handshake live) is dropped entirely. Anything malformed, or of
    the reserved or extension type, is dropped."""
    if len(frame) < 10:
        return None
    fc0, fc1 = frame[0], frame[1]
    if fc0 & 3:
        return None
    ftype, sub = (fc0 >> 2) & 3, fc0 >> 4
    if ftype == 1:
        return bytes(frame)
    if ftype == 0:
        if len(frame) < 24:
            return None
        if sub in _KEEP_WHOLE:
            return bytes(frame)
        if sub in _KEEP_REASON:
            return bytes(frame[:26])
        return bytes(frame[:24])
    if ftype == 2:
        qos = bool(sub & 8)
        hdr = 24 + (6 if fc1 & 3 == 3 else 0) + (2 if qos else 0) + (4 if qos and fc1 & 0x80 else 0)
        if len(frame) < hdr:
            return None
        body = frame[hdr:hdr + 8]
        if not fc1 & 0x40 and (body == _EAPOL_SNAP or (len(body) < 8 and _EAPOL_SNAP.startswith(body) and body)):
            return None
        return bytes(frame[:hdr])
    return None


class PcapWriter:
    """A classic pcap file of radiotap frames, created private to the user (mode 0600). What is kept of each frame
    is decided by capture_keep: no data payload, no EAPOL frame and no authentication body is ever written."""

    SNAPLEN = 4096

    def __init__(self, path, owner=None, dir_fd=None):
        self.fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dir_fd)
        if owner:
            try:
                os.chown(self.fd, owner.pw_uid, owner.pw_gid)
            except OSError:
                pass
        os.write(self.fd, struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, self.SNAPLEN, 127))

    def write(self, raw, now):
        """Write what capture_keep allows of one radiotap frame. A frame whose radiotap header cannot be read is
        not written at all. Returns True if anything was written."""
        fields = radiotap_fields(raw)
        if not fields:
            return False
        kept = capture_keep(raw[fields[2]:])
        if kept is None:
            return False
        out = (bytes(raw[:fields[2]]) + kept)[:self.SNAPLEN]
        os.write(self.fd, struct.pack("<IIII", int(now), int((now % 1) * 1e6), len(out), len(raw)) + out)
        return True

    def close(self):
        os.close(self.fd)


def _survey_owner():
    """The user who ran sudo, so a capture file ends up in their own folder and belongs to them."""
    name = os.environ.get("SUDO_USER")
    if not name or os.geteuid() != 0:
        return None
    try:
        return pwd.getpwnam(name)
    except KeyError:
        return None


def capture_folder_fd(folder, owner=None):
    """Open the capture folder, making it if need be, and return it as an open folder for files to be created in.

    Root makes this folder inside the calling user's home. Changing its owner by name would follow a link put
    there in its place and hand some other folder to that user, so the folder is opened without following links,
    must be a real folder, and its owner is changed through the open folder: only when root has just made it.
    A folder that belongs to anyone else is refused."""
    made = False
    try:
        os.mkdir(folder, 0o700)
        made = True
    except FileExistsError:
        pass
    dfd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        st = os.fstat(dfd)
        want = owner.pw_uid if owner else os.geteuid()
        if owner and made and st.st_uid == os.geteuid() and st.st_uid != want:
            os.fchown(dfd, owner.pw_uid, owner.pw_gid)
            st = os.fstat(dfd)
        if st.st_uid != want or stat.S_IMODE(st.st_mode) & 0o022:
            raise OSError(f"{folder} is not a private folder of the user, so no capture is saved there.")
    except BaseException:
        os.close(dfd)
        raise
    return dfd


def survey_run(iface, band, seconds, emit, pcap=False):
    import signal
    if os.geteuid() != 0:
        raise OSError("Surveying needs root: run it as 'sudo aiov2_ctl --wifi-survey'.")
    monitor_check(iface)
    channels = list(SURVEY_BANDS[band])
    owner = _survey_owner()
    writer, survey = None, Survey()
    if pcap:
        home = owner.pw_dir if owner else os.path.expanduser("~")
        folder = os.path.join(home, "aiov2-captures")
        dfd = capture_folder_fd(folder, owner)
        try:
            writer = PcapWriter(time.strftime("survey-%Y%m%d-%H%M%S.pcap"), owner, dir_fd=dfd)
        finally:
            os.close(dfd)

    def stop(_sig, _frm):
        raise KeyboardInterrupt
    old = signal.signal(signal.SIGTERM, stop)
    sock = None
    try:
        sock = monitor_setup(iface, channels[0])
        iw = _tool("iw")
        # The channel the radio is really on, and since when. Time is credited to it only: a channel change
        # that fails leaves the radio where it was, so that time belongs to the old channel.
        tuned, tuned_at = channels[0], time.monotonic()
        index, hop_at, report_at = 0, time.monotonic() + SURVEY_DWELL, time.monotonic() + SURVEY_EVERY
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                raw = sock.recv(4096)
                survey.see(raw, time.time())
                if writer:
                    writer.write(raw, time.time())
            except socket.timeout:
                pass
            now = time.monotonic()
            if now >= hop_at and len(channels) > 1:
                survey.listened(tuned, now - tuned_at)
                tuned_at = now
                for _ in range(len(channels)):
                    index = (index + 1) % len(channels)
                    try:
                        _monitor_cmd(iw, "dev", iface, "set", "channel", str(channels[index]))
                        tuned, tuned_at = channels[index], time.monotonic()
                        break
                    except subprocess.CalledProcessError:
                        channels.pop(index)         # this radio or region cannot use it
                        if not channels:
                            raise OSError("The radio accepted none of the channels.")
                        index %= len(channels)
                hop_at = now + SURVEY_DWELL
            if now >= report_at:
                now = time.monotonic()
                survey.listened(tuned, now - tuned_at)
                tuned_at = now
                emit(survey.snapshot(time.time(), channels[index] if channels else None))
                report_at = now + SURVEY_EVERY
    except KeyboardInterrupt:
        pass
    except subprocess.CalledProcessError as exc:
        raise OSError(f"Could not set {iface} up for surveying: {(exc.stderr or '').strip() or exc}")
    finally:
        if sock:
            sock.close()
        if writer:
            writer.close()
        monitor_restore(iface)
        signal.signal(signal.SIGTERM, old)


def survey_command(args):
    usage = (
        "Usage: aiov2_ctl --wifi-survey [--iface wlan1] [--band 2g|5g|all] [--seconds 3600] [--pcap] [--json]   (passive, needs root)\n"
        "Hops between channels and lists the access points, stations, probe requests, deauthentication floods and\n"
        "channel use it hears. --pcap also saves a capture (data frames header only, no EAPOL or authentication bodies) under ~/aiov2-captures.\n"
        "Never transmits; the radio is returned to normal when it stops."
    )
    args = list(args)
    as_json, pcap = "--json" in args, "--pcap" in args
    args = [a for a in args if a not in ("--json", "--pcap")]
    opts = {"--iface": MONITOR_IFACE, "--band": "all", "--seconds": str(MONITOR_SECONDS)}
    while args:
        if args[0] not in opts or len(args) < 2:
            print(usage)
            return 1
        opts[args[0]] = args[1]
        del args[:2]
    try:
        seconds = float(opts["--seconds"])
    except ValueError:
        print(usage)
        return 1
    if opts["--band"] not in SURVEY_BANDS or not 1 <= seconds <= MONITOR_SECONDS or not _IFACE_RE.match(opts["--iface"]):
        print(usage)
        return 1

    def emit(snap):
        if as_json:
            print(json.dumps(snap), flush=True)
        else:
            print(f"ch {snap['channel']}: {len(snap['aps'])} access points, {len(snap['stations'])} stations, "
                  f"{len(snap['probes'])} probing, {len(snap['deauth'])} deauth floods", flush=True)
    try:
        survey_run(opts["--iface"], opts["--band"], seconds, emit, pcap)
        return 0
    except OSError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


# ---- Passive Bluetooth scan ------------------------------------------------------------------------------------
# Lists the Bluetooth Low Energy devices advertising nearby. The controller is put into PASSIVE scanning: it only
# receives the advertisements devices broadcast anyway. It never sends a scan request, never connects, never pairs
# and never advertises. Needs root for the raw HCI socket; scanning is always switched off again when it stops.
BT_ADAPTER = "hci1"                      # the internal USB card's Bluetooth radio
BT_SECONDS = 600
_BT_ADAPTER_RE = re.compile(r"^hci[0-9]$")
BT_OP_SCAN_PARAMS, BT_OP_SCAN_ENABLE = 0x200B, 0x200C                # legacy LE scanning
BT_OP_EXT_SCAN_PARAMS, BT_OP_EXT_SCAN_ENABLE = 0x2041, 0x2042        # extended (Bluetooth 5) LE scanning
BT_SCAN_PASSIVE = 0x00                   # the scan type that sends nothing. 0x01 (active) is never used here.
BT_INTERVAL, BT_WINDOW = 0x0060, 0x0030  # listen 30 ms in every 60: the card shares its antenna time with Wi-Fi
BT_STATUS_DISALLOWED = 0x0C
BT_KEEP_BYTES = 27                       # at most this much of manufacturer or service data is kept, as hex

# Company identifiers from the Bluetooth SIG's assigned numbers: only well known ones, the rest show as a number.
BT_COMPANIES = {
    0x0002: "Intel", 0x0003: "IBM", 0x0006: "Microsoft", 0x000A: "Qualcomm (CSR)", 0x000D: "Texas Instruments",
    0x000F: "Broadcom", 0x001D: "Qualcomm", 0x0030: "STMicroelectronics", 0x0046: "MediaTek", 0x004C: "Apple",
    0x0059: "Nordic Semiconductor", 0x0075: "Samsung", 0x0087: "Garmin", 0x009E: "Bose", 0x00C4: "LG Electronics",
    0x00E0: "Google", 0x012D: "Sony", 0x0131: "Cypress", 0x0157: "Huami", 0x0171: "Amazon", 0x02E5: "Espressif",
    0x038F: "Xiaomi", 0x0499: "Ruuvi",
}
# 16 bit service numbers a device may list, again only well known ones.
BT_SERVICES = {
    0x1802: "immediate alert", 0x1803: "link loss", 0x1805: "current time", 0x1809: "thermometer",
    0x180A: "device information", 0x180D: "heart rate", 0x180F: "battery", 0x1810: "blood pressure",
    0x1812: "keyboard or mouse", 0x1816: "cycling speed", 0x1818: "cycling power", 0x181A: "environment sensor",
    0x181D: "weight scale", 0x1826: "fitness machine", 0xFD6F: "exposure notification", 0xFE2C: "Google Fast Pair",
    0xFEAA: "Eddystone beacon", 0xFEED: "Tile tracker",
}


def bt_cmd(opcode, params=b""):
    """One HCI command packet."""
    return struct.pack("<BHB", 0x01, opcode, len(params)) + params


def bt_scan_commands(extended, enable):
    """The (parameters, enable) command packets for passive scanning, or just the one that switches it off."""
    if extended:
        on = bt_cmd(BT_OP_EXT_SCAN_ENABLE, struct.pack("<BBHH", 1 if enable else 0, 0, 0, 0))
        params = bt_cmd(BT_OP_EXT_SCAN_PARAMS,
                        struct.pack("<BBB", 0x00, 0x00, 0x01) + struct.pack("<BHH", BT_SCAN_PASSIVE, BT_INTERVAL, BT_WINDOW))
    else:
        on = bt_cmd(BT_OP_SCAN_ENABLE, struct.pack("<BB", 1 if enable else 0, 0))
        params = bt_cmd(BT_OP_SCAN_PARAMS, struct.pack("<BHHBB", BT_SCAN_PASSIVE, BT_INTERVAL, BT_WINDOW, 0x00, 0x00))
    return [params, on] if enable else [on]


def bt_addr(raw):
    """A Bluetooth address as text. On the wire it is sent last byte first."""
    return ":".join(f"{b:02x}" for b in reversed(raw))


def bt_addr_kind(addr_type, addr):
    """What sort of address this is. Most phones and wearables use a private address that changes every few
    minutes, so the same device shows up again under a new one; only a public address names its maker."""
    if addr_type in (0x00, 0x02):
        return "public"
    top = int(addr[:2], 16) >> 6
    return {0b11: "random (fixed)", 0b01: "private (changes)", 0b00: "private (changes)"}.get(top, "random")


def bt_parse_ad(data):
    """The fields of one advertisement: {'name', 'company', 'mfr_data', 'services', 'service_data', 'tx_power',
    'flags'}; only what was sent. mfr_data and service_data keep the bytes that were already received (as hex,
    keyed by company or service number as 4 hex digits) so the app can recognise known kinds of device."""
    out, i = {}, 0
    while i + 1 < len(data):
        length = data[i]
        if length == 0 or i + 1 + length > len(data):
            break
        kind, body = data[i + 1], data[i + 2:i + 1 + length]
        if kind in (0x08, 0x09):                                          # shortened or complete local name
            name = body.decode("utf-8", "replace").strip("\x00").strip()
            if name and (kind == 0x09 or "name" not in out):
                out["name"] = "".join(c for c in name if c.isprintable())[:40]
        elif kind == 0xFF and len(body) >= 2:                             # manufacturer data: who made it
            out["company"] = struct.unpack_from("<H", body)[0]
            out.setdefault("mfr_data", {})[f"{out['company']:04x}"] = body[2:2 + BT_KEEP_BYTES].hex()
        elif kind in (0x02, 0x03):                                        # 16 bit service numbers
            out.setdefault("services", [])
            out["services"] += [struct.unpack_from("<H", body, j)[0] for j in range(0, len(body) - 1, 2)]
        elif kind == 0x16 and len(body) >= 2:                             # service data starts with its service
            number = struct.unpack_from("<H", body)[0]
            out.setdefault("services", []).append(number)
            out.setdefault("service_data", {})[f"{number:04x}"] = body[2:2 + BT_KEEP_BYTES].hex()
        elif kind == 0x0A and len(body) == 1:
            out["tx_power"] = struct.unpack("<b", body)[0]
        elif kind == 0x01 and body:
            out["flags"] = body[0]
        i += 1 + length
    if "services" in out:
        out["services"] = sorted(set(out["services"]))
    return out


def _bt_rssi(value):
    return value if -127 <= value <= 20 else None                        # 127 means "no reading"


def bt_legacy_reports(params):
    """The reports in an LE Advertising Report event (everything after its subevent byte)."""
    out, i = [], 1
    for _ in range(params[0] if params else 0):
        if i + 9 > len(params):
            break
        event_type, addr_type, raw = params[i], params[i + 1], params[i + 2:i + 8]
        n = params[i + 8]
        if i + 9 + n + 1 > len(params):
            break
        data = params[i + 9:i + 9 + n]
        rssi = struct.unpack_from("<b", params, i + 9 + n)[0]
        addr = bt_addr(raw)
        out.append({"addr": addr, "kind": bt_addr_kind(addr_type, addr), "connectable": event_type in (0x00, 0x01),
                    "rssi": _bt_rssi(rssi), **bt_parse_ad(data)})
        i += 10 + n
    return out


def bt_ext_reports(params):
    """The reports in an LE Extended Advertising Report event (everything after its subevent byte)."""
    out, i = [], 1
    for _ in range(params[0] if params else 0):
        if i + 24 > len(params):
            break
        event_type = struct.unpack_from("<H", params, i)[0]
        addr_type, raw = params[i + 2], params[i + 3:i + 9]
        rssi = struct.unpack_from("<b", params, i + 13)[0]
        n = params[i + 23]
        if i + 24 + n > len(params):
            break
        data = params[i + 24:i + 24 + n]
        addr = bt_addr(raw)
        out.append({"addr": addr, "kind": bt_addr_kind(addr_type, addr), "connectable": bool(event_type & 0x01),
                    "rssi": _bt_rssi(rssi), **bt_parse_ad(data)})
        i += 24 + n
    return out


def bt_event(packet):
    """What one packet from the controller says: ('reports', [...]), ('done', opcode, status) or None."""
    if len(packet) < 3 or packet[0] != 0x04:
        return None
    code, body = packet[1], packet[3:3 + packet[2]]
    if code == 0x3E and body:                                             # LE meta event
        if body[0] == 0x02:
            return ("reports", bt_legacy_reports(body[1:]))
        if body[0] == 0x0D:
            return ("reports", bt_ext_reports(body[1:]))
    elif code == 0x0E and len(body) >= 4:                                 # command complete
        return ("done", struct.unpack_from("<H", body, 1)[0], body[3])
    elif code == 0x0F and len(body) >= 4:                                 # command status
        return ("done", struct.unpack_from("<H", body, 2)[0], body[0])
    return None


class BtScan:
    """Every device heard so far, by address."""

    def __init__(self, vendor=None):
        self.devices = {}
        self.vendor = vendor or (lambda mac: "")

    def add(self, report, now):
        dev = self.devices.setdefault(report["addr"], {
            "addr": report["addr"], "kind": report["kind"], "name": "", "maker": "", "services": [],
            "mfr_data": {}, "service_uuids": [], "service_data": {}, "connectable": False, "rssi": None, "best": None, "count": 0, "first": now, "last": now})
        dev["count"] += 1
        dev["last"] = now
        dev["connectable"] = dev["connectable"] or bool(report.get("connectable"))
        if report.get("name"):
            dev["name"] = report["name"]
        if report.get("rssi") is not None:
            dev["rssi"] = report["rssi"]
            dev["best"] = report["rssi"] if dev["best"] is None else max(dev["best"], report["rssi"])
        if report.get("company") is not None and not dev["maker"]:
            dev["maker"] = BT_COMPANIES.get(report["company"], f"company 0x{report['company']:04x}")
        if not dev["maker"] and dev["kind"] == "public":
            dev["maker"] = self.vendor(dev["addr"])
        for number in report.get("services", []):
            label = BT_SERVICES.get(number, f"0x{number:04x}")
            if label not in dev["services"]:
                dev["services"].append(label)
            if f"{number:04x}" not in dev["service_uuids"]:
                dev["service_uuids"].append(f"{number:04x}")
        dev["mfr_data"].update(report.get("mfr_data") or {})            # the latest bytes each company sent
        dev["service_data"].update(report.get("service_data") or {})
        return dev

    def snapshot(self, now, adapter, mode):
        rows = sorted(self.devices.values(), key=lambda d: (-(d["rssi"] if d["rssi"] is not None else -999), d["addr"]))
        return {"t": round(now, 1), "adapter": adapter, "mode": mode, "devices": [dict(d, mfr_data=dict(d["mfr_data"]), service_uuids=list(d["service_uuids"]),
                                          service_data=dict(d["service_data"])) for d in rows]}


def _bt_wait(sock, opcode, timeout=2.0):
    """The status the controller gives for one command, feeding nothing else on. None if it never answers."""
    import select
    end = time.time() + timeout
    while time.time() < end:
        ready, _, _ = select.select([sock], [], [], max(0.0, end - time.time()))
        if not ready:
            break
        ev = bt_event(sock.recv(1024))
        if ev and ev[0] == "done" and ev[1] == opcode:
            return ev[2]
    return None


def _bt_send(sock, packet):
    sock.send(packet)
    return _bt_wait(sock, struct.unpack_from("<H", packet, 1)[0])


def bt_scan_run(adapter, seconds, emit):
    import select
    import signal
    import socket
    if os.geteuid() != 0:
        raise OSError("Bluetooth scanning needs root: run it as 'sudo aiov2_ctl --bt-scan'.")
    if not all(hasattr(socket, name) for name in ("AF_BLUETOOTH", "BTPROTO_HCI", "SOL_HCI", "HCI_FILTER")):
        raise OSError("This Python has no Bluetooth socket support.")
    if not os.path.isdir(f"/sys/class/bluetooth/{adapter}"):
        raise OSError(f"No Bluetooth adapter {adapter}. The internal card needs the USB rail on.")

    def stop(_sig, _frm):
        raise KeyboardInterrupt
    old = signal.signal(signal.SIGTERM, stop)
    sock, mode = None, None
    scan = BtScan(oui_vendor)
    try:
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
        sock.bind((int(adapter[3:]),))
        # every event packet, nothing else. The kernel wants the whole 16 byte filter, padding included.
        sock.setsockopt(socket.SOL_HCI, socket.HCI_FILTER, struct.pack("<IIIHH", 1 << 0x04, 0xFFFFFFFF, 0xFFFFFFFF, 0, 0))
        status = None
        for extended in (True, False):                                    # Bluetooth 5 cards want the extended form
            status = None
            for packet in bt_scan_commands(extended, True):
                status = _bt_send(sock, packet)
                if status != 0:
                    break
            if status == 0:
                mode = "extended" if extended else "legacy"
                break
        if mode is None:
            if status == BT_STATUS_DISALLOWED:
                raise OSError(f"{adapter} is busy: something else is scanning or connecting with it. Stop that and try again.")
            raise OSError(f"{adapter} would not start a passive scan" + (f" (status 0x{status:02x})." if status is not None
                                                                         else ": no answer. Is Bluetooth switched on?"))
        end, last = time.time() + seconds, 0.0
        while time.time() < end:
            ready, _, _ = select.select([sock], [], [], 0.5)
            now = time.time()
            if ready:
                ev = bt_event(sock.recv(2048))
                if ev and ev[0] == "reports":
                    for report in ev[1]:
                        scan.add(report, now)
            if now - last >= 2.0:
                last = now
                emit(scan.snapshot(now, adapter, mode))
        emit(scan.snapshot(time.time(), adapter, mode))
    except KeyboardInterrupt:
        pass
    finally:
        if sock is not None:
            if mode is not None:
                try:                                                      # always leave the radio not scanning
                    for packet in bt_scan_commands(mode == "extended", False):
                        _bt_send(sock, packet)
                except OSError:
                    pass
            sock.close()
        signal.signal(signal.SIGTERM, old)


def bt_command(args):
    usage = (
        "Usage: aiov2_ctl --bt-scan [--adapter hci1] [--seconds 30] [--json]   (passive, needs root)\n"
        "Lists the Bluetooth Low Energy devices advertising nearby: name, address, maker and signal strength.\n"
        "Passive scanning only: it listens to what devices broadcast and never sends a scan request, connects, pairs\n"
        "or advertises. Scanning is switched off again when it stops."
    )
    args = list(args)
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    opts = {"--adapter": BT_ADAPTER, "--seconds": "30"}
    while args:
        if args[0] not in opts or len(args) < 2:
            print(usage)
            return 1
        opts[args[0]] = args[1]
        del args[:2]
    try:
        seconds = float(opts["--seconds"])
    except ValueError:
        print(usage)
        return 1
    if not _BT_ADAPTER_RE.match(opts["--adapter"]) or not 1 <= seconds <= BT_SECONDS:
        print(usage)
        return 1
    final = {}

    def emit(snap):
        final.clear()
        final.update(snap)
        if as_json:
            print(json.dumps(snap), flush=True)
    try:
        bt_scan_run(opts["--adapter"], seconds, emit)
    except OSError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if not as_json:
        devices = final.get("devices", [])
        print(f"{len(devices)} Bluetooth devices heard on {opts['--adapter']} ({final.get('mode', 'no')} passive scan):")
        for d in devices:
            signal_text = f"{d['rssi']} dBm" if d["rssi"] is not None else "-"
            print(f"  {d['addr']}  {signal_text:>8}  {d['kind']:<17}  {d['name'] or '-'}"
                  + (f"  [{d['maker']}]" if d["maker"] else "") + (f"  {', '.join(d['services'])}" if d["services"] else ""))
    return 0


def ensure_system_packages(say=print):
    """Install the apt packages this program relies on, if they are missing. Never fatal."""
    missing = [pkg for pkg, path in SYSTEM_PACKAGES.items() if not os.path.exists(path)]
    if not missing:
        return True
    say(f"Installing required packages: {', '.join(missing)}\n")
    try:
        subprocess.check_call(apt_install_argv(missing), env=apt_quiet_env(), stdin=subprocess.DEVNULL)
        return True
    except (OSError, subprocess.CalledProcessError):
        say(f"Could not install {', '.join(missing)}. Run: sudo apt install {' '.join(missing)}\n")
        return False



# Resting voltage of a lithium-ion cell against how full it is. Rough by nature: good to ten percent or so.
CELL_CURVE = ((3.00, 0), (3.30, 2), (3.50, 6), (3.60, 12), (3.65, 20), (3.70, 30), (3.75, 40), (3.80, 50),
              (3.87, 60), (3.92, 70), (4.00, 80), (4.10, 90), (4.20, 100))
CELL_RESISTANCE = 0.07               # ohms, the pack and its wiring: how far the voltage sags per amp drawn
GAUGE_TOLERANCE = 30                 # percent the gauge may differ from the voltage's story before it is doubted


def percent_from_voltage(volts, amps=0.0, resistance=CELL_RESISTANCE):
    """About how full the cell is, from its voltage. `amps` is positive while charging and negative while
    discharging; the voltage is corrected for the sag (or lift) that current causes. None with no reading."""
    if not isinstance(volts, (int, float)) or volts <= 0:
        return None
    rest = volts - (amps or 0.0) * resistance
    if rest <= CELL_CURVE[0][0]:
        return 0
    for (v0, p0), (v1, p1) in zip(CELL_CURVE, CELL_CURVE[1:]):
        if rest <= v1:
            return int(round(p0 + (p1 - p0) * (rest - v0) / (v1 - v0)))
    return 100


FULL_GAUGE = 99                      # a gauge at or above this is saying "full"
FULL_CHARGE_AMPS = 0.3               # a battery that really is full takes less charge than this


def gauge_unbelievable(gauge, estimate, tolerance=GAUGE_TOLERANCE, amps=0.0):
    """True when the gauge's percentage cannot be right. Two tests. One: it is further from what the voltage
    says than a rough curve can explain. Two: it says full while the battery is still taking a real charging
    current (`amps`, positive while charging). The second is needed because a charger lifts the cell voltage:
    the stuck gauge on this board read 100% at a real 56%, and once on charge the voltage's story rose to
    within the first test's tolerance and the 100% was believed again."""
    if gauge is None or estimate is None:
        return False
    if abs(gauge - estimate) >= tolerance:
        return True
    return gauge >= FULL_GAUGE and isinstance(amps, (int, float)) and amps >= FULL_CHARGE_AMPS and estimate < gauge


def capacity_text(summary):
    """The percentage as --status prints it: '45%', or '~45% (estimated ...)' when it is not the gauge's."""
    cap = summary.get("capacity")
    if summary.get("capacity_source") == "voltage":
        return f"~{cap}% (estimated from the cell voltage)"
    return f"{cap}%"


def gauge_warning_text(summary):
    """What to say when the gauge cannot be believed, or '' when it can. Two different cases. When the
    percentage shown is already our estimate, the sentence is about the gauge being stuck. Otherwise it is the
    older case of a high percentage on a very low cell, which points at the battery itself."""
    if summary.get("capacity_source") == "voltage":
        return (f"the battery gauge says {summary.get('capacity_gauge')}%, which the cell voltage "
                f"({summary.get('voltage')} V) rules out. The percentage above is worked out from the voltage "
                "instead and is rough, good to about ten percent. The gauge needs calibrating: one full "
                "charge, a full discharge, and a full charge again.")
    if gauge_disagrees(summary):
        return (f"the gauge says {summary.get('capacity')}% but the battery is at {summary.get('voltage')} V. "
                "A full cell reads about 4.2 V, so one cell may be flat or not making contact. "
                "Check the battery before running on battery power.")
    return ""


def gauge_disagrees(summary):
    """True when the reported charge level cannot match the cell voltage."""
    if summary.get("capacity_source") == "voltage":
        return True
    cap, volts = summary.get("capacity"), summary.get("voltage")
    return cap is not None and volts is not None and cap >= 90 and volts < 3.3


def status_data():
    """Everything --status shows, as plain data, for the GUI and other tools."""
    rails = {}
    for name, pin in GPIO_MAP.items():
        state_bool, _source = GpioController.get_pin_state(pin)
        rails[name] = "on" if state_bool else "off"
    summary = Telemetry.power_summary()
    ignored, taken = Telemetry.last_discarded
    return {
        "rails": rails,
        "power": summary,
        "gauge_ignored": {"ignored": ignored, "taken": taken},
        "gauge_warning": bool(summary and gauge_disagrees(summary)),
        "companions": detect_companions(),
    }


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
    ignored, taken = Telemetry.last_discarded
    if not summary:
        if taken:
            print("Power: n/a (battery gauge readings were not believable. Check the battery contacts.)")
        else:
            print("Power: n/a")
        return

    print(f"Source    : {summary['source']}")
    print(f"Status    : {summary['status']}")
    print(f"Capacity  : {capacity_text(summary)}")
    print(f"Direction : {summary['direction']}")
    print(f"Mode      : {summary['mode']}")
    print(f"Voltage   : {summary['voltage']} V")
    print(f"Current   : {summary['current']} A")
    print(f"Power     : {summary['power']} W")
    warning = gauge_warning_text(summary)
    if warning:
        print(f"Warning   : {warning}")
    if ignored:
        print(f"Gauge     : {ignored} of {taken} readings ignored as implausible. "
              "If this keeps happening, check the battery contacts.")

# ==============================
# Sampling / Measurement
# ==============================
# ==============================
# Doctor: read only health checks for the uConsole
# ==============================
# Everything here only reads. Each check takes plain text in and returns a list
# of (level, message), where level is "ok", "info" or "warn", so it can be
# tested without a uConsole. Sources are in the README.
DOCTOR_CONFIG_PATH = "/boot/firmware/config.txt"
KERNEL_PACKAGES = ("clockworkpi-kernel", "akrex-kernel")
EEPROM_MIN_DATE = "2025-01-06"   # Rex's Trixie post: CM5 Lite needs newer than this


def _doctor_cmd(args, timeout=10):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def _config_lines(text):
    return [l.strip() for l in (text or "").splitlines()
            if l.strip() and not l.strip().startswith("#")]


def check_rtc(config_text, rtc_names, dmesg_text):
    lines = _config_lines(config_text)
    rtc_off = "dtparam=rtc=off" in lines
    overlay = any(l.startswith("dtoverlay=i2c-rtc") and "pcf85063" in l for l in lines)
    out = []
    if rtc_off and not overlay:
        out.append(("warn", "RTC: dtparam=rtc=off is set but there is no i2c-rtc,pcf85063a overlay, so there is no clock at all. "
                            "Add: dtoverlay=i2c-rtc,pcf85063a,i2c_csi_dsi0 under [pi5]."))
    elif overlay:
        out.append(("ok", "RTC: AIO clock overlay is in config.txt."))
    if overlay and not any("pcf85063" in n for n in rtc_names):
        out.append(("warn", "RTC: the overlay is set but no pcf85063 clock registered. Check the board is seated."))
    if dmesg_text and "POR issue detected" in dmesg_text:
        out.append(("info", "RTC: the clock reported a power on reset at boot. If the time is wrong after power off, "
                            "check the CR1220 battery is fitted, tight and not flat, then set it once with: "
                            "sudo hwclock --systohc"))
    if dmesg_text and "hctosys: unable to read" in dmesg_text:
        out.append(("info", "RTC: the system could not read the clock at boot. It is harmless once the clock is set."))
    return out


def check_kernel(installed, sim_upgrade_text):
    out = []
    if any(p in installed for p in KERNEL_PACKAGES):
        out.append(("ok", "Kernel: uConsole kernel package is installed (" +
                    ", ".join(p for p in KERNEL_PACKAGES if p in installed) + ")."))
    else:
        out.append(("warn", "Kernel: no clockworkpi-kernel or akrex-kernel package found. If you upgraded recently, "
                            "try: sudo apt install clockworkpi-cm-firmware akrex-kernel"))
    removing = []
    for line in (sim_upgrade_text or "").splitlines():
        if line.startswith("Remv") and re.search(r"kernel|linux-image|clockworkpi|firmware", line):
            removing.append(line.split()[1])
    if removing:
        out.append(("warn", "Upgrade: a full-upgrade would REMOVE " + ", ".join(removing) +
                            ". Do not upgrade until you know why."))
    elif sim_upgrade_text is not None:
        out.append(("ok", "Upgrade: a full-upgrade would not remove any kernel or firmware package."))
    return out


def check_eeprom(version_text):
    m = re.search(r"(\d{4})/(\d{2})/(\d{2})|(\d{4}-\d{2}-\d{2})", version_text or "")
    if not m:
        return [("info", "EEPROM: could not read the bootloader version.")]
    date = m.group(4) or f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    if date <= EEPROM_MIN_DATE:
        return [("warn", f"EEPROM: bootloader is dated {date}. A CM5 Lite should be newer than {EEPROM_MIN_DATE}. "
                         "Check with: sudo rpi-eeprom-update")]
    return [("ok", f"EEPROM: bootloader is dated {date}.")]


def check_config(config_text):
    lines = _config_lines(config_text)
    out = []
    if "dtparam=spi=on" in lines:
        out.append(("warn", "config.txt: dtparam=spi=on disables the backlight on the CM5. Use dtoverlay=spi1-1cs instead."))
    if any(l.startswith("disable_audio_dither") for l in lines):
        out.append(("info", "config.txt: disable_audio_dither is not recommended on the uConsole."))
    if any(l.startswith("gpio=11=op") for l in lines):
        out.append(("info", "config.txt: gpio=11=op,dh is for the CM3 only."))
    if not out:
        out.append(("ok", "config.txt: no known problem settings."))
    return out


def check_alsa(version):
    if version and version.startswith("1.2.14-1") and not version.startswith("1.2.14-2"):
        return [("info", f"Audio: alsa-utils {version} has a known udev rule typo (Debian bug 1093057). "
                         "It only prints a warning at boot.")]
    return []


def check_battery(summary, throttled):
    out = []
    if summary:
        if gauge_disagrees(summary):
            out.append(("warn", f"Battery: gauge says {summary['capacity']}% but the cell is at {summary['voltage']} V. "
                                "One cell may be flat or not making contact."))
        else:
            out.append(("ok", f"Battery: {summary['capacity']}% at {summary['voltage']} V."))
    else:
        out.append(("warn", "Battery: the gauge gave no believable reading. Check the battery contacts."))
    if throttled is not None and throttled != 0:
        out.append(("warn", f"Power: the Pi reports under voltage or throttling (0x{throttled:x}). "
                            "Check the charger, cable and battery."))
    elif throttled == 0:
        out.append(("ok", "Power: no undervoltage or throttling since boot."))
    return out


def check_boot_rails(system_cfg, user_cfg):
    """
    The boot service only reads the system config. A rails_on_boot entry in the
    user config (left by a --boot-rail run without sudo) is ignored at boot, so
    say when the two disagree. Reports only, never edits either file.
    """
    if not isinstance(user_cfg, dict) or "rails_on_boot" not in user_cfg:
        return [("ok", "Boot rails: no stray boot settings in your user config.")]
    system = _rails_on_boot_from_config(system_cfg or {})
    user = _rails_on_boot_from_config(user_cfg)
    differ = [f for f in GPIO_MAP if system.get(f) != user.get(f)]
    if not differ:
        return [("ok", "Boot rails: user and system boot settings agree.")]
    wants = ", ".join(f"{f} {'on' if user[f] else 'off'}" for f in differ)
    has = ", ".join(f"{f} {'on' if system[f] else 'off'}" for f in differ)
    return [("info", f"Boot rails: your user config ({USER_CONFIG_PATH}) says {wants}, but the system config "
                     f"({CONFIG_PATH}) says {has}. The boot service only reads the system config, so the user "
                     "entry is ignored. Boot states are set with: sudo aiov2_ctl --boot-rail <RAIL> on|off")]


def check_storage(root_options, dmesg_text):
    out = []
    if root_options and "ro" in root_options.split(","):
        out.append(("warn", "Storage: the root filesystem is read only. The system may have found errors."))
    errors = [l for l in (dmesg_text or "").splitlines()
              if re.search(r"EXT4-fs.*(error|warning)|mmc\d.*(error|timeout)|I/O error", l, re.I)]
    if errors:
        out.append(("warn", f"Storage: {len(errors)} filesystem or card error line(s) in this boot's kernel log. "
                            "Back up, then run fsck from another machine. Latest: " + errors[-1].strip()))
    if not out:
        out.append(("ok", "Storage: root is read write with no filesystem errors this boot."))
    return out


def check_mesh(known, active, rail, spi_node):
    """
    The LoRa radio needs two things: the LORA rail on and meshtasticd running.
    known: meshtasticd has a unit file (None or False means it is not set up,
    so say nothing). active: the service is running. rail: 'on', 'off' or None.
    """
    if not known or rail is None:
        return []
    if rail == "on" and active:
        if not spi_node:
            return [("warn", "Mesh: LORA rail is on and meshtasticd is running, but /dev/spidev1.0 "
                             "is missing. Check 'dtoverlay=spi1-1cs' in /boot/firmware/config.txt.")]
        return [("ok", "Mesh: LORA rail on and meshtasticd running.")]
    if rail == "on":
        return [("warn", "Mesh: LORA rail is on but meshtasticd is not running. "
                         "Start it with: sudo systemctl start meshtasticd")]
    if active:
        return [("warn", "Mesh: meshtasticd is running but the LORA rail is off, so the radio has no power. "
                         "Turn it on with: aiov2_ctl LORA on")]
    return [("info", "Mesh: idle (LORA rail off, meshtasticd stopped). Turn the rail on, then "
                     "start meshtasticd, to use it.")]


def parse_iw_modes(text):
    """The interface modes listed under 'Supported interface modes:' in 'iw phy' output, as a list of lower case
    names (for example ['managed', 'monitor']), or None when the output has no such list."""
    modes, inside = None, False
    for line in (text or "").splitlines():
        if re.match(r"^\s*Supported interface modes:", line):
            modes, inside = [], True
            continue
        if inside:
            m = re.match(r"^\s*\*\s*(.+?)\s*$", line)
            if not m:
                inside = False
                continue
            modes.append(m.group(1).lower())
    return modes


def parse_iw_link_freq(text):
    """The frequency in MHz from 'iw dev <iface> link' when connected, or None when not connected."""
    if not text or "Connected to" not in text:
        return None
    m = re.search(r"^\s*freq:\s*(\d+)", text, re.M)
    return int(float(m.group(1))) if m else 0


def check_wifi_monitor(iface, driver, kernel, iw_found, phy_text, link_text):
    """
    What can be told about listening (monitor mode) on the internal card without changing anything.
    iface: its interface name, or None when the card is not present (it is on a switchable power rail and is
    often off). phy_text: 'iw phy <phy> info' output, link_text: 'iw dev <iface> link' output. Advertising
    monitor mode does not prove it works on this kernel; only a real listen shows that.
    """
    if not iface:
        return [("info", "Wi-Fi listening: the internal card (USB 0e8d:7961) is not present, so there is nothing "
                         "to check. It is on a switchable power rail and is often off.")]
    about = f"{iface}, driver {driver or 'unknown'}, kernel {kernel or 'unknown'}"
    if not iw_found:
        return [("info", f"Wi-Fi listening: {about}. 'iw' is not installed, so monitor mode support cannot be read. "
                         "Install it with: sudo apt install iw")]
    modes = parse_iw_modes(phy_text)
    out = []
    if modes is None:
        out.append(("info", f"Wi-Fi listening: {about}. Could not read the card's supported modes from 'iw phy'."))
    elif "monitor" in modes:
        out.append(("ok", f"Wi-Fi listening: {about}. The card advertises monitor mode. That it works on this "
                          "kernel is only shown by a real listen (Survey or Sense)."))
    else:
        out.append(("warn", f"Wi-Fi listening: {about}. The card does not advertise monitor mode, so Survey and "
                            "Sense cannot listen with it."))
    freq = parse_iw_link_freq(link_text)
    if freq is not None:
        where = f" on channel {freq_to_channel(freq)} ({freq} MHz)" if freq and freq_to_channel(freq) else ""
        out.append(("info", f"Wi-Fi listening: {iface} is connected to a network{where}. While it is connected the "
                            "card stays on that network's channel, so listening follows that channel; Survey and "
                            "Sense ask for it to be disconnected first."))
    return out


def _doctor_wifi_monitor():
    """Gather check_wifi_monitor's inputs. Reads only: sysfs, 'iw dev', 'iw phy ... info' and 'iw dev ... link'."""
    iface, driver = None, ""
    for radio in detect_wifi_radios():
        if radio["kind"] == "internal-usb":
            iface, driver = radio["name"], radio["driver"]
            break
    if not iface:
        return check_wifi_monitor(None, "", "", False, None, None)
    iw = _tool("iw")
    phy_text = link_text = None
    if iw:
        phy = (read_sysfs(os.path.join(SYS_NET, iface, "phy80211", "name")) or "").strip()
        if re.match(r"^[A-Za-z0-9_.-]{1,32}$", phy):
            phy_text = _doctor_cmd([iw, "phy", phy, "info"])
        link_text = _doctor_cmd([iw, "dev", iface, "link"])
    return check_wifi_monitor(iface, driver, os.uname().release, bool(iw), phy_text, link_text)


def doctor_command():
    config = None
    try:
        with open(DOCTOR_CONFIG_PATH) as f:
            config = f.read()
    except OSError:
        pass
    dmesg = _doctor_cmd(["dmesg"])
    rtc_names = []
    try:
        for d in sorted(os.listdir("/sys/class/rtc")):
            rtc_names.append(read_sysfs(f"/sys/class/rtc/{d}/name") or "")
    except OSError:
        pass
    pkgs = _doctor_cmd(["dpkg-query", "-W", "-f=${Package}\\n", *KERNEL_PACKAGES]) or ""
    sim = _doctor_cmd(["apt-get", "-s", "full-upgrade"], timeout=60)
    alsa = (_doctor_cmd(["dpkg-query", "-W", "-f=${Version}", "alsa-utils"]) or "").strip()
    throttled = None
    m = re.search(r"0x([0-9a-fA-F]+)", _doctor_cmd(["vcgencmd", "get_throttled"]) or "")
    if m:
        throttled = int(m.group(1), 16)
    root_opts = (_doctor_cmd(["findmnt", "-no", "OPTIONS", "/"]) or "").strip()

    results = []
    results += check_rtc(config, rtc_names, dmesg)
    results += check_kernel(set(pkgs.split()), sim)
    results += check_eeprom(_doctor_cmd(["vcgencmd", "bootloader_version"]))
    results += check_config(config)
    results += check_alsa(alsa)
    results += check_battery(Telemetry.power_summary(), throttled)
    try:
        with open(USER_CONFIG_PATH) as f:
            user_cfg = json.load(f)
    except (OSError, ValueError):
        user_cfg = None
    results += check_boot_rails(load_system_config(), user_cfg)
    results += check_storage(root_opts, dmesg)
    units = _doctor_cmd(["systemctl", "list-unit-files", "meshtasticd.service"]) or ""
    state = (_doctor_cmd(["systemctl", "is-active", "meshtasticd"]) or "").strip()
    lora_pin = GPIO_MAP.get("LORA")
    try:
        lora_level = GpioController.get_gpio(lora_pin) if lora_pin is not None else None
    except Exception:
        lora_level = None
    lora_rail = None if lora_level is None else ("on" if lora_level else "off")
    try:
        with open(autostart_path()) as f:
            autostart_text = f.read()
    except OSError:
        autostart_text = None
    tray_up = bool((_doctor_cmd(["pgrep", "-f", "aiov2_ctl --gui"]) or "").strip())
    results += check_tray_autostart(autostart_text, tray_up)
    results += check_mesh("meshtasticd.service" in units, state == "active", lora_rail,
                          os.path.exists("/dev/spidev1.0"))
    results += _doctor_wifi_monitor()

    print("aiov2_ctl doctor (read only)")
    print("----------------------------")
    tags = {"ok": "OK  ", "info": "INFO", "warn": "WARN"}
    for level, msg in results:
        print(f"{tags[level]}  {msg}")
    warns = sum(1 for l, _ in results if l == "warn")
    print("----------------------------")
    print("Nothing to worry about." if not warns else f"{warns} thing(s) worth a look.")
    return 0


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

    def open_ops(kind, path):
        result = ops_open(kind, path)
        if result == "already":
            tray.showMessage("uConsole Ops", "It is already open.", QSystemTrayIcon.MessageIcon.Information, 2500)
        elif result == "failed":
            tray.showMessage("uConsole Ops", "Could not open it.", QSystemTrayIcon.MessageIcon.Warning, 4000)

    ops_actions = []
    for kind, path, label in ops_shortcuts():
        a = QAction(label)
        a.triggered.connect(lambda _checked=False, kind=kind, path=path: open_ops(kind, path))
        menu.addAction(a)
        ops_actions.append(a)
    if ops_actions:
        menu.addSeparator()


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
    mesh_start_action.triggered.connect(lambda _=False: (GpioController._run_service("start", "meshtasticd.service"), service_changed("meshtasticd.service")))
    mesh_stop_action.triggered.connect(lambda _=False: (GpioController._run_service("stop", "meshtasticd.service"), service_changed("meshtasticd.service")))
    mesh_restart_action.triggered.connect(lambda _=False: (GpioController._run_service("restart", "meshtasticd.service"), service_changed("meshtasticd.service")))

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
    readsb_start_action.triggered.connect(lambda _=False: (GpioController._run_service("start", "readsb.service"), service_changed("readsb.service")))
    readsb_stop_action.triggered.connect(lambda _=False: (GpioController._run_service("stop", "readsb.service"), service_changed("readsb.service")))
    readsb_restart_action.triggered.connect(lambda _=False: (GpioController._run_service("restart", "readsb.service"), service_changed("readsb.service")))

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
    menu.addAction("Add or remove features…", lambda: subprocess.Popen(
        [INSTALLED_PATH, "--installer"], start_new_session=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    menu.addAction(update_action)

    menu.addSeparator()
    menu.addAction("Quit", app.quit)
    menu_active = [False]
    menu_opened_at = [None]
    def on_menu_show():
        menu_active[0] = True
        menu_opened_at[0] = time.monotonic()
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

    # What systemd last said about each service: name -> (when, installed,
    # active). refresh() runs every second while the menu or window is
    # open; the answers are reused until service_check_due says ask again.
    service_seen = {}

    def service_state(name, rail_on, visible, forced):
        seen = service_seen.get(name)
        last_asked = seen[0] if seen else None
        if seen is None or service_check_due(rail_on, visible, last_asked,
                                             time.monotonic(), forced):
            installed = GpioController._service_installed(name)
            active = bool(installed and GpioController._service_active(name))
            seen = (time.monotonic(), installed, active)
            service_seen[name] = seen
        return seen[1], seen[2]

    def service_changed(name):
        # The user just started or stopped it: forget the old answer.
        service_seen.pop(name, None)
        refresh()

    def refresh(force=False):
        visible = window.isVisible() or menu_counts_as_open(menu_active[0], menu_opened_at[0], time.monotonic())
        if not force and not visible:
            return
        # one reading of the board, shared with the command line and the ops app
        status = shared_status(0 if force else STATUS_SHARE_SECONDS)
        summary = status.get("power")
        rails_on_boot = get_rails_on_boot_config(system_only=True)
        if summary:
            power_label.setText(
                f"{summary['mode']} | {summary['power']} W | {summary['capacity']}%"
            )
            power_action.setText(f"Power: {summary['power']} W")
        else:
            power_label.setText("Power: n/a")
            power_action.setText("Power: n/a")

        companion_action.setText(companions_summary_line(status.get("companions") or []))

        rail_on = {}
        for f, p in GPIO_MAP.items():
            state = (status.get("rails") or {}).get(f) == "on"
            rail_on[f] = bool(state)

            checkboxes[f].blockSignals(True)
            checkboxes[f].setChecked(state)
            checkboxes[f].blockSignals(False)

            actions[f].blockSignals(True)
            actions[f].setChecked(state)
            actions[f].blockSignals(False)

            boot_actions[f].blockSignals(True)
            boot_actions[f].setChecked(bool(rails_on_boot.get(f, False)))
            boot_actions[f].blockSignals(False)

        mesh_installed, mesh_active = service_state(
            "meshtasticd.service", rail_on.get("LORA", False), visible, force)
        if not mesh_installed:
            mesh_state = "Not installed"
            mesh_start_action.setVisible(False)
            mesh_stop_action.setVisible(False)
            mesh_restart_action.setVisible(False)
        else:
            mesh_state = "Running" if mesh_active else "Stopped"
            mesh_start_action.setVisible(True)
            mesh_stop_action.setVisible(True)
            mesh_restart_action.setVisible(True)
            mesh_start_action.setEnabled(not mesh_active)
            mesh_stop_action.setEnabled(mesh_active)

        mesh_status_action.setText(f"    Status: {mesh_state}")

        readsb_installed, readsb_active = service_state(
            "readsb.service", rail_on.get("SDR", False), visible, force)
        if not readsb_installed:
            readsb_state = "Not installed"
            readsb_start_action.setVisible(False)
            readsb_stop_action.setVisible(False)
            readsb_restart_action.setVisible(False)
        else:
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
        elif update_is_unfinished():
            # An older updater put this version in place without running its installer.
            update_action.setText("Finish the update → Install")
            update_action.setEnabled(True)
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

    def finish_prompt():
        # An older updater put this version in place without running its installer: offer to finish, once.
        stamp = file_stamp(INSTALLED_PATH)
        try:
            with open(FINISH_ASKED_PATH) as f:
                asked = f.read()
        except OSError:
            asked = ""
        if not finish_prompt_due(update_is_unfinished(), stamp, asked):
            return
        try:
            os.makedirs(os.path.dirname(FINISH_ASKED_PATH), exist_ok=True)
            with open(FINISH_ASKED_PATH, "w") as f:
                f.write(stamp)
        except OSError:
            pass
        from PyQt6.QtWidgets import QMessageBox
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("AIO v2: finish the update")
        box.setText(FINISH_PROMPT)
        now = box.addButton("Finish now", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is now:
            subprocess.Popen([INSTALLED_PATH, "--installer"], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    QTimer.singleShot(5000, finish_prompt)
    
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


# ==============================
# One installer
# ==============================
# Everything that can be installed is a row in FEATURES: plain data, so the command line here, and later a picker
# and a graphical installer, all read the same list. install_plan() turns a selection into steps (also plain
# data) without touching the machine; run_install() carries the steps out. Rules the plan keeps:
#   - no step switches a rail on or starts a radio;
#   - nothing is made to start at boot, except this tool's own two housekeeping units that install has always
#     set up (OWN_BOOT_UNITS) and the tray when 'autostart' is picked. A service that a package brings with it
#     is switched back off if this install is what added it;
#   - root does only what needs root: git and python in the user's home run as that user.

# The ops app and the ops console are part of this repository: ops_gui/, ops_agent/ and ops.py, beside this
# file. They run, as the user, from the same checkout this tool is installed from. They used to be a repository
# of their own, cloned into a folder in the user's home; that folder is only recognised now, so that an install
# or an uninstall can clear it away.
APP_FILES = (os.path.join("ops_gui", "__main__.py"), "ops.py")
OPS_REPO_NAME = "AceMedia/uConsole-AIO-OPS"
OPS_CHECKOUT_NAME = "ops-agentic"
FLIPPER_USB_ID = ("0483", "5740")
RTLSDR_USB_IDS = (("0bda", "2832"), ("0bda", "2838"))
ASSET_BASE = "/usr/local/share/aiov2_ctl"
COMPLETION_PATH = "/etc/bash_completion.d/aiov2_ctl"
SYSTEM_DESKTOP_PATH = "/usr/share/applications/aiov2_ctl.desktop"
INSTALLER_DESKTOP_PATH = "/usr/share/applications/aiov2_ctl-installer.desktop"
AIO_BOARD_PACKAGE_DOC = "/usr/share/doc/hackergadgets-uconsole-aio-board"
# The only units an install step may enable at boot. Both are this tool's own and both were enabled by install
# before features existed: one sets each rail to the state the owner saved (all off unless they chose otherwise),
# the other checks once a minute whether a running readsb has lost its tuner. Neither switches on a rail that
# was off.
OWN_BOOT_UNITS = (RAILS_BOOT_SERVICE, SDR_WATCHDOG_TIMER)

FEATURES = (
    {"key": "core", "title": "Controller and command line",
     "summary": "The aiov2_ctl command, rail control, bash completion and the rules that let it work without a password.",
     "required": True, "default": True, "apt": (), "hardware": "aio_board", "needs": ()},
    {"key": "tray", "title": "Tray",
     "summary": "The tray icon for rails and services (aiov2_ctl --gui) and its start-menu entry.",
     "required": False, "default": True, "apt": ("python3-pyqt6",), "hardware": "desktop", "needs": ("core",)},
    {"key": "app", "title": "Ops app",
     "summary": "The uConsole Ops window: rails, radios, GPS, LoRa, SDR and the Flipper in one place.",
     "required": False, "default": True, "apt": ("git", "python3-pyqt6", "fonts-spleen"), "hardware": "desktop",
     "needs": ("core",)},
    {"key": "console", "title": "Ops console",
     "summary": "The same tools in a terminal (ops.py).",
     "required": False, "default": True, "apt": ("git", "python3-textual", "python3-numpy"), "hardware": None,
     "needs": ("core",)},
    {"key": "wiki", "title": "Local wiki",
     "summary": "The project's wiki as a tab in the ops app, read offline. The pages come with the checkout.",
     "required": False, "default": True, "apt": (), "hardware": None, "needs": ("app",)},
    {"key": "wikipics", "title": "Wiki pictures",
     "summary": "The wiki's pictures, downloaded once (about 3 MB), and the package that lets the app show them.",
     "required": False, "default": False, "apt": ("qt6-image-formats-plugins",), "hardware": None,
     "needs": ("wiki",)},
    {"key": "gps", "title": "GPS",
     "summary": "gpsd, which reads the GPS receiver for the app and the console.",
     "required": False, "default": True, "apt": ("gpsd",), "hardware": "aio_board", "needs": ("core",)},
    {"key": "mesh", "title": "LoRa mesh",
     "summary": "meshtasticd, the service that runs the LoRa mesh radio.",
     "required": False, "default": True, "apt": ("meshtasticd",), "hardware": "aio_board", "needs": ("core",)},
    {"key": "sdr", "title": "Software radio",
     "summary": "The RTL-SDR tools, and the recovery that brings back a tuner that has stopped answering.",
     "required": False, "default": True, "apt": ("rtl-sdr",), "hardware": "aio_board", "needs": ("core",)},
    {"key": "adsb", "title": "Aircraft receiver",
     "summary": "readsb, which decodes aircraft positions from the software radio.",
     "required": False, "default": False, "apt": ("readsb",), "hardware": "aio_board", "needs": ("sdr",)},
    {"key": "listening", "title": "Passive Wi-Fi and Bluetooth listening",
     "summary": "Listening only, on the internal card: --wifi-listen, --wifi-survey and --bt-scan without a password.",
     "required": False, "default": True, "apt": ("iw",), "hardware": "internal_card", "needs": ("core",)},
    {"key": "flipper", "title": "Flipper and Marauder",
     "summary": "The rule that lets you, not just root, open a Flipper Zero or ESP32 board on USB.",
     "required": False, "default": True, "apt": (), "hardware": "flipper", "needs": ("core",)},
    {"key": "autostart", "title": "Tray at login",
     "summary": "Start the tray when you log in.",
     "required": False, "default": False, "apt": (), "hardware": "desktop", "needs": ("tray",)},
)

# Packages a feature can do without: where one is not in the package sources the feature still installs.
OPTIONAL_PACKAGES = {"console": ("python3-textual", "python3-numpy")}
# What to say when a package is not in this system's package sources.
PACKAGE_HINTS = {
    "meshtasticd": "It comes from the Meshtastic package source, which 'sudo aiov2_ctl --add-apps' sets up.",
    "readsb": "'sudo aiov2_ctl --add-apps' installs it together with the map.",
}
# Units a package enables as it installs. This install switches them back off, and only when it is what added
# the package: nothing starts at boot unless the owner says so.
PACKAGE_UNITS = {
    "gps": ("gpsd", ("gpsd.socket", "gpsd.service"), "sudo systemctl enable --now gpsd.socket"),
    "mesh": ("meshtasticd", ("meshtasticd.service",), "aiov2_ctl --mesh-on-boot on"),
    "adsb": ("readsb", ("readsb.service",), "sudo systemctl start readsb"),
}
HARDWARE_TITLES = {
    "aio_board": "the AIO board",
    "internal_card": "the internal Wi-Fi and Bluetooth card",
    "flipper": "a Flipper Zero",
    "desktop": "a desktop",
}
HARDWARE_MISSING = {
    "aio_board": "No sign of the AIO board. Its radios only show while their rail is on, so this may mean nothing. "
                 "It is installed anyway and no rail was switched on to look.",
    "internal_card": "The internal Wi-Fi and Bluetooth card (USB 0e8d:7961) was not found. It only shows while the "
                     "USB rail is on. It is installed anyway and the rail is left as it is.",
    "flipper": "No Flipper is plugged in (USB 0483:5740). The rule is installed anyway and applies when one is.",
    "desktop": "No desktop was found on this machine. It is installed anyway and needs a desktop to show.",
}
# What install did, in order, before features existed. The default selection must still do every one of these.
LEGACY_INSTALL_STEPS = (
    "listening.apt", "core.copy", "tray.assets", "core.completion", "tray.desktop-entry", "flipper.udev",
    "core.rails-boot", "sdr.recovery", "core.sudoers", "core.meta",
)
NO_USER_MESSAGE = (
    "Cannot tell whose home the ops app belongs in: this is running as root with no SUDO_USER. "
    "Log in as yourself and run: sudo aiov2_ctl --install"
)
INSTALL_USAGE = (
    "Usage: sudo aiov2_ctl --install [--pick] [--all | --only LIST | --without LIST] [--dry-run] [--yes]\n"
    "       --pick lets you tick features on screen. LIST is feature keys separated by commas.\n"
    "       See them with: aiov2_ctl --features"
)


class InstallError(ValueError):
    """A selection or command line the installer refuses, with words fit to show the user."""


def feature_keys():
    return [f["key"] for f in FEATURES]


def feature_row(key):
    for f in FEATURES:
        if f["key"] == key:
            return f
    raise InstallError(f"Unknown feature '{key}'. Valid features: {', '.join(feature_keys())}")


def default_features():
    return [f["key"] for f in FEATURES if f["required"] or f["default"]]


def _key_list(text):
    keys = [k.strip().lower() for k in (text or "").split(",") if k.strip()]
    if not keys:
        raise InstallError("A list of features is needed, for example: --only core,tray\n" + INSTALL_USAGE)
    unknown = [k for k in keys if k not in feature_keys()]
    if unknown:
        raise InstallError(f"Unknown feature{'s' if len(unknown) > 1 else ''}: {', '.join(unknown)}. "
                           f"Valid features: {', '.join(feature_keys())}")
    return keys


def parse_install_args(args):
    """The words after --install as a dict. Raises InstallError for anything it does not know."""
    opts = {"all": False, "only": None, "without": [], "dry_run": False, "yes": False, "show_mesh_status": False,
            "pick": False, "repair": False}
    args = list(args or [])
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--all":
            opts["all"] = True
        elif arg in ("--only", "--without"):
            if i + 1 >= len(args):
                raise InstallError(f"{arg} needs a list of features.\n" + INSTALL_USAGE)
            keys = _key_list(args[i + 1])
            if arg == "--only":
                opts["only"] = (opts["only"] or []) + keys
            else:
                opts["without"] += keys
            i += 1
        elif arg == "--dry-run":
            opts["dry_run"] = True
        elif arg == "--pick":
            opts["pick"] = True
        elif arg == "--repair":
            opts["repair"] = True
        elif arg in ("--yes", "-y"):
            opts["yes"] = True
        elif arg == "--show-mesh-status":
            opts["show_mesh_status"] = True
        else:
            raise InstallError(f"Unknown option for --install: {arg}\n" + INSTALL_USAGE)
        i += 1
    if opts["all"] and opts["only"] is not None:
        raise InstallError("--all and --only cannot be used together.\n" + INSTALL_USAGE)
    if opts["repair"] and (opts["all"] or opts["only"] is not None or opts["without"] or opts["pick"]):
        raise InstallError("--repair goes over what is already installed, so it takes no choice of features. "
                           "Use: sudo aiov2_ctl --repair [--dry-run] [--yes]")
    return opts


def resolve_features(keys):
    """The selection with everything it depends on and every required feature added, in the registry's order."""
    wanted = set()
    todo = [f["key"] for f in FEATURES if f["required"]] + list(keys)
    while todo:
        key = todo.pop()
        if key not in wanted:
            wanted.add(key)
            todo += list(feature_row(key)["needs"])
    return [k for k in feature_keys() if k in wanted]


def select_features(args, facts=None):
    """Which features to install, from --all, --only LIST, --without LIST, or the defaults when none is given.

    `facts` is taken so a caller can pass what it knows, but missing hardware never removes a feature here: it is
    still installed and the plan says what was not found."""
    opts = args if isinstance(args, dict) else parse_install_args(args)
    if opts["only"] is not None:
        keys = list(opts["only"])
    elif opts["all"]:
        keys = feature_keys()
    else:
        keys = default_features()
    for key in opts["without"]:
        if feature_row(key)["required"]:
            raise InstallError(f"'{key}' cannot be left out: everything else needs it.")
    keys = resolve_features(keys)
    # Leaving a feature out also leaves out whatever cannot work without it.
    dropped = set(opts["without"])
    changed = True
    while changed:
        changed = False
        for key in keys:
            if key not in dropped and dropped & set(feature_row(key)["needs"]):
                dropped.add(key)
                changed = True
    if opts["only"] is not None:
        clash = sorted(dropped & set(resolve_features(opts["only"])) - set(opts["without"]))
        if clash:
            raise InstallError(f"{', '.join(clash)} was asked for with --only but needs a feature left out "
                               f"with --without.")
    return [k for k in keys if k not in dropped]


# ---- what is here: hardware and what is already installed -----------------------------------------------------

def usb_ids_present(base=None):
    """{(vendor, product)} for every device on the USB bus, from sysfs."""
    base = base or USB_SYSFS_BASE
    ids = set()
    try:
        names = os.listdir(base)
    except OSError:
        return ids
    for name in names:
        if ":" in name:           # an interface such as 1-1:1.0, not a device
            continue
        vid = read_sysfs(os.path.join(base, name, "idVendor"))
        pid = read_sysfs(os.path.join(base, name, "idProduct"))
        if vid and pid:
            ids.add((vid.lower(), pid.lower()))
    return ids


def desktop_available(environ=None, listdir=os.listdir):
    """True with a desktop session running, or one installed to log in to. sudo drops most of the session's
    variables, so the installed sessions are looked at as well."""
    env = os.environ if environ is None else environ
    if any(env.get(v) for v in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE")):
        return True
    for folder in ("/usr/share/xsessions", "/usr/share/wayland-sessions"):
        try:
            if any(name.endswith(".desktop") for name in listdir(folder)):
                return True
        except OSError:
            pass
    return False


def hardware_facts(usb_ids=None, environ=None, exists=os.path.exists, listdir=os.listdir):
    """Plain facts about this machine, for the planner. Read only: nothing is switched on to look, so a radio
    whose rail is off is honestly 'not found'."""
    ids = usb_ids_present() if usb_ids is None else set(usb_ids)
    internal_card = MT7921_USB_ID in ids
    aio_board = bool(internal_card or any(i in ids for i in RTLSDR_USB_IDS)
                     or exists(AIO_BOARD_PACKAGE_DOC) or exists("/dev/spidev1.0"))
    return {
        "aio_board": aio_board,
        "internal_card": internal_card,
        "flipper": FLIPPER_USB_ID in ids,
        "desktop": desktop_available(environ, listdir),
    }


def invoking_user(environ=None, euid=None, by_name=pwd.getpwnam, by_uid=pwd.getpwuid):
    """The person this install is for, as a passwd entry, or None when that cannot be told.

    Not root: whoever is running this. Root: only the user named by sudo. Plain root (a root login, su, a boot
    script) gets None on purpose: guessing would put the ops app in the wrong home or run git as root."""
    env = os.environ if environ is None else environ
    euid = os.geteuid() if euid is None else euid
    try:
        if euid != 0:
            return by_uid(euid)
        name = env.get("SUDO_USER", "")
        uid = env.get("PKEXEC_UID", "")
        if not name and uid.isdigit() and int(uid) != 0:
            # Started by pkexec (the installer window asking for a password): it names the user by number.
            entry = by_uid(int(uid))
            return entry if entry.pw_uid != 0 and _USERNAME_RE.match(entry.pw_name) else None
        if not name or name == "root" or not _USERNAME_RE.match(name):
            return None
        entry = by_name(name)
    except KeyError:
        return None
    return entry if entry.pw_uid != 0 else None


def as_user(user, cmd, euid=None, env=None):
    """The command line that runs `cmd` as `user`. As root that is sudo -u (with the user's own HOME); when
    already that user it is the command itself. `env` is passed on the command line because sudo starts the
    command with a clean environment."""
    euid = os.geteuid() if euid is None else euid
    cmd = list(cmd)
    if env:
        cmd = ["env", *[f"{k}={v}" for k, v in sorted(env.items())], *cmd]
    if euid == 0:
        return ["sudo", "-u", user, "-H", "--", *cmd]
    return cmd


def ops_checkout_path(home):
    """Where the ops app used to be cloned, when it was a repository of its own."""
    return os.path.join(home, OPS_CHECKOUT_NAME)


def app_source(repo, isfile=os.path.isfile):
    """The folder the ops app and console run from: this tool's checkout when it holds them, else None. An
    install made from a single downloaded file has no checkout, and so no app."""
    if repo and all(isfile(os.path.join(repo, name)) for name in APP_FILES):
        return repo
    return None


def source_repo():
    """The checkout this run would install from, or None: the one it is run from, else the one an earlier
    install recorded."""
    recorded = (load_install_meta() or {}).get("repo_path")
    repo = source_checkout(os.path.realpath(__file__), os.path.realpath(INSTALLED_PATH), get_git_root(),
                           recorded if recorded and os.path.isdir(recorded) else None)
    return os.path.realpath(repo) if repo else None


def is_ops_remote(url):
    """True when a git remote address is the old ops repository, however it is spelled (https, ssh, with or without
    .git)."""
    text = (url or "").strip().lower()
    if text.endswith(".git"):
        text = text[:-4]
    text = text.rstrip("/")
    want = OPS_REPO_NAME.lower()
    return any(text == prefix + want for prefix in (
        "https://github.com/", "http://github.com/", "git@github.com:", "ssh://git@github.com/",
        "git://github.com/"))


def _run_text(cmd, cwd=None, timeout=None):
    """(return code, stdout, stderr). A command that cannot be started counts as failed."""
    try:
        done = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return done.returncode, done.stdout or "", done.stderr or ""
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)


def checkout_state(path, user, run=_run_text, lstat=os.lstat, euid=None):
    """What is at the place the ops app used to be cloned: 'missing', 'ours' (that repository), 'link' (a
    symbolic link, never followed), 'not-git' (something else is there) or 'other' (a different repository). git runs as the
    user, never as root."""
    try:
        st = lstat(path)
    except OSError:
        return "missing"
    if stat.S_ISLNK(st.st_mode):
        return "link"
    if not stat.S_ISDIR(st.st_mode):
        return "not-git"
    rc, out, _ = run(as_user(user, ["git", "-C", path, "remote", "get-url", "origin"], euid=euid), timeout=30)
    if rc != 0:
        return "not-git"
    return "ours" if is_ops_remote(out) else "other"


def apt_candidate(policy_text):
    """The version apt would install, from 'apt-cache policy PKG', or None when there is none."""
    m = re.search(r"^\s*Candidate:\s*(\S+)", policy_text or "", re.M)
    if not m or m.group(1) == "(none)":
        return None
    return m.group(1)


def package_installed(pkg, run=_run_text):
    rc, out, _ = run(["dpkg-query", "-W", "-f=${Status}", pkg], timeout=30)
    return rc == 0 and "install ok installed" in out


def package_available(pkg, run=_run_text):
    rc, out, _ = run(["apt-cache", "policy", pkg], timeout=60)
    return rc == 0 and apt_candidate(out) is not None


def package_lists_present(listdir=os.listdir, path="/var/lib/apt/lists"):
    """True when apt has fetched its package lists. A fresh system image often has none, and then apt knows
    of no package at all, which says nothing about what its sources hold."""
    try:
        return any(n.endswith("_Packages") or "_Packages." in n for n in listdir(path))
    except OSError:
        return False


def all_feature_packages():
    seen = []
    for f in FEATURES:
        for pkg in f["apt"]:
            if pkg not in seen:
                seen.append(pkg)
    return seen


def _file_is(path, text):
    try:
        with open(path) as f:
            return f.read() == text
    except OSError:
        return False


def install_state(environ=None, euid=None, run=_run_text):
    """What is installed now, for the planner and for --features. Read only and safe without root; what cannot
    be read without root is left as 'not known' (None)."""
    euid = os.geteuid() if euid is None else euid
    who = invoking_user(environ, euid)
    installed = {p for p in all_feature_packages() if package_installed(p, run)}
    lists = package_lists_present()
    unavailable = ({p for p in all_feature_packages() if p not in installed and not package_available(p, run)}
                   if lists else set())
    present = set()
    if os.path.isfile(INSTALLED_PATH):
        present.add("exe")
    if os.path.isfile(COMPLETION_PATH):
        present.add("completion")
    if os.path.isfile(RAILS_BOOT_SERVICE_PATH):
        present.add("rails-unit")
    if os.path.isfile(SYSTEM_DESKTOP_PATH):
        present.add("desktop-entry")
    if _file_is(COMPANION_UDEV_PATH, COMPANION_UDEV_RULES):
        present.add("udev")
    if os.path.isfile(SDR_RECOVERY_DROPIN_PATH):
        present.add("sdr-recovery")
    source = app_source(source_repo())
    old_checkout = False
    if who:
        old_checkout = checkout_state(ops_checkout_path(who.pw_dir), who.pw_name, run, euid=euid) == "ours"
        apps = os.path.join(who.pw_dir, ".local", "share", "applications")
        for marker, (_, name, _) in zip(("gui-shortcut", "console-shortcut"), OPS_SHORTCUTS):
            if os.path.isfile(os.path.join(apps, name)):
                present.add(marker)
        if os.path.isfile(os.path.join(who.pw_dir, ".config", "autostart", "aiov2_ctl.desktop")):
            present.add("autostart")
    if source:
        present.add("app-files")
    pictures = load_wiki_pictures(source) if source else {}
    if who:
        if os.path.isfile(os.path.join(wiki_home(who.pw_dir), "enabled")):
            present.add("wiki-on")
        if pictures and not wiki_pictures_missing(pictures, wiki_home(who.pw_dir)):
            present.add("wiki-pictures")
    # The rules file can only be read by root; anyone else asks sudo what they may run, which needs no password
    # once the rules are there.
    listening = None
    if euid == 0:
        try:
            with open(SUDOERS_PATH) as f:
                listening = "--wifi-listen" in f.read()
        except OSError:
            listening = False
    else:
        rc, out, _ = run(["sudo", "-n", "-l"], timeout=20)
        if rc == 0:
            listening = "--wifi-listen" in out
    if listening:
        present.add("sudoers-listening")
    return {
        "user": who.pw_name if who else None,
        "home": who.pw_dir if who else None,
        "app_source": source,
        "old_checkout": old_checkout,
        "wiki_pictures": {"count": len(pictures), "bytes": sum(p["bytes"] for p in pictures.values())},
        "remote": (load_install_meta() or {}).get("remote"),
        "installed": installed,
        "unavailable": unavailable,
        "no_package_lists": not lists,
        "sudoers_listening": listening,
        "present": present,
    }


FEATURE_MARKERS = {
    "core": ("exe", "completion", "rails-unit"),
    "tray": ("desktop-entry",),
    "app": ("app-files", "gui-shortcut"),
    "console": ("app-files", "console-shortcut"),
    "wiki": ("wiki-on",),
    "wikipics": ("wiki-pictures",),
    "sdr": ("sdr-recovery",),
    "listening": ("sudoers-listening",),
    "flipper": ("udev",),
    "autostart": ("autostart",),
}


def feature_installed(key, state):
    """'yes', 'partly' or 'no': whether a feature looks installed, from the state alone."""
    f = feature_row(key)
    optional = OPTIONAL_PACKAGES.get(key, ())
    packages = [pkg in state.get("installed", ()) for pkg in f["apt"] if pkg not in optional]
    markers = FEATURE_MARKERS.get(key, ())
    marks = [m in state.get("present", ()) for m in markers]
    if (packages or marks) and all(packages + marks):
        return "yes"
    # A package on its own proves nothing (git and PyQt are on most machines), and nor do the app's files, which
    # come with every checkout. Where a feature writes something of its own, only that counts as a start.
    own = [m in state.get("present", ()) for m in markers if m != "app-files"]
    return "partly" if any(own if own else packages) else "no"


# ---- the planner ------------------------------------------------------------------------------------------------

def _step(feature_key, name, kind, detail, root, undo, **extra):
    step = {"id": f"{feature_key}.{name}", "feature": feature_key, "kind": kind, "detail": detail, "root": root,
            "undo": undo}
    step.update(extra)
    return step


def _apt_step(key, state):
    f = feature_row(key)
    if not f["apt"]:
        return []
    have = [p for p in f["apt"] if p in state.get("installed", ())]
    need = [p for p in f["apt"] if p not in have]
    if need:
        detail = "Install the package" + ("s " if len(need) > 1 else " ") + ", ".join(need)
        if have:
            detail += f" (already here: {', '.join(have)})"
    else:
        detail = "Packages already here: " + ", ".join(have)
    steps = [_step(key, "apt", "apt", detail + ".", True,
                   "Remove with: sudo apt remove " + " ".join(f["apt"]),
                   packages=tuple(f["apt"]), optional=tuple(OPTIONAL_PACKAGES.get(key, ())))]
    for pkg in need:
        if pkg in state.get("unavailable", ()):
            if pkg in OPTIONAL_PACKAGES.get(key, ()):
                text = (f"{pkg} is not in this system's package sources. The console installs what it lacks "
                        f"itself the first time you run it, as you, not as root.")
            else:
                text = f"{pkg} is not in this system's package sources, so it cannot be installed from here."
                if pkg in PACKAGE_HINTS:
                    text += " " + PACKAGE_HINTS[pkg]
            steps.append(_step(key, f"apt-missing-{pkg}", "note", text, False, ""))
    return steps


def _hold_step(key):
    if key not in PACKAGE_UNITS:
        return []
    pkg, units, start = PACKAGE_UNITS[key]
    return [_step(key, "hold", "service-off",
                  f"If this install is what adds {pkg}, stop it and keep it from starting at boot "
                  f"({', '.join(units)}). One that was already here is left exactly as it is. "
                  f"Start it yourself with: {start}", True, "", package=pkg, disables=tuple(units), start=start)]


# ---- the local wiki ----------------------------------------------------------------------------------------------
# The wiki's pages come with the checkout (docs/wiki) and the ops app shows them in a Wiki tab. Its pictures do
# not come with it: they are lossless WebP files kept in the project's GitHub wiki, listed in
# docs/wiki/pictures.json with their size and SHA-256, and downloaded only for someone who picks 'wikipics'.
WIKI_DIR = os.path.join("docs", "wiki")
PROJECT_REPO = "AceMedia/aiov2_ctl"
WIKI_PICTURE_LIMIT = 4 << 20          # no single picture is anywhere near this
_WIKI_PICTURE_PATH = re.compile(r"img/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+\.webp")


def wiki_home(home):
    """Where a user's wiki switch and downloaded pictures are kept."""
    return os.path.join(home, ".local", "share", "aiov2_ctl", "wiki")


def repo_slug(url):
    """'OWNER/NAME' from a GitHub address, however it is spelled, or None."""
    m = re.search(r"github\.com[:/]+([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?\s*$", url or "")
    return f"{m.group(1)}/{m.group(2)}" if m else None


def wiki_pictures_url(remote=None):
    """Where the wiki's pictures are served from: the wiki of the repository this was installed from."""
    return f"https://raw.githubusercontent.com/wiki/{repo_slug(remote) or PROJECT_REPO}/"


def good_wiki_pictures(data):
    """The entries of a pictures list that are what they should be: a WebP under img/, a sane size, a SHA-256.
    Anything else is dropped, so a path can never lead out of the pictures folder."""
    good = {}
    for path, info in (data.items() if isinstance(data, dict) else ()):
        if (isinstance(path, str) and _WIKI_PICTURE_PATH.fullmatch(path) and ".." not in path
                and isinstance(info, dict) and isinstance(info.get("bytes"), int)
                and not isinstance(info.get("bytes"), bool) and 0 < info["bytes"] <= WIKI_PICTURE_LIMIT
                and isinstance(info.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", info["sha256"])):
            good[path] = {"bytes": info["bytes"], "sha256": info["sha256"]}
    return good


def load_wiki_pictures(source):
    """The list of the wiki's pictures kept in the checkout, or {} when there is none."""
    try:
        with open(os.path.join(source, WIKI_DIR, "pictures.json"), encoding="utf-8") as f:
            return good_wiki_pictures(json.load(f))
    except (OSError, ValueError, TypeError):
        return {}


def wiki_pictures_missing(pictures, folder, size=os.path.getsize):
    """The listed pictures that are not in `folder` at their right size."""
    gone = []
    for path, info in sorted(pictures.items()):
        try:
            if size(os.path.join(folder, path)) != info["bytes"]:
                gone.append(path)
        except OSError:
            gone.append(path)
    return gone


def fetch_wiki_pictures(pictures, folder, base_url, get=None):
    """Download the listed pictures that are not already here. Each is checked against its size and SHA-256
    before it is put in place, so a wrong or cut-off file is never kept. Returns (fetched, already, failed);
    failed is [(path, why)]."""
    import hashlib

    def over_the_network(url):
        import urllib.request
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.read(WIKI_PICTURE_LIMIT + 1)
    get = get or over_the_network
    fetched, already, failed = [], [], []
    for path, info in sorted(pictures.items()):
        target = os.path.join(folder, path)
        try:
            with open(target, "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() == info["sha256"]:
                    already.append(path)
                    continue
        except OSError:
            pass
        try:
            data = get(base_url + path)
        except Exception as exc:
            failed.append((path, str(exc) or type(exc).__name__))
            continue
        if len(data) != info["bytes"] or hashlib.sha256(data).hexdigest() != info["sha256"]:
            failed.append((path, "what came back is not the listed picture"))
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        part = target + ".part"
        with open(part, "wb") as f:
            f.write(data)
        os.replace(part, target)
        fetched.append(path)
    return fetched, already, failed


def wiki_pictures_command(args):
    """aiov2_ctl --wiki-pictures fetch CHECKOUT FOLDER URL: the download step, run as the user by the
    installer. Returns the exit code."""
    args = list(args or ())
    if len(args) != 4 or args[0] != "fetch":
        print("Usage: aiov2_ctl --wiki-pictures fetch CHECKOUT FOLDER URL")
        return 2
    if os.geteuid() == 0:
        print("Run this as yourself, not as root: the pictures go in your own folder.")
        return 1
    if not args[3].startswith(("https://", "file://")):
        print("The address must start with https:// (or file:// for a copy on this machine).")
        return 2
    pictures = load_wiki_pictures(args[1])
    if not pictures:
        print(f"No list of pictures in {os.path.join(args[1], WIKI_DIR)}.")
        return 1
    fetched, already, failed = fetch_wiki_pictures(pictures, args[2], args[3])
    print(f"{len(fetched)} downloaded, {len(already)} already here, {len(failed)} failed.")
    for path, why in failed[:5]:
        print(f"  {path}: {why}")
    return 1 if failed else 0


def _wiki_steps(key, state):
    """The steps for the local wiki (the Wiki tab's switch) and for its pictures (the download)."""
    user, home, source = state.get("user"), state.get("home"), state.get("app_source")
    if not user or not home:
        return [_step(key, "no-user", "note", NO_USER_MESSAGE.replace("the ops app", "the wiki"), False, "",
                      blocks=True)]
    if not source:
        return [_step(key, "no-files", "note",
                      "The wiki's pages come with this tool's git checkout, and this install is not running "
                      "from one. Install git and run install.sh, and they go in with it.", False, "",
                      skips=True)]
    folder = wiki_home(home)
    if key == "wiki":
        return [_step(key, "on", "wiki-on",
                      f"Switch on the Wiki tab in the ops app for {user}: an empty file, {folder}/enabled. The "
                      f"pages are already here, in {os.path.join(source, WIKI_DIR)}, and are read offline.",
                      False, f"Remove {folder}/enabled", path=folder)]
    info = state.get("wiki_pictures") or {}
    if not info.get("count"):
        return [_step(key, "no-list", "note",
                      "This version has no list of the wiki's pictures, so there is nothing to download.",
                      False, "", skips=True)]
    url = wiki_pictures_url(state.get("remote"))
    return [_step(key, "fetch", "wiki-pictures",
                  f"Download the wiki's {info['count']} pictures ({info['bytes'] / 1e6:.1f} MB) from {url} "
                  f"into {folder}, as {user}. Each is checked against the list in docs/wiki/pictures.json "
                  f"before it is kept. Nothing about you or this machine is sent.", False,
                  f"Remove {folder}/img", path=folder, source=source, url=url)]


def _ops_steps(key, selected, state):
    """The shortcuts, shared by the app and the console (their files are this tool's own checkout): planned once, under whichever of the
    two comes first, and counted for both."""
    both = [k for k in ("app", "console") if k in selected]
    if key != both[0]:
        return []
    shared = tuple(both[1:])
    user, home = state.get("user"), state.get("home")
    if not user or not home:
        return [_step(key, "no-user", "note", NO_USER_MESSAGE, False, "", shared_with=shared, blocks=True)]
    path = state.get("app_source")
    if not path:
        return [_step(key, "no-files", "note",
                      "The ops app and console come with this tool's git checkout, and this install is not "
                      "running from one that holds them. Install git and run install.sh, which keeps such a "
                      "checkout, and they go in with it.", False, "", shared_with=shared, skips=True)]
    steps = [_step(key, "launchers", "launchers",
                   f"Write the start-menu shortcuts (uConsole Ops and uConsole Ops Console) as {user}: "
                   f"python3 -m ops_gui --install-launchers, run in {path}. The app and the console run from "
                   f"that folder, as {user}.", False,
                   f"Run in {path}: python3 -m ops_gui --remove-launchers", path=path, shared_with=shared)]
    if state.get("old_checkout"):
        old = ops_checkout_path(home)
        steps.append(_step(key, "old-folder", "old-folder",
                           f"Remove {old}, as {user}: the ops app used to be kept there and now comes with "
                           f"this tool. Only if it holds nothing of yours: no changed files, nothing unpushed, "
                           f"nothing stashed. Otherwise it is left and you are told why. An ops app or "
                           f"console running from it is closed first.", False, "",
                           path=old, shared_with=shared))
    return steps


def install_plan(selected_keys, facts, state):
    """The steps that install a selection, in order, as plain data. Pure: reads nothing and changes nothing.

    Each step is a dict: id, feature, kind (apt, copy, sudoers, udev, systemd, service-off, old-folder,
    launchers, desktop-entry, autostart, note), detail in plain words, root (whether it needs root), undo (how
    to take it back, '' when there is nothing to take back). Dependencies are added. A feature whose hardware
    was not found is still planned, with a note saying what was missing."""
    facts = facts or {}
    state = state or {}
    selected = resolve_features(selected_keys)
    user = state.get("user")
    listening = "listening" in selected or bool(state.get("sudoers_listening"))
    plan = []
    for key in selected:
        f = feature_row(key)
        if f["hardware"] and facts.get(f["hardware"]) is False:
            plan.append(_step(key, "hardware", "note", HARDWARE_MISSING[f["hardware"]], False, ""))
        plan += _apt_step(key, state)
        if key == "core":
            if state.get("no_package_lists"):
                plan.append(_step(key, "no-lists", "note",
                                  "This system has not fetched its package lists yet. The install fetches them "
                                  "first (apt-get update); until then it is not known whether a package is "
                                  "missing from the package sources.", False, ""))
            plan.append(_step(key, "copy", "copy",
                              f"Copy this program to {INSTALLED_PATH}, and link it where sudo looks if it "
                              f"would not find it there.", True, f"Remove {INSTALLED_PATH}"))
            plan.append(_step(key, "completion", "copy", f"Write bash completion to {COMPLETION_PATH}.", True,
                              f"Remove {COMPLETION_PATH}"))
            plan.append(_step(key, "rails-boot", "systemd",
                              f"Write {RAILS_BOOT_SERVICE} and keep it enabled, as install always has: at boot "
                              f"it puts each rail in the state you saved (all off unless you chose otherwise). "
                              f"It switches nothing now.", True,
                              f"sudo systemctl disable {RAILS_BOOT_SERVICE}; remove {RAILS_BOOT_SERVICE_PATH}",
                              enables=(RAILS_BOOT_SERVICE,)))
            who = user or "the user who ran sudo"
            plan.append(_step(key, "sudoers", "sudoers",
                              f"Write {SUDOERS_PATH} for {who}: exact commands only, for rails, the boot "
                              f"device and the mesh and aircraft services"
                              + (", and passive listening." if listening else "; no listening rules."), True,
                              "sudo aiov2_ctl --setup-sudoers --remove", listening=listening))
            plan.append(_step(key, "meta", "copy",
                              f"Note where this was installed from in {INSTALL_META_PATH}, for --update.", True,
                              f"Remove {INSTALL_META_PATH}"))
        elif key == "tray":
            plan.append(_step(key, "assets", "copy", f"Copy the icons to {ASSET_BASE}.", True,
                              f"Remove {ASSET_BASE}/img"))
            plan.append(_step(key, "desktop-entry", "desktop-entry",
                              f"Write the start-menu entry {SYSTEM_DESKTOP_PATH}.", True,
                              f"Remove {SYSTEM_DESKTOP_PATH}"))
            plan.append(_step(key, "installer-entry", "desktop-entry",
                              f"Write the installer's start-menu entry {INSTALLER_DESKTOP_PATH}.", True,
                              f"Remove {INSTALLER_DESKTOP_PATH}"))
        elif key in ("app", "console"):
            plan += _ops_steps(key, selected, state)
            if key == "console":
                plan.append(_step(key, "python", "note",
                                  "The console's meshtastic library is not an apt package. The console installs "
                                  "what it lacks itself the first time you run it, as you; this installer "
                                  "never runs pip as root.", False, ""))
        elif key in ("wiki", "wikipics"):
            plan += _wiki_steps(key, state)
        elif key == "sdr":
            plan.append(_step(key, "recovery", "systemd",
                              f"Write the readsb recovery ({SDR_RECOVERY_DROPIN_PATH}) and the once-a-minute "
                              f"check ({SDR_WATCHDOG_TIMER}), kept enabled as install always has. It acts only "
                              f"while the SDR rail is on and readsb is running without a tuner; it never "
                              f"switches on a rail that is off.", True,
                              f"sudo systemctl disable --now {SDR_WATCHDOG_TIMER}; remove "
                              f"{SDR_RECOVERY_DROPIN_PATH}", enables=(SDR_WATCHDOG_TIMER,)))
        elif key == "flipper":
            plan.append(_step(key, "udev", "udev",
                              f"Write {COMPANION_UDEV_PATH} so you can open a Flipper or ESP32 board without "
                              f"root.", True, f"Remove {COMPANION_UDEV_PATH}"))
        elif key == "autostart":
            who = user or "you"
            if user:
                plan.append(_step(key, "entry", "autostart",
                                  f"Start the tray when {who} logs in: write "
                                  f"~{who}/.config/autostart/aiov2_ctl.desktop as {who}.", False,
                                  "aiov2_ctl --no-autostart", starts_at_login=True))
            else:
                plan.append(_step(key, "no-user", "note", NO_USER_MESSAGE.replace("the ops app", "the tray"),
                                  False, "", blocks=True))
        plan += _hold_step(key)
    return plan


def format_plan(plan, selected):
    lines = ["This will install: " + ", ".join(selected), ""]
    current = None
    n = 0
    for step in plan:
        if step["feature"] != current:
            current = step["feature"]
            lines.append(f"{feature_row(current)['title']} ({current})")
        if step["kind"] == "note":
            lines.append(f"      note: {step['detail']}")
        else:
            n += 1
            lines.append(f"  {n:>2}. {step['detail']}" + ("" if step["root"] else "  [as you, not root]"))
    lines.append("")
    lines.append("No rail is switched on, no radio is started, and nothing is made to start at boot beyond "
                 "what is listed above.")
    return "\n".join(lines)


def format_features(state, facts):
    lines = [f"{'feature':<10} {'default':<8} {'installed':<10} what it is, and what it needs", "-" * 78]
    for f in FEATURES:
        default = "always" if f["required"] else ("on" if f["default"] else "off")
        needs = []
        if f["apt"]:
            needs.append("packages " + ", ".join(f["apt"]))
        if f["hardware"]:
            found = facts.get(f["hardware"])
            needs.append(HARDWARE_TITLES[f["hardware"]]
                         + (" (found)" if found else " (not found now)" if found is False else ""))
        if f["needs"]:
            needs.append("feature " + ", ".join(f["needs"]))
        lines.append(f"{f['key']:<10} {default:<8} {feature_installed(f['key'], state):<10} {f['title']}: "
                     f"{f['summary']}")
        lines.append(f"{'':<30} needs: {'; '.join(needs) if needs else 'nothing else'}")
    lines.append("")
    lines.append("Install with: sudo aiov2_ctl --install [--pick] [--all | --only LIST | --without LIST] [--dry-run] [--yes]")
    lines.append("Tick them on screen with: sudo aiov2_ctl --install --pick     In a window: aiov2_ctl --installer")
    lines.append("Put back what is missing: sudo aiov2_ctl --repair      Remove: sudo aiov2_ctl --uninstall [--only LIST]")
    return "\n".join(lines)


def features_report(state, meta, facts, how=None, unfinished=False):
    """The features as data, for a program that shows them (the ops app's Settings). Each says whether the
    installer has it down as installed, what is missing from it, what adding it would bring with it and what
    removing it would take with it. `root` is how a program gets root for a change: 'sudo', 'pkexec' or None.
    `unfinished` is what is left of an update: '' or False (nothing), 'program' (a newer version is in the
    checkout but was never put in place) or 'installer' (it is in place but its installer has not run); True,
    from an older caller, means 'installer'. The report carries it as `pending`, and as `unfinished` (true or
    false) for an app older than `pending`."""
    pending = unfinished if unfinished in ("program", "installer") else ("installer" if unfinished else "")
    recorded = recorded_features(meta, state)
    rows = []
    for f in FEATURES:
        key = f["key"]
        found = facts.get(f["hardware"]) if f["hardware"] else None
        rows.append({
            "key": key,
            "title": f["title"],
            "summary": f["summary"],
            "required": bool(f["required"]),
            # Asked for, and something of it is here: the same test the table uses. A feature whose package this
            # system does not have, or an app with no checkout to run from, is wanted and not installed, and
            # `missing` says what it lacks.
            "installed": key in recorded and feature_installed(key, state) != "no",
            "wanted": key in recorded,
            "missing": feature_missing(key, state) if key in recorded else [],
            "adds": [k for k in resolve_features([key]) if k != key and k not in recorded],
            "removes": [k for k in uninstall_selection([key]) if k != key and k in recorded],
            "hardware": HARDWARE_TITLES[f["hardware"]] if f["hardware"] else "",
            "hardware_found": found if isinstance(found, bool) else None,
        })
    return {"version": 1, "root": how, "unfinished": bool(pending), "pending": pending, "features": rows}


def features_command(args=()):
    args = list(args or ())
    if args == ["--json"]:
        how = elevation(_run_text(["sudo", "-n", "true"], timeout=10)[0] == 0, shutil.which("pkexec"))
        print(json.dumps(features_report(install_state(), load_install_meta(), hardware_facts(), how,
                                         pending_update()), indent=1))
        return 0
    if args:
        print("Usage: aiov2_ctl --features [--json]")
        return 2
    print(format_features(install_state(), hardware_facts()))
    return 0


# ---- the picker: choosing features on a terminal ---------------------------------------------------------------
# FeaturePicker and picker_lines are pure. The two front ends (a full screen, and plain numbered lines for a
# terminal that cannot do one) only read keys and draw what picker_lines returns.

PICKER_KEYS_HELP = "Up/Down move   Space tick or untick   A everything   U the usual set   N only what is needed   Enter go on   Q quit"


class FeaturePicker:
    """Which features are ticked, and where the cursor is. Ticking one ticks what it needs; unticking one unticks
    whatever cannot work without it; a required feature stays ticked."""

    def __init__(self, ticked=None):
        self.keys = feature_keys()
        self.ticked = set(resolve_features(default_features() if ticked is None else ticked))
        self.cursor = 0
        self.message = ""

    def selected(self):
        return [k for k in self.keys if k in self.ticked]

    def current(self):
        return self.keys[self.cursor]

    def move(self, delta):
        self.cursor = max(0, min(len(self.keys) - 1, self.cursor + delta))
        self.message = ""

    def toggle(self, key=None):
        key = self.current() if key is None else key
        row = feature_row(key)
        if key in self.ticked:
            if row["required"]:
                self.message = f"{row['title']} cannot be left out: everything else needs it."
                return
            gone = {key}
            changed = True
            while changed:
                changed = False
                for other in self.keys:
                    if other in self.ticked and other not in gone and gone & set(feature_row(other)["needs"]):
                        gone.add(other)
                        changed = True
            self.ticked -= gone
            also = [feature_row(k)["title"] for k in self.keys if k in gone and k != key]
            self.message = (f"Also left out, because it needs {row['title']}: {', '.join(also)}." if also else "")
        else:
            added = set(resolve_features([key])) - self.ticked
            self.ticked |= added
            also = [feature_row(k)["title"] for k in self.keys if k in added and k != key]
            self.message = (f"Also ticked, because {row['title']} needs it: {', '.join(also)}." if also else "")

    def choose(self, keys, message=""):
        self.ticked = set(resolve_features(keys))
        self.message = message

    def press(self, key):
        """One key press, by name. Returns 'accept', 'cancel' or None to carry on."""
        if key in ("up", "k"):
            self.move(-1)
        elif key in ("down", "j"):
            self.move(1)
        elif key == "home":
            self.move(-len(self.keys))
        elif key == "end":
            self.move(len(self.keys))
        elif key in ("space", "x"):
            self.toggle()
        elif key == "a":
            self.choose(self.keys, "Everything is ticked.")
        elif key == "u":
            self.choose(default_features(), "The usual set is ticked.")
        elif key == "n":
            self.choose([], "Only what everything else needs is ticked.")
        elif key == "enter":
            return "accept"
        elif key in ("q", "escape"):
            return "cancel"
        return None


def picker_row_note(key, state, facts):
    """The few words at the end of a feature's row: whether it is installed and whether its hardware was found."""
    row = feature_row(key)
    words = {"yes": "installed", "partly": "partly installed", "no": ""}[feature_installed(key, state)]
    notes = [words] if words else []
    if row["hardware"] and facts.get(row["hardware"]) is False:
        notes.append(HARDWARE_TITLES[row["hardware"]] + " not found")
    return ", ".join(notes)


def picker_lines(picker, state, facts, numbered=False):
    """What the picker shows, as [(text, is_the_cursor_row)]: a heading, a row per feature, then what the feature
    under the cursor is and needs, and any message."""
    lines = [("Choose what to install", False), ("", False)]
    width = max(len(feature_row(k)["title"]) for k in picker.keys)
    for i, key in enumerate(picker.keys):
        row = feature_row(key)
        mark = "[x]" if key in picker.ticked else "[ ]"
        number = f"{i + 1:>2}. " if numbered else ""
        note = picker_row_note(key, state, facts)
        if row["required"]:
            note = ", ".join(n for n in ("always", note) if n)
        text = f" {number}{mark} {row['title']:<{width}}  {note}".rstrip()
        lines.append((text, i == picker.cursor and not numbered))
    lines.append(("", False))
    if not numbered:
        row = feature_row(picker.current())
        lines.append((row["summary"], False))
        needs = []
        if row["apt"]:
            needs.append("packages " + ", ".join(row["apt"]))
        if row["needs"]:
            needs.append(", ".join(feature_row(k)["title"] for k in row["needs"]))
        lines.append(("Needs: " + ("; ".join(needs) if needs else "nothing else"), False))
        if row["hardware"] and facts.get(row["hardware"]) is False:
            lines.append((HARDWARE_MISSING[row["hardware"]], False))
        lines.append(("", False))
    if any(k not in picker.ticked and feature_installed(k, state) != "no" for k in picker.keys):
        lines.append(("Unticking something that is already installed does not remove it. Remove it with: "
                      "sudo aiov2_ctl --uninstall --only LIST", False))
    if picker.message:
        lines.append((picker.message, False))
    return lines


def wrap_for_screen(lines, width):
    """picker_lines folded to a screen `width` wide, keeping which row is the cursor's."""
    import textwrap
    out = []
    for text, cursor in lines:
        # A feature's row that is too long carries on under its title, not under its tick box.
        hang = "     " if text.startswith((" [", "  ")) and "] " in text[:12] else ""
        parts = textwrap.wrap(text, max(20, width - 1), subsequent_indent=hang) or [""]
        out += [(part, cursor) for part in parts]
    return out


def pick_on_screen(picker, state, facts):
    """The full-screen picker. Returns the chosen keys, or None if the user quit. Raises if the terminal cannot
    do a full screen, so the caller can fall back to plain lines."""
    import curses

    names = {curses.KEY_UP: "up", curses.KEY_DOWN: "down", curses.KEY_HOME: "home", curses.KEY_END: "end",
             curses.KEY_ENTER: "enter", 10: "enter", 13: "enter", 27: "escape", ord(" "): "space"}

    def loop(screen):
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        while True:
            screen.erase()
            height, width = screen.getmaxyx()
            rows = wrap_for_screen(picker_lines(picker, state, facts), width)
            rows += [("", False)] + wrap_for_screen([(PICKER_KEYS_HELP, False)], width)
            for y, (text, cursor) in enumerate(rows[:height]):
                try:
                    screen.addstr(y, 0, text[:width - 1], curses.A_REVERSE if cursor else curses.A_NORMAL)
                except curses.error:
                    pass
            screen.refresh()
            code = screen.getch()
            name = names.get(code) or (chr(code).lower() if 32 < code < 127 else "")
            done = picker.press(name)
            if done:
                return picker.selected() if done == "accept" else None

    return curses.wrapper(loop)


def pick_by_lines(picker, state, facts, ask=input, say=print):
    """The picker for a terminal that cannot do a full screen: a numbered list and a typed answer."""
    while True:
        say("")
        for text, _ in picker_lines(picker, state, facts, numbered=True):
            say(text)
        try:
            answer = ask("\nA number to tick or untick it, a = everything, u = the usual set, n = only what is "
                         "needed, Enter = go on, q = quit: ").strip().lower()
        except EOFError:
            return None
        if answer == "":
            return picker.selected()
        if answer in ("q", "quit"):
            return None
        if answer in ("a", "u", "n"):
            picker.press(answer)
            continue
        picker.message = ""
        for word in answer.replace(",", " ").split():
            if word.isdigit() and 1 <= int(word) <= len(picker.keys):
                picker.toggle(picker.keys[int(word) - 1])
            elif word in picker.keys:
                picker.toggle(word)
            else:
                picker.message = f"'{word}' is not one of the numbers or names above."


def pick_features(picker, state, facts, ask=input, say=print, screen=pick_on_screen, environ=None):
    """Run whichever picker this terminal can show."""
    env = os.environ if environ is None else environ
    if env.get("TERM", "dumb") not in ("", "dumb"):
        try:
            return screen(picker, state, facts)
        except Exception:
            pass
    return pick_by_lines(picker, state, facts, ask, say)


# ---- carrying a plan out --------------------------------------------------------------------------------------
# Each action returns (status, words): 'done', 'already' (nothing needed changing), 'failed' or 'note'.

def source_checkout(this_file, installed_path, git_root, recorded):
    """The checkout to install from, or None to install the running file itself. The file being run always
    wins: its own git checkout if it is in one, else itself. Only the installed copy running itself falls back
    to the checkout an earlier install recorded. (A downloaded newer file used to be passed over for that older
    checkout, and the install reported success having put the old program back.)"""
    if git_root:
        return git_root
    if this_file == installed_path:
        return recorded
    return None


def install_source():
    """(source file, icons folder or None, metadata) for this run: the git checkout this is run from when there
    is one, else just this file; the installed copy run by itself uses the checkout a previous install recorded."""
    stored = load_install_meta() or {}
    recorded = stored.get("repo_path")
    repo = source_checkout(os.path.realpath(__file__), os.path.realpath(INSTALLED_PATH), get_git_root(),
                           recorded if recorded and os.path.isdir(recorded) else None)
    meta = dict(stored)
    if not repo and os.path.realpath(__file__) != os.path.realpath(INSTALLED_PATH):
        # Run from a loose file (a download, a copy): the old record no longer says where this came from.
        for key in ("repo_path", "branch"):
            meta.pop(key, None)
    if repo and os.path.isfile(os.path.join(repo, "aiov2_ctl.py")):
        repo = os.path.realpath(repo)
        meta["repo_path"] = repo
        try:
            meta["remote"] = subprocess.check_output(["git", "remote", "get-url", "origin"], cwd=repo,
                                                     text=True).strip()
        except Exception:
            # The same refusal as in get_git_root: read the address from the checkout's own file.
            try:
                with open(os.path.join(repo, ".git", "config")) as f:
                    url = origin_from_config(f.read())
            except OSError:
                url = None
            if url:
                meta["remote"] = url
        meta["branch"] = get_git_branch(repo) or "main"
        return os.path.join(repo, "aiov2_ctl.py"), os.path.join(repo, "img"), meta
    return os.path.realpath(__file__), None, meta


def _write_system_file(path, text, mode=0o644):
    """Write a file root owns, in a folder root owns. True when it changed."""
    if _file_is(path, text):
        os.chmod(path, mode)
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, mode)
    return True


def apt_install_argv(packages):
    """The command that installs packages without ever stopping to ask. `-y` answers apt's own question, but
    not the package tool's when a package brings a settings file and one is already there (left by an earlier
    install, or edited): that one waits for a key, and a run with --yes, or one inside the installer window,
    waited for ever. The two options tell it to keep the file that is there and carry on."""
    return ["apt-get", "install", "-y", "-o", "Dpkg::Options::=--force-confdef",
            "-o", "Dpkg::Options::=--force-confold", *packages]


def apt_quiet_env(environ=None):
    """The environment for it: no package's own set-up questions either."""
    return dict(os.environ if environ is None else environ, DEBIAN_FRONTEND="noninteractive")


def _act_apt(step, ctx):
    missing = [p for p in step["packages"] if not package_installed(p)]
    if not missing:
        return "already", ""
    if not ctx.get("apt_updated"):
        ctx["apt_updated"] = True
        subprocess.call(["apt-get", "update"])
    absent = [p for p in missing if not package_available(p)]
    wanted = [p for p in missing if p not in absent]
    env = apt_quiet_env()
    failed = []
    if wanted and subprocess.call(apt_install_argv(wanted), env=env, stdin=subprocess.DEVNULL) != 0:
        # One at a time, so one broken package does not keep the others out.
        for pkg in wanted:
            if not package_installed(pkg) and subprocess.call(apt_install_argv([pkg]), env=env,
                                                               stdin=subprocess.DEVNULL) != 0:
                failed.append(pkg)
    ctx.setdefault("added", set()).update(p for p in wanted if p not in failed)
    hard = [p for p in absent if p not in step.get("optional", ())]
    soft = [p for p in absent if p in step.get("optional", ())]
    words = []
    if failed:
        words.append(f"could not install {', '.join(failed)}. Try: sudo apt install {' '.join(failed)}")
    for pkg in hard:
        words.append(f"{pkg} is not in this system's package sources. {PACKAGE_HINTS.get(pkg, '')}".strip())
    if soft:
        words.append(f"{', '.join(soft)} not in the package sources: the console installs it on first run, as you")
    if failed or any(p not in PACKAGE_HINTS for p in hard):
        return "failed", "; ".join(words)
    if hard:
        # A package known to come from outside this system's sources: nothing went wrong, it just is not here
        # yet. The feature is reported as not installed and the rest of the install is a success.
        return "skipped", "; ".join(words)
    return ("done" if wanted else "note"), "; ".join(words)


def _act_service_off(step, ctx):
    if step["package"] not in ctx.get("added", ()):
        return "already", ""
    if step["feature"] == "mesh" and load_config().get("mesh_on_boot"):
        return "note", "you have set the mesh to start at boot, so that was kept"
    subprocess.call(["systemctl", "disable", "--now", *step["disables"]],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return "note", f"not started and will not start at boot. Start it with: {step['start']}"


def _act_core_copy(step, ctx):
    src = ctx["source"][0]
    if os.path.realpath(src) == os.path.realpath(INSTALLED_PATH):
        ensure_sudo_path_link(INSTALLED_PATH)
        return "already", ""
    same = False
    try:
        with open(src, "rb") as a, open(INSTALLED_PATH, "rb") as b:
            same = a.read() == b.read()
    except OSError:
        pass
    subprocess.check_call(["cp", src, INSTALLED_PATH])
    subprocess.check_call(["chmod", "+x", INSTALLED_PATH])
    ensure_sudo_path_link(INSTALLED_PATH)
    return ("already" if same else "done"), ""


def _act_core_completion(step, ctx):
    return ("done" if _write_system_file(COMPLETION_PATH, BASH_COMPLETION) else "already"), ""


def _act_core_rails_boot(step, ctx):
    changed = _write_system_file(RAILS_BOOT_SERVICE_PATH, RAILS_BOOT_SERVICE_UNIT)
    subprocess.call(["systemctl", "daemon-reload"])
    subprocess.call(["systemctl", "enable", RAILS_BOOT_SERVICE])
    return ("done" if changed else "already"), ""


def _act_core_sudoers(step, ctx):
    before = None
    try:
        with open(SUDOERS_PATH) as f:
            before = f.read()
    except OSError:
        pass
    # Exact, validated rules for the invoking user only (see build_sudoers).
    if setup_sudoers(listening=step.get("listening", True)) != 0:
        return "failed", "the rules were not written. Run 'sudo aiov2_ctl --setup-sudoers' from your own account"
    return ("already" if _file_is(SUDOERS_PATH, before) else "done"), ""


def _act_core_meta(step, ctx):
    meta = dict(ctx["source"][2])
    if ctx.get("features") is not None:
        meta["features"] = list(ctx["features"])
    if ctx.get("stamp"):
        meta["installed_by"] = ctx["stamp"]
    text = json.dumps(meta, indent=2)
    return ("done" if _write_system_file(INSTALL_META_PATH, text) else "already"), ""


def _act_tray_assets(step, ctx):
    img = ctx["source"][1]
    if not img or not os.path.isdir(img):
        return "note", "no icons beside this file (it was not run from a git checkout), so the tray uses a stock icon"
    import filecmp
    there = os.path.join(ASSET_BASE, os.path.basename(os.path.normpath(img)))
    if os.path.isdir(there):
        cmp = filecmp.dircmp(img, there)
        if not cmp.left_only and not cmp.diff_files and not cmp.funny_files:
            return "already", ""
    subprocess.check_call(["mkdir", "-p", ASSET_BASE])
    subprocess.check_call(["cp", "-r", img, ASSET_BASE])
    return "done", ""


def _act_tray_desktop_entry(step, ctx):
    return ("done" if _write_system_file(SYSTEM_DESKTOP_PATH, SYSTEM_DESKTOP_ENTRY) else "already"), ""


def _act_tray_installer_entry(step, ctx):
    return ("done" if _write_system_file(INSTALLER_DESKTOP_PATH, INSTALLER_DESKTOP_ENTRY) else "already"), ""


def _act_flipper_udev(step, ctx):
    if _file_is(COMPANION_UDEV_PATH, COMPANION_UDEV_RULES):
        return "already", ""
    ensure_companion_udev_rules()
    return "done", "replug the device for the rule to apply"


def _act_sdr_recovery(step, ctx):
    changed = False
    for path, content in (
        (SDR_RECOVERY_DROPIN_PATH, SDR_RECOVERY_DROPIN),
        (f"/etc/systemd/system/{SDR_WATCHDOG_SERVICE}", SDR_WATCHDOG_SERVICE_UNIT),
        (f"/etc/systemd/system/{SDR_WATCHDOG_TIMER}", SDR_WATCHDOG_TIMER_UNIT),
    ):
        changed = _write_system_file(path, content) or changed
    subprocess.call(["systemctl", "daemon-reload"])
    subprocess.call(["systemctl", "enable", "--now", SDR_WATCHDOG_TIMER])
    return ("done" if changed else "already"), ""


def _act_old_folder(step, ctx, run=_run_text, closer=None, asking=None):
    """Clear away the folder the ops app used to be cloned into, under the same rule an uninstall uses: only
    when it holds nothing of the user's. An app running from it that asked for this install keeps its folder:
    it goes at the next install, once the app has been started again from its new place."""
    return _remove_ops_folder(step["path"], ctx, run, closer, keep_if_asked=True, asking=asking)


def _act_wiki_on(step, ctx, run=_run_text):
    user = ctx.get("user")
    if not user:
        return "failed", NO_USER_MESSAGE
    if os.path.isfile(os.path.join(step["path"], "enabled")):
        return "already", ""
    rc, _, err = run(as_user(user, ["sh", "-c", 'mkdir -p "$1" && : > "$1/enabled"', "sh", step["path"]]),
                     cwd="/", timeout=30)
    if rc != 0:
        return "failed", (err.strip().splitlines() or ["the switch could not be written"])[-1]
    return "done", "restart the ops app to see the Wiki tab"


def _act_wiki_pictures(step, ctx, run=_run_text):
    user = ctx.get("user")
    if not user:
        return "failed", NO_USER_MESSAGE
    pictures = load_wiki_pictures(step["source"])
    if pictures and not wiki_pictures_missing(pictures, step["path"]):
        return "already", ""
    # The download runs as the user, never as root: the pictures go in the user's own folder.
    rc, out, err = run(as_user(user, ["/usr/bin/python3", os.path.realpath(__file__), "--wiki-pictures", "fetch",
                                      step["source"], step["path"], step["url"]]), cwd="/", timeout=1800)
    said = [line.strip() for line in (out + "\n" + err).splitlines() if line.strip()]
    if rc != 0:
        return "failed", "; ".join(said[-3:]) or "the pictures could not be downloaded"
    return "done", (said[0] if said else "")


def _shortcut_texts(home):
    texts = []
    for _, name, _ in OPS_SHORTCUTS:
        try:
            fd = os.open(os.path.join(home, ".local", "share", "applications", name),
                         os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                texts.append(os.read(fd, 1 << 16))
            finally:
                os.close(fd)
        except OSError:
            texts.append(None)
    return texts


def _act_launchers(step, ctx, run=_run_text):
    user, home, path = ctx.get("user"), ctx.get("home"), step["path"]
    if not user or not home:
        return "failed", NO_USER_MESSAGE
    if not app_source(path):
        return "failed", "the ops app is not there, so no shortcuts were written"
    before = _shortcut_texts(home)
    python3 = "/usr/bin/python3"
    rc, _, err = run(as_user(user, [python3, "-m", "ops_gui", "--install-launchers"]), cwd=path, timeout=120)
    if rc != 0:
        last = [l for l in err.strip().splitlines() if l.strip()]
        return "failed", "the shortcuts could not be written" + (f": {last[-1].strip()}" if last else "")
    return ("already" if None not in before and _shortcut_texts(home) == before else "done"), ""


def _act_autostart(step, ctx, run=_run_text):
    user = ctx.get("user")
    if not user:
        return "failed", NO_USER_MESSAGE
    rc, out, err = run(as_user(user, ["/usr/bin/python3", INSTALLED_PATH, "--autostart"]), cwd="/", timeout=60)
    if rc != 0:
        return "failed", (err or out).strip() or "the autostart entry could not be written"
    return ("already" if "already" in out.lower() else "done"), ""


def _act_note(step, ctx):
    if step.get("skips"):
        return "skipped", step["detail"]
    return ("failed" if step.get("blocks") else "note"), step["detail"]


INSTALL_ACTIONS = {
    "core.copy": _act_core_copy,
    "core.completion": _act_core_completion,
    "core.rails-boot": _act_core_rails_boot,
    "core.sudoers": _act_core_sudoers,
    "core.meta": _act_core_meta,
    "tray.assets": _act_tray_assets,
    "tray.desktop-entry": _act_tray_desktop_entry,
    "tray.installer-entry": _act_tray_installer_entry,
    "flipper.udev": _act_flipper_udev,
    "sdr.recovery": _act_sdr_recovery,
    "autostart.entry": _act_autostart,
}
INSTALL_ACTIONS_BY_KIND = {
    "apt": _act_apt,
    "service-off": _act_service_off,
    "old-folder": _act_old_folder,
    "launchers": _act_launchers,
    "wiki-on": _act_wiki_on,
    "wiki-pictures": _act_wiki_pictures,
    "note": _act_note,
}


def step_action(step):
    return INSTALL_ACTIONS.get(step["id"]) or INSTALL_ACTIONS_BY_KIND.get(step["kind"])


def run_install(plan, ctx, say=print, action_for=step_action):
    """Carry out every step. A step that fails, or blows up, is recorded and the rest still run. Returns
    [(step, status, words)]."""
    outcomes = []
    for step in plan:
        if step["kind"] != "note":
            say(f"• {step['detail']}")
        action = action_for(step)
        try:
            if action is None:
                status, words = "failed", f"this version does not know how to do '{step['id']}'"
            else:
                status, words = action(step, ctx)
        except Exception as exc:        # one feature failing must not stop the others
            status, words = "failed", f"{type(exc).__name__}: {exc}"
        if status == "failed":
            say(f"  failed: {words}")
        elif status == "skipped":
            say(f"  not installed: {words}")
        outcomes.append((step, status, words))
    return outcomes


def feature_results(selected, outcomes):
    """[(feature, 'installed' | 'already there' | 'not installed' | 'failed', words)] in the selection's order.
    'not installed' is a feature whose package this system's sources do not have: nothing failed."""
    rows = []
    for key in selected:
        mine = [(s, st, w) for s, st, w in outcomes if key == s["feature"] or key in s.get("shared_with", ())]
        failed = [w for _, st, w in mine if st == "failed"]
        skipped = [w for _, st, w in mine if st == "skipped"]
        notes = [w for s, st, w in mine if st in ("note", "already", "done") and w and s["kind"] != "note"]
        if failed:
            rows.append((key, "failed", "; ".join(failed)))
        elif skipped:
            rows.append((key, "not installed", "; ".join(skipped)))
        elif any(st == "done" for _, st, _ in mine):
            rows.append((key, "installed", "; ".join(notes)))
        else:
            rows.append((key, "already there", "; ".join(notes)))
    return rows


def format_results(rows):
    lines = ["", "Result", "------"]
    for key, status, words in rows:
        if status in ("failed", "not installed"):
            lines.append(f"  {key:<10} {status}: {words}")
        else:
            lines.append(f"  {key:<10} {status}" + (f" ({words})" if words else ""))
    return "\n".join(lines)


def install_command(args, facts=None, state=None, execute=run_install, ask=input, isatty=None, euid=None,
                    say=print, pick=pick_features, known=None):
    """aiov2_ctl --install. Returns the exit code: 0 done (a feature whose package this system does not have is
    said so and is not a failure), 1 something failed or was refused, 2 bad arguments."""
    try:
        opts = parse_install_args(args)
        selected = select_features(opts, facts)
    except InstallError as exc:
        say(str(exc))
        return 2
    euid = os.geteuid() if euid is None else euid
    if not opts["dry_run"] and euid != 0:
        say("Install requires sudo.")
        say("Run: sudo aiov2_ctl --install")
        say("To see what it would do without changing anything: aiov2_ctl --install --dry-run")
        return 1
    interactive = sys.stdin.isatty() if isatty is None else isatty
    if opts.get("pick") and not interactive:
        say("Not a terminal, so there is nowhere to show the list. Choose with --only LIST or --without LIST; "
            "see the features with: aiov2_ctl --features")
        return 1
    if not opts["dry_run"] and not opts["yes"] and not interactive:
        say("Not a terminal, so there is nobody to ask. Run it again with --yes to install, or --dry-run to "
            "see the plan.")
        return 1

    facts = hardware_facts() if facts is None else facts
    state = install_state(euid=euid) if state is None else state
    meta = (load_install_meta() or {}) if known is None else {"features": known}
    if opts["only"] is None and not opts["all"] and not opts["without"] and not opts.get("repair"):
        selected, left = plain_selection(meta, state)
        if left:
            say("Keeping to what is installed. Not installed, and left that way: "
                + ", ".join(feature_row(k)["title"] for k in left)
                + ".\nAdd them with --only LIST, --pick or --all.\n")
    if opts.get("repair"):
        selected = repair_selection(state, meta)
        if not selected:
            say("Nothing of this tool's is installed, so there is nothing to repair. Install with: "
                "sudo aiov2_ctl --install")
            return 0
        say(format_repair(selected, state))
    if opts.get("pick"):
        # Starts from what the other flags chose (the usual set when there are none).
        selected = pick(FeaturePicker(selected), state, facts)
        if selected is None:
            say("Nothing was chosen and nothing was changed.")
            return 0
    plan = install_plan(selected, facts, state)
    say(format_plan(plan, selected))
    if opts["dry_run"]:
        say("\nDry run: nothing was changed.")
        return 0
    if not opts["yes"]:
        try:
            answer = ask("\nContinue? [y/N]: ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            say("Nothing was changed.")
            return 0

    say("")
    # What is recorded as installed afterwards: what was there before, and this selection.
    keep = set(recorded_features(meta, state)) | set(selected)
    ctx = {"user": state.get("user"), "home": state.get("home"), "source": None, "added": set(),
           "features": [k for k in feature_keys() if k in keep]}
    if execute is run_install:
        ctx["source"] = install_source()
        # Taken now, before this run copies a newer program over the installed one.
        ctx["stamp"] = file_stamp(os.path.realpath(__file__))
    outcomes = execute(plan, ctx, say)
    rows = feature_results(selected, outcomes)
    say(format_results(rows))
    failed = [key for key, status, _ in rows if status == "failed"]
    say("\nInstall complete." if not failed else
        f"\nInstall finished, but not everything went in: {', '.join(failed)}. Run it again once that is sorted.")
    left = [key for key, status, _ in rows if status == "not installed"]
    if left:
        say(f"Not installed: {', '.join(left)}. The list above says why and what to do. Run the install again "
            f"afterwards.")
    say("Open a new shell for bash completion to activate.")
    if opts["show_mesh_status"]:
        print_mesh_on_boot_status_hint("Meshtastic boot config status:")
    return 1 if failed else 0


# ---- repair: put back whatever is missing from what is installed -------------------------------------------------

MARKER_WORDS = {
    "exe": "the program itself",
    "completion": "bash completion",
    "rails-unit": "the rail boot service",
    "desktop-entry": "the tray's start-menu entry",
    "app-files": "the ops app's own files, which come with this tool's checkout",
    "gui-shortcut": "the uConsole Ops shortcut",
    "console-shortcut": "the uConsole Ops Console shortcut",
    "wiki-on": "the Wiki tab's switch",
    "wiki-pictures": "the wiki's pictures",
    "sdr-recovery": "the readsb recovery",
    "sudoers-listening": "the rules for listening without a password",
    "udev": "the Flipper rule",
    "autostart": "the tray's login entry",
}


def feature_missing(key, state):
    """What a feature should have and does not, in words. Empty when it looks complete."""
    f = feature_row(key)
    optional = OPTIONAL_PACKAGES.get(key, ())
    gone = [f"the package {p}" for p in f["apt"] if p not in optional and p not in state.get("installed", ())]
    gone += [MARKER_WORDS[m] for m in FEATURE_MARKERS.get(key, ()) if m not in state.get("present", ())]
    return gone


def features_in_place(state):
    """The features that look installed, for a machine set up before installs were recorded. A package on its
    own proves nothing (git and iw are on most machines), so a feature counts only by something this tool wrote
    for it. The app and the console count by their own shortcut, since they share their files. The features that
    are only a package (GPS, mesh, aircraft) count when the package is here and this tool is too."""
    present = state.get("present", ())
    found = []
    for key in feature_keys():
        own = [m for m in FEATURE_MARKERS.get(key, ()) if m != "app-files"]
        if own and any(m in present for m in own):
            found.append(key)
    if found:
        for key in feature_keys():
            f = feature_row(key)
            if key not in FEATURE_MARKERS and f["apt"] and all(p in state.get("installed", ()) for p in f["apt"]):
                found.append(key)
    return [k for k in feature_keys() if k in found]


def recorded_features(meta, state):
    """What has been installed: the list the installer keeps, or what is in place when there is no list yet."""
    listed = (meta or {}).get("features")
    if isinstance(listed, list):
        return [k for k in feature_keys() if k in listed]
    return features_in_place(state)


def plain_selection(meta, state):
    """What `--install` with no choice installs, and which usual features that leaves alone.

    With a record of what is installed it keeps to that, so an update never brings back something that was
    removed. With no record (a new machine, or one set up by a version from before installs were recorded) it
    is the usual set and whatever is already in place."""
    listed = (meta or {}).get("features")
    if isinstance(listed, list) and listed:
        chosen = set(resolve_features([k for k in feature_keys() if k in listed]))
        left = [k for k in default_features() if k not in chosen]
    else:
        chosen = set(resolve_features(default_features())) | set(resolve_features(features_in_place(state)))
        left = []
    return [k for k in feature_keys() if k in chosen], left


def file_stamp(path):
    """A fingerprint of a program file, or None when it cannot be read."""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def update_state(meta, installed_stamp, source_stamp=None):
    """What is left of an update: '' (nothing), 'program' or 'installer'.

    'program': the checkout holds a different aiov2_ctl from the installed one. The new code was pulled, by
    `git pull` in the checkout or by anything else that is not --update, and never put in place. Everything
    that follows the copy has not happened either: the new installer has not run, and what it keeps up to date
    (the wiki's pictures, for one) is stale.
    'installer': the installed program is not the one whose installer last ran. An updater from an older
    version pulls the new program and then installs it with the old code, so the new version's own install
    steps have not happened yet.

    No record or no installed program means nothing is installed, not that an update is half done."""
    if not isinstance(meta, dict) or not installed_stamp:
        return ""
    if source_stamp and source_stamp != installed_stamp:
        return "program"
    return "installer" if meta.get("installed_by") != installed_stamp else ""


def update_unfinished(meta, installed_stamp, source_stamp=None):
    """True when an update has something left to do (see update_state)."""
    return bool(update_state(meta, installed_stamp, source_stamp))


def source_stamp(meta):
    """The fingerprint of aiov2_ctl in the checkout this was installed from, or None when there is none."""
    try:
        return file_stamp(os.path.join(os.path.realpath(meta["repo_path"]), "aiov2_ctl.py"))
    except (KeyError, TypeError):
        return None


def pending_update():
    """'' , 'program' or 'installer' for this computer, now."""
    meta = load_install_meta()
    return update_state(meta, file_stamp(INSTALLED_PATH), source_stamp(meta))


def update_is_unfinished():
    return bool(pending_update())


UNFINISHED_NOTICE = ("aiov2_ctl was updated, but the new version's installer has not run yet, so some of it is "
                     "not set up.\nFinish the update with: sudo aiov2_ctl --install")
PENDING_NOTICE = ("A newer aiov2_ctl is in the checkout than the one installed: it was pulled but not put in place."
                  "\nPut it in place with: aiov2_ctl --update")
# Commands a person runs to look at things, where the notice is worth a line.
NOTICE_ARGS = (None, "--status", "--help", "-h", "--doctor", "--features", "--check-update", "--channel")


def unfinished_notice_for(arg, rest, isatty, unfinished):
    """The notice, or '' when this command should stay quiet: not a terminal, a command a program reads, or
    nothing to say."""
    if not isatty or arg not in NOTICE_ARGS or "--json" in rest:
        return ""
    state = unfinished()
    if state == "program":
        return PENDING_NOTICE
    return UNFINISHED_NOTICE if state else ""


def update_install_steps(python3, installed):
    """The two installs an update is made of. The first is the installed (old) program copying the new one into
    place; the second is the new program running its own installer, which keeps to the features already
    installed. Both run the root-owned installed copy, never the checkout."""
    return [[python3, installed, "--install", "--only", "core", "--yes"],
            [python3, installed, "--install", "--yes", "--show-mesh-status"]]


def update_commands(python3, installed, two_steps, lead=("sudo",)):
    """What an update runs as root once the new code is pulled: one command, so root is asked for once. With
    two_steps it is --update-install, which runs both installs; without, it is the new program's installer
    alone (finishing an update an older updater left half done)."""
    if two_steps:
        return [[*lead, python3, installed, "--update-install"]]
    return [[*lead, *update_install_steps(python3, installed)[1]]]


def root_lead(isatty, sudo_without_password, pkexec_path):
    """How to start a command as root: sudo on a terminal (it can ask there); otherwise sudo when it needs no
    password, or the desktop's password box. None when there is no way from here."""
    if isatty:
        return ["sudo"]
    how = elevation(sudo_without_password(), pkexec_path)
    return {"sudo": ["sudo", "-n"], "pkexec": [pkexec_path]}.get(how)


def update_install_command(run=subprocess.call, say=print, euid=None, trusted=None):
    """--update-install: the root half of an update, both installs one after the other. Each is a fresh run of
    the installed program, so the second one is the new version."""
    if (os.geteuid() if euid is None else euid) != 0:
        say("This is the root half of an update. Run: aiov2_ctl --update")
        return 1
    if not (is_trusted_file(INSTALLED_PATH) if trusted is None else trusted):
        say(f"{INSTALLED_PATH} is not a root-owned program, so it was not run.")
        return 1
    python3 = shutil.which("python3") or "/usr/bin/python3"
    steps = update_install_steps(python3, INSTALLED_PATH)
    for i, cmd in enumerate(steps, 1):
        say(f"\nStep {i} of {len(steps)}: "
            + ("putting the new program in place" if i == 1 else "running the new version's installer") + "\n")
        sys.stdout.flush()
        if run(cmd) != 0:
            say("\nThe update stopped here. Finish it with: sudo aiov2_ctl --install")
            return 1
    return 0


def finish_update(two_steps, run=subprocess.call, trusted=None, say=print, lead=None):
    """Run the root half of an update. Returns an exit code."""
    trusted = is_trusted_file(INSTALLED_PATH) if trusted is None else trusted
    if not trusted:
        # Not installed yet, so there is no root-owned copy to run: install from here, as before.
        rerun_with_sudo(["--install", "--yes", "--show-mesh-status"], allow_untrusted=True)
        return 0
    if lead is None:
        lead = root_lead(sys.stdin.isatty(), lambda: _run_text(["sudo", "-n", "true"], timeout=10)[0] == 0,
                         shutil.which("pkexec"))
    if lead is None:
        say("\nThere is no way to get root from here. Run this in a terminal: aiov2_ctl --update")
        return 1
    python3 = shutil.which("python3") or "/usr/bin/python3"
    sys.stdout.flush()
    for cmd in update_commands(python3, INSTALLED_PATH, two_steps, lead):
        if run(cmd) != 0:
            say("\nThe update stopped here. Finish it with: sudo aiov2_ctl --install")
            return 1
    return 0


# ---- channels -------------------------------------------------------------------------------------------------
# A channel is a branch of the repository this was installed from. Stable is main; beta is the branch where
# changes are tried first. Switching checks out the other branch and then updates in the usual way, so it keeps
# to the installed features. Going back to stable installs an older version: what beta added is not removed.

CHANNELS = (("stable", "main"), ("beta", "beta"))
CHANNEL_USAGE = ("Usage: aiov2_ctl --channel                 show the channel and what is waiting\n"
                 "       aiov2_ctl --channel stable|beta [--yes]   switch, then update\n"
                 "       aiov2_ctl --channel --json")


def channel_of(branch):
    """'stable' or 'beta' for a branch, or None for a branch that is not a channel (someone's own work)."""
    for name, b in CHANNELS:
        if b == branch:
            return name
    return None


def channel_branch(name):
    return dict(CHANNELS).get(name)


def update_branch(meta, checkout_branch):
    """The branch an update follows: the one the checkout is on; failing that, the recorded one; else main."""
    if checkout_branch and checkout_branch != "HEAD":
        return checkout_branch
    return (meta or {}).get("branch") or "main"


def remote_heads(text):
    """The branch names in the output of `git ls-remote --heads`."""
    heads = set()
    for line in (text or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].startswith("refs/heads/"):
            heads.add(parts[1][len("refs/heads/"):])
    return heads


def channel_report(branch, heads, incoming, how, error=""):
    """The channels as data, for the ops app. `heads` is the branches the repository has, or None when it could
    not be reached; `incoming` is the waiting changes, one line each, or None when not known."""
    return {
        "version": 1,
        "channel": channel_of(branch),
        "branch": branch,
        "channels": [{"name": name, "branch": b, "available": heads is not None and b in heads,
                      "current": b == branch} for name, b in CHANNELS],
        "incoming": None if incoming is None else list(incoming),
        "root": how,
        "error": error,
    }


def format_channel(report):
    name = report["channel"]
    lines = [f"Channel: {name} (branch {report['branch']})" if name
             else f"Channel: none. This checkout is on its own branch, {report['branch']}."]
    if report["error"]:
        lines.append(report["error"])
    elif report["incoming"] is not None:
        n = len(report["incoming"])
        lines.append("Up to date." if n == 0 else f"{n} change{'s' if n != 1 else ''} waiting. "
                     "Install with: aiov2_ctl --update")
    for ch in report["channels"]:
        if ch["current"]:
            continue
        if ch["available"]:
            lines.append(f"Switch to {ch['name']} with: aiov2_ctl --channel {ch['name']}")
        elif not report["error"]:
            lines.append(f"This repository has no {ch['name']} channel.")
    return "\n".join(lines)


def switch_problem(name, current, heads, dirty):
    """Why a switch to a channel cannot go ahead, or ''."""
    target = channel_branch(name)
    if target is None:
        return f"Unknown channel: {name}. The channels are: " + ", ".join(n for n, _ in CHANNELS) + "."
    if target == current:
        return f"Already on the {name} channel. Update with: aiov2_ctl --update"
    if heads is None:
        return "The repository could not be reached, so nothing was changed."
    if target not in heads:
        return f"This repository has no {name} channel yet, so nothing was changed."
    if dirty:
        return ("The checkout this was installed from has changes of your own, so it was left as it is. "
                "Commit or stash them, then switch.")
    return ""


def switch_summary(name, incoming, leaving):
    """What a switch means, before it is confirmed."""
    lines = [f"Switching to the {name} channel."]
    if incoming:
        lines.append(f"It brings {len(incoming)} change{'s' if len(incoming) != 1 else ''}:")
        lines += ["  " + line for line in incoming[:15]]
        if len(incoming) > 15:
            lines.append(f"  and {len(incoming) - 15} more")
    if leaving:
        lines.append(f"It leaves {leaving} newer change{'s' if leaving != 1 else ''} behind: this installs an "
                     "older version. What the newer one set up is not removed.")
    if name == "beta":
        lines.append("Beta gets changes first, before they are well tested.")
    return "\n".join(lines)


def _update_source():
    """(repo, branch) to update from, or (None, None) after saying why not."""
    meta = load_install_meta()
    if not meta or "repo_path" not in meta:
        print("No install metadata found.")
        print("Reinstall from the original git checkout.")
        return None, None
    repo = os.path.realpath(meta["repo_path"])
    if not os.path.isdir(repo):
        print(f"Source repo not found: {repo}")
        print("Reinstall required.")
        return None, None
    return repo, update_branch(meta, get_git_branch(repo))


def channel_look(repo, branch, run=_run_text):
    """(heads or None, incoming lines or None, error) from the repository. Fetches; changes no file."""
    rc, out, _ = run(["git", "ls-remote", "--heads", "origin"], cwd=repo, timeout=30)
    if rc != 0:
        return None, None, "The repository could not be reached just now."
    heads = remote_heads(out)
    if branch not in heads:
        return heads, None, ""
    if run(["git", "fetch", "--quiet", "origin", branch], cwd=repo, timeout=120)[0] != 0:
        return heads, None, "The repository could not be reached just now."
    rc, out, _ = run(["git", "log", "--oneline", "--no-decorate", f"HEAD..origin/{branch}"], cwd=repo, timeout=60)
    return heads, (out.strip().splitlines() if rc == 0 else None), ""


def channel_command(args=()):
    args = list(args or ())
    yes = any(a in ("--yes", "-y") for a in args)
    rest = [a for a in args if a not in ("--yes", "-y")]
    if rest in ([], ["--json"]):
        if os.geteuid() == 0:
            print("Run this as yourself, not with sudo.")
            return 1
        repo, branch = _update_source()
        if not repo:
            return 1
        heads, incoming, error = channel_look(repo, branch)
        how = None
        if rest:
            how = elevation(_run_text(["sudo", "-n", "true"], timeout=10)[0] == 0, shutil.which("pkexec"))
        report = channel_report(branch, heads, incoming, how, error)
        print(json.dumps(report, indent=1) if rest else format_channel(report))
        return 0
    if len(rest) != 1 or rest[0].startswith("-"):
        print(CHANNEL_USAGE)
        return 2
    return switch_channel(rest[0], confirmed=yes)


def switch_channel(name, confirmed=False):
    if os.geteuid() == 0:
        print("Do not switch channel with sudo.")
        print(f"Run: aiov2_ctl --channel {name}")
        return 1
    repo, current = _update_source()
    if not repo:
        return 1
    rc, out, _ = _run_text(["git", "ls-remote", "--heads", "origin"], cwd=repo, timeout=30)
    heads = remote_heads(out) if rc == 0 else None
    rc, out, _ = _run_text(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repo, timeout=60)
    problem = switch_problem(name, current, heads, rc != 0 or bool(out.strip()))
    if problem:
        print(problem)
        return 1
    target = channel_branch(name)
    if not run_cmd(["git", "fetch", "--quiet", "origin", target], cwd=repo):
        print("Git fetch failed. Nothing was changed.")
        return 1
    incoming = _run_text(["git", "log", "--oneline", "--no-decorate", f"HEAD..origin/{target}"],
                         cwd=repo, timeout=60)[1].strip().splitlines()
    leaving = _run_text(["git", "rev-list", "--count", f"origin/{target}..HEAD"], cwd=repo, timeout=60)[1].strip()
    print(switch_summary(name, incoming, int(leaving) if leaving.isdigit() else 0))

    if load_config().get("require_signed_updates"):
        if not run_cmd(["git", "verify-commit", f"origin/{target}"], cwd=repo):
            print("\nrequire_signed_updates is on and the newest commit is not validly signed. Not switching.")
            return 1

    if not confirmed:
        if not sys.stdin.isatty():
            print("\nNot interactive. Re-run with --yes to switch and install as root.")
            return 1
        resp = input("\nThis will run that version's code as root to install it. Continue? [y/N]: ")
        if resp.strip().lower() not in ("y", "yes"):
            print("Left on the channel it was on.")
            return 0

    def back(why):
        run_cmd(["git", "checkout", "--quiet", current], cwd=repo)
        print(f"\n{why} Nothing was changed: still on {channel_of(current) or current}.")
        return 1

    local = _run_text(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{target}"], cwd=repo)[0] == 0
    checkout = (["git", "checkout", "--quiet", target] if local
                else ["git", "checkout", "--quiet", "-b", target, "--track", f"origin/{target}"])
    if not run_cmd(checkout, cwd=repo):
        return back("The checkout could not change branch.")
    if local and not run_cmd(["git", "merge", "--quiet", "--ff-only", f"origin/{target}"], cwd=repo):
        return back(f"Your copy of {target} has gone its own way.")

    print("\nInstalling that version…\n")
    report_and_disable_mesh_autostart_if_default("Meshtastic boot config status:")
    before = file_stamp(INSTALLED_PATH)
    said = []
    code = finish_update(two_steps=True, say=said.append)
    if code != 0 and file_stamp(INSTALLED_PATH) == before:
        return back("The install did not start.")
    for line in said:
        print(line)
    if code == 0:
        print(f"\nNow on the {name} channel.")
    return code


# ---- the tray's offer to finish an update ---------------------------------------------------------------------
FINISH_ASKED_PATH = os.path.expanduser("~/.config/aiov2_ctl/finish_asked")
FINISH_PROMPT = ("aiov2_ctl was updated, and the new version has more to set up: the ops app, its console and "
                 "the installer's menu entries.\n\nFinish now opens the installer. It shows what it will do and "
                 "asks before it changes anything.")


def finish_prompt_due(unfinished, stamp, asked):
    """True when the tray should offer to finish the update: it is unfinished, and this version has not asked
    yet (Later is remembered, so the box does not come back at every login; the menu item stays)."""
    return bool(unfinished and stamp and (asked or "").strip() != stamp)


def repair_selection(state, meta=None):
    """The features to repair: every one that was installed, with what they need."""
    found = recorded_features(meta, state)
    return resolve_features(found) if found else []


def format_repair(selected, state):
    lines = ["Repair goes over everything that is installed and puts back what is missing or out of date.", ""]
    for key in selected:
        gone = feature_missing(key, state)
        lines.append(f"  {key:<10} " + ("missing: " + ", ".join(gone) if gone else "looks complete"))
    lines.append("")
    return "\n".join(lines)


# ---- uninstall ------------------------------------------------------------------------------------------------
# Removes what this tool wrote and nothing else. It never removes a package, never switches a rail, never stops
# a radio service, and never deletes your saved data. The old ops app folder goes only when it holds nothing of
# yours: no changed files, nothing unpushed, nothing stashed.

UNINSTALL_USAGE = (
    "Usage: sudo aiov2_ctl --uninstall [--only LIST] [--dry-run] [--yes]\n"
    "       With no LIST everything this tool installed is removed. LIST is feature keys separated by commas;\n"
    "       whatever cannot work without them is removed too. See them with: aiov2_ctl --features"
)
SUDO_LINK_DIRS = ("/usr/bin", "/usr/sbin")


def parse_uninstall_args(args):
    opts = {"only": None, "dry_run": False, "yes": False}
    args = list(args or [])
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--only":
            if i + 1 >= len(args):
                raise InstallError("--only needs a list of features.\n" + UNINSTALL_USAGE)
            opts["only"] = (opts["only"] or []) + _key_list(args[i + 1])
            i += 1
        elif arg == "--dry-run":
            opts["dry_run"] = True
        elif arg in ("--yes", "-y"):
            opts["yes"] = True
        else:
            raise InstallError(f"Unknown option for --uninstall: {arg}\n" + UNINSTALL_USAGE)
        i += 1
    return opts


def uninstall_selection(only=None):
    """The features to remove, last installed first: everything, or the ones named with whatever needs them."""
    keys = feature_keys()
    if only is None:
        gone = set(keys)
    else:
        gone = set(only)
        changed = True
        while changed:
            changed = False
            for key in keys:
                if key not in gone and gone & set(feature_row(key)["needs"]):
                    gone.add(key)
                    changed = True
    return [k for k in reversed(keys) if k in gone]


def _unstep(feature_key, name, kind, detail, root, **extra):
    step = {"id": f"un.{feature_key}.{name}", "feature": feature_key, "kind": kind, "detail": detail, "root": root}
    step.update(extra)
    return step


def uninstall_plan(selected, state):
    """The steps that remove a selection, as plain data. Pure: reads nothing and changes nothing."""
    state = state or {}
    selected = list(selected)
    user, home = state.get("user"), state.get("home")
    apps = os.path.join(home, ".local", "share", "applications") if home else None
    plan = []
    for key in selected:
        if key == "autostart":
            if home:
                plan.append(_unstep(key, "entry", "user-file",
                                    f"Remove the tray's login entry, as {user}.", False,
                                    paths=(os.path.join(home, ".config", "autostart", "aiov2_ctl.desktop"),)))
            else:
                plan.append(_unstep(key, "no-user", "note", NO_USER_MESSAGE.replace("the ops app", "the tray"),
                                    False, blocks=True))
        elif key in ("wiki", "wikipics"):
            if not home:
                plan.append(_unstep(key, "no-user", "note", NO_USER_MESSAGE.replace("the ops app", "the wiki"),
                                    False, blocks=True))
            elif key == "wiki":
                plan.append(_unstep(key, "off", "user-file",
                                    f"Switch off the Wiki tab in the ops app, as {user}. The pages stay in "
                                    f"the checkout.", False,
                                    paths=(os.path.join(wiki_home(home), "enabled"),)))
            else:
                gone = os.path.join(wiki_home(home), "img")
                plan.append(_unstep(key, "pictures", "wiki-pictures-gone",
                                    f"Remove the downloaded wiki pictures in {gone}, as {user}. The package "
                                    f"that shows them stays installed.", False, path=gone))
        elif key == "flipper":
            plan.append(_unstep(key, "udev", "udev",
                                f"Remove {COMPANION_UDEV_PATH}. After that only root can open a Flipper or ESP32 "
                                f"board on USB.", True))
        elif key == "listening":
            if "core" not in selected:
                plan.append(_unstep(key, "sudoers", "sudoers-quiet",
                                    f"Rewrite {SUDOERS_PATH} without the listening rules: --wifi-listen, "
                                    f"--wifi-survey and --bt-scan will ask for a password again.", True))
        elif key == "sdr":
            plan.append(_unstep(key, "recovery", "units",
                                f"Switch off the once-a-minute SDR check ({SDR_WATCHDOG_TIMER}) and remove it "
                                f"and the readsb recovery. The SDR rail and readsb are left as they are.", True,
                                units=(SDR_WATCHDOG_TIMER,),
                                paths=(f"/etc/systemd/system/{SDR_WATCHDOG_TIMER}",
                                       f"/etc/systemd/system/{SDR_WATCHDOG_SERVICE}", SDR_RECOVERY_DROPIN_PATH)))
        elif key in ("app", "console"):
            which = "gui" if key == "app" else "console"
            name = next(n for k, n, _ in OPS_SHORTCUTS if k == which)
            title = next(t for k, _, t in OPS_SHORTCUTS if k == which)
            if not home:
                plan.append(_unstep(key, "no-user", "note", NO_USER_MESSAGE, False, blocks=True))
                continue
            plan.append(_unstep(key, "shortcut", "user-file",
                                f"Remove the start-menu shortcut '{title}', as {user}.", False,
                                paths=(os.path.join(apps, name),)))
            if key == "app":
                plan.append(_unstep(key, "startup", "user-file",
                                    f"Remove the file that opens the app in full screen at login, as {user}, if "
                                    f"Settings > General put one there.", False,
                                    paths=(os.path.join(home, ".config", "autostart", OPS_STARTUP_FILE),)))
            src = state.get("app_source")
            if src:
                what = "uConsole Ops app" if key == "app" else "Ops console"
                plan.append(_unstep(key, "close", "close-ops",
                                    f"Close the {what} if it is running. If it is what asked for this, it is "
                                    f"left open.", False, path=src, which=which))
            other = "console" if key == "app" else "app"
            last = key == [k for k in selected if k in ("app", "console")][-1]
            if state.get("old_checkout") and other in selected and last:
                old = ops_checkout_path(home)
                plan.append(_unstep(key, "checkout", "checkout",
                                    f"Remove {old}, as {user}: the folder the ops app used to be kept in. Only "
                                    f"if it holds nothing of yours: no changed files, nothing unpushed, nothing "
                                    f"stashed. Otherwise it is left and you are told why. An ops app or "
                                    f"console running from it is closed first.", False,
                                    path=old, shared_with=(other,)))
            if last:
                plan.append(_unstep(key, "files-kept", "note",
                                    "The app's own files are part of this tool's checkout and stay there. Your "
                                    "logs and settings in ~/.local/state/ops_gui are never touched.", False))
        elif key == "tray":
            plan.append(_unstep(key, "close", "close-tray",
                                "Close the tray if it is running. If the tray is what asked for this, it is "
                                "left open: quit it yourself afterwards.", True))
            plan.append(_unstep(key, "desktop-entry", "files",
                                f"Remove the start-menu entries for the tray and the installer, and the "
                                f"tray's icons.", True,
                                paths=(SYSTEM_DESKTOP_PATH, INSTALLER_DESKTOP_PATH), trees=(os.path.join(ASSET_BASE, "img"),)))
        elif key == "core":
            plan.append(_unstep(key, "sudoers", "files",
                                f"Remove {SUDOERS_PATH}: rails and services will ask for a password again.", True,
                                paths=(SUDOERS_PATH,)))
            plan.append(_unstep(key, "rails-boot", "units",
                                f"Switch off and remove {RAILS_BOOT_SERVICE}. Rails are no longer set at boot; "
                                f"none is switched now.", True,
                                units=(RAILS_BOOT_SERVICE,), paths=(RAILS_BOOT_SERVICE_PATH,)))
            plan.append(_unstep(key, "files", "files",
                                f"Remove bash completion and the note of where this was installed from.", True,
                                paths=(COMPLETION_PATH, INSTALL_META_PATH)))
            plan.append(_unstep(key, "exe", "exe",
                                f"Remove the program {INSTALLED_PATH} and any link to it where sudo looks. "
                                f"Your saved rail settings in {CONFIG_PATH} are left.", True))
        elif key in PACKAGE_UNITS:
            pkg, units, _ = PACKAGE_UNITS[key]
            plan.append(_unstep(key, "kept", "note",
                                f"Nothing of this tool's to remove. The {pkg} package and its service "
                                f"({', '.join(units)}) are left exactly as they are.", False))
    return plan


def uninstall_packages_left(selected):
    seen = []
    for key in reversed(selected):
        for pkg in feature_row(key)["apt"]:
            if pkg not in seen:
                seen.append(pkg)
    return seen


def format_uninstall_plan(plan, selected):
    lines = ["This will remove: " + ", ".join(selected), ""]
    current = None
    n = 0
    for step in plan:
        if step["feature"] != current:
            current = step["feature"]
            lines.append(f"{feature_row(current)['title']} ({current})")
        if step["kind"] == "note":
            lines.append(f"      note: {step['detail']}")
        else:
            n += 1
            lines.append(f"  {n:>2}. {step['detail']}" + ("" if step["root"] else "  [as you, not root]"))
    lines.append("")
    packages = uninstall_packages_left(selected)
    if packages:
        lines.append("No package is removed. These stay installed, since other things may use them: "
                     + ", ".join(packages) + ".")
    lines.append("No rail is switched and no radio service is stopped or started.")
    if "autostart" in selected and "core" in selected:
        lines.append("The tray's login entry is not part of the usual set, so installing again does not bring it "
                     "back. Afterwards, run: aiov2_ctl --autostart")
    return "\n".join(lines)


def process_table(proc="/proc"):
    """[(pid, uid, parent, argv, cwd)] for each process that can be read. Reads only."""
    rows = []
    try:
        names = os.listdir(proc)
    except OSError:
        return rows
    for name in names:
        if not name.isdigit():
            continue
        base = os.path.join(proc, name)
        try:
            uid = os.stat(base).st_uid
            with open(os.path.join(base, "cmdline"), "rb") as f:
                argv = [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
            with open(os.path.join(base, "stat")) as f:
                parent = int(f.read().rsplit(")", 1)[1].split()[1])
            cwd = os.readlink(os.path.join(base, "cwd"))
        except (OSError, ValueError, IndexError):
            continue
        rows.append((int(name), uid, parent, argv, cwd))
    return rows


def is_tray_process(argv, cwd):
    return INSTALLED_PATH in argv[:2] and "--gui" in argv


def runs_ops_from(path, which=None):
    """A test for process_table rows: the ops app or the ops console, started in `path`. Not a shell or an
    editor that merely has the folder open. `which` narrows it to the app ('gui') or the console."""
    def test(argv, cwd):
        if cwd != path:
            return False
        if not argv or not os.path.basename(argv[0]).startswith("python"):
            return False
        app = any(a == "-m" and b == "ops_gui" for a, b in zip(argv, argv[1:]))
        console = any(os.path.basename(a) == "ops.py" for a in argv[1:])
        return {"gui": app, "console": console}.get(which, app or console)
    return test


def processes_to_close(table, uid, own_pid, wanted):
    """(pids to close, asked) for the user's processes that `wanted(argv, cwd)` picks. A picked process that is
    this one or one that started it is not closed, since closing it would stop the removal half way: asked is
    True then."""
    parents = {pid: parent for pid, _, parent, _, _ in table}
    line, pid = set(), own_pid
    while pid and pid not in line:
        line.add(pid)
        pid = parents.get(pid)
    picked = [pid for pid, owner, _, argv, cwd in table if owner == uid and wanted(argv, cwd)]
    return [p for p in picked if p not in line], any(p in line for p in picked)


def close_processes(pids, kill=os.kill, alive=None, sleep=time.sleep, wait=3.0):
    """Ask each to finish (SIGTERM, never SIGKILL) and wait a moment for them. Returns how many went."""
    alive = alive or (lambda pid: os.path.exists(f"/proc/{pid}"))
    asked = []
    for pid in pids:
        try:
            kill(pid, 15)
            asked.append(pid)
        except OSError:
            pass
    waited = 0.0
    while waited < wait and any(alive(p) for p in asked):
        sleep(0.2)
        waited += 0.2
    return len([p for p in asked if not alive(p)])


def _close_users(user, wanted, table=None, close=close_processes):
    """(how many closed, asked) for the user's processes `wanted` picks."""
    try:
        uid = pwd.getpwnam(user).pw_uid
    except KeyError:
        return 0, False
    pids, asked = processes_to_close(process_table() if table is None else table, uid, os.getpid(), wanted)
    return (close(pids) if pids else 0), asked


def _un_close_tray(step, ctx, table=None, close=close_processes):
    user = ctx.get("user")
    if not user:
        return "already", ""
    closed, asked = _close_users(user, is_tray_process, table, close)
    if asked:
        return "note", "the tray asked for this, so it is still open: quit it yourself"
    return ("done" if closed else "already"), ""


def _remove_paths(paths):
    """Remove each file that is there. True if any was."""
    removed = False
    for path in paths:
        try:
            os.remove(path)
            removed = True
        except FileNotFoundError:
            pass
    return removed


def _un_files(step, ctx):
    removed = _remove_paths(step.get("paths", ()))
    for tree in step.get("trees", ()):
        if os.path.isdir(tree) and not os.path.islink(tree):
            shutil.rmtree(tree)
            removed = True
    return ("done" if removed else "already"), ""


def _un_user_file(step, ctx, run=_run_text):
    user = ctx.get("user")
    if not user:
        return "failed", NO_USER_MESSAGE
    there = [p for p in step["paths"] if os.path.lexists(p)]
    if not there:
        return "already", ""
    rc, _, err = run(as_user(user, ["rm", "-f", "--", *there]), cwd="/", timeout=30)
    if rc != 0:
        return "failed", (err.strip().splitlines() or ["could not be removed"])[-1]
    return "done", ""


def _un_wiki_pictures(step, ctx, run=_run_text):
    user, home = ctx.get("user"), ctx.get("home")
    if not user or not home:
        return "failed", NO_USER_MESSAGE
    if step["path"] != os.path.join(wiki_home(home), "img"):
        return "failed", f"{step['path']} is not where the wiki's pictures go; nothing was removed"
    if not os.path.lexists(step["path"]):
        return "already", ""
    rc, _, err = run(as_user(user, ["rm", "-rf", "--", step["path"]]), cwd="/", timeout=60)
    if rc != 0:
        return "failed", (err.strip().splitlines() or ["could not be removed"])[-1]
    return "done", ""


def _un_units(step, ctx):
    there = [p for p in step["paths"] if os.path.lexists(p)]
    if not there:
        return "already", ""
    for unit in step.get("units", ()):
        subprocess.call(["systemctl", "disable", "--now", unit], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _remove_paths(there)
    subprocess.call(["systemctl", "daemon-reload"])
    return "done", ""


def _un_udev(step, ctx):
    if not os.path.lexists(COMPANION_UDEV_PATH):
        return "already", ""
    if not _file_is(COMPANION_UDEV_PATH, COMPANION_UDEV_RULES):
        return "note", f"{COMPANION_UDEV_PATH} is not as this tool wrote it, so it was left alone"
    os.remove(COMPANION_UDEV_PATH)
    subprocess.call(["udevadm", "control", "--reload-rules"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return "done", ""


def _un_sudoers_quiet(step, ctx):
    if not os.path.lexists(SUDOERS_PATH):
        return "already", ""
    with open(SUDOERS_PATH) as f:
        if "--wifi-listen" not in f.read():
            return "already", ""
    if setup_sudoers(listening=False) != 0:
        return "failed", "the rules could not be rewritten; they are as they were"
    return "done", ""


def _un_exe(step, ctx):
    removed = False
    for folder in SUDO_LINK_DIRS:
        link = os.path.join(folder, os.path.basename(INSTALLED_PATH))
        if os.path.islink(link) and os.path.realpath(link) == os.path.realpath(INSTALLED_PATH):
            os.remove(link)
            removed = True
    removed = _remove_paths(compiled_copies(INSTALLED_PATH)) or removed
    try:
        os.rmdir(os.path.join(os.path.dirname(INSTALLED_PATH), "__pycache__"))     # only when it is empty
    except OSError:
        pass
    return ("done" if _remove_paths((INSTALLED_PATH,)) or removed else "already"), ""


def compiled_copies(path, listdir=os.listdir):
    """The copies Python compiled of the program at `path` and left in __pycache__ beside it."""
    cache = os.path.join(os.path.dirname(path), "__pycache__")
    name = os.path.basename(path)
    try:
        names = listdir(cache)
    except OSError:
        return []
    return [os.path.join(cache, n) for n in sorted(names)
            if n.endswith(".pyc") and (n.startswith(name + ".") or n.startswith(name + "cpython-"))]


def checkout_holds_nothing(path, user, run=_run_text):
    """(True, '') when the ops checkout has nothing of the user's in it, else (False, why)."""
    def git(*args):
        return run(as_user(user, ["git", "-C", path, *args]), cwd="/", timeout=60)
    rc, out, _ = git("status", "--porcelain")
    if rc != 0:
        return False, "git could not read it"
    if out.strip():
        return False, "it has files you changed or added"
    rc, out, _ = git("stash", "list")
    if rc != 0 or out.strip():
        return False, "it has stashed work"
    rc, out, _ = git("log", "--branches", "--not", "--remotes", "--oneline")
    if rc != 0 or out.strip():
        return False, "it has commits that were never pushed"
    return True, ""


def _user_asked(user, wanted, table=None):
    """True when one of the user's processes that `wanted` picks is what started this run. Closes nothing."""
    try:
        uid = pwd.getpwnam(user).pw_uid
    except KeyError:
        return False
    return processes_to_close(process_table() if table is None else table, uid, os.getpid(), wanted)[1]


def _remove_ops_folder(path, ctx, run=_run_text, closer=None, keep_if_asked=False, asking=None):
    """Remove the folder the ops app used to be cloned into, and nothing else, when it holds nothing of the
    user's. With `keep_if_asked`, an app running from it that asked for this keeps its folder."""
    user, home = ctx.get("user"), ctx.get("home")
    if not user or not home:
        return "failed", NO_USER_MESSAGE
    if path != ops_checkout_path(home):
        return "failed", f"{path} is not where the ops app used to go; nothing was removed"
    kind = checkout_state(path, user, run)
    if kind == "missing":
        return "already", ""
    if kind != "ours":
        return "note", f"{path} is not a checkout of {OPS_REPO_NAME}, so it was left alone"
    empty, why = checkout_holds_nothing(path, user, run)
    if not empty:
        return "note", f"{path} was left: {why}. Delete it yourself if you want it gone"
    kept = (f"{path} was left for now: the app that asked for this is running from it. Start the app again "
            f"from the start menu; the folder goes at the next install or update")
    if keep_if_asked and (asking or _user_asked)(user, runs_ops_from(path)):
        return "note", kept                      # decided before anything is closed
    _, asked = (closer or _close_users)(user, runs_ops_from(path))
    if asked and keep_if_asked:
        return "note", kept
    rc, _, err = run(as_user(user, ["rm", "-rf", "--", path]), cwd="/", timeout=120)
    if rc != 0:
        return "failed", (err.strip().splitlines() or [f"{path} could not be removed"])[-1]
    return "done", ("the ops app asked for this, so it is still open: close it yourself" if asked else "")


def _un_checkout(step, ctx, run=_run_text, closer=None):
    return _remove_ops_folder(step["path"], ctx, run, closer)


def _un_close_ops(step, ctx, closer=None):
    user = ctx.get("user")
    if not user:
        return "already", ""
    closed, asked = (closer or _close_users)(user, runs_ops_from(step["path"], step.get("which")))
    if asked:
        return "note", "it asked for this, so it is still open: close it yourself"
    return ("done" if closed else "already"), ""


def _un_note(step, ctx):
    return ("failed" if step.get("blocks") else "note"), ("" if not step.get("blocks") else step["detail"])


UNINSTALL_ACTIONS = {
    "files": _un_files,
    "user-file": _un_user_file,
    "units": _un_units,
    "udev": _un_udev,
    "sudoers-quiet": _un_sudoers_quiet,
    "exe": _un_exe,
    "close-tray": _un_close_tray,
    "close-ops": _un_close_ops,
    "wiki-pictures-gone": _un_wiki_pictures,
    "checkout": _un_checkout,
    "note": _un_note,
}


def uninstall_action(step):
    return UNINSTALL_ACTIONS.get(step["kind"])


def uninstall_results(selected, outcomes):
    """[(feature, 'removed' | 'was not there' | 'nothing to remove' | 'failed', words)] in the selection's order."""
    rows = []
    for key in selected:
        mine = [(s, st, w) for s, st, w in outcomes if key == s["feature"] or key in s.get("shared_with", ())]
        failed = [w for _, st, w in mine if st == "failed"]
        notes = [w for s, st, w in mine if st in ("note", "done") and w]
        real = [st for s, st, _ in mine if s["kind"] != "note"]
        if failed:
            rows.append((key, "failed", "; ".join(failed)))
        elif "done" in real:
            rows.append((key, "removed", "; ".join(notes)))
        elif real:
            rows.append((key, "was not there", "; ".join(notes)))
        else:
            rows.append((key, "nothing to remove", "; ".join(notes)))
    return rows


def forget_features(removed, state):
    """Take features out of the installer's record, so repair does not put them back."""
    meta = load_install_meta() or {}
    kept = [k for k in recorded_features(meta, state) if k not in removed]
    meta["features"] = kept
    _write_system_file(INSTALL_META_PATH, json.dumps(meta, indent=2))


def uninstall_command(args, state=None, execute=run_install, ask=input, isatty=None, euid=None, say=print,
                      forget=forget_features):
    """aiov2_ctl --uninstall. Returns the exit code: 0 done, 1 something failed or was refused, 2 bad arguments."""
    try:
        opts = parse_uninstall_args(args)
        selected = uninstall_selection(opts["only"])
    except InstallError as exc:
        say(str(exc))
        return 2
    euid = os.geteuid() if euid is None else euid
    if not opts["dry_run"] and euid != 0:
        say("Uninstall requires sudo.")
        say("Run: sudo aiov2_ctl --uninstall")
        say("To see what it would remove without changing anything: aiov2_ctl --uninstall --dry-run")
        return 1
    interactive = sys.stdin.isatty() if isatty is None else isatty
    if not opts["dry_run"] and not opts["yes"] and not interactive:
        say("Not a terminal, so there is nobody to ask. Run it again with --yes to remove, or --dry-run to see "
            "the plan.")
        return 1
    state = install_state(euid=euid) if state is None else state
    plan = uninstall_plan(selected, state)
    say(format_uninstall_plan(plan, selected))
    if opts["dry_run"]:
        say("\nDry run: nothing was changed.")
        return 0
    if not opts["yes"]:
        try:
            answer = ask("\nRemove these? [y/N]: ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            say("Nothing was changed.")
            return 0
    say("")
    ctx = {"user": state.get("user"), "home": state.get("home")}
    outcomes = execute(plan, ctx, say, uninstall_action) if execute is run_install else execute(plan, ctx, say)
    rows = uninstall_results(selected, outcomes)
    say(format_results(rows))
    failed = [key for key, status, _ in rows if status == "failed"]
    if "core" not in selected:
        # With the controller gone its record went with it; otherwise note what is no longer installed. The
        # record is read against the state from before the removal, so a machine with no record yet gets one.
        forget([key for key in selected if key not in failed], state)
    say("\nUninstall complete." if not failed else
        f"\nUninstall finished, but not everything was removed: {', '.join(failed)}.")
    if "core" not in selected:
        say("Put anything back with: sudo aiov2_ctl --install --only LIST")
    return 1 if failed else 0


# ---- apply: make what is installed exactly a list -----------------------------------------------------------------

APPLY_USAGE = (
    "Usage: sudo aiov2_ctl --apply --keep LIST [--dry-run] [--yes]\n"
    "       Makes the installed features exactly LIST (feature keys separated by commas, and whatever they\n"
    "       need): installed features not in it are removed, then LIST is installed or brought up to date."
)


def parse_apply_args(args):
    opts = {"keep": None, "dry_run": False, "yes": False}
    args = list(args or [])
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--keep":
            if i + 1 >= len(args):
                raise InstallError("--keep needs a list of features.\n" + APPLY_USAGE)
            opts["keep"] = (opts["keep"] or []) + _key_list(args[i + 1])
            i += 1
        elif arg == "--dry-run":
            opts["dry_run"] = True
        elif arg in ("--yes", "-y"):
            opts["yes"] = True
        else:
            raise InstallError(f"Unknown option for --apply: {arg}\n" + APPLY_USAGE)
        i += 1
    if not opts["keep"]:
        raise InstallError("--apply needs --keep LIST.\n" + APPLY_USAGE)
    return opts


def apply_changes(keep, recorded):
    """{'remove': [...], 'install': [...]} to make the installed features exactly `keep` and what it needs.
    Removal is last installed first; only features that are installed are ever removed."""
    wanted = set(resolve_features(list(keep)))
    gone = [k for k in recorded if k not in wanted]
    remove = [k for k in uninstall_selection(gone) if k in recorded] if gone else []
    return {"remove": remove, "install": [k for k in feature_keys() if k in wanted]}


def apply_command(args, state=None, meta=None, remove=None, install=None, say=print):
    """aiov2_ctl --apply. Returns an exit code. The removing and the installing are the ordinary commands."""
    remove = uninstall_command if remove is None else remove
    install = install_command if install is None else install
    try:
        opts = parse_apply_args(args)
        state = install_state() if state is None else state
        meta = (load_install_meta() or {}) if meta is None else meta
        changes = apply_changes(opts["keep"], recorded_features(meta, state))
    except InstallError as exc:
        say(str(exc))
        return 2
    flags = (["--dry-run"] if opts["dry_run"] else []) + (["--yes"] if opts["yes"] else [])
    if changes["remove"]:
        code = remove(["--only", ",".join(changes["remove"]), *flags])
        if code != 0:
            return code
        say("")
    return install(["--only", ",".join(changes["install"]), *flags])


# ---- the graphical installer ------------------------------------------------------------------------------------
# A window over the same pieces: FeaturePicker for the ticks, install_plan for the plan, and this same program run
# as root for the work. The window itself never runs as root. Everything it decides is in the pure helpers here;
# the window only shows them.

def elevation(sudo_without_password, pkexec_path):
    """How the window gets root for the install: 'sudo' when sudo needs no password, 'pkexec' when the desktop
    can ask for one, else None (the user is shown the command to run in a terminal)."""
    if sudo_without_password:
        return "sudo"
    return "pkexec" if pkexec_path else None


def installer_arguments(mode, keys=()):
    """What follows the program's name for a run from the window."""
    if mode == "repair":
        return ["--repair", "--yes"]
    return ["--apply", "--keep", ",".join(keys), "--yes"]


def installer_argv(how, program, mode, keys=(), python="/usr/bin/python3", pkexec="pkexec"):
    """The full command the window runs as root. -u so the output arrives line by line."""
    lead = ["sudo", "-n"] if how == "sudo" else [pkexec]
    return [*lead, python, "-u", program, *installer_arguments(mode, keys)]


def installer_command_text(mode, keys=()):
    """The same thing as a person would type it, for when the window cannot get root itself."""
    return "sudo aiov2_ctl " + " ".join(installer_arguments(mode, keys)[:-1])


def installer_rows(picker, state, facts):
    """A row per feature for the window: what to show and whether its box is ticked or fixed."""
    rows = []
    for key in picker.keys:
        f = feature_row(key)
        needs = [feature_row(k)["title"] for k in f["needs"] if not feature_row(k)["required"]]
        rows.append({
            "key": key,
            "title": f["title"],
            "summary": f["summary"] + (f" Needs {', '.join(needs)}." if needs else ""),
            "ticked": key in picker.ticked,
            "fixed": bool(f["required"]),
            "installed": feature_installed(key, state),
            "note": picker_row_note(key, state, facts),
        })
    return rows


def installer_summary(picker, state, recorded=()):
    """One line under the list: how many are ticked, how many of those are new, and what unticking removes."""
    chosen = picker.selected()
    new = [k for k in chosen if feature_installed(k, state) != "yes"]
    gone = apply_changes(chosen, list(recorded))["remove"]
    if not new:
        text = f"{len(chosen)} ticked, all already installed. Installing again brings them up to date."
    else:
        text = (f"{len(chosen)} ticked, {len(new)} to add or finish: "
                + ", ".join(feature_row(k)["title"] for k in new) + ".")
    if gone:
        text += f" {len(gone)} to remove: " + ", ".join(feature_row(k)["title"] for k in gone) + "."
    return text


def installer_finished_text(mode, exit_code):
    what = "Repair" if mode == "repair" else "Install"
    if exit_code == 0:
        return f"{what} complete."
    if exit_code in (126, 127):
        return f"{what} did not start: the password was not given, or root was refused. Nothing was changed."
    return f"{what} finished, but not everything went in. The list above says what failed."


INSTALLER_STYLE = """
QWidget { font-size: 12pt; }
QLabel#heading { font-size: 17pt; font-weight: bold; }
QLabel#summary { font-size: 10pt; }
QLabel#note { font-size: 10pt; }
QLabel#message { font-size: 11pt; }
QCheckBox { font-weight: bold; spacing: 10px; }
QCheckBox::indicator { width: 22px; height: 22px; }
QPushButton { padding: 8px 16px; }
QPlainTextEdit { font-family: monospace; font-size: 10pt; }
"""


def build_installer_window(state=None, facts=None, how=None, program=None):
    """The installer window, built but not shown. Needs a QApplication. Kept apart from run_installer so it can
    be driven by hand on a real screen."""
    from PyQt6.QtCore import QProcess, Qt
    from PyQt6.QtWidgets import (QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                                 QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

    state = install_state() if state is None else state
    facts = hardware_facts() if facts is None else facts
    if how is None:
        how = elevation(_run_text(["sudo", "-n", "true"], timeout=10)[0] == 0, shutil.which("pkexec"))
    program = program or os.path.realpath(__file__)

    class InstallerWindow(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("AIO v2 installer")
            self.setStyleSheet(INSTALLER_STYLE)
            self.state, self.facts, self.how, self.program = state, facts, how, program
            self.picker = FeaturePicker(recorded_features(load_install_meta() or {}, state) or None)
            self.mode = "install"
            self.process = None
            self.boxes, self.notes = {}, {}
            self.stack = QStackedWidget()
            outer = QVBoxLayout(self)
            outer.setContentsMargins(18, 14, 18, 14)
            outer.addWidget(self.stack)
            self.stack.addWidget(self.choose_page())
            self.stack.addWidget(self.plan_page())
            self.stack.addWidget(self.run_page())
            self.show_ticks()

        # -- page 1: choose
        def choose_page(self):
            page = QWidget()
            col = QVBoxLayout(page)
            head = QLabel("Choose what to install")
            head.setObjectName("heading")
            col.addWidget(head)
            inner = QWidget()
            grid = QGridLayout(inner)
            grid.setColumnStretch(0, 1)
            grid.setVerticalSpacing(2)
            for i, row in enumerate(installer_rows(self.picker, self.state, self.facts)):
                box = QCheckBox(row["title"])
                box.clicked.connect(lambda _=False, key=row["key"]: self.toggle(key))
                note = QLabel()
                note.setObjectName("note")
                note.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                summary = QLabel(row["summary"])
                summary.setObjectName("summary")
                summary.setWordWrap(True)
                summary.setContentsMargins(34, 0, 0, 8)
                grid.addWidget(box, i * 2, 0)
                grid.addWidget(note, i * 2, 1)
                grid.addWidget(summary, i * 2 + 1, 0, 1, 2)
                self.boxes[row["key"]], self.notes[row["key"]] = box, note
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(inner)
            col.addWidget(scroll, 1)
            self.message = QLabel()
            self.message.setObjectName("message")
            self.message.setWordWrap(True)
            col.addWidget(self.message)
            keys = QHBoxLayout()
            for text, name in (("The usual set", "u"), ("Everything", "a"), ("Only what is needed", "n")):
                b = QPushButton(text)
                b.clicked.connect(lambda _=False, name=name: self.preset(name))
                keys.addWidget(b)
            keys.addStretch(1)
            self.repair_button = QPushButton("Repair what is installed")
            self.repair_button.clicked.connect(lambda: self.show_plan("repair"))
            keys.addWidget(self.repair_button)
            self.next_button = QPushButton("See the plan")
            self.next_button.setDefault(True)
            self.next_button.clicked.connect(lambda: self.show_plan("install"))
            keys.addWidget(self.next_button)
            col.addLayout(keys)
            return page

        def toggle(self, key):
            self.picker.toggle(key)
            self.show_ticks()

        def preset(self, name):
            self.picker.press(name)
            self.show_ticks()

        def show_ticks(self):
            for row in installer_rows(self.picker, self.state, self.facts):
                box = self.boxes[row["key"]]
                box.blockSignals(True)
                box.setChecked(row["ticked"])
                box.blockSignals(False)
                self.notes[row["key"]].setText(", ".join(n for n in ("always" if row["fixed"] else "", row["note"]) if n))
            self.recorded = recorded_features(load_install_meta() or {}, self.state)
            self.message.setText(self.picker.message
                                 or installer_summary(self.picker, self.state, self.recorded))
            self.repair_button.setEnabled(bool(repair_selection(self.state, load_install_meta() or {})))

        # -- page 2: the plan
        def plan_page(self):
            page = QWidget()
            col = QVBoxLayout(page)
            self.plan_head = QLabel()
            self.plan_head.setObjectName("heading")
            col.addWidget(self.plan_head)
            self.plan_text = QPlainTextEdit()
            self.plan_text.setReadOnly(True)
            col.addWidget(self.plan_text, 1)
            self.how_label = QLabel()
            self.how_label.setObjectName("message")
            self.how_label.setWordWrap(True)
            col.addWidget(self.how_label)
            keys = QHBoxLayout()
            back = QPushButton("Back")
            back.clicked.connect(lambda: self.stack.setCurrentIndex(0))
            keys.addWidget(back)
            keys.addStretch(1)
            self.go_button = QPushButton("Install")
            self.go_button.clicked.connect(self.start)
            keys.addWidget(self.go_button)
            col.addLayout(keys)
            return page

        def show_plan(self, mode):
            self.mode = mode
            if mode == "repair":
                self.chosen = repair_selection(self.state, load_install_meta() or {})
                lead = format_repair(self.chosen, self.state)
            else:
                self.chosen = self.picker.selected()
                gone = apply_changes(self.chosen, self.recorded)["remove"]
                lead = format_uninstall_plan(uninstall_plan(gone, self.state), gone) + chr(10) if gone else ""
            plan = install_plan(self.chosen, self.facts, self.state)
            self.plan_head.setText("This is what repair will do" if mode == "repair" else "This is what will happen")
            self.plan_text.setPlainText((lead + chr(10) if lead else lead) + format_plan(plan, self.chosen))
            self.go_button.setText("Repair" if mode == "repair" else "Remove and install" if lead else "Install")
            self.go_button.setEnabled(self.how is not None)
            self.how_label.setText({
                "sudo": "Nothing has been changed yet. Pressing the button runs this as root with sudo.",
                "pkexec": "Nothing has been changed yet. Pressing the button asks for your password, then runs "
                          "this as root.",
                None: "This window cannot get root on this system. Run this in a terminal instead:  "
                      + installer_command_text(mode, self.chosen),
            }[self.how])
            self.stack.setCurrentIndex(1)

        # -- page 3: doing it
        def run_page(self):
            page = QWidget()
            col = QVBoxLayout(page)
            self.run_head = QLabel()
            self.run_head.setObjectName("heading")
            col.addWidget(self.run_head)
            self.log = QPlainTextEdit()
            self.log.setReadOnly(True)
            col.addWidget(self.log, 1)
            self.done_label = QLabel()
            self.done_label.setObjectName("message")
            self.done_label.setWordWrap(True)
            col.addWidget(self.done_label)
            keys = QHBoxLayout()
            self.again_button = QPushButton("Back to the list")
            self.again_button.clicked.connect(self.back_to_list)
            keys.addWidget(self.again_button)
            keys.addStretch(1)
            self.close_button = QPushButton("Close")
            self.close_button.clicked.connect(self.close)
            keys.addWidget(self.close_button)
            col.addLayout(keys)
            return page

        def start(self):
            argv = installer_argv(self.how, self.program, self.mode, self.chosen)
            self.run_head.setText("Repairing" if self.mode == "repair" else "Installing")
            self.log.setPlainText("")
            self.done_label.setText("Working. This can take a few minutes when packages are downloaded.")
            self.again_button.setEnabled(False)
            self.close_button.setEnabled(False)
            self.stack.setCurrentIndex(2)
            self.process = QProcess(self)
            self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            self.process.readyReadStandardOutput.connect(self.read_output)
            self.process.finished.connect(self.finished)
            self.process.errorOccurred.connect(self.failed_to_start)
            self.process.start(argv[0], argv[1:])

        def read_output(self):
            text = bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace")
            self.log.moveCursor(self.log.textCursor().MoveOperation.End)
            self.log.insertPlainText(text)
            self.log.ensureCursorVisible()

        def failed_to_start(self, error):
            if error == QProcess.ProcessError.FailedToStart:
                self.finished(127)

        def finished(self, code, _status=None):
            self.run_head.setText("Finished")
            self.done_label.setText(installer_finished_text(self.mode, code))
            self.again_button.setEnabled(True)
            self.close_button.setEnabled(True)

        def back_to_list(self):
            self.state = install_state()
            self.picker.message = ""
            self.show_ticks()
            self.stack.setCurrentIndex(0)

        def closeEvent(self, event):
            # An install that is under way is never cut off half done by closing the window.
            if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
                event.ignore()
                return
            super().closeEvent(event)

    return InstallerWindow()


def run_installer():
    """aiov2_ctl --installer. Returns an exit code."""
    if os.geteuid() == 0:
        print("Run the installer window as yourself, not as root: it asks for root when it installs.")
        return 1
    if not desktop_running():
        print("There is no desktop to show a window on. Use the terminal picker instead: "
              "sudo aiov2_ctl --install --pick")
        return 1
    try:
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        print("The installer window needs PyQt6, which is not installed.")
        print("Install it with: sudo apt install python3-pyqt6")
        print("Or use the terminal picker, which needs nothing extra: sudo aiov2_ctl --install --pick")
        return 1
    app = QApplication(sys.argv[:1])
    window = build_installer_window()
    window.resize(900, 620)
    window.show()
    return app.exec()


def desktop_running(environ=None):
    """True when this process has a display to open a window on."""
    env = os.environ if environ is None else environ
    return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))


def install_self():
    """Kept for callers of the old name: the same as `aiov2_ctl --install` with whatever followed it."""
    return install_command(sys.argv[2:])


SUDOERS_PATH = "/etc/sudoers.d/aiov2_ctl"
_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
_SUDOERS_SERVICES = ("meshtasticd", "meshtasticd.service", "readsb", "readsb.service")


def build_sudoers(user, listening=True):
    """
    Exact, argument-by-argument rules for what the tray needs. No wildcards,
    so the user cannot smuggle extra arguments past sudo. `listening` False
    leaves out the passive Wi-Fi and Bluetooth listening commands (the
    installer's 'listening' feature).
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
    # Wired network: listening is passive, the sweep is the one active command and the app asks first.
    cmds.append(f"{INSTALLED_PATH} --net listen --seconds 30 --json")
    cmds.append(f"{INSTALLED_PATH} --net sweep --yes --json")
    # Wi-Fi monitor mode for Sense: passive, fixed radio and duration, one rule per channel.
    for ch in (MONITOR_CHANNELS if listening else ()):
        cmds.append(f"{INSTALLED_PATH} --wifi-listen --iface {MONITOR_IFACE} --channel {ch} --seconds {MONITOR_SECONDS} --json")
        # The same, with one sender always reported. Its address is read from standard input, so this rule is
        # still one exact command and no argument is left open.
        cmds.append(f"{INSTALLED_PATH} --wifi-listen --iface {MONITOR_IFACE} --channel {ch} --seconds {MONITOR_SECONDS} --only - --json")
    if listening:
        cmds.append(f"{INSTALLED_PATH} --wifi-listen --restore --iface {MONITOR_IFACE}")
    for band in (SURVEY_BANDS if listening else ()):
        for extra in ("", " --pcap"):
            cmds.append(f"{INSTALLED_PATH} --wifi-survey --iface {MONITOR_IFACE} --band {band} --seconds {MONITOR_SECONDS}{extra} --json")
    # Passive Bluetooth scan on the internal card: fixed adapter and duration.
    if listening:
        cmds.append(f"{INSTALLED_PATH} --bt-scan --adapter {BT_ADAPTER} --seconds {BT_SECONDS} --json")
    cmds.append(f"{systemctl} enable meshtasticd")
    cmds.append(f"{systemctl} disable --now meshtasticd")

    lines = [
        "# Managed by aiov2_ctl --setup-sudoers. Remove with --setup-sudoers --remove.",
        "# Lets the tray GUI change rails and services without a password prompt.",
        f"{user} ALL=(root) NOPASSWD: " + ", \\\n    ".join(cmds),
        "",
    ]
    return "\n".join(lines)


def setup_sudoers(remove=False, listening=True):
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

    content = build_sudoers(user, listening)
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

    branch = update_branch(meta, get_git_branch(repo))
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
        print("Nothing new to fetch.")
        state = pending_update()
        if state:
            pulled = state == "program"         # the new program is in the checkout and still has to be copied
            print("\n" + (PENDING_NOTICE if pulled else UNFINISHED_NOTICE).split("\n")[0])
            later = "aiov2_ctl --update --yes" if pulled else "sudo aiov2_ctl --install --yes"
            if pulled and load_config().get("require_signed_updates"):
                # Nothing was fetched to look over, so what goes in as root is the checkout as it stands: it has
                # to be a signed commit with nothing changed on top.
                changed = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repo,
                                         text=True, capture_output=True).stdout.strip()
                if changed or not run_cmd(["git", "verify-commit", "HEAD"], cwd=repo):
                    print("\nrequire_signed_updates is on, and the checkout is not a validly signed commit with "
                          "nothing changed since. Not installing it.")
                    return 1
            if not confirmed and "--yes" not in sys.argv:
                if not sys.stdin.isatty():
                    print(f"Finish the update with: {later}")
                    return 1
                resp = input("\nRun the installer now, as root, to finish the update? [y/N]: ")
                if resp.strip().lower() not in ("y", "yes"):
                    print(f"Left as it is. Finish it later with: {later.replace(' --yes', '')}")
                    return 0
            report_and_disable_mesh_autostart_if_default("Meshtastic boot config status:")
            return finish_update(two_steps=pulled)
        print("Already up to date.")
        return 0

    print("Incoming changes:\n")
    print(summary)
    run_cmd(["git", "diff", "--stat", incoming], cwd=repo)

    # The signature covers the whole repository: this tool, which runs as root, and the ops app, which does not.
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

    # Root is only used for the install; the user has just reviewed the changes.
    return finish_update(two_steps=True)

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

    branch = update_branch(meta, get_git_branch(repo))
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
        branch = update_branch(load_install_meta(), get_git_branch(repo) if repo else None)

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
    notice = unfinished_notice_for(sys.argv[1] if len(sys.argv) > 1 else None, sys.argv[2:],
                                   sys.stdout.isatty(), pending_update)
    if notice:
        print(notice + "\n", file=sys.stderr)
    if len(sys.argv) == 1:
        for f, p in GPIO_MAP.items():
            print(f"{f}: {'ON' if GpioController.get_gpio(p) else 'OFF'}")
        return

    arg = sys.argv[1]

    if arg in ("--help", "-h"):
        print(HELP_TEXT)

    elif arg == "--install":
        sys.exit(install_command(sys.argv[2:]))

    elif arg == "--features":
        sys.exit(features_command(sys.argv[2:]))

    elif arg == "--wiki-pictures":
        sys.exit(wiki_pictures_command(sys.argv[2:]))

    elif arg == "--installer":
        sys.exit(run_installer())

    elif arg == "--apply":
        sys.exit(apply_command(sys.argv[2:]))

    elif arg == "--repair":
        sys.exit(install_command(["--repair", *sys.argv[2:]]))

    elif arg == "--uninstall":
        sys.exit(uninstall_command(sys.argv[2:]))

    elif arg == "--update":
        sys.exit(update_self())

    elif arg == "--update-install":
        sys.exit(update_install_command())

    elif arg == "--channel":
        sys.exit(channel_command(sys.argv[2:]))

    elif arg == "--check-update":
        sys.exit(check_update_interactive())

    elif arg == "--status":
        if "--json" in sys.argv[2:]:
            print(json.dumps(shared_status(status_max_age(sys.argv[2:])), indent=2))
        else:
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

    elif arg == "--wifi":
        sys.exit(wifi_command(sys.argv[2:]))

    elif arg == "--net":
        sys.exit(net_command(sys.argv[2:]))

    elif arg == "--wifi-survey":
        sys.exit(survey_command(sys.argv[2:]))

    elif arg == "--wifi-listen":
        sys.exit(monitor_command(sys.argv[2:]))

    elif arg == "--bt-scan":
        sys.exit(bt_command(sys.argv[2:]))

    elif arg == "--marauder":
        sys.exit(marauder_command(sys.argv[2:]))

    elif arg == "--flipper":
        sys.exit(flipper_command(sys.argv[2:]))

    elif arg == "--doctor":
        sys.exit(doctor_command())

    elif arg == "--companions":
        sys.exit(show_companions(as_json="--json" in sys.argv[2:]))

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
