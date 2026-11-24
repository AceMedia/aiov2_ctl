#!/usr/bin/python3
import sys
import os
import subprocess
import time
import json
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
  aiov2_ctl --check-update
  aiov2_ctl --help
  aiov2_ctl --autostart
  aiov2_ctl --no-autostart
    aiov2_ctl --mesh-on-boot [on|off|status]
    aiov2_ctl --boot-rail <FEATURE> on|off|status
    aiov2_ctl --boot-rails-status
    aiov2_ctl --net status|neighbors|listen|sweep|ports   (wired network, passive first)
    aiov2_ctl --wifi-listen [--channel 6]                (passive Wi-Fi signal listening for Sense, needs root)
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
MARAUDER_SCAN_MODES = {"all": "scanall", "beacon": "sniffbeacon"}
MARAUDER_LISTS = {"aps": "list -a", "stations": "list -c"}


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
    try:
        while time.monotonic() < end:
            for line in ser.read_lines(min(0.5, max(0.0, end - time.monotonic()))):
                say(line)
                log.write(line)
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


def marauder_command(args):
    usage = (
        "Usage: aiov2_ctl --marauder <command> [--port /dev/ttyXXX]\n"
        "  info                       board and firmware info\n"
        "  help                       list the firmware's own commands\n"
        "  scan [all|beacon] [--seconds N]   (default 30; under about 15 may hear nothing)\n"
        "                             passive scan, streamed and saved to ~/aiov2-captures\n"
        "  list [aps|stations]        show what the board has found\n"
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
MONITOR_LINKS_PER_SECOND = 8
SYSTEM_PACKAGES = {"iw": "/usr/sbin/iw"}      # apt package -> a file it provides


def radiotap_info(raw):
    """(signal_dbm or None, bad_checksum, header_length) from a radiotap header, or None if it is not one."""
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
    flags, signal = 0, None
    for bit, (align, size) in enumerate(((8, 8), (1, 1), (1, 1), (2, 4), (2, 2), (1, 1))):
        if not present & (1 << bit):
            continue
        pos = (pos + align - 1) // align * align
        if pos + size > length:
            return None
        if bit == 1:
            flags = raw[pos]
        elif bit == 5:
            signal = struct.unpack_from("<b", raw, pos)[0]
        pos += size
    return signal, bool(flags & 0x40), length


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


def monitor_summary(seen, now):
    """One second of (mac, dbm) pairs as a record the Sense page can read."""
    per = {}
    for mac, dbm in seen:
        per.setdefault(mac, []).append(dbm)
    links = sorted(per.items(), key=lambda kv: -len(kv[1]))[:MONITOR_LINKS_PER_SECOND]
    return {
        "t": round(now, 2),
        "frames": len(seen),
        "rssi": round(mean(d for _m, d in seen), 1) if seen else None,
        "links": {m: [len(v), round(mean(v), 1)] for m, v in links},
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


def monitor_run(iface, channel, seconds, emit):
    """Listen on one channel for `seconds`, calling emit(record) every second. Restores the radio afterwards."""
    import signal
    if os.geteuid() != 0:
        raise OSError("Monitor mode needs root: run it as 'sudo aiov2_ctl --wifi-listen'.")
    iw, ip = _tool("iw"), _tool("ip")
    if not iw or not ip:
        raise OSError("'iw' is not installed. Run: sudo aiov2_ctl --install   (or: sudo apt install iw)")
    if not _IFACE_RE.match(iface) or not os.path.isdir(os.path.join(SYS_NET, iface, "wireless")):
        raise OSError(f"{iface} is not a Wi-Fi interface on this machine.")
    link = _monitor_cmd(iw, "dev", iface, "link", check=False).stdout
    if "Connected to" in link:
        raise OSError(f"{iface} is connected to a network. Disconnect it first; monitor mode would drop that connection.")

    def stop(_sig, _frm):
        raise KeyboardInterrupt
    old = signal.signal(signal.SIGTERM, stop)
    sock = None
    try:
        if _tool("nmcli"):
            _monitor_cmd("nmcli", "device", "set", iface, "managed", "no", check=False)
        _monitor_cmd(ip, "link", "set", iface, "down")
        _monitor_cmd(iw, "dev", iface, "set", "type", "monitor")
        _monitor_cmd(ip, "link", "set", iface, "up")
        _monitor_cmd(iw, "dev", iface, "set", "channel", str(channel))
        sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(0x0003))
        sock.bind((iface, 0))
        sock.settimeout(0.25)
        end = time.monotonic() + seconds
        bucket_end, seen = time.monotonic() + 1.0, []
        while time.monotonic() < end:
            try:
                got = monitor_frame(sock.recv(4096))
                if got:
                    seen.append(got)
            except socket.timeout:
                pass
            if time.monotonic() >= bucket_end:
                emit(monitor_summary(seen, time.time()))
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
        "Usage: aiov2_ctl --wifi-listen [--iface wlan1] [--channel 6] [--seconds 3600] [--json]   (passive, needs root)\n"
        "       aiov2_ctl --wifi-listen --restore [--iface wlan1]                                  (put the radio back to normal)\n"
        "Listens to the Wi-Fi traffic in the air on one channel and reports its signal strength once a second.\n"
        "Never transmits. The radio is returned to normal mode when it stops."
    )
    args = list(args)
    as_json, restore = "--json" in args, "--restore" in args
    args = [a for a in args if a not in ("--json", "--restore")]
    opts = {"--iface": MONITOR_IFACE, "--channel": "6", "--seconds": str(MONITOR_SECONDS)}
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
        monitor_run(iface, channel, seconds, emit)
        return 0
    except OSError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def ensure_system_packages(say=print):
    """Install the apt packages this program relies on, if they are missing. Never fatal."""
    missing = [pkg for pkg, path in SYSTEM_PACKAGES.items() if not os.path.exists(path)]
    if not missing:
        return True
    say(f"Installing required packages: {', '.join(missing)}\n")
    try:
        subprocess.check_call(["apt-get", "install", "-y", *missing])
        return True
    except (OSError, subprocess.CalledProcessError):
        say(f"Could not install {', '.join(missing)}. Run: sudo apt install {' '.join(missing)}\n")
        return False



def gauge_disagrees(summary):
    """True when the reported charge level cannot match the cell voltage."""
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
    print(f"Capacity  : {summary['capacity']}%")
    print(f"Direction : {summary['direction']}")
    print(f"Mode      : {summary['mode']}")
    print(f"Voltage   : {summary['voltage']} V")
    print(f"Current   : {summary['current']} A")
    print(f"Power     : {summary['power']} W")
    if gauge_disagrees(summary):
        print(f"Warning   : the gauge says {summary['capacity']}% but the battery is at "
              f"{summary['voltage']} V. A full cell reads about 4.2 V, so one cell may be flat "
              "or not making contact. Check the battery before running on battery power.")
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
    # Packages this program relies on
    # ------------------------------
    ensure_system_packages()

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
    # Exact, validated rules for the invoking user only (see build_sudoers).
    # This used to grant the service commands to every local user.
    if setup_sudoers() != 0:
        print("Sudoers rule not installed. Run 'sudo aiov2_ctl --setup-sudoers' from your own account.\n")

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
    # Wired network: listening is passive, the sweep is the one active command and the app asks first.
    cmds.append(f"{INSTALLED_PATH} --net listen --seconds 30 --json")
    cmds.append(f"{INSTALLED_PATH} --net sweep --yes --json")
    # Wi-Fi monitor mode for Sense: passive, fixed radio and duration, one rule per channel.
    for ch in MONITOR_CHANNELS:
        cmds.append(f"{INSTALLED_PATH} --wifi-listen --iface {MONITOR_IFACE} --channel {ch} --seconds {MONITOR_SECONDS} --json")
    cmds.append(f"{INSTALLED_PATH} --wifi-listen --restore --iface {MONITOR_IFACE}")
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
        if "--json" in sys.argv[2:]:
            print(json.dumps(status_data(), indent=2))
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

    elif arg == "--wifi-listen":
        sys.exit(monitor_command(sys.argv[2:]))

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
