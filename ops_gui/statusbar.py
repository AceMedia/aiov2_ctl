"""
The bottom status bar: CPU, memory, temperature, disk, uptime, address and anything throttling.

Everything is read from /proc and /sys (plus one quick vcgencmd now and then), so it costs next to
nothing. The helpers are pure where they can be, so they can be tested without a uConsole.
"""
import html
import os
import socket
import subprocess
import time

COLOURS = {"ok": "#9e9e9e", "good": "#43a047", "warn": "#f59f00", "hot": "#e03131"}
THROTTLE_BITS = ((0x1, "under voltage"), (0x2, "frequency capped"), (0x4, "throttled"), (0x8, "soft temperature limit"))


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def parse_cpu(text):
    """(busy, total) jiffies from the first line of /proc/stat, or None."""
    for line in (text or "").splitlines():
        if line.startswith("cpu "):
            try:
                v = [int(x) for x in line.split()[1:9]]
            except ValueError:
                return None
            idle = v[3] + (v[4] if len(v) > 4 else 0)
            total = sum(v)
            return total - idle, total
    return None


def cpu_percent(prev, cur):
    if not prev or not cur or cur[1] <= prev[1]:
        return None
    return max(0.0, min(100.0, 100.0 * (cur[0] - prev[0]) / (cur[1] - prev[1])))


def parse_mem(text):
    mem = {}
    for line in (text or "").splitlines():
        k, _, v = line.partition(":")
        if v.split() and v.split()[0].isdigit():
            mem[k] = int(v.split()[0])
    if not mem.get("MemTotal"):
        return None
    used = mem["MemTotal"] - mem.get("MemAvailable", 0)
    return used / 1048576, mem["MemTotal"] / 1048576, 100.0 * used / mem["MemTotal"]


def uptime_text(seconds):
    s = int(seconds)
    d, rest = divmod(s, 86400)
    h, rest = divmod(rest, 3600)
    m = rest // 60
    return f"{d}d {h}h" if d else f"{h}h {m:02d}m" if h else f"{m}m"


def throttle_text(value):
    """Words for a get_throttled value, or '' when nothing is wrong right now."""
    if not value:
        return ""
    return ", ".join(label for bit, label in THROTTLE_BITS if value & bit)


def parse_throttled(text):
    try:
        return int((text or "").strip().split("=")[-1], 16)
    except ValueError:
        return 0


def local_address():
    """This machine's main address without sending anything: a UDP socket is only pointed, never used."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return ""


def read_throttled():
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    return parse_throttled(out)


def read_stats(prev_cpu=None, throttled=0):
    """A dict of what the bar shows, plus the cpu sample to pass in next time."""
    cpu = parse_cpu(_read("/proc/stat"))
    temp = _read("/sys/class/thermal/thermal_zone0/temp").strip()
    load = _read("/proc/loadavg").split()
    up = _read("/proc/uptime").split()
    disk = None
    try:
        st = os.statvfs("/")
        disk = 100.0 * (1 - st.f_bavail / st.f_blocks) if st.f_blocks else None
    except OSError:
        pass
    return {
        "cpu": cpu_percent(prev_cpu, cpu), "sample": cpu,
        "temp": int(temp) / 1000 if temp.isdigit() else None,
        "load": float(load[0]) if load else None,
        "mem": parse_mem(_read("/proc/meminfo")),
        "disk": disk, "uptime": float(up[0]) if up else None,
        "ip": local_address(), "throttled": throttled,
    }


def level(value, warn, hot):
    if value is None:
        return "ok"
    return "hot" if value >= hot else "warn" if value >= warn else "good"


def chip_items(stats, dark=True):
    """[(key, text, level)] for the bar, in a stable order. Missing readings are left out. `dark` picks the
    temperature colours that can be read on that theme."""
    out = []
    if stats.get("cpu") is not None:
        out.append(("cpu", f"CPU {stats['cpu']:.0f}%", level(stats["cpu"], 70, 90)))
    if stats.get("temp") is not None:
        from .pixel import temp_colour
        out.append(("temp", f"{stats['temp']:.0f}\u00b0C", temp_colour(stats["temp"], dark)))
    if stats.get("mem"):
        used, total, pct = stats["mem"]
        out.append(("ram", f"RAM {used:.1f}/{total:.1f} GB", level(pct, 75, 90)))
    if stats.get("load") is not None:
        out.append(("load", f"load {stats['load']:.2f}", "ok"))
    if stats.get("disk") is not None:
        out.append(("disk", f"disk {stats['disk']:.0f}%", level(stats["disk"], 80, 92)))
    if stats.get("uptime") is not None:
        out.append(("uptime", f"up {uptime_text(stats['uptime'])}", "ok"))
    if stats.get("ip"):
        out.append(("ip", stats["ip"], "ok"))
    words = throttle_text(stats.get("throttled"))
    if words:
        out.append(("power", f"power: {words}", "hot"))
    return out


def chips(stats, hidden=(), dark=True):
    """[(text, level)] for the bar, without the items the user turned off."""
    return [(text, lv) for key, text, lv in chip_items(stats, dark) if key not in hidden]


def chips_html(chip_list, extra=()):
    """Rich text for the bar. `extra` chips (like the battery) come first."""
    parts = [f'<span style="color:{lv if lv.startswith("#") else COLOURS.get(lv, COLOURS["ok"])}">{html.escape(text)}</span>'
             for text, lv in list(extra) + list(chip_list)]
    return '&nbsp;<span style="color:#e95420">/</span>&nbsp;'.join(parts)
