"""Coordinates from Meshtastic positions, checked once here for every view that uses them.

A position may carry floats (latitude, longitude) or the radio's integers (latitudeI, longitudeI, in units of
1e-7 degrees). Anything out of range, not a finite number, or the pair 0,0 (what a node with no fix reports) is
not a position. Pure, no Meshtastic needed."""
import math


def _coordinate(value, scaled):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if scaled:
        value *= 1e-7
    return value if math.isfinite(value) else None


def latlon(position):
    """(lat, lon) in degrees from a position dict, or None when it is not a usable position."""
    if not isinstance(position, dict):
        return None
    lat = _coordinate(position.get("latitude"), False)
    if lat is None and position.get("latitude") is None:
        lat = _coordinate(position.get("latitudeI"), True)
    lon = _coordinate(position.get("longitude"), False)
    if lon is None and position.get("longitude") is None:
        lon = _coordinate(position.get("longitudeI"), True)
    if lat is None or lon is None:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    if lat == 0.0 and lon == 0.0:
        return None
    return lat, lon
