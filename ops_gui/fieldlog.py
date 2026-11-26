"""
The field log: a detailed record of a session, for reading afterwards.

The plain debug log says what the app did. This says what the hardware reported while it did it, so that an hour
outdoors can be gone through later: every few seconds the GPS reading (fix, position, both altitudes, error
estimates, satellites and their signal), every half minute the rails, battery, temperature and what each
listening page has gathered, plus anything the kernel said about the radios, and every line of the activity log.

It follows the same switch as the debug log (Settings > Debug) and the same rules: off until switched on, kept
only on this uConsole in a private file, never sent anywhere, never put into a problem report (it holds
positions), and removed by the same Clear button. One file a day, one JSON object a line, written straight
away so a flat battery loses nothing. A day's file stops growing at FILE_CAP.

The helpers at the top are pure so they can be tested without a receiver or a window.
"""
import json
import os
import re
import subprocess
import threading
import time

GPS_EVERY = 5.0              # seconds between GPS lines while the GPS rail is on
STATE_EVERY = 30.0           # seconds between state lines, and between looks at the kernel log
FILE_CAP = 20 * 1024 * 1024  # a day's file stops growing here (an hour is about half a megabyte)
KERNEL_WORDS = re.compile(r"hci\d|bluetooth|usb \d|mt79|brcmf|wlan\d|tty(ACM|USB|AMA|S)\d|under-?voltage|"
                          r"throttl|thermal|gps|pps|rtl|dvb|sx12|spi|i2c|oom|segfault|error|fail", re.I)


def log_dir():
    from . import reports
    return reports.state_dir()


def path_for(now, directory=None):
    return os.path.join(directory or log_dir(), time.strftime("field-%Y%m%d.jsonl", time.localtime(now)))


def record(kind, fields, now):
    """One line's worth: when (local time and epoch), what kind, then the fields that have a value."""
    out = {"t": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)), "ts": round(now, 1), "k": kind}
    out.update({k: v for k, v in (fields or {}).items() if v is not None and v != ""})
    return out


def newest(lines, cls):
    """The newest gpsd report of one class (TPV, SKY) among raw JSON lines, or None."""
    found = None
    for line in lines or []:
        try:
            rec = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("class") == cls:
            found = rec
    return found


def sky_summary(sky):
    """Satellites seen and used, the dilution figures, and the signal of the ones in use. {} with no SKY report."""
    if not sky:
        return {}
    sats = [s for s in sky.get("satellites") or [] if isinstance(s, dict)]
    used = [s for s in sats if s.get("used")]
    snr = [s["ss"] for s in used if isinstance(s.get("ss"), (int, float))]
    all_snr = [s["ss"] for s in sats if isinstance(s.get("ss"), (int, float))]
    out = {"seen": sky.get("nSat", len(sats)) if sats or sky.get("nSat") is not None else None,
           "used": sky.get("uSat", len(used)) if sats or sky.get("uSat") is not None else None,
           "hdop": sky.get("hdop"), "vdop": sky.get("vdop"), "pdop": sky.get("pdop")}
    if snr:
        out["snr_used_max"], out["snr_used_mean"] = max(snr), round(sum(snr) / len(snr), 1)
    if all_snr:
        out["snr_max"] = max(all_snr)
    return out


def gps_fields(tpv, sky):
    """What one GPS line holds. Both altitudes are kept as the receiver gave them: above mean sea level and above
    the ellipsoid differ by tens of metres, and which one a reading was matters when checking it afterwards.
    With no answer from gpsd at all, says so."""
    if not tpv and not sky:
        return {"answer": False}
    t = tpv or {}
    out = {"mode": t.get("mode", 0), "lat": t.get("lat"), "lon": t.get("lon"),
           "alt_msl": t.get("altMSL", t.get("alt")), "alt_hae": t.get("altHAE"),
           "speed": t.get("speed"), "track": t.get("track"), "climb": t.get("climb"),
           "eph": t.get("eph"), "epv": t.get("epv"), "gps_time": t.get("time")}
    out.update(sky_summary(sky))
    return out


class FixWatch:
    """Turns a stream of fix modes into the moments worth a line of their own: the rail coming on, the first fix
    and how long it took, a fix lost and found again."""

    def __init__(self):
        self.on_since, self.had_fix, self.fixed = None, False, False

    def update(self, rail_on, mode, now):
        """[(kind, fields)] for this reading. `mode` is gpsd's (2 or 3 is a fix), or None with no answer."""
        out = []
        if not rail_on:
            if self.on_since is not None:
                out.append(("gps_rail_off", {}))
            self.on_since, self.had_fix, self.fixed = None, False, False
            return out
        if self.on_since is None:
            self.on_since = now
            out.append(("gps_rail_on", {}))
        fix = (mode or 0) >= 2
        if fix and not self.fixed:
            took = {"seconds_since_rail_on": round(now - self.on_since, 1), "mode": mode}
            out.append(("gps_fix_back", took) if self.had_fix else ("gps_first_fix", took))
            self.had_fix = True
        elif not fix and self.fixed:
            out.append(("gps_fix_lost", {"mode": mode}))
        self.fixed = fix
        return out


def state_fields(snapshot, stats=None, names=None, tab=None, sdr_mhz=None, mesh_up=None, battery=None):
    """The half-minute picture: which rails are on, the battery, how hot and how loaded, which tabs exist.
    `battery` is (percent, state, watts) as the Home screen shows it."""
    snap = snapshot or {}
    pct, state, watts = battery or (None, None, None)
    s = stats or {}
    return {"rails": snap.get("rails") or {}, "battery": pct, "power": state, "watts": watts,
            "temp": s.get("temp"), "cpu": s.get("cpu"),
            "load": s.get("load"), "tabs": list(names or []), "tab": tab,
            "sdr_mhz": sdr_mhz, "mesh_up": mesh_up}


def kernel_lines(text, limit=40, seen=None):
    """The kernel lines worth keeping from a stretch of its log: radios, USB, power and heat, errors.
    `seen` is a list of lines already written: the log is asked for "since the last look" to the second, so a
    line on the boundary came back twice. Lines in `seen` are left out, and the new ones are added to it."""
    lines = [line.strip() for line in (text or "").splitlines() if KERNEL_WORDS.search(line)]
    if seen is not None:
        lines = [line for line in lines if line not in seen]
        seen.extend(lines)
        del seen[:-400]
    return lines[-limit:]


def megabytes(n):
    return f"{n / (1024 * 1024):.0f} MB" if n >= 1024 * 1024 else f"{n} bytes"


def full_reason(cap):
    return f"today's file is full ({megabytes(cap)}), so nothing more is kept until tomorrow."


def write_reason(exc):
    """Why a line could not be written, in plain words."""
    text = getattr(exc, "strerror", None) or str(exc) or type(exc).__name__
    return f"the file could not be written ({text[:1].lower() + text[1:]})."


def status_text(log, on, clock_text=None):
    """The line Settings > Debug shows about the field log: whether lines are really reaching the file, and when
    the last one did. '' while logging is off."""
    if not on or log is None:
        return ""
    stamp = clock_text or (lambda t: time.strftime("%H:%M:%S", time.localtime(t)))
    last = f"The last line was written at {stamp(log.ok_at)}." if log.ok_at else "Nothing has been written this session."
    if log.failure:
        return f"Logging is switched on but is NOT being written: {log.failure} {last}"
    return f"Field log is being written. {last}" if log.ok_at else "Field log is on. Nothing has been written yet."


class FieldLog:
    """Appends lines to today's file while `enabled()` says so. Never raises: a log that cannot be written is
    not a reason for the app to stop. It does keep count, though: `ok_at` is when a line last reached the file
    and `failure` says why lines are not reaching it now ('' while they are), so the app can stop saying that
    logging is on when it is not."""

    def __init__(self, enabled, directory=None, clock=time.time, cap=FILE_CAP):
        self.enabled, self.directory, self.clock, self.cap = enabled, directory, clock, cap
        self.lock, self.full_said = threading.Lock(), None
        self.ok_at, self.failure, self.failed_at, self.told = None, "", None, False

    def _failed(self, reason, now):
        if not self.failure:
            self.failed_at, self.told = now, False
        self.failure = reason

    def news(self):
        """The reason, once, the first time this is asked after lines stopped reaching the file; '' otherwise.
        (Writes happen in worker threads; the window asks this from its own timer and shows one notification.)"""
        if self.failure and not self.told:
            self.told = True
            return self.failure
        return ""

    def event(self, kind, **fields):
        if not self.enabled():
            return False
        now = self.clock()
        path = path_for(now, self.directory)
        try:
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            with self.lock:
                size = os.path.getsize(path) if os.path.exists(path) else 0
                full = size >= self.cap
                if full:
                    if self.full_said == path:
                        self._failed(full_reason(self.cap), now)
                        return False
                    self.full_said, kind, fields = path, "log_full", {"bytes": size}
                line = json.dumps(record(kind, fields, now), separators=(",", ":"), default=str)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, "a") as f:
                    f.write(line + "\n")
                if full:                          # that was the closing line: from here on nothing is kept today
                    self._failed(full_reason(self.cap), now)
                else:
                    self.ok_at, self.failure, self.failed_at = now, "", None
            return True
        except (OSError, TypeError, ValueError) as exc:
            if isinstance(exc, OSError):          # a line that could not be put into words is not a broken log
                self._failed(write_reason(exc), now)
            return False


def files(directory=None):
    """Every field log on this device, oldest first."""
    d = directory or log_dir()
    try:
        return sorted(os.path.join(d, f) for f in os.listdir(d) if f.startswith("field-") and f.endswith(".jsonl"))
    except OSError:
        return []


def session_fields():
    """What was running, for the line written when logging starts."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def run(args):
        try:
            return subprocess.run(args, capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    return {"app": run(["git", "-C", here, "rev-parse", "--short", "HEAD"]), "kernel": run(["uname", "-r"]),
            "gpsd": run(["gpsd", "-V"])[:60]}


def attach(win):
    """Start the field log for a window: mirror its activity log, and sample on a timer. Qt is imported here.
    Everything slow (gpsd, the kernel log) is asked in a thread; with logging off the timer does nothing."""
    from PyQt6.QtCore import QTimer
    from . import home_panel, pixel, statusbar

    log = FieldLog(lambda: bool(win.settings.get("debug_log")))
    watch = FixWatch()
    st = {"gps_at": 0.0, "state_at": 0.0, "kernel_from": time.time(), "busy": False, "cpu": None, "was_on": False}
    win.activity.listeners.append(lambda e: log.event("activity", text=e[2], level=e[1]))

    def page_fields():
        out = {}
        bt = win.pages.get("Bluetooth") if hasattr(win, "pages") else None
        if bt is not None:
            out["bt"] = {"scanning": bool(getattr(bt, "feed", None)), "heard": getattr(bt, "heard", 0),
                         "radio_restarts": getattr(bt, "resumes", 0),
                         "logged": len(getattr(getattr(bt, "log", None), "nets", None) or ())}
        sv = getattr(getattr(win, "wifi_panel", None), "survey", None)
        if sv is not None:
            out["survey"] = {"running": bool(getattr(sv, "feed", None)),
                             "networks": sv.aps.rowCount() if hasattr(sv, "aps") else None,
                             "devices": sv.stas.rowCount() if hasattr(sv, "stas") else None,
                             "logged": len(getattr(getattr(sv, "log", None), "nets", None) or ())}
        scan = getattr(getattr(win, "wifi_panel", None), "scan_data", None) or {}
        if scan.get("networks"):
            out["wifi_scan_networks"] = len(scan["networks"])
        return out

    def work(now, rail_on, do_gps, do_state, snap, names, tab, pages):
        try:
            if do_gps:
                from . import position
                lines = position.gpsd_lines(2.0) if rail_on else []
                tpv, sky = newest(lines, "TPV"), newest(lines, "SKY")
                for kind, fields in watch.update(rail_on, (tpv or {}).get("mode") if rail_on else None, now):
                    log.event(kind, **fields)
                if rail_on:
                    log.event("gps", **gps_fields(tpv, sky))
            if do_state:
                stats = statusbar.read_stats(st["cpu"], 0)
                st["cpu"] = stats.get("sample")
                mhz, mesh = home_panel.scan_processes()
                log.event("state", **dict(state_fields(snap, stats, names, tab, mhz, mesh,
                                                       pixel.battery_summary(snap)), **pages))
                since, st["kernel_from"] = st["kernel_from"], now
                try:
                    text = subprocess.run(["journalctl", "-k", "-q", "--no-pager", "-o", "short-iso",
                                           f"--since=@{int(since)}"], capture_output=True, text=True, timeout=8).stdout
                except (OSError, subprocess.SubprocessError):
                    text = ""
                kept = kernel_lines(text, seen=st.setdefault("kernel_seen", []))
                if kept:
                    log.event("kernel", lines=kept)
        finally:
            st["busy"] = False

    def tick():
        on = log.enabled()
        why = log.news() if on else ""
        if why:                                           # once per failure, not once per lost line
            win.notifier.add("warnings", "Field log not being written", "Logging is on, but " + why, "warn")
        if on and not st["was_on"]:                       # just switched on (or on at start): say what is running
            st["kernel_from"] = time.time()
            threading.Thread(target=lambda: log.event("session", **session_fields()), daemon=True).start()
        st["was_on"] = on
        if not on or st["busy"]:
            return
        now = time.time()
        rail_on = ((win.snap or {}).get("rails") or {}).get("GPS") == "on"
        do_gps = now - st["gps_at"] >= GPS_EVERY and (rail_on or watch.on_since is not None)
        do_state = now - st["state_at"] >= STATE_EVERY
        if not (do_gps or do_state):
            return
        if do_gps:
            st["gps_at"] = now
        if do_state:
            st["state_at"] = now
        st["busy"] = True
        names = list(getattr(win, "names", ()))
        idx = win.tabs.currentIndex() if hasattr(win, "tabs") else -1
        tab = names[idx] if 0 <= idx < len(names) else None
        threading.Thread(target=work, args=(now, rail_on, do_gps, do_state, win.snap, names, tab,
                                            page_fields() if do_state else {}), daemon=True).start()

    timer = QTimer(win)
    timer.timeout.connect(tick)
    timer.start(1000)
    log.timer = timer
    return log
