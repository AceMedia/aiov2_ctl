"""
Pure helpers for the Flipper tab's session view: what the plugged-in Flipper says it is, which of the tab's
read-only features can work on it, its clock, and short previews of capture files copied off it.

Nothing here talks to the Flipper or to Qt. The tab feeds in text it already has (the `device_info` answer that
`aiov2_ctl --flipper status` collects, the `date` answer, a capture file copied to the uConsole) and shows what
comes back, so all of it can be tested without a Flipper.
"""
import re

_KEY_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _clean(value, limit=120):
    """A value safe to show: control characters dropped, length capped."""
    return _CONTROL_RE.sub("", str(value)).strip()[:limit]


def _norm_key(key):
    # Older firmware prints `firmware_origin_fork`, newer `info device` prints `firmware.origin.fork`.
    return key.strip().lower().replace(".", "_").replace(" ", "_")


# ---- 1. What the Flipper says it is ------------------------------------------------------------------------

def parse_device_info(source):
    """A small record from the Flipper's own device information.

    `source` is either the text of `device_info` / `info device` ('key : value' lines) or the dict that
    `aiov2_ctl --flipper status --json` already holds under "device". Only what the device reports is used:
    the origin (stock or a fork) is the device's own `firmware_origin_fork`, never guessed from a version
    string. Returns {} for empty or unrecognisable input."""
    if isinstance(source, dict):
        pairs = list(source.items())
    elif isinstance(source, str):
        pairs = [line.partition(":")[::2] for line in source.splitlines() if ":" in line]
    else:
        return {}
    kv = {}
    for key, value in pairs:
        if not isinstance(key, str) or value is None:
            continue
        key, value = _norm_key(key), _clean(value)
        if _KEY_RE.match(key) and value:
            kv[key] = value
    if not any(k.startswith(("hardware_", "firmware_", "radio_")) for k in kv):
        return {}

    def pair(major, minor):
        if kv.get(major) is None:
            return ""
        return kv[major] + (f".{kv[minor]}" if kv.get(minor) is not None else "")

    radio = pair("radio_stack_major", "radio_stack_minor")
    if radio and kv.get("radio_stack_type"):
        radio += f" (type {kv['radio_stack_type']})"      # a number on the device; not decoded here
    modules = []
    for key, name, yes, no in (("radio_alive", "Bluetooth radio core", "running", "not running"),
                               ("enclave_valid", "secure enclave", "valid", "not valid")):
        if key in kv:
            state = kv[key].lower()
            modules.append((name, yes if state in ("true", "1", "yes") else no if state in ("false", "0", "no")
                            else kv[key]))
    return {
        "model": kv.get("hardware_model", ""),
        "name": kv.get("hardware_name", ""),
        "hardware_version": kv.get("hardware_ver", ""),
        "region": kv.get("hardware_region", ""),
        "firmware_version": kv.get("firmware_version", ""),
        "branch": kv.get("firmware_branch", ""),
        "origin": kv.get("firmware_origin_fork", ""),
        "origin_git": kv.get("firmware_origin_git", ""),
        "commit": kv.get("firmware_commit", ""),
        "build_date": kv.get("firmware_build_date", ""),
        "radio_stack": radio,
        "rpc_version": pair("protobuf_version_major", "protobuf_version_minor"),
        "modules": modules,
    }


def about_rows(record):
    """[(label, value)] for the tab's compact 'About this Flipper' block."""
    if not record:
        return [("Details", "The Flipper has not described itself yet.")]
    hw = " ".join(x for x in (record.get("model") or "Flipper",
                              f"“{record['name']}”" if record.get("name") else "",
                              f"hardware {record['hardware_version']}" if record.get("hardware_version") else "")
                  if x)
    fw = record.get("firmware_version") or "not reported"
    if record.get("build_date"):
        fw += f", built {record['build_date']}"
    origin = record.get("origin") or "not reported"
    if record.get("origin_git"):
        origin += f" ({record['origin_git']})"
    rows = [("Hardware", hw), ("Firmware", fw), ("Branch", record.get("branch") or "not reported"),
            ("Origin", origin), ("Radio stack", record.get("radio_stack") or "not reported")]
    if record.get("modules"):
        rows.append(("Reports", ", ".join(f"{n} {s}" for n, s in record["modules"])))
    return rows



# ---- 2. Which of the tab's features can work on this Flipper ---------------------------------------------

FEATURES = {
    "live_screen": "Live display",
    "files": "File browser",
    "copy": "Copy to uConsole",
    "send": "Send a file / new folder / install",
    "apps": "App list",
    "pins": "Reading the header pins",
}
_NEEDS_RPC = ("live_screen", "copy", "send")       # these go over the protobuf RPC session
_NEEDS_SD = ("files", "copy", "send")


def capabilities(record, status=None):
    """{feature: (available, reason)} for the tab's existing features.

    A feature is only switched off on real evidence that it cannot work: the Flipper is in USB-UART Bridge
    mode, it reports no SD card, or it described itself without an RPC (protobuf) version, which the screen
    and file transfers need. When the Flipper has not described itself at all nothing is switched off, so an
    unexpected device_info layout never takes away something that works today."""
    status = status or {}
    out = {}
    for key in FEATURES:
        ok, why = True, ""
        if status.get("mode") == "bridge":
            ok, why = False, "The Flipper is in USB-UART Bridge mode. Press Back on it for the CLI."
        elif key in _NEEDS_SD and status.get("sd_card") == "missing":
            ok, why = False, "The Flipper reports no SD card."
        elif key in _NEEDS_RPC and record and not record.get("rpc_version"):
            ok, why = False, "This firmware does not report an RPC version, which this needs."
        out[key] = (ok, why)
    return out


def unavailable_rows(caps):
    """[(feature label, reason)] for the features switched off, to show under the About block."""
    return [(FEATURES.get(k, k), why) for k, (ok, why) in (caps or {}).items() if not ok]


def available(caps, feature):
    """True unless `caps` says the feature cannot work (unknown features are left alone)."""
    return bool((caps or {}).get(feature, (True, ""))[0])

