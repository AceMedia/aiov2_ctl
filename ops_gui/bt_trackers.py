"""
Names item trackers from what a Bluetooth advertisement already says about itself.

Nothing here touches a radio. It reads the fields a passive scan has kept for each address heard: the
manufacturer data (company number and the bytes after it), the 16 bit service numbers listed, and the service
data. Only public, documented markers are used, and each result says which one matched, so the app can show why.

What this does not do: it does not say who a tracker belongs to, where it is, or whether it is following anyone.
Trackers are common in public. A match only says what kind of device the advertisement claims to be.

The device shape it reads (any part may be missing):
    {"mfr_data": {"004c": "1219..."},          company number as 4 hex digits -> the bytes after it, as hex
     "service_uuids": ["fd5a", ...],           16 bit numbers, or full 128 bit Bluetooth base UUIDs, or ints
     "service_data": {"feaa": "40..."}}        service number -> the bytes after it, as hex
"""

BASE_SUFFIX = "-0000-1000-8000-00805f9b34fb"     # the Bluetooth base UUID that 16 bit numbers stand for

APPLE = 0x004C
APPLE_OFFLINE_FINDING = 0x12                     # Find My network accessories and AirTags
GOOGLE_EDDYSTONE = 0xFEAA
GOOGLE_FMDN_FRAMES = (0x40, 0x41)                # Find My Device network frames carried in Eddystone service data

# Service numbers that on their own mark a tracker: number -> (kind, label).
SERVICE_TRACKERS = {
    0xFD5A: ("samsung_smarttag", "Samsung SmartTag"),
    0xFEED: ("tile", "Tile tracker"),
    0xFEEC: ("tile", "Tile tracker"),
    0xFE33: ("chipolo", "Chipolo tracker"),
}


def uuid16(value):
    """A 16 bit service number from an int, '0xFD5A', 'fd5a', 'FD5A' or the full base form
    '0000fd5a-0000-1000-8000-00805f9b34fb'. None for anything else, including 128 bit UUIDs outside the base."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 0xFFFF else None
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if len(text) == 36:
        if not (text.startswith("0000") and text.endswith(BASE_SUFFIX)):
            return None
        text = text[4:8]
    elif text.startswith("0x"):
        text = text[2:]
    if not 1 <= len(text) <= 4 or any(c not in "0123456789abcdef" for c in text):
        return None
    return int(text, 16)


def hex_bytes(value):
    """Bytes from a hex string (or bytes as they are). None when it is not clean hex."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if not isinstance(value, str):
        return None
    try:
        return bytes.fromhex(value.strip())
    except ValueError:
        return None


def _keyed(mapping):
    """A dict keyed by 16 bit number, with its bytes. Bad keys or values are left out."""
    out = {}
    if not isinstance(mapping, dict):
        return out
    for key, value in mapping.items():
        number, data = uuid16(key), hex_bytes(value)
        if number is not None and data is not None:
            out[number] = data
    return out


def identify(device):
    """{'kind', 'label', 'basis'} when the advertisement carries a known tracker marker, otherwise None."""
    if not isinstance(device, dict):
        return None
    mfr = _keyed(device.get("mfr_data"))
    apple = mfr.get(APPLE)
    if apple and apple[0] == APPLE_OFFLINE_FINDING:
        return {"kind": "apple_find_my", "label": "Apple Find My tracker (AirTag or similar)",
                "basis": "manufacturer data 004C, type 12"}
    service_data = _keyed(device.get("service_data"))
    google = service_data.get(GOOGLE_EDDYSTONE)
    if google and google[0] in GOOGLE_FMDN_FRAMES:
        return {"kind": "google_find_my_device", "label": "Google Find My Device tracker",
                "basis": f"service data FEAA, frame type {google[0]:02X}"}
    listed = device.get("service_uuids")
    numbers = [uuid16(u) for u in listed] if isinstance(listed, (list, tuple)) else []
    numbers += list(service_data)
    for number in numbers:
        if number in SERVICE_TRACKERS:
            kind, label = SERVICE_TRACKERS[number]
            return {"kind": kind, "label": label, "basis": f"service {number:04X}"}
    return None


NOTE = ("Trackers are common in public, and hearing one nearby is normal. The tracker label only names what kind "
        "of device an advertisement says it is. It does not say whose it is, where it is or whether it is "
        "following anyone.")
