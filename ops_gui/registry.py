"""
A shared record of every BSSID (access point address) seen anywhere in the app: Survey's passive monitor-mode
listening and an ordinary Wi-Fi scan both see real BSSIDs with names and security; Sense's air source only ever
sees bare addresses. Feeding everything a device hears into one registry means a BSSID can carry a name, channel
and security from whichever source saw it best, and Sense can show what a sender actually is instead of the last
few characters of its address.

A small JSON file alongside the in-memory table (one entry per BSSID, the newest facts win) survives a restart of
the app, so it also works as a baseline: a BSSID this registry has never seen before, or a known one whose security
has changed, can be told apart from one that is simply being seen again. Nothing here reads a payload or sends
anything; it only keeps what the passive tools already produce.
"""
import json
import os
import re
import time

REGISTRY_PATH = os.path.join(os.path.expanduser("~"), ".config", "ops_gui", "bssids.json")
MAX_ENTRIES = 4000                # keep the file from growing without bound on a very long-running uConsole
# A registry only counts as a baseline once something in it is this old. Before that everything is new, and saying
# so about every network in range the first time the app listens would only be noise.
BASELINE_SECONDS = 600
_UNKNOWN = ("", "unknown", "?")
_OPEN = ("open", "none")


def known_security(security):
    """The security word if it says something, or '' for nothing, 'unknown' and the like."""
    text = (security or "").strip() if isinstance(security, str) else ""
    return "" if text.lower() in _UNKNOWN else text


def is_open(security):
    return known_security(security).lower() in _OPEN


def describe(event):
    """(notification source, title, body, level) for one event from Registry.notable. Says what was heard and
    nothing more: a changed or missing security setting is worth a look, it is not proof of anything."""
    name = event.get("ssid") or "A network with no name"
    bssid = event.get("bssid", "")
    if event.get("kind") == "security":
        return ("netsec", "Network security changed", f"{name} ({bssid}) was {event['old']}, now {event['new']}.", "warn")
    if event.get("kind") == "twin":
        return ("netsec", "Same name, no security",
                f"{name} is now also announced by {bssid} with no security. It is {event['old']} on the one already known.",
                "warn")
    security = known_security(event.get("security")) or "security not known"
    return ("netnew", "New network", f"{name} ({bssid}), {security}.", "info")
_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def normalise(bssid):
    """A plain lower case 'aa:bb:cc:dd:ee:ff', or '' for anything that is not shaped like one."""
    if not isinstance(bssid, str):
        return ""
    text = bssid.strip().lower()
    return text if _MAC_RE.match(text) else ""


def short_label(bssid):
    """The fallback name when nothing is known about a BSSID yet: the same style Sense has always used."""
    return "ap " + normalise(bssid)[-8:] if normalise(bssid) else "ap ?"


class Registry:
    """entries: bssid -> {'ssid', 'channel', 'security', 'rssi', 'first', 'last', 'sources'}."""

    def __init__(self):
        self.entries = {}

    def see(self, bssid, ssid=None, channel=None, security=None, rssi=None, source="", now=None):
        """Record one sighting of an access point. Returns True the first time this BSSID is ever seen."""
        mac = normalise(bssid)
        if not mac:
            return False
        now = time.time() if now is None else now
        entry = self.entries.get(mac)
        is_new = entry is None
        if entry is None:
            entry = self.entries[mac] = {"ssid": "", "channel": None, "security": "", "rssi": None,
                                         "first": now, "last": now, "sources": []}
        if ssid:
            entry["ssid"] = ssid
        if channel:
            entry["channel"] = channel
        if security:
            entry["security"] = security
            if source and known_security(security):       # each source words security its own way: compare like with like
                entry.setdefault("security_by", {})[source] = security
        if rssi is not None:
            entry["rssi"] = rssi
        entry["last"] = now
        if source and source not in entry["sources"]:
            entry["sources"].append(source)
        return is_new

    def see_survey(self, snap, source="survey", now=None):
        """Feed one --wifi-survey snapshot (the shape survey.py already reads). Returns newly seen BSSIDs."""
        if not isinstance(snap, dict):
            return []
        new = []
        when = now if now is not None else snap.get("t")
        for ap in snap.get("aps", []) if isinstance(snap.get("aps"), list) else []:
            if not isinstance(ap, dict):
                continue
            if self.see(ap.get("bssid"), ssid=ap.get("ssid"), channel=ap.get("channel"),
                       security=ap.get("security"), rssi=ap.get("rssi"), source=source, now=when):
                new.append(normalise(ap.get("bssid")))
        return new

    def see_wifi_scan(self, networks, source="wifi", now=None):
        """Feed an ordinary `aiov2_ctl --wifi scan` network list (wifi_panel.py's own shape)."""
        new = []
        for n in networks or []:
            if not isinstance(n, dict):
                continue
            if self.see(n.get("bssid"), ssid=n.get("ssid"), channel=n.get("channel"),
                       security=n.get("security"), rssi=n.get("signal_dbm"), source=source, now=now):
                new.append(normalise(n.get("bssid")))
        return new

    def settled(self, now):
        """True once the registry is old enough to be a baseline (see BASELINE_SECONDS)."""
        return any(isinstance(e.get("first"), (int, float)) and e["first"] <= now - BASELINE_SECONDS
                   for e in self.entries.values())

    def notable(self, bssid, ssid=None, security=None, source="", now=None):
        """What is worth telling someone about this sighting, judged against what is already known. Call it BEFORE
        see(), which then records the sighting. Returns a list of events (usually empty):
          {'kind': 'security', ...}  a known access point now announces a different security than the same source
                                     heard from it before;
          {'kind': 'twin', ...}      a never seen access point announces, with no security, a name that is already
                                     known with security;
          {'kind': 'new', ...}       a never seen access point (only once the registry is a settled baseline)."""
        mac = normalise(bssid)
        if not mac:
            return []
        now = time.time() if now is None else now
        security = known_security(security)
        entry = self.entries.get(mac)
        base = {"bssid": mac, "ssid": (ssid or (entry or {}).get("ssid") or "").strip(), "security": security}
        if entry is not None:
            old = known_security((entry.get("security_by") or {}).get(source))
            if old and security and old != security:
                return [dict(base, kind="security", old=old, new=security)]
            return []
        if not self.settled(now):
            return []
        if base["ssid"] and is_open(security):
            for other in self.entries.values():
                secured = known_security(other.get("security"))
                if (other.get("ssid") or "").strip() == base["ssid"] and secured and not is_open(secured):
                    return [dict(base, kind="twin", old=secured)]
        return [dict(base, kind="new")]

    def observe_survey(self, snap, source="survey", now=None):
        """Feed one --wifi-survey snapshot and say what was notable in it: (newly seen BSSIDs, events)."""
        events = []
        if isinstance(snap, dict) and isinstance(snap.get("aps"), list):
            when = now if now is not None else snap.get("t")
            for ap in snap["aps"]:
                if isinstance(ap, dict):
                    events += self.notable(ap.get("bssid"), ap.get("ssid"), ap.get("security"), source, when)
        return self.see_survey(snap, source, now), events

    def observe_wifi_scan(self, networks, source="wifi", now=None):
        """Feed an ordinary Wi-Fi scan and say what was notable in it: (newly seen BSSIDs, events)."""
        events = []
        for n in networks or []:
            if isinstance(n, dict):
                events += self.notable(n.get("bssid"), n.get("ssid"), n.get("security"), source, now)
        return self.see_wifi_scan(networks, source, now), events

    def known(self, bssid):
        return self.entries.get(normalise(bssid))

    def label(self, bssid):
        """The best name to show for a BSSID: its SSID if one is known (never blank or just whitespace,
        and never the placeholder hidden-network text), else the old "ap xx:xx:xx" style fallback."""
        entry = self.known(bssid)
        ssid = (entry or {}).get("ssid") or ""
        ssid = ssid.strip()
        return ssid if ssid else short_label(bssid)

    def changed_security(self, bssid, security):
        """True when this BSSID is already known with a *different*, non-empty security than given."""
        entry = self.known(bssid)
        old = (entry or {}).get("security") or ""
        return bool(entry and old and security and old != security)

    def to_json(self):
        return json.dumps(self.entries, indent=0)

    def load(self, path=None):
        """Read the registry from disk, if it is there. Never raises; a missing or broken file just leaves it empty."""
        try:
            with open(path or REGISTRY_PATH) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            self.entries = {normalise(k): v for k, v in data.items() if normalise(k) and isinstance(v, dict)}

    def save(self, path=None):
        """Write the registry to disk, trimmed to the most recently seen entries. Never raises."""
        path = path or REGISTRY_PATH
        try:
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            trimmed = dict(sorted(self.entries.items(), key=lambda kv: kv[1].get("last", 0), reverse=True)[:MAX_ENTRIES])
            tmp = path + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(trimmed, f)
            os.replace(tmp, path)
        except OSError:
            pass
