"""
Wi-Fi scan panel data, read through `aiov2_ctl --wifi ... --json`.

Optional and passive. Nothing runs unless `--wifi radios` reported an extra
radio (USB adapter or Flipper Marauder board) besides the onboard one. A scan
only happens when the user presses the key, never in the background. The
panel lists what is heard; it never connects, deauths or injects.
"""
import json
import subprocess

from .power import debug

TIMEOUT_SECONDS = 20
RADIOS_TIMEOUT_SECONDS = 5
EXTRA_KINDS = ("internal-usb", "external-usb", "flipper-marauder")


def _run(args, timeout):
    try:
        out = subprocess.run(["aiov2_ctl", "--wifi", *args, "--json"],
                             text=True, capture_output=True, timeout=timeout).stdout
        data = json.loads(out)
    except Exception as e:
        debug(f"wifi {args[0]} unavailable: {e}")
        return {}
    return data if isinstance(data, dict) else {}


def get_radios():
    """{"radios": [...]} from aiov2_ctl, or {} on any failure."""
    data = _run(["radios"], RADIOS_TIMEOUT_SECONDS)
    return data if isinstance(data.get("radios"), list) else {}


def get_scan():
    """One scan result dict, or {} on any failure."""
    data = _run(["scan"], TIMEOUT_SECONDS)
    return data if isinstance(data.get("networks"), list) else {}


def has_extra_radio(radios_data):
    """True only when a radio besides the onboard one exists.

    Chosen so a single-radio uConsole sees nothing new: the panel and key
    appear for a USB adapter or a Flipper Marauder board, whether or not it
    is currently up.
    """
    return any(isinstance(r, dict) and r.get("kind") in EXTRA_KINDS
               for r in (radios_data or {}).get("radios", []))


def _signal(n):
    s = n.get("signal_dbm")
    return s if isinstance(s, (int, float)) else -999


def sort_by_signal(networks):
    return sorted((n for n in networks or [] if isinstance(n, dict)), key=_signal, reverse=True)


def band(n):
    f = n.get("freq_mhz")
    if isinstance(f, (int, float)):
        return "2.4" if f < 3000 else "5" if f < 5925 else "6"
    ch = n.get("channel")
    if isinstance(ch, int):
        return "2.4" if ch <= 14 else "5"
    return "?"


def channel_occupancy(networks):
    """{"2.4": {channel: count}, "5": {...}, "6": {...}}, empty bands left out."""
    out = {}
    for n in networks or []:
        if not isinstance(n, dict) or not isinstance(n.get("channel"), int):
            continue
        counts = out.setdefault(band(n), {})
        counts[n["channel"]] = counts.get(n["channel"], 0) + 1
    return out


def busiest(counts):
    """(channel, count) with the most networks, lowest channel on a tie."""
    if not counts:
        return None
    ch = min(counts, key=lambda c: (-counts[c], c))
    return ch, counts[ch]


def congestion_line(networks):
    occ = channel_occupancy(networks)
    parts = []
    for b in ("2.4", "5", "6"):
        top = busiest(occ.get(b, {}))
        if top:
            parts.append(f"{b} GHz busiest ch {top[0]} ({top[1]})")
    return "Congestion: " + ("; ".join(parts) if parts else "nothing heard")


def group_by_radio(networks):
    groups = {}
    for n in sort_by_signal(networks):
        groups.setdefault(n.get("radio") or "?", []).append(n)
    return groups


def radio_line(r):
    state = "up" if r.get("up") else "down"
    return f"{r.get('name', '?')} ({r.get('kind', '?')}, {r.get('driver') or 'no driver'}): {state}"


def network_line(n):
    ssid = n.get("ssid") or "<hidden>"
    sig = n.get("signal_dbm")
    sig = f"{sig} dBm" if isinstance(sig, (int, float)) else "n/a"
    return f"{sig:>8} ch{n.get('channel', '?'):<3} {n.get('security') or '?':<6} {ssid[:24]:<24} [{n.get('radio') or '?'}]"


MAX_ROWS = 8


def panel_lines(scan, radios=None):
    """Plain text lines for the panel. Pure, so it can be tested."""
    lines = [radio_line(r) for r in (radios or scan.get("radios") or []) if isinstance(r, dict)]
    if not scan:
        return lines + ["No scan yet. Press l to scan once."]
    nets = sort_by_signal(scan.get("networks"))
    lines.append(congestion_line(nets))
    lines += [network_line(n) for n in nets[:MAX_ROWS]]
    if len(nets) > MAX_ROWS:
        lines.append(f"... and {len(nets) - MAX_ROWS} more")
    if not nets:
        lines.append("No networks heard.")
    return lines
