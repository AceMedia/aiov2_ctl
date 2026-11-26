"""
Companion devices (Flipper Zero, ESP32 boards) as reported by aiov2_ctl.

This only reads `aiov2_ctl --companions --json`. If that fails for any
reason (older aiov2_ctl, not installed, odd output, timeout) the result is
empty and the rest of the console behaves exactly as it did before.
"""
import json
import subprocess

from .power import debug

TIMEOUT_SECONDS = 5


def get_companions():
    """Return {"usb_rail": "on"|"off"|None, "devices": [...]} or {} on any failure."""
    try:
        out = subprocess.check_output(
            ["aiov2_ctl", "--companions", "--json"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=TIMEOUT_SECONDS,
        )
        data = json.loads(out)
    except Exception as e:
        debug(f"companions unavailable: {e}")
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("devices"), list):
        debug("companions: unexpected output")
        return {}
    return data


def companion_ports(data):
    """Serial ports that belong to companions (and so are not the LoRa radio).

    Only devices aiov2_ctl is sure about count. Generic USB-serial chips are
    reported as 'possible-esp32' and are left alone, since the LoRa radio may
    use the same chips.
    """
    return {
        d["tty"]
        for d in data.get("devices", [])
        if isinstance(d, dict) and d.get("tty") and d.get("kind") in ("flipper", "esp32")
    }


def summary(data):
    """One line for the status log, or '' if there is nothing to say."""
    devices = [d for d in data.get("devices", []) if isinstance(d, dict)]
    if not devices:
        return ""
    parts = []
    for d in devices:
        label = d.get("label") or d.get("name") or "device"
        if d.get("kind") == "possible-esp32":
            label += " (possible)"
        parts.append(label + (f" @ {d['tty']}" if d.get("tty") else ""))
    return "Companions: " + ", ".join(parts)
