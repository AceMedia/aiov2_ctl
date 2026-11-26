"""
Flipper Zero panel data, read through `aiov2_ctl --flipper status --json`.

Optional on purpose. Nothing here runs unless aiov2_ctl already reported a
Flipper on USB, and the status is only fetched when the panel starts or the
user presses its refresh key. It never polls in the background, because the
Flipper's serial port is opened exclusively while it is read.
"""
import json
import subprocess

from .power import debug

TIMEOUT_SECONDS = 20


def has_flipper(companion_data):
    return any(isinstance(d, dict) and d.get("kind") == "flipper" and d.get("tty")
               for d in (companion_data or {}).get("devices", []))


def get_status():
    """The status dict from aiov2_ctl, or {} on any failure."""
    try:
        out = subprocess.run(
            ["aiov2_ctl", "--flipper", "status", "--json"],
            text=True, capture_output=True, timeout=TIMEOUT_SECONDS,
        ).stdout
        data = json.loads(out)
    except Exception as e:
        debug(f"flipper status unavailable: {e}")
        return {}
    return data if isinstance(data, dict) and "mode" in data else {}


def panel_lines(status):
    """Plain text lines for the panel. Pure, so it can be tested."""
    if not status:
        return ["No reading yet. Press k to read the Flipper."]
    lines = []
    dev = status.get("device") or {}
    if dev.get("firmware_version"):
        lines.append(f"Firmware : {dev['firmware_version']} ({dev.get('firmware_origin_fork', 'n/a')})")
    mode = "USB-UART Bridge" if status.get("mode") == "bridge" else "CLI"
    lines.append(f"Mode     : {mode}")
    bat = status.get("battery") or {}
    if bat.get("charge.level"):
        lines.append(f"Battery  : {bat['charge.level']}% ({bat.get('charge.state', 'n/a')})")
    lines.append(f"SD card  : {status.get('sd_card', 'unknown')}")
    apps = status.get("apps") or []
    lines.append("Apps     : " + (", ".join(apps[:6]) + (" ..." if len(apps) > 6 else "") if apps else "none seen"))
    lines.append(f"Wi-Fi board: {status.get('wifi_board', 'unchecked')}")
    for tip in status.get("suggestions") or []:
        lines.append(f"Tip: {tip}")
    return lines


SCREEN_LINES = 16


def valid_screen(text):
    """True for a braille screen as printed by `aiov2_ctl --flipper screen`."""
    lines = (text or "").splitlines()
    return len(lines) == SCREEN_LINES and all(
        l and all("\u2800" <= ch <= "\u28ff" for ch in l) for l in lines)


def get_screen():
    """One frame of the Flipper's display as text, or '' on any failure."""
    try:
        out = subprocess.run(
            ["aiov2_ctl", "--flipper", "screen"],
            text=True, capture_output=True, timeout=TIMEOUT_SECONDS,
        ).stdout.rstrip("\n")
    except Exception as e:
        debug(f"flipper screen unavailable: {e}")
        return ""
    return out if valid_screen(out) else ""
