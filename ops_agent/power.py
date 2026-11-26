import subprocess
import sys
import termios
import tty
from datetime import datetime, timezone

def get_key():
    """Get a single keypress without waiting for Enter"""
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(sys.stdin.fileno())
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return ch


# -----------------------------
# Runtime flags
# -----------------------------

DEBUG = False


# -----------------------------
# Logging
# -----------------------------

status_log = []
debug_log = []

def log(msg):
    status_log.append(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {msg}")
    del status_log[:-6]

def debug(msg):
    if DEBUG:
        debug_log.append(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {msg}")
        del debug_log[:-20]


# -----------------------------
# AIO v2 power control
# -----------------------------

FEATURES = {
    "GPS":  "gps",
    "SDR":  "sdr",
    "LORA": "lora",
    "USB":  "usb",
}

def get_aio_status():
    try:
        out = subprocess.check_output(
            ["aiov2_ctl", "--status"],
            text=True
        )
        debug("aiov2_ctl --status OK")
    except Exception as e:
        debug(f"aiov2_ctl status failed: {e}")
        return {}

    status = {}
    for line in out.splitlines():
        for name in FEATURES:
            if line.startswith(name):
                status[name] = "ON" in line
    return status


def enable_feature(feature_name):
    """
    Enable a hardware feature via aiov2_ctl.
    Uses the canonical command form:
      aiov2_ctl <FEATURE> on
    """
    debug(f"Enabling {feature_name}")
    subprocess.run(
        ["sudo", "aiov2_ctl", feature_name, "on"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def disable_feature(feature_name):
    """
    Disable a hardware feature via aiov2_ctl.
    Uses the canonical command form:
      aiov2_ctl <FEATURE> off
    """
    debug(f"Disabling {feature_name}")
    subprocess.run(
        ["sudo", "aiov2_ctl", feature_name, "off"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def startup_enabled(status):
    """Rails as they are, with no questions: on stays enabled, off stays off."""
    return {name: bool(flag) for name, flag in (status or {}).items()}


def prompt_for_power(status):
    enabled = {}
    print("\nAIO v2 hardware check:\n")

    for name, flag in status.items():
        if flag:
            debug(f"{name} is ON")
            print(f"{name} is ON. Keep it on? [y/n]: ", end='', flush=True)

            # Get single keypress
            while True:
                key = get_key().lower()
                if key in ['y', 'n']:
                    print(key)  # Echo the choice
                    break

            if key == 'y':
                enabled[name] = True
                print(f"✔ {name} stays ON")
                debug(f"{name} stays ON")
            else:
                disable_feature(name)
                enabled[name] = False
                print(f"→ {name} powered OFF")
                debug(f"{name} powered OFF")
        else:
            debug(f"{name} is OFF")
            print(f"{name} is OFF. Power it on? [y/n]: ", end='', flush=True)

            # Get single keypress
            while True:
                key = get_key().lower()
                if key in ['y', 'n']:
                    print(key)  # Echo the choice
                    break

            if key == 'y':
                enable_feature(name)
                enabled[name] = True
                print(f"→ {name} powered ON")
                debug(f"{name} powered ON")
            else:
                enabled[name] = False
                print(f"✘ {name} left OFF")
                debug(f"{name} left OFF")

    print()
    return enabled


def prompt_for_shutdown(enabled):
    """Ask user which hardware features to power off before exit"""
    # Check current status to see what's actually ON
    status = get_aio_status()
    
    # Find enabled features that are currently ON
    active_features = {name: flag for name, flag in status.items() if flag and enabled.get(name)}
    
    if not active_features:
        print("\nNo hardware features to power off.\n")
        return
    
    print("\n\nShutdown - Active hardware:\n")
    
    for name in active_features:
        print(f"{name} is ON. Power it off? [y/n]: ", end='', flush=True)
        
        # Get single keypress
        while True:
            key = get_key().lower()
            if key in ['y', 'n']:
                print(key)  # Echo the choice
                break
        
        if key == 'y':
            disable_feature(name)
            print(f"→ {name} powered OFF")
            debug(f"{name} powered OFF at shutdown")
        else:
            print(f"✘ {name} left ON")
            debug(f"{name} left ON at shutdown")
    
    print()


def set_debug(enabled):
    global DEBUG
    DEBUG = enabled
