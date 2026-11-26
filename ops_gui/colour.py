"""
Colour helpers for the feature colour: hex checking, hue saturation value maths, a legibility clamp and the second
colour derived from the first. Pure Python so it can be tested without a screen.
"""
import colorsys
import re

DEFAULT = "#d70a53"                       # Debian red
PRESETS = ("#d70a53", "#e95420", "#f5a623", "#62e06b", "#3fe0d0", "#4c8dff", "#9b59ff", "#ff5fb5")
_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def valid_hex(text):
    """'#rrggbb' in lower case for '#rgb', 'rgb', '#rrggbb' or 'rrggbb', else None."""
    m = _HEX.match((text or "").strip())
    if not m:
        return None
    h = m.group(1).lower()
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "#" + h


def to_rgb(hex_colour):
    h = valid_hex(hex_colour) or DEFAULT
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def from_rgb(r, g, b):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(x)))) for x in (r, g, b))


def hex_to_hsv(hex_colour):
    """(hue 0 to 360, saturation 0 to 1, value 0 to 1)."""
    r, g, b = (c / 255.0 for c in to_rgb(hex_colour))
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    return h * 360.0, s, v


def hsv_to_hex(h, s, v):
    r, g, b = colorsys.hsv_to_rgb((h % 360.0) / 360.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return from_rgb(r * 255, g * 255, b * 255)


def luminance(hex_colour):
    def lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in to_rgb(hex_colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def readable(hex_colour, floor=0.06):
    """The colour, lightened just enough that it still shows against the dark background."""
    h = valid_hex(hex_colour) or DEFAULT
    hue, sat, val = hex_to_hsv(h)
    while luminance(h) < floor and val < 1.0:
        val = min(1.0, val + 0.05)
        h = hsv_to_hex(hue, sat, val)
    return h


def secondary(hex_colour):
    """The partner colour for stripes and highlights: a little lighter and a touch warmer than the feature colour."""
    hue, sat, val = hex_to_hsv(hex_colour)
    return hsv_to_hex(hue + 12.0, sat * 0.9, min(1.0, val + 0.12 + (0.0 if val < 0.9 else -0.0)))
