"""
Data for the native app. One source of truth: `aiov2_ctl --status --json`.

Pure helpers turn that into the words and colours the window shows, so they
can be tested without a screen. Nothing here changes hardware.
"""
import json
import os
import subprocess

TIMEOUT_SECONDS = 15


STATUS_SHARE_SECONDS = 2.0           # how long one reading of the board serves everyone (as in aiov2_ctl)


def status_cache_path(environ=None, uid=None, isdir=os.path.isdir):
    """Where aiov2_ctl keeps its last full status reading, beside the rails mark."""
    return rails_stamp_path(environ, uid, isdir)[:-len(".rails")] + ".status.json"


def status_cache_fresh(written, now, mark_time, max_age):
    """The same rule aiov2_ctl uses: recent enough, not from the future, and no rail switched since."""
    if written is None or max_age <= 0:
        return False
    if not 0 <= now - written <= max_age:
        return False
    return mark_time is None or mark_time <= written


def shared_snapshot(max_age=STATUS_SHARE_SECONDS, now=None, path=None, mark=None):
    """The status the tray or the command line read from the board a moment ago, if it can still be used;
    otherwise None. Reading it costs one small file instead of starting aiov2_ctl. With an older aiov2_ctl
    there is no such file and this is always None."""
    import time
    try:
        with open(path or status_cache_path()) as f:
            kept = json.load(f)
        written, status = kept.get("written"), kept.get("status")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(status, dict) or "rails" not in status or not isinstance(written, (int, float)):
        return None
    try:
        mark_time = os.path.getmtime(mark or rails_stamp_path())
    except OSError:
        mark_time = None
    now = time.time() if now is None else now
    return status if status_cache_fresh(written, now, mark_time, max_age) else None


def companions_from_snapshot(snapshot):
    """What `aiov2_ctl --companions --json` would say, taken from a status that already holds it, or None when
    the status does not (then it has to be asked for)."""
    snap = snapshot if isinstance(snapshot, dict) else {}
    devices = snap.get("companions")
    if not isinstance(devices, list):
        return None
    return {"usb_rail": (snap.get("rails") or {}).get("USB"), "devices": devices}


def fetch_snapshot():
    """The status dict from aiov2_ctl, or {} on any failure. If the tray or the command line read the board
    within the last couple of seconds, that reading is used instead of reading it again."""
    kept = shared_snapshot()
    if kept is not None:
        return kept
    try:
        out = subprocess.run(["aiov2_ctl", "--status", "--json"], text=True,
                             capture_output=True, timeout=TIMEOUT_SECONDS).stdout
        data = json.loads(out)
    except Exception:
        return {}
    return data if isinstance(data, dict) and "rails" in data else {}


def battery_card(snapshot):
    """(headline, detail lines, level) for the battery card. level: ok, warn or none."""
    power = (snapshot or {}).get("power")
    if not power:
        ign = (snapshot or {}).get("gauge_ignored") or {}
        why = ("The battery gauge readings were not believable. Check the battery contacts."
               if ign.get("taken") and ign.get("ignored") == ign.get("taken") else "No battery reading.")
        return "n/a", [why], "none"
    lines = [f"{power['voltage']:.2f} V   {power['current']:+.2f} A   {power['power']:+.2f} W",
             f"{power['status']} ({power['mode']})"]
    level = "ok"
    estimated = battery_estimated(snapshot)
    if estimated:
        lines.append(f"The battery gauge says {power.get('capacity_gauge')}%, which the cell voltage rules out. "
                     f"About {power['capacity']}% is worked out from the voltage instead, and is rough.")
        level = "warn"
    elif snapshot.get("gauge_warning"):
        lines.append("The gauge and the cell voltage disagree. Check the battery.")
        level = "warn"
    ign = snapshot.get("gauge_ignored") or {}
    if ign.get("ignored"):
        lines.append(f"{ign['ignored']} of {ign['taken']} readings ignored as implausible.")
    return percent_text(power["capacity"], estimated), lines, level


def is_charging(power):
    """True only while the battery is being charged. The word "discharging" contains "charging", and looking
    for that inside the status made the status bar say "charging" on battery."""
    p = power or {}
    direction = str(p.get("direction", "")).lower()
    if direction in ("charging", "discharging", "idle"):
        return direction == "charging"
    return str(p.get("status", "")).strip().lower().startswith("charg")


def battery_estimated(snapshot):
    """True when the percentage in this status is aiov2_ctl's estimate from the cell voltage, given because the
    gauge's own figure could not be true."""
    return ((snapshot or {}).get("power") or {}).get("capacity_source") == "voltage"


def percent_text(pct, estimated=False):
    """The percentage as shown: '52%', or '~52%' when it is an estimate and not a measurement."""
    return "---" if pct is None else f"{'~' if estimated else ''}{pct}%"


def known_wh_per_percent(snapshot):
    """Watt hours in one percent of the battery, from the pack size the kernel reports, or None."""
    wh = ((snapshot or {}).get("power") or {}).get("energy_full_wh")
    return wh / 100.0 if isinstance(wh, (int, float)) and 2 <= wh <= 200 else None


def rail_pills(snapshot):
    """[(name, 'on'|'off')] in a stable order."""
    rails = (snapshot or {}).get("rails") or {}
    return [(name, rails[name]) for name in ("GPS", "LORA", "SDR", "USB") if name in rails]


def companion_lines(snapshot):
    """One line per detected companion. Empty when there are none, so the card can hide."""
    out = []
    for d in (snapshot or {}).get("companions") or []:
        label = d.get("label") or d.get("name") or "device"
        out.append(label + (f"  {d['tty']}" if d.get("tty") else ""))
    return out


# ---- tabs: Wi-Fi, Flipper, companions, help ----

def run_json(args, timeout=TIMEOUT_SECONDS):
    """Run aiov2_ctl with args and parse its JSON. {} on any failure."""
    try:
        out = subprocess.run(["aiov2_ctl"] + list(args), text=True,
                             capture_output=True, timeout=timeout).stdout
        result = json.loads(out)
    except Exception:
        return {}
    return result if isinstance(result, dict) else {}


def fetch_wifi_scan():
    return run_json(["--wifi", "scan", "--json"], timeout=45)


def fetch_flipper_status():
    return run_json(["--flipper", "status", "--json"])


FLIPPER_USB = ("0483", "5740")           # the Flipper Zero's USB vendor and product, as aiov2_ctl knows it
FLIPPER_ABSENT = {"absent": True}        # what the window is told when the Flipper has been unplugged


def usb_ids(root="/sys/bus/usb/devices"):
    """[(vendor, product)] of everything on USB right now, read from sysfs. Costs nothing and asks no device."""
    import os
    out = []
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for name in names:
        vendor = _read(os.path.join(root, name, "idVendor")).strip().lower()
        product = _read(os.path.join(root, name, "idProduct")).strip().lower()
        if vendor and product:
            out.append((vendor, product))
    return out


def rails_stamp_path(environ=None, uid=None, isdir=os.path.isdir):
    """The file aiov2_ctl touches whenever a rail is switched, by the tray, the command line or this app. Watching
    it is how a switch made anywhere shows here at once. The rule for where it lives must match aiov2_ctl's
    rails_stamp_path."""
    env = os.environ if environ is None else environ
    if uid is None:
        sudo_uid = env.get("SUDO_UID", "")
        uid = int(sudo_uid) if sudo_uid.isdigit() else os.getuid()
    base = f"/run/user/{uid}"
    return os.path.join(base, "aiov2_ctl.rails") if isdir(base) else f"/tmp/aiov2_ctl-{uid}.rails"


def flipper_plugged(ids=None):
    """True while a Flipper is on USB, whatever mode it is in and whether or not it answers."""
    return FLIPPER_USB in (usb_ids() if ids is None else ids)


def flipper_report(status, plugged):
    """What to tell the window after a Flipper probe. An empty status on its own does not say much: the port may
    just be busy, and then what is on screen should stay. An empty status with no Flipper on USB means it has
    been unplugged, and its tab should go."""
    return status if status or plugged else dict(FLIPPER_ABSENT)


def fetch_radios():
    return run_json(["--wifi", "radios", "--json"])


def _read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def system_lines():
    """CPU temperature, load, memory, disk and uptime, read straight from /proc and /sys."""
    out = []
    raw = _read("/sys/class/thermal/thermal_zone0/temp").strip()
    load = _read("/proc/loadavg").split()
    head = []
    if raw.isdigit():
        head.append(f"CPU {int(raw) / 1000:.0f} C")
    if load:
        head.append("load " + " ".join(load[:3]))
    if head:
        out.append("   ".join(head))
    mem = {}
    for line in _read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        if v.split():
            mem[k] = int(v.split()[0])
    if mem.get("MemTotal"):
        used = mem["MemTotal"] - mem.get("MemAvailable", 0)
        out.append(f"Memory {used / 1048576:.1f} of {mem['MemTotal'] / 1048576:.1f} GB")
    try:
        st = os.statvfs("/")
        total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
        out.append(f"Disk {(total - free) / 1e9:.1f} of {total / 1e9:.1f} GB")
    except OSError:
        pass
    up = _read("/proc/uptime").split()
    if up:
        mins = int(float(up[0]) // 60)
        out.append(f"Up {mins // 1440}d {mins // 60 % 24}h {mins % 60}m")
    return out


def radios_overview(result):
    """One line per Wi-Fi radio. Empty when the answer is missing."""
    out = []
    for r in (result or {}).get("radios") or []:
        kind = str(r.get("kind", "")).replace("-", " ")
        out.append(f"{r.get('name', '?')}  {kind}  {'up' if r.get('up') else 'down'}")
    return out


def fetch_companions(snapshot=None):
    """The companion devices. The status already lists them, so with a status in hand nothing more is asked."""
    known = companions_from_snapshot(snapshot)
    return known if known is not None else run_json(["--companions", "--json"])


def fit_frame(frame_w, frame_h, box_w, box_h):
    """(width, height) to draw a Flipper screen frame at inside its box: the whole frame, as large as fits,
    keeping its shape. A whole-number enlargement is used when one fits, so the Flipper's pixels stay square
    and sharp. The frame used to be shown at its own size, which is larger than the box, so only a corner of
    the Flipper's screen could be seen. (0, 0) for a frame or box with no size."""
    if min(frame_w, frame_h, box_w, box_h) <= 0:
        return 0, 0
    scale = min(box_w / frame_w, box_h / frame_h)
    if scale >= 1:
        scale = int(scale)
    return max(1, int(frame_w * scale)), max(1, int(frame_h * scale))


def fetch_flipper_png(directory, name="screen.png"):
    """Ask for one frame of the Flipper display. Returns the path or None.
    The target must not exist, so any old frame is removed first."""
    path = os.path.join(directory, name)
    try:
        if os.path.exists(path):
            os.remove(path)
        subprocess.run(["aiov2_ctl", "--flipper", "screen", "--png", path],
                       capture_output=True, timeout=10)
    except Exception:
        return None
    return path if os.path.isfile(path) and os.path.getsize(path) > 0 else None


def flipper_detected(status):
    """True when the status names a device or a serial port (bridge mode has no device
    info but is still a Flipper), so the tab stays hidden when nothing is plugged in."""
    return bool(isinstance(status, dict) and (status.get("device") or status.get("port")))


def _fmt(value):
    if isinstance(value, dict):
        return ", ".join(f"{k} {_fmt(v)}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return ", ".join(_fmt(v) for v in value) or "none"
    return str(value)


def _flipper_device(dev):
    """('Flipper Zero (name)', 'unlshd-095 (Unleashed)') from the CLI's long device dictionary."""
    if not isinstance(dev, dict):
        return _fmt(dev), ""
    model = dev.get("hardware_model") or "Flipper"
    name = dev.get("hardware_name")
    fw, fork = dev.get("firmware_version"), dev.get("firmware_origin_fork")
    firmware = " ".join(x for x in (str(fw) if fw else "", f"({fork})" if fork else "") if x)
    return (f"{model} ({name})" if name else model), firmware


def _flipper_battery(bat):
    if isinstance(bat, dict) and bat.get("charge.level") is not None:
        state = bat.get("charge.state")
        return f"{bat['charge.level']}%" + (f" ({state})" if state else "")
    return _fmt(bat)


def _flipper_apps(apps, limit=6):
    if isinstance(apps, (list, tuple)) and len(apps) > limit:
        return ", ".join(map(str, apps[:limit])) + f" and {len(apps) - limit} more"
    return _fmt(apps)


def flipper_lines(status):
    """Short plain text lines for the Flipper status. Empty when no Flipper."""
    if not flipper_detected(status):
        return []
    device, firmware = _flipper_device(status.get("device"))
    shown = {
        "device": device if status.get("device") not in (None, "", [], {}) else None,
        "firmware": firmware or None,
        "port": status.get("port"), "mode": status.get("mode"),
        "battery": _flipper_battery(status["battery"]) if status.get("battery") else None,
        "sd_card": status.get("sd_card"),
        "apps": _flipper_apps(status["apps"]) if status.get("apps") else None,
        "wifi_board": status.get("wifi_board"),
    }
    names = [("device", "Device"), ("firmware", "Firmware"), ("port", "Port"), ("mode", "Mode"),
             ("battery", "Battery"), ("sd_card", "SD card"), ("apps", "Apps"), ("wifi_board", "Wi-Fi board")]
    lines = [f"{label}: {shown[key]}" for key, label in names if shown[key] not in (None, "", [], {})]
    for hint in status.get("suggestions") or []:
        lines.append(f"Tip: {_fmt(hint)}")
    return lines


def flipper_facts(status, with_apps=False):
    """[(label, value)] for a tidy Flipper card. Empty when no Flipper."""
    if not flipper_detected(status):
        return []
    device, firmware = _flipper_device(status.get("device"))
    rows = [("Device", device if status.get("device") not in (None, "", [], {}) else None),
            ("Firmware", firmware or None),
            ("Battery", _flipper_battery(status["battery"]) if status.get("battery") else None),
            ("SD card", status.get("sd_card")),
            ("Mode", {"bridge": "USB-UART bridge", "cli": "CLI"}.get(status.get("mode"), status.get("mode"))),
            ("Port", status.get("port")),
            ("Wi-Fi board", status.get("wifi_board"))]
    if with_apps and status.get("apps"):
        rows.append(("Apps", _flipper_apps(status["apps"])))
    return [(k, str(v)) for k, v in rows if v not in (None, "", [], {})]


def flipper_tips(status):
    return [_fmt(h) for h in (status or {}).get("suggestions") or []]


def facts_html(facts, tips=(), muted="#9e9e9e"):
    """A small two column table, plus tips underneath, as rich text for a label."""
    import html
    rows = "".join(f'<tr><td style="color:{muted}; padding-right:14px">{html.escape(k)}</td>'
                   f'<td><b>{html.escape(v)}</b></td></tr>' for k, v in facts)
    out = f"<table cellspacing='3'>{rows}</table>" if rows else ""
    for tip in tips:
        out += f'<p style="color:{muted}; margin:4px 0 0 0">{html.escape(tip)}</p>'
    return out


def signal_bars(dbm):
    """Four step bar string for a dBm value."""
    if dbm is None:
        return "----"
    steps = sum(dbm >= t for t in (-85, -75, -65, -55))
    return "#" * steps + "-" * (4 - steps)


def band_of(freq_mhz, channel=0):
    if freq_mhz:
        return "2.4" if freq_mhz < 3000 else ("6" if freq_mhz >= 5925 else "5")
    return "2.4" if channel and channel <= 14 else "5"


def wifi_rows(scan):
    """Rows (ssid, signal text, bars, channel, security, radio), strongest first."""
    nets = (scan or {}).get("networks") or []
    nets = sorted(nets, key=lambda n: n.get("signal_dbm") if n.get("signal_dbm") is not None else -999,
                  reverse=True)
    rows = []
    for n in nets:
        dbm = n.get("signal_dbm")
        rows.append((n.get("ssid") or "(hidden)",
                     f"{dbm} dBm" if dbm is not None else "?",
                     signal_bars(dbm), str(n.get("channel", "?")),
                     n.get("security") or "open", n.get("radio") or "?"))
    return rows


def channel_congestion(scan):
    """{band: {channel: count}} counting networks per channel."""
    out = {}
    for n in (scan or {}).get("networks") or []:
        ch = n.get("channel")
        if not ch:
            continue
        band = band_of(n.get("freq_mhz"), ch)
        out.setdefault(band, {})
        out[band][ch] = out[band].get(ch, 0) + 1
    return out


def congestion_summary(scan):
    """Short lines: busiest channel per band and a quiet 2.4 GHz suggestion."""
    cong = channel_congestion(scan)
    if not cong:
        return ["No networks seen."]
    lines = []
    for band in sorted(cong):
        chans = cong[band]
        top = max(chans, key=lambda c: (chans[c], -c))
        lines.append(f"{band} GHz: {sum(chans.values())} networks, busiest channel {top} ({chans[top]})")
    if "2.4" in cong:
        # channels 1, 6 and 11 do not overlap; load counts neighbours within 4 channels
        chans = cong["2.4"]
        load = {c: sum(n for ch, n in chans.items() if abs(ch - c) < 5) for c in (1, 6, 11)}
        best = min(load, key=lambda c: (load[c], c))
        lines.append(f"Quietest 2.4 GHz choice: channel {best} ({load[best]} overlapping)")
    return lines


def radio_lines(scan):
    out = []
    for r in (scan or {}).get("radios") or []:
        out.append(f"{r.get('name', '?')} {r.get('kind', '')} {r.get('driver', '')} "
                   f"{'up' if r.get('up') else 'down'}".replace("  ", " ").strip())
    return out


def companions_text(result, snapshot=None):
    """Lines for the Companions tab from --companions --json, else the status snapshot."""
    items = result.get("companions") if isinstance(result, dict) else None
    if items is None and isinstance(result, dict) and result:
        items = [{"label": k, "tty": v if isinstance(v, str) else ""} for k, v in result.items()]
    lines = []
    for d in items or []:
        if isinstance(d, dict):
            label = d.get("label") or d.get("name") or "device"
            lines.append(label + (f"  {d['tty']}" if d.get("tty") else ""))
        else:
            lines.append(str(d))
    return lines or companion_lines(snapshot) or ["No companion devices detected."]


def tab_keys(tab_names):
    """{'1': 'Overview', ...} for the shown tabs, in order, at most nine."""
    return {str(i + 1): name for i, name in enumerate(tab_names[:9])}


def footer_for(tab, count, live=False):
    base = f"1-{count} or Tab: switch   r: refresh   q: quit"
    extra = {"Wi-Fi": "   s: scan", "Help": "   t: light/dark", "Flipper": "   i: live display " + ("ON" if live else "off")}
    return base + extra.get(tab, "")


# ---- antenna tabs and theme

ANTENNA_RAILS = (("GPS", "GPS"), ("LoRa", "LORA"), ("SDR", "SDR"))


def has_ethernet(base="/sys/class/net"):
    """True when there is a real wired interface (ethernet, not wireless, not docker or a bridge)."""
    try:
        for name in os.listdir(base):
            path = os.path.join(base, name)
            if _read(os.path.join(path, "type")).strip() != "1" or os.path.isdir(os.path.join(path, "wireless")):
                continue
            if "/devices/virtual/" not in os.path.realpath(path):
                return True
    except OSError:
        pass
    return False


def tab_names(snapshot, flipper_shown, marauder_shown=False, home=False, network=False, bluetooth=False,
              everything=False, wiki=False, apps=False, ghost=False):
    """Tabs in order: Home, Overview, Everything, Wi-Fi, Bluetooth, one per powered antenna, Flipper, Settings,
    Help. Bluetooth, like every hardware tab, only while its radio is there (the internal card, on the USB rail).
    Everything is always there in the app: it has no radio of its own and uses whichever are on."""
    rails = (snapshot or {}).get("rails") or {}
    names = (["Home"] if home else []) + ["Overview"] + (["Everything"] if everything else []) + ["Wi-Fi"]
    if bluetooth:
        names.append("Bluetooth")
    if network:
        names.append("Network")
    if marauder_shown:
        names.append("Marauder")
    names += [tab for tab, rail in ANTENNA_RAILS if rails.get(rail) == "on"]
    if flipper_shown:
        names.append("Flipper")
    if apps:                                    # the installed programs, when switched on in Settings
        names.append("Apps")
    names += ["Settings", "Help"]
    if wiki:                                    # the local wiki, when the installer's feature is on: after Help
        names.append("Wiki")
    if ghost:                                   # the optional Ghost hunt add-on, only when it is installed
        names.append("Ghost hunt")
    return names


def _unit_active(unit):
    try:
        r = subprocess.run(["systemctl", "is-active", unit], text=True, capture_output=True, timeout=5)
        return r.stdout.strip() == "active"
    except Exception:
        return False


def _usb_has(vid_pid):
    try:
        out = subprocess.run(["lsusb"], text=True, capture_output=True, timeout=5).stdout
    except Exception:
        return False
    return vid_pid in out


def antenna_lines(name, snapshot):
    """Short, passive status for a GPS, LoRa or SDR tab. Reads only."""
    rails = (snapshot or {}).get("rails") or {}
    rail = dict(ANTENNA_RAILS).get(name)
    lines = [f"Power rail {rail}: {rails.get(rail, 'unknown')}"]
    if name == "GPS":
        lines.append("gpsd: " + ("running" if _unit_active("gpsd") else "not running"))
    elif name == "LoRa":
        lines.append("meshtasticd: " + ("running" if _unit_active("meshtasticd") else "stopped"))
        lines.append("SPI radio node: " + ("present" if os.path.exists("/dev/spidev1.0") else "missing"))
    elif name == "SDR":
        lines.append("RTL-SDR dongle: " + ("detected" if _usb_has("0bda:2838") else "not detected"))
    return lines


def resolve_theme(saved, system_dark):
    """'dark' or 'light'. A saved choice wins, otherwise follow the system."""
    if saved in ("dark", "light"):
        return saved
    return "dark" if system_dark else "light"


def settings_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "ops_gui", "settings.json")


def load_settings(path=None):
    try:
        with open(path or settings_path()) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(values, path=None):
    path = path or settings_path()
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        tmp = path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(values, f)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


# ---- pixel icons

TAB_ICONS = {"Overview": "blocks", "Wi-Fi": "globe", "GPS": "compass", "LoRa": "email",
             "SDR": "search", "Flipper": "code", "Settings": "cog", "Help": "notes", "Wiki": "domain", "Apps": "rocket", "Marauder": "shield", "Home": "heartbeat", "Network": "server",
             "Bluetooth": "bluetooth", "Everything": "growth", "Ghost hunt": "media"}

SUN = [
    "................",
    ".......##.......",
    "..#....##....#..",
    "...#........#...",
    "......####......",
    "....########....",
    "....########....",
    ".##.########.##.",
    ".##.########.##.",
    "....########....",
    "....########....",
    "......####......",
    "...#........#...",
    "..#....##....#..",
    ".......##.......",
    "................",
]

MOON = [
    "................",
    ".....#####......",
    "....####........",
    "...####.........",
    "..####..........",
    "..####..........",
    ".####...........",
    ".####...........",
    ".####...........",
    ".####...........",
    "..####..........",
    "..####.....##...",
    "...####..####...",
    "....########....",
    ".....######.....",
    "................",
]


TERMINAL = [
    "................",
    ".##############.",
    ".#............#.",
    ".#............#.",
    ".#.##.........#.",
    ".#..##........#.",
    ".#...##.......#.",
    ".#....##......#.",
    ".#...##.......#.",
    ".#..##........#.",
    ".#.##.....###.#.",
    ".#............#.",
    ".#............#.",
    ".##############.",
    "................",
    "................",
]

FULLSCREEN = [
    "................",
    "................",
    ".####......####.",
    ".#............#.",
    ".#............#.",
    ".#............#.",
    "................",
    "................",
    "................",
    "................",
    ".#............#.",
    ".#............#.",
    ".#............#.",
    ".####......####.",
    "................",
    "................",
]


def tab_icon_path(tab, size=32):
    """Path of the pixel icon for a tab, or None when the file is missing."""
    name = TAB_ICONS.get(tab)
    if not name:
        return None
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", str(size), f"{name}-bare.png")
    return path if os.path.exists(path) else None


# ---- rail control and measured power

RAILS = ("GPS", "LORA", "SDR", "USB")
RAIL_LABEL = {"GPS": "GPS", "LORA": "LoRa", "SDR": "SDR", "USB": "USB"}
RAIL_KEYS = {"GPS": "g", "LORA": "m", "SDR": "d", "USB": "u"}


def run_privileged(args, timeout):
    """aiov2_ctl through sudo -n, the same way the CLI itself escalates. (ok, stdout)."""
    try:
        r = subprocess.run(["sudo", "-n", "aiov2_ctl", *args], text=True,
                           capture_output=True, timeout=timeout)
    except Exception as e:
        return False, str(e)
    return r.returncode == 0, (r.stdout or r.stderr or "").strip()


def set_rail(name, on, runner=run_privileged):
    """Turn one power rail on or off. Returns (ok, message)."""
    if name not in RAILS:
        return False, f"Unknown rail {name}."
    ok, out = runner([name, "on" if on else "off"], 40)
    word = "on" if on else "off"
    return ok, (f"{RAIL_LABEL[name]} rail turned {word}." if ok else
                f"Could not turn {RAIL_LABEL[name]} {word}: {out or 'no answer from aiov2_ctl'}")


def parse_measure(text):
    """{'off': W, 'on': W, 'delta': W, 'source': 'AC'|'battery'} from aiov2_ctl --measure."""
    import re
    def num(label):
        m = re.search(rf"^{label}\s*:\s*([0-9.]+) W", text or "", re.M)
        return float(m.group(1)) if m else None
    off, on = num("OFF"), num("ON ?")
    if off is None or on is None:
        return None
    src = "AC" if "AC online" in (text or "") else "battery"
    return {"off": off, "on": on, "delta": round(on - off, 2), "source": src}


def measure_rail(name, runner=run_privileged):
    """Measure one rail's draw by toggling it briefly. aiov2_ctl puts the rail back."""
    if name not in RAILS:
        return None
    ok, out = runner(["--measure", name, "--seconds", "3", "--settle", "1"], 90)
    return parse_measure(out) if ok else None


def rail_cache_path():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "ops_gui", "rail-power.json")


def load_rail_power(path=None):
    return load_settings(path or rail_cache_path())


def save_rail_power(name, result, when, path=None):
    path = path or rail_cache_path()
    cache = load_rail_power(path)
    cache[name] = dict(result, when=when)
    return save_settings(cache, path)


def rail_power_text(name, cache):
    """Words for the power column. The board has one gauge, so these are measured, not live."""
    m = (cache or {}).get(name)
    if not m or "delta" not in m:
        return "not measured"
    if m.get("source") == "AC":
        return "not reliable on AC: unplug the charger, then measure again"
    return f"about {m['delta']:+.2f} W (measured {m.get('when', '?')}, on {m.get('source', '?')})"


def rails_estimate(snapshot, cache):
    """Sum of the measured draw of the rails that are on, or None if none were measured."""
    rails = (snapshot or {}).get("rails") or {}
    parts = [cache[n]["delta"] for n in RAILS if rails.get(n) == "on" and n in (cache or {})
             and "delta" in cache[n] and cache[n].get("source") != "AC"]
    return round(sum(parts), 2) if parts else None


def rail_warning(name, turning_on, snapshot, radios=None, flipper_shown=False):
    """What turning a rail off will take away. Empty when there is nothing to warn about."""
    if turning_on:
        return ""
    if name == "USB":
        bits = []
        if any(r.get("kind") == "internal-usb" for r in (radios or {}).get("radios", [])):
            bits.append("the internal Wi-Fi (wlan1)")
        if flipper_shown:
            bits.append("the Flipper link")
        return "Turning USB off drops " + (" and ".join(bits) if bits else "everything on the AIO USB hub") + "."
    if name == "SDR":
        return "Turning SDR off stops anything using the RTL-SDR dongle."
    if name == "LORA":
        return "Turning LoRa off stops meshtasticd."
    return ""


def measure_warning(name):
    extra = " It also stops and restarts meshtasticd." if name == "LORA" else ""
    if name == "USB":
        extra = " The internal Wi-Fi and the Flipper link drop for a few seconds."
    return (f"Measuring {RAIL_LABEL.get(name, name)} switches the rail the other way for about 10 seconds "
            f"and puts it back.{extra} On AC power the battery gauge cannot see the load, so the "
            "figure is only trustworthy on battery.")


RAIL_INFO = {
    "GPS": ("GPS", "compass", "u-blox GNSS receiver"),
    "LORA": ("LoRa", "email", "SX1262 mesh radio"),
    "SDR": ("SDR", "search", "RTL-SDR dongle, 24 MHz and up"),
    "USB": ("USB hub", "server", "AIO hub: internal Wi-Fi, Flipper"),
}


def on_battery(snapshot):
    """True only when the gauge says we are running on the battery (so a measurement means something)."""
    power = (snapshot or {}).get("power") or {}
    return bool(power) and power.get("source") != "AC"


def rail_icon_path(name, size=32):
    icon = RAIL_INFO.get(name, (None, None, None))[1]
    if not icon:
        return None
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", str(size), f"{icon}-bare.png")
    return path if os.path.exists(path) else None


# ---- responsive layout

def rail_columns(width):
    """Rail rows: one column on a narrow window, two once there is room."""
    return 1 if width < 820 else 2


def lower_columns(width):
    """Companions, radios and system cards: stack, pair up, or sit in a row as width allows."""
    if width < 760:
        return 1
    if width < 1150:
        return 2
    return 3 if width < 1240 else 4


def stat_tiles(snapshot):
    """[(caption, value)] for the battery row, empty when there is no reading."""
    power = (snapshot or {}).get("power")
    if not power:
        return []
    return [("Voltage", f"{power['voltage']:.2f} V"), ("Current", f"{power['current']:+.2f} A"),
            ("Power", f"{power['power']:+.2f} W")]


def marauder_present(radios_result):
    """True when `aiov2_ctl --wifi radios` lists the Flipper's Marauder board."""
    return any(isinstance(r, dict) and r.get("kind") == "flipper-marauder"
               for r in (radios_result or {}).get("radios") or [])
