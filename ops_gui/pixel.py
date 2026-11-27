"""
Pure helpers for the pixel art Home screen: fonts, palettes, layout and the live data behind each block.

The look is Debian flavoured (Debian red frames, a warm orange, and a cold cyan for live values) drawn with the
Spleen pixel font that Debian packages as fonts-spleen. Nothing here needs Qt, so it can all be tested anywhere.
Rule of thumb for a handheld: anything that carries data is drawn at 24 pixels or more; 16 is only for titles.
"""
import re
import time

# Spleen ships one font per size; each is crisp only at its own pixel height.
SPLEEN = {12: "Spleen 6x12", 16: "Spleen 8x16", 24: "Spleen 12x24", 32: "Spleen 16x32", 64: "Spleen 32x64"}
DEBIAN_RED, DEBIAN_DARK, UBUNTU_ORANGE = "#d70a53", "#a80030", "#e95420"

PALETTES = {
    "dark": {"bg": "#0b090d", "dots": "#17111d", "panel": "#15111a", "raised": "#1d1724", "line": "#3b2f48",
             "text": "#f6f2fa", "dim": "#b9aec6", "accent": DEBIAN_RED, "accent2": UBUNTU_ORANGE,
             "label": "#ff7a45", "data": "#3fe0d0", "good": "#62e06b", "warn": "#ffd23f", "hot": "#ff3b3b",
             "off": "#463d50", "screen": "#06050a"},
    "light": {"bg": "#f4efe6", "dots": "#e7dfd0", "panel": "#fffaf0", "raised": "#f0e8da", "line": "#bfae9a",
              "text": "#2a2230", "dim": "#5f5468", "accent": DEBIAN_RED, "accent2": UBUNTU_ORANGE,
              "label": "#b8400f", "data": "#0b7f76", "good": "#1f8f2c", "warn": "#a86b00", "hot": "#c62828",
              "off": "#cfc4b6", "screen": "#2a2230"},
}
# F6 is a sixth key and not a new job for one of the five: each of those is used, and six still fit (198 px
# each on the 1280 wide screen, where the longest label needs 138).
SOFT_KEYS = (("F1", "SCAN"), ("F2", "RAILS"), ("F3", "TERM"), ("F4", "THEME"), ("F5", "FULL"), ("F6", "RECORD"))
TICKER_TICKS = 8                     # half second ticks per message, so 4 seconds
RACK_NAMES = ("GPS", "LORA", "SDR", "USB", "FLIPPER", "WIFI")


# High contrast (Settings > General): pure black and white, solid lines, no dotted background. The colours that
# carry meaning stay colours, each strong enough to read on its black or white.
CONTRAST_PALETTES = {
    "dark": {"bg": "#000000", "dots": "#000000", "panel": "#000000", "raised": "#000000", "line": "#ffffff",
             "text": "#ffffff", "dim": "#d6d6d6", "accent": DEBIAN_RED, "accent2": UBUNTU_ORANGE,
             "label": "#ff9a6b", "data": "#5ff0e2", "good": "#6dff78", "warn": "#ffe066", "hot": "#ff7b7b",
             "off": "#5c5c5c", "screen": "#000000"},
    "light": {"bg": "#ffffff", "dots": "#ffffff", "panel": "#ffffff", "raised": "#ffffff", "line": "#000000",
              "text": "#000000", "dim": "#2b2b2b", "accent": DEBIAN_RED, "accent2": UBUNTU_ORANGE,
              "label": "#8a2c00", "data": "#005a53", "good": "#0e5c1a", "warn": "#6f4600", "hot": "#9e1212",
              "off": "#a8a8a8", "screen": "#000000"},
}
HIGH_CONTRAST = False


def set_accent(accent, accent2):
    """Use the feature colour on the Home screen too."""
    for table in (PALETTES, CONTRAST_PALETTES):
        for pal in table.values():
            pal["accent"], pal["accent2"] = accent, accent2


def set_contrast(on):
    """Switch the hand-drawn screens to their high contrast colours, or back. Returns whether it is on."""
    global HIGH_CONTRAST
    HIGH_CONTRAST = bool(on)
    return HIGH_CONTRAST


def palette(mode):
    table = CONTRAST_PALETTES if HIGH_CONTRAST else PALETTES
    return table["light" if mode == "light" else "dark"]


def nearest_size(px):
    """The Spleen size at or below `px` (12 at the smallest)."""
    fits = [s for s in SPLEEN if s <= px]
    return max(fits) if fits else min(SPLEEN)


def font_family(px, available):
    """The Spleen family for a size when it is installed, else Terminus, else None (use the default)."""
    fam = SPLEEN.get(nearest_size(px))
    if fam in available:
        return fam
    return "Terminus" if "Terminus" in available else None


def level_for(value, warn, hot, previous=None, margin=5):
    """'good', 'warn' or 'hot' for a reading. With `previous`, a level is only left once the value is
    `margin` clear of its threshold, so a number hovering on a line does not make the colour flicker."""
    if value is None:
        return "dim"
    level = "hot" if value >= hot else "warn" if value >= warn else "good"
    if previous in ("good", "warn", "hot") and previous != level:
        order = {"good": 0, "warn": 1, "hot": 2}
        if order[previous] > order[level]:             # coming down: hold until clearly below the line
            line = hot if previous == "hot" else warn
            if value > line - margin:
                return previous
    return level


# Temperature runs cold blue, cool cyan, comfortable green, warm yellow, hot red.
TEMP_STOPS = ((30.0, (74, 163, 255)), (45.0, (63, 224, 208)), (55.0, (98, 224, 107)),
              (68.0, (255, 210, 63)), (80.0, (255, 59, 59)))
HISTORY = 90                          # samples kept for the line charts (one a second)
SPARK_RANGES = {"CPU": (0.0, 100.0), "TEMP": (25.0, 85.0), "RAM": (0.0, 100.0), "DISK": (0.0, 100.0)}


def temp_colour(temp, dark=True):
    """Hex colour for a temperature, blending between the stops. None for no reading."""
    if temp is None:
        return None
    stops = TEMP_STOPS
    if temp <= stops[0][0]:
        rgb = stops[0][1]
    elif temp >= stops[-1][0]:
        rgb = stops[-1][1]
    else:
        for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
            if t0 <= temp <= t1:
                k = (temp - t0) / (t1 - t0)
                rgb = tuple(round(a + (b - a) * k) for a, b in zip(c0, c1))
                break
    if not dark:
        rgb = tuple(round(v * 0.62) for v in rgb)        # darker on paper so it stays readable
    return "#%02x%02x%02x" % rgb


def push_history(history, value, limit=HISTORY):
    """The history with a new value on the end, trimmed. A missing reading repeats the last one."""
    if value is None:
        value = history[-1] if history else None
    return (list(history) + [value])[-limit:]


def spark_columns(values, lo, hi, width, height, step=4):
    """[(x, y)] one per `step` pixels, newest at the right edge; y runs down from the top of the box.

    Points are snapped to a 2 pixel grid so the line looks drawn by hand in pixels, not smoothed."""
    cols = max(1, width // step)
    vals = [v for v in values[-cols:] if v is not None]
    if not vals or hi <= lo:
        return []
    out = []
    first = cols - len(vals)
    for i, v in enumerate(vals):
        frac = min(1.0, max(0.0, (v - lo) / (hi - lo)))
        y = (height - 2) - int(frac * (height - 2)) // 2 * 2
        out.append(((first + i) * step, y))
    return out


def rack_toggle(row_index):
    """The rail a rack row switches, or None for rows (Flipper, Wi-Fi) that are not rails."""
    return ("GPS", "LORA", "SDR", "USB")[row_index] if 0 <= row_index < 4 else None


def rack_at(x, y, rack_rects):
    return key_at(x, y, rack_rects)


def blend(a, b, k):
    """A hex colour k of the way from a to b (k from 0 to 1)."""
    k = min(1.0, max(0.0, k))
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * k) for x, y in zip(ca, cb))


def lit_segments(percent, segments=10):
    if percent is None or percent <= 0:
        return 0
    return min(segments, -(-int(percent) * segments // 100))


def clock_text(now=None, colon=True):
    return time.strftime("%H:%M" if colon else "%H %M", time.localtime(now))


def date_text(now=None):
    return time.strftime("%a %d %b", time.localtime(now)).upper()


# ---- layout: real pixels from the size of the widget

def layout(width, height):
    """Rectangles (x, y, w, h) for every block of the Home screen."""
    m, g, kh, top_h = 12, 8, 48, 120
    col_w = (width - 2 * m - 2 * g) // 3
    xs = [m, m + col_w + g, m + 2 * (col_w + g)]
    widths = [col_w, col_w, width - m - xs[2]]
    main_y = m + top_h + g
    main_h = max(200, height - main_y - g - kh - m)
    tab = 36                                    # room for a panel's title tab
    rack_h = (main_h - tab) // 6
    vit_h = (main_h - tab) // 4
    link_h = (main_h - 2 * g) // 3
    n = len(SOFT_KEYS)
    key_w = (width - 2 * m - (n - 1) * g) // n
    return {
        "clock": (xs[0], m, widths[0], top_h),
        "power": (xs[1], m, widths[1], top_h),
        "ticker": (xs[2], m, widths[2], top_h),
        "rack_panel": (xs[0], main_y, widths[0], main_h),
        "rack": [(xs[0], main_y + tab + i * rack_h, widths[0], rack_h) for i in range(6)],
        "vitals_panel": (xs[1], main_y, widths[1], main_h),
        "vitals": [(xs[1], main_y + tab + i * vit_h, widths[1], vit_h) for i in range(4)],
        "links": [(xs[2], main_y + i * (link_h + g), widths[2], link_h) for i in range(3)],
        "keys": [(m + i * (key_w + g), height - m - kh, key_w, kh) for i in range(n)],
    }


def key_text_x(key_width):
    """(x of the key's name, x of its label) inside a soft key. On a narrow window the two sit closer to the
    edge and to each other so the longest label still ends inside the key."""
    longest = max(len(label) for _, label in SOFT_KEYS) * 12
    return (18, 66) if 66 + longest + 26 <= key_width else (8, 40)


def all_rects(lay):
    """Every drawable rectangle in a layout, flattened, for fit and overlap checks."""
    out = [lay["clock"], lay["power"], lay["ticker"], lay["rack_panel"], lay["vitals_panel"]]
    out += lay["links"] + lay["keys"]
    return out


def key_at(x, y, key_rects):
    for i, (kx, ky, kw, kh) in enumerate(key_rects):
        if kx <= x < kx + kw and ky <= y < ky + kh:
            return i
    return None


# ---- numbers: fixed width fields so values do not jitter

def pct_text(v):
    return "  -" if v is None else f"{v:3.0f}%"


def temp_text(v):
    return "  -" if v is None else f"{v:3.0f}C"


def uptime_short(seconds):
    s = int(seconds)
    d, rest = divmod(s, 86400)
    h, rest = divmod(rest, 3600)
    return f"{d}d{h}h" if d else f"{h}h{rest // 60:02d}" if h else f"{rest // 60}m"


def average(values, count=3):
    vals = [v for v in values[-count:] if v is not None]
    return sum(vals) / len(vals) if vals else None


def vital_rows(stats, previous=None):
    """The four vitals: [(label, value text, percent, level)]. `previous` is the last list, for hysteresis."""
    stats = stats or {}
    mem = stats.get("mem")
    cpu, temp, disk = stats.get("cpu"), stats.get("temp"), stats.get("disk")
    ram = mem[2] if mem else None
    prev = {r[0]: r[3] for r in previous or []}
    return [
        ("CPU", pct_text(cpu), cpu, level_for(cpu, 70, 90, prev.get("CPU"))),
        ("TEMP", temp_text(temp), None if temp is None else min(100.0, temp), level_for(temp, 65, 80, prev.get("TEMP"))),
        ("RAM", "  -" if not mem else f"{mem[0]:.1f}G", ram, level_for(ram, 75, 90, prev.get("RAM"))),
        ("DISK", pct_text(disk), disk, level_for(disk, 80, 92, prev.get("DISK"))),
    ]


# ---- data parsing

def parse_ip_addr(text):
    """{interface: first IPv4 address} from `ip -4 -o addr`."""
    out = {}
    for line in (text or "").splitlines():
        m = re.match(r"^\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)", line)
        if m and m.group(1) != "lo":
            out.setdefault(m.group(1), m.group(2))
    return out


def parse_nmcli_dev(text):
    """{device: (state, connection)} from `nmcli -t -f DEVICE,STATE,CONNECTION dev`."""
    out = {}
    for line in (text or "").splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[0]:
            out[parts[0]] = (parts[1], ":".join(parts[2:]) if parts[2] != "--" else "")
    return out


def parse_gpsd(lines):
    """(mode, satellites used) from gpsd's JSON lines. Mode is 0 to 3, None when nothing useful came."""
    import json
    mode, used = None, None
    for line in lines or []:
        try:
            msg = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(msg, dict):
            continue
        if msg.get("class") == "TPV" and isinstance(msg.get("mode"), int):
            mode = msg["mode"]
        elif msg.get("class") == "SKY" and isinstance(msg.get("satellites"), list):
            used = sum(1 for s in msg["satellites"] if isinstance(s, dict) and s.get("used"))
    return mode, used


def parse_rtl_cmdline(text):
    """Frequency in MHz from an rtl_sdr command line (arguments separated by NUL or space), else None."""
    parts = (text or "").replace("\x00", " ").split()
    if not parts or not parts[0].endswith("rtl_sdr"):
        return None
    for i, p in enumerate(parts[:-1]):
        if p == "-f":
            try:
                return int(parts[i + 1]) / 1e6
            except ValueError:
                return None
    return None


def interface_cards(radios, addrs, nmdev):
    """[{name, state, detail, ip, level}] for the Wi-Fi radios, in the order they were reported."""
    cards = []
    for r in (radios or {}).get("radios") or []:
        if not isinstance(r, dict) or not r.get("name"):
            continue
        name, up = r["name"], bool(r.get("up"))
        state, conn = (nmdev or {}).get(name, ("", ""))
        kind = str(r.get("kind", "")).replace("-", " ")
        cards.append({"name": name, "state": "UP" if up else "DOWN", "ip": (addrs or {}).get(name, ""),
                      "detail": conn or ("Marauder board" if "marauder" in name else kind.upper()),
                      "level": "good" if up else "off"})
    return cards


def ethernet_card(base="/sys/class/net", addrs=None):
    """A radio-style card for the first wired interface that has a cable plugged in, or None.

    Read straight from sysfs (cheap), so the Home screen can show a cable the moment it is plugged in."""
    import os
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return None
    for name in names:
        path = os.path.join(base, name)

        def read(leaf):
            try:
                with open(os.path.join(path, leaf)) as f:
                    return f.read().strip()
            except OSError:
                return ""
        if read("type") != "1" or os.path.isdir(os.path.join(path, "wireless")) or \
                "/devices/virtual/" in os.path.realpath(path):
            continue
        if read("carrier") != "1":
            continue
        speed = read("speed")
        detail = f"{speed} MBIT/S" if speed.isdigit() and int(speed) > 0 else "CABLE IN"
        return {"name": name, "state": "LINK", "detail": detail, "ip": (addrs or {}).get(name, ""), "level": "good"}
    return None


def channel_histogram(networks):
    """[(label, count)] for 2.4 GHz channels 1 to 13 then 5 GHz buckets, from a Wi-Fi scan's networks."""
    two = {c: 0 for c in range(1, 14)}
    five = {"36": 0, "52": 0, "100": 0, "149": 0}
    for n in networks or []:
        ch = n.get("channel") if isinstance(n, dict) else None
        if not isinstance(ch, int):
            continue
        if 1 <= ch <= 13:
            two[ch] += 1
        elif ch >= 36:
            key = "36" if ch < 52 else "52" if ch < 100 else "100" if ch < 149 else "149"
            five[key] += 1
    return [(str(c), n) for c, n in two.items()] + list(five.items())


def battery_summary(snapshot):
    """(percent or None, state text, watts text) for the power block."""
    power = (snapshot or {}).get("power") or {}
    pct = power.get("capacity")
    if pct is None:
        return None, "NO READING", ""
    status = str(power.get("status", "")).upper()
    state = "DISCHARGING" if "DISCH" in status else "CHARGING" if "CHARG" in status else status or "ON AC"
    watts = power.get("power")
    return pct, state, "" if watts is None else f"{watts:+.2f}W"


BATTERY_BOLT = ["....###", "...###.", "..###..", ".###...", "######.", ".######", "...###.", "..###..", ".###...", "##....."]
BATTERY_BARS = 5                     # the Home battery: half the Overview's ten bars, so it fits beside the percentage
BATTERY_UNIT = 2                     # screen pixels per icon pixel
BATTERY_GAP = 12                     # between the battery and the percentage


def battery_size(bars=BATTERY_BARS, unit=BATTERY_UNIT):
    """(width, height) in screen pixels of the pixel battery, cap included."""
    return (4 * bars + 10) * unit, 20 * unit


def battery_cells(pct, charging=False, phase=0, bars=BATTERY_BARS):
    """The pixel battery as [(x, y, w, h, role)] in icon pixels: the same drawing as the Overview's, with fewer
    bars. Roles: wall, lit, sweep (the bright bar that runs along while charging), pulse (the next bar,
    blinking while charging), empty, stroke and bolt (the charging mark). No reading draws an empty battery."""
    body = 4 * bars + 7
    cells = [(1, 0, body - 2, 2, "wall"), (1, 18, body - 2, 2, "wall"), (0, 1, 2, 18, "wall"),
             (body - 2, 1, 2, 18, "wall"), (body, 6, 2, 8, "wall"), (body + 2, 7, 1, 6, "wall")]
    share = 100 / bars
    lit = 0 if not pct or pct <= 0 else min(bars, -(-int(pct) // int(share)))
    for i in range(bars):
        role = "lit" if i < lit else "empty"
        if charging and i < lit and (i - phase) % (lit + 3) == 0:
            role = "sweep"
        if charging and i == lit and (phase // 3) % 2 == 0:
            role = "pulse"
        cells.append((4 + i * 4, 4, 3, 12, role))
    if charging:
        left = (body - len(BATTERY_BOLT[0])) // 2
        mark = {(x, y) for y, row in enumerate(BATTERY_BOLT) for x, c in enumerate(row) if c == "#"}
        edge = {(x + dx, y + dy) for x, y in mark for dx in (-1, 0, 1) for dy in (-1, 0, 1)} - mark
        cells += [(left + x, 5 + y, 1, 1, "stroke") for x, y in sorted(edge)]
        cells += [(left + x, 5 + y, 1, 1, "bolt") for x, y in sorted(mark)]
    return cells


def power_chars(block_width, pct, estimated=False, icon=0):
    """How many 24 px characters fit on the power block's lines, to the right of the big percentage.
    An estimated percentage is drawn with a leading ~, which takes the room of one more big character.
    `icon` is the width taken by the battery drawn to the left of the percentage, gap included."""
    big = (len("---" if pct is None else f"{pct}%") + (1 if estimated and pct is not None else 0)) * 32
    return max(0, (block_width - 28 - big - 12 - icon) // 12)


def power_line(watts, options, chars):
    """The power block's second line: the watts, then the time left when there is room for it. `options` are
    ways of saying the time, longest first; the first that fits beside the watts is used, and the watts are
    never dropped for it."""
    for extra in options or ():
        line = f"{watts} {extra}".strip()
        if len(line) <= chars:
            return line
    return watts


def session_text(seconds):
    """How long a session has been recording, for the ticker: '<1 MIN', '12 MIN', '1H 05M'."""
    minutes = int(max(0, seconds) // 60)
    if minutes < 1:
        return "<1 MIN"
    return f"{minutes} MIN" if minutes < 60 else f"{minutes // 60}H {minutes % 60:02d}M"


# ---- the rack: one row per thing you care about

RACK_TABS = {"FLIPPER": "Flipper", "BLUETOOTH": "Bluetooth", "WIFI": "Wi-Fi"}


def rack_tab(row_name):
    """The tab a rack row that is not a rail opens when it is tapped, or None."""
    return RACK_TABS.get(row_name)


def rack_rows(snapshot, gps=None, sdr_mhz=None, mesh_up=None, flip_mode=None, wifi=None, bt=None):
    """[(name, level, value)] for GPS, LORA, SDR, USB, a companion row and WIFI, always six rows.

    `gps` is (mode, satellites used) or None, `wifi` is (network count, best SSID) or None. `bt` is None when
    the internal card's Bluetooth radio is not there (the USB rail is off), otherwise how many addresses the last
    scan heard (0 before any scan). The fifth row is the Flipper while one is plugged in and answering; with no
    Flipper it is the Bluetooth radio, when that is there. It never shows a thing that is not there to use.
    """
    rails = (snapshot or {}).get("rails") or {}
    on = lambda key: rails.get(key) == "on"
    rows = []
    if not on("GPS"):
        rows.append(("GPS", "off", "OFF"))
    else:
        mode, sats = gps if gps else (None, None)
        text = {3: "3D FIX", 2: "2D FIX"}.get(mode, "NO FIX" if mode is not None else "WAITING")
        if sats:
            text += f" {sats}S"
        rows.append(("GPS", "good" if mode in (2, 3) else "warn", text))
    rows.append(("LORA", "off", "OFF") if not on("LORA") else
                ("LORA", "good" if mesh_up else "warn", "MESH UP" if mesh_up else "NO SERVICE"))
    if not on("SDR"):
        rows.append(("SDR", "off", "OFF"))
    else:
        rows.append(("SDR", "good" if sdr_mhz else "dim", f"{sdr_mhz:.3f}M" if sdr_mhz else "IDLE"))
    rows.append(("USB", "good" if on("USB") else "off", "HUB ON" if on("USB") else "OFF"))
    if flip_mode in ("cli", "bridge") or bt is None:
        rows.append(("FLIPPER", "good" if flip_mode in ("cli", "bridge") else "off",
                     {"cli": "CLI", "bridge": "BRIDGE"}.get(flip_mode, "NONE")))
    else:
        rows.append(("BLUETOOTH", "good", f"{bt} HEARD" if bt else "READY"))
    count, best = wifi if wifi else (None, "")
    rows.append(("WIFI", "good" if count else "dim", f"{count} AP" if count else "NO SCAN"))
    return rows


# ---- the ticker screen

def ticker_messages(ctx):
    """[(headline, sub line, level, pinned)] to cycle through. Warnings are pinned and shown on their own."""
    c = ctx or {}
    msgs = []
    pct = c.get("pct")
    if pct is not None and pct < 15 and not c.get("charging"):
        msgs.append(("LOW BATTERY " + f"{pct}%", "PLUG IN SOON", "hot", True))
    if c.get("temp") is not None and c["temp"] >= 80:
        msgs.append((f"HOT {c['temp']:.0f}C", "CPU IS THROTTLING", "hot", True))
    if c.get("throttled"):
        msgs.append(("POWER WARNING", str(c["throttled"]).upper()[:24], "hot", True))
    if any(m[3] for m in msgs):
        return msgs
    # no plain "BATTERY n%" message here: the power card already shows that permanently, so repeating it
    # in the ticker would put the same reading on screen twice at once.
    # only what is switched on or plugged in gets a turn: a rail that is off is already shown as off in the
    # rack beside it, and saying so again here only pushes the live readings out of sight
    # a session that is recording goes first: it is the one thing here that was started and has to be stopped
    if c.get("session") is not None:
        msgs.append(("RECORDING " + session_text(c["session"]), "EVERYTHING SESSION", "good", False))
    gps = c.get("gps")
    if gps:
        msgs.append(("GPS " + gps[0], gps[1], gps[2], False))
    if c.get("lora") is not None:
        msgs.append(("MESH UP", "LORA RAIL IS ON", "good", False) if c["lora"]
                    else ("LORA ON", "MESH SERVICE NOT RUNNING", "warn", False))
    wifi = c.get("wifi")
    msgs.append((f"WIFI {wifi[0]} AP", ("BEST " + wifi[1])[:26], "data", False) if wifi
                else ("WIFI", "PRESS F1 TO SCAN", "dim", False))
    if c.get("bt") is not None:
        msgs.append((f"BLUETOOTH {c['bt']} HEARD", "IN THE LAST SCAN", "data", False) if c["bt"]
                    else ("BLUETOOTH READY", "OPEN ITS TAB TO LISTEN", "dim", False))
    if c.get("sdr"):
        msgs.append(("SDR " + c["sdr"], "RECEIVING", "data", False))
    if c.get("flipper"):
        msgs.append(("FLIPPER " + str(c["flipper"]).upper(), "PLUGGED IN", "data", False))
    if c.get("uptime") is not None:
        msgs.append(("UP " + uptime_short(c["uptime"]), f"LOAD {c.get('load', 0):.2f}", "dim", False))
    if c.get("ip"):
        msgs.append((c["ip"], (c.get("ssid") or "").upper()[:26], "data", False))
    return msgs


def pick_ticker(messages, tick):
    """The message to show on this tick: pinned warnings stay, others change every TICKER_TICKS ticks."""
    if not messages:
        return ("", "", "dim", False)
    pinned = [m for m in messages if m[3]]
    pool = pinned or messages
    return pool[(tick // TICKER_TICKS) % len(pool)]


def fit(text, chars):
    """Cut text to a width in characters, ending in ~ when it was too long."""
    return text if len(text) <= chars else text[:max(1, chars - 1)] + "~"
